"""Asset twins — a dead link's surviving equivalent that is ACTUALLY in the dig.

The magpu precedent: every `.mp3` link was dead, but the same songs survived
as `.ogg`. Pointing the link at the survivor recovers the page's function
without inventing anything — the twin is a real archived file, and the swap
is logged with its reason like every other rewrite.

A twin is only ever a file already on disk. Kinds, tried safest-first:

1. case        — same path, different letter case (the original server was
                 case-insensitive; a static host is not)
2. spelling    — same file, extension spelled differently (.htm/.html, .jpg/.jpeg)
3. index       — a directory's other index document (index.htm, default.htm, ...)
4. format      — same name, another format of the same medium (.mp3 → .ogg)

Ambiguity is never guessed through: several equally good candidates of kinds
1–3 mean no swap. Format twins follow a fixed preference order, because
"which of two encodings of the same song" has no wrong answer.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

SPELLINGS: tuple[frozenset[str], ...] = (
    frozenset({"htm", "html"}), frozenset({"jpg", "jpeg", "jpe"}), frozenset({"mid", "midi"}),
    frozenset({"mpg", "mpeg"}), frozenset({"tif", "tiff"}), frozenset({"aif", "aiff"}),
)
# preference order within a medium: most playable in a modern browser first
FORMATS: tuple[tuple[str, ...], ...] = (
    ("mp3", "ogg", "oga", "m4a", "wav", "aif", "aiff", "wma", "ra", "ram", "rm", "au", "mid", "midi"),
    ("mp4", "webm", "ogv", "mov", "mpg", "mpeg", "avi", "wmv", "asf", "qt"),
    ("png", "gif", "jpg", "jpeg", "jpe", "bmp"),
)
INDEX_NAMES = ("index.html", "index.htm", "default.htm", "default.html", "home.html", "home.htm",
               "index.shtml", "index.php", "index.asp")


@dataclass(frozen=True)
class Twin:
    path: str
    kind: str      # case | spelling | index | format
    reason: str


def _split(path: str) -> tuple[str, str]:
    """(path without extension, extension) — both lower-cased."""
    head, name = posixpath.split(path)
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return path.lower(), ""
    return posixpath.join(head, stem).lower(), ext.lower()


class TwinIndex:
    def __init__(self, available: set[str]) -> None:
        self.available = available
        self._by_lower: dict[str, list[str]] = {}
        self._by_stem: dict[str, list[str]] = {}
        self._by_dir: dict[str, list[str]] = {}
        for path in sorted(available):
            self._by_lower.setdefault(path.lower(), []).append(path)
            self._by_stem.setdefault(_split(path)[0], []).append(path)
            self._by_dir.setdefault(posixpath.dirname(path).lower(), []).append(path)

    def find(self, target: str) -> Twin | None:
        """The surviving equivalent of a missing `target`; None if it is on
        disk already or has no unambiguous twin."""
        if target in self.available:
            return None
        same_case = self._by_lower.get(target.lower(), [])
        if len(same_case) == 1:
            return Twin(same_case[0], "case", "same path, different letter case")
        if same_case:
            return None  # two files differ only by case from each other — cannot choose

        stem, ext = _split(target)
        siblings = [p for p in self._by_stem.get(stem, []) if _split(p)[1] != ext]
        for group in SPELLINGS:
            if ext in group:
                spelled = [p for p in siblings if _split(p)[1] in group]
                if len(spelled) == 1:
                    return Twin(spelled[0], "spelling", f".{ext} and .{_split(spelled[0])[1]} are the same file type")
                if spelled:
                    return None

        name = posixpath.basename(target).lower()
        if name in INDEX_NAMES:
            indexes = [p for p in self._by_dir.get(posixpath.dirname(target).lower(), [])
                       if posixpath.basename(p).lower() in INDEX_NAMES]
            if len(indexes) == 1:
                return Twin(indexes[0], "index", "the directory's index document under another name")
            if indexes:
                return None

        for family in FORMATS:
            if ext in family:
                by_ext = {_split(p)[1]: p for p in reversed(siblings) if _split(p)[1] in family}
                for candidate in family:
                    if candidate in by_ext:
                        return Twin(by_ext[candidate], "format",
                                    f"same name in another format (.{ext} → .{candidate})")
        return None
