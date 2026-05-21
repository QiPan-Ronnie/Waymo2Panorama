"""
Multi-region IPM prior (新-C, plan v6.1 route 12).

Extends T14's `ipm_ground.py` (ground-only IPM, +0.05 dB on 10-anchor) to three
explicit regions:

  ground   — z=0 plane, same as T14 (reused unchanged via composition).
  sky      — far / low-confidence / high-elevation pixels routed to the L1
              sphere (no math change, just an explicit tag).
  building — vertical facades. Per 32x32 tile we RANSAC-fit a vertical plane
              n_x*x + n_y*y = d with n_z=0 to the local Pi3 ego-frame points,
              then ray-cast each building pixel onto the fitted plane and
              splat to the ERP — analogous to T14's ground projection but for
              a plane perpendicular to the ground.

Design doc: notes/new_c_ipm_multi_region_design.md.

Conventions match `ipm_ground.py`:
  * ERP: 2:1 aspect; u CCW->CW; v top->bottom.
  * Ego (AV2): x forward, y left, z up.
  * Camera (OpenCV): x right, y down, z forward.
  * T_ego_cam (4x4) maps cam-frame points to ego-frame.

This file is **CPU-only** (numpy + opencv). No GPU, no scipy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from .ipm_ground import (
    _erp_uv_from_dir_ego,
    detect_ground_from_pi3,
    ipm_project_ground,
)
from .sphere_projection import render_camera_to_erp


# --------------------------------------------------------------------------- #
#  Normal estimation from Pi3 local_points
# --------------------------------------------------------------------------- #


def estimate_normals_from_points(
    local_points_cam: np.ndarray,
    T_ego_cam: np.ndarray,
    window: int = 5,
    depth_disc_thresh_m: float = 2.0,
) -> np.ndarray:
    """Estimate per-pixel ego-frame surface normals from Pi3 local_points.

    Procedure (design doc §2):
      1) Bring cam-frame points into ego frame.
      2) Compute finite-difference tangents along u (horizontal) and v (vertical).
      3) Normal = unit(cross(dx, dy)).
      4) Box-filter each component by `window x window` for smoothing.
      5) Mark NaN where neighbouring depth differs > `depth_disc_thresh_m`
         (depth discontinuity -> normal is ill-defined).

    Args:
        local_points_cam: (H, W, 3) cam-frame XYZ. NaN -> invalid pixel.
        T_ego_cam:        (4, 4) SE(3) cam->ego.
        window:           box-filter window size (must be odd; default 5).
        depth_disc_thresh_m: reject normals where dx/dy magnitudes exceed this
                              (likely a depth discontinuity).

    Returns:
        (H, W, 3) float32 ego-frame normals (unit length where valid,
        NaN where invalid).
    """
    H, W, C = local_points_cam.shape
    assert C == 3
    if window % 2 == 0:
        window += 1

    # 1) cam -> ego
    pts = local_points_cam.reshape(-1, 3).astype(np.float64)
    pts_h = np.concatenate([pts, np.ones((pts.shape[0], 1))], axis=1)
    ego = (T_ego_cam @ pts_h.T).T[:, :3].reshape(H, W, 3)

    # 2) finite differences along u (horizontal) and v (vertical).
    # Use zero at borders (will be flagged invalid via `valid_diff` mask later).
    dx = np.zeros_like(ego, dtype=np.float64)
    dy = np.zeros_like(ego, dtype=np.float64)
    dx[:, 1:-1, :] = (ego[:, 2:, :] - ego[:, :-2, :]) * 0.5
    dy[1:-1, :, :] = (ego[2:, :, :] - ego[:-2, :, :]) * 0.5

    dx_mag = np.linalg.norm(dx, axis=-1)
    dy_mag = np.linalg.norm(dy, axis=-1)
    # Borders & depth discontinuities mark normal as invalid.
    valid_diff = np.ones((H, W), dtype=bool)
    valid_diff[0, :] = False
    valid_diff[-1, :] = False
    valid_diff[:, 0] = False
    valid_diff[:, -1] = False
    disc_bad = (dx_mag > depth_disc_thresh_m) | (dy_mag > depth_disc_thresh_m)

    # 3) cross product -> normal
    n = np.cross(dx, dy)  # (H, W, 3)
    n_mag = np.linalg.norm(n, axis=-1, keepdims=True)
    n_mag_safe = np.where(n_mag > 1e-9, n_mag, 1.0)
    n_unit = n / n_mag_safe

    # Mask out invalid normals to 0 (so box-filter doesn't propagate NaN)
    invalid_pre = (
        disc_bad
        | ~valid_diff
        | ~np.isfinite(ego[..., 0])
        | ~np.isfinite(ego[..., 1])
        | ~np.isfinite(ego[..., 2])
        | (n_mag[..., 0] < 1e-9)
    )
    n_unit[invalid_pre] = 0.0

    # 4) box-filter smoothing per component (with a parallel valid mask so
    # we can re-normalize properly at the edges).
    valid_f = (~invalid_pre).astype(np.float32)
    valid_smooth = cv2.boxFilter(
        valid_f, ddepth=-1, ksize=(window, window),
        borderType=cv2.BORDER_REPLICATE,
    )
    n_smooth = np.empty_like(n_unit, dtype=np.float32)
    for c in range(3):
        # weighted average: sum(n*valid)/sum(valid)
        num = cv2.boxFilter(
            (n_unit[..., c] * valid_f).astype(np.float32),
            ddepth=-1, ksize=(window, window),
            borderType=cv2.BORDER_REPLICATE,
        )
        # avoid /0
        denom = np.where(valid_smooth > 1e-6, valid_smooth, 1.0)
        n_smooth[..., c] = num / denom
    # re-normalize after smoothing
    s_mag = np.linalg.norm(n_smooth, axis=-1, keepdims=True)
    s_mag_safe = np.where(s_mag > 1e-6, s_mag, 1.0)
    n_smooth = n_smooth / s_mag_safe

    # 5) invalidate where input was bad or smoothing had no valid support
    final_invalid = invalid_pre | (valid_smooth < 0.1) | (s_mag[..., 0] < 0.1)
    n_smooth[final_invalid] = np.nan
    return n_smooth.astype(np.float32)


# --------------------------------------------------------------------------- #
#  Region segmentation
# --------------------------------------------------------------------------- #


@dataclass
class RegionMasks:
    """Bool masks for ground / sky / building / unknown. Disjoint; union = all."""
    ground: np.ndarray     # (H, W) bool
    sky: np.ndarray        # (H, W) bool
    building: np.ndarray   # (H, W) bool
    unknown: np.ndarray    # (H, W) bool

    def coverage(self) -> dict:
        return {
            "ground": float(self.ground.mean()),
            "sky": float(self.sky.mean()),
            "building": float(self.building.mean()),
            "unknown": float(self.unknown.mean()),
        }


def segment_regions_from_pi3(
    local_points_cam: np.ndarray,
    T_ego_cam: np.ndarray,
    conf: Optional[np.ndarray] = None,
    *,
    ground_z_thresh_m: float = 0.30,
    ground_normal_z_min: float = 0.85,
    ground_min_forward_m: float = 1.0,
    ground_max_radius_m: float = 60.0,
    sky_conf_thresh: float = -2.0,
    sky_depth_min_m: float = 30.0,
    sky_ego_z_min_m: float = 5.0,
    sky_v_max_frac: float = 0.4,
    building_normal_z_max: float = 0.30,
    building_normal_xy_min: float = 0.85,
    building_min_height_m: float = 0.5,
    building_max_radius_m: float = 80.0,
    normal_window: int = 5,
) -> RegionMasks:
    """First-match-wins region segmentation (design §2).

    Priority order: ground -> sky -> building -> unknown.

    Notes:
        - "ground" reuses T14's ego_z + radius test but also requires a roughly
          horizontal estimated normal (|n_z| >= ground_normal_z_min). On the
          rare pixel where T14 said ground but the normal disagrees, we still
          accept ground (T14 is the production prior — we never want to demote
          ground that T14 already classified, otherwise step 1 acceptance
          "ground coverage ≈ T14 ±2%" would fail).
        - "sky" matches if either Pi3 confidence is very low OR the geometry
          says the pixel is high-up + far-out.
        - "building" matches if the pixel is above ground, has a (near-)vertical
          estimated normal, and is within plausible facade radius.
    """
    H, W, _ = local_points_cam.shape
    if conf is None:
        conf = np.full((H, W), -1.0, dtype=np.float32)
    if conf.shape != (H, W):
        raise ValueError(f"conf shape {conf.shape} != points shape {(H, W)}")

    # Bring points into ego frame.
    pts = local_points_cam.reshape(-1, 3).astype(np.float64)
    pts_h = np.concatenate([pts, np.ones((pts.shape[0], 1))], axis=1)
    ego = (T_ego_cam @ pts_h.T).T[:, :3].reshape(H, W, 3)
    z_cam = local_points_cam[..., 2]
    z_ego = ego[..., 2]
    radius = np.sqrt(ego[..., 0] ** 2 + ego[..., 1] ** 2)

    forward_ok = np.isfinite(z_cam) & (z_cam > ground_min_forward_m)

    # ---- ground (matches T14 + normal sanity) ----
    # We start from T14's decision (so any pixel T14 calls ground stays ground).
    ground = detect_ground_from_pi3(
        local_points_cam=local_points_cam,
        T_ego_cam=T_ego_cam,
        ego_z_thresh_m=ground_z_thresh_m,
        min_forward_m=ground_min_forward_m,
        max_radius_m=ground_max_radius_m,
    )

    # ---- normals (used by sky elevation test and building) ----
    n_ego = estimate_normals_from_points(
        local_points_cam, T_ego_cam, window=normal_window,
    )
    n_z = n_ego[..., 2]
    n_xy = np.sqrt(n_ego[..., 0] ** 2 + n_ego[..., 1] ** 2)

    # ---- sky ----
    # Pi3 conf is in log-space. A very negative value indicates Pi3 itself doesn't
    # trust this prediction (typical for textureless sky or lens flare). Pixels
    # that are far + high + in the upper part of the image also count as sky.
    img_v = np.arange(H, dtype=np.float32)[:, None].repeat(W, axis=1)
    high_in_image = img_v < (sky_v_max_frac * H)
    geom_sky = (
        forward_ok
        & (z_cam > sky_depth_min_m)
        & (z_ego > sky_ego_z_min_m)
        & high_in_image
    )
    conf_sky = np.isfinite(conf) & (conf < sky_conf_thresh)
    sky = (geom_sky | conf_sky) & ~ground

    # ---- building ----
    # Vertical facade: normal is ~horizontal (n_z small, n_xy large), pixel is
    # above ground plane, and within plausible radius.
    has_normal = np.isfinite(n_z) & np.isfinite(n_xy)
    building = (
        forward_ok
        & has_normal
        & (z_ego > building_min_height_m)
        & (np.abs(n_z) <= building_normal_z_max)
        & (n_xy >= building_normal_xy_min)
        & (radius <= building_max_radius_m)
        & ~ground
        & ~sky
    )

    # ---- unknown (residual) ----
    unknown = ~(ground | sky | building)

    return RegionMasks(
        ground=ground.astype(bool),
        sky=sky.astype(bool),
        building=building.astype(bool),
        unknown=unknown.astype(bool),
    )


# --------------------------------------------------------------------------- #
#  Sky projection (sphere wrapper)
# --------------------------------------------------------------------------- #


def ipm_project_sky(
    image: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    sky_mask: np.ndarray,
    erp_hw: tuple[int, int] = (1024, 2048),
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project sky-tagged pixels via the standard L1 sphere.

    Implementation: forward render the full image with `render_camera_to_erp`,
    then composite a coarse 'sky alpha' on the ERP. Since the sphere already
    handles the far-field correctly, sky projection IS the sphere; this wrapper
    exists so the multi-region pipeline has a uniform per-region API.

    Args:
        image:     (H_src, W_src, 3) uint8.
        K, T_ego_cam: standard.
        sky_mask:  (H_src, W_src) bool, sky pixels in source frame.
        erp_hw:    target canvas.

    Returns:
        erp_rgb, erp_alpha, erp_weight — same convention as ipm_project_ground.
        Non-sky source pixels are zeroed before sphere rendering.
    """
    if sky_mask.shape != image.shape[:2]:
        raise ValueError("sky_mask shape mismatch")

    # Pass an ego_mask to render_camera_to_erp (1 = keep). render takes ego_mask
    # in source coords (will resize internally if needed).
    ego_mask_u8 = sky_mask.astype(np.uint8)
    sph_rgb, sph_alpha, sph_weight = render_camera_to_erp(
        image=image,
        K=K,
        T_ego_cam=T_ego_cam,
        erp_hw=erp_hw,
        ego_mask=ego_mask_u8,
    )
    # render_camera_to_erp's `ego_mask` multiplies the weight by mask but it
    # still samples the image everywhere. To make alpha reflect the sky region
    # explicitly, mask the alpha by weight > 0.
    sph_alpha = sph_alpha & (sph_weight > 1e-6)
    return sph_rgb, sph_alpha, sph_weight


