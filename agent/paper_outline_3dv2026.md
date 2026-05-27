# Paper Outline — 3DV 2026

**Working title**: "Three Layers of Basic CV Beat Depth for Multi-Camera 360° Panorama Stitching"

(or more conservatively): "View-Synthesis-Aware Blending for Multi-Camera ERP Panoramas"

## Hook (1-paragraph abstract sketch)

Multi-camera 360° panorama stitching from autonomous vehicle ring cameras
suffers from "doubled feature" ghosts when adjacent cameras' overlapping views
of near-field objects (e.g., parked cars within 5m) are blended with cosine-
squared feathering or multi-band blending. We show that this is fundamentally
a view synthesis problem — two cameras see different angles of the same object
— and that depth-based corrections (LiDAR splat, monocular depth) cannot
resolve it. Instead, we propose a three-layer pipeline using only classical
computer vision: (L1) hard camera selection by per-pixel cos² argmax to
sidestep the view-mixing ghost; (L2) joint global least-squares luminance
gain solve to equalize cross-camera exposure; (L3) per-overlap Farneback
optical flow chain warp to correct spatial parallax misalignment. The full
pipeline runs in ~50 seconds per anchor at 2048×4096 on a T4 GPU with no
new dependencies. Experiments on Argoverse 2 validation set show [X]% reduction
in cross-camera seam luminance gap, [Y]% reduction in YOLO-detected
near-field ghost objects, and qualitative elimination of all doubled-feature
artifacts on a 575-anchor test set across 5 diverse driving scenes.

## Sections

### 1. Introduction
- Problem: multi-cam 360° panoramas for autonomous driving world model training (Bosch use case)
- Why this matters: synthetic data for AV perception needs ghost-free panoramas
- Naive cos² + multiband blending produces visible doubled-feature ghost
- Recent depth-based approaches (DA-V2, MonoDEVSNet, MVS) fail for view-synthesis reasons
- **Contribution**: three-layer basic-CV pipeline that sidesteps the depth problem

### 2. Related Work
- Classical image stitching (Brown & Lowe 2003 AutoStitch, OpenCV Stitcher, Hugin)
- Multi-band blending (Burt & Adelson 1983 pyramid blend)
- 360° panorama composition (DeepPanorama, Stereo Panorama, etc.)
- Monocular depth (DPT, MiDaS, DA-V2)
- Sparse-to-dense depth from LiDAR (kNN fill, learned completion)
- 3D scene reconstruction for view synthesis (NeRF, 3DGS, Seam360GS)
- Recent ring-cam panorama work in AV (Waymo, AV2 papers if any)

### 3. Method

#### 3.1 Problem formulation
- Multi-cam ring with known K, T_ego_cam (calibration)
- Project each cam to ERP via sphere projection (legacy infinity-depth)
- Each ERP pixel may be valid in 1+ cams (overlap zones)
- Goal: select / combine cams' projections into a clean ERP

