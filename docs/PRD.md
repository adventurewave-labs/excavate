# PRD: Digital Archaeology Platform (working title: "Excavate")
**Author:** Marcus Patman · **Date:** 2026-09-11 · **Status:** Draft v0.4
**Supersedes:** v0.3 (same date, pre-research); v0.1 "Wayback Resurrector"
**v0.4 changelog:** research amendments folded in from `docs/research/RESEARCH-ADDENDUM-2026-09-11.md` — competitive/market verification, naming shortlist, artifact-storage tier, two-sided redaction gate, archive-grade export standards, playable-artifacts layer, concrete survey stack, Ollama tier, new risks, revised milestones/metrics, business-model section. Repo: `adventurewave-labs/excavate` (private).

## 1. Vision
**Point it at any digital remains — a Wayback URL, a cloud drive, a directory, an entire hard drive — and it excavates, catalogs, and rebuilds them into living websites that tell the story.**

Two complementary modes, one pipeline:
- **Resurrection Mode** — restore a dead website from the Wayback Machine (or other archives) into a modern, deployed site. (Full spec: v0.1, carried forward as Appendix A.)
- **Excavation Mode** — point at an arbitrary corpus (Google Drive folder, Dropbox, local directory, mounted old hard drive) and the agent inventories what's there, identifies eras/themes/artifacts, and generates one or more **narrative "story sites"** — curated, browsable, chaptered — from the raw material.

Proof-of-concept already exists in Marcus's own portfolio: terminal-28800 (raw IRC logs + BBS lists + phreaker phonebooks → curated "Dial-Up Archaeology" site with a sysop-login experience), sdelic.org (donated original files → reassembled multi-page site), hammer-of-the-gods (tour data → archive site), velvet-bbs (binary BBS dump → live multi-user revival), magpu (Wayback CDX pipeline → 3-era archive site). Excavation Mode productizes that manual workflow; the magpu README already documents the Resurrection pipeline end-to-end.

## 2. Problem
- Everyone over ~35 has digital remains: old hard drives, ZIP disks dumped to folders, Drive accounts full of dead projects, family photo dumps, defunct band/personal sites. The material is unbrowsable — thousands of files, no structure, no narrative.
- Existing tools are either **downloaders** (Wayback gems — verified broken/decaying: hartator gem at 181 open issues incl. "Doesn't properly work anymore"; restoration shops sell 1:1 fidelity for $7–20/domain with no curation), **file organizers** (dedupe/photo apps — no narrative output; LegacyBox/iMemories turn tapes into cloud galleries, not stories), or **generic site builders** (start from nothing, not from a corpus).
- Nothing turns *raw digital sediment into a story you can walk through* — and nothing closes the loop to a deployed, shareable site. Re-verified by market scan, Sep 2026: the corpus→narrative→deployed-repo stack remains unoccupied end-to-end (nearest nibbles: Wayback Recon, Legacy Marker, Klokbox, ArchiveBox — none combine private corpora, era detection, narrative, deploy, and redaction).

## 3. Target Users
1. **Digital archaeologists / nostalgia rebuilders** — retro-computing, BBS/IRC-era culture, personal history (primary persona = Marcus).
2. **Families & memorializers** — "here's Dad's hard drive" → a legacy site with chapters. Emotionally powerful, willing to pay (memorial pages clear $99–200 one-time; Empathy raised $72M Series C May 2025; digital-legacy market est. $15–26B 2025).
3. **Agencies/freelancers** — dead-site restoration for clients (Resurrection Mode).
4. **Domain/SEO investors** — revive expired domains with original structure (Resurrection Mode).
5. **Researchers/OSINT/historians** — corpus → navigable exhibit.

## 4. Core User Flows

