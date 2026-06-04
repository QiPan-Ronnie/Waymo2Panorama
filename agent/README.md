# Agent Workspace — Waymo2Panorama

> **TL;DR for agents new to this repo**: the 4 living docs are `handoff.md` + `progress.md` + `decision_briefs.md` + this `README.md` — write to those ONLY. Don't create new `.md` files under `deliverables/`. **Before starting any new experiment direction, open a brief in `decision_briefs.md` (the experiment gate — it has Kill criteria + Max scope).** Read the [rules below](#-rules-for-new-agents-per-user-2026-05-27) FIRST.

> **Current pointer (2026-06-04):** read the top of `handoff.md`, `progress.md`, `decision_briefs.md`, and `plans/2026-06-04-egsr-seam-and-route-roadmap.md` before acting. DB43/DB44 are accepted; DB45 is running. DB45e accepted only `vggt-roi-confidence-diagnostic-only`; DB45f accepted only `vggt-target-uv-sampling-diagnostic-only`; DB45g accepted only `vggt-official-source-decode-path-diagnostic-only`; DB45h accepted only `vggt-residual-job-contract-only`. Together these kill VGGT confidence-only RED promotion and define the future extractor contract, but still accept no physical target-surface geometry evidence, no residual readiness, no source-faithful repair permission, and no RED promotion. Future VGGT residual work must save pose tensors/decoded extrinsics, align VGGT camera centers to the Waymo rig by Sim(3), and compare target-surface samples to LiDAR/raw residuals under a fresh bounded sub-scope. DB25/DB41 remain `RED/abstain`, DB41 lower-right remains zero-LiDAR abstain, DB36/DB40 remain generated fake-geometry rejects.

---

## Current Seam State (2026-05-28)

Read `handoff.md` first, then the top of `progress.md`.

