"""LPAM-inspired seam-local alignment for hard-select panoramas.

This module keeps the L1 hard-select contract: every output ERP pixel is still
copied from exactly one source camera. The only change is that a losing-side
camera slab may be locally translated inside a narrow band around the seam so
that lane lines and other local structures meet more cleanly at the cut.

It is intentionally conservative:
  - translation-only local model
  - narrow seam band only
  - per-tile rejection gates
  - identity fallback whenever confidence is low
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from waymo2panorama.blending.hard_hdr_of import (
    RING_PAIRS,
    apply_hdr,
    compute_hdr_gains,
    hard_select,
)


@dataclass
class TranslationEstimate:
    """Small local translation estimate for one seam patch."""

    dx: float
    dy: float
    accepted: bool
    reason: str
    ncc_before: float
    ncc_after: float
    method: str


def _to_gray_f32(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        gray = img
    else:
        gray = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    return gray.astype(np.float32)


def _masked_ncc(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float:
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    if mask is not None:
        m = mask.astype(bool)
        if int(m.sum()) < 16:
            return 0.0
        av = a[m]
        bv = b[m]
    else:
        av = a.reshape(-1)
        bv = b.reshape(-1)
    av = av - float(av.mean())
    bv = bv - float(bv.mean())
    denom = float(np.sqrt(np.sum(av * av) * np.sum(bv * bv)))
    if denom < 1e-6:
        return 0.0
    return float(np.clip(np.sum(av * bv) / denom, -1.0, 1.0))


def _warp_translation_gray(gray: np.ndarray, dx: float, dy: float) -> np.ndarray:
    M = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
    return cv2.warpAffine(
        gray.astype(np.float32),
        M,
        (gray.shape[1], gray.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )


def _ecc_candidate(
    ref_gray: np.ndarray,
    mov_gray: np.ndarray,
    mask: np.ndarray | None,
    max_dx: int,
    max_dy: int,
) -> tuple[float, float] | None:
    """Try OpenCV ECC translation and return (dx, dy), or None on failure."""
    ref = ref_gray.astype(np.float32)
    mov = mov_gray.astype(np.float32)
    if ref.max() > 1.5:
        ref = ref / 255.0
    if mov.max() > 1.5:
        mov = mov / 255.0
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        50,
        1e-5,
    )
    input_mask = None
    if mask is not None:
        input_mask = (mask.astype(np.uint8) * 255)
    try:
        _cc, warp = cv2.findTransformECC(
            ref,
            mov,
            warp,
            cv2.MOTION_TRANSLATION,
            criteria,
            inputMask=input_mask,
            gaussFiltSize=5,
        )
    except cv2.error:
        return None
    dx = float(warp[0, 2])
    dy = float(warp[1, 2])
    if not np.isfinite(dx) or not np.isfinite(dy):
        return None
    if abs(dx) > max_dx or abs(dy) > max_dy:
        return None
    return dx, dy


def estimate_translation_ecc(
    ref_gray: np.ndarray,
    mov_gray: np.ndarray,
    mask: np.ndarray | None = None,
    max_dx: int = 24,
    max_dy: int = 8,
    min_ncc_gain: float = 0.03,
    refine_radius: int = 3,
) -> TranslationEstimate:
    """Estimate a conservative translation that aligns `mov_gray` to `ref_gray`.

    ECC is tried first, then an integer NCC search is used as a robust fallback
    and local refinement. The returned `(dx, dy)` follows `cv2.warpAffine`
    convention: applying this translation to `mov_gray` aligns it to `ref_gray`.
    """
    if ref_gray.shape != mov_gray.shape:
        raise ValueError(f"shape mismatch: {ref_gray.shape} vs {mov_gray.shape}")
    if min(ref_gray.shape[:2]) < 8:
        return TranslationEstimate(0, 0, False, "tile_too_small", 0.0, 0.0, "none")

    ref = _to_gray_f32(ref_gray)
    mov = _to_gray_f32(mov_gray)
    mask_bool = mask.astype(bool) if mask is not None else None
    if mask_bool is not None and int(mask_bool.sum()) < max(32, ref.size // 20):
        ncc0 = _masked_ncc(ref, mov, mask_bool)
        return TranslationEstimate(0, 0, False, "mask_too_small", ncc0, ncc0, "none")

    ncc_before = _masked_ncc(ref, mov, mask_bool)

    candidates: set[tuple[int, int]] = {(0, 0)}

    def add_neighborhood(cx: float, cy: float, radius: int) -> None:
        ix = int(round(cx))
        iy = int(round(cy))
        for yy in range(iy - radius, iy + radius + 1):
            for xx in range(ix - radius, ix + radius + 1):
                if abs(xx) <= max_dx and abs(yy) <= max_dy:
                    candidates.add((xx, yy))

    ecc = _ecc_candidate(ref, mov, mask_bool, max_dx=max_dx, max_dy=max_dy)
    if ecc is not None:
        add_neighborhood(ecc[0], ecc[1], refine_radius)

    # Phase correlation is a cheap translation cue when ECC fails or lands in
    # a nearby basin. It is not trusted directly; it only seeds NCC refinement.
    try:
        ref_pc = ref.astype(np.float32)
        mov_pc = mov.astype(np.float32)
        if mask_bool is not None:
            m = mask_bool.astype(np.float32)
            ref_pc = ref_pc * m
            mov_pc = mov_pc * m
        (px, py), _response = cv2.phaseCorrelate(mov_pc, ref_pc)
        if np.isfinite(px) and np.isfinite(py) and abs(px) <= max_dx and abs(py) <= max_dy:
            add_neighborhood(px, py, refine_radius)
    except cv2.error:
        pass

    # Always allow a small identity-centered refinement for cases where both
    # estimators fail but the true offset is very small.
    add_neighborhood(0, 0, min(refine_radius, 2))

    best_dx = 0
    best_dy = 0
    best_ncc = ncc_before
    for dx, dy in candidates:
        warped = _warp_translation_gray(mov, dx, dy)
        ncc = _masked_ncc(ref, warped, mask_bool)
        if ncc > best_ncc:
            best_ncc = ncc
            best_dx = dx
            best_dy = dy

    gain = best_ncc - ncc_before
    if gain < min_ncc_gain:
        return TranslationEstimate(
            0.0, 0.0, False, "low_ncc_gain", ncc_before, best_ncc, "ecc+ncc"
        )
    return TranslationEstimate(
        float(best_dx), float(best_dy), True, "accepted", ncc_before, best_ncc, "ecc+ncc"
    )


def _overlap_wraps(overlap: np.ndarray) -> bool:
    if not overlap.any():
        return False
    cols = overlap.any(axis=0)
    idx = np.flatnonzero(cols)
    if idx.size == 0:
        return False
    return bool(cols[0] and cols[-1] and (int(idx[-1] - idx[0] + 1) > 0.6 * overlap.shape[1]))


def build_voronoi_seam_band(
    weight_a: np.ndarray,
    weight_b: np.ndarray,
    band_half_width: int = 48,
    threshold: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a narrow band around the cos-squared hard-select seam.

    Returns `(band, signed_distance)`. Distance is row-wise pixel offset from
    the equality seam; negative values are on cam-A's side, positive values are
    on cam-B's side. This is simple by design because AV ring seams are mostly
    vertical in ERP after L1 projection.
    """
    if weight_a.shape != weight_b.shape:
        raise ValueError(f"weight shape mismatch: {weight_a.shape} vs {weight_b.shape}")
    H, W = weight_a.shape
    overlap = (weight_a > threshold) & (weight_b > threshold)
    band = np.zeros((H, W), dtype=bool)
    signed = np.full((H, W), np.inf, dtype=np.float32)
    u = np.arange(W, dtype=np.float32)
    diff = weight_b - weight_a
    for v in range(H):
        cols = np.flatnonzero(overlap[v])
        if cols.size == 0:
            continue
        seam_u = int(cols[np.argmin(np.abs(diff[v, cols]))])
        dist = u - float(seam_u)
        row_band = np.abs(dist) <= float(band_half_width)
        row_band &= overlap[v]
        band[v, row_band] = True
        signed[v] = dist
    return band, signed


