# Excavate (working title)

**Point it at any digital remains — a Wayback URL, a cloud drive, a directory, an entire hard drive — and it excavates, catalogs, and rebuilds them into living websites that tell the story.**

Two modes, one pipeline:
- **Resurrection** — dead website from the Wayback Machine → modern, deployed site.
- **Excavation** — arbitrary corpus (drive, Drive folder, ZIP, mbox) → inventory → era detection → curated, chaptered **story site** with a redaction gate.

## Status
- **PRD v0.4** — [`docs/PRD.md`](docs/PRD.md) (research-backed; addendum in [`docs/research/`](docs/research/))
- **M0 Resurrection CLI** — implemented and hardened on branch `claude/sota-loop` (draft PR #3, not merged);
  spec and its implementation status: [`docs/specs/M0-resurrection-cli.md`](docs/specs/M0-resurrection-cli.md);
  what each hardening loop did and what is still unverified: [`docs/SOTA_ROADMAP.md`](docs/SOTA_ROADMAP.md).
- **Not yet run against the live Wayback Machine.** Every test uses a mocked archive. The magpu and strops
  acceptance fixtures named in the spec are not in this repo, so those two acceptance tests are still open.
- Naming undecided (working title only — "Excavate" collides with an existing OSS project; shortlist and domain checks in the PRD §10.1 / addendum §3)

## Quick start
```bash
pip install -e .
excavate run magpu.com            # inventory → pull → clean → check → scan → report
excavate status                   # where the dig stands, what to run next (no network)
excavate pull --dry-run           # how many URLs, how long — fetches nothing
excavate export --zip             # BagIt bag: site + raw captures + paper trail
```
A dig lives in `digs/<domain>/`; every command resumes it. Every command takes `--json`.
Exit codes: `0` done · `1` failed, or a gate said no · `2` usage.

## Commands
| command | what it does |
|---|---|
| `run <domain>` | the whole pipeline; `--limit N` to pull in instalments, `--discover`, `--strict` (exit 1 unless complete and the gate is clean) |
| `inventory <domain>` | CDX survey, paginated, never collapsed or filtered. `--match prefix\|host\|domain`, `--from/--to`, `--at DATE` (captures nearest a date instead of the latest) |
| `pull` | fetch captures at ≤30 req/min, resumable. `id_` then `im_` replay, then other captures of the same URL; digest-verified. `--limit`, `--dry-run`, `--quiet` |
| `clean` | `raw/` → `site/`: links made page-relative, Wayback chrome removed, each page kept in its own charset, dead links pointed at surviving twins (`--no-twins` to disable). Every change logged in `REWRITES.md` |
| `check` | walk the cleaned site's links → `crawl.json`: recovered / never archived / archived but not recovered / case mismatch / orphans. `--strict` |
| `discover` | ask CDX for the exact URL of each linked-but-missing file; hits become pending captures |
| `scan` | publish gate: credentials and bulk personal data in `site/` (or `--path DIR`). Exit 1 on any unreviewed finding; `--allow FP --reason "…"` |
| `report` | `VERIFICATION-REPORT.md` — counts, integrity, fallbacks, twin swaps, crawl check, gate, chain of custody |
| `export` | BagIt (RFC 8493) bag; `--zip`, `--no-raw`, `--require-clean`; `--verify BAG` checks any bag |
| `status` | state counts, link accounting, gate verdict, next command |

## Pipeline (Resurrection, M0)
```
domain ──inventory──▶ manifest.csv (every raw CDX row) · manifest.json · dig.sqlite
       ──pull────────▶ raw/        (id_ → im_ → alternate captures; ≤30 req/min; digest-verified; resumable)
       ──clean───────▶ site/       (de-Waybacked, charset-faithful, twin swaps) · REWRITES.md · site/PROVENANCE.json
       ──check───────▶ crawl.json  (links walked: recovered / missing / orphans)
       ──discover────▶ more pending captures (exact-URL lookups for linked-but-missing files)
       ──scan────────▶ scan.json   (publish gate — exit 1 if anything unreviewed)
       ──report──────▶ VERIFICATION-REPORT.md
       ──export──────▶ export/<domain>-bag[.zip]  (BagIt: site + raw + paper trail)
```
Standing rules: a missing file is listed as missing, never faked; every rewrite is logged; the Internet Archive
is read at half its observed rate ceiling; every stage appends to `events.jsonl`.

## Repo layout
```
docs/PRD.md                 # product master (v0.4)
docs/research/              # 2026-09-11 research addendum (market, stack, naming)
docs/specs/                 # delta specs per milestone (rig Law 5)
docs/SOTA_ROADMAP.md        # hardening loops L0–L15: what landed, what is unverified
src/excavate/
  cli.py                    # commands, --json, run
  cdx.py                    # CDX inventory: pagination, scope, capture selection, alternates
  pull.py                   # rate-limited fetch, backoff, digest check, fallback chain
  clean.py                  # raw/ → site/ orchestration
  rewrite.py                # span tokenizer: finds and rewrites URLs in HTML/CSS
  chrome.py                 # Wayback toolbar / script / trailer removal
  encoding.py               # charset detection, byte-faithful round trip
  twins.py                  # surviving-equivalent lookup for dead links
  crawl.py                  # link-graph audit (check)
  discover.py               # exact-URL lookups for linked-but-missing files
  scan.py                   # output-scan publish gate
  provenance.py             # events.jsonl, site/PROVENANCE.json
  bagit.py                  # BagIt export + verifier
  report.py · status.py · dig.py (state store: dig.sqlite + captures.json export)
tests/                      # 288 tests, all offline (mocked archive, on-disk fixtures)
digs/<domain>/              # one dig — git-ignored; digs are data, not code
```

## Development
```bash
pip install -e ".[dev]"
scripts/check.sh            # ruff + mypy + pytest — the gate every change must pass; no network, no keys
```
`tests/test_golden.py` runs one synthetic site through the whole pipeline against a mocked archive and pins the
exact output; `tests/test_properties.py` throws seeded random URLs, markup and bytes at the path mapper,
rewriter and charset layer.

## Process
This repo follows the rig constitution (`~/rig/AGENTS.md`): every PR gated by `~/rig/scripts/review.sh` (builder ≠ reviewer), agents never push to main, **humans merge**. License TBD with the OSS-wedge decision (PRD §10.2).