- **Safest visual baseline**: AV2 raw L1 `hard_select`. It removes multiband ghost/halo and avoids near-field OF fragmentation.
- **No-DL / source-faithful seam fixes tested**: calibration BA, fixed-radius sphere, multi-radius selection, sparse stereo displacement, seam-local ECC, DP seam-routing, region-coherent/component repair, semantic object coherence, same-frame ground-plane layer, DiT-as-oracle source selection, and temporal ego-motion ground replacement are all NEG or weak MIXED. They do not beat L1 `hard_select`.
- **Stage A latest**: DP seam-routing v2 moves the hard seam path only, but still cuts visible cars/people/lane structures. Evidence: `deliverables/seam_routing_v2/three_anchor_v1_review/`.
- **Stage B latest**: DiT360 has been pushed through raw masked completion, post/soft/evidence/fidelity compose, multi-seed, adaptive masks, low-frequency residual, residual-multiband, Poisson/gradient-domain gated compose, DiT-as-oracle source selection, and v14 tri-map latent clamp. Safe variants preserve evidence but stay close to hard_select; loose/raw variants look smoother by blurring/rewriting cars, lane lines, pillars, and buildings. It is **NEG as the Bosch training-data solver**, useful only as a qualitative/color-prior baseline. Evidence: `deliverables/dit360_seam_completion/runs_v10_*`, `runs_v11_*`, `runs_v12_residual_multiband/`, `runs_v13_poisson_gate/`, `runs_v14_trimap_clamp_*`, plus `deliverables/dit360_oracle_source/three_anchor_v1/`.
- **Ground/temporal latest**: `scripts/phase3/test_ground_plane_layer.py` uses current-frame raw camera samples on a local road plane; `scripts/phase3/test_temporal_ground_seam.py` uses nearby AV2 frames + ego poses to fill seam-band ground strips. Both are real geometry/evidence routes, but one ground plane creates rectangular pasted/driven strips and drops NCC versus hard_select. Evidence: `deliverables/ground_plane_layer_compact_mid_review.jpg`, Drive `results/ground_plane_layer_v1/`, and `deliverables/temporal_ground_seam/three_anchor_v1/`.
- **Semantic latest**: `scripts/phase3/test_semantic_object_coherent.py` projects YOLOv8x-seg vehicle/person masks into ERP and forces near-seam object support to one source camera. It is weak MIXED / mostly NEG: fbee dY improves slightly, BMW/clean do not, and road/facade/lane seams remain. Evidence: `deliverables/semantic_object_coherent_compact_review.jpg`, Drive `results/semantic_object_coherent_v1/`.
- **Sparse-stereo latest**: `scripts/phase3/score_ghost_yolo_v2.py` selected fbee anchor85 as the most ghost-likely stride-5 anchor, then `run_wide_baseline_stereo.py` + `run_l1_sparse_disp.py` v5 were tested. Still NEG: only 201 final stereo points and almost no overlap-alignment change. Evidence: `deliverables/sparse_stereo_v5_fbee_a085_review.jpg`, Drive `results/sparse_stereo_v5_fbee_a085/`.
- **Depth latest**: `scripts/phase3/depth_visibility_seam_probe.py` uses AV2 LiDAR only as seam visibility/risk metadata. It is POS as diagnostic but weak NEG as repair: LiDAR supports 49.26% of seam band, 28.60% of supported seam is high depth-risk, and depth-veto Y repair is safer but weaker than source-risk-only repair. Evidence: `deliverables/depth_visibility_seam_probe/depth_visibility_three_anchor_compact_review.jpg`, Drive `results/depth_visibility_seam_probe_v1/`.
- **Dense depth latest**: `scripts/phase3/dense_depth_edge_seam_probe.py` uses Depth Anything V2 Small as dense seam-edge metadata only. It is also POS as diagnostic but weak NEG as repair: dense depth risk marks 4.81% of seam band, mostly overlaps RGB structure risk, and dense-depth-veto Y repair is weaker than source-risk-only repair. Evidence: `deliverables/dense_depth_edge_seam_probe/dense_depth_edge_three_anchor_compact_review.jpg`, Drive `results/dense_depth_edge_seam_probe_v1/`.
- **Depth routing latest**: `scripts/phase3/test_depth_aware_seam_routing.py` lets dense DA-V2 risk change the DP seam path. It is NEG: seam dY improves, but source-fidelity NCC collapses from hard_select `0.9925` to depth route `0.8233`, and visual review shows jagged source swaps. Evidence: `deliverables/depth_aware_seam_routing/depth_aware_route_three_anchor_compact_review.jpg`, Drive `results/depth_aware_seam_routing_v1/`.
- **Superpixel latest**: `scripts/phase3/test_superpixel_depth_coherent.py` uses SLIC over RGB+DA-V2 depth and assigns seam-split superpixels to one source camera. It is NEG: larger regions reduce dY but introduce blocky pasted strips; NCC drops from hard_select `0.9925` to `0.9131`. Evidence: `deliverables/superpixel_depth_coherent/superpixel_depth_three_anchor_compact_review.jpg`, Drive `results/superpixel_depth_coherent_v1/`.
- **Parallax-budget latest**: `scripts/phase3/parallax_budget_map.py` uses AV2 LiDAR and adjacent camera centers to compute physical ERP displacement at seam pixels. It is POS as evidence, not repair: LiDAR supports 50.23% of the seam band; supported pixels have p90 parallax `17.65 px`, with `23.92% >= 10 px` and `7.14% >= 20 px`. Evidence: `deliverables/parallax_budget_map/parallax_budget_three_anchor_compact_review.jpg`, Drive `results/parallax_budget_map_v1/`.
- **Recommendation**: keep L1 `hard_select` as the conservative training-data baseline; pair it with seam confidence/risk metadata and optional risk-gated Y-only color polish. Use DiT360/temporal-ground outputs as qualitative baselines or negative evidence, not production stitchers.

Most recent execution plan: `plans/2026-05-27-seam-routing-dit360-goal.md` plus the temporal-ground follow-up recorded at the top of `progress.md`.

---

## 🔒 Rules for new agents (per user 2026-05-27)

These rules exist because the repo had bloated to **47 stale finding/summary mds** that duplicated information already in `progress.md`. User asked to consolidate and enforce going forward.

> **🚫 NO NEW `.md` FILES unless the user explicitly asks (per user 2026-06-02).** Default: capture EVERYTHING in the **4 living docs** (`README` / `handoff` / `progress` / `decision_briefs`) + visual evidence as images under `deliverables/<topic>/`. This applies EVERYWHERE — `agent/`, `deliverables/`, `notes/`. Do NOT spawn brainstorm/plan/summary/finding `.md`s on your own. Only create a standalone `.md` (e.g. a handoff prompt, or a human-facing `deliverables/handoff_to_<name>_<date>.md`) when the user specifically requests it. If you feel the urge to write a new doc, put it in a `progress.md` entry or a `decision_briefs.md` brief instead.

### ✅ DO

