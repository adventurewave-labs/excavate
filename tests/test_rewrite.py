"""SOTA loop 3 — tokenizer-based rewriting (offline)."""

from __future__ import annotations

import unittest

from excavate.clean import clean_text, internal_targets
from excavate.rewrite import scan


def clean(html: str, page: str = "index.html", domain: str = "x.com") -> tuple[str, list[str]]:
    return clean_text(html, domain, page, is_css=page.endswith(".css"))


class TestAttributeForms(unittest.TestCase):
    def test_unquoted_attribute(self):
        text, log = clean("<a href=http://x.com/page.html>p</a><IMG SRC=/img/a.gif border=0>")
        self.assertEqual(text, "<a href=page.html>p</a><IMG SRC=img/a.gif border=0>")
        self.assertEqual(len(log), 2)

    def test_unquoted_value_gains_quotes_only_when_needed(self):
        text, _ = clean("<a href=http://x.com/my%20file.html>p</a>")
        self.assertEqual(text, '<a href="my file.html">p</a>')

    def test_single_quotes_and_spacing_preserved(self):
        text, _ = clean("<a  class=k   HREF = '/a.html'  target=_top >a</a>")
        self.assertEqual(text, "<a  class=k   HREF = 'a.html'  target=_top >a</a>")

    def test_more_url_attributes(self):
        html = ('<body background="/bg.gif"><table background="http://x.com/t.gif">'
                '<video poster="/p.jpg"></video><object data="/m.swf"></object>'
                '<img src="/a.gif" lowsrc="/a-low.gif" longdesc="/a.txt">'
                '<blockquote cite="/src.html">q</blockquote>')
        text, log = clean(html, "d/page.html")
        for expected in ('background="../bg.gif"', 'background="../t.gif"', 'poster="../p.jpg"', 'data="../m.swf"',
                         'lowsrc="../a-low.gif"', 'longdesc="../a.txt"', 'cite="../src.html"'):
            self.assertIn(expected, text)
        self.assertEqual(len(log), 8)

    def test_data_and_cite_only_on_their_tags(self):
        html = '<div data="/not-a-url"><span cite="/nope">x</span></div>'
        self.assertEqual(clean(html)[0], html)

    def test_entity_encoded_query_maps_to_the_real_url(self):
        # `&amp;` in markup is `&` in the URL — both must land on the same file
        entity, _ = clean('<a href="/s.php?a=1&amp;b=2">s</a>')
        plain, _ = clean('<a href="/s.php?a=1&b=2">s</a>')
        self.assertEqual(entity, plain)
        self.assertEqual(internal_targets('<a href="/s.php?a=1&amp;b=2">', "x.com", "index.html"),
                         internal_targets('<a href="/s.php?a=1&b=2">', "x.com", "index.html"))

    def test_srcset(self):
        html = '<img srcset="http://x.com/a.png 1x, /a@2x.png 2x, https://cdn.example.com/a.png 3x">'
        text, log = clean(html, "p/i.html")
        self.assertEqual(text, '<img srcset="../a.png 1x, ../a@2x.png 2x, https://cdn.example.com/a.png 3x">')
        self.assertEqual(len(log), 2)

    def test_meta_refresh(self):
        text, log = clean('<meta http-equiv="Refresh" content="5; URL=http://x.com/new/">')
        self.assertEqual(text, '<meta http-equiv="Refresh" content="5; URL=new/index.html">')
        self.assertIn("meta refresh", log[0])
        untouched = '<meta name="description" content="0; url=http://x.com/new/">'
        self.assertEqual(clean(untouched)[0], untouched)  # only a refresh carries a URL

    def test_inline_style_and_style_block_and_import(self):
        html = ('<style>@import "/css/a.css"; body{background:url( "http://x.com/bg.gif" )} /* url(/no.gif) */</style>'
                '<td style="background:url(/cell.gif)">x</td>')
        text, log = clean(html, "d/p.html")
        self.assertIn('@import "../css/a.css"', text)
        self.assertIn('url( "../bg.gif" )', text)
        self.assertIn("/* url(/no.gif) */", text)  # CSS comment untouched
        self.assertIn('style="background:url(../cell.gif)"', text)
        self.assertEqual(len(log), 3)

    def test_css_file_import_and_unquoted_url_with_space(self):
        text, _ = clean("@import '/base.css';\nb{background:url(/my%20bg.gif)}", "css/site.css")
        self.assertEqual(text, "@import '../base.css';\nb{background:url(\"../my bg.gif\")}")


