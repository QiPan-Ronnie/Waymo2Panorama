# Parallax Overlap Fix — Design Spec

**Date**: 2026-05-26
**Author**: Qi Pan + Claude (brainstorming session)
**Status**: Design approved, ready for implementation plan

---

## 1. Background

5.22 BOSCH meeting prompt identified visible artifacts in our panorama output:
- 2-wheel ghost on AV2 sphere L1 (cars at overlap)
- White "pillar" traces on cylinder L2 overlap zones
- User confirmed on 2026-05-26: **all 8 baseline routes have this issue** (verified via user-marked screenshots of cylindrical_l2.png anchor 60)

Root cause (agreed across team after T4 + T5 NEG exploration):
- L1 sphere / L2 cylinder assume scene depth = ∞ → near-field 3D content projects to DIFFERENT ERP pixels from adjacent cams (parallax shift)
- multiband blender averages the disagreement → washed-out white bands + double edges + 2-wheel ghost

Previously explored, all NEG'd (documented in `agent/progress.md` 2026-05-26 entries):
- **T4 Option B reweight** v1/v2/v3 — pure weight tweaks can't move cycle-PSNR (multiband normalize cancels uniform/symmetric boost; held-out metric structurally blind to multiband-only changes)
- **T5 L1+ORB hybrid** v1/v2/v3 — chain warp is geometrically impossible for ring cams pointing in different directions (~60° apart); rotation-only fits get parallax-biased; rotation refinement BA converges to do-no-harm

These NEGs concluded: **simple alignment / weight modifications can't fix parallax** on AV ring cams with near-field content. Need to either use **real 3D info** or **avoid the disagreement** (seam-based).

---

## 2. Goal

Eliminate / substantially reduce parallax-induced overlap artifacts (white traces, double edges, ghosts) in L1 ERP output, while preserving:
- L1 cycle PSNR baseline (currently 12.34 dB headline on AV2 log `02a00399-...`)
- L1 visual cleanness elsewhere (the only reason L1 is the strongest baseline despite no depth-awareness)
- All existing baselines (L1 / L2 / L3 / 新-B / 新-C / 新-D / 新-E / 新-F) intact — code NOT deleted, only added to

---

## 3. Scope

**In scope**:
- Implement and compare **3 candidate parallax-fix approaches**, each as an independent module + driver
- Test on 4 anchors (0, 60, 90, 150 — the ones with cached `outputs/phase3/p3.6_stereo/anchor_NNN/` stereo data)
- Visual + quantitative comparison
- Decide: pick one winner OR design **hybrid** combining best aspects

