# Changelog

## Unreleased — SOTA loop (`claude/sota-loop`)

### L3 — tokenizer-based rewriting
- **Changed:** link discovery and rewriting use a span tokenizer (`excavate.rewrite`) instead of
  a regex over `href|src|action`; only the URL spans are edited.
- **Added:** unquoted attributes, `srcset`, `background`, `poster`, `<object data>`,
  `<meta refresh>`, inline `style`, `@import`, `<base href>` resolution.
- **Fixed:** URLs inside `<script>`, comments and `<textarea>` are no longer rewritten or counted;
  `&amp;`-encoded query strings map to the right file; non-http schemes (`ftp:`, `irc:`, …) are
  no longer reported as never-crawled; `#`, `%`, `?` in file names are escaped in links.

### L2 — encoding-faithful clean
- **Fixed:** cleaned HTML/CSS is written back in the charset the page declares instead of always
  UTF-8 (legacy pages no longer turn to mojibake); pages with no rewrites are byte-identical.
- **Added:** `excavate.encoding` (BOM / `<meta>` / `@charset` detection with strict round-trip
  checks); `clean` prints the charsets it kept and discloses wrong declarations in `REWRITES.md`.

### L1 — CDX digest verification
- **Added:** each fetched `id_` body is verified against the CDX SHA-1 digest (`digest_ok`,
  `digest_note` per capture). The report counts verified / mismatched / unverifiable and lists
  every mismatch; unverifiable bodies carry their reason instead of a guess.

### L0 — foundation + correctness fixes
- **Fixed:** links rewritten on pages below the site root now resolve (page-relative paths);
  previously every root-absolute or absolute same-site link on a nested page was broken and
  mis-reported as never-crawled.
- **Fixed:** `#fragment` is preserved when a link is rewritten.
- **Fixed:** `pull` retries transient failures (`failed_5xx`) on resume; first-time failures are
  no longer labelled "re-fetch failed".
- **Changed:** dig state files are written atomically.
- **Added:** `scripts/check.sh` gate (ruff, mypy, pytest), `[dev]` extra, `docs/SOTA_ROADMAP.md`.
