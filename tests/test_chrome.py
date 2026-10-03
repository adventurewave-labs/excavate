"""SOTA loop 4 — Wayback chrome removal (offline; fixtures modelled on replay output)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from excavate.chrome import MARKER, strip_chrome
from excavate.clean import clean_text, internal_targets, run_clean
from excavate.dig import Dig
from test_m0 import fresh_capture

ORIGINAL = """<html>
<head>
<title>Magpu</title>
<link rel="stylesheet" href="style.css">
<script src="js/site.js"></script>
<script>var hits = 3; document.write("since 1999");</script>
</head>
<body bgcolor="#000000">
<!-- site's own comment -->
<h1>Welcome</h1>
<a href="music/">music</a>
</body>
</html>
"""

# what a non-id_ replay of ORIGINAL looks like: head include + toolbar + trailers
REPLAYED = """<html>
<head><script type="text/javascript" src="https://web.archive.org/_static/js/bundle-playback.js?v=1B2M2Y8A" charset="utf-8"></script>
<script type="text/javascript" src="/_static/js/wombat.js?v=txqj7nKC" charset="utf-8"></script>
<script>window.RufflePlayer=window.RufflePlayer||{};window.RufflePlayer.config={"autoplay":"off","unmuteOverlay":"hidden"};</script>
<script type="text/javascript" src="/_static/js/ruffle/ruffle.js"></script>
<script type="text/javascript">
    __wm.init("https://web.archive.org/web");
  __wm.wombat("http://magpu.com/","20030601000000","https://web.archive.org/","web","/_static/",
          "1054425600");
</script>
<link rel="stylesheet" type="text/css" href="/_static/css/banner-styles.css?v=S1zqJCYt" />
<link rel="stylesheet" type="text/css" href="/_static/css/iconochive.css?v=3PDvdIFv" />
<!-- End Wayback Rewrite JS Include -->

<title>Magpu</title>
<link rel="stylesheet" href="/web/20030601000000cs_/http://magpu.com/style.css">
<script src="/web/20030601000000js_/http://magpu.com/js/site.js"></script>
<script>var hits = 3; document.write("since 1999");</script>
</head>
<body bgcolor="#000000"><!-- BEGIN WAYBACK TOOLBAR INSERT -->
<script>__wm.rw(0);</script>
<div id="wm-ipp-base" lang="en" style="display:none;direction:ltr;">
<div id="wm-ipp" style="position:fixed;left:0;top:0;right:0;">
<a href="/web/" title="Wayback Machine home page"><img src="/_static/images/toolbar/wayback-toolbar-logo-200.png"></a>
<form action="/web/submit"><input name="url" value="http://magpu.com/"></form>
</div></div>
<script type="text/javascript">__wm.bt(700,27,25,2,"web","http://magpu.com/","20030601000000",1996,"/_static/",["/_static/css/banner-styles.css"]);</script>
<!-- END WAYBACK TOOLBAR INSERT -->
<!-- site's own comment -->
<h1>Welcome</h1>
<a href="/web/20030601000000/http://magpu.com/music/">music</a>
</body>
</html>
<!--
     FILE ARCHIVED ON 00:00:00 Jun 01, 2003 AND RETRIEVED FROM THE
     INTERNET ARCHIVE ON 16:02:10 Oct 02, 2026.
     JAVASCRIPT APPENDED BY WAYBACK MACHINE, COPYRIGHT INTERNET ARCHIVE.

     ALL OTHER PORTIONS OF THIS PAGE ARE RESERVED TO THE COPYRIGHT OWNER.
     SECTION 108(a)(3)).
-->
<!--
playback timings (ms):
  captures_list: 0.611
  exclusion.robots: 0.027
  load_resource: 91.325
