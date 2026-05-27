# Section 4: Experiments (Draft)

## 4.1 Dataset

We evaluate on the Argoverse 2 sensor dataset [Wilson et al., NeurIPS 2021], specifically the validation split. Each log contains ~10 seconds of 7-ring-camera footage at 20 Hz, synchronized with LiDAR. We select 5 logs spanning diverse driving scenes:

| Log ID | Scene type | # anchors |
|---|---|---|
| 02a00399 | Quiet residential street, parked cars | 319 |
| 0bae3b5e | Busy urban downtown, traffic | 64 |
| 2c652f9e | Multi-lane intersection | 64 |
| 9f871fb4 | Highway with overpass | 64 |
| fbee355f | Parking garage, low light | 64 |
| **Total** | | **575** |

Anchor timestamps are synchronized to the LiDAR (10 Hz). Output panorama resolution: $2048 \times 4096$ (1:2 aspect ratio per ERP convention).

## 4.2 Baselines

**Multiband** [Burt & Adelson 1983]: Cosine-squared feather weights + 5-level Laplacian pyramid blend with horizontal wrap padding for ERP. Current SOTA for traditional ring-camera stitching; produces the doubled-feature ghost we aim to eliminate.

**N1 (depth-aware) family** — 4 variants, all FAIL (documented as negative ablation):

| Variant | Depth source | Result |
|---|---|---|
| N1 single-r | Single convergence radius $r \in \{2, 3, 5, 10\}$m | Single r can't cover both BMW (3m) and sky ($\infty$); residual ghost at non-r distances |
| N1 + LiDAR | AV2 LiDAR splat to ERP + kNN fill | LiDAR sparse on smooth surfaces (car bodies); kNN propagates wrong depth |
| N1 + LiDAR + graphcut | Add Dijkstra seam routing on depth-corrected ERP | Cost function couldn't find clean path through doubled-content overlap |
| N1 + Depth Anything V2 | Monocular CNN depth (HuggingFace) | Single-view depth has view-synthesis residual same as no depth |

All 4 N1 variants share the failure mode: trying to fix a **view synthesis** problem (different angles of same object) with depth/geometry. Documented in `deliverables/N1_AUTONOMOUS_RUN_SUMMARY.md`.

## 4.3 Quantitative results

### Seam luminance gap (lower is better)

We measure mean $|\Delta Y|$ at seam pixels — defined as ERP pixels where the argmax-of-cos²-weight changes between adjacent columns. This isolates the cross-camera brightness mismatch.

**Method**: For each seam pixel $(r, c)$ where $\arg\max_i w_i(r, c) \neq \arg\max_i w_i(r, c+1)$, compute $|Y_{i^*(r,c)}(r, c) - Y_{i^*(r,c+1)}(r, c+1)|$ in YCrCb. Average over all seam pixels per anchor, then over anchors.

**Results on log 02a00399** (6 anchors: 0, 50, 100, 150, 200, 250):

| Anchor | Raw (no HDR) | L2 v1 (anchored) | L2 v2 (centered) |
|---|---|---|---|
| 0 | 14.93 | 14.05 | **13.89** |
| 50 | 22.64 | 14.05 | 14.15 |
| 100 | 24.42 | 22.00 | **21.83** |
| 150 | 28.70 | 27.46 | **26.52** |
| 200 | 28.71 | 29.21 | **28.35** |
| 250 | 26.83 | 28.78 | **26.61** |
| **Mean** | **24.37** | 22.59 (-8.2%) | **21.89 (-10.8%)** |

Key observations:
- L2 v1 (anchored) improves on average but **worsens** anchors 200, 250 (front_center in shadow → all other gains > 1 → over-amplification)
- L2 v2 (centered, geometric mean of gains = 1) fixes worst cases without sacrificing best cases. Robustly $\geq 0$ improvement on all anchors.
- Best-case improvement on anchor 50: $-37.5\%$ (one cam had strong sun glare; HDR brought it down)

### Computational cost