#### 3.2 Why blending creates doubled-feature ghost (motivation)
- Show with diagram: two cams seeing a BMW from different angles
- Their projections to ERP land at different positions (parallax)
- cos² weighted average → two faded BMW bodies blended
- This is a VIEW SYNTHESIS problem, not an alignment problem
- Depth-aware projection (N1) doesn't fix it because cams see DIFFERENT
  CONTENT (e.g., cam A sees BMW's left side, cam B sees right side)

#### 3.3 L1: Hard camera selection
- Per-pixel argmax of cos² weight
- Eliminates view-mixing by picking ONE cam's view per pixel
- Trade-off: introduces visible seams (color jump + texture cut)
- **Empirical observation**: BMW doubled ghost → eliminated

#### 3.4 L2: Joint global luminance HDR
- Per-cam scalar gain on Y channel (preserves chroma)
- Linear system: log(g_i) - log(g_j) = log(m_j) - log(m_i) for each pair
- Solve over all 7 ring pairs (incl back seam) via lstsq, anchor: front_center
- **Why luminance-only?** Per-channel chain produced rear-cam green=1.33×
  → magenta cast (ablation, §4.3)
- **Why joint vs chain?** Chain accumulates drift (28% gain span);
  joint closes loop (18%), back-seam ratio 1.07× vs 1.30×

#### 3.5 L3: Per-overlap optical flow chain warp
- Farneback dense OF in each overlap zone
- Warp cam B to align with cam A using flow as displacement
- Chain from front_center: CCW (front_center → front_left → ... → rear_left)
  and CW (front_center → front_right → ... → rear_right)
- **Key insight**: two cams looking at the same scene = ground truth for
  parallax. No external depth (LiDAR, CNN) needed.

#### 3.6 Pipeline order
- project → L2 HDR → L3 OF → L1 hard_select
- L2 before L3 so OF doesn't lock onto brightness mismatches
- L1 last as the final per-pixel pick

### 4. Experiments

#### 4.1 Dataset
- Argoverse 2 sensor dataset (val split)
- 5 diverse logs: 02a00399 (quiet residential), 0bae3b5e (busy urban),
  2c652f9e (intersection), 9f871fb4 (highway), fbee355f (parking garage)
- 575 anchor timestamps total across 5 logs
- Output ERP resolution: 2048×4096

#### 4.2 Baselines
- **Multiband** (Burt-Adelson) — current SOTA classical stitcher
- **N1 single-r** (cam-translation-aware finite radius projection)
- **N1 + LiDAR per-pixel** (sparse splat + kNN fill)
- **N1 + LiDAR + graphcut hard seam** (smart seam routing)
- **N1 + Depth Anything V2** (dense monocular depth from CNN)
- (all N1 variants ALSO failed — honest ablation)

#### 4.3 Ablation table

| Method | Seam ΔY mean ↓ | YOLO ghost count ↓ | Visual? |
|---|---|---|---|
| Multiband | xx.x | xx.x | doubled |
| L1 only | xx.x | xx.x | single + step |
| L1+L2 (joint) | xx.x | xx.x | single + flat |
| L1+L2 (chain) | xx.x | xx.x | drift cast |
| L1+L2 (per-ch) | xx.x | xx.x | magenta cast |
| L1+L2+L3 (full) | xx.x | xx.x | best |
| N1 + LiDAR | xx.x | xx.x | fail |
| N1 + DA-V2 | xx.x | xx.x | fail |

#### 4.4 Cost analysis
- Multiband: 6s/anchor
- L1 only: 1s/anchor (+ projection 9s = 10s total)
- L1+L2: +3s for HDR solve = 13s/anchor
- L1+L2+L3: +27s for OF chain = 40s/anchor
- No new dependencies (cv2 + numpy)

#### 4.5 Qualitative results
- BMW anchor (02a00399 a0): show before/after for L1, L1+L2, L1+L2+L3
- Porsche anchor (same): show same progression
- Busy scene (fbee a95): show robustness
- Failure mode: textureless overlap (sky), what happens with OF

### 5. Discussion & Limitations
- Doesn't fully solve view synthesis (residual ~5-10 px back-seam offset)
- OF can be unstable in textureless areas (sky); mitigated by Gaussian smoothing
- Future work: feature-based stricter alignment (Brown-Lowe SIFT+RANSAC),
  graphcut seam routing on hard_select baseline, joint OF (not chain)
- Out of scope: temporal consistency (each anchor processed independently),
  HDR tone mapping (just exposure equalization), full view synthesis (NeRF/3DGS)

### 6. Conclusion
- View-mixing ghost is the FUNDAMENTAL problem, not geometry
- Three layers of basic CV (hard select + joint HDR + per-overlap OF) suffice
- Useful for: AV synthetic data, surveillance ring-cam composition, panoramic
  photography from rigid multi-cam rigs

## Status of paper materials (as of 2026-05-27)

| Item | Status |
|---|---|
| Method (L1+L2+L3) | ✅ shipped to `main`, runs in `stitch_one_frame(blend_mode='hard_hdr_of')` |
| BMW + Porsche qualitative figures | ✅ `deliverables/hard_select_hdr_joint/` |
| 4-way comparison panel | ✅ `bmw_4way.png`, `porsche_4way.png` |
| N1 NEG ablations (4 phases) | ✅ documented in `agent/progress.md` + `deliverables/N1_AUTONOMOUS_RUN_SUMMARY.md` |
| Full-log preview grid (5 logs) | 🔄 rendering at stride=10 (~1.5 hr) |
| Seam ΔY metric | ✅ 6 anchors of 02a00399: raw 24.4 → hdr 22.6 = 8.2% mean. Range: -7% to +38% per anchor. Best when anchor cam in shadow (250: -7%) or cam with sun glare (50: +38%). |
| YOLO ghost count | ❌ not yet measured on new pipeline; YOLO v2 scoring exists for old multiband |
| User study | ❌ not yet |
| Computational efficiency table | ✅ measured (40s/anchor at 2048×4096 T4) |

## TODO for paper draft

1. [ ] Render figures: bigger BMW/Porsche zooms for paper figure quality
2. [ ] Write the actual sphere projection equations (use existing notes/02_cv_foundations 02 ERP.md)
3. [x] Quantify cross-cam ΔY before/after L2 (script ready, awaiting metric run) — done, 8.2% mean improvement on 6 anchors
4. [ ] Run seam-gap metric on ALL 5 logs for paper Table 2
5. [ ] Run YOLO v2 scoring on new pipeline outputs for ghost count metric
6. [ ] Draft user study: pairs of (multiband, hard_hdr_of) on N anchors, ask which has fewer artifacts
7. [ ] Cite related work properly
8. [ ] Write related-work section

## Known improvements to consider before paper

### Improvement A: Center HDR gains in log space
- Currently HDR anchors front_center to gain=1.0 → when front_center is in shadow,
  all other gains > 1.0, amplifying/clipping the rest of the panorama
- Fix: after lstsq, subtract `log_g.mean()` so gains are centered (geometric mean = 1.0)
- Empirical: anchor 250 had all gains in [1.0, 1.25] (front_center darkest), got -7% seam-gap
  improvement (worse than baseline). Centered gains would put it in [0.89, 1.11] — no clipping.
- 5 LOC change in `compute_hdr_gains`
- Should improve worst-case anchors without changing best-case
- Suggest: add as ablation in paper, show "centered vs anchored" comparison

### Improvement B: Add back-seam OF correction
- Current OF chains end at rear cams; back seam (rear_left vs rear_right) has no
  direct OF correction
- Add one more OF pair: warp rear_right (already-warped) to align with rear_left
  (already-warped) in their back-seam overlap
- Closes the OF "loop"
- 10 LOC change in `of_chain_warp`

### Improvement C: Per-channel chroma correction (without re-introducing cast)
- Current HDR is Y-only. Some anchors have visible chroma drift (different cam
  white balance settings)
- Could solve Cr and Cb independently with joint lstsq, then apply
- Need to verify this doesn't reintroduce the v1 magenta cast — Cr/Cb operate in
  YCrCb space, so corrections are additive (not multiplicative on RGB)
- If it works → fully solves color drift
- ~30 LOC change
