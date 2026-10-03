"""SOTA loop 0 — regressions for link relativization, resume, atomic state."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from excavate.clean import clean_text, internal_targets, run_clean
from excavate.dig import Dig
from excavate.pull import run_pull
from test_m0 import fresh_capture, mock_client, no_wait_limiter


class TestPageRelativeRewrites(unittest.TestCase):
    def test_nested_page_links_climb_to_site_root(self):
        html = '<a href="http://x.com/img/a.gif">a</a><a href="/top.html">t</a><a href="/sub/deep/sib.html">s</a>'
        text, log = clean_text(html, "x.com", "sub/deep/page.html", is_css=False)
        self.assertIn('href="../../img/a.gif"', text)
        self.assertIn('href="../../top.html"', text)
        self.assertIn('href="sib.html"', text)
        self.assertEqual(len(log), 3)
        # the link graph must agree with what was written — no phantom never-crawled
        self.assertEqual(
            internal_targets(text, "x.com", "sub/deep/page.html"),
            {"img/a.gif", "top.html", "sub/deep/sib.html"},
        )

    def test_css_url_in_nested_stylesheet(self):
        css = "body{background:url(/img/bg.gif)}"
        text, _ = clean_text(css, "x.com", "css/site.css", is_css=True)
        self.assertEqual(text, "body{background:url(../img/bg.gif)}")

    def test_fragment_survives_rewrite(self):
        text, _ = clean_text('<a href="http://x.com/faq.html#q3">q</a>', "x.com", "index.html", is_css=False)
        self.assertIn('href="faq.html#q3"', text)

    def test_end_to_end_no_false_never_crawled(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            (dig.raw_dir / "a").mkdir()
            (dig.raw_dir / "a" / "page.html").write_bytes(b'<a href="/index.html">home</a>')
            (dig.raw_dir / "index.html").write_bytes(b"<html>home</html>")
            caps = [fresh_capture("http://x.com/a/page.html"), fresh_capture("http://x.com/")]
            caps[0].update(fetch_state="fetched", raw_path="raw/a/page.html")
            caps[1].update(fetch_state="fetched", raw_path="raw/index.html")
            dig.save_captures(caps)
            summary = run_clean(dig)
            self.assertEqual(summary["never_crawled"], [])
            self.assertIn('href="../index.html"', (dig.site_dir / "a" / "page.html").read_text())


class TestResumeRetriesTransient(unittest.TestCase):
    def test_failed_5xx_retried_failed_4xx_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            caps = [fresh_capture("http://x.com/t.html"), fresh_capture("http://x.com/p.html")]
            caps[0].update(fetch_state="failed_5xx", error="id_: 503")
            caps[1].update(fetch_state="failed_4xx", error="im_: 404")
            dig.save_captures(caps)

            def handler(request: httpx.Request) -> httpx.Response:
                assert "p.html" not in str(request.url), "permanent failures are never re-requested"
                return httpx.Response(200, content=b"<html>t</html>")

            summary = run_pull(dig, mock_client(handler), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertEqual((summary["attempted"], summary["fetched"]), (1, 1))
            states = {c["url"]: c for c in dig.load_captures()}
            self.assertEqual(states["http://x.com/t.html"]["fetch_state"], "fetched")
            self.assertIsNone(states["http://x.com/t.html"]["error"])
            self.assertEqual(states["http://x.com/p.html"]["fetch_state"], "failed_4xx")

    def test_first_failure_not_labelled_refetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures([fresh_capture("http://x.com/g.html")])
            run_pull(dig, mock_client(lambda r: httpx.Response(404)), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertNotIn("re-fetch", dig.load_captures()[0]["error"])


class TestAtomicState(unittest.TestCase):
    def test_no_tmp_left_and_state_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures([fresh_capture()])
            self.assertEqual(len(dig.load_captures()), 1)
            self.assertEqual([p.name for p in dig.root.glob("*.tmp")], [])


if __name__ == "__main__":
    unittest.main()