**Out of scope** (for this spec; deferred):
- Foundation model rebuild approaches (NeRF, 3DGS, 4D Gaussian, diffusion inpainting) — these are 1+ week each; left for WS4+ if all 3 NEG
- L3 (Pi3) quality improvement (VGGT replacement, etc.) — already documented as weak baseline
- Waymo rolling shutter / distance-to-boundary blending (queue mate's items)
- Modifications to existing L1 / L2 / L3 / 新-X codepaths

---

## 4. Architecture

### 4.1 Integration point (CRITICAL — additive only)

Every new approach plugs in **between** L1 sphere projection and multiband blend. The L1 sphere projection runs unchanged. Each new approach receives the **7 ERP slabs + 7 weight maps** and produces **modified slabs / weights** that get fed into the existing multiband blender.

```
Original L1 path (UNTOUCHED):
  cam_imgs → render_camera_to_erp (per cam) → 7 ERP slabs + weights → multiband_blend → final ERP

New approaches (additive, each = separate driver):
  cam_imgs → render_camera_to_erp (per cam) → 7 ERP slabs + weights
              ↓ (intercept here)
       [parallax_fix_module: A2 | C1 | B1 | hybrid]
              ↓ (modified slabs / weights)
         multiband_blend → final ERP
```

**No file in `code/waymo2panorama/projection/sphere_projection.py`, `code/waymo2panorama/blending/multiband.py`, or `code/waymo2panorama/pipeline/stitch_frame.py` will be modified**. All new functionality goes in new files.

### 4.2 New modules (3 + 1 hybrid)

#### Module A2 — Sparse Stereo Displacement (CPU-friendly)

**File**: `code/waymo2panorama/alignment/sparse_displacement.py` (NEW, ~300 LOC)

**Mechanism**:
1. For each cam pair `(cam_a, cam_b)` and its stereo `.npz` (already cached at `outputs/phase3/p3.6_stereo/anchor_NNN/stereo_<a>__<b>.npz`):
   - Load `pts_3d_ego` (N, 3) sparse 3D points
   - Project each 3D point twice via `ego_points_to_erp_uv`: once using L1 sphere assumption per cam (the "expected wrong position"), once using true 3D depth (the "ideal correct position")
   - Per-point displacement vector: `(delta_u, delta_v) = ideal - expected_cam_X` for each cam
2. Interpolate sparse displacement vectors into a **dense displacement field** in the overlap region, using thin-plate spline (TPS) or radial basis function (RBF)
3. Apply displacement to each cam's ERP slab via `cv2.remap`: each pixel reads from the displaced source location
4. Apply HALF the displacement to cam_A and HALF (opposite direction) to cam_B — symmetric warp; both cams meet in the middle
5. Weights are updated proportionally (or kept identity, TBD during impl)

**Inputs**: 7 ERP slabs + 7 weight maps + stereo .npz files + per-cam K + T_ego_cam
**Outputs**: 7 warped slabs + 7 (possibly modified) weight maps
**Compute**: CPU only

**Risk**: stereo coverage is sparse (~5%); TPS extrapolation in stereo-free overlap regions might mis-shift. Mitigation: gate displacement application to a confidence map = sum of Gaussian kernels around splat points; outside high-confidence region, no displacement applied.

#### Module C1 — RAFT Optical Flow Alignment (GPU required ⚠️)

**File**: `code/waymo2panorama/alignment/optical_flow_align.py` (NEW, ~250 LOC)

**Mechanism**:
1. For each cam pair `(cam_a, cam_b)`, take ORIGINAL cam images (504×504 letterboxed)
2. Compute the overlap region in cam_a's pixel plane (project cam_b's frustum into cam_a)
3. Crop both cams to the overlap region (smaller area, faster RAFT)
4. Run kornia RAFT (`kornia.models.optical_flow.RAFT`) on `(crop_a, crop_b)` → dense (H, W, 2) flow
5. Forward-warp cam_b's overlap pixels via flow to cam_a's overlap pixels
6. Continue to L1 sphere projection with warped cam_b image (cam_a unchanged); or symmetric half-flow both sides

