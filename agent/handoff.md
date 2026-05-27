# Waymo2Panorama — Agent Handoff

**Updated**: 2026-05-27 (Latest: seam-first local ECC alignment implemented and tested on 3 AV2 anchors; MIXED / weak NEG. Safer than full-image OF but not better than L1 hard_select. Prior: doc consolidation; seam-root-cause investigation; Xihan Waymo E2ED L1+L2 HDR color shift solved; hard_hdr_of NCC +25.3% but visually risky on near-field large objects.)
**Maintainer**: rotating Claude sessions; user is Qi Pan (panq@usc.edu), advisor Koi Chen

---

## 🎯 LATEST FINDING (2026-05-27) — read this FIRST

**5 layers of "no-depth / no-DL" seam fixes tested. Seam-local alignment is safer than full OF, but still does not beat L1 hard_select. This reinforces the current framing: without explicit depth or object/label coherence, perfect AV ring-camera panorama is impossible.**

| # | Method | Result |
|---|---|---|
| 1 | L0 — AV2 calibration BA refine | ❌ NEG. Bias ~1.3 px (negligible vs 46 ERP px parallax) |
| 2 | L1 — Single fixed sphere R={∞,30,10,5,3} m | ❌ Trade-off, no R fits all depths (Δ = baseline/R - baseline/D) |
| 3 | L1 — Multi-R per-pixel v1 (Y-diff argmin) | ❌ Frankenstein doubling at object boundaries (pedestrian) |
| 4 | L1 — Multi-R per-pixel v2 (HDR + 9×9 NCC + 11px median R) | ❌ Marginally better than v1, still doubled, worse than L1 hard_select |
| 5 | L2 — Seam-first local ECC alignment on hard_select | ⚠️ MIXED / weak NEG. No BMW fragmentation, but no clear visual improvement over hard_select |

**Fundamental diagnosis**: at object/background boundary, foreground (5m) wants R=5m, background (30m) wants R=30m. Per-pixel argmin switches rapidly even with smoothing → cam_A's R=5m slab + cam_B's R=30m slab composited = Frankenstein. **Criterion right, execution can't enforce object-level coherence.**

**Current safest visual baseline**: L1 `hard_select` on AV2 raw. It removes multiband ghost/halo and avoids near-field OF fragmentation. `hard_hdr_of.py` remains an important ablation (+25.3% NCC, -37.7% seam-gap ΔY mean across 5 logs), but do **not** call it unconditional production default after the user visually rejected OF on the BMW near-field case. HDR and OF/local-align should be optional ablations.

**3 paths from here**:
- **Seam routing / label coherence** — keep hard selection, but optimize where the seam passes; this is the most promising no-DL 2D path.
- **Paper pivot to "impossibility framing"** — use calibration / fixed-R / multi-R / local-align NEG evidence to argue no-depth panorama has a ceiling, then propose artifact minimization and confidence maps.
- **Ship hard_select-first baseline** — deliver AV2 raw L1 hard_select (+ optional Y-only HDR where color shift matters) as the conservative Bosch-facing baseline.

**Full details**: `agent/progress.md` top entries + `deliverables/seam_local_align/three_anchor_v1/`.

---

## 📋 Documentation Rules (user 2026-05-27)

**Living docs (3 only)**: `agent/handoff.md` (this file) + `agent/progress.md` + `agent/README.md`. Write to these, not new mds.

