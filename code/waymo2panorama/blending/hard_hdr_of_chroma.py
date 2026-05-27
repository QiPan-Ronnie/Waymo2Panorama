"""
Chroma-aware extension of the hard_hdr_of pipeline.

Adds an L2b CHROMA correction stage that runs AFTER the L2 luminance HDR and
BEFORE the L3 OF chain warp. The luminance HDR (hard_hdr_of.compute_hdr_gains)
is reused as-is — it already equalizes Y across cams. The new stage solves for
an additive offset (delta_Cr, delta_Cb) per camera in YCrCb space so that
overlap-zone chroma matches across adjacent cams.

Why "additive offset" and not "gain"?
  Cr/Cb are SIGNED chroma channels centered at 128 in 8-bit YCrCb. A
  multiplicative gain on (Cr - 128) is equivalent to an additive offset
  after recentering when the dynamic range is small, but the additive form
  is more interpretable as a white-balance bias correction and is exactly
  the model used in OpenCV's stitching::detail::GainCompensator beta-term
  (intensities are pulled toward each other via b += beta*N).

Why "Tikhonov-regularized + outlier-rejected joint LS"?
  Naive per-channel chain HDR was tried previously and FAILED catastrophically
  on RGB: rear cams accumulated to green=1.33x -> magenta cast (see commit
  history). The danger case for chroma is REAL scene differences in overlap:
  if cam A sees a green tree and cam B sees a gray wall in their overlap
  wedge, blind equalization would shift colors INCORRECTLY.

  Mitigations (all three combined, belt-and-suspenders):
    1. Ring-closure global lstsq (no chain drift). Same trick as L2.
    2. Tikhonov L2 prior on offsets (lambda > 0 pulls each delta toward 0).
       Inspired by OpenCV's GainCompensator which uses beta=100 to pull
       per-image gains toward 1.0. We use lambda = 0.5 (moderate damping
       — strong enough to suppress runaway solutions on degenerate ring
       constraints, weak enough to leave a meaningful correction).
    3. Similarity-gated pair inclusion: if the abs chroma diff in a pair's
       overlap exceeds a threshold (35 in 0-255), drop that pair from the
       LS. This is the same idea as OpenCV's similarity_threshold_ in
       GainCompensator. Large-diff pairs are flagged as "probable real
       scene chroma difference, not a WB problem".

  Plus a final hard clamp on the solved offsets to [-20, +20] so even if
  the solver returns nonsense the output stays viewable.

Pipeline order (mirrors hard_hdr_of with one extra step):
    project -> L2 lum HDR -> L2b chroma offsets -> L3 OF warp -> L1 hard_select

References:
  OpenCV cv2.detail.GainCompensator (modules/stitching/src/exposure_compensate.cpp):
    A(i,i) += beta * N(i,j) + 2*alpha*I(i,j)^2*N(i,j)
    A(i,j) -= 2*alpha*I(i,j)*I(j,i)*N(i,j)
    b(i)   += beta * N(i,j)
  Hardcoded alpha=0.01, beta=100. ChannelsCompensator runs three independent
  GainCompensator instances (one per channel).
"""
from __future__ import annotations

import cv2
import numpy as np

from waymo2panorama.blending.hard_hdr_of import (
    RING_PAIRS,
    apply_hdr,
    compute_hdr_gains,
    hard_select,
    of_chain_warp,
)


# ---------------------------------------------------------------------------
# Tunables (intentionally exposed at module level for ablation runs)
# ---------------------------------------------------------------------------

# L2 regularization weight on per-cam chroma offset (Tikhonov prior toward 0).
# Larger -> more conservative (offsets pulled harder toward 0). 0.5 is moderate.
CHROMA_TIKHONOV_LAMBDA: float = 0.5

# Skip a pair from the LS if |overlap chroma diff| (mean abs delta) exceeds
# this threshold (in 0-255 scale). Large diff -> probably real scene chroma
# difference, not a WB problem; trusting it would shift colors incorrectly.
# 35 ~ "more than ~14% of full chroma range" which is well outside WB drift.
CHROMA_OUTLIER_THRESHOLD: float = 35.0

# Final hard safety clip on per-cam Cr/Cb offsets, in 0-255 scale. Even if
# the solver goes crazy or the regularization is wrong, output stays viewable.
CHROMA_OFFSET_CLIP: float = 20.0

# Minimum overlap pixels required to use a pair in the LS.
MIN_OVERLAP_PIXELS: int = 100


# ---------------------------------------------------------------------------
# L2b: chroma offsets via Tikhonov-regularized joint LS with outlier rejection
# ---------------------------------------------------------------------------

