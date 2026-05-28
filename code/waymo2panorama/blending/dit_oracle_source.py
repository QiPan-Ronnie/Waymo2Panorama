"""Source-faithful selection guided by a DiT360 seam proposal.

The DiT360 output is treated only as an oracle target.  The final image still
copies each pixel from one of the original ERP camera slabs; no generated pixel
is inserted into the panorama.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class DiTOracleConfig:
    """Parameters for DiT-guided source selection."""

    mask_dilate_px: int = 8
    min_weight: float = 1e-4
    color_weight: float = 1.0
    grad_weight: float = 0.18
    weight_penalty: float = 22.0
    stay_penalty: float = 3.0
    switch_margin: float = 7.0
    min_component_area: int = 96
    min_component_gain: float = 8.5
    max_changed_frac: float = 0.08


def _as_stack(items: list[np.ndarray], dtype=np.float32) -> np.ndarray:
    return np.stack([np.asarray(x, dtype=dtype) for x in items], axis=0)


def _gray(rgb: np.ndarray) -> np.ndarray:
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)


def _grad_mag(gray: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return np.sqrt(gx * gx + gy * gy).astype(np.float32)


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask.astype(bool)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    return cv2.dilate(mask.astype(np.uint8), k).astype(bool)


def hard_label_map(weights: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Return argmax-weight labels and winner weights."""
    w_stack = _as_stack(weights, dtype=np.float32)
    labels = np.argmax(w_stack, axis=0).astype(np.int16)
    winner = np.take_along_axis(w_stack, labels[None, ...], axis=0)[0]
    return labels, winner


def compose_from_labels(slabs: list[np.ndarray], labels: np.ndarray) -> np.ndarray:
    """Copy pixels from ERP slabs according to a label map."""
    slab_stack = _as_stack(slabs, dtype=np.uint8)
    h, w = labels.shape
    yy, xx = np.indices((h, w))
    return slab_stack[labels, yy, xx].astype(np.uint8)


