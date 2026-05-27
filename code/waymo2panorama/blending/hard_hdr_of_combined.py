"""
Combined production-grade pipeline: L2 Y-HDR + L2b chroma + L3 OF + L1' graphcut.

Composes Improvement A (`hard_hdr_of_chroma.py`) with Improvement B
(`hard_hdr_of_graphcut.py`) on top of the shipped L1+L2+L3 baseline
(`hard_hdr_of.py`). All three building blocks are imported as-is — this
module is *pure composition*, no logic duplication.

Pipeline order (rationale unchanged from the constituent variants):
    project
      -> L2  luminance HDR        (compute_hdr_gains + apply_hdr)
      -> L2b chroma offsets       (compute_chroma_offsets + apply_chroma)
      -> L3  Farneback OF warp    (of_chain_warp)
      -> L1' graph-cut hard select (hard_select_with_graphcut)

Why this should be the production variant:
    - Chroma offset closes the per-cam white-balance gap that pure Y-HDR
      cannot touch (luminance gain preserves chroma). Without it, even
      after L2 the rear cams can keep a slight magenta/green cast.
    - Graph-cut routes seams around objects (BMW bumper, lane lines,
      columns) instead of cutting through them, which the cos² argmax
      pick would do by Voronoi geometry. Importantly, graph-cut consumes
      the *already* chroma-corrected slabs, so its color-disagreement
      cost reflects real geometric mismatch rather than WB drift — i.e.
      the two improvements compound rather than interfere.
    - L3 OF still runs on color-balanced slabs (same as the chroma-only
      variant) so flow does not lock onto chroma mismatches as features.

API mirrors `blend_hard_hdr_of_chroma` plus the `cost_type` hook from
`blend_hard_hdr_of_graphcut`. Returns the same uint8 ERP (with optional
diagnostics dict when `return_offsets=True`).
"""
from __future__ import annotations

import numpy as np

from waymo2panorama.blending.hard_hdr_of import (
    apply_hdr,
    compute_hdr_gains,
    of_chain_warp,
)
from waymo2panorama.blending.hard_hdr_of_chroma import (
    CHROMA_OFFSET_CLIP,
    CHROMA_OUTLIER_THRESHOLD,
    CHROMA_TIKHONOV_LAMBDA,
    apply_chroma,
    compute_chroma_offsets,
)
from waymo2panorama.blending.hard_hdr_of_graphcut import (
    hard_select_with_graphcut,
)


def blend_hard_hdr_of_combined(
    slabs: list[np.ndarray],
    weights: list[np.ndarray],
    apply_of: bool = True,
    chroma_lambda: float = CHROMA_TIKHONOV_LAMBDA,
    chroma_outlier_thresh: float = CHROMA_OUTLIER_THRESHOLD,
    chroma_clip: float = CHROMA_OFFSET_CLIP,
    cost_type: str = "COST_COLOR_GRAD",
    return_offsets: bool = False,
):
    """L2 Y-HDR + L2b chroma + L3 OF + L1' graph-cut hard select.

    Args:
        slabs:                  list of N (H, W, 3) float32 RGB in [0, 255]
        weights:                list of N (H, W) float32 cos² weights
        apply_of:               if False, skip the L3 OF warp (faster, slightly
                                worse parallax alignment)
        chroma_lambda:          Tikhonov regularization for chroma LS
        chroma_outlier_thresh:  per-pair skip threshold (0-255 scale)
        chroma_clip:            final hard clip on per-cam offsets (0-255 scale)
        cost_type:              cost mode for cv2.detail.GraphCutSeamFinder
                                ("COST_COLOR" or "COST_COLOR_GRAD")
        return_offsets:         if True, also return a diagnostics dict with
                                {"gains": [...], "cr_offsets": [...],
                                 "cb_offsets": [...]} — useful for A/B compare
                                 scripts

    Returns:
        uint8 (H, W, 3) ERP image, or (erp, diagnostics) if return_offsets.
    """
    # L2: luminance HDR (untouched solver).
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
    # L1': graph-cut hard select replaces cos² argmax pick.
    erp = hard_select_with_graphcut(
        slabs_warp, weights_warp, cost_type=cost_type,
    )
    if return_offsets:
        return erp, {
            "gains": list(gains),
            "cr_offsets": list(cr_off),
            "cb_offsets": list(cb_off),
        }
    return erp
