"""
Cylindrical projection: one perspective camera image -> cylindrical ERP-like slab
(Phase 3, route 10 / 新-A, L2 baseline).

L2 motivation vs L1 sphere:
  - 7 AV2 ring cams are mounted at roughly the same height on the roof — their
    optical centers form a (near-)horizontal ring. Geometrically this matches a
    *cylinder* much better than a sphere: vertical lines (poles, building edges,
    light posts) project to vertical columns on the cylinder, whereas the sphere
    bends them toward the poles ("equirectangular distortion").
  - Same translation-ignored assumption as L1 (every cam mounted at ego origin,
    only rotation matters). Depth-free, CPU-only, no Pi3 needed.

Mapping (canonical horizontal cylinder, axis = ego z = up):
  Forward 3D point (x_e, y_e, z_e) in ego frame -> (u, v) on cylinder:
      theta = atan2(y_e, x_e)            # horizontal angle (same as sphere)
      r_xy  = sqrt(x_e^2 + y_e^2)        # cylinder radial distance
      h     = z_e / r_xy                 # *vertical tangent*, NOT asin(z/r)

Compared to sphere (`sphere_projection.py`):
   sphere uses phi = asin(z / r),  r = sqrt(x^2+y^2+z^2)
   cylinder uses h = z / r_xy,    r_xy = sqrt(x^2+y^2)
The cylinder is just an unrolled side-surface: a column at azimuth theta is a
straight line in z/r_xy.  Practical canvas mapping: pick a `v_scale` so the
finite vertical FOV of the ring cams (roughly |h| < 1 i.e. 45 deg elevation)
fills the canvas.

Conventions (must match `sphere_projection.py`):
  - Output canvas: (H_erp, W_erp) = (1024, 2048), 2:1 aspect.
      u in [0, W_erp)  ->  theta in [pi, -pi)   (forward = column W/2)
      v in [0, H_erp)  ->  h in [+v_max, -v_max]   (top = up)
  - Ego frame (AV2): x forward, y left, z up. Right-handed.
  - Camera frame (OpenCV): x right, y down, z forward.

The output is *compatible* with the existing multi-band blending pipeline
(`waymo2panorama.blending.multiband.multiband_blend`), so we can swap this in
for `render_camera_to_erp` and reuse everything downstream.
"""
from __future__ import annotations

import cv2
import numpy as np


# ---- cylinder vertical-FOV constant -----------------------------------------
# v_max controls how much elevation the 1024-row canvas covers.
# h = tan(elevation). The 7 ring cams' useful elevation is roughly [-30 deg, +30 deg]
# (hood + low sky). h = tan(30 deg) ~ 0.577. We extend a bit beyond to keep
# tall close-by objects (street lights, building tops) inside the canvas.
# v_max = 1.0 => visible elevation [-45 deg, +45 deg].
DEFAULT_V_MAX: float = 1.0


