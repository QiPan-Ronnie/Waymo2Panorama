# Experiment Archive — 2026-05-29 — E0 (ERP geometry ruler) + E1 (seam-confined fusion)

**Session**: continuation after the REALITY-CHECK pivot (full-frame single-center 3DGS is globally warped → rejected). New validated direction (2 adversarial sweeps, unanimous):

> **Keep rigid L1 hard_select as the globally-clean geometry backbone (far field byte-identical → cannot warp); fuse ONLY the ~7 near-field overlap seam strips.**

Executed as a 3-rung ladder, each rung isolating ONE variable (ties to the "isolate the input variable first" lesson):
- **E0** = build/validate an objective ERP-geometry RULER (needed for any "de-warped" claim + the paper).
- **E1** = cheapest seam fix (photometric blend in the strips) → isolates the PHOTOMETRIC seam.
- **E1.5** = low-frequency-only blend → photometric fix WITHOUT doubling.
- **E2** (not yet built) = closed-form parallax alignment in the strips → the actual near-field merge.

Maintainer: Qi Pan (panq@usc.edu), advisor Koi Chen. Anchor scene "BMW" = AV2 log `02a00399-3857-444e-8db3-a8f58489c394`, anchor index 0.

---

## 0. Code artifacts created this session (file → purpose)

| File | Purpose |
|---|---|
| `scripts/phase3/erp_geometry_metric.py` | ERP geometry metrics: `gcsr` (great-circle sagitta, no-ref), `vertical_lean` (VLS, no-ref), `relative_warp` (DIS-flow-vs-L1, **the validated ruler**), `vdr` (relative vertical drift) + diagnostic overlays |
| `code/waymo2panorama/blending/seam_confined.py` | **E1/E1.5 blend**: `blend_seam_confined` (hard_select base + multiband confined to ~7 seam strips, far field byte-identical) and `multiband_lowfreq_blend` (E1.5: low-freq-only, single-camera high-freq → no doubling) |
| `code/waymo2panorama/pipeline/stitch_frame.py` | added `blend_mode="hard_seamconfined"` dispatch |
| `scripts/phase3/run_e1_seam_confined.py` | Colab driver: render 7 cams → blend_seam_confined → save L1/E1/multiband/alpha + seam crops. Arg `--lowfreq-cutoff` switches E1↔E1.5 |
| `scripts/_colab.py` | agent-colab-direct HTTP helper (`exec`/`put`/`get` via CF tunnel) |

---

## 1. Experiment → Result table (this is the review index)

