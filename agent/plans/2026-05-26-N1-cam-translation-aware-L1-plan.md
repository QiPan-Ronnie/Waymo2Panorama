# N1 Cam-Translation-Aware L1 — Brainstorming Checkpoint Plan

**Date**: 2026-05-26
**Status**: BRAINSTORMING CHECKPOINT — not final, X/Y path choice still open
**Origin**: `/brainstorming` session after Stage 3 v5 ghost-truth audit (5-26 ~20:00 UTC)
**Next**: resume `/brainstorming` to lock X vs Y + paper venue + N4 ablation

---

## Context

5.22 prompt §1a (Porsche/BMW 2-wheel ghost in L1 ERP) remains unresolved after **9 sparse-displacement attempts** all in the same axis:

- T4 v1/v2/v3 (Option B reweight) — NEG: multiband normalize cancels reweight
- T5 v1/v2/v3 (L1+ORB chain warp) — NEG: ring cams geometrically can't share image plane
- WS4 A2/B1 (sparse stereo displacement + graphcut disparity) — NEG: sparse anchor too sparse
- Stage 3 Phase A/B/C v1-v5 (joint midpoint + gauss + min_parallax filter) — metric "polished" but visible ghost unchanged (Porsche v5 max_diff=0, BMW v5 5% scatter, neither modifies the 2-wheel ghost)

Stage 3 v5 audit concluded "L1+sparse displacement geometrically impossible". This brainstorming session **challenges that conclusion** by surfacing 5 fresh directions (N1-N5) the 9 attempts didn't try, and pinning the actual root cause via code inspection + verified AV2 calibration values.

---

## Root cause — newly verified (this session)

### Code-layer (sphere_projection.py:86-89)

```python
R_ego_cam = T_ego_cam[:3, :3]
R_cam_ego = R_ego_cam.T
d_cam = d_ego @ R_cam_ego.T
# T_ego_cam[:3, 3] (cam translation) is silently dropped
```

The L1 baseline docstring (line 5-12) even admits: *"L1 assumption: ignore the camera's translation. Treat every ring camera as if it were mounted at the ego-vehicle origin."*

### Data-layer (AV2 actual cam positions, from sensor.feather)

```
ring_front_center: (x=1.631, y=-0.001, z=1.433) m
ring_front_left:   (x=1.550, y=+0.198, z=1.431) m
ring_front_right:  (x=1.554, y=-0.194, z=1.431) m
ring_side_left:    (x=1.310, y=+0.268, z=1.433) m
ring_side_right:   (x=1.310, y=-0.273, z=1.436) m
ring_rear_left:    (x=1.104, y=+0.124, z=1.446) m
ring_rear_right:   (x=1.103, y=-0.128, z=1.428) m
```

- Adjacent ring cam baseline: **0.21-0.26 m**
- All 7 cams mounted **1.0-1.6 m forward of ego origin** (systematic offset)
- Current L1 treats all of these as (0,0,0)

### Geometric prediction (this session)

For a 3D point at (3, 1, 0) — 3m forward, 1m left of ego origin (typical Porsche-at-curb position):
- ego origin angular position: θ = 18.4°
- ring_side_left view: θ = 23.1°
- ring_front_left view: θ = 28.7°
- Inter-cam ERP angular ghost ≈ **5.6° = ~32 px** (on 2048-wide ERP)

**Matches observed Porsche 2-wheel ghost magnitude (~30-50 px)** ⇒ hypothesis sound.

---

## Direction selected — N1 cam-translation-aware finite-radius L1

Replace the projection logic with translation-aware version:

```python
P_ego = r * d_ego                                   # 3D point at distance r in ego frame
P_cam = (P_ego - T_ego_cam[:3, 3]) @ R_cam_ego.T    # translate then rotate
u_img = K[0,0] * P_cam[..., 0] / P_cam[..., 2] + K[0, 2]
v_img = K[1,1] * P_cam[..., 1] / P_cam[..., 2] + K[1, 2]
```

`r → ∞` degenerates back to current L1 (backward compat). For the Porsche scene, predicted optimal r ≈ 3-5m.

---

## Two execution paths (X/Y decision pending)

### Path X — Conservative gated (4-5 day)

```
Day 1 (0.5d): N1.a single r ∈ {3,5,7,10,15,30,∞} sweep on Porsche+BMW frame
              GATE: r=5m visibly reduces 2-wheel ghost?
              YES  → Day 2
              NO   → STOP, audit other ghost factors (time-sync, motion blur)

Day 2-3 (1-2d): N1.c LiDAR per-pixel r (skip N1.b per-region)
              NEW: code/waymo2panorama/depth/lidar_to_erp_depth.py
              A/B vs N1.a single-r

Day 4-5 (1-1.5d): N2 LiDAR-MRF graphcut seam on N1.c residual ghost regions
              Extend blending/graphcut_seam.py with depth term in MRF energy
              (ISPRS 2024 published direction)
```

Risk: LOW. Hard gate at Day 1 prevents wasted Phase B/C work if hypothesis fails.

### Path Y — Aggressive paper-first (5-7 day)

```
Day 1-2: N1.c LiDAR per-pixel directly (skip sanity check)
Day 3:   A/B visual + metric vs plain L1
Day 4-5: N2 LiDAR-MRF graphcut seam
Day 6-7: 5 val log cross-validation + paper figure regeneration
```

Risk: MED. Day 1-2 LiDAR pipeline debug if calibration sync / 64-beam sparsity edge cases hit.

