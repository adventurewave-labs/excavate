# Spec M0 — Resurrection CLI (inventory · pull · clean → local static folder)

**Status:** Implemented on `claude/sota-loop` (draft PR #3), awaiting human review and a live run — see *Implementation status* at the end · **Date:** 2026-09-11 · **Milestone:** M0 (PRD §9) · **Repo:** `adventurewave-labs/excavate`
**Ceremony:** delta spec per rig constitution Law 5; reviewed in the same PR as the skeleton it introduces.

## Goal

A local-first CLI that turns a dead domain into a cleaned static site folder on disk. No LLM, no deploy, no story generation — those are M1+. The pipeline is the one already proven manually in `magpu` (README documents it end-to-end) and `strops-org-wayback-archive` (methodology). M0 productizes that exact workflow.

## Non-goals (M0)

- No deploy adapters (M1), no LLM stages (M3), no survey/strata (M2), no OCR, no WARC/WACZ (M5).
- No modification of any source; Internet Archive is read with respect (rate budget below).

## Command contracts

```
excavate inventory <domain> [--output digs/<domain>/]   # CDX survey → manifest
excavate pull [--dig digs/<domain>/] [--limit N]        # fetch captures per manifest
excavate clean [--dig digs/<domain>/]                   # de-Wayback → site/
excavate report [--dig digs/<domain>/]                  # completeness accounting
```

Every command is idempotent and resumable against the manifest; `report` emits `VERIFICATION-REPORT.md`.

## Pipeline (5 stages, per magpu + strops evidence)

1. **Inventory** — query CDX API `url=<domain>*`; **no collapse, no mimetype filter** (strops lesson: 1,340 raw rows audited that way; filtering hides captures). Resolve each URL to its best surviving capture. Output `manifest.csv` + `manifest.json`: `url, timestamp, status, mimetype, digest, sha1_fetched, fetch_state`.
2. **Pull** — fetch raw content via `https://web.archive.org/web/<timestamp>id_/<url>` (unrewritten, no toolbar), with `im_` replay fallback when `id_` 404s (strops pattern). **Rate budget: ≤ 30 req/min sustained, hard-stop backoff on HTTP 429** (observed ceiling ~60 req/min post-Oct-2024-breach tightening; we run at half). Magic-byte validation on every body (content-type lies). Resumable: `fetch_state` per row (`pending|fetched|failed_4xx|failed_5xx|skipped`).
3. **Clean** — de-Wayback rewrite into `site/`:
   - localize same-domain URLs to relative paths; map directory URLs → `index.html`;
   - strip Wayback chrome (`banner-styles.css`, `iconochive.css`, injected toolbar markup — catalogued from sdelic captures);
   - swap never-archived asset links to surviving equivalents when they exist in the manifest (magpu precedent: dead `.mp3` links → archived `.ogg` twins);
   - **every rewrite logged in `REWRITES.md`** (path, before → after, reason) — disclosed-deviations model, never silent.
4. **Crawl-check** — the live site's own navigation drives verification: every internal link in cleaned pages must resolve to a pulled file, a manifest-known-missing file, or a deliberate rewrite. Gaps become the report's "never-crawled" section (strops accounting: 664 referenced / 340 recovered / 410 never-crawled).
5. **Report** — `VERIFICATION-REPORT.md`: counts (inventoried / fetched / failed / never-crawled), rewrites applied, files genuinely missing (listed as missing, **never faked**), anything reconstructed by hand (reconstruction ledger — magpu rebuilt 2 files this way and documented both).

## Acceptance tests (fixtures with known-good outputs)

Replaced 2026-11-16: the magpu and strops corpora were never added to this repo and the shipped
853-file comparison artifact no longer exists, so those two fixture tests were unsatisfiable as
written. They are superseded by the **live-dig acceptance test**, which has now **PASSED**:

1. **Live-dig acceptance (supersedes magpu + strops fixtures) — PASSED 2026-11-15/16.** One complete
   dig against the real Wayback Machine (`info.cern.ch`), judged by the pipeline's own accounting:
   114,893 raw CDX rows (paginated live, no collapse/filter) → 9,143 unique URLs → 3,644 fetched with
   zero unclassified failures; site rebuilt (3,644 files / 3,207 pages); crawl accounting renders
   (983 referenced / 645 recovered / 272 archived-not-recovered / 65 never-archived, all 65 confirmed
   absent via `discover`); publish gate holds a real finding (not auto-approved); BagIt bag exported
   and verified VALID. Evidence: `docs/live-dig-2026-11-16.md`. Residual risk lives in
   `docs/SOTA_ROADMAP.md` (see its 2026-11-16 update note).
2. **Resume test:** kill pull mid-run; re-run; zero duplicate fetches; manifest states consistent.
3. **429 test:** mocked 429 → backoff engaged, no dropped rows.
4. **Degradation test:** lived, not mocked — two 45–50-minute cycles under Wayback slowdown resumed
   cleanly with zero corruption (evidence log, cycles 11–12 and P-Cycle 1).

## Constraints

- Python 3.11+, stdlib + minimal deps (httpx or requests; no framework). Local execution only (control plane; nothing heavy enough for a remote plane).
- Secrets never in git (Law 9) — no keys required for M0 (CDX + replay are keyless).
- Read-only toward IA; the rate budget is a product feature, not just politeness.

## Implementation status (2026-10-02, branch `claude/sota-loop`)

What the code does today, against this spec. Detail per change: `docs/SOTA_ROADMAP.md`.

| spec item | state |
|---|---|
| `inventory` — no collapse, no mimetype filter, best capture per URL, `manifest.csv` + `manifest.json` | done; also paginated, rate-limited, `--match`, `--from/--to`, `--at` |
| `pull` — `id_` with `im_` fallback, ≤30 req/min, backoff on 429, magic-byte check, resumable | done; also CDX digest verification, fallback to other captures of the same URL, SQLite state |
| `clean` — localize URLs, directory → `index.html`, strip Wayback chrome, twin swap, `REWRITES.md` | done; also charset-faithful output, tokenizer-based rewriting, `<base href>`, `srcset`, CSS `@import` |
| crawl-check — every internal link resolves or is accounted for | done (`excavate check`, `crawl.json`) |
| `report` — counts, rewrites, missing listed never faked, reconstruction ledger | done; the reconstruction ledger is a fixed "none" line — there is no way yet to *record* a hand reconstruction |
| every command idempotent and resumable | done |

Beyond the spec (pulled forward from the PRD because they are deterministic and cheap): `discover`, `scan`
(FR-E5b output gate), `export` (FR-E14 BagIt), provenance (FR-E9), `run`, `status`, `--json`.

### Acceptance tests

1. **Live-dig acceptance (supersedes the magpu + strops fixtures)** — **PASSED** 2026-11-15/16 against the
   real Wayback Machine; evidence and per-cycle log: [`docs/live-dig-2026-11-16.md`](../live-dig-2026-11-16.md).
   The no-collapse/no-filter invariant (the strops fixture's real assertion) also ran live: 114,893 raw rows
   survived pagination unfiltered.
2. **Resume test** — passing (`tests/test_state_store.py`: kill mid-run, re-run, zero duplicate fetches).
3. **429 test** — passing (`tests/test_m0.py`, `tests/test_cdx_inventory.py`: backoff engaged, no dropped rows).

### Not verified

No command has been run against the live Wayback Machine — the build environment cannot reach it. Behaviours
inferred from documentation rather than observed: the CDX resume-key page shape, which header `id_` replay uses
to expose a content-encoded payload, and what `im_` replay injects into HTML.
