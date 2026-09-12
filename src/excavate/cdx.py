"""Stage 1 — CDX inventory (no collapse, no mimetype filter).

manifest.csv keeps EVERY raw row (the strops invariant: row count is never
reduced by filtering). captures.json resolves each unique URL to its best
surviving capture — the thing `pull` works through.
"""

from __future__ import annotations

import httpx

CDX_API = "https://web.archive.org/cdx/search/cdx"

# CDX JSON reply: first row is the header, remaining rows are captures.
FIELDS = ("urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length")

REVISIT_MIMETYPE = "warc/revisit"


def fetch_cdx_rows(domain: str, client: httpx.Client) -> list[dict]:
    """Query the CDX API for url=<domain>* — one request, raw rows only."""
    response = client.get(CDX_API, params={"url": f"{domain}*", "output": "json"})
    response.raise_for_status()
    payload = response.json()
    if not payload:
        return []
    header, *rows = payload
    return [dict(zip(header, row)) for row in rows]


def resolve_best(rows: list[dict]) -> list[dict]:
    """Best surviving capture per original URL: 2xx with content, newest wins.

    Revisit records (mimetype warc/revisit) carry no body — usable as a tiebreak
    on existence but never as the fetch target.
    """
    best: dict[str, dict] = {}
    for row in rows:
        url = row["original"]
        rank = _rank(row)
        if url not in best or rank > _rank(best[url]):
            best[url] = row
    caps = []
    for url, row in best.items():
        fetchable = row["mimetype"] != REVISIT_MIMETYPE and str(row["statuscode"]).startswith("2")
        caps.append(
            {
                "url": url,
                "timestamp": row["timestamp"],
                "status": row["statuscode"],
                "mimetype": row["mimetype"],
                "digest": row["digest"],
                "fetch_state": "pending" if fetchable else "skipped",
                "skip_reason": None if fetchable else _skip_reason(row),
                "sha1_fetched": None,
                "source": None,  # id_ | im_
                "http_status": None,
                "magic_ok": None,
                "error": None,
            }
        )
    caps.sort(key=lambda c: c["url"])
    return caps


def _rank(row: dict) -> tuple:
    """Higher wins: has-content beats revisit beats error; newer beats older."""
    has_content = row["mimetype"] != REVISIT_MIMETYPE and str(row["statuscode"]).startswith("2")
    ok_status = str(row["statuscode"]).startswith("2")
    return (has_content, ok_status, row["timestamp"])


def _skip_reason(row: dict) -> str:
    if row["mimetype"] == REVISIT_MIMETYPE:
        return "revisit-only (no body in CDX)"
    return f"best capture status {row['statuscode']}"


def run_inventory(domain: str, dig, client: httpx.Client) -> dict:
    rows = fetch_cdx_rows(domain, client)
    caps = resolve_best(rows)
    dig.write_raw_manifest(rows)
    dig.save_captures(caps)
    pending = sum(1 for c in caps if c["fetch_state"] == "pending")
    return {
        "domain": domain,
        "raw_rows": len(rows),
        "unique_urls": len(caps),
        "pending": pending,
        "skipped": len(caps) - pending,
    }
