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

- [x] **L3 — tokenizer-based rewriting** (82 → 103 tests)
  - New `rewrite.py`: a span tokenizer that locates each URL-bearing value and replaces only that
    span — quoting, spacing, attribute order and broken markup pass through untouched. One scanner
    feeds both the rewriter and the link graph.
  - Now handled: unquoted attributes, `srcset`, `background`, `poster`, `<object data>`, `lowsrc`,
    `longdesc`, `cite`, `<meta refresh>`, inline `style`, `<style>` blocks, CSS `@import`,
    `<base href>` (links resolved against it, tag removed, logged), entity-encoded URLs
    (`&amp;` in a query used to hash to a different file name than the real URL).
  - No longer mistaken for links: anything inside `<script>`, comments, `<textarea>`, `<title>`,
    CSS comments, or plain text. `ftp:` / `news:` / `irc:` / `aim:` links are external, not
    "never-crawled". File names containing `#`, `%` or `?` are escaped when linked.
  - Unverified: exercised on hand-written fixtures only, not on real 1990s tag soup at volume.
    Not handled: URLs built in JavaScript, `<applet archive>`, SVG `xlink:href`, conditional
    comments. Wayback prefixes with a short (<6 digit) timestamp are not recognised.

- [x] **L4 — complete Wayback chrome removal** (103 → 114 tests)
  - New `chrome.py` removes the toolbar block, the `/_static/js/` playback/wombat/ruffle includes,
    the `__wm.init` / `__wm.wombat` / `archive_analytics` init scripts, banner stylesheets, the
    include end marker and the `FILE ARCHIVED ON` / `playback timings` trailers (HTML and CSS).
    A replayed fixture page cleans back to its original byte for byte, bar one marker comment.
  - Every removal is logged by kind and counted (`chrome_removed`), separately from URL rewrites.
    All two-letter replay modifiers (`if_ fw_ oe_ mp_ …`) are stripped from links.
  - Patterns are narrow on purpose: a site's own `/static/js/…` or `banner-styles.css` survives;
    legacy `/static/js/analytics.js` is only removed on a page that carries another Wayback marker.
  - Unverified: the fixture is written from memory of what replay injects, not captured from a
    live `im_` response — whether the toolbar's trailing newline belongs to the page is a guess.
    Not handled: the `_____WB$wombat$assign$function_____` wrapper around `js_` files (we do not
    clean `.js`), and chrome inside framesets fetched with `if_`.

- [x] **L5 — CDX inventory robustness** (114 → 129 tests)
  - Inventory is paginated (`showResumeKey` + `limit=5000`, resume keys followed, loop guard). It
    was a single unpaginated request — a large site would have been silently truncated or timed out.
  - CDX calls go through the same rate limiter and 429/5xx/transport backoff as `pull`; if a page
    still fails, the inventory aborts and **nothing is written** (no partial inventory).
  - `inventory --match prefix|host|domain` and `--from/--to` (validated before any request). Still
    no collapse / mimetype / status filter on the wire — a test pins the request parameters.
  - `manifest.json` (required by the M0 spec, never written before): the spec's seven fields per
    URL plus the query and timestamp; refreshed after `pull`.
  - Unverified: the resume-key page shape (`[], ["key"]` tail, header repeated per page) is from the
    CDX docs, not observed live. `--match domain` pulls subdomain captures, but `clean` still treats
    other hosts as external and same-path files from two hosts collide (disclosed, suffixed) —
    per-host layout is a follow-up for L10.

- [x] **L6 — capture selection + fallback chain** (129 → 146 tests)
  - `inventory --at YYYY[MMDD…]` picks each URL's capture nearest that date (a site as it stood in
    one era); default is still the latest. A fetchable capture always beats a nearer error/revisit.
  - Each URL remembers up to 5 alternate captures. When the wanted one is permanently gone (4xx in
    both replay modes), `pull` works down the alternates before declaring the file lost. The row
    records what was wanted and what was used; the report lists every fallback.
  - A transient failure never triggers fallback (it is retried on resume, mid-chain included); an
    exhausted chain is permanent and is not re-walked by resume or re-inventory.
  - Unverified: how often a second capture is retrievable when the first 404s on the real Wayback
    Machine — the mechanism is tested, the recovery rate is unknown until a live run. Each fallback
    attempt costs up to 2 requests of the 30/min budget.

