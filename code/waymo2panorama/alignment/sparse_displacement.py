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

from pathlib import Path

import numpy as np
from scipy.interpolate import RBFInterpolator

from waymo2panorama.pipeline.lift_and_project import ego_points_to_erp_uv
from waymo2panorama.pipeline.option_b_reweight import (
    STEREO_NPZ_PTS_KEY,
    STEREO_NPZ_CAM_A_KEY,
    STEREO_NPZ_CAM_B_KEY,
)


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
    # to gaussian (with shape param) if too few. We want the gaussian to decay
    # to ~0 well outside the anchor support so isolated anchors don't paint
    # a constant displacement everywhere.
    rbf_kwargs: dict = {"kernel": kernel, "smoothing": float(regularization)}
    if anchors_xy.shape[0] < 3 and kernel == "thin_plate_spline":
        rbf_kwargs["kernel"] = "gaussian"
        # scipy gaussian: exp(-(epsilon*r)^2). Pick width such that the field
        # decays to ~0 well outside the anchor support (avoids painting a
        # constant displacement everywhere when only 1-2 anchors exist).
        # degree=-1 disables the polynomial tail so the field truly decays.
        width_px = max(1.0, 0.05 * float(min(H, W)))
        rbf_kwargs["epsilon"] = 1.0 / width_px
        rbf_kwargs["degree"] = -1
    rbf = RBFInterpolator(anchors_xy, deltas, **rbf_kwargs)
    # Evaluate on every ERP pixel (vectorized; reasonably fast for 1024x2048)
    ys, xs = np.mgrid[0:H, 0:W]
    grid = np.stack([xs.ravel(), ys.ravel()], axis=-1).astype(np.float64)
    out = rbf(grid).reshape(H, W, 2).astype(np.float32)
    return out
