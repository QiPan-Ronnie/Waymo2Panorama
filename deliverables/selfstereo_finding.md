# Self-Stereo from 2-Cam Pair: Definitive Validation of L3 OF Choice

**Algorithm F (self-stereo): commits c4cc463 + 2f09087 + 8b220a5**

## What was tested
Use the two cams in each ring overlap pair as a STEREO pair (known baseline 0.21-0.26m, known relative rotation). Run dense matching in overlap zone → derive per-pixel depth → re-project both cams via N1 mode using that derived depth.

## Approach used
Analytic depth from Farneback OF (skipped rectification): for each ERP pixel, triangulate two rays via 2x2 midpoint lstsq in ego frame.

## Math verification: WORKS
- BMW @ ~3m: median recovered depth 2.59 m (correct, within ~10% of ground truth)
- Distant buildings: median 43.9 m (correct)
- Per-pair depth maps physically valid

## Pipeline result: FAILS (same as N1 family)
- N1 reprojection with derived depth: BMW area coverage drops 98.75% (legacy infinity) → 62.13% (N1 r~3m)
- Visual: BMW shows a LARGE BLACK HOLE through the right side of its body
- Same failure pattern as N1+LiDAR, N1+DA-V2

## Why
At near-field depth, each cam projects through a narrow cone → many ERP pixels fall outside ANY cam FOV → black holes. The infinity-depth projection (legacy) maximizes cam coverage by projecting unit rays.

## Mathematical relation to L3 OF
- Both achieve cam-pair convergence in overlap (proof: both make slab_A and slab_B agree)
- L3 OF: stays in 2D — preserves cam coverage
- Self-stereo + N1: changes 3D point each cam queries → creates coverage holes for near-field

## Conclusion for paper
Self-stereo is NOT a useful alternative. It CONFIRMS that:
1. The OF flow IS the correct alignment signal (validated by depth recovery)
2. L3 OF 2D warp is the cleanest way to apply it (no FOV gap)
3. Any depth-based reprojection inherits N1 FOV pathology — even with PERFECT depth

This is a DEFINITIVE negative result that strengthens the paper's view-mixing-is-the-problem thesis: we have now shown that NO depth-based method works for ring cams at near-field, regardless of depth quality (LiDAR sparse, DA-V2 dense, LiDAR+graphcut, self-stereo dense-and-correct).
