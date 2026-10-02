"""SOTA loop 11 — provenance: event log + per-file citations (offline)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from excavate import __version__
from excavate.cdx import apply_inventory
from excavate.clean import run_clean
from excavate.crawl import analyze, run_check
from excavate.dig import Dig, sha1_base32
from excavate.provenance import (
    SITE_PROVENANCE,
    lines_hash,
    load_events,
    record_event,
    sha256_hex,
    tree_hash,
)
from excavate.pull import run_pull
from excavate.report import run_report
from test_m0 import cdx_row, mock_client, no_wait_limiter

PAGE = b'<a href="http://x.com/a.gif">a</a>'
GIF = b"GIF89a-bytes"


def replay(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=GIF if request.url.path.endswith(".gif") else PAGE)


def full_run(tmp: str) -> Dig:
    dig = Dig.create(Path(tmp), "x.com")
    apply_inventory(dig, [cdx_row("http://x.com/", "20030601000000", "text/html", "200", digest=sha1_base32(PAGE)),
                          cdx_row("http://x.com/a.gif", "20030602000000", "image/gif", "200", digest="NOT-A-DIGEST")])
    run_pull(dig, mock_client(replay), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
    run_clean(dig)
    run_report(dig)
    return dig


class TestHashes(unittest.TestCase):
    def test_lines_hash_is_order_independent_and_content_sensitive(self):
        self.assertEqual(lines_hash(["b", "a"]), lines_hash(["a", "b"]))
        self.assertNotEqual(lines_hash(["a", "b"]), lines_hash(["a", "c"]))
        self.assertNotEqual(lines_hash(["ab"]), lines_hash(["a", "b"]))
        self.assertTrue(lines_hash([]).startswith("sha256:"))

    def test_tree_hash_tracks_content_and_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"one")
            first = tree_hash(root)
            (root / "a.txt").write_bytes(b"two")
            self.assertNotEqual(tree_hash(root), first)
            (root / "a.txt").write_bytes(b"one")
            self.assertEqual(tree_hash(root), first)
            (root / "a.txt").rename(root / "b.txt")
            self.assertNotEqual(tree_hash(root), first)
            (root / "skip.json").write_bytes(b"x")
            self.assertEqual(tree_hash(root, exclude=("skip.json",)), tree_hash(root, exclude=("skip.json",)))
            self.assertEqual(tree_hash(root / "missing"), lines_hash([]))


class TestEventLog(unittest.TestCase):
    def test_every_stage_appends_one_premis_lite_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = full_run(tmp)
            events = load_events(dig)
            self.assertEqual([e["event_type"] for e in events], ["inventory", "pull", "clean", "report"])
            for event in events:
                self.assertEqual(set(event), {"timestamp", "agent", "tool", "version", "event_type", "input_hash", "outcome"})
                self.assertEqual((event["agent"], event["version"]), ("excavate", __version__))
                self.assertEqual(event["tool"], f"excavate.{event['event_type']}")
                self.assertRegex(event["input_hash"], r"^sha256:[0-9a-f]{64}$")
                self.assertRegex(event["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
            pull = events[1]["outcome"]
            self.assertEqual((pull["completed"], pull["fetched"], pull["totals"]["fetched"]), (True, 2, 2))
            self.assertEqual(events[2]["outcome"]["modified_files"], 1)
            self.assertEqual(events[0]["outcome"]["query"]["domain"], "x.com")

    def test_log_is_append_only_across_reruns(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = full_run(tmp)
            before = (dig.root / "events.jsonl").read_text()
            run_clean(dig)
            after = (dig.root / "events.jsonl").read_text()
            self.assertTrue(after.startswith(before))  # history is never rewritten
            self.assertEqual(len(load_events(dig)), 5)

    def test_same_input_same_hash_changed_input_new_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = full_run(tmp)
            run_clean(dig)
            cleans = [e for e in load_events(dig) if e["event_type"] == "clean"]
            self.assertEqual(cleans[0]["input_hash"], cleans[1]["input_hash"])
            self.assertEqual(cleans[0]["outcome"]["site_hash"], cleans[1]["outcome"]["site_hash"])  # reproducible output
            (dig.raw_dir / "index.html").write_bytes(b"<p>tampered after the pull</p>")
            caps = dig.load_captures()
            caps[0]["sha1_fetched"] = "changed"
            dig.save_captures(caps)
            run_clean(dig)
            third = [e for e in load_events(dig) if e["event_type"] == "clean"][2]
            self.assertNotEqual(third["input_hash"], cleans[0]["input_hash"])
            self.assertNotEqual(third["outcome"]["site_hash"], cleans[0]["outcome"]["site_hash"])

    def test_interrupted_pull_is_recorded_as_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            apply_inventory(dig, [cdx_row("http://x.com/", "20030601000000", "text/html", "200")])

            def dying(request: httpx.Request) -> httpx.Response:
                raise KeyboardInterrupt

            with self.assertRaises(KeyboardInterrupt):
                run_pull(dig, mock_client(dying), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            event = load_events(dig)[-1]
            self.assertEqual((event["event_type"], event["outcome"]["completed"]), ("pull", False))

    def test_check_records_but_report_does_not_double_log_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = full_run(tmp)
            self.assertNotIn("check", [e["event_type"] for e in load_events(dig)])
            run_check(dig)
            self.assertEqual(load_events(dig)[-1]["event_type"], "check")

    def test_unknown_event_type_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            with self.assertRaises(ValueError):
                record_event(dig, "magic", lines_hash([]), {})
            self.assertFalse((dig.root / "events.jsonl").exists())

    def test_report_shows_chain_of_custody(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = full_run(tmp)
            text = (dig.root / "VERIFICATION-REPORT.md").read_text()
            self.assertIn("## Chain of custody", text)
            self.assertIn("| inventory |", text)
            self.assertIn("site/PROVENANCE.json", text)


class TestSiteProvenance(unittest.TestCase):
    def test_every_site_file_cites_its_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = full_run(tmp)
            data = json.loads((dig.site_dir / SITE_PROVENANCE).read_text())
            self.assertEqual(data["domain"], "x.com")
            self.assertEqual(sorted(data["files"]), ["a.gif", "index.html"])
            page = data["files"]["index.html"]
            self.assertEqual(page["url"], "http://x.com/")
            self.assertEqual(page["capture_timestamp"], "20030601000000")
            self.assertEqual(page["wayback_url"], "https://web.archive.org/web/20030601000000/http://x.com/")
            self.assertEqual((page["cdx_digest"], page["digest_verified"], page["replay_mode"]), (sha1_base32(PAGE), True, "id_"))
            self.assertTrue(page["modified_by_clean"])  # its link was localized
            self.assertEqual(page["raw_sha256"], sha256_hex(PAGE))
            self.assertEqual(page["sha256"], sha256_hex((dig.site_dir / "index.html").read_bytes()))
            gif = data["files"]["a.gif"]
            self.assertFalse(gif["modified_by_clean"])
            self.assertEqual(gif["sha256"], gif["raw_sha256"])
            self.assertIsNone(gif["digest_verified"])  # unusable CDX digest: unverifiable, said so
            self.assertIsNone(gif["wanted_timestamp"])

    def test_fallback_capture_is_cited_as_such(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            apply_inventory(dig, [cdx_row("http://x.com/", ts, "text/html", "200") for ts in ("19990101000000", "20080101000000")])

            def only_1999(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=b"<p>1999</p>") if "/1999" in request.url.path else httpx.Response(404)

            run_pull(dig, mock_client(only_1999), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            run_clean(dig)
            entry = json.loads((dig.site_dir / SITE_PROVENANCE).read_text())["files"]["index.html"]
            self.assertEqual((entry["capture_timestamp"], entry["wanted_timestamp"]), ("19990101000000", "20080101000000"))
            self.assertIn("/web/19990101000000/", entry["wayback_url"])  # the link opens the capture actually used

    def test_provenance_file_is_not_counted_as_site_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = full_run(tmp)
            crawl = analyze(dig)
            self.assertEqual(crawl["counts"]["site_files"], 2)
            self.assertNotIn(SITE_PROVENANCE, crawl["orphans"])

    def test_site_with_its_own_provenance_json_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            apply_inventory(dig, [cdx_row("http://x.com/PROVENANCE.json", "20030601000000", "application/json", "200")])
            run_pull(dig, mock_client(lambda r: httpx.Response(200, content=b'{"the site\'s own": true}')),
                     limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            run_clean(dig)
            self.assertEqual((dig.site_dir / "PROVENANCE.json").read_bytes(), b'{"the site\'s own": true}')
            ours = json.loads((dig.root / "PROVENANCE.json").read_text())
            self.assertIn("PROVENANCE.json", ours["files"])
            self.assertEqual(analyze(dig)["counts"]["site_files"], 1)  # and the site's file still counts as site content


if __name__ == "__main__":
    unittest.main()
