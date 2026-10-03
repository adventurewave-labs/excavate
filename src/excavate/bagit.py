"""Archive-grade export — a BagIt bag (RFC 8493) of the dig (PRD FR-E14).

"This outlives Vercel": a bag is a plain directory (or one ZIP) that any
library, archive or future script can verify without this tool — a payload
under data/, a sha256 manifest of every payload file, and a few tag files.

Payload layout:
    data/site/   the rebuilt site, exactly as `clean` produced it
    data/raw/    the captures byte-for-byte as fetched (omit with include_raw=False)
    data/dig/    the paper trail: report, rewrite ledger, CDX manifest,
                 capture state, crawl check, event log, scan result

The bag is a preservation copy, not a publication: it is written whatever the
output-scan gate says, and records that gate's verdict in bag-info.txt so
nobody mistakes an unscanned bag for a cleared one. `require_clean=True`
turns the verdict into a refusal.

Serializing is deterministic: the same bag directory always zips to the same
bytes. (Each export appends an event to the dig's log, so two successive bags
of one dig differ by that one line of data/dig/events.jsonl — by design.)
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .dig import Dig
from .provenance import SITE_PROVENANCE, lines_hash, record_event
from .scan import scan_status

BAGIT_TXT = "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"
ALGORITHM = "sha256"
DIG_DOCUMENTS = ("VERIFICATION-REPORT.md", "REWRITES.md", "manifest.csv", "manifest.json", "captures.json",
                 "crawl.json", "events.jsonl", "scan.json", "scan-allow.json", "discover.json", "meta.json",
                 SITE_PROVENANCE)
_CHUNK = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def encode_path(path: str) -> str:
    """RFC 8493 §2.1.3: in a manifest, %, CR and LF in a file name are percent-encoded."""
    return path.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def decode_path(path: str) -> str:
    return path.replace("%0A", "\n").replace("%0a", "\n").replace("%0D", "\r").replace("%0d", "\r").replace("%25", "%")


def _files(root: Path) -> list[Path]:
    return sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix())


def _copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    for path in _files(source):
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def _write_manifest(bag: Path, name: str, files: list[Path]) -> None:
    lines = [f"{_sha256_file(path)}  {encode_path(path.relative_to(bag).as_posix())}\n" for path in files]
    (bag / name).write_text("".join(lines), encoding="utf-8", newline="\n")


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def build_bag(dig: Dig, output: Path | None = None, include_raw: bool = True, require_clean: bool = False,
              date: str | None = None) -> dict:
    gate, _ = scan_status(dig)
    if require_clean and gate != "clean":
        raise SystemExit(f"export refused: output scan is '{gate}' (run `excavate scan` and resolve it, or drop --require-clean)")
    if not dig.site_dir.exists() or not any(dig.site_dir.rglob("*")):
        raise SystemExit(f"nothing to export: {dig.site_dir} is empty — run `excavate clean` first")
    date = date or datetime.now(UTC).strftime("%Y-%m-%d")
    domain = dig.domain
    dig.load_captures()
    dig.export_captures_json()  # the bag carries the readable state, current as of now

    bag = (Path(output) if output else dig.root / "export") / f"{domain}-bag"
    if bag.exists():
        if not (bag / "bagit.txt").exists():
            raise SystemExit(f"refusing to overwrite {bag}: it exists and is not a bag")
        shutil.rmtree(bag)
    data = bag / "data"
    _copy_tree(dig.site_dir, data / "site")
    if include_raw:
        _copy_tree(dig.raw_dir, data / "raw")
    for name in DIG_DOCUMENTS:
        if (dig.root / name).is_file():
            (data / "dig").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dig.root / name, data / "dig" / name)

    payload = _files(data)
    octets = sum(path.stat().st_size for path in payload)
    selection = (dig.meta().get("selection") or {}).get("at")
    info = [
        ("Bag-Software-Agent", f"excavate {__version__}"),
        ("Bagging-Date", date),
        ("External-Identifier", domain),
        ("External-Description", f"Static rebuild of {domain} from Internet Archive Wayback Machine captures, "
                                 "with the capture manifest, rewrite ledger and verification report."),
        ("Payload-Oxum", f"{octets}.{len(payload)}"),
        ("Bag-Size", _human(octets)),
        ("Excavate-Capture-Selection", f"nearest to {selection}" if selection else "latest capture per URL"),
        ("Excavate-Raw-Captures-Included", "yes" if include_raw else "no"),
        ("Excavate-Publish-Gate", {"clean": "clean", "findings": "BLOCKED - unreviewed findings; do not publish",
                                   "stale": "stale - site changed after the last scan",
                                   "not run": "not run - this bag has not passed the output scan"}[gate]),
    ]
    (bag / "bagit.txt").write_text(BAGIT_TXT, encoding="utf-8", newline="\n")
    (bag / "bag-info.txt").write_text("".join(f"{key}: {value}\n" for key, value in info), encoding="utf-8", newline="\n")
    _write_manifest(bag, f"manifest-{ALGORITHM}.txt", payload)
    _write_manifest(bag, f"tagmanifest-{ALGORITHM}.txt",
                    [bag / "bag-info.txt", bag / "bagit.txt", bag / f"manifest-{ALGORITHM}.txt"])

    summary = {"bag": str(bag), "files": len(payload), "octets": octets, "size": _human(octets),
               "include_raw": include_raw, "publish_gate": gate, "date": date}
    record_event(dig, "export", lines_hash((bag / f"manifest-{ALGORITHM}.txt").read_text(encoding="utf-8").splitlines()), summary)
    return summary


def zip_bag(bag: Path, date: str | None = None) -> Path:
    """Serialize a bag as <bag>.zip with the bag directory as its single
    top-level entry (RFC 8493 §4). Entry order, timestamps and permissions are
    fixed, so the same bag always yields the same bytes."""
    bag = Path(bag)
    date = date or datetime.now(UTC).strftime("%Y-%m-%d")
    year, month, day = (int(part) for part in date.split("-"))
    stamp = (max(year, 1980), month, day, 0, 0, 0)
    target = bag.with_name(bag.name + ".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in _files(bag):
            info = zipfile.ZipInfo(f"{bag.name}/{path.relative_to(bag).as_posix()}", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=6)
    return target


def _parse_manifest(path: Path) -> tuple[dict[str, str], list[str]]:
    entries: dict[str, str] = {}
    errors: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        checksum, _, name = line.partition(" ")
        name = decode_path(name.lstrip(" \t*"))
        if len(checksum) != 64 or not name:
            errors.append(f"{path.name}:{number}: malformed line")
            continue
        if name.startswith("/") or ".." in name.split("/"):
            errors.append(f"{path.name}:{number}: path escapes the bag: {name}")
            continue
        if name in entries:
            errors.append(f"{path.name}:{number}: listed twice: {name}")
        entries[name] = checksum.lower()
    return entries, errors


def verify_bag(path: Path) -> list[str]:
    """Every way the bag at `path` (directory or .zip) fails RFC 8493
    validity. An empty list means the bag is complete and valid."""
    path = Path(path)
    if path.is_file():
        with tempfile.TemporaryDirectory() as tmp:
            try:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                    if any(name.startswith("/") or ".." in name.split("/") for name in names):
                        return ["zip contains a path that escapes the archive"]
                    tops = {name.split("/", 1)[0] for name in names}
                    if len(tops) != 1:
                        return [f"a serialized bag must have exactly one top-level directory (found {len(tops)})"]
                    archive.extractall(tmp)
            except zipfile.BadZipFile:
                return ["not a readable zip file"]
            return verify_bag(Path(tmp) / tops.pop())
    if not path.is_dir():
        return [f"no bag at {path}"]

    errors: list[str] = []
    declaration = path / "bagit.txt"
    if not declaration.is_file():
        return ["bagit.txt is missing — not a bag"]
    if declaration.read_text(encoding="utf-8") != BAGIT_TXT:
        errors.append("bagit.txt: unexpected declaration (want BagIt-Version 1.0, UTF-8)")
    manifest = path / f"manifest-{ALGORITHM}.txt"
    if not manifest.is_file():
        return [*errors, f"manifest-{ALGORITHM}.txt is missing"]
    entries, parse_errors = _parse_manifest(manifest)
    errors += parse_errors

    data = path / "data"
    on_disk = {p.relative_to(path).as_posix() for p in _files(data)} if data.is_dir() else set()
    errors += [f"listed in the manifest but missing: {name}" for name in sorted(set(entries) - on_disk)]
    errors += [f"in data/ but not in the manifest: {name}" for name in sorted(on_disk - set(entries))]
    for name in sorted(set(entries) & on_disk):
        if _sha256_file(path / name) != entries[name]:
            errors.append(f"checksum mismatch: {name}")

    tagmanifest = path / f"tagmanifest-{ALGORITHM}.txt"
    if tagmanifest.is_file():
        tags, tag_errors = _parse_manifest(tagmanifest)
        errors += tag_errors
        for name, checksum in sorted(tags.items()):
            if not (path / name).is_file():
                errors.append(f"tag file missing: {name}")
            elif _sha256_file(path / name) != checksum:
                errors.append(f"tag file checksum mismatch: {name}")

    info = path / "bag-info.txt"
    if info.is_file():
        for line in info.read_text(encoding="utf-8").splitlines():
            if line.startswith("Payload-Oxum:"):
                octets = sum((path / name).stat().st_size for name in on_disk)
                if line.split(":", 1)[1].strip() != f"{octets}.{len(on_disk)}":
                    errors.append(f"Payload-Oxum says {line.split(':', 1)[1].strip()}, payload is {octets}.{len(on_disk)}")
    return errors
