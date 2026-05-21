# 新-C IPM Multi-Region Prior Extension — Implementation Design

**Plan agent**: a87fd4b4 (2026-05-21)
**Target**: `code/waymo2panorama/projection/ipm_multi_region.py`
**Reference**: `ipm_ground.py` (T14, +0.05 dB on 10-anchor), `sphere_projection.py` (L1)
**Goal**: 3 priors (ground / sky / building), target +0.5 dB on 10-anchor

---

## §1 Module Architecture

`ipm_multi_region.py` **composes** ipm_ground.py rather than subclassing — keeps T14 untouched and reusable. Public API:

```python
def segment_regions_from_pi3(
    local_points_cam: np.ndarray,        # (H, W, 3) cam-frame XYZ from Pi3
    T_ego_cam: np.ndarray,               # (4, 4)
    conf: np.ndarray | None = None,      # (H, W) Pi3 log-conf
    *,
    ground_z_thresh_m: float = 0.30,
    ground_normal_z_min: float = 0.85,
    sky_conf_thresh: float = -2.0,       # log scale; Pi3 conf < ~-2 is junk
    sky_depth_min_m: float = 30.0,
    sky_ego_z_min_m: float = 5.0,
    building_normal_z_max: float = 0.30,
    building_min_height_m: float = 1.0,
    normal_window: int = 5,
) -> RegionMasks: ...                     # dataclass: ground/sky/building/unknown bool masks

def estimate_normals_from_points(
    local_points_cam: np.ndarray,
    T_ego_cam: np.ndarray,
    window: int = 5,
) -> np.ndarray: ...                      # (H, W, 3) ego-frame unit normals

def ipm_project_sky(image, K, T_ego_cam, sky_mask, erp_hw) -> ...        # wraps sphere
def ipm_project_building(image, K, T_ego_cam, local_points_cam, ...) -> ... # NEW RANSAC plane fit
def ipm_project_multi_region(image, K, T_ego_cam, local_points_cam, ...) -> MultiRegionSlab
```

**Reuse**: `ipm_project_ground` (T14) called verbatim. Sky is sphere-equivalent routing (no math change). **Only Building is genuinely new code**.

---

## §2 Region Segmentation Decision Tree

First-match-wins, priority order:

| Region | Predicate (all conjuncts must hold) |
|---|---|
| **ground** | `forward_ok` (cam-z > 1.0 m) **AND** `|ego_z| ≤ 0.30 m` **AND** `n_ego_z ≥ 0.85` **AND** `radius ≤ 60 m` |
| **sky** | `conf < -2.0` (log) **OR** (`cam_z > 30 m` AND `ego_z > 5 m` AND `v_img < 0.4*H`) |
| **building** | `forward_ok` AND `ego_z > 0.5 m` AND `|n_ego_z| ≤ 0.30` (vertical normal) AND `sqrt(n_x²+n_y²) ≥ 0.85` AND `radius ≤ 80 m` AND ¬ground |
| **unknown** | fall back to L1 sphere |

**Pi3 conf range** (verified on anchor_060 front_center): roughly `[-14, +1]`. `conf < -2` ≈ texture-less / sky / lens-flare.

**Normal estimation** (no external normal map, must derive from local_points):
```python
P = T_ego_cam @ homogenize(local_points)   # (H, W, 3) ego frame
dx = P[:, 2:] - P[:, :-2]                  # ∂P/∂u
dy = P[2:, :] - P[:-2, :]                  # ∂P/∂v
n = cross(dx[1:-1, :], dy[:, 1:-1])
n /= |n|                                   # unit
# Plus cv2.boxFilter 5×5 smoothing per component
# Plus nan where neighbor depth nan OR distance > 2.0 m (depth discontinuity)
```

---

## §3 Per-Region Projection Math

**Ground**: call `ipm_project_ground(...)` unchanged from T14.
**Sky**: call `render_camera_to_erp(image_masked, K, T_ego_cam, erp_hw)` with non-sky pixels zeroed. Tag alpha with "sky" channel.
**Building** (new):
1. Tile image into 32×32 windows over `building_mask`
2. Per tile, gather ego-frame 3D points where mask True + conf ≥ -2. If < 0.4*32*32=410 valid → skip
3. RANSAC vertical-plane fit: `n_x x + n_y y = d`, `n_z = 0`, `||n_xy||=1`. 50 iters, threshold 0.20 m
4. Reject if inlier frac < 0.40 OR neighboring tile normal differs > 30°. Merge via union-find (Δd < 0.5 m, Δθ < 10°)
5. Per inlier pixel: cam ray → ego direction → intersect plane: `t = (d - n_x*t_x - n_y*t_y) / (n_x*d_x + n_y*d_y)`. Reject t ≤ 0 OR t > 80 m
6. Intersection point p = cam_origin + t * d_ego
7. ERP projection from panorama_center (0, 0, 1.5 m) via `_erp_uv_from_dir_ego` (reuse from ipm_ground)
8. Splat with weight `exp(-t/40)` + forward-warp + dilation (same as T14 lines 285-322)

