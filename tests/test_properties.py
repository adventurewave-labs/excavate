"""SOTA loop 15 — property-style tests (seeded random inputs, no extra dependency).

Each test states an invariant and throws a few thousand generated inputs at
it. The seed is fixed, so a failure is reproducible; the failing input is in
the assertion message.
"""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from excavate.clean import clean_text, internal_targets
from excavate.dig import safe_relpath
from excavate.encoding import decode_document, encode_document
from excavate.rewrite import scan
from excavate.twins import TwinIndex

SEED = 20261002
N = 3000

SEGMENTS = ["a", "img", "Music", "index.html", "page.htm", "a b", "..", ".", "", "%2e%2e", "..%2f", "x" * 300, "é" * 200,
            "日本語" * 90, "file.tar.gz", "\x00evil", "\x1fctl", "con", "a\\b", "~user", "100%", "q?x", ".hidden", "trailing.",
            "%00", "%2F", "name.with.dots.html", "UPPER.GIF", "é.html", "#frag", "semi;colon", "a" * 180 + ".html"]
QUERIES = ["", "", "", "?id=1", "?a=1&b=2", "?" + "q" * 400, "?x=%2e%2e/", "?é=日本"]
HOSTS = ["x.com", "www.x.com", "X.COM", "x.com:8080"]


def random_url(rng: random.Random) -> str:
    path = "/".join(rng.choice(SEGMENTS) for _ in range(rng.randint(0, 6)))
    if rng.random() < 0.3:
        path = quote(path)
    return f"http://{rng.choice(HOSTS)}/{path}{rng.choice(QUERIES)}"


class TestSafeRelpathProperties(unittest.TestCase):
    def test_always_a_safe_relative_path_inside_the_target_directory(self):
        rng = random.Random(SEED)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for _ in range(N):
                url = random_url(rng)
                rel = safe_relpath(url)
                self.assertTrue(rel, url)
                self.assertFalse(rel.startswith("/"), url)
                parts = rel.split("/")
                self.assertNotIn("..", parts, url)
                self.assertNotIn(".", parts, url)
                self.assertNotIn("", parts, url)
                self.assertFalse(any(ord(ch) < 32 or ch == "\x7f" for ch in rel), url)
                self.assertTrue((root / rel).resolve().is_relative_to(root), url)
                # every segment fits a 255-BYTE filesystem name limit (not just 255 characters)
                self.assertLessEqual(max(len(part.encode("utf-8")) for part in parts), 255, url)
                self.assertIn(".", parts[-1], url)  # always names a file, never a bare directory

    def test_deterministic_and_query_sensitive(self):
        rng = random.Random(SEED + 1)
        for _ in range(N):
            url = random_url(rng)
            self.assertEqual(safe_relpath(url), safe_relpath(url))
        self.assertNotEqual(safe_relpath("http://x.com/a.php?id=1"), safe_relpath("http://x.com/a.php?id=2"))
        self.assertEqual(safe_relpath("http://x.com/a/b.html"), safe_relpath("https://www.x.com/a/b.html"))

    def test_distinct_long_names_stay_distinct(self):
        rng = random.Random(SEED + 2)
        seen: dict[str, str] = {}
        for _ in range(500):
            name = "".join(rng.choice("abcé日") for _ in range(rng.randint(200, 400))) + ".html"
            rel = safe_relpath(f"http://x.com/{quote(name)}")
            self.assertEqual(seen.setdefault(rel, name), name, "two different long names collapsed to one path")


LINKS = ["http://x.com/a.html", "http://www.x.com/img/a.gif", "/root.html", "/dir/", "rel.html", "../up.html", "sub/deep.css",
         "http://other.com/x.html", "mailto:a@x.com", "#top", "javascript:void(0)", "ftp://x.com/f", "", "  /spaced.html  ",
         "/q.php?a=1&amp;b=2", "/web/20030601000000im_/http://x.com/w.gif", "https://web.archive.org/web/2003060100/http://x.com/w2.gif",
         "//cdn.other.com/lib.js", "/a%23b.html", "/é.html", "HTTP://X.COM/CAPS.HTML", "/frag.html#sec", "data:image/gif;base64,AAAA"]
TEMPLATES = ['<a href="{0}">t</a>', "<a href='{0}'>t</a>", "<a href={0}>t</a>", '<img src="{0}" alt=x>', '<body background="{0}">',
             '<link rel=stylesheet href="{0}">', '<img srcset="{0} 1x, {1} 2x">', '<td style="background:url({0})">',
             "<style>b{{background:url('{0}')}} @import \"{1}\";</style>", '<meta http-equiv="refresh" content="3; url={0}">',
             "<script>var u = '{0}';</script>", "<!-- <a href=\"{0}\"> -->", "<p>plain {0} text</p>", "<form action=\"{0}\">",
             "<a href=\"{0}\"", "<<< {0} >>>", "<textarea>{0}</textarea>", '<iframe src="{0}"></iframe>']
PAGES = ["index.html", "a/page.html", "a/b/c/deep.html", "é/p.html"]


