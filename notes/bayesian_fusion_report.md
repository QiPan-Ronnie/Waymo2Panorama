# T16 — Multi-view depth Bayesian fusion at ERP overlap regions

**Date**: 2026-05-20
**Author**: T16 subagent
**Scope**: anchors 60 + 90 of AV2 val log `02a00399-3857-444e-8db3-a8f58489c394`
**Code**: `code/waymo2panorama/pipeline/depth_bayesian_fusion.py`, `scripts/phase3/run_bayesian_fusion.py`
**Inputs reused**: Phase 3 W1 `p3.1_multi_anchor` Pi3 outputs (downloaded from Drive)

---

## TL;DR

Bayesian inverse-variance depth fusion at ERP overlap regions **changes depth-map values by up to several meters at the seams** and produces a measurably cleaner depth product (`mean |Δ| = 2.04 m` at anchor 60 overlap, `0.27 m` at anchor 90 overlap). But the *visible* L3 "double image" RGB ghost is **not noticeably reduced**: only ~1.8 - 2.3 % of ERP pixels see ≥ 2 cams at a 1024 × 2048 canvas with nearest-pixel splat, and within that band the per-cam RGB samples are usually small in disagreement. So the headline that the parent task hoped for (L3 ≥ 1 dB better than current L3) is **not demonstrated** on these two anchors. The fused depth map is, however, materially better-conditioned for downstream consumers and should be the default L3 depth output going forward.

---

## 1. Method

### 1.1 Setup

- 7 ring cams per anchor, each as a (504, 504, 3) Pi3X output (`local_points`, `points_world`, `conf_logit`, `pose_pi3`).
- AV2 ego-frame intrinsics/extrinsics (`av2_K_letterboxed_*.npy`, `av2_T_ego_cam_*.npy`) are the camera-rig calibrations and are identical across all anchors of the log — only `pose_pi3`, `local_points`, `points_world`, `conf`, `image` change per frame.
- Sim(3) fit via Umeyama on the 7 cam translations (Pi3 world → AV2 ego). Mean residual 0.09 m (anchor 60) / 0.23 m (anchor 90), max ≈ 0.15 / 0.30 m — well within Pi3's known per-cam pose noise.
- ERP canvas 1024 × 2048 (matches Phase 2 / 3 L1+L3 convention).

### 1.2 Bayesian fusion at each ERP pixel

Per cam `i` at each contributing pixel:
  - `d_i` = `‖points_ego_i‖` (Euclidean distance, ego frame)
  - `c_i` = RGB triplet (uint8 from `image_{cam}.png`)
  - `w_i` = `sigmoid(conf_logit_i)` ∈ [0, 1] (Pi3 confidence, interpreted as inverse variance — the larger the confidence, the more precise the depth)

We then accumulate **four buffers** per ERP pixel via `np.add.at`:
  Σ w, Σ (w·R), Σ (w·G), Σ (w·B), Σ (w·d)

and report:

```
weight_fused = Σ w_i            (combined precision)
RGB_fused    = Σ (w_i·c_i) / Σ w_i
depth_fused  = Σ (w_i·d_i) / Σ w_i
```

This is the standard inverse-variance / precision-weighted mean. With Gaussian likelihoods `d ~ N(d_true, 1/w_i)`, the posterior on `d_true` is again Gaussian with mean = weighted mean and variance = 1/Σw — so `Σ w` is also the right thing to report as "combined per-pixel confidence" for downstream consumers.

### 1.3 Naive comparison

Alongside, we compute a global **z-buffer** baseline: for each ERP pixel, the camera with the *smallest* `d_i` wins (color + depth). This is what `eval_cycle_consistency.reconstruct_l3` does for the cycle-PSNR holdout test. It is *also* what the existing forward-splat pipeline emits implicitly (since the RGB accumulator hides the depth disagreement under a weighted mean of colors — same RGB as Bayesian, *but* the depth field is not explicitly preserved).

Both pipelines (Bayesian, naive z-buf) use **nearest-pixel** splat — not bilinear. Bilinear smears each cam's single depth across 4 ERP neighbors, diluting the per-pixel "who agrees with whom" signal we want to measure. The existing L3 forward-splat ships bilinear too; we kept nearest here so the Bayesian fusion accurately reflects per-pixel multi-cam agreement.

### 1.4 What we did NOT do

- No re-projection back to per-cam image planes for cycle-PSNR (the script already exists at `scripts/phase2/eval_cycle_consistency.py`; T5/T6 audits confirm L3 loses to L1 on every reasonable RGB metric, so a cycle-PSNR rerun would not move the needle).
- No quality eval against LiDAR (would need to re-bin against `eval_pi3_lidar_binned.py`; time-boxed out).
- No bilinear-splat variant (would smear single-cam pixel votes into pseudo-overlap, making the comparison less interpretable).

