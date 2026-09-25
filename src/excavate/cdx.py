"""Stage 1 — CDX inventory (no collapse, no mimetype filter).

manifest.csv keeps EVERY raw row (the strops invariant: row count is never
reduced by filtering). captures.json resolves each unique URL to its best
surviving capture — the thing `pull` works through. Re-running inventory
MERGES into existing state rather than wiping it (fetch results survive).
"""

from __future__ import annotations

import httpx

from .dig import Dig

CDX_API = "https://web.archive.org/cdx/search/cdx"

REVISIT_MIMETYPE = "warc/revisit"

_STATE_FIELDS = ("fetch_state", "sha1_fetched", "source", "http_status",
                 "magic_ok", "error", "raw_path", "skip_reason", "path_collision")


def fetch_cdx_rows(domain: str, client: httpx.Client) -> list[dict]:
    """Query the CDX API for url=<domain>* — one request, raw rows only.

    Ragged rows (length != header) are skipped: the CDX API occasionally
    returns error/not-found lines that must not crash the inventory.
    """
    response = client.get(CDX_API, params={"url": f"{domain}*", "output": "json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        # CDX error replies are JSON objects or plain strings — never a quiet empty dig
        raise ValueError(f"malformed CDX payload (got {type(payload).__name__})")
    header, *rows = payload
    return [dict(zip(header, row)) for row in rows if len(row) == len(header)]


def _rank(row: dict) -> tuple:
    """Higher wins: has-content 2xx beats revisit beats error; newer beats older."""
    status = str(row.get("statuscode", ""))
    has_content = row.get("mimetype") != REVISIT_MIMETYPE and status.startswith("2")
    return (has_content, status.startswith("2"), row.get("timestamp", ""))


def resolve_best(rows: list[dict]) -> list[dict]:
    """Best surviving capture per original URL: 2xx with content, newest wins."""
    best: dict[str, dict] = {}
    for row in rows:
        url = row.get("original")
        if not url:
            continue
        if url not in best or _rank(row) > _rank(best[url]):
            best[url] = row
    caps = []
    for url, row in best.items():
        fetchable = row.get("mimetype") != REVISIT_MIMETYPE and str(row.get("statuscode", "")).startswith("2")
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
                "error": None,
                "raw_path": None,
                "path_collision": None,
            }
        )
    caps.sort(key=lambda c: c["url"])
    return caps


def _skip_reason(row: dict) -> str:
    if row.get("mimetype") == REVISIT_MIMETYPE:
        return "revisit-only (no body in CDX)"
    return f"best capture status {row.get('statuscode', '?')}"


def apply_inventory(dig: Dig, rows: list[dict]) -> dict:
    """Persist raw rows + resolved captures, MERGING into existing state.

    A URL whose best capture is unchanged (same timestamp) keeps its fetch
    results; a re-resolved URL (different timestamp) resets to pending — the
    previously downloaded bytes belong to a different capture.
    """
    caps = resolve_best(rows)
    if not caps:
        raise SystemExit("CDX returned zero usable rows — refusing to write an empty inventory")
    if (dig.root / "captures.json").exists():
        existing = {c["url"]: c for c in dig.load_captures()}
        for cap in caps:
            prev = existing.get(cap["url"])
            if prev and prev.get("timestamp") == cap["timestamp"]:
                for field in _STATE_FIELDS:
                    cap[field] = prev.get(field)
    dig.write_raw_manifest(rows)
    dig.save_captures(caps)
    pending = sum(1 for c in caps if c["fetch_state"] == "pending")
    return {
        "domain": dig.domain,
        "raw_rows": len(rows),
        "unique_urls": len(caps),
        "pending": pending,
        "skipped": len(caps) - pending,
    }


def run_inventory(domain: str, dig: Dig, client: httpx.Client) -> dict:
    return apply_inventory(dig, fetch_cdx_rows(domain, client))
