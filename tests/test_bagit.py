"""SOTA loop 13 — BagIt (RFC 8493) export and verification (offline)."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from excavate.bagit import BAGIT_TXT, build_bag, decode_path, encode_path, verify_bag, zip_bag
from excavate.clean import run_clean
from excavate.cli import main
from excavate.dig import Dig
from excavate.provenance import load_events
from excavate.report import run_report
from excavate.scan import run_scan
from test_m0 import fresh_capture

FILES = {
    "index.html": b'<a href="http://x.com/music/song.ogg">song</a><img src="/img/100%25.gif">',
    "music/song.ogg": b"OggS-bytes",
    "img/100%.gif": b"GIF89a",
}


def make_dig(tmp: str, files: dict[str, bytes] | None = None) -> Dig:
    dig = Dig.create(Path(tmp), "x.com")
    caps = []
    for rel, data in (files or FILES).items():
        target = dig.raw_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        cap = fresh_capture("http://x.com/" + ("" if rel == "index.html" else rel.replace("%", "%25")))
        cap.update(fetch_state="fetched", raw_path=f"raw/{rel}")
        caps.append(cap)
    dig.save_captures(caps)
    run_clean(dig)
    run_report(dig)
    return dig


def manifest(bag: Path) -> dict[str, str]:
    lines = (bag / "manifest-sha256.txt").read_text().splitlines()
    return {decode_path(line.split("  ", 1)[1]): line.split("  ", 1)[0] for line in lines}


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dig = make_dig(self.tmp.name)
        self.summary = build_bag(self.dig, date="2026-10-02")
        self.bag = Path(self.summary["bag"])

    def test_structure_follows_the_rfc(self):
        self.assertEqual(self.bag.name, "x.com-bag")
        self.assertEqual((self.bag / "bagit.txt").read_text(), BAGIT_TXT)
        self.assertEqual(sorted(p.name for p in self.bag.iterdir()),
                         ["bag-info.txt", "bagit.txt", "data", "manifest-sha256.txt", "tagmanifest-sha256.txt"])
        self.assertEqual(sorted(p.name for p in (self.bag / "data").iterdir()), ["dig", "raw", "site"])
        self.assertEqual(verify_bag(self.bag), [])

    def test_manifest_lists_every_payload_file_with_its_sha256(self):
        entries = manifest(self.bag)
        on_disk = {p.relative_to(self.bag).as_posix() for p in (self.bag / "data").rglob("*") if p.is_file()}
        self.assertEqual(set(entries), on_disk)
        for name, checksum in entries.items():
            self.assertEqual(hashlib.sha256((self.bag / name).read_bytes()).hexdigest(), checksum)
        self.assertIn("data/site/index.html", entries)
        self.assertIn("data/site/PROVENANCE.json", entries)
        for document in ("VERIFICATION-REPORT.md", "REWRITES.md", "captures.json", "events.jsonl", "crawl.json", "meta.json"):
            self.assertIn(f"data/dig/{document}", entries)
        self.assertNotIn("data/dig/dig.sqlite", entries)

    def test_percent_in_a_file_name_is_encoded_in_the_manifest(self):
        text = (self.bag / "manifest-sha256.txt").read_text()
        self.assertIn("data/site/img/100%25.gif", text)
        self.assertIn("data/site/img/100%.gif", manifest(self.bag))
        self.assertEqual(decode_path(encode_path("a%b\nc\rd")), "a%b\nc\rd")
        self.assertEqual(encode_path("a%b\nc\rd"), "a%25b%0Ac%0Dd")

    def test_raw_captures_are_byte_identical_and_site_is_as_cleaned(self):
        self.assertEqual((self.bag / "data/raw/index.html").read_bytes(), FILES["index.html"])
        self.assertEqual((self.bag / "data/site/index.html").read_bytes(), (self.dig.site_dir / "index.html").read_bytes())
        self.assertNotEqual((self.bag / "data/site/index.html").read_bytes(), FILES["index.html"])  # cleaned ≠ raw

    def test_bag_info(self):
        info = dict(line.split(": ", 1) for line in (self.bag / "bag-info.txt").read_text().splitlines())
        payload = [p for p in (self.bag / "data").rglob("*") if p.is_file()]
        self.assertEqual(info["Payload-Oxum"], f"{sum(p.stat().st_size for p in payload)}.{len(payload)}")
        self.assertEqual((info["Bagging-Date"], info["External-Identifier"]), ("2026-10-02", "x.com"))
        self.assertTrue(info["Bag-Software-Agent"].startswith("excavate "))
        self.assertEqual(info["Excavate-Raw-Captures-Included"], "yes")
        self.assertIn("not run", info["Excavate-Publish-Gate"])  # an unscanned bag says so on its label

    def test_export_is_recorded(self):
        event = load_events(self.dig)[-1]
        self.assertEqual((event["event_type"], event["outcome"]["files"]), ("export", self.summary["files"]))


class TestOptionsAndGate(unittest.TestCase):
    def test_no_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp)
            bag = Path(build_bag(dig, include_raw=False)["bag"])
            self.assertFalse((bag / "data/raw").exists())
            self.assertIn("Excavate-Raw-Captures-Included: no", (bag / "bag-info.txt").read_text())
            self.assertEqual(verify_bag(bag), [])

    def test_gate_verdict_is_recorded_and_require_clean_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp, {**FILES, "users.txt": b"sysop:$" + b"apr1$" + b"fakesalt$fakefakefakefakefakefa\n"})
            with self.assertRaises(SystemExit) as ctx:
                build_bag(dig, require_clean=True)
            self.assertIn("not run", str(ctx.exception))
            run_scan(dig)
            with self.assertRaises(SystemExit):
                build_bag(dig, require_clean=True)
            self.assertFalse((dig.root / "export").exists())  # a refusal leaves nothing behind
            bag = Path(build_bag(dig)["bag"])  # the preservation copy is still allowed — and labelled
            self.assertIn("Excavate-Publish-Gate: BLOCKED", (bag / "bag-info.txt").read_text())

    def test_clean_scan_is_recorded_and_passes_require_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp)
            run_scan(dig)
            bag = Path(build_bag(dig, require_clean=True)["bag"])
            self.assertIn("Excavate-Publish-Gate: clean\n", (bag / "bag-info.txt").read_text())

    def test_rebuild_replaces_a_bag_but_never_another_directory(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            dig = make_dig(tmp)
            first = Path(build_bag(dig, Path(out))["bag"])
            (first / "data/site/stale.txt").write_text("left over")
            second = Path(build_bag(dig, Path(out))["bag"])
            self.assertFalse((second / "data/site/stale.txt").exists())
            self.assertEqual(verify_bag(second), [])
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            dig = make_dig(tmp)
            precious = Path(out) / "x.com-bag"
            precious.mkdir()
            (precious / "thesis.doc").write_text("not a bag")
            with self.assertRaises(SystemExit):
                build_bag(dig, Path(out))
            self.assertEqual((precious / "thesis.doc").read_text(), "not a bag")

    def test_nothing_to_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures([fresh_capture()])
            with self.assertRaises(SystemExit):
                build_bag(dig)


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bag = Path(build_bag(make_dig(self.tmp.name), date="2026-10-02")["bag"])

    def test_tampered_payload(self):
        (self.bag / "data/site/index.html").write_bytes(b"<p>edited after bagging</p>")
        errors = verify_bag(self.bag)
        self.assertIn("checksum mismatch: data/site/index.html", errors)
        self.assertTrue(any("Payload-Oxum" in e for e in errors))

    def test_missing_and_extra_files(self):
        (self.bag / "data/site/music/song.ogg").unlink()
        (self.bag / "data/site/smuggled.html").write_text("not in the manifest")
        errors = verify_bag(self.bag)
        self.assertIn("listed in the manifest but missing: data/site/music/song.ogg", errors)
        self.assertIn("in data/ but not in the manifest: data/site/smuggled.html", errors)

    def test_tampered_manifest_is_caught_by_the_tag_manifest(self):
        path = self.bag / "manifest-sha256.txt"
        path.write_text(path.read_text().replace("data/site/index.html", "data/site/index.htm"))
        self.assertIn("tag file checksum mismatch: manifest-sha256.txt", verify_bag(self.bag))

    def test_not_a_bag(self):
        self.assertEqual(verify_bag(Path(self.tmp.name) / "nope"), [f"no bag at {Path(self.tmp.name) / 'nope'}"])
        (self.bag / "bagit.txt").unlink()
        self.assertEqual(verify_bag(self.bag), ["bagit.txt is missing — not a bag"])

    def test_manifest_path_escape_rejected(self):
        with (self.bag / "manifest-sha256.txt").open("a") as fh:
            fh.write("0" * 64 + "  ../../etc/passwd\n")
        self.assertTrue(any("escapes the bag" in e for e in verify_bag(self.bag)))


class TestZip(unittest.TestCase):
    def test_zip_is_valid_deterministic_and_single_rooted(self):
        with tempfile.TemporaryDirectory() as tmp:
            bag = Path(build_bag(make_dig(tmp), date="2026-10-02")["bag"])
            archive = zip_bag(bag, "2026-10-02")
            first = archive.read_bytes()
            self.assertEqual(verify_bag(archive), [])
            with zipfile.ZipFile(archive) as z:
                names = z.namelist()
                self.assertEqual({n.split("/", 1)[0] for n in names}, {"x.com-bag"})
                self.assertEqual(names, sorted(names))
            self.assertEqual(zip_bag(bag, "2026-10-02").read_bytes(), first)  # same bag → same bytes

    def test_corrupt_or_hostile_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "junk.zip"
            junk.write_bytes(b"this is not a zip")
            self.assertEqual(verify_bag(junk), ["not a readable zip file"])
            hostile = Path(tmp) / "hostile.zip"
            with zipfile.ZipFile(hostile, "w") as z:
                z.writestr("../outside.txt", "escape")
            self.assertEqual(verify_bag(hostile), ["zip contains a path that escapes the archive"])
            self.assertFalse((Path(tmp).parent / "outside.txt").exists())


class TestCli(unittest.TestCase):
    def test_export_zip_then_verify(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            dig = make_dig(tmp)
            self.assertEqual(main(["export", "--dig", str(dig.root), "--output", out, "--zip"]), 0)
            self.assertEqual(main(["export", "--verify", str(Path(out) / "x.com-bag.zip")]), 0)
            self.assertEqual(main(["export", "--verify", str(Path(out) / "x.com-bag")]), 0)
            (Path(out) / "x.com-bag" / "data" / "site" / "index.html").write_text("tampered")
            self.assertEqual(main(["export", "--verify", str(Path(out) / "x.com-bag")]), 1)


if __name__ == "__main__":
    unittest.main()
