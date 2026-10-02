"""SOTA loop 10 — never-crawled recovery (offline; mocked CDX API)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from excavate.cdx import apply_inventory
from excavate.clean import external_hosts, run_clean
from excavate.crawl import analyze
from excavate.dig import Dig
from excavate.discover import candidate_urls, run_discover
from excavate.pull import run_pull
from excavate.report import run_report
from test_m0 import CDX_HEADER, cdx_row, mock_client, no_wait_limiter

INDEX = (b'<a href="/lost.html">lost</a><a href="/old/">old dir</a><img src="/img/pic.gif">'
         b'<a href="/nowhere.html">nowhere</a><a href="/s.php?id=5">query</a>'
         b'<img src="http://img.x-cdn.com/a.gif"><img src="http://img.x-cdn.com/b.gif"><a href="http://www2.x.com/">w2</a>')


def cdx_reply(*rows: list[str]) -> httpx.Response:
    return httpx.Response(200, json=[CDX_HEADER, *rows] if rows else [])


def line(url: str, ts: str = "20030601000000", status: str = "200") -> list[str]:
    return ["key", ts, url, "text/html", status, "DIG", "10"]


class Cdx:
    """Exact-match CDX: url (as asked) → rows."""

    def __init__(self, holdings: dict[str, list[list[str]]], fail_on: str | None = None) -> None:
        self.holdings = holdings
        self.asked: list[str] = []
        self.fail_on = fail_on

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url.params["url"]
        self.asked.append(url)
        assert "matchType" not in request.url.params and "collapse" not in request.url.params
        if url == self.fail_on:
            return httpx.Response(404)
        return cdx_reply(*self.holdings.get(url, []))


def make_dig(tmp: str) -> Dig:
    dig = Dig.create(Path(tmp), "x.com")
    apply_inventory(dig, [cdx_row("http://x.com/", "20030601000000", "text/html", "200")])
    run_pull(dig, mock_client(lambda r: httpx.Response(200, content=INDEX)), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
    run_clean(dig)
    return dig


def discover(dig: Dig, cdx: Cdx, **kwargs) -> dict:
    return run_discover(dig, mock_client(cdx), limiter=no_wait_limiter(), sleep_fn=lambda s: None, **kwargs)


HOLDINGS = {
    "x.com/lost.html": [line("http://www.x.com/lost.html", "19990101000000"), line("http://www.x.com/lost.html", "20010101000000")],
    "x.com/old/": [line("http://x.com/old/")],
    "x.com/img/pic.gif": [line("http://x.com/img/pic.gif", status="404")],
}


class TestCandidateUrls(unittest.TestCase):
    def test_reverse_mapping(self):
        self.assertEqual(candidate_urls("x.com", "a/b.html"), ["x.com/a/b.html"])
        self.assertEqual(candidate_urls("x.com", "old/index.html"), ["x.com/old/", "x.com/old/index.html"])
        self.assertEqual(candidate_urls("x.com", "index.html"), ["x.com/", "x.com/index.html"])
        self.assertEqual(candidate_urls("x.com", "my file#1.html"), ["x.com/my%20file%231.html"])

    def test_query_hashed_names_are_not_reversible(self):
        self.assertEqual(candidate_urls("x.com", "s-0a1b2c3d.php"), [])
        self.assertEqual(candidate_urls("x.com", "page-0a1b2c3d"), [])
        self.assertEqual(candidate_urls("x.com", "well-being.html"), ["x.com/well-being.html"])


class TestDiscover(unittest.TestCase):
    def test_finds_adds_and_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp)
            cdx = Cdx(HOLDINGS)
            summary = discover(dig, cdx)
            self.assertEqual(summary["never_archived"], 5)
            self.assertEqual((summary["looked_up"], summary["found"], summary["fetchable"], summary["not_fetchable"]), (4, 3, 2, 1))
            self.assertEqual((summary["still_missing"], summary["irreversible"], summary["deferred"]), (1, 1, 0))
            # the directory hit on its first candidate; nowhere.html needed one lookup
            self.assertEqual(sorted(cdx.asked), ["x.com/img/pic.gif", "x.com/lost.html", "x.com/nowhere.html", "x.com/old/"])
            self.assertEqual(summary["lookups"], 4)

            caps = {c["url"]: c for c in dig.load_captures()}
            lost = caps["http://www.x.com/lost.html"]
            self.assertEqual((lost["fetch_state"], lost["timestamp"]), ("pending", "20010101000000"))  # latest capture chosen
            self.assertEqual(len(lost["alternates"]), 1)
            self.assertIn("lost.html", lost["discovered_by"])
            self.assertEqual(caps["http://x.com/img/pic.gif"]["fetch_state"], "skipped")  # archived only as a 404: disclosed, not fetched
            self.assertEqual(len(dig.raw_manifest_rows()), 1 + 4)  # every looked-up row is in the audit trail
            manifest = json.loads((dig.root / "manifest.json").read_text())
            self.assertEqual(len(manifest["captures"]), 4)

    def test_pull_clean_report_after_discover_recovers_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp)
            self.assertEqual(analyze(dig)["counts"]["never_archived"], 5)
            discover(dig, Cdx(HOLDINGS))
            seen: list[str] = []

            def replay(request: httpx.Request) -> httpx.Response:
                seen.append(str(request.url))
                return httpx.Response(200, content=b"<html>recovered</html>")

            run_pull(dig, mock_client(replay), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertEqual(len(seen), 2)  # only the two new fetchable captures; index.html not re-fetched
            run_clean(dig)
            self.assertTrue((dig.site_dir / "lost.html").exists())
            self.assertTrue((dig.site_dir / "old" / "index.html").exists())
            crawl = analyze(dig)
            self.assertEqual(sorted(e["target"] for e in crawl["never_archived"]), ["nowhere.html", "s-" + _hash("id=5") + ".php"])
            self.assertEqual([e["target"] for e in crawl["archived_not_recovered"]], ["img/pic.gif"])
            report = run_report(dig)
            self.assertEqual(report["discovered"], 3)
            self.assertIn("added by `discover`", (dig.root / "VERIFICATION-REPORT.md").read_text())

    def test_second_run_asks_nothing_and_recheck_asks_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp)
            discover(dig, Cdx(HOLDINGS))
            again = Cdx(HOLDINGS)
            summary = discover(dig, again)
            self.assertEqual(again.asked, [])
            self.assertEqual((summary["looked_up"], summary["found"]), (0, 0))
            rechecked = Cdx(HOLDINGS)
            discover(dig, rechecked, recheck=True)
            # nowhere.html is still never-archived; the others are in the manifest now, so only it (and the
            # irreversible query page, which costs no request) are reconsidered
            self.assertEqual(rechecked.asked, ["x.com/nowhere.html"])

    def test_limit_defers_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp)
            first = Cdx(HOLDINGS)
            summary = discover(dig, first, limit=2)
            self.assertEqual((summary["looked_up"], summary["deferred"]), (2, 2))
            self.assertEqual(len(first.asked), 2)
            second = Cdx(HOLDINGS)
            summary = discover(dig, second)
            self.assertEqual((summary["looked_up"], summary["deferred"]), (2, 0))
            self.assertEqual(set(first.asked) & set(second.asked), set())

    def test_error_midway_keeps_what_was_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp)
            with self.assertRaises(httpx.HTTPStatusError):
                discover(dig, Cdx(HOLDINGS, fail_on="x.com/nowhere.html"))
            urls = {c["url"] for c in dig.load_captures()}
            self.assertIn("http://www.x.com/lost.html", urls)  # found before the failure: kept
            memo = json.loads((dig.root / "discover.json").read_text())
            self.assertNotIn("nowhere.html", memo)  # the failed lookup is not recorded as "absent"

    def test_reinventory_keeps_discovered_captures(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp)
            discover(dig, Cdx(HOLDINGS))
            apply_inventory(dig, [cdx_row("http://x.com/", "20030601000000", "text/html", "200")])
            caps = {c["url"]: c for c in dig.load_captures()}
            self.assertIn("http://www.x.com/lost.html", caps)
            self.assertEqual(caps["http://x.com/"]["fetch_state"], "fetched")

    def test_complete_site_costs_no_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            apply_inventory(dig, [cdx_row("http://x.com/", "20030601000000", "text/html", "200")])
            run_pull(dig, mock_client(lambda r: httpx.Response(200, content=b"<p>no links</p>")),
                     limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            run_clean(dig)
            cdx = Cdx({})
            self.assertEqual(discover(dig, cdx)["looked_up"], 0)
            self.assertEqual(cdx.asked, [])


class TestExternalHosts(unittest.TestCase):
    def test_hosts_counted_and_reported(self):
        self.assertEqual(sorted(external_hosts(INDEX.decode(), "x.com", "index.html")),
                         ["img.x-cdn.com", "img.x-cdn.com", "www2.x.com"])
        self.assertEqual(external_hosts('<a href="http://www.x.com/a">same site</a><a href="mailto:a@b.com">m</a>', "x.com"), [])
        with tempfile.TemporaryDirectory() as tmp:
            dig = make_dig(tmp)
            self.assertEqual(analyze(dig)["external_hosts"],
                             [{"host": "img.x-cdn.com", "links": 2}, {"host": "www2.x.com", "links": 1}])
            run_report(dig)
            self.assertIn("- `img.x-cdn.com` — 2 links", (dig.root / "VERIFICATION-REPORT.md").read_text())


def _hash(query: str) -> str:
    import hashlib

    return hashlib.sha1(query.encode()).hexdigest()[:8]


if __name__ == "__main__":
    unittest.main()
