# Changelog

## Unreleased — SOTA loop (`claude/sota-loop`)

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