def _bbox(mask: np.ndarray, pad: int = 0) -> tuple[int, int, int, int] | None:
    if not mask.any():
        return None
    yy, xx = np.where(mask)
    y0 = max(0, int(yy.min()) - pad)
    y1 = min(mask.shape[0], int(yy.max()) + 1 + pad)
    x0 = max(0, int(xx.min()) - pad)
    x1 = min(mask.shape[1], int(xx.max()) + 1 + pad)
    return y0, y1, x0, x1


def _iter_tiles(
    bbox: tuple[int, int, int, int],
    tile_hw: tuple[int, int],
    stride_hw: tuple[int, int],
) -> list[tuple[int, int, int, int]]:
    y0, y1, x0, x1 = bbox
    th, tw = tile_hw
    sy, sx = stride_hw
    if y1 - y0 < th:
        ys = [y0]
        th = y1 - y0
    else:
        ys = list(range(y0, y1 - th + 1, sy))
        if ys[-1] != y1 - th:
            ys.append(y1 - th)
    if x1 - x0 < tw:
        xs = [x0]
        tw = x1 - x0
    else:
        xs = list(range(x0, x1 - tw + 1, sx))
        if xs[-1] != x1 - tw:
            xs.append(x1 - tw)
    return [(y, y + th, x, x + tw) for y in ys for x in xs]


