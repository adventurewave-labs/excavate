# Excavate (working title)

**Point it at any digital remains — a Wayback URL, a cloud drive, a directory, an entire hard drive — and it excavates, catalogs, and rebuilds them into living websites that tell the story.**

Two modes, one pipeline:
- **Resurrection** — dead website from the Wayback Machine → modern, deployed site.
- **Excavation** — arbitrary corpus (drive, Drive folder, ZIP, mbox) → inventory → era detection → curated, chaptered **story site** with a redaction gate.

## Status
- **PRD v0.4** — [`docs/PRD.md`](docs/PRD.md) (research-backed; addendum in [`docs/research/`](docs/research/))
- **M0 in progress** — Resurrection CLI; spec: [`docs/specs/M0-resurrection-cli.md`](docs/specs/M0-resurrection-cli.md)
- Naming undecided (working title only — "Excavate" collides with an existing OSS project; shortlist and domain checks in the PRD §10.1 / addendum §3)

## Repo layout
```
docs/PRD.md                        # product master (v0.4)
docs/research/                     # 2026-09-11 research addendum (market, stack, naming)
docs/specs/                        # delta specs per milestone (rig Law 5)
src/excavate/                      # CLI (M0: inventory · pull · clean · report)
```

## Pipeline (Resurrection, M0)
```
domain ──inventory──▶ manifest (CDX, no filters)
       ──pull────────▶ raw captures (id_ + im_ fallback, ≤30 req/min, resumable)
       ──clean───────▶ site/  (de-Wayback, rewrites logged, never fake missing files)
       ──report──────▶ VERIFICATION-REPORT.md (completeness accounting)
```

## Development
```bash
pip install -e .
excavate --help
```

Fixture tests run the **magpu** corpus (~921 CDX URLs) through the CLI and diff against the known-good 853-file shipped site — see the M0 spec's acceptance tests.

## Process
This repo follows the rig constitution (`~/rig/AGENTS.md`): every PR gated by `~/rig/scripts/review.sh` (builder ≠ reviewer), agents never push to main, **humans merge**. License TBD with the OSS-wedge decision (PRD §10.2).
