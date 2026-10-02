"""Stage 4 — VERIFICATION-REPORT.md: completeness accounting.

Counts, failures, magic-byte mismatches, never-crawled targets, and the
never-fake rule: anything missing is listed as missing.
"""

from __future__ import annotations

from collections import Counter

from .clean import REWRITE_LINE, TWIN_LINE, site_rel
from .crawl import accounting_line, display_target, run_check
from .dig import Dig
from .provenance import load_events, record_event, selection_hash

EVENTS_SHOWN = 15


def _ledger_lines(dig: Dig) -> list[str]:
    path = dig.root / "REWRITES.md"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _rewrite_count(dig: Dig) -> int:
    return sum(1 for line in _ledger_lines(dig) if REWRITE_LINE.match(line))


def _referrers(entry: dict) -> str:
    shown = ", ".join(f"`{page}`" for page in entry["referenced_by"])
    extra = entry["referrer_count"] - len(entry["referenced_by"])
    return f"linked from {shown}" + (f" and {extra} more" if extra > 0 else "")


def _crawl_sections(crawl: dict) -> list[str]:
    counts = crawl["counts"]
    if not counts["site_files"]:
        return []
    lines = ["## Crawl check (the site's own links, walked)", "", accounting_line(counts) + ".", ""]
    if crawl["entry_page"] is None:
        lines += ["No entry page (index.html or similar) was recovered — reachability not computed.", ""]
    else:
        lines += [f"From `{crawl['entry_page']}`: {counts['reachable_from_entry']} of {counts['site_files']} files "
                  f"reachable by following links; {counts['orphans']} orphans ({counts['orphan_pages']} pages) — "
                  "recovered files nothing links to. Listed in `crawl.json`.", ""]
    if crawl["never_archived"]:
        lines += ["### Referenced but never captured (never-crawled)", "",
                  "The archive holds no capture of these at all. Listed as missing; nothing is faked.", ""]
        lines += [f"- `{display_target(e['target'])}` — {_referrers(e)}" for e in crawl["never_archived"]] + [""]
    if crawl["archived_not_recovered"]:
        lines += ["### Referenced, known to the archive, not recovered", ""]
        lines += [f"- `{display_target(e['target'])}` — {e['reason']}; {_referrers(e)}"
                  for e in crawl["archived_not_recovered"]] + [""]
    if crawl["case_mismatch"]:
        lines += ["### Links that miss only by letter case (broken on a case-sensitive host)", ""]
        lines += [f"- `{e['target']}` — on disk as `{e['on_disk']}`; {_referrers(e)}" for e in crawl["case_mismatch"]] + [""]
    if crawl["external_hosts"]:
        lines += ["### Hosts the site links out to (not part of this dig)", "",
                  "A subdomain or sibling host here may deserve its own `excavate inventory`.", ""]
        lines += [f"- `{e['host']}` — {e['links']} link{'s' if e['links'] != 1 else ''}" for e in crawl["external_hosts"]] + [""]
    return lines


