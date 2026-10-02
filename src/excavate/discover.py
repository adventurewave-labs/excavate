"""Never-crawled recovery — the navigation-driven step of the magpu pipeline.

The inventory asks the archive "what do you hold under this domain?". The
cleaned pages ask a sharper question: "do you hold THIS file I link to?".
`discover` takes every target the crawl check found referenced but absent
from the manifest and asks CDX for that exact URL — which reaches captures a
windowed (`--from/--to`) or host-scoped inventory left out, and captures
filed under another scheme, `www.` form or letter case.

Hits are added to the dig as pending captures (marked `discovered_by`), their
raw CDX rows appended to manifest.csv. Nothing is fetched here: run `pull`,
`clean`, `report` again afterwards. Lookups are remembered in discover.json,
so a second run only asks about what is new.

Budget: one CDX request per candidate URL, through the same rate limiter and
backoff as everything else. `--limit` caps a run; the rest waits for the next.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from .cdx import ExactQuery, fetch_cdx_pages, resolve_best
from .crawl import analyze
from .dig import Dig, _atomic_write
from .provenance import lines_hash, record_event
from .pull import RateLimiter

DEFAULT_LIMIT = 200          # ≈ 7 minutes of CDX budget at 30 req/min
_QUERY_HASHED = re.compile(r"-[0-9a-f]{8}(\.[A-Za-z0-9]{1,10})?$")


def candidate_urls(domain: str, target: str) -> list[str]:
    """URLs a site path could have come from. A path is not always reversible:
    `dir/index.html` may be the directory URL or a literal file, and a
    query-string page (stored as name-<hash>.ext) cannot be reversed at all."""
    if _QUERY_HASHED.search(target):
        return []
    encoded = quote(target, safe="/~!$&'()*+,;=:@-._")
    urls = [f"{domain}/{encoded}"]
    if target == "index.html":
        urls.insert(0, f"{domain}/")
    elif target.endswith("/index.html"):
        urls.insert(0, f"{domain}/{encoded[: -len('index.html')]}")
    return urls


def _memo_path(dig: Dig):
    return dig.root / "discover.json"


def load_memo(dig: Dig) -> dict:
    path = _memo_path(dig)
    return json.loads(path.read_text()) if path.exists() else {}


def run_discover(dig: Dig, client: httpx.Client, limiter: RateLimiter | None = None, sleep_fn=time.sleep,
                 limit: int | None = DEFAULT_LIMIT, recheck: bool = False) -> dict:
    limiter = limiter or RateLimiter(sleep_fn=sleep_fn)
    captures = dig.load_captures()
    known = {c["url"] for c in captures}
    domain = dig.domain
    at = (dig.meta().get("selection") or {}).get("at")
    memo = {} if recheck else load_memo(dig)

    missing = [entry["target"] for entry in analyze(dig, captures)["never_archived"]]
    todo = [target for target in missing if target not in memo]
    irreversible = [target for target in todo if not candidate_urls(domain, target)]
    todo = [target for target in todo if target not in irreversible]
    deferred = todo[limit:] if limit is not None and limit > 0 else []
    if deferred:
        todo = todo[: len(todo) - len(deferred)]

    added: list[dict] = []
    new_rows: list[dict] = []
    lookups = 0
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        for target in todo:
            found_url = None
            for url in candidate_urls(domain, target):
                rows, _ = fetch_cdx_pages(ExactQuery(url), client, limiter, sleep_fn, allow_empty=True)
                lookups += 1
                fresh = [cap for cap in resolve_best(rows, at) if cap["url"] not in known]
                if fresh:
                    for cap in fresh:
                        cap["discovered_by"] = f"link to `{target}`"
                        known.add(cap["url"])
                    added.extend(fresh)
                    new_rows.extend(rows)
                    found_url = fresh[0]["url"]
                    break
                if rows:
                    found_url = rows[0].get("original")  # archived, and already in the dig under that URL
                    break
            memo[target] = {"checked_at": stamp, "found": found_url}
    finally:
        # whatever was learned before an error or Ctrl-C is kept — the budget spent is not wasted
        for target in irreversible:
            memo[target] = {"checked_at": stamp, "found": None, "note": "query-string page; URL not recoverable from its file name"}
        if added:
            dig.append_raw_manifest(new_rows)
            dig.save_captures(sorted([*captures, *added], key=lambda c: c["url"]))
            dig.write_manifest_json(dig.load_captures())
        _atomic_write(_memo_path(dig), json.dumps(memo, indent=2, sort_keys=True))

    fetchable = sum(1 for cap in added if cap["fetch_state"] == "pending")
    summary = {
        "never_archived": len(missing),
        "already_checked": len(missing) - len(todo) - len(deferred) - len(irreversible),
        "looked_up": len(todo),
        "lookups": lookups,
        "found": len(added),
        "fetchable": fetchable,
        "not_fetchable": len(added) - fetchable,
        "irreversible": len(irreversible),
        "deferred": len(deferred),
        "still_missing": sum(1 for target in todo if not memo.get(target, {}).get("found")),
    }
    record_event(dig, "discover", lines_hash(todo), summary)
    return summary