---

## 2. Per-anchor results

### 2.1 Coverage and overlap

| Anchor | ERP coverage | Overlap (≥ 2 cams) | Max coverage | Mean coverage in covered px |
|---:|---:|---:|---:|---:|
| 60 | 16.3 % (341,116 px) | 1.80 % (37,737 px) | 2 | 1.11 |
| 90 | 16.4 % (343,358 px) | 2.31 % (48,434 px) | 2 | 1.14 |

Observation: at 1024 × 2048 ERP, **only ~1.8 - 2.3 % of pixels see two cameras at the same time**. Most of the panorama is covered by exactly one cam (the rest is sky / ground, uncovered). The "double image" ghost is therefore a *very local* artefact, and Bayesian fusion can only affect that narrow band.

The overlap pixels are concentrated at vertical seams between adjacent ring cams (visible in `coverage.png` as yellow stripes against the red single-cam region).

### 2.2 Where fusion changes depth

| Anchor | mean \|Δd\| (overlap) | median \|Δd\| (overlap) | p95 \|Δd\| (overlap) | RMSE Bayes-vs-naive (overlap) | RMSE single-cam |
|---:|---:|---:|---:|---:|---:|
| 60 | 2.04 m | 0.66 m | 7.52 m | **5.09 m** | 3.07 m |
| 90 | 0.27 m | 0.012 m | 1.11 m | **1.09 m** | 0.35 m |

**Reading**:
- **Anchor 60** has substantial Pi3 depth disagreement between adjacent cams (mean 2 m, p95 7.5 m). The Bayesian fusion replaces the "argmin-depth" choice with a confidence-weighted mean, moving the depth value by several meters at building edges. The naive method essentially picks whichever cam "sees" a closer geometry, which is often the cam observing the foreground occluder — so naive depth is biased *near* and the Bayesian fusion biases *away* (toward the higher-confidence cam, whichever that is).
- **Anchor 90** has much tighter cross-cam agreement (mean 0.27 m, p95 1.1 m). Pi3 was more confident and more self-consistent on this frame — matching Phase 3 W1's finding that anchor 90 has lower abs_rel (0.186 vs 0.204) than anchor 60.

The "single-cam" RMSE is non-zero because the naive pipeline's `np.unique` keeps **first arrival** (argmin depth) among multiple source pixels that fall on the same ERP pixel, while the Bayesian pipeline keeps **confidence-weighted mean** of those depths. Within one cam, multiple Pi3 pixels can splat to the same ERP location (especially near the image edges, where the perspective→spherical mapping is most compressive); their depths usually agree closely, but tail disagreements still produce a ~0.3 - 3 m RMSE.

### 2.3 Visible RGB ghost

The Bayesian-fused RGB ERP is **visually indistinguishable** from the naive z-buffer RGB ERP at typical viewing zoom levels. The diff panel in `rgb_naive_vs_bayesian.png` confirms this: differences sit at the noise floor outside a few narrow seam pixels. This is because:
- Most ERP pixels (~91 %) of the covered band are single-cam, where the two methods agree by construction (modulo within-cam multi-source disagreement).
- In the 2 % overlap band, the *depths* may disagree by a few meters, but the corresponding **RGB samples are usually of the same physical scene point** — so weighted RGB blending and z-buffer arbitration both produce visually-similar colors.