def _chroma_pair_means(
    slab_i_ycrcb: np.ndarray,
    slab_j_ycrcb: np.ndarray,
    overlap: np.ndarray,
) -> tuple[float, float, float, float]:
    """Return (mean_Cr_i, mean_Cr_j, mean_Cb_i, mean_Cb_j) in overlap zone."""
    cr_i = float(slab_i_ycrcb[..., 1][overlap].mean())
    cr_j = float(slab_j_ycrcb[..., 1][overlap].mean())
    cb_i = float(slab_i_ycrcb[..., 2][overlap].mean())
    cb_j = float(slab_j_ycrcb[..., 2][overlap].mean())
    return cr_i, cr_j, cb_i, cb_j


def _solve_one_channel(
    pair_diffs: list[tuple[int, int, float]],
    n: int,
    lam: float,
) -> np.ndarray:
    """Solve Tikhonov-regularized joint LS for per-cam additive offset.

    For each (i, j, target_diff) constraint:
        delta_i - delta_j = target_diff  (target = mean_j - mean_i)
    Anchor: delta_0 = 0 (hard constraint, weighted heavily).
    Regularizer: lam * I (pulls every delta toward 0).

    Returns length-n vector of solved offsets.
    """
    A_rows: list[np.ndarray] = []
    b_rows: list[float] = []
    # Pair constraints (matched chroma in overlap).
    for (i, j, target) in pair_diffs:
        row = np.zeros(n)
        row[i] = 1.0
        row[j] = -1.0
        A_rows.append(row)
        b_rows.append(target)
    # Hard anchor on cam 0 (delta_0 = 0). Heavy weight so it dominates noise
    # in the otherwise underdetermined direction.
    anchor = np.zeros(n)
    anchor[0] = 1.0
    A_rows.append(anchor)
    b_rows.append(0.0)
    # Tikhonov: add lam*I rows (sqrt(lam) in least-squares form).
    if lam > 0:
        sqrt_lam = float(np.sqrt(lam))
        for k in range(n):
            row = np.zeros(n)
            row[k] = sqrt_lam
            A_rows.append(row)
            b_rows.append(0.0)
    A = np.asarray(A_rows, dtype=np.float64)
    b = np.asarray(b_rows, dtype=np.float64)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    return sol


