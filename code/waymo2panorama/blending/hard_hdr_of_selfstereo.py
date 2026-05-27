"""
Self-stereo depth derivation + N1 re-projection (L1+L2+depth pipeline variant).

Drop-in alternative to ``hard_hdr_of`` (the L1 hard_select + L2 joint HDR +
L3 Farneback OF chain warp pipeline). Replaces L3 OF (2D pixel warp) with a
geometrically principled step:

  L3' self-stereo  : for every adjacent ring-cam pair (A, B), use the two
                     legacy ERP slabs as a STEREO pair. Compute dense Farneback
                     OF in the overlap, then invert the math to recover the
                     per-ERP-pixel 3D depth via two-view triangulation in
                     ego frame. Combine all 7 pair-depth maps into a single
                     global ERP depth map (fallback ``FAR_DEPTH_M = 1000m``
                     outside overlap — see note below on why NOT +inf).
                     Then re-project all cams via ``render_camera_to_erp(
                     convergence_distance_m=depth_map)`` (N1 mode). Both cams
                     in each overlap now sample the SAME 3D point, so their
                     ERP slabs naturally agree in overlap -> no doubled-feature
                     ghost AT THE OVERLAP CENTERS.

Order of operations: project legacy -> L2 HDR -> derive depth -> re-project
N1 -> hard select. Note we re-render after the depth solve; we do NOT also
apply L3 OF on top, because the depth solve already addresses what L3 OF
was hacking around (cam-pair disagreement in overlap).

WHY THIS MIGHT WORK WHERE LIDAR / DA-V2 DIDN'T:

  N1 Phase C (LiDAR sparse) and Phase D (Depth Anything V2 dense) both
  failed to remove the BMW double-wheel ghost (see N1_AUTONOMOUS_RUN_SUMMARY).
  LiDAR is sparse on smooth car surfaces; DA-V2 is a single-view CNN guess
  that has no notion of the OTHER camera.

  Self-stereo USES BOTH CAMS DIRECTLY: the depth that explains their visible
  disagreement IS exactly the depth that makes them agree once we re-project
  with it. By construction, the derived depth solves the cam-pair overlap
  alignment problem.

  Mathematical relation to L3 OF: L3 OF warps slab_b by 2D pixel displacement
  to overlay slab_a (a 2D hack). Self-stereo recovers the 3D depth that
  CAUSES that 2D displacement, then re-renders both cams from scratch with
  that depth. The two should produce VISUALLY SIMILAR results in pure
  overlap, but self-stereo is more geometrically principled (no warp
  artifacts in slab_b non-overlap, e.g., near cam-FOV boundaries).

ASSUMPTIONS / CAVEATS:

  * Approximation: cam A's legacy ERP places content at ERP ray direction
    ``d_ego(u, v)`` measured FROM CAM A's optical center (not from ego
    origin). When we derive depth and write it to ``depth_map[u, v]``,
    we are treating the ERP ray direction as if measured from ego origin
    (N1 mode's convention). For depth >> |t_cam| (~1.5 m forward), the two
    are within ~3 degrees -- acceptable. Near-field (<5 m) introduces a
    rebinning error of ~30 px @ 4096 width, but both cams pick it up
    identically under N1, so the alignment still holds; only the depth
    placement is slightly off.

  * Degenerate triangulation: rays parallel (depth -> infinity) or flow
    too small to be reliable. Falls back to ``FAR_DEPTH_M`` (1000 m)
    -> ~legacy projection at that pixel.

  * No sub-pixel disparity refinement; OF resolution sets depth precision.

EMPIRICAL FINDING (2026-05-26, BMW anchor 02a00399 / pi3_cache anchor_000):

  Self-stereo IS recovering plausible depth: median 2.59 m in the
  front_center+front_right overlap (matches the BMW at ~3 m), 43 m at the
  back seam (matches the receding buildings).

  HOWEVER, plugging that depth into N1 mode reproduces the FUNDAMENTAL
  cam-FOV-gap failure documented in N1 Phase A: at near-field depths
  (~3 m), each cam's projection cone covers only a small ERP region, and
  adjacent ERP pixels with depth ~3 m vs ~1000 m project to wildly
  different cam-image coords -> large black "missing data" blobs in the
  re-projected BMW (~37% of the BMW area is lost vs legacy projection).

  See ``deliverables/selfstereo/bmw_compare.png``: the BMW in pipeline
  (b) has the right portion of its body cut to black, even though (a)
  L3 OF shows the BMW intact (just with the doubled-wheel ghost).

  Conclusion: self-stereo derivation is mathematically sound and
  CONFIRMS the L3 OF approach (both achieve cam-pair convergence in
  overlap). But applying it via N1 mode for FINAL rendering inherits
  N1's cam-FOV-gap pathology — the same issue that killed N1 Phase
  C (LiDAR) and Phase D (DA-V2) for near-field objects.

  This is the "view-synthesis problem, not alignment problem" finding
  re-confirmed via a completely different depth source.

All ops are basic CV (cv2 + numpy). Runtime ~ same as ``hard_hdr_of``
(dominated by the per-pair OF call, which we share).
"""
from __future__ import annotations

