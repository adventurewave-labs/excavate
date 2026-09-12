"""M0 unit tests — no network. httpx.MockTransport stands in for IA."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from excavate.cdx import resolve_best  # noqa: E402
from excavate.clean import clean_text  # noqa: E402
from excavate.dig import Dig, safe_relpath  # noqa: E402
from excavate.pull import RateLimiter, fetch_one, magic_ok, sniff_type  # noqa: E402

CDX_HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]


def cdx_row(url: str, ts: str, mime: str, status: str, digest: str = "abc") -> dict:
    return dict(zip(CDX_HEADER, [url, ts, url, mime, status, digest, "100"]))


class TestResolveBest(unittest.TestCase):
    def test_latest_2xx_with_content_wins(self):
        rows = [
            cdx_row("http://x.com/", "20010101000000", "text/html", "200"),
            cdx_row("http://x.com/", "20050601120000", "text/html", "200"),
            cdx_row("http://x.com/", "20090101000000", "text/html", "404"),
        ]
        caps = resolve_best(rows)
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0]["timestamp"], "20050601120000")
        self.assertEqual(caps[0]["fetch_state"], "pending")

    def test_revisit_never_fetchable(self):
        rows = [
            cdx_row("http://x.com/", "20010101000000", "warc/revisit", "200"),
            cdx_row("http://x.com/a.html", "20030101000000", "text/html", "302"),
        ]
        caps = {c["url"]: c for c in resolve_best(rows)}
        self.assertEqual(caps["http://x.com/"]["fetch_state"], "skipped")
        self.assertEqual(caps["http://x.com/a.html"]["fetch_state"], "skipped")

    def test_raw_row_count_preserved_upstream(self):
        rows = [cdx_row("http://x.com/", f"200{m:02d}01000000", "text/html", "200") for m in range(1, 13)]
        self.assertEqual(len(rows), 12)
        self.assertEqual(len(resolve_best(rows)), 1)


class TestRateLimiter(unittest.TestCase):
    def test_min_interval_enforced(self):
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        clock = iter([0.0, 1.0, 1.0])  # waited#1, elapsed-check#2, waited#2
        limiter = RateLimiter(per_minute=30, sleep_fn=fake_sleep, clock=lambda: next(clock))
        limiter.wait()
        limiter.wait()  # 1.0 elapsed < 2.0 interval -> sleeps 1.0
        self.assertEqual(sleeps, [1.0])


class TestPull(unittest.TestCase):
    def _capture(self) -> dict:
        return {
            "url": "http://x.com/", "timestamp": "20000101000000", "status": "200",
            "mimetype": "text/html", "digest": "abc", "fetch_state": "pending",
            "skip_reason": None, "sha1_fetched": None, "source": None,
            "http_status": None, "magic_ok": None, "error": None,
        }

    def test_id_success_first_try(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "id_" in str(request.url)
            return httpx.Response(200, content=b"<html>hi</html>")

        limiter = RateLimiter(sleep_fn=lambda s: None, clock=lambda: 0.0)
        result = fetch_one(self._capture(), httpx.Client(transport=httpx.MockTransport(handler)), limiter, sleep_fn=lambda s: None)
        self.assertEqual(result["fetch_state"], "fetched")
        self.assertEqual(result["source"], "id_")
        import hashlib
        self.assertEqual(result["sha1_fetched"], hashlib.sha1(b"<html>hi</html>").hexdigest())

    def test_im_fallback_on_404(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "id_" in str(request.url):
                return httpx.Response(404, content=b"not found")
            return httpx.Response(200, content=b"<html>via im</html>")

        limiter = RateLimiter(sleep_fn=lambda s: None, clock=lambda: 0.0)
        result = fetch_one(self._capture(), httpx.Client(transport=httpx.MockTransport(handler)), limiter, sleep_fn=lambda s: None)
        self.assertEqual(result["fetch_state"], "fetched")
        self.assertEqual(result["source"], "im_")

    def test_429_backoff_then_success(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if "id_" in str(request.url) and calls["n"] <= 2:
                return httpx.Response(429, content=b"slow down")
            return httpx.Response(200, content=b"<html>ok</html>")

        backoffs: list[float] = []
        limiter = RateLimiter(sleep_fn=lambda s: None, clock=lambda: 0.0)
        result = fetch_one(self._capture(), httpx.Client(transport=httpx.MockTransport(handler)), limiter,
                           sleep_fn=backoffs.append)
        self.assertEqual(result["fetch_state"], "fetched")
        self.assertEqual(backoffs, [30, 60])  # schedule is 30/60/120 — two 429s cost two backoffs

    def test_429_exhaustion_fails_row(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, content=b"slow down")

        backoffs: list[float] = []
        limiter = RateLimiter(sleep_fn=lambda s: None, clock=lambda: 0.0)
        result = fetch_one(self._capture(), httpx.Client(transport=httpx.MockTransport(handler)), limiter,
                           sleep_fn=backoffs.append)
        self.assertEqual(result["fetch_state"], "failed_4xx")
        self.assertEqual(len(backoffs), 6)  # 3 per replay mode


class TestMagicBytes(unittest.TestCase):
    def test_sniff_and_match(self):
        self.assertEqual(sniff_type(b"\x89PNG\r\n\x1a\n" + b"0" * 20), "png")
        self.assertTrue(magic_ok(b"\x89PNG" + b"0" * 20, "img/pic.png"))
        self.assertFalse(magic_ok(b"<html>not a png</html>", "img/pic.png"))
        self.assertIsNone(magic_ok(b"\x00\x01binary", "data/file.dat"))


class TestClean(unittest.TestCase):
    def test_rewrites_logged_and_chrome_stripped(self):
        html = (
            '<html><head><link rel="stylesheet" href="/web/20030601000000im_/http://magpu.com/style.css">'
            '<link rel="stylesheet" href="https://web.archive.org/web/20030601000000im_/https://magpu.com/_static/banner-styles.css"></head>'
            '<body><a href="http://magpu.com/music/">music</a>'
            '<a href="https://example.com/elsewhere">ext</a>'
            '<img src="https://magpu.com/img/logo.png"></body></html>'
        )
        pulled = {"music/index.html", "img/logo.png", "style.css"}
        text, log = clean_text(html, "magpu.com", "index.html", pulled, is_css=False)
        self.assertIn('href="music/index.html"', text)
        self.assertIn('src="img/logo.png"', text)
        self.assertIn('href="style.css"', text)
        self.assertIn("wayback chrome removed", text)
        self.assertNotIn("banner-styles.css", text)
        self.assertIn('href="https://example.com/elsewhere"', text)  # external untouched
        chrome_log = [line for line in log if "wayback chrome removed" in line]
        self.assertEqual(len(chrome_log), 1)  # chrome stripping is a logged rewrite too
        self.assertGreaterEqual(len(log), 4)

    def test_missing_directory_url_left_as_is(self):
        html = '<a href="/gone/dir/">gone</a>'
        text, log = clean_text(html, "x.com", "index.html", set(), is_css=False)
        self.assertIn('href="gone/dir/"', text)  # not mapped to index.html — that file was never pulled
        self.assertEqual(len(log), 1)  # absolute→relative localization is still a logged rewrite


class TestSafeRelpath(unittest.TestCase):
    def test_directory_and_index_mapping(self):
        self.assertEqual(safe_relpath("http://x.com/"), "index.html")
        self.assertEqual(safe_relpath("http://x.com/music/"), "music/index.html")
        self.assertEqual(safe_relpath("http://x.com/music"), "music/index.html")
        self.assertEqual(safe_relpath("http://x.com/a/b.png"), "a/b.png")


class TestResume(unittest.TestCase):
    def test_pull_resumes_only_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            caps = [
                {"url": "http://x.com/a.html", "timestamp": "20000101000000", "status": "200",
                 "mimetype": "text/html", "digest": "a", "fetch_state": "fetched",
                 "skip_reason": None, "sha1_fetched": "aa", "source": "id_",
                 "http_status": 200, "magic_ok": None, "error": None, "raw_path": "raw/a.html"},
                {"url": "http://x.com/b.html", "timestamp": "20000101000000", "status": "200",
                 "mimetype": "text/html", "digest": "b", "fetch_state": "pending",
                 "skip_reason": None, "sha1_fetched": None, "source": None,
                 "http_status": None, "magic_ok": None, "error": None},
            ]
            dig.save_captures(caps)

            def handler(request: httpx.Request) -> httpx.Response:
                assert "b.html" in str(request.url), "must not re-fetch already-fetched a.html"
                return httpx.Response(200, content=b"<html>b</html>")

            from excavate.pull import run_pull
            summary = run_pull(dig, httpx.Client(transport=httpx.MockTransport(handler)),
                               limiter=RateLimiter(sleep_fn=lambda s: None, clock=lambda: 0.0),
                               sleep_fn=lambda s: None)
            self.assertEqual(summary["attempted"], 1)
            self.assertEqual(summary["fetched"], 1)
            states = {c["url"]: c["fetch_state"] for c in dig.load_captures()}
            self.assertEqual(states["http://x.com/a.html"], "fetched")
            self.assertEqual(states["http://x.com/b.html"], "fetched")


if __name__ == "__main__":
    unittest.main()
