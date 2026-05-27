# Phase 2 D1 — Foundation-model backbone decision (Pi3 vs DVGT)

Date: 2026-05-19
Status: Design doc, awaiting user approval before execution.
Owner: Track A (main).

## The decision

Phase 2 (the 3D-aware stitching main line) needs a feed-forward foundation model that
takes an image and returns a dense per-pixel 3D point map. plan v2 §5 D1 wired this
as "Pi3 default, DVGT head-to-head on Phase 2 Day 1-2; switch only if DVGT clearly wins".

This document specifies how the head-to-head will be run.

## Why we need it

L1 is parallax-naïve (treats every camera as if at ego origin). The dominant 2025
research paradigm for parallax-correct stitching (LiftProj 2512.24276, PIS3R 2508.04236)
is: lift each image into 3D via a foundation model, fuse the point clouds in a unified
frame, then re-project onto an ERP sphere. That gives parallax-correct outputs and
removes ghosting and exposure-driven seams.

Two candidate backbones:

| Backbone | Pre-existing | Driving-tuned | Metric scale | Code | Inputs we know it accepts |
|---|---|---|---|---|---|
| **Pi3X** | We already run it (`01-pi3/`) | No (general) | No — scale-free | Yes | 504×504, 7 views OK |
| **DVGT** | New, Dec 2025 | **Yes** (Waymo/nuScenes/KITTI/DDAD) | **Yes** — metric | Yes ([wzzheng/DVGT](https://github.com/wzzheng/DVGT)) | 2–8 cams dynamic |

## The experiment

We pick **one AV2 frame** (from the spike log `02a00399-…`, anchor t = front_center
first timestamp) and run both backbones on the 7 ring cams. Then we measure.

### Inputs (frozen)

| Item | Value |
|---|---|
| AV2 log | `02a00399-3857-444e-8db3-a8f58489c394` (val) |
| Anchor timestamp | front_center index 0 |
| Cameras | 7 ring (drop 2 stereo at L3 too) |
| Image size | 504×504 (Pi3 native; resize/center-crop AV2's portrait/landscape) |

### Metrics we'll compare

| # | Metric | How |
|---|---|---|
| 1 | **Point cloud density** | Count valid (conf > τ) points per view |
| 2 | **Scale plausibility** | For each view: median point depth in metres. DVGT should be ≈ camera-mount-height ± vehicle length; Pi3 is scale-free, expect numbers ~unit-normalized |
| 3 | **Cross-view consistency** | Pick a feature visible in two adjacent cams (e.g. road yellow line). Compute the 3D point each cam predicts for it. Distance between the two = cross-view error |
| 4 | **Confidence map usefulness** | Plot confidence histograms. Are there clear "trust" / "don't trust" regions, or is everything saturated to one tail? |
| 5 | **GPU memory** | Peak `torch.cuda.max_memory_allocated()` per 7-view pass |
| 6 | **Wall-clock latency** | Time per 7-view forward pass on Colab A100 |
| 7 | **Edge-case behavior** | Inspect outputs on (a) sky regions, (b) reflective car surfaces, (c) the ego vehicle hood — does the model fail loudly or silently? |

### Tie-breaker rules

- If DVGT wins clearly on 5+ of 7 metrics: switch backbone to DVGT.
- If Pi3 wins or ties on 4+ metrics: keep Pi3 (lower risk, we already operate it).
- If a clear failure mode appears on one model only: hard rule that disqualifies it.

## Concrete script plan

Two paired scripts, runnable on Colab A100 via the W2P-003 submit/poll pattern.

### `scripts/phase2/run_pi3_one_frame.py`
```
inputs : --log-dir, --anchor-idx (default 0)
outputs: outputs/phase2/pi3_one_frame/
           per-view: points_{cam}.npy, conf_{cam}.npy, intrinsic_recovered_{cam}.npy
           per-frame: pose_{cam}.npy (Pi3X-recovered cam-to-world)
           summary.json (density, depth stats, gpu mem, latency)
```
Wraps the existing `01-pi3/scripts/run_pi3x_export.py` but inputs are AV2 ring cams
(resized to 504×504), not Pantheon360 crops.

### `scripts/phase2/run_dvgt_one_frame.py`
```
inputs : --log-dir, --anchor-idx (default 0), --dvgt-checkpoint
outputs: outputs/phase2/dvgt_one_frame/
           per-view: points_{cam}.npy, depth_{cam}.npy, conf_{cam}.npy
           per-frame: ego_pose_{cam}.npy (DVGT predicts ego trajectory)
           summary.json (same fields)
```
Clones DVGT into `code/external/DVGT/` and uses their pre-trained checkpoint. Need
to find which is the recommended checkpoint for AV2-style data.

### `scripts/phase2/compare_pi3_vs_dvgt.py`
```
inputs : outputs/phase2/pi3_one_frame/, outputs/phase2/dvgt_one_frame/
outputs: outputs/phase2/d1_comparison.md (the report) + per-metric plots
```
Reads both `summary.json` and computes the 7 metrics. Writes a markdown report with
recommendation.

## Open questions / risks

1. **DVGT may need PyTorch >2.5** that conflicts with Pi3's `torch==2.5.1+cu124`.
   Test in a fresh Colab session before committing.
2. **DVGT may not accept 504×504** input. Their paper trained on what sizes? Read
   their README carefully.
3. **Scale calibration**: Pi3 outputs are scale-free. For metric 3 (cross-view error),
   we'd need to align Pi3's scale to DVGT's (or AV2 LiDAR's) before comparing. Sim(3)
   alignment via shared LiDAR ground truth.
4. **AV2 ring cams are different sizes** (front_center portrait, others landscape).
   Pi3 takes square input; we'll center-crop or letterbox. DVGT may want different
   handling. Document the choice.
5. **GPU memory at 7 cams**: A100 (40GB) should fit both. But if either fails OOM at
   504×504×7 views, tile-and-aggregate fallback.

## Time budget

| Step | Estimated time |
|---|---|
| Write `run_pi3_one_frame.py` | 1 h (adapt existing Pi3 entry) |
| Write `run_dvgt_one_frame.py` | 3 h (new integration, may need DVGT repo fiddling) |
| Run both on 1 AV2 frame (Colab A100) | 30 min for both |
| Write `compare_pi3_vs_dvgt.py` + run | 1 h |
| Review numbers + write decision | 30 min |
| **Total** | **~6 h focused work, spread over Day 1-2 of Phase 2** |

## Next step

Once user approves this design, the agent will:

1. Clone DVGT into `code/external/DVGT/` (no modification — pristine for upgradeability)
2. Write `run_pi3_one_frame.py` (likely a thin wrapper)
3. Write `run_dvgt_one_frame.py` (real integration work)
4. Submit both as W2P-003 jobs on Colab A100 runtime
5. Write the comparison script + run
6. Produce `notes/backbone_decision.md` with the verdict
7. Tag the verdict as `v0.2-d1-resolved` before continuing into Phase 2 main line

If results are close, escalate to user for the final call.