def run_report(dig: Dig) -> dict:
    captures = dig.load_captures()
    by_state = Counter(c["fetch_state"] for c in captures)
    mismatches = [c for c in captures if c["fetch_state"] == "fetched" and c.get("magic_ok") is False]
    fetched = [c for c in captures if c["fetch_state"] == "fetched"]
    digest_verified = sum(1 for c in fetched if c.get("digest_ok") is True)
    digest_bad = [c for c in fetched if c.get("digest_ok") is False]
    digest_unverifiable = [c for c in fetched if c.get("digest_ok") is None]
    fallbacks = [c for c in fetched if c.get("fallback_from")]
    discovered = [c for c in captures if c.get("discovered_by")]
    selection_at = (dig.meta().get("selection") or {}).get("at")
    permanent = [c for c in captures if c["fetch_state"] == "failed_4xx"]
    transient = [c for c in captures if c["fetch_state"] == "failed_5xx"]
    skipped = [c for c in captures if c["fetch_state"] == "skipped"]
    crawl = run_check(dig, record=False)  # also refreshes crawl.json
    never_crawled = crawl["unresolved"]
    raw_rows = len(dig.raw_manifest_rows()) if dig.raw_manifest_path().exists() else 0
    rewrites = _rewrite_count(dig)
    twin_lines = [line for line in _ledger_lines(dig) if TWIN_LINE.match(line)]

    lines = [
        f"# VERIFICATION REPORT — {dig.domain}",
        "",
        "Completeness accounting for this dig. Missing files are listed as missing; nothing is faked.",
        "",
        f"Capture selection: {'nearest to ' + selection_at if selection_at else 'latest'} 2xx capture per URL.",
        "",
        "## Counts",
        "",
        "| metric | value |",
        "|---|---|",
        f"| raw CDX rows (no collapse/filter) | {raw_rows} |",
        f"| unique URLs | {len(captures)} |",
        f"| — added by `discover` (found by following the site's own links) | {len(discovered)} |",
        f"| fetched | {by_state.get('fetched', 0)} |",
        f"| — via a fallback capture (wanted one was gone) | {len(fallbacks)} |",
        f"| — digest verified against CDX (SHA-1) | {digest_verified} |",
        f"| — digest MISMATCH | {len(digest_bad)} |",
        f"| — digest unverifiable (reason listed) | {len(digest_unverifiable)} |",
        f"| pending (not yet pulled) | {by_state.get('pending', 0)} |",
        f"| failed 4xx (permanent) | {by_state.get('failed_4xx', 0)} |",
        f"| failed 5xx (transient — retry on resume) | {by_state.get('failed_5xx', 0)} |",
        f"| skipped (revisit-only / non-2xx best) | {by_state.get('skipped', 0)} |",
        f"| URL rewrites applied | {rewrites} |",
        f"| — of which links swapped to a surviving twin | {len(twin_lines)} |",
        f"| referenced but not on disk (see crawl check) | {len(never_crawled)} |",
        "",
    ]
    if mismatches:
        lines += ["## Magic-byte mismatches (content-type lied)", ""]
        lines += [f"- `{site_rel(c)}` — body did not match extension (source {c['source']})" for c in mismatches] + [""]
    if fallbacks:
        lines += ["## Fallback captures (the wanted capture was gone; another capture of the same URL was used)", ""]
        lines += [f"- `{c['url']}` — wanted {c['fallback_from']}, used {c['timestamp']}" for c in fallbacks] + [""]
    if digest_bad:
        lines += ["## Integrity — digest mismatches (bytes on disk differ from the archived payload)", ""]
        lines += [f"- `{site_rel(c)}` — {c.get('digest_note')}" for c in digest_bad] + [""]
    if digest_unverifiable:
        reasons = Counter(c.get("digest_note") or "not checked (pulled before digest verification existed)"
                          for c in digest_unverifiable)
        lines += ["## Integrity — unverifiable (not a failure; the comparison was not meaningful)", ""]
        lines += [f"- {count} × {reason}" for reason, count in sorted(reasons.items())] + [""]
    if permanent:
        lines += ["## Failed fetches — permanent (4xx)", ""] + [f"- `{c['url']}` — {c.get('error')}" for c in permanent] + [""]
    if transient:
        lines += ["## Failed fetches — transient (5xx/429/transport; resume retries these)", ""] + [f"- `{c['url']}` — {c.get('error')}" for c in transient] + [""]
    if skipped:
        lines += ["## Skipped (no fetchable best capture)", ""] + [f"- `{c['url']}` — {c.get('skip_reason')}" for c in skipped] + [""]
    if twin_lines:
        lines += ["## Twin swaps (the linked file was never recovered; the link now points at a surviving equivalent)", ""]
        lines += [*twin_lines, ""]
    lines += _crawl_sections(crawl)
    lines += ["## Reconstruction ledger", "", "No files reconstructed by hand in this run."]

    summary = {
        "raw_rows": raw_rows,
        "unique_urls": len(captures),
        "fetched": by_state.get("fetched", 0),
        "pending": by_state.get('pending', 0),
        "failed_permanent": by_state.get("failed_4xx", 0),
        "failed_transient": by_state.get("failed_5xx", 0),
        "skipped": by_state.get("skipped", 0),
        "never_crawled": len(never_crawled),
        "crawl": crawl["counts"],
        "mismatches": len(mismatches),
        "fallbacks": len(fallbacks),
        "discovered": len(discovered),
        "digest_verified": digest_verified,
        "digest_mismatch": len(digest_bad),
        "digest_unverifiable": len(digest_unverifiable),
        "rewrites": rewrites,
        "twin_swaps": len(twin_lines),
    }
    events = load_events(dig)
    lines += ["", "## Chain of custody", "",
              f"{len(events) + 1} recorded events in `events.jsonl` (append-only; one line per stage run). "
              "Per-file source citations: `site/PROVENANCE.json`.", ""]
    if events:
        lines += ["| when (UTC) | stage | tool version | input |", "|---|---|---|---|"]
        lines += [f"| {e['timestamp']} | {e['event_type']} | {e['version']} | `{e['input_hash'][:19]}…` |" for e in events[-EVENTS_SHOWN:]]
        if len(events) > EVENTS_SHOWN:
            lines += ["", f"(last {EVENTS_SHOWN} shown; full history in `events.jsonl`)"]
    (dig.root / "VERIFICATION-REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    record_event(dig, "report", selection_hash(captures),
                 {key: value for key, value in summary.items() if not isinstance(value, dict)})
    return summary
