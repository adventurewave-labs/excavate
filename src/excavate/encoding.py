"""Charset detection and byte-faithful round-tripping for clean.

Old sites are not UTF-8. A cleaned page must stay in the charset it declares
(otherwise the browser reads UTF-8 bytes as ISO-8859-1 and every accented
character turns to mojibake), and a page that needed no rewrite must come out
byte-identical. So: detect → decode strictly → rewrite → re-encode in the SAME
charset, and only ever touch the bytes of the URLs that were rewritten.

Detection order (WHATWG-shaped, minus content sniffing):
BOM → declared charset (<meta> / CSS @charset) → UTF-8 → windows-1252 →
latin-1 as a byte-transparent last resort. A candidate is accepted only if it
decodes strictly AND re-encodes to the exact original bytes; anything else
falls through, so the worst case is latin-1, where untouched bytes are
guaranteed to survive unchanged.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

# WHATWG label → the codec browsers actually use for it
_LABEL_OVERRIDES = {
    "iso-8859-1": "cp1252", "iso8859-1": "cp1252", "latin1": "cp1252", "l1": "cp1252",
    "ascii": "cp1252", "us-ascii": "cp1252", "ansi_x3.4-1968": "cp1252",
    "shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932", "x-sjis": "cp932",
    "ms_kanji": "cp932", "windows-31j": "cp932",
    "gb2312": "gbk", "x-gbk": "gbk",
    "euc-kr": "cp949", "ks_c_5601-1987": "cp949",
    "utf8": "utf-8", "unicode-1-1-utf-8": "utf-8",
}

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:\-]+)""", re.IGNORECASE)
_CSS_CHARSET = re.compile(rb"""^\s*@charset\s+["']([^"']+)["']""", re.IGNORECASE)
_SNIFF_BYTES = 4096

LAST_RESORT = "latin-1"


@dataclass(frozen=True)
class Decoded:
    text: str
    codec: str          # python codec used for decode AND re-encode
    bom: bytes          # original BOM, re-emitted verbatim
    how: str            # bom | declared | utf-8 | cp1252 | fallback
    note: str | None    # disclosed when the declared charset could not be honoured


def declared_charset(data: bytes, is_css: bool) -> str | None:
    """The charset label the document itself declares, if any (raw label)."""
    head = data[:_SNIFF_BYTES]
    match = (_CSS_CHARSET if is_css else _META_CHARSET).search(head)
    return match.group(1).decode("ascii", "replace").strip().lower() if match else None


def resolve_label(label: str) -> str | None:
    """Python codec name for a charset label; None if unknown or unusable."""
    label = label.strip().lower()
    if label.startswith(("utf-16", "utf16", "ucs-2", "unicode")):
        return None  # a BOM-less "utf-16" declaration read as ASCII is a lie (WHATWG: ignore)
    name = _LABEL_OVERRIDES.get(label, label)
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


def _round_trips(body: bytes, codec: str) -> str | None:
    try:
        text = body.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None
    try:
        return text if text.encode(codec) == body else None
    except UnicodeEncodeError:
        return None


def decode_document(data: bytes, is_css: bool = False) -> Decoded:
    for bom, codec in _BOMS:
        if data.startswith(bom):
            text = _round_trips(data[len(bom):], codec)
            if text is not None:
                return Decoded(text, codec, bom, "bom", None)
    note = None
    label = declared_charset(data, is_css)
    if label:
        resolved = resolve_label(label)
        declared_text = _round_trips(data, resolved) if resolved else None
        if declared_text is not None and resolved is not None:
            return Decoded(declared_text, resolved, b"", "declared", None)
        note = (f"declared charset `{label}` is unknown" if resolved is None
                else f"bytes are not valid `{label}`")
    for codec, how in (("utf-8", "utf-8"), ("cp1252", "cp1252")):
        text = _round_trips(data, codec)
        if text is not None:
            return Decoded(text, codec, b"", how, note)
    # every byte maps to one code point and back: untouched bytes cannot change
    return Decoded(data.decode(LAST_RESORT), LAST_RESORT, b"", "fallback",
                   note or "not valid UTF-8 or windows-1252; handled byte-transparently")


def _css_escape(error: UnicodeError) -> tuple[str, int]:
    if not isinstance(error, UnicodeEncodeError):
        raise error
    return "".join(f"\\{ord(ch):x} " for ch in error.object[error.start:error.end]), error.end


codecs.register_error("excavate-css-escape", _css_escape)


def encode_document(text: str, doc: Decoded, is_css: bool = False) -> bytes:
    """Re-encode in the document's own charset. A rewritten URL may contain a
    character the charset cannot express — it is written as an escape the
    format itself understands (HTML character reference / CSS escape), never
    dropped or replaced with '?'."""
    errors = "excavate-css-escape" if is_css else "xmlcharrefreplace"
    return doc.bom + text.encode(doc.codec, errors=errors)