def _warp_slab_by_displacement(slab: np.ndarray, disp: np.ndarray) -> np.ndarray:
    H, W = slab.shape[:2]
    u, v = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    # disp follows warpAffine "move content by dx/dy" convention. remap samples
    # from the inverse coordinate.
    map_u = u - disp[..., 0].astype(np.float32)
    map_v = v - disp[..., 1].astype(np.float32)
    return cv2.remap(
        slab.astype(np.float32),
        map_u,
        map_v,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )


def _align_pair_field(
    slab_a: np.ndarray,
    weight_a: np.ndarray,
    slab_b: np.ndarray,
    weight_b: np.ndarray,
    band_half_width: int,
    tile_hw: tuple[int, int],
    stride_hw: tuple[int, int],
    max_dx: int,
    max_dy: int,
    min_ncc_gain: float,
    min_tile_band_frac: float,
) -> tuple[np.ndarray, np.ndarray, dict]:
    H, W = weight_a.shape
    overlap = (weight_a > 1e-6) & (weight_b > 1e-6)
    wraps = _overlap_wraps(overlap)
    roll = W // 2 if wraps else 0

    if wraps:
        slab_a_p = np.roll(slab_a, roll, axis=1)
        slab_b_p = np.roll(slab_b, roll, axis=1)
        weight_a_p = np.roll(weight_a, roll, axis=1)
        weight_b_p = np.roll(weight_b, roll, axis=1)
    else:
        slab_a_p, slab_b_p = slab_a, slab_b
        weight_a_p, weight_b_p = weight_a, weight_b

    band, signed = build_voronoi_seam_band(
        weight_a_p, weight_b_p, band_half_width=band_half_width
    )
    box = _bbox(band, pad=2)
    disp_sum = np.zeros((H, W, 2), dtype=np.float32)
    conf_sum = np.zeros((H, W), dtype=np.float32)
    reasons: dict[str, int] = {}
    estimates: list[TranslationEstimate] = []
    total_tiles = 0
    accepted_tiles = 0

    if box is not None:
        gray_a = _to_gray_f32(slab_a_p)
        gray_b = _to_gray_f32(slab_b_p)
        for y0, y1, x0, x1 in _iter_tiles(box, tile_hw, stride_hw):
            total_tiles += 1
            m = band[y0:y1, x0:x1]
            if float(m.mean()) < min_tile_band_frac:
                reasons["low_band_fraction"] = reasons.get("low_band_fraction", 0) + 1
                continue
            est = estimate_translation_ecc(
                gray_a[y0:y1, x0:x1],
                gray_b[y0:y1, x0:x1],
                mask=m,
                max_dx=max_dx,
                max_dy=max_dy,
                min_ncc_gain=min_ncc_gain,
            )
            estimates.append(est)
            reasons[est.reason] = reasons.get(est.reason, 0) + 1
            if not est.accepted:
                continue
            accepted_tiles += 1
            local_conf = m.astype(np.float32) * max(est.ncc_after - est.ncc_before, 1e-3)
            disp_sum[y0:y1, x0:x1, 0] += local_conf * est.dx
            disp_sum[y0:y1, x0:x1, 1] += local_conf * est.dy
            conf_sum[y0:y1, x0:x1] += local_conf

    valid = conf_sum > 1e-6
    disp = np.zeros_like(disp_sum)
    disp[valid, 0] = disp_sum[valid, 0] / conf_sum[valid]
    disp[valid, 1] = disp_sum[valid, 1] / conf_sum[valid]
    if valid.any():
        disp[..., 0] = cv2.GaussianBlur(disp[..., 0], (0, 0), 7.0)
        disp[..., 1] = cv2.GaussianBlur(disp[..., 1], (0, 0), 7.0)
        conf_s = cv2.GaussianBlur(conf_sum, (0, 0), 7.0)
        conf_norm = np.clip(conf_s / max(float(conf_s.max()), 1e-6), 0.0, 1.0)
        taper = np.clip(1.0 - np.abs(signed) / float(band_half_width + 1), 0.0, 1.0)
        taper = np.where(np.isfinite(taper), taper, 0.0).astype(np.float32)
        disp *= (conf_norm * taper)[..., None]
    if wraps:
        disp = np.roll(disp, -roll, axis=1)
        conf_sum = np.roll(conf_sum, -roll, axis=1)
        band = np.roll(band, -roll, axis=1)

    accepted = [e for e in estimates if e.accepted]
    diag = {
        "total_tiles": int(total_tiles),
        "accepted_tiles": int(accepted_tiles),
        "rejected_tiles": int(total_tiles - accepted_tiles),
        "rejection_reasons": reasons,
        "median_dx": float(np.median([e.dx for e in accepted])) if accepted else 0.0,
        "median_dy": float(np.median([e.dy for e in accepted])) if accepted else 0.0,
        "mean_ncc_before": float(np.mean([e.ncc_before for e in estimates])) if estimates else 0.0,
        "mean_ncc_after": float(np.mean([e.ncc_after for e in estimates])) if estimates else 0.0,
        "band_pixels": int(band.sum()),
        "wraps": bool(wraps),
    }
    return disp, conf_sum, diag


