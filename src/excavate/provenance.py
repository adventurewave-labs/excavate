"""Provenance — chain of custody for a dig (PRD FR-E9).

Two records, both plain files that outlive this tool:

`events.jsonl` (dig root) — a PREMIS-lite event log. One line per stage run:
who acted (`agent`), with what (`tool`, `version`), what kind of act
(`event_type`), when, on which input (`input_hash`), and how it came out.
Append-only: re-running a stage adds a line, it never edits history. It is
what makes a dig reproducible and auditable after the fact.

`site/PROVENANCE.json` — per-file citations. Every file in the rebuilt site
maps to the URL it was, the capture it came from (with a Wayback link anyone
can open), the archive's digest for that capture, how it was fetched, and
whether the bytes were modified by cleaning. It ships with the site, so a
visitor — not just the operator — can check where any page came from.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .dig import Dig, _atomic_write

AGENT = "excavate"
SITE_PROVENANCE = "PROVENANCE.json"
EVENT_TYPES = ("inventory", "pull", "clean", "check", "discover", "report", "scan", "export")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lines_hash(lines: Iterable[str]) -> str:
    """Order-independent hash of a set of facts (one per line)."""
    digest = hashlib.sha256()
    for line in sorted(lines):
        digest.update(line.encode("utf-8", "surrogateescape"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def tree_hash(root: Path, exclude: tuple[str, ...] = ()) -> str:
    """Hash of a directory's contents: every file's path and sha256."""
    if not root.exists():
        return lines_hash([])
    return lines_hash(
        f"{path.relative_to(root).as_posix()} {sha256_hex(path.read_bytes())}"
        for path in root.rglob("*") if path.is_file() and path.relative_to(root).as_posix() not in exclude
    )


def selection_hash(captures: list[dict]) -> str:
    """What a pull is asked to fetch: each URL and the capture chosen for it."""
    return lines_hash(f"{c['url']} {c.get('timestamp', '')}" for c in captures)


def raw_hash(captures: list[dict]) -> str:
    """What clean reads: each fetched file and the sha1 recorded when it was pulled."""
    return lines_hash(f"{c.get('raw_path')} {c.get('sha1_fetched')}" for c in captures if c.get("raw_path"))


def events_path(dig: Dig) -> Path:
    return dig.root / "events.jsonl"


def record_event(dig: Dig, event_type: str, input_hash: str, outcome: dict, tool: str | None = None) -> dict:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type!r}")
    event = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "agent": AGENT,
        "tool": tool or f"excavate.{event_type}",
        "version": __version__,
        "event_type": event_type,
        "input_hash": input_hash,
        "outcome": outcome,
    }
    with events_path(dig).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def load_events(dig: Dig) -> list[dict]:
    path = events_path(dig)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def wayback_url(capture: dict) -> str:
    return f"https://web.archive.org/web/{capture.get('timestamp', '')}/{capture['url']}"


def file_entry(capture: dict, raw_bytes: bytes, site_bytes: bytes) -> dict:
    return {
        "url": capture["url"],
        "capture_timestamp": capture.get("timestamp"),
        "wayback_url": wayback_url(capture),
        "cdx_digest": capture.get("digest"),
        "digest_verified": capture.get("digest_ok"),
        "replay_mode": capture.get("source"),
        "wanted_timestamp": capture.get("fallback_from"),  # set only when an alternate capture was used
        "sha256": sha256_hex(site_bytes),
        "raw_sha256": sha256_hex(raw_bytes),
        "modified_by_clean": site_bytes != raw_bytes,
    }


def write_site_provenance(dig: Dig, files: dict[str, dict]) -> Path:
    """Write the per-file citations. Lives in site/ so it travels with the
    site — unless the site itself has a file of that name, in which case the
    site's file wins and ours goes to the dig root (never overwrite an artifact)."""
    payload = {
        "domain": dig.domain,
        "generated_by": f"{AGENT} {__version__}",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": "Each file below was recovered from the Internet Archive capture cited. "
                "modified_by_clean=true means links were localized or Wayback chrome removed; see REWRITES.md.",
        "files": dict(sorted(files.items())),
    }
    collides = any(rel.lower() == SITE_PROVENANCE.lower() for rel in files)
    path = (dig.root if collides else dig.site_dir) / SITE_PROVENANCE
    _atomic_write(path, json.dumps(payload, indent=2))
    return path
