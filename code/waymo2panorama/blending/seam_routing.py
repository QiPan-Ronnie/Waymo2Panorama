"""Visibility-aware DP seam routing for L1 hard-select panoramas.

This module does not blend, warp, estimate depth, or use a learned model.
It keeps the hard-select contract: every output pixel is copied from exactly
one camera slab. The only change is that the seam path inside each adjacent
camera overlap can move within a narrow band around the original cos^2
Voronoi boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band


@dataclass
class SeamRouteConfig:
    """Weights and search parameters for DP seam routing."""

    band_half_width: int = 64
    max_step: int = 3
    threshold: float = 1e-6
    color_weight: float = 1.0
    grad_weight: float = 0.8
    edge_weight: float = 2.0
    reliability_weight: float = 0.8
    center_weight: float = 0.5
    external_weight: float = 0.0
    canny_low: int = 60
    canny_high: int = 150
    edge_dilate: int = 5


def _to_gray_u8(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)


def _norm01(x: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    x = x.astype(np.float32)
    if mask is not None and mask.any():
        vals = x[mask]
    else:
        vals = x.reshape(-1)
    if vals.size == 0:
        return np.zeros_like(x, dtype=np.float32)
    lo = float(np.percentile(vals, 5))
    hi = float(np.percentile(vals, 95))
    if hi <= lo + 1e-6:
        return np.zeros_like(x, dtype=np.float32)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    gray_f = gray.astype(np.float32)
    gx = cv2.Sobel(gray_f, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray_f, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def _edge_line_penalty(gray_a: np.ndarray, gray_b: np.ndarray, cfg: SeamRouteConfig) -> np.ndarray:
    """Return a dilated edge/line penalty map in [0, 1]."""
    edges_a = cv2.Canny(gray_a, cfg.canny_low, cfg.canny_high)
    edges_b = cv2.Canny(gray_b, cfg.canny_low, cfg.canny_high)
    edges = cv2.bitwise_or(edges_a, edges_b)
    if cfg.edge_dilate > 1:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.edge_dilate, cfg.edge_dilate))
        edges = cv2.dilate(edges, k)
    return (edges.astype(np.float32) / 255.0).astype(np.float32)


def build_seam_route_cost(
    slab_a: np.ndarray,
    weight_a: np.ndarray,
    slab_b: np.ndarray,
    weight_b: np.ndarray,
    band: np.ndarray,
    signed: np.ndarray,
    cfg: SeamRouteConfig,
    external_cost: np.ndarray | None = None,
) -> np.ndarray:
    """Build the content/structure cost used by DP inside `band`."""
    valid = band.astype(bool)
    rgb_a = np.clip(slab_a, 0, 255).astype(np.float32)
    rgb_b = np.clip(slab_b, 0, 255).astype(np.float32)
    gray_a = _to_gray_u8(rgb_a)
    gray_b = _to_gray_u8(rgb_b)

    color = np.mean(np.abs(rgb_a - rgb_b), axis=2)
    color = _norm01(color, valid)

    grad = np.abs(_sobel_mag(gray_a) - _sobel_mag(gray_b))
    grad = _norm01(grad, valid)

    edge = _edge_line_penalty(gray_a, gray_b, cfg)

    min_w = np.minimum(weight_a, weight_b).astype(np.float32)
    rel = _norm01(min_w, valid)
    reliability_penalty = 1.0 - rel

    center = np.zeros_like(min_w, dtype=np.float32)
    finite = np.isfinite(signed)
    if cfg.band_half_width > 0:
        center[finite] = np.clip(np.abs(signed[finite]) / float(cfg.band_half_width), 0.0, 1.0)
        center = center * center

    cost = (
        cfg.color_weight * color
        + cfg.grad_weight * grad
        + cfg.edge_weight * edge
        + cfg.reliability_weight * reliability_penalty
        + cfg.center_weight * center
    )
    if external_cost is not None and cfg.external_weight > 0:
        if external_cost.shape != weight_a.shape:
            raise ValueError(f"external_cost shape {external_cost.shape} != {weight_a.shape}")
        ext = _norm01(external_cost.astype(np.float32), valid)
        cost = cost + cfg.external_weight * ext
    cost = np.where(valid, cost, np.inf).astype(np.float32)
    return cost


def solve_dp_seam(cost: np.ndarray, valid: np.ndarray, max_step: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Find a top-to-bottom min-cost seam path.

    Args:
        cost: 2D cost image. Invalid entries may be inf.
        valid: 2D bool mask where the seam is allowed to pass.
        max_step: maximum horizontal drift per valid row.

    Returns:
        path_x: length-H int array with seam column per row.
        path_valid: length-H bool array indicating rows with a valid seam point.
    """
    if cost.shape != valid.shape:
        raise ValueError(f"cost/valid shape mismatch: {cost.shape} vs {valid.shape}")
    H, W = cost.shape
    rows = np.flatnonzero(valid.any(axis=1))
    path_x = np.zeros(H, dtype=np.int32)
    path_valid = np.zeros(H, dtype=bool)
    if rows.size == 0:
        path_x[:] = W // 2
        return path_x, path_valid

    INF = 1e18
    dp_prev = np.full(W, INF, dtype=np.float64)
    parent: dict[int, np.ndarray] = {}
    first = int(rows[0])
    dp_prev[valid[first]] = cost[first, valid[first]].astype(np.float64)
    prev_row = first

    for row in rows[1:]:
        row = int(row)
        best = np.full(W, INF, dtype=np.float64)
        par = np.zeros(W, dtype=np.int16)
        for off in range(-max_step, max_step + 1):
            shifted = np.full(W, INF, dtype=np.float64)
            if off < 0:
                shifted[:off] = dp_prev[-off:]
            elif off > 0:
                shifted[off:] = dp_prev[:-off]
            else:
                shifted[:] = dp_prev
            better = shifted < best
            best[better] = shifted[better]
            par[better] = off
        cur = np.full(W, INF, dtype=np.float64)
        cur[valid[row]] = best[valid[row]] + cost[row, valid[row]].astype(np.float64)
        parent[row] = par
        dp_prev = cur
        prev_row = row

    end_x = int(np.argmin(dp_prev))
    if not np.isfinite(dp_prev[end_x]) or dp_prev[end_x] >= INF * 0.5:
        path_x[:] = W // 2
        return path_x, path_valid

    cur_x = end_x
    for row in rows[::-1]:
        row = int(row)
        path_x[row] = cur_x
        path_valid[row] = True
        if row == first:
            break
        cur_x = int(np.clip(cur_x - int(parent[row][cur_x]), 0, W - 1))

    # Fill gaps/top/bottom by nearest valid row for visualization and side tests.
    for idx in range(1, rows.size):
        a = int(rows[idx - 1])
        b = int(rows[idx])
        if b > a + 1:
            path_x[a + 1 : b] = path_x[a]
    path_x[: first] = path_x[first]
    path_x[prev_row + 1 :] = path_x[prev_row]
    return path_x, path_valid