import cv2
import numpy as np

from waymo2panorama.blending.hard_hdr_of import (
    CCW, CW, RING_PAIRS,
    apply_hdr, compute_hdr_gains, hard_select,
)
from waymo2panorama.projection.sphere_projection import render_camera_to_erp

# "Far / infinity" depth value used wherever self-stereo can't recover depth.
# 1000m matches N1 Phase C (lidar_to_erp_depth.fill_far_m). FINITE on purpose
# -- N1 mode's `r * d_ego - t_cam` produces NaN when r is inf.
FAR_DEPTH_M = 1000.0


# ---------------------------------------------------------------------------
# ERP <-> ray geometry helpers (mirror sphere_projection.py conventions)
# ---------------------------------------------------------------------------

def _erp_ray_grid(h_erp: int, w_erp: int) -> np.ndarray:
    """Build the (H, W, 3) unit-ray-in-ego grid for a given ERP shape.

    Mirrors ``render_camera_to_erp``'s convention exactly:
      theta = pi - (u + 0.5)/W * 2pi
      phi   = pi/2 - (v + 0.5)/H * pi
      d_ego = (cos(phi)cos(theta), cos(phi)sin(theta), sin(phi))
    """
    u_idx = np.arange(w_erp, dtype=np.float64)
    v_idx = np.arange(h_erp, dtype=np.float64)
    uu, vv = np.meshgrid(u_idx, v_idx)
    theta = np.pi - (uu + 0.5) / w_erp * (2.0 * np.pi)
    phi = (np.pi / 2.0) - (vv + 0.5) / h_erp * np.pi
    cos_phi = np.cos(phi)
    d_ego = np.stack(
        [cos_phi * np.cos(theta), cos_phi * np.sin(theta), np.sin(phi)],
        axis=-1,
    )
    return d_ego  # (H, W, 3)


