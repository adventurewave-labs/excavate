"""Stage 1 — CDX inventory (no collapse, no mimetype filter).

manifest.csv keeps EVERY raw row (the strops invariant: row count is never
reduced by filtering). captures.json resolves each unique URL to its best
surviving capture — the thing `pull` works through. Re-running inventory
MERGES into existing state rather than wiping it (fetch results survive).

The CDX call itself is paginated (resume keys), rate-limited and backed off
exactly like pull: one unpaginated request silently truncates a big site, and
an unthrottled one is the easiest way to earn a 429.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .dig import Dig
from .pull import BACKOFF_SCHEDULE, RateLimiter

CDX_API = "https://web.archive.org/cdx/search/cdx"
CDX_PAGE_SIZE = 5000    # rows per request; big sites take several pages
CDX_MAX_PAGES = 2000    # 10M rows — a runaway-pagination guard, not a product limit
MATCH_TYPES = ("prefix", "host", "domain")
MAX_ALTERNATES = 5      # fallback captures remembered per URL (each costs up to 2 requests to try)
_TIMESTAMP = re.compile(r"\d{4,14}")

REVISIT_MIMETYPE = "warc/revisit"

_STATE_FIELDS = ("fetch_state", "sha1_fetched", "source", "http_status",
                 "magic_ok", "error", "raw_path", "skip_reason", "path_collision",
                 "digest_ok", "digest_note")


@dataclass(frozen=True)
class CdxQuery:
    """What to ask the CDX API for. Never a collapse, never a mimetype or
    status filter — narrowing happens by scope (match) and time (from/to)
    only, so the raw manifest stays a complete audit trail of that scope."""

    domain: str
    match: str = "prefix"          # prefix | host | domain (domain = subdomains too)
    from_ts: str | None = None     # 4–14 digits, inclusive
    to_ts: str | None = None

    def __post_init__(self) -> None:
        if self.match not in MATCH_TYPES:
            raise ValueError(f"match must be one of {', '.join(MATCH_TYPES)} (got {self.match!r})")
        for label, value in (("from", self.from_ts), ("to", self.to_ts)):
            if value is not None and not _TIMESTAMP.fullmatch(value):
                raise ValueError(f"--{label} must be 4–14 digits, e.g. 2003 or 20030601 (got {value!r})")
        if self.from_ts and self.to_ts and self.from_ts[:len(self.to_ts)] > self.to_ts:
            raise ValueError(f"--from {self.from_ts} is after --to {self.to_ts}")

    def params(self) -> dict[str, str]:
        params = {"output": "json", "showResumeKey": "true", "limit": str(CDX_PAGE_SIZE)}
        if self.match == "prefix":
            params["url"] = f"{self.domain}*"  # the form M0 has always sent
        else:
            params["url"] = self.domain
            params["matchType"] = self.match
        if self.from_ts:
            params["from"] = self.from_ts
        if self.to_ts:
            params["to"] = self.to_ts
        return params

    def describe(self) -> dict:
        return {"match": self.match, "from": self.from_ts, "to": self.to_ts}


def _get_page(client: httpx.Client, params: dict[str, str], limiter: RateLimiter, sleep_fn) -> list:
    """One CDX page, inside the same rate budget and backoff rules as pull:
    429 / 5xx / transport errors get 3 backed-off retries, then the inventory
    fails loudly — a partial inventory is never written."""
    backoff = list(BACKOFF_SCHEDULE)
    while True:
        limiter.wait()
        try:
            response = client.get(CDX_API, params=params)
        except httpx.TransportError:
            if not backoff:
                raise
            sleep_fn(backoff.pop(0))
            continue
        if (response.status_code == 429 or response.status_code >= 500) and backoff:
            sleep_fn(backoff.pop(0))
            continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or any(not isinstance(row, list) for row in payload):
            # CDX error replies are JSON objects or plain strings — never a quiet empty dig
            raise ValueError(f"malformed CDX payload (got {type(payload).__name__})")
        return payload


def _split_page(payload: list) -> tuple[list[list], str | None]:
    """(rows, resume_key). With showResumeKey the page ends `[], ["<key>"]`
    when more rows remain."""
    if len(payload) >= 2 and payload[-2] == [] and len(payload[-1]) == 1:
        return payload[:-2], str(payload[-1][0])
    return payload, None


@dataclass(frozen=True)
class ExactQuery:
    """Every capture of ONE url (CDX exact match — which is scheme-, `www`-
    and case-insensitive on IA's side). Same no-collapse, no-filter rule."""

    url: str

    def params(self) -> dict[str, str]:
        return {"output": "json", "showResumeKey": "true", "limit": str(CDX_PAGE_SIZE), "url": self.url}


def fetch_cdx_pages(query: CdxQuery | ExactQuery, client: httpx.Client, limiter: RateLimiter | None = None,
                    sleep_fn=time.sleep, allow_empty: bool = False) -> tuple[list[dict], int]:
    """Every raw CDX row for the query, following resume keys. Returns (rows, pages).

    Ragged rows (length != header) are skipped: the CDX API occasionally
    returns error/not-found lines that must not crash the inventory.
    """
    limiter = limiter or RateLimiter(sleep_fn=sleep_fn)
    params = query.params()
    header: list | None = None
    rows: list[dict] = []
    seen_keys: set[str] = set()
    pages = 0
    while True:
        page, resume_key = _split_page(_get_page(client, params, limiter, sleep_fn))
        pages += 1
        if header is None:
            if not page:
                if allow_empty:
                    return [], pages  # a lookup that finds nothing is an answer, not an error
                raise ValueError("malformed CDX payload (empty reply — no captures for this query?)")
            header = page[0]
        if page and page[0] == header:
            page = page[1:]  # every page repeats the header
        rows.extend(dict(zip(header, row, strict=True)) for row in page if len(row) == len(header))
        if resume_key is None:
            return rows, pages
        if resume_key in seen_keys or pages >= CDX_MAX_PAGES:
            raise ValueError(f"CDX pagination did not terminate (page {pages}, resume key repeated or page cap hit)")
        seen_keys.add(resume_key)
        params = {**params, "resumeKey": resume_key}


def fetch_cdx_rows(domain: str, client: httpx.Client, limiter: RateLimiter | None = None,
                   sleep_fn=time.sleep) -> list[dict]:
    """Raw rows for url=<domain>* (default scope), all pages."""
    return fetch_cdx_pages(CdxQuery(domain), client, limiter, sleep_fn)[0]


def parse_timestamp(value: str) -> datetime:
    """A (possibly partial) Wayback timestamp as a datetime; `2003` means the
    start of 2003. Raises ValueError for anything that is not a real date."""
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError(f"not a timestamp: {value!r} (4–14 digits, e.g. 2003 or 20030601)")
    return datetime.strptime(value + "00000101000000"[len(value):], "%Y%m%d%H%M%S")


def _fetchable(row: dict) -> bool:
    return row.get("mimetype") != REVISIT_MIMETYPE and str(row.get("statuscode", "")).startswith("2")


def _distance(row: dict, target: datetime) -> float:
    try:
        return abs((parse_timestamp(str(row.get("timestamp", ""))) - target).total_seconds())
    except ValueError:
        return float("inf")


def _preference(rows: list[dict], at: str | None) -> list[dict]:
    """One URL's captures, most wanted first. Fetchable (2xx with a body)
    always beats revisit beats error. Within a class: newest first, or — with
    `at` — nearest to that moment (ties go to the earlier capture, the one
    that already existed at `at`)."""
    target = parse_timestamp(at) if at else None

    def key(row: dict) -> tuple:
        status = str(row.get("statuscode", ""))
        klass = (not _fetchable(row), not status.startswith("2"))
        stamp = str(row.get("timestamp", ""))
        if target is None:
            return (*klass, 0.0, _invert(stamp))
        return (*klass, _distance(row, target), stamp)

    return sorted(rows, key=key)


def _invert(stamp: str) -> str:
    """Sort key that orders digit strings descending under an ascending sort."""
    return "".join(chr(ord("9") - ord(ch) + ord("0")) if ch.isdigit() else ch for ch in stamp.ljust(14, "0"))


def resolve_best(rows: list[dict], at: str | None = None) -> list[dict]:
    """The wanted capture per original URL, plus its fallback chain.

    Default: the newest 2xx capture with a body. With `at`: the one nearest
    that date — a site as it stood in one era, not the last state of every
    file. `alternates` are the next-most-wanted fetchable captures of the
    same URL; pull works down them when the wanted one turns out to be gone.
    """
    by_url: dict[str, list[dict]] = {}
    for row in rows:
        url = row.get("original")
        if url:
            by_url.setdefault(url, []).append(row)
    caps = []
    for url, url_rows in by_url.items():
        ordered = _preference(url_rows, at)
        row = ordered[0]
        fetchable = _fetchable(row)
        seen = {row.get("timestamp")}
        alternates: list[dict] = []
        for other in ordered[1:]:
            if len(alternates) >= MAX_ALTERNATES or not _fetchable(other):
                break
            if other.get("timestamp") in seen:
                continue  # CDX lists a capture once per WARC record; one try is enough
            seen.add(other.get("timestamp"))
            alternates.append({"timestamp": other.get("timestamp", ""), "digest": other.get("digest", ""),
                               "mimetype": other.get("mimetype", "")})
        caps.append(
            {
                "url": url,
                "timestamp": row.get("timestamp", ""),
                "status": row.get("statuscode", ""),
                "mimetype": row.get("mimetype", ""),
                "digest": row.get("digest", ""),
                "fetch_state": "pending" if fetchable else "skipped",
                "skip_reason": None if fetchable else _skip_reason(row),
                "sha1_fetched": None,
                "source": None,  # id_ | im_
                "http_status": None,
                "magic_ok": None,
                "digest_ok": None,  # True | False | None (unverifiable — see digest_note)
                "digest_note": None,
                "error": None,
                "raw_path": None,
                "path_collision": None,
                "alternates": alternates if fetchable else [],
                "fallback_from": None,  # the wanted timestamp, when an alternate was used instead
            }
        )
    caps.sort(key=lambda c: c["url"])
    return caps


def _skip_reason(row: dict) -> str:
    if row.get("mimetype") == REVISIT_MIMETYPE:
        return "revisit-only (no body in CDX)"
    return f"best capture status {row.get('statuscode', '?')}"


def apply_inventory(dig: Dig, rows: list[dict], query: CdxQuery | None = None, pages: int = 1,
                    at: str | None = None) -> dict:
    """Persist raw rows + resolved captures, MERGING into existing state.

    A URL whose best capture is unchanged (same timestamp) keeps its fetch
    results; a re-resolved URL (different timestamp) resets to pending — the
    previously downloaded bytes belong to a different capture.
    """
    caps = resolve_best(rows, at)
    if not caps:
        raise SystemExit("CDX returned zero usable rows — refusing to write an empty inventory")
    if dig.has_state():
        existing = {c["url"]: c for c in dig.load_captures()}
        for cap in caps:
            prev = existing.get(cap["url"])
            # "unchanged" means the WANTED capture is the same — a row that fell
            # back to an alternate keeps that result instead of re-trying the dead one
            if prev and (prev.get("fallback_from") or prev.get("timestamp")) == cap["timestamp"]:
                for field in _STATE_FIELDS:
                    cap[field] = prev.get(field)
                if prev.get("fallback_from"):
                    for field in ("timestamp", "digest", "mimetype", "fallback_from", "alternates"):
                        cap[field] = prev.get(field)
                elif prev.get("fetch_state") == "failed_4xx" and "alternates" in prev:
                    cap["alternates"] = prev["alternates"]  # already-exhausted chain stays exhausted
        # URLs added by `discover` came from exact lookups outside this query's
        # scope — a re-inventory must not silently drop them (their bytes may be on disk)
        fresh = {cap["url"] for cap in caps}
        caps.extend(prev for url, prev in existing.items() if prev.get("discovered_by") and url not in fresh)
        caps.sort(key=lambda c: c["url"])
    dig.write_raw_manifest(rows)
    dig.save_captures(caps)
    dig.save_meta({**dig.meta(), "selection": {"at": at}})
    if query is not None:
        dig.save_meta({**dig.meta(), "query": query.describe(), "cdx_pages": pages, "raw_rows": len(rows),
                       "inventoried_at": datetime.now(UTC).isoformat(timespec="seconds")})
    dig.write_manifest_json(caps)
    pending = sum(1 for c in caps if c["fetch_state"] == "pending")
    return {
        "domain": dig.domain,
        "raw_rows": len(rows),
        "pages": pages,
        "unique_urls": len(caps),
        "pending": pending,
        "skipped": len(caps) - pending,
    }


def run_inventory(domain: str, dig: Dig, client: httpx.Client, query: CdxQuery | None = None,
                  limiter: RateLimiter | None = None, sleep_fn=time.sleep, at: str | None = None) -> dict:
    query = query or CdxQuery(domain)
    if at is not None:
        parse_timestamp(at)  # fail before spending a request
    rows, pages = fetch_cdx_pages(query, client, limiter, sleep_fn)
    return apply_inventory(dig, rows, query, pages, at)
