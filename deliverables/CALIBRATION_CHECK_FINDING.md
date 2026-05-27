# Calibration Check — AV2 ring camera extrinsics/intrinsics bias measurement

**Date**: 2026-05-27
**Question**: Is the panorama seam-misalignment problem rooted in AV2 calibration bias (Level 0 "原理性" fix candidate via bundle adjustment), or purely in parallax (Level 1+ fix needed)?

**TL;DR**: AV2 calibration is **good but not perfect** — global median bias **~1.3 px in camera space** (≈ 0.5-1 px in ERP space). This is **mild bias**, not "real bias" that would justify a BA refinement effort. Parallax (40-46 px on near-field objects in ERP space) **dominates calibration error by ~30-40x**. **Direction A (BA refine) is essentially dead** for solving the seam misalignment problem.

## Method

For each of the 7 adjacent ring-camera pairs (CCW order), on 5 anchors of each of 3 val logs:

1. SIFT keypoint detection + Lowe-ratio match (ratio 0.75)
2. `cv2.findFundamentalMat(RANSAC, 3 px)` to filter SIFT mismatches → "data-driven F"
3. Compute **calibration-implied F** from AV2 extrinsics+intrinsics:
   $$F_\text{calib} = K_B^{-T} \cdot [t]_\times R \cdot K_A^{-1}$$
   where $(R, t) = T_{ego,B}^{-1} \cdot T_{ego,A}$.
4. On RANSAC inliers, compute Sampson distance to both:
   - `sampson(F_data)` ≈ SIFT localization noise floor (0.2-0.4 px)
   - `sampson(F_calib)` = SIFT noise + calibration bias
5. **Calibration bias** = `sampson(F_calib) − sampson(F_data)` (depth-independent)

## Results

### Global (median across pairs × anchors)

| Log | scene | data F sampson | calib F sampson | **bias (px)** |
|---|---|---|---|---|
| 02a00399 | quiet residential | 0.26 | 1.55 | **+1.28** |
| 0bae3b5e | busy urban | 0.22 | 1.32 | **+1.10** |
| fbee355f | parking garage | 0.29 | 1.68 | **+1.39** |

**Consistent ~1.1-1.4 px calibration bias across very different scenes.**

### Per-pair breakdown

| Pair | 02a00399 | 0bae3b5e | fbee355f | comment |
|---|---|---|---|---|
| front_center → front_left | **+0.69** | **+0.10** | **+0.48** | Excellent (front cams well-calibrated) |
| front_left → side_left | +2.71 | +0.55 | +0.52 | Variable; one log shows real bias |
| side_left → rear_left | +2.20 | +2.38 | +135 (n=3) | Side-rear consistently 2+ px; fbee outlier is SIFT failure (24% inlier rate) |
| rear_left → rear_right | +1.29 | +1.42 | +1.91 | Rear pair: mild bias |
| rear_right → side_right | +0.62 | +1.24 | +1.67 | Side-rear right: mild bias |
| side_right → front_right | n=3 sparse | +0.56 | +0.32 | Excellent when SIFT works |
| front_right → front_center | **+1.05** | **+0.51** | **+0.74** | Excellent |

**Pattern**: front-cam pairs are sub-pixel calibrated. Side-cam pairs have 1-3 px bias. This is consistent with manufacturer calibration drift on the side-mounted cameras (presumably calibrated less precisely or shifted slightly over vehicle lifetime).

## Decision

**Bias < 2 px globally → BA refinement is in the "marginal" zone.**

Translating to ERP space (cam 2048 px ≈ 70° HFOV ≈ 796 ERP px at 4096 width):
- 1 cam px ≈ 0.4 ERP px
- Worst calibration bias 3 cam px ≈ 1.2 ERP px
- **Parallax of 3m object: ~46 ERP px**

Calibration error contributes **at most 1-2 ERP px** of seam misalignment. The visible "接缝" we see (10-50 ERP px) is **dominated by parallax**, not calibration.

→ **Direction A (BA refine extrinsics) would improve seams by ≤1-2 ERP px** — negligible compared to the parallax-driven misalignment.

→ **Move to Direction B (geometry / multi-sphere implicit depth)** as the principled fix.

## Caveats / Honest Limitations

1. **5 anchors per log** — sufficient for noise robustness on calibration (which is constant per log) but could be expanded to 20+ for tighter confidence intervals.
2. **SIFT noise floor (~0.2 px)** is comparable to the bias — for tightest measurement we'd want a sub-pixel feature like LightGlue or a synthetic test.
3. **The 136 px outlier on fbee355f side_left→rear_left** is a SIFT failure (24% inlier rate, only 3 anchors), not a real calibration anomaly. Should be excluded from any pair-level conclusion.
4. **Side cam intrinsics K** may also have small distortion residual — AV2 says imagery is undistorted but quality of undistortion varies. Sampson distance captures both extrinsic and intrinsic error.

## Artifacts

- Script: `scripts/phase3/calibration_check.py` (RANSAC v2)
- JSON: `outputs/calibration_check/{02a00399,0bae3b5e,fbee355f}_v2.json` (on Drive: `koi_waymo2pano_colab/outputs/calibration_check/`)
- PNG: `outputs/calibration_check/{log}_v2_summary.png` (per-pair boxplots)
- Commit: `1ec738e` (v2 RANSAC script)
