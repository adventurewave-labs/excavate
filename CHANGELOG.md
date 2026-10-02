# Changelog

## Unreleased — SOTA loop (`claude/sota-loop`)

### L9 — asset twin swap
- **Added:** dead links are pointed at a surviving twin that is on disk (case, extension spelling,
  index document, same-medium format such as `.mp3` → `.ogg`); each swap is logged with its reason
  and listed in the report. `clean --no-twins` disables it.

### L8 — crawl-check stage
- **Added:** `excavate check [--strict]` and `crawl.json` — link-graph audit of the cleaned site:
  recovered / never archived / archived but not recovered / case mismatch, plus reachability and
  orphans.
- **Changed:** the verification report's never-crawled list is split into those three groups, each
  entry naming the pages that link to it.

### L7 — SQLite state store
- **Changed:** dig state lives in `dig.sqlite`; `pull` updates one row per URL (constant cost at
  any dig size) instead of rewriting `captures.json` after every fetch.
- **Kept:** `captures.json` as a per-command export; legacy digs migrate automatically; an
  externally edited `captures.json` is re-imported.

### L6 — capture selection + fallback chain
- **Added:** `inventory --at <date>` selects each URL's capture nearest a date instead of its latest.
- **Added:** `pull` falls back through a URL's other captures when the wanted one is permanently
  gone; `fallback_from` records the substitution and the report has a "Fallback captures" section.
- **Changed:** the report states the capture-selection rule the dig was built with.

### L5 — CDX inventory robustness
- **Fixed:** the CDX inventory is paginated; previously one unpaginated request could truncate a
  large site without notice.
- **Changed:** CDX requests are rate-limited and backed off like `pull`; a failed inventory writes
  nothing.
- **Added:** `inventory --match prefix|host|domain`, `--from`, `--to`; `manifest.json` (spec
  manifest: url, timestamp, status, mimetype, digest, sha1_fetched, fetch_state).

### L4 — complete Wayback chrome removal
- **Added:** `excavate.chrome` strips the Wayback toolbar, replay script includes and init
  snippets, banner stylesheets and archive trailer comments (HTML and CSS); each removal is logged
  by kind and `clean` reports the count.
- **Changed:** all replay modifiers (`if_`, `fw_`, `oe_`, `mp_`, …) are recognised in links, not
  just `id_ im_ js_ cs_`. One marker comment per document instead of one per removed element.

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
