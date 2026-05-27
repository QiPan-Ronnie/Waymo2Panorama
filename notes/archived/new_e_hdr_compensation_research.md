# 新-E HDR Cross-Cam Color Compensation — Research Report

**Explore agent**: a9254f1d (2026-05-21)
**Recommendation**: **Global least-squares over 7-cam ring topology, 6-param gain+bias per cam, scipy.optimize.least_squares with Huber loss**

---

## §1 Model Choice — 6-param Gain+Bias Per Cam

3 candidates:
1. Reinhard 2001 global color transfer (histogram match in lαβ) — brittle on spatially-varying illumination
2. **Gain + bias per channel** (6 params per cam: 3 gain, 3 bias) ← **RECOMMENDED**
3. 3×3 full color matrix (12 params) — overfit risk on ~100-1000 overlap pixels

Why 6-param: AV2 auto-WB makes shifts mostly luminance (gain) + dark-level (bias); 50+ luminance delta captured by single gain; 6-param fits cleanly in LS without overfitting; physically interpretable.

---

## §2 Global LS (Recommended) vs Pairwise Greedy

**Pairwise greedy** accumulates error around ring (cam_6 drifts from cam_0).

**Global LS**: solve all 7 corrections simultaneously, pin cam_0 = identity. Ring closure handled naturally. ~36 free params, solvable in ~100 ms on CPU.

```
min over {g_i, b_i for i=1..6, with g_0=[1,1,1], b_0=0}:
  Σ over (i,j) overlap_pair, Σ over p in overlap_pixels:
    ρ_huber(|| (g_i * RGB_i(p) + b_i) - (g_j * RGB_j(p) + b_j) ||_2)
```

scipy.optimize.least_squares with `loss='huber'` + Levenberg-Marquardt converges ~50 iters.

---

## §3 Overlap Correspondence — ERP-Space (Recommended)

Both cam_i and cam_j rendered to ERP via `sphere_projection.py`. **Same ERP pixel coord = same world ray = same scene point** (modulo parallax — irrelevant for color matching).

Filter: `valid_i AND valid_j` (both weights > 0.01) → extract per-pair RGB tuples. Require ≥ 50 pixels per pair.

No feature matching needed (~1 sec/pair slow) — ERP dense matching suffices.

---

## §4 Robust Estimation

Outliers: specular highlights, moving objects (peds/vehicles crossing seams), depth discontinuities (3-5 px shifts at edges).

Two-tier robustness:
- **RANSAC** per-channel inner loop (100 trials, 50% sample, threshold ~10 unit residual)
- **Huber loss** in outer scipy.optimize.least_squares

---

## §5 White-Balance vs Exposure Decomposition

6-param can post-hoc decompose to (1 exposure + 3 WB + 3 bias = 7 params), but 6-param is simpler for paper. Skip decomposition.

---

## §6 Pipeline Integration

Single-point change in `stitch_frame.py`:
```python
for cam in RING_CAMS_7:
    rgb, _, w = render_camera_to_erp(...)
    slabs_before.append(rgb)
    weights.append(w)

overlaps = extract_overlap_pixels(slabs_before, weights, valid_threshold=0.01)
corrections = global_color_correction(overlaps, num_cams=7)  # (7, 6)

slabs_after = []
for i, (slab, x) in enumerate(zip(slabs_before, corrections)):
    g, b = x[:3], x[3:6]
    corrected = (g[None, None, :] * slab + b[None, None, :]).clip(0, 255)
    slabs_after.append(corrected)

erp = multiband_blend(slabs_after, weights, num_bands=5, wrap=True)
```

---

## §7 Expected Outcome

| Metric | Δ |
|---|---|
| Cycle-PSNR | **+0.05 to +0.20 dB** |
| SSIM | +0.01 to +0.03 |
| **Visual** | **Significant** — uniform sky/shadow across cam seams |

**Paper framing**: preprocessing win, not algorithm win. Section 5 "Per-Camera Color Consistency" subsection.

