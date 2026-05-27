# L1+L2+L3 Basic-CV Pipeline — Handoff Doc

**Status as of 2026-05-27 UTC**: Pipeline shipped to `main`, currently rendering 5 val logs (stride=10, ~1.75 hr expected).

## TL;DR

Three-layer basic-CV pipeline solves the AV2 → ERP panorama ghosting problem **without depth**:

```
ring slabs (legacy infinity projection)
        │
        ▼
   L2 HDR (joint global lstsq, Y-channel)   ← exposure equalization
        │
        ▼
   L3 OF chain warp (Farneback, anchor=front_center)  ← parallax correction
        │
        ▼
   L1 hard select (argmax of cos² weights)  ← view-mixing ghost elimination
        │
        ▼
   ERP panorama (uint8)
```

**Cost**: 40-50s/anchor @ 2048×4096 on T4 GPU. No new dependencies (cv2 + numpy).

**Code**: `code/waymo2panorama/blending/hard_hdr_of.py` (~200 LOC), wired into `stitch_one_frame(blend_mode="hard_hdr_of")`.

## Why each layer

### L1 hard_select — kills doubled-feature ghost
- **Problem**: cos²-weighted multiband blend AVERAGES two cams in overlap. When both cams see the same object (e.g., BMW) from different angles, the average becomes a *doubled* object (two car bodies, ghosted wheels).
- **Root cause**: view synthesis. Cam A sees left side of BMW, cam B sees right side. Their projections to ERP land at slightly different positions due to parallax. Blending them = sum of two different views.
- **Fix**: don't blend. Pick the ONE cam with highest cos²(angle-from-optical-axis) weight per pixel. Each ERP pixel = exactly one cam.
- **Side benefit**: also eliminates the *translucent ghost* (faint column-shaped artifact where one cam sees content the other doesn't — naive blend gives half-strength residual).

### L2 HDR — kills cross-cam brightness step
- **Problem**: AV2 ring cams have different auto-exposure → mean luminance gap of 5.5 dB (max 9.1 dB) between adjacent cams. Hard select makes this visible as a sharp vertical brightness step at every seam.
- **Fix**: per-cam scalar gain on Y channel (preserves chroma/hue). Computed via joint global lstsq over all 7 ring pairs (including back seam between rear_left and rear_right). Anchor: front_center gain = 1.0.
- **Why luminance-only?** v1 per-channel chain caused color cast (rear_right green = 1.33×, magenta cast on right half). Y-channel-only in YCrCb preserves hue, only adjusts brightness.
- **Why joint vs chain?** Chain accumulates drift along the ring (28% gain span). Joint closes the loop via back-seam constraint, halving the span (18%) and equalizing back-seam ratio (1.07× vs chain's 1.30×).

### L3 OF chain warp — kills spatial parallax seam
- **Problem**: two cams looking at the same road line from different positions project that line to slightly different ERP pixels. After hard select, the lane line "jumps" at the seam.
- **Fix**: per-pair Farneback dense optical flow in overlap zone → warp cam B to align with cam A. Chain from front_center anchor in both CCW and CW directions.
- **Why OF, not depth?** Two depth approaches were tried (LiDAR sparse + DA-V2 dense) and both failed for view-synthesis reasons. OF uses the two cams *themselves* as ground truth — wherever they disagree on pixel position IS the parallax displacement.
- **Effect**: subtle but real. Lane lines, curbs, road markings have better continuity at seams. Buildings/cars (already far enough away that parallax is small) ~unchanged.

## Pipeline order

```
project → L2 HDR → L3 OF → L1 hard_select
```

L2 first so OF doesn't lock onto brightness mismatches as if they were features. L1 last as the final per-pixel pick.

## Files

| Path | Purpose |
|---|---|
| `code/waymo2panorama/blending/hard_hdr_of.py` | The 3-layer module |
| `code/waymo2panorama/pipeline/stitch_frame.py` | Updated with `blend_mode` arg |
| `scripts/phase3/render_log_with_hard_hdr_of.py` | CLI to render all anchors of a log |
| `scripts/phase3/test_hard_select_hdr_of.py` | 4-way comparison prototype (multiband / HS / HS+HDR / HS+HDR+OF) |
| `deliverables/hard_select/bmw_compare.png` | L1 proves itself: doubled BMW → single BMW |
| `deliverables/hard_select_hdr_lum/porsche_old_vs_new_hdr.png` | L2v1 (per-channel) vs L2v2 (lum-only): chain cast fixed |
| `deliverables/hard_select_hdr_joint/porsche_chain_vs_joint.png` | L2 chain vs joint: drift fixed |
| `deliverables/verify_pipeline/anchor_0000.png` | Productionized output on BMW anchor |

## What was tried and didn't work (negative results)

These are documented in `deliverables/N1_AUTONOMOUS_RUN_SUMMARY.md` and `agent/progress.md`:

| Approach | Why it failed |
|---|---|
| N1 single convergence radius | One r can't cover both 3m (BMW) and ∞ (sky) at once |
| N1 LiDAR per-pixel depth | LiDAR is sparse on smooth car surfaces; kNN-fill wrong on near-field |
| N1 LiDAR + graphcut hard seam | Same depth issue, graphcut cost couldn't find clean path |
| N1 Depth Anything V2 dense depth | Single-view CNN depth has view-synthesis residual same as no depth |
| 5-way HDR + N1 + graphcut stack | All layers stacking depth errors, made things worse |

**Common failure mode**: trying to fix view-synthesis problem (different-angle views of same object) with depth/geometry. The blending paradigm itself is the problem, not the geometry. Hard select sidesteps the entire issue.

## Usage

```python
from waymo2panorama.pipeline.stitch_frame import stitch_one_frame
from waymo2panorama.data_io.av2_loader import AV2RingLoader

loader = AV2RingLoader("/path/to/av2/log_dir")
ts = loader.anchor_timestamps_ns()
frame = loader.load_synced_frame(ts[anchor_idx])

# Full pipeline (50s @ 2048x4096)
erp = stitch_one_frame(frame, erp_hw=(2048, 4096), blend_mode="hard_hdr_of")

# Faster: skip OF (~20s, slightly worse seams)
erp = stitch_one_frame(frame, erp_hw=(2048, 4096), blend_mode="hard_hdr")

# Backward-compat: multiband (original, has ghost)
erp = stitch_one_frame(frame, erp_hw=(2048, 4096), blend_mode="multiband")
```

CLI batch render:
```bash
python scripts/phase3/render_log_with_hard_hdr_of.py \
    --log-dir /content/drive/.../02a00399-... \
    --output-dir /content/drive/.../full_pipeline_v1/02a00399 \
    --erp-h 2048 --erp-w 4096 --stride 10
```

## Remaining minor artifacts

These are visible if you look closely but acceptable for most use:

1. **Back seam residual** — CCW and CW OF chains both end at rear cams; the back seam between rear_left and rear_right has no direct OF correction (only HDR closes the loop). Residual: ~5-10 px misalignment on near-field rear objects.
2. **OF in textureless areas** — Farneback can have unstable flow on uniform sky or smooth road. Smoothing (σ=5px) + clipping to overlap mask mitigates but doesn't eliminate.
3. **Auto-white-balance variation** — HDR fixes Y but cams sometimes have different white balance settings. Chroma can have small step jumps. Would need a per-cam white-balance solve to fully fix.

## Paper framing

**Method**: Three-layer view-synthesis-aware blending for multi-camera 360° panorama stitching, replacing naive Burt-Adelson multiband blend.

**Baselines**:
- Multiband (current SOTA for traditional stitching)
- N1 family (4 phases — depth-based attempts, all NEG; honest ablation)

**Eval metrics**:
- YOLO bbox-in-seam-zone count (object-aware ghost detector, v2)
- Cross-cam luminance gap at seam (HDR effectiveness)
- Manual user study (which panorama looks cleaner)

**Ablation**:
- L1 only / L1+L2 / L1+L2+L3 — each layer's contribution
- Joint vs chain HDR
- Per-channel vs luminance-only HDR

## Next obvious next-steps (in priority order)

1. **Wait for 5-log run to finish** → generate Bosch preview grid → ship deliverable
2. **OF tuning**: larger window (winsize=51), less smoothing — might give cleaner seams
3. **Stronger HDR**: regularize gains toward 1.0 (avoid extreme corrections)
4. **Graphcut seam**: route final seam through low-mismatch contours after L1+L2+L3 — additional polish
5. **Brown-Lowe SIFT+RANSAC**: as a stricter alternative to Farneback for OF (more robust in textureless areas)
6. **Write paper draft** (3DV 2026 deadline)
