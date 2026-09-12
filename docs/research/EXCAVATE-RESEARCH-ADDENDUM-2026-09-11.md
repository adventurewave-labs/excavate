# Excavate PRD — Research Addendum
**Companion to:** PRD: Digital Archaeology Platform (working title "Excavate"), Draft v0.3, 2026-09-11
**Research date:** 2026-09-11 (web sources skew 2024–2026; all prices USD)
**Method:** three research tracks — local repo/asset inventory, market & competitive scan, technical building-blocks evaluation. Findings keyed to v0.3 section numbers so amendments can be merged individually.

---

## 1. TL;DR

- **The gap holds.** As of Sep 2026, no product was found that does *private file corpus → era detection → curated narrative → chaptered site → deployed repo with PII redaction*. Four edges are being nibbled (Wayback Recon, Legacy Marker, Klokbox, ArchiveBox — §2.4), but the full stack is unoccupied.
- **Willingness-to-pay is documented at every layer of the ladder** — from $7–20/domain restoration shops up to $1,099–2,199 human memoir services (§2). The concierge "send us the drive" model has a proven adjacent market (LegacyBox/iMemories, 1M+ families each).
- **One architecture landmine:** GitHub Pages caps sites at 1 GB; Vercel Hobby caps CLI uploads at 100 MB / 15k files. A 50 GB dig's raw artifacts **cannot live in the site repo**. The PRD needs an external artifact-storage tier (§4.2) or FR-E8/E9/E14 over-promise.
- **One safety war story from your own portfolio:** velvet-bbs shipped 266 users' plaintext passwords, real names, emails and private mail in the *output repo* while its README claimed they "stay private." The corpus wasn't the leak — the build was. FR-E5 must scan the generated output pre-push, not just the corpus (§4.1).
- **The deterministic survey stack is now concretely selectable** (§4.5, §6): Siegfried, batch-mode exiftool, Apple Vision OCR, whisper.cpp, NudeNet, TLSH/MinHash-LSH, SQLite manifest. No research risk left in M2.
- **Naming:** "Excavate" is taken by an OSS tech-debt CLI that brands itself "code archaeology" (tryexcavate.com). Fresh shortlist in §3 — top picks **Varve** and **Core Sample**, incumbent **Midden**.
- **Cautionary market signals:** MyHeritage discontinued its DeepStory auto-biography (Aug 2025); Conifer (web-archiving service) sunsets June 2026. Both validate v0.3's sequencing (retro/hobbyist first) and the export/longevity requirement.
- **Ollama answer (Open Q4): yes** — 30B-class Q4 is the tier for publishable narrative prose; generate per-chapter (map-reduce), budget 5–15 min/chapter on M-series (§4.7).

---

## 2. Market & Competitive Findings

