"""
Multi-band blending (Burt & Adelson 1983) with horizontal-wrap support for ERP.

Why multi-band:
  - Naive feather blending blurs sharp edges across overlap regions.
  - Multi-band splits images into Laplacian-pyramid bands, blends each band with a
    Gaussian-pyramid version of the weight, and recombines.
  - High-frequency bands get sharper weights; low-frequency bands get smoother weights.
  - Net effect: sharp content at edges, smooth color transitions in overlap.

ERP wrap handling:
  - The ERP has a horizontal seam at theta = pi: pixels in the last column should
    "see" the first column. Naively running pyrDown/pyrUp ignores this and produces
    a vertical seam at the wrap.
  - Workaround: before building pyramids, circularly pad both slabs and weights on
    the horizontal axis by `pad_w` pixels. Trim back after collapsing the pyramid.
  - `pad_w` is rounded up to a multiple of 2**num_bands so downsampling stays clean.
"""
from __future__ import annotations

import cv2
import numpy as np


def _wrap_pad_h(arr: np.ndarray, pad_w: int) -> np.ndarray:
    """Horizontal circular padding: prepend last `pad_w` cols + append first `pad_w` cols."""
    if pad_w <= 0:
        return arr
    left = arr[:, -pad_w:, ...]
    right = arr[:, :pad_w, ...]
    return np.concatenate([left, arr, right], axis=1)


def _wrap_unpad_h(arr: np.ndarray, pad_w: int) -> np.ndarray:
    if pad_w <= 0:
        return arr
    return arr[:, pad_w:-pad_w, ...]


def _gaussian_pyramid(img: np.ndarray, levels: int) -> list[np.ndarray]:
    pyr = [img.astype(np.float32)]
    for _ in range(levels):
        pyr.append(cv2.pyrDown(pyr[-1]))
    return pyr


def _laplacian_pyramid(img: np.ndarray, levels: int) -> list[np.ndarray]:
    g = _gaussian_pyramid(img, levels)
    lap: list[np.ndarray] = []
    for i in range(levels):
        up = cv2.pyrUp(g[i + 1], dstsize=(g[i].shape[1], g[i].shape[0]))
        lap.append(g[i].astype(np.float32) - up.astype(np.float32))
    lap.append(g[-1].astype(np.float32))
    return lap


def multiband_blend(
    slabs: list[np.ndarray],
    weights: list[np.ndarray],
    num_bands: int = 5,
    wrap: bool = True,
) -> np.ndarray:
    """Blend N ERP slabs into one image via Burt-Adelson multi-band.

    Args:
        slabs:     list of (H, W, 3) float32 in [0, 255]
        weights:   list of (H, W)    float32 in [0, 1]  (will be re-normalized)
        num_bands: number of Laplacian bands (pyramid depth = num_bands + 1)
        wrap:      True for ERP (handle horizontal wrap-around)

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

    # Circular pad for ERP wrap (round to multiple of 2**num_bands so pyramid is clean)
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

    # Normalize weights at each pixel so they sum to 1 (avoid division by zero)
    weight_stack = np.stack(weights_p, axis=0).astype(np.float32)
    weight_sum = weight_stack.sum(axis=0, keepdims=True)
    weight_norm = weight_stack / np.maximum(weight_sum, 1e-12)

    # Build per-slab Laplacian pyramids and per-weight Gaussian pyramids
    laps = [_laplacian_pyramid(s, num_bands) for s in slabs_p]
    wpyrs = [_gaussian_pyramid(weight_norm[i], num_bands) for i in range(len(slabs_p))]

    n_levels = num_bands + 1
    blended_levels: list[np.ndarray] = []
    for lvl in range(n_levels):
        acc = np.zeros_like(laps[0][lvl], dtype=np.float32)
        wsum = np.zeros(laps[0][lvl].shape[:2], dtype=np.float32) + 1e-12
        for i in range(len(slabs_p)):
            w_i = wpyrs[i][lvl]
            acc += laps[i][lvl] * w_i[..., None]
            wsum += w_i
        blended_levels.append(acc / wsum[..., None])

    # Collapse the pyramid
    out = blended_levels[-1]
    for lvl in range(n_levels - 2, -1, -1):
        h_lvl, w_lvl = blended_levels[lvl].shape[:2]
        out = cv2.pyrUp(out, dstsize=(w_lvl, h_lvl))
        out = out + blended_levels[lvl]

    # Trim horizontal wrap padding
    if pad_w > 0:
        out = _wrap_unpad_h(out, pad_w)

    out = np.clip(out, 0.0, 255.0).astype(np.uint8)
    return out
