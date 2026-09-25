"""Stage 3 — de-Wayback clean: raw/ → site/.

Every rewrite is logged to REWRITES.md (disclosed-deviations model, never
silent). Genuinely missing files stay missing — they surface in the report.
site/ mirrors raw/ exactly (paths derive from raw_path, not from re-deriving
the URL), and the tree is wiped before each full clean so no orphans linger.
"""

from __future__ import annotations

import posixpath
import re
import shutil
from pathlib import Path, PurePosixPath

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


def same_site_host(host: str, domain: str) -> bool:
    """www. variants of the inventory domain are the same site."""
    return host.lower().removeprefix("www.") == domain.lower().removeprefix("www.")


def _normalize(domain: str, url: str) -> tuple[str, str]:
    """Classify a URL: ("rewrite", local_path) or ("skip", unchanged).

    Wayback prefixes are stripped from anything; only same-domain (incl. www
    variant) absolute URLs are localized. Everything else is left untouched.
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
        if not same_site_host(match.group(1), domain):
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

    def sub_chrome(match: re.Match) -> str:
        log.append(f"- wayback chrome removed ({rel_path}): {match.group(0)[:80]}…")
        return "<!-- wayback chrome removed -->"

    if is_css:
        text = CSS_URL.sub(sub_css, text)
    else:
        text = CHROME_ASSETS.sub(sub_chrome, text)
        text = ATTR_URL.sub(sub_attr, text)
        text = CSS_URL.sub(sub_css, text)  # url() inside <style> blocks
    return text, log


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _target_of(domain: str, url: str, pulled: set[str], base_dir: str) -> str | None:
    """Resolve one link to its local target, or None if not a local reference.

    Handles wayback-prefixed, absolute same-site (incl. www), root-relative,
    and page-relative URLs — resolving against the page's directory like a
    browser would. Query strings and anchors are stripped.
    """
    url = url.strip()
    if not url or url.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    url = url.split("#", 1)[0].split("?", 1)[0] or None
    if url is None:
        return None
    kind, value = _normalize(domain, url)
    if kind == "rewrite":
        return _dir_to_index(value, pulled).lstrip("/")
    match = re.match(r"https?://([^/]+)(/.*)?$", url)
    if match:  # external (same-site was consumed by _normalize above)
        return None
    if url.startswith("//"):
        return None  # protocol-relative external
    if url.startswith("/"):
        return _dir_to_index(url, pulled).lstrip("/")
    resolved = posixpath.normpath(posixpath.join(base_dir, url)) if base_dir else posixpath.normpath(url)
    return _dir_to_index("/" + resolved, pulled).lstrip("/")


def internal_targets(text: str, domain: str, pulled: set[str], page_rel: str = "") -> set[str]:
    """Local link targets in a document, resolved the way a browser would."""
    base_dir = posixpath.dirname(page_rel)
    targets: set[str] = set()
    for match in ATTR_URL.finditer(text):
        target = _target_of(domain, match.group("url"), pulled, base_dir)
        if target is not None:
            targets.add(target)
    for match in CSS_URL.finditer(text):
        target = _target_of(domain, match.group("url"), pulled, base_dir)
        if target is not None:
            targets.add(target)
    return targets


def site_rel(capture: dict) -> str:
    """Site path mirrors the raw tree — derived from raw_path when present."""
    raw_path = capture.get("raw_path") or ""
    if raw_path.startswith("raw/"):
        try:
            return Path(raw_path).relative_to("raw").as_posix()
        except ValueError:
            pass  # malformed — fall through to URL-derived path (reads are still gated)
    return safe_relpath(capture["url"])


def _resolve_raw(dig: Dig, capture: dict) -> Path | None:
    """Resolve the raw file, refusing paths that escaped the dig directory."""
    raw_file = (dig.root / capture["raw_path"]).resolve()
    if not raw_file.is_relative_to(dig.root.resolve()):
        return None
    return raw_file


def run_clean(dig: Dig) -> dict:
    domain = dig.domain
    captures = [c for c in dig.load_captures() if c["fetch_state"] == "fetched" and c.get("raw_path")]
    all_log: list[str] = []
    internal: set[str] = set()

    # pre-validate reads: only captures with a real, in-dig raw file contribute
    # to `pulled` — a phantom path here would make links resolve to nothing
    valid: list[dict] = []
    for capture in captures:
        raw_file = _resolve_raw(dig, capture)
        if raw_file is not None and raw_file.is_file():
            valid.append(capture)
        else:
            all_log.append(f"- ERROR: raw file missing or outside dig for `{capture['url']}` — skipped")
    pulled = {site_rel(c) for c in valid}

    # full re-clean: wipe stale output so no orphan survives from earlier runs
    if dig.site_dir.exists():
        shutil.rmtree(dig.site_dir)
    dig.site_dir.mkdir(parents=True, exist_ok=True)

    for capture in valid:
        raw_file = _resolve_raw(dig, capture)
        rel = site_rel(capture)
        target = dig.site_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.lower().endswith(TEXT_SUFFIXES):
            text = _decode(raw_file.read_bytes())
            text, log = clean_text(text, domain, rel, pulled, is_css=rel.lower().endswith(".css"))
            target.write_text(text, encoding="utf-8")
            all_log.extend(log)
            internal |= internal_targets(text, domain, pulled, page_rel=rel)
        else:
            shutil.copyfile(raw_file, target)

        if capture.get("path_collision"):
            canonical = safe_relpath(capture["url"])
            all_log.append(
                f"- path collision: `{capture['url']}` stored at `{rel}` "
                f"(in-page links resolve to the first capture at `{canonical}`)"
            )

    (dig.root / "REWRITES.md").write_text(
        f"# REWRITES — {domain}\n\nEvery URL rewrite applied during clean. Nothing silent.\n\n"
        + "\n".join(all_log) + "\n",
        encoding="utf-8",
    )
    never_crawled = sorted(t for t in internal if t not in pulled and t != "")
    return {"cleaned": len(captures), "rewrites": len(all_log), "never_crawled": never_crawled}
