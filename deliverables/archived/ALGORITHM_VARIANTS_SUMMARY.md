# Algorithm Variants Summary

Catalog of all blend pipelines shipped during the autonomous work session.
All in `code/waymo2panorama/blending/` and selectable via the test scripts in
`scripts/phase3/`.

## Shipped variants

| Variant | Module | Status | Notes |
|---|---|---|---|
| **Multiband** (baseline) | `multiband.py` | Reference | Burt-Adelson Laplacian pyramid. Produces doubled-feature ghost. |
| **L1 hard select only** | `hard_hdr_of.py` (`hard_select`) | Reference | Argmax of cos² weight. No ghost but visible seams. |
| **L1+L2+L3 (shipped)** | `hard_hdr_of.py` (`blend_hard_hdr_of`) | **DEFAULT** | hard_select + joint global luminance HDR + Farneback OF chain warp + back-seam closure. Production pipeline used for the 5-log Bosch deliverable. |
| **+Improvement A chroma** | `hard_hdr_of_chroma.py` | Reviewed ✓ | Tikhonov-regularized Cr/Cb offsets in YCrCb (additional layer after L2 luminance HDR). |
| **+Improvement B graphcut** | `hard_hdr_of_graphcut.py` | Pending | (Subagent still running) cv2.detail.SeamFinder for content-aware seam routing. |
| **+Improvement F self-stereo** | `hard_hdr_of_selfstereo.py` | NEG ✗ | Derive depth from cam-pair OF, re-project with N1 mode. Math works (correct depth) but inherits N1 FOV-gap pathology — BMW coverage 98.7% → 62.1%, large black holes. Confirms L3 OF (2D warp) is the right way to apply OF alignment signal. |
| **G freq-band hybrid** | `hard_hdr_of_freqhybrid.py` | Reviewed ✓ | Low-freq bands: cos² blend (smooth tones). High-freq bands: hard select (sharp details). Cutoff=2/5. Validated: cutoff=0 == multiband; cutoff>num_bands == hard_select. Limitation: low-freq bands still leak attenuated ghost. |
| **H bidirectional OF** (3 modes) | `hard_hdr_of_bidir.py` | Reviewed ✓ + fixed | `mode="chain"` (default, true bidirectional via mean half-flow per cam — equivalent to single Jacobi iteration of joint solve); `mode="joint"` (global lstsq with anchor + Tikhonov, ~26s/anchor at 2048×4096); `mode="half_chain"` (legacy asymmetric, kept for back-compat). |

## Cumulative file count

After all dispatches, the `blending/` module count:
- `multiband.py` (existing)
- `hard_hdr_of.py` (shipped default)
- `hard_hdr_of_chroma.py` (Improvement A)
- `hard_hdr_of_graphcut.py` (Improvement B, pending)
- `hard_hdr_of_selfstereo.py` (Improvement F, NEG)
- `hard_hdr_of_freqhybrid.py` (Improvement G)
- `hard_hdr_of_bidir.py` (Improvement H, 3 modes)

## Key negative findings (paper ablations)

1. **N1 family (4 phases tested earlier)** — all NEG. Depth-aware projection can't fix doubled-feature ghost because it's view-mixing, not alignment.
2. **Improvement F self-stereo** — NEG. Even SELF-DERIVED perfect depth fails because N1 reprojection narrows cam FOV cones → coverage holes. Strengthens (1)'s claim.
3. **YOLO panorama detection count** — wrong metric. hard_hdr_of has MORE detections (+43%) than multiband, because hard select sharpens distant objects multiband blurred away. Not a ghost metric.
4. **YOLO doubled-pair count** (IoU 0.1-0.7) — also wrong metric. Scales with detection count. Conf=0.3: mb 0.05 / hho 0.06. Conf=0.1: mb 1.03 / hho 1.23. Both essentially equal after normalizing for detection count.

## Key positive findings

1. **L1+L2+L3 ships and works** — 50s/anchor, 160 panoramas across 5 logs, BMW visibly de-ghosted, brightness uniform, lane lines aligned.
2. **L2 joint HDR (centered Y-only)** — 38% mean seam-gap reduction across 5 logs (range -13% to -57% by log).
3. **L1+L2+L3 is THE basic-CV winner** — all 4 alternatives (chroma, graphcut, freqhybrid, bidir) are minor variations on the same idea; self-stereo confirms depth-based is wrong.

## Variants worth A/B comparing on more data

Once user picks "ship for paper":
- **Improvement A (chroma)** — may visibly improve color uniformity. Quick to add as default.
- **Improvement H bidir chain** — symmetric, no preferred cam. May reduce drift at back seam. Quick to add as default.
- **Improvement G freq-hybrid** — soft tones + sharp details combo. Risk: low-freq ghost leak.

Not recommended for default:
- **Improvement F (self-stereo)** — NEG, demonstrated worse.
- **Improvement B (graphcut)** — pending subagent result.
