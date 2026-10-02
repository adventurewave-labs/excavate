"""SOTA loop 14 — operator UX: run, status, --json, exit codes, progress, dry-run (offline)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from excavate import cli
from excavate.cdx import apply_inventory
from excavate.clean import run_clean
from excavate.dig import Dig
from excavate.pull import run_pull
from excavate.scan import run_scan
from excavate.status import dig_status, eta_minutes, pull_plan
from test_m0 import CDX_HEADER, cdx_row, mock_client, no_wait_limiter

SITE = {
    "http://x.com/": b'<a href="/about.html">about</a><img src="/logo.gif"><a href="/lost.html">lost</a>',
    "http://x.com/about.html": b'<a href="/">home</a>',
    "http://x.com/logo.gif": b"GIF89a",
}


def archive(site: dict[str, bytes] | None = None, flaky: set[str] | None = None):
    """A tiny Internet Archive: the CDX API and Wayback replay for `site`."""
    site = SITE if site is None else site
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/cdx/search/cdx":
            asked = request.url.params["url"]
            if asked.endswith("*"):
                rows = [["k", "20030601000000", url, "text/html", "200", "D", "1"] for url in sorted(site)]
            else:
                rows = [["k", "20030601000000", url, "text/html", "200", "D", "1"] for url in site
                        if url.split("://", 1)[1] == asked]
            return httpx.Response(200, json=[CDX_HEADER, *rows] if rows else [])
        original = request.url.path.split("/", 3)[3]
        if flaky and original in flaky:
            return httpx.Response(503)
        url = original if original in site else original + "/"
        return httpx.Response(200, content=site[url]) if url in site else httpx.Response(404)

    handler.requests = requests  # type: ignore[attr-defined]
    return handler


def invoke(argv: list[str], handler=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(cli, "_client", lambda: mock_client(handler or archive())), \
            mock.patch.object(cli, "_limiter", no_wait_limiter), \
            mock.patch("excavate.pull.time.sleep", lambda s: None), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse / command refusals
            code = exc.code if isinstance(exc.code, int) else 1
            err.write(str(exc.code))
    return code, out.getvalue(), err.getvalue()


class TestRun(unittest.TestCase):
    def test_whole_pipeline_in_one_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = invoke(["run", "x.com", "--output", tmp])
            self.assertEqual(code, 0, err)
            dig = Dig(Path(tmp) / "x.com")
            self.assertEqual(dig.state_counts()["fetched"], 3)
            self.assertIn('href="about.html"', (dig.site_dir / "index.html").read_text())
            for artifact in ("manifest.csv", "manifest.json", "crawl.json", "scan.json", "events.jsonl",
                             "REWRITES.md", "VERIFICATION-REPORT.md"):
                self.assertTrue((dig.root / artifact).exists(), artifact)
            self.assertIn("run: 3 files recovered", out)
            self.assertIn("publish gate: clean", out)
            self.assertIn("[3/3] fetched", err)  # progress goes to stderr, not stdout
            self.assertNotIn("[3/3]", out)

    def test_strict_fails_on_an_unresolved_link_but_plain_run_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(invoke(["run", "x.com", "--output", tmp])[0], 0)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(invoke(["run", "x.com", "--output", tmp, "--strict"])[0], 1)  # /lost.html was never archived
        complete = {url: body.replace(b'<a href="/lost.html">lost</a>', b"") for url, body in SITE.items()}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(invoke(["run", "x.com", "--output", tmp, "--strict"], archive(complete))[0], 0)

    def test_limit_leaves_a_resumable_dig_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = invoke(["run", "x.com", "--output", tmp, "--limit", "1"])
            self.assertEqual(code, 0)
            self.assertIn("2 URLs still to pull", out)
            dig = Dig(Path(tmp) / "x.com")
            self.assertEqual(invoke(["run", "x.com", "--output", tmp, "--limit", "1", "--strict"])[0], 1)
            code, out, _ = invoke(["pull", "--dig", str(dig.root), "--quiet"])
            self.assertEqual(dig.state_counts()["fetched"], 3)

    def test_discover_flag_recovers_a_linked_file_outside_the_inventory_window(self):
        site = {**SITE, "http://x.com/lost.html": b"<p>found</p>"}
        base = archive(site)

        def windowed(request: httpx.Request) -> httpx.Response:
            # the prefix inventory does not return lost.html (as a --from/--to window would hide it)
            if request.url.path == "/cdx/search/cdx" and request.url.params["url"].endswith("*"):
                rows = [["k", "20030601000000", url, "text/html", "200", "D", "1"] for url in sorted(SITE)]
                return httpx.Response(200, json=[CDX_HEADER, *rows])
            return base(request)

        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = invoke(["run", "x.com", "--output", tmp, "--discover", "--strict"], windowed)
            self.assertEqual(code, 0, err)
            self.assertEqual((Path(tmp) / "x.com" / "site" / "lost.html").read_bytes(), b"<p>found</p>")

    def test_bad_arguments_stop_before_anything_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(invoke(["run", "x.com", "--output", tmp, "--at", "junk"])[0], 1)
            self.assertEqual(invoke(["run", "not a domain", "--output", tmp])[0], 1)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class TestJson(unittest.TestCase):
    def test_every_command_emits_one_json_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = str(Path(tmp) / "x.com")
            steps = [(["inventory", "x.com", "--output", tmp], 0), (["pull", "--dig", dig], 0), (["clean", "--dig", dig], 0),
                     (["check", "--dig", dig], 0), (["discover", "--dig", dig], 0), (["scan", "--dig", dig], 0),
                     (["report", "--dig", dig], 0), (["status", "--dig", dig], 0), (["export", "--dig", dig], 0),
                     (["export", "--verify", str(Path(dig) / "export" / "x.com-bag")], 0)]
            for argv, expected in steps:
                with self.subTest(command=argv[0]):
                    code, out, _ = invoke([*argv, "--json"])
                    payload = json.loads(out)  # stdout is exactly one JSON document
                    self.assertEqual((code, payload["exit_code"], payload["command"]), (expected, expected, argv[0]))
                    self.assertIsInstance(payload["result"], dict)
                    self.assertIsInstance(payload["messages"], list)

    def test_flag_works_before_or_after_the_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            invoke(["run", "x.com", "--output", tmp, "--quiet"])
            dig = str(Path(tmp) / "x.com")
            before = json.loads(invoke(["--json", "status", "--dig", dig])[1])
            after = json.loads(invoke(["status", "--dig", dig, "--json"])[1])
            self.assertEqual(before["result"]["states"], after["result"]["states"])
            self.assertEqual(before["result"]["states"]["fetched"], 3)

    def test_gate_failure_is_json_with_a_nonzero_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = {**SITE, "http://x.com/users.txt": b"sysop:$" + b"apr1$" + b"fakesalt$fakefakefakefakefakefa\n"}
            code, out, _ = invoke(["run", "x.com", "--output", tmp, "--json", "--strict"], archive(site))
            payload = json.loads(out)
            self.assertEqual((code, payload["exit_code"]), (1, 1))
            self.assertEqual(payload["result"]["publish_gate"], "findings")
            self.assertEqual(payload["result"]["stages"]["scan"]["blocking"], 1)
            code, out, _ = invoke(["scan", "--dig", str(Path(tmp) / "x.com"), "--json"])
            self.assertEqual((code, json.loads(out)["result"]["clean"]), (1, False))

    def test_errors_are_json_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = invoke(["status", "--dig", str(Path(tmp) / "nope"), "--json"])
            payload = json.loads(out)
            self.assertEqual((code, payload["exit_code"]), (1, 1))
            self.assertIn("not a dig directory", payload["error"])
            code, out, _ = invoke(["inventory", "x.com", "--output", tmp, "--from", "junk", "--json"])
            self.assertIn("--from", json.loads(out)["error"])

    def test_text_mode_is_unchanged_and_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = invoke(["inventory", "x.com", "--output", tmp])
            self.assertTrue(out.startswith("inventoried x.com: 3 raw CDX rows"))
            self.assertEqual(code, 0)


class TestStatusAndPlan(unittest.TestCase):
    def _dig(self, tmp: str) -> Dig:
        dig = Dig.create(Path(tmp), "x.com")
        apply_inventory(dig, [cdx_row(url, "20030601000000", "text/html", "200") for url in SITE])
        return dig

    def test_eta_is_the_rate_budget(self):
        self.assertEqual([eta_minutes(n) for n in (0, 1, 30, 31, 921)], [0, 1, 1, 2, 31])

    def test_next_step_follows_the_dig_through_the_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            status = dig_status(dig)
            self.assertEqual((status["urls"], status["pull"]["to_fetch"], status["site_files"]), (3, 3, 0))
            self.assertTrue(status["next"].startswith("excavate pull"))
            run_pull(dig, mock_client(archive()), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertEqual(dig_status(dig)["next"], "excavate clean")
            run_clean(dig)
            status = dig_status(dig)
            self.assertEqual((status["site_files"], status["publish_gate"]), (3, "not run"))
            self.assertTrue(status["next"].startswith("excavate scan"))
            run_scan(dig)
            self.assertIn("excavate export", dig_status(dig)["next"])
            # a later pull makes the cleaned site stale again
            caps = dig.load_captures()
            caps[0]["fetch_state"] = "pending"
            dig.save_captures(caps)
            run_pull(dig, mock_client(archive()), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            self.assertEqual(dig_status(dig)["next"], "excavate clean")

    def test_transient_failures_count_as_work_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            run_pull(dig, mock_client(archive(flaky={"http://x.com/logo.gif"})), limiter=no_wait_limiter(), sleep_fn=lambda s: None)
            plan = pull_plan(dig)
            self.assertEqual((plan["pending"], plan["transient_retries"], plan["to_fetch"], plan["eta_minutes"]), (0, 1, 1, 1))
            self.assertEqual(pull_plan(dig, limit=0)["this_run"], 1)

    def test_status_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = self._dig(tmp)
            code, out, _ = invoke(["status", "--dig", str(dig.root)])
            self.assertEqual(code, 0)
            self.assertIn("3 URLs: 0 fetched, 3 pending", out)
            self.assertIn("next: excavate pull", out)


class TestPullUx(unittest.TestCase):
    def test_dry_run_fetches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            apply_inventory(dig, [cdx_row(f"http://x.com/{i}.html", "20030601000000", "text/html", "200") for i in range(921)])
            handler = archive()
            code, out, _ = invoke(["pull", "--dig", str(dig.root), "--dry-run"], handler)
            self.assertEqual(code, 0)
            self.assertIn("921 URLs this run", out)
            self.assertIn("at least 31 min at 30 req/min", out)  # the README's magpu example
            self.assertEqual(handler.requests, [])
            self.assertEqual(dig.state_counts()["pending"], 921)
            self.assertIn("100 URLs this run", invoke(["pull", "--dig", str(dig.root), "--dry-run", "--limit", "100"], handler)[1])

    def test_progress_callback_and_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            apply_inventory(dig, [cdx_row(url, "20030601000000", "text/html", "200") for url in SITE])
            seen: list[tuple[int, int, str]] = []
            run_pull(dig, mock_client(archive()), limiter=no_wait_limiter(), sleep_fn=lambda s: None,
                     progress=lambda done, total, cap: seen.append((done, total, cap["fetch_state"])))
            self.assertEqual(seen, [(1, 3, "fetched"), (2, 3, "fetched"), (3, 3, "fetched")])
        with tempfile.TemporaryDirectory() as tmp:
            invoke(["inventory", "x.com", "--output", tmp])
            _, _, err = invoke(["pull", "--dig", str(Path(tmp) / "x.com"), "--quiet"])
            self.assertEqual(err, "")

    def test_help_documents_exit_codes(self):
        code, out, _ = invoke(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("exit codes:", out)
        for command in ("inventory", "pull", "clean", "check", "discover", "scan", "export", "status", "run", "report"):
            self.assertIn(command, out)


if __name__ == "__main__":
    unittest.main()
