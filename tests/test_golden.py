"""SOTA loop 15 — one golden end-to-end run.

A small synthetic site, served by a mocked Internet Archive (CDX API + Wayback
replay), goes through the real CLI: run (inventory → pull → discover → clean →
check → scan → report), then status and export. It is built to hit every
behaviour the pipeline promises at once:

  latin-1 page · unquoted attributes · absolute / root / relative links ·
  CSS url() · digest verification · im_ fallback with Wayback chrome ·
  alternate-capture fallback · case twin · .mp3→.ogg twin · a never-archived
  link · a link archived only as a 404 · revisit and ragged CDX rows ·
  an external link

The expected outputs below are the contract. Nothing here touches the network.
"""

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
from excavate.bagit import verify_bag
from excavate.dig import Dig, sha1_base32
from excavate.provenance import load_events
from test_m0 import CDX_HEADER, mock_client, no_wait_limiter

D = "golden.test"

INDEX = (
    '<html><head><meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">\r\n'
    "<link rel=stylesheet href=/css/site.css>\r\n"
    f'</head><body background="http://{D}/img/bg.gif">\r\n'
    "<h1>Café Golden, señor</h1>\r\n"
    f'<a href="http://www.{D}/music/">music</a>\r\n'
    '<a href="/old/page.html#top">old</a>\r\n'
    '<a href="gone.html">gone</a>\r\n'
    '<img src="/images/logo.gif">\r\n'
    '<a href="/never.html">never</a>\r\n'
    '<a href="/dead.html">dead</a>\r\n'
    '<a href="http://elsewhere.example/">out</a>\r\n'
    "</body></html>\r\n"
).encode("iso-8859-1")

INDEX_CLEAN = (
    INDEX.replace(b"href=/css/site.css", b"href=css/site.css")
    .replace(f'background="http://{D}/img/bg.gif"'.encode(), b'background="img/bg.gif"')
    .replace(f'href="http://www.{D}/music/"'.encode(), b'href="music/index.html"')
    .replace(b'href="/old/page.html#top"', b'href="old/page.html#top"')
    .replace(b'src="/images/logo.gif"', b'src="Images/Logo.GIF"')       # case twin
    .replace(b'href="/never.html"', b'href="never.html"')
    .replace(b'href="/dead.html"', b'href="dead.html"')
)

CSS = b"body { background: url(/img/bg.gif); }\n"
CSS_CLEAN = b"body { background: url(../img/bg.gif); }\n"
GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00;"
OGG = b"OggS\x00\x02golden-song"
MUSIC = b'<a href="song.mp3">the song</a> <a href="../index.html">home</a>\n'
MUSIC_CLEAN = b'<a href="song.ogg">the song</a> <a href="../index.html">home</a>\n'   # format twin
OLD_ORIGINAL = b'<html><body><a name="top"></a><p>old page</p><a href="index.html">home</a></body></html>\n'
OLD_REPLAYED = (
    b'<html><body><!-- BEGIN WAYBACK TOOLBAR INSERT -->\n<div id="wm-ipp-base">toolbar</div>\n<!-- END WAYBACK TOOLBAR INSERT -->'
    b'<a name="top"></a><p>old page</p>'
    b'<a href="/web/20040101000000/http://' + D.encode() + b'/">home</a></body></html>\n'
    b"<!--\n     FILE ARCHIVED ON 00:00:00 Jan 01, 2004 AND RETRIEVED FROM THE\n     INTERNET ARCHIVE ON 20:25:12 Oct 02, 2026.\n-->\n"
)
OLD_CLEAN = (b'<html><body><!-- wayback chrome removed --><a name="top"></a><p>old page</p>'
             b'<a href="../index.html">home</a></body></html>\n')
GONE_2002 = b"<p>the 2002 edition of this page</p>\n"


def row(path: str, ts: str, body: bytes | None = None, status: str = "200", mime: str = "text/html") -> list[str]:
    return [f"test,golden)/{path}", ts, f"http://{D}/{path}", mime, status, sha1_base32(body) if body else "-", "1"]


CDX_ROWS = [
    row("", "20030601000000", INDEX),
    row("css/site.css", "20030601000001", CSS, mime="text/css"),
    row("img/bg.gif", "20030601000002", GIF, mime="image/gif"),
    row("music/", "20030601000003", MUSIC),
    row("music/", "20060101000000", None, mime="warc/revisit"),         # must not displace the real capture
    row("music/song.ogg", "20030601000004", OGG, mime="audio/ogg"),
    row("old/page.html", "20040101000000", OLD_ORIGINAL),
    row("gone.html", "20020101000000", GONE_2002),
    row("gone.html", "20050101000000", b"<p>2005</p>"),                  # the wanted (latest) capture: gone from replay
    row("Images/Logo.GIF", "20030601000005", GIF, mime="image/gif"),
    row("dead.html", "20030601000006", None, status="404"),
    ["ragged", "row"],
]

