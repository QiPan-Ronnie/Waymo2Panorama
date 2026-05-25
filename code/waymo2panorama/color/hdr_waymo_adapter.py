"""
Waymo-specific HDR gain+bias compensation adapter.

Wraps the dataset-agnostic building blocks in
`code/waymo2panorama/color/hdr_gain_estimate.py` for Waymo's 5-cam
front-facing rig.

Gauge difference vs AV2
-----------------------
AV2 uses a 7-cam *ring* (front_center ... front_right) that closes on itself:
front_right has an overlap with front_center.  Pinning cam_0 alone provides
enough constraints because the ring closure equation anchors both ends of the
arc.

Waymo uses 5 *front-facing* cams (no rear cam).  The overlap graph is an
open arc — there is no closing edge.  If only cam_0 is pinned to identity, the
LS system has a gauge ambiguity: the free parameters of cam_4 (the far end of
the arc) can drift uniformly (e.g., all gains scale together) without
increasing the residual, because no constraint links cam_4 back to cam_0.

Fix: pin BOTH cam_0 AND cam_{num_cams-1} to identity.  This adds 6 hard
constraints that anchor both ends of the open arc, removing the ambiguity.

Implementation
--------------
`_waymo_global_color_correction()` is a focused fork of
`global_color_correction()` from `hdr_gain_estimate.py`.  The only
substantive change is in `unpack()`: cam_{num_cams-1} also returns IDENTITY_6.
Everything else (Huber LS, bounds, prior, interface) is identical.

`extract_overlap_pixels()` and `overlap_diagnostics()` are reused verbatim
from `hdr_gain_estimate.py` — no reimplementation.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

import numpy as np
from scipy.optimize import least_squares

from waymo2panorama.color.hdr_gain_estimate import (
    IDENTITY_6,
    _apply_xc,
    extract_overlap_pixels,
    overlap_diagnostics,
)


# ---------------------------------------------------------------------------
# Internal: Waymo-specific LS solver (two pinned cams)
# ---------------------------------------------------------------------------


def _waymo_global_color_correction(
    overlaps: Dict[Tuple[int, int], Tuple[np.ndarray, np.ndarray]],
    num_cams: int = 5,
    huber_f_scale: float = 8.0,
    gain_bounds: tuple[float, float] = (0.35, 3.0),
    bias_bounds: tuple[float, float] = (-60.0, 60.0),
    prior_weight: float = 2.0,
    max_nfev: int = 5000,
    verbose: bool = False,
) -> np.ndarray:
    """Solve for per-cam corrections with cam_0 AND cam_{num_cams-1} pinned.

    This is a fork of ``global_color_correction()`` from hdr_gain_estimate.py.
    The only algorithmic change is in ``unpack()``: both the first and last cam
    return IDENTITY_6, anchoring the open arc at both ends.

    Returns
    -------
    corrections : (num_cams, 6) float32 array.
        corrections[0] and corrections[num_cams-1] are identity; inner cams
        are solved by LS.  Row layout: [gain_r, gain_g, gain_b, bias_r,
        bias_g, bias_b].
    """
    last = num_cams - 1
    # n_free: all cams except cam_0 and cam_last (both pinned).
    n_free = num_cams - 2

    if not overlaps or n_free <= 0:
        out = np.tile(IDENTITY_6, (num_cams, 1))
        return out.astype(np.float32)

    pair_index: list[tuple[int, int, np.ndarray, np.ndarray]] = []
    for (i, j), (rgb_i, rgb_j) in overlaps.items():
        pair_index.append((i, j, rgb_i, rgb_j))

    bias_prior_scale = float(prior_weight)
    gain_prior_scale = float(prior_weight) * 25.0

    def unpack(x: np.ndarray, cam: int) -> np.ndarray:
        if cam == 0 or cam == last:
            return IDENTITY_6
        # Cams 1..last-1 map directly to slots in x: cam c is at x[(c-1)*6:(c)*6].
        return x[(cam - 1) * 6 : cam * 6]

    def residuals(x_flat: np.ndarray) -> np.ndarray:
        chunks: list[np.ndarray] = []
        for i, j, rgb_i, rgb_j in pair_index:
            ci = _apply_xc(unpack(x_flat, i), rgb_i)
            cj = _apply_xc(unpack(x_flat, j), rgb_j)
            chunks.append((ci - cj).ravel())
        # Identity prior on free cams only.
        x_view = x_flat.reshape(n_free, 6)
        gain_dev = (x_view[:, :3] - 1.0).ravel() * gain_prior_scale
        bias_dev = x_view[:, 3:6].ravel() * bias_prior_scale
        chunks.append(gain_dev)
        chunks.append(bias_dev)
        return np.concatenate(chunks)

    x0 = np.tile(IDENTITY_6, (n_free, 1)).ravel().astype(np.float64)
    lb = np.tile(
        [gain_bounds[0]] * 3 + [bias_bounds[0]] * 3, n_free
    ).astype(np.float64)
    ub = np.tile(
        [gain_bounds[1]] * 3 + [bias_bounds[1]] * 3, n_free
    ).astype(np.float64)

    res = least_squares(
        residuals,
        x0,
        bounds=(lb, ub),
        loss="huber",
        f_scale=float(huber_f_scale),
        max_nfev=max_nfev,
    )
    if verbose:
        print(
            f"[hdr-waymo-LS] status={res.status} nfev={res.nfev} "
            f"cost={res.cost:.3f} ({len(pair_index)} pairs, pinned=[0,{last}], "
            f"bounds g={gain_bounds} b={bias_bounds}, prior={prior_weight})"
        )

    corrections = np.zeros((num_cams, 6), dtype=np.float32)
    corrections[0] = IDENTITY_6
    corrections[last] = IDENTITY_6
    # Fill free cams (cams 1 .. last-1) with solved values.
    corrections[1:last] = res.x.reshape(n_free, 6).astype(np.float32)
    return corrections


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_waymo_hdr_compensation(
    slabs: Iterable[np.ndarray],
    weights: Iterable[np.ndarray],
    num_cams: int = 5,
    *,
    valid_threshold: float = 0.05,
    min_pixels: int = 50,
    max_pixels_per_pair: int = 4000,
    huber_f_scale: float = 8.0,
    gain_bounds: tuple[float, float] = (0.35, 3.0),
    bias_bounds: tuple[float, float] = (-60.0, 60.0),
    prior_weight: float = 2.0,
    max_nfev: int = 5000,
    verbose: bool = False,
    rng_seed: int = 0,
) -> tuple[np.ndarray, dict]:
    """Compute per-cam HDR gain+bias corrections for Waymo's 5-cam rig.

    This is the Waymo equivalent of the AV2 path in
    ``run_hdr_compensation.py``.  The algorithmic steps are identical; the
    only difference is that *two* cams are pinned to identity (cam_0 and
    cam_{num_cams-1}) instead of one.  This is required because Waymo's
    front-only rig has no ring closure — see module docstring.

    Args:
        slabs:               sequence of ``num_cams`` ERP slabs,
                             each (H, W, 3) float32 in [0, 255].
        weights:             sequence of ``num_cams`` feather weight maps,
                             each (H, W) float32 in [0, 1].
        num_cams:            number of cameras (default 5 for Waymo).
        valid_threshold:     passed to ``extract_overlap_pixels``.
        min_pixels:          passed to ``extract_overlap_pixels``.
        max_pixels_per_pair: passed to ``extract_overlap_pixels``.
        huber_f_scale:       Huber transition point (pixel-value units).
        gain_bounds:         (lo, hi) box constraint for gain coefficients.
        bias_bounds:         (lo, hi) box constraint for bias coefficients.
        prior_weight:        L2 identity prior weight.
        max_nfev:            max function evaluations for LS.
        verbose:             if True, print LS convergence diagnostics.
        rng_seed:            RNG seed for pixel subsampling.

    Returns:
        corrections: (num_cams, 6) float32 array.
            Row layout per cam: [gain_r, gain_g, gain_b, bias_r, bias_g, bias_b].
            corrections[0] and corrections[num_cams-1] are always identity.
        diagnostics: dict with keys:
            ``"pairs"``  — list of per-pair dicts from ``overlap_diagnostics``;
            ``"n_overlap_pairs"`` — int count of pairs with valid overlap;
            ``"overlap_gap_summary"`` — dict with before/after lum-gap stats.
    """
    overlaps = extract_overlap_pixels(
        slabs,
        weights,
        valid_threshold=valid_threshold,
        min_pixels=min_pixels,
        max_pixels_per_pair=max_pixels_per_pair,
        rng_seed=rng_seed,
    )
    corrections = _waymo_global_color_correction(
        overlaps,
        num_cams=num_cams,
        huber_f_scale=huber_f_scale,
        gain_bounds=gain_bounds,
        bias_bounds=bias_bounds,
        prior_weight=prior_weight,
        max_nfev=max_nfev,
        verbose=verbose,
    )
    diag_raw = overlap_diagnostics(overlaps, corrections)

    lum_gaps_before = [p["mean_abs_lum_gap_before"] for p in diag_raw["pairs"]]
    lum_gaps_after = [
        p["mean_abs_lum_gap_after"]
        for p in diag_raw["pairs"]
        if p["mean_abs_lum_gap_after"] is not None
    ]
    gap_before_mean = float(np.mean(lum_gaps_before)) if lum_gaps_before else 0.0
    gap_after_mean = float(np.mean(lum_gaps_after)) if lum_gaps_after else 0.0

    diagnostics: Dict[str, Any] = {
        "pairs": diag_raw["pairs"],
        "n_overlap_pairs": len(overlaps),
        "overlap_gap_summary": {
            "mean_abs_lum_gap_before": gap_before_mean,
            "mean_abs_lum_gap_after": gap_after_mean,
            "delta": gap_before_mean - gap_after_mean,
            "n_pairs": len(overlaps),
        },
    }
    return corrections, diagnostics
