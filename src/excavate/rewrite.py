"""Span-based URL scanner/rewriter for HTML and CSS.

A tokenizer, not a re-serializer: it finds the exact character span of every
URL-bearing value and replaces only those spans. Everything else in the
document — whitespace, attribute order, quoting style, broken markup, odd
bytes — passes through untouched.

What a regex over `href=|src=` missed, and this handles:
- unquoted attribute values (`<a href=page.html>`), ubiquitous on 1990s pages
- `srcset`, `background`, `poster`, `<object data>`, `longdesc`, `cite`, ...
- `<meta http-equiv="refresh" content="0; url=...">`
- inline `style="..."`, `<style>` blocks, CSS `@import "..."`
- `<base href>` (reported to the caller, which resolves links against it)
- entity-encoded values (`?a=1&amp;b=2` is the URL `?a=1&b=2`)
and what it must NOT touch: anything inside `<script>`, comments, `<textarea>`,
`<title>`, `<xmp>` or CSS comments — a URL-looking string there is not a link.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass

# attribute name → tags it is a URL on (None = any tag)
URL_ATTRS: dict[str, frozenset[str] | None] = {
    "href": None, "src": None, "action": None, "background": None, "poster": None,
    "longdesc": None, "lowsrc": None, "dynsrc": None, "formaction": None,
    "cite": frozenset({"blockquote", "q", "del", "ins"}),
    "data": frozenset({"object"}),
    "codebase": frozenset({"object", "applet"}),
    "manifest": frozenset({"html"}),
}
SRCSET_ATTRS = frozenset({"srcset", "imagesrcset"})
RAW_TEXT_SKIP = frozenset({"script", "textarea", "title", "xmp"})

_TAG_NAME = re.compile(r"[A-Za-z][^\s/>]*")
_ATTR = re.compile(r"""([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*)))?""")
_CSS_TOKEN = re.compile(
    r"""/\*.*?\*/"""
    r"""|url\(\s*(?P<q>["']?)(?P<url>[^"')]*?)(?P=q)\s*\)"""
    r"""|@import\s+(?P<iq>["'])(?P<iurl>[^"']*)(?P=iq)""",
    re.IGNORECASE | re.DOTALL,
)
_REFRESH = re.compile(r"""^\s*[\d.]+\s*[;,]\s*url\s*=\s*(?P<q>["']?)(?P<url>.*?)(?P=q)\s*$""", re.IGNORECASE | re.DOTALL)
_SRCSET_CANDIDATE = re.compile(r"\s*(?P<url>[^\s,]\S*?)(?P<desc>\s+[^,]*)?\s*(?:,|$)")
_NEEDS_QUOTES = re.compile(r"""[\s"'=<>`]""")
_CSS_NEEDS_QUOTES = re.compile(r"""[\s"'()]""")

# fn(url, flavor) -> replacement, or None to leave the URL alone
UrlFn = Callable[[str, str], str | None]


@dataclass(frozen=True)
class Slot:
    start: int
    end: int
    kind: str    # url | srcset | style | refresh | css | base
    quote: str   # the attribute's quote char ("" = unquoted); unused for css/base


@dataclass(frozen=True)
class Scan:
    text: str
    slots: tuple[Slot, ...]
    base_href: str | None

    def apply(self, fn: UrlFn, base_replacement: str | None = None) -> str:
        """Run `fn` over every URL; rebuild the text touching only changed spans."""
        out: list[str] = []
        cursor = 0
        for slot in self.slots:
            raw = self.text[slot.start:slot.end]
            new_raw = _apply_slot(slot, raw, fn, base_replacement)
            if new_raw is not None and new_raw != raw:
                out.append(self.text[cursor:slot.start])
                out.append(new_raw)
                cursor = slot.end
        out.append(self.text[cursor:])
        return "".join(out)

    def urls(self) -> list[str]:
        found: list[str] = []

        def collect(url: str, flavor: str) -> None:
            found.append(url)

        self.apply(collect)
        return found


def scan(text: str, is_css: bool = False) -> Scan:
    if is_css:
        return Scan(text, (Slot(0, len(text), "css", ""),), None)
    slots: list[Slot] = []
    base_href: str | None = None
    pos, n = 0, len(text)
    while True:
        lt = text.find("<", pos)
        if lt < 0:
            break
        if text.startswith("<!--", lt):
            end = text.find("-->", lt + 4)
            pos = n if end < 0 else end + 3
            continue
        match = _TAG_NAME.match(text, lt + 1)
        if not match:  # </close>, <!doctype>, <?pi?>, or a stray '<'
            pos = lt + 1
            continue
        tag = match.group(0).lower()
        attrs, tag_end = _scan_attrs(text, match.end())
        if tag_end < 0:  # unterminated tag: nothing after it is trustworthy markup
            break
        pos = tag_end
        if tag == "base":
            href = next((a for a in attrs if a[0] == "href"), None)
            if href is not None and base_href is None:
                base_href = html.unescape(text[href[1]:href[2]]).strip()
                slots.append(Slot(lt, tag_end, "base", ""))
            continue
        slots.extend(_attr_slots(tag, attrs, text))
        if tag == "style":
            close = _find_close(text, "style", tag_end)
            slots.append(Slot(tag_end, close, "css", ""))
            pos = close
        elif tag in RAW_TEXT_SKIP:
            pos = _find_close(text, tag, tag_end)
    return Scan(text, tuple(slots), base_href)


def _scan_attrs(text: str, pos: int) -> tuple[list[tuple[str, int, int, str]], int]:
    """Attributes of the tag whose name ended at `pos`: (name, value_start,
    value_end, quote). Returns (attrs, index after '>'), or -1 if unterminated."""
    attrs: list[tuple[str, int, int, str]] = []
    n = len(text)
    while pos < n:
        ch = text[pos]
        if ch == ">":
            return attrs, pos + 1
        if ch.isspace() or ch == "/":
            pos += 1
            continue
        match = _ATTR.match(text, pos)
        if not match or match.end() == pos:
            pos += 1
            continue
        for group, quote in ((2, '"'), (3, "'"), (4, "")):
            if match.group(group) is not None:
                attrs.append((match.group(1).lower(), match.start(group), match.end(group), quote))
                break
        pos = match.end()
    return attrs, -1


def _find_close(text: str, tag: str, pos: int) -> int:
    match = re.compile(rf"</{tag}\b", re.IGNORECASE).search(text, pos)
    return match.start() if match else len(text)


def _attr_slots(tag: str, attrs: list[tuple[str, int, int, str]], text: str) -> list[Slot]:
    slots: list[Slot] = []
    is_refresh = tag == "meta" and any(
        name == "http-equiv" and text[start:end].strip().lower() == "refresh" for name, start, end, _ in attrs)
    for name, start, end, quote in attrs:
        if name in URL_ATTRS:
            tags = URL_ATTRS[name]
            if tags is None or tag in tags:
                slots.append(Slot(start, end, "url", quote))
        elif name in SRCSET_ATTRS:
            slots.append(Slot(start, end, "srcset", quote))
        elif name == "style":
            slots.append(Slot(start, end, "style", quote))
        elif name == "content" and is_refresh:
            slots.append(Slot(start, end, "refresh", quote))
    return slots


def _apply_slot(slot: Slot, raw: str, fn: UrlFn, base_replacement: str | None) -> str | None:
    if slot.kind == "base":
        return base_replacement
    if slot.kind == "css":
        return rewrite_css(raw, fn)
    value = html.unescape(raw)
    if slot.kind == "url":
        new = fn(value.strip(), "")
        new_value = value if new is None else new
    elif slot.kind == "srcset":
        new_value = _rewrite_srcset(value, fn)
    elif slot.kind == "style":
        new_value = rewrite_css(value, fn, flavor=", style attribute")
    else:
        new_value = _rewrite_refresh(value, fn)
    if new_value == value:
        return None
    escaped = html.escape(new_value, quote=True)
    if slot.quote == "" and (escaped == "" or _NEEDS_QUOTES.search(escaped)):
        return f'"{escaped}"'
    return escaped


def rewrite_css(text: str, fn: UrlFn, flavor: str = ", css") -> str:
    def replace(match: re.Match) -> str:
        group = "url" if match.group("url") is not None else "iurl" if match.group("iurl") is not None else None
        if group is None:
            return match.group(0)  # a comment
        url = match.group(group)
        new = fn(url.strip(), flavor)
        if new is None or new == url:
            return match.group(0)
        if group == "url" and not match.group("q") and _CSS_NEEDS_QUOTES.search(new):
            new = '"' + new.replace('"', "%22") + '"'
        offset = match.start()
        whole = match.group(0)
        return whole[:match.start(group) - offset] + new + whole[match.end(group) - offset:]

    return _CSS_TOKEN.sub(replace, text)


def _rewrite_srcset(value: str, fn: UrlFn) -> str:
    def replace(match: re.Match) -> str:
        url = match.group("url")
        new = fn(url, ", srcset")
        if new is None or new == url:
            return match.group(0)
        offset = match.start()
        whole = match.group(0)
        return whole[:match.start("url") - offset] + new.replace(" ", "%20") + whole[match.end("url") - offset:]

    return _SRCSET_CANDIDATE.sub(replace, value)


def _rewrite_refresh(value: str, fn: UrlFn) -> str:
    match = _REFRESH.match(value)
    if not match:
        return value
    url = match.group("url")
    new = fn(url.strip(), ", meta refresh")
    if new is None or new == url:
        return value
    return value[:match.start("url")] + new + value[match.end("url"):]
