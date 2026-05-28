"""LiDAR z-buffer layer for seam-local source-faithful rendering.

This module treats AV2 LiDAR as sparse surface evidence for a virtual
ego-center ERP camera. It is intentionally narrower than a full depth renderer:
callers can use it only in seam bands and fall back to L1 hard_select anywhere
LiDAR support or camera visibility is weak.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np


@dataclass
class CameraZBuffer:
    """A dilated per-camera LiDAR z-buffer in raw image coordinates."""

    depth_z_m: np.ndarray
    support: np.ndarray
    n_projected: int
    n_hit_pixels: int
    n_support_pixels: int


@dataclass
class LidarSurfaceRender:
    """Per-camera ERP renders from a LiDAR-supported surface."""

    slabs: list[np.ndarray]
    weights: list[np.ndarray]
    visible: list[np.ndarray]
    visible_count: np.ndarray
    support_mask: np.ndarray
    diagnostics: dict[str, object]


def erp_dirs_ego(erp_hw: tuple[int, int]) -> np.ndarray:
    """Return unit ERP rays in AV2 ego frame."""
    h_erp, w_erp = erp_hw
    uu, vv = np.meshgrid(
        np.arange(w_erp, dtype=np.float32),
        np.arange(h_erp, dtype=np.float32),
    )
    theta = np.pi - (uu + 0.5) / w_erp * (2.0 * np.pi)
    phi = (np.pi / 2.0) - (vv + 0.5) / h_erp * np.pi
    cos_phi = np.cos(phi)
    return np.stack(
        [
            cos_phi * np.cos(theta),
            cos_phi * np.sin(theta),
            np.sin(phi),
        ],
        axis=-1,
    ).astype(np.float32)


def _project_points_to_camera(
    points_ego: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    r_cam_ego = T_ego_cam[:3, :3].T
    t_ego_cam = T_ego_cam[:3, 3]
    p_cam = (points_ego - t_ego_cam[None, :]) @ r_cam_ego.T
    z = p_cam[:, 2]
    z_safe = np.where(z > 1e-9, z, 1.0)
    u = K[0, 0] * (p_cam[:, 0] / z_safe) + K[0, 2]
    v = K[1, 1] * (p_cam[:, 1] / z_safe) + K[1, 2]
    return u, v, z, p_cam


def build_camera_zbuffer(
    points_ego: np.ndarray,
    image_shape_hw: tuple[int, int],
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    *,
    min_range_m: float = 0.5,
    max_range_m: float = 80.0,
    dilation_px: int = 4,
) -> CameraZBuffer:
    """Project LiDAR points into one camera and keep nearest optical depth."""
    h_img, w_img = image_shape_hw
    ranges = np.linalg.norm(points_ego, axis=1)
    keep = (ranges >= float(min_range_m)) & (ranges <= float(max_range_m))
    pts = points_ego[keep].astype(np.float64)

    u, v, z, _p_cam = _project_points_to_camera(pts, K, T_ego_cam)
    margin = 0.5
    valid = (
        (z > 1e-6)
        & (u >= margin)
        & (u <= w_img - 1 - margin)
        & (v >= margin)
        & (v <= h_img - 1 - margin)
    )
    if not np.any(valid):
        depth = np.full((h_img, w_img), np.inf, dtype=np.float32)
        return CameraZBuffer(depth, np.zeros((h_img, w_img), dtype=bool), 0, 0, 0)

    uu = np.round(u[valid]).astype(np.int32)
    vv = np.round(v[valid]).astype(np.int32)
    zz = z[valid].astype(np.float32)

    # Far points first, near points overwrite.
    order = np.argsort(-zz)
    sparse = np.full((h_img, w_img), 1.0e6, dtype=np.float32)
    sparse[vv[order], uu[order]] = zz[order]
    hit = sparse < 1.0e6

    if dilation_px > 0:
        k = np.ones((2 * int(dilation_px) + 1, 2 * int(dilation_px) + 1), dtype=np.uint8)
        dense = cv2.erode(sparse, k, borderType=cv2.BORDER_CONSTANT, borderValue=1.0e6)
    else:
        dense = sparse
    support = dense < 1.0e6
    dense = np.where(support, dense, np.inf).astype(np.float32)
    return CameraZBuffer(
        depth_z_m=dense,
        support=support,
        n_projected=int(valid.sum()),
        n_hit_pixels=int(hit.sum()),
        n_support_pixels=int(support.sum()),
    )


def build_ring_zbuffers(
    points_ego: np.ndarray,
    images: Sequence[np.ndarray],
    Ks: Sequence[np.ndarray],
    T_ego_cams: Sequence[np.ndarray],
    *,
    min_range_m: float = 0.5,
    max_range_m: float = 80.0,
    dilation_px: int = 4,
) -> list[CameraZBuffer]:
    """Build z-buffers for all ring cameras."""
    out: list[CameraZBuffer] = []
    for image, K, T in zip(images, Ks, T_ego_cams):
        out.append(
            build_camera_zbuffer(
                points_ego,
                image.shape[:2],
                K,
                T,
                min_range_m=min_range_m,
                max_range_m=max_range_m,
                dilation_px=dilation_px,
            )
        )
    return out


def render_lidar_surface_to_erp(
    images: Sequence[np.ndarray],
    Ks: Sequence[np.ndarray],
    T_ego_cams: Sequence[np.ndarray],
    depth_map_ego_range_m: np.ndarray,
    zbuffers: Sequence[CameraZBuffer],
    *,
    depth_support_max_m: float = 120.0,
    min_cam_cos: float = 0.03,
    z_tolerance_abs_m: float = 0.75,
    z_tolerance_rel: float = 0.04,
) -> LidarSurfaceRender:
    """Render a LiDAR-supported virtual ERP surface into all cameras.

    The target surface is `P_ego = depth * ERP_ray_from_ego_origin`. A camera
    sample is accepted only if the same 3D point is close to that camera's
    LiDAR z-buffer, which rejects many occluded/disoccluded samples.
    """
    h_erp, w_erp = depth_map_ego_range_m.shape
    erp_hw = (h_erp, w_erp)
    support = np.isfinite(depth_map_ego_range_m) & (depth_map_ego_range_m < float(depth_support_max_m))
    dirs = erp_dirs_ego(erp_hw)
    p_ego = dirs * depth_map_ego_range_m.astype(np.float32)[..., None]

    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    visible_masks: list[np.ndarray] = []
    visible_count = np.zeros((h_erp, w_erp), dtype=np.uint8)
    per_cam_diag: list[dict[str, object]] = []

    for idx, (image, K, T, zbuf) in enumerate(zip(images, Ks, T_ego_cams, zbuffers)):
        h_img, w_img = image.shape[:2]
        r_cam_ego = T[:3, :3].T
        t_ego_cam = T[:3, 3].astype(np.float32)
        p_cam = (p_ego - t_ego_cam[None, None, :]) @ r_cam_ego.T.astype(np.float32)
        z = p_cam[..., 2]
        in_front = z > 1e-6
        z_safe = np.where(in_front, z, 1.0)
        u_img = K[0, 0] * (p_cam[..., 0] / z_safe) + K[0, 2]
        v_img = K[1, 1] * (p_cam[..., 1] / z_safe) + K[1, 2]
        margin = 0.5
        in_bounds = (
            (u_img >= margin)
            & (u_img <= w_img - 1 - margin)
            & (v_img >= margin)
            & (v_img <= h_img - 1 - margin)
        )
        norm = np.linalg.norm(p_cam, axis=-1)
        cam_cos = np.where(norm > 1e-9, z / np.maximum(norm, 1e-9), 0.0)
        geom_valid = support & in_front & in_bounds & (cam_cos >= float(min_cam_cos))

        map_x = np.where(geom_valid, u_img, -1.0).astype(np.float32)
        map_y = np.where(geom_valid, v_img, -1.0).astype(np.float32)
        sampled_rgb = cv2.remap(
            image.astype(np.float32),
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0.0, 0.0, 0.0),
        )
        sampled_z = cv2.remap(
            zbuf.depth_z_m,
            map_x,
            map_y,
            interpolation=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=1.0e6,
        )
        tol = np.maximum(float(z_tolerance_abs_m), float(z_tolerance_rel) * np.maximum(z, 0.0))
        visible = geom_valid & (sampled_z < 1.0e6) & (np.abs(z - sampled_z) <= tol)
        weight = np.where(visible, np.clip(cam_cos, 0.0, 1.0) ** 2, 0.0).astype(np.float32)
        sampled_rgb = np.where(visible[..., None], sampled_rgb, 0.0).astype(np.float32)
        slabs.append(sampled_rgb)
        weights.append(weight)
        visible_masks.append(visible)
        visible_count += visible.astype(np.uint8)
        per_cam_diag.append(
            {
                "cam_index": int(idx),
                "zbuffer_projected_points": int(zbuf.n_projected),
                "zbuffer_hit_pixels": int(zbuf.n_hit_pixels),
                "zbuffer_support_pixels": int(zbuf.n_support_pixels),
                "erp_geom_valid_pixels": int(geom_valid.sum()),
                "erp_visible_pixels": int(visible.sum()),
                "erp_visible_frac": float(visible.mean()),
                "erp_visible_support_frac": float(visible.sum() / max(1, int(support.sum()))),
            }
        )

    diagnostics = {
        "support_pixels": int(support.sum()),
        "support_frac": float(support.mean()),
        "visible_any_pixels": int((visible_count > 0).sum()),
        "visible_any_frac": float((visible_count > 0).mean()),
        "visible_any_support_frac": float((visible_count > 0).sum() / max(1, int(support.sum()))),
        "visible_ge2_pixels": int((visible_count >= 2).sum()),
        "visible_ge2_support_frac": float((visible_count >= 2).sum() / max(1, int(support.sum()))),
        "per_cam": per_cam_diag,
        "params": {
            "depth_support_max_m": float(depth_support_max_m),
            "min_cam_cos": float(min_cam_cos),
            "z_tolerance_abs_m": float(z_tolerance_abs_m),
            "z_tolerance_rel": float(z_tolerance_rel),
        },
    }
    return LidarSurfaceRender(
        slabs=slabs,
        weights=weights,
        visible=visible_masks,
        visible_count=visible_count,
        support_mask=support,
        diagnostics=diagnostics,
    )
