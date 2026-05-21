# 新-B Graph-cut Optimal Seam Selection — Implementation Design

**Plan agent**: ab7f06d (2026-05-21)
**Target file**: `code/waymo2panorama/blending/graphcut_seam.py` (~250 LOC)
**Key insight**: `multiband_blend` already accepts arbitrary per-slab weights → **no blender patch needed**

---

## §1 Module Architecture (key insight)

The L1 pipeline (`code/waymo2panorama/pipeline/stitch_frame.py`) already uses:
```python
for cam in RING_CAMS_7:
    rgb, alpha, w = render_camera_to_erp(...)   # ERP slab + cos²(angle) weight
    slabs.append(rgb); weights.append(w)
return multiband_blend(slabs, weights, num_bands=5, wrap=True)
```

`multiband_blend` accepts arbitrary float (H, W) weights, re-normalizes per pixel. **Perceived "vertical seams" come from cos² weights collapsing onto midlines** — not from fixed angular boundaries in code. So graph-cut just replaces the cos² weights with hard 0/1 masks (slightly feathered) on optimal seam paths.

New file `code/waymo2panorama/blending/graphcut_seam.py` with API:
- `compute_pair_overlap_energy(slab_a, slab_b, alpha_a, alpha_b)` → (energy_map, overlap_mask)
- `find_optimal_seam(energy_map, overlap_mask, source_seed, sink_seed)` → assignment int8 (0=cam-a, 1=cam-b, -1=outside)
- `build_pair_seeds(alpha_a, alpha_b)` → only-a and only-b bool masks
- `apply_graphcut_seams(slabs, alphas, cos2_weights, cam_order, cam_axes_erp)` → new per-cam weights

Caller flag: `stitch_one_frame(..., use_graphcut: bool = False)`.

---

## §2 Energy Function

Per pixel `(u, v)` in overlap region of pair `(a, b)`:
```
E(u,v) = α * color_diff_L1 + β * grad_diff_L1 + γ * boundary_penalty
       (pre-normalized to ~[0,1])

color_diff = mean(|slab_a - slab_b|) / 255.0  over 3 channels
grad_diff  = |Sobel_a - Sobel_b| / max_grad
boundary_penalty = |circular_diff(u, midline_u)| / overlap_half_width
```

Start with `α=1.0, β=0.5, γ=0.1`. Color dominates in textureless regions (sky); gradient dominates near object edges (seam "snaps" to edges); boundary penalty prevents pathological wrap-around in identical sky.

---

## §3 Graph Construction (PyMaxflow + scipy fallback)

**Library**: `PyMaxflow` (pip-installable on Windows, has wheels py3.9-3.12 win_amd64). Fallback: `scipy.sparse.csgraph.maximum_flow`.

Graph built **only on overlap bbox** (not full ERP) — keeps it small (~200×400 per pair):
- Each overlap pixel = node
- 4-connectivity edges, weight = `E(p) + E(q)`
- Source = "only cam-a" region (alpha_a AND NOT alpha_b)
- Sink = "only cam-b" region (alpha_b AND NOT alpha_a)
- T-edges: INF to source for source-region pixels, INF to sink for sink-region pixels

Min-cut → binary label per pixel → assignment mask.

---

## §4 Integration (multiband-compatible)

```python
def stitch_one_frame(..., use_graphcut: bool = False):
    slabs, alphas, weights = [], [], []
    for cam in RING_CAMS_7:
        rgb, alpha, w = render_camera_to_erp(...)
        slabs.append(rgb); alphas.append(alpha); weights.append(w)
    if use_graphcut:
        weights = apply_graphcut_seams(slabs, alphas, weights,
                                        RING_CAMS_7, cam_axes_erp)
    return multiband_blend(slabs, weights, num_bands=5, wrap=True)
```

Light Gaussian blur (5×5) on output weights to give multiband's lowest band a smooth seed.

---

## §5 Cam-Pair Adjacency (ERP-sorted)

Verified RING_CAMS_7 order: `front_center, front_left, side_left, rear_left, rear_right, side_right, front_right`.

ERP adjacency (sort by `cam_axes_erp` column, wrap):
- front_center ↔ front_left (large overlap)
- front_left ↔ side_left (medium)
- side_left ↔ rear_left (medium)
- rear_left ↔ rear_right (large — rear stereo)
- rear_right ↔ side_right (medium)
- side_right ↔ front_right (medium)
- front_right ↔ front_center (large)

For pairs with overlap < 0.5% → skip (use cos² weights). For 3+-cam pixels at zenith corners → fall back to argmax cos² (not worth multi-label α-expansion).

---

## §6 Implementation Order (3 steps with PNGs)

**Step 1 (~3 h)**: `compute_pair_overlap_energy` + viz. Deliverable: `outputs/graphcut/anchor60_front_center_front_right_energy.png` (3-panel: slab_a, slab_b, energy heatmap).

**Step 2 (~4 h)**: `find_optimal_seam` + `apply_graphcut_seams`. Deliverable: `outputs/graphcut/anchor60_seam_compare.png` (L1 vs graphcut, seam overlay highlight).

**Step 3 (~3 h)**: integration + cycle eval. Deliverable: `outputs/graphcut/cycle_psnr_comparison.csv` (per-anchor PSNR L1 vs graphcut).

---

## §7 Expected Outcome (Honest)

- Cycle-PSNR Δ likely **+0.0 to +0.15 dB** (multiband already hides seams in low-frequency bands; graph-cut mostly helps highest band's sharp edges)
- **Visual seam visibility drops substantially in object-dense regions** — paper-figure value, not metric win
- Paper Section 5: "Graph-cut seam vs fixed midline: cycle-PSNR Δ < 0.15 dB (statistically insignificant n=10) but qualitatively eliminates vertical seams cutting through buildings/vehicles"

---

## §8 Risks + Fallback

- PyMaxflow Windows install fails → scipy.csgraph.maximum_flow (3-5× slower but pure scipy)
- OOM at 1024×2048 → bbox crop already to ~200×400 per pair; if tight, downsample 2× before mincut
- Triple-overlap corners → argmax cos² fallback (not worth multi-label expansion)
- Per-frame cost: ~0.5 s on CPU (7 pairs × ~70 ms) → 100 anchors × 7 frames = 6 min, acceptable

---

## §9 Handoff to gp Implementer

**Files to read first**:
- `code/waymo2panorama/blending/multiband.py` (confirms arbitrary weights accepted, lines 99-100)
- `code/waymo2panorama/projection/sphere_projection.py` (lines 69-73 ERP-column from cam axis math; keep `alpha` mask, currently discarded in stitch_frame.py:31)
- `code/waymo2panorama/pipeline/stitch_frame.py` (extend with `use_graphcut` flag, plumb `alphas` list)
- `scripts/run_l1_baseline.py` (add `--use-graphcut` CLI flag)
- `scripts/phase3/batch_eval_cycle.py` (cycle-PSNR sweep pattern, reuse for step 3)

**Outputs**:
- `code/waymo2panorama/blending/graphcut_seam.py` (~250 lines)
- `outputs/graphcut/anchor60_*_energy.png`, `anchor60_seam_compare.png`, `cycle_psnr_comparison.csv`
- `deliverables/images/route_graphcut_seam_compare.png` (paper-quality, copy of seam_compare)
- Updated `deliverables/handoff_to_koi_v6.md` route 11 section
