"""
Hard cam selection with graph-cut seam routing + joint HDR + OF chain warp.

Variant of `hard_hdr_of.py` that replaces the L1 cos²-argmax pick with a
graph-cut seam in each pairwise overlap. The other two layers (joint HDR,
Farneback OF) are unchanged — we import them from `hard_hdr_of` rather than
redefine.

Why graph-cut seam:
  - Plain argmax(cos²) defines a fixed angular boundary (Voronoi over the
    cam principal axes). Whatever object happens to sit at that boundary
    gets cut through (lane lines, car bumpers, columns).
  - Graph-cut chooses, within the overlap region of each cam pair, a seam
    path that minimizes per-pixel content disagreement between the two
    cams' projections. After HDR + OF, that disagreement is already small
    in flat textureless areas (sky, road, walls) and large on misaligned
    object boundaries — so the cut snakes around objects rather than
    through them.

Why this should succeed where N2 / N1+graphcut failed:
  - N1+graphcut ran on LiDAR-depth-corrected ERP where the depth was wrong.
    Both cams "looked right" inside the overlap → flat cost field → cut
    routed arbitrarily through random objects.
  - Hard-select baseline runs on raw infinity-depth projections (no broken
    depth) after HDR (no brightness step) and OF (parallax aligned in the
    overlap). The remaining residual is honest content disagreement, which
    is exactly what the cost function targets.

Method per adjacent pair (i, j) in RING_PAIRS:
  1. Crop both cams' RGB + alpha to the overlap-region bounding box
     (with handling for the back-seam wrap at the rear of the ring).
  2. Build the per-pixel cost (using cv2.detail.GraphCutSeamFinder with
     COST_COLOR by default — already includes intensity + gradient terms
     under the hood).
  3. cv2.detail finds an optimal cut, returning binary masks that partition
     the overlap region into "use cam i" vs "use cam j" sub-regions.
  4. We translate that into a per-pixel assignment override, which is
     applied on top of the global cos² argmax. Pixels not touched by any
     pair (no overlap at all, or only one cam covers them) fall through
     to the original argmax.

Fallback: if cv2.detail.GraphCutSeamFinder is unavailable for some reason
(non-default OpenCV build), `find_smart_seam_per_pair` falls back to a
per-row Dijkstra DP over per-pixel |slab_a − slab_b|, which finds a
near-optimal vertical seam — same complexity class, slightly less smooth
than full 2D graph-cut.

Public API:
  - find_smart_seam_per_pair(slab_a, weight_a, slab_b, weight_b)
        -> (assign, overlap)  # int8 (H,W), bool (H,W)
  - hard_select_with_graphcut(slabs, weights) -> ERP uint8
  - blend_hard_hdr_of_graphcut(slabs, weights, apply_of=True) -> ERP uint8
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

# Reuse the L2 HDR and L3 OF layers from the shipped pipeline — same module,
# we are only modifying L1 (the final pick).
from waymo2panorama.blending.hard_hdr_of import (
    RING_PAIRS,
    apply_hdr,
    compute_hdr_gains,
    of_chain_warp,
)


# ---------------------------------------------------------------------------
# Per-pair graph-cut seam
# ---------------------------------------------------------------------------


def _overlap_bbox_with_wrap(
    weight_a: np.ndarray, weight_b: np.ndarray, pad: int = 2, threshold: float = 1e-6,
) -> tuple[Optional[tuple[int, int, int, int]], bool]:
    """Find the overlap bounding box, with detection of ERP wrap-around.

    The back-seam pair (rear_left vs rear_right) typically has its overlap
    split across the left and right edges of the ERP (near col 0 and col W).
    In that case we report `wraps=True` and the caller should roll the array
    by W/2 before running graphcut so the overlap is contiguous.

    Returns:
        bbox: (v0, v1, u0, u1) exclusive on v1, u1; or None if no overlap.
              When wraps=True, u0/u1 are in the rolled (by W/2) coordinate.
        wraps: True if the overlap region wraps around the ERP horizontal
               boundary and the caller should roll the input.
    """
    overlap = (weight_a > threshold) & (weight_b > threshold)
    if not overlap.any():
        return None, False

    H, W = overlap.shape
    cols_any = overlap.any(axis=0)
    rows_any = overlap.any(axis=1)
    v0 = max(0, int(np.argmax(rows_any)) - pad)
    v1 = min(H, H - int(np.argmax(rows_any[::-1])) + pad)

    cols_idx = np.flatnonzero(cols_any)
    u_min = int(cols_idx[0])
    u_max = int(cols_idx[-1])
    span = u_max - u_min + 1

    # Wrap heuristic: overlap touches both ends and spans more than 60% of W.
    # That happens when the "real" overlap is a narrow band straddling col 0.
    wraps = (
        bool(cols_any[0]) and bool(cols_any[-1]) and span > int(0.6 * W)
    )
    if not wraps:
        u0 = max(0, u_min - pad)
        u1 = min(W, u_max + 1 + pad)
        return (v0, v1, u0, u1), False

    # Wrapped case: roll by W/2 so the overlap becomes contiguous, then take bbox
    rolled = np.roll(overlap, W // 2, axis=1)
    cols_r = rolled.any(axis=0)
    cidx_r = np.flatnonzero(cols_r)
    if cidx_r.size == 0:
        return None, False
    u0 = max(0, int(cidx_r[0]) - pad)
    u1 = min(W, int(cidx_r[-1]) + 1 + pad)
    return (v0, v1, u0, u1), True




def _dijkstra_seam_fallback(
    cost: np.ndarray, overlap: np.ndarray,
) -> np.ndarray:
    """Per-row min-cost vertical-ish path through `cost`, restricted to `overlap`.

    Used when cv2.detail.GraphCutSeamFinder isn't available or trips on weird
    masks. Same input/output contract as graph-cut: returns int8 (h, w)
    assignment in {0=A, 1=B}. Pixels left of the seam path -> A; right -> B.

    The seam is built by DP over rows: for each row v, dp[v, u] = min cumulative
    cost to reach (v, u). Transitions from row v-1 allow horizontal drift of
    [-1, 0, +1] (8-connected). Traceback gives a column index per row.
    """
    h, w = cost.shape
    INF = 1e18
    dp = np.full((h, w), INF, dtype=np.float64)
    par = np.zeros((h, w), dtype=np.int32)  # column offset of parent: -1/0/+1
    # init row 0
    cost0 = np.where(overlap[0], cost[0], INF)
    dp[0] = cost0
    for v in range(1, h):
        prev = dp[v - 1]
        # shifted neighbors
        left = np.concatenate([[INF], prev[:-1]])   # par offset -1
        mid = prev                                   # par offset  0
        right = np.concatenate([prev[1:], [INF]])    # par offset +1
        stack = np.stack([left, mid, right], axis=0)  # (3, w)
        best_off = np.argmin(stack, axis=0)           # 0/1/2
        best_val = np.min(stack, axis=0)
        cur_cost = np.where(overlap[v], cost[v], INF)
        dp[v] = best_val + cur_cost
        par[v] = best_off - 1  # -1, 0, +1

    # If a row has no overlap, dp[v] is all INF; skip those rows entirely
    # by computing the seam over rows-with-overlap and broadcasting.
    seam_col = np.zeros(h, dtype=np.int32)
    valid_rows = np.where(overlap.any(axis=1))[0]
    if valid_rows.size == 0:
        # No overlap to cut — fall back to vertical split in the middle.
        seam_col[:] = w // 2
    else:
        # Find the seam endpoint by min of dp on the last valid row, then trace back.
        v_end = int(valid_rows[-1])
        u_end = int(np.argmin(dp[v_end]))
        seam_col[v_end] = u_end
        # back trace upward; par[v, u] = column offset (-1, 0, +1) leading to
        # the parent on row v-1
        for v in range(v_end, 0, -1):
            u_end = u_end + int(par[v, u_end])
            u_end = max(0, min(w - 1, u_end))
            seam_col[v - 1] = u_end
        # forward fill for rows after v_end (no overlap there) using last seam col
        last_u = seam_col[v_end]
        for v in range(v_end + 1, h):
            seam_col[v] = last_u
        # backward fill for rows before first valid (mirror)
        v_first = int(valid_rows[0])
        if v_first > 0:
            first_u = seam_col[v_first]
            for v in range(0, v_first):
                seam_col[v] = first_u

    # Build assignment: cols left of seam → A (0), right → B (1)
    u_grid = np.broadcast_to(np.arange(w), (h, w))
    seam_col_b = seam_col[:, None]
    assign = (u_grid > seam_col_b).astype(np.int8)
    return assign


def _seam_band_around_voronoi(
    weight_a: np.ndarray, weight_b: np.ndarray,
    band_half_width: int, threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return narrow-band coverage masks (`ma`, `mb`) and a `band` mask.

    The cos²-argmax seam is approximately a vertical line for each pair
    (at the locus where weight_a == weight_b). We expand that line by
    ±band_half_width pixels (in column space) per row. Inside this band,
    BOTH masks are set to True (so graph-cut can pick either cam freely
    inside). Outside the band:
      - to the "A side" (where weight_a > weight_b) → ma=True, mb=False
      - to the "B side" (where weight_b > weight_a) → mb=True, ma=False
    This narrowing makes graph-cut's search space linear in seam length
    instead of quadratic in overlap area; on AV2 ring pairs at 2048×4096
    a 60-px band finishes in <0.3s per pair (vs minutes on the full
    overlap region).

    Args:
        weight_a, weight_b: (H, W) per-cam weights (cos²)
        band_half_width:    pixel half-width of the band (search range from
                            the Voronoi seam outward)
        threshold:          weight > threshold is "this cam covers"
    Returns:
        ma:   (H, W) uint8 0/255
        mb:   (H, W) uint8 0/255
        band: (H, W) bool — True inside the seam-search band (both masks
              True here)
    """
    cover_a = weight_a > threshold
    cover_b = weight_b > threshold
    overlap = cover_a & cover_b
    # Side decision based on which cos² weight is larger (the Voronoi rule).
    a_wins = weight_a >= weight_b  # ties go to A; arbitrary
    a_strict = overlap & a_wins
    b_strict = overlap & (~a_wins)

    # Distance transform: per-pixel distance to the OTHER side. This is the
    # right way to grow the band along the curved seam shape, not just along
    # cols. We dilate by band_half_width on each side to make the union "band".
    # In numpy, cv2.distanceTransform of (~side) gives distance to the side.
    H, W = weight_a.shape
    band = np.zeros((H, W), dtype=bool)
    if overlap.any():
        # Distance from "b_strict" within overlap = how far into "A side" we are
        a_only = (a_strict & ~b_strict).astype(np.uint8)
        b_only = (b_strict & ~a_strict).astype(np.uint8)
        # cv2.distanceTransform: zero pixels at value 0; positive distances
        # outward into the value-1 region. We want distance from the
        # opposing side, so build the inverted mask.
        dist_from_b = cv2.distanceTransform((1 - b_only).astype(np.uint8), cv2.DIST_L2, 3)
        dist_from_a = cv2.distanceTransform((1 - a_only).astype(np.uint8), cv2.DIST_L2, 3)
        band = (dist_from_b <= band_half_width) & (dist_from_a <= band_half_width)
        band = band & overlap

    # Build the seed-region masks for graph-cut:
    #   ma = 255 on (A side outside band) ∪ (inside band) ∪ (only-A region)
    #   mb = 255 on (B side outside band) ∪ (inside band) ∪ (only-B region)
    only_a = cover_a & ~cover_b
    only_b = cover_b & ~cover_a
    ma = (only_a | band | (a_strict & ~band)).astype(np.uint8) * 255
    mb = (only_b | band | (b_strict & ~band)).astype(np.uint8) * 255
    return ma, mb, band