✅ **DO**:
- Experiment results → new entry at top of `progress.md`
- Visual evidence → `deliverables/<topic>/*.png` (PNGs OK, they're not bloat)
- Formal handoff to humans → `deliverables/handoff_to_<name>_<date>.md`
- Paper drafts → `paper/`

❌ **DON'T**:
- Don't create `deliverables/*_FINDING.md` / `*_SUMMARY.md` / `*_PIPELINE.md` (use `progress.md`)
- Don't create per-experiment standalone .md in `deliverables/` (use `progress.md`)
- Don't add files to `deliverables/archived/` or `notes/archived/` — those are read-only history

---

## Recent milestones (5-27 catch-up, read first)

In reverse chronological order — the 8-route TL;DR below is the broader project state from 5-26.

### 2026-05-27 ~23:00 — L1+L2 HDR runs on Xihan's REAL Waymo frame, **color shift solved**
- End-to-end on Colab T4: user accepted Waymo Open Dataset EULA (`panq@usc.edu`) → `gcloud auth login` → `gsutil cp` shard 0 of `gs://waymo_open_dataset_end_to_end_camera_v_1_0_0` to Drive (1.7 GB, 94 s) → parse `end_to_end_driving_data_pb2.E2EDFrame` → 8 cam (K, T_ego_cam, distortion) extracted → run our pipeline.
- Frame `8e737334b520fdd0c04e36f463b2d211-085` (the one Xihan handed us) verified.
- **Output**: `deliverables/xihan/l1_on_waymo/{README.md, compare_4way_thumb.png, l1_hdr_multiband_1024x512.png}` — visually shows our L1 sphere + 8-cam L2 HDR + multiband produces uniform brightness, no over-exposed center cam.
- **2 critical bugs fixed (Waymo-only, no AV2 code path touched)**:
  1. Waymo cam frame (x=fwd, y=left, z=up) ≠ OpenCV convention (x=right, y=down, z=fwd) → added `R_WAYMOCAM_OPENCVCAM` rotation to `T_ego_cam` before sphere_projection.
  2. `hard_hdr_of.py:32-41` `RING_PAIRS` hardcoded for 7-cam AV2 (indices 0..6, no index 7) → on 8-cam Waymo, cam[7] unconstrained → garbage gain. Inline `compute_hdr_gains_waymo8` in runner with 8-cam ring pairs.
- L3 OF chain still 7-cam-bound (ValueError on 8 cams) — color shift already solved by L1+L2; L3 is parallax not color, port to follow.
- New scripts: `scripts/phase3/{parse_waymo_e2ed_frame, run_waymo_e2ed_l1, compare_xihan_vs_l1, compare_waymo_4way}.py`.

### 2026-05-27 ~22:30 — **Seam root-cause investigation conclusion: 4 "no-depth" layers all NEG, empirical impossibility evidence**
Tested 4 layers of fix without explicit depth:
1. ❌ **L0 Calibration refine (BA)** — AV2 bias ~1.3 px global. Negligible vs 46 ERP px parallax.
2. ❌ **L1 Single fixed R** — R={∞,30,10,5,3} all trade-offs, no winner (fbee a95 行人 在 R=5/3 反而更糟).
3. ❌ **L1 Multi-R per-pixel v1** (Y diff argmin) — Frankenstein doubling at object boundaries.
4. ❌ **L1 Multi-R per-pixel v2** (HDR + 9×9 NCC + 11px median R) — marginally better than v1 but still doubled, still worse than L1 hard_select.

**Fundamental diagnosis**: at object/background boundary, foreground (5m) wants R=5m, background (30m) wants R=30m. Per-pixel argmin switches rapidly, even with smoothing → cam_A's R=5m slab + cam_B's R=30m slab composited = Frankenstein. **Criterion is right, execution can't enforce object-level coherence.** Real fix needs MRF/graphcut on R label map OR SAM segmentation-aware OR bilateral filter on R map OR go back to explicit depth (user excluded NeRF/3DGS).

**Empirically confirms what brainstorm derived analytically**: 7-cam different optical centers ⇒ no `2D surface` projection fits all depths exactly. Any "no-depth basic-CV" method has ceiling = L1 hard_select + L2 HDR + L3 OF (current ship, +25.3% NCC). Going beyond needs MRF/segmentation/explicit-depth.

**Code shipped (clean, don't pollute AV2 main)**:
- `code/waymo2panorama/blending/multi_radius_select.py` — both v1 (per-pixel argmin) + v2 (HDR+NCC+medR)
- `scripts/phase3/calibration_check.py` — RANSAC + data F vs calib F comparison
- `scripts/phase3/test_multi_radius_sphere.py` — single-R sweep
- `scripts/phase3/test_multi_r_select.py` — v1 driver
- `scripts/phase3/test_multi_r_select_v2.py` — v2 driver

**Visual evidence (all on `main`)**:
- `deliverables/multi_radius_test/bmw_crop_stack_small.jpg` — 5-row R sweep
- `deliverables/multi_radius_test/02a00399_a000_bmw_crop_hires_q88.jpg` — hi-res sweep
- `deliverables/multi_r_select/fbee355f_a095_bmw_crop_q85.jpg` — v1 NEG evidence (pedestrian doubled)
- `deliverables/multi_r_select_v2/fbee355f_a095_v2_bmw_crop_q85.jpg` — v2 NEG (still doubled)

**Quantitative**: `outputs/calibration_check/{02a00399,0bae3b5e,fbee355f}_v2.json` on Drive.

**Full historical writeup**: `deliverables/archived/CALIBRATION_CHECK_FINDING.md` (CALIBRATION_CHECK_FINDING was moved to archived/ during 2026-05-27 doc consolidation).

**3 paths from here** (user-decided):
- (B') **Substantial fix**: MRF graphcut / SAM / bilateral filter on R map (half day to 1 day)
- (C) **Paper pivot to "impossibility framing"**: write paper around mathematical impossibility + artifact-minimization framework (2-3 hr drafting)
- (D) **Ship as-is**: L1+L2+L3 `hard_hdr_of` already +25.3% NCC, -37.7% seam-gap. Done deliverable for Bosch.

### 2026-05-27 ~17:30 — Xihan handoff: L1 principle + 2 examples + Waymo brighten
- `deliverables/l1_sphere_principle.md` (8 sections) — full L1 baseline math + Waymo porting caveats.
- `deliverables/xihan/l1_examples_panel.png` — 2 representative L1 outputs (clean far-field + ghost near-field).
- `scripts/phase3/brighten_xihan_waymo_panorama.py` — post-hoc joint global Y-gain on Xihan's pre-stitched panorama. **Seam |ΔY| 40.86 → 33.36 = -18%**. CLAHE baseline +14% worse (CLAHE doesn't know cam boundaries).
- `deliverables/handoff_to_xihan_2026-05-27_brighten_and_l1.md` — full 7-section handoff.

---

## TL;DR

Sub-project of the Koi paper line. **Goal (per 5.22 meeting with teammate + Bosch)**: produce **clean 360° ERP panorama dataset** that **Bosch's autonomous-driving world model** can consume — they tested panorama input and it works, but real panorama datasets are scarce/expensive to collect, so we synthesize from existing AV ring-cam logs (AV2 ours, Waymo teammate's). Target venue: **3DV 2026** (main or D&B), advisor Koi Chen.

**Current state (2026-05-26 evening)**:
- **8 stitching routes done + benchmarked** on AV2 raw inputs (L1 sphere `cycle-PSNR 12.34 dB` baseline + L3 Pi3 NEG + 新-A through 新-E). Final Koi deliverable shipped: `deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.{md,pdf}` (13 pages, 11 figures).
- **Stage 2 (T1-T5, 5-25 → 5-26)**: WS1 ship (HDR-Waymo adapter, ego mask, cos⁴ feather, Waymo loader skeleton) ✓. T4 Option B reweight + T5 L1+ORB hybrid all NEG for documented reasons.
- **WS4 D1-D6 (5-26)**: A2 sparse-stereo displacement + B1 disparity-aware graphcut seam shipped + tested. Visual NEG: halos persist. Initially I thought halos were parallax artifacts in L1 baseline.
- **WS4-Diag2 (5-26 evening, KEY FINDING)**: ran decisive A/B experiment — same L1 sphere code, same anchor 60, only varied input (AV2 raw 2048×1550 vs pi3-cache 504×504 letterbox). **AV2 raw output is clean; pi3-cache output has halos**. The "halos" everyone (including the 5.22 prompt) was complaining about were **pi3-cache input degradation** (lanczos resize + letterbox boundary Gibbs ringing leaks into multiband low-freq bands), not stitching pipeline bugs. **7 prior NEG attempts (T4 v1/v2/v3 + T5 v1/v2/v3 + WS4 A2/B1) were misdiagnosed** — they tried to fix the symptom on the wrong root cause. Evidence in `deliverables/parallax_visual_review/smoking_gun_input_is_root_cause.png` (3-row stack, 2048×3170). **Pi3 dependency is no longer needed** for the panorama pipeline.
- **Real artifacts still to address (from 5.22 prompt)**: (a) **2-wheel ghost** in `l1_erp.png` (AV2 raw) — this is REAL parallax artifact in the proper baseline, not the same as the misdiagnosed halo; (b) cylinder white seam traces — likely also pi3-cache rendered, needs re-render with AV2 raw to confirm; (c) **teammate's Waymo color-shift issue** — WS1.1 HDR adapter shipped, ready for teammate to test.
- **新-F VGGT** (4th backbone NEG) — blocked: gated HF repo, user click pending.
- **T13 self-sup Pi3 finetune** — deferred. Probably **no longer relevant** since we don't need Pi3.
- **Paper angle**: original candidate A' Method paper. Now stronger story emerges: "L1 sphere + cos² feather + simple WA on AV2 raw is a clean baseline; 7 'fix-the-halo' attempts (T4/T5/WS4) all failed because the halo wasn't there in the proper pipeline — it was a pi3-cache input degradation artifact. This isolates the parallax + multiband interaction question and ablates 7 candidate fixes."
- **Infrastructure**: agent-colab-direct v0.1.0 active. Works via HTTP curl + Bearer token from Drive's `active_url.json` even when MCP server not registered. 2-hour Stage 2 session ran 3 Colab GPU jobs + 50+ exec calls + 30+ file ops without issue.

**What the next agent should do**:
- **First step always**: read top 3 entries of `agent/progress.md` (WS4-Diag2 root cause + WS4-D6 NEG + earlier stage 2). Then `deliverables/parallax_visual_review/smoking_gun_input_is_root_cause.png` for the visual evidence.
- **If user wants to address real 5.22 prompt items (recommended)**:
  - 2-wheel ghost in AV2 raw L1: parallax-fixing direction needs **depth-aware** approach. Options: (a) re-extract stereo at AV2 raw full-res (not pi3-cache downsampled) and retry WS4 A2/B1 (they may work on dense full-res data); (b) Pi3 forward splat using **AV2 raw input** instead of letterboxed 504×504 (un-letterbox before Pi3, then use depth — Pi3 forward splat NEG result from 5-21 may be partially attributable to letterbox too); (c) different paradigm (4D Gaussian, neural radiance).
  - Cylinder white seam traces: re-render `route_cylinder_vs_sphere.png` with AV2 raw to verify whether traces are pi3-cache artifacts or geometric (cos² fast decay at v_max=±45°). If geometric, the cos⁴ feather fix already shipped (WS1.3) should help — re-render with that on AV2 raw to verify.
  - Teammate Waymo color-shift: HDR adapter is at `code/waymo2panorama/color/hdr_waymo_adapter.py`. Ship to teammate.
- **If user wants paper writeup**: the new framing (7 NEG → root cause = wrong input) is a sharper ablation story. Start with `agent/progress.md` 5-26 evening entry, weave in pre-existing 8-route benchmarks.
- **If user wants to run a Colab task**: notebook + HTTP-direct via `agent-colab-direct`. MCP server not registered in some sessions — fallback to raw HTTP curl with token from `MyDrive/.../runtime/active_url.json` works fine.

**What the next agent should NOT do**:
- **Don't continue adding "halo fix" attempts on pi3-cache output** — root cause is pi3-cache input itself.
- **Don't pursue WS4 D8 (C1 RAFT), D9 (hybrid), D10** as previously planned — those plans assumed halo was pipeline issue.
- **Don't keep Pi3 as the L1 input source** — Pi3 was useful as a depth backbone for L3, but pi3-cache letterbox images degrade L1 output. Use AV2 raw via `AV2RingLoader.load_synced_frame()`.

---

## Start here (new agent onboarding, ~30 min read)

In this order:

1. **`agent/progress.md`** — single source of truth, every track's "怎么做 / 结果 / Deliverables / Status / Next" block. Top of file is the latest entry. Read top 5-10 blocks for current state.
2. **`deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.md`** — what we told the advisor. Has the 8-route summary table (§1), per-route details (§1.1-§1.8), downstream demos (§2), external baselines (§3), pending GPU routes (§4), final ranking table (§5).
3. **`C:\Users\14294\.claude\plans\snug-shimmying-wave.md`** — full plan v6.1 with strategic context (why we pivoted from system integration to stitching-only), 16 routes table, sequencing, decision gates G1-G5, risk register, contracts for each subagent.
4. **`agent/2026-05-15-brainstorm-survey.md`** — original brainstorm survey (CV concepts, dataset comparison, related work). Reference; concepts still apply.

---

## Project chain context (Koi Chen paper line)

```
AV2 / Waymo / nuScenes multi-cam (THIS PROJECT)
        │
        ▼ stitch 7-cam → 360° ERP
ERP panoramic video + 3D cache
        │
        ▼ (downstream, paused per v6.1 pivot)
04-pantheon360  (3D-aware 360° video diffusion, CVPR 2026)
        │
        ▼
360 world simulation / Cosmos-Predict / Argus
```

We control the **first step only**. Downstream (Pantheon360 / GEN3C / Argus / Cosmos) is paused per v6.1 strategic pivot — user has paper deadlines on stitching methodology, downstream is post-publication work.

---

## 8 stitching routes (current snapshot)

| ID | Name | Verdict | Headline metric | Code |
|---|---|---|---|---|
| **L1** | Sphere baseline + 5-band Laplacian | ✅ Strong baseline | cycle-PSNR **12.34 ± 1.31 dB** (10 anchors) | `code/waymo2panorama/projection/sphere.py` |
| **L3** | Pi3 forward-splat | ❌ Structural NEG (paper Section 4) | -3.15 dB vs L1, 10/10 lose | `code/waymo2panorama/pipeline/lift_and_project.py` |
| **IPM** (T14) | Ground plane hybrid | ⚠️ Marginal +0.05 dB | +0.20 dB on ground-only mask | `code/waymo2panorama/projection/ipm_ground.py` |
| **新-A** | Cylindrical L2 | ⚠️ Coverage gain only | +24.9 pp coverage, cycle ~flat | `code/waymo2panorama/projection/cylinder.py` |
| **新-B** | Graph-cut seam | ✅ Visual win | -12.4% seam-band \|grad\| (4/4 anchors) | `code/waymo2panorama/blending/graphcut_seam.py` |
| **新-C** | IPM multi-region (ground+sky+building) | ✅ Method win | **+0.20 dB on ground** (4× T14), building branch deferred | `code/waymo2panorama/projection/ipm_multi_region.py` |
| **新-D** | Wide-baseline stereo (邻 cam) | ⚠️ Partial (5/7 pairs) | 44 inlier 3D pts/pair median, sparse | `code/waymo2panorama/stereo/wide_baseline_stereo.py` |
| **新-E** | HDR cross-cam compensation | ✅ Drop-in preprocess | **-18% lum gap** (4 anchors mean) | `code/waymo2panorama/color/hdr_gain_estimate.py` |

Plus **3 external NEG**: OmniStitch (-6.67 dB), Apple Depth Pro (2.84× worse abs_rel), Temporal Pi3 (no improvement).
Plus **3 downstream demos** (paused): ViPE SLAM, GEN3C, Panacea+ (modality NEG).

---

## Open decisions (G3 v6 gate, awaits user/Koi)

| Decision | Default if no input | Trigger to flip |
|---|---|---|
| **Paper angle**: A' Method / B-with-C / C Negative-only | A' Method (3 positives stackable) | Koi reviews PDF + says "not enough positives, go B" |
| **Run 新-F VGGT** (4th backbone NEG) | Skip (gated repo blocker + low marginal value) | User requests HF access + says "do it" |
| **Run T13 Pi3 self-sup finetune** (5-6 day A100) | Skip (high cost, paper angle A' doesn't need it) | Koi says "need at least 1 backbone-level win" |
| **Target venue**: 3DV 2026 main / D&B / CVPR workshop | 3DV 2026 main (angle A' fits) | Reviewer feedback on draft v0 |

---

## Currently in-flight

- **Colab runtime**: not currently running. The old agent-colab-queue worker scripts have been deleted from this repo. Next Colab task: open `notebooks/runtime.ipynb`, Run All, get `✓ READY` + tunnel URL. The notebook lives in this repo and is reproducible.
- **New 新-F VGGT attempt**: still gated on HF access (`facebook/VGGT-1B-Commercial`). The old `jobs/phase3-new-f-vggt-*.json` specs are now historical artifacts (worker no longer pulls them). When user gets HF access, re-attempt via the new framework: `mcp__colab-direct__exec(cmd=["bash", "scripts/<new-f-script>.sh"])` instead of submitting a job spec.
- **T13 Pi3 self-sup finetune**: still deferred pending Koi feedback. Same migration story — when greenlit, run via new framework.
- **Paper draft v0**: not started. Gated on Koi 拍板 about paper angle.

To unblock 新-F: user clicks "Agree and access" at https://huggingface.co/facebook/VGGT-1B-Commercial. After that, the next agent reframes the work as a `colab-direct` task (no jobs/ json needed).

---

## Video deliverables (added 2026-05-23, paper supplementary)

7 of 8 routes have 5-second mp4 videos at anchor 60 area (start_sec=3.0, 100 frames @ 20 fps, 1024×2048 ERP). 新-D wide-baseline stereo is NOT videoable (outputs sparse 3D points, not dense ERP).

All on Drive at `MyDrive/koi_waymo2pano_colab/outputs/<route>_video/<log_id>/<route>_video.mp4`:

| Route | Drive folder | Size | Wall time | Direct view URL |
|---|---|---|---|---|
| L1 baseline (HDR `--also-baseline`) | `hdr_video/.../baseline_video.mp4` | 17 MB | (parallel with HDR) | `13jNNJCV8FjMGMUbqo03I47ZMTTTJBpro` |
| L3 Pi3 forward-splat | `l3_video/.../l3_video.mp4` | 24 MB | 7 min | `1PZEvwFoCeQUc0oatymgYL7cw0XyF-AcL` |
| T14 IPM ground hybrid | `ipm_hybrid_video/.../ipm_hybrid_video.mp4` | 13 MB | 7.7 min | `1ozuDgzl4g-Anxg1qHJTq8m6liQrSDkn4` |
| 新-A 柱面 (L2) | `cylindrical_video/.../cylindrical_video.mp4` | 26 MB | 5.7 min | `1YvkYTW2dEHrBkH0wKTmxl2s9UoZwIs1z` |
| 新-B Graph-cut seam | `graphcut_video/.../graphcut_video.mp4` | 17 MB | 16 min | `1aA9iw8RTLFTOXFwGYFYAwBFFIvHbwa2s` |
| 新-C IPM multi-region | `ipm_multi_region_video/.../ipm_multi_region_video.mp4` | 13 MB | 12 min | `1O5dAAq6MASxUtFyebuzrPN3fK6FTbLoX` |
| 新-E HDR cross-cam | `hdr_video/.../hdr_video.mp4` | 15 MB | 16 min | `1Ln-BV6zU_FwQ7yzdY2_e9Y0X3V74-cUA` |

**Video driver scripts** (all at `scripts/run_*_video.py`, follow same pattern as `run_l1_baseline.py`):
- `run_l3_video.py` — Pi3 → Sim(3) → forward-splat per frame
- `run_cylindrical_video.py` — sphere → cylinder, multiband blend
- `run_graphcut_video.py` — L1 + apply_graphcut_seams (Boykov-Kolmogorov min-cut)
- `run_hdr_video.py` — L1 + per-cam 6-param gain+bias (Huber LS); `--also-baseline` flag writes parallel L1 mp4
- `run_ipm_hybrid_video.py` — Pi3 + detect_ground_from_pi3 + ipm_project_ground + sphere fallback
- `run_ipm_multi_region_video.py` — Pi3 + ipm_project_multi_region (ground + sky routing; building branch off by default)

**To generate more videos** (different anchor / log / duration): copy a `run_*_video.py` script, edit args (or just pass CLI flags), write a new `jobs/phase3-*-video-*.json` spec. Worker picks up automatically. Pattern documented in `jobs/README.md`.

---

## Infrastructure (must-know)

### agent-colab-direct (current — use this for ALL new Colab work)

- Framework repo: <https://github.com/QiPan-Ronnie/agent-colab-direct> v0.1.0, public, MIT
- Entry: `notebooks/runtime.ipynb` (generated; regenerate via `colab-direct generate-notebook` if config drifts)
- Setup cell does: pip install agent-colab-direct → mount Drive → clone this repo → start Flask executor + Cloudflare quick-tunnel + heartbeat (writes URL+token to `<workspace>/runtime/active_url.json` every 5s)
- Agent invokes MCP tools: `mcp__colab-direct__exec(cmd=[...])`, `__shell`, `__write_file`, `__read_file`, `__status`, `__list_jobs`, `__get_job`, `__wait_for_job`, `__kill_job`, `__shell_reset`, `__heartbeat`, `__refresh_url` (12 tools total)
- `exec` is auto sync↔async: short cmds return inline with `{mode: "sync", stdout, exit_code}`; long cmds return `{mode: "async", job_id}` and the agent can `wait_for_job(id)` later
- `shell` is a persistent bash session (pexpect): `cd`/`export`/`source venv/bin/activate` stick across calls (SSH-like UX)
- For mid-task resume on long batch jobs: decorate functions with `@cd.checkpointed(unit_id_fn=..., storage_dir=drive_path)`. On disconnect+resume, completed units skip via `.done` marker files
- See `[[agent-colab-direct-framework]]` memory for details
- MCP server registration: `colab-direct mcp` + `COLAB_DIRECT_RUNTIME_DIR=<local Drive>/koi_waymo2pano_colab/runtime` — example `.mcp.json` snippet in `agent-colab-direct/docs/migration_from_acq.md`

### agent-colab-queue (FROZEN — do NOT submit new jobs)

- Pip package stays installed locally (`uvx --from .../tools/agent-colab-queue`) — kept ONLY for reading old `jobs/*.json` archive
- **Do NOT call `mcp__agent-colab-queue__submit_job`** for new work
- Old `jobs/*.json` (86 files) preserved in this repo as audit archive — they document what we tried on the old infra
- Old worker scripts (`scripts/cell_acq_worker.py`, `cell_worker_bootstrap.py`, `runtime_filter.py`, `code/waymo2panorama/utils/drive_queue.py`) deleted 2026-05-24
- See `[[agent-colab-queue-framework]]` memory (marked frozen)

### Drive workspace
- Root: `MyDrive/koi_waymo2pano_colab/` (panq@usc.edu)
- Cached fileIds in `[[drive-folder-ids-koi-waymo2pano]]` memory
- Subdirs: `outputs/phase3/` (all run results), `data/argoverse2/` (4 logs, ~32 GB), `cache/` (tar'd conda envs for fast restore), `worker/heartbeat.json`, `results/<job_id>.json`

### GitHub
- Repo: `git@github.com:QiPan-Ronnie/Waymo2Panorama.git`
- Direct push to main is **authorized** (per memory `[[feedback-direct-push-main-waymo2pano]]`) — no PR review needed for this repo
- Worker pulls from main on every poll

### Code layout
```
code/waymo2panorama/
  data_io/         AV2 ring loader (RING_CAMS_7, calib parsing)
  projection/      sphere / cylinder / ipm_ground / ipm_multi_region
  blending/        multiband (Laplacian pyramid) + graphcut_seam
  stereo/          wide_baseline_stereo
  color/           hdr_gain_estimate
  alignment/       sim3_align (Umeyama)
  pipeline/        lift_and_project (L3 forward-splat) + depth_bayesian_fusion
scripts/
  phase2/          single-anchor evals (eval_pi3_vs_lidar, eval_cycle_consistency)
  phase3/          multi-anchor batch evals + each new route driver + run_pi3_multi_anchor + run_vggt_multi_anchor
  phase4/          (future) T13 finetune scripts will go here
deliverables/
  handoff_to_koi_w2_2026-05-21_v6cpu_done.{md,pdf}    THE Koi handoff
  _make_*.py / _render_pdf*.py                         figure / PDF tooling
  images/route_*.png                                   actual figures used
  learning_plan.md                                     user's own CV roadmap
agent/
  progress.md          single source of truth, append-only log
  handoff.md           this file
notes/
  new_{a,b,c,d,e,f}_*.md  per-route research/design docs
  t13_*.md                T13 finetune design doc
jobs/*.json              Colab queue specs (worker pulls these)
outputs/phase3/*         all run results (mostly mirrored to Drive)
```

---

## Defensive lessons (HARD-WON — read before any Colab job)

These cost us hours/dollars. Don't repeat:

1. **`set -e -o pipefail` + `cmd | head -N` = death**. `head` closes stdin after N lines, sends SIGPIPE upstream, pipefail kills the script. Use `tail -N` instead. Bit us on T11 GEN3C install (exit 141, 5 sec into install).
2. **Conda activate hates `set -u`**. `activate-gcc_linux-64.sh` references unbound `SYS_SYSROOT`. Wrap `conda activate` in `set +u`/`set -u`. Bit us on T11 v1.
3. **HF gated repos need explicit access click**. `facebook/VGGT-1B-Commercial` 403'd on us even with HF token — must click "Agree and access" on the HF model page. Bit us on 新-F.
4. **Worker sorts jobs alphabetically, NOT by created_at**. If you submit `bbb-install` then `aaa-eval`, eval runs first. Name jobs with prefix order in mind.
5. **Tar conda env to Drive after heavy installs**. Saves ~50 min on Colab reconnects. See `[[feedback-colab-tar-env-to-drive]]` for the zstd-tar restore pattern.
6. **No way to remotely shut down Colab runtime**. Worker is a Python process inside Colab; we can stop submitting jobs but A100 stays alive until user manually disconnects. Tell user explicitly when failing.
7. **Pi3 vs VGGT input pipeline difference**: Pi3 takes raw (B, V, 3, H, W) tensor; VGGT takes file paths via `load_and_preprocess_images()`. The driver `run_vggt_multi_anchor.py` saves letterbox images to a tmp dir then feeds paths to VGGT.
8. **`eval_cycle_consistency.py` applies Sim(3) alignment via `pose_<cam>.npy`**. For Pi3 this converts world→ego. For VGGT/Depth Pro we use AV2 truth extrinsics so save `pose_<cam>.npy = T_ego_cam` to make Sim(3) collapse to identity.
9. **Pi3 repo path on fresh Colab session is `/content/Pi3`, NOT `/content/01-pi3-Pi3`**. Use the 3-URL clone fallback: `git clone yyfan2014/Pi3 || yyfz/Pi3 || yyfan2014/Pi3-clean`. Bit us on L3 video v1 + v2 (wasted 3 min total). Pass `--pi3-repo /content/Pi3` explicitly in job spec.
10. **API signature drift between similar modules**: `detect_ground_from_pi3()` does NOT take `conf` kwarg even though `segment_regions_from_pi3()` does. Don't copy-paste kwarg patterns between modules — always `grep -n "^def "` the target module first. Bit us on T14 video v1 (crashed first frame).
11. **Path resolution from `scripts/` vs `scripts/phase3/`**: scripts at `scripts/` level need `here / "../code"` (1 level up) and `here / "../../01-pi3/..."` (2 levels up). Scripts at `scripts/phase3/` need `"../../code"` and `"../../../01-pi3/..."`. Don't blindly copy the path constants between locations. Bit us on L3 video v1.
12. **Python `print()` is block-buffered when piped via `tee`** (default 4-8 KB buffer). Long Pi3 model loads can take 30-60s with NO output visible in `tail -f run.log`. Use `print(..., flush=True)` or `PYTHONUNBUFFERED=1` env var for real-time progress on long-running scripts.
13. **Worker idle ≠ A100 free**. Worker Python process can be alive + polling but A100 runtime is still allocated and billed (~$3-4/h). Always tell user to disconnect Colab runtime when work finishes, even if worker is "just idle".
14. **Drive API metadata cache delay**: `modifiedTime` from `search_files` can be 30-60s stale. If checking worker liveness, do 2-3 reads spaced ~30s apart before declaring worker dead.
15. **Colab FUSE write vs Drive backend sync** (NEW, 2026-05-24 smoke test): a file written to `/content/drive/...` from Colab is **instantly visible inside the Colab kernel** (FUSE mount), but propagation to Google's actual Drive backend can lag **minutes**. During agent-colab-direct smoke test, `active_url.json` was confirmed written by the executor's heartbeat thread, but Drive MCP `search_files` returned empty for >2 minutes. Don't tight-loop on Drive search after a Colab write; either read the file via Colab's left file panel (FUSE, instant), open a separate Colab notebook tab to cat it, or just wait 60-90s. Captured in memory `[[feedback-drive-colab-sync-delay]]`.
16. **agent-colab-direct daily-use validation pending** (2026-05-24): Smoke test passed end-to-end on Colab CPU, but the first real research task using the new framework hasn't happened yet. Expect rough edges — when you (next agent) hit one, take notes for v0.1.1 polish. Don't silently work around bugs; flag them to the user.

---

## Memory references (Claude session memory)

Located in `C:\Users\14294\.claude\projects\D--BaiduSyncdisk-2024-to-future-koi-chen\memory\` (auto-loaded into every new session — agent reads `MEMORY.md` index then pulls relevant entries):
- `[[agent-colab-direct-framework]]` — **active** framework: 12 MCP tools, 6 optimizations, smoke-tested 2026-05-23
- `[[agent-colab-queue-framework]]` — **FROZEN** as of 2026-05-23; do NOT submit new jobs through it
- `[[drive-folder-ids-koi-waymo2pano]]` — cached Drive fileIds (root, results, heartbeat); still valid
- `[[feedback-direct-push-main-waymo2pano]]` — user authorizes direct push to main for THIS repo
- `[[feedback-colab-tar-env-to-drive]]` — zstd-tar pattern for heavy installs (Apex/TE etc.); now used inside agent-colab-direct's `cache.py`
- `[[feedback-prefer-robust-frameworks]]` — engineer fixes over workarounds (drove the agent-colab-direct refactor)
- `[[feedback-drive-colab-sync-delay]]` — **NEW 2026-05-24** Colab FUSE write ≠ Drive backend sync; don't tight-loop on Drive search

---

## Tooling

- Python 3.10/3.12 (Colab compat)
- AV2 API: `pip install av2`
- Pi3: `../../../01-pi3/code/official/Pi3` (local clone) + HF `yyfz233/Pi3X` (open)
- VGGT: `git clone https://github.com/facebookresearch/vggt` + HF `facebook/VGGT-1B-Commercial` (GATED)
- Pytorch: bf16 native on A100, fp32 fallback
- HF auth: token at `C:\Users\14294\.huggingface\token` (local only, NEVER send to Colab/agent)
- GitHub SSH key: `C:\Users\14294\.ssh\id_ed25519_github_new` (local only, NEVER send to Colab/agent)
- Compute: Colab Pro A100 (user's account, panq@usc.edu)
