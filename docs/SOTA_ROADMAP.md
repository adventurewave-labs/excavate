# SOTA roadmap — Resurrection CLI hardening loop

Branch `claude/sota-loop` · one loop ≈ one item · started 2026-10-02.
Scope is the deterministic layer (PRD §6: "must be valuable with the LLM turned off").
No LLM stages, no deploy, no paid API calls, no live Internet Archive traffic.

## Loop contract

Every loop, in order:

1. `git pull --rebase origin claude/sota-loop`; pick the **first unchecked** item below.
2. Implement it with tests. Tests are offline: `httpx.MockTransport` and on-disk fixtures only.
   `web.archive.org` is not reachable from the build environment and must never be hit by tests.
3. Gate: `scripts/check.sh` (ruff + mypy + pytest) must be fully green. Never weaken a test to pass.
4. Update `CHANGELOG.md`; tick the item here with the test-count delta and one line on what
   remains unverified. If an item is too large, land the coherent core and add a follow-up line.
5. Commit and push to `claude/sota-loop`. **Never push to `main`, never merge** — humans merge (README § Process).
6. Product rules that outrank any item: never fake a missing file; every rewrite is logged in
   `REWRITES.md`; state stays resumable and kill-safe; the ≤30 req/min IA budget is never raised.

## Done

- [x] **L0 — foundation + correctness fixes** (43 → 50 tests)
  - Rewritten links are now **page-relative**. Before, `/top.html` linked from `sub/deep/page.html`
    was written as `top.html` — a broken link on every non-root page, and a false "never-crawled"
    entry in the report for each one.
  - `#fragment` survives a rewrite (was silently dropped).
  - `pull` now retries transient failures (`failed_5xx`) on resume, as the report always claimed;
    permanent 4xx is still never re-requested. First-time failures are no longer labelled "re-fetch".
  - `captures.json` / `meta.json` are written atomically (tmp + rename) — a kill mid-write can no
    longer truncate the resume state.
  - Tooling: ruff + mypy config, `[dev]` extra, `scripts/check.sh` gate; lint and type errors cleared.
  - Unverified: nothing here has run against the live Wayback Machine or the magpu/strops fixtures
    (those corpora are not in this repo).

- [x] **L1 — CDX digest verification** (50 → 63 tests)
  - Every `id_` body is hashed (SHA-1, base32) and compared with the CDX `digest`; the verdict is
    stored per capture as `digest_ok` + `digest_note` and survives re-inventory.
  - Three honest outcomes: verified, **mismatch** (kept on disk — it is the only copy — and listed
    in the report), or unverifiable with the reason (`im_` replay, content-encoded payload, no
    usable CDX digest, pulled before this check existed).
  - Unverified: the content-encoding rule is inferred, not observed — it assumes Wayback exposes the
    original encoding as `Content-Encoding` or `X-Archive-Orig-Content-Encoding` on `id_` replies.
    If it does not, gzip-archived captures will show as mismatches on a live run. Follow-up: hash
    the raw (undecoded) stream so those become verifiable instead of n/a.

- [x] **L2 — encoding-faithful clean** (63 → 82 tests)
  - New `encoding.py`: BOM → declared charset (`<meta>` / CSS `@charset`, WHATWG label mapping) →
    UTF-8 → windows-1252 → byte-transparent latin-1. A candidate is accepted only if it decodes
    strictly and re-encodes to the exact original bytes.
  - Cleaned pages are re-encoded in their **own** charset (was: always UTF-8 under an unchanged
    `charset=iso-8859-1` declaration → mojibake). A page with no rewrites is copied byte-for-byte.
  - A wrong or unknown charset declaration is disclosed in `REWRITES.md`; a rewritten URL the
    charset cannot express becomes an HTML character reference / CSS escape, never `?`.
  - Unverified: fixtures are synthetic (ISO-8859-1, Shift_JIS, CP437, KOI8-R, UTF-8/16 BOMs), not
    real captures. No statistical sniffing — an undeclared Shift_JIS/KOI8 page is handled
    byte-transparently (correct output, but its link scan runs on latin-1-decoded text).

## Backlog (in order)