def find_smart_seam_per_pair(
    slab_a: np.ndarray, weight_a: np.ndarray,
    slab_b: np.ndarray, weight_b: np.ndarray,
    threshold: float = 1e-6, pad: int = 2,
    cost_type: str = "COST_COLOR_GRAD",
    band_half_width: int = 30,
) -> tuple[np.ndarray, np.ndarray]:
    """Graph-cut seam for one adjacent ring pair.

    Args:
        slab_a, slab_b:   (H, W, 3) RGB slabs in [0, 255] (any float/uint dtype)
        weight_a, weight_b: (H, W) per-cam coverage / cos² weight
        threshold:        global coverage threshold (defines what counts as
                          "this cam covers")
        pad:              padding for the overlap bbox
        cost_type:        "COST_COLOR" or "COST_COLOR_GRAD" — passed to
                          cv2.detail.GraphCutSeamFinder. COLOR_GRAD considers
                          both intensity and gradient mismatch (slightly slower
                          but more robust at object edges).
        band_half_width:  pixel half-width (perpendicular to the cos²-Voronoi
                          seam) of the band that graph-cut can route inside.
                          Smaller → faster but less flexibility to dodge
                          object boundaries; larger → slower but the seam
                          can deviate further. 30 px ≈ 5° of azimuth at
                          2048×4096, plenty for most object-edge avoidance.
                          See `_seam_band_around_voronoi` for why this
                          narrowing is needed (cv2 maxflow scales quadratically
                          in overlap area).

    Returns:
        assign:   (H, W) int8 in {0=A, 1=B, -1=outside overlap-or-band}
        overlap:  (H, W) bool — pixels where BOTH cams cover (the FULL overlap,
                  by `threshold`; the band is a subset of this)
    """
    if slab_a.shape != slab_b.shape:
        raise ValueError(f"slab shape mismatch: {slab_a.shape} vs {slab_b.shape}")
    if weight_a.shape != weight_b.shape or weight_a.shape != slab_a.shape[:2]:
        raise ValueError("weight shape mismatch with slab")

    H, W = slab_a.shape[:2]
    assign = np.full((H, W), -1, dtype=np.int8)
    overlap_full = (weight_a > threshold) & (weight_b > threshold)
    if not overlap_full.any():
        return assign, overlap_full

    bbox_info = _overlap_bbox_with_wrap(weight_a, weight_b, pad=pad, threshold=threshold)
    bbox, wraps = bbox_info
    if bbox is None:
        return assign, overlap_full
    v0_full, v1_full, u0_full, u1_full = bbox

    # If wrapped, roll input by W//2 so the overlap is contiguous in cols.
    shift = W // 2 if wraps else 0
    if wraps:
        wa_full = np.roll(weight_a, shift, axis=1)[v0_full:v1_full, u0_full:u1_full]
        wb_full = np.roll(weight_b, shift, axis=1)[v0_full:v1_full, u0_full:u1_full]
    else:
        wa_full = weight_a[v0_full:v1_full, u0_full:u1_full]
        wb_full = weight_b[v0_full:v1_full, u0_full:u1_full]

    # Build the narrow-band coverage masks on the full overlap bbox, then
    # crop again to just the band's bounding box. This is the key perf trick
    # (see _seam_band_around_voronoi docstring): graph-cut over the full
    # overlap bbox is O(area²); over the band it's roughly O(area).
    _, _, band_in_overlap = _seam_band_around_voronoi(
        wa_full.astype(np.float32), wb_full.astype(np.float32),
        band_half_width=band_half_width, threshold=threshold,
    )
    if not band_in_overlap.any():
        return assign, overlap_full

    # Sub-bbox around just the band (+ a small pad for graph-cut seed margin).
    band_pad = max(2, pad)
    rows_b = band_in_overlap.any(axis=1)
    cols_b = band_in_overlap.any(axis=0)
    v0_loc = max(0, int(np.argmax(rows_b)) - band_pad)
    v1_loc = min(wa_full.shape[0], wa_full.shape[0] - int(np.argmax(rows_b[::-1])) + band_pad)
    u0_loc = max(0, int(np.argmax(cols_b)) - band_pad)
    u1_loc = min(wa_full.shape[1], wa_full.shape[1] - int(np.argmax(cols_b[::-1])) + band_pad)

    if wraps:
        sa = np.roll(slab_a, shift, axis=1)[v0_full + v0_loc:v0_full + v1_loc,
                                              u0_full + u0_loc:u0_full + u1_loc].astype(np.float32)
        sb = np.roll(slab_b, shift, axis=1)[v0_full + v0_loc:v0_full + v1_loc,
                                              u0_full + u0_loc:u0_full + u1_loc].astype(np.float32)
        wa = np.roll(weight_a, shift, axis=1)[v0_full + v0_loc:v0_full + v1_loc,
                                                u0_full + u0_loc:u0_full + u1_loc]
        wb = np.roll(weight_b, shift, axis=1)[v0_full + v0_loc:v0_full + v1_loc,
                                                u0_full + u0_loc:u0_full + u1_loc]
    else:
        sa = slab_a[v0_full + v0_loc:v0_full + v1_loc, u0_full + u0_loc:u0_full + u1_loc].astype(np.float32)
        sb = slab_b[v0_full + v0_loc:v0_full + v1_loc, u0_full + u0_loc:u0_full + u1_loc].astype(np.float32)
        wa = wa_full[v0_loc:v1_loc, u0_loc:u1_loc]
        wb = wb_full[v0_loc:v1_loc, u0_loc:u1_loc]

    # Track the band bbox origin for writing the assignment back below.
    v0 = v0_full + v0_loc
    u0 = u0_full + u0_loc

    # Recompute the narrow-band masks on the cropped (band-bbox) region.
    ma, mb, band_local = _seam_band_around_voronoi(
        wa.astype(np.float32), wb.astype(np.float32),
        band_half_width=band_half_width, threshold=threshold,
    )
    if not band_local.any():
        return assign, overlap_full

    # --- Try cv2.detail.GraphCutSeamFinder first ----------------------------
    use_gc = hasattr(cv2, "detail") and hasattr(cv2.detail, "GraphCutSeamFinder")
    label_local: np.ndarray
    if use_gc:
        try:
            cost_arg = cost_type if cost_type in ("COST_COLOR", "COST_COLOR_GRAD") else "COST_COLOR_GRAD"
            finder = cv2.detail.GraphCutSeamFinder(cost_arg)
            # `find` modifies masks in place AND returns them; copies to be safe
            ma_in = ma.copy()
            mb_in = mb.copy()
            result = finder.find([sa, sb], [(0, 0), (0, 0)], [ma_in, mb_in])
            r0, r1 = result
            ra = r0.get() if isinstance(r0, cv2.UMat) else np.asarray(r0)
            rb = r1.get() if isinstance(r1, cv2.UMat) else np.asarray(r1)
            # GraphCutSeamFinder partitions the input mask region cleanly:
            # ra > 0 means "use cam A here", rb > 0 means "use cam B here".
            label_local = np.where(rb > 0, 1, 0).astype(np.int8)
        except Exception:
            # Fall back to DP if graph-cut blows up for any reason
            label_local = _seam_via_dijkstra(sa, sb, band_local)
    else:
        label_local = _seam_via_dijkstra(sa, sb, band_local)

    # Restrict the assignment override to the BAND region. Outside the band,
    # the choice between cam A and cam B is unambiguous (Voronoi rule, same
    # answer the baseline argmax will produce), so writing it here would be
    # redundant. Restricting to the band also avoids changing pixels far from
    # the seam (e.g. deep in cam A territory where label_local might be 0 or 1
    # arbitrarily based on the input mask edges).
    label_local_masked = np.where(band_local, label_local, -1).astype(np.int8)

    # Map back to full ERP coords, undoing the wrap roll if any.
    h_b, w_b = label_local_masked.shape
    if not wraps:
        assign[v0:v0 + h_b, u0:u0 + w_b] = label_local_masked
        overlap = overlap_full
    else:
        # Embed into a rolled full-W array, then roll back.
        tmp = np.full((H, W), -1, dtype=np.int8)
        tmp[v0:v0 + h_b, u0:u0 + w_b] = label_local_masked
        assign = np.roll(tmp, -shift, axis=1)
        overlap = overlap_full  # unchanged in non-rolled coords
    return assign, overlap


