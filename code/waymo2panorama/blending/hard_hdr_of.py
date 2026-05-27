"""
Hard cam selection + joint global HDR + Farneback OF chain warp.

Three-stage pipeline that produces ghost-free, exposure-matched, parallax-aligned
ERP panoramas from a set of ring-camera ERP slabs + weights:

  L1 hard_select: argmax of cos² weight → each ERP pixel from exactly one cam.
                  Eliminates view-mixing ghost in overlap (e.g., doubled BMW).
  L2 HDR        : joint global lstsq for per-cam luminance gain. Closes the
                  ring loop, no chain drift. Preserves chroma. Equalizes
                  exposure across cams.
  L3 OF warp    : per-pair Farneback dense optical flow → warp cam B to align
                  with cam A. Chain from front_center. Corrects spatial
                  parallax misalignment of lane lines etc.

Order of operations: project → L2 HDR → L3 OF → L1 hard_select.
HDR before OF so flow doesn't lock onto brightness mismatches;
hard_select last as the final pick.

All three layers are basic CV: only cv2 + numpy. ~50s/anchor at 2048x4096
on a T4 GPU (mostly CPU-bound; GPU only for cv2 if compiled with CUDA).
"""
from __future__ import annotations

import cv2
import numpy as np


# Cam indices in RING_CAMS_7 ordering:
# 0=front_center, 1=front_left, 2=side_left, 3=rear_left,
# 4=rear_right, 5=side_right, 6=front_right
CCW = [0, 1, 2, 3]   # left half chain
CW  = [0, 6, 5, 4]   # right half chain

# All adjacent pairs in the 7-cam ring (including back seam between cam 3 & 4).
# Used by joint HDR lstsq to close the ring loop and prevent drift.
RING_PAIRS = [
    (0, 1), (1, 2), (2, 3),   # CCW: front_center → left half
    (0, 6), (6, 5), (5, 4),   # CW: front_center → right half
    (3, 4),                   # back seam — closes the loop
]


# ---------------------------------------------------------------------------
# L2: Joint global HDR (luminance-only)
# ---------------------------------------------------------------------------

def compute_hdr_gains(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    centered: bool = True,
) -> list[float]:
    """Joint global least-squares solve for per-cam luminance gain.

    For each adjacent pair (i, j), enforce in log space:
      log(g_i) + log(mean_Y_i) = log(g_j) + log(mean_Y_j)
    i.e. matched brightness in overlap zone. Anchor: log(g_0) = 0.
    Solve over all 7 ring pairs (incl back seam) via lstsq — no chain drift.

    `centered`: if True (default), shift log-gains so their mean = 0 (geometric
    mean of gains = 1.0). This avoids over-amplification when the natural
    anchor (front_center) happens to be in shadow — without centering, all
    other cams would get gain > 1 and risk clipping. With centering, gains
    spread symmetrically around 1.0 regardless of anchor brightness.

    Returns 7 scalar gains.
    """
    n = len(slabs)
    A_rows: list[np.ndarray] = []
    b_rows: list[float] = []
    for (i, j) in RING_PAIRS:
        overlap = (weights[i] > 1e-6) & (weights[j] > 1e-6)
        if int(overlap.sum()) < 100:
            continue
        y_i = cv2.cvtColor(slabs[i].astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]
        y_j = cv2.cvtColor(slabs[j].astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]
        m_i = max(float(y_i[overlap].mean()), 1.0)
        m_j = max(float(y_j[overlap].mean()), 1.0)
        row = np.zeros(n)
        row[i] = 1.0; row[j] = -1.0
        A_rows.append(row)
        b_rows.append(np.log(m_j) - np.log(m_i))
    anchor = np.zeros(n); anchor[0] = 1.0
    A_rows.append(anchor); b_rows.append(0.0)

    A = np.array(A_rows); b = np.array(b_rows)
    log_g, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    if centered:
        log_g = log_g - log_g.mean()
    gains = np.exp(log_g)
    gains = np.clip(gains, 0.5, 2.0)
    return list(gains)


def apply_hdr(slabs: list[np.ndarray], gains: list[float]) -> list[np.ndarray]:
    """Multiply Y channel by gain (preserves chroma), convert back to RGB."""
    out = []
    for s, g in zip(slabs, gains):
        ycrcb = cv2.cvtColor(s.astype(np.uint8), cv2.COLOR_RGB2YCrCb).astype(np.float32)
        ycrcb[..., 0] = np.clip(ycrcb[..., 0] * g, 0, 255)
        rgb = cv2.cvtColor(ycrcb.astype(np.uint8), cv2.COLOR_YCrCb2RGB)
        out.append(rgb.astype(np.float32))
    return out


# ---------------------------------------------------------------------------
# L3: Farneback OF chain warp
# ---------------------------------------------------------------------------

