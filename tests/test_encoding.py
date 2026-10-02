"""SOTA loop 2 — encoding-faithful clean (offline, on-disk fixtures)."""

from __future__ import annotations

import codecs
import tempfile
import unittest
from pathlib import Path

from excavate.clean import run_clean
from excavate.dig import Dig
from excavate.encoding import decode_document, encode_document, resolve_label
from test_m0 import fresh_capture


def dig_with(tmp: str, files: dict[str, bytes]) -> Dig:
    dig = Dig.create(Path(tmp), "x.com")
    caps = []
    for rel, data in files.items():
        target = dig.raw_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        cap = fresh_capture(f"http://x.com/{rel}")
        cap.update(fetch_state="fetched", raw_path=f"raw/{rel}")
        caps.append(cap)
    dig.save_captures(caps)
    return dig


class TestDetection(unittest.TestCase):
    def test_utf8_bom(self):
        doc = decode_document(codecs.BOM_UTF8 + "é".encode())
        self.assertEqual((doc.text, doc.codec, doc.how, doc.bom), ("é", "utf-8", "bom", codecs.BOM_UTF8))

    def test_utf16_both_endians_keep_their_bom(self):
        for bom, codec in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            with self.subTest(codec=codec):
                data = bom + "<p>ñ</p>".encode(codec)
                doc = decode_document(data)
                self.assertEqual(doc.text, "<p>ñ</p>")
                self.assertEqual(encode_document(doc.text, doc), data)

    def test_meta_charset_forms(self):
        body = "café".encode("iso-8859-1")
        for head in (b'<meta charset="iso-8859-1">', b"<META CHARSET=ISO-8859-1>",
                     b'<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">'):
            with self.subTest(head=head):
                doc = decode_document(head + body)
                self.assertEqual((doc.codec, doc.how), ("cp1252", "declared"))  # browsers read latin1 as cp1252
                self.assertTrue(doc.text.endswith("café"))

    def test_css_at_charset(self):
        doc = decode_document('@charset "shift_jis";\nbody:after{content:"日本"}'.encode("cp932"), is_css=True)
        self.assertEqual((doc.codec, doc.how), ("cp932", "declared"))
        self.assertIn("日本", doc.text)

    def test_undeclared_legacy_bytes_fall_to_cp1252(self):
        doc = decode_document(b"<p>caf\xe9 \x93quoted\x94</p>")
        self.assertEqual((doc.codec, doc.how), ("cp1252", "cp1252"))
        self.assertIn("café “quoted”", doc.text)

    def test_wrong_declaration_is_disclosed_not_trusted(self):
        # declares utf-8 but the bytes are latin-1
        doc = decode_document(b'<meta charset="utf-8"><p>caf\xe9</p>')
        self.assertEqual(doc.codec, "cp1252")
        self.assertIn("not valid `utf-8`", doc.note)

    def test_unknown_label_is_disclosed(self):
        doc = decode_document(b'<meta charset="x-made-up"><p>hi</p>')
        self.assertEqual(doc.codec, "utf-8")
        self.assertIn("unknown", doc.note)

    def test_bomless_utf16_declaration_ignored(self):
        self.assertIsNone(resolve_label("utf-16"))
        self.assertEqual(decode_document(b'<meta charset="utf-16"><p>hi</p>').codec, "utf-8")

    def test_undecodable_everywhere_is_byte_transparent(self):
        data = b"<p>\x81\x8d\x8f\x90\x9d\xff</p>"  # holes in cp1252, invalid utf-8
        doc = decode_document(data)
        self.assertEqual((doc.codec, doc.how), ("latin-1", "fallback"))
        self.assertEqual(encode_document(doc.text, doc), data)

    def test_every_byte_value_round_trips(self):
        data = bytes(range(256))
        doc = decode_document(data)
        self.assertEqual(encode_document(doc.text, doc), data)


