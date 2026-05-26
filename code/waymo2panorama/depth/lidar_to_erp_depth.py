"""
N1 Phase C — LiDAR → ERP dense depth map.

Projects AV2 64-beam LiDAR sweeps (in egovehicle frame) onto the ERP canvas
to produce a per-pixel depth map suitable for `render_camera_to_erp`'s
`convergence_distance_m` parameter. Sparse LiDAR points are densified via
kNN search with a fill-radius cap so far-from-LiDAR pixels gracefully
degrade to a configurable "far" distance (approximating legacy L1 behavior).

Pipeline:
    1. Load LiDAR feather for the sweep nearest to the anchor camera timestamp.
       Points are in egovehicle frame (x_forward, y_left, z_up).
    2. Filter by range [min_range_m, max_range_m] to drop noisy near/far returns.
    3. Convert XYZ → (theta, phi, r) spherical coordinates (ego origin).
    4. Map (theta, phi) → ERP (u, v) using the project's convention.
    5. Splat to ERP grid, keeping the MINIMUM range per pixel (closer surface wins
       — this is the visible surface from ego's perspective).
    6. Densify via kNN-fill (scipy cKDTree): for each NaN pixel, copy the depth
       of its nearest LiDAR-hit pixel within `densify_radius_px`. Pixels beyond
       the radius keep `fill_far_m` (defaults to 1000m, ~= legacy infinity).

ERP convention (matches sphere_projection.py):
    theta = pi - (u + 0.5) / W * 2pi   in [+pi, -pi)
    phi = pi/2 - (v + 0.5) / H * pi   in [+pi/2, -pi/2]

Usage (typical):
    import numpy as np
    from av2.utils.io import read_lidar_sweep
    from waymo2panorama.depth.lidar_to_erp_depth import (
        load_lidar_sweep_nearest_to_ts, project_lidar_to_erp_depth)

    pts = load_lidar_sweep_nearest_to_ts(log_dir, anchor_ts_ns)
    depth_map = project_lidar_to_erp_depth(pts, erp_hw=(1024, 2048))
    # depth_map: (H, W) float32 in meters, filled for use with N1 mode.
    erp = stitch_one_frame(frame, convergence_distance_m=depth_map)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np


def load_lidar_sweep_nearest_to_ts(
    log_dir: Path,
    anchor_ts_ns: int,
    max_delta_ms: float = 75.0,
) -> tuple[np.ndarray, int, float]:
    """Find and load the LiDAR sweep whose timestamp is closest to `anchor_ts_ns`.

    Returns:
        points_ego: (N, 3) float64 LiDAR points in egovehicle frame.
        sweep_ts_ns: timestamp of the loaded sweep.
        delta_ms: time delta to the anchor (in ms).
    """
    from av2.utils.io import read_lidar_sweep  # local import; av2 only needed here

    sweep_dir = Path(log_dir) / "sensors" / "lidar"
    if not sweep_dir.exists():
        raise FileNotFoundError(f"LiDAR sweep dir missing: {sweep_dir}")
    sweeps = sorted(sweep_dir.glob("*.feather"))
    if not sweeps:
        raise FileNotFoundError(f"No LiDAR sweeps in: {sweep_dir}")

    sweep_tss = np.array([int(p.stem) for p in sweeps], dtype=np.int64)
    idx = int(np.argmin(np.abs(sweep_tss - anchor_ts_ns)))
    delta_ms = abs(int(sweep_tss[idx]) - int(anchor_ts_ns)) / 1e6
    if delta_ms > max_delta_ms:
        raise ValueError(
            f"Nearest LiDAR sweep is {delta_ms:.2f} ms from anchor "
            f"(threshold {max_delta_ms} ms)"
        )
    points = read_lidar_sweep(sweeps[idx])
    if points.ndim != 2 or points.shape[1] not in (3, 4):
        raise ValueError(f"Unexpected LiDAR shape: {points.shape}")
    return np.asarray(points[:, :3], dtype=np.float64), int(sweep_tss[idx]), float(delta_ms)


def project_lidar_to_erp_depth(
    points_ego: np.ndarray,
    erp_hw: tuple[int, int] = (1024, 2048),
    min_range_m: float = 0.5,
    max_range_m: float = 100.0,
    densify_radius_px: int = 6,
    fill_far_m: float = 1000.0,
) -> tuple[np.ndarray, dict]:
    """Project LiDAR points to ERP and densify into a per-pixel depth map.

    Args:
        points_ego: (N, 3) LiDAR points in egovehicle frame (x_forward, y_left, z_up).
        erp_hw: (H, W) ERP resolution.
        min_range_m: drop returns closer than this (LiDAR noise / car body).
        max_range_m: drop returns farther than this (sparse far returns are
            unreliable for stitching; we'll fall back to `fill_far_m` for them).
        densify_radius_px: NaN pixels within this radius of a hit pixel get
            the hit's depth (kNN fill). Pixels beyond keep `fill_far_m`.
        fill_far_m: depth value used for pixels with no LiDAR support within
            `densify_radius_px`. Default 1000m ≈ "infinity" for ERP projection.

    Returns:
        depth_map: (H, W) float32. Per-pixel range in meters. Always finite
            (no NaN), with fill_far_m where LiDAR is sparse.
        summary: dict with diagnostics:
            n_input_pts, n_after_range_filter, n_hit_pixels,
            n_densified_pixels, n_far_fill_pixels,
            hit_pct, densified_pct, far_fill_pct
    """
    if points_ego.ndim != 2 or points_ego.shape[1] != 3:
        raise ValueError(f"points_ego must be (N, 3), got {points_ego.shape}")
    h_erp, w_erp = erp_hw

    # 1. Range filter
    ranges = np.linalg.norm(points_ego, axis=1)
    keep = (ranges >= min_range_m) & (ranges <= max_range_m)
    pts = points_ego[keep]
    ranges = ranges[keep].astype(np.float32)
    n_input = int(points_ego.shape[0])
    n_after_range = int(pts.shape[0])

    # 2. Spherical coords from ego origin
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    horiz = np.sqrt(x * x + y * y)
    theta = np.arctan2(y, x)        # [-pi, pi]
    phi = np.arctan2(z, horiz)      # [-pi/2, pi/2]

    # 3. (theta, phi) -> ERP (u, v) per project convention
    # theta = pi - (u + 0.5)/W * 2pi  =>  u = (pi - theta) / (2pi) * W - 0.5
    u = (np.pi - theta) / (2.0 * np.pi) * w_erp - 0.5
    v = (np.pi / 2.0 - phi) / np.pi * h_erp - 0.5
    # ERP wraps horizontally — wrap u into [0, W)
    u_int = np.mod(np.round(u).astype(np.int32), w_erp)
    v_int = np.round(v).astype(np.int32)
    in_v = (v_int >= 0) & (v_int < h_erp)
    u_int = u_int[in_v]
    v_int = v_int[in_v]
    ranges = ranges[in_v]

    # 4. Splat — keep min range per pixel
    # Strategy: sort points by descending range, then scatter (later writes
    # with smaller range overwrite). For ties this is order-dependent but
    # accuracy is well within LiDAR noise.
    order = np.argsort(-ranges)
    u_sorted = u_int[order]
    v_sorted = v_int[order]
    r_sorted = ranges[order]
    sparse_depth = np.full((h_erp, w_erp), np.nan, dtype=np.float32)
    sparse_depth[v_sorted, u_sorted] = r_sorted
    n_hit = int(np.isfinite(sparse_depth).sum())

    # 5. Densify via kNN with max radius
    dense_depth, n_densified = _knn_fill(
        sparse_depth,
        max_radius_px=densify_radius_px,
        fill_value=fill_far_m,
    )
    n_far_fill = int((dense_depth == fill_far_m).sum())

    total_px = h_erp * w_erp
    summary = {
        "n_input_pts": n_input,
        "n_after_range_filter": n_after_range,
        "n_hit_pixels": n_hit,
        "n_densified_pixels": int(n_densified),
        "n_far_fill_pixels": n_far_fill,
        "hit_pct": round(100.0 * n_hit / total_px, 3),
        "densified_pct": round(100.0 * n_densified / total_px, 3),
        "far_fill_pct": round(100.0 * n_far_fill / total_px, 3),
        "erp_hw": [h_erp, w_erp],
        "min_range_m": min_range_m,
        "max_range_m": max_range_m,
        "densify_radius_px": densify_radius_px,
        "fill_far_m": fill_far_m,
    }
    return dense_depth, summary


def _knn_fill(
    sparse_depth: np.ndarray,
    max_radius_px: int,
    fill_value: float,
) -> tuple[np.ndarray, int]:
    """Fill NaN pixels with the value of their nearest non-NaN pixel within
    `max_radius_px`. Pixels with no neighbor in range get `fill_value`.

    Uses scipy.ndimage.distance_transform_edt for an O(H*W)-ish solution
    (linear in pixel count, much faster than per-pixel kNN search).

    Returns:
        dense: (H, W) float32, no NaN.
        n_densified: number of NaN pixels filled with a real neighbor's depth.
    """
    from scipy.ndimage import distance_transform_edt  # local import

    mask_nan = np.isnan(sparse_depth)
    if not mask_nan.any():
        return sparse_depth.astype(np.float32), 0
    # distance_transform_edt with return_indices gives, for each pixel in mask=True,
    # the index of the nearest pixel where mask=False.
    # Note: convention is to set mask to True where we WANT to fill (i.e., NaN).
    dist, (yy_nn, xx_nn) = distance_transform_edt(mask_nan, return_indices=True)
    dense = sparse_depth.copy()
    # For NaN pixels where neighbor is within radius, fill with neighbor's depth.
    fill_mask = mask_nan & (dist <= max_radius_px)
    dense[fill_mask] = sparse_depth[yy_nn[fill_mask], xx_nn[fill_mask]]
    # Remaining NaN pixels (no neighbor in range) → fill_value.
    far_mask = mask_nan & ~fill_mask
    dense[far_mask] = float(fill_value)
    n_densified = int(fill_mask.sum())
    return dense.astype(np.float32), n_densified


def visualize_depth_map(
    depth_map: np.ndarray,
    log_clip_m: float = 50.0,
    far_value_threshold_m: float = 500.0,
) -> np.ndarray:
    """Convert a depth map to an 8-bit RGB visualization for debugging.

    Near (small depth) -> red; mid -> green; far -> blue.
    Pixels with depth >= far_value_threshold_m are drawn dark gray.
    """
    h, w = depth_map.shape[:2]
    out = np.zeros((h, w, 3), dtype=np.uint8)
    near_mask = depth_map < far_value_threshold_m
    if near_mask.any():
        d = depth_map[near_mask]
        d_log = np.log10(np.clip(d, 0.5, log_clip_m))
        d_norm = (d_log - np.log10(0.5)) / (np.log10(log_clip_m) - np.log10(0.5))
        d_norm = np.clip(d_norm, 0.0, 1.0)
        # turbo-like colormap (approximation): red->green->blue
        r = np.clip(2 * (1 - d_norm) * 255, 0, 255).astype(np.uint8)
        g = np.clip(255 - 2 * np.abs(d_norm - 0.5) * 255, 0, 255).astype(np.uint8)
        b = np.clip(2 * d_norm * 255, 0, 255).astype(np.uint8)
        out[near_mask] = np.stack([r, g, b], axis=-1)
    out[~near_mask] = (40, 40, 40)
    return out
