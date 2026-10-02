"""Stage 4 — crawl-check: let the site's own navigation audit the dig.

The CDX inventory says what the archive holds; the pages say what the site
actually used. Walking every internal link of the cleaned site answers the
questions an inventory cannot:

- which referenced files were recovered, and which were not;
- of the missing ones, which the archive knows about but could not deliver
  (failed / skipped / still pending — a retry or another capture might help)
  versus which it never captured at all (gone for good);
- which links only miss by letter case (fine on the original Windows server,
  broken on any case-sensitive host);
- which recovered files nothing links to (orphans — reachable only by URL).

Nothing here modifies the site. `never-crawled` keeps its M0 meaning — every
referenced target that is not on disk — and is broken down, not redefined.
"""

from __future__ import annotations

import json
import posixpath
from collections import deque

from .clean import _decode, internal_targets, site_rel
from .dig import Dig, _atomic_write, safe_relpath

PAGE_SUFFIXES = (".html", ".htm")
LINKING_SUFFIXES = (*PAGE_SUFFIXES, ".css")
ENTRY_CANDIDATES = ("index.html", "index.htm", "default.htm", "default.html", "home.html", "home.htm")
REFERRERS_SHOWN = 3


def _site_files(dig: Dig) -> set[str]:
    if not dig.site_dir.exists():
        return set()
    return {p.relative_to(dig.site_dir).as_posix() for p in dig.site_dir.rglob("*") if p.is_file()}


def _link_graph(dig: Dig, files: set[str]) -> dict[str, set[str]]:
    """page → internal targets it references (HTML and CSS only carry links)."""
    domain = dig.domain
    graph: dict[str, set[str]] = {}
    for rel in sorted(files):
        if rel.lower().endswith(LINKING_SUFFIXES):
            data = (dig.site_dir / rel).read_bytes()
            targets = internal_targets(_decode(data, rel.lower().endswith(".css")), domain, page_rel=rel)
            graph[rel] = {t for t in targets if t and t != rel}
    return graph


def _manifest_index(captures: list[dict]) -> dict[str, dict]:
    """site path → capture, for every URL the archive knows about."""
    index: dict[str, dict] = {}
    for capture in captures:
        for path in {safe_relpath(capture["url"]), site_rel(capture)}:
            index.setdefault(path, capture)
    return index


def _why(capture: dict) -> str:
    state = capture["fetch_state"]
    if state == "skipped":
        return f"skipped — {capture.get('skip_reason')}"
    if state == "pending":
        return "pending — not pulled yet"
    if state == "fetched":
        return "marked fetched but its file is not in site/ (re-run pull, then clean)"
    kind = "permanent" if state == "failed_4xx" else "transient, retried on resume"
    return f"fetch failed ({kind}) — {capture.get('error')}"


def analyze(dig: Dig, captures: list[dict] | None = None) -> dict:
    captures = dig.load_captures() if captures is None else captures
    files = _site_files(dig)
    graph = _link_graph(dig, files)
    manifest = _manifest_index(captures)
    lower_files: dict[str, str] = {}
    for rel in sorted(files):
        lower_files.setdefault(rel.lower(), rel)

    referrers: dict[str, list[str]] = {}
    for page, targets in graph.items():
        for target in targets:
            referrers.setdefault(target, []).append(page)

    recovered: list[str] = []
    case_mismatch: list[dict] = []
    archived_not_recovered: list[dict] = []
    never_archived: list[dict] = []
    for target in sorted(referrers):
        pages = sorted(referrers[target])
        entry: dict = {"target": target, "referenced_by": pages[:REFERRERS_SHOWN], "referrer_count": len(pages)}
        if target in files:
            recovered.append(target)
        elif target.lower() in lower_files:
            case_mismatch.append({**entry, "on_disk": lower_files[target.lower()]})
        elif target in manifest:
            capture = manifest[target]
            archived_not_recovered.append({**entry, "url": capture["url"], "fetch_state": capture["fetch_state"],
                                           "reason": _why(capture)})
        else:
            never_archived.append(entry)

    entry_page = next((name for name in ENTRY_CANDIDATES if name in files), None)
    reachable: set[str] = set()
    if entry_page is not None:
        queue = deque([entry_page])
        reachable.add(entry_page)
        while queue:
            for target in graph.get(queue.popleft(), ()):
                if target in files and target not in reachable:
                    reachable.add(target)
                    queue.append(target)
    orphans = sorted(files - reachable) if entry_page is not None else []

    unresolved = sorted(e["target"] for e in (*case_mismatch, *archived_not_recovered, *never_archived))
    return {
        "domain": dig.domain,
        "entry_page": entry_page,
        "counts": {
            "site_files": len(files),
            "pages": sum(1 for rel in files if rel.lower().endswith(PAGE_SUFFIXES)),
            "referenced": len(referrers),
            "recovered": len(recovered),
            "case_mismatch": len(case_mismatch),
            "archived_not_recovered": len(archived_not_recovered),
            "never_archived": len(never_archived),
            "unresolved": len(unresolved),
            "reachable_from_entry": len(reachable),
            "orphans": len(orphans),
            "orphan_pages": sum(1 for rel in orphans if rel.lower().endswith(PAGE_SUFFIXES)),
        },
        "unresolved": unresolved,
        "case_mismatch": case_mismatch,
        "archived_not_recovered": archived_not_recovered,
        "never_archived": never_archived,
        "orphans": orphans,
    }


def run_check(dig: Dig) -> dict:
    """Analyze the cleaned site and write crawl.json next to it."""
    result = analyze(dig)
    _atomic_write(dig.root / "crawl.json", json.dumps(result, indent=2))
    return result


def accounting_line(counts: dict) -> str:
    """The strops-style one-liner: referenced / recovered / not recovered."""
    return (f"{counts['referenced']} referenced / {counts['recovered']} recovered / "
            f"{counts['archived_not_recovered']} archived but not recovered / "
            f"{counts['never_archived']} never archived / {counts['case_mismatch']} case mismatch")


def display_target(target: str) -> str:
    # a trailing-slash target could never match a file by construction;
    # show what would have to exist for it to resolve
    return posixpath.join(target, "index.html") if target.endswith("/") else target