def _left_side_prefers_a(weight_a: np.ndarray, weight_b: np.ndarray, signed: np.ndarray, band: np.ndarray) -> bool:
    left = band & np.isfinite(signed) & (signed < 0)
    right = band & np.isfinite(signed) & (signed > 0)
    left_score = float((weight_a[left] - weight_b[left]).mean()) if left.any() else 0.0
    right_score = float((weight_a[right] - weight_b[right]).mean()) if right.any() else 0.0
    if abs(left_score) > 1e-6:
        return left_score >= 0
    return right_score <= 0


def route_pair_seam(
    slab_a: np.ndarray,
    weight_a: np.ndarray,
    slab_b: np.ndarray,
    weight_b: np.ndarray,
    cfg: SeamRouteConfig | None = None,
    external_cost: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Route a seam for one adjacent pair.

    Returns:
        assign_a: bool image; True means choose cam A in the routed band.
        band: bool image where this pair is allowed to override hard_select.
        seam_mask: bool one-pixel seam path visualization.
        diagnostics: JSON-serializable summary.
    """
    cfg = cfg or SeamRouteConfig()
    band, signed = build_voronoi_seam_band(
        weight_a,
        weight_b,
        band_half_width=cfg.band_half_width,
        threshold=cfg.threshold,
    )
    overlap = (weight_a > cfg.threshold) & (weight_b > cfg.threshold)
    band &= overlap
    if not band.any():
        return (
            np.zeros_like(weight_a, dtype=bool),
            band,
            np.zeros_like(weight_a, dtype=bool),
            {
            "band_pixels": 0,
            "path_rows": 0,
            "status": "no_overlap",
            },
        )

    cost = build_seam_route_cost(
        slab_a,
        weight_a,
        slab_b,
        weight_b,
        band,
        signed,
        cfg,
        external_cost=external_cost,
    )
    yy, xx = np.where(band)
    y0, y1 = int(yy.min()), int(yy.max()) + 1
    x0, x1 = int(xx.min()), int(xx.max()) + 1
    local_cost = cost[y0:y1, x0:x1]
    local_valid = np.isfinite(local_cost) & band[y0:y1, x0:x1]
    path_local, path_valid = solve_dp_seam(local_cost, local_valid, max_step=cfg.max_step)

    assign_a = np.zeros_like(weight_a, dtype=bool)
    seam_mask = np.zeros_like(weight_a, dtype=bool)
    left_is_a = _left_side_prefers_a(weight_a, weight_b, signed, band)
    for lr, ok in enumerate(path_valid):
        if not ok:
            continue
        y = y0 + lr
        sx = x0 + int(path_local[lr])
        row_band = band[y]
        cols = np.flatnonzero(row_band)
        if cols.size == 0:
            continue
        seam_mask[y, sx] = True
        if left_is_a:
            assign_a[y, cols[cols <= sx]] = True
        else:
            assign_a[y, cols[cols > sx]] = True

    path_cols = (x0 + path_local[path_valid]).astype(np.float32)
    edge_full = _edge_line_penalty(_to_gray_u8(slab_a), _to_gray_u8(slab_b), cfg) > 0.5
    diag = {
        "band_pixels": int(band.sum()),
        "path_rows": int(path_valid.sum()),
        "bbox": [y0, y1, x0, x1],
        "left_is_a": bool(left_is_a),
        "mean_path_x": float(path_cols.mean()) if path_cols.size else None,
        "std_path_x": float(path_cols.std()) if path_cols.size else None,
        "mean_cost_on_path": float(local_cost[path_valid, path_local[path_valid]].mean())
        if path_valid.any()
        else None,
        "edge_cross_count": int((edge_full & seam_mask).sum()),
        "external_weight": float(cfg.external_weight),
        "status": "ok",
    }
    return assign_a, band, seam_mask, diag


def blend_seam_routing(
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    ring_pairs: Sequence[tuple[int, int]] = RING_PAIRS,
    return_diagnostics: bool = False,
    external_cost: np.ndarray | None = None,
    **kwargs,
) -> np.ndarray | tuple[np.ndarray, dict]:
    """Hard-select panorama with DP-routed seams inside adjacent overlaps."""
    if len(slabs) != len(weights):
        raise ValueError(f"slabs/weights length mismatch: {len(slabs)} vs {len(weights)}")
    if not slabs:
        raise ValueError("empty slabs")
    cfg = SeamRouteConfig(**kwargs)
    work_slabs = [np.clip(s, 0, 255).astype(np.float32) for s in slabs]
    work_weights = [w.astype(np.float32) for w in weights]
    stack_w = np.stack(work_weights, axis=0)
    base_label = np.argmax(stack_w, axis=0).astype(np.int16)
    routed_label = base_label.copy()
    seam_mask = np.zeros_like(base_label, dtype=bool)
    pair_diags: list[dict] = []

    for i, j in ring_pairs:
        if i >= len(work_slabs) or j >= len(work_slabs):
            continue
        assign_a, pair_band, pair_seam, diag = route_pair_seam(
            work_slabs[i],
            work_weights[i],
            work_slabs[j],
            work_weights[j],
            cfg=cfg,
            external_cost=external_cost,
        )
        mutable = pair_band & ((base_label == i) | (base_label == j))
        routed_label[mutable & assign_a] = i
        routed_label[mutable & ~assign_a] = j
        seam_mask |= pair_seam
        diag["pair"] = [int(i), int(j)]
        diag["mutable_pixels"] = int(mutable.sum())
        pair_diags.append(diag)

    out = np.zeros_like(work_slabs[0], dtype=np.uint8)
    for idx, slab in enumerate(work_slabs):
        m = routed_label == idx
        out[m] = np.clip(slab[m], 0, 255).astype(np.uint8)

    diagnostics = {
        "pairs": pair_diags,
        "routed_pixels_changed": int((routed_label != base_label).sum()),
        "seam_mask_pixels": int(seam_mask.sum()),
        "label_map": routed_label,
        "seam_mask": seam_mask,
    }
    if return_diagnostics:
        public_diag = dict(diagnostics)
        public_diag["label_map"] = routed_label
        public_diag["seam_mask"] = seam_mask
        return out, public_diag
    return out


def seam_mask_to_rgb(seam_mask: np.ndarray, base: np.ndarray | None = None) -> np.ndarray:
    """Overlay seam path in red for diagnostics."""
    if base is None:
        rgb = np.zeros((*seam_mask.shape, 3), dtype=np.uint8)
    else:
        rgb = np.clip(base, 0, 255).astype(np.uint8).copy()
    dil = cv2.dilate(seam_mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    rgb[dil] = np.array([255, 0, 0], dtype=np.uint8)
    return rgb