class TestCleanKeepsCharset(unittest.TestCase):
    def _assert_clean(self, codec: str, label: str, body: str):
        head = f'<meta http-equiv="Content-Type" content="text/html; charset={label}">'
        raw = (head + f'<a href="http://x.com/next.html">{body}</a>').encode(codec)
        with tempfile.TemporaryDirectory() as tmp:
            dig = dig_with(tmp, {"index.html": raw})
            summary = run_clean(dig)
            out = (dig.site_dir / "index.html").read_bytes()
        self.assertEqual(out, (head + f'<a href="next.html">{body}</a>').encode(codec))
        self.assertEqual(summary["rewrites"], 1)
        return summary

    def test_iso_8859_1_page_stays_iso_8859_1(self):
        summary = self._assert_clean("iso-8859-1", "iso-8859-1", "café más señor")
        self.assertEqual(summary["encodings"], {"cp1252": 1})

    def test_shift_jis_page(self):
        self._assert_clean("cp932", "Shift_JIS", "日本語のページ")

    def test_cp437_bbs_page(self):
        self._assert_clean("cp437", "cp437", "░▒▓█ SysOp ║ ╔═╗ █▓▒░")

    def test_koi8_r_page(self):
        self._assert_clean("koi8-r", "koi8-r", "Привет, мир")

    def test_page_without_rewrites_is_byte_identical(self):
        # utf-8-undecodable AND with a trailing-space oddity a re-serializer would lose
        raw = b"<html>\r\n<p>caf\xe9</p>  \r\n<a href=song.mp3>s</a>\x00</html>"
        with tempfile.TemporaryDirectory() as tmp:
            dig = dig_with(tmp, {"a/page.html": raw})
            run_clean(dig)
            self.assertEqual((dig.site_dir / "a" / "page.html").read_bytes(), raw)

    def test_untouched_bytes_survive_a_rewrite_exactly(self):
        raw = b'<p>\x81\x8d\xff raw bytes</p><img src="/i.gif"><p>caf\xe9</p>'
        with tempfile.TemporaryDirectory() as tmp:
            dig = dig_with(tmp, {"d/page.html": raw})
            run_clean(dig)
            out = (dig.site_dir / "d" / "page.html").read_bytes()
            ledger = (dig.root / "REWRITES.md").read_text()
        self.assertEqual(out, raw.replace(b'src="/i.gif"', b'src="../i.gif"'))
        self.assertIn("- encoding: `d/page.html`", ledger)  # the fallback is disclosed

    def test_bom_preserved_through_rewrite(self):
        raw = codecs.BOM_UTF8 + '<a href="/é.html">é</a>'.encode()
        with tempfile.TemporaryDirectory() as tmp:
            dig = dig_with(tmp, {"index.html": raw})
            run_clean(dig)
            out = (dig.site_dir / "index.html").read_bytes()
        self.assertEqual(out, codecs.BOM_UTF8 + '<a href="é.html">é</a>'.encode())

    def test_unencodable_rewritten_url_is_escaped_not_lost(self):
        # percent-encoded Japanese path linked from a latin-1 page
        raw = b'<meta charset="iso-8859-1"><a href="/%E6%97%A5.html">caf\xe9</a>'
        with tempfile.TemporaryDirectory() as tmp:
            dig = dig_with(tmp, {"index.html": raw})
            run_clean(dig)
            out = (dig.site_dir / "index.html").read_bytes()
        self.assertIn(b'href="&#26085;.html"', out)  # a character reference the browser resolves
        self.assertIn(b"caf\xe9", out)

    def test_css_unencodable_url_uses_css_escape(self):
        raw = '@charset "iso-8859-1";\nbody{background:url(/%E6%97%A5.gif)}'.encode("iso-8859-1")
        with tempfile.TemporaryDirectory() as tmp:
            dig = dig_with(tmp, {"css/s.css": raw})
            run_clean(dig)
            out = (dig.site_dir / "css" / "s.css").read_bytes()
        self.assertIn(b"url(../\\65e5 .gif)", out)


if __name__ == "__main__":
    unittest.main()