- [x] **L7 — SQLite state store** (146 → 160 tests)
  - Working state is `dig.sqlite` (WAL); `pull` commits one row per fetched URL instead of
    rewriting the whole `captures.json`. Measured here: per-row write is ~1.2 ms at 1k, 20k and
    100k URLs; the old whole-file rewrite was 41 ms at 20k and 211 ms at 100k — about six hours of
    cumulative disk writes over a 100k-URL pull.
  - `captures.json` is still written as an export, once per command (including a pull cut short by
    Ctrl-C). Existing digs migrate on first use. If the JSON was changed by anything other than our
    own export (hand-edited, or written by an older version) it is re-imported — the file a human
    can see is never ignored.
  - Resume acceptance test from the M0 spec now exists: kill mid-run → zero duplicate fetches.
  - Unverified: a hard kill (SIGKILL / power loss) mid-pull leaves `captures.json` stale until the
    next command; the database is correct and wins, but that path is covered by a simulated test,
    not a real kill. Not designed for two `excavate` processes on one dig at the same time.

- [x] **L8 — crawl-check stage** (160 → 174 tests)
  - New `crawl.py` + `excavate check [--strict]`: walks every internal link of the cleaned site
    (HTML and CSS) and writes `crawl.json`. Each referenced target is classified: recovered /
    **never archived** (no capture exists) / **archived but not recovered** (failed, skipped or
    pending — with the reason) / **case mismatch** (on disk under different letter case).
  - Reachability from the entry page: reachable files and orphans (recovered files nothing links to).
  - The report now carries the strops-style accounting line and the three lists, each with the
    pages that link to the missing file. `never_crawled` keeps its M0 meaning (everything
    referenced but not on disk) — broken down, not redefined. `--strict` exits 1 if anything is
    unresolved.
  - Unverified: fixture site only. Links created by JavaScript, image maps served by CGI, and
    form targets are invisible to the walk, so "orphan" means "not statically linked", not "unused".

- [x] **L9 — asset twin swap** (174 → 191 tests)
  - New `twins.py`: when a link's target was never recovered, `clean` points it at a surviving
    equivalent that is on disk — letter-case variant, extension spelling (`.htm`/`.html`,
    `.jpg`/`.jpeg`), the directory's other index document, or another format of the same medium
    (`.mp3` → `.ogg`, the magpu precedent). Page-relative links are covered too.
  - Never guesses: ambiguous case/spelling/index candidates mean no swap; a link with no twin stays
    exactly as written and stays listed as missing. No file is ever created.
  - Each swap is a logged rewrite with its reason; the report has a count and a "Twin swaps"
    section. `clean --no-twins` gives a strictly as-archived rebuild.
  - Unverified: not run against the real magpu corpus the rule comes from. A format twin changes
    what the visitor gets (a different encoding, or an image in another format) — that is disclosed
    but is a judgment call; image format twins (`.gif` → `.png`) are the most debatable.