def seam_local_align_slabs(
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    ring_pairs: Sequence[tuple[int, int]] = RING_PAIRS,
    band_half_width: int = 48,
    tile_hw: tuple[int, int] = (128, 96),
    stride_hw: tuple[int, int] = (64, 48),
    max_dx: int = 24,
    max_dy: int = 8,
    min_ncc_gain: float = 0.03,
    min_tile_band_frac: float = 0.10,
    return_diagnostics: bool = False,
) -> tuple[list[np.ndarray], list[np.ndarray], dict]:
    """Return locally aligned slabs while preserving original weight fields."""
    n = len(slabs)
    if n != len(weights):
        raise ValueError(f"slabs/weights length mismatch: {n} vs {len(weights)}")
    if n == 0:
        return [], [], {"pairs": []}
    H, W = weights[0].shape
    accum_disp = [np.zeros((H, W, 2), dtype=np.float32) for _ in range(n)]
    accum_conf = [np.zeros((H, W), dtype=np.float32) for _ in range(n)]
    pair_diags: list[dict] = []

    for i, j in ring_pairs:
        if i >= n or j >= n:
            continue
        disp, conf, diag = _align_pair_field(
            slabs[i], weights[i], slabs[j], weights[j],
            band_half_width=band_half_width,
            tile_hw=tile_hw,
            stride_hw=stride_hw,
            max_dx=max_dx,
            max_dy=max_dy,
            min_ncc_gain=min_ncc_gain,
            min_tile_band_frac=min_tile_band_frac,
        )
        accum_disp[j] += disp * conf[..., None]
        accum_conf[j] += conf
        diag["pair"] = [int(i), int(j)]
        pair_diags.append(diag)

    aligned: list[np.ndarray] = []
    for idx, slab in enumerate(slabs):
        valid = accum_conf[idx] > 1e-6
        if not valid.any():
            aligned.append(slab.astype(np.float32).copy())
            continue
        disp = np.zeros_like(accum_disp[idx])
        disp[valid, 0] = accum_disp[idx][valid, 0] / accum_conf[idx][valid]
        disp[valid, 1] = accum_disp[idx][valid, 1] / accum_conf[idx][valid]
        aligned.append(_warp_slab_by_displacement(slab, disp))

    diagnostics = {
        "pairs": pair_diags,
        "accepted_tiles": int(sum(d["accepted_tiles"] for d in pair_diags)),
        "total_tiles": int(sum(d["total_tiles"] for d in pair_diags)),
    }
    return aligned, [w.copy() for w in weights], diagnostics


def blend_seam_local_align(
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    apply_hdr_pre: bool = False,
    return_diagnostics: bool = False,
    **align_kwargs,
) -> np.ndarray | tuple[np.ndarray, dict]:
    """Blend by optional HDR, seam-local alignment, then hard_select."""
    work_slabs = [s.astype(np.float32) for s in slabs]
    work_weights = [w.astype(np.float32) for w in weights]
    gains = [1.0] * len(work_slabs)
    if apply_hdr_pre:
        gains = compute_hdr_gains(work_slabs, work_weights, centered=True)
        work_slabs = apply_hdr(work_slabs, gains)

    aligned_slabs, aligned_weights, diagnostics = seam_local_align_slabs(
        work_slabs,
        work_weights,
        return_diagnostics=True,
        **align_kwargs,
    )
    erp = hard_select(aligned_slabs, aligned_weights)
    diagnostics["hdr_gains"] = [float(g) for g in gains]
    if return_diagnostics:
        return erp, diagnostics
    return erp
