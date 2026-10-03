"""SOTA loop 7 — SQLite state store (offline)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

from excavate.cdx import apply_inventory
from excavate.clean import run_clean
from excavate.cli import main
from excavate.dig import SCHEMA_VERSION, Dig
from excavate.pull import run_pull
from excavate.report import run_report
from test_m0 import cdx_row, fresh_capture, mock_client, no_wait_limiter, ok_handler


def caps(n: int) -> list[dict]:
    return [fresh_capture(f"http://x.com/p{i:05d}.html") for i in range(n)]


def pull(dig: Dig, handler=ok_handler, **kwargs) -> dict:
    return run_pull(dig, mock_client(handler), limiter=no_wait_limiter(), sleep_fn=lambda s: None, **kwargs)


class TestStore(unittest.TestCase):
    def test_round_trip_preserves_order_and_every_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            original = caps(3)
            original[1].update(alternates=[{"timestamp": "1999", "digest": "D", "mimetype": "text/html"}], magic_ok=False)
            dig.save_captures(original)
            self.assertEqual(dig.load_captures(), original)
            self.assertEqual(json.loads(dig.json_path.read_text()), original)  # export matches
            with sqlite3.connect(dig.db_path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_new_dig_has_no_state_until_first_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            self.assertFalse(dig.has_state())
            self.assertFalse(dig.db_path.exists())
            with self.assertRaises(FileNotFoundError):
                dig.load_captures()
            self.assertFalse(dig.db_path.exists())  # a failed read must not conjure an empty dig

    def test_save_capture_updates_one_row_without_rewriting_the_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures(caps(50))
            exported = dig.json_path.read_bytes()
            row = dig.load_captures()[17]
            row.update(fetch_state="fetched", raw_path="raw/p00017.html")
            dig.save_capture(row)
            self.assertEqual(dig.json_path.read_bytes(), exported)  # no O(n) rewrite per row
            loaded = dig.load_captures()
            self.assertEqual(loaded[17]["fetch_state"], "fetched")
            self.assertEqual([c["url"] for c in loaded], [c["url"] for c in caps(50)])  # position kept
            dig.export_captures_json()
            self.assertEqual(json.loads(dig.json_path.read_text())[17]["raw_path"], "raw/p00017.html")

    def test_save_capture_inserts_unknown_url_at_the_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures(caps(2))
            dig.save_capture(fresh_capture("http://x.com/new.html"))
            self.assertEqual(dig.load_captures()[-1]["url"], "http://x.com/new.html")

    def test_state_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            rows = caps(5)
            rows[0]["fetch_state"] = "fetched"
            rows[1]["fetch_state"] = "failed_4xx"
            dig.save_captures(rows)
            self.assertEqual(dig.state_counts(),
                             {"pending": 3, "fetched": 1, "failed_4xx": 1, "failed_5xx": 0, "skipped": 0})

    def test_no_wal_or_tmp_litter_after_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures(caps(3))
            dig.load_captures()
            names = sorted(p.name for p in dig.root.iterdir() if p.is_file())
            self.assertEqual(names, ["captures.json", "dig.sqlite", "meta.json"])


class TestMigration(unittest.TestCase):
    def _legacy_dig(self, tmp: str) -> Dig:
        """A dig as written by excavate before the SQLite store: captures.json only."""
        root = Path(tmp) / "x.com"
        (root / "raw").mkdir(parents=True)
        (root / "site").mkdir()
        (root / "meta.json").write_text(json.dumps({"domain": "x.com"}))
        legacy = caps(3)
        legacy[0].update(fetch_state="fetched", raw_path="raw/p00000.html", sha1_fetched="aa")
        (root / "raw" / "p00000.html").write_bytes(b"<html>kept</html>")
        (root / "captures.json").write_text(json.dumps(legacy, indent=2))
        return Dig(root)

    def test_legacy_dig_is_recognised_and_migrated_transparently(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._legacy_dig(tmp)
            self.assertTrue(dig.has_state())
            self.assertEqual(Dig.latest(Path(tmp)).root, dig.root)
            loaded = dig.load_captures()
            self.assertEqual(loaded[0]["fetch_state"], "fetched")
            self.assertTrue(dig.db_path.exists())

            def handler(request: httpx.Request) -> httpx.Response:
                assert "p00000" not in str(request.url), "already-fetched legacy rows must not be re-fetched"
                return httpx.Response(200, content=b"<html>new</html>")

            summary = pull(dig, handler)
            self.assertEqual((summary["attempted"], summary["fetched"]), (2, 2))
            self.assertEqual(run_clean(dig)["cleaned"], 3)
            self.assertEqual(run_report(dig)["fetched"], 3)

    def test_cli_accepts_a_legacy_dig(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._legacy_dig(tmp)
            self.assertEqual(main(["report", "--dig", str(dig.root)]), 0)
            self.assertTrue((dig.root / "VERIFICATION-REPORT.md").exists())

    def test_hand_edited_json_wins_over_the_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            rows = caps(2)
            rows[0]["fetch_state"] = "failed_4xx"
            dig.save_captures(rows)
            # the operator forces a retry by editing the file they can see
            edited = json.loads(dig.json_path.read_text())
            edited[0]["fetch_state"] = "pending"
            dig.json_path.write_text(json.dumps(edited, indent=2))
            self.assertEqual(dig.load_captures()[0]["fetch_state"], "pending")
            self.assertEqual(dig.state_counts()["pending"], 2)
            # and the edit is now the database's state too — not re-imported forever
            dig.json_path.unlink()
            self.assertEqual(dig.load_captures()[0]["fetch_state"], "pending")

    def test_stale_export_does_not_overwrite_newer_database_state(self):
        # a killed pull leaves captures.json behind the database; the database must win
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures(caps(2))
            row = dig.load_captures()[0]
            row["fetch_state"] = "fetched"
            dig.save_capture(row)  # …and the process dies before the export
            self.assertEqual(json.loads(dig.json_path.read_text())[0]["fetch_state"], "pending")
            self.assertEqual(dig.load_captures()[0]["fetch_state"], "fetched")


class TestPullOnTheStore(unittest.TestCase):
    def test_kill_mid_run_loses_nothing_and_refetches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures(caps(6))
            seen: list[str] = []

            def dying(request: httpx.Request) -> httpx.Response:
                if len(seen) == 3:
                    raise KeyboardInterrupt  # the operator hits Ctrl-C on the 4th request
                seen.append(str(request.url))
                return httpx.Response(200, content=b"<html>x</html>")

            with self.assertRaises(KeyboardInterrupt):
                pull(dig, dying)
            self.assertEqual(dig.state_counts()["fetched"], 3)
            # the export was refreshed on the way out
            self.assertEqual(sum(c["fetch_state"] == "fetched" for c in json.loads(dig.json_path.read_text())), 3)

            again: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                again.append(str(request.url))
                return httpx.Response(200, content=b"<html>x</html>")

            summary = pull(dig, handler)
            self.assertEqual((summary["attempted"], summary["fetched"]), (3, 3))
            self.assertEqual(set(seen) & set(again), set())  # zero duplicate fetches (spec acceptance test 3)
            self.assertEqual(dig.state_counts()["fetched"], 6)

    def test_export_is_written_once_per_run_not_once_per_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures(caps(40))
            exports = {"n": 0}
            original = dig.export_captures_json

            def counting() -> None:
                exports["n"] += 1
                original()

            dig.export_captures_json = counting  # type: ignore[method-assign]
            pull(dig)
            self.assertEqual(exports["n"], 1)
            self.assertEqual(dig.state_counts()["fetched"], 40)

    def test_large_dig_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures(caps(20_000))
            for row in dig.load_captures()[:200]:
                row["fetch_state"] = "fetched"
                dig.save_capture(row)
            counts = dig.state_counts()
            self.assertEqual((counts["fetched"], counts["pending"]), (200, 19_800))

    def test_reinventory_merges_through_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            rows = [cdx_row("http://x.com/", "20010101000000", "text/html", "200")]
            apply_inventory(dig, rows)
            pull(dig)
            apply_inventory(dig, rows)
            self.assertEqual(dig.state_counts()["fetched"], 1)


if __name__ == "__main__":
    unittest.main()