### 4.1 Resurrection (Wayback → live site)
Unchanged from v0.1: connect keys → paste URL → CDX inventory → select → rate-limit-aware pull → de-Wayback clean → agentic rebuild (static/dynamic track) → GitHub repo → Vercel/Railway deploy → verification report. See Appendix A. Pipeline reference implementation: magpu README (CDX inventory → `id_` raw fetch → URL localization → navigation-driven crawl → reconstruction ledger).

### 4.2 Excavation (corpus → story site)
1. **Connect a source** — pluggable source adapters:
   - Local path / mounted drive (CLI)
   - Google Drive folder (OAuth)
   - Dropbox / OneDrive (v1.5)
   - Uploaded ZIP
   - Wayback pull output (the two modes chain: resurrect a site, then excavate it into a story)
2. **Survey (the "dig grid")** — deterministic scan first, no LLM: file tree, types, sizes, date ranges (mtime + embedded metadata + EXIF), text extraction (incl. legacy encodings — CP437/ANSI, Word 6, WordPerfect, .nfo/.diz), duplicate detection, media thumbnailing. Output: a **Site Survey manifest**.
3. **Interpret (agentic)** — user's LLM key (hosted, or local via Ollama) reads the survey + sampled content and proposes:
   - **Strata**: detected eras/clusters ("1996–99: BBS files", "2003–05: band era", "2011: photo dump from the Mexico move")
   - **Story structures**: one site vs. several; suggested chapters; the narrative angle ("a sysop's archive", "a musician's decade", "a family's migration")
   - **Exhibits**: hero artifacts worth featuring (the oldest file, the weirdest file, recurring characters found in logs)
