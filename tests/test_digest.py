"""SOTA loop 1 — CDX digest verification (offline)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from excavate.cdx import apply_inventory
from excavate.dig import Dig, sha1_base32
from excavate.pull import fetch_one, run_pull, verify_digest
from excavate.report import run_report
from test_m0 import cdx_row, fresh_capture, mock_client, no_wait_limiter

BODY = b"<html>the archived payload</html>"
NO_HEADERS = httpx.Headers()


def cap_with_digest(body: bytes = BODY, url: str = "http://x.com/") -> dict:
    cap = fresh_capture(url)
    cap["digest"] = sha1_base32(body)
    return cap


class TestSha1Base32(unittest.TestCase):
    def test_known_vectors(self):
        # the empty-payload digest every CDX user has seen
        self.assertEqual(sha1_base32(b""), "3I42H3S6NNFQ2MSVX7XZKYAYSCX5QBYJ")
        self.assertEqual(len(sha1_base32(b"abc")), 32)


class TestVerifyDigest(unittest.TestCase):
    def test_match(self):
        self.assertEqual(verify_digest(cap_with_digest(), BODY, "id_", NO_HEADERS), (True, None))

    def test_lowercase_digest_still_matches(self):
        cap = cap_with_digest()
        cap["digest"] = cap["digest"].lower()
        self.assertEqual(verify_digest(cap, BODY, "id_", NO_HEADERS)[0], True)

    def test_mismatch_is_false_with_both_hashes(self):
        ok, note = verify_digest(cap_with_digest(), b"tampered", "id_", NO_HEADERS)
        self.assertIs(ok, False)
        self.assertIn(sha1_base32(b"tampered"), note)
        self.assertIn(sha1_base32(BODY), note)

    def test_im_replay_is_unverifiable_not_a_mismatch(self):
        ok, note = verify_digest(cap_with_digest(), b"rewritten by wayback", "im_", NO_HEADERS)
        self.assertIsNone(ok)
        self.assertIn("im_", note)

    def test_content_encoded_mismatch_is_unverifiable(self):
        for header in ("content-encoding", "x-archive-orig-content-encoding"):
            with self.subTest(header=header):
                ok, note = verify_digest(cap_with_digest(), b"decoded bytes", "id_", httpx.Headers({header: "gzip"}))
                self.assertIsNone(ok)
                self.assertIn("gzip", note)

    def test_content_encoded_but_matching_is_verified(self):
        ok, _ = verify_digest(cap_with_digest(), BODY, "id_", httpx.Headers({"content-encoding": "gzip"}))
        self.assertIs(ok, True)

    def test_unusable_digest(self):
        for digest in ("", "abc", "-", None, "!" * 32):
            with self.subTest(digest=digest):
                cap = fresh_capture()
                cap["digest"] = digest
                self.assertEqual(verify_digest(cap, BODY, "id_", NO_HEADERS), (None, "no usable CDX digest"))


class TestPullRecordsDigest(unittest.TestCase):
    def test_fetch_one_verifies(self):
        result = fetch_one(cap_with_digest(), mock_client(lambda r: httpx.Response(200, content=BODY)), no_wait_limiter())
        self.assertIs(result["digest_ok"], True)
        self.assertIsNone(result["digest_note"])

    def test_im_fallback_recorded_unverifiable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "id_/" in str(request.url):
                return httpx.Response(404)
            return httpx.Response(200, content=b"<html>replayed</html>")

        result = fetch_one(cap_with_digest(), mock_client(handler), no_wait_limiter())
        self.assertEqual(result["source"], "im_")
        self.assertIsNone(result["digest_ok"])

    def test_mismatch_still_saved_and_disclosed_in_report(self):
        # a mismatching body is kept (it is the only copy) and reported loudly
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            good = "http://x.com/good.html"
            bad = "http://x.com/bad.html"
            dig.save_captures([cap_with_digest(url=good), cap_with_digest(url=bad), fresh_capture("http://x.com/nodigest.html")])

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=b"<html>corrupt</html>" if "bad.html" in str(request.url) else BODY)

            run_pull(dig, mock_client(handler), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            caps = {c["url"]: c for c in dig.load_captures()}
            self.assertIs(caps[good]["digest_ok"], True)
            self.assertIs(caps[bad]["digest_ok"], False)
            self.assertEqual(caps[bad]["fetch_state"], "fetched")
            self.assertTrue((dig.root / caps[bad]["raw_path"]).exists())

            summary = run_report(dig)
            self.assertEqual(
                (summary["digest_verified"], summary["digest_mismatch"], summary["digest_unverifiable"]), (1, 1, 1))
            report = (dig.root / "VERIFICATION-REPORT.md").read_text()
            self.assertIn("digest mismatches", report)
            self.assertIn("`bad.html`", report)
            self.assertIn("1 × no usable CDX digest", report)

    def test_reinventory_keeps_digest_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            rows = [cdx_row("http://x.com/", "20010101000000", "text/html", "200", digest=sha1_base32(BODY))]
            apply_inventory(dig, rows)
            run_pull(dig, mock_client(lambda r: httpx.Response(200, content=BODY)),
                     limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            apply_inventory(dig, rows)
            self.assertIs(dig.load_captures()[0]["digest_ok"], True)

    def test_legacy_captures_without_digest_fields_report_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            cap = fresh_capture()
            cap.update(fetch_state="fetched", raw_path="raw/index.html")  # no digest_ok key at all
            (dig.raw_dir / "index.html").write_bytes(BODY)
            dig.save_captures([cap])
            summary = run_report(dig)
            self.assertEqual(summary["digest_unverifiable"], 1)
            self.assertIn("pulled before digest verification existed", (dig.root / "VERIFICATION-REPORT.md").read_text())


if __name__ == "__main__":
    unittest.main()