class TestNotLinks(unittest.TestCase):
    def test_script_comment_textarea_title_untouched(self):
        html = ('<script>var a = \'<a href="http://x.com/in-script.html">\';</script>'
                '<!-- <a href="http://x.com/in-comment.html"> -->'
                '<textarea><a href="http://x.com/in-textarea.html"></textarea>'
                '<title>href="http://x.com/in-title.html"</title>')
        text, log = clean(html)
        self.assertEqual(text, html)
        self.assertEqual(log, [])
        self.assertEqual(internal_targets(html, "x.com", "index.html"), set())

    def test_script_src_is_still_a_link(self):
        text, _ = clean('<script src="http://x.com/js/a.js">var u = "http://x.com/x";</script>')
        self.assertEqual(text, '<script src="js/a.js">var u = "http://x.com/x";</script>')

    def test_text_that_looks_like_an_attribute(self):
        html = '<p>write href="http://x.com/a.html" in your page</p>'
        self.assertEqual(clean(html)[0], html)

    def test_other_schemes_are_external_not_never_crawled(self):
        html = ('<a href="ftp://ftp.x.com/pub/">f</a><a href="news:alt.bbs">n</a><a href="irc://irc.efnet.org/x">i</a>'
                '<a href="aim:goim?screenname=x">a</a><a href="MAILTO:a@x.com">m</a><a href="JavaScript:void(0)">j</a>')
        text, log = clean(html, "d/p.html")
        self.assertEqual(log, [])
        self.assertEqual(text, html)
        self.assertEqual(internal_targets(html, "x.com", "d/p.html"), set())

    def test_malformed_markup_does_not_crash_or_corrupt(self):
        for html in ("<a href=", '<a href="/a.html', "<<<>>><a", "<a href='x' <b>", "<", "<!-- never closed <a href='/a'>",
                     "<style>url(/a.gif)", "<script>never closed", ""):
            with self.subTest(html=html):
                text, _ = clean(html)
                self.assertIsInstance(text, str)
                scan(html).urls()

    def test_untouched_document_is_identical(self):
        html = "<HTML>\r\n<BODY  BGCOLOR=#FFFFFF>\t<A HREF=local.html>x</A>\r\n<IMG SRC='pics/a.gif'  >\r\n"
        self.assertEqual(clean(html, "a/b.html")[0], html)


class TestBaseHref(unittest.TestCase):
    def test_relative_links_resolved_against_base_and_base_removed(self):
        html = ('<head><base href="http://x.com/docs/"></head>'
                '<a href="guide.html">g</a><img src="img/a.gif"><a href="#top">t</a>'
                '<a href="http://other.com/z.html">o</a>')
        text, log = clean(html, "index.html")
        self.assertNotIn("<base", text)
        self.assertIn('href="docs/guide.html"', text)
        self.assertIn('src="docs/img/a.gif"', text)
        self.assertIn('href="#top"', text)
        self.assertIn('href="http://other.com/z.html"', text)
        self.assertTrue(any("<base href" in line for line in log))
        self.assertEqual(internal_targets(text, "x.com", "index.html"), {"docs/guide.html", "docs/img/a.gif"})

    def test_link_graph_honours_base_before_cleaning(self):
        html = '<base href="/docs/"><a href="guide.html">g</a>'
        self.assertEqual(internal_targets(html, "x.com", "index.html"), {"docs/guide.html"})

    def test_external_base_makes_relative_links_absolute(self):
        text, log = clean('<base href="http://cdn.example.com/x/"><img src="a.gif">')
        self.assertIn('src="http://cdn.example.com/x/a.gif"', text)
        self.assertEqual(len(log), 2)  # the rewrite and the base removal are both disclosed
        self.assertEqual(internal_targets(text, "x.com", "index.html"), set())

    def test_wayback_prefixed_base(self):
        text, _ = clean('<base href="https://web.archive.org/web/20030601000000im_/http://x.com/a/"><a href="b.html">b</a>', "a/i.html")
        self.assertIn('href="b.html"', text)


class TestReservedCharsInFilenames(unittest.TestCase):
    def test_hash_percent_and_question_mark_in_a_filename(self):
        text, _ = clean('<a href="/a%23b.html">h</a><a href="/100%25.html">p</a><a href="/what%3F.html#s">q</a>')
        self.assertIn('href="a%23b.html"', text)
        self.assertIn('href="100%25.html"', text)
        self.assertIn('href="what%3F.html#s"', text)
        # and the graph reads them back as the files that are on disk
        self.assertEqual(internal_targets(text, "x.com", "index.html"), {"a#b.html", "100%.html", "what?.html"})


if __name__ == "__main__":
    unittest.main()