4. **Curate** — user reviews the proposal: accept/rename chapters, exclude private material (**redaction pass**: agent flags PII, credentials, addresses, phone numbers for review before anything is published), pick tone (museum-formal / zine / terminal-retro / memorial).
5. **Build** — agent generates the site(s): chaptered narrative pages + browsable raw-artifact galleries (logs viewer, image galleries, document reader), themed (templates incl. retro terminal, era-authentic styling). **Raw originals are preserved byte-identical in the artifact store, not the site repo** (§6, artifact-storage tier); the site embeds/links to them.
6. **Ship** — GitHub repo (private by default) + Vercel deploy; optional password gate (à la terminal-28800's sysop login). **A dig cannot ship until the output-scan gate passes** (FR-E5).
7. **Report** — DIG-REPORT.md: what was found, what was featured, what was excluded/redacted, what couldn't be read (unknown formats).

## 5. Feature Requirements (Excavation Mode)
- **FR-E1 Source adapters:** local FS, Google Drive, ZIP (v1); Dropbox/OneDrive (v1.5). Read-only, streamed, never modifies source.
- **FR-E2 Survey engine:** deterministic, resumable, handles 100k+ files; legacy-format text extraction; EXIF/metadata timeline; NSFW/media classification for triage. *Concrete stack (decided):* Siegfried (PRONOM format ID); exiftool in **batch mode only** (`-r`/`-@` — per-file invocation is hours-slow); Apple Vision OCR default on macOS with PaddleOCR/RapidOCR as cross-platform tier and Tesseract last-resort; whisper.cpp for audio triage; NudeNet (local ONNX) as the image gate feeding the redaction flow (triage, not adjudication — human-in-loop for borderline); near-duplicate detection via **TLSH/MinHash-LSH bucketing** (ssdeep/sdhash all-pairs is O(n²) — rejected); resumable **SQLite/DuckDB manifest** keyed by content hash — `(path, mtime, size, siegfried_id, exif_json, ocr_status, pii_status, published_flag)` — with borg-style dirty detection on `(path, mtime, size)` and hash-verification as a trust level, not the default (mtime is unreliable across APFS→exFAT copies); SAUCE records in .ANS/.DIZ parsed as free provenance; CP437/ANSI rendering via ansi_up + CP437→Unicode table (terminal-28800's `src/lib/ansi.tsx` is a fuller harvestable implementation).
- **FR-E3 Strata detection:** clustering by time + path + content type; agent-labeled eras.
- **FR-E4 Story proposal:** multiple candidate narratives with chapter outlines; token-cost estimate before running (**two axes: hosted = dollars ±20% target; local = minutes per chapter, free tokens**); "one directory = one story" scoping or whole-drive multi-site plan.
- **FR-E5 Redaction pass (two-sided, hard gate):** mandatory review gate before any deploy.
  - **(a) Corpus side:** PII/credential/secret scan (deterministic patterns + agent review, ePADD-style NER assist — Stanford ePADD is the precedent for NER-assisted PII review before publication).
  - **(b) Output side:** an automated scan of the **generated repo/build directory before any push** — every file, including data exports, JSON payloads, and dotfiles — for credentials, keys, and unapproved PII. A dig cannot ship with an unclean output scan. Hard gate, not warning.
  - *Justifying incident:* velvet-bbs shipped 266 users' plaintext passwords, real names, emails, and private mail in its output repo (`data/bbs/users/*/info`, `data/system/master.passwd-bbs-users.txt`) while its README claimed they "stay private." The corpus was handled carefully; the generated output leaked. The gate scans what gets published, not just what gets read.
- **FR-E6 Artifact viewers:** IRC/BBS log reader (ANSI rendering), image gallery, document reader, code viewer, audio player — raw material stays browsable, not just summarized.
- **FR-E7 Theming:** template packs (terminal-retro, museum, zine, memorial, era-authentic ~1998 GeoCities); custom via agent.
- **FR-E8 Multi-site output:** a whole-drive dig can emit N repos/sites or one site with N chapters — user choice.
- **FR-E9 Provenance & metadata (archive-grade):** every page cites its source files (path + date); originals byte-identical **in the artifact store (§6)**. Per-artifact Dublin-Core-lite fields (title, creator, date, description, subject, rights) emitted as a **CollectionBuilder-compatible CSV** (MIT; buys library-world interop and a second output engine); a **PREMIS-lite event log** (JSONL: `agent, tool, version, event_type, timestamp, input_hash`) records every scan/extract/redact/generate step — chain-of-custody that makes a dig reproducible. Reconstruction follows the disclosed-deviations model (sdelic_org_OG): archivist notes inline, genuinely-missing files listed as missing, never faked.
- **FR-E10 Email adapter:** mbox / PST / Gmail Takeout as sources; threads become strata/chapter input like any other artifact. *Concrete:* readpst/libpst (GPL, mature) as the CLI stage → mbox; Google Takeout quirks handled deterministically (one mbox per label with multiply-labeled duplication; labels in `X-Gmail-Labels` headers, not folders; All-Mail includes archived+sent; threading rebuilt via Message-ID/In-Reply-To; dedup on `X-Gmail-Message-ID`).
- **FR-E11 Media resurrection layer:** convert dead formats to playable/viewable — RealAudio/old codecs (stock ffmpeg: native RA Cook/SIPR/ATRAC3/RA144/288 decoders + RM demuxer remain in-tree through 8.x, which also added native RealVideo 6.0 — no preservation fork needed; keep ffmpeg a CLI subprocess for GPL isolation), tracker music .mod/.s3m/.xm (libopenmpt; chiptune3 WASM in-browser), WordPerfect/Word 6 (Apache Tika as fast first-pass text/metadata extractor; LibreOffice headless as fidelity fallback — output is derivative, originals never discarded, `soffice` instances pooled), ANSI art rendering. "Listed" → "playable in the exhibit." Interactive artifacts: FR-E15.
- **FR-E12 OCR pass:** scanned docs, screenshots of chats, photographed papers → extracted text feeds the story engine (deterministic, pre-LLM). *Stack per FR-E2:* Vision (macOS default) → PaddleOCR/RapidOCR (cross-platform) → Tesseract (fallback).
- **FR-E13 Timeline view:** first-class cross-chapter chronological scrubber over all dated artifacts.
- **FR-E14 Archive-grade export:** **BagIt (RFC 8493)** ZIP export of any built site (`manifest-sha256.txt`, `bag-info.txt`, `data/`; pair with zstd — BagIt has no compression layer); optional **WACZ output** (zip-indexed, range-request-streamable — not plain WARC, which requires full download before replay) replayable in any static site via the embedded **replayweb.page** client-side viewer (AGPL-3.0: embed unmodified, never fork-and-close; `sw.js` shim must 404 from the real server); sites must be trivially self-hostable ("this outlives Vercel"). Conifer's June 2026 sunset is the standing justification.
- **FR-E15 Playable artifacts layer (new):** extend media resurrection to *interactive* artifacts, in-browser:
  - **Ruffle** (Apache-2.0/MIT, npm): Flash/SWF plays client-side (AS1/2 ~99% language coverage; AS3 usable with edge cases). Every SWF gated playable-with-screenshot-fallback.
  - **v86** (BSD-2) or js-dos: DOS-era executables run in-browser; with v86's zstd state save/restore, ship **pre-booted snapshots** — the exhibit's "boot the dead machine" moment. One shared emulator instance per site (not per artifact); disk images live in the artifact store, never the repo; **FreeDOS is the licensing-safe default OS image**; keep booted images lean (DOS > Win9x).
  - **chiptune3** (libopenmpt WASM): .mod/.s3m/.xm/.it play client-side.
  - This productizes terminal-28800's modem-handshake lesson: the artifact that *performs* is what gets shared.
- **FR-E16 Artifact-storage tier (new):** the build reads from / writes to a **pluggable artifact store** — local directory, GitHub Releases assets (≤2 GB/file), R2, or an Internet Archive item — and emits pages that link/embed store URLs. `/archive` in the site repo holds manifests + small originals only. The ZIP export is produced from the store, so self-hosting stays one download. *Reason: GitHub Pages caps published sites at 1 GB; Vercel caps uploads at 100 MB Hobby / 1 GB Pro and 15k files — originals-in-repo is physically impossible at dig scale.*

## 6. Architecture Notes
- Same pipeline skeleton as v0.1: **deterministic stages (Survey, Extract, Redact-scan) + agentic stages (Interpret, Build)**, resumable job manifest, local-first, BYO keys.
- Source adapters and output adapters are the two plugin surfaces; Wayback is just one source adapter — this unifies both modes into one product.
- **Artifact-storage tier** (FR-E16) sits between Build and Ship; site repo ≠ data store.
- **Local LLM tier:** Ollama as the private-processing option — 30B-class Q4 (~16–24 GB) is the minimum tier for publishable chapter prose in 2026 (7–8B only for titles/captions/tagging). Generation is map-reduce per-chapter/per-stratum from sampled cluster summaries, never one mega-prompt (better prose, resumable, fits context). Budget ~5–15 min/chapter at that tier on M-series. Ollama exposes OpenAI-compatible endpoints, so local ↔ hosted BYO keys is a config swap.
- **Build rail constraint (learned):** push via git proper, never the GitHub contents API — it can't carry binaries or CP437 text byte-cleanly (sdelic_org_OG); byte-perfect corpora travel out-of-band (release asset / artifact store).
- Large-corpus economics: survey is free (no LLM); interpretation samples intelligently (summaries of clusters, not every file) — cost estimator shown before the agent runs (FR-E4).
- Deterministic layer must be valuable with the LLM turned off (survey, viewers, provenance, export) — narrative PMF is not guaranteed (MyHeritage discontinued DeepStory, Aug 2025).

## 7. Differentiation
- Wayback tools: download-only, broken (verified Sep 2026). Archivarix: PHP CMS lock-in, $10/1k files, no narrative. Restoration shops ($7–20/domain): 1:1 fidelity only, no curation/redaction/reporting. Photo organizers & digitization services (LegacyBox/iMemories, $60–400/project, 1M+ families each): output galleries, not stories. AI memoir tools: prompt/interview-driven, none ingest a corpus (MyHeritage DeepStory discontinued). Klokbox: in-app storybooks, no deployed site. Site builders: no corpus ingestion. **Nobody does corpus → curated narrative site → deployed repo with redaction. Re-verified Sep 2026.**
- Library-world precedent validates the discipline (BitCurator for donated drives, ePADD for email redaction, CollectionBuilder for CSV→exhibit) without competing for consumers — positioning headroom: "BitCurator for normal people."
- Fits CTM positioning: agentic pipeline with verification, reports, and human review gates (redaction) — not a one-shot generator.

## 8. Risks
- **Privacy:** hard drives contain secrets. Redaction gate is non-negotiable **and two-sided (FR-E5 — the velvet-bbs incident was an output-repo leak, not a corpus leak)**; private-by-default repos; local-first processing; nothing leaves the machine except LLM calls the user controls.
- **Illegal content (new, explicit):** pointing at *any* hard drive can surface CSAM or other illegal material. Posture: private-by-default; never auto-publish (a human approves every deploy — structurally already true); NSFW/PII gates are triage, not adjudication; a written policy for what the operator does when a scan surfaces illegal content (stop + escalate; the tool is not an investigative instrument). This risk exists whether or not the PRD names it.
- **Copyright in corpora:** user attestation; personal-archive framing.
- **Emotional stakes (memorial use):** quality bar is higher; ship retro/hobbyist personas first, memorial vertical after templates mature. (MyHeritage's DeepStory shutdown reinforces: narrative-first memorial marketing is unproven; the deterministic layer is the guaranteed-value floor.)
- **Estate/consent (memorial):** publishing a deceased person's drive requires authority — attestation checkbox ("I have the right to publish this material"); **RUFADAA** (adopted by most US states) is the governing frame for fiduciary access to digital assets; GDPR does not cover the deceased at EU level but member-state rules vary — keep the memorial vertical geo-scoped at launch. Private-by-default remains the backstop.
- **Corpus scale:** cap v1 at ~50GB / 100k files with clear messaging; whole-drive scans need the resumable survey engine solid first.
- **Platform fragility (new):** IA post-breach 429 tightening (~60 req/min CDX budget, conservative); Conifer sunsetting June 2026; GitHub Pages 1 GB / Vercel 100 MB caps. Export-first (FR-E14/16) is the standing hedge — keep it non-negotiable.
- **AI-scraper PR ambient friction:** 23+ major news sites block ia_archiverbot (Wired, 2025–26); an AI-branded archiving product carries edge — the verification/report discipline and IA-respecting rate budgets are the rebuttal.

## 8b. Success Metrics
- Time-to-first-deployed-site (target: < 30 min Resurrection, < 2 hrs Excavation on a 10GB corpus)
- % of corpus surfaced in the story (featured + browsable vs. ignored)
- Redaction catch rate (secrets flagged pre-publish; zero post-publish incidents) **+ output-scan gate: zero exceptions allowed, checklist metric not a ratio**
- Repos deployed / week; % of digs exported to ZIP/WARC (longevity adoption)
- LLM cost per dig vs. estimator accuracy (**split: hosted = dollars ±20%; local = minutes/chapter**)

## 9. Milestones (revised v0.4)
- **M0 (2 wks):** Resurrection CLI — inventory/pull/clean → local static folder. Spec: `docs/specs/M0-resurrection-cli.md` (pipeline per magpu README; rate budget to the observed ~60 req/min CDX cap, conservative). Dogfood: velvet-bbs. **Fixture tests: re-run the magpu corpus (921 CDX URLs) through the CLI and diff against the shipped 853-file site; strops (1,340 CDX rows) and sdelic become fixtures #2/#3 — known-good outputs already exist.**
- **M1 (+2 wks):** GitHub + Vercel deploy + verification report.
- **M2 (+3 wks):** Excavation survey engine (local dir + ZIP) + strata detection — **stack per FR-E2 (Siegfried + batch exiftool + Vision OCR + SQLite/DuckDB manifest + TLSH near-dup)**; dogfood on a real old-drive dump.
- **M3 (+3 wks):** Agentic story proposal + build + **two-sided redaction gate (output-repo scan is a ship blocker)** → first end-to-end story site.
- **M4 (+4 wks):** Google Drive adapter, template packs, Railway dynamic track, web UI shell; **artifact-store abstraction (FR-E16) lands before template packs ship publicly — it's what makes multi-GB digs deployable; CollectionBuilder-compatible CSV becomes the exhibit metadata format.**
- **M5 (+4 wks):** Email adapter (FR-E10 specifics), media resurrection layer, OCR pass, timeline view, **BagIt ZIP + WACZ export with embedded replayweb.page** (replaces "optional WARC"); playable layer (FR-E15) if accepted into scope.

## 10. Open Questions
1. **Brand:** "Excavate" is **taken** (OSS tech-debt CLI at tryexcavate.com self-branded "code archaeology"); Strata/Sediment/Tessera/Palimpsest all conflict-heavy. Fresh shortlist researched: **Varve** (one year's sediment layer — the strata metaphor in one word; but exact-match .app/.dev/.io registered), **Core Sample** (sediment core + hard-drive core + sampling culture; **coresample.app and .dev likely available — only shortlist name with exact-match domains open**), Midden (cleanest software-space availability; exact-match .app/.dev registered), Sherds, Test Pit, Mattock, Loess, Backfill, Dowser. Current ranking: Core Sample ≥ Varve > Midden. **Decision pending (mp).**
2. **One product with two modes, or Resurrector as the OSS wedge and Excavation as the paid layer?** Trade-off memo in addendum §4.12; recommendation = **OSS survey core, both modes one product** ("BitCurator for normal people"; the survey layer is free to run, credible, and every dig starts there). **Open — mp's call.**
3. **Memorial vertical:** separate branding later, or a template pack? Research supports template-pack-first at v1 (DeepStory cautionary precedent; sequencing already retro-first). 
4. **Local LLM support (Ollama)?** **Answered: yes** — 30B-class Q4 tier, map-reduce chapter generation (§6).

## 11. Business Model (new, draft — pending Q2 decision)
Blended price ladder (documented anchors, Sep 2026):
| Tier | Price anchor | Basis |
|---|---|---|
| OSS core (survey engine, viewers, export) | free | wedge; zero API-cost anxiety; credibility |
| Resurrection as a service | $29–79/domain | above the $7–20 commodity floor, justified by verification report + deploy |
| Excavation dig (self-serve) | $149–499/project | between memorial pages and LegacyBox bundles |
| Concierge "send us the drive" | $300–800/project | LegacyBox/iMemories precedent ($60–400 digitization, 1M+ families each; LegacyBox ~19% one-star = care-quality opening) |
| Memorial template packs + hosting | one-time per dig + optional hosting | memorial pages ceiling $99–200 one-time — do not force subscriptions |
| Premium memoir-tier digs | up to $1,099+ | No Story Lost / LifeBook human-memoir ceiling |

Concierge logistics and memorial branding follow Q2/Q3 decisions.

---
## Appendix A — Resurrection Mode full spec
(Carried from v0.1: competitive analysis of hartator gem/Archivarix/SaaS downloaders, CDX inventory, rate-limit-aware downloader with `id_` raw URLs, de-Wayback cleaner, static/dynamic rebuild tracks, deploy adapters, verification/screenshot diff, business model options, IA ToS + copyright risks. See PRD-wayback-resurrector.md — original file not yet recovered into this repo; the v0.1 substance is superseded here by verified 2026-09 research in any case.)
