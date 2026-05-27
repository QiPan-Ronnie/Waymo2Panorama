"""
Frequency-band hybrid blend: multiband at low freq + hard select at high freq.

Motivation
----------
Both multiband and hard_select have band-dependent failure modes:

  - multiband (cos² weighted blend at every Laplacian band) BLENDS in overlap.
    For LOW-freq bands (smooth tones, sky color, road tarmac) this is correct
    — averaging "blue sky from cam A" + "blue sky from cam B" still produces
    "blue sky" and the seam is hidden. For HIGH-freq bands (sharp edges, lane
    markings, car contours), averaging two slightly mis-registered edges
    creates a DOUBLED feature: the famous "ghost BMW".

  - hard_select (argmax of cos² weight at full resolution) picks ONE cam per
    ERP pixel, so it never doubles anything. But the transition between cams
    is a sharp cut, and any cross-cam tone / colour offset that L2 HDR + L2b
    chroma didn't fully eliminate appears as a visible vertical seam, mostly
    in low-freq channels (the cut is hidden in high-freq detail but stands
    out in smooth gradients like a clear sky).

Insight: pick the right strategy per frequency band.

  - LOW-frequency bands (level >= cutoff): cos² weighted average like
    multiband. Hides residual tone steps in smooth content.
  - HIGH-frequency bands (level <  cutoff): argmax of Gaussian-pyramid
    weight at that level → pick the cam, take its Laplacian coefficient.
    Keeps a single-cam edge, no doubling.

The transition between hard / blended bands happens INSIDE the pyramid; on
collapse, the two contributions sum cleanly.

Cutoff choice
-------------
Default `hard_cutoff_level = num_bands // 2`. At num_bands=5 this means
levels 0..1 are hard-selected (the two finest, which carry edges) and levels
2..5 are blended (coarser tones, broad colour). Levels are indexed 0=full
resolution → L=coarsest.

ERP wrap handling
-----------------
Same circular-pad trick as multiband.py: pad horizontally by `pad_w` before
building pyramids, trim after collapse. `pad_w` is rounded up to a multiple
of 2**num_bands so downsampling is clean.

Known weakness to validate
--------------------------
At fine pyramid levels the per-level Gaussian-weight argmax can flip
cam-to-cam pixel-by-pixel in regions where two cams have nearly identical
weights (deep inside overlap, far from any cam's optical axis). In theory
this creates speckle. In practice:
  (1) the cos² weight already biases the picks because optical-axis
      distance differs even slightly inside overlap,
  (2) hard select at full resolution (the existing baseline) has the SAME
      property and it works fine,
  (3) any speckle is in the high-freq band only, so each flip carries a
      tiny zero-mean Laplacian coefficient — adjacent flips cancel and the
      result is visually quiet.
If point (3) breaks on real data, the fix is to raise the cutoff (pick
more bands as "low") so the hard-select region is restricted to truly fine
edges where one cam usually dominates.
"""
from __future__ import annotations

import cv2
import numpy as np

from waymo2panorama.blending.hard_hdr_of import (
    apply_hdr,
    compute_hdr_gains,
    of_chain_warp,
)
from waymo2panorama.blending.multiband import (
    _gaussian_pyramid,
    _laplacian_pyramid,
    _wrap_pad_h,
    _wrap_unpad_h,
)