def warp_pair_with_of(
    slab_a: np.ndarray, weight_a: np.ndarray,
    slab_b: np.ndarray, weight_b: np.ndarray,
    of_winsize: int = 31, of_levels: int = 4, of_iter: int = 5,
    smooth_sigma: float = 5.0, overlap_dilate_px: int = 20,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute Farneback OF in overlap of (a, b), warp slab_b + weight_b to align with a.

    The flow encodes per-pixel parallax displacement between the two cams'
    ERP projections (using the cams themselves as ground truth — neither LiDAR
    nor monocular depth required). Flow is masked + smoothed to the overlap
    zone so non-overlap regions of slab_b stay put.
    """
    H, W = slab_a.shape[:2]
    overlap = (weight_a > 1e-6) & (weight_b > 1e-6)
    if int(overlap.sum()) < 100:
        return slab_b.copy(), weight_b.copy()

    ga = cv2.cvtColor(slab_a.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor(slab_b.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    ga_m = np.where(overlap, ga, 0).astype(np.uint8)
    gb_m = np.where(overlap, gb, 0).astype(np.uint8)

    flow = cv2.calcOpticalFlowFarneback(
        gb_m, ga_m, flow=None,
        pyr_scale=0.5, levels=of_levels, winsize=of_winsize,
        iterations=of_iter, poly_n=7, poly_sigma=1.5, flags=0,
    )

    overlap_f = overlap.astype(np.float32)
    flow_x = flow[..., 0] * overlap_f
    flow_y = flow[..., 1] * overlap_f
    if smooth_sigma > 0:
        flow_x = cv2.GaussianBlur(flow_x, (0, 0), smooth_sigma)
        flow_y = cv2.GaussianBlur(flow_y, (0, 0), smooth_sigma)
        m_smooth = cv2.GaussianBlur(overlap_f, (0, 0), smooth_sigma)
        m_smooth = np.where(m_smooth > 1e-3, m_smooth, 1.0)
        flow_x = flow_x / m_smooth
        flow_y = flow_y / m_smooth

    if overlap_dilate_px > 0:
        k = np.ones((overlap_dilate_px*2+1, overlap_dilate_px*2+1), np.uint8)
        overlap_d = cv2.dilate(overlap.astype(np.uint8), k).astype(bool)
    else:
        overlap_d = overlap
    flow_x = np.where(overlap_d, flow_x, 0)
    flow_y = np.where(overlap_d, flow_y, 0)

    u, v = np.meshgrid(np.arange(W), np.arange(H))
    map_u = (u + flow_x).astype(np.float32)
    map_v = (v + flow_y).astype(np.float32)
    sw = cv2.remap(slab_b, map_u, map_v, cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0))
    ww = cv2.remap(weight_b, map_u, map_v, cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return sw, ww


def of_chain_warp(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    close_back_seam: bool = True,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Chain Farneback OF warps two ways from front_center anchor.

    CCW: front_center → front_left → side_left → rear_left
    CW : front_center → front_right → side_right → rear_right
    Back seam (rear_left vs rear_right): if close_back_seam=True (default),
    one more OF warp aligns rear_right (CW-warped) to rear_left (CCW-warped)
    in their back-seam overlap. This closes the OF loop and prevents residual
    drift at the back of the panorama.
    """
    n = len(slabs)
    warped_s: list[np.ndarray] = [None] * n  # type: ignore
    warped_w: list[np.ndarray] = [None] * n  # type: ignore
    warped_s[0] = slabs[0]; warped_w[0] = weights[0]
    for chain in [CCW, CW]:
        for i in range(1, len(chain)):
            prev = chain[i-1]; cur = chain[i]
            sw, ww = warp_pair_with_of(
                warped_s[prev], warped_w[prev], slabs[cur], weights[cur]
            )
            warped_s[cur] = sw; warped_w[cur] = ww
    if close_back_seam:
        sw, ww = warp_pair_with_of(
            warped_s[3], warped_w[3], warped_s[4], warped_w[4]
        )
        warped_s[4] = sw; warped_w[4] = ww
    return warped_s, warped_w


# ---------------------------------------------------------------------------
# L1: hard select (argmax of weights)
# ---------------------------------------------------------------------------

def hard_select(
    slabs: list[np.ndarray], weights: list[np.ndarray],
) -> np.ndarray:
    """Pick the cam with the highest cos² weight at each ERP pixel.

    Returns uint8 ERP image. Invalid pixels (no cam covers them) → black.
    """
    w_stack = np.stack(weights, axis=0)
    rgb_stack = np.stack(slabs, axis=0).astype(np.float32)
    argmax = w_stack.argmax(axis=0)
    valid = w_stack.max(axis=0) > 0
    idx = argmax[None, ..., None]
    picked = np.take_along_axis(rgb_stack, idx, axis=0)[0]
    return np.where(valid[..., None], picked, 0).astype(np.uint8)


# ---------------------------------------------------------------------------
# Combined pipeline
# ---------------------------------------------------------------------------

def blend_hard_hdr_of(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    apply_of: bool = True,
) -> np.ndarray:
    """Full L2→L3→L1 pipeline. Returns uint8 ERP image.

    apply_of=False skips L3 OF chain warp (just HDR + hard_select; ~20s/anchor
    vs ~50s with OF).
    """
    # L2 HDR first
    gains = compute_hdr_gains(slabs, weights)
    slabs_hdr = apply_hdr(slabs, gains)
    # L3 OF chain warp on HDR-corrected slabs
    if apply_of:
        slabs_warp, weights_warp = of_chain_warp(slabs_hdr, weights)
    else:
        slabs_warp, weights_warp = slabs_hdr, weights
    # L1 hard select
    return hard_select(slabs_warp, weights_warp)
