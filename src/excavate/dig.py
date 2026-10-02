"""Dig directory model — where a dig's state lives on disk.

Layout (spec: docs/specs/M0-resurrection-cli.md):

    digs/<domain>/
      manifest.csv            # raw CDX rows, immutable (no collapse, no filters)
      captures.json           # resolved best capture per URL + fetch state (resumable)
      raw/                    # fetched bytes, URL-mapped
      site/                   # cleaned output
      REWRITES.md
      VERIFICATION-REPORT.md
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

FETCH_STATES = ("pending", "fetched", "failed_4xx", "failed_5xx", "skipped")


class Dig:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.site_dir = self.root / "site"

    # -- lifecycle -----------------------------------------------------------
    @classmethod
    def create(cls, base: Path, domain: str) -> Dig:
        root = Path(base) / domain
        if root.exists() and (root / "captures.json").exists():
            return cls(root)  # resuming an existing dig is the normal path
        root.mkdir(parents=True, exist_ok=True)
        (root / "raw").mkdir(exist_ok=True)
        (root / "site").mkdir(exist_ok=True)
        dig = cls(root)
        dig.save_meta({"domain": domain})
        return dig

    @classmethod
    def latest(cls, base: Path) -> Dig:
        base = Path(base)
        candidates = [p for p in base.iterdir() if (p / "captures.json").exists()] if base.exists() else []
        if not candidates:
            raise SystemExit(f"no digs found under {base}")
        return cls(max(candidates, key=lambda p: (p / "captures.json").stat().st_mtime))

    # -- meta / manifest -----------------------------------------------------
    def meta(self) -> dict:
        data = json.loads((self.root / "meta.json").read_text())
        if "domain" not in data:
            raise SystemExit(f"dig state corrupt: meta.json has no domain — {self.root}")
        return data

    def save_meta(self, meta: dict) -> None:
        _atomic_write(self.root / "meta.json", json.dumps(meta, indent=2))

    @property
    def domain(self) -> str:
        return self.meta()["domain"]

    def raw_manifest_path(self) -> Path:
        return self.root / "manifest.csv"

    def write_raw_manifest(self, rows: list[dict]) -> None:
        if not rows:
            raise SystemExit("CDX returned zero rows — refusing to write an empty manifest")
        with self.raw_manifest_path().open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def raw_manifest_rows(self) -> list[dict]:
        with self.raw_manifest_path().open(newline="") as fh:
            return list(csv.DictReader(fh))

    # -- captures (the resumable working set) ---------------------------------
    def load_captures(self) -> list[dict]:
        return json.loads((self.root / "captures.json").read_text())

    def save_captures(self, caps: list[dict]) -> None:
        _atomic_write(self.root / "captures.json", json.dumps(caps, indent=2))


def _atomic_write(path: Path, text: str) -> None:
    """Write-then-rename: a kill mid-write leaves the previous state intact,
    never a truncated captures.json (the resume contract depends on it)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sha1_base32(data: bytes) -> str:
    """The CDX `digest` encoding: base32 of the SHA-1 of the archived payload."""
    return base64.b32encode(hashlib.sha1(data).digest()).decode("ascii")


def safe_relpath(url: str) -> str:
    """Map a URL to a filesystem-safe relative path.

    CDX `original` is attacker-controllable (anyone can get an arbitrary URL
    archived), so traversal/dot segments and control bytes are stripped, and
    path segments are capped well under filesystem limits (255 bytes/segment).
    The result is always a plain relative path inside the target directory.
    """
    path = unquote(urlsplit(url).path)
    path = "".join(ch for ch in path if ord(ch) >= 32 and ch != "\x7f")
    parts = [seg for seg in path.split("/") if seg not in ("", ".", "..")]
    parts = [_shorten_segment(seg) for seg in parts]
    query = urlsplit(url).query
    if query and parts:
        # distinct query strings are distinct resources — fold a query hash
        # into the filename instead of silently collapsing them
        digest = hashlib.sha1(query.encode()).hexdigest()[:8]
        last = parts[-1]
        stem, dot, ext = last.rpartition(".")
        parts[-1] = f"{stem}-{digest}.{ext}" if dot and len(ext) <= 10 else f"{last}-{digest}"
    path = "/".join(parts)
    if not path:
        return "index.html"
    if "." not in path.rsplit("/", 1)[-1]:
        path += "/index.html"
    return path


def _shorten_segment(segment: str, limit: int = 180) -> str:
    """Cap one path segment, preserving a short extension and disambiguating
    the truncation with a content hash (two long names must not collide)."""
    if len(segment) <= limit:
        return segment
    digest = hashlib.sha1(segment.encode()).hexdigest()[:8]
    stem, dot, ext = segment.rpartition(".")
    if dot and len(ext) <= 10:
        return f"{stem[: limit - 11 - len(ext)]}-{digest}.{ext}"
    return f"{segment[: limit - 9]}-{digest}"