| When you... | Do this |
|---|---|
| Finish an experiment | Add a new entry **at the top of `agent/progress.md`** (format: 怎么做 / 结果 / Deliverables / Status / Next) |
| Have visual evidence (PNG/JPG) | Put under `deliverables/<topic>/*.png` — images are NOT bloat, they're evidence |
| Need to hand off to a human (advisor/teammate) | Create `deliverables/handoff_to_<name>_<date>.md` (the one allowed type of new .md in deliverables/) |
| Write paper sections | Put under `paper/` (e.g. `paper/method_draft.md`) |
| Take random research notes | Add to a `progress.md` entry's body, or as a code-module docstring |

### ❌ DON'T

| Don't... | Because... |
|---|---|
| Create `deliverables/*_FINDING.md` / `*_SUMMARY.md` / `*_PIPELINE.md` | Info belongs in `progress.md`; standalone files go stale + bloat |
| Create per-experiment standalone `.md` in `deliverables/` | Same — use a `progress.md` entry |
| Move/rename `handoff.md`, `progress.md`, `README.md` | They're stable entry points; renaming breaks future agents |
| Add new files to `deliverables/archived/` or `notes/archived/` | Those folders are read-only history snapshots |
| Commit user personal files (`self_learning/`, `self reading.md`, `agent/plans/5.26 *`) | They're in `.gitignore` — keep them out |

### Commit hygiene (added 2026-05-27)

- `.gitignore` excludes user personal notes, large data (`.tfrecord`/`.npy`/`.feather`), generated PDFs.
- `.gitattributes` normalizes line endings (`eol=lf` in repo) + declares binaries (PNG/JPG/PDF/tfrecord/npy). No more `LF will be replaced by CRLF` warnings on Windows.
- Direct push to `main` authorized for THIS repo (per `[[feedback-direct-push-main-waymo2pano]]` memory) — no PR review needed.
- Commit messages: imperative subject (≤ 60 chars), then a blank line, then a short body. End with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## 🚦 Experiment Decision Gate (added 2026-06-02)

Before starting any new experiment direction, create or update an entry in `agent/decision_briefs.md`.

Each decision brief must include:
- Question / hypothesis
- Why this is worth testing now
- Expected observable output
- **Kill criteria**
- **Max scope / budget**
- Required vision check

After the experiment finishes:
- Write the detailed result block in `agent/progress.md`
- Update the corresponding decision brief status to `explored`, `accepted`, `rejected`, or `paused`
- Add a one-line result summary and link to the exact `progress.md` block
- **Do not start a follow-up experiment unless it has its own decision brief or explicitly extends the existing one.**

Why this exists: the project's recurring failure mode is NOT lack of ideas — it is patch-on-patch on a direction that "looks promising" until it turns out NEG. The brief is the entry gate that stops that. `decision_briefs.md` and `progress.md` are **strongly bound but never duplicate**: briefs hold the *decision state* (why / hypothesis / kill / scope + a one-line result link); `progress.md` holds the *facts* (commands / params / artifacts / metrics / vision verdict / POS-MIXED-NEG evidence).

---

## 📍 Recording Experiment Artifacts: the 3-Location Rule (added 2026-06-02)

Every completed experiment MUST have its products recorded in **three durable locations** (reproducibility + auditability + cross-session continuity). `progress.md` entries must state, per artifact, WHERE it lives in all three:

