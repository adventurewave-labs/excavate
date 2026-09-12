"""Stage 4 — VERIFICATION-REPORT.md: completeness accounting.

Counts, failures, magic-byte mismatches, never-crawled targets, and the
never-fake rule: anything missing is listed as missing.
"""

from __future__ import annotations

import re
from collections import Counter

from .clean import _decode, internal_targets
from .dig import Dig, safe_relpath

REWRITE_LINE = re.compile(r"^- `.*` → `.*` \(")


def _never_crawled(dig: Dig, captures: list[dict]) -> list[str]:
    pulled = {safe_relpath(c["url"]) for c in captures}
    targets: set[str] = set()
    for page in dig.site_dir.rglob("*"):
        if page.suffix.lower() in (".html", ".htm"):
            targets |= internal_targets(_decode(page.read_bytes()), dig.domain, pulled)
    return sorted(t for t in targets if t not in pulled and t != "")


def _rewrite_count(dig: Dig) -> int:
    path = dig.root / "REWRITES.md"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text().splitlines() if REWRITE_LINE.match(line))


def run_report(dig: Dig) -> dict:
    captures = dig.load_captures()
    by_state = Counter(c["fetch_state"] for c in captures)
    mismatches = [c for c in captures if c["fetch_state"] == "fetched" and c.get("magic_ok") is False]
    failures = [c for c in captures if c["fetch_state"].startswith("failed_")]
    skipped = [c for c in captures if c["fetch_state"] == "skipped"]
    never_crawled = _never_crawled(dig, captures) if dig.site_dir.exists() else []
    raw_rows = len(dig.raw_manifest_rows()) if dig.raw_manifest_path().exists() else 0
    rewrites = _rewrite_count(dig)

    lines = [
        f"# VERIFICATION REPORT — {dig.domain}",
        "",
        "Completeness accounting for this dig. Missing files are listed as missing; nothing is faked.",
        "",
        "## Counts",
        "",
        "| metric | value |",
        "|---|---|",
        f"| raw CDX rows (no collapse/filter) | {raw_rows} |",
        f"| unique URLs | {len(captures)} |",
        f"| fetched | {by_state.get('fetched', 0)} |",
        f"| pending (not yet pulled) | {by_state.get('pending', 0)} |",
        f"| failed 4xx | {by_state.get('failed_4xx', 0)} |",
        f"| failed 5xx | {by_state.get('failed_5xx', 0)} |",
        f"| skipped (revisit-only / non-2xx best) | {by_state.get('skipped', 0)} |",
        f"| URL rewrites applied | {rewrites} |",
        f"| referenced but never captured | {len(never_crawled)} |",
        "",
    ]
    if mismatches:
        lines += ["## Magic-byte mismatches (content-type lied)", ""]
        lines += [f"- `{safe_relpath(c['url'])}` — body did not match extension (source {c['source']})" for c in mismatches] + [""]
    if failures:
        lines += ["## Failed fetches", ""] + [f"- `{c['url']}` — {c.get('error')}" for c in failures] + [""]
    if skipped:
        lines += ["## Skipped (no fetchable best capture)", ""] + [f"- `{c['url']}` — {c.get('skip_reason')}" for c in skipped] + [""]
    if never_crawled:
        lines += ["## Referenced but never captured (never-crawled)", ""] + [f"- `{t}`" for t in never_crawled] + [""]
    lines += ["## Reconstruction ledger", "", "No files reconstructed by hand in this run."]

    (dig.root / "VERIFICATION-REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return {
        "raw_rows": raw_rows,
        "unique_urls": len(captures),
        "fetched": by_state.get("fetched", 0),
        "pending": by_state.get("pending", 0),
        "failed": by_state.get("failed_4xx", 0) + by_state.get("failed_5xx", 0),
        "skipped": by_state.get("skipped", 0),
        "never_crawled": len(never_crawled),
        "mismatches": len(mismatches),
        "rewrites": rewrites,
    }