Non-inlier "building" pixels → `unknown` → L1.

---

## §4 Multi-Region Blending (Hard + Feathered)

Replace merge in `run_ipm_hybrid.py` lines 189-197:
```python
merged_rgb = sphere_rgb.copy()                          # base = L1 everywhere
merged_weight = sphere_weight.copy()
merged_rgb[building_alpha] = building_rgb[building_alpha]
merged_weight[building_alpha] = building_weight[building_alpha] * 1.5 + 0.3
merged_rgb[ground_alpha] = ground_rgb[ground_alpha]
merged_weight[ground_alpha] = ground_weight[ground_alpha] * 2.0 + 0.5
# Sky stays = sphere
```

3-pixel Gaussian blur on **weight** at binary edges (NOT rgb) for soft transitions through multi-band blending.

---

## §5 Implementation Order (3 Steps, Each With Visual)

**Step 1 — Segmentation only** (~3 h):
- Implement `estimate_normals_from_points` + `segment_regions_from_pi3`
- New script `scripts/phase3/dump_region_masks.py` on anchor_060
- Deliverable: `outputs/phase3/p3.3_multi_region/anchor_060/region_mask_<cam>.png` — 4-color overlay per cam (green=ground, blue=sky, red=building, gray=unknown)
- Acceptance: ground ≈ T14 ±2%, building >5% on front-left/right, sky >10% on side cams

**Step 2 — Per-region projection isolated** (~4 h):
- Implement `ipm_project_building` RANSAC tile fit
- New script `scripts/phase3/run_ipm_multi_region.py` (clone-extend run_ipm_hybrid.py)
- Deliverable: per-region ERP slabs `ground_only.png`, `sky_only.png`, `building_only.png` + composite
- Acceptance: building slab shows aligned facade texture across seams

**Step 3 — Full pipeline + cycle eval** (~3 h):
- Wire §4 merge, run `eval_ipm_multi_region_cycle.py` on 10 anchors
- Deliverable: `compare_L1_vs_T14_vs_newC.png` 3-way panel + `agg_10anchors.json`
- Acceptance: cycle-PSNR ≥ T14 (no regression); target +0.3 dB over T14

---

## §6 Expected Numbers + Failure Modes

| Component | Expected contribution (10-anchor mean) |
|---|---|
| Ground (T14 carry-over) | +0.05 dB (measured) |
| Sky tagging (no math change) | +0.00 to +0.05 dB |
| **Building plane IPM (new)** | **+0.15 to +0.40 dB** (the big unknown) |
| **Total target** | **+0.20 to +0.50 dB** vs L1 |

**Failures**:
- A. Facade over-segmentation → mitigation: min inlier 0.40, plane merging, max distance 80 m, neighborhood normal sanity (>30° rejected)
- B. Pi3 normals too noisy at 504×504 → mitigation: bilateral filter pre-depth, fall back to 9×9 normal window
- C. Sloped facades / awnings → mitigation: fall to `unknown` → L1 when normal-z > 0.30

**Hard floor**: if any anchor regresses > 0.5 dB from L1, ablate building layer → ship "ground + sky tagging" (still ≥ T14).

---

## §7 Implementation gp Handoff

**Files to read first**:
- `code/waymo2panorama/projection/ipm_ground.py` (reuse `_erp_uv_from_dir_ego`, `ipm_project_ground`)
- `code/waymo2panorama/projection/sphere_projection.py` (`render_camera_to_erp`)
- `scripts/phase3/run_ipm_hybrid.py` lines 76-262 (mirror scaffolding)

**Inputs from Pi3 cache (all exist on disk at `outputs/phase3/pi3_cache/anchor_060/`):**
- `image_<cam>.png` (504×504×3 uint8)
- `av2_K_letterboxed_<cam>.npy` (3×3)
- `av2_T_ego_cam_<cam>.npy` (4×4)
- `local_points_<cam>.npy` (504×504×3 float32, cam frame)
- `conf_<cam>.npy` (504×504 float32, log-conf)
- **Verified**: no normal map file → must estimate from local_points

**Outputs the gp subagent must produce**:
- `code/waymo2panorama/projection/ipm_multi_region.py` (~500 lines new)
- `scripts/phase3/run_ipm_multi_region.py` (clone-extend run_ipm_hybrid.py)
- `scripts/phase3/eval_ipm_multi_region_cycle.py` (clone of eval_ipm_hybrid_cycle.py)
- 3 deliverable PNGs per step
- `summary.json` with `per_cam.region_coverage`, `per_cam.building_plane_count`, `per_cam.building_inlier_frac`

**Sequencing**: step 1 fully done + Koi visual sanity check before step 2. Step 2 building RANSAC behind `--enable-building` flag default-off for cheap A/B ablation in step 3.
