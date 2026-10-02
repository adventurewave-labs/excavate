"""Dig directory model — where a dig's state lives on disk.

Layout (spec: docs/specs/M0-resurrection-cli.md):

    digs/<domain>/
      manifest.csv            # raw CDX rows, immutable (no collapse, no filters)
      dig.sqlite              # the working state: one row per URL (resumable, kill-safe)
      captures.json           # human-readable export of that state (refreshed per command)
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
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote, urlsplit

MANIFEST_FIELDS = ("url", "timestamp", "status", "mimetype", "digest", "sha1_fetched", "fetch_state")
SCHEMA_VERSION = 1
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
        if root.exists() and cls(root).has_state():
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
        candidates = [cls(p) for p in base.iterdir() if cls(p).has_state()] if base.exists() else []
        if not candidates:
            raise SystemExit(f"no digs found under {base}")
        return max(candidates, key=lambda dig: dig._state_mtime())

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

    def append_raw_manifest(self, rows: list[dict]) -> None:
        """Add rows from a later lookup (discover) — the manifest stays the
        complete audit trail of every CDX row this dig was built from."""
        if not rows:
            return
        path = self.raw_manifest_path()
        if not path.exists():
            self.write_raw_manifest(rows)
            return
        with path.open(newline="") as fh:
            fieldnames = next(csv.reader(fh))
        with path.open("a", newline="") as fh:
            csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore").writerows(rows)

    def raw_manifest_rows(self) -> list[dict]:
        with self.raw_manifest_path().open(newline="") as fh:
            return list(csv.DictReader(fh))

    def write_manifest_json(self, caps: list[dict]) -> None:
        """manifest.json — the M0 spec's machine-readable manifest: one entry
        per URL with exactly the spec's fields, plus what was asked of CDX.
        A snapshot export; captures.json remains the working state."""
        meta = self.meta()
        payload = {
            "domain": meta["domain"],
            "query": meta.get("query"),
            "inventoried_at": meta.get("inventoried_at"),
            "raw_rows": meta.get("raw_rows"),
            "captures": [{field: cap.get(field) for field in MANIFEST_FIELDS} for cap in caps],
        }
        _atomic_write(self.root / "manifest.json", json.dumps(payload, indent=2))

    # -- captures (the resumable working set) ---------------------------------
    #
    # State lives in dig.sqlite (WAL): pull updates ONE row per fetched URL.
    # Rewriting a whole captures.json after every row was O(n²) — fine at 900
    # URLs, hours of pure disk churn at 100k. captures.json is still written,
    # as an export, at the end of every command that changes state.
    #
    # If captures.json has been changed by something other than our own export
    # (hand-edited to force a re-fetch, or written by an older excavate), the
    # JSON wins and is re-imported — the file a human can see is never ignored.

    @property
    def db_path(self) -> Path:
        return self.root / "dig.sqlite"

    @property
    def json_path(self) -> Path:
        return self.root / "captures.json"

    def has_state(self) -> bool:
        return self.db_path.exists() or self.json_path.exists()

    def _state_mtime(self) -> float:
        return max(p.stat().st_mtime for p in (self.db_path, self.json_path) if p.exists())

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS captures ("
                           "url TEXT PRIMARY KEY, position INTEGER NOT NULL, fetch_state TEXT NOT NULL, data TEXT NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS captures_state ON captures (fetch_state)")
                db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return db

    def _json_changed_externally(self, db: sqlite3.Connection) -> bool:
        if not self.json_path.exists():
            return False
        row = db.execute("SELECT value FROM kv WHERE key = 'export_sha1'").fetchone()
        return row is None or row[0] != sha1_hex(self.json_path.read_bytes())

    def _replace_all(self, db: sqlite3.Connection, caps: list[dict]) -> None:
        with db:
            db.execute("DELETE FROM captures")
            db.executemany("INSERT INTO captures (url, position, fetch_state, data) VALUES (?, ?, ?, ?)",
                           [(c["url"], i, c["fetch_state"], json.dumps(c)) for i, c in enumerate(caps)])

    def _export(self, db: sqlite3.Connection) -> None:
        caps = [json.loads(row[0]) for row in db.execute("SELECT data FROM captures ORDER BY position")]
        text = json.dumps(caps, indent=2)
        _atomic_write(self.json_path, text)
        with db:
            db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('export_sha1', ?)", (sha1_hex(text.encode()),))

    def load_captures(self) -> list[dict]:
        if not self.has_state():
            raise FileNotFoundError(self.json_path)
        with closing(self._connect()) as db:
            if self._json_changed_externally(db):
                self._replace_all(db, json.loads(self.json_path.read_text()))
                self._export(db)
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM captures ORDER BY position")]

    def save_captures(self, caps: list[dict]) -> None:
        """Replace the whole working set (inventory, migration) and export it."""
        with closing(self._connect()) as db:
            self._replace_all(db, caps)
            self._export(db)

    def save_capture(self, cap: dict) -> None:
        """Persist ONE row — what pull does after every fetch. Constant cost
        regardless of dig size; no JSON export (see export_captures_json)."""
        with closing(self._connect()) as db, db:
            updated = db.execute("UPDATE captures SET fetch_state = ?, data = ? WHERE url = ?",
                                 (cap["fetch_state"], json.dumps(cap), cap["url"])).rowcount
            if not updated:
                position = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM captures").fetchone()[0]
                db.execute("INSERT INTO captures (url, position, fetch_state, data) VALUES (?, ?, ?, ?)",
                           (cap["url"], position, cap["fetch_state"], json.dumps(cap)))

    def export_captures_json(self) -> None:
        with closing(self._connect()) as db:
            self._export(db)

    def state_counts(self) -> dict[str, int]:
        """Rows per fetch_state, straight from the index — no need to load the dig."""
        self.load_captures()  # migrates / re-imports first if needed
        with closing(self._connect()) as db:
            counts = dict(db.execute("SELECT fetch_state, COUNT(*) FROM captures GROUP BY fetch_state"))
        return {state: counts.get(state, 0) for state in FETCH_STATES}


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