**Inputs**: 7 cam images + per-cam K + T_ego_cam + RAFT model
**Outputs**: 7 modified cam images (cam_b's overlap region warped to match cam_a; or symmetric)
**Compute**: **GPU required** for RAFT inference. Per pair ~0.5 s on A100.

**Risk**: RAFT trained on small-baseline temporal pairs; AV ring cam pair has 60° angular baseline = large viewpoint change. May produce noisy flow. Mitigation: fallback to A2 sparse stereo if flow confidence below threshold.

#### Module B1 — Disparity-Aware Graphcut Seam (CPU-friendly)

**File**: `code/waymo2panorama/blending/graphcut_disparity.py` (NEW, ~250 LOC)

**Mechanism**:
1. For each adjacent cam pair, compute per-pixel disparity signal in the ERP overlap region:
   - Option (a): from A2's sparse stereo displacement vectors → interpolated disparity magnitude
   - Option (b): from C1's RAFT flow magnitude (if RAFT was run)
2. Compute pixel-wise "disagreement" energy: combination of disparity + color difference between cam_a slab and cam_b slab
3. Run graphcut: find min-cost seam through the overlap region that minimizes total disagreement along the cut
4. Output: hard binary mask (cam_a wins on one side, cam_b on the other) replacing the soft cos² feather in overlap region
5. Feed modified weights to multiband blend

**Inputs**: 7 ERP slabs + 7 weight maps + disparity signal (from A2 or C1)
**Outputs**: 7 modified weight maps (binary in overlap regions)
**Compute**: CPU only. Graphcut via `pymaxflow` or `networkx` min-cut.

**Risk**: hard seam might be visible at high-texture boundaries. Mitigation: 1-2 pixel soft transition at the seam (small Gaussian blur on the binary mask).

#### Module HYBRID — Per-pixel best-of approach (designed after 3 candidates measured)

**File**: `code/waymo2panorama/alignment/parallax_hybrid.py` (NEW after candidates evaluated)

**Mechanism (TBD pending eval)**: e.g., fallback ladder per ERP pixel:
- If A2 confidence (sparse stereo coverage) > τ → use A2's displacement
- Else if C1 RAFT flow confidence > τ → use C1's flow
- Else (low-info region) → use B1 seam

### 4.3 New drivers (per approach + one hybrid)

Each candidate gets its own self-contained driver, mirroring the existing `scripts/phase3/run_l1_baseline.py` / `run_l1_rotation_refine.py` pattern:

- `scripts/phase3/run_l1_sparse_disp.py` (~200 LOC)
- `scripts/phase3/run_l1_optflow.py` (~200 LOC)
- `scripts/phase3/run_l1_graphcut_disp.py` (~200 LOC)
- `scripts/phase3/run_l1_parallax_hybrid.py` (~200 LOC, after 3 candidates done)

Each accepts `--pi3-dir` (anchor directory), produces a final ERP PNG + summary JSON.

### 4.4 New eval scripts

- `scripts/phase3/eval_parallax_fixes_cycle.py` (~300 LOC)
  - Per anchor × per cam hold-out cycle PSNR (cam plane GT-anchored)
  - Same protocol as `eval_l1_rotation_refine_cycle.py`
- `scripts/phase3/eval_parallax_seam_metric.py` (~200 LOC, NEW METRIC)
  - Compute "seam visibility" metric: ratio of Laplacian variance in expected overlap regions vs non-overlap regions
  - Lower in overlap = less visible seam = better
- `scripts/phase3/build_parallax_compare_panel.py` (~150 LOC)
  - Generate side-by-side visual comparison: plain L1 / A2 / C1 / B1 / hybrid for anchor 60 + at least 2 others

---

## 5. Data flow

For each anchor:

1. **Input**: Pi3 cache `outputs/phase3/p3.1_multi_anchor/anchor_NNN/` (7 cam images + K + T_ego_cam) + stereo cache `outputs/phase3/p3.6_stereo/anchor_NNN/` (7 .npz files with sparse 3D points)
2. **L1 sphere projection** (unchanged): 7 ERP slabs (1024×2048×3 each) + 7 weight maps
3. **Parallax fix** (one of A2 / C1 / B1 / hybrid):
   - Determine adjacent pair overlap masks (where both cams' weight > 0)
   - Compute parallax signal (displacement / flow / disparity) per pair
   - Modify slabs (warp) and/or weights (seam mask) accordingly
4. **multiband_blend** (unchanged): 7 modified slabs + 7 modified weights → final ERP
5. **Save**: ERP PNG + summary JSON

---

## 6. Eval strategy

### 6.1 Visual

Generate 4 side-by-side comparison panels (one per anchor in {0, 60, 90, 150}):
- 5-row stack: plain L1 / A2 / C1 / B1 / hybrid (or 4-row if hybrid skipped)
- Each row labeled
- Save to `outputs/phase3/p3.X_parallax_compare/anchor_NNN/compare_panel.png`

User visually inspects: which approach best eliminates white traces / 2-wheel ghost?

### 6.2 Quantitative — cycle PSNR (vs L1 baseline 12.34 dB)

Same 28-measurement protocol as T5 v3 (4 anchors × 7 cam hold-out, cam-plane reconstruction with the modified extrinsics / slabs):
- For each candidate: mean PSNR + per-cam delta vs plain L1
- Decision: candidate is "do no harm or better" if mean delta ≥ -0.1 dB

### 6.3 Quantitative — new seam visibility metric

For each candidate:
- Compute Laplacian variance in **expected overlap regions** (where ≥ 2 cams' weight > 0) vs **non-overlap regions**
- Lower variance in overlap region = less artifact (better)
- Report ratio vs plain L1

### 6.4 Decision criteria

After all 4 metrics across 4 anchors:
| Outcome | Action |
|---|---|
| One approach clearly best on visual + cycle PSNR + seam metric | Ship that approach |
| Different approaches win on different metrics | Design hybrid combining the wins |
| All 3 ≤ L1 on cycle PSNR but visual obviously better | Ship the visually best (cycle metric might be insensitive — same lesson as T4) |
| All 3 NEG visually + quantitatively | Document as NEG paper datapoint; move to D series (NeRF / 3DGS) |

---

## 7. Key design decisions (rationale)

1. **Intervene at ERP slab layer, not source image layer**
   - Why: keeps L1 sphere projection clean and unmodified; localizes correction to ERP coords; allows per-pair independence
   - Tradeoff: small loss of fidelity (warping post-projection rather than pre-projection); accepted because the alternative changes L1 path

2. **Per-pair independent processing (not global)**
   - Why: T5 chain warp NEG showed global compose accumulates drift; per-pair avoids that entirely
   - Tradeoff: no global consistency across the ring; OK because each overlap is independent

3. **Symmetric half-warp (both cams move toward middle)**
   - Why: more symmetric, no asymmetric bias; matches AutoStitch convention
   - Tradeoff: slightly more compute (warp both); acceptable

4. **No modification of weights in A2 / C1, only in B1 (seam)**
   - Why: keeps the cos² feather behavior consistent with L1 wherever possible; only seam approach intrinsically needs binary mask

5. **All baselines preserved**
   - User constraint: cannot delete original L1 / L2 / L3 / 新-X code
   - Implementation: ALL new functionality goes in NEW files. Zero modifications to existing modules.

---

## 8. Risks

1. **All 3 NEG**: ~30% probability based on T4 + T5 track record. If so, paper writeup gets a stronger "alignment / blending approaches don't work, must go depth-aware" narrative + next sprint = NeRF / 3DGS.

2. **C1 RAFT GPU dependency**: kornia RAFT requires GPU; if Colab GPU not available when needed, defer C1 to later. A2 + B1 can ship CPU-only.

3. **TPS interpolation instability**: with only ~50 stereo points per pair, TPS surface can wobble in low-coverage areas. Mitigation: regularize TPS strongly + confidence-gated application (no warp where confidence < τ).

4. **Cycle PSNR insensitivity (T4-style)**: the metric might not capture visual improvement. Mitigation: rely on visual comparison + new seam metric as primary; cycle PSNR as secondary "do no harm" check.

5. **Time overrun**: 2-day estimate; if overrun → ship A2 only (the simplest and most asset-leveraging), defer C1 + B1.

---

## 9. Time estimate

| Phase | Estimated time | GPU needed? |
|---|---|---|
| A2 sparse displacement: module + driver + pytest | 4-6 h | No |
| A2 Colab single-anchor smoke + bug fix | 1-2 h | No |
| B1 graphcut disparity: module + driver + pytest | 4-6 h | No |
| B1 Colab smoke | 1 h | No |
| C1 RAFT optical flow: module + driver + pytest | 4-6 h | **YES** (RAFT inference) |
| C1 Colab smoke | 1 h | **YES** |
| Eval scripts (cycle + seam metric) | 2-3 h | No |
| Visual compare panel generator | 1-2 h | No |
| Run all 3 on 4 anchors + collect metrics | 1-2 h | YES (for C1 portion) |
| Hybrid design + impl + eval (if needed) | 3-4 h | Conditional |
| progress.md writeup + commit | 1 h | No |
| **Total** | **~2 days** | GPU needed for C1 portion (~half day total GPU time) |

---

## 10. Success criteria

T5 v3 shipped at "do no harm" — minimal change, parity with L1. This spec aims **further**:

- **Minimum success**: at least 1 approach visibly reduces overlap white traces in side-by-side panel, cycle PSNR delta ≥ -0.1 dB (do no harm or better)
- **Target success**: 1 approach visibly reduces traces AND cycle PSNR delta ≥ 0 dB
- **Stretch success**: hybrid combining wins from 2+ approaches achieves cycle PSNR delta ≥ +0.1 dB AND visible improvement

If none reached: document as NEG, escalate to D series (NeRF / 3DGS / 4D Gaussian) for paper main contribution.