- [x] **L10 — never-crawled recovery** (191 → 201 tests)
  - New `excavate discover [--limit N] [--recheck]`: for each file the pages link to that is not
    in the manifest, ask CDX for that exact URL (scheme-, `www`- and case-insensitive on IA's side;
    ignores the inventory's `--from/--to` window). Hits join the dig as pending captures marked
    `discovered_by`; their CDX rows are appended to `manifest.csv`. Then `pull` → `clean` → `report`.
  - Budgeted and resumable: one rate-limited request per candidate, lookups remembered in
    `discover.json`, `--limit` defers the rest, an error mid-run keeps what was found. Re-inventory
    no longer drops discovered captures.
  - The crawl check and report now list the hosts the site links out to (subdomains / sibling
    hosts that may deserve their own dig).
  - Unverified, and worth saying plainly: with the default unwindowed prefix inventory, CDX has
    already been asked for everything under the domain, so `discover` will mostly *confirm* absence
    rather than recover files. Its real yield is on windowed or `--match host` digs. Files hosted
    on another host are still not pulled or localized — only listed. Query-string pages cannot be
    reversed from their file name and are skipped.

- [x] **L11 — provenance** (201 → 214 tests)
  - `events.jsonl`: append-only PREMIS-lite log, one line per stage run (inventory, pull, clean,
    check, discover, report) with `agent, tool, version, event_type, timestamp, input_hash` and the
    outcome. An interrupted pull is recorded as incomplete. Same input → same hash; `clean` also
    records the hash of the site it produced, so "did this rebuild change?" has a one-line answer.
  - `site/PROVENANCE.json`: every file in the rebuilt site cites its URL, capture timestamp, a
    Wayback link to that exact capture, the CDX digest and whether it verified, the replay mode,
    the wanted capture when a fallback was used, and whether cleaning modified the bytes. It ships
    with the site; if the site has its own `PROVENANCE.json`, ours goes to the dig root instead.
  - The report gained a "Chain of custody" section.
  - Unverified: this is PREMIS-*lite* by the PRD's field list — it has not been validated against
    the PREMIS schema or loaded into any preservation tool. Timestamps are the local clock, unsigned;
    the log is tamper-evident only in the sense that hashes would stop matching.

- [x] **L12 — output-scan gate** (214 → 230 tests)
  - New `excavate scan [--path DIR]`: scans every file that would be published (dotfiles, data
    files and binaries included) for private keys, cloud/API tokens, password hashes and
    passwd-format lines, clear-text credential assignments, payment card numbers (Luhn-checked),
    SSNs, risky file names (`.env`, `*.sql`, `master.passwd*`, `.git/…`) and bulk emails/phones.
  - A hard gate: exit 1 with any unreviewed finding. An archive or oversized file that could not be
    inspected is itself a finding. A reviewed finding is allow-listed by fingerprint with a
    mandatory reason (`scan --allow <fp> --reason "…"`, kept in `scan-allow.json`); the allowance
    covers that exact value in that file only.
  - Findings, `scan.json` and the report show a redacted preview, never the secret. The report
    states the gate: not run / stale (site changed since the scan) / clean / BLOCKED.
  - Unverified: pattern coverage is the well-known credential shapes, tested on synthetic strings;
    it has not been run on the velvet-bbs output that motivated it, and false-positive rates on
    real old sites are unknown. It does not look inside archives, does no NER for names or
    addresses, and nothing yet *prevents* a deploy — there is no deploy step in M0 to block.

- [x] **L13 — BagIt export** (230 → 249 tests)
  - New `excavate export [--zip] [--no-raw] [--require-clean] [--output DIR]`: an RFC 8493 bag —
    `bagit.txt`, `bag-info.txt` (with Payload-Oxum), `manifest-sha256.txt`, `tagmanifest-sha256.txt`
    and a payload of `data/site/` (the rebuild), `data/raw/` (captures byte-for-byte as fetched) and
    `data/dig/` (report, rewrite ledger, CDX manifest, capture state, crawl check, event log, scan).
  - `excavate export --verify <bag|zip>`: completeness and every checksum, tag manifest, Payload-Oxum,
    path-escape checks; exit 1 on any problem. `--zip` output is deterministic and single-rooted.
  - The bag records the output-scan verdict in `bag-info.txt`. Decision: a bag is a preservation
    copy, so it is written whatever the gate says and *labelled*; `--require-clean` makes the gate
    a refusal. Flag that if you would rather export always refused.
  - Unverified: validated only by this repo's own verifier — not yet checked with an independent
    tool (`bagit.py` from the Library of Congress, or bagger). ZIP uses deflate, not zstd (the PRD's
    suggestion needs Python 3.14). No WACZ output; that part of FR-E14 is not started.

- [x] **L14 — operator UX** (249 → 266 tests)
  - `excavate run <domain>`: inventory → pull → clean → check → scan → report in one command
    (`--limit`, `--at`, `--from/--to`, `--match`, `--discover`, `--no-twins`). It says what is left
    and how to resume. `--strict` exits 1 unless the pull finished, every link resolves and the
    publish gate is clean.
  - `excavate status`: counts by state, site size, link accounting, gate verdict, last event and the
    next command to run — no network.
  - `--json` on every command (before or after the subcommand): one JSON object with `result`,
    `messages`, `exit_code`, and `error` on failure. Exit codes are documented in `--help`.
  - `pull`: per-URL progress and a lower-bound ETA on stderr (`--quiet` to silence); `--dry-run`
    prints "921 URLs ≈ at least 31 min at 30 req/min" and fetches nothing.
  - Unverified: `run` is exercised end to end only against a mocked archive. The ETA is the rate
    budget alone — real runs are slower (fallbacks, `im_` retries, backoff), by how much is unknown.
    `pull` still exits 0 with failures left over; only `run --strict` turns that into a failing code.

## Backlog (in order)

- [ ] **L15 — wrap-up.** One end-to-end golden fixture (synthetic site served by a mocked IA
  transport, exercising every loop above), property tests for `safe_relpath` and the rewriter,
  refresh README / M0 spec status / CHANGELOG, and a final summary of what is still unverified.

## Deliberately out of scope here

- GitHub/Vercel deploy (M1) — external side effects; needs a human decision per site.
- A CI workflow — the gate is `scripts/check.sh`; wiring it to GitHub Actions is a one-file
  follow-up if wanted.
- Anything requiring an LLM or a paid API.
