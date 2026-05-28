"""Region-coherent hard seam routing for AV ring panoramas.

The previous DP seam router can move the seam, but it still treats each row
independently. That lets the path cut through connected structures such as
cars, lane markings, poles, and facade edges. This module keeps the same
source-faithful contract as hard_select: every output pixel is copied from
exactly one input slab. The only extra rule is region coherence near a seam:
if a high-structure connected component is split by the routed seam, assign
the whole component to one camera.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from waymo2panorama.blending.hard_hdr_of import RING_PAIRS
from waymo2panorama.blending.seam_routing import (
    SeamRouteConfig,
    route_pair_seam,
    seam_mask_to_rgb,
)


@dataclass
class RegionCoherentConfig:
    """Parameters for source-faithful region-coherent seam cleanup."""

    band_half_width: int = 72
    max_step: int = 3
    threshold: float = 1e-6
    canny_low: int = 55
    canny_high: int = 145
    edge_dilate: int = 7
    seam_dilate: int = 9
    min_component_area: int = 36
    max_component_area: int = 18000
    max_component_width_frac: float = 0.42
    max_component_height_frac: float = 0.92
    boundary_weight: float = 1.0
    source_weight: float = 18.0
    change_weight: float = 3.0
    min_switch_gain: float = 0.0


def _to_u8(rgb: np.ndarray) -> np.ndarray:
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _to_y_u8(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(_to_u8(rgb), cv2.COLOR_RGB2YCrCb)[..., 0]


def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    gray_f = gray.astype(np.float32)
    gx = cv2.Sobel(gray_f, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray_f, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy).astype(np.float32)


def _norm01(x: np.ndarray, mask: np.ndarray) -> np.ndarray:
    vals = x[mask].astype(np.float32) if mask.any() else x.reshape(-1).astype(np.float32)
    if vals.size == 0:
        return np.zeros_like(x, dtype=np.float32)
    lo = float(np.percentile(vals, 8))
    hi = float(np.percentile(vals, 96))
    if hi <= lo + 1e-6:
        return np.zeros_like(x, dtype=np.float32)
    return np.clip((x.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    if not mask.any():
        return None
    yy, xx = np.where(mask)
    return int(yy.min()), int(yy.max()) + 1, int(xx.min()), int(xx.max()) + 1


def _label_to_y(label: np.ndarray, slab_y: Sequence[np.ndarray]) -> np.ndarray:
    out = np.zeros(label.shape, dtype=np.float32)
    for idx, y in enumerate(slab_y):
        m = label == idx
        if m.any():
            out[m] = y[m].astype(np.float32)
    return out


def _build_structure_components(
    slab_a: np.ndarray,
    slab_b: np.ndarray,
    band: np.ndarray,
    seam_mask: np.ndarray,
    cfg: RegionCoherentConfig,
) -> tuple[np.ndarray, dict[str, int]]:
    """Find high-structure connected components near the routed seam."""
    gray_a = _to_y_u8(slab_a)
    gray_b = _to_y_u8(slab_b)
    edges = cv2.bitwise_or(
        cv2.Canny(gray_a, cfg.canny_low, cfg.canny_high),
        cv2.Canny(gray_b, cfg.canny_low, cfg.canny_high),
    )
    if cfg.edge_dilate > 1:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.edge_dilate, cfg.edge_dilate))
        edges = cv2.dilate(edges, k)

    sobel = np.maximum(_sobel_mag(gray_a), _sobel_mag(gray_b))
    sobel_n = _norm01(sobel, band)
    color = np.abs(gray_a.astype(np.float32) - gray_b.astype(np.float32))
    color_n = _norm01(color, band)

    # High source structure is the part where a hard seam is visually risky.
    structure = ((edges > 0) | (sobel_n >= 0.62) | ((sobel_n >= 0.42) & (color_n >= 0.38))) & band
    if cfg.seam_dilate > 1:
        ks = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.seam_dilate, cfg.seam_dilate))
        near_seam = cv2.dilate(seam_mask.astype(np.uint8), ks) > 0
    else:
        near_seam = seam_mask
    structure &= near_seam

    if cfg.edge_dilate > 1:
        structure = cv2.morphologyEx(
            structure.astype(np.uint8),
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        ).astype(bool)
        structure &= band

    num, labels = cv2.connectedComponents(structure.astype(np.uint8), connectivity=8)
    stats = {
        "raw_components": max(0, int(num) - 1),
        "raw_structure_pixels": int(structure.sum()),
    }
    return labels.astype(np.int32), stats


def _boundary_score(
    candidate_y: np.ndarray,
    current_y: np.ndarray,
    component: np.ndarray,
    valid: np.ndarray,
) -> tuple[float, int]:
    """Mean 4-neighbor Y discontinuity around the component boundary."""
    vals: list[np.ndarray] = []
    # inner pixel is component, outer neighbor is current output.
    m = component[:-1, :] & (~component[1:, :]) & valid[1:, :]
    if m.any():
        vals.append(np.abs(candidate_y[:-1, :][m] - current_y[1:, :][m]))
    m = component[1:, :] & (~component[:-1, :]) & valid[:-1, :]
    if m.any():
        vals.append(np.abs(candidate_y[1:, :][m] - current_y[:-1, :][m]))
    m = component[:, :-1] & (~component[:, 1:]) & valid[:, 1:]
    if m.any():
        vals.append(np.abs(candidate_y[:, :-1][m] - current_y[:, 1:][m]))
    m = component[:, 1:] & (~component[:, :-1]) & valid[:, :-1]
    if m.any():
        vals.append(np.abs(candidate_y[:, 1:][m] - current_y[:, :-1][m]))
    if not vals:
        return 0.0, 0
    all_vals = np.concatenate([v.astype(np.float32).reshape(-1) for v in vals])
    return float(all_vals.mean()), int(all_vals.size)


def _component_owner(
    component: np.ndarray,
    label: np.ndarray,
    slabs_y: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    pair: tuple[int, int],
    valid: np.ndarray,
    cfg: RegionCoherentConfig,
) -> tuple[int, dict[str, float | int]]:
    """Choose whether a whole structure component should come from cam i or j."""
    i, j = pair
    current_y = _label_to_y(label, slabs_y)
    cand_scores: dict[int, float] = {}
    cand_boundary: dict[int, float] = {}
    cand_support: dict[int, int] = {}
    cand_weight: dict[int, float] = {}
    cand_change: dict[int, float] = {}
    area = float(component.sum())
    for cam in (i, j):
        boundary, n_boundary = _boundary_score(slabs_y[cam].astype(np.float32), current_y, component, valid)
        mean_weight = float(weights[cam][component].mean()) if component.any() else 0.0
        changed_frac = float(np.mean(label[component] != cam)) if component.any() else 0.0
        score = (
            cfg.boundary_weight * boundary
            + cfg.source_weight * (1.0 - mean_weight)
            + cfg.change_weight * changed_frac
        )
        cand_scores[cam] = float(score)
        cand_boundary[cam] = float(boundary)
        cand_support[cam] = int(n_boundary)
        cand_weight[cam] = float(mean_weight)
        cand_change[cam] = float(changed_frac)

    owner = i if cand_scores[i] <= cand_scores[j] else j
    current_major = i if float(np.mean(label[component] == i)) >= float(np.mean(label[component] == j)) else j
    if cand_scores[owner] + cfg.min_switch_gain > cand_scores[current_major]:
        owner = current_major

    diag: dict[str, float | int] = {
        "owner": int(owner),
        "area": int(area),
        "score_i": cand_scores[i],
        "score_j": cand_scores[j],
        "boundary_i": cand_boundary[i],
        "boundary_j": cand_boundary[j],
        "support_i": cand_support[i],
        "support_j": cand_support[j],
        "weight_i": cand_weight[i],
        "weight_j": cand_weight[j],
        "change_i": cand_change[i],
        "change_j": cand_change[j],
    }
    return owner, diag


def protect_pair_regions(
    routed_label: np.ndarray,
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    pair: tuple[int, int],
    band: np.ndarray,
    seam_mask: np.ndarray,
    cfg: RegionCoherentConfig,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """Make high-structure regions cut by a pair seam source-coherent."""
    i, j = pair
    H, W = routed_label.shape
    valid = band & ((routed_label == i) | (routed_label == j))
    if not valid.any() or not seam_mask.any():
        return routed_label, np.zeros_like(routed_label, dtype=bool), {
            "accepted_components": 0,
            "rejected_components": 0,
            "protected_pixels": 0,
            "status": "no_valid_seam",
        }

    component_labels, struct_stats = _build_structure_components(slabs[i], slabs[j], band, seam_mask, cfg)
    out_label = routed_label.copy()
    protected = np.zeros_like(routed_label, dtype=bool)
    slab_y = [_to_y_u8(s).astype(np.float32) for s in slabs]
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []

    for comp_id in range(1, int(component_labels.max()) + 1):
        comp = (component_labels == comp_id) & valid
        area = int(comp.sum())
        if area < cfg.min_component_area:
            if area:
                rejected.append({"component": int(comp_id), "area": area, "reason": "too_small"})
            continue
        bb = _bbox(comp)
        if bb is None:
            continue
        y0, y1, x0, x1 = bb
        if area > cfg.max_component_area:
            rejected.append({"component": int(comp_id), "area": area, "bbox": [y0, y1, x0, x1], "reason": "too_large"})
            continue
        if (x1 - x0) > cfg.max_component_width_frac * W or (y1 - y0) > cfg.max_component_height_frac * H:
            rejected.append({"component": int(comp_id), "area": area, "bbox": [y0, y1, x0, x1], "reason": "too_wide"})
            continue
        if not seam_mask[comp].any():
            rejected.append({"component": int(comp_id), "area": area, "bbox": [y0, y1, x0, x1], "reason": "misses_seam"})
            continue
        labs = out_label[comp]
        if not (np.any(labs == i) and np.any(labs == j)):
            rejected.append({"component": int(comp_id), "area": area, "bbox": [y0, y1, x0, x1], "reason": "already_coherent"})
            continue

        owner, diag = _component_owner(comp, out_label, slab_y, weights, pair, valid, cfg)
        out_label[comp] = owner
        protected |= comp
        diag.update({"component": int(comp_id), "bbox": [y0, y1, x0, x1]})
        accepted.append(diag)

    diag = {
        **struct_stats,
        "accepted_components": len(accepted),
        "rejected_components": len(rejected),
        "protected_pixels": int(protected.sum()),
        "accepted": accepted[:64],
        "rejected": rejected[:64],
        "status": "ok",
    }
    return out_label, protected, diag


def blend_region_coherent_seam(
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    ring_pairs: Sequence[tuple[int, int]] = RING_PAIRS,
    return_diagnostics: bool = False,
    **kwargs,
) -> np.ndarray | tuple[np.ndarray, dict]:
    """Hard-select panorama with DP seam routing plus region coherence."""
    if len(slabs) != len(weights):
        raise ValueError(f"slabs/weights length mismatch: {len(slabs)} vs {len(weights)}")
    if not slabs:
        raise ValueError("empty slabs")
    cfg = RegionCoherentConfig(**kwargs)
    route_cfg = SeamRouteConfig(
        band_half_width=cfg.band_half_width,
        max_step=cfg.max_step,
        threshold=cfg.threshold,
        canny_low=cfg.canny_low,
        canny_high=cfg.canny_high,
        edge_dilate=cfg.edge_dilate,
    )
    work_slabs = [np.clip(s, 0, 255).astype(np.float32) for s in slabs]
    work_weights = [w.astype(np.float32) for w in weights]
    base_label = np.argmax(np.stack(work_weights, axis=0), axis=0).astype(np.int16)
    routed_label = base_label.copy()
    seam_mask_all = np.zeros_like(base_label, dtype=bool)
    protected_all = np.zeros_like(base_label, dtype=bool)
    pair_diags: list[dict[str, object]] = []

    for i, j in ring_pairs:
        if i >= len(work_slabs) or j >= len(work_slabs):
            continue
        assign_a, pair_band, pair_seam, route_diag = route_pair_seam(
            work_slabs[i],
            work_weights[i],
            work_slabs[j],
            work_weights[j],
            cfg=route_cfg,
        )
        mutable = pair_band & ((base_label == i) | (base_label == j))
        routed_label[mutable & assign_a] = i
        routed_label[mutable & ~assign_a] = j
        routed_label, protected, protect_diag = protect_pair_regions(
            routed_label,
            work_slabs,
            work_weights,
            (i, j),
            pair_band,
            pair_seam,
            cfg,
        )
        seam_mask_all |= pair_seam
        protected_all |= protected
        route_diag["pair"] = [int(i), int(j)]
        route_diag["mutable_pixels"] = int(mutable.sum())
        route_diag["region_coherence"] = protect_diag
        pair_diags.append(route_diag)

    out = np.zeros_like(work_slabs[0], dtype=np.uint8)
    for idx, slab in enumerate(work_slabs):
        m = routed_label == idx
        out[m] = np.clip(slab[m], 0, 255).astype(np.uint8)

    diagnostics = {
        "pairs": pair_diags,
        "routed_pixels_changed": int((routed_label != base_label).sum()),
        "protected_pixels": int(protected_all.sum()),
        "seam_mask_pixels": int(seam_mask_all.sum()),
        "label_map": routed_label,
        "seam_mask": seam_mask_all,
        "protected_mask": protected_all,
    }
    if return_diagnostics:
        return out, diagnostics
    return out


def region_mask_to_rgb(mask: np.ndarray, base: np.ndarray | None = None) -> np.ndarray:
    """Overlay protected regions in cyan for diagnostics."""
    if base is None:
        rgb = np.zeros((*mask.shape, 3), dtype=np.uint8)
    else:
        rgb = _to_u8(base).copy()
    dil = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    rgb[dil] = np.array([0, 255, 255], dtype=np.uint8)
    return rgb


def seam_and_region_to_rgb(seam_mask: np.ndarray, region_mask: np.ndarray, base: np.ndarray) -> np.ndarray:
    """Overlay seam path in red and protected regions in cyan."""
    rgb = region_mask_to_rgb(region_mask, base)
    return seam_mask_to_rgb(seam_mask, rgb)
