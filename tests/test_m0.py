"""M0 unit tests — no network. httpx.MockTransport stands in for IA."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from excavate.cdx import apply_inventory, resolve_best  # noqa: E402
from excavate.clean import clean_text, run_clean  # noqa: E402
from excavate.dig import Dig, safe_relpath  # noqa: E402
from excavate.pull import (  # noqa: E402
    RateLimiter,
    fetch_one,
    magic_ok,
    sniff_type,
    run_pull,
)
from excavate.report import run_report  # noqa: E402

CDX_HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]


def cdx_row(url: str, ts: str, mime: str, status: str, digest: str = "abc") -> dict:
    return dict(zip(CDX_HEADER, [url, ts, url, mime, status, digest, "100"]))


def no_wait_limiter() -> RateLimiter:
    return RateLimiter(sleep_fn=lambda s: None, clock=lambda: 0.0)


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"<html>body</html>")


def fresh_capture(url: str = "http://x.com/") -> dict:
    return {
        "url": url, "timestamp": "20000101000000", "status": "200",
        "mimetype": "text/html", "digest": "abc", "fetch_state": "pending",
        "skip_reason": None, "sha1_fetched": None, "source": None,
        "http_status": None, "magic_ok": None, "error": None,
        "raw_path": None, "path_collision": None,
    }


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

    def test_ragged_rows_skipped_not_crash(self):
        rows = [cdx_row("http://x.com/", "20010101000000", "text/html", "200")]
        rows.append({"urlkey": "x", "timestamp": "2002"})  # malformed — short
        caps = resolve_best(rows)
        self.assertEqual(len(caps), 1)  # ragged row contributed nothing, no KeyError


class TestInventoryMerge(unittest.TestCase):
    def test_same_timestamp_keeps_fetch_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            rows = [cdx_row("http://x.com/", "20010101000000", "text/html", "200")]
            apply_inventory(dig, rows)
            caps = dig.load_captures()
            caps[0].update(fetch_state="fetched", raw_path="raw/index.html", sha1_fetched="deadbeef")
            dig.save_captures(caps)

            apply_inventory(dig, rows)  # re-inventory must not wipe state
            cap = dig.load_captures()[0]
            self.assertEqual(cap["fetch_state"], "fetched")
            self.assertEqual(cap["raw_path"], "raw/index.html")
            self.assertEqual(cap["sha1_fetched"], "deadbeef")

    def test_different_timestamp_resets_to_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            apply_inventory(dig, [cdx_row("http://x.com/", "20010101000000", "text/html", "200")])
            caps = dig.load_captures()
            caps[0]["fetch_state"] = "fetched"
            dig.save_captures(caps)

            apply_inventory(dig, [cdx_row("http://x.com/", "20090101000000", "text/html", "200")])
            cap = dig.load_captures()[0]
            self.assertEqual(cap["fetch_state"], "pending")
            self.assertEqual(cap["timestamp"], "20090101000000")

    def test_raw_manifest_keeps_every_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            rows = [cdx_row("http://x.com/", f"200{m:02d}01000000", "text/html", "200") for m in range(1, 13)]
            apply_inventory(dig, rows)
            self.assertEqual(len(dig.raw_manifest_rows()), 12)  # strops invariant, for real this time


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
        self.assertEqual(limiter._last, 1.0)  # post-sleep clock recorded

    def test_zero_rate_rejected(self):
        with self.assertRaises(ValueError):
            RateLimiter(per_minute=0)


class TestPullClassification(unittest.TestCase):
    def test_id_success_first_try(self):
        import hashlib
        result = fetch_one(fresh_capture(), mock_client(ok_handler), no_wait_limiter(), sleep_fn=lambda s: None)
        self.assertEqual(result["fetch_state"], "fetched")
        self.assertEqual(result["source"], "id_")
        self.assertEqual(result["sha1_fetched"], hashlib.sha1(b"<html>body</html>").hexdigest())

    def test_im_fallback_on_404(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "id_" in str(request.url):
                return httpx.Response(404, content=b"not found")
            return httpx.Response(200, content=b"<html>via im</html>")

        result = fetch_one(fresh_capture(), mock_client(handler), no_wait_limiter(), sleep_fn=lambda s: None)
        self.assertEqual(result["fetch_state"], "fetched")
        self.assertEqual(result["source"], "im_")

    def test_404_both_modes_is_permanent_4xx(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, content=b"gone")

        result = fetch_one(fresh_capture(), mock_client(handler), no_wait_limiter(), sleep_fn=lambda s: None)
        self.assertEqual(result["fetch_state"], "failed_4xx")

    def test_transport_error_is_transient_5xx(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        result = fetch_one(fresh_capture(), mock_client(handler), no_wait_limiter(), sleep_fn=lambda s: None)
        self.assertEqual(result["fetch_state"], "failed_5xx")

    def test_transport_on_im_fallback_after_404_is_transient(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "id_" in str(request.url):
                return httpx.Response(404, content=b"gone")
            raise httpx.ConnectError("died mid-fallback")

        result = fetch_one(fresh_capture(), mock_client(handler), no_wait_limiter(), sleep_fn=lambda s: None)
        # the row never finished its fallback attempt — resume must retry it
        self.assertEqual(result["fetch_state"], "failed_5xx")

    def test_429_backoff_then_success(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if "id_" in str(request.url) and calls["n"] <= 2:
                return httpx.Response(429, content=b"slow down")
            return httpx.Response(200, content=b"<html>ok</html>")

        backoffs: list[float] = []
        result = fetch_one(fresh_capture(), mock_client(handler), no_wait_limiter(), sleep_fn=backoffs.append)
        self.assertEqual(result["fetch_state"], "fetched")
        self.assertEqual(backoffs, [30, 60])  # schedule 30/60/120 — two 429s cost two chances

    def test_429_exhaustion_shared_budget_fails_transient(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, content=b"slow down")

        backoffs: list[float] = []
        result = fetch_one(fresh_capture(), mock_client(handler), no_wait_limiter(), sleep_fn=backoffs.append)
        self.assertEqual(result["fetch_state"], "failed_5xx")  # 429 is transient — resume retries it
        self.assertEqual(backoffs, [30, 60, 120])  # 3 chances per row, shared across modes

    def test_5xx_retried_with_backoff(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, content=b"overloaded")
            return httpx.Response(200, content=b"<html>ok</html>")

        backoffs: list[float] = []
        result = fetch_one(fresh_capture(), mock_client(handler), no_wait_limiter(), sleep_fn=backoffs.append)
        self.assertEqual(result["fetch_state"], "fetched")
        self.assertEqual(backoffs, [30])


class TestMagicBytes(unittest.TestCase):
    def test_sniff_and_match(self):
        self.assertEqual(sniff_type(b"\x89PNG\r\n\x1a\n" + b"0" * 20), "png")
        self.assertTrue(magic_ok(b"\x89PNG" + b"0" * 20, "img/pic.png"))
        self.assertFalse(magic_ok(b"<html>not a png</html>", "img/pic.png"))
        self.assertIsNone(magic_ok(b"\x00\x01binary", "data/file.dat"))

    def test_html_bodies_validated(self):
        self.assertTrue(magic_ok(b"<html>x</html>", "index.html"))
        self.assertFalse(magic_ok(b"\x89PNGx", "page.html"))  # a png saved as .html is a lie


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

    def test_www_variant_is_internal(self):
        html = '<a href="http://www.magpu.com/page.html">www</a>'
        text, log = clean_text(html, "magpu.com", "index.html", {"page.html"}, is_css=False)
        self.assertIn('href="page.html"', text)
        self.assertEqual(len(log), 1)

    def test_near_miss_domain_stays_external(self):
        html = '<a href="http://notmagpu.com/page.html">evil</a>'
        text, log = clean_text(html, "magpu.com", "index.html", {"page.html"}, is_css=False)
        self.assertIn('href="http://notmagpu.com/page.html"', text)
        self.assertEqual(log, [])


class TestSafeRelpath(unittest.TestCase):
    def test_directory_and_index_mapping(self):
        self.assertEqual(safe_relpath("http://x.com/"), "index.html")
        self.assertEqual(safe_relpath("http://x.com/music/"), "music/index.html")
        self.assertEqual(safe_relpath("http://x.com/music"), "music/index.html")
        self.assertEqual(safe_relpath("http://x.com/a/b.png"), "a/b.png")

    def test_traversal_neutralized(self):
        # CDX `original` is attacker-controllable — this must never escape the dig
        self.assertEqual(safe_relpath("http://x.com/%2e%2e/%2e%2e/etc/passwd"), "etc/passwd/index.html")
        self.assertEqual(safe_relpath("http://x.com/a/../../b.html"), "a/b.html")
        self.assertEqual(safe_relpath("http://x.com/..%2f..%2fsecret"), "secret/index.html")
        self.assertNotIn("..", safe_relpath("http://x.com/foo/../bar/baz.css"))

    def test_control_bytes_stripped(self):
        self.assertEqual(safe_relpath("http://x.com/a\x00b.html"), "ab.html")

    def test_long_segments_truncated_unique_and_fs_safe(self):
        long_a = "a" * 300 + ".png"
        long_b = "b" * 300 + ".png"
        rel_a = safe_relpath(f"http://x.com/{long_a}")
        rel_b = safe_relpath(f"http://x.com/{long_b}")
        self.assertTrue(all(len(part) < 200 for part in rel_a.split("/")))  # fs limits
        self.assertNotEqual(rel_a, rel_b)  # truncation is hash-disambiguated
        self.assertTrue(rel_a.endswith(".png"))  # extension survives
        Path(rel_a)  # constructible as a path without raising


class TestPathCollision(unittest.TestCase):
    def test_url_keyed_dedupe_and_resume_no_fork(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            # /a and /a/ both map to a/index.html — one canonical path, two URLs
            dig.save_captures([fresh_capture("http://x.com/a"), fresh_capture("http://x.com/a/")])
            summary = run_pull(dig, mock_client(ok_handler), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertEqual(summary["fetched"], 2)
            caps = {c["url"]: c for c in dig.load_captures()}
            self.assertEqual(caps["http://x.com/a"]["raw_path"], "raw/a/index.html")
            self.assertFalse(caps["http://x.com/a"]["path_collision"])
            self.assertEqual(caps["http://x.com/a/"]["raw_path"], "raw/a/index-1.html")
            self.assertTrue(caps["http://x.com/a/"]["path_collision"])

            # resume: re-fetch the collision row — must reuse its own path, not fork -2
            caps["http://x.com/a/"]["fetch_state"] = "pending"
            dig.save_captures(list(caps.values()))
            run_pull(dig, mock_client(ok_handler), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            caps = {c["url"]: c for c in dig.load_captures()}
            self.assertEqual(caps["http://x.com/a/"]["raw_path"], "raw/a/index-1.html")
            self.assertEqual(len(list((dig.root / "raw" / "a").iterdir())), 2)  # no -2 fork


class TestCleanAndReport(unittest.TestCase):
    def _dig_with_fetched(self, tmp: str) -> Dig:
        dig = Dig.create(Path(tmp), "x.com")
        index = dig.raw_dir / "index.html"
        index.write_bytes(b'<html><body><a href="/a/">a</a><a href="/missing.html">m</a></body></html>')
        sub = dig.raw_dir / "a"
        sub.mkdir(exist_ok=True)
        (sub / "index-1.html").write_bytes(b"<html>collision twin</html>")
        dig.save_captures([
            {"url": "http://x.com/", "timestamp": "20000101000000", "status": "200",
             "mimetype": "text/html", "digest": "a", "fetch_state": "fetched",
             "skip_reason": None, "sha1_fetched": "aa", "source": "id_",
             "http_status": 200, "magic_ok": None, "error": None,
             "raw_path": "raw/index.html", "path_collision": None},
            {"url": "http://x.com/a/", "timestamp": "20000101000000", "status": "200",
             "mimetype": "text/html", "digest": "b", "fetch_state": "fetched",
             "skip_reason": None, "sha1_fetched": "bb", "source": "id_",
             "http_status": 200, "magic_ok": None, "error": None,
             "raw_path": "raw/a/index-1.html", "path_collision": True},
        ])
        return dig

    def test_clean_mirrors_raw_paths_and_discloses_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig_with_fetched(tmp)
            summary = run_clean(dig)
            self.assertEqual(summary["cleaned"], 2)
            self.assertTrue((dig.site_dir / "index.html").exists())
            self.assertTrue((dig.site_dir / "a" / "index-1.html").exists())  # site mirrors raw, no silent drop
            ledger = (dig.root / "REWRITES.md").read_text()
            self.assertIn("path collision", ledger)  # disclosed, not silent
            self.assertIn("missing.html", summary["never_crawled"])

    def test_clean_refuses_raw_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures([
                {"url": "http://x.com/evil", "timestamp": "20000101000000", "status": "200",
                 "mimetype": "text/html", "digest": "e", "fetch_state": "fetched",
                 "skip_reason": None, "sha1_fetched": None, "source": "id_",
                 "http_status": 200, "magic_ok": None, "error": None,
                 "raw_path": "../escaped.txt", "path_collision": None},
            ])
            summary = run_clean(dig)
            self.assertEqual(summary["cleaned"], 0)  # the escape row cleaned nothing
            self.assertIn("ERROR: raw file missing or outside dig", (dig.root / "REWRITES.md").read_text())

    def test_missing_raw_file_not_phantom_in_pulled(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            # real page links to ghost/index.html; ghost capture claims bytes that don't exist
            (dig.raw_dir / "index.html").write_bytes(b'<a href="/ghost/index.html">g</a>')
            dig.save_captures([
                {"url": "http://x.com/", "timestamp": "20000101000000", "status": "200",
                 "mimetype": "text/html", "digest": "a", "fetch_state": "fetched",
                 "skip_reason": None, "sha1_fetched": "aa", "source": "id_",
                 "http_status": 200, "magic_ok": None, "error": None,
                 "raw_path": "raw/index.html", "path_collision": None},
                {"url": "http://x.com/ghost/index.html", "timestamp": "20000101000000", "status": "200",
                 "mimetype": "text/html", "digest": "g", "fetch_state": "fetched",
                 "skip_reason": None, "sha1_fetched": "gg", "source": "id_",
                 "http_status": 200, "magic_ok": None, "error": None,
                 "raw_path": "raw/ghost/index.html", "path_collision": None},  # file never written
            ])
            summary = run_clean(dig)
            # the ghost must NOT count as pulled — its link stays honestly never-crawled
            self.assertIn("ghost/index.html", summary["never_crawled"])
            self.assertFalse((dig.site_dir / "ghost").exists())
            self.assertIn("ERROR: raw file missing", (dig.root / "REWRITES.md").read_text())
            # report agrees with clean (same validity rule) — no phantom pulled set
            report = run_report(dig)
            self.assertGreaterEqual(report["never_crawled"], 1)

    def test_report_survives_directory_named_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig_with_fetched(tmp)
            run_clean(dig)
            (dig.site_dir / "weird.html").mkdir()  # a directory where a page should be
            summary = run_report(dig)  # must not crash
            self.assertEqual(summary["fetched"], 2)


class TestResume(unittest.TestCase):
    def test_failed_refetch_clears_stale_raw_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            raw = dig.raw_dir / "a.html"
            raw.write_bytes(b"<html>old bytes</html>")
            dig.save_captures([
                {"url": "http://x.com/a.html", "timestamp": "20000101000000", "status": "200",
                 "mimetype": "text/html", "digest": "a", "fetch_state": "pending",
                 "skip_reason": None, "sha1_fetched": None, "source": None,
                 "http_status": None, "magic_ok": None, "error": None,
                 "raw_path": "raw/a.html", "path_collision": None},
            ])

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(404, content=b"gone")

            summary = run_pull(dig, mock_client(handler), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertEqual(summary["failed"], 1)
            cap = dig.load_captures()[0]
            self.assertEqual(cap["fetch_state"], "failed_4xx")
            self.assertIsNone(cap["raw_path"])  # state no longer points at stale bytes
            self.assertFalse(raw.exists())  # and the stale file is gone

    def test_malformed_cdx_payload_rejected(self):
        from excavate.cdx import fetch_cdx_rows

        for garbage in ({"error": "no results"}, "plain string", []):
            with self.subTest(garbage=garbage):
                client = mock_client(lambda r, g=garbage: httpx.Response(200, json=g))
                with self.assertRaises(ValueError):
                    fetch_cdx_rows("x.com", client)

    def test_zero_usable_rows_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            with self.assertRaises(SystemExit):
                apply_inventory(dig, [])  # never write an empty inventory

    def test_pull_resumes_only_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            caps = [
                {"url": "http://x.com/a.html", "timestamp": "20000101000000", "status": "200",
                 "mimetype": "text/html", "digest": "a", "fetch_state": "fetched",
                 "skip_reason": None, "sha1_fetched": "aa", "source": "id_",
                 "http_status": 200, "magic_ok": None, "error": None,
                 "raw_path": "raw/a.html", "path_collision": None},
                {"url": "http://x.com/b.html", "timestamp": "20000101000000", "status": "200",
                 "mimetype": "text/html", "digest": "b", "fetch_state": "pending",
                 "skip_reason": None, "sha1_fetched": None, "source": None,
                 "http_status": None, "magic_ok": None, "error": None,
                 "raw_path": None, "path_collision": None},
            ]
            dig.save_captures(caps)

            def handler(request: httpx.Request) -> httpx.Response:
                assert "b.html" in str(request.url), "must not re-fetch already-fetched a.html"
                return httpx.Response(200, content=b"<html>b</html>")

            summary = run_pull(dig, mock_client(handler), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertEqual(summary["attempted"], 1)
            self.assertEqual(summary["fetched"], 1)
            states = {c["url"]: c["fetch_state"] for c in dig.load_captures()}
            self.assertEqual(states["http://x.com/a.html"], "fetched")
            self.assertEqual(states["http://x.com/b.html"], "fetched")


if __name__ == "__main__":
    unittest.main()
