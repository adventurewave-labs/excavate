"""Wayback Machine chrome — everything replay injects that the site never had.

`id_` replay returns the archived bytes untouched, but `im_` (our fallback)
and any page that was itself saved from a replay view carry Wayback's
furniture: the toolbar block, the wombat/playback script includes and their
init call, banner stylesheets, and the "FILE ARCHIVED ON" trailer comments.
A resurrected site must not ship any of it — the scripts point at
`/_static/` paths that only exist on web.archive.org.

Every removal is returned as a log line (the disclosed-deviations rule).
Patterns are deliberately narrow: each one names a Wayback-only marker, so
original site markup can never match by accident.
"""

from __future__ import annotations

import re

MARKER = "<!-- wayback chrome removed -->"

_HOST = r"""(?:(?:https?:)?//web\.archive\.org)?"""
# pre-2016 replay served its includes from /static/ — a path real sites use
# too, so those are matched by exact file name AND only in a document that
# carries an unmistakable Wayback marker elsewhere
_LEGACY_NAMES = r"(?:analytics|disclaim-element|graph-calc|timestamp|toolbar|auto-complete)\.js"
_STRONG_MARKERS = ("archive_analytics.values", "WAYBACK TOOLBAR INSERT", "FILE ARCHIVED ON", "__wm.")

_TOOLBAR = re.compile(
    # spliced in directly after <body ...> — the newline that follows is the page's own
    r"<!--\s*BEGIN WAYBACK TOOLBAR INSERT\s*-->.*?<!--\s*END WAYBACK TOOLBAR INSERT\s*-->",
    re.IGNORECASE | re.DOTALL)
_SCRIPT_SRC = re.compile(
    rf"""<script\b[^>]*\bsrc\s*=\s*["']?{_HOST}/_static/js/[^"'\s>]*["']?[^>]*>\s*</script>[ \t]*\r?\n?""",
    re.IGNORECASE)
_SCRIPT_SRC_LEGACY = re.compile(
    rf"""<script\b[^>]*\bsrc\s*=\s*["']?{_HOST}/static/js/{_LEGACY_NAMES}[^"'\s>]*["']?[^>]*>\s*</script>[ \t]*\r?\n?""",
    re.IGNORECASE)
_INLINE_SCRIPT = re.compile(r"<script\b(?P<attrs>[^>]*)>(?P<body>(?:(?!</script).)*)</script>[ \t]*\r?\n?",
                            re.IGNORECASE | re.DOTALL)
_INLINE_MARKERS = ("__wm.init(", "__wm.wombat(", "archive_analytics.values", "__wm.rw(",
                   "window.RufflePlayer.config", "_____WB$wombat$")
_INLINE_MAX = 4000  # injected init snippets are tiny; never eat a large real script
_LINK = re.compile(
    # `_static/` anywhere in the href is Wayback's (it survives being wrapped in
    # a replay prefix); the legacy `/static/css/` form must be root-anchored
    rf"""<link\b[^>]*\bhref\s*=\s*["']?(?:[^"'\s>]*/_static/(?:css/)?|{_HOST}/static/css/)"""
    rf"""(?:banner-styles|iconochive)\.css[^"'\s>]*["']?[^>]*>[ \t]*\r?\n?""",
    re.IGNORECASE)
_END_INCLUDE = re.compile(r"<!--\s*End Wayback Rewrite JS Include\s*-->[ \t]*\r?\n?", re.IGNORECASE)
_TRAILER_HTML = re.compile(
    r"<!--\s*(?:FILE ARCHIVED ON\b|playback timings \(ms\):).*?-->[ \t]*\r?\n?", re.IGNORECASE | re.DOTALL)
_TRAILER_CSS = re.compile(
    r"/\*\s*(?:FILE ARCHIVED ON\b|playback timings \(ms\):).*?\*/[ \t]*\r?\n?", re.IGNORECASE | re.DOTALL)


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= 80 else flat[:79] + "…"


def strip_chrome(text: str, is_css: bool = False) -> tuple[str, list[str]]:
    """Remove Wayback-injected markup. Returns (text, [what was removed])."""
    removed: list[str] = []

    def drop(kind: str):
        def replace(match: re.Match) -> str:
            removed.append(f"{kind}: {_snippet(match.group(0))}")
            # one visible marker per document (where the first piece of chrome
            # sat) — the rest vanish without leaving a trail of comments
            return MARKER if len(removed) == 1 and not is_css else ""
        return replace

    if is_css:
        return _TRAILER_CSS.sub(drop("archive trailer comment"), text), removed

    legacy = any(marker in text for marker in _STRONG_MARKERS)
    text = _TOOLBAR.sub(drop("toolbar block"), text)
    if legacy:
        text = _SCRIPT_SRC_LEGACY.sub(drop("replay script include"), text)
    text = _SCRIPT_SRC.sub(drop("replay script include"), text)

    inline = drop("replay init script")

    def drop_inline(match: re.Match) -> str:
        body = match.group("body")
        if len(body) <= _INLINE_MAX and any(marker in body for marker in _INLINE_MARKERS):
            return inline(match)
        return match.group(0)

    text = _INLINE_SCRIPT.sub(drop_inline, text)
    text = _LINK.sub(drop("banner stylesheet"), text)
    text = _END_INCLUDE.sub(drop("include end marker"), text)
    text = _TRAILER_HTML.sub(drop("archive trailer comment"), text)
    return text, removed
