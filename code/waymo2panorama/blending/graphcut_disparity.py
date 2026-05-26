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


def apply_seam_to_pair_weights(
    w_a: np.ndarray,
    w_b: np.ndarray,
    seam_u: np.ndarray,
    overlap_mask: np.ndarray,
    soft_px: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert soft cos^2 blend to graphcut seam: left of seam = cam_a, right = cam_b.

    Inside overlap_mask:
      - Columns < seam_u[v]: w_a = w_a + w_b (cam_a takes over), w_b = 0
      - Columns >= seam_u[v]: w_b = w_b + w_a (cam_b takes over), w_a = 0
      - soft_px > 0: small Gaussian blur on the binary mask edge for visual smoothness

    Outside overlap_mask: weights unchanged.

    Returns (w_a_new, w_b_new) float32 arrays same shape as input.
    """
    H, W = w_a.shape
    assert w_b.shape == (H, W) and overlap_mask.shape == (H, W)
    w_total = (w_a + w_b).astype(np.float32)
    mask_a_side = np.zeros((H, W), dtype=np.float32)
    for v in range(H):
        u_cut = int(seam_u[v])
        mask_a_side[v, :u_cut] = 1.0  # left of seam belongs to cam_a
    if soft_px > 0:
        import cv2
        k = max(3, soft_px * 2 + 1)
        mask_a_side = cv2.GaussianBlur(mask_a_side, (k, k), soft_px)
    mask_b_side = 1.0 - mask_a_side
    # Only apply inside overlap
    in_overlap = overlap_mask.astype(np.float32)
    w_a_new = w_a * (1.0 - in_overlap) + (w_total * mask_a_side) * in_overlap
    w_b_new = w_b * (1.0 - in_overlap) + (w_total * mask_b_side) * in_overlap
    return w_a_new.astype(np.float32), w_b_new.astype(np.float32)


from pathlib import Path

from waymo2panorama.alignment.sparse_displacement import (
    build_per_cam_displacements_from_stereo,
)


def build_seam_weights_b1(
    l1_weights: dict[str, np.ndarray],
    stereo_npz_paths,
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    adjacent_pairs: list[tuple[str, str]],
    erp_hw: tuple[int, int],
    disparity_sigma_px: float = 20.0,
    seam_smoothness: float = 1.0,
    seam_soft_px: int = 2,
) -> tuple[dict[str, np.ndarray], dict]:
    """Orchestrator: L1 weights + stereo cache -> seam-modified weights per pair.

    For each adjacent pair (cam_a, cam_b):
      1. Find overlap mask (both weights > eps)
      2. Build per-pair disparity from stereo .npz that involves THIS pair
      3. Find min-disparity seam through overlap
      4. Replace soft cos^2 blend with hard 0/1 mask (with soft edge)
    """
    cam_names = list(l1_weights.keys())
    sparse_per_cam = build_per_cam_displacements_from_stereo(
        stereo_npz_paths, cam_K=cam_K, cam_T_ego_cam=cam_T_ego_cam,
        cam_names=cam_names, erp_hw=erp_hw,
    )
    # Re-index sparse displacements by stereo .npz path for per-pair lookup
    pair_anchors: dict[tuple[str, str], tuple[list, list]] = {}
    from waymo2panorama.alignment.sparse_displacement import _load_stereo_pair
    for p in stereo_npz_paths:
        loaded = _load_stereo_pair(Path(p))
        if loaded is None: continue
        cam_a, cam_b, pts = loaded
        if cam_a not in cam_names or cam_b not in cam_names: continue
        cam_a_anchors_for_pair = []
        cam_b_anchors_for_pair = []
        from waymo2panorama.alignment.sparse_displacement import (
            _compute_l1_erp_pixel_per_cam,
        )
        from waymo2panorama.pipeline.lift_and_project import ego_points_to_erp_uv
        for pt in pts:
            u_ideal, v_ideal, _ = ego_points_to_erp_uv(pt.reshape(1, 3), erp_hw=erp_hw)
            ideal_uv = np.array([float(u_ideal[0]), float(v_ideal[0])])
            l1_a = _compute_l1_erp_pixel_per_cam(pt, cam_K[cam_a], cam_T_ego_cam[cam_a], erp_hw)
            l1_b = _compute_l1_erp_pixel_per_cam(pt, cam_K[cam_b], cam_T_ego_cam[cam_b], erp_hw)
            if np.any(np.isnan(l1_a)) or np.any(np.isnan(l1_b)): continue
            W = erp_hw[1]
            def _wrap(d):
                if d > W / 2: return d - W
                elif d < -W / 2: return d + W
                return d
            delta_a = np.array([_wrap(ideal_uv[0] - l1_a[0]), ideal_uv[1] - l1_a[1]])
            delta_b = np.array([_wrap(ideal_uv[0] - l1_b[0]), ideal_uv[1] - l1_b[1]])
            cam_a_anchors_for_pair.append((ideal_uv, delta_a))
            cam_b_anchors_for_pair.append((ideal_uv, delta_b))
        if cam_a_anchors_for_pair:
            pair_anchors[(cam_a, cam_b)] = (cam_a_anchors_for_pair, cam_b_anchors_for_pair)

    out_weights = {c: l1_weights[c].astype(np.float32).copy() for c in cam_names}
    n_pairs_done = 0
    per_pair_log: list[dict] = []
    for (cam_a, cam_b) in adjacent_pairs:
        if cam_a not in out_weights or cam_b not in out_weights:
            continue
        overlap_mask = (out_weights[cam_a] > 1e-3) & (out_weights[cam_b] > 1e-3)
        if not overlap_mask.any():
            per_pair_log.append({
                "cam_a": cam_a, "cam_b": cam_b, "status": "no_overlap",
            })
            continue
        anchors = pair_anchors.get((cam_a, cam_b))
        if anchors is None:
            per_pair_log.append({
                "cam_a": cam_a, "cam_b": cam_b, "status": "no_stereo",
            })
            continue
        disp_mag = build_pair_disparity_magnitude(
            anchors[0], anchors[1], erp_hw=erp_hw, sigma_px=disparity_sigma_px,
        )
        # Boost cost outside overlap to keep seam inside
        cost_field = disp_mag.copy()
        cost_field[~overlap_mask] = 1e6
        seam_u = find_min_disparity_seam(
            cost_field, overlap_mask, u_smoothness=seam_smoothness,
        )
        out_weights[cam_a], out_weights[cam_b] = apply_seam_to_pair_weights(
            out_weights[cam_a], out_weights[cam_b], seam_u,
            overlap_mask, soft_px=seam_soft_px,
        )
        n_pairs_done += 1
        per_pair_log.append({
            "cam_a": cam_a, "cam_b": cam_b, "status": "ok",
            "n_anchors": len(anchors[0]),
            "max_disp_mag": float(disp_mag.max()),
        })
    summary = {
        "n_pairs_total": len(adjacent_pairs),
        "n_pairs_with_seam": n_pairs_done,
        "per_pair": per_pair_log,
    }
    return out_weights, summary
