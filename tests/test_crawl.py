"""SOTA loop 8 — crawl-check stage (offline, on-disk fixture site)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from excavate.clean import run_clean
from excavate.cli import main
from excavate.crawl import analyze, run_check
from excavate.dig import Dig
from excavate.report import run_report
from test_m0 import fresh_capture

FILES = {
    "index.html": b'<link rel="stylesheet" href="/css/site.css"><a href="/music/">music</a>'
                  b'<a href="/about.html">about</a><img src="/Images/Logo.GIF"><a href="/lost.html">lost</a>'
                  b'<a href="/gone.html">gone</a><a href="/later.html">later</a><a href="http://other.com/">ext</a>',
    "music/index.html": b'<a href="../index.html">home</a><a href="song.ogg">song</a><a href="demo.mp3">demo</a>',
    "music/song.ogg": b"OggS....",
    "about.html": b'<a href="/index.html">home</a><a href="/lost.html">lost again</a>',
    "css/site.css": b"body{background:url(/img/bg.gif)} h1{background:url(/img/missing-bg.gif)}",
    "img/bg.gif": b"GIF89a",
    "images/logo.gif": b"GIF89a",
    "secret/old-draft.html": b'<a href="/index.html">home</a><img src="/secret/pic.gif">',
    "secret/pic.gif": b"GIF89a",
}


def build(tmp: str, files: dict[str, bytes] | None = None) -> Dig:
    dig = Dig.create(Path(tmp), "x.com")
    captures = []
    for rel, data in (FILES if files is None else files).items():
        target = dig.raw_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        url = "http://x.com/" + ("" if rel == "index.html" else rel.removesuffix("index.html"))
        cap = fresh_capture(url)
        cap.update(fetch_state="fetched", raw_path=f"raw/{rel}")
        captures.append(cap)
    gone = fresh_capture("http://x.com/gone.html")
    gone.update(fetch_state="failed_4xx", error="im_: 404")
    later = fresh_capture("http://x.com/later.html")
    demo = fresh_capture("http://x.com/music/demo.mp3")
    demo.update(fetch_state="skipped", skip_reason="best capture status 404")
    dig.save_captures([*captures, gone, later, demo])
    run_clean(dig, twin_swaps=False)  # the crawl check must classify links exactly as the site wrote them
    return dig


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dig = build(self.tmp.name)
        self.result = analyze(self.dig)
        self.counts = self.result["counts"]

    def test_accounting_adds_up(self):
        c = self.counts
        self.assertEqual(c["referenced"],
                         c["recovered"] + c["case_mismatch"] + c["archived_not_recovered"] + c["never_archived"])
        self.assertEqual(c["unresolved"], c["referenced"] - c["recovered"])
        self.assertEqual((c["site_files"], c["pages"]), (9, 4))

    def test_never_archived_vs_known_to_the_archive(self):
        self.assertEqual([e["target"] for e in self.result["never_archived"]], ["img/missing-bg.gif", "lost.html"])
        known = {e["target"]: e for e in self.result["archived_not_recovered"]}
        self.assertEqual(sorted(known), ["gone.html", "later.html", "music/demo.mp3"])
        self.assertIn("permanent", known["gone.html"]["reason"])
        self.assertIn("404", known["gone.html"]["reason"])
        self.assertIn("pending", known["later.html"]["reason"])
        self.assertIn("skipped", known["music/demo.mp3"]["reason"])
        self.assertEqual(known["music/demo.mp3"]["url"], "http://x.com/music/demo.mp3")

    def test_referrers_recorded(self):
        lost = next(e for e in self.result["never_archived"] if e["target"] == "lost.html")
        self.assertEqual((lost["referenced_by"], lost["referrer_count"]), (["about.html", "index.html"], 2))
        bg = next(e for e in self.result["never_archived"] if e["target"] == "img/missing-bg.gif")
        self.assertEqual(bg["referenced_by"], ["css/site.css"])  # links inside CSS are walked too

    def test_case_mismatch_is_its_own_bucket(self):
        self.assertEqual(self.result["case_mismatch"][0]["target"], "Images/Logo.GIF")
        self.assertEqual(self.result["case_mismatch"][0]["on_disk"], "images/logo.gif")
        self.assertIn("Images/Logo.GIF", self.result["unresolved"])  # still unresolved: broken on Linux hosts

    def test_reachability_and_orphans(self):
        self.assertEqual(self.result["entry_page"], "index.html")
        # nothing links to secret/ (or to the lowercase logo) from the entry page's link closure
        self.assertEqual(self.result["orphans"], ["images/logo.gif", "secret/old-draft.html", "secret/pic.gif"])
        self.assertEqual((self.counts["orphans"], self.counts["orphan_pages"]), (3, 1))
        self.assertEqual(self.counts["reachable_from_entry"], 6)  # css and its background are reachable via the stylesheet

    def test_external_links_are_not_counted(self):
        self.assertFalse(any("other.com" in t for t in self.result["unresolved"]))

    def test_analyze_does_not_modify_the_site(self):
        before = {p: p.read_bytes() for p in self.dig.site_dir.rglob("*") if p.is_file()}
        run_check(self.dig)
        self.assertEqual({p: p.read_bytes() for p in self.dig.site_dir.rglob("*") if p.is_file()}, before)


class TestEdges(unittest.TestCase):
    def test_no_entry_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = build(tmp, {"page.html": b'<a href="/other.html">o</a>', "other.html": b"<p>o</p>"})
            result = analyze(dig)
            self.assertIsNone(result["entry_page"])
            self.assertEqual((result["orphans"], result["counts"]["reachable_from_entry"]), ([], 0))
            run_report(dig)
            self.assertIn("No entry page", (dig.root / "VERIFICATION-REPORT.md").read_text())

    def test_link_cycles_terminate(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = build(tmp, {"index.html": b'<a href="/a.html">a</a><a href="/index.html">self</a>',
                              "a.html": b'<a href="/index.html">home</a><a href="/a.html">self</a>'})
            self.assertEqual(analyze(dig)["counts"]["orphans"], 0)

    def test_empty_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures([fresh_capture()])
            result = analyze(dig)
            self.assertEqual((result["counts"]["site_files"], result["unresolved"]), (0, []))
            self.assertEqual(run_report(dig)["never_crawled"], 0)


class TestCommandAndReport(unittest.TestCase):
    def test_check_command_writes_crawl_json_and_strict_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = build(tmp)
            self.assertEqual(main(["check", "--dig", str(dig.root)]), 0)
            data = json.loads((dig.root / "crawl.json").read_text())
            self.assertEqual(data["counts"]["never_archived"], 2)
            self.assertEqual(main(["check", "--dig", str(dig.root), "--strict"]), 1)

    def test_strict_passes_on_a_complete_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = build(tmp, {"index.html": b'<a href="/a.html">a</a>', "a.html": b"<p>a</p>"})
            dig.save_captures([c for c in dig.load_captures() if c["fetch_state"] == "fetched"])
            self.assertEqual(main(["check", "--dig", str(dig.root), "--strict"]), 0)

    def test_check_before_clean_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures([fresh_capture()])
            with self.assertRaises(SystemExit) as ctx:
                main(["check", "--dig", str(dig.root)])
            self.assertIn("excavate clean", str(ctx.exception))

    def test_report_carries_the_breakdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = build(tmp)
            summary = run_report(dig)
            self.assertEqual(summary["never_crawled"], 6)  # M0 meaning kept: everything referenced but not on disk
            self.assertEqual(summary["crawl"]["never_archived"], 2)
            text = (dig.root / "VERIFICATION-REPORT.md").read_text()
            self.assertIn("13 referenced / 7 recovered / 3 archived but not recovered / 2 never archived / 1 case mismatch", text)
            self.assertIn("- `lost.html` — linked from `about.html`, `index.html`", text)
            self.assertIn("- `gone.html` — fetch failed (permanent)", text)
            self.assertIn("- `Images/Logo.GIF` — on disk as `images/logo.gif`", text)
            self.assertIn("3 orphans (1 pages)", text)
            self.assertTrue((dig.root / "crawl.json").exists())  # report refreshes it


if __name__ == "__main__":
    unittest.main()