REPLAY: dict[tuple[str, str], dict[str, bytes]] = {
    ("", "20030601000000"): {"id_": INDEX},
    ("css/site.css", "20030601000001"): {"id_": CSS},
    ("img/bg.gif", "20030601000002"): {"id_": GIF},
    ("music/", "20030601000003"): {"id_": MUSIC},
    ("music/song.ogg", "20030601000004"): {"id_": OGG},
    ("old/page.html", "20040101000000"): {"im_": OLD_REPLAYED},        # id_ 404s; only the replay view survives
    ("gone.html", "20020101000000"): {"id_": GONE_2002},
    ("Images/Logo.GIF", "20030601000005"): {"id_": GIF},
}


def archive(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/cdx/search/cdx":
        if request.url.params["url"].endswith("*"):
            return httpx.Response(200, json=[CDX_HEADER, *CDX_ROWS])
        return httpx.Response(200, json=[])                              # exact lookups (discover) find nothing
    _, _, stamp_mode, original = request.url.path.split("/", 3)
    bodies = REPLAY.get((original.split(f"{D}/", 1)[1], stamp_mode[:14]), {})
    body = bodies.get(stamp_mode[14:])
    return httpx.Response(200, content=body) if body is not None else httpx.Response(404)


def excavate(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with mock.patch.object(cli, "_client", lambda: mock_client(archive)), \
            mock.patch.object(cli, "_limiter", no_wait_limiter), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(list(argv))
    return code, out.getvalue()


class TestGoldenRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.code, cls.out = excavate("run", D, "--output", cls.tmp.name, "--discover", "--json")
        cls.payload = json.loads(cls.out)
        cls.dig = Dig(Path(cls.tmp.name) / D)
        cls.report = (cls.dig.root / "VERIFICATION-REPORT.md").read_text(encoding="utf-8")
        cls.ledger = (cls.dig.root / "REWRITES.md").read_text(encoding="utf-8")

    def test_run_completes(self):
        self.assertEqual((self.code, self.payload["exit_code"], self.payload["command"]), (0, 0, "run"))
        self.assertEqual(self.payload["result"]["publish_gate"], "clean")
        self.assertEqual(self.payload["result"]["left_to_pull"], 0)
        self.assertEqual(self.payload["result"]["unresolved_links"], 2)

    def test_inventory_keeps_every_usable_row(self):
        self.assertEqual(len(self.dig.raw_manifest_rows()), 11)           # the ragged row is the only one dropped
        states = self.dig.state_counts()
        self.assertEqual((states["fetched"], states["skipped"], states["pending"], states["failed_4xx"], states["failed_5xx"]),
                         (8, 1, 0, 0, 0))
        manifest = json.loads((self.dig.root / "manifest.json").read_text())
        self.assertEqual(len(manifest["captures"]), 9)

    def test_site_tree_is_exactly_what_was_recovered(self):
        files = sorted(p.relative_to(self.dig.site_dir).as_posix() for p in self.dig.site_dir.rglob("*") if p.is_file())
        self.assertEqual(files, ["Images/Logo.GIF", "PROVENANCE.json", "css/site.css", "gone.html", "img/bg.gif",
                                 "index.html", "music/index.html", "music/song.ogg", "old/page.html"])
        self.assertFalse((self.dig.site_dir / "never.html").exists())    # never faked
        self.assertFalse((self.dig.site_dir / "dead.html").exists())
        self.assertFalse((self.dig.site_dir / "music" / "song.mp3").exists())

    def test_cleaned_bytes_are_golden(self):
        site = self.dig.site_dir
        self.assertEqual((site / "index.html").read_bytes(), INDEX_CLEAN)        # still ISO-8859-1, CRLF intact
        self.assertEqual((site / "css/site.css").read_bytes(), CSS_CLEAN)
        self.assertEqual((site / "music/index.html").read_bytes(), MUSIC_CLEAN)
        self.assertEqual((site / "old/page.html").read_bytes(), OLD_CLEAN)       # chrome gone, link localized
        self.assertEqual((site / "gone.html").read_bytes(), GONE_2002)
        for binary, expected in (("img/bg.gif", GIF), ("Images/Logo.GIF", GIF), ("music/song.ogg", OGG)):
            self.assertEqual((site / binary).read_bytes(), expected)             # binaries byte-identical

    def test_capture_details(self):
        caps = {c["url"].split(D, 1)[1]: c for c in self.dig.load_captures()}
        self.assertEqual((caps["/"]["source"], caps["/"]["digest_ok"]), ("id_", True))
        self.assertEqual((caps["/old/page.html"]["source"], caps["/old/page.html"]["digest_ok"]), ("im_", None))
        self.assertEqual((caps["/gone.html"]["timestamp"], caps["/gone.html"]["fallback_from"]),
                         ("20020101000000", "20050101000000"))
        self.assertIs(caps["/gone.html"]["digest_ok"], True)              # verified against the capture actually used
        self.assertEqual(caps["/music/"]["timestamp"], "20030601000003")  # the revisit row did not win
        self.assertEqual((caps["/dead.html"]["fetch_state"], caps["/dead.html"]["skip_reason"]),
                         ("skipped", "best capture status 404"))

    def test_ledger_discloses_every_deviation(self):
        for needle in ("`/css/site.css` → `css/site.css` (index.html)",
                       "`/images/logo.gif` → `Images/Logo.GIF` (index.html; twin: same path, different letter case",
                       "`song.mp3` → `song.ogg` (music/index.html; twin: same name in another format (.mp3 → .ogg)",
                       "`/img/bg.gif` → `../img/bg.gif` (css/site.css, css)",
                       "wayback chrome removed (old/page.html): toolbar block",
                       "wayback chrome removed (old/page.html): archive trailer comment"):
            self.assertIn(needle, self.ledger)
        clean = self.payload["result"]["stages"]["clean"]
        self.assertEqual((clean["cleaned"], clean["twin_swaps"], clean["chrome_removed"], clean["rewrites"]), (8, 2, 2, 10))
        self.assertEqual(clean["encodings"], {"cp1252": 1, "utf-8": 4})

    def test_crawl_accounting(self):
        crawl = json.loads((self.dig.root / "crawl.json").read_text())
        counts = crawl["counts"]
        self.assertEqual((counts["referenced"], counts["recovered"], counts["never_archived"],
                          counts["archived_not_recovered"], counts["case_mismatch"]), (10, 8, 1, 1, 0))
        self.assertEqual([e["target"] for e in crawl["never_archived"]], ["never.html"])
        self.assertEqual([e["target"] for e in crawl["archived_not_recovered"]], ["dead.html"])
        self.assertEqual((crawl["entry_page"], crawl["orphans"]), ("index.html", []))
        self.assertEqual(crawl["external_hosts"], [{"host": "elsewhere.example", "links": 1}])

    def test_report_says_all_of_it(self):
        for needle in ("# VERIFICATION REPORT — golden.test",
                       "| raw CDX rows (no collapse/filter) | 11 |", "| unique URLs | 9 |", "| fetched | 8 |",
                       "| — via a fallback capture (wanted one was gone) | 1 |",
                       "| — digest verified against CDX (SHA-1) | 7 |", "| — digest MISMATCH | 0 |",
                       "wanted 20050101000000, used 20020101000000",
                       "1 × im_ replay is rewritten by Wayback",
                       "10 referenced / 8 recovered / 1 archived but not recovered / 1 never archived / 0 case mismatch.",
                       "- `never.html` — linked from `index.html`",
                       "- `dead.html` — skipped — best capture status 404; linked from `index.html`",
                       "## Twin swaps", "## Output scan (publish gate)", "Clean — no unreviewed findings.",
                       "## Chain of custody", "No files reconstructed by hand in this run."):
            self.assertIn(needle, self.report)

    def test_provenance_and_event_log(self):
        citations = json.loads((self.dig.site_dir / "PROVENANCE.json").read_text())["files"]
        self.assertEqual(len(citations), 8)
        self.assertEqual(citations["gone.html"]["wayback_url"], f"https://web.archive.org/web/20020101000000/http://{D}/gone.html")
        self.assertEqual(citations["gone.html"]["wanted_timestamp"], "20050101000000")
        self.assertEqual({rel for rel, entry in citations.items() if entry["modified_by_clean"]},
                         {"index.html", "css/site.css", "music/index.html", "old/page.html"})
        self.assertEqual([e["event_type"] for e in load_events(self.dig)],
                         ["inventory", "pull", "clean", "discover", "clean", "check", "scan", "report"])

    def test_status_then_export_and_verify(self):
        code, out = excavate("status", "--dig", str(self.dig.root), "--json")
        status = json.loads(out)["result"]
        self.assertEqual((code, status["site_files"], status["publish_gate"]), (0, 8, "clean"))
        self.assertIn("excavate export", status["next"])
        code, out = excavate("export", "--dig", str(self.dig.root), "--zip", "--require-clean", "--json")
        result = json.loads(out)["result"]
        self.assertEqual(code, 0)
        self.assertEqual(verify_bag(Path(result["bag"])), [])
        self.assertEqual(verify_bag(Path(result["zip"])), [])
        self.assertEqual((Path(result["bag"]) / "data/raw/index.html").read_bytes(), INDEX)   # the original, untouched

    def test_strict_run_refuses_to_call_this_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = excavate("run", D, "--output", tmp, "--strict", "--quiet")
            self.assertEqual(code, 1)  # two referenced files are not on disk — a strict run says so with its exit code


if __name__ == "__main__":
    unittest.main()
