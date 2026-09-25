"""Stage 3 — de-Wayback clean: raw/ → site/.

Every rewrite is logged to REWRITES.md (disclosed-deviations model, never
silent). Genuinely missing files stay missing — they surface in the report.
site/ mirrors raw/ exactly (paths derive from raw_path, not from re-deriving
the URL), and the tree is wiped before each full clean so no orphans linger.

One mapping function (rewrite_target / local_target → safe_relpath) serves
both the rewriter and the link-graph, so a link's written form and its
analysis target can never disagree.
"""

from __future__ import annotations

import posixpath
import re
import shutil
from pathlib import Path

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
_SKIP_PREFIXES = ("#", "mailto:", "tel:", "javascript:", "data:")

# a "rewrite" proper: `before` → `after` — chrome strips, collision notes and
# ERROR lines are disclosed in the ledger but counted separately
REWRITE_LINE = re.compile(r"^- `.*` → `.*` \(")


def same_site_host(host: str, domain: str) -> bool:
    """www. variants of the inventory domain are the same site."""
    return host.lower().removeprefix("www.") == domain.lower().removeprefix("www.")


def _strip_wayback(url: str) -> str:
    stripped = WAYBACK_PREFIX.sub("", url)
    return WAYBACK_PREFIX_REL.sub("", stripped)


def _classify(domain: str, url: str) -> tuple[str, str] | None:
    """(kind, path) for a URL, or None if not a local reference.

    kind is "absolute" (http(s) same-site, incl. www variant) or "root"
    (starts with /). Page-relative URLs return None from here — the rewriter
    leaves them untouched; the link-graph resolves them against base_dir.
    """
    url = url.strip()
    if not url or url.startswith(_SKIP_PREFIXES):
        return None
    stripped = _strip_wayback(url)
    if stripped != url:
        url = stripped
    match = re.match(r"https?://([^/?#]+)([/?#].*)?$", url)
    if match:
        if not same_site_host(match.group(1), domain):
            return None  # external link
        rest = match.group(2) or "/"
    elif url.startswith("//"):
        return None  # protocol-relative external
    elif not url.startswith("/"):
        return ("relative", url)
    else:
        rest = url
    if rest.startswith(("#", "?")):
        return None
    return ("local", rest)


def rewrite_target(domain: str, url: str) -> str | None:
    """Local path for absolute/wayback/root-absolute links; None = leave alone.

    Page-relative links return None here — they keep working unchanged in the
    mirrored tree.
    """
    classified = _classify(domain, url)
    if classified is None or classified[0] == "relative":
        return None
    return safe_relpath(f"http://{domain}{classified[1]}")


def local_target(domain: str, url: str, base_dir: str = "") -> str | None:
    """Local path for ANY link (the link-graph mapping), resolving
    page-relative URLs against base_dir the way a browser would."""
    classified = _classify(domain, url)
    if classified is None:
        return None
    kind, value = classified
    if kind == "relative":
        joined = posixpath.join(base_dir, value) if base_dir else value
        value = "/" + posixpath.normpath(joined)
    return safe_relpath(f"http://{domain}{value}")


def clean_text(text: str, domain: str, rel_path: str, is_css: bool) -> tuple[str, list[str]]:
    """Rewrite one HTML/CSS document; returns (new_text, log_lines)."""
    log: list[str] = []

    def record(before: str, after: str, flavor: str) -> None:
        if after != before:
            log.append(f"- `{before}` → `{after}` ({rel_path}{flavor})")

    def sub_attr(match: re.Match) -> str:
        url = match.group("url")
        local = rewrite_target(domain, url)
        if local is None:
            return match.group(0)
        record(url, local, "")
        return f'{match.group("attr")}={match.group("q")}{local}{match.group("q")}'

    def sub_css(match: re.Match) -> str:
        url = match.group("url")
        local = rewrite_target(domain, url)
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


def internal_targets(text: str, domain: str, page_rel: str = "") -> set[str]:
    """Local link targets in a document, resolved the way a browser would."""
    base_dir = posixpath.dirname(page_rel)
    targets: set[str] = set()
    for match in ATTR_URL.finditer(text):
        target = local_target(domain, match.group("url"), base_dir)
        if target is not None:
            targets.add(target)
    for match in CSS_URL.finditer(text):
        target = local_target(domain, match.group("url"), base_dir)
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


def effective_pulled(dig: Dig, captures: list[dict]) -> tuple[set[str], list[dict], list[str]]:
    """Paths actually present in raw/ for fetched captures.

    Shared by clean and report so both agree: only captures with a real,
    in-dig raw file count as pulled. Returns (pulled_paths, valid_captures,
    error_lines).
    """
    valid: list[dict] = []
    errors: list[str] = []
    for capture in captures:
        if capture.get("fetch_state") != "fetched" or not capture.get("raw_path"):
            continue
        raw_file = _resolve_raw(dig, capture)
        if raw_file is not None and raw_file.is_file():
            valid.append(capture)
        else:
            errors.append(f"- ERROR: raw file missing or outside dig for `{capture['url']}` — skipped")
    return {site_rel(c) for c in valid}, valid, errors


def run_clean(dig: Dig) -> dict:
    domain = dig.domain
    captures = dig.load_captures()
    all_log: list[str] = []
    internal: set[str] = set()

    pulled, valid, errors = effective_pulled(dig, captures)
    all_log.extend(errors)

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
            text, log = clean_text(text, domain, rel, is_css=rel.lower().endswith(".css"))
            target.write_text(text, encoding="utf-8")
            all_log.extend(log)
            internal |= internal_targets(text, domain, page_rel=rel)
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
    rewrites = sum(1 for line in all_log if REWRITE_LINE.match(line))
    return {"cleaned": len(valid), "rewrites": rewrites, "never_crawled": never_crawled}
