"""
L3 lift-and-project: take Pi3 per-cam 3D point clouds in some world frame,
align them to AV2 ego frame via Sim(3), and forward-splat onto an ERP canvas.

This is the parallax-correct counterpart to L1 sphere projection. L1 ignores
translation (parallax-naive). L3 uses real 3D, so the same physical point seen
by two cameras maps to the SAME ERP location, eliminating ghosting on near
objects (which is L1's main failure mode).

Conventions match L1 (see sphere_projection.py):
  - ERP: u in [0, W) -> azimuth theta in [+pi, -pi)
         v in [0, H) -> elevation phi in [+pi/2, -pi/2]
  - Ego frame: x forward, y left, z up (right-handed)
"""
from __future__ import annotations

import numpy as np

from ..alignment.sim3_align import Sim3


def apply_sim3_to_points(points: np.ndarray, sim3: Sim3) -> np.ndarray:
    """Apply y = s R x + t to points (..., 3)."""
    shape = points.shape
    flat = points.reshape(-1, 3)
    out = sim3.apply(flat)
    return out.reshape(shape)


def ego_points_to_erp_uv(
    points_ego: np.ndarray,
    erp_hw: tuple[int, int] = (1024, 2048),
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Forward-project 3D ego-frame points to ERP pixel coords.

    Args:
        points_ego: (N, 3) points in ego frame (x forward, y left, z up)
        erp_hw: (H_erp, W_erp)

    Returns:
        u_float : (N,) ERP horizontal coord, may be negative or > W (caller mods)
        v_float : (N,) ERP vertical coord, valid in [0, H)
        valid   : (N,) bool, True where point has finite, non-zero distance
    """
    h_erp, w_erp = erp_hw
    x, y, z = points_ego[..., 0], points_ego[..., 1], points_ego[..., 2]

    r = np.linalg.norm(points_ego, axis=-1)
    valid = np.isfinite(r) & (r > 1e-6)

    # Elevation: phi in [-pi/2, pi/2]
    r_safe = np.where(valid, r, 1.0)
    sin_phi = np.where(valid, z / r_safe, 0.0)
    sin_phi = np.clip(sin_phi, -1.0, 1.0)
    phi = np.arcsin(sin_phi)

    # Azimuth: theta in (-pi, pi]
    theta = np.arctan2(y, x)

    # Match L1 ERP convention: theta = pi - (u+0.5)/W * 2pi
    # Invert: u = (pi - theta) * W / (2pi) - 0.5
    u_f = (np.pi - theta) * w_erp / (2.0 * np.pi) - 0.5
    v_f = (np.pi / 2.0 - phi) * h_erp / np.pi - 0.5

    # Wrap u to [0, W) (azimuth is periodic)
    u_f = np.mod(u_f, w_erp)

    return u_f, v_f, valid


def splat_to_erp(
    points_ego: np.ndarray,
    colors: np.ndarray,
    confs: np.ndarray,
    erp_hw: tuple[int, int] = (1024, 2048),
    conf_threshold: float = 0.1,
    min_distance_m: float = 0.5,
    max_distance_m: float = 200.0,
    bilinear: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Forward-splat a single camera's 3D points onto an ERP canvas.

    Args:
        points_ego: (N, 3) points in ego frame, float
        colors:     (N, 3) RGB in [0, 1] or [0, 255], float
        confs:      (N,)    confidence in [0, 1], float
        erp_hw:     (H, W)
        conf_threshold: drop points with conf below this
        min/max_distance_m: drop points too close/far (likely degenerate)
        bilinear:   if True, splat with 2x2 bilinear weights; else nearest

    Returns:
        erp_rgb_weighted : (H, W, 3) sum of (color * conf) accumulator
        erp_weight       : (H, W)    sum of conf
    """
    h_erp, w_erp = erp_hw
    erp_rgb = np.zeros((h_erp, w_erp, 3), dtype=np.float32)
    erp_w = np.zeros((h_erp, w_erp), dtype=np.float32)

    u_f, v_f, valid = ego_points_to_erp_uv(points_ego, erp_hw)

    r = np.linalg.norm(points_ego, axis=-1)
    valid &= (confs > conf_threshold)
    valid &= (r > min_distance_m) & (r < max_distance_m)
    valid &= (v_f >= 0.0) & (v_f < h_erp - 1.0)  # elevation must be in bounds

    if not np.any(valid):
        return erp_rgb, erp_w

    u_v = u_f[valid].astype(np.float64)
    v_v = v_f[valid].astype(np.float64)
    c_v = colors[valid].astype(np.float32)
    w_v = confs[valid].astype(np.float32)

    if not bilinear:
        ui = np.round(u_v).astype(np.int64) % w_erp
        vi = np.clip(np.round(v_v).astype(np.int64), 0, h_erp - 1)
        # Use np.add.at to handle duplicates
        np.add.at(erp_w, (vi, ui), w_v)
        for c_ch in range(3):
            np.add.at(erp_rgb, (vi, ui, c_ch), w_v * c_v[:, c_ch])
        return erp_rgb, erp_w

    # Bilinear splat: distribute each point's weight across 2x2 neighbors
    u0 = np.floor(u_v).astype(np.int64)
    v0 = np.floor(v_v).astype(np.int64)
    du = (u_v - u0).astype(np.float32)
    dv = (v_v - v0).astype(np.float32)

    weights_4 = np.stack([
        (1 - du) * (1 - dv),  # (u0, v0)
        du * (1 - dv),        # (u0+1, v0)
        (1 - du) * dv,        # (u0, v0+1)
        du * dv,              # (u0+1, v0+1)
    ], axis=0)  # (4, N)

    u_offsets = [0, 1, 0, 1]
    v_offsets = [0, 0, 1, 1]
    for k in range(4):
        ui = (u0 + u_offsets[k]) % w_erp
        vi = v0 + v_offsets[k]
        in_v = (vi >= 0) & (vi < h_erp)
        if not np.any(in_v):
            continue
        wk = (weights_4[k] * w_v)[in_v]
        ui_in = ui[in_v]
        vi_in = vi[in_v]
        cv_in = c_v[in_v]
        np.add.at(erp_w, (vi_in, ui_in), wk)
        for c_ch in range(3):
            np.add.at(erp_rgb, (vi_in, ui_in, c_ch), wk * cv_in[:, c_ch])

    return erp_rgb, erp_w


def lift_and_project_multiview(
    cam_points_ego: dict[str, np.ndarray],
    cam_colors: dict[str, np.ndarray],
    cam_confs: dict[str, np.ndarray],
    erp_hw: tuple[int, int] = (1024, 2048),
    conf_threshold: float = 0.1,
    min_distance_m: float = 0.5,
    max_distance_m: float = 200.0,
    bilinear: bool = True,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Accumulate L3 splats from multiple cameras into one ERP image.

    Args:
        cam_points_ego: {cam: (H, W, 3) points in ego frame (after Sim3 alignment)}
        cam_colors:     {cam: (H, W, 3) RGB in [0, 1]}
        cam_confs:      {cam: (H, W) confidence in [0, 1]}
        erp_hw:         (H, W) output ERP size
        conf_threshold, min/max_distance_m: see splat_to_erp
        bilinear:       bilinear (smoother) vs nearest splat

    Returns:
        erp_rgb     : (H_erp, W_erp, 3) float32 in [0, 1] — blended ERP
        erp_weight  : (H_erp, W_erp)    float32 total accumulated confidence
        diagnostics : dict with per-cam contribution stats
    """
    h_erp, w_erp = erp_hw
    erp_rgb_sum = np.zeros((h_erp, w_erp, 3), dtype=np.float32)
    erp_w_sum = np.zeros((h_erp, w_erp), dtype=np.float32)

    per_cam_stats: dict[str, dict] = {}
    for cam, pts_hw3 in cam_points_ego.items():
        col_hw3 = cam_colors[cam]
        conf_hw = cam_confs[cam]
        N = pts_hw3.shape[0] * pts_hw3.shape[1]
        pts_flat = pts_hw3.reshape(-1, 3)
        col_flat = col_hw3.reshape(-1, 3)
        conf_flat = conf_hw.reshape(-1)

        erp_rgb_i, erp_w_i = splat_to_erp(
            pts_flat, col_flat, conf_flat,
            erp_hw=erp_hw, conf_threshold=conf_threshold,
            min_distance_m=min_distance_m, max_distance_m=max_distance_m,
            bilinear=bilinear,
        )
        erp_rgb_sum += erp_rgb_i
        erp_w_sum += erp_w_i

        per_cam_stats[cam] = {
            "n_total": int(N),
            "n_after_conf_threshold": int((conf_flat > conf_threshold).sum()),
            "coverage_ratio": float((erp_w_i > 0).sum() / (h_erp * w_erp)),
            "weight_sum": float(erp_w_i.sum()),
        }

    # Normalize
    w_safe = np.where(erp_w_sum > 1e-6, erp_w_sum, 1.0)
    erp_rgb = erp_rgb_sum / w_safe[..., None]
    erp_rgb = np.where(erp_w_sum[..., None] > 1e-6, erp_rgb, 0.0)

    diagnostics = {
        "per_cam": per_cam_stats,
        "erp_coverage_ratio": float((erp_w_sum > 0).sum() / (h_erp * w_erp)),
        "erp_total_weight": float(erp_w_sum.sum()),
    }
    return erp_rgb, erp_w_sum, diagnostics
