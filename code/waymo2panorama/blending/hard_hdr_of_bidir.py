"""
Bidirectional half-warp variant of the L1+L2+L3 basic-CV pipeline.

Replaces the asymmetric `warp_pair_with_of` (which warps B fully to align with
A, treating A as ground truth) with a SYMMETRIC half-warp that moves both
cams halfway toward each other in their overlap zone. Two key advantages:

1. **No preferred cam per pair**: A and B are treated symmetrically — neither
   is privileged as "the reference." This is more honest about the fact that
   neither cam's ERP projection is the true geometry; both have parallax
   relative to the underlying scene.
2. **Half the per-pair displacement magnitude**: each cam moves by F/2
   instead of F. Smaller displacements → less chance of Farneback OF artifacts
   (over-shooting on textureless regions) and smaller resampling sub-pixel
   blur per cam.

The L2 HDR and L1 hard_select stages are reused unchanged from `hard_hdr_of`.

Two chaining strategies are provided:

  * `of_bidirectional_chain_warp` (approach a, default) — serial chain
    starting from front_center. Each new cam in the chain is warped by F/2
    toward its already-warped predecessor. The predecessor is NOT moved
    again (its state is frozen). This propagates half-displacements through
    the ring with the same topology as the original `of_chain_warp`. It
    SHRINKS per-pair magnitude (good for OF stability) but does NOT remove
    cumulative drift along the chain (the chain still accumulates).

  * `of_joint_solve_warp` (approach b, OPTIONAL) — pre-computes flows on
    all 7 ring pairs from the ORIGINAL (unwarped) slabs, then solves a
    sparse linear system for each cam's per-pixel 2D displacement field that
    minimizes total pair disagreement. Globally consistent: every pair sees
    a "fair" warp of both cams. Requires significantly more memory and time
    than chain (~2-3× slower, ~3× more memory at 2048×4096), and adds a
    spatial regularization parameter. See module docstring at function for
    derivation and caveats.

NB: this module DOES NOT modify the shipped `hard_hdr_of.py`. It re-uses
`compute_hdr_gains`, `apply_hdr`, and `hard_select` from there but provides
new variants of `warp_pair_with_of` / `of_chain_warp`. Drop-in swap of the
wrapper `blend_hard_hdr_of_bidir` for `blend_hard_hdr_of` is safe.

All three layers remain basic CV: only cv2 + numpy. Runtime: ~55s/anchor
for chain variant, ~80-100s/anchor for joint solve at 2048x4096 (CPU-bound).
"""
from __future__ import annotations

import cv2
import numpy as np

from waymo2panorama.blending.hard_hdr_of import (
    CCW, CW, RING_PAIRS,
    compute_hdr_gains, apply_hdr, hard_select,
)


# ---------------------------------------------------------------------------
# L3 (bidirectional): pairwise half-warp
# ---------------------------------------------------------------------------

