"""Raw-AV2 ground-plane layer for seam-region experiments.

This module changes the projection geometry only for road-plane candidates.
It does not need Pi3, optical flow, diffusion, or a depth network.

The idea is deliberately narrow:
  * keep the standard L1 sphere hard-select panorama as the base image;
  * render a second set of per-camera slabs by intersecting ERP rays with the
    ego-frame ground plane z=0 and projecting that 3D point into each camera;
  * replace only seam-band pixels where adjacent ground-plane samples agree.

If the pixel is a real ground/lane marking, the plane projection should reduce
the baseline-induced offset. If it is a vehicle/pedestrian/facade/occlusion,
the two cameras usually disagree and the conservative gate falls back to L1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band


@dataclass
class GroundPlaneCameraRender:
    """One camera rendered through the shared ground plane."""

    rgb: np.ndarray
    alpha: np.ndarray
    weight: np.ndarray
    distance_m: np.ndarray


def _erp_dirs_ego(erp_hw: tuple[int, int]) -> np.ndarray:
    """Return unit ERP rays in AV2 ego frame."""
    h_erp, w_erp = erp_hw
    u_idx = np.arange(w_erp, dtype=np.float64)
    v_idx = np.arange(h_erp, dtype=np.float64)
    uu, vv = np.meshgrid(u_idx, v_idx)
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
    )


def render_ground_plane_to_erp(
    image: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    erp_hw: tuple[int, int],
    panorama_center_ego: np.ndarray,
    ground_z_m: float = 0.0,
    min_distance_m: float = 1.5,
    max_distance_m: float = 55.0,
    min_cam_cos: float = 0.05,
) -> GroundPlaneCameraRender:
    """Render one camera onto ERP by inverse-mapping through the ground plane.

    For each ERP pixel, the virtual panorama ray starts at
    `panorama_center_ego`. If the ray intersects z=ground_z_m in front of the
    virtual viewer, that 3D ground point is projected into the real camera.
    Valid projections are sampled with bilinear interpolation.

    This is the reverse-map counterpart of `ipm_project_ground`: it avoids the
    stippled holes created by forward splatting and works directly on raw AV2
    images rather than Pi3 letterboxed cache images.
    """
    if image.dtype != np.uint8:
        raise ValueError(f"expected uint8 image, got {image.dtype}")
    if panorama_center_ego.shape != (3,):
        raise ValueError("panorama_center_ego must be a length-3 vector")

    h_erp, w_erp = erp_hw
    h_img, w_img = image.shape[:2]
    center = panorama_center_ego.astype(np.float64)
    dirs = _erp_dirs_ego(erp_hw)

    dz = dirs[..., 2]
    denom_ok = np.abs(dz) > 1e-8
    s = np.where(denom_ok, (float(ground_z_m) - center[2]) / np.where(denom_ok, dz, 1.0), -1.0)
    p_ego = center[None, None, :] + s[..., None] * dirs
    dist = np.linalg.norm(p_ego - center[None, None, :], axis=-1)
    ground_candidate = (
        denom_ok
        & (s > 0.0)
        & np.isfinite(dist)
        & (dist >= float(min_distance_m))
        & (dist <= float(max_distance_m))
    )

    r_ego_cam = T_ego_cam[:3, :3]
    r_cam_ego = r_ego_cam.T
    t_ego_cam = T_ego_cam[:3, 3]
    p_cam = (p_ego - t_ego_cam[None, None, :]) @ r_cam_ego.T
    z_cam = p_cam[..., 2]
    in_front = z_cam > 1e-6
    z_safe = np.where(in_front, z_cam, 1.0)
    u_img = K[0, 0] * (p_cam[..., 0] / z_safe) + K[0, 2]
    v_img = K[1, 1] * (p_cam[..., 1] / z_safe) + K[1, 2]

    margin = 0.5
    in_bounds = (
        (u_img >= margin)
        & (u_img <= w_img - 1 - margin)
        & (v_img >= margin)
        & (v_img <= h_img - 1 - margin)
    )
    cam_norm = np.linalg.norm(p_cam, axis=-1)
    cam_cos = np.where(cam_norm > 1e-9, z_cam / np.maximum(cam_norm, 1e-9), 0.0)
    view_ok = cam_cos >= float(min_cam_cos)
    valid = ground_candidate & in_front & in_bounds & view_ok

    map_x = np.where(valid, u_img, -1.0).astype(np.float32)
    map_y = np.where(valid, v_img, -1.0).astype(np.float32)
    sampled = cv2.remap(
        image.astype(np.float32),
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0.0, 0.0, 0.0),
    )
    sampled = np.where(valid[..., None], sampled, 0.0).astype(np.float32)

    # Prefer cameras that see the ground point near their optical axis, with a
    # weak distance falloff so far-horizon road does not dominate seams.
    weight = (np.clip(cam_cos, 0.0, 1.0) ** 2).astype(np.float32)
    weight *= np.exp(-dist / max(float(max_distance_m), 1.0)).astype(np.float32)
    weight = np.where(valid, weight, 0.0).astype(np.float32)
    distance = np.where(valid, dist, np.inf).astype(np.float32)
    return GroundPlaneCameraRender(
        rgb=sampled,
        alpha=valid,
        weight=weight,
        distance_m=distance,
    )


def estimate_panorama_center_z(cam_T_ego_cam: Sequence[np.ndarray], fallback_z_m: float = 1.55) -> float:
    """Use the median ring-camera height as the virtual panorama height."""
    zs = [float(T[:3, 3][2]) for T in cam_T_ego_cam if np.isfinite(T[:3, 3][2])]
    if not zs:
        return float(fallback_z_m)
    z = float(np.median(zs))
    if not np.isfinite(z) or z <= 0.2:
        return float(fallback_z_m)
    return z


def _to_y(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]


def build_ground_replace_mask(
    sphere_weights: Sequence[np.ndarray],
    ground_slabs: Sequence[np.ndarray],
    ground_weights: Sequence[np.ndarray],
    *,
    band_half_width: int = 96,
    core_half_width: int = 3,
    min_ground_weight: float = 0.01,
    agree_y_thresh: float = 18.0,
    max_structure_y: float | None = None,
    y_min_frac: float = 0.42,
) -> tuple[np.ndarray, dict]:
    """Find seam-band pixels where the ground-plane layer is trustworthy.

    The mask is pairwise: for each adjacent camera pair, only pixels inside the
    current sphere hard-select seam band are considered. Both cameras must have
    valid ground-plane samples at that ERP pixel, and their Y-channel values
    must agree within `agree_y_thresh`.
    """
    h, w = sphere_weights[0].shape
    y_floor = int(round(float(y_min_frac) * h))
    lower = np.zeros((h, w), dtype=bool)
    lower[y_floor:, :] = True

    replace = np.zeros((h, w), dtype=bool)
    seam_band_all = np.zeros((h, w), dtype=bool)
    seam_core_all = np.zeros((h, w), dtype=bool)
    pair_diags: list[dict] = []

    ground_y = [_to_y(s) for s in ground_slabs]
    if max_structure_y is not None:
        structure = np.zeros((h, w), dtype=np.float32)
        for gy in ground_y:
            gx = cv2.Sobel(gy.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
            gyv = cv2.Sobel(gy.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
            structure = np.maximum(structure, cv2.magnitude(gx, gyv))
        structure_ok = structure <= float(max_structure_y)
    else:
        structure_ok = np.ones((h, w), dtype=bool)

    for i, j in RING_PAIRS:
        if i >= len(sphere_weights) or j >= len(sphere_weights):
            pair_diags.append({"pair": [int(i), int(j)], "status": "missing_cam", "band_pixels": 0})
            continue
        wi = sphere_weights[i].astype(np.float32)
        wj = sphere_weights[j].astype(np.float32)
        overlap = (wi > 1e-6) & (wj > 1e-6)
        band, signed = build_voronoi_seam_band(wi, wj, band_half_width=band_half_width)
        band &= overlap & lower
        core = band & (np.abs(signed) <= float(core_half_width))
        seam_band_all |= band
        seam_core_all |= core
        if not band.any():
            pair_diags.append({"pair": [int(i), int(j)], "status": "no_band", "band_pixels": 0})
            continue

        valid_ground = (
            (ground_weights[i] >= float(min_ground_weight))
            & (ground_weights[j] >= float(min_ground_weight))
        )
        ydiff = np.abs(ground_y[i].astype(np.float32) - ground_y[j].astype(np.float32))
        ok = band & valid_ground & (ydiff <= float(agree_y_thresh)) & structure_ok
        replace |= ok
        vals = ydiff[band & valid_ground]
        pair_diags.append(
            {
                "pair": [int(i), int(j)],
                "status": "ok",
                "band_pixels": int(band.sum()),
                "core_pixels": int(core.sum()),
                "valid_ground_pixels": int((band & valid_ground).sum()),
                "replace_pixels": int(ok.sum()),
                "replace_frac_band": float(ok.sum() / max(1, int(band.sum()))),
                "ground_ydiff_mean": float(vals.mean()) if vals.size else None,
                "ground_ydiff_p50": float(np.percentile(vals, 50)) if vals.size else None,
                "ground_ydiff_p90": float(np.percentile(vals, 90)) if vals.size else None,
            }
        )

    diag = {
        "band_half_width": int(band_half_width),
        "core_half_width": int(core_half_width),
        "min_ground_weight": float(min_ground_weight),
        "agree_y_thresh": float(agree_y_thresh),
        "max_structure_y": None if max_structure_y is None else float(max_structure_y),
        "y_min_frac": float(y_min_frac),
        "seam_band_pixels": int(seam_band_all.sum()),
        "seam_core_pixels": int(seam_core_all.sum()),
        "replace_pixels": int(replace.sum()),
        "replace_frac_erp": float(replace.mean()),
        "replace_frac_band": float(replace.sum() / max(1, int(seam_band_all.sum()))),
        "pair_diags": pair_diags,
    }
    return replace, diag


def compose_ground_plane_hybrid(
    sphere_slabs: Sequence[np.ndarray],
    sphere_weights: Sequence[np.ndarray],
    ground_slabs: Sequence[np.ndarray],
    ground_weights: Sequence[np.ndarray],
    *,
    band_half_width: int = 96,
    core_half_width: int = 3,
    min_ground_weight: float = 0.01,
    agree_y_thresh: float = 18.0,
    max_structure_y: float | None = None,
    y_min_frac: float = 0.42,
    return_diagnostics: bool = False,
) -> np.ndarray | tuple[np.ndarray, dict]:
    """Replace trusted road-plane seam pixels in the L1 hard-select output."""
    base = hard_select(list(sphere_slabs), list(sphere_weights))
    ground = hard_select(list(ground_slabs), list(ground_weights))
    replace, diag = build_ground_replace_mask(
        sphere_weights,
        ground_slabs,
        ground_weights,
        band_half_width=band_half_width,
        core_half_width=core_half_width,
        min_ground_weight=min_ground_weight,
        agree_y_thresh=agree_y_thresh,
        max_structure_y=max_structure_y,
        y_min_frac=y_min_frac,
    )
    out = base.copy()
    out[replace] = ground[replace]
    diag["ground_valid_pixels"] = int((np.stack(ground_weights, axis=0).max(axis=0) > 0).sum())
    diag["base_changed_pixels"] = int(np.any(out != base, axis=-1).sum())
    if return_diagnostics:
        diag["replace_mask"] = replace
        return out, diag
    return out


def replace_mask_overlay(rgb: np.ndarray, replace_mask: np.ndarray) -> np.ndarray:
    """Visualize replacement mask in cyan over an RGB panorama."""
    out = np.clip(rgb, 0, 255).astype(np.uint8).copy()
    if replace_mask.any():
        mask = cv2.dilate(replace_mask.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool)
        cyan = np.array([40, 220, 255], dtype=np.uint8)
        out[mask] = (0.45 * out[mask].astype(np.float32) + 0.55 * cyan.astype(np.float32)).astype(np.uint8)
    return out