---

## §8 1-Day Timeline

| Task | Time |
|---|---|
| Pixel correspondence extraction | 2.5 h |
| RANSAC + LS solver (`hdr_gain_estimate.py`) | 1.5 h |
| Integration into stitch_frame | 0.75 h |
| Cycle-PSNR eval on 10 anchors | 1.0 h |
| Visual figures + JSON export | 1.25 h |
| Buffer | 1.0 h |
| **Total** | **8 h** |

---

## §9 Risks + Fallback

| Risk | Mitigation |
|---|---|
| Ring-wide LS diverges | Pairwise greedy fallback |
| Sky vs shadow opposite trends | Per-region (sky/ground) correction (Wave 1.5 enhancement) |
| Multiband already hides → 0 visual delta | Ship as "preprocessing necessity" framing |
| RANSAC fails on dynamic scenes | Add depth prior (use Pi3 far-field only) |

---

## §10 Concrete Deliverables (for gp Implementer)

### A. `code/waymo2panorama/color/hdr_gain_estimate.py` (~150 LOC)

```python
"""Per-cam color gain+bias estimation via global LS with Huber loss."""
import numpy as np
from scipy.optimize import least_squares

def extract_overlap_pixels(slabs, weights, valid_threshold=0.01):
    """slabs: list of (H,W,3); weights: list of (H,W). Returns {(i,j): (rgb_i_N3, rgb_j_N3)}."""
    overlaps = {}
    for i in range(len(slabs)):
        for j in range(i+1, len(slabs)):
            mask = (weights[i] > valid_threshold) & (weights[j] > valid_threshold)
            if np.sum(mask) > 50:
                overlaps[(i, j)] = (slabs[i][mask].astype(np.float32),
                                    slabs[j][mask].astype(np.float32))
    return overlaps

def global_color_correction(overlaps, num_cams=7):
    """Solve {g_i, b_i for i=1..num_cams-1} with cam_0 pinned identity.
    Returns (num_cams, 6) array."""

    def residuals(x):
        # x is (num_cams-1, 6) flattened
        x = x.reshape(num_cams-1, 6)
        r_list = []
        for (i, j), (rgb_i, rgb_j) in overlaps.items():
            x_i = np.array([1,1,1,0,0,0]) if i == 0 else x[i-1]
            x_j = np.array([1,1,1,0,0,0]) if j == 0 else x[j-1]
            g_i, b_i = x_i[:3], x_i[3:6]
            g_j, b_j = x_j[:3], x_j[3:6]
            c_i = rgb_i * g_i + b_i
            c_j = rgb_j * g_j + b_j
            r_list.append((c_i - c_j).ravel())
        return np.concatenate(r_list)

    x0 = np.tile([1,1,1,0,0,0], (num_cams-1, 1)).ravel().astype(np.float32)
    res = least_squares(residuals, x0, loss='huber', f_scale=1.0, max_nfev=5000)

    corrections = np.zeros((num_cams, 6), dtype=np.float32)
    corrections[0] = [1,1,1,0,0,0]
    corrections[1:] = res.x.reshape(num_cams-1, 6)
    return corrections
```

### B. Driver `scripts/phase3/run_hdr_compensation.py` (~200 LOC)

Wraps: load frame → render slabs → extract overlaps → solve LS → apply corrections → blend before+after → save JSON + before.png + after.png + correction_matrices.json.

### C. Output Structure
```
outputs/phase3/p3.7_hdr/anchor_<id>/
  correction_matrices.json  # {cam: {gain: [3], bias: [3]}}
  before.png                # L1 baseline ERP
  after.png                 # L1 + HDR ERP
```

### D. Paper Figure
`deliverables/images/route_hdr_before_after.png` — side-by-side panorama crops (sky, ground, transition) showing color uniformity improvement.

### E. handoff_to_koi_v6.md Route 14 section replace placeholder with method + numbers + image + verdict.
