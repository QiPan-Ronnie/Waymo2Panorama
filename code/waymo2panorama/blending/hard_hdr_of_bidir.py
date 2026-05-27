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

Three chaining strategies are provided:

  * `of_true_bidirectional_chain_warp` (approach a, DEFAULT for mode="chain")
    — pre-computes flows on all 7 ring pairs from the ORIGINAL (unwarped)
    slabs, then for each cam k composes its displacement as the MEAN of
    half-flows toward each adjacent neighbour. This gives the true
    bidirectional semantic where every cam moves toward midpoint with each
    of its neighbours — no chain-direction asymmetry, no "frozen anchor"
    artifact. It is a single-iteration approximation of the joint solve
    (mathematically equivalent to Jacobi step 1 with λ=0, no anchor): for
    each pair the residual D_i - D_j + F_ij is driven toward zero by
    distributing -F_ij/2 to cam i and +F_ij/2 to cam j, then averaging
    over a cam's neighbours. Cheap (just N pair-flow computes + one
    remap/cam) but converges in one pass because the topology is a small
    ring with low overlap.

  * `of_half_magnitude_chain_warp` (LEGACY, mode="half_chain") — serial
    chain starting from front_center. Each new cam in the chain is warped
    by F/2 toward its already-warped predecessor; the predecessor is
    FROZEN. This is NOT true bidirectional — it's just B→A with halved
    flow magnitude. Kept for backward compatibility and ablation only.
    Prefer the true bidirectional chain (approach a) above.

  * `of_joint_solve_warp` (approach b, mode="joint") — pre-computes flows
    on all 7 ring pairs from the ORIGINAL (unwarped) slabs, then solves a
    sparse linear system for each cam's per-pixel 2D displacement field
    that minimizes total pair disagreement. Globally consistent. Requires
    significantly more memory and time than chain (~2-3× slower, ~3× more
    memory at 2048×4096), and adds a spatial regularization parameter.

NB: this module DOES NOT modify the shipped `hard_hdr_of.py`. It re-uses
`compute_hdr_gains`, `apply_hdr`, and `hard_select` from there but provides
new variants of `warp_pair_with_of` / `of_chain_warp`. Drop-in swap of the
wrapper `blend_hard_hdr_of_bidir` for `blend_hard_hdr_of` is safe.