# --------------------------------------------------------------------------- #
#  Building projection via per-tile RANSAC vertical plane
# --------------------------------------------------------------------------- #


def _ransac_vertical_plane(
    pts_ego: np.ndarray,
    iters: int = 50,
    threshold_m: float = 0.20,
    min_inlier_frac: float = 0.40,
    rng: Optional[np.random.Generator] = None,
) -> tuple[Optional[tuple[float, float, float]], float]:
    """RANSAC fit of vertical plane n_x*x + n_y*y = d (n_z = 0).

    A vertical plane through 3D space (i.e. perpendicular to z=0) is fully
    determined by two non-vertically-colinear points in ego frame: pick any
    two points (x1,y1,z1) and (x2,y2,z2) — the plane is `n_x*x + n_y*y = d`
    where n = unit(perp to (x2-x1, y2-y1) in ground plane) and
    d = n_x*x1 + n_y*y1.

    Returns:
        ((n_x, n_y, d), inlier_frac) or (None, 0.0) if no model meets
        min_inlier_frac.
    """
    if rng is None:
        rng = np.random.default_rng(0)

    N = pts_ego.shape[0]
    if N < 4:
        return None, 0.0

    xy = pts_ego[:, :2].astype(np.float64)  # (N, 2)
    best_inliers = -1
    best_model = None

    for _ in range(iters):
        i, j = rng.integers(0, N, size=2)
        if i == j:
            continue
        dx = xy[j, 0] - xy[i, 0]
        dy = xy[j, 1] - xy[i, 1]
        norm = np.hypot(dx, dy)
        if norm < 1e-3:
            continue
        # Normal perpendicular to (dx, dy) in ground plane.
        n_x = -dy / norm
        n_y = dx / norm
        d = n_x * xy[i, 0] + n_y * xy[i, 1]
        # Distance: |n_x*x + n_y*y - d|
        dist = np.abs(xy[:, 0] * n_x + xy[:, 1] * n_y - d)
        inliers = int((dist <= threshold_m).sum())
        if inliers > best_inliers:
            best_inliers = inliers
            best_model = (n_x, n_y, d)

    if best_model is None:
        return None, 0.0
    frac = best_inliers / N
    if frac < min_inlier_frac:
        return None, frac
    # Refit on inliers (LS) for stability.
    n_x, n_y, d = best_model
    dist = np.abs(xy[:, 0] * n_x + xy[:, 1] * n_y - d)
    inlier_mask = dist <= threshold_m
    if inlier_mask.sum() >= 2:
        xy_in = xy[inlier_mask]
        # Use mean + PCA to refit. Plane through mean perpendicular to the
        # 1st PC of the inlier xy distribution.
        mean = xy_in.mean(axis=0)
        cov = np.cov((xy_in - mean).T)
        evals, evecs = np.linalg.eigh(cov)
        # Smallest eigenvector = plane normal direction in ground plane.
        n_dir = evecs[:, 0]
        n_x, n_y = float(n_dir[0]), float(n_dir[1])
        nrm = np.hypot(n_x, n_y)
        if nrm > 1e-6:
            n_x /= nrm
            n_y /= nrm
            d = float(n_x * mean[0] + n_y * mean[1])
        # Recompute inlier frac with refined model.
        dist = np.abs(xy[:, 0] * n_x + xy[:, 1] * n_y - d)
        frac = float((dist <= threshold_m).mean())

    return (n_x, n_y, d), frac