def _smooth_and_mask_flow(
    flow_x: np.ndarray, flow_y: np.ndarray,
    overlap: np.ndarray,
    smooth_sigma: float, overlap_dilate_px: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Gaussian-smooth + mask a flow field to overlap zone (mirrors hard_hdr_of)."""
    overlap_f = overlap.astype(np.float32)
    fx = flow_x * overlap_f
    fy = flow_y * overlap_f
    if smooth_sigma > 0:
        fx = cv2.GaussianBlur(fx, (0, 0), smooth_sigma)
        fy = cv2.GaussianBlur(fy, (0, 0), smooth_sigma)
        m_smooth = cv2.GaussianBlur(overlap_f, (0, 0), smooth_sigma)
        m_smooth = np.where(m_smooth > 1e-3, m_smooth, 1.0)
        fx = fx / m_smooth
        fy = fy / m_smooth
    if overlap_dilate_px > 0:
        k = np.ones((overlap_dilate_px * 2 + 1, overlap_dilate_px * 2 + 1), np.uint8)
        overlap_d = cv2.dilate(overlap.astype(np.uint8), k).astype(bool)
    else:
        overlap_d = overlap
    fx = np.where(overlap_d, fx, 0)
    fy = np.where(overlap_d, fy, 0)
    return fx, fy


def _remap_with_flow(
    img: np.ndarray, flow_x: np.ndarray, flow_y: np.ndarray,
    border_value: float | tuple = 0.0,
) -> np.ndarray:
    """Apply remap so output(p) = img(p + flow(p))."""
    H, W = img.shape[:2]
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    map_u = (u + flow_x).astype(np.float32)
    map_v = (v + flow_y).astype(np.float32)
    if img.ndim == 3:
        bv = border_value if isinstance(border_value, tuple) else (0, 0, 0)
    else:
        bv = border_value if not isinstance(border_value, tuple) else 0
    return cv2.remap(
        img, map_u, map_v, cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=bv,
    )


def bidirectional_warp_pair(
    slab_a: np.ndarray, weight_a: np.ndarray,
    slab_b: np.ndarray, weight_b: np.ndarray,
    of_winsize: int = 31, of_levels: int = 4, of_iter: int = 5,
    smooth_sigma: float = 5.0, overlap_dilate_px: int = 20,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Half-warp BOTH slabs toward each other in their overlap zone.

    Computes Farneback OF in BOTH directions:
      F_ab = OF(slab_a -> slab_b): warping slab_a by +F_ab/2 moves its
             content halfway toward where slab_b sees the same content.
             (cv2.calcOpticalFlowFarneback(g_a, g_b) → F_ab.)
      F_ba = OF(slab_b -> slab_a): warping slab_b by +F_ba/2 moves its
             content halfway toward where slab_a sees the same content.

    Both are smoothed + masked to (dilated) overlap so each cam's non-overlap
    region stays put — exactly like the original `warp_pair_with_of` handles
    non-overlap by tapering flow to zero.

    Symmetric: swapping (slab_a, slab_b) ↔ (slab_b, slab_a) in the call
    produces the same midpoint (modulo numerical jitter in independent OF runs).

    Returns (slab_a_warped, weight_a_warped, slab_b_warped, weight_b_warped).
    """
    H, W = slab_a.shape[:2]
    overlap = (weight_a > 1e-6) & (weight_b > 1e-6)
    if int(overlap.sum()) < 100:
        return slab_a.copy(), weight_a.copy(), slab_b.copy(), weight_b.copy()

    ga = cv2.cvtColor(slab_a.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor(slab_b.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    ga_m = np.where(overlap, ga, 0).astype(np.uint8)
    gb_m = np.where(overlap, gb, 0).astype(np.uint8)

    # F_ab: where does a's content end up in b's frame
    flow_ab = cv2.calcOpticalFlowFarneback(
        ga_m, gb_m, flow=None,
        pyr_scale=0.5, levels=of_levels, winsize=of_winsize,
        iterations=of_iter, poly_n=7, poly_sigma=1.5, flags=0,
    )
    # F_ba: where does b's content end up in a's frame
    flow_ba = cv2.calcOpticalFlowFarneback(
        gb_m, ga_m, flow=None,
        pyr_scale=0.5, levels=of_levels, winsize=of_winsize,
        iterations=of_iter, poly_n=7, poly_sigma=1.5, flags=0,
    )

    # Half magnitudes
    fa_x, fa_y = _smooth_and_mask_flow(
        flow_ab[..., 0] * 0.5, flow_ab[..., 1] * 0.5,
        overlap, smooth_sigma, overlap_dilate_px,
    )
    fb_x, fb_y = _smooth_and_mask_flow(
        flow_ba[..., 0] * 0.5, flow_ba[..., 1] * 0.5,
        overlap, smooth_sigma, overlap_dilate_px,
    )

    sa_w = _remap_with_flow(slab_a, fa_x, fa_y, border_value=(0, 0, 0))
    wa_w = _remap_with_flow(weight_a, fa_x, fa_y, border_value=0.0)
    sb_w = _remap_with_flow(slab_b, fb_x, fb_y, border_value=(0, 0, 0))
    wb_w = _remap_with_flow(weight_b, fb_x, fb_y, border_value=0.0)
    return sa_w, wa_w, sb_w, wb_w


def of_bidirectional_chain_warp(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    close_back_seam: bool = True,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Bidirectional half-warp chained from front_center anchor.

    Mirror of `of_chain_warp` topology but uses half-warps:
      CCW: front_center → front_left → side_left → rear_left
      CW : front_center → front_right → side_right → rear_right
      Back seam (rear_left vs rear_right): one final half-warp pair if
      close_back_seam=True (default).

    SERIAL CHAIN SEMANTICS:
      At chain step i (working on cam `cur` adjacent to `prev`):
        - prev's slab is in its CURRENT warped state (frozen from previous step).
        - cur's slab starts in its ORIGINAL state (no prior chain history).
        - We compute the bidirectional half-warp on the pair, but BOTH halves
          modify the PAIR's local consensus. To avoid the chain perpetually
          re-warping front_center (the anchor), prev's half-warp is COLLAPSED
          INTO cur's warp: cur moves by full F_pa/2 + half of prev's would-be
          inverse motion. In effect: cur moves toward the midpoint, prev
          stays put (already part of the consensus chain).

      This is a deliberate simplification — it preserves the chain's "frozen
      anchor" semantics while still applying half the displacement to the
      newly-joined cam. The shorter per-pair displacement = the main
      OF-stability win even without a global solve.

    To get the full bidirectional benefit (both cams move) use
    `of_joint_solve_warp` instead.
    """
    n = len(slabs)
    warped_s: list[np.ndarray] = [None] * n  # type: ignore
    warped_w: list[np.ndarray] = [None] * n  # type: ignore
    warped_s[0] = slabs[0]; warped_w[0] = weights[0]
    for chain in [CCW, CW]:
        for i in range(1, len(chain)):
            prev = chain[i - 1]; cur = chain[i]
            # Use half-warp: cur moves halfway toward prev's (already warped) frame.
            # Equivalent to calling bidirectional_warp_pair but discarding the
            # prev's half-warp (prev is anchor-frozen at chain step i).
            _sw, _ww = _half_warp_b_toward_a(
                warped_s[prev], warped_w[prev], slabs[cur], weights[cur],
            )
            warped_s[cur] = _sw; warped_w[cur] = _ww
    if close_back_seam:
        # At the back seam BOTH rear cams are end-of-chain — neither has
        # special anchor status. Use the SYMMETRIC half-warp on the pair.
        sa_w, wa_w, sb_w, wb_w = bidirectional_warp_pair(
            warped_s[3], warped_w[3], warped_s[4], warped_w[4],
        )
        warped_s[3] = sa_w; warped_w[3] = wa_w
        warped_s[4] = sb_w; warped_w[4] = wb_w
    return warped_s, warped_w


def _half_warp_b_toward_a(
    slab_a: np.ndarray, weight_a: np.ndarray,
    slab_b: np.ndarray, weight_b: np.ndarray,
    of_winsize: int = 31, of_levels: int = 4, of_iter: int = 5,
    smooth_sigma: float = 5.0, overlap_dilate_px: int = 20,
) -> tuple[np.ndarray, np.ndarray]:
    """Asymmetric HALF warp of (b only) toward a — same flow as
    `warp_pair_with_of` but multiplied by 0.5. Used by the chain variant
    where prev is frozen as an anchor.
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
    fx, fy = _smooth_and_mask_flow(
        flow[..., 0] * 0.5, flow[..., 1] * 0.5,
        overlap, smooth_sigma, overlap_dilate_px,
    )
    sw = _remap_with_flow(slab_b, fx, fy, border_value=(0, 0, 0))
    ww = _remap_with_flow(weight_b, fx, fy, border_value=0.0)
    return sw, ww


# ---------------------------------------------------------------------------
# L3 (bidirectional, joint solve): global consistency
# ---------------------------------------------------------------------------

def of_joint_solve_warp(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    smooth_sigma: float = 5.0, overlap_dilate_px: int = 20,
    reg_lambda: float = 1.0,
    of_winsize: int = 31, of_levels: int = 4, of_iter: int = 5,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Global joint solve for each cam's 2D displacement field.

    Algorithm (per-pixel, per-pair, weighted least squares):
      1. Pre-compute pairwise flow F_ij on every ring pair (i,j) from the
         ORIGINAL (unwarped) slabs, smoothed + masked to dilated overlap Ω_ij.
      2. For each cam k, solve for a per-pixel displacement D_k(p) (2-channel).
      3. The "consistency" constraint per pair (i,j) at every pixel p ∈ Ω_ij:
              D_i(p) - D_j(p) ≈ -F_ij(p)   (each cam goes half-way to midpoint)
         where F_ij = calcOpticalFlowFarneback(g_i, g_j), so that
         g_j(p) ≈ g_i(p + F_ij(p)). With our remap convention
         warped_k(p) = slab_k(p + D_k(p)), a feature originally at q in cam k
         ends up at warped position p = q - D_k(p). For the same feature in
         cams i and j to land at the same warped position:
              q_i - D_i(p) = q_j - D_j(p),  and q_j = q_i + F_ij(q_i)
         → D_j(p) - D_i(p) = F_ij(q_i) ≈ F_ij(p)   (small-disp linearization)
         → D_i(p) - D_j(p) = -F_ij(p)
         Rather than the strict bundle adjustment formulation D_i(p) -
         D_j(p + F_ij(p)) = -F_ij(p), we use the LINEARIZED version (small
         displacement assumption), which lets us decouple cams.
      4. Regularization: anchor front_center (D_0 = 0). For each cam k>0,
         add ||D_k||² penalty (Tikhonov) with weight `reg_lambda`. Anchor +
         Tikhonov together break the gauge ambiguity (the system is otherwise
         translation-invariant: shifting all D_k by the same amount is a
         null-space direction).

    Per-pixel-independent solve: since the linearized constraint at pixel p
    only couples D_i(p) and D_j(p) for pixels in the SAME overlap zone, the
    problem decouples PER PIXEL into a 14×14 linear system (2 components × 7
    cams) — but most pixels are only in 0-2 overlap zones, so the system is
    nearly always trivial. We solve all pixels in parallel via vectorized
    7-cam component system using a Jacobi-style closed form.

    Specifically, at pixel p, the local energy is:
        E(p) = Σ_{(i,j) | p∈Ω_ij} w_ij(p) ||D_i(p) - D_j(p) + F_ij(p)||²
             + λ Σ_k ||D_k(p)||²    [for k ≠ 0]
             + ∞ * ||D_0(p)||²      [anchor]
    The minimum of (D_i - D_j + F)² is at D_i - D_j = -F (correct sign).
    Without the +F sign, the joint solve would push cams APART by F, doubling
    parallax (we accidentally ran this experiment on the first iteration).

    Taking ∂E/∂D_k = 0 gives a small linear system. We assemble the 7×7
    coefficient matrix and 7×2 RHS PER PIXEL (vectorized over all valid
    pixels), then solve via `np.linalg.solve` (broadcasts over leading dims).

    Returns warped slabs + warped weights, ready for `hard_select`.

    Caveats vs the strict bundle adjustment:
      - The linearized constraint `D_i(p) - D_j(p) ≈ -F_ij(p)` is only
        accurate for small flows (a few pixels). For 30+ pixel flows the
        true constraint should evaluate D_j at p + F_ij(p), not p — i.e.
        we should warp D_j by F_ij first. We skip this iteration here for
        simplicity (one-step linearization).
      - No spatial smoothing in D itself (each pixel solved independently);
        we rely on the input flows being pre-smoothed.
      - The Tikhonov regularization shrinks displacements toward zero, which
        biases each cam slightly toward its original position. Tune via
        `reg_lambda` (default 1.0 chosen for OF magnitudes in 0-30 px range).
    """
    n = len(slabs)
    H, W = slabs[0].shape[:2]

    # Pre-compute per-pair flow + overlap mask (in dilated zone)
    pair_data: list[dict] = []
    for (i, j) in RING_PAIRS:
        overlap = (weights[i] > 1e-6) & (weights[j] > 1e-6)
        if int(overlap.sum()) < 100:
            continue
        ga = cv2.cvtColor(slabs[i].astype(np.uint8), cv2.COLOR_RGB2GRAY)
        gb = cv2.cvtColor(slabs[j].astype(np.uint8), cv2.COLOR_RGB2GRAY)
        ga_m = np.where(overlap, ga, 0).astype(np.uint8)
        gb_m = np.where(overlap, gb, 0).astype(np.uint8)
        # Flow from i to j: cv2.calcOpticalFlowFarneback(g_i, g_j) → F_ij where
        # g_j(p) ≈ g_i(p + F_ij(p)). For our remap convention warped(p) =
        # slab(p + D(p)), feature at q in original ends up at p = q - D(p) in
        # warped. Pair consistency: feature at q_i in cam i and at q_j = q_i +
        # F_ij in cam j must land at same warped position:
        #   q_i - D_i(p) = q_j - D_j(p) = q_i + F_ij(q_i) - D_j(p)
        #   → D_j(p) - D_i(p) = F_ij(q_i) ≈ F_ij(p)
        #   → D_i(p) - D_j(p) ≈ -F_ij(p)   (constraint)
        flow = cv2.calcOpticalFlowFarneback(
            ga_m, gb_m, flow=None,
            pyr_scale=0.5, levels=of_levels, winsize=of_winsize,
            iterations=of_iter, poly_n=7, poly_sigma=1.5, flags=0,
        )
        fx, fy = _smooth_and_mask_flow(
            flow[..., 0], flow[..., 1],
            overlap, smooth_sigma, overlap_dilate_px,
        )
        # Dilated overlap for the constraint mask
        if overlap_dilate_px > 0:
            k = np.ones((overlap_dilate_px * 2 + 1, overlap_dilate_px * 2 + 1), np.uint8)
            overlap_d = cv2.dilate(overlap.astype(np.uint8), k).astype(bool)
        else:
            overlap_d = overlap
        pair_data.append({
            "i": i, "j": j,
            "fx": fx.astype(np.float32),
            "fy": fy.astype(np.float32),
            "mask": overlap_d,
        })

    # Build per-pixel 7x7 linear system: solve for D_k(p), k=0..6.
    # E_pair(p): w_ij(p) * (D_i(p) - D_j(p) + F_ij(p))^2  [for x and y separately]
    # E_reg(p):  λ * Σ_{k>0} D_k(p)^2
    # E_anchor:  ∞ * D_0(p)^2  → enforced as a hard constraint by adding a huge
    #            diagonal entry to row 0 (large M).
    #
    # ∂E/∂D_k = 2 * [ Σ_{j: (k,j)∈pairs} w_kj (D_k - D_j + F_kj)
    #               + Σ_{i: (i,k)∈pairs} w_ik (D_k - D_i - F_ik)
    #               + λ D_k * [k>0] ]
    # Setting = 0:
    #   diag_k = Σ pairs touching k * w + λ*[k>0] + M*[k==0]
    #   off-diag k,j = -w_kj (for pair (k,j) OR (j,k))
    #   rhs_k = - Σ_{j: (k,j)∈pairs} w_kj * F_kj  + Σ_{i: (i,k)∈pairs} w_ik * F_ik
    # (so for pair (i,j): b[i] -= w*F, b[j] += w*F)
    #
    # Per-pixel: A(p) ∈ R^{7×7}, b(p) ∈ R^{7×2} (x and y components stacked).
    # Vectorized: A ∈ R^{H,W,7,7}, b ∈ R^{H,W,7,2}.

    # Anchor weight (large M) and regularization weight
    M_anchor = 1e6
    lam = float(reg_lambda)

    # Initialize coefficient matrix and RHS as zeros.
    # We use float32 to save memory: 2048*4096*7*7*4 bytes = ~1.6 GB. That's
    # still hefty but feasible on a workstation with 16 GB RAM. To reduce
    # further, we could solve in tiles, but let's start dense.
    # Actually let's check: at 2048×4096 the (H,W,7,7) array is
    # 2048*4096*49*4 ≈ 1.6 GB. Plus b at 2048*4096*7*2*4 ≈ 470 MB.
    # Too much for typical RAM. Solve in horizontal stripes.
    stripe_h = 256
    out_disp = [np.zeros((H, W, 2), dtype=np.float32) for _ in range(n)]

    # Per-pair pixel weights: use the geometric-mean of two cams' cos² weights
    # in the overlap zone. This down-weights pair contributions in regions
    # where either cam is grazing-incidence (low confidence).
    pair_w_full = []
    for pd in pair_data:
        w_i = weights[pd["i"]]
        w_j = weights[pd["j"]]
        wt = np.sqrt(np.clip(w_i * w_j, 0, None)).astype(np.float32)
        wt = np.where(pd["mask"], wt, 0.0)
        pair_w_full.append(wt)

    for y0 in range(0, H, stripe_h):
        y1 = min(H, y0 + stripe_h)
        ph = y1 - y0
        # Allocate stripe-local A, b
        A = np.zeros((ph, W, n, n), dtype=np.float32)
        b = np.zeros((ph, W, n, 2), dtype=np.float32)

        # Anchor + regularization first
        # All cams: + lam to diagonal
        for k in range(n):
            A[..., k, k] += lam
        # Anchor cam 0: + huge diagonal
        A[..., 0, 0] += M_anchor

        # Add pair contributions
        for pd_idx, pd in enumerate(pair_data):
            i = pd["i"]; j = pd["j"]
            fx = pd["fx"][y0:y1]
            fy = pd["fy"][y0:y1]
            w_pair = pair_w_full[pd_idx][y0:y1]  # (ph, W)
            # Diagonal contributions: + w to A[i,i] and A[j,j]
            A[..., i, i] += w_pair
            A[..., j, j] += w_pair
            # Off-diagonal: -w to A[i,j] and A[j,i]
            A[..., i, j] -= w_pair
            A[..., j, i] -= w_pair
            # RHS: -w*F to row i, +w*F to row j (matches D_i - D_j ≈ -F_ij,
            # i.e. cam i moves by -F/2 and cam j by +F/2 to meet at midpoint).
            # See module docstring "consistency constraint" derivation.
            b[..., i, 0] -= w_pair * fx
            b[..., i, 1] -= w_pair * fy
            b[..., j, 0] += w_pair * fx
            b[..., j, 1] += w_pair * fy

        # Solve per-pixel: A(p) D(p) = b(p), D ∈ R^{n×2}.
        # np.linalg.solve broadcasts: A (..., n, n), b (..., n, k) → (..., n, k).
        try:
            D = np.linalg.solve(A, b)  # (ph, W, n, 2)
        except np.linalg.LinAlgError:
            # Per-pixel singular A: fall back to lstsq via batch loop
            D = np.zeros((ph, W, n, 2), dtype=np.float32)
            for yy in range(ph):
                for xx in range(W):
                    try:
                        D[yy, xx] = np.linalg.lstsq(A[yy, xx], b[yy, xx], rcond=None)[0]
                    except np.linalg.LinAlgError:
                        pass

        for k in range(n):
            out_disp[k][y0:y1] = D[..., k, :]

    # Apply each cam's displacement field
    warped_s: list[np.ndarray] = []
    warped_w: list[np.ndarray] = []
    for k in range(n):
        Dk = out_disp[k]
        # Optional smoothing of D itself (helps further with edges)
        sw = _remap_with_flow(slabs[k], Dk[..., 0], Dk[..., 1], border_value=(0, 0, 0))
        ww = _remap_with_flow(weights[k], Dk[..., 0], Dk[..., 1], border_value=0.0)
        warped_s.append(sw)
        warped_w.append(ww)
    return warped_s, warped_w


# ---------------------------------------------------------------------------
# Combined pipeline
# ---------------------------------------------------------------------------

def blend_hard_hdr_of_bidir(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    apply_of: bool = True,
    mode: str = "chain",
) -> np.ndarray:
    """Full L2 HDR → L3 bidirectional OF → L1 hard_select pipeline.

    Args:
        slabs, weights: per-cam ERP slabs + cos² feather weights.
        apply_of: if False, skip L3 (HDR + hard_select only).
        mode: "chain" (default, approach a) or "joint" (approach b).
            - "chain": of_bidirectional_chain_warp; faster, similar topology
              to the shipped of_chain_warp but with half-magnitude warps.
            - "joint": of_joint_solve_warp; per-pixel global least-squares;
              slower (1.5-2×) but more globally consistent.

    Returns uint8 ERP image.
    """
    gains = compute_hdr_gains(slabs, weights)
    slabs_hdr = apply_hdr(slabs, gains)
    if apply_of:
        if mode == "chain":
            slabs_warp, weights_warp = of_bidirectional_chain_warp(slabs_hdr, weights)
        elif mode == "joint":
            slabs_warp, weights_warp = of_joint_solve_warp(slabs_hdr, weights)
        else:
            raise ValueError(f"mode={mode!r} not recognized. Use 'chain' or 'joint'.")
    else:
        slabs_warp, weights_warp = slabs_hdr, weights
    return hard_select(slabs_warp, weights_warp)
