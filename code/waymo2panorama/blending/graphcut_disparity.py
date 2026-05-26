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