def _sample_ray_grid(d_ego_grid: np.ndarray, u_f: np.ndarray, v_f: np.ndarray) -> np.ndarray:
    """Bilinearly sample (H, W, 3) ray grid at fractional (u_f, v_f) coords.

    Falls back to nearest at out-of-bounds. Output shape: matches u_f/v_f + (3,).
    """
    h, w, _ = d_ego_grid.shape
    # Per-channel cv2.remap then stack -- cv2 supports up to 4 channels per remap call
    map_u = u_f.astype(np.float32)
    map_v = v_f.astype(np.float32)
    out = cv2.remap(
        d_ego_grid.astype(np.float32),
        map_u, map_v,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    # Re-normalize (interpolation breaks unit length slightly).
    norm = np.linalg.norm(out, axis=-1, keepdims=True)
    out = np.where(norm > 1e-9, out / norm, out)
    return out


# ---------------------------------------------------------------------------
# Triangulation: 2-view midpoint, fully vectorized
# ---------------------------------------------------------------------------

def _triangulate_midpoint(
    t_a: np.ndarray, d_a: np.ndarray,
    t_b: np.ndarray, d_b: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Closest-point (mid-point) triangulation of two skew rays per pixel.

    Each cam contributes a ray: ``r_a(s) = t_a + s * d_a``, same for b.
    The closest points are found by solving the 2x2 linear system arising
    from ``d/ds [|r_a(s_a) - r_b(s_b)|^2] = 0``:

        [ d_a.d_a   -d_a.d_b ] [s_a]   [ d_a.(t_b - t_a) ]
        [ d_b.d_a   -d_b.d_b ] [s_b] = [ d_b.(t_b - t_a) ]

    Mid-point of the segment is the triangulated 3D position.

    Args:
        t_a: (3,) cam A center in ego frame.
        d_a: (H, W, 3) per-pixel unit ray direction in ego frame.
        t_b: (3,) cam B center in ego frame.
        d_b: (H, W, 3) per-pixel unit ray direction in ego frame.

    Returns:
        P_midpoint: (H, W, 3) triangulated 3D point in ego frame.
        valid_mask: (H, W) bool — True where triangulation is well-conditioned
                    and both s_a, s_b are positive (point in front of both cams).
    """
    # Per-pixel dot products
    daa = np.sum(d_a * d_a, axis=-1)
    dab = np.sum(d_a * d_b, axis=-1)
    dbb = np.sum(d_b * d_b, axis=-1)
    delta = t_b - t_a                                   # (3,)
    rhs_a = np.einsum('hwk,k->hw', d_a, delta)
    rhs_b = np.einsum('hwk,k->hw', d_b, delta)
    # 2x2 system, det = -daa*dbb + dab*dab = -(daa*dbb - dab^2)
    det = -(daa * dbb - dab * dab)
    safe_det = np.where(np.abs(det) > 1e-9, det, 1.0)
    s_a = (-dbb * rhs_a + dab * rhs_b) / safe_det
    s_b = (-dab * rhs_a + daa * rhs_b) / safe_det
    well_cond = np.abs(det) > 1e-6
    in_front = (s_a > 0) & (s_b > 0)
    valid = well_cond & in_front

    P_a = t_a + s_a[..., None] * d_a
    P_b = t_b + s_b[..., None] * d_b
    P = 0.5 * (P_a + P_b)
    return P, valid


# ---------------------------------------------------------------------------
# Per-pair depth from self-stereo
# ---------------------------------------------------------------------------

def derive_depth_from_of(
    slab_a: np.ndarray, weight_a: np.ndarray,
    slab_b: np.ndarray, weight_b: np.ndarray,
    t_ego_cam_a: np.ndarray, t_ego_cam_b: np.ndarray,
    erp_hw: tuple[int, int],
    of_winsize: int = 31, of_levels: int = 4, of_iter: int = 5,
    smooth_sigma: float = 5.0,
    min_overlap_px: int = 100,
    depth_clip_m: tuple[float, float] = (1.0, 1000.0),
) -> tuple[np.ndarray, np.ndarray]:
    """Use cam pair (A, B) as a stereo pair; derive per-ERP-pixel depth in overlap.

    Steps:
      1. Run Farneback OF in the (a, b) overlap (same OF as L3, same params).
      2. For each overlap pixel (u, v) in slab_a, identify the matching pixel
         (u - flow_x, v - flow_y) in slab_b. (Standard cv2 convention:
         ``flow = calcOpticalFlowFarneback(prev=slab_b, next=slab_a)`` gives
         flow such that gb_pixel(x, y) -> ga_pixel(x + flow_x, y + flow_y),
         so to find the b-pixel matching a-pixel (u, v), subtract the flow
         evaluated at the destination — an approximation valid for smooth
         flow.)
      3. Convert both pixel coords to unit ego rays via the ERP geometry
         (same convention as ``render_camera_to_erp``).
      4. Triangulate (midpoint of skew-ray closest points) in ego frame.
      5. Depth = ``|P_midpoint|`` (distance from ego origin -- N1 convention).
      6. Clip to plausible range; reject parallel-ray degeneracies.

    Args:
        slab_a, slab_b: (H_erp, W_erp, 3) float32 RGB ERP slabs from legacy
                        projection (convergence_distance_m=None).
        weight_a, weight_b: (H_erp, W_erp) float32 cos^2 weight maps from the
                            same legacy projection.
        t_ego_cam_a, t_ego_cam_b: (3,) cam A and B optical centers in ego frame.
        erp_hw: (H_erp, W_erp).
        of_winsize, of_levels, of_iter, smooth_sigma: Farneback OF params.
                            Defaults match ``hard_hdr_of.warp_pair_with_of``.
        min_overlap_px: skip if overlap has fewer pixels (likely back seam in
                        a tiny ERP).
        depth_clip_m: (min, max) plausible depth in meters. Values outside ->
                      fallback ``inf``.

    Returns:
        depth_map: (H_erp, W_erp) float32, distance-from-ego-origin in meters.
                   Outside the overlap (or where triangulation failed):
                   ``+inf`` (N1 will then degenerate to ~legacy at those px).
        valid_mask: (H_erp, W_erp) bool, True where depth_map has a real value.
    """
    H, W = erp_hw
    overlap = (weight_a > 1e-6) & (weight_b > 1e-6)
    # NOTE: we use a large but FINITE depth (`FAR_DEPTH_M`) for unobserved
    # pixels, NOT +inf. The N1 path in `render_camera_to_erp` multiplies
    # `r * d_ego` then subtracts `t_cam`; `inf - finite` produces NaN
    # downstream and the projection silently drops the pixel. See N1 Phase C
    # `lidar_to_erp_depth.fill_far_m = 1000.0` for the same convention.
    depth_map = np.full((H, W), FAR_DEPTH_M, dtype=np.float32)
    valid_mask = np.zeros((H, W), dtype=bool)
    if int(overlap.sum()) < min_overlap_px:
        return depth_map, valid_mask

    # --- 1. Farneback OF on overlap-masked gray ---
    ga = cv2.cvtColor(slab_a.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor(slab_b.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    ga_m = np.where(overlap, ga, 0).astype(np.uint8)
    gb_m = np.where(overlap, gb, 0).astype(np.uint8)
    flow = cv2.calcOpticalFlowFarneback(
        gb_m, ga_m, flow=None,
        pyr_scale=0.5, levels=of_levels, winsize=of_winsize,
        iterations=of_iter, poly_n=7, poly_sigma=1.5, flags=0,
    )
    flow_x = flow[..., 0]
    flow_y = flow[..., 1]
    # Smooth & re-normalize within overlap (same as L3) to reduce noise.
    overlap_f = overlap.astype(np.float32)
    flow_x = flow_x * overlap_f
    flow_y = flow_y * overlap_f
    if smooth_sigma > 0:
        flow_x = cv2.GaussianBlur(flow_x, (0, 0), smooth_sigma)
        flow_y = cv2.GaussianBlur(flow_y, (0, 0), smooth_sigma)
        m_smooth = cv2.GaussianBlur(overlap_f, (0, 0), smooth_sigma)
        m_smooth = np.where(m_smooth > 1e-3, m_smooth, 1.0)
        flow_x = flow_x / m_smooth
        flow_y = flow_y / m_smooth

    # --- 2. Build ego-ray grids for cam A's ERP pixel & cam B's matched pixel ---
    d_ego_grid = _erp_ray_grid(H, W)                   # (H, W, 3)
    # Cam A's per-pixel ray = d_ego(u, v) (just the canonical grid).
    d_a = d_ego_grid                                    # (H, W, 3)
    # Cam B's matched pixel for slab_a (u, v) is (u - flow_x, v - flow_y).
    u_grid, v_grid = np.meshgrid(np.arange(W, dtype=np.float32),
                                  np.arange(H, dtype=np.float32))
    u_b = u_grid - flow_x
    v_b = v_grid - flow_y
    # Wrap u_b for ERP horizontal periodicity (rare but possible near seam).
    u_b = np.mod(u_b, float(W))
    v_b = np.clip(v_b, 0, H - 1)
    d_b = _sample_ray_grid(d_ego_grid, u_b, v_b)        # (H, W, 3)

    # --- 3. Triangulate per pixel ---
    P, tri_valid = _triangulate_midpoint(
        np.asarray(t_ego_cam_a, dtype=np.float64),
        d_a.astype(np.float64),
        np.asarray(t_ego_cam_b, dtype=np.float64),
        d_b.astype(np.float64),
    )

    # --- 4. Depth = distance from ego origin ---
    depth = np.linalg.norm(P, axis=-1).astype(np.float32)
    d_min, d_max = float(depth_clip_m[0]), float(depth_clip_m[1])
    in_range = (depth >= d_min) & (depth <= d_max)

    # --- 5. Reject pixels where flow magnitude is tiny (sky / textureless)
    #         -- triangulation is unreliable when displacement < ~0.5 px.
    flow_mag = np.sqrt(flow_x ** 2 + flow_y ** 2)
    flow_reliable = flow_mag > 0.5

    valid = overlap & tri_valid & in_range & flow_reliable
    depth_map = np.where(valid, depth, FAR_DEPTH_M).astype(np.float32)
    valid_mask = valid
    return depth_map, valid_mask


# ---------------------------------------------------------------------------
# Combine per-pair depths into a single global ERP depth
# ---------------------------------------------------------------------------

def combine_pair_depths(
    pair_depths: list[tuple[np.ndarray, np.ndarray]],
    erp_hw: tuple[int, int],
    smooth_sigma_px: float = 8.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Combine N (depth, valid_mask) pairs into one global depth map.

    Combination rule: at each pixel, average the per-pair depths in inverse
    space (i.e., average inverse depth = disparity), which is robust to
    a single bad outlier pulling things to infinity. Pixels with no valid
    pair -> fallback ``inf`` (legacy projection at that pixel).

    Final smoothing keeps the depth field locally consistent (cams ought to
    transition gradually). Smoothing is done in disparity (1/depth) space so
    we don't have to special-case infinity.

    Args:
        pair_depths: list of (depth_map, valid_mask) from ``derive_depth_from_of``.
        erp_hw: (H, W) for output shape.
        smooth_sigma_px: gaussian smoothing in disparity space (set 0 to skip).

    Returns:
        global_depth: (H, W) float32, in meters. ``+inf`` where no valid pair.
        coverage: (H, W) bool, True where at least one pair contributed.
    """
    H, W = erp_hw
    inv_sum = np.zeros((H, W), dtype=np.float64)
    cnt = np.zeros((H, W), dtype=np.float64)
    coverage = np.zeros((H, W), dtype=bool)
    for depth, valid in pair_depths:
        if depth is None:
            continue
        finite = valid & np.isfinite(depth) & (depth > 0)
        coverage = coverage | finite
        # inverse depth (disparity) — guard division by zero / inf
        inv_d = np.where(finite, 1.0 / np.where(depth > 1e-6, depth, 1e-6), 0.0)
        inv_sum = inv_sum + inv_d
        cnt = cnt + finite.astype(np.float64)
    cnt_safe = np.where(cnt > 0, cnt, 1.0)
    inv_mean = inv_sum / cnt_safe
    inv_mean = np.where(cnt > 0, inv_mean, 0.0)  # 0 disparity = inf depth

    if smooth_sigma_px > 0:
        inv_mean_f = inv_mean.astype(np.float32)
        cov_f = coverage.astype(np.float32)
        inv_smooth = cv2.GaussianBlur(inv_mean_f * cov_f, (0, 0), smooth_sigma_px)
        m_smooth = cv2.GaussianBlur(cov_f, (0, 0), smooth_sigma_px)
        m_safe = np.where(m_smooth > 1e-3, m_smooth, 1.0)
        # Only update where coverage exists; outside, keep 0 (= inf depth).
        inv_mean = np.where(m_smooth > 1e-3, inv_smooth / m_safe, 0.0)

    # Where inverse depth is essentially zero (no coverage even after smoothing),
    # fall back to FAR_DEPTH_M (~= legacy projection). NOT +inf -- that breaks
    # N1 mode's `r * d_ego - t_cam` math (see FAR_DEPTH_M docstring).
    global_depth = np.where(
        inv_mean > 1e-9,
        1.0 / np.maximum(inv_mean, 1e-9),
        FAR_DEPTH_M,
    )
    global_depth = global_depth.astype(np.float32)
    # Belt & suspenders: any non-positive or non-finite -> FAR.
    bad = ~(np.isfinite(global_depth) & (global_depth > 0))
    global_depth = np.where(bad, FAR_DEPTH_M, global_depth)
    return global_depth, coverage


# ---------------------------------------------------------------------------
# Combined pipeline (matches blend_hard_hdr_of's signature shape)
# ---------------------------------------------------------------------------

def blend_hard_hdr_of_selfstereo(
    slabs: list[np.ndarray], weights: list[np.ndarray],
    frame,
    erp_hw: tuple[int, int],
    ego_masks: dict | None = None,
    smooth_sigma_px: float = 8.0,
    return_diagnostics: bool = False,
) -> np.ndarray | tuple[np.ndarray, dict]:
    """Full L1+L2+self-stereo-depth+N1-reproject pipeline.

    Pipeline:
      1. Use input ``slabs``/``weights`` (legacy projection) for OF inputs.
      2. L2 joint HDR (compute_hdr_gains + apply_hdr).
      3. For each of the 7 ring pairs, derive depth via self-stereo
         (Farneback OF -> triangulation).
      4. Combine into one global ERP depth map (inverse-depth mean +
         disparity-space smoothing).
      5. Re-project all 7 cams in N1 mode with the global depth map.
      6. Re-apply L2 HDR gains (same gains from step 2) on the re-projected
         slabs. (We don't recompute gains -- the photometric problem doesn't
         change with re-projection, only the geometry.)
      7. Hard select.

    Args:
        slabs: 7 legacy-projection (H, W, 3) ERP slabs.
        weights: 7 cos^2 weight maps.
        frame: the source ``FrameSample``; we re-read each cam's image + K +
               T_ego_cam for the N1 re-projection. (We need raw cam images
               again, not the ERP slabs.)
        erp_hw: (H, W).
        ego_masks: optional dict cam_name -> (H_src, W_src) uint8 mask, same
                   convention as ``stitch_one_frame``.
        smooth_sigma_px: smoothing for global depth field. 0 = none.
        return_diagnostics: if True, also return dict with depth maps,
                            coverage, per-pair stats.

    Returns:
        erp: (H, W, 3) uint8 hard-selected panorama.
        diagnostics (if requested): dict with keys:
            "global_depth_map": (H, W) float32, in meters (inf where unknown)
            "depth_coverage": (H, W) bool
            "pair_depths": list of per-pair (depth, valid) tuples
            "hdr_gains": list of 7 floats
    """
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7

    H, W = erp_hw

    # ---- L2 HDR ----
    gains = compute_hdr_gains(slabs, weights)
    slabs_hdr = apply_hdr(slabs, gains)

    # ---- Self-stereo depth per pair ----
    cam_names = list(RING_CAMS_7)
    pair_depths: list[tuple[np.ndarray, np.ndarray]] = []
    for (i, j) in RING_PAIRS:
        cam_a = cam_names[i]; cam_b = cam_names[j]
        t_a = np.asarray(frame.calibrations[cam_a].T_ego_cam[:3, 3], dtype=np.float64)
        t_b = np.asarray(frame.calibrations[cam_b].T_ego_cam[:3, 3], dtype=np.float64)
        depth_ij, valid_ij = derive_depth_from_of(
            slabs_hdr[i], weights[i], slabs_hdr[j], weights[j],
            t_a, t_b, erp_hw=erp_hw,
        )
        pair_depths.append((depth_ij, valid_ij))

    global_depth, coverage = combine_pair_depths(
        pair_depths, erp_hw=erp_hw, smooth_sigma_px=smooth_sigma_px,
    )

    # ---- Re-project all cams in N1 mode with global depth ----
    slabs_n1: list[np.ndarray] = []
    weights_n1: list[np.ndarray] = []
    for cam in cam_names:
        calib = frame.calibrations[cam]
        img = frame.images[cam]
        mask = ego_masks.get(cam) if ego_masks else None
        rgb, _alpha, w = render_camera_to_erp(
            image=img, K=calib.K, T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw, ego_mask=mask,
            convergence_distance_m=global_depth,
        )
        slabs_n1.append(rgb)
        weights_n1.append(w)

    # ---- Re-apply L2 HDR (same gains) to N1 slabs ----
    slabs_n1_hdr = apply_hdr(slabs_n1, gains)

    # ---- Hard select ----
    erp = hard_select(slabs_n1_hdr, weights_n1)

    if return_diagnostics:
        return erp, {
            "global_depth_map": global_depth,
            "depth_coverage": coverage,
            "pair_depths": pair_depths,
            "hdr_gains": list(gains),
        }
    return erp