- [ ] **L3 — tokenizer-based rewriting.** Replace the regex attribute scan with an
  `html.parser`-driven span rewriter (edits only the URL bytes, leaves all other markup untouched):
  unquoted attributes, `srcset`, `background`, `poster`, `data`, `longdesc`, `<meta refresh>`,
  `<base href>` resolution, inline `style=""`, CSS `@import`; never rewrite inside `<script>` or comments.
- [ ] **L4 — complete Wayback chrome removal.** Toolbar block
  (`<!-- BEGIN WAYBACK TOOLBAR INSERT -->…<!-- END … -->`), `/_static/` script/link injections,
  wombat / `__wm` init scripts, the `FILE ARCHIVED ON` trailer comment, and all replay modifiers
  (`id_ im_ js_ cs_ if_ fw_ oe_ mp_`). Each removal logged. Fixtures modelled on real `im_` output.
- [ ] **L5 — CDX inventory robustness.** Pagination (`showResumeKey` + `limit`), the CDX call goes
  through the rate limiter with 429/5xx backoff, `--match prefix|host|domain` (subdomains),
  `--from/--to` bounds, and `manifest.json` (required by the M0 spec, not yet written).
- [ ] **L6 — capture selection + fallback chain.** `--at YYYYMMDD` picks the capture nearest a date
  (a coherent era instead of "latest of everything"). When the chosen capture fails permanently,
  fall back to the next-best capture of the same URL from `manifest.csv` before declaring it lost;
  the report discloses which capture was actually used.
- [ ] **L7 — SQLite state store.** `dig.sqlite` (WAL) replaces rewriting the whole `captures.json`
  after every row (O(n²) at 100k URLs; PRD FR-E2 already decides on SQLite). Transparent migration
  from existing digs; `captures.json` still exported for humans and old tooling.
- [ ] **L8 — crawl-check stage (spec stage 4).** Its own module and `excavate check`: internal link
  graph, reachability from the entry page, orphans, and the strops-style accounting
  (referenced / recovered / never-crawled) written to `crawl.json` and consumed by the report.
- [ ] **L9 — asset twin swap.** Dead link → surviving equivalent that is actually in the dig
  (`.mp3`→`.ogg`, case variants, `.htm`↔`.html`, `www`/scheme variants, trailing slash). Always a
  logged rewrite with its reason; never applied when the twin is not on disk.
- [ ] **L10 — never-crawled recovery.** `inventory --discover`: for never-crawled targets, query CDX
  for the exact URL and its scheme/`www`/subdomain variants and add hits to the dig — the
  "navigation-driven crawl" step from the magpu pipeline. Bounded and budgeted.
- [ ] **L11 — provenance.** PREMIS-lite `events.jsonl` (`agent, tool, version, event_type,
  timestamp, input_hash`) for every stage, plus `site/PROVENANCE.json` mapping each output file to
  URL, capture timestamp, CDX digest and replay mode (PRD FR-E9).
- [ ] **L12 — output-scan gate.** `excavate scan`: deterministic secret/credential/PII scan over
  everything in `site/` (keys, tokens, password files, private-key blocks, bulk emails/phones),
  reviewed-allowlist file, non-zero exit on findings (PRD FR-E5b — the velvet-bbs lesson).
- [ ] **L13 — BagIt export.** `excavate export --bagit`: RFC 8493 bag (`bagit.txt`,
  `manifest-sha256.txt`, `tagmanifest`, `bag-info.txt`) built deterministically, plus a verifier
  (`excavate export --verify`) (PRD FR-E14).
- [ ] **L14 — operator UX.** `excavate run <domain>` (inventory → pull → clean → check → report),
  `status`, `--json` on every command, meaningful exit codes, progress + ETA, and `pull --dry-run`
  ("921 URLs ≈ 31 min at 30 req/min").
- [ ] **L15 — wrap-up.** One end-to-end golden fixture (synthetic site served by a mocked IA
  transport, exercising every loop above), property tests for `safe_relpath` and the rewriter,
  refresh README / M0 spec status / CHANGELOG, and a final summary of what is still unverified.

## Deliberately out of scope here

- GitHub/Vercel deploy (M1) — external side effects; needs a human decision per site.
- A CI workflow — the gate is `scripts/check.sh`; wiring it to GitHub Actions is a one-file
  follow-up if wanted.
- Anything requiring an LLM or a paid API.