def _seam_via_dijkstra(
    sa: np.ndarray, sb: np.ndarray, overlap: np.ndarray,
) -> np.ndarray:
    """Build per-pixel mismatch cost then call _dijkstra_seam_fallback."""
    diff = np.abs(sa.astype(np.float32) - sb.astype(np.float32)).mean(axis=2)
    # Add gradient term to penalize cutting across edges where the two cams
    # agree on edge presence but disagree on position.
    gsa = cv2.cvtColor(sa.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    gsb = cv2.cvtColor(sb.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    gxa = cv2.Sobel(gsa, cv2.CV_32F, 1, 0, ksize=3)
    gya = cv2.Sobel(gsa, cv2.CV_32F, 0, 1, ksize=3)
    gxb = cv2.Sobel(gsb, cv2.CV_32F, 1, 0, ksize=3)
    gyb = cv2.Sobel(gsb, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt((gxa - gxb) ** 2 + (gya - gyb) ** 2)
    cost = diff / 255.0 + 0.3 * (mag / max(1.0, float(mag.max())))
    return _dijkstra_seam_fallback(cost.astype(np.float32), overlap)


# ---------------------------------------------------------------------------
# L1': hard select with graph-cut routing
# ---------------------------------------------------------------------------


def hard_select_with_graphcut(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    pairs: list[tuple[int, int]] | None = None,
    cost_type: str = "COST_COLOR_GRAD",
) -> np.ndarray:
    """Drop-in replacement for `hard_select` using per-pair graph-cut seams.

    Steps:
      1. Compute baseline argmax over cos² weights (same as hard_select).
      2. For each adjacent pair (i, j), run graph-cut over their overlap and
         override the argmax inside that overlap with the cut assignment.
         Pairs are processed in RING_PAIRS order; the back-seam pair is last
         so it has the final word on rear-half assignments.
      3. Compose the final ERP by taking the selected cam at each pixel.

    Args:
        slabs:   list of N (H, W, 3) RGB slabs
        weights: list of N (H, W) cos² weight maps
        pairs:   list of (i, j) cam-pair indices. Default: RING_PAIRS
                 (7 pairs incl. back seam).
        cost_type: cv2.detail.GraphCutSeamFinder cost mode.

    Returns:
        uint8 (H, W, 3) ERP image.
    """
    n = len(slabs)
    if pairs is None:
        pairs = RING_PAIRS
    H, W = slabs[0].shape[:2]

    # 1. Baseline argmax over cos² (same as hard_hdr_of.hard_select).
    w_stack = np.stack(weights, axis=0)  # (N, H, W)
    argmax = w_stack.argmax(axis=0).astype(np.int32)  # (H, W)
    valid = w_stack.max(axis=0) > 0

    # 2. For each pair, run graph-cut seam and override the argmax in overlap.
    for (i, j) in pairs:
        assign, overlap = find_smart_seam_per_pair(
            slabs[i], weights[i], slabs[j], weights[j],
            cost_type=cost_type,
        )
        # In overlap pixels:
        #   assign == 0 -> use cam i
        #   assign == 1 -> use cam j
        # Outside the pair's overlap (assign == -1), leave argmax untouched.
        ov = overlap & (assign >= 0)
        if not ov.any():
            continue
        argmax = np.where(ov & (assign == 0), i, argmax)
        argmax = np.where(ov & (assign == 1), j, argmax)

    # 3. Gather the picked slab values via take_along_axis.
    rgb_stack = np.stack(slabs, axis=0).astype(np.float32)  # (N, H, W, 3)
    idx = argmax[None, ..., None]                          # (1, H, W, 1)
    picked = np.take_along_axis(rgb_stack, idx, axis=0)[0]  # (H, W, 3)
    return np.where(valid[..., None], picked, 0).astype(np.uint8)


# ---------------------------------------------------------------------------
# Combined pipeline
# ---------------------------------------------------------------------------


def blend_hard_hdr_of_graphcut(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    apply_of: bool = True,
    cost_type: str = "COST_COLOR_GRAD",
) -> np.ndarray:
    """Full pipeline: L2 HDR → L3 OF → L1' hard_select_with_graphcut.

    Same operation order as `blend_hard_hdr_of` (HDR before OF so flow
    doesn't lock onto brightness mismatches, then L1 picks last), only the
    final pick step is upgraded from raw cos² argmax to graph-cut routed
    seams.

    Args:
        slabs:   list of N (H, W, 3) RGB slabs from project step.
        weights: list of N (H, W) cos² weight maps from project step.
        apply_of: if False, skips L3 OF (faster; slightly worse seams).
        cost_type: cost mode for cv2.detail.GraphCutSeamFinder.

    Returns:
        uint8 (H, W, 3) ERP image.
    """
    # L2 HDR
    gains = compute_hdr_gains(slabs, weights)
    slabs_hdr = apply_hdr(slabs, gains)
    # L3 OF chain warp on HDR-corrected slabs
    if apply_of:
        slabs_warp, weights_warp = of_chain_warp(slabs_hdr, weights)
    else:
        slabs_warp, weights_warp = slabs_hdr, weights
    # L1' hard select with graph-cut seam routing
    return hard_select_with_graphcut(
        slabs_warp, weights_warp, cost_type=cost_type,
    )