### 2.1 Wayback restoration tools (Resurrection Mode's "competition is broken" claim — verified)
- **hartator/wayback-machine-downloader** (Ruby): 5.9k stars, 181 open issues (Sep 2026), including "Doesn't properly work anymore" (#275) and an "actively maintained fork" discussion thread. De-facto maintenance decay. The PRD's premise stands.
- **wpull** (ArchiveTeam): alive, but it's a live-site crawler that writes WARC — not a restoration tool.
- **Archivarix:** first 200 files free; $10 for first 1,000 files, then $1 per additional 1,000. Outputs a static site + a free open-source flat-file PHP CMS for editing. No narrative layer, no redaction.
- **Restoration shops cluster:** waybackmachinedownloader.com $12/domain; WaybackRestoration.com from $7/domain; WaybackDownloaders.com $20/site; DomRecovery (WordPress rebuilds). All sell 1:1 fidelity. **This is the commodity floor — Resurrection Mode alone is a $7–20/domain business unless the narrative layer rides on top.**
- **Wayback Recon** (2025–26, indie, "built in a single Sunday" per vibe-coding community post): archive.org → rebuild → redeploy. Hobby-scale but directionally identical to Resurrection Mode. Expect more of these; the moat is curation/redaction/reporting, not the pull.
- **IA/CDX status:** ~60 requests/min average with 429 enforcement; users report stricter caps after the Oct 2024 IA breach (31M accounts; services back Oct 13). No formal ToS rewrite — enforcement is technical. Design the downloader to a conservative rate budget. Ambient friction: 23+ major news sites now block ia_archiverbot (Wired) — an AI-branded archiving product carries some PR edge.

### 2.2 Memorial / legacy / digital-estate market (the vertical with real money)
- **Empathy:** $72M Series C (May 2025), $162M total raised, ~$400M valuation at Series B. The bereavement-logistics category is venture-scale.
- **StoryWorth:** $59 / $109 / $199 per year tiers; 1M+ books printed since 2013. **Remento:** $99/yr. **Everplans:** $99.99/yr (vault, not narrative).
- **Memorial page builders' ceiling:** Ever Loved free + $199.99 one-time upgrade; Keeper Plus $99 one-time; ForeverMissed $159.95 lifetime (310k families, 47 countries). **Implication: price the memorial dig as a one-time project fee, not a subscription, unless you're hosting it.**
- **Market size:** digital legacy/estate estimates run $15–26B in 2025 (Precedence: $15.11B → $62.6B by 2035; Mordor: $23.02B). Definitions vary wildly — directional only, but every researcher agrees on double-digit CAGR.

### 2.3 Digitize-the-pile services (the behavioral precedent for "point it at Dad's drive")
- **LegacyBox:** $59.99 for 2 items up to $360+ bundles ($15–40/item), 6–10 week turnaround, 1M+ families served. **iMemories:** ~$29.99/tape + $49.99/yr cloud, 1M+ customers.
- LegacyBox Trustpilot ~19% one-star (quality/comms complaints) — a service-quality opening for a concierge tier done with actual care.
- **None of them output a narrative.** They output cloud galleries. The step *after* digitization is the product.

### 2.4 Direct-competitor scan (gap verification)
- **No dedicated "turn a hard drive into a website" service found.** Nearest: Tech Donkey "Digital Archaeology" (file migration lifeboat — no site output); Iron Mountain drive vaulting (enterprise).
- **Legacy Marker:** AI-generates memorial sites from *public records/obituaries* — not private corpora. **Chronicle Chronicler** (chronicle.rip): chat → memorial page. **Klokbox** ($5.99/mo / $49.99/yr): "digital clutter → storybooks," in-app output only — the closest corpus-ish product, and it doesn't deploy a site.
- **ArchiveBox** (v0.7.2, active): self-hosted URL/bookmark archiver to HTML/PNG/PDF/WARC/SQLite. Inventory/preservation layer, zero narrative. **Perkeep**: personal storage, no narrative.
- **AI memoir tools** (Memoirji, Squibler, HereAfter AI $7.99/mo voice avatar, StoryFile): all prompt- or interview-driven; none ingest a file corpus.
- **Cautionary signal:** MyHeritage **discontinued DeepStory** (tree+photos → video biography) in Aug 2025. Even MyHeritage couldn't find PMF for auto-narrative from structured personal data. Supports v0.3's retro/hobbyist-first sequencing — sell to people who already know they're archaeologists before selling to grieving families.

### 2.5 Documented price ladder (business-model input)
| Layer | Anchor | Source |
|---|---|---|
| Wayback domain restoration | $7–20/domain one-time | restoration shops, Sep 2026 |
| Prompt-based memoir SaaS | $59–199/yr | StoryWorth, Remento |
| Memorial page, one-time | $99–200 | Keeper, Ever Loved, ForeverMissed |
| Digitize-the-pile concierge | $60–400/project | LegacyBox, iMemories |
| Human memoir premium | $1,099–2,199 | No Story Lost; LifeBook ~£4,500/15k words |

---

## 3. Naming (PRD Open Q1)

### 3.1 Verdicts on the v0.3 candidates
| Name | Verdict | Conflict |
|---|---|---|
| Excavate | **TAKEN** | OSS tech-debt CLI at tryexcavate.com (npm `excavate`) that itself brands as "code archaeology" + Excalidraw phonetic slip |
| Strata | HEAVILY TAKEN | Strata Identity (acquired by Rubrik), Strata Decision Tech, O'Reilly conference legacy |
| Sediment | TAKEN (adjacent) | "Semantic memory for AI agents" — rendro/sediment, exactly your mindshare space |
| Tessera | TAKEN | Semiconductor IP chain (Adeia/Xperi) + defunct NFT protocol |
| Palimpsest | TAKEN (exact space) | Two writing apps (palimpsest.page, palimpsestapp.com) |
| Midden | **mostly clear** | One niche Rust CLI (context-heap cleaner); cleanest of the original list |
| Trowel | Lightly taken | Spanish construction app + bioinformatics tool; workable but not clean |

### 3.2 Fresh shortlist (ranked; all need final domain + trademark + app-store check before adoption)
1. **Varve** — a varve is *one year's sediment layer* in a glacial lake core: the product's strata metaphor compressed into one word. Short, pronounceable, no known software conflict. Strongest fresh pick.
2. **Core Sample** (CoreSample) — triple meaning: sediment core, hard-drive core, sampling culture (the terminal-28800 audience lives here). Two words is the con; possible drilling-industry collisions to verify.
3. **Midden** — incumbent clean pick. The refuse heap archaeologists treasure is literally what the product digs; obscure-but-sticky; needs a plain-English tagline.
4. **Sherds** — the pottery fragments used to date a site; "the fragments tell the story." Misspelling risk (shards/shirts).
5. **Test Pit** (TestPit) — the trial excavation that starts every dig; maps exactly to survey-first UX ("point it at a directory, see what's there").
6. **Mattock** — the digging tool; punchy. Verify game/tooling collisions.
7. **Loess** — wind-blown sediment deposit; elegant, but spelling/pronunciation friction.
8. **Backfill** — refilling the excavation; dev double-meaning ("backfill data") cuts both ways.
9. **Dowser** — finds what's buried; whimsical, but dowsing's pseudoscience aura may undercut the verification-first positioning.

**Recommendation:** lead with **Varve**, keep **Core Sample** and **Midden** as finalists. Whatever wins, the tagline must say "digital archaeology" in plain English — the term itself reads retro-academic to consumers (Kelsey Museum uses it for *digital tools doing archaeology*; Media Archaeology Lab owns the hobbyist resonance) and does no naming work by itself.

---

## 4. PRD v0.4 Amendments (keyed to v0.3 sections, individually dispatchable, ranked)

### 4.1 FR-E5 amendment — redaction gate scans the OUTPUT, not just the corpus *(priority 1: safety)*
**Incident:** velvet-bbs (live, public repo per audit 2026-09-10) contains 266 users' plaintext passwords, real names, emails, and private mail under `data/bbs/users/*/info` and `data/system/master.passwd-bbs-users.txt` — while the README claims they "stay private." The corpus was handled carefully; the **generated output repo** leaked. Flagged as data-breach-in-waiting; history purge recommended.
**Amended FR-E5 text (add):** *The redaction pass is two-sided: (a) corpus-side flagging of PII/credentials/secrets for review before content enters any page (deterministic patterns + agent review, ePADD-style NER assist); and (b) an **output-side scan of the generated repo/build directory before any push** — every file, including data exports, JSON payloads, and dotfiles, checked for credentials, keys, and un-approved PII. A dig cannot ship with an unclean output scan. Hard-gate, not warning.*
Precedent to cite in the PRD: Stanford's ePADD (Apache 2.0) — NER-assisted PII review with explicit redaction before email publication; your terminal-28800 PR #1 ethics pause (private-log payload deliberately unpushed pending owner decision) is the same instinct, already practiced.

### 4.2 Architecture amendment — external artifact-storage tier *(priority 2: unblocks the 50GB promise)*
**Facts:** GitHub Pages caps a published site at **1 GB** (+100 GB/mo soft bandwidth); Vercel caps CLI/uploads at **100 MB Hobby / 1 GB Pro** and 15,000 files. The v0.3 promise "originals preserved in `/archive`" is impossible in-repo at dig scale.
**Amendment (Architecture Notes + FR-E9/E14):** introduce an **artifact store abstraction**: the build reads from a store (local dir, GitHub Releases assets ≤2 GB/file, R2, or an Internet Archive item) and emits pages that link/embed from store URLs; `/archive` in the site repo holds manifests + small originals only. The ZIP export (FR-E14) is produced *from the store*, so self-hosting remains one download.
Related implementation gotcha from sdelic_org_OG: the GitHub **contents API can't carry binaries or CP437 text byte-cleanly** — the build rail must push via git proper, with the byte-perfect corpus distributed out-of-band (their solution: an out-of-git zip on the release).

### 4.3 FR-E9/E14 rewrite — archive-grade standards, minimal consumer subset *(priority 3)*
- **BagIt (RFC 8493)** for the ZIP export: `manifest-sha256.txt`, `bag-info.txt`, `data/` — fixity + portability, library-legible. Pair with zstd compression (BagIt has none built in).
- **Dublin-Core-lite metadata per artifact** (title, creator, date, description, subject, rights) emitted as a **CollectionBuilder-compatible CSV** (U. Idaho, MIT-licensed). Even if the generator stays custom Next/Astro, keeping the CSV schema CB-compatible buys interop with the library/exhibit world and a second output engine for free.
- **PREMIS-lite event log** instead of PREMIS XML: JSONL records `agent, tool, version, event_type, timestamp, input_hash` for every scan/extract/redact/generate step. This is the chain-of-custody that makes a dig reproducible — and it's the BitCurator report-set idea in consumer form.
- **WACZ, not raw WARC, plus embedded replayweb.page**: plain WARC requires full download before replay; WACZ (zip-indexed) supports range requests so big archives stream. Embed via `<replay-web-page source="...wacz">` + the `/replay/sw.js` shim — fully client-side, no server. License note: replayweb.page is **AGPL-3.0 — embed unmodified, never fork-and-close**. Webrecorder's `web-replay-gen` is the reference implementation of exactly this pattern. Conifer's sunset (June 2026) is the live justification: exports must outlive any host, including this one's own viewer.

### 4.4 New FR-E15 — Playable artifacts layer *(priority 4: demo value, schedule at M4+)*
Extends FR-E11 ("listed → playable") from media conversion to **interactive** artifacts:
- **Ruffle** (Apache-2.0/MIT, npm `@ruffle-rs/ruffle`): Flash/SWF plays in-browser; AS1/2 ~99% language coverage, AS3 usable. Gate every SWF as playable-with-screenshot-fallback.
- **v86** (BSD-2, npm `v86`) or **js-dos**: boot DOS-era artifacts — and with v86's state save/restore (zstd), ship **pre-booted snapshots**: the exhibit's "boot the dead machine" moment. One shared emulator instance per site, not per artifact; keep images lean (DOS > Win9x); **FreeDOS is the licensing-safe default OS image**; disk images live in the artifact store (§4.2), never the repo.
- **chiptune3** (libopenmpt WASM): .mod/.s3m/.xm/.it play client-side, tiny footprint vs audio assets.
This is the terminal-28800 modem-handshake lesson productized: the artifact that *performs* is what gets shared.

### 4.5 FR-E2/E12 concretization — the deterministic survey stack *(fills M2 with named parts)*
- **Format ID:** Siegfried (Go, CLI + library, PRONOM + freedesktop MIME, container-aware, MIT-family). DROID = skip (Java batch tool).
- **Metadata:** exiftool in **batch mode only** (`-r` or `-@` argfile) — per-file invocation makes 10k files take hours. One stage of the resumable manifest.
- **OCR (FR-E12):** Apple Vision `RecognizeText` as default on macOS (free, local, ANE-accelerated, beats Tesseract in practice); PaddleOCR/RapidOCR (Apache 2.0) as the cross-platform tier; Tesseract demoted to last-resort.
- **Audio:** whisper.cpp (MIT, Metal/CoreML) — large-v3 runs ~10x real-time on M-series; small/medium for triage, large only for published excerpts.
- **Image gate:** NudeNet v3 (ONNX, local) feeding the redaction gate; human-in-loop for borderline calls — triage, not adjudication.
- **Near-dup detection:** **TLSH / MinHash-LSH bucketing** — ssdeep/sdhash all-pairs is O(n²) (5×10⁹ comparisons at 100k files) and infeasible. Reserve fuzzy hashes for one-to-one drill-down checks only.
- **Manifest (resumable):** SQLite or DuckDB keyed by content hash: `(path, mtime, size, siegfried_id, exif_json, ocr_status, pii_status, published_flag)` with borg-style dirty detection on `(path, mtime, size)`. Don't trust mtime alone across filesystems (APFS→exFAT copies lose it) — hash verification is a trust level, not the default.
- **Free provenance:** parse SAUCE records in .ANS/.DIZ files (title/author/date) — strip before display, keep as metadata.
- **CP437/ANSI rendering:** `ansi_up` (MIT) + CP437→Unicode table is the standard recipe — and terminal-28800's `src/lib/ansi.tsx` is already a fuller implementation (cursor control, save/restore) that can be harvested.

### 4.6 FR-E10/E11 specifics (email + media, for M5)
- **PST:** readpst/libpst (GPL, mature, Homebrew-packaged) as the CLI stage → mbox; libpff/pypff only if in-process parsing ever matters (and it doesn't need to — CLI-first is the house pattern).
- **Google Takeout quirks (deterministic, documented):** one mbox *per label* with multiply-labeled messages duplicated; labels live in `X-Gmail-Labels` headers, not folders; All-Mail includes archived+sent; **no threading — rebuild via Message-ID/In-Reply-To**; dedup on `X-Gmail-Message-ID`.
- **Documents:** Apache Tika (Apache 2.0) as fast first-pass text+metadata extractor; LibreOffice headless as the fidelity fallback for WordPerfect/Word 6 rendering. Treat LO output as *derivative*; never discard originals. Pool `soffice` instances — one per file is slow.
- **RealAudio/RealMedia: no fork needed.** FFmpeg 8.x still ships native RA (Cook/SIPR/ATRAC3/RA144/288) decoders and the RM demuxer, and *added* a native RealVideo 6.0 decoder in 8.0 (Aug 2025). Keep ffmpeg a CLI subprocess for GPL isolation; transcode to Opus/MP3 derivatives for the exhibit.

### 4.7 Open Q4 answered — local LLM (Ollama) tier
**Yes.** 30B-class at Q4 (~16–24 GB) is the minimum tier for publishable chapter prose in 2026 (Gemma 4 31B / Qwen3.x 27–35B class; 7–8B only for titles/captions/tagging). Throughput on M-series: ~20–30 tok/s generation at that tier → **5–15 min per chapter job** after prefill; 70B = 15–45 min and needs the RAM. **Design call: map-reduce — generate per-chapter/per-stratum from sampled cluster summaries, never one 50k-token mega-prompt** (better prose, resumable, fits context). Ollama exposes OpenAI-compatible endpoints, so local ↔ hosted BYO keys is a config swap. FR-E4's cost estimator gets a second axis: hosted = dollars ±20%, local = minutes (free tokens, time-metered).

### 4.8 Risks — additions to §8
- **Illegal-content exposure (new, must be explicit):** pointing at *any* hard drive can surface CSAM or other illegal material. Posture: private-by-default, never auto-publish (a human approves every deploy — already structurally true), deterministic NSFW/PII gates are triage not adjudication, and a written policy for what the operator does when a scan surfaces illegal content (stop + escalate; the tool is not an investigative instrument). This risk exists whether or not the PRD names it.
- **Estate authority (memorial vertical):** name the frame — **RUFADAA** (Revised Uniform Fiduciary Access to Digital Assets Act, adopted by most US states) governs fiduciary access to deceased persons' digital assets; the existing attestation checkbox aligns with it. GDPR does not cover the deceased at the EU level but member-state rules vary — keep the memorial vertical geo-scoped at launch.
- **Auto-narrative PMF is not guaranteed** (MyHeritage DeepStory discontinued Aug 2025) — hence retro/hobbyist-first sequencing stays load-bearing, and the deterministic layer (survey, viewers, provenance) must be valuable with the LLM turned off.
- **Platform fragility is the norm, not the edge case:** IA post-breach 429 tightening; Conifer sunsetting June 2026; GitHub Pages/Vercel size caps. The export-first stance (FR-E14 + §4.2/§4.3) is the correct hedge — keep it non-negotiable.

### 4.9 Milestones — revisions
- **M0:** implement per magpu's README pipeline (already the working spec): CDX inventory → `id_` raw fetch → URL localization/rewrite → navigation-driven crawl → reconstruction ledger. Rate budget to the observed ~60 req/min CDX cap, conservative.
- **M2:** survey engine = §4.5 stack (Siegfried + batch exiftool + Vision OCR + SQLite/DuckDB manifest + TLSH near-dup). Dogfood corpus: terminal-28800's raw data or the planned old-drive dump.
- **M3:** redaction gate goes two-sided per §4.1 (output-repo scan is a ship blocker).
- **M4:** artifact-store abstraction (§4.2) lands *before* template packs ship publicly — it's what makes multi-GB digs deployable at all; CollectionBuilder-compatible CSV becomes the exhibit metadata format.
- **M5:** WACZ export + replayweb.page embed (replaces "optional WARC"); playable layer (§4.4) if FR-E15 accepted; email adapter with §4.6 specifics.

### 4.10 Success metrics — tweaks
- Add: **output-repo leak-scan pass rate** — every push passes an automated credential/PII scan of the *built* repo; target is zero exceptions (hard gate, so it's a checklist metric, not a ratio).
- Split LLM-cost metric: hosted (dollars, ±20% estimator) vs local (minutes/chapter).

### 4.11 Business model section (new §, if adopted)
Blended ladder from §2.5: OSS core stays free; **Resurrection as a $29–79/service tier** (above the $7–20 commodity floor, justified by the verification report + deploy); **Excavation digs as $149–499 projects** (anchored between memorial pages and LegacyBox bundles); **concierge "send us the drive"** at LegacyBox-anchored $300–800 (digitize + dig, with the care LegacyBox's 19% one-star crowd isn't getting); **memorial template packs + hosting** as the recurring layer if demand appears. Memorial pricing = one-time, per §2.2.

### 4.12 Wedge strategy (Open Q2 — trade-off memo, call left open per mp)
- **Option A — OSS survey core, both modes one product (recommended):** the deterministic, no-LLM survey engine is the credible OSS wedge ("BitCurator for normal people"), costs nothing to run, and every dig starts there anyway. Paid = hosted UI, memorial packs, concierge. Con: the OSS story is "a scanner," which markets quieter than "resurrect dead sites."
- **Option B — Resurrector OSS + Excavation paid (v0.1's split):** simpler story, sexier OSS demo. Con: forks the unified pipeline v0.3 was written to avoid, and strands the redaction/report discipline on the paid side where fewer people see it.
- **Recommendation: A,** with Resurrection Mode's first triumphant dig (velvet-bbs dogfood is M0) doing the marketing the scanner can't.

---

## 5. Reusable-Asset Map (your repos are the head start v0.3 undersells)

| Repo | Supplies | Maps to |
|---|---|---|
| **magpu** (public) | README-documented 5-step CDX pipeline (921 URLs inventoried, `id_` raw fetch, URL localization, mp3→archived-ogg twin matching, reconstruction ledger: 853 files/340MB, 3 eras, version portal); nginx Dockerfile + Railway deploy | M0 spec; Resurrection Mode end-to-end |
| **strops-org-wayback-archive** | Cleanest CDX methodology: 1,340 rows audited with no collapse/no mimetype filter; `im_` replay with `id_` fallback; magic-byte validation; SHA1 manifest (CSV+JSON); completeness accounting (664 referenced / 340 recovered / 410 never-crawled); AES-256 release zip; zero-dependency serverless gate (`api/gate.js`) | Survey/manifest design; FR-E9 provenance; FR-E14 export; password gate |
| **terminal-28800** | `scripts/prep_irc.py` (cp1252 decode, mIRC color strip, per-channel chrono merge, nick leaderboards); `src/lib/ansi.tsx` (full ANSI.SYS renderer beyond ansi_up); `src/proxy.ts` sysop gate (Next 16 proxy gotcha: `middleware.ts` silently ignored; 401 on `/data/*.json`; latency-throttled login); WebAudio modem handshake; ARJ unsealing (`tools/arjroot`); Telix `.PBK` binary RE (247-byte records); PR #1's stacked second gate + ethics pause | FR-E2 legacy parsers; FR-E6 viewers; FR-E7 theming; FR-E5 workflow precedent |
| **velvet-bbs** | `DATA_NOTES.md` binary RE from a level-0 `dump(8)` (8160-byte MSGBODY field-offset table incl. alignment pad); pure `parse.mjs` → `seed.mjs` separation; scrypt re-hash of 295 original passwords; self-seeding Railway deploy (`railway.json` + `nixpacks.toml`); **and the §4.1 incident** | Dynamic track; binary-format survey; redaction-gate justification |
| **sdelic_org / sdelic_org_OG** | `archive_extract/hunt/` CDX hunt JSONs; donated-files track (`www_orig/`, `original-2001/` byte-mirror, `<base href>`-strip as sole edit, per-file provenance README); disclosed-deviations model (archivist notes inline, missing files listed as missing, never faked); contents-API binary/CP437 gotcha | Provenance conventions (FR-E9); donation intake; build-rail constraints |
| **hammer-of-the-gods** | `scripts/parse_data.py` → typed JSON data layer with documented data corrections; screenshot verification scripts | Data-correction ledger pattern; verification |
| **html-sites** | GitHub Actions auto-deploy rail for single-file sites (`deploy.yml`/`deploy.sh`/`generate-showcase.js`), 90+ sites shipped | The static output rail; zero-config deploys |

**Honest gaps:** no OCR tooling exists anywhere in the portfolio (FR-E12 is a genuine build, not a harvest); no WARC/WACZ handling (every project used CDX + `id_`/`im_` — the replay layer is new); NSFW/PII scanning is new code, though strops' magic-byte validation and terminal-28800's byte-size audit matching are adjacent prior art.

---

## 6. Build / Borrow / Skip (the stack, decided)

| Area | USE directly | MIMIC | SKIP |
|---|---|---|---|
| Forensics | BagIt (RFC 8493) | BitCurator report set; ePADD redaction flow; DC + PREMIS-lite | Archivematica; full OAIS/PREMIS tooling |
| Replay/export | warcio; ReplayWeb.page (embed unmodified, AGPL); web-replay-gen (reference) | — | Conifer (sunsetting Jun 2026); Emularity/JSMESS |
| Exhibits | CollectionBuilder CSV schema + (optional) template | Quire chapter model; Wax IIIF; Scalar paths | Omeka S |
| Playback | Ruffle (npm); v86 **or** js-dos; chiptune3; ffmpeg (RA decoders in-tree) | — | Emularity |
| Extraction | LO headless; Tika; readpst; dbfread; ansi_up | — | pypff as primary (CLI-first) |
| Local ML | Apple Vision OCR (macOS); whisper.cpp; NudeNet; PaddleOCR/RapidOCR (tier 2) | — | Tesseract as primary |
| Scale | Siegfried; exiftool (batch mode only) | borg files-cache resume; git-annex keying; LSH dedup | ssdeep/sdhash all-pairs; DROID |
| LLM | Ollama (30B-class Q4 tier) | map-reduce chapter generation | one-shot mega-prompts |

---

## 7. Sources (key)

**Market/competitive:** github.com/hartator/wayback-machine-downloader (+issues/275) · archivarix.com/en/blog/webarchive-2026 · waybackmachinedownloader.com pricing · blog.archive.org/2024/10/21/internet-archive-services-update-2024-10-21 · archive.org/about/terms.php · wired.com/story/the-internets-most-powerful-archiving-tool-is-in-mortal-peril · welcome.storyworth.com/storyworth-pricing · remento.co · fortune.com/2025/05/29/exclusive-empathy-raises-72-million-series-c · everplans.com/pricing · support.everloved.com (pricing) · mykeeper.com/pricing · forevermissed.com · precedenceresearch.com/digital-legacy-market · mordorintelligence.com/industry-reports/digital-legacy-market · legacybox.com · imemories.com/legacy · legacydigital.net/imemories-vs-legacybox · education.myheritage.com (DeepStory discontinued) · nostorylost.com · meminto.com/compare · klokbox.com/pricing · chronicle.rip/chronicler · legacymarker.com · archivebox.io · perkeep.org · techdonkey.com/services/digitization-archiving/digital-archaeology · tryexcavate.com · lib.rs/crates/midden · github.com/rendro/sediment · palimpsest.page

**Technical:** bitcurator.net (+github.com/BitCurator/bitcurator-salt) · epadd.stanford.edu · loc.gov/standards/bagit · loc.gov/standards/premis · dublincore.org · github.com/webrecorder/warcio · replayweb.page/docs/embedding · github.com/webrecorder/web-replay-gen · webrecorder.net/blog/2025-12-18-browsertrix-1-21 · docs.github.com/en/pages (limits) · vercel.com/docs/limits · collectionbuilder.github.io · github.com/minicomp/wax · quire.getty.edu · journal.code4lib.org/articles/18372 (lib-static) · github.com/ruffle-rs/ruffle · github.com/copy/v86 · js-dos.com · npmjs.com/package/chiptune3 · ffmpeg.org + phoronix.com/news/FFmpeg-8.0-Released · tika.apache.org · help.libreoffice.org (MS import limits) · github.com/drudru/ansi_up · dbfread.readthedocs.io · formulae.brew.sh/formula/libpst · github.com/libyal/libpff · developer.apple.com/documentation/vision/recognizing-text-in-images · github.com/PADDLEPADDLE/PADDLEOCR · github.com/notAI-tech/NudeNet · virusbulletin.com/virusbulletin/2015/11/optimizing-ssdeep-use-scale · huggingface.co/blog/dedup · exiftool.org/mistakes.html · github.com/richardlehane/siegfried · reddit.com/r/LocalLLaMA (Apr 2026 tier thread) · inventivehq.com/blog/local-llm-performance-what-to-expect

---

## 8. Net-Additions Accounting & Rejected-as-Bloat

**Accounting:** 1 file added (this one), 0 modified, 0 deleted, no code, no git operations. The PRD master stays wherever it lives; every amendment in §4 is written to be merged by section number.

**Considered and rejected as bloat (with reasons):**
- **Full IIIF stack (Wax, Omeka S, image tiles):** scholarly-infrastructure weight; CollectionBuilder CSV gives the interop without the tile pipeline. Revisit only if an institutional customer asks.
- **Archivematica integration:** server-fleet microservices for a consumer CLI; its useful idea (report set) is mimicked in §4.3 instead.
- **Browsertrix/crawling in MVP:** corpus is donated files, not live web; add post-M5 only.
- **ssdeep/sdhash at scale:** O(n²); replaced by TLSH/MinHash-LSH.
- **Tesseract as primary OCR:** dominated by Vision (macOS) + PaddleOCR (cross-platform).
- **Quire/Hugo scholarly toolchain:** generates chaptered output we can emit directly; the *model* is mimicked, the toolchain isn't adopted.
- **Custom WARC viewer:** replayweb.page embeds client-side under AGPL-unmodified; building one is pure duplication.
- **Per-file exiftool invocation:** hours-long scans; batch mode is the only acceptable pattern.
- **Additional output files (research memo + strategy memo + naming memo separately):** one addendum, keyed to the PRD, is the merge-friendly unit.

**Status of PRD §10 Open Questions after this research:**
1. **Naming** — shortlist delivered (§3); final pick pending mp (verify domain/trademark before adopting).
2. **One product vs. two** — trade-off memo §4.12; recommendation Option A; **open**.
3. **Memorial vertical branding** — research supports current sequencing (template pack later, not separate brand at v1); MyHeritage's DeepStory shutdown argues for caution on narrative-first memorial marketing.
4. **Local LLM** — answered: yes, Ollama 30B-class Q4, map-reduce chapters (§4.7).
