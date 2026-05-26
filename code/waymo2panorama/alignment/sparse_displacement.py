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