1. **GitHub** (committed) — `git@github.com:QiPan-Ronnie/Waymo2Panorama.git`, branch `main`, direct-push authorized. Commit: pipeline code (`code/**`, `scripts/**`), the `progress.md` entry, and visual evidence PNG/JPG under `deliverables/<topic>/` (images ARE evidence, not bloat).
2. **Local** — this Windows dev machine: `D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\` (synced via BaiduSync). The working tree + pulled result figures live here.
3. **Drive** — `MyDrive/koi_waymo2pano_colab/` (panq@usc.edu): raw/large run outputs (`outputs/phase3/`, `results/`), AV2 data (`data/argoverse2/`), cached envs + FLUX/LoRA (`cache/`). The Colab compute reads/writes here.

A `progress.md` result block should therefore name: the GitHub path(s) committed, the local figure path(s), and the Drive `results/...` path for the full/large outputs.

---

## 🔑 Account & Drive access — MIGRATED to 1jingshuo1 (2026-06-03)

The Drive workspace `MyDrive/koi_waymo2pano_colab/` is **OWNED by `panq@usc.edu`** (USC account). As of 2026-06-03 it is **shared (Editor) to `1jingshuo1@gmail.com`** (the user's personal account), and a **shortcut to it was added in 1jingshuo1's "My Drive"** — so the Colab mount path `/content/drive/MyDrive/koi_waymo2pano_colab/` is **identical under both accounts; scripts need NO change.**

- **Operate under `1jingshuo1` going forward**: run Colab logged into 1jingshuo1; the Claude Drive connector is now 1jingshuo1. Verified 2026-06-03 on a 1jingshuo1 CPU Colab — data mounts via the shortcut, **write access OK (Editor)**, repo present at `be82ba7`.
- **Colab connection = the cloudflare tunnel** (`url`+`token` in `runtime/active_url.json`), **independent of the Drive account** — switching the Drive/connector account does NOT affect the Colab tunnel.
- **This is ACCESS/OPERATION migration, NOT ownership** — the data physically still lives in panq's Drive (nothing was copied).
- **⚠️ TODO if panq/USC access is ever lost** (cross-org ownership transfer usc.edu→gmail is BLOCKED, so you must COPY): copy the irreplaceable items into 1jingshuo1's OWN Drive — `results/dfwd_av2_finetune_v1/` (finetuned depth_net/gs_net, ~1.3 h A100, irreplaceable) and `cache/df_env_torch22cu121.tar.zst` (2.6 GB built `df` env). AV2 raw data under `data/argoverse2/` is the PUBLIC dataset → re-downloadable, no need to copy. (`secrets/` — do not touch.)

---

## The 4 source-of-truth files (living docs)

- **`handoff.md`** — current consensus + roadmap; onboarding doc for any agent picking up this work. **Read this first.** Includes documentation rules + recent milestones + infrastructure notes.
- **`progress.md`** — append-only FACTS timeline. Each completed track → a 4-line block (怎么做 / 结果 / Deliverables[GitHub/local/Drive] / Status / Next). Latest entry at top.
- **`decision_briefs.md`** — the experiment GATE (direction decisions: question/hypothesis/kill/scope + one-line result link). See the Decision Gate section above.
- **`README.md`** — this file. The agent dir guide + work protocol. Updated when rules change.

Historical experiment records (every prior exploration path) are preserved under `../notes/archived/` (see its `README.md` index) and `../deliverables/archived/`; the original method landscape is `../notes/archived/2026-05-15-brainstorm-survey.md`. Nothing is deleted — old paths are archived, not lost.

---

## Where everything else lives

| Topic | Location |
|---|---|
| Paper drafts (intro/method/experiments/discussion/related_work/outline) | `../paper/` |
| Historical method-landscape brainstorm (5.15) | `../notes/archived/2026-05-15-brainstorm-survey.md` |
| Old design plans (N1, parallax fix, etc.) | `plans/` (here, historical) |
| Old design specs | `specs/` (here, historical) |
| External-facing handoffs (delivered to advisor/teammate) | `../deliverables/handoff_to_*.md` |
| User-facing learning doc | `../deliverables/learning_plan.md` |
| User's deep self-study | `../self_learning/` (gitignored, except 6 originally-tracked overview mds) |
| Per-route research notes (archived) | `../notes/archived/` |
| Old experiment-finding mds (archived 2026-05-27) | `../deliverables/archived/` |
| Code | `../code/` |
| Run drivers | `../scripts/` |
| Run outputs (Drive primary) | `../outputs/` (mostly gitignored, `agg_*.json` tracked) |

---

## Latest housekeeping (2026-05-27)

- **47 mds archived** (10 deliverables/*_FINDING/*_SUMMARY → `deliverables/archived/`, 37 notes/*.md → `notes/archived/`).
- **6 paper drafts moved** to `paper/`.
- **3 living docs lock** enforced via the rules above.
- **`.gitignore`** updated for user personal notes + commit hygiene.
- **`.gitattributes`** added for line-ending + binary file declarations.

---

## Active project plan

Most recent concrete plan: `plans/2026-05-27-seam-routing-dit360-goal.md`. Its Stage A and Stage B tasks are completed, and the temporal-ground follow-up is documented at the top of `progress.md`.

## Parent project

Sub-project under `koi chen/experiments/Waymo2Panorama/`. Siblings:
- `koi chen/01-pi3/` — Pi3 visual geometry foundation model (we use its outputs)
- `koi chen/04-pantheon360/` — 3D-aware 360° video diffusion (downstream, paused per v6.1 pivot)
- `koi chen/05-argus-video-to-360/` — perspective → 360 video diffusion (blind-spot completion candidate)
- `koi chen/02-vipe/` — ViPE multi-view SLAM (downstream demo for L1 ERP)
