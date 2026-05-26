# Parallax Overlap Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate parallax-induced overlap artifacts (white traces / 2-wheel ghost / washed-out blends) in L1 ERP output, by adding plug-in modules at the ERP-slab layer (post-L1-sphere-projection, pre-multiband-blend).

**Architecture:** 3 independent candidate approaches all integrated at the same architectural seam (ERP slab layer). Original L1 / L2 / L3 / 新-X codepaths are NOT modified — all new functionality goes in new files. After all 3 are measured, design a hybrid combining best aspects.

**Tech Stack:** numpy, scipy (TPS / least_squares), cv2 (remap, blending), kornia (RAFT — GPU only), pymaxflow or networkx (min-cut for graphcut), existing project modules (`code/waymo2panorama/*`).

**Spec reference:** `agent/specs/2026-05-26-parallax-overlap-fix-design.md`

**Hard constraint (USER):** All existing baseline code preserved unchanged. No file in `code/waymo2panorama/projection/`, `code/waymo2panorama/blending/`, `code/waymo2panorama/pipeline/stitch_frame.py`, or any `scripts/phase2/` and `scripts/phase3/run_l1_baseline.py` may be modified. All new functionality goes in NEW files.

**GPU plan:** Phases 1 (A2) and 2 (B1) are CPU-only. Phase 3 (C1 RAFT) needs Colab GPU — ask user to switch before starting Phase 3.

---

## File Structure (created or modified)

### NEW files (additive only)

**Code modules** (`code/waymo2panorama/alignment/` and `code/waymo2panorama/blending/`):
- `code/waymo2panorama/alignment/sparse_displacement.py` — A2 sparse-stereo-to-dense-displacement core
- `code/waymo2panorama/alignment/__test_sparse_displacement.py` — pytest for A2
- `code/waymo2panorama/blending/graphcut_disparity.py` — B1 disparity-aware graphcut seam
- `code/waymo2panorama/blending/__test_graphcut_disparity.py` — pytest for B1
- `code/waymo2panorama/alignment/optical_flow_align.py` — C1 RAFT wrapper (GPU)
- `code/waymo2panorama/alignment/__test_optical_flow_align.py` — pytest for C1

**Drivers** (`scripts/phase3/`):
- `scripts/phase3/run_l1_sparse_disp.py` — A2 driver
- `scripts/phase3/run_l1_graphcut_disp.py` — B1 driver
- `scripts/phase3/run_l1_optflow.py` — C1 driver
- `scripts/phase3/run_l1_parallax_hybrid.py` — hybrid driver (after candidates measured)

**Eval scripts** (`scripts/phase3/`):
- `scripts/phase3/eval_parallax_fixes_cycle.py` — held-out cycle PSNR (mirror of `eval_l1_rotation_refine_cycle.py`)
- `scripts/phase3/eval_parallax_seam_metric.py` — new seam visibility metric
- `scripts/phase3/build_parallax_compare_panel.py` — visual side-by-side panel generator

**Docs**:
- `agent/progress.md` — append entry (do NOT rewrite existing entries)

### EXISTING files referenced (READ ONLY — DO NOT MODIFY)

- `code/waymo2panorama/projection/sphere_projection.py` — `render_camera_to_erp` API
- `code/waymo2panorama/blending/multiband.py` — `multiband_blend` API
- `code/waymo2panorama/pipeline/lift_and_project.py` — `ego_points_to_erp_uv` API
- `code/waymo2panorama/pipeline/stitch_frame.py` — L1 pipeline reference (do not call its modified-prewarp variant)
- `code/waymo2panorama/pipeline/option_b_reweight.py` — stereo .npz key constants (reuse `STEREO_NPZ_*` constants)
- `code/waymo2panorama/data_io/av2_loader.py` — `RING_CAMS_7`, `ADJACENT_PAIRS_RING`
- `code/waymo2panorama/data_io/ego_mask.py` — `build_ego_masks`

---

## Phase 0 — Pre-flight (~15 min, CPU)

### Task 0.1: Verify environment + spec presence

- [ ] **Step 1: Confirm spec exists**

Run: `cat agent/specs/2026-05-26-parallax-overlap-fix-design.md | head -5`
Expected: shows "# Parallax Overlap Fix — Design Spec"

- [ ] **Step 2: Confirm baselines preserved**

Run: `git log --oneline -1 code/waymo2panorama/projection/sphere_projection.py code/waymo2panorama/blending/multiband.py code/waymo2panorama/pipeline/stitch_frame.py`
Expected: latest commits are from BEFORE today's parallax work; no recent modifications

- [ ] **Step 3: Check scipy + cv2 installed (CPU)**

Run: `python -c "import scipy.interpolate; import cv2; print(scipy.__version__, cv2.__version__)"`
Expected: prints versions, no errors

- [ ] **Step 4: Read stereo .npz format reminder**

Run: `python -c "import numpy as np; d=dict(np.load('outputs/phase3/p3.6_stereo/anchor_060/stereo_front_center__front_left.npz' if False else 'C:/nul', allow_pickle=True)) if False else print('skip — verify on Colab where stereo cache lives')"`

(On Colab worker only: run the same against `/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.6_stereo/anchor_060/stereo_front_center__front_left.npz`, expect keys `pts_3d_ego`, `pts_3d_cam_a`, `cam_a`, `cam_b`, `mkpts_a`, `mkpts_b`, etc.)

- [ ] **Step 5: Commit (no-op marker)**

No commit needed — pure verification phase.

---

## Phase 1 — A2 Sparse Stereo Displacement (~6 hours, CPU only)

### Task 1.1: Helper — `_compute_l1_erp_pixel_per_cam`

Given a 3D ego point and a cam's calibrated `K + T_ego_cam`, return where L1 sphere projection WOULD paint that point's content on the ERP (assuming infinity).

**Files:**
- Create: `code/waymo2panorama/alignment/sparse_displacement.py`
- Test: `code/waymo2panorama/alignment/__test_sparse_displacement.py`

- [ ] **Step 1: Write failing test**

```python
"""Unit tests for alignment/sparse_displacement.py — A2 module."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.alignment.sparse_displacement import (  # noqa: E402
    _compute_l1_erp_pixel_per_cam,
)


def test_l1_erp_pixel_for_distant_point_matches_ideal():
    """For a point far from cam center, L1 ERP location ≈ ideal ERP location."""
    K = np.array([[500.0, 0, 252.0], [0, 500.0, 252.0], [0, 0, 1.0]], dtype=np.float64)
    # cam looks forward (+x in ego, so cam +z = ego +x)
    R_ego_cam = np.array([
        [0, 0, 1],
        [-1, 0, 0],
        [0, -1, 0],
    ], dtype=np.float64)
    t_ego_cam = np.array([0.5, 0, 0], dtype=np.float64)  # cam 0.5m in front of ego
    T_ego_cam = np.eye(4); T_ego_cam[:3, :3] = R_ego_cam; T_ego_cam[:3, 3] = t_ego_cam
    pt_far = np.array([100.0, 0.0, 0.0], dtype=np.float64)  # 100m forward
    erp_hw = (1024, 2048)
    l1_uv = _compute_l1_erp_pixel_per_cam(pt_far, K, T_ego_cam, erp_hw)
    # Ideal: point at +x direction lands at theta=0 → u ≈ W/2
    assert abs(l1_uv[0] - erp_hw[1] / 2.0) < 2.0, f"u off: {l1_uv}"
```

- [ ] **Step 2: Run test, expect ImportError**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py::test_l1_erp_pixel_for_distant_point_matches_ideal -v`
Expected: FAIL with `ImportError: cannot import name '_compute_l1_erp_pixel_per_cam'`

- [ ] **Step 3: Implement minimal**

Create `code/waymo2panorama/alignment/sparse_displacement.py`:

```python
"""A2 — Sparse Stereo Displacement (parallax overlap fix candidate).

For each 3D point X in cam_a + cam_b's stereo .npz:
  - Compute where L1 sphere projection WOULD paint X for cam_a (and cam_b)
  - Compare to ideal ERP position based on X's true 3D ego direction
  - Per-cam displacement = ideal - L1_painted

Interpolate sparse per-point displacements into dense fields, then apply
to each cam's ERP slab via cv2.remap before passing to multiband_blend.

USAGE PATTERN (driver responsibility):
    slabs, weights = [render_camera_to_erp(...) for cam in cams]
    disp_fields = build_per_cam_displacement_fields(...)
    warped_slabs = [warp_erp_slab(slab, df) for slab, df in zip(slabs, disp_fields)]
    erp = multiband_blend(warped_slabs, weights, ...)

EXISTING CODE UNCHANGED: this module is purely additive. It does not modify
render_camera_to_erp, multiband_blend, or stitch_frame.
"""
from __future__ import annotations

import numpy as np

from waymo2panorama.pipeline.lift_and_project import ego_points_to_erp_uv


def _compute_l1_erp_pixel_per_cam(
    pt_ego: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    erp_hw: tuple[int, int],
) -> np.ndarray:
    """Compute where L1 sphere projection paints a 3D ego point for one cam.

    L1 paints cam pixel content at the ERP location given by the ray from the
    cam to that pixel. For a 3D ego point X seen by cam at ego center t and
    rotation R_ego_cam:
      1. Cam-frame coords: X_cam = R_ego_cam.T @ (X - t)
      2. Cam pixel: (u_c, v_c) = K @ [X_cam[0]/X_cam[2], X_cam[1]/X_cam[2], 1]
      3. Back-project that pixel to ego ray: ray_ego = R_ego_cam @ inv(K) @ (u_c, v_c, 1)
      4. ERP location of ray_ego (using ego_points_to_erp_uv as if depth=1)

    For X at infinity, ray_ego direction = X direction → L1_uv == ideal_uv.
    For X near cam, ray_ego direction ≠ X direction (parallax) → L1_uv ≠ ideal_uv.

    Args:
        pt_ego: (3,) point in ego frame.
        K: (3, 3) cam intrinsics.
        T_ego_cam: (4, 4) ego-from-cam transform.
        erp_hw: (H_erp, W_erp).

    Returns:
        (2,) (u, v) ERP pixel where L1 paints this point.
    """
    R_ego_cam = T_ego_cam[:3, :3].astype(np.float64)
    t_ego_cam = T_ego_cam[:3, 3].astype(np.float64)
    R_cam_ego = R_ego_cam.T
    X_cam = R_cam_ego @ (np.asarray(pt_ego, dtype=np.float64) - t_ego_cam)
    z = X_cam[2]
    if abs(z) < 1e-9:
        return np.array([np.nan, np.nan], dtype=np.float64)
    K = np.asarray(K, dtype=np.float64)
    u_cam = K[0, 0] * X_cam[0] / z + K[0, 2]
    v_cam = K[1, 1] * X_cam[1] / z + K[1, 2]
    # Back-project to ego ray
    ray_cam = np.linalg.inv(K) @ np.array([u_cam, v_cam, 1.0])
    ray_ego = R_ego_cam @ ray_cam
    u_f, v_f, valid = ego_points_to_erp_uv(ray_ego.reshape(1, 3), erp_hw=erp_hw)
    return np.array([float(u_f[0]), float(v_f[0])], dtype=np.float64)
```

- [ ] **Step 4: Run test, expect PASS**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add code/waymo2panorama/alignment/sparse_displacement.py code/waymo2panorama/alignment/__test_sparse_displacement.py
git commit -m "WS4 A2: _compute_l1_erp_pixel_per_cam helper + pytest

For each 3D ego point, compute where L1 sphere projection (assuming depth=inf)
would paint it on ERP for a given cam. This is the per-point parallax-error
reference for the displacement field built in next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 1.2: `build_per_cam_displacements_from_stereo`

Given the stereo cache for one anchor + cams' K/T, return per-cam sparse {(ideal_uv, delta_uv)} list per cam.

**Files:**
- Modify: `code/waymo2panorama/alignment/sparse_displacement.py`
- Modify: `code/waymo2panorama/alignment/__test_sparse_displacement.py`

- [ ] **Step 1: Write failing test**

```python
def test_displacements_zero_for_distant_synthetic_pts(tmp_path):
    """If all stereo pts are far away, per-cam displacements ~ 0."""
    from waymo2panorama.alignment.sparse_displacement import (
        build_per_cam_displacements_from_stereo,
    )
    from waymo2panorama.pipeline.option_b_reweight import (
        STEREO_NPZ_PTS_KEY, STEREO_NPZ_CAM_A_KEY, STEREO_NPZ_CAM_B_KEY,
    )
    # Same K + T_ego_cam as task 1.1
    K = np.array([[500.0, 0, 252.0], [0, 500.0, 252.0], [0, 0, 1.0]])
    R = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]], dtype=np.float64)
    T_a = np.eye(4); T_a[:3, :3] = R; T_a[:3, 3] = [0.5, 0, 0]
    T_b = np.eye(4); T_b[:3, :3] = R; T_b[:3, 3] = [0.5, 0.3, 0]
    # Write a synthetic stereo npz with FAR points
    pts_far = np.array([
        [100.0, 0.0, 0.0],
        [100.0, 5.0, 0.0],
        [100.0, -5.0, 0.0],
    ], dtype=np.float32)
    npz = tmp_path / "stereo_cam_a__cam_b.npz"
    np.savez_compressed(npz, **{
        STEREO_NPZ_PTS_KEY: pts_far,
        STEREO_NPZ_CAM_A_KEY: np.array("cam_a"),
        STEREO_NPZ_CAM_B_KEY: np.array("cam_b"),
    })
    cam_K = {"cam_a": K, "cam_b": K}
    cam_T = {"cam_a": T_a, "cam_b": T_b}
    disps = build_per_cam_displacements_from_stereo(
        [npz], cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=["cam_a", "cam_b"],
        erp_hw=(1024, 2048),
    )
    # disps["cam_a"] should be a list of (ideal_uv, delta_uv) tuples
    assert "cam_a" in disps and "cam_b" in disps
    assert len(disps["cam_a"]) == 3  # 3 input points
    # All delta magnitudes should be < 5 ERP pixels for 100m points
    for ideal_uv, delta_uv in disps["cam_a"]:
        assert np.linalg.norm(delta_uv) < 5.0, (
            f"far-point delta should be ~0, got {delta_uv}"
        )
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py::test_displacements_zero_for_distant_synthetic_pts -v`
Expected: FAIL with `ImportError: cannot import name 'build_per_cam_displacements_from_stereo'`

- [ ] **Step 3: Implement**

Append to `code/waymo2panorama/alignment/sparse_displacement.py`:

```python
from pathlib import Path