def render_camera_to_cylinder(
    image: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    erp_hw: tuple[int, int] = (1024, 2048),
    ego_mask: np.ndarray | None = None,
    v_max: float = DEFAULT_V_MAX,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Render one camera image onto a cylindrical canvas (ERP-shaped, 2:1).

    Args:
        image:      (H_src, W_src, 3) uint8 RGB camera image.
        K:          (3, 3) pinhole intrinsics matrix.
        T_ego_cam:  (4, 4) SE(3) that maps a 3D point from cam frame to ego frame.
        erp_hw:     (H_erp, W_erp). 2:1 aspect recommended (matches sphere).
        ego_mask:   optional (H_src, W_src) uint8 mask in source-image coords.
                    Value 1 keeps the pixel; value 0 excludes it (e.g. ego hood).
        v_max:      vertical extent on the cylinder. h in [-v_max, +v_max] maps
                    onto the canvas rows. Default 1.0 (~ +/-45 deg elevation).

    Returns:
        cyl_rgb     (H_erp, W_erp, 3) float32, sampled RGB in [0, 255]
        cyl_alpha   (H_erp, W_erp)    bool,    True where this camera contributed
        cyl_weight  (H_erp, W_erp)    float32 in [0, 1], feather weight for blending
    """
    if image.dtype != np.uint8:
        raise ValueError(f"expected uint8 image, got {image.dtype}")
    h_erp, w_erp = erp_hw
    if v_max <= 0:
        raise ValueError(f"v_max must be > 0, got {v_max}")

    h_img, w_img = image.shape[:2]
    img_f32 = image.astype(np.float32)

    # 1) Build canvas pixel grid -> (theta, h)
    # Match `sphere_projection.py` horizontal convention: theta decreases as u increases.
    # WHY: AV2 ego y = +left; "unrolling" the world with the gaze sweeping RIGHT
    # means azimuth must DECREASE. This is the same convention as the sphere code
    # (post-2026-05-19 fix); the cylindrical canvas is *visually* identical to ERP
    # along the equator row.
    u_idx = np.arange(w_erp, dtype=np.float64)
    v_idx = np.arange(h_erp, dtype=np.float64)
    uu, vv = np.meshgrid(u_idx, v_idx)
    theta = np.pi - (uu + 0.5) / w_erp * (2.0 * np.pi)             # azimuth in (pi, -pi]
    h_cyl = v_max - (vv + 0.5) / h_erp * (2.0 * v_max)             # h in (+v_max, -v_max)

    # 2) Cylindrical ray in ego frame.
    # A point on the unit-radius cylinder at (theta, h) has ego coordinates
    #   x_e = cos(theta),  y_e = sin(theta),  z_e = h
    # The full direction vector (not unit) is therefore (cos t, sin t, h), and
    # the *unit* ray would be (cos t, sin t, h) / sqrt(1 + h^2). We don't need
    # to normalize for pinhole reprojection (only the direction matters).
    d_ego = np.stack(
        [
            np.cos(theta),  # x = forward
            np.sin(theta),  # y = left
            h_cyl,          # z = up (scaled by 1/r_xy = 1 because r_xy = 1 on unit cylinder)
        ],
        axis=-1,
    )  # (H_erp, W_erp, 3)

    # 3) Rotate ray to camera frame: d_cam = R_ego_cam.T @ d_ego
    R_ego_cam = T_ego_cam[:3, :3]
    R_cam_ego = R_ego_cam.T
    d_cam = d_ego @ R_cam_ego.T  # einsum('ij,...j->...i', R_cam_ego, d_ego)

    # 4) Pinhole projection (only valid in front of camera: z_cam > 0)
    z_cam = d_cam[..., 2]
    in_front = z_cam > 1e-6
    z_safe = np.where(in_front, z_cam, 1.0)
    u_img = K[0, 0] * (d_cam[..., 0] / z_safe) + K[0, 2]
    v_img = K[1, 1] * (d_cam[..., 1] / z_safe) + K[1, 2]

    margin = 0.5  # half-pixel
    in_bounds = (
        (u_img >= margin) & (u_img <= w_img - 1 - margin)
        & (v_img >= margin) & (v_img <= h_img - 1 - margin)
    )
    valid = in_front & in_bounds

    # 5) Bilinear sample via OpenCV remap
    map_x = np.where(valid, u_img, -1.0).astype(np.float32)
    map_y = np.where(valid, v_img, -1.0).astype(np.float32)
    sampled = cv2.remap(
        img_f32,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0.0, 0.0, 0.0),
    )

    # 6) Feather weight: cos^2(angle from optical axis).
    # In cam frame, optical axis is +z. The ray d_cam has magnitude
    # ||d_cam|| = ||d_ego|| = sqrt(1 + h^2), so cos(angle) = z_cam / ||d_cam||.
    # Using a per-pixel norm keeps the feather correct as h grows (top/bottom
    # rows of the canvas would otherwise be over-weighted).
    d_norm = np.sqrt(d_cam[..., 0] ** 2 + d_cam[..., 1] ** 2 + d_cam[..., 2] ** 2)
    d_norm = np.maximum(d_norm, 1e-9)
    cos_axis = np.clip(z_cam / d_norm, 0.0, 1.0)
    weight = (cos_axis ** 2).astype(np.float32)

    # 7) Optional ego mask in source-image coords (1 = keep, 0 = exclude)
    if ego_mask is not None:
        if ego_mask.shape[:2] != image.shape[:2]:
            ego_mask = cv2.resize(
                ego_mask, (w_img, h_img), interpolation=cv2.INTER_NEAREST,
            )
        mask_f = ego_mask.astype(np.float32)
        mask_sampled = cv2.remap(
            mask_f, map_x, map_y,
            interpolation=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0.0,
        )
        weight = weight * mask_sampled

    weight = np.where(valid, weight, 0.0).astype(np.float32)
    sampled = np.where(valid[..., None], sampled, 0.0).astype(np.float32)
    alpha = valid

    return sampled, alpha, weight


# Public alias matching the sphere module's function name, for drop-in swaps.
def render_camera_to_erp(
    image: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    erp_hw: tuple[int, int] = (1024, 2048),
    ego_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Drop-in API-compatible wrapper around `render_camera_to_cylinder`.

    Lets callers swap the import line:
        from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    for:
        from waymo2panorama.projection.cylinder import render_camera_to_erp
    without further changes.
    """
    return render_camera_to_cylinder(
        image=image,
        K=K,
        T_ego_cam=T_ego_cam,
        erp_hw=erp_hw,
        ego_mask=ego_mask,
    )