| Method | Anchor time | Total deps |
|---|---|---|
| Multiband (baseline) | 6 s | cv2, numpy |
| L1 only (hard select) | 10 s (= 9 s projection + 1 s argmax) | cv2, numpy |
| L1+L2 (HDR) | 13 s | cv2, numpy |
| L1+L2+L3 (full) | 50 s | cv2, numpy |
| N1 + LiDAR | 25 s | + scipy |
| N1 + Depth Anything V2 | 45 s | + torch, transformers |

Measured at $2048 \times 4096$ ERP on Tesla T4 GPU (Colab). Note our full pipeline is essentially CPU-bound (Farneback OF); the T4 GPU is used only for cv2's CUDA hooks where available.

### YOLO ghost count

(TODO once new pipeline panoramas are scored)

Earlier ghost detection on the **multiband baseline** identified 7 strict ghost-free anchors (YOLO score = 0) and 146 relaxed (score $\leq$ 2) across 575 anchors. We will re-run the same YOLO v2 scorer on the new pipeline outputs to measure ghost reduction.

## 4.4 Qualitative results

### BMW close-up (log 02a00399 anchor 0)

`deliverables/hard_select_hdr_joint/bmw_4way.png` shows 4-way comparison:
1. **Multiband** (top): visibly doubled BMW body, ghosted wheel
2. **L1 only**: single BMW, but visible pink/magenta cast at bottom-front (from one cam's exposure)
3. **L1+L2** (HDR Y-only): single BMW, color cast reduced
4. **L1+L2+L3** (full): single BMW, lane lines aligned across seam

### Porsche close-up (log 02a00399 anchor 0)

`deliverables/hard_select_hdr_lum/porsche_4way.png`:
1. **Multiband**: doubled wheel + soft body double
2. **L1**: single Porsche, vertical brightness step across road
3. **L1+L2**: brightness step substantially reduced
4. **L1+L2+L3**: same + subtle lane-line continuity improvement

### v1 vs v2 ablation

`deliverables/hard_select_hdr_lum/porsche_old_vs_new_hdr.png`: v1 per-channel HDR (top) shows visible magenta cast on right half; v2 luminance-only HDR (bottom) preserves chroma.

`deliverables/hard_select_hdr_joint/porsche_chain_vs_joint.png`: chain HDR vs joint HDR — joint version more uniform across panorama (no chain drift).

## 4.5 Failure cases

(TODO: section pending full 5-log analysis)

Expected failure modes:
1. **Textureless overlap** (clear sky, smooth road) → Farneback OF unstable. Mitigated by Gaussian smoothing on flow field, but residual misalignment can occur.
2. **Moving objects** → temporally inconsistent if cam exposures captured the object at slightly different sub-frame times. AV2's hardware-triggered sync minimizes this but doesn't eliminate.
3. **Strong sun flare in one cam** → HDR over-compensates, can cause clipping. Centered gains help but don't fully solve.
4. **Very near-field (< 1m)** → parallax exceeds OF search window; lane lines on the ground directly in front of ego may still misalign.

## 4.6 Discussion

Our approach demonstrates that the **doubled-feature ghost** in multi-camera 360° panoramas is fundamentally a **view-mixing** problem, not a depth/geometry problem. All 4 of our depth-based attempts (N1 family) failed because they tried to "align" two cameras that were seeing genuinely different content. The fix is to STOP averaging incompatible views: hard camera selection eliminates the ghost at the cost of seams, which two layers of classical CV (luminance HDR and Farneback OF warp) then close.

The full pipeline runs in 50s/anchor with no dependencies beyond OpenCV. We argue this is a strong baseline for any future work in ring-camera panorama stitching, and that depth-based approaches must demonstrate clear improvement *over hard selection*, not just over multiband blending.

Future directions:
- Joint OF solve (not chain) for fully consistent alignment across all 7 cams
- Brown-Lowe SIFT+RANSAC as a more robust alternative to dense Farneback in textureless overlaps
- Per-channel chroma correction in YCrCb (additive offsets) for full color equalization
- Temporal consistency across consecutive anchors for video output (per-anchor gains can flicker)
- Eventually: NeRF/3DGS-based **true view synthesis** at object level for the few remaining doubled features in very-near-field (sub-1m) cases