---

## Critical files

**Modify**:
- `code/waymo2panorama/projection/sphere_projection.py` — add `convergence_distance_m: float | np.ndarray | None = None` to `render_camera_to_erp`. `None` → current behavior (backward compat).
- `code/waymo2panorama/pipeline/stitch_frame.py` — pass-through param to both `stitch_one_frame` and `stitch_one_frame_with_prewarp`.

**New**:
- `code/waymo2panorama/depth/lidar_to_erp_depth.py` (Phase C of X / Phase 1 of Y) — AV2 LiDAR sweep → ERP dense depth map. Handles 64-beam sparsity via bilateral fill.
- `scripts/phase3/run_l1_finite_radius.py` — r-sweep driver (default r ∈ {3,5,7,10,15,30,∞}).
- `scripts/phase3/eval_l1_finite_radius.py` — ghost-bbox SSIM + cycle PSNR.

**Reuse** (already shipped, do not rewrite):
- `data_io/av2_loader.py` — `T_ego_cam` already has translation, just need to start using it.
- `blending/graphcut_seam.py` (新-B) — shipped, add depth term for N2.
- `scripts/phase3/eval_cycle_consistency.py` — existing metric, reuse.

---

## Verification

### Visual gate (decisive, every phase)

- log `02a00399` anchor 0, zoom on:
  - Porsche (col ~1500, row ~1000-1300)
  - BMW (col ~3500, row ~900-1300)
- 3-row or N-row stack: plain L1 / N1.a r-sweep / N1.c LiDAR
- Pixel diff overlay (amplified 4×) per row

### Quantitative

- **Ghost-bbox SSIM** (new metric) — localized on Porsche / BMW bounding box, measures structural similarity at the ghost region
- **Cycle-PSNR** (existing, `eval_cycle_consistency.py`)
- **4-anchor sweep** on log 02a00399 (anchor 0, 60, 90, 150)
- **Cross-log validation** on 5 val logs (Phase C only)

### Unit tests

- `convergence_distance_m=None` → byte-identical to current `render_camera_to_erp`
- `convergence_distance_m=1e6` → numerically ≈ current within 1e-3 px
- synthetic 2-cam ring + known 3D point at distance r → 2 cams place point at same ERP angular location at r, drift at other distances

---

## Outstanding brainstorm decisions (resume `/brainstorming`)

1. **X vs Y path choice** — gate-on-Porsche-first vs commit-LiDAR-upfront
2. **Add N4 (stereo-confidence blending) as ablation column**? — extra 1 day, gives "blend selection vs blend uniformly" comparison
3. **Paper venue**: 3DV 2026 (~Sept deadline) vs ECCV/CVPR 2027 (~Mar deadline)
4. **Retry L3 (Pi3 forward splat) on AV2 raw**? — Pi3 was NEG on pi3-cache but **never retried on un-letterboxed AV2 raw**; could be revealing ablation
5. **DVGT as alternative depth backbone for N1.c** — instead of LiDAR-per-pixel, use DVGT (metric-scaled, no Sim(3)) for pure RGB depth; LiDAR vs DVGT becomes paper ablation

---

## Out of scope

- Paradigm shift (D class: PIS3R, Seam360GS, generative)
- Task reframe (E class: frame selection, temporal fusion)
- Teammate Waymo work (user explicitly deprioritized: color shift, ORB deformation, rolling shutter, jelly effect)
- New stitching routes beyond the N1+N2 combo in this plan

---

## Novelty positioning (honest)

N1 alone is **NOT paper-grade novel** — "finite convergence distance" is patent-known VR practice (US Patent 12,387,427 — "Light field camera system convergence distance"). Consumer 360 cameras (Insta360 Pro 2) let users set this manually.

Paper novelty comes from the **combination**:
1. First AV ring-cam-specific implementation. No published method in 2024-2026 SOTA targets this exact setup:
   - OmniStitch (ACM MM 2024) trains on CARLA GV360, dataset private, AV2 generalize NEG (-6.67 dB per project notes)
   - PIS3R (arxiv 2508.04236) is 2-image stitching, not ring
   - PTRS (IEEE 2025) targets 90°/180° baselines, not our 60° ring
   - LiftProj (arxiv 2512.24276) is single-pano construction, not multi-cam fusion
2. LiDAR-anchored per-pixel r (N1.c) — uses AV2 64-beam sensor advantage that RGB-only methods can't
3. Combination with N2 (LiDAR-MRF graphcut seam, ISPRS 2024 published direction for general panorama) — first AV application
4. Honest ablation: 9 prior NEG attempts (sparse-displacement axis) become the "what doesn't work" ablation table

---

## Sources verified this session

- AV2 cam positions: https://argoverse.github.io/user-guide/datasets/sensor.html
- ISPRS 2024 depth-MRF seamline: https://isprs-archives.copernicus.org/articles/XLVIII-4-W10-2024/191/2024/
- DVGT CVPR 2026: https://arxiv.org/abs/2512.16919
- PIS3R: https://arxiv.org/abs/2508.04236
- OmniStitch ACM MM 2024: https://dl.acm.org/doi/10.1145/3664647.3681208
- PTRS IEEE 2025: https://ieeexplore.ieee.org/document/11229251/
- Depth-Supervised Fusion Network (Oct 2025): https://arxiv.org/abs/2510.21396
- Seam360GS (Aug 2025): https://arxiv.org/abs/2508.20080
- US Patent 12,387,427 (convergence distance prior art): https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12387427