def compute_chroma_offsets(
    slabs: list[np.ndarray],
    weights: list[np.ndarray],
    lam: float = CHROMA_TIKHONOV_LAMBDA,
    outlier_thresh: float = CHROMA_OUTLIER_THRESHOLD,
    clip: float = CHROMA_OFFSET_CLIP,
) -> tuple[list[float], list[float]]:
    """Solve for per-cam additive Cr / Cb offsets via Tikhonov-regularized
    joint least-squares with similarity-threshold outlier rejection.

    For each adjacent (i, j) pair in RING_PAIRS, the target diff is
    `mean_chroma_j - mean_chroma_i` in the overlap zone. A pair is INCLUDED
    only if both its Cr-diff AND Cb-diff are below `outlier_thresh` (large
    diff -> probable real scene chroma difference, not WB). Cam 0
    (front_center) is anchored at 0. The LS is regularized by lam * I to
    suppress runaway corrections on degenerate constraints. Final offsets
    are clipped to [-clip, +clip] for safety.

    Args:
        slabs:          list of (H, W, 3) float32 RGB in [0, 255]. Should
                        be the OUTPUT of `apply_hdr` (luminance-corrected).
        weights:        list of (H, W) float32. Overlap = (w_i > 1e-6) &
                        (w_j > 1e-6) for each pair.
        lam:            Tikhonov regularization weight. 0 = unregularized
                        (NOT recommended). Default 0.5.
        outlier_thresh: pair-skip threshold in 0-255 scale. Default 35.
        clip:           final per-cam offset clip in 0-255 scale. Default 20.

    Returns:
        (cr_offsets, cb_offsets): two length-n lists of additive offsets
        (typically in [-20, +20]). Apply via `apply_chroma`.
    """
    n = len(slabs)
    if n == 0:
        return [], []

    # Pre-convert all slabs to YCrCb once (uint8 cv2.cvtColor expects uint8).
    ycrcb_all = [
        cv2.cvtColor(np.clip(s, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)
        for s in slabs
    ]

    cr_constraints: list[tuple[int, int, float]] = []
    cb_constraints: list[tuple[int, int, float]] = []
    pair_diag: list[dict] = []

    for (i, j) in RING_PAIRS:
        overlap = (weights[i] > 1e-6) & (weights[j] > 1e-6)
        n_ovl = int(overlap.sum())
        if n_ovl < MIN_OVERLAP_PIXELS:
            pair_diag.append({"pair": (i, j), "n_overlap": n_ovl, "skip": "too_small"})
            continue
        cr_i, cr_j, cb_i, cb_j = _chroma_pair_means(
            ycrcb_all[i], ycrcb_all[j], overlap,
        )
        cr_diff = cr_j - cr_i
        cb_diff = cb_j - cb_i
        # Outlier rejection: skip pair if EITHER channel diff is too large
        # (suggesting real scene chroma difference, not WB drift).
        if abs(cr_diff) > outlier_thresh or abs(cb_diff) > outlier_thresh:
            pair_diag.append({
                "pair": (i, j), "n_overlap": n_ovl,
                "skip": "outlier", "cr_diff": cr_diff, "cb_diff": cb_diff,
            })
            continue
        cr_constraints.append((i, j, cr_diff))
        cb_constraints.append((i, j, cb_diff))
        pair_diag.append({
            "pair": (i, j), "n_overlap": n_ovl,
            "cr_diff": cr_diff, "cb_diff": cb_diff,
        })

    # Solve each channel independently (chroma channels decouple in this model).
    cr_sol = _solve_one_channel(cr_constraints, n, lam)
    cb_sol = _solve_one_channel(cb_constraints, n, lam)

    # Hard safety clip.
    cr_sol = np.clip(cr_sol, -clip, clip)
    cb_sol = np.clip(cb_sol, -clip, clip)

    return list(cr_sol), list(cb_sol)


def apply_chroma(
    slabs: list[np.ndarray],
    cr_offsets: list[float],
    cb_offsets: list[float],
) -> list[np.ndarray]:
    """Apply additive Cr / Cb offsets per cam in YCrCb space, return RGB.

    Y channel is untouched (luminance HDR has already corrected it). Cr and
    Cb are shifted by their solved per-cam offset, clipped to [0, 255], and
    the result converted back to RGB float32 in [0, 255].
    """
    assert len(slabs) == len(cr_offsets) == len(cb_offsets), \
        "slabs / cr_offsets / cb_offsets length mismatch"
    out: list[np.ndarray] = []
    for s, dcr, dcb in zip(slabs, cr_offsets, cb_offsets):
        ycrcb = cv2.cvtColor(
            np.clip(s, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb,
        ).astype(np.float32)
        ycrcb[..., 1] = np.clip(ycrcb[..., 1] + float(dcr), 0, 255)
        ycrcb[..., 2] = np.clip(ycrcb[..., 2] + float(dcb), 0, 255)
        rgb = cv2.cvtColor(ycrcb.astype(np.uint8), cv2.COLOR_YCrCb2RGB)
        out.append(rgb.astype(np.float32))
    return out


# ---------------------------------------------------------------------------
# Combined pipeline (mirrors blend_hard_hdr_of with chroma stage inserted)
# ---------------------------------------------------------------------------

def blend_hard_hdr_of_chroma(
    slabs: list[np.ndarray],
    weights: list[np.ndarray],
    apply_of: bool = True,
    chroma_lambda: float = CHROMA_TIKHONOV_LAMBDA,
    chroma_outlier_thresh: float = CHROMA_OUTLIER_THRESHOLD,
    chroma_clip: float = CHROMA_OFFSET_CLIP,
    return_offsets: bool = False,
):
    """Full L2(Y) + L2b(chroma) + L3(OF) + L1(hard_select) pipeline.

    Order rationale:
        L2 Y HDR first         -> equalize brightness without touching chroma
        L2b chroma offsets     -> equalize Cr/Cb on Y-corrected slabs so the
                                  chroma stats are not biased by gain
        L3 OF chain warp       -> on color-balanced slabs so flow doesn't lock
                                  onto color mismatches as features
        L1 hard_select         -> final per-pixel argmax pick

    Args:
        slabs:                  list of (H, W, 3) float32 RGB in [0, 255]
        weights:                list of (H, W) float32 (cos^2 weights)
        apply_of:               if False, skip the L3 OF warp (faster, slightly
                                worse parallax alignment)
        chroma_lambda:          Tikhonov regularization for chroma LS
        chroma_outlier_thresh:  per-pair skip threshold (0-255 scale)
        chroma_clip:            final hard clip on per-cam offsets (0-255 scale)
        return_offsets:         if True, also return a diagnostics dict with
                                {"gains": [...], "cr_offsets": [...],
                                 "cb_offsets": [...]} — useful for the
                                 test_chroma_correction.py compare script.

    Returns:
        uint8 ERP image (H, W, 3), or (erp, diagnostics) if return_offsets.
    """
    # L2: luminance HDR (reuse existing solver, untouched).
    gains = compute_hdr_gains(slabs, weights)
    slabs_y = apply_hdr(slabs, gains)
    # L2b: chroma offsets on Y-corrected slabs.
    cr_off, cb_off = compute_chroma_offsets(
        slabs_y, weights,
        lam=chroma_lambda,
        outlier_thresh=chroma_outlier_thresh,
        clip=chroma_clip,
    )
    slabs_chroma = apply_chroma(slabs_y, cr_off, cb_off)
    # L3: OF chain warp on color-balanced slabs.
    if apply_of:
        slabs_warp, weights_warp = of_chain_warp(slabs_chroma, weights)
    else:
        slabs_warp, weights_warp = slabs_chroma, weights
    # L1: hard select.
    erp = hard_select(slabs_warp, weights_warp)
    if return_offsets:
        return erp, {
            "gains": list(gains),
            "cr_offsets": list(cr_off),
            "cb_offsets": list(cb_off),
        }
    return erp