| # | Experiment | What it tested | Result | Files (under `deliverables/`) |
|---|---|---|---|---|
| E0-a | Metric-design adversarial **workflow** (`wf_55c3370b-47c`, 13 agents) | propose 6 ERP-geometry metrics → adversarially break each → synthesize | ✅ Produced GCSR/VDR/FWF suite; every absolute line-metric has the **LSD-fragmentation blind spot** (a wavy edge splits into straight chords → warp laundered) | workflow journal `subagents/workflows/wf_55c3370b-47c/`; full result `…/tasks/wk51smmzv.output` |
| E0-b | **GCSR** (great-circle sagitta, whole connected-component) on BMW L1 vs 3DGS | absolute no-ref warp score | ❌ **FAILED / inverted**: L1=106 mrad vs 3DGS=85 mrad. Cause (seen in figure): `connectedComponents` merges many edges + wide skyline sweeps into one 2D blob → great-circle fit meaningless | `erp_geometry_metric/bmw_gcsr_diagnostic.png`, `…/bmw_metrics.json` |
| E0-c | **VLS** (roll-invariant vertical-lean spread) on BMW L1 vs 3DGS | absolute no-ref warp score | ❌ **FAILED / inverted**: L1=5.64° vs 3DGS=4.27°, stable across thresholds. The 3DGS warp is **low-frequency/global**; local short-segment tilt is blind to it, and L1's real non-vertical clutter sets a ~5° floor | `erp_geometry_metric/bmw_vls_diagnostic.png` |
| E0-d | **relative_warp** (cv2 DIS flow vs L1 + cos φ scaling + far-field warp-fraction) on SYNTHETIC warps | does the relative ruler catch LF warp & certify confined edits? | ✅ **VALIDATED**: identity→M_p90=0/frac=0; LF global warp amp 4/8/16px → M_p90 3.99/7.97/15.9 (monotone), frac_warp 0.67→0.93; **confined 40px-strip edit → frac_warp=0.025**. THIS is the E1/E2 ruler | (numbers in this doc + progress.md) |
| E1 | **Seam-confined full multiband** on BMW (Colab A100) | does photometric blend in the strips help? | ⚠️ **Isolation result**: far field **byte-identical** (12.2% changed, all in ~8 seam strips); photometric step removed BUT **near-field DOUBLING** reappears (tree/building/car doubled). Trades L1's hard cut for ghosting | `e1_seam_confined/`: `bmw_L1.png`, `bmw_E1.png`, `bmw_multiband.png`, `bmw_diff_amp3.png`, `bmw_carseam_zoom4x.png`, `bmw_car_L1_E1_MB.png`, `bmw_seamcrops.png`, `bmw_seam_L1vsE1_hires.png`, `bmw_alpha.png` |
| E1.5-c4 | **Low-freq-only** seam blend, cutoff=4 (Colab) | fix colour step without doubling | ⚠️ doubling **reduced but present** — 17px parallax lives in the blended bands (≥16px) | `e1_seam_confined/bmw_E15_lf4.png`, `bmw_carseam_L1_E1_E15.png` |
| E1.5-c5 | **Low-freq-only** seam blend, cutoff=5 (Colab) | blend only the coarsest band (global colour) | ✅ **Best cheap baseline**: near-field **clean (no gross doubling)**, hard tonal step softened, far field byte-identical. mean\|Δ\|@changed: E1=19.2 vs E1.5-c5=12.9 (more conservative). **BUT geometric offset (parallax cut) remains** | `e1_seam_confined/bmw_E15_lf5.png`, **`bmw_carseam_4way.png`** (decisive: L1\|E1\|E1.5-c4\|E1.5-c5) |

---

## 2. Conclusions (what this session settles)