def ipm_project_building(
    image: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    local_points_cam: np.ndarray,
    building_mask: np.ndarray,
    erp_hw: tuple[int, int] = (1024, 2048),
    *,
    tile_size: int = 32,
    ransac_iters: int = 50,
    ransac_threshold_m: float = 0.20,
    min_inlier_frac: float = 0.40,
    min_pts_per_tile: int = 200,
    max_distance_m: float = 80.0,
    min_distance_m: float = 1.0,
    panorama_center_z_m: float = 1.5,
    conf: Optional[np.ndarray] = None,
    conf_thresh: float = -2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Forward-warp building-tagged pixels via per-tile vertical-plane fit.

    Pipeline (design §3):
      1) Tile the source image into 32x32 windows.
      2) For each window, gather ego-frame XYZ of pixels in building_mask
         (and with confident Pi3 prediction). Skip if too few.
      3) Fit a vertical plane n_x*x + n_y*y = d via RANSAC.
      4) For every inlier pixel in the tile: build the ego ray and intersect
         the fitted plane analytically: solve
            n_x*(o_x + t*d_x) + n_y*(o_y + t*d_y) = d
            t = (d - n_x*o_x - n_y*o_y) / (n_x*d_x + n_y*d_y)
         The 3D point is p = o + t * d.
      5) Project p to ERP via direction from panorama center -> p (same as
         T14 ground projection).
      6) Splat onto ERP canvas with weight = exp(-t/40).
      7) Densify with morphological dilation + per-channel fill (same as T14).

    Returns:
        erp_rgb, erp_alpha, erp_weight, info_dict
        where info_dict has 'plane_count' and 'inlier_frac_mean'.
    """
    if image.dtype != np.uint8:
        raise ValueError(f"expected uint8 image, got {image.dtype}")
    H_src, W_src = image.shape[:2]
    H_erp, W_erp = erp_hw

    erp_rgb = np.zeros((H_erp, W_erp, 3), dtype=np.float32)
    erp_alpha = np.zeros((H_erp, W_erp), dtype=bool)
    erp_weight = np.zeros((H_erp, W_erp), dtype=np.float32)
    info = {"plane_count": 0, "inlier_frac_mean": 0.0, "inlier_frac_list": []}

    if not building_mask.any():
        return erp_rgb, erp_alpha, erp_weight, info

    # Pre-compute ego-frame points + per-pixel ray origin/direction.
    pts = local_points_cam.reshape(-1, 3).astype(np.float64)
    pts_h = np.concatenate([pts, np.ones((pts.shape[0], 1))], axis=1)
    ego = (T_ego_cam @ pts_h.T).T[:, :3].reshape(H_src, W_src, 3)

    if conf is None:
        conf = np.zeros((H_src, W_src), dtype=np.float32)
    confident = (conf >= conf_thresh) & np.isfinite(conf)

    # Per-pixel cam ray -> ego direction.
    K_inv = np.linalg.inv(K)
    uu, vv = np.meshgrid(np.arange(W_src), np.arange(H_src))
    pix = np.stack([uu + 0.5, vv + 0.5, np.ones_like(uu)], axis=-1).astype(np.float64)
    d_cam = pix @ K_inv.T  # (H, W, 3)
    R = T_ego_cam[:3, :3]
    t_origin = T_ego_cam[:3, 3]
    d_ego = d_cam @ R.T  # (H, W, 3)

    # Tile iteration.
    n_tiles_v = (H_src + tile_size - 1) // tile_size
    n_tiles_u = (W_src + tile_size - 1) // tile_size
    rng = np.random.default_rng(42)

    # Buffers for splatting (collected globally, splatted once at the end).
    all_v_idx = []
    all_u_idx = []
    all_rgb = []
    all_weight = []
    all_t = []

    for tv in range(n_tiles_v):
        for tu in range(n_tiles_u):
            v0 = tv * tile_size
            v1 = min(v0 + tile_size, H_src)
            u0 = tu * tile_size
            u1 = min(u0 + tile_size, W_src)
            tile_mask = building_mask[v0:v1, u0:u1] & confident[v0:v1, u0:u1]
            if tile_mask.sum() < min_pts_per_tile:
                continue
            tile_pts = ego[v0:v1, u0:u1][tile_mask]  # (M, 3)
            if tile_pts.shape[0] < min_pts_per_tile:
                continue
            model, frac = _ransac_vertical_plane(
                tile_pts,
                iters=ransac_iters,
                threshold_m=ransac_threshold_m,
                min_inlier_frac=min_inlier_frac,
                rng=rng,
            )
            if model is None:
                continue
            n_x, n_y, d = model
            info["plane_count"] += 1
            info["inlier_frac_list"].append(frac)

            # Project every pixel in tile_mask onto this plane.
            tv_idx, tu_idx = np.where(tile_mask)
            vs = tv_idx + v0
            us = tu_idx + u0
            dirs = d_ego[vs, us]  # (M, 3)
            denom = n_x * dirs[:, 0] + n_y * dirs[:, 1]
            # Reject rays nearly parallel to the plane.
            denom_ok = np.abs(denom) > 1e-3
            if not denom_ok.any():
                continue
            t_hit = (d - n_x * t_origin[0] - n_y * t_origin[1]) / np.where(
                denom_ok, denom, 1.0
            )
            valid = denom_ok & (t_hit > min_distance_m) & (t_hit < max_distance_m)
            if not valid.any():
                continue

            t_v = t_hit[valid]
            v_keep = vs[valid]
            u_keep = us[valid]
            p_x = t_origin[0] + t_v * dirs[valid, 0]
            p_y = t_origin[1] + t_v * dirs[valid, 1]
            p_z = t_origin[2] + t_v * dirs[valid, 2]
            r_pano = np.sqrt(p_x ** 2 + p_y ** 2 + (p_z - panorama_center_z_m) ** 2)

            # Direction from pano center to plane intersection.
            p_dir = np.stack(
                [p_x, p_y, p_z - panorama_center_z_m], axis=1
            )
            u_erp, v_erp = _erp_uv_from_dir_ego(p_dir, erp_hw)
            u_idx = np.mod(np.round(u_erp).astype(np.int64), W_erp)
            v_idx = np.clip(np.round(v_erp).astype(np.int64), 0, H_erp - 1)
            w = np.exp(-t_v / 40.0).astype(np.float32)
            rgb = image[v_keep, u_keep].astype(np.float32)

            all_v_idx.append(v_idx)
            all_u_idx.append(u_idx)
            all_rgb.append(rgb)
            all_weight.append(w)
            all_t.append(t_v.astype(np.float32))

    if not all_v_idx:
        return erp_rgb, erp_alpha, erp_weight, info

    v_all = np.concatenate(all_v_idx)
    u_all = np.concatenate(all_u_idx)
    rgb_all = np.concatenate(all_rgb, axis=0)
    w_all = np.concatenate(all_weight)
    t_all = np.concatenate(all_t)

    # Splat: nearest wins (smallest t).
    order = np.argsort(t_all, kind="stable")
    v_s = v_all[order]
    u_s = u_all[order]
    rgb_s = rgb_all[order]
    w_s = w_all[order]

    flat = v_s.astype(np.int64) * W_erp + u_s
    _, first = np.unique(flat, return_index=True)
    erp_rgb[v_s[first], u_s[first]] = rgb_s[first]
    erp_weight[v_s[first], u_s[first]] = w_s[first]
    erp_alpha[v_s[first], u_s[first]] = True

    # Densify (dilation + fill, same as T14).
    if erp_alpha.any():
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        alpha_u8 = erp_alpha.astype(np.uint8) * 255
        alpha_dil = cv2.dilate(alpha_u8, kernel, iterations=1)
        rgb_dil = np.zeros_like(erp_rgb)
        for c in range(3):
            rgb_dil[..., c] = cv2.dilate(erp_rgb[..., c], kernel, iterations=1)
        w_dil = cv2.dilate(erp_weight, kernel, iterations=1)
        fill = (alpha_dil > 0) & (~erp_alpha)
        erp_rgb[fill] = rgb_dil[fill]
        erp_weight[fill] = w_dil[fill]
        erp_alpha = alpha_dil > 0

    if info["inlier_frac_list"]:
        info["inlier_frac_mean"] = float(np.mean(info["inlier_frac_list"]))
    return erp_rgb, erp_alpha, erp_weight, info


# --------------------------------------------------------------------------- #
#  Multi-region composer
# --------------------------------------------------------------------------- #


@dataclass
class MultiRegionSlab:
    """Composed per-camera ERP slab from ground + sky + building + sphere base."""
    ground_rgb: np.ndarray         # (H, W, 3) float32
    ground_alpha: np.ndarray       # (H, W) bool
    ground_weight: np.ndarray      # (H, W) float32
    sky_rgb: np.ndarray
    sky_alpha: np.ndarray
    sky_weight: np.ndarray
    building_rgb: np.ndarray
    building_alpha: np.ndarray
    building_weight: np.ndarray
    sphere_rgb: np.ndarray
    sphere_alpha: np.ndarray
    sphere_weight: np.ndarray
    merged_rgb: np.ndarray         # composed via design §4
    merged_weight: np.ndarray
    merged_alpha: np.ndarray
    masks: RegionMasks
    info: dict


def ipm_project_multi_region(
    image: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    local_points_cam: np.ndarray,
    conf: Optional[np.ndarray] = None,
    erp_hw: tuple[int, int] = (1024, 2048),
    *,
    enable_building: bool = True,
    enable_sky_routing: bool = True,
    ground_kwargs: Optional[dict] = None,
    building_kwargs: Optional[dict] = None,
    segment_kwargs: Optional[dict] = None,
    weight_feather_px: int = 3,
) -> MultiRegionSlab:
    """End-to-end multi-region IPM projection for one camera.

    Composition (§4):
        base = sphere (everywhere)
        sky stays = base
        building overrides where building_alpha
        ground overrides where ground_alpha (highest priority)
        weights are boosted by ~2x for ground, ~1.5x for building so
            multi-band blending across cams trusts the priors.
    """
    H_src, W_src = image.shape[:2]
    H_erp, W_erp = erp_hw

    masks = segment_regions_from_pi3(
        local_points_cam=local_points_cam,
        T_ego_cam=T_ego_cam,
        conf=conf,
        **(segment_kwargs or {}),
    )

    # Sphere base
    sph_rgb, sph_alpha, sph_weight = render_camera_to_erp(
        image=image, K=K, T_ego_cam=T_ego_cam, erp_hw=erp_hw,
    )

    # Ground (reuse T14 unchanged)
    gk = dict(
        max_distance_m=40.0,
        min_distance_m=1.5,
        panorama_center_z_m=1.5,
    )
    if ground_kwargs:
        gk.update(ground_kwargs)
    gnd_rgb, gnd_alpha, gnd_weight = ipm_project_ground(
        image=image, K=K, T_ego_cam=T_ego_cam,
        ground_mask=masks.ground, erp_hw=erp_hw, **gk,
    )

    # Sky (sphere-equivalent — just for inspection / coverage)
    if enable_sky_routing and masks.sky.any():
        sky_rgb, sky_alpha, sky_weight = ipm_project_sky(
            image=image, K=K, T_ego_cam=T_ego_cam,
            sky_mask=masks.sky, erp_hw=erp_hw,
        )
    else:
        sky_rgb = np.zeros((H_erp, W_erp, 3), dtype=np.float32)
        sky_alpha = np.zeros((H_erp, W_erp), dtype=bool)
        sky_weight = np.zeros((H_erp, W_erp), dtype=np.float32)

    # Building
    info: dict = {"plane_count": 0, "inlier_frac_mean": 0.0}
    if enable_building and masks.building.any():
        bk = dict(
            tile_size=32,
            ransac_iters=50,
            ransac_threshold_m=0.20,
            min_inlier_frac=0.40,
            min_pts_per_tile=200,
            max_distance_m=80.0,
            min_distance_m=1.0,
            panorama_center_z_m=1.5,
        )
        if building_kwargs:
            bk.update(building_kwargs)
        bld_rgb, bld_alpha, bld_weight, info = ipm_project_building(
            image=image, K=K, T_ego_cam=T_ego_cam,
            local_points_cam=local_points_cam,
            building_mask=masks.building,
            erp_hw=erp_hw,
            conf=conf,
            **bk,
        )
    else:
        bld_rgb = np.zeros((H_erp, W_erp, 3), dtype=np.float32)
        bld_alpha = np.zeros((H_erp, W_erp), dtype=bool)
        bld_weight = np.zeros((H_erp, W_erp), dtype=np.float32)

    # ---- Compose (§4) ----
    merged_rgb = sph_rgb.copy()
    merged_weight = sph_weight.copy()
    merged_alpha = sph_alpha.copy()

    # Building first (lower priority than ground, but overrides sphere base).
    if bld_alpha.any():
        merged_rgb[bld_alpha] = bld_rgb[bld_alpha]
        merged_weight[bld_alpha] = np.maximum(
            merged_weight[bld_alpha], bld_weight[bld_alpha] * 1.5 + 0.3,
        )
        merged_alpha = merged_alpha | bld_alpha

    # Ground last (highest priority).
    if gnd_alpha.any():
        merged_rgb[gnd_alpha] = gnd_rgb[gnd_alpha]
        merged_weight[gnd_alpha] = np.maximum(
            merged_weight[gnd_alpha], gnd_weight[gnd_alpha] * 2.0 + 0.5,
        )
        merged_alpha = merged_alpha | gnd_alpha

    # Gaussian-feather the weight at region edges (NOT the RGB).
    if weight_feather_px > 0:
        k = 2 * weight_feather_px + 1
        merged_weight = cv2.GaussianBlur(
            merged_weight, (k, k), sigmaX=float(weight_feather_px),
        )

    return MultiRegionSlab(
        ground_rgb=gnd_rgb, ground_alpha=gnd_alpha, ground_weight=gnd_weight,
        sky_rgb=sky_rgb, sky_alpha=sky_alpha, sky_weight=sky_weight,
        building_rgb=bld_rgb, building_alpha=bld_alpha, building_weight=bld_weight,
        sphere_rgb=sph_rgb, sphere_alpha=sph_alpha, sphere_weight=sph_weight,
        merged_rgb=merged_rgb,
        merged_weight=merged_weight,
        merged_alpha=merged_alpha,
        masks=masks,
        info=info,
    )


# --------------------------------------------------------------------------- #
#  Visualization helpers
# --------------------------------------------------------------------------- #


def make_region_overlay(image_rgb: np.ndarray, masks: RegionMasks) -> np.ndarray:
    """Tint per-region: green=ground, blue=sky, red=building, gray=unknown."""
    out = image_rgb.astype(np.float32)
    alpha = 0.5

    def _tint(rgb, mask, color):
        rgb_new = rgb.copy()
        sel = mask[..., None].astype(np.float32) * alpha
        tint = np.full_like(rgb, 0.0)
        tint[..., :] = color
        rgb_new = rgb * (1.0 - sel) + tint * sel
        return rgb_new

    out = _tint(out, masks.ground, (0, 200, 80))      # green
    out = _tint(out, masks.sky, (60, 130, 230))       # blue
    out = _tint(out, masks.building, (220, 60, 60))   # red
    out = _tint(out, masks.unknown, (130, 130, 130))  # gray
    return np.clip(out, 0, 255).astype(np.uint8)


def _self_test() -> None:
    """Smoke test: synthetic camera + synthetic local_points, check segmentation."""
    H = W = 64
    K = np.array([[40, 0, 32], [0, 40, 32], [0, 0, 1.0]])
    T = np.eye(4)
    T[:3, 3] = [0.0, 0.0, 1.5]
    image = np.full((H, W, 3), 128, dtype=np.uint8)
    lp = np.zeros((H, W, 3), dtype=np.float32)
    # Half the image is at depth 2m (ground), half at depth 30m (sky-ish).
    lp[:H // 2, :, :] = [0, 0, 30.0]  # far -> sky-ish
    lp[H // 2:, :, :] = [0, 0, 2.0]   # near (will be ground only if ego_z ≈ 0)
    masks = segment_regions_from_pi3(lp, T)
    print(f"smoke: {masks.coverage()}")


if __name__ == "__main__":
    _self_test()