def freq_band_hybrid_blend(
    slabs: list[np.ndarray],
    weights: list[np.ndarray],
    num_bands: int = 5,
    hard_cutoff_level: int | None = None,
    wrap: bool = True,
) -> np.ndarray:
    """Blend N ERP slabs via per-band hybrid (hard-select fine, weighted blend coarse).

    Args:
        slabs:              list of (H, W, 3) float32 in [0, 255]
        weights:            list of (H, W)    float32 in [0, 1]
                            (cos² ring weights; re-normalized internally)
        num_bands:          number of Laplacian bands (pyramid depth = num_bands + 1).
        hard_cutoff_level:  integer in [0, num_bands]. Levels with index
                            STRICTLY LESS THAN this value use HARD select.
                            Levels >= cutoff use cos² WEIGHTED BLEND. Default
                            `num_bands // 2`. e.g. num_bands=5, cutoff=2 →
                            levels 0,1 = hard;  levels 2,3,4,5 = blend.
        wrap:               True for ERP (circular pad on horizontal axis).

    Returns:
        (H, W, 3) uint8 blended image.
    """
    assert len(slabs) == len(weights), "slabs and weights must have same length"
    if not slabs:
        raise ValueError("no slabs provided")
    H, W = slabs[0].shape[:2]
    for s, w in zip(slabs, weights):
        if s.shape[:2] != (H, W) or w.shape != (H, W):
            raise ValueError(f"slab/weight shape mismatch: got {s.shape} / {w.shape}")

    if hard_cutoff_level is None:
        hard_cutoff_level = num_bands // 2
    if not (0 <= hard_cutoff_level <= num_bands + 1):
        raise ValueError(
            f"hard_cutoff_level must be in [0, num_bands+1]={num_bands+1}, "
            f"got {hard_cutoff_level}"
        )

    # Circular pad for ERP wrap (same recipe as multiband.py).
    if wrap:
        base = max(W // 32, 16)
        step = 1 << num_bands
        pad_w = ((base + step - 1) // step) * step
    else:
        pad_w = 0

    if pad_w > 0:
        slabs_p = [_wrap_pad_h(s, pad_w) for s in slabs]
        weights_p = [_wrap_pad_h(w, pad_w) for w in weights]
    else:
        slabs_p = list(slabs)
        weights_p = list(weights)

    # Normalize weights per-pixel so they sum to 1.
    weight_stack = np.stack(weights_p, axis=0).astype(np.float32)
    weight_sum = weight_stack.sum(axis=0, keepdims=True)
    weight_norm = weight_stack / np.maximum(weight_sum, 1e-12)

    # Per-cam Laplacian + per-cam Gaussian-of-weight pyramids.
    laps = [_laplacian_pyramid(s, num_bands) for s in slabs_p]
    wpyrs = [_gaussian_pyramid(weight_norm[i], num_bands) for i in range(len(slabs_p))]

    n_levels = num_bands + 1
    blended_levels: list[np.ndarray] = []
    for lvl in range(n_levels):
        # Stack per-cam weight at this level → (K, h_lvl, w_lvl)
        w_lvl_stack = np.stack([wpyrs[i][lvl] for i in range(len(slabs_p))], axis=0)
        # Stack per-cam Laplacian at this level → (K, h_lvl, w_lvl, 3)
        lap_lvl_stack = np.stack([laps[i][lvl] for i in range(len(slabs_p))], axis=0)

        if lvl < hard_cutoff_level:
            # HIGH-freq band: hard select. argmax of per-cam Gaussian-weight at
            # this level; pick that cam's Laplacian coefficient.
            argmax = w_lvl_stack.argmax(axis=0)  # (h_lvl, w_lvl)
            valid = w_lvl_stack.max(axis=0) > 0
            idx = argmax[None, ..., None]                # (1, h, w, 1)
            picked = np.take_along_axis(lap_lvl_stack, idx, axis=0)[0]  # (h, w, 3)
            picked = np.where(valid[..., None], picked, 0.0).astype(np.float32)
            blended_levels.append(picked)
        else:
            # LOW-freq band: cos² weighted blend (same arithmetic as multiband).
            wsum = w_lvl_stack.sum(axis=0) + 1e-12               # (h_lvl, w_lvl)
            acc = (lap_lvl_stack * w_lvl_stack[..., None]).sum(axis=0)  # (h, w, 3)
            blended_levels.append(acc / wsum[..., None])

    # Collapse the merged pyramid.
    out = blended_levels[-1]
    for lvl in range(n_levels - 2, -1, -1):
        h_lvl, w_lvl = blended_levels[lvl].shape[:2]
        out = cv2.pyrUp(out, dstsize=(w_lvl, h_lvl))
        out = out + blended_levels[lvl]

    if pad_w > 0:
        out = _wrap_unpad_h(out, pad_w)

    out = np.clip(out, 0.0, 255.0).astype(np.uint8)
    return out


def blend_hard_hdr_of_freqhybrid(
    slabs: list[np.ndarray],
    weights: list[np.ndarray],
    apply_of: bool = True,
    num_bands: int = 5,
    hard_cutoff_level: int | None = None,
) -> np.ndarray:
    """Full pipeline: L2 HDR → L3 OF → frequency-band hybrid blend.

    Replaces the L1 hard_select stage of blend_hard_hdr_of with the
    frequency-band hybrid blend. Other stages are identical and reused as-is
    from `hard_hdr_of`.

    Order rationale (unchanged from hard_hdr_of):
      L2 first  — equalize brightness before flow / blending so they don't
                  lock onto exposure mismatches.
      L3 next   — warp each cam so adjacent cams are parallax-aligned in
                  overlap; this is the ONLY chance to reduce inter-cam
                  spatial offset, which hybrid blend cannot recover from.
      hybrid    — final pixel pick: high-freq from one cam, low-freq blended.

    Args:
        slabs:              list of (H, W, 3) float32 in [0, 255]
        weights:            list of (H, W)    float32 cos² ring weights
        apply_of:           if False, skip the L3 OF chain warp
                            (faster — ~20s/anchor vs ~50s with OF)
        num_bands:          forwarded to freq_band_hybrid_blend
        hard_cutoff_level:  forwarded to freq_band_hybrid_blend

    Returns:
        (H, W, 3) uint8 ERP image.
    """
    # L2: luminance HDR (joint global lstsq).
    gains = compute_hdr_gains(slabs, weights)
    slabs_hdr = apply_hdr(slabs, gains)
    # L3: Farneback OF chain warp on HDR-corrected slabs.
    if apply_of:
        slabs_warp, weights_warp = of_chain_warp(slabs_hdr, weights)
    else:
        slabs_warp, weights_warp = slabs_hdr, weights
    # Final: frequency-band hybrid blend.
    return freq_band_hybrid_blend(
        slabs_warp, weights_warp,
        num_bands=num_bands,
        hard_cutoff_level=hard_cutoff_level,
        wrap=True,
    )