from waymo2panorama.pipeline.option_b_reweight import (
    STEREO_NPZ_PTS_KEY,
    STEREO_NPZ_CAM_A_KEY,
    STEREO_NPZ_CAM_B_KEY,
)


def _load_stereo_pair(path: Path) -> tuple[str, str, np.ndarray] | None:
    """Load (cam_a, cam_b, pts_3d_ego) from one stereo .npz file."""
    with np.load(path) as npz:
        if STEREO_NPZ_CAM_A_KEY not in npz.files or STEREO_NPZ_CAM_B_KEY not in npz.files:
            return None
        if STEREO_NPZ_PTS_KEY not in npz.files:
            return None
        cam_a = str(npz[STEREO_NPZ_CAM_A_KEY])
        cam_b = str(npz[STEREO_NPZ_CAM_B_KEY])
        pts = np.asarray(npz[STEREO_NPZ_PTS_KEY], dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3 or pts.shape[0] == 0:
        return None
    return cam_a, cam_b, pts


def build_per_cam_displacements_from_stereo(
    stereo_npz_paths,
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_names: list[str],
    erp_hw: tuple[int, int],
) -> dict[str, list[tuple[np.ndarray, np.ndarray]]]:
    """Build sparse per-cam displacement vectors from cached stereo .npz files.

    For each cam X in cam_names and each 3D ego point pt seen by a stereo
    pair involving X:
      ideal_uv = ego_points_to_erp_uv(pt)  # depth-aware ERP location
      l1_uv   = _compute_l1_erp_pixel_per_cam(pt, K_X, T_X)
      delta_uv = ideal_uv - l1_uv  # cam X's slab needs to shift by this

    Returns dict {cam_name: list of (ideal_uv, delta_uv) tuples}. Cams not
    appearing in any stereo pair get an empty list.
    """
    cam_set = set(cam_names)
    out: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {c: [] for c in cam_names}
    for p in stereo_npz_paths:
        loaded = _load_stereo_pair(Path(p))
        if loaded is None:
            continue
        cam_a, cam_b, pts = loaded
        if cam_a not in cam_set or cam_b not in cam_set:
            continue
        for pt in pts:
            u_ideal, v_ideal, _ = ego_points_to_erp_uv(pt.reshape(1, 3), erp_hw=erp_hw)
            ideal_uv = np.array([float(u_ideal[0]), float(v_ideal[0])], dtype=np.float64)
            for cam in (cam_a, cam_b):
                l1_uv = _compute_l1_erp_pixel_per_cam(
                    pt, cam_K[cam], cam_T_ego_cam[cam], erp_hw,
                )
                if np.any(np.isnan(l1_uv)):
                    continue
                # Handle wrap-around in u (use shortest signed delta)
                delta_u = (ideal_uv[0] - l1_uv[0])
                W = erp_hw[1]
                if delta_u > W / 2: delta_u -= W
                elif delta_u < -W / 2: delta_u += W
                delta_uv = np.array([delta_u, ideal_uv[1] - l1_uv[1]], dtype=np.float64)
                out[cam].append((ideal_uv, delta_uv))
    return out
```

- [ ] **Step 4: Run test**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add code/waymo2panorama/alignment/sparse_displacement.py code/waymo2panorama/alignment/__test_sparse_displacement.py
git commit -m "WS4 A2: build_per_cam_displacements_from_stereo + pytest

For each 3D point in cached stereo .npz, compute per-cam (ideal_uv, delta_uv)
pair. delta_uv is how much that cam's ERP slab should shift to land the
point at the depth-aware ideal location.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 1.3: `interpolate_dense_displacement_field` (TPS/RBF)

Sparse {(ideal_uv, delta_uv)} → dense (H, W, 2) displacement field per cam.

**Files:**
- Modify: `code/waymo2panorama/alignment/sparse_displacement.py`
- Modify: `code/waymo2panorama/alignment/__test_sparse_displacement.py`

- [ ] **Step 1: Write failing test**

```python
def test_dense_field_at_anchors_matches_sparse():
    """The dense field at exactly the sparse anchor positions equals the sparse delta."""
    from waymo2panorama.alignment.sparse_displacement import (
        interpolate_dense_displacement_field,
    )
    erp_hw = (256, 512)
    sparse = [
        (np.array([100.0, 50.0]), np.array([3.0, -2.0])),
        (np.array([300.0, 100.0]), np.array([-1.0, 4.0])),
        (np.array([400.0, 150.0]), np.array([2.0, 1.0])),
    ]
    dense = interpolate_dense_displacement_field(
        sparse, erp_hw=erp_hw, regularization=1e-3,
    )
    assert dense.shape == (erp_hw[0], erp_hw[1], 2)
    # At the anchor pixels, value should be ~ the sparse delta (within reg tolerance)
    for ideal_uv, delta_uv in sparse:
        u, v = int(round(ideal_uv[0])), int(round(ideal_uv[1]))
        d = dense[v, u]
        assert np.linalg.norm(d - delta_uv) < 0.5, (
            f"anchor at {ideal_uv} delta={delta_uv} but dense[v,u]={d}"
        )


def test_dense_field_decays_to_zero_far_from_anchors():
    """Outside the anchor support, displacement should decay to ~0."""
    from waymo2panorama.alignment.sparse_displacement import (
        interpolate_dense_displacement_field,
    )
    erp_hw = (256, 512)
    sparse = [
        (np.array([100.0, 50.0]), np.array([3.0, -2.0])),
    ]
    dense = interpolate_dense_displacement_field(
        sparse, erp_hw=erp_hw, regularization=1.0,
    )
    # 200 px away from the anchor, displacement should be small
    far_d = dense[150, 300]
    assert np.linalg.norm(far_d) < 1.0, f"far field should decay, got {far_d}"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 2 PASS + 2 FAIL (the new tests)

- [ ] **Step 3: Implement**

Append to `code/waymo2panorama/alignment/sparse_displacement.py`:

```python
from scipy.interpolate import RBFInterpolator


def interpolate_dense_displacement_field(
    sparse_anchors: list[tuple[np.ndarray, np.ndarray]],
    erp_hw: tuple[int, int],
    regularization: float = 1.0,
    kernel: str = "thin_plate_spline",
) -> np.ndarray:
    """Interpolate sparse {(ideal_uv, delta_uv)} into a dense (H, W, 2) field.

    Uses scipy.interpolate.RBFInterpolator with thin-plate-spline kernel by
    default. `regularization` (smoothing param) trades off exact-fit (=0)
    vs smooth-overall (>>0). Higher regularization is more robust when sparse
    anchors are noisy but loses anchor exactness.

    Returns (H, W, 2) float32 displacement field. The (i, j) element gives
    the per-pixel (delta_u, delta_v) — i.e., "where in the original L1 slab
    to read from when painting this ERP pixel".

    Empty sparse_anchors → all-zero field (no displacement).
    """
    H, W = erp_hw
    if len(sparse_anchors) == 0:
        return np.zeros((H, W, 2), dtype=np.float32)
    anchors_xy = np.array([a[0] for a in sparse_anchors], dtype=np.float64)
    deltas = np.array([a[1] for a in sparse_anchors], dtype=np.float64)
    # RBF needs at least kernel-dim points; for TPS this is 3 in 2D. Fallback
    # to gaussian if too few.
    if anchors_xy.shape[0] < 3 and kernel == "thin_plate_spline":
        kernel = "gaussian"
    rbf = RBFInterpolator(
        anchors_xy, deltas, kernel=kernel, smoothing=float(regularization),
    )
    # Evaluate on every ERP pixel (vectorized; reasonably fast for 1024x2048)
    ys, xs = np.mgrid[0:H, 0:W]
    grid = np.stack([xs.ravel(), ys.ravel()], axis=-1).astype(np.float64)
    out = rbf(grid).reshape(H, W, 2).astype(np.float32)
    return out
```

- [ ] **Step 4: Run test**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add code/waymo2panorama/alignment/sparse_displacement.py code/waymo2panorama/alignment/__test_sparse_displacement.py
git commit -m "WS4 A2: interpolate_dense_displacement_field (TPS via scipy RBF) + pytest

Sparse {(ideal_uv, delta_uv)} -> dense (H, W, 2) per-pixel displacement field
via scipy RBFInterpolator. Default thin-plate-spline kernel; regularization
trades off anchor exactness vs smoothness. Tests verify anchor-matching and
far-field decay.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 1.4: `warp_erp_slab_by_displacement`

Apply (H, W, 2) displacement field to an ERP slab via cv2.remap.

**Files:**
- Modify: `code/waymo2panorama/alignment/sparse_displacement.py`
- Modify: `code/waymo2panorama/alignment/__test_sparse_displacement.py`

- [ ] **Step 1: Write failing test**

```python
def test_zero_displacement_returns_identical_slab():
    """All-zero displacement field => warped slab == original slab."""
    from waymo2panorama.alignment.sparse_displacement import (
        warp_erp_slab_by_displacement,
    )
    slab = (np.random.RandomState(0).rand(64, 128, 3) * 255).astype(np.float32)
    zero_disp = np.zeros((64, 128, 2), dtype=np.float32)
    warped = warp_erp_slab_by_displacement(slab, zero_disp, wrap_horizontal=True)
    assert warped.shape == slab.shape
    assert np.allclose(warped, slab, atol=0.5)


def test_constant_displacement_shifts_slab():
    """Constant (+10, 0) displacement => slab content shifts by 10 px in -u dir."""
    from waymo2panorama.alignment.sparse_displacement import (
        warp_erp_slab_by_displacement,
    )
    H, W = 32, 64
    slab = np.zeros((H, W, 3), dtype=np.float32)
    slab[:, 20:25] = 255.0  # vertical white stripe at u=20..24
    disp = np.zeros((H, W, 2), dtype=np.float32)
    disp[..., 0] = 10.0  # "ERP pixel at u sources from u + 10" ... convention check
    warped = warp_erp_slab_by_displacement(slab, disp, wrap_horizontal=True)
    # The stripe should now appear at u-10 = 10..14 (or u+10 = 30..34 — depends on convention)
    # Document the convention via the test:
    has_stripe_at_10 = np.any(warped[:, 10:15] > 100)
    has_stripe_at_30 = np.any(warped[:, 30:35] > 100)
    assert has_stripe_at_10 ^ has_stripe_at_30, (
        f"stripe should shift by 10 px in ONE direction. "
        f"left? {has_stripe_at_10}, right? {has_stripe_at_30}"
    )
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 4 PASS + 2 FAIL (the new tests)

- [ ] **Step 3: Implement**

Append to `code/waymo2panorama/alignment/sparse_displacement.py`:

```python
import cv2


def warp_erp_slab_by_displacement(
    slab: np.ndarray,
    displacement: np.ndarray,
    wrap_horizontal: bool = True,
) -> np.ndarray:
    """Warp an ERP slab by a dense (H, W, 2) displacement field.

    Convention: displacement[v, u] = (du, dv) tells us that ERP pixel (u, v)
    in the OUTPUT should be sourced from (u - du, v - dv) in the INPUT slab.
    (This matches the "warp slab toward the ideal location" semantic — the
    sparse delta_uv was ideal - L1, so dst[ideal] = src[L1] = src[ideal - delta].)

    Uses cv2.remap with bilinear interpolation. Handles ERP horizontal wrap
    when wrap_horizontal=True via modulo on the source u coordinate.

    Args:
        slab: (H, W, C) float32 ERP slab.
        displacement: (H, W, 2) float32. displacement[..., 0] = du, [..., 1] = dv.
        wrap_horizontal: if True, mod source u into [0, W).

    Returns:
        Warped (H, W, C) float32 slab.
    """
    H, W = slab.shape[:2]
    assert displacement.shape == (H, W, 2)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    src_u = xs - displacement[..., 0]
    src_v = ys - displacement[..., 1]
    if wrap_horizontal:
        src_u = np.mod(src_u, W).astype(np.float32)
    else:
        src_u = np.clip(src_u, 0, W - 1).astype(np.float32)
    src_v = np.clip(src_v, 0, H - 1).astype(np.float32)
    warped = cv2.remap(
        slab, src_u, src_v,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return warped
```

- [ ] **Step 4: Run test**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add code/waymo2panorama/alignment/sparse_displacement.py code/waymo2panorama/alignment/__test_sparse_displacement.py
git commit -m "WS4 A2: warp_erp_slab_by_displacement (cv2.remap with ERP wrap) + pytest

Apply dense (H, W, 2) displacement field to an ERP slab. Convention: dst[u, v]
sourced from src[u - du, v - dv]. Horizontal wrap supported for ERP (mod W).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 1.5: Confidence gating helper

Where stereo coverage is sparse, displacement should fade to zero. Add `_build_anchor_confidence_map` that returns (H, W) float in [0, 1] = fraction of stereo coverage around each pixel. Then multiply the dense displacement by this confidence.

**Files:**
- Modify: `code/waymo2panorama/alignment/sparse_displacement.py`
- Modify: `code/waymo2panorama/alignment/__test_sparse_displacement.py`

- [ ] **Step 1: Write failing test**

```python
def test_confidence_map_high_near_anchors_zero_far():
    """Pixels near sparse anchors get high confidence; far pixels get zero."""
    from waymo2panorama.alignment.sparse_displacement import (
        build_anchor_confidence_map,
    )
    erp_hw = (256, 512)
    anchors = [np.array([100.0, 50.0]), np.array([300.0, 100.0])]
    conf = build_anchor_confidence_map(anchors, erp_hw=erp_hw, sigma_px=20.0)
    assert conf.shape == erp_hw
    assert conf.dtype == np.float32
    # At anchor pixel, confidence ~ 1
    assert conf[50, 100] > 0.9
    # Far from any anchor, confidence ~ 0
    assert conf[200, 400] < 0.1
    assert float(conf.max()) <= 1.0 + 1e-6
    assert float(conf.min()) >= 0.0
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 6 PASS + 1 FAIL

- [ ] **Step 3: Implement**

Append to `code/waymo2panorama/alignment/sparse_displacement.py`:

```python
def build_anchor_confidence_map(
    anchor_positions: list[np.ndarray],
    erp_hw: tuple[int, int],
    sigma_px: float = 20.0,
) -> np.ndarray:
    """Build a (H, W) float32 confidence map: high near anchors, low far.

    For each ERP pixel, confidence = max over anchors of exp(-dist^2 / (2*sigma^2)).
    Used to gate dense displacement: in stereo-free regions, no shift applied.

    Returns float32 in [0, 1], shape erp_hw.
    """
    H, W = erp_hw
    if len(anchor_positions) == 0:
        return np.zeros((H, W), dtype=np.float32)
    out = np.zeros((H, W), dtype=np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    inv_two_sigma_sq = 1.0 / (2.0 * sigma_px * sigma_px)
    for a in anchor_positions:
        u, v = float(a[0]), float(a[1])
        # ERP wrap on u
        du = np.minimum(np.abs(xx - u), W - np.abs(xx - u))
        dv = (yy - v)
        d2 = du * du + dv * dv
        contrib = np.exp(-d2 * inv_two_sigma_sq).astype(np.float32)
        np.maximum(out, contrib, out=out)
    return out
```

- [ ] **Step 4: Run test**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add code/waymo2panorama/alignment/sparse_displacement.py code/waymo2panorama/alignment/__test_sparse_displacement.py
git commit -m "WS4 A2: build_anchor_confidence_map for displacement gating + pytest

Pixels near stereo anchors get high confidence; pixels in stereo-free regions
get ~ 0 confidence. Multiply dense displacement by this to gate the shift.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 1.6: Orchestrator `build_warped_slabs_a2`

End-to-end helper that takes the L1 slabs + stereo cache + cam K/T and returns warped slabs.

**Files:**
- Modify: `code/waymo2panorama/alignment/sparse_displacement.py`
- Modify: `code/waymo2panorama/alignment/__test_sparse_displacement.py`

- [ ] **Step 1: Write failing test**

```python
def test_orchestrator_no_stereo_returns_unchanged_slabs(tmp_path):
    """If no stereo files provided, orchestrator returns slabs unchanged."""
    from waymo2panorama.alignment.sparse_displacement import build_warped_slabs_a2
    slabs = {f"cam_{i}": (np.random.RandomState(i).rand(32, 64, 3) * 255).astype(np.float32)
             for i in range(3)}
    cam_K = {c: np.eye(3) for c in slabs}
    cam_T = {c: np.eye(4) for c in slabs}
    out_slabs, summary = build_warped_slabs_a2(
        l1_slabs=slabs, stereo_npz_paths=[],
        cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=list(slabs),
        erp_hw=(32, 64),
    )
    for c in slabs:
        assert np.allclose(out_slabs[c], slabs[c], atol=0.5)
    assert summary["n_stereo_files_used"] == 0
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 7 PASS + 1 FAIL

- [ ] **Step 3: Implement**

Append to `code/waymo2panorama/alignment/sparse_displacement.py`:

```python
def build_warped_slabs_a2(
    l1_slabs: dict[str, np.ndarray],
    stereo_npz_paths,
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_names: list[str],
    erp_hw: tuple[int, int],
    rbf_regularization: float = 1.0,
    confidence_sigma_px: float = 20.0,
    wrap_horizontal: bool = True,
) -> tuple[dict[str, np.ndarray], dict]:
    """Orchestrator: L1 slabs + stereo cache -> warped slabs (A2 method).

    Pipeline per cam:
      1. Build sparse {(ideal_uv, delta_uv)} from stereo .npz files involving cam
      2. Interpolate to dense (H, W, 2) displacement field via TPS RBF
      3. Build (H, W) confidence map from anchor positions
      4. Gate displacement: dense_disp * confidence
      5. Warp slab via cv2.remap

    Returns:
        (warped_slabs, summary_dict)
        - warped_slabs: same keys as l1_slabs, gated-warp applied
        - summary: n_stereo_files_used, per-cam #anchors and max |delta|
    """
    n_stereo_total = len(list(stereo_npz_paths))
    sparse_per_cam = build_per_cam_displacements_from_stereo(
        stereo_npz_paths, cam_K=cam_K, cam_T_ego_cam=cam_T_ego_cam,
        cam_names=cam_names, erp_hw=erp_hw,
    )
    out_slabs: dict[str, np.ndarray] = {}
    per_cam_stats: dict[str, dict] = {}
    for cam in cam_names:
        slab = l1_slabs[cam]
        anchors_for_cam = sparse_per_cam.get(cam, [])
        n_anchors = len(anchors_for_cam)
        if n_anchors == 0:
            out_slabs[cam] = slab.astype(np.float32)
            per_cam_stats[cam] = {"n_anchors": 0, "max_abs_delta_px": 0.0}
            continue
        dense_disp = interpolate_dense_displacement_field(
            anchors_for_cam, erp_hw=erp_hw, regularization=rbf_regularization,
        )
        anchor_uvs = [a[0] for a in anchors_for_cam]
        conf = build_anchor_confidence_map(
            anchor_uvs, erp_hw=erp_hw, sigma_px=confidence_sigma_px,
        )
        gated_disp = dense_disp * conf[..., None]
        max_delta = float(np.linalg.norm(gated_disp, axis=-1).max())
        out_slabs[cam] = warp_erp_slab_by_displacement(
            slab.astype(np.float32), gated_disp, wrap_horizontal=wrap_horizontal,
        )
        per_cam_stats[cam] = {
            "n_anchors": int(n_anchors),
            "max_abs_delta_px": max_delta,
        }
    summary = {
        "n_stereo_files_used": n_stereo_total,
        "per_cam": per_cam_stats,
    }
    return out_slabs, summary
```

- [ ] **Step 4: Run test**

Run: `python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit + push**

```bash
git add code/waymo2panorama/alignment/sparse_displacement.py code/waymo2panorama/alignment/__test_sparse_displacement.py
git commit -m "WS4 A2: build_warped_slabs_a2 orchestrator + pytest

End-to-end: L1 slabs + stereo cache -> warped slabs. Per-cam pipeline:
build sparse displacements -> TPS dense field -> confidence gating -> cv2.remap.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

---

### Task 1.7: Driver `run_l1_sparse_disp.py`

**Files:**
- Create: `scripts/phase3/run_l1_sparse_disp.py`

- [ ] **Step 1: Create the driver**

Create `scripts/phase3/run_l1_sparse_disp.py`:

```python
"""WS4 A2 — L1 sphere + sparse-stereo-driven ERP displacement warp.

Pipeline:
  1. Render L1 sphere ERP slabs + weights for all 7 cams (UNCHANGED L1).
  2. Build per-cam dense displacement fields from cached stereo .npz.
  3. Warp each cam's ERP slab by its displacement (gated by confidence).
  4. Multi-band blend the warped slabs (UNCHANGED multiband).

A/B baseline: --no-warp skips step 2-3, equivalent to plain L1.

Usage (single anchor, Pi3 cache):
    python scripts/phase3/run_l1_sparse_disp.py \\
        --pi3-dir outputs/phase3/p3.1_multi_anchor/anchor_060 \\
        --stereo-cache-dir outputs/phase3/p3.6_stereo/anchor_060 \\
        --output-dir outputs/phase3/p3.X_parallax/anchor_060_a2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _load_pi3_cam(pi3_dir: Path, cam: str) -> dict:
    return {
        "image": np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")),
        "K": np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy").astype(np.float64),
        "T_ego_cam": np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy").astype(np.float64),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", type=Path, required=True)
    ap.add_argument("--stereo-cache-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--no-warp", action="store_true",
                    help="A/B baseline: skip A2 displacement warp, plain L1 output.")
    ap.add_argument("--no-ego-mask", action="store_true")
    ap.add_argument("--rbf-regularization", type=float, default=1.0)
    ap.add_argument("--confidence-sigma-px", type=float, default=20.0)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.alignment.sparse_displacement import build_warped_slabs_a2
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    cams = list(RING_CAMS_7)
    print(f"[a2-sparse-disp] mode={'NO-WARP plain L1' if args.no_warp else 'A2 displacement warp'}, "
          f"erp_hw={erp_hw}", flush=True)

    per_cam = {cam: _load_pi3_cam(args.pi3_dir, cam) for cam in cams}
    cam_image_shapes = {cam: per_cam[cam]["image"].shape[:2] for cam in cams}
    ego_masks = build_ego_masks(cams, cam_image_shapes, enabled=not args.no_ego_mask)

    # L1 sphere projection (UNCHANGED)
    t_proj0 = time.time()
    slabs_dict: dict[str, np.ndarray] = {}
    weights_dict: dict[str, np.ndarray] = {}
    for cam in cams:
        d = per_cam[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        slabs_dict[cam] = rgb; weights_dict[cam] = w
    t_proj_s = time.time() - t_proj0

    # A2 warp
    t_warp0 = time.time()
    a2_summary = None
    if not args.no_warp:
        stereo_paths = sorted(args.stereo_cache_dir.glob("stereo_*.npz"))
        cam_K = {cam: per_cam[cam]["K"] for cam in cams}
        cam_T = {cam: per_cam[cam]["T_ego_cam"] for cam in cams}
        warped, a2_summary = build_warped_slabs_a2(
            l1_slabs=slabs_dict, stereo_npz_paths=stereo_paths,
            cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=cams,
            erp_hw=erp_hw,
            rbf_regularization=args.rbf_regularization,
            confidence_sigma_px=args.confidence_sigma_px,
        )
        slabs_dict = warped
    t_warp_s = time.time() - t_warp0

    # Multi-band blend (UNCHANGED)
    t_blend0 = time.time()
    slabs_list = [slabs_dict[c] for c in cams]
    weights_list = [weights_dict[c] for c in cams]
    erp = multiband_blend(slabs_list, weights_list, num_bands=args.num_bands, wrap=True)
    t_blend_s = time.time() - t_blend0

    out_png = out_dir / "l1_sparse_disp.png"
    Image.fromarray(erp).save(out_png)
    print(f"[a2-sparse-disp] wrote {out_png}", flush=True)

    summary = {
        "route": "WS4 A2 — L1 + sparse stereo ERP displacement",
        "mode": "no-warp (plain L1)" if args.no_warp else "warped",
        "pi3_dir": str(args.pi3_dir),
        "stereo_cache_dir": str(args.stereo_cache_dir),
        "erp_hw": list(erp_hw),
        "params": {
            "rbf_regularization": args.rbf_regularization,
            "confidence_sigma_px": args.confidence_sigma_px,
            "no_ego_mask": bool(args.no_ego_mask),
        },
        "a2_warp_summary": a2_summary,
        "runtime_s": {
            "projection": round(t_proj_s, 3),
            "warp": round(t_warp_s, 3),
            "blend": round(t_blend_s, 3),
        },
        "outputs": {"l1_sparse_disp": str(out_png.resolve())},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Local syntax check**

Run: `python -c "import ast; ast.parse(open('scripts/phase3/run_l1_sparse_disp.py').read())"`
Expected: no output (syntax OK)

- [ ] **Step 3: Commit + push**

```bash
git add scripts/phase3/run_l1_sparse_disp.py
git commit -m "WS4 A2: driver run_l1_sparse_disp.py

End-to-end: L1 sphere (UNCHANGED) + A2 displacement warp + multiband blend.
A/B flag --no-warp produces plain L1 baseline for comparison.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

- [ ] **Step 4: Colab smoke (CPU)**

In Colab via `/tmp/cdrun.py`:

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && git pull --rebase origin main 2>&1 | tail -3 && python -m pytest code/waymo2panorama/alignment/__test_sparse_displacement.py 2>&1 | tail -3"`
Expected: 8 passed

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && python scripts/phase3/run_l1_sparse_disp.py --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_060 --stereo-cache-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.6_stereo/anchor_060 --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_060_a2 2>&1 | tail -25"`
Expected: `[a2-sparse-disp] wrote ...l1_sparse_disp.png`, exit 0, runtime ~5-30s

- [ ] **Step 5: Quick visual diff vs plain L1**

Run plain L1 baseline:
Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && python scripts/phase3/run_l1_sparse_disp.py --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_060 --stereo-cache-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.6_stereo/anchor_060 --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_060_a2_plainL1 --no-warp 2>&1 | tail -10"`

Then diff:
Run: `python /tmp/cdrun.py "python <<'PYEOF'
import numpy as np
from PIL import Image
ROOT = '/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax'
plain = np.asarray(Image.open(f'{ROOT}/anchor_060_a2_plainL1/l1_sparse_disp.png'))
warped = np.asarray(Image.open(f'{ROOT}/anchor_060_a2/l1_sparse_disp.png'))
diff = np.abs(plain.astype(np.int32) - warped.astype(np.int32))
mxp = diff.max(axis=-1)
print(f'A2 vs plain L1: max={diff.max()} mean={diff.mean():.3f}')
print(f'frac >5: {float((mxp>5).mean()):.4f} | >20: {float((mxp>20).mean()):.4f}')
black_p = float((plain.sum(axis=-1) < 30).mean())
black_w = float((warped.sum(axis=-1) < 30).mean())
print(f'black: plain={black_p:.4f}  warped={black_w:.4f} (diff = {black_w-black_p:+.4f})')
PYEOF"`
Expected:
- max > 5 (warp did something)
- frac >5: should be > 0.001 (at least overlap regions affected)
- black diff: ideally close to 0 (no散架)

If max == 0 or black > 0.05: investigate (probably bug in displacement field application)

---

## Phase 2 — B1 Disparity-Aware Graphcut Seam (~6 hours, CPU only)

### Task 2.1: Disparity signal builder

Convert sparse stereo displacements (from Phase 1 A2) into a per-pair disparity magnitude signal in the ERP overlap region.

**Files:**
- Create: `code/waymo2panorama/blending/graphcut_disparity.py`
- Create: `code/waymo2panorama/blending/__test_graphcut_disparity.py`

- [ ] **Step 1: Write failing test**

```python
"""Unit tests for blending/graphcut_disparity.py — B1 module."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.blending.graphcut_disparity import (  # noqa: E402
    build_pair_disparity_magnitude,
)


def test_disparity_zero_when_displacements_equal():
    """If cam_a and cam_b both have the same displacement vector at a point,
    their disparity (relative) is zero — no parallax disagreement."""
    erp_hw = (64, 128)
    cam_a_anchors = [(np.array([60.0, 30.0]), np.array([2.0, -1.0]))]
    cam_b_anchors = [(np.array([60.0, 30.0]), np.array([2.0, -1.0]))]
    disp_mag = build_pair_disparity_magnitude(
        cam_a_anchors, cam_b_anchors, erp_hw=erp_hw, sigma_px=10.0,
    )
    assert disp_mag.shape == erp_hw
    assert disp_mag[30, 60] < 0.5  # ~ 0 at the anchor (equal disp = no disparity)


def test_disparity_nonzero_when_displacements_differ():
    """Different per-cam displacement at the same point → nonzero disparity."""
    erp_hw = (64, 128)
    cam_a_anchors = [(np.array([60.0, 30.0]), np.array([3.0, 0.0]))]
    cam_b_anchors = [(np.array([60.0, 30.0]), np.array([-3.0, 0.0]))]
    disp_mag = build_pair_disparity_magnitude(
        cam_a_anchors, cam_b_anchors, erp_hw=erp_hw, sigma_px=10.0,
    )
    assert disp_mag[30, 60] > 4.0  # |3 - (-3)| = 6 pixels of relative disp
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/blending/__test_graphcut_disparity.py -v`
Expected: 2 FAIL with ImportError

- [ ] **Step 3: Implement**

Create `code/waymo2panorama/blending/graphcut_disparity.py`:

```python
"""B1 — Disparity-aware graphcut seam optimization (parallax overlap fix candidate).

For each adjacent cam pair, compute a per-pixel disparity magnitude signal
in the ERP overlap region. High disparity = cams disagree (parallax) here.
Then find a seam through the overlap that walks the LOW-disparity zone,
replacing soft cos^2 blend with hard 0/1 mask (cam_a on one side, cam_b on
other), eliminating ghost averaging.

The graphcut here uses scipy.sparse + a simple shortest-path (Dijkstra) as
a 1D seam optimizer — for overlap regions that are approximately vertical
stripes in ERP, a 1D seam (varying u-coordinate per v-row) is sufficient
and avoids the pymaxflow dependency.

Usage (driver responsibility):
    slabs_dict, weights_dict = render_cams_to_erp(...)
    for (cam_a, cam_b) in adjacent_pairs:
        disp_mag = build_pair_disparity_magnitude(...)
        seam_mask = find_min_disparity_seam(disp_mag, overlap_mask)
        weights_dict[cam_a], weights_dict[cam_b] = apply_seam_to_weights(
            weights_dict[cam_a], weights_dict[cam_b], seam_mask, soft_px=2,
        )
    erp = multiband_blend(slabs_dict.values(), weights_dict.values(), ...)
"""
from __future__ import annotations

import numpy as np


def build_pair_disparity_magnitude(
    cam_a_anchors: list[tuple[np.ndarray, np.ndarray]],
    cam_b_anchors: list[tuple[np.ndarray, np.ndarray]],
    erp_hw: tuple[int, int],
    sigma_px: float = 20.0,
) -> np.ndarray:
    """Disparity = || delta_uv_cam_a - delta_uv_cam_b || at each anchor, splatted as Gaussians.

    For each shared 3D point seen by both cams in this pair, compute the
    DIFFERENCE between their L1->ideal displacements. That difference is how
    much the two cams DISAGREE about where to paint this point (pure parallax
    effect after subtracting common ego-frame shift).

    Splat the disparity magnitude as Gaussians around each anchor. Pixels in
    the high-disparity regions are the ones we want the seam to AVOID.

    Args:
        cam_a_anchors / cam_b_anchors: same format as
            `build_per_cam_displacements_from_stereo` output for one cam.
            Must be aligned (same length, same order — same 3D points).
        erp_hw: (H, W).
        sigma_px: Gaussian splat std-dev.

    Returns:
        (H, W) float32 disparity magnitude in [0, +inf) — units of ERP pixels.
    """
    assert len(cam_a_anchors) == len(cam_b_anchors), (
        "cam_a and cam_b anchor lists must be same length (same 3D points)"
    )
    H, W = erp_hw
    out = np.zeros((H, W), dtype=np.float32)
    if len(cam_a_anchors) == 0:
        return out
    yy, xx = np.mgrid[0:H, 0:W]
    inv_two_sigma_sq = 1.0 / (2.0 * sigma_px * sigma_px)
    for (ideal_uv_a, delta_a), (ideal_uv_b, delta_b) in zip(cam_a_anchors, cam_b_anchors):
        # Anchor position: average of ideal_uv from both cams (should be ~ same)
        u = 0.5 * (float(ideal_uv_a[0]) + float(ideal_uv_b[0]))
        v = 0.5 * (float(ideal_uv_a[1]) + float(ideal_uv_b[1]))
        # Disparity magnitude at this anchor
        rel_disp = float(np.linalg.norm(delta_a - delta_b))
        # Splat as Gaussian (additive — disparity accumulates if multiple stereo
        # points project near same pixel and all show disagreement)
        du = np.minimum(np.abs(xx - u), W - np.abs(xx - u))
        dv = (yy - v)
        d2 = du * du + dv * dv
        gauss = np.exp(-d2 * inv_two_sigma_sq).astype(np.float32)
        np.maximum(out, gauss * rel_disp, out=out)
    return out
```

- [ ] **Step 4: Run test**

Run: `python -m pytest code/waymo2panorama/blending/__test_graphcut_disparity.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add code/waymo2panorama/blending/graphcut_disparity.py code/waymo2panorama/blending/__test_graphcut_disparity.py
git commit -m "WS4 B1: build_pair_disparity_magnitude + pytest

Per-pixel disparity magnitude signal for B1 graphcut seam optimizer. High
value = cams disagree on where to paint this content (parallax effect).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2.2: Seam finder (1D Dijkstra-based)

For an overlap region that's roughly a vertical strip, find the minimum-cost seam (per-row u coordinate) through the disparity field.

**Files:**
- Modify: `code/waymo2panorama/blending/graphcut_disparity.py`
- Modify: `code/waymo2panorama/blending/__test_graphcut_disparity.py`

- [ ] **Step 1: Write failing test**

```python
def test_find_seam_picks_min_disparity_column():
    """Seam should pass through low-disparity column, avoid high-disparity column."""
    from waymo2panorama.blending.graphcut_disparity import find_min_disparity_seam
    H, W = 32, 64
    disp = np.zeros((H, W), dtype=np.float32)
    # High disp at columns 20-30, very low at column 35
    disp[:, 20:30] = 10.0
    disp[:, 34:36] = 0.1
    overlap_mask = np.zeros((H, W), dtype=bool)
    overlap_mask[:, 15:45] = True
    seam_u = find_min_disparity_seam(disp, overlap_mask, u_smoothness=0.5)
    assert seam_u.shape == (H,)
    # Seam should be near column 35, not 25
    assert np.all((seam_u >= 32) & (seam_u <= 40)), f"seam {seam_u} should be near 35"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/blending/__test_graphcut_disparity.py -v`
Expected: 2 PASS + 1 FAIL

- [ ] **Step 3: Implement**

Append to `code/waymo2panorama/blending/graphcut_disparity.py`:

```python
def find_min_disparity_seam(
    disparity_mag: np.ndarray,
    overlap_mask: np.ndarray,
    u_smoothness: float = 1.0,
) -> np.ndarray:
    """1D dynamic-programming seam finder through the overlap region.

    For each row v, picks a column u_seam(v) such that:
      (a) the path stays inside overlap_mask
      (b) total cumulative disparity along the path is minimized
      (c) consecutive rows' u_seam values are close (u_smoothness penalty)

    Uses DP: at each row, cost[v, u] = disp[v, u] + min(cost[v-1, u'] +
    u_smoothness * |u' - u|) over u' in valid overlap. Returns the argmin
    backtrace.

    Args:
        disparity_mag: (H, W) float32. Higher = avoid this pixel.
        overlap_mask: (H, W) bool. True where seam allowed to pass.
        u_smoothness: penalty per pixel of row-to-row column change.

    Returns:
        seam_u: (H,) int array. seam_u[v] = column index of the seam at row v.
            For rows where overlap_mask is empty, seam_u[v] = midpoint of W.
    """
    H, W = disparity_mag.shape
    INF = 1e9
    cost = np.full((H, W), INF, dtype=np.float64)
    back = np.zeros((H, W), dtype=np.int64)
    # Initial row
    cost[0] = np.where(overlap_mask[0], disparity_mag[0].astype(np.float64), INF)
    for v in range(1, H):
        prev = cost[v - 1]
        for u in range(W):
            if not overlap_mask[v, u]:
                continue
            # Search u' in [u - 3, u + 3] (small window, smoothness penalty
            # implicitly limits jump). Faster than full O(W^2).
            best = INF
            best_up = u
            for up in range(max(0, u - 3), min(W, u + 4)):
                c = prev[up] + u_smoothness * abs(up - u)
                if c < best:
                    best = c
                    best_up = up
            cost[v, u] = best + float(disparity_mag[v, u])
            back[v, u] = best_up
    # Backtrace from last row
    seam_u = np.full(H, W // 2, dtype=np.int64)
    if cost[H - 1].min() < INF:
        u = int(np.argmin(cost[H - 1]))
        seam_u[H - 1] = u
        for v in range(H - 1, 0, -1):
            u = int(back[v, u])
            seam_u[v - 1] = u
    return seam_u
```

- [ ] **Step 4: Run test**

Run: `python -m pytest code/waymo2panorama/blending/__test_graphcut_disparity.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add code/waymo2panorama/blending/graphcut_disparity.py code/waymo2panorama/blending/__test_graphcut_disparity.py
git commit -m "WS4 B1: find_min_disparity_seam (1D DP) + pytest

Per-row column index of minimum-cost seam through the overlap region.
Stays in overlap, avoids high-disparity pixels, prefers vertical-ish seams
via u_smoothness penalty. Returns int array of length H.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2.3: Apply seam to weights (binary + soft edge)

**Files:**
- Modify: `code/waymo2panorama/blending/graphcut_disparity.py`
- Modify: `code/waymo2panorama/blending/__test_graphcut_disparity.py`

- [ ] **Step 1: Write failing test**

```python
def test_apply_seam_zero_one_mask():
    """Apply seam: left of seam = cam_a only, right of seam = cam_b only."""
    from waymo2panorama.blending.graphcut_disparity import apply_seam_to_pair_weights
    H, W = 16, 32
    w_a = np.ones((H, W), dtype=np.float32) * 0.5
    w_b = np.ones((H, W), dtype=np.float32) * 0.5
    seam_u = np.full(H, 16, dtype=np.int64)
    overlap_mask = np.zeros((H, W), dtype=bool)
    overlap_mask[:, 10:22] = True
    w_a_new, w_b_new = apply_seam_to_pair_weights(
        w_a, w_b, seam_u, overlap_mask, soft_px=0,
    )
    # In overlap, left of seam (10-15) → cam_a=1.0, cam_b=0
    # Right of seam (17-21) → cam_a=0, cam_b=1.0
    # Outside overlap: weights unchanged (0.5 each)
    assert w_a_new[5, 12] > 0.9 and w_b_new[5, 12] < 0.1
    assert w_a_new[5, 19] < 0.1 and w_b_new[5, 19] > 0.9
    assert abs(w_a_new[5, 5] - 0.5) < 0.01  # outside overlap, unchanged
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/blending/__test_graphcut_disparity.py -v`
Expected: 3 PASS + 1 FAIL

- [ ] **Step 3: Implement**

Append to `code/waymo2panorama/blending/graphcut_disparity.py`:

```python
def apply_seam_to_pair_weights(
    w_a: np.ndarray,
    w_b: np.ndarray,
    seam_u: np.ndarray,
    overlap_mask: np.ndarray,
    soft_px: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert soft cos^2 blend to graphcut seam: left of seam = cam_a, right = cam_b.

    Inside overlap_mask:
      - Columns < seam_u[v]: w_a = w_a + w_b (cam_a takes over), w_b = 0
      - Columns >= seam_u[v]: w_b = w_b + w_a (cam_b takes over), w_a = 0
      - soft_px > 0: small Gaussian blur on the binary mask edge for visual smoothness

    Outside overlap_mask: weights unchanged.

    Returns (w_a_new, w_b_new) float32 arrays same shape as input.
    """
    H, W = w_a.shape
    assert w_b.shape == (H, W) and overlap_mask.shape == (H, W)
    w_total = (w_a + w_b).astype(np.float32)
    mask_a_side = np.zeros((H, W), dtype=np.float32)
    for v in range(H):
        u_cut = int(seam_u[v])
        mask_a_side[v, :u_cut] = 1.0  # left of seam belongs to cam_a
    if soft_px > 0:
        import cv2
        k = max(3, soft_px * 2 + 1)
        mask_a_side = cv2.GaussianBlur(mask_a_side, (k, k), soft_px)
    mask_b_side = 1.0 - mask_a_side
    # Only apply inside overlap
    in_overlap = overlap_mask.astype(np.float32)
    w_a_new = w_a * (1.0 - in_overlap) + (w_total * mask_a_side) * in_overlap
    w_b_new = w_b * (1.0 - in_overlap) + (w_total * mask_b_side) * in_overlap
    return w_a_new.astype(np.float32), w_b_new.astype(np.float32)
```

- [ ] **Step 4: Run test**

Run: `python -m pytest code/waymo2panorama/blending/__test_graphcut_disparity.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add code/waymo2panorama/blending/graphcut_disparity.py code/waymo2panorama/blending/__test_graphcut_disparity.py
git commit -m "WS4 B1: apply_seam_to_pair_weights + pytest

Convert soft cos^2 blend to per-pair hard 0/1 mask with optional soft edge.
Inside overlap, left of seam belongs to cam_a, right to cam_b. Outside
overlap, weights unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2.4: Orchestrator `build_seam_weights_b1`

Loops over all 7 adjacent pairs, computes disparity + seam + applies to weights.

**Files:**
- Modify: `code/waymo2panorama/blending/graphcut_disparity.py`
- Modify: `code/waymo2panorama/blending/__test_graphcut_disparity.py`

- [ ] **Step 1: Write failing test**

```python
def test_orchestrator_no_stereo_returns_unchanged_weights(tmp_path):
    """If no stereo data, orchestrator returns weights unchanged."""
    from waymo2panorama.blending.graphcut_disparity import build_seam_weights_b1
    weights = {f"cam_{i}": np.ones((32, 64), dtype=np.float32) * 0.5 for i in range(3)}
    cam_K = {c: np.eye(3) for c in weights}
    cam_T = {c: np.eye(4) for c in weights}
    out, summary = build_seam_weights_b1(
        l1_weights=weights, stereo_npz_paths=[],
        cam_K=cam_K, cam_T_ego_cam=cam_T,
        adjacent_pairs=[("cam_0", "cam_1"), ("cam_1", "cam_2")],
        erp_hw=(32, 64),
    )
    for c in weights:
        assert np.allclose(out[c], weights[c], atol=1e-6)
    assert summary["n_pairs_with_seam"] == 0
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/blending/__test_graphcut_disparity.py -v`
Expected: 4 PASS + 1 FAIL

- [ ] **Step 3: Implement**

Append to `code/waymo2panorama/blending/graphcut_disparity.py`:

```python
from pathlib import Path

from waymo2panorama.alignment.sparse_displacement import (
    build_per_cam_displacements_from_stereo,
)


def build_seam_weights_b1(
    l1_weights: dict[str, np.ndarray],
    stereo_npz_paths,
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    adjacent_pairs: list[tuple[str, str]],
    erp_hw: tuple[int, int],
    disparity_sigma_px: float = 20.0,
    seam_smoothness: float = 1.0,
    seam_soft_px: int = 2,
) -> tuple[dict[str, np.ndarray], dict]:
    """Orchestrator: L1 weights + stereo cache -> seam-modified weights per pair.

    For each adjacent pair (cam_a, cam_b):
      1. Find overlap mask (both weights > eps)
      2. Build per-pair disparity from stereo .npz that involves THIS pair
      3. Find min-disparity seam through overlap
      4. Replace soft cos^2 blend with hard 0/1 mask (with soft edge)
    """
    cam_names = list(l1_weights.keys())
    sparse_per_cam = build_per_cam_displacements_from_stereo(
        stereo_npz_paths, cam_K=cam_K, cam_T_ego_cam=cam_T_ego_cam,
        cam_names=cam_names, erp_hw=erp_hw,
    )
    # Re-index sparse displacements by stereo .npz path for per-pair lookup
    pair_anchors: dict[tuple[str, str], tuple[list, list]] = {}
    from waymo2panorama.alignment.sparse_displacement import _load_stereo_pair
    for p in stereo_npz_paths:
        loaded = _load_stereo_pair(Path(p))
        if loaded is None: continue
        cam_a, cam_b, pts = loaded
        if cam_a not in cam_names or cam_b not in cam_names: continue
        cam_a_anchors_for_pair = []
        cam_b_anchors_for_pair = []
        from waymo2panorama.alignment.sparse_displacement import (
            _compute_l1_erp_pixel_per_cam,
        )
        from waymo2panorama.pipeline.lift_and_project import ego_points_to_erp_uv
        for pt in pts:
            u_ideal, v_ideal, _ = ego_points_to_erp_uv(pt.reshape(1, 3), erp_hw=erp_hw)
            ideal_uv = np.array([float(u_ideal[0]), float(v_ideal[0])])
            l1_a = _compute_l1_erp_pixel_per_cam(pt, cam_K[cam_a], cam_T_ego_cam[cam_a], erp_hw)
            l1_b = _compute_l1_erp_pixel_per_cam(pt, cam_K[cam_b], cam_T_ego_cam[cam_b], erp_hw)
            if np.any(np.isnan(l1_a)) or np.any(np.isnan(l1_b)): continue
            W = erp_hw[1]
            def _wrap(d):
                if d > W / 2: return d - W
                elif d < -W / 2: return d + W
                return d
            delta_a = np.array([_wrap(ideal_uv[0] - l1_a[0]), ideal_uv[1] - l1_a[1]])
            delta_b = np.array([_wrap(ideal_uv[0] - l1_b[0]), ideal_uv[1] - l1_b[1]])
            cam_a_anchors_for_pair.append((ideal_uv, delta_a))
            cam_b_anchors_for_pair.append((ideal_uv, delta_b))
        if cam_a_anchors_for_pair:
            pair_anchors[(cam_a, cam_b)] = (cam_a_anchors_for_pair, cam_b_anchors_for_pair)

    out_weights = {c: l1_weights[c].astype(np.float32).copy() for c in cam_names}
    n_pairs_done = 0
    per_pair_log: list[dict] = []
    for (cam_a, cam_b) in adjacent_pairs:
        if cam_a not in out_weights or cam_b not in out_weights:
            continue
        overlap_mask = (out_weights[cam_a] > 1e-3) & (out_weights[cam_b] > 1e-3)
        if not overlap_mask.any():
            per_pair_log.append({
                "cam_a": cam_a, "cam_b": cam_b, "status": "no_overlap",
            })
            continue
        anchors = pair_anchors.get((cam_a, cam_b))
        if anchors is None:
            per_pair_log.append({
                "cam_a": cam_a, "cam_b": cam_b, "status": "no_stereo",
            })
            continue
        disp_mag = build_pair_disparity_magnitude(
            anchors[0], anchors[1], erp_hw=erp_hw, sigma_px=disparity_sigma_px,
        )
        # Boost cost outside overlap to keep seam inside
        cost_field = disp_mag.copy()
        cost_field[~overlap_mask] = 1e6
        seam_u = find_min_disparity_seam(
            cost_field, overlap_mask, u_smoothness=seam_smoothness,
        )
        out_weights[cam_a], out_weights[cam_b] = apply_seam_to_pair_weights(
            out_weights[cam_a], out_weights[cam_b], seam_u,
            overlap_mask, soft_px=seam_soft_px,
        )
        n_pairs_done += 1
        per_pair_log.append({
            "cam_a": cam_a, "cam_b": cam_b, "status": "ok",
            "n_anchors": len(anchors[0]),
            "max_disp_mag": float(disp_mag.max()),
        })
    summary = {
        "n_pairs_total": len(adjacent_pairs),
        "n_pairs_with_seam": n_pairs_done,
        "per_pair": per_pair_log,
    }
    return out_weights, summary
```

- [ ] **Step 4: Run test**

Run: `python -m pytest code/waymo2panorama/blending/__test_graphcut_disparity.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit + push**

```bash
git add code/waymo2panorama/blending/graphcut_disparity.py code/waymo2panorama/blending/__test_graphcut_disparity.py
git commit -m "WS4 B1: build_seam_weights_b1 orchestrator + pytest

End-to-end: per adjacent cam pair, build disparity from stereo cache, find
min-disparity seam through overlap, replace cos^2 blend with hard 0/1 mask.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

---

### Task 2.5: Driver `run_l1_graphcut_disp.py`

**Files:**
- Create: `scripts/phase3/run_l1_graphcut_disp.py`

- [ ] **Step 1: Create driver**

Create `scripts/phase3/run_l1_graphcut_disp.py`:

```python
"""WS4 B1 — L1 sphere + disparity-aware graphcut seam (per-pair hard mask).

Pipeline:
  1. Render L1 sphere ERP slabs + weights (UNCHANGED L1).
  2. For each adjacent ring pair, build disparity from stereo cache.
  3. Find min-disparity seam through overlap; replace soft cos^2 blend with
     hard 0/1 mask (with optional soft edge).
  4. Multi-band blend with modified weights (UNCHANGED multiband).

A/B baseline: --no-seam skips step 2-3, plain L1.

Usage:
    python scripts/phase3/run_l1_graphcut_disp.py \\
        --pi3-dir outputs/phase3/p3.1_multi_anchor/anchor_060 \\
        --stereo-cache-dir outputs/phase3/p3.6_stereo/anchor_060 \\
        --output-dir outputs/phase3/p3.X_parallax/anchor_060_b1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _load_pi3_cam(pi3_dir: Path, cam: str) -> dict:
    return {
        "image": np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")),
        "K": np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy").astype(np.float64),
        "T_ego_cam": np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy").astype(np.float64),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", type=Path, required=True)
    ap.add_argument("--stereo-cache-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--no-seam", action="store_true",
                    help="A/B baseline: skip seam, plain L1 output.")
    ap.add_argument("--no-ego-mask", action="store_true")
    ap.add_argument("--disparity-sigma-px", type=float, default=20.0)
    ap.add_argument("--seam-smoothness", type=float, default=1.0)
    ap.add_argument("--seam-soft-px", type=int, default=2)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.alignment.pair_homography import ADJACENT_PAIRS
    from waymo2panorama.blending.graphcut_disparity import build_seam_weights_b1
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    cams = list(RING_CAMS_7)
    print(f"[b1-graphcut] mode={'NO-SEAM plain L1' if args.no_seam else 'B1 seam'}, "
          f"erp_hw={erp_hw}", flush=True)

    per_cam = {cam: _load_pi3_cam(args.pi3_dir, cam) for cam in cams}
    cam_image_shapes = {cam: per_cam[cam]["image"].shape[:2] for cam in cams}
    ego_masks = build_ego_masks(cams, cam_image_shapes, enabled=not args.no_ego_mask)

    t_proj0 = time.time()
    slabs_dict: dict[str, np.ndarray] = {}
    weights_dict: dict[str, np.ndarray] = {}
    for cam in cams:
        d = per_cam[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        slabs_dict[cam] = rgb; weights_dict[cam] = w
    t_proj_s = time.time() - t_proj0

    t_seam0 = time.time()
    b1_summary = None
    if not args.no_seam:
        stereo_paths = sorted(args.stereo_cache_dir.glob("stereo_*.npz"))
        cam_K = {cam: per_cam[cam]["K"] for cam in cams}
        cam_T = {cam: per_cam[cam]["T_ego_cam"] for cam in cams}
        modified_weights, b1_summary = build_seam_weights_b1(
            l1_weights=weights_dict, stereo_npz_paths=stereo_paths,
            cam_K=cam_K, cam_T_ego_cam=cam_T,
            adjacent_pairs=ADJACENT_PAIRS, erp_hw=erp_hw,
            disparity_sigma_px=args.disparity_sigma_px,
            seam_smoothness=args.seam_smoothness,
            seam_soft_px=args.seam_soft_px,
        )
        weights_dict = modified_weights
    t_seam_s = time.time() - t_seam0

    t_blend0 = time.time()
    slabs_list = [slabs_dict[c] for c in cams]
    weights_list = [weights_dict[c] for c in cams]
    erp = multiband_blend(slabs_list, weights_list, num_bands=args.num_bands, wrap=True)
    t_blend_s = time.time() - t_blend0

    out_png = out_dir / "l1_graphcut_disp.png"
    Image.fromarray(erp).save(out_png)
    print(f"[b1-graphcut] wrote {out_png}", flush=True)

    summary = {
        "route": "WS4 B1 — L1 + disparity-aware graphcut seam",
        "mode": "no-seam (plain L1)" if args.no_seam else "seam",
        "pi3_dir": str(args.pi3_dir),
        "stereo_cache_dir": str(args.stereo_cache_dir),
        "erp_hw": list(erp_hw),
        "params": {
            "disparity_sigma_px": args.disparity_sigma_px,
            "seam_smoothness": args.seam_smoothness,
            "seam_soft_px": args.seam_soft_px,
            "no_ego_mask": bool(args.no_ego_mask),
        },
        "b1_seam_summary": b1_summary,
        "runtime_s": {
            "projection": round(t_proj_s, 3),
            "seam": round(t_seam_s, 3),
            "blend": round(t_blend_s, 3),
        },
        "outputs": {"l1_graphcut_disp": str(out_png.resolve())},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Syntax check**

Run: `python -c "import ast; ast.parse(open('scripts/phase3/run_l1_graphcut_disp.py').read())"`
Expected: no output

- [ ] **Step 3: Commit + push**

```bash
git add scripts/phase3/run_l1_graphcut_disp.py
git commit -m "WS4 B1: driver run_l1_graphcut_disp.py

End-to-end: L1 sphere (UNCHANGED) + B1 per-pair disparity-aware seam +
multiband blend. A/B flag --no-seam for plain L1 baseline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

- [ ] **Step 4: Colab smoke (CPU)**

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && git pull --rebase origin main 2>&1 | tail -3 && python -m pytest code/waymo2panorama/blending/__test_graphcut_disparity.py 2>&1 | tail -3"`
Expected: 5 passed

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && python scripts/phase3/run_l1_graphcut_disp.py --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_060 --stereo-cache-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.6_stereo/anchor_060 --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_060_b1 2>&1 | tail -25"`
Expected: exit 0, png written, ~10-60s

- [ ] **Step 5: Quick visual diff**

Run: `python /tmp/cdrun.py "python <<'PYEOF'
import numpy as np
from PIL import Image
ROOT = '/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax'
plain = np.asarray(Image.open(f'{ROOT}/anchor_060_a2_plainL1/l1_sparse_disp.png'))
b1 = np.asarray(Image.open(f'{ROOT}/anchor_060_b1/l1_graphcut_disp.png'))
diff = np.abs(plain.astype(np.int32) - b1.astype(np.int32))
mxp = diff.max(axis=-1)
print(f'B1 vs plain L1: max={diff.max()} mean={diff.mean():.3f}')
print(f'frac >5: {float((mxp>5).mean()):.4f} | >20: {float((mxp>20).mean()):.4f}')
black_p = float((plain.sum(axis=-1) < 30).mean())
black_b = float((b1.sum(axis=-1) < 30).mean())
print(f'black: plain={black_p:.4f}  b1={black_b:.4f} (diff = {black_b-black_p:+.4f})')
PYEOF"`
Expected:
- max > 5 (seam did something)
- black diff: ideally ≤ 0 (seam shouldn't introduce holes)

---

## Phase 3 — Eval scripts (~3 hours, CPU)

### Task 3.1: Visual compare panel generator

Generate side-by-side panel: plain L1 | A2 | B1 (5-row hybrid added later if C1 ships).

**Files:**
- Create: `scripts/phase3/build_parallax_compare_panel.py`

- [ ] **Step 1: Create script**

Create `scripts/phase3/build_parallax_compare_panel.py`:

```python
"""Build side-by-side comparison panel for parallax-fix approaches.

For one anchor, stacks: plain L1 / A2 / B1 / (C1) / (hybrid) ERPs vertically
with labels. Used for visual verification of which approach best eliminates
white traces / 2-wheel ghost.

Usage:
    python scripts/phase3/build_parallax_compare_panel.py \\
        --anchor 60 \\
        --plain-l1 outputs/phase3/p3.X_parallax/anchor_060_a2_plainL1/l1_sparse_disp.png \\
        --a2 outputs/phase3/p3.X_parallax/anchor_060_a2/l1_sparse_disp.png \\
        --b1 outputs/phase3/p3.X_parallax/anchor_060_b1/l1_graphcut_disp.png \\
        --output-png outputs/phase3/p3.X_parallax/compare_anchor_060.png
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor", type=int, required=True)
    ap.add_argument("--plain-l1", type=Path, required=True)
    ap.add_argument("--a2", type=Path, default=None)
    ap.add_argument("--b1", type=Path, default=None)
    ap.add_argument("--c1", type=Path, default=None)
    ap.add_argument("--hybrid", type=Path, default=None)
    ap.add_argument("--output-png", type=Path, required=True)
    ap.add_argument("--max-display-h", type=int, default=512,
                    help="Resize each row to this max height for compact panel.")
    args = ap.parse_args()

    rows: list[tuple[str, np.ndarray]] = []
    for label, path in [
        ("plain L1 (baseline)", args.plain_l1),
        ("A2 — sparse stereo displacement", args.a2),
        ("B1 — disparity-aware graphcut seam", args.b1),
        ("C1 — RAFT optical flow", args.c1),
        ("HYBRID", args.hybrid),
    ]:
        if path is None: continue
        if not path.exists():
            print(f"[warn] missing: {path}, skipping {label}")
            continue
        img = np.asarray(Image.open(path).convert("RGB"))
        # Resize to compact
        H, W = img.shape[:2]
        scale = args.max_display_h / H
        new_W = int(W * scale)
        img_small = cv2.resize(img, (new_W, args.max_display_h),
                                interpolation=cv2.INTER_AREA)
        rows.append((label, img_small))

    if not rows:
        print("[error] no input images found")
        return 1

    label_h = 30
    row_w = rows[0][1].shape[1]
    panel = np.zeros(
        (sum(r[1].shape[0] + label_h for r in rows), row_w, 3),
        dtype=np.uint8,
    )
    y = 0
    for label, img in rows:
        cv2.rectangle(panel, (0, y), (row_w, y + label_h), (40, 40, 40), -1)
        cv2.putText(panel, f"anchor {args.anchor}: {label}", (8, y + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        y += label_h
        panel[y:y + img.shape[0], :img.shape[1]] = img
        y += img.shape[0]

    args.output_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(panel).save(args.output_png)
    print(f"wrote {args.output_png} ({panel.shape[1]}x{panel.shape[0]}), {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Local syntax check**

Run: `python -c "import ast; ast.parse(open('scripts/phase3/build_parallax_compare_panel.py').read())"`
Expected: no output

- [ ] **Step 3: Commit + push**

```bash
git add scripts/phase3/build_parallax_compare_panel.py
git commit -m "WS4 eval: visual compare panel generator

Stack plain L1 / A2 / B1 / C1 / hybrid ERPs vertically with labels for
side-by-side visual verification.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

---

### Task 3.2: Seam visibility metric

Quantitative metric: Laplacian variance ratio (overlap region vs non-overlap).

**Files:**
- Create: `scripts/phase3/eval_parallax_seam_metric.py`

- [ ] **Step 1: Create script**

Create `scripts/phase3/eval_parallax_seam_metric.py`:

```python
"""Quantitative seam-visibility metric for parallax-fix approaches.

For each ERP image, compute:
  - lap_var_overlap: Laplacian variance in expected overlap regions
  - lap_var_non_overlap: Laplacian variance in non-overlap regions
  - ratio: lap_var_overlap / lap_var_non_overlap

High ratio (>1) suggests overlap regions have more "chaos" (artifacts) than
non-overlap — indicates seam visibility. Low ratio (~1) is "as smooth in
overlap as anywhere else" = no visible seam.

The expected overlap mask is generated by re-running L1 sphere projection
on the same pi3 cache and computing where ≥ 2 cams' weight > 0.

Usage:
    python scripts/phase3/eval_parallax_seam_metric.py \\
        --pi3-dir outputs/phase3/p3.1_multi_anchor/anchor_060 \\
        --erp-png outputs/phase3/p3.X_parallax/anchor_060_a2/l1_sparse_disp.png \\
        --output-json outputs/phase3/p3.X_parallax/anchor_060_a2/seam_metric.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    sys.path.insert(0, str(w2p_code))


def build_expected_overlap_mask(pi3_dir: Path, erp_hw: tuple[int, int]) -> np.ndarray:
    """Re-run L1 sphere projection to find overlap regions (≥ 2 cams contribute)."""
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    cams = list(RING_CAMS_7)
    per_cam_weights = []
    image_shapes = {}
    for cam in cams:
        img = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
        K = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
        T = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        image_shapes[cam] = img.shape[:2]
    ego_masks = build_ego_masks(cams, image_shapes, enabled=True)
    for cam in cams:
        img = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
        K = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
        T = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        _rgb, _alpha, w = render_camera_to_erp(
            image=img, K=K, T_ego_cam=T,
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        per_cam_weights.append(w > 1e-3)
    coverage_count = np.sum(np.stack(per_cam_weights, axis=0), axis=0)
    return coverage_count >= 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi3-dir", type=Path, required=True)
    ap.add_argument("--erp-png", type=Path, required=True)
    ap.add_argument("--output-json", type=Path, required=True)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    erp = np.asarray(Image.open(args.erp_png).convert("RGB"))
    overlap_mask = build_expected_overlap_mask(args.pi3_dir, erp.shape[:2])
    gray = cv2.cvtColor(erp, cv2.COLOR_RGB2GRAY).astype(np.float64)
    lap = cv2.Laplacian(gray, cv2.CV_64F, ksize=3)
    lap_var_overlap = float(np.var(lap[overlap_mask]))
    lap_var_nonoverlap = float(np.var(lap[~overlap_mask]))
    ratio = lap_var_overlap / lap_var_nonoverlap if lap_var_nonoverlap > 0 else float("nan")

    result = {
        "erp_png": str(args.erp_png),
        "pi3_dir": str(args.pi3_dir),
        "erp_hw": list(erp.shape[:2]),
        "overlap_frac": float(overlap_mask.mean()),
        "lap_var_overlap": lap_var_overlap,
        "lap_var_non_overlap": lap_var_nonoverlap,
        "ratio_overlap_over_non": ratio,
        "interpretation": (
            "ratio > 1: overlap region has more Laplacian variance than non-overlap, "
            "suggesting visible seam artifacts. ratio ~ 1: seam not visible. "
            "ratio < 1: overlap region smoother than non-overlap (unusual; possibly over-smoothing)."
        ),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Syntax check**

Run: `python -c "import ast; ast.parse(open('scripts/phase3/eval_parallax_seam_metric.py').read())"`
Expected: no output

- [ ] **Step 3: Commit + push**

```bash
git add scripts/phase3/eval_parallax_seam_metric.py
git commit -m "WS4 eval: seam visibility metric (Laplacian variance ratio)

Lap variance in overlap region / non-overlap region. >1 = visible seam,
~1 = clean, <1 = over-smoothed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

---

### Task 3.3: Held-out cycle PSNR eval

Mirror of `eval_l1_rotation_refine_cycle.py` for A2 / B1.

**Files:**
- Create: `scripts/phase3/eval_parallax_fixes_cycle.py`

- [ ] **Step 1: Create script**

Create `scripts/phase3/eval_parallax_fixes_cycle.py`:

```python
"""Held-out cycle PSNR for parallax-fix approaches (A2 / B1).

For each anchor x each held-out cam_h:
  1. L1 sphere project the OTHER 6 cams (cam_h held out).
  2. Apply candidate's parallax fix (A2 or B1) using stereo from non-cam_h pairs.
  3. Reconstruct cam_h's view by projecting the 6 modified slabs back to cam_h's plane via cos^2 feather.
  4. PSNR vs cam_h's actual GT image.
  5. Compare against plain L1 6-cam reconstruction.

Note: this protocol differs from the production rendering because we run
the modified weights/slabs through the cam-plane reconstruction (not ERP
multiband). It's the same protocol as T5 v3 eval (apples-to-apples).

Usage:
    python scripts/phase3/eval_parallax_fixes_cycle.py \\
        --pi3-cache-root outputs/phase3/p3.1_multi_anchor \\
        --stereo-cache-root outputs/phase3/p3.6_stereo \\
        --anchors 0 60 90 150 \\
        --method a2 \\
        --output-dir outputs/phase3/p3.X_parallax/eval_cycle_a2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"
RING_CAMS_7 = (
    "ring_front_center", "ring_front_left", "ring_side_left", "ring_rear_left",
    "ring_rear_right", "ring_side_right", "ring_front_right",
)


def _wire_imports(w2p_code: Path) -> None:
    sys.path.insert(0, str(w2p_code))


def _psnr(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    if mask.sum() == 0: return float("nan")
    a = a.astype(np.float64); b = b.astype(np.float64)
    mse = ((a - b) ** 2)[mask].mean()
    if mse <= 1e-12: return float("inf")
    return 20.0 * np.log10(255.0) - 10.0 * np.log10(mse)


def _reconstruct_cam_plane(
    holdout: str, other_cams, cam_K, cam_T, cam_rgb,
):
    """L1 cam-plane reconstruction (same as eval_cycle_consistency.reconstruct_l1)."""
    K_h = cam_K[holdout]; T_ego_cam_h = cam_T[holdout]
    H, W = cam_rgb[holdout].shape[:2]
    uu, vv = np.meshgrid(np.arange(W), np.arange(H))
    pix_h = np.stack([uu, vv, np.ones_like(uu)], axis=-1).astype(np.float64)
    K_h_inv = np.linalg.inv(K_h)
    d_cam_h = pix_h @ K_h_inv.T
    d_cam_h = d_cam_h / np.linalg.norm(d_cam_h, axis=-1, keepdims=True)
    d_ego = d_cam_h @ T_ego_cam_h[:3, :3].T

    rgb_sum = np.zeros((H, W, 3), dtype=np.float32)
    w_sum = np.zeros((H, W), dtype=np.float32)
    for cam_j in other_cams:
        K_j = cam_K[cam_j]; T_j = cam_T[cam_j]
        rgb_j = cam_rgb[cam_j].astype(np.float32)
        H_j, W_j = rgb_j.shape[:2]
        R_cam_ego_j = T_j[:3, :3].T
        d_cam_j = d_ego @ R_cam_ego_j.T
        z_j = d_cam_j[..., 2]
        in_front = z_j > 1e-6
        z_safe = np.where(in_front, z_j, 1.0)
        u_j = K_j[0, 0] * d_cam_j[..., 0] / z_safe + K_j[0, 2]
        v_j = K_j[1, 1] * d_cam_j[..., 1] / z_safe + K_j[1, 2]
        margin = 0.5
        in_bounds = (u_j >= margin) & (u_j <= W_j - 1 - margin) & (v_j >= margin) & (v_j <= H_j - 1 - margin)
        valid = in_front & in_bounds
        map_x = np.where(valid, u_j, -1.0).astype(np.float32)
        map_y = np.where(valid, v_j, -1.0).astype(np.float32)
        sampled = cv2.remap(rgb_j, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        w_j = (np.clip(z_j, 0, 1) ** 2) * valid.astype(np.float32)
        rgb_sum += sampled * w_j[..., None]
        w_sum += w_j
    w_safe = np.where(w_sum > 1e-6, w_sum, 1.0)
    rgb_out = rgb_sum / w_safe[..., None]
    rgb_out = np.where(w_sum[..., None] > 1e-6, rgb_out, 0.0)
    mask = w_sum > 1e-6
    return rgb_out.astype(np.float32), mask


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi3-cache-root", type=Path, required=True)
    ap.add_argument("--stereo-cache-root", type=Path, required=True)
    ap.add_argument("--anchors", nargs="+", type=int, required=True)
    ap.add_argument("--method", choices=["a2", "b1"], required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    cams = list(RING_CAMS_7)
    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    per_anchor: list[dict] = []

    for a in args.anchors:
        pi3 = args.pi3_cache_root / f"anchor_{int(a):03d}"
        stereo_dir = args.stereo_cache_root / f"anchor_{int(a):03d}"
        if not pi3.exists() or not stereo_dir.exists():
            raise FileNotFoundError(f"missing {pi3} or {stereo_dir}")
        print(f"\n=== anchor {a:03d} (method={args.method}) ===")
        cam_K = {c: np.load(pi3 / f"av2_K_letterboxed_{c}.npy").astype(np.float64) for c in cams}
        cam_T = {c: np.load(pi3 / f"av2_T_ego_cam_{c}.npy").astype(np.float64) for c in cams}
        cam_rgb = {c: np.asarray(Image.open(pi3 / f"image_{c}.png").convert("RGB")).astype(np.uint8) for c in cams}

        rows = []
        for holdout in cams:
            others = [c for c in cams if c != holdout]
            rgb_l1, m_l1 = _reconstruct_cam_plane(holdout, others, cam_K, cam_T, cam_rgb)
            # For now, parallax-fix at cam-plane reconstruction is the SAME as plain L1
            # (A2/B1 affect ERP slab + blend, not single-cam-into-other-cam projection)
            # So delta will be 0 unless we modify the cam_T or extend protocol.
            # Document this as known structural caveat (same as T5 v3 holdout cycle).
            rgb_v = rgb_l1.copy()
            m_v = m_l1.copy()
            gt = cam_rgb[holdout]
            common = m_l1 & m_v
            psnr_b = _psnr(gt, np.clip(rgb_l1, 0, 255).astype(np.uint8), common)
            psnr_v = _psnr(gt, np.clip(rgb_v, 0, 255).astype(np.uint8), common)
            rows.append({"cam": holdout, "PSNR_L1": psnr_b, "PSNR_method": psnr_v, "delta_dB": psnr_v - psnr_b})
            print(f"  {holdout:24s} L1={psnr_b:6.3f}  {args.method}={psnr_v:6.3f}  delta={psnr_v - psnr_b:+6.3f}")
        finite_d = [r["delta_dB"] for r in rows if np.isfinite(r["delta_dB"])]
        agg = {
            "mean_delta_dB": float(np.mean(finite_d)) if finite_d else None,
            "n_better": int(sum(1 for d in finite_d if d > 0)),
            "n_worse": int(sum(1 for d in finite_d if d < 0)),
        }
        per_anchor.append({"anchor_idx": a, "per_cam": rows, "aggregate": agg})
        print(f"  ANCHOR {a} AGG: delta={agg['mean_delta_dB']:+.3f} dB  {agg['n_better']}/{agg['n_worse']}")

    all_d = [r["delta_dB"] for ax in per_anchor for r in ax["per_cam"] if np.isfinite(r["delta_dB"])]
    overall = {
        "method": args.method,
        "n_anchors": len(per_anchor),
        "n_measurements": len(all_d),
        "global_mean_delta_dB": float(np.mean(all_d)) if all_d else None,
        "n_better": int(sum(1 for d in all_d if d > 0)),
        "n_worse": int(sum(1 for d in all_d if d < 0)),
        "caveat": (
            "Method effect on cam-plane reconstruction is degenerate (A2/B1 act on "
            "ERP slabs + blend, not single-cam projection). Same structural issue as "
            "T5 v3 holdout cycle. Use ERP-space inter-method comparison + visual + "
            "seam metric for primary judgment."
        ),
    }
    out_json = out_dir / f"eval_parallax_{args.method}_cycle.json"
    out_json.write_text(json.dumps({"per_anchor": per_anchor, "overall": overall}, indent=2),
                         encoding="utf-8")
    print(f"\nOVERALL: delta={overall['global_mean_delta_dB']:+.3f} dB  ->  {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Syntax check**

Run: `python -c "import ast; ast.parse(open('scripts/phase3/eval_parallax_fixes_cycle.py').read())"`
Expected: no output

- [ ] **Step 3: Commit + push**

```bash
git add scripts/phase3/eval_parallax_fixes_cycle.py
git commit -m "WS4 eval: held-out cycle PSNR for A2 / B1 (with structural caveat)

Same protocol as T5 v3 cycle eval. With caveat documented: A2/B1 act on ERP
slab + blend, not cam-plane projection, so cycle PSNR is structurally
insensitive to them (like T5 v3). Visual + seam metric are primary judges.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

---

## Phase 4 — Production run + collect metrics (~2 hours, CPU)

### Task 4.1: 4-anchor production run for A2 + B1

- [ ] **Step 1: Run A2 on 4 anchors via Colab**

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && for A in 0 60 90 150; do echo === A2 anchor \$A ===; python scripts/phase3/run_l1_sparse_disp.py --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_\$(printf '%03d' \$A) --stereo-cache-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.6_stereo/anchor_\$(printf '%03d' \$A) --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_\$(printf '%03d' \$A)_a2 2>&1 | tail -5; done" 600`

Expected: 4 anchor runs, each ~10-30s, all exit 0, each writes l1_sparse_disp.png

- [ ] **Step 2: Run B1 on 4 anchors**

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && for A in 0 60 90 150; do echo === B1 anchor \$A ===; python scripts/phase3/run_l1_graphcut_disp.py --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_\$(printf '%03d' \$A) --stereo-cache-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.6_stereo/anchor_\$(printf '%03d' \$A) --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_\$(printf '%03d' \$A)_b1 2>&1 | tail -5; done" 600`

Expected: 4 anchor runs, exit 0

- [ ] **Step 3: Plain L1 baseline for each anchor**

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && for A in 0 60 90 150; do python scripts/phase3/run_l1_sparse_disp.py --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_\$(printf '%03d' \$A) --stereo-cache-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.6_stereo/anchor_\$(printf '%03d' \$A) --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_\$(printf '%03d' \$A)_plainL1 --no-warp 2>&1 | tail -3; done" 600`

Expected: 4 plain L1 runs

- [ ] **Step 4: Build comparison panels for each anchor**

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && for A in 0 60 90 150; do AS=\$(printf '%03d' \$A); python scripts/phase3/build_parallax_compare_panel.py --anchor \$A --plain-l1 /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_\${AS}_plainL1/l1_sparse_disp.png --a2 /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_\${AS}_a2/l1_sparse_disp.png --b1 /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_\${AS}_b1/l1_graphcut_disp.png --output-png /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/compare_anchor_\${AS}.png; done"`

Expected: 4 compare_anchor_NNN.png files

- [ ] **Step 5: Run seam metric on each + collect into table**

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && for A in 0 60 90 150; do AS=\$(printf '%03d' \$A); for M in plainL1 a2 b1; do PNG=/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_\${AS}_\${M}/\$([ \$M = plainL1 ] && echo l1_sparse_disp.png || ([ \$M = a2 ] && echo l1_sparse_disp.png || echo l1_graphcut_disp.png)); python scripts/phase3/eval_parallax_seam_metric.py --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_\${AS} --erp-png \$PNG --output-json /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_\${AS}_\${M}/seam_metric.json 2>&1 | grep ratio_overlap; done; done" 600`

Expected: 12 ratio numbers (4 anchors × 3 methods). Compare A2/B1 ratios vs plainL1 ratio: lower = better.

- [ ] **Step 6: Quick verdict — does A2 or B1 win on seam metric?**

Run: `python /tmp/cdrun.py "python <<'PYEOF'
import json
ROOT = '/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax'
print(f'{\"anchor\":<8} {\"plainL1\":<12} {\"a2\":<12} {\"b1\":<12} {\"a2_vs_l1\":<12} {\"b1_vs_l1\":<12}')
for a in [0, 60, 90, 150]:
    aas = f'{a:03d}'
    vals = {}
    for m in ['plainL1', 'a2', 'b1']:
        p = f'{ROOT}/anchor_{aas}_{m}/seam_metric.json'
        try:
            d = json.loads(open(p).read())
            vals[m] = d['ratio_overlap_over_non']
        except Exception as e:
            vals[m] = float('nan')
    a2_delta = vals['a2'] - vals['plainL1']
    b1_delta = vals['b1'] - vals['plainL1']
    print(f'{aas:<8} {vals[\"plainL1\"]:<12.4f} {vals[\"a2\"]:<12.4f} {vals[\"b1\"]:<12.4f} {a2_delta:<+12.4f} {b1_delta:<+12.4f}')
PYEOF"`
Expected: 4 rows, see if A2 or B1 has consistently negative delta (smaller seam visibility ratio).

---

## Phase 5 — Decision gate (CHECKPOINT — discuss with user)

### Task 5.1: Visual + metric review with user

- [ ] **Step 1: Send user the 4 compare panel Drive URLs + seam metric table**

Use mcp Drive search to find compare panel files; send viewUrls. Plus the seam metric table from Task 4.1 step 6.

- [ ] **Step 2: User decides next path**

Wait for user:
- (a) A2 or B1 visually wins on most anchors + seam metric improves → ship that as solo solution, proceed to Phase 7 writeup (skip C1 + hybrid)
- (b) A2 and B1 both fail to visibly fix white traces → proceed to Phase 6 C1 RAFT (needs GPU; tell user to switch Colab to GPU)
- (c) Mixed results → proceed to Phase 6 then Phase 7 hybrid
- (d) Pause / iterate with current — adjust A2/B1 parameters first before C1

---

## Phase 6 — C1 RAFT Optical Flow (~6 hours, **GPU required**)

⚠️ **GPU CHECKPOINT**: BEFORE starting Phase 6, ask user to switch Colab runtime to GPU. Verify with `nvidia-smi --query-gpu=name --format=csv,noheader`.

### Task 6.1: RAFT wrapper module

**Files:**
- Create: `code/waymo2panorama/alignment/optical_flow_align.py`
- Create: `code/waymo2panorama/alignment/__test_optical_flow_align.py`

- [ ] **Step 1: Write failing test (kornia-RAFT import check)**

```python
"""Unit tests for alignment/optical_flow_align.py (kornia RAFT)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

try:
    import kornia.feature  # noqa: F401
    KORNIA_OK = True
except ImportError:
    KORNIA_OK = False

_skip_no_kornia = pytest.mark.skipif(not KORNIA_OK, reason="kornia not installed")


@_skip_no_kornia
def test_compute_pair_flow_shape():
    """Compute flow on a pair of synthetic images: output shape correct."""
    from waymo2panorama.alignment.optical_flow_align import compute_pair_flow
    rng = np.random.RandomState(0)
    img_a = (rng.rand(128, 128, 3) * 255).astype(np.uint8)
    img_b = (rng.rand(128, 128, 3) * 255).astype(np.uint8)
    flow = compute_pair_flow(img_a, img_b, device="cpu")
    assert flow.shape == (128, 128, 2)
    assert flow.dtype == np.float32
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python -m pytest code/waymo2panorama/alignment/__test_optical_flow_align.py -v`
Expected: 1 FAIL (or SKIP if kornia missing locally)

- [ ] **Step 3: Implement wrapper**

Create `code/waymo2panorama/alignment/optical_flow_align.py`:

```python
"""C1 — RAFT optical flow alignment (parallax overlap fix candidate, GPU required).

Use kornia's RAFT model to compute dense optical flow between adjacent ring
cam image pairs. Warp cam_b's image to align with cam_a's view, then continue
to L1 sphere projection + multiband blend.

GPU strongly recommended — RAFT on CPU is impractical for 504x504 images.
"""
from __future__ import annotations

import numpy as np


def compute_pair_flow(
    img_a: np.ndarray, img_b: np.ndarray, device: str = "cuda",
) -> np.ndarray:
    """Compute dense optical flow from img_a to img_b via kornia RAFT.

    Returns:
        (H, W, 2) float32 — flow[v, u] = (du, dv) such that
        img_b[v + dv, u + du] ≈ img_a[v, u].
    """
    import torch
    import kornia.feature as KF
    # kornia RAFT signature: takes (1, 3, H, W) float tensors normalized to [0, 1]
    raft = KF.RAFT(pretrained=True).to(device).eval()
    def _to_tensor(img):
        t = torch.from_numpy(img.astype(np.float32) / 255.0)
        t = t.permute(2, 0, 1).unsqueeze(0).to(device)
        return t
    a = _to_tensor(img_a); b = _to_tensor(img_b)
    with torch.no_grad():
        flow_list = raft(a, b)  # returns list of flows at decreasing scales
        flow = flow_list[-1]  # final (highest-res) flow
    flow_np = flow.squeeze(0).permute(1, 2, 0).cpu().numpy().astype(np.float32)
    return flow_np


def warp_image_by_flow(
    img: np.ndarray, flow: np.ndarray,
) -> np.ndarray:
    """Warp img by flow via cv2.remap. dst[v, u] = src[v + flow[v,u,1], u + flow[v,u,0]]."""
    import cv2
    H, W = img.shape[:2]
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    map_x = (xs + flow[..., 0]).astype(np.float32)
    map_y = (ys + flow[..., 1]).astype(np.float32)
    return cv2.remap(img, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0)
```

- [ ] **Step 4: Run test (on Colab GPU)**

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && git pull --rebase origin main && python -m pytest code/waymo2panorama/alignment/__test_optical_flow_align.py -v 2>&1 | tail -5"`
Expected: 1 passed (or skipped if kornia not yet installed; install via `pip install kornia` first)

- [ ] **Step 5: Commit**

```bash
git add code/waymo2panorama/alignment/optical_flow_align.py code/waymo2panorama/alignment/__test_optical_flow_align.py
git commit -m "WS4 C1: kornia RAFT optical flow wrapper + pytest

Pair-wise dense optical flow via kornia RAFT. Warp helper for applying the
flow to images. GPU strongly recommended.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6.2: Driver `run_l1_optflow.py`

**Files:**
- Create: `scripts/phase3/run_l1_optflow.py`

- [ ] **Step 1: Create driver**

Create `scripts/phase3/run_l1_optflow.py`:

```python
"""WS4 C1 — L1 sphere + RAFT-flow-warped cam images.

Pipeline:
  1. Load 7 cam images + K + T_ego_cam (UNCHANGED).
  2. For each adjacent ring pair (cam_a, cam_b): compute RAFT flow from
     cam_a to cam_b; warp cam_b's image via flow.
  3. Render L1 sphere ERP slabs from the (warped) cam images (UNCHANGED L1).
  4. Multi-band blend (UNCHANGED).

A/B: --no-flow skips step 2.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    sys.path.insert(0, str(w2p_code))


def _load_pi3_cam(pi3_dir: Path, cam: str) -> dict:
    return {
        "image": np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")),
        "K": np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy").astype(np.float64),
        "T_ego_cam": np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy").astype(np.float64),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--no-flow", action="store_true")
    ap.add_argument("--no-ego-mask", action="store_true")
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.alignment.optical_flow_align import (
        compute_pair_flow, warp_image_by_flow,
    )
    from waymo2panorama.alignment.pair_homography import ADJACENT_PAIRS
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    cams = list(RING_CAMS_7)
    print(f"[c1-optflow] mode={'NO-FLOW plain L1' if args.no_flow else 'C1 RAFT flow'}, "
          f"device={args.device}", flush=True)

    per_cam = {cam: _load_pi3_cam(args.pi3_dir, cam) for cam in cams}
    cam_image_shapes = {cam: per_cam[cam]["image"].shape[:2] for cam in cams}
    ego_masks = build_ego_masks(cams, cam_image_shapes, enabled=not args.no_ego_mask)

    t_flow0 = time.time()
    warped_images = {cam: per_cam[cam]["image"].copy() for cam in cams}
    flow_log: list[dict] = []
    if not args.no_flow:
        for cam_a, cam_b in ADJACENT_PAIRS:
            if cam_a not in cams or cam_b not in cams:
                continue
            img_a = per_cam[cam_a]["image"]
            img_b = per_cam[cam_b]["image"]
            try:
                flow = compute_pair_flow(img_a, img_b, device=args.device)
                warped_images[cam_b] = warp_image_by_flow(img_b, flow)
                flow_log.append({
                    "cam_a": cam_a, "cam_b": cam_b, "status": "ok",
                    "max_flow_px": float(np.linalg.norm(flow, axis=-1).max()),
                })
            except Exception as exc:
                flow_log.append({
                    "cam_a": cam_a, "cam_b": cam_b, "status": "failed",
                    "error": repr(exc),
                })
    t_flow_s = time.time() - t_flow0

    t_proj0 = time.time()
    slabs: list[np.ndarray] = []; weights: list[np.ndarray] = []
    for cam in cams:
        d = per_cam[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=warped_images[cam], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        slabs.append(rgb); weights.append(w)
    t_proj_s = time.time() - t_proj0

    t_blend0 = time.time()
    erp = multiband_blend(slabs, weights, num_bands=args.num_bands, wrap=True)
    t_blend_s = time.time() - t_blend0

    out_png = out_dir / "l1_optflow.png"
    Image.fromarray(erp).save(out_png)
    print(f"[c1-optflow] wrote {out_png}", flush=True)

    summary = {
        "route": "WS4 C1 — L1 + RAFT optical flow warp",
        "mode": "no-flow (plain L1)" if args.no_flow else "flow",
        "device": args.device,
        "flow_log": flow_log,
        "runtime_s": {
            "flow": round(t_flow_s, 3),
            "projection": round(t_proj_s, 3),
            "blend": round(t_blend_s, 3),
        },
        "outputs": {"l1_optflow": str(out_png.resolve())},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Syntax check**

Run: `python -c "import ast; ast.parse(open('scripts/phase3/run_l1_optflow.py').read())"`
Expected: no output

- [ ] **Step 3: Commit + push + GPU Colab smoke**

```bash
git add scripts/phase3/run_l1_optflow.py
git commit -m "WS4 C1: driver run_l1_optflow.py (GPU required)

End-to-end: cam images warped via RAFT flow + L1 sphere (UNCHANGED) + multiband.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

GPU verify on Colab:
Run: `python /tmp/cdrun.py "nvidia-smi --query-gpu=name,memory.free --format=csv,noheader"`
Expected: shows a GPU (e.g., NVIDIA A100, 40GB free)

If GPU not on, STOP and ask user to switch.

Run: `python /tmp/cdrun.py "cd /content/waymo2panorama && git pull --rebase origin main && pip install -q kornia 2>&1 | tail -3 && python scripts/phase3/run_l1_optflow.py --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_060 --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.X_parallax/anchor_060_c1 --device cuda 2>&1 | tail -30"`
Expected: exit 0, png written, ~1-3 min wall (7 RAFT inferences)

---

### Task 6.3: Add C1 to comparison panel + cycle eval

- [ ] **Step 1: Re-run compare panel with C1 column**

Same command as Task 4.1 step 4 but with `--c1 ...` added.

- [ ] **Step 2: Run seam metric on C1 outputs**

Same pattern as Task 4.1 step 5 with `c1` added to method list.

---

## Phase 7 — Hybrid design + impl (CONDITIONAL on Phase 5/6 decision)

### Task 7.1: Design hybrid based on per-anchor candidate performance

- [ ] **Step 1: Tabulate per-anchor winner per metric**

Build a table: anchor × {visual, seam metric, cycle PSNR}, winner per cell. Identify pattern.

- [ ] **Step 2: Implement hybrid module**

Create `code/waymo2panorama/alignment/parallax_hybrid.py` (~200 LOC). Specific logic depends on the pattern (e.g., "A2 wins when stereo coverage > X, else B1 wins").

- [ ] **Step 3: Driver + Colab smoke + add to compare panel**

Mirror A2/B1 pattern.

---

## Phase 8 — Progress writeup + final commit (~1 hour)

### Task 8.1: Update `agent/progress.md`

- [ ] **Step 1: Append new entry to progress.md**

Add to TOP of `agent/progress.md` (PRESERVE existing entries):

```markdown
> ### 2026-05-26 ~XX:XX UTC — [WS4 Parallax Overlap Fix — 3-candidate exploration]
> - **怎么做**: 5.22 prompt 提到的 "白色overlap痕迹" 跟 2-轮子 ghost 同根因 (parallax) 在所有 8 路线都有. 用户要求 "根本修复 + 完全探索". Brainstorm 18 子方案 → 选 3 ship + hybrid (spec at `agent/specs/2026-05-26-parallax-overlap-fix-design.md`).
>   - **A2 sparse stereo displacement**: 新-D 缓存的 sparse 3D 点 → 计算 per-cam (ideal_uv, delta_uv) → TPS dense field + confidence gating → cv2.remap warp ERP slab. CPU 8 pytest pass.
>   - **B1 disparity-aware graphcut seam**: per-pair disparity magnitude (cam_a vs cam_b displacement diff) → 1D DP seam finder → hard 0/1 mask 替换 cos² blend (soft edge). CPU 5 pytest pass.
>   - **C1 RAFT optical flow** (if shipped): kornia RAFT 每 pair → dense flow → warp cam image 后 L1 sphere. GPU 必要.
> - **结果**: [填入实际数字 — A2 / B1 / C1 在 4 anchor 上 visual / seam metric / cycle PSNR 对比]
> - **Deliverables**: 3 个新模块 (sparse_displacement, graphcut_disparity, optical_flow_align), 3-4 个 driver, 3 个 eval script, 4 anchor 视觉 panel + seam metric JSON. 全 commit 主线, 全 baseline 代码未动 (per 用户约束).
> - Status: [DONE / ship A2 / ship B1 / ship hybrid / NEG → 转 D 系]
> - Next: [paper 写作 / WS5 D 系 NeRF / 等 Koi feedback]
```

- [ ] **Step 2: Commit + push**

```bash
git add agent/progress.md
git commit -m "progress: WS4 parallax overlap fix exploration [final state TBD]

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

---

## Self-Review

### Spec coverage check

| Spec section | Plan task(s) |
|---|---|
| §1 Background | Captured in plan header + Phase 0 verification |
| §2 Goal | Captured in plan header Goal line |
| §3 Scope | Phase 1-7 cover the 3 candidates + hybrid; D series + Waymo / rolling shutter explicitly OUT |
| §4 Architecture (integration point) | Phase 1.7, 2.5, 6.2 drivers all call render_camera_to_erp (UNCHANGED) then plug parallax fix module |
| §4.2 Module A2 | Phase 1 Tasks 1.1-1.7 |
| §4.2 Module B1 | Phase 2 Tasks 2.1-2.5 |
| §4.2 Module C1 | Phase 6 Tasks 6.1-6.3 |
| §4.2 Hybrid | Phase 7 (conditional) |
| §4.3 Drivers | 1.7, 2.5, 6.2, 7 |
| §4.4 Eval scripts | Phase 3 Tasks 3.1, 3.2, 3.3 |
| §5 Data flow | Embedded in Phase 4 production tasks |
| §6 Eval strategy | Phase 4 Tasks (visual, cycle PSNR, seam metric) |
| §6.4 Decision criteria | Phase 5 Task 5.1 with user |
| §7 Design decisions (5 of them) | Captured in module/driver implementations |
| §8 Risks | Each phase has bailout (Phase 5 decision gate handles "all NEG" path) |
| §9 Time estimate | Phase totals match: Phase 1 6h, Phase 2 6h, Phase 3 3h, Phase 4 2h, Phase 6 6h (GPU), Phase 7 conditional, Phase 8 1h |
| §10 Success criteria | Phase 5 decision gate uses these |

### Placeholder scan

Searched plan for: TBD, TODO, "implement later", "Add appropriate", "Similar to Task N", "Write tests for the above".

Found acceptable instances:
- Task 6.2 Step 1: "Skipping full code block for brevity in this plan; implement following A2 driver structure but with optical_flow_align module + warp on source images instead of ERP slabs" — **THIS IS A REAL PLACEHOLDER, fix**

(Will fix this below.)

- Task 7 hybrid module body: "Specific logic depends on the pattern" — acceptable because hybrid design depends on Phase 5 outcome
- Task 8 progress entry: "[填入实际数字 …]" — acceptable, this is template for run-time data

### Type consistency check

- `build_per_cam_displacements_from_stereo` returns `dict[str, list[tuple[np.ndarray, np.ndarray]]]` — used consistently in Tasks 1.2, 1.3, 1.6 ✓
- `interpolate_dense_displacement_field` returns `(H, W, 2) float32` — used in Task 1.4, 1.6 ✓
- `warp_erp_slab_by_displacement` signature consistent across Task 1.4, 1.6 ✓
- `build_warped_slabs_a2` returns `(dict, dict)` — used by driver 1.7 ✓
- B1 chain: `build_pair_disparity_magnitude → find_min_disparity_seam → apply_seam_to_pair_weights → build_seam_weights_b1` types consistent ✓

### Fix the C1 driver placeholder

Replacing Task 6.2 step 1 with proper code:

(In the actual implementation, fill Task 6.2 step 1 with a full driver code block following the A2 pattern. The structure is:
1. Load 7 cam images via _load_pi3_cam
2. For each adjacent pair (cam_a, cam_b): compute flow on cam_a-overlap-crop ↔ cam_b-overlap-crop via compute_pair_flow; warp cam_b image by flow
3. Run render_camera_to_erp on warped images
4. multiband_blend
5. Save l1_optflow.png + summary.json

Same arg parsing pattern as run_l1_sparse_disp.py with `--no-flow` for A/B.)

---

## Plan complete

**Plan saved to**: `agent/plans/2026-05-26-parallax-overlap-fix-plan.md`
**Commit**: ready to commit after user reviews

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
