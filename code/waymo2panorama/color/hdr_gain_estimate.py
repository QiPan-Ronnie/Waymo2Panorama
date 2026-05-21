"""
Per-cam color gain+bias estimation via global LS with Huber loss.

Route 14 / 新-E (Wave 1, plan v6.1).

Problem
-------
AV2's 7 ring cameras run independent auto-exposure + auto-WB controllers.
Overlap regions between adjacent cameras can show 50+ luminance delta and
non-trivial WB shifts. Multiband blending hides the discontinuity at the
seam but cannot reconcile the gross color mismatch across the panorama.

Model
-----
Per camera: 3-channel gain + 3-channel bias (6 parameters per cam).
    rgb_corrected = gain * rgb + bias        (element-wise on R, G, B)

Pin cam_0 (front_center) to identity (gain=[1,1,1], bias=[0,0,0]) for a
global gauge. Remaining 6 cams have 6 free params each -> 36 unknowns.

Estimation
----------
Render every cam to ERP (existing `sphere_projection.render_camera_to_erp`).
For each ordered pair (i, j), the pixels where both `weight_i > tau` AND
`weight_j > tau` are paired observations of the same scene point.

Solve

    min_x  Σ_(i,j)  Σ_p  ρ_huber( (g_i * c_i(p) + b_i) - (g_j * c_j(p) + b_j) )

with scipy.optimize.least_squares + Huber loss. Result: a (7, 6) array of
gain+bias coefficients consumed by `apply_correction`.

Apply BEFORE multiband blending (slab is still float32 [0, 255]).
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

import numpy as np
from scipy.optimize import least_squares

IDENTITY_6 = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float32)


# ---------------------------------------------------------------------------


def extract_overlap_pixels(
    slabs: Iterable[np.ndarray],
    weights: Iterable[np.ndarray],
    valid_threshold: float = 0.05,
    min_pixels: int = 50,
    max_pixels_per_pair: int = 4000,
    rng_seed: int = 0,
) -> Dict[Tuple[int, int], Tuple[np.ndarray, np.ndarray]]:
    """Collect overlap-pixel RGB pairs between every pair of ERP slabs.

    Args:
        slabs:           sequence of (H, W, 3) float32 ERP slabs in [0, 255].
        weights:         sequence of (H, W) float32 feather weights from
                         `render_camera_to_erp` (0 outside this cam's FOV,
                         peaks near optical axis).
        valid_threshold: a pixel is considered to "see" both cams when both
                         weights exceed this value. 0.05 selects "deep" overlap
                         pixels (clear of feather decay), which substantially
                         reduces parallax / aliasing outliers in the LS.
        min_pixels:      a pair is dropped if it has fewer than this many
                         valid overlap pixels. 50 keeps the LS well-posed.
        max_pixels_per_pair:
                         random subsample to this many pixels per pair to keep
                         the LS Jacobian tractable. Set to 0 to disable.
        rng_seed:        seed for subsampling reproducibility.

    Returns:
        dict mapping (i, j) with i < j to a tuple (rgb_i_N3, rgb_j_N3) of
        float32 arrays. Both arrays have shape (N, 3).
    """
    slabs = list(slabs)
    weights = list(weights)
    if len(slabs) != len(weights):
        raise ValueError("slabs and weights must have same length")

    rng = np.random.default_rng(rng_seed)
    overlaps: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}

    for i in range(len(slabs)):
        for j in range(i + 1, len(slabs)):
            wi = weights[i]
            wj = weights[j]
            mask = (wi > valid_threshold) & (wj > valid_threshold)
            n = int(mask.sum())
            if n < min_pixels:
                continue
            rgb_i = slabs[i][mask].astype(np.float32)
            rgb_j = slabs[j][mask].astype(np.float32)

            # RANSAC-lite per-pair outlier filter: drop pixels whose per-pixel
            # luminance delta is far from the per-pair median. These are
            # parallax / dynamic-object / depth-discontinuity outliers and
            # would otherwise drag the global LS solution toward a uniform
            # gray "compromise". 3 * median is a tight tail.
            lum_i = rgb_i.mean(axis=1)
            lum_j = rgb_j.mean(axis=1)
            diff = np.abs(lum_i - lum_j)
            med = float(np.median(diff)) + 1e-6
            keep = diff <= max(3.0 * med, 5.0)
            rgb_i = rgb_i[keep]
            rgb_j = rgb_j[keep]
            n2 = int(rgb_i.shape[0])
            if n2 < min_pixels:
                continue

            if max_pixels_per_pair and n2 > max_pixels_per_pair:
                idx = rng.choice(n2, size=max_pixels_per_pair, replace=False)
                rgb_i = rgb_i[idx]
                rgb_j = rgb_j[idx]
            overlaps[(i, j)] = (rgb_i, rgb_j)

    return overlaps


# ---------------------------------------------------------------------------


def _apply_xc(x_cam: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """rgb (N, 3) * gain + bias with x_cam = [g_r,g_g,g_b,b_r,b_g,b_b]."""
    g = x_cam[:3]
    b = x_cam[3:6]
    return rgb * g + b


def global_color_correction(
    overlaps: Dict[Tuple[int, int], Tuple[np.ndarray, np.ndarray]],
    num_cams: int = 7,
    huber_f_scale: float = 8.0,
    gain_bounds: tuple[float, float] = (0.35, 3.0),
    bias_bounds: tuple[float, float] = (-60.0, 60.0),
    prior_weight: float = 2.0,
    max_nfev: int = 5000,
    verbose: bool = False,
) -> np.ndarray:
    """Solve for {g_i, b_i for i=1..num_cams-1} with cam_0 pinned identity.

    Args:
        overlaps:      dict from `extract_overlap_pixels`.
        num_cams:      total cameras (cam_0 is the pinned anchor).
        huber_f_scale: Huber transition point in residual units (pixel-value
                       absolute residual). 8.0 means deltas <= 8 levels are
                       quadratic; outliers become linear.
        gain_bounds:   (lo, hi) box constraint on each gain coefficient.
                       Tight bounds prevent degenerate solutions (e.g., zero
                       gain + uniform bias = collapse to mean-gray) that LS
                       will otherwise find when an irreducible residual pair
                       dominates the loss.
        bias_bounds:   (lo, hi) box constraint on each bias coefficient.
        prior_weight:  weight of L2 "stay near identity" prior added as extra
                       residual rows. 5.0 means 1 pixel-value of (g-1) deviation
                       has the same cost as 5 unit pixel-residual.
        max_nfev:      max function evaluations for `least_squares`.
        verbose:       if True, print convergence diagnostics.

    Returns:
        corrections: (num_cams, 6) float32 array. corrections[0] is identity.
        Row layout per cam: [gain_r, gain_g, gain_b, bias_r, bias_g, bias_b].
    """
    n_free = num_cams - 1

    if not overlaps:
        # Nothing to solve — return identity (apply_correction will be no-op).
        out = np.tile(IDENTITY_6, (num_cams, 1))
        return out.astype(np.float32)

    # Pre-flatten observations so residual() is a tight loop.
    pair_index: list[tuple[int, int, np.ndarray, np.ndarray]] = []
    for (i, j), (rgb_i, rgb_j) in overlaps.items():
        pair_index.append((i, j, rgb_i, rgb_j))

    # Per-cam mean RGB used for the bias prior so the bias prior matches
    # the brightness scale of *this* cam (deviating bias from 0 by N pixel
    # values costs the same regardless of scene brightness).
    bias_prior_scale = float(prior_weight)
    # Gains are dimensionless ~1, but a 1 px-value residual is at ~255
    # pixel scale; "stay near 1" should cost roughly prior_weight per
    # 0.1 gain deviation, so multiply by 25 (= ~255/10) to keep ratios sane.
    gain_prior_scale = float(prior_weight) * 25.0

    def unpack(x: np.ndarray, cam: int) -> np.ndarray:
        if cam == 0:
            return IDENTITY_6
        return x[(cam - 1) * 6 : cam * 6]

    def residuals(x_flat: np.ndarray) -> np.ndarray:
        chunks: list[np.ndarray] = []
        for i, j, rgb_i, rgb_j in pair_index:
            ci = _apply_xc(unpack(x_flat, i), rgb_i)
            cj = _apply_xc(unpack(x_flat, j), rgb_j)
            chunks.append((ci - cj).ravel())
        # Identity prior (Tikhonov): pull each free cam back to identity
        x_view = x_flat.reshape(n_free, 6)
        gain_dev = (x_view[:, :3] - 1.0).ravel() * gain_prior_scale
        bias_dev = x_view[:, 3:6].ravel() * bias_prior_scale
        chunks.append(gain_dev)
        chunks.append(bias_dev)
        return np.concatenate(chunks)

    x0 = np.tile(IDENTITY_6, (n_free, 1)).ravel().astype(np.float64)
    # Box bounds matching IDENTITY_6 layout [g_r, g_g, g_b, b_r, b_g, b_b].
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
            f"[hdr-LS] status={res.status} nfev={res.nfev} "
            f"cost={res.cost:.3f} ({len(pair_index)} pairs, "
            f"bounds g={gain_bounds} b={bias_bounds}, prior={prior_weight})"
        )

    corrections = np.zeros((num_cams, 6), dtype=np.float32)
    corrections[0] = IDENTITY_6
    corrections[1:] = res.x.reshape(n_free, 6).astype(np.float32)
    return corrections


# ---------------------------------------------------------------------------


def apply_correction(slab: np.ndarray, gain_bias_6: np.ndarray) -> np.ndarray:
    """Apply per-channel gain+bias to one ERP slab (float32 in [0, 255]).

    Args:
        slab:        (H, W, 3) float32. Values outside [0, 255] are tolerated;
                     they will be clipped after correction.
        gain_bias_6: (6,) array [g_r, g_g, g_b, b_r, b_g, b_b].

    Returns:
        Corrected (H, W, 3) float32 in [0, 255].
    """
    if slab.ndim != 3 or slab.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3) slab, got {slab.shape}")
    gain_bias_6 = np.asarray(gain_bias_6, dtype=np.float32).reshape(6)
    g = gain_bias_6[:3].reshape(1, 1, 3)
    b = gain_bias_6[3:6].reshape(1, 1, 3)
    out = slab.astype(np.float32) * g + b
    return np.clip(out, 0.0, 255.0).astype(np.float32)


# ---------------------------------------------------------------------------


def overlap_diagnostics(
    overlaps: Dict[Tuple[int, int], Tuple[np.ndarray, np.ndarray]],
    corrections: np.ndarray | None = None,
) -> dict:
    """Compute before/after L1 luminance gap stats per pair (paper figures)."""
    rows = []
    for (i, j), (rgb_i, rgb_j) in overlaps.items():
        lum_i = rgb_i.mean(axis=1)
        lum_j = rgb_j.mean(axis=1)
        gap_before = float(np.abs(lum_i - lum_j).mean())
        gap_after = None
        if corrections is not None:
            ci = _apply_xc(corrections[i], rgb_i)
            cj = _apply_xc(corrections[j], rgb_j)
            gap_after = float(np.abs(ci.mean(axis=1) - cj.mean(axis=1)).mean())
        rows.append(
            {
                "pair": [int(i), int(j)],
                "n_pixels": int(rgb_i.shape[0]),
                "mean_abs_lum_gap_before": gap_before,
                "mean_abs_lum_gap_after": gap_after,
            }
        )
    return {"pairs": rows}