-->
"""


class TestStripChrome(unittest.TestCase):
    def test_replayed_page_cleans_back_to_the_original(self):
        text, _ = clean_text(REPLAYED, "magpu.com", "index.html", is_css=False)
        # everything Wayback added is gone; what the site had is intact, byte for byte
        # (bar the one marker and the directory link normalized to its index file)
        self.assertEqual(text.replace(MARKER, "").replace("music/index.html", "music/"), ORIGINAL)
        for needle in ("_static", "__wm", "wombat", "wm-ipp", "RufflePlayer", "FILE ARCHIVED ON",
                       "playback timings", "WAYBACK TOOLBAR", "web.archive.org", "/web/2003"):
            self.assertNotIn(needle, text)
        self.assertEqual(text.count(MARKER), 1)  # one marker, not one per removed piece

    def test_every_removal_is_logged_by_kind(self):
        _, removed = strip_chrome(REPLAYED)
        kinds = [item.split(":", 1)[0] for item in removed]
        self.assertEqual(kinds.count("toolbar block"), 1)
        self.assertEqual(kinds.count("replay script include"), 3)
        self.assertEqual(kinds.count("replay init script"), 2)
        self.assertEqual(kinds.count("banner stylesheet"), 2)
        self.assertEqual(kinds.count("include end marker"), 1)
        self.assertEqual(kinds.count("archive trailer comment"), 2)
        _, log = clean_text(REPLAYED, "magpu.com", "index.html", is_css=False)
        self.assertEqual(sum(1 for line in log if line.startswith("- wayback chrome removed (index.html)")), 11)

    def test_toolbar_links_never_reach_the_link_graph(self):
        text, _ = clean_text(REPLAYED, "magpu.com", "index.html", is_css=False)
        self.assertEqual(internal_targets(text, "magpu.com", "index.html"),
                         {"style.css", "js/site.js", "music/index.html"})

    def test_original_page_is_untouched(self):
        text, removed = strip_chrome(ORIGINAL)
        self.assertEqual(text, ORIGINAL)
        self.assertEqual(removed, [])

    def test_site_markup_that_merely_resembles_chrome_survives(self):
        # /static/ is a directory real sites use; none of this is Wayback's
        html = ('<script src="/static/js/app.js"></script>'
                '<script src="/static/js/analytics.js"></script>'      # same name as legacy chrome, no Wayback marker
                '<script src="js/_static/js/x.js"></script>'
                '<script>var note = "see the wayback machine";</script>'
                '<link rel="stylesheet" href="banner.css">'
                '<link rel="stylesheet" href="/css/banner-styles.css">'
                '<!-- FILE notes: ARCHIVED ON request -->')
        self.assertEqual(strip_chrome(html), (html, []))

    def test_site_static_dir_survives_even_on_a_replayed_page(self):
        html = '<script src="/static/js/app.js"></script><!-- BEGIN WAYBACK TOOLBAR INSERT -->x<!-- END WAYBACK TOOLBAR INSERT -->'
        text, removed = strip_chrome(html)
        self.assertEqual(text, '<script src="/static/js/app.js"></script>' + MARKER)
        self.assertEqual(len(removed), 1)

    def test_large_real_script_mentioning_a_marker_is_kept(self):
        html = "<script>" + "x();" * 2000 + ' var s = "__wm.init(";</script>'
        self.assertEqual(strip_chrome(html), (html, []))

    def test_legacy_analytics_injection(self):
        html = ('<script type="text/javascript" src="/static/js/analytics.js"></script>\n'
                '<script type="text/javascript">archive_analytics.values.server_name="wwwb-app5.us.archive.org";</script>\n'
                '<link type="text/css" rel="stylesheet" href="/static/css/banner-styles.css"/>\n<p>page</p>')
        text, removed = strip_chrome(html)
        self.assertEqual(text, MARKER + "<p>page</p>")
        self.assertEqual(len(removed), 3)

    def test_css_trailer(self):
        css = ("body{color:red}\n/*\n     FILE ARCHIVED ON 00:00:00 Jun 01, 2003 AND RETRIEVED FROM THE\n"
               "     INTERNET ARCHIVE ON 16:02:10 Oct 02, 2026.\n*/\n/*\nplayback timings (ms):\n  load: 1.2\n*/\n")
        text, log = clean_text(css, "x.com", "s.css", is_css=True)
        self.assertEqual(text, "body{color:red}\n")
        self.assertEqual(len(log), 2)
        self.assertEqual(clean_text("/* my archive notes */ b{}", "x.com", "s.css", is_css=True)[0], "/* my archive notes */ b{}")

    def test_all_replay_modifiers_are_stripped_from_links(self):
        for mod in ("id_", "im_", "js_", "cs_", "if_", "fw_", "oe_", "mp_", ""):
            with self.subTest(mod=mod):
                text, _ = clean_text(f'<img src="/web/20030601000000{mod}/http://x.com/a.gif">', "x.com", "d/p.html", False)
                self.assertEqual(text, '<img src="../a.gif">')


class TestCleanCountsChrome(unittest.TestCase):
    def test_summary_and_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "magpu.com")
            (dig.raw_dir / "index.html").write_text(REPLAYED)
            cap = fresh_capture("http://magpu.com/")
            cap.update(fetch_state="fetched", raw_path="raw/index.html", source="im_")
            dig.save_captures([cap])
            summary = run_clean(dig)
            self.assertEqual(summary["chrome_removed"], 11)
            self.assertEqual(summary["rewrites"], 3)  # chrome removals are not counted as URL rewrites
            self.assertIn("toolbar block", (dig.root / "REWRITES.md").read_text())
            self.assertNotIn("_static", (dig.site_dir / "index.html").read_text())


if __name__ == "__main__":
    unittest.main()