def random_html(rng: random.Random) -> str:
    parts = []
    for _ in range(rng.randint(0, 12)):
        template = rng.choice(TEMPLATES)
        parts.append(template.format(rng.choice(LINKS), rng.choice(LINKS)))
        if rng.random() < 0.3:
            parts.append(rng.choice(["\r\n", "\t", "  ", "é", "\x00", "<br>"]))
    return "".join(parts)


class TestRewriterProperties(unittest.TestCase):
    def test_never_raises_and_cleaning_is_idempotent(self):
        rng = random.Random(SEED + 3)
        for _ in range(N):
            html, page = random_html(rng), rng.choice(PAGES)
            once, _ = clean_text(html, "x.com", page, is_css=False)
            twice, log = clean_text(once, "x.com", page, is_css=False)
            self.assertEqual(twice, once, f"not idempotent on {page}: {html!r}")
            self.assertEqual(log, [], f"second pass still rewrote something: {html!r}")

    def test_cleaning_preserves_the_link_graph(self):
        # whatever a browser would have resolved before cleaning, it resolves to the same file after
        rng = random.Random(SEED + 4)
        for _ in range(N):
            html, page = random_html(rng), rng.choice(PAGES)
            cleaned, _ = clean_text(html, "x.com", page, is_css=False)
            self.assertEqual(internal_targets(cleaned, "x.com", page), internal_targets(html, "x.com", page),
                             f"link graph changed on {page}: {html!r}")

    def test_documents_without_site_urls_come_back_untouched(self):
        rng = random.Random(SEED + 5)
        inert = [link for link in LINKS if not any(marker in link.lower() for marker in ("x.com", "/", "web.archive"))
                 or link.startswith(("http://other.com", "mailto:", "javascript:", "data:", "//cdn"))]
        inert = [link for link in inert if not link.strip().startswith("/") or link.startswith("//")]
        for _ in range(N):
            html = "".join(rng.choice(TEMPLATES).format(rng.choice(inert), rng.choice(inert)) for _ in range(rng.randint(0, 8)))
            self.assertEqual(clean_text(html, "x.com", rng.choice(PAGES), is_css=False), (html, []), html)

    def test_rewritten_site_links_never_point_outside_or_at_the_archive(self):
        rng = random.Random(SEED + 6)
        for _ in range(N):
            html, page = random_html(rng), rng.choice(PAGES)
            cleaned, _ = clean_text(html, "x.com", page, is_css=False)
            for url in scan(cleaned).urls():
                self.assertNotRegex(url, r"(?i)^/web/\d{6,14}", f"wayback prefix survived: {html!r}")
                self.assertNotRegex(url, r"(?i)^https?://(www\.)?x\.com([:/]|$)", f"absolute same-site link survived: {html!r}")
                self.assertFalse(url.startswith("/") and not url.startswith("//"), f"root-absolute link survived: {url!r} in {html!r}")

    def test_css_rewriting_is_idempotent(self):
        rng = random.Random(SEED + 7)
        for _ in range(N):
            css = "".join(rng.choice(["b{{background:url({0})}}", "@import '{0}';", "/* url({0}) */", "c{{x:url(\"{0}\")}}", "p{{}}"])
                          .format(rng.choice(LINKS)) for _ in range(rng.randint(0, 8)))
            once, _ = clean_text(css, "x.com", "css/site.css", is_css=True)
            self.assertEqual(clean_text(once, "x.com", "css/site.css", is_css=True), (once, []), css)


class TestEncodingProperties(unittest.TestCase):
    def test_any_bytes_round_trip_exactly(self):
        rng = random.Random(SEED + 8)
        heads = [b"", b'<meta charset="utf-8">', b'<meta charset="iso-8859-1">', b'<meta charset="shift_jis">',
                 b'<meta charset="koi8-r">', b'<meta charset="bogus">', b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"]
        for _ in range(N):
            data = rng.choice(heads) + bytes(rng.randrange(256) for _ in range(rng.randint(0, 64)))
            for is_css in (False, True):
                doc = decode_document(data, is_css)
                self.assertEqual(encode_document(doc.text, doc, is_css), data, f"{data!r}")


class TestTwinProperties(unittest.TestCase):
    def test_a_twin_is_always_a_different_file_that_exists(self):
        rng = random.Random(SEED + 9)
        names = ["a", "Song", "index", "logo", "page"]
        exts = ["mp3", "ogg", "wav", "gif", "png", "jpg", "jpeg", "htm", "html", "txt", "MP3", "GIF"]
        dirs = ["", "music/", "Music/", "img/"]
        for _ in range(N):
            available = {rng.choice(dirs) + rng.choice(names) + "." + rng.choice(exts) for _ in range(rng.randint(0, 8))}
            index = TwinIndex(available)
            target = rng.choice(dirs) + rng.choice(names) + "." + rng.choice(exts)
            twin = index.find(target)
            if target in available:
                self.assertIsNone(twin, f"{target} is on disk; it needs no twin")
            elif twin is not None:
                self.assertIn(twin.path, available, f"twin for {target} is not a real file: {twin.path}")
                self.assertNotEqual(twin.path, target)


if __name__ == "__main__":
    unittest.main()
