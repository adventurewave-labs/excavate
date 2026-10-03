"""SOTA loop 6 — capture selection (--at) and the fallback chain (offline)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from excavate.cdx import MAX_ALTERNATES, apply_inventory, parse_timestamp, resolve_best
from excavate.cli import main
from excavate.dig import Dig, sha1_base32
from excavate.pull import run_pull
from excavate.report import run_report
from test_m0 import cdx_row, mock_client, no_wait_limiter

URL = "http://x.com/page.html"
ERAS = ["19990101000000", "20030601000000", "20080101000000"]


def rows_for(url: str = URL, stamps: list[str] | None = None, digest_of=None) -> list[dict]:
    return [cdx_row(url, ts, "text/html", "200", digest=(digest_of(ts) if digest_of else f"D{ts[:4]}"))
            for ts in (stamps or ERAS)]


def replay(available: dict[str, bytes], transient: set[str] | None = None, log: list[str] | None = None):
    """A Wayback replay endpoint that only has the given timestamps."""
    def handler(request: httpx.Request) -> httpx.Response:
        stamp = request.url.path.split("/")[2][:14]
        if log is not None:
            log.append(stamp + request.url.path.split("/")[2][14:])
        if transient and stamp in transient:
            return httpx.Response(503)
        if stamp in available:
            return httpx.Response(200, content=available[stamp])
        return httpx.Response(404)
    return handler


def pull(dig: Dig, handler) -> dict:
    return run_pull(dig, mock_client(handler), limiter=no_wait_limiter(), sleep_fn=lambda s: None)


class TestParseTimestamp(unittest.TestCase):
    def test_partial_timestamps_mean_start_of_period(self):
        self.assertEqual(parse_timestamp("2003").isoformat(), "2003-01-01T00:00:00")
        self.assertEqual(parse_timestamp("200306").isoformat(), "2003-06-01T00:00:00")
        self.assertEqual(parse_timestamp("20030615120000").isoformat(), "2003-06-15T12:00:00")

    def test_rejects_non_dates(self):
        for bad in ("03", "abcd", "20031301", "20030231", "2003-06-01", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_timestamp(bad)


class TestSelection(unittest.TestCase):
    def test_default_is_latest_with_older_ones_as_alternates(self):
        cap = resolve_best(rows_for())[0]
        self.assertEqual(cap["timestamp"], "20080101000000")
        self.assertEqual([a["timestamp"] for a in cap["alternates"]], ["20030601000000", "19990101000000"])
        self.assertEqual(cap["alternates"][0]["digest"], "D2003")

    def test_at_picks_nearest_and_orders_alternates_by_distance(self):
        cap = resolve_best(rows_for(), at="2004")[0]
        self.assertEqual(cap["timestamp"], "20030601000000")
        self.assertEqual([a["timestamp"] for a in cap["alternates"]], ["20080101000000", "19990101000000"])
        self.assertEqual(resolve_best(rows_for(), at="1998")[0]["timestamp"], "19990101000000")
        self.assertEqual(resolve_best(rows_for(), at="2030")[0]["timestamp"], "20080101000000")

    def test_tie_goes_to_the_capture_that_already_existed(self):
        rows = rows_for(stamps=["20030101000000", "20030103000000"])
        self.assertEqual(resolve_best(rows, at="20030102")[0]["timestamp"], "20030101000000")

    def test_fetchable_beats_nearer_error_or_revisit(self):
        rows = [cdx_row(URL, "19990101000000", "text/html", "200"),
                cdx_row(URL, "20040101000000", "text/html", "404"),
                cdx_row(URL, "20040102000000", "warc/revisit", "200")]
        cap = resolve_best(rows, at="2004")[0]
        self.assertEqual((cap["timestamp"], cap["fetch_state"], cap["alternates"]), ("19990101000000", "pending", []))

    def test_unfetchable_url_has_no_alternates(self):
        cap = resolve_best([cdx_row(URL, "20040101000000", "text/html", "404")])[0]
        self.assertEqual((cap["fetch_state"], cap["alternates"]), ("skipped", []))

    def test_alternates_capped_and_deduplicated(self):
        stamps = [f"20{y:02d}0101000000" for y in range(1, 20)]
        cap = resolve_best(rows_for(stamps=stamps + stamps[:3]))[0]
        self.assertEqual(len(cap["alternates"]), MAX_ALTERNATES)
        self.assertEqual(len({a["timestamp"] for a in cap["alternates"]}), MAX_ALTERNATES)

    def test_selection_is_per_url(self):
        rows = rows_for() + rows_for("http://x.com/only-old.html", ["19970101000000"])
        caps = {c["url"]: c["timestamp"] for c in resolve_best(rows, at="2003")}
        self.assertEqual(caps, {URL: "20030601000000", "http://x.com/only-old.html": "19970101000000"})


class TestFallbackChain(unittest.TestCase):
    def _dig(self, tmp: str, at: str | None = None, digest_of=None) -> Dig:
        dig = Dig.create(Path(tmp), "x.com")
        apply_inventory(dig, rows_for(digest_of=digest_of), at=at)
        return dig

    def test_falls_back_to_next_capture_and_discloses_it(self):
        body = b"<html>2003 edition</html>"
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp, digest_of=lambda ts: sha1_base32(body) if ts.startswith("2003") else "X" * 32)
            calls: list[str] = []
            summary = pull(dig, replay({"20030601000000": body}, log=calls))
            cap = dig.load_captures()[0]
            self.assertEqual((summary["fetched"], summary["failed"]), (1, 0))
            self.assertEqual((cap["fetch_state"], cap["timestamp"], cap["fallback_from"]),
                             ("fetched", "20030601000000", "20080101000000"))
            self.assertIs(cap["digest_ok"], True)  # verified against the digest of the capture actually used
            self.assertEqual([a["timestamp"] for a in cap["alternates"]], ["19990101000000"])
            # wanted capture tried in both replay modes first, then the alternate
            self.assertEqual(calls, ["20080101000000id_", "20080101000000im_", "20030601000000id_"])
            self.assertEqual((dig.root / cap["raw_path"]).read_bytes(), body)

            report = run_report(dig)
            self.assertEqual(report["fallbacks"], 1)
            text = (dig.root / "VERIFICATION-REPORT.md").read_text()
            self.assertIn("wanted 20080101000000, used 20030601000000", text)

    def test_chain_exhausted_is_permanent_and_lists_every_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            calls: list[str] = []
            summary = pull(dig, replay({}, log=calls))
            cap = dig.load_captures()[0]
            self.assertEqual(summary["failed"], 1)
            self.assertEqual((cap["fetch_state"], cap["timestamp"], cap["fallback_from"], cap["alternates"]),
                             ("failed_4xx", "20080101000000", None, []))
            self.assertIn("3 captures tried", cap["error"])
            for stamp in ERAS:
                self.assertIn(stamp, cap["error"])
            self.assertEqual(len(calls), 6)
            self.assertIsNone(cap["raw_path"])  # nothing faked

            # permanent: neither a resume nor a re-inventory re-walks the dead chain
            calls.clear()
            pull(dig, replay({}, log=calls))
            apply_inventory(dig, rows_for())
            pull(dig, replay({}, log=calls))
            self.assertEqual(calls, [])

    def test_transient_failure_never_triggers_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            calls: list[str] = []
            pull(dig, replay({"20030601000000": b"<html>old</html>"}, transient={"20080101000000"}, log=calls))
            cap = dig.load_captures()[0]
            self.assertEqual((cap["fetch_state"], cap["timestamp"]), ("failed_5xx", "20080101000000"))
            self.assertEqual(len(cap["alternates"]), 2)
            self.assertNotIn("20030601000000id_", calls)  # a flaky server is not a reason to settle for an older copy
            # resume: the wanted capture is back, and is what we get
            pull(dig, replay({"20080101000000": b"<html>new</html>"}))
            cap = dig.load_captures()[0]
            self.assertEqual((cap["fetch_state"], cap["timestamp"], cap["fallback_from"]), ("fetched", "20080101000000", None))

    def test_transient_mid_chain_resumes_at_that_alternate(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            pull(dig, replay({}, transient={"20030601000000"}))
            cap = dig.load_captures()[0]
            self.assertEqual(cap["fetch_state"], "failed_5xx")
            self.assertEqual([a["timestamp"] for a in cap["alternates"]], ["20030601000000", "19990101000000"])
            calls: list[str] = []
            pull(dig, replay({"20030601000000": b"<html>2003</html>"}, log=calls))
            cap = dig.load_captures()[0]
            self.assertEqual((cap["fetch_state"], cap["timestamp"], cap["fallback_from"]),
                             ("fetched", "20030601000000", "20080101000000"))

    def test_reinventory_keeps_a_fallback_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            pull(dig, replay({"20030601000000": b"<html>2003</html>"}))
            apply_inventory(dig, rows_for())  # same wanted capture → nothing to redo
            cap = dig.load_captures()[0]
            self.assertEqual((cap["fetch_state"], cap["timestamp"], cap["fallback_from"]),
                             ("fetched", "20030601000000", "20080101000000"))
            calls: list[str] = []
            pull(dig, replay({}, log=calls))
            self.assertEqual(calls, [])

    def test_changing_at_reselects_and_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            pull(dig, replay({"20080101000000": b"<html>2008</html>"}))
            apply_inventory(dig, rows_for(), at="1999")
            cap = dig.load_captures()[0]
            self.assertEqual((cap["fetch_state"], cap["timestamp"]), ("pending", "19990101000000"))
            self.assertEqual(dig.meta()["selection"], {"at": "1999"})
            run_report(dig)
            self.assertIn("Capture selection: nearest to 1999", (dig.root / "VERIFICATION-REPORT.md").read_text())

    def test_report_names_default_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            run_report(dig)
            self.assertIn("Capture selection: latest 2xx capture per URL.", (dig.root / "VERIFICATION-REPORT.md").read_text())


class TestCli(unittest.TestCase):
    def test_bad_at_is_a_clean_error_and_creates_no_dig(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["inventory", "x.com", "--output", tmp, "--at", "20031301"]), 1)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