1. **E0 — the ruler**: absolute LOCAL ERP metrics (vertical-lean, per-segment great-circle) **do NOT separate** clean-L1 from warped-3DGS, because the warp is **low-frequency/global** and L1 has a ~5° local-edge floor. *Vision caught every failed metric — never trusted a number unseen.* The working ruler is **`relative_warp`** (dense flow vs L1): it catches LF warp and reads a confined seam edit as ~0 far-field warp — exactly what's needed to judge E1/E2 (whose outputs are aligned to L1 by construction).
2. **E1/E1.5 — the seam fix**: the far-field byte-identity guarantee **holds in practice** (only ~12% of pixels, all in the ~8 seam strips, ever change). AV2 ring cams are exposure-matched, so the **photometric seam is minor**; E1.5-cut5 removes it cleanly with no doubling (a good **"L1+" baseline**). The DOMINANT near-field seam artifact is **PARALLAX (geometric offset)** — a photometric blend (E1) only converts L1's "hard cut" into "doubling", and a low-freq blend (E1.5) only colour-smooths across the cut without aligning it.
3. **→ E2 is the real contribution**: to actually MERGE near-field offset content you must **align** the non-selected camera's pixels into the selected camera's rays (rig's known H∞ + epipole + LiDAR z, closed-form) BEFORE blending. This is the clean E1→E2 isolation the ladder was built to produce.

---

## 3. Infrastructure (how to re-run on Colab)

- **Connect**: agent-colab-direct (CF quick-tunnel + Flask). Heartbeat `active_url.json` (URL + bearer token) is provided by the user per session (tunnel URL churns). Helper: `scripts/_colab.py` with env `COLAB_URL`, `COLAB_TOKEN`. Endpoints: `POST /exec {cmd:[argv],cwd,timeout_s}`, `GET /jobs/<id>` (log_tail 4096B), `POST /write {path,content,base64}`, `GET /read?path=&base64=true`.
- **Windows gotcha**: export `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'` or Git-Bash mangles `/content/...` argv → `C:/Program Files/Git/content/...`.
- **Colab repo**: `/content/waymo2panorama` (code at `/content/waymo2panorama/code`). Deps all present in base py3.12 (cv2 4.13, numpy 2.0.2, pandas, PIL, scipy, pyarrow, av2, torch). E1 is CPU-bound (~5.5s/anchor; no GPU needed).
- **AV2 data (5 val logs staged on Drive)**: `/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/{02a00399…, 0bae3b5e…, 2c652f9e…, 9f871fb4…, fbee355f…}`. Anchor index via `loader.anchor_timestamps_ns()`; named anchors: BMW=`02a00399` a0, fbee=`fbee355f` a95, 0bae=`0bae3b5e` a30.
- **Re-run E1.5 BMW**: `python3 scripts/phase3/run_e1_seam_confined.py --log-dir <BMW log> --anchor 0 --tag bmw_lf5 --output-dir /content/e1_out --erp-h 1024 --erp-w 2048 --lowfreq-cutoff 5` (cwd `/content/waymo2panorama`), then `get` the PNGs.

---

## 4. Open items / Next

- **E2 (next build)**: in each of the ~7 strips, reproject the non-selected camera's near-field pixels into the selected camera's ERP rays via rig's KNOWN H∞ + epipole + per-pixel LiDAR z (closed-form, NOT estimated; forced 0 outside strips), then blend → aligned content, no doubling. Judge: `relative_warp` far-field frac_warp ~0 (untouched) + near-seam doubling gone (vision).
- **De-risk first**: overlap-only check that strip-interior LiDAR z is dense/accurate enough at 1.5m / 21-26cm baseline (if z is wrong, doubling degrades to localized blur — fall back to E1.5).
- **Generalize**: run E1.5 + E2 on fbee/0bae too; quantify with `relative_warp` + the line-straightness story for the paper (clean-vs-fused Pareto frontier).
- **Residual risk**: non-planar near OBJECT straddling a seam (car/pole/pedestrian) has no clean homography → fall back to L1/E1.5 for that strip.

---

## 5. E2 (closed-form depth-reprojection) — BUILT + RAN, depth-accuracy-bound [added 2026-05-29 later]

Code: `code/waymo2panorama/blending/seam_confined.py` (`confined_depth_rmap`, `blend_seam_e2_depth`), `code/waymo2panorama/depth/dense_lidar_depth.py` (LiDAR-anchored DA-V2 dense depth), driver `scripts/phase3/run_e2_seam_depth.py`, de-risk `scripts/phase3/e2_lidar_coverage_probe.py`. Mechanism: feed per-pixel ego-range as N1 `convergence_distance_m` (closed-form "known H∞+epipole+z") so overlapping cams reproject to the ego centre and AGREE → blend merges. Confined to strips; far field byte-identical.

| # | Experiment | Depth source | Result | Files (`deliverables/e2_seam_depth/`) |
|---|---|---|---|---|
| E2-pre | LiDAR-coverage de-risk probe | sparse 64-beam | strip 38% supported (near/mid covered; far=sky correctly uncovered) → green-lit | `bmw_e2probe.png`, `bmw_e2probe_carzoom.png` |
| E2-v1 | full depth-align | sparse LiDAR+kNN | ⚠️ road/dense-near aligns BUT mid-range building SMEARS (blocky densified depth). mean\|Δ\|=48.3 | `bmw_E2.png`, `bmw_carseam_E2_4way.png` |
| E2-v2 | near<12m only + low-freq | sparse LiDAR+kNN | ⚠️ building smear reduced, residual streak remains. mean\|Δ\|=27.5 | `bmw_E2_v2.png`, `bmw_carseam_E2v1_v2.png` |
| E2-dense | full depth-align | DA-V2 + per-cam affine→LiDAR | ❌ OVER-WARPS (building swept into a curve); mono-depth not metric-accurate enough per-pixel. mean\|Δ\|=118.3 | `bmw_E2_dense.png`, `bmw_carseam_dense_4way.png` |

**E2 conclusion**: the seam doubling lives on MID-RANGE (10-30m) surfaces — enough parallax to double, but no available depth (sparse LiDAR OR affine-aligned mono) is accurate enough there for clean N1 reprojection (ERP shift ~ baseline·focal·Δ(1/z) → ~2x depth error = tens of px wrong shift). **Closed-form depth-reprojection is depth-accuracy-bound.** Robust deliverable remains **E1.5-cut5**. Next real option = LEARNED multi-view band plane-sweep/MPI (disparity from cross-view photo-consistency, not a depth prior) OR ship E1.5 + the depth-bound Pareto-frontier paper story.
