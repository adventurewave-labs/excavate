"""Stage 3 — de-Wayback clean: raw/ → site/.

Every rewrite is logged to REWRITES.md (disclosed-deviations model, never
silent). Genuinely missing files stay missing — they surface in the report.
"""

from __future__ import annotations

import re
import shutil
from pathlib import PurePosixPath

from .dig import Dig, safe_relpath

# /web/<timestamp><mode>/ prefix inside im_-sourced or stale-linked content —
# absolute and path-relative forms
WAYBACK_PREFIX = re.compile(r"https?://web\.archive\.org/web/\d{6,14}(?:id_|im_|js_|cs_)?/")
WAYBACK_PREFIX_REL = re.compile(r"^/web/\d{6,14}(?:id_|im_|js_|cs_)?/")

# Wayback toolbar assets injected into im_ replay pages
CHROME_ASSETS = re.compile(r"<link[^>]+(banner-styles\.css|iconochive\.css)[^>]*>", re.IGNORECASE)

# attribute URLs we rewrite inside HTML; CSS url() handled separately
ATTR_URL = re.compile(r"""(?P<attr>href|src|action)\s*=\s*(?P<q>["'])(?P<url>[^"']*)(?P=q)""")
CSS_URL = re.compile(r"""url\((?P<q>["']?)(?P<url>[^"')]+)(?P=q)\)""")

TEXT_SUFFIXES = (".html", ".htm", ".css")


def _normalize(domain: str, url: str) -> tuple[str, str]:
    """Classify a URL: ("rewrite", local_path) or ("skip", unchanged).

    Wayback prefixes are stripped from anything; only same-domain absolute
    URLs are localized. Everything else (external, mailto, anchors) is left
    untouched.
    """
    url = url.strip()
    if not url or url.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return "skip", url
    stripped = WAYBACK_PREFIX.sub("", url)
    stripped = WAYBACK_PREFIX_REL.sub("", stripped)
    if stripped != url:
        url = stripped
    match = re.match(r"https?://([^/]+)(/.*)?$", url)
    if match:
        if match.group(1).lower() != domain.lower():
            return "skip", url  # external link
        url = match.group(2) or "/"
    if not url.startswith("/"):
        return "skip", url  # already relative
    return "rewrite", url


def _dir_to_index(url: str, pulled: set[str]) -> str:
    """Map directory-style URLs to index.html when the pulled tree has it."""
    if url.endswith("/") or "." not in PurePosixPath(url).name:
        candidate = (url if url.endswith("/") else url + "/") + "index.html"
        if candidate.lstrip("/") in pulled:
            return candidate
    return url


def _localize(domain: str, url: str, pulled: set[str]) -> str | None:
    """Full pipeline for one URL: None = leave alone, else the local target."""
    kind, value = _normalize(domain, url)
    if kind == "skip":
        return None
    return _dir_to_index(value, pulled).lstrip("/")


def clean_text(text: str, domain: str, rel_path: str, pulled: set[str], is_css: bool) -> tuple[str, list[str]]:
    """Rewrite one HTML/CSS document; returns (new_text, log_lines)."""
    log: list[str] = []

    def record(before: str, after: str, flavor: str) -> None:
        if after != before:
            log.append(f"- `{before}` → `{after}` ({rel_path}{flavor})")

    def sub_attr(match: re.Match) -> str:
        url = match.group("url")
        local = _localize(domain, url, pulled)
        if local is None:
            return match.group(0)
        record(url, local, "")
        return f'{match.group("attr")}={match.group("q")}{local}{match.group("q")}'

    def sub_css(match: re.Match) -> str:
        url = match.group("url")
        local = _localize(domain, url, pulled)
        if local is None:
            return match.group(0)
        record(url, local, ", css")
        return f"url({match.group('q')}{local}{match.group('q')})"

    if is_css:
        text = CSS_URL.sub(sub_css, text)
    else:
        text = CHROME_ASSETS.sub(lambda m: _chrome_removal(m, rel_path, log) or "<!-- wayback chrome removed -->", text)
        text = ATTR_URL.sub(sub_attr, text)
        text = CSS_URL.sub(sub_css, text)  # url() inside <style> blocks
    return text, log


def _chrome_removal(match: re.Match, rel_path: str, log: list[str]) -> str:
    log.append(f"- wayback chrome removed ({rel_path}): {match.group(0)[:80]}…")
    return "<!-- wayback chrome removed -->"


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def internal_targets(text: str, domain: str, pulled: set[str]) -> set[str]:
    """Local link targets in a cleaned document, normalized exactly like clean_text."""
    targets: set[str] = set()
    for match in ATTR_URL.finditer(text):
        local = _localize(domain, match.group("url"), pulled)
        if local is not None:
            targets.add(local)
    for match in CSS_URL.finditer(text):
        local = _localize(domain, match.group("url"), pulled)
        if local is not None:
            targets.add(local)
    return targets


def run_clean(dig: Dig) -> dict:
    domain = dig.domain
    captures = [c for c in dig.load_captures() if c["fetch_state"] == "fetched" and c.get("raw_path")]
    pulled = {safe_relpath(c["url"]) for c in captures}
    all_log: list[str] = []
    internal: set[str] = set()

    for capture in captures:
        raw_file = dig.root / capture["raw_path"]
        rel = safe_relpath(capture["url"])
        target = dig.site_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.lower().endswith(TEXT_SUFFIXES):
            text = _decode(raw_file.read_bytes())
            text, log = clean_text(text, domain, rel, pulled, is_css=rel.lower().endswith(".css"))
            target.write_text(text, encoding="utf-8")
            all_log.extend(log)
            internal |= internal_targets(text, domain, pulled)
        else:
            shutil.copyfile(raw_file, target)

    (dig.root / "REWRITES.md").write_text(
        f"# REWRITES — {domain}\n\nEvery URL rewrite applied during clean. Nothing silent.\n\n"
        + "\n".join(all_log) + "\n",
        encoding="utf-8",
    )
    never_crawled = sorted(t for t in internal if t not in pulled and t not in ("",))
    return {"cleaned": len(captures), "rewrites": len(all_log), "never_crawled": never_crawled}