def blend_dit_oracle_source(
    slabs: list[np.ndarray],
    weights: list[np.ndarray],
    dit_target: np.ndarray,
    preserve_mask: np.ndarray | None = None,
    config: DiTOracleConfig | None = None,
    return_diagnostics: bool = False,
) -> np.ndarray | tuple[np.ndarray, dict]:
    """Select real source pixels using DiT360 as a target appearance oracle.

    Args:
        slabs: Per-camera ERP RGB slabs.
        weights: Per-camera ERP visibility weights.
        dit_target: DiT360 raw RGB output, resized to the ERP size.
        preserve_mask: Optional DiT mask, white/255 means source preserved,
            black/0 means DiT generated.  Selection is only allowed inside the
            generated region plus a small halo.
        config: Selection hyperparameters.
        return_diagnostics: If true, also return masks and scalar stats.

    Returns:
        Source-faithful RGB image, and optionally a diagnostics dictionary.
    """
    cfg = config or DiTOracleConfig()
    slab_stack = _as_stack(slabs, dtype=np.float32)
    w_stack = _as_stack(weights, dtype=np.float32)
    n_cam, h, w = w_stack.shape
    if dit_target.shape[:2] != (h, w):
        dit_target = cv2.resize(
            np.clip(dit_target, 0, 255).astype(np.uint8),
            (w, h),
            interpolation=cv2.INTER_CUBIC,
        )
    target = dit_target.astype(np.float32)

    labels, winner_w = hard_label_map(weights)
    hard = compose_from_labels(slabs, labels)
    overlap = np.sum(w_stack > cfg.min_weight, axis=0) >= 2
    if preserve_mask is None:
        interest = overlap
        core = overlap.copy()
    else:
        if preserve_mask.shape[:2] != (h, w):
            preserve_mask = cv2.resize(preserve_mask, (w, h), interpolation=cv2.INTER_NEAREST)
        core = preserve_mask < 128
        interest = _dilate(core, cfg.mask_dilate_px) & overlap

    target_gray = _gray(target)
    target_grad = _grad_mag(target_gray)
    slab_grads = np.stack([_grad_mag(_gray(slab_stack[i])) for i in range(n_cam)], axis=0)

    costs = np.empty((n_cam, h, w), dtype=np.float32)
    max_w = np.max(w_stack, axis=0)
    for i in range(n_cam):
        color = np.mean(np.abs(slab_stack[i] - target), axis=2)
        grad = np.abs(slab_grads[i] - target_grad)
        cost = cfg.color_weight * color + cfg.grad_weight * grad
        cost += cfg.weight_penalty * np.maximum(max_w - w_stack[i], 0.0)
        cost += cfg.stay_penalty * (labels != i)
        cost[w_stack[i] <= cfg.min_weight] = 1e9
        costs[i] = cost

    yy, xx = np.indices((h, w))
    current_cost = costs[labels, yy, xx]
    best_labels = np.argmin(costs, axis=0).astype(np.int16)
    best_cost = costs[best_labels, yy, xx]
    gain = current_cost - best_cost
    raw_change = interest & (best_labels != labels) & (gain > cfg.switch_margin)

    selected = np.zeros((h, w), dtype=bool)
    if raw_change.any():
        n_comp, comp_labels, stats, _centroids = cv2.connectedComponentsWithStats(raw_change.astype(np.uint8), 8)
        for comp_id in range(1, n_comp):
            comp = comp_labels == comp_id
            area = int(stats[comp_id, cv2.CC_STAT_AREA])
            if area < cfg.min_component_area:
                continue
            mean_gain = float(gain[comp].mean())
            if mean_gain < cfg.min_component_gain:
                continue
            selected |= comp

    max_changed = int(round(cfg.max_changed_frac * float(overlap.sum())))
    if max_changed > 0 and int(selected.sum()) > max_changed:
        vals = gain[selected]
        cutoff = np.partition(vals, vals.size - max_changed)[vals.size - max_changed]
        selected &= gain >= cutoff

    out_labels = labels.copy()
    out_labels[selected] = best_labels[selected]
    out = compose_from_labels(slabs, out_labels)

    if not return_diagnostics:
        return out

    hard_delta = np.abs(hard.astype(np.float32) - target).mean(axis=2)
    out_delta = np.abs(out.astype(np.float32) - target).mean(axis=2)
    region = interest
    core_region = core & overlap
    diag = {
        "config": cfg.__dict__,
        "overlap_pixels": int(overlap.sum()),
        "interest_pixels": int(interest.sum()),
        "core_overlap_pixels": int(core_region.sum()),
        "raw_change_pixels": int(raw_change.sum()),
        "selected_change_pixels": int(selected.sum()),
        "selected_change_frac_of_overlap": float(selected.sum() / max(1, overlap.sum())),
        "mean_gain_selected": float(gain[selected].mean()) if selected.any() else 0.0,
        "mean_gain_raw_change": float(gain[raw_change].mean()) if raw_change.any() else 0.0,
        "target_mae_hard_interest": float(hard_delta[region].mean()) if region.any() else float("nan"),
        "target_mae_oracle_interest": float(out_delta[region].mean()) if region.any() else float("nan"),
        "target_mae_hard_core": float(hard_delta[core_region].mean()) if core_region.any() else float("nan"),
        "target_mae_oracle_core": float(out_delta[core_region].mean()) if core_region.any() else float("nan"),
        "label_map": out_labels,
        "selected_mask": selected,
        "raw_change_mask": raw_change,
        "interest_mask": interest,
        "gain": gain.astype(np.float32),
    }
    return out, diag


def oracle_overlay(base: np.ndarray, selected: np.ndarray, interest: np.ndarray | None = None) -> np.ndarray:
    """Visualize selected source switches over an RGB image."""
    out = np.clip(base, 0, 255).astype(np.uint8).copy()
    if interest is not None:
        cyan = np.zeros_like(out)
        cyan[..., 1] = 210
        cyan[..., 2] = 255
        out[interest] = (0.78 * out[interest].astype(np.float32) + 0.22 * cyan[interest].astype(np.float32)).astype(np.uint8)
    mag = np.zeros_like(out)
    mag[..., 0] = 255
    mag[..., 2] = 255
    out[selected] = (0.42 * out[selected].astype(np.float32) + 0.58 * mag[selected].astype(np.float32)).astype(np.uint8)
    return out