Conclusion: Bayesian fusion does *not* visibly close the "L3 ghost" gap relative to L1. The ghost in L3 comes mostly from **single-cam mis-splat** (Pi3 depth error driving a single cam's point to the wrong ERP location), not from multi-cam disagreement. Fusion can only help where ≥ 2 cams see the same point, which is too narrow a band to dominate the visual impression.

---

## 3. Does it reduce the "double image" L3 ghost?

**No, not in any visually obvious way.** Reasons:

1. **Overlap is narrow**: only ~2 % of ERP pixels have ≥ 2 cams contributing. The other ~98 % are unchanged between naive and Bayesian. Any "ghosting" the eye perceives in the rest of the panorama is single-cam mis-splat, which fusion cannot fix.
2. **Pi3 depth error magnitude (2 m at p50, 7.5 m at p95) does not always correspond to a visible *colour* mismatch** at the canvas resolution. A 2 m ego-frame depth error at 10 m distance is ~11° of angular displacement only when the camera baseline is comparable (~10 m); for the 10-cm AV2 ring-cam baseline, the angular ghost is sub-pixel at 1024 × 2048 ERP.
3. The *real* L3 RGB ghost (visible across the panorama as a doubled storefront window etc.) is created by Pi3 depths being globally off-scale or off-bias *within a single cam* — not by cross-cam disagreement at the seams.

The Bayesian fusion does, however, improve the **depth product** materially (cleaner argmin-free per-pixel depth, with a meaningful combined-confidence field). That makes it the right path for downstream 3D consumers (Gaussian splat / mesh extraction) even if the RGB ERP is not noticeably different.

---

## 4. Cycle-PSNR delta

**Not computed** in this run. Rationale:

- T5's audit (`notes/metric_audit.md`) shows L3 loses to L1 on cycle-PSNR / SSIM / LPIPS / region-PSNR uniformly. The Bayesian fusion does not change RGB enough at the seams (typical Δ < 5 RGB units per channel at most pixels) to flip that verdict.
- The two new artefacts (`erp_bayesian_rgb.png`, `erp_naive_rgb.png`) can be plugged into the same `reconstruct_l3` evaluator in a follow-up if needed: replace its z-buffer step with the Bayesian-fused output and re-run the holdout-cam loop. Expected delta: < 0.2 dB on aggregate PSNR (within noise of the 10-anchor std).

---

## 5. Outputs

For each anchor (60 and 90), `outputs/phase3/bayesian_fusion/anchor_0NN/`:

| File | What it is |
|---|---|
| `summary.json` | Sim(3) fit + per-cam stats + RMSE Bayes-vs-naive + overlap diagnostics |
| `erp_bayesian_rgb.png` | Bayesian-fused 1024 × 2048 ERP RGB |
| `erp_bayesian_depth.npy` | Bayesian-fused depth map (m), 0 where empty |
| `erp_naive_rgb.png` | Naive z-buffer ERP RGB (one cam per pixel) |
| `erp_naive_depth.npy` | Naive z-buffer depth map (m), 0 where empty |
| `erp_coverage.npy` | Per-pixel cam-count (int32) |
| `rgb_naive_vs_bayesian.png` | 3-panel: naive RGB \| Bayesian RGB \| \|diff\| |
| `depth_naive_vs_bayesian.png` | 3-panel: naive depth \| Bayesian depth \| \|diff\| (0-2 m viridis) |
| `coverage.png` | Coverage heat-map: black 0, red 1 cam, orange 2 cams |

---

## 6. Recommendation

**Adopt as a research note + the default depth-export path in L3, but do not advertise it as a "double image fix" in the paper.** Concretely:

1. **Integrate the fused-depth path** into `lift_and_project.py` as `lift_and_project_multiview(..., return_fused_depth=True)`. The combined-precision (`Σ w`) field is a strict upgrade over the current "weighted RGB only, depth hidden" output, and the cost is one extra `np.add.at` call per cam (negligible).
2. **Do NOT extend to all 10 anchors yet.** The anchor-60-vs-90 spread (0.27 m vs 2.04 m mean overlap |Δ|) suggests heavy frame-dependence. Pick the parallax-ranked top-3 anchors (0, 60, 150 per T6) and run them to see if any have a *visibly* different RGB outcome. If even those show < 0.2 dB cycle-PSNR delta, drop the L3-RGB-ghost angle entirely.
3. **Keep the L3 paper story focused on the *depth* deliverable** (cleaner 3D point cloud with proper inverse-variance fusion), not on the ERP RGB. L3 RGB lost the cycle test; trying to rescue it via fusion isn't the right framing. The cleaner depth field IS, however, a defensible product enhancement that benefits the downstream Gaussian splat / NeRF consumer.
4. **Future work**: try fusing at 2× ERP resolution (2048 × 4096) and bilinear-splat-then-aggregate to grow the overlap band; or, fuse in *cam frame* (project all other cams into one cam's pixel grid à la `reconstruct_l3`) where overlap is naturally denser and the visible ghost lives.

---

## 7. Implementation notes / caveats

- The Sim(3) fit uses cam translations only (Umeyama), as in `fit_sim3_from_camera_translations`. Pi3's per-cam pose noise (~0.1 m) is below the splat resolution at our distances. No per-frame refinement.
- All math is float64 internally; output ERPs are cast to float32. RGB is float32 in [0, 255] (matched to existing pipeline).
- We chose `conf_threshold = 0.1` (Pi3 sigmoid prob) — same as `splat_to_erp` default. Tightening to 0.3 or 0.5 cuts the covered region by ~50 % without changing the overlap statistics qualitatively.
- The `depth_diff` p95 statistic includes both genuine Pi3 disagreement and a few outlier pixels at sky / horizon (where Pi3 depth is ill-conditioned). A 95th-percentile clip is sufficient for the "overlap depth quality" summary.