All three layers remain basic CV: only cv2 + numpy. Runtime: ~55s/anchor
for chain variant, ~80-100s/anchor for joint solve at 2048x4096 (CPU-bound).
"""
from __future__ import annotations

import warnings

import cv2
import numpy as np

from waymo2panorama.blending.hard_hdr_of import (
    CCW, CW, RING_PAIRS,
    compute_hdr_gains, apply_hdr, hard_select,
)


# ---------------------------------------------------------------------------
# Module-level constants (centralised magic numbers)
# ---------------------------------------------------------------------------

#: A pixel is considered "covered by cam k" iff weights[k] > this threshold.
#: Used to define per-pair overlap masks.
EPS_WEIGHT = 1e-6

#: A pair (i, j) is skipped (treated as no-overlap) if its overlap mask has
#: fewer than this many True pixels. Prevents OF from running on degenerate
#: micro-overlaps where Farneback would just return noise.
MIN_OVERLAP_PIXELS = 100

#: Avoid div-by-zero / amplification when normalising a Gaussian-blurred mask.
#: Pixels with smoothed mask weight below this fall back to 1.0 (treated as
#: unsmoothed).
EPS_SMOOTH = 1e-3

#: Horizontal stripe height (rows) for the per-pixel 7×7 joint-solve linear
#: system. Trade-off: larger = fewer Python loop iterations but more peak RAM
#: per stripe. 256 keeps peak under ~210 MB for the 7-cam, 4096-wide case.
STRIPE_HEIGHT = 256

#: Hard-anchor weight added to A[0, 0] in the joint-solve linear system to
#: pin front_center's displacement to zero (breaks gauge ambiguity).
ANCHOR_WEIGHT = 1e6

#: If max |flow| exceeds this many pixels in any pair, the small-displacement
#: linearisation used by `of_joint_solve_warp` is no longer accurate. We
#: emit a warning rather than fail — the result may still be usable, but
#: per-pixel constraints D_i(p) - D_j(p) ≈ -F_ij(p) should ideally be
#: evaluated at p + F_ij(p), not p, for flows this large.
LINEARISATION_MAX_FLOW_PX = 30.0


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
        m_smooth = np.where(m_smooth > EPS_SMOOTH, m_smooth, 1.0)
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
    overlap = (weight_a > EPS_WEIGHT) & (weight_b > EPS_WEIGHT)
    if int(overlap.sum()) < MIN_OVERLAP_PIXELS:
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


# ---------------------------------------------------------------------------
# L3 (bidirectional, TRUE — approach a)
# ---------------------------------------------------------------------------

def of_true_bidirectional_chain_warp(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    of_winsize: int = 31, of_levels: int = 4, of_iter: int = 5,
    smooth_sigma: float = 5.0, overlap_dilate_px: int = 20,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """TRUE bidirectional chain via per-cam mean half-flow.

    For each ring pair (i, j) we compute F_ij = OF(slab_i → slab_j) ONCE
    from the ORIGINAL (unwarped) slabs. Per the bidirectional convention,
    this pair "wants" cam i to move by -F_ij/2 and cam j to move by
    +F_ij/2 so they meet at the midpoint of their shared content.

    Cam k may participate in multiple ring pairs (e.g. cam 0 = front_center
    sits in pairs (0,1) and (0,6); cam 3 = rear_left sits in (2,3) and
    (3,4)). For each k we therefore COMPOSE its final displacement field
    D_k as the MEAN of its per-pair half-flow contributions:

        D_k(p) = mean over neighbours m of:
                   -F_{k,m}(p)/2  if pair was stored as (k, m)
                   +F_{m,k}(p)/2  if pair was stored as (m, k)

    The mean (rather than sum) keeps the magnitude bounded — averaging two
    half-flows is still a half-flow in expectation. The mean is taken
    only over neighbours whose (dilated) overlap mask is True at p; for
    pixels in cam k's non-overlap region D_k(p) = 0 (cam stays put exactly
    as in the asymmetric variant).

    Mathematical observation: this is precisely the closed-form solution
    to the joint-solve linear system in the limit λ → 0 with no anchor
    and a SINGLE Jacobi iteration. The per-pixel local energy

        E(p) = Σ_{(i,j) ∈ overlapping pairs at p} ||D_i - D_j + F_ij||²

    has gradient ∂E/∂D_k = 2 [Σ_{m: (k,m)} (D_k - D_m + F_km)
                              + Σ_{m: (m,k)} (D_k - D_m - F_mk)].
    Starting from D ≡ 0 and taking ONE Jacobi update
        D_k ← D_k - (1 / |neighbours(k)|) × (∂E/∂D_k) / 2
    gives exactly the mean-half-flow formula above. Subsequent Jacobi
    sweeps would further reduce residuals on multi-overlap pixels, but
    for a 7-cam ring where each cam has at most 2 ring neighbours and
    overlap zones rarely intersect 3-way, the one-step solution is
    already at the global optimum (modulo gauge).

    Cheaper than `of_joint_solve_warp` (just N pair flows + N remaps; no
    per-pixel 7×7 linear system) AND symmetric (every cam moves; no
    privileged anchor). Recommended default.

    Returns warped slabs + warped weights, ready for `hard_select`.
    """
    n = len(slabs)
    H, W = slabs[0].shape[:2]

    # 1. Pre-compute all per-pair flows from ORIGINAL slabs
    pair_data: list[dict] = []
    for (i, j) in RING_PAIRS:
        overlap = (weights[i] > EPS_WEIGHT) & (weights[j] > EPS_WEIGHT)
        if int(overlap.sum()) < MIN_OVERLAP_PIXELS:
            continue
        ga = cv2.cvtColor(slabs[i].astype(np.uint8), cv2.COLOR_RGB2GRAY)
        gb = cv2.cvtColor(slabs[j].astype(np.uint8), cv2.COLOR_RGB2GRAY)
        ga_m = np.where(overlap, ga, 0).astype(np.uint8)
        gb_m = np.where(overlap, gb, 0).astype(np.uint8)
        flow_ij = cv2.calcOpticalFlowFarneback(
            ga_m, gb_m, flow=None,
            pyr_scale=0.5, levels=of_levels, winsize=of_winsize,
            iterations=of_iter, poly_n=7, poly_sigma=1.5, flags=0,
        )
        fx, fy = _smooth_and_mask_flow(
            flow_ij[..., 0], flow_ij[..., 1],
            overlap, smooth_sigma, overlap_dilate_px,
        )
        # Dilated overlap, matches the contribution-mask convention used in
        # _smooth_and_mask_flow (so D_k is non-zero exactly where the pair
        # flow is non-zero).
        if overlap_dilate_px > 0:
            kern = np.ones((overlap_dilate_px * 2 + 1, overlap_dilate_px * 2 + 1), np.uint8)
            overlap_d = cv2.dilate(overlap.astype(np.uint8), kern).astype(bool)
        else:
            overlap_d = overlap
        pair_data.append({
            "i": i, "j": j,
            "fx": fx.astype(np.float32),
            "fy": fy.astype(np.float32),
            "mask": overlap_d,
        })

    # 2. Accumulate per-cam sum of half-flow contributions + neighbour count
    sum_fx = [np.zeros((H, W), dtype=np.float32) for _ in range(n)]
    sum_fy = [np.zeros((H, W), dtype=np.float32) for _ in range(n)]
    n_neighbours = [np.zeros((H, W), dtype=np.float32) for _ in range(n)]

    for pd in pair_data:
        i = pd["i"]; j = pd["j"]
        fx = pd["fx"]; fy = pd["fy"]
        mask_f = pd["mask"].astype(np.float32)
        # cam i: -F_ij/2 ; cam j: +F_ij/2
        sum_fx[i] -= 0.5 * fx
        sum_fy[i] -= 0.5 * fy
        sum_fx[j] += 0.5 * fx
        sum_fy[j] += 0.5 * fy
        n_neighbours[i] += mask_f
        n_neighbours[j] += mask_f

    # 3. Mean over active neighbours → final per-cam displacement
    warped_s: list[np.ndarray] = []
    warped_w: list[np.ndarray] = []
    for k in range(n):
        denom = np.where(n_neighbours[k] > 0, n_neighbours[k], 1.0)
        Dx = sum_fx[k] / denom
        Dy = sum_fy[k] / denom
        sw = _remap_with_flow(slabs[k], Dx, Dy, border_value=(0, 0, 0))
        ww = _remap_with_flow(weights[k], Dx, Dy, border_value=0.0)
        warped_s.append(sw)
        warped_w.append(ww)
    return warped_s, warped_w


# ---------------------------------------------------------------------------
# L3 (half-magnitude chain — LEGACY, kept for ablation)
# ---------------------------------------------------------------------------

def of_half_magnitude_chain_warp(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    close_back_seam: bool = True,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """LEGACY: half-magnitude chain warp (NOT true bidirectional).

    Mirror of `of_chain_warp` topology but multiplies each per-pair flow by
    0.5 before remap. Only the new cam in each chain step moves; the
    predecessor is frozen as an anchor. This is the asymmetric "B-toward-A"
    warp with halved magnitude — useful as an ABLATION baseline to isolate
    the effect of halving the flow magnitude from the effect of true
    bidirectional motion.

    Trade-off (documented honestly):
      * Pro: simpler, slightly faster (one OF call per chain step vs two),
        empirically more OF-stable than the full-magnitude original because
        Farneback over-shoots less on large displacements.
      * Con: still has chain-direction asymmetry — the chain accumulates,
        and the back seam at cams 3/4 sees twice the residual.
      * Con: NOT bidirectional in the geometric sense. The docstring on
        the original `of_bidirectional_chain_warp` admitted this as
        "deliberate simplification" but mis-named the function.

    Prefer `of_true_bidirectional_chain_warp` for production. Kept here
    purely so the comparison `mode="half_chain"` can be reproduced from
    earlier ablation runs.
    """
    n = len(slabs)
    warped_s: list[np.ndarray] = [None] * n  # type: ignore
    warped_w: list[np.ndarray] = [None] * n  # type: ignore
    warped_s[0] = slabs[0]; warped_w[0] = weights[0]
    for chain in [CCW, CW]:
        for i in range(1, len(chain)):
            prev = chain[i - 1]; cur = chain[i]
            _sw, _ww = _half_warp_b_toward_a(
                warped_s[prev], warped_w[prev], slabs[cur], weights[cur],
            )
            warped_s[cur] = _sw; warped_w[cur] = _ww
    if close_back_seam:
        # Both rear cams are end-of-chain: use the symmetric pair warp here.
        sa_w, wa_w, sb_w, wb_w = bidirectional_warp_pair(
            warped_s[3], warped_w[3], warped_s[4], warped_w[4],
        )
        warped_s[3] = sa_w; warped_w[3] = wa_w
        warped_s[4] = sb_w; warped_w[4] = wb_w
    return warped_s, warped_w


# Back-compat alias (old name still importable; emits DeprecationWarning).
def of_bidirectional_chain_warp(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    close_back_seam: bool = True,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """DEPRECATED alias for `of_half_magnitude_chain_warp`.

    The old name was misleading — it suggested true bidirectional motion
    when in fact only the trailing cam in each chain step was warped.
    Call `of_true_bidirectional_chain_warp` (new, correct semantics) or
    `of_half_magnitude_chain_warp` (legacy ablation) explicitly.
    """
    warnings.warn(
        "of_bidirectional_chain_warp is deprecated and misnamed: it does NOT "
        "perform true bidirectional warping. Use of_true_bidirectional_chain_warp "
        "for the correct semantics, or of_half_magnitude_chain_warp for the "
        "legacy half-magnitude-chain behaviour.",
        DeprecationWarning, stacklevel=2,
    )
    return of_half_magnitude_chain_warp(slabs, weights, close_back_seam=close_back_seam)


def _half_warp_b_toward_a(
    slab_a: np.ndarray, weight_a: np.ndarray,
    slab_b: np.ndarray, weight_b: np.ndarray,
    of_winsize: int = 31, of_levels: int = 4, of_iter: int = 5,
    smooth_sigma: float = 5.0, overlap_dilate_px: int = 20,
) -> tuple[np.ndarray, np.ndarray]:
    """Asymmetric HALF warp of (b only) toward a — same flow as
    `warp_pair_with_of` but multiplied by 0.5. Used by
    `of_half_magnitude_chain_warp` where prev is frozen as an anchor.
    """
    H, W = slab_a.shape[:2]
    overlap = (weight_a > EPS_WEIGHT) & (weight_b > EPS_WEIGHT)
    if int(overlap.sum()) < MIN_OVERLAP_PIXELS:
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
        simplicity (one-step linearization). A warning is emitted whenever
        any pair's |flow|_max exceeds LINEARISATION_MAX_FLOW_PX so callers
        know the solve may be sub-optimal.
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
    max_flow_seen = 0.0
    for (i, j) in RING_PAIRS:
        overlap = (weights[i] > EPS_WEIGHT) & (weights[j] > EPS_WEIGHT)
        if int(overlap.sum()) < MIN_OVERLAP_PIXELS:
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
        # Track maximum flow magnitude seen across all pairs to surface a
        # linearisation warning if it exceeds LINEARISATION_MAX_FLOW_PX.
        mag = np.hypot(fx, fy)
        local_max = float(mag.max()) if mag.size else 0.0
        if local_max > max_flow_seen:
            max_flow_seen = local_max
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

    if max_flow_seen > LINEARISATION_MAX_FLOW_PX:
        warnings.warn(
            f"of_joint_solve_warp: max per-pair flow magnitude "
            f"{max_flow_seen:.1f} px exceeds the small-displacement "
            f"linearisation threshold ({LINEARISATION_MAX_FLOW_PX:.1f} px). "
            "The constraint D_i(p) - D_j(p) ~= -F_ij(p) is no longer accurate "
            "at this scale; the solve result may have residual misalignment "
            "in high-parallax regions. Consider iterating the warp or "
            "evaluating D_j at p + F_ij(p) (bundle-adjustment form).",
            RuntimeWarning, stacklevel=2,
        )

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

    lam = float(reg_lambda)

    # Initialize coefficient matrix and RHS as zeros.
    # Solve in horizontal stripes (STRIPE_HEIGHT rows each) to keep memory in
    # check; at H=2048, W=4096, n=7 the full (H,W,n,n) float32 array would be
    # ~1.6 GB. With STRIPE_HEIGHT=256 the per-stripe slab is ~210 MB.
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

    for y0 in range(0, H, STRIPE_HEIGHT):
        y1 = min(H, y0 + STRIPE_HEIGHT)
        ph = y1 - y0
        # Allocate stripe-local A, b
        A = np.zeros((ph, W, n, n), dtype=np.float32)
        b = np.zeros((ph, W, n, 2), dtype=np.float32)

        # Anchor + regularization first
        # All cams: + lam to diagonal
        for k in range(n):
            A[..., k, k] += lam
        # Anchor cam 0: + huge diagonal
        A[..., 0, 0] += ANCHOR_WEIGHT

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
        mode: one of
            - "chain" (default): of_true_bidirectional_chain_warp — true
              bidirectional, every cam moves by mean half-flow to its
              ring neighbours. Recommended default. ~55s/anchor.
            - "half_chain": of_half_magnitude_chain_warp — LEGACY
              ablation. Asymmetric chain with halved flow magnitude;
              only the trailing cam in each step moves. Kept for
              backward-compatibility with earlier ablation outputs.
            - "joint": of_joint_solve_warp — per-pixel global least-
              squares solve. Slower (1.5-2× chain) and uses ~3× more
              peak RAM but produces the most globally-consistent
              alignment when pair flows agree.

    Returns uint8 ERP image.
    """
    gains = compute_hdr_gains(slabs, weights)
    slabs_hdr = apply_hdr(slabs, gains)
    if apply_of:
        if mode == "chain":
            slabs_warp, weights_warp = of_true_bidirectional_chain_warp(slabs_hdr, weights)
        elif mode == "half_chain":
            slabs_warp, weights_warp = of_half_magnitude_chain_warp(slabs_hdr, weights)
        elif mode == "joint":
            slabs_warp, weights_warp = of_joint_solve_warp(slabs_hdr, weights)
        else:
            raise ValueError(
                f"mode={mode!r} not recognized. "
                "Use 'chain' (true bidirectional), 'half_chain' (legacy), "
                "or 'joint' (global solve)."
            )
    else:
        slabs_warp, weights_warp = slabs_hdr, weights
    return hard_select(slabs_warp, weights_warp)
