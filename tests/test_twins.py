"""SOTA loop 9 — asset twin swap (offline, on-disk fixtures)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from excavate.clean import clean_text, run_clean
from excavate.cli import main
from excavate.crawl import analyze
from excavate.dig import Dig
from excavate.report import run_report
from excavate.twins import TwinIndex
from test_m0 import fresh_capture


def index(*paths: str) -> TwinIndex:
    return TwinIndex(set(paths))


class TestFind(unittest.TestCase):
    def test_present_file_needs_no_twin(self):
        self.assertIsNone(index("a.mp3", "a.ogg").find("a.mp3"))

    def test_case_twin(self):
        twin = index("images/logo.gif").find("Images/Logo.GIF")
        self.assertEqual((twin.path, twin.kind), ("images/logo.gif", "case"))

    def test_case_ambiguity_is_not_guessed(self):
        self.assertIsNone(index("a/Readme.txt", "a/README.txt").find("a/readme.txt"))

    def test_extension_spelling(self):
        self.assertEqual(index("about.htm").find("about.html").path, "about.htm")
        self.assertEqual(index("p/photo.jpeg").find("p/photo.jpg").kind, "spelling")
        self.assertEqual(index("tune.midi").find("tune.mid").path, "tune.midi")

    def test_index_document_variants(self):
        twin = index("music/default.htm").find("music/index.html")
        self.assertEqual((twin.path, twin.kind), ("music/default.htm", "index"))
        self.assertIsNone(index("music/default.htm", "music/home.html").find("music/index.html"))  # two candidates
        self.assertIsNone(index("other/default.htm").find("music/index.html"))  # not the same directory

    def test_format_twin_the_magpu_case(self):
        twin = index("music/song.ogg").find("music/song.mp3")
        self.assertEqual((twin.path, twin.kind), ("music/song.ogg", "format"))
        self.assertIn(".mp3 → .ogg", twin.reason)

    def test_format_preference_order_is_deterministic(self):
        self.assertEqual(index("s.wav", "s.ogg", "s.ra").find("s.mp3").path, "s.ogg")
        self.assertEqual(index("v.avi", "v.mov").find("v.wmv").path, "v.mov")
        self.assertEqual(index("i.bmp", "i.png").find("i.gif").path, "i.png")

    def test_no_twin_across_media_or_names(self):
        self.assertIsNone(index("song.gif").find("song.mp3"))       # an image is not the song
        self.assertIsNone(index("song2.ogg").find("song.mp3"))      # a different file
        self.assertIsNone(index("page.txt").find("page.html"))      # no family for documents
        self.assertIsNone(index("a/song.ogg").find("b/song.mp3"))   # a different directory
        self.assertIsNone(index("song.mp3.bak").find("song.mp3"))

    def test_case_insensitive_stem_and_extension(self):
        self.assertEqual(index("Music/SONG.OGG").find("music/song.mp3").path, "Music/SONG.OGG")


class TestCleanText(unittest.TestCase):
    def test_relative_and_absolute_links_swap_and_are_logged_with_reason(self):
        twins = index("music/song.ogg", "music/index.html", "img/a.gif")
        html = '<a href="song.mp3">relative</a><a href="http://x.com/music/song.mp3#t=30">absolute</a><img src="/IMG/A.GIF">'
        text, log = clean_text(html, "x.com", "music/index.html", is_css=False, twins=twins)
        self.assertEqual(text, '<a href="song.ogg">relative</a><a href="song.ogg#t=30">absolute</a><img src="../img/a.gif">')
        self.assertEqual(len(log), 3)
        self.assertIn("twin: same name in another format (.mp3 → .ogg) — the linked file was never recovered", log[0])
        self.assertIn("twin: same path, different letter case", log[2])

    def test_working_relative_link_is_still_untouched(self):
        twins = index("music/song.mp3", "music/song.ogg")
        html = '<a href="song.mp3">s</a><a href="../up.html">u</a>'
        self.assertEqual(clean_text(html, "x.com", "music/list.html", False, twins=twins), (html, []))

    def test_missing_without_twin_stays_as_written(self):
        html = '<a href="gone.mp3">s</a><a href="/also-gone.html">g</a>'
        text, log = clean_text(html, "x.com", "music/list.html", False, twins=index("music/list.html"))
        self.assertIn('href="gone.mp3"', text)                   # never pointed at something that is not there
        self.assertIn('href="../also-gone.html"', text)
        self.assertFalse(any("twin" in line for line in log))

    def test_css_and_srcset_swap_too(self):
        text, _ = clean_text("b{background:url(/img/BG.gif)}", "x.com", "css/s.css", True, twins=index("img/bg.gif"))
        self.assertEqual(text, "b{background:url(../img/bg.gif)}")
        text, _ = clean_text('<img srcset="a.jpg 1x, b.jpg 2x">', "x.com", "index.html", False, twins=index("a.jpeg", "b.jpg"))
        self.assertEqual(text, '<img srcset="a.jpeg 1x, b.jpg 2x">')

    def test_external_and_special_links_never_twinned(self):
        html = '<a href="http://other.com/song.mp3">x</a><a href="mailto:a@x.com">m</a><a href="#top">t</a>'
        self.assertEqual(clean_text(html, "x.com", "index.html", False, twins=index("song.ogg"))[0], html)

    def test_without_an_index_nothing_changes(self):
        html = '<a href="song.mp3">relative</a>'
        self.assertEqual(clean_text(html, "x.com", "music/index.html", False), (html, []))


class TestEndToEnd(unittest.TestCase):
    def _dig(self, tmp: str) -> Dig:
        dig = Dig.create(Path(tmp), "x.com")
        files = {
            "index.html": b'<a href="/music/song.mp3">song</a><img src="/Images/Logo.GIF"><a href="/about.html">a</a>'
                          b'<a href="/lost.mp3">lost</a>',
            "music/song.ogg": b"OggS",
            "images/logo.gif": b"GIF89a",
            "about.htm": b"<p>about</p>",
        }
        caps = []
        for rel, data in files.items():
            (dig.raw_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (dig.raw_dir / rel).write_bytes(data)
            cap = fresh_capture("http://x.com/" + ("" if rel == "index.html" else rel))
            cap.update(fetch_state="fetched", raw_path=f"raw/{rel}")
            caps.append(cap)
        dig.save_captures(caps)
        return dig

    def test_clean_swaps_reports_and_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            summary = run_clean(dig)
            self.assertEqual(summary["twin_swaps"], 3)
            page = (dig.site_dir / "index.html").read_text()
            self.assertIn('href="music/song.ogg"', page)
            self.assertIn('src="images/logo.gif"', page)
            self.assertIn('href="about.htm"', page)
            self.assertIn('href="lost.mp3"', page)  # no twin → honestly dead
            self.assertFalse((dig.site_dir / "music" / "song.mp3").exists())  # nothing was fabricated

            crawl = analyze(dig)
            self.assertEqual(crawl["unresolved"], ["lost.mp3"])
            self.assertEqual(crawl["counts"]["case_mismatch"], 0)

            report = run_report(dig)
            self.assertEqual(report["twin_swaps"], 3)
            text = (dig.root / "VERIFICATION-REPORT.md").read_text()
            self.assertIn("## Twin swaps", text)
            self.assertIn("`/music/song.mp3` → `music/song.ogg`", text)

    def test_no_twins_flag_leaves_links_as_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            self.assertEqual(main(["clean", "--dig", str(dig.root), "--no-twins"]), 0)
            page = (dig.site_dir / "index.html").read_text()
            self.assertIn('href="music/song.mp3"', page)
            self.assertIn('src="Images/Logo.GIF"', page)
            self.assertEqual(len(analyze(dig)["unresolved"]), 4)
            self.assertEqual(run_report(dig)["twin_swaps"], 0)


if __name__ == "__main__":
    unittest.main()
