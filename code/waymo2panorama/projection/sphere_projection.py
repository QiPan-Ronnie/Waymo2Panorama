"""
Spherical projection: one perspective camera image -> ERP slab (Phase 1, L1 baseline).

L1 assumption: ignore the camera's translation. Treat every ring camera as if it
were mounted at the ego-vehicle origin. Only the camera's rotation matters.

Consequences:
  + Math is trivial; no depth needed; runs on CPU.
  + Correct for far objects (where parallax is small).
  - Wrong for close objects: same 3D point seen by two cams maps to two different
    ERP locations -> visible ghosting near the seams. This is EXPECTED at L1 and
    is exactly the failure mode Phase 2 (Pi3/DVGT 3D-lift) is meant to fix.

Conventions:
  - ERP image: 2:1 aspect. Origin top-left.
      u in [0, W_erp)  ->  azimuth theta in [-pi, +pi)   (0 = +x = forward, increases CCW)
      v in [0, H_erp)  ->  elevation phi in [+pi/2, -pi/2]  (top = up)
  - Ego frame (AV2): x forward, y left, z up. Right-handed.
  - Camera frame (OpenCV convention): x right, y down, z forward.
  - K @ (X/Z, Y/Z, 1) -> (u_img, v_img, 1) with image origin top-left, +u right, +v down.
  - T_ego_cam (4x4 SE(3)) maps a point in camera frame to a point in ego frame.
"""
from __future__ import annotations

import cv2
import numpy as np


def render_camera_to_erp(
    image: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    erp_hw: tuple[int, int] = (1024, 2048),
    ego_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Render one camera image onto an ERP canvas.

    Args:
        image:      (H_src, W_src, 3) uint8 RGB camera image.
        K:          (3, 3) pinhole intrinsics matrix.
        T_ego_cam:  (4, 4) SE(3) that maps a 3D point from cam frame to ego frame.
        erp_hw:     (H_erp, W_erp). H_erp:W_erp must be 1:2 for valid equirectangular.
        ego_mask:   optional (H_src, W_src) uint8 mask in source-image coords.
                    Value 1 keeps the pixel; value 0 excludes it (e.g. ego vehicle hood).

    Returns:
        erp_rgb     (H_erp, W_erp, 3) float32, sampled RGB in [0, 255]
        erp_alpha   (H_erp, W_erp)    bool,    True where this camera contributed
        erp_weight  (H_erp, W_erp)    float32 in [0, 1], feather weight for blending
    """
    if image.dtype != np.uint8:
        raise ValueError(f"expected uint8 image, got {image.dtype}")
    h_erp, w_erp = erp_hw
    if w_erp != 2 * h_erp:
        # not fatal but worth a heads-up
        pass

    h_img, w_img = image.shape[:2]
    img_f32 = image.astype(np.float32)

    # 1) Build ERP pixel grid -> (theta, phi)
    u_idx = np.arange(w_erp, dtype=np.float64)
    v_idx = np.arange(h_erp, dtype=np.float64)
    uu, vv = np.meshgrid(u_idx, v_idx)
    theta = (uu + 0.5) / w_erp * (2.0 * np.pi) - np.pi          # azimuth in (-pi, pi]
    phi = (np.pi / 2.0) - (vv + 0.5) / h_erp * np.pi              # elevation in (pi/2, -pi/2)

    # 2) Unit ray in ego frame
    cos_phi = np.cos(phi)
    d_ego = np.stack(
        [
            cos_phi * np.cos(theta),  # x = forward
            cos_phi * np.sin(theta),  # y = left
            np.sin(phi),              # z = up
        ],
        axis=-1,
    )  # (H_erp, W_erp, 3)

    # 3) Rotate ray to camera frame: d_cam = R_ego_cam.T @ d_ego
    R_ego_cam = T_ego_cam[:3, :3]
    R_cam_ego = R_ego_cam.T
    d_cam = d_ego @ R_cam_ego.T  # equivalent to einsum('ij,...j->...i', R_cam_ego, d_ego)

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
    # In cam frame, optical axis is +z, so cos(angle) = z_cam for a unit ray.
    cos_axis = np.clip(z_cam, 0.0, 1.0)
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
