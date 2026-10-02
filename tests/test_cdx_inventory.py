"""SOTA loop 5 — CDX inventory robustness (offline; mocked CDX API)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from excavate.cdx import CDX_PAGE_SIZE, CdxQuery, fetch_cdx_pages, run_inventory
from excavate.cli import main
from excavate.dig import MANIFEST_FIELDS, Dig
from excavate.pull import BACKOFF_SCHEDULE, run_pull
from test_m0 import CDX_HEADER, mock_client, no_wait_limiter


def row(url: str, ts: str = "20030601000000", status: str = "200", mime: str = "text/html") -> list[str]:
    return ["com,x)/", ts, url, mime, status, "DIGEST", "100"]


class PagedCdx:
    """A CDX API that serves `pages` in order, keyed by resumeKey."""

    def __init__(self, pages: list[list[list[str]]], fail_first: list[int] | None = None) -> None:
        self.pages = pages
        self.requests: list[httpx.Request] = []
        self.fail_first = list(fail_first or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_first:
            return httpx.Response(self.fail_first.pop(0))
        key = request.url.params.get("resumeKey")
        index = 0 if key is None else int(key.removeprefix("key"))
        body: list[list[str]] = [CDX_HEADER, *self.pages[index]]
        if index + 1 < len(self.pages):
            body += [[], [f"key{index + 1}"]]
        return httpx.Response(200, json=body)


def fetch(api: PagedCdx, query: CdxQuery | None = None, sleeps: list | None = None):
    sleeps = sleeps if sleeps is not None else []
    return fetch_cdx_pages(query or CdxQuery("x.com"), mock_client(api), no_wait_limiter(), sleep_fn=sleeps.append)


class TestPagination(unittest.TestCase):
    def test_follows_resume_keys_and_keeps_every_row(self):
        api = PagedCdx([[row("http://x.com/a"), row("http://x.com/b")], [row("http://x.com/c")], [row("http://x.com/d")]])
        rows, pages = fetch(api)
        self.assertEqual(pages, 3)
        self.assertEqual([r["original"] for r in rows], ["http://x.com/a", "http://x.com/b", "http://x.com/c", "http://x.com/d"])
        self.assertEqual([r.url.params.get("resumeKey") for r in api.requests], [None, "key1", "key2"])

    def test_single_page_without_resume_key(self):
        rows, pages = fetch(PagedCdx([[row("http://x.com/")]]))
        self.assertEqual((len(rows), pages), (1, 1))

    def test_request_shape_never_filters(self):
        api = PagedCdx([[row("http://x.com/")]])
        fetch(api)
        params = dict(api.requests[0].url.params)
        self.assertEqual(params, {"output": "json", "showResumeKey": "true", "limit": str(CDX_PAGE_SIZE), "url": "x.com*"})
        for forbidden in ("collapse", "filter", "fl"):
            self.assertNotIn(forbidden, params)  # the strops invariant holds at the wire

    def test_ragged_rows_on_any_page_skipped(self):
        api = PagedCdx([[row("http://x.com/a"), ["short"]], [["also", "short"], row("http://x.com/b")]])
        rows, _ = fetch(api)
        self.assertEqual(len(rows), 2)

    def test_resume_key_loop_is_an_error_not_a_hang(self):
        def looping(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[CDX_HEADER, row("http://x.com/"), [], ["same-key"]])

        with self.assertRaises(ValueError) as ctx:
            fetch_cdx_pages(CdxQuery("x.com"), mock_client(looping), no_wait_limiter(), sleep_fn=lambda s: None)
        self.assertIn("did not terminate", str(ctx.exception))

    def test_empty_later_page_ends_cleanly(self):
        api = PagedCdx([[row("http://x.com/a")], []])
        rows, pages = fetch(api)
        self.assertEqual((len(rows), pages), (1, 2))


class TestScope(unittest.TestCase):
    def test_match_types_and_date_bounds_on_the_wire(self):
        api = PagedCdx([[row("http://sub.x.com/")]])
        fetch(api, CdxQuery("x.com", match="domain", from_ts="1999", to_ts="20031231"))
        params = dict(api.requests[0].url.params)
        self.assertEqual((params["url"], params["matchType"], params["from"], params["to"]),
                         ("x.com", "domain", "1999", "20031231"))
        api = PagedCdx([[row("http://x.com/")]])
        fetch(api, CdxQuery("x.com", match="host"))
        self.assertEqual(dict(api.requests[0].url.params)["matchType"], "host")

    def test_bad_scope_rejected_before_any_request(self):
        for kwargs in ({"match": "regex"}, {"from_ts": "03"}, {"to_ts": "2003-06-01"},
                       {"from_ts": "2005", "to_ts": "2003"}, {"from_ts": "20030701", "to_ts": "200306"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CdxQuery("x.com", **kwargs)
        CdxQuery("x.com", from_ts="2003", to_ts="2003")  # same year is a valid window


class TestBackoff(unittest.TestCase):
    def test_429_and_5xx_backed_off_then_succeeds(self):
        api = PagedCdx([[row("http://x.com/")]], fail_first=[429, 503])
        sleeps: list = []
        rows, _ = fetch(api, sleeps=sleeps)
        self.assertEqual(len(rows), 1)
        self.assertEqual(sleeps, list(BACKOFF_SCHEDULE[:2]))

    def test_exhaustion_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            api = PagedCdx([[row("http://x.com/")]], fail_first=[429] * 10)
            with self.assertRaises(httpx.HTTPStatusError):
                run_inventory("x.com", dig, mock_client(api), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertEqual(len(api.requests), len(BACKOFF_SCHEDULE) + 1)
            self.assertFalse((dig.root / "captures.json").exists())  # no partial inventory
            self.assertFalse(dig.raw_manifest_path().exists())

    def test_failure_on_a_later_page_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            calls = {"n": 0}

            def handler(request: httpx.Request) -> httpx.Response:
                calls["n"] += 1
                if calls["n"] == 1:
                    return httpx.Response(200, json=[CDX_HEADER, row("http://x.com/a"), [], ["k"]])
                return httpx.Response(500)

            with self.assertRaises(httpx.HTTPStatusError):
                run_inventory("x.com", dig, mock_client(handler), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertFalse((dig.root / "captures.json").exists())

    def test_transport_error_retried(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("reset")
            return httpx.Response(200, json=[CDX_HEADER, row("http://x.com/")])

        rows, _ = fetch_cdx_pages(CdxQuery("x.com"), mock_client(handler), no_wait_limiter(), sleep_fn=lambda s: None)
        self.assertEqual(len(rows), 1)

    def test_cdx_requests_go_through_the_rate_limiter(self):
        waits = {"n": 0}

        class Counting:
            def wait(self) -> float:
                waits["n"] += 1
                return 0.0

        api = PagedCdx([[row("http://x.com/a")], [row("http://x.com/b")]], fail_first=[503])
        fetch_cdx_pages(CdxQuery("x.com"), mock_client(api), Counting(), sleep_fn=lambda s: None)  # type: ignore[arg-type]
        self.assertEqual(waits["n"], 3)  # every attempt, including the retried one


class TestManifestJson(unittest.TestCase):
    def test_written_at_inventory_and_refreshed_by_pull(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            api = PagedCdx([[row("http://x.com/a.html")], [row("http://x.com/b.html", status="404")]])
            summary = run_inventory("x.com", dig, mock_client(api), CdxQuery("x.com", from_ts="2003"),
                                    limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertEqual((summary["raw_rows"], summary["pages"], summary["unique_urls"]), (2, 2, 2))
            manifest = json.loads((dig.root / "manifest.json").read_text())
            self.assertEqual(manifest["domain"], "x.com")
            self.assertEqual(manifest["query"], {"match": "prefix", "from": "2003", "to": None})
            self.assertEqual(manifest["raw_rows"], 2)
            self.assertTrue(manifest["inventoried_at"])
            self.assertEqual([tuple(c) for c in manifest["captures"]], [MANIFEST_FIELDS] * 2)  # exactly the spec's fields
            self.assertEqual([c["fetch_state"] for c in manifest["captures"]], ["pending", "skipped"])

            run_pull(dig, mock_client(lambda r: httpx.Response(200, content=b"<html>a</html>")),
                     limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            after = json.loads((dig.root / "manifest.json").read_text())["captures"][0]
            self.assertEqual(after["fetch_state"], "fetched")
            self.assertTrue(after["sha1_fetched"])
            self.assertEqual(len(dig.raw_manifest_rows()), 2)  # manifest.csv still every raw row


class TestCliValidation(unittest.TestCase):
    def test_bad_date_is_a_clean_error_and_creates_no_dig(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["inventory", "x.com", "--output", tmp, "--from", "junk"]), 1)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
