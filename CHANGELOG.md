# Changelog

## Unreleased — SOTA loop (`claude/sota-loop`)

### L0 — foundation + correctness fixes
- **Fixed:** links rewritten on pages below the site root now resolve (page-relative paths);
  previously every root-absolute or absolute same-site link on a nested page was broken and
  mis-reported as never-crawled.
- **Fixed:** `#fragment` is preserved when a link is rewritten.
- **Fixed:** `pull` retries transient failures (`failed_5xx`) on resume; first-time failures are
  no longer labelled "re-fetch failed".
- **Changed:** dig state files are written atomically.
- **Added:** `scripts/check.sh` gate (ruff, mypy, pytest), `[dev]` extra, `docs/SOTA_ROADMAP.md`.
