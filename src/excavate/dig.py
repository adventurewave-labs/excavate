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

import csv
import hashlib
import json
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
    def create(cls, base: Path, domain: str) -> "Dig":
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
    def latest(cls, base: Path) -> "Dig":
        base = Path(base)
        candidates = [p for p in base.iterdir() if (p / "captures.json").exists()] if base.exists() else []
        if not candidates:
            raise SystemExit(f"no digs found under {base}")
        return cls(max(candidates, key=lambda p: (p / "captures.json").stat().st_mtime))

    # -- meta / manifest -----------------------------------------------------
    def meta(self) -> dict:
        return json.loads((self.root / "meta.json").read_text())

    def save_meta(self, meta: dict) -> None:
        (self.root / "meta.json").write_text(json.dumps(meta, indent=2))

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
        (self.root / "captures.json").write_text(json.dumps(caps, indent=2))


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def safe_relpath(url: str) -> str:
    """Map a URL to a filesystem-safe relative path under raw/ or site/.

    Directory URLs (trailing slash or extensionless) map to <dir>/index.html —
    the spec's directory→index rule.
    """
    path = unquote(urlsplit(url).path).lstrip("/")
    if not path:
        return "index.html"
    if path.endswith("/"):
        return path + "index.html"
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return path + "/index.html"
    # strip anything path-hostile; keep unicode filenames as-is on disk
    return path
