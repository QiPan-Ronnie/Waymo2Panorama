"""A2 — Sparse Stereo Displacement (parallax overlap fix candidate).

For each 3D point X in cam_a + cam_b's stereo .npz:
  - Compute where L1 sphere projection WOULD paint X for cam_a (and cam_b)
  - Compare to ideal ERP position based on X's true 3D ego direction
  - Per-cam displacement = ideal - L1_painted

Interpolate sparse per-point displacements into dense fields, then apply
to each cam's ERP slab via cv2.remap before passing to multiband_blend.

USAGE PATTERN (driver responsibility):
    slabs, weights = [render_camera_to_erp(...) for cam in cams]
    disp_fields = build_per_cam_displacement_fields(...)
    warped_slabs = [warp_erp_slab(slab, df) for slab, df in zip(slabs, disp_fields)]
    erp = multiband_blend(warped_slabs, weights, ...)

EXISTING CODE UNCHANGED: this module is purely additive. It does not modify
render_camera_to_erp, multiband_blend, or stitch_frame.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from scipy.interpolate import RBFInterpolator

from waymo2panorama.pipeline.lift_and_project import ego_points_to_erp_uv
from waymo2panorama.pipeline.option_b_reweight import (
    STEREO_NPZ_PTS_KEY,
    STEREO_NPZ_CAM_A_KEY,
    STEREO_NPZ_CAM_B_KEY,
)


def _compute_l1_erp_pixel_per_cam(
    pt_ego: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    erp_hw: tuple[int, int],
) -> np.ndarray:
    """Compute where L1 sphere projection paints a 3D ego point for one cam.

    L1 paints cam pixel content at the ERP location given by the ray from the
    cam to that pixel. For a 3D ego point X seen by cam at ego center t and
    rotation R_ego_cam:
      1. Cam-frame coords: X_cam = R_ego_cam.T @ (X - t)
      2. Cam pixel: (u_c, v_c) = K @ [X_cam[0]/X_cam[2], X_cam[1]/X_cam[2], 1]
      3. Back-project that pixel to ego ray: ray_ego = R_ego_cam @ inv(K) @ (u_c, v_c, 1)
      4. ERP location of ray_ego (using ego_points_to_erp_uv as if depth=1)

    For X at infinity, ray_ego direction = X direction → L1_uv == ideal_uv.
    For X near cam, ray_ego direction ≠ X direction (parallax) → L1_uv ≠ ideal_uv.

    Args:
        pt_ego: (3,) point in ego frame.
        K: (3, 3) cam intrinsics.
        T_ego_cam: (4, 4) ego-from-cam transform.
        erp_hw: (H_erp, W_erp).

    Returns:
        (2,) (u, v) ERP pixel where L1 paints this point.
    """
    R_ego_cam = T_ego_cam[:3, :3].astype(np.float64)
    t_ego_cam = T_ego_cam[:3, 3].astype(np.float64)
    R_cam_ego = R_ego_cam.T
    X_cam = R_cam_ego @ (np.asarray(pt_ego, dtype=np.float64) - t_ego_cam)
    z = X_cam[2]
    if abs(z) < 1e-9:
        return np.array([np.nan, np.nan], dtype=np.float64)
    K = np.asarray(K, dtype=np.float64)
    u_cam = K[0, 0] * X_cam[0] / z + K[0, 2]
    v_cam = K[1, 1] * X_cam[1] / z + K[1, 2]
    # Back-project to ego ray
    ray_cam = np.linalg.inv(K) @ np.array([u_cam, v_cam, 1.0])
    ray_ego = R_ego_cam @ ray_cam
    u_f, v_f, valid = ego_points_to_erp_uv(ray_ego.reshape(1, 3), erp_hw=erp_hw)
    return np.array([float(u_f[0]), float(v_f[0])], dtype=np.float64)


def _load_stereo_pair(path: Path) -> tuple[str, str, np.ndarray] | None:
    """Load (cam_a, cam_b, pts_3d_ego) from one stereo .npz file."""
    with np.load(path) as npz:
        if STEREO_NPZ_CAM_A_KEY not in npz.files or STEREO_NPZ_CAM_B_KEY not in npz.files:
            return None
        if STEREO_NPZ_PTS_KEY not in npz.files:
            return None
        cam_a = str(npz[STEREO_NPZ_CAM_A_KEY])
        cam_b = str(npz[STEREO_NPZ_CAM_B_KEY])
        pts = np.asarray(npz[STEREO_NPZ_PTS_KEY], dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3 or pts.shape[0] == 0:
        return None
    return cam_a, cam_b, pts


def _shortest_wrap_delta(u_target: float, u_src: float, W: int) -> float:
    """Shortest-path signed horizontal delta on a wrap-around ERP."""
    d = u_target - u_src
    if d > W / 2:
        d -= W
    elif d < -W / 2:
        d += W
    return d


def _midpoint_uv_wrap(uv_a: np.ndarray, uv_b: np.ndarray, W: int) -> np.ndarray:
    """Midpoint of two ERP positions, respecting horizontal wrap-around.

    The v-axis (latitude) is linear; midpoint is straightforward.
    The u-axis (longitude) wraps at W; midpoint is uv_a + 0.5 * shortest_delta(uv_a, uv_b).
    """
    half_du = _shortest_wrap_delta(uv_b[0], uv_a[0], W) * 0.5
    u_mid = (uv_a[0] + half_du) % W
    v_mid = 0.5 * (uv_a[1] + uv_b[1])
    return np.array([u_mid, v_mid], dtype=np.float64)


def build_per_cam_displacements_from_stereo(
    stereo_npz_paths,
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_names: list[str],
    erp_hw: tuple[int, int],
    target_mode: str = "ideal",
    min_parallax_px: float = 0.0,
) -> dict[str, list[tuple[np.ndarray, np.ndarray]]]:
    """Build sparse per-cam displacement vectors from cached stereo .npz files.

    target_mode controls where each pair's two cams are warped to:

      "ideal" (default, original A2 / WS4-D1): for each 3D point pt,
        target = ego_points_to_erp_uv(pt)  # depth-aware ERP location
        cam_X's delta = target - L1_projection_X
        Architectural risk: per-cam anchor lists built INDEPENDENTLY. If
        cam_b has zero anchors from its OTHER pairs, only this pair's
        anchors warp it — but other regions of cam_b's slab stay put,
        breaking alignment elsewhere. Decisive NEG (Stage 3 A.4-A.5 eval).

      "midpoint" (joint per-pair, Stage 3 Phase C): for each stereo pair
        (cam_a, cam_b) and 3D point pt:
          L1_uv_a = _compute_l1_erp_pixel_per_cam(pt, ..., cam_a)
          L1_uv_b = _compute_l1_erp_pixel_per_cam(pt, ..., cam_b)
          target  = wrap-aware midpoint(L1_uv_a, L1_uv_b)
          cam_a's delta = target - L1_uv_a
          cam_b's delta = target - L1_uv_b
        Both cams in the pair are forced to meet halfway. Symmetric, no
        depth required, no asymmetry from "one cam has anchors, other
        doesn't" because each pair-stereo contributes equally to both
        cams' anchor lists.

    Returns dict {cam_name: list of (anchor_uv, delta_uv) tuples}. Cams
    not appearing in any stereo pair get an empty list.
    """
    if target_mode not in ("ideal", "midpoint"):
        raise ValueError(f"target_mode must be 'ideal' or 'midpoint', got {target_mode!r}")
    if min_parallax_px < 0:
        raise ValueError(f"min_parallax_px must be >= 0, got {min_parallax_px}")
    cam_set = set(cam_names)
    W = erp_hw[1]
    out: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {c: [] for c in cam_names}
    for p in stereo_npz_paths:
        loaded = _load_stereo_pair(Path(p))
        if loaded is None:
            continue
        cam_a, cam_b, pts = loaded
        if cam_a not in cam_set or cam_b not in cam_set:
            continue
        for pt in pts:
            # Per-cam L1 sphere projections (used by both modes)
            l1_uv_a = _compute_l1_erp_pixel_per_cam(pt, cam_K[cam_a], cam_T_ego_cam[cam_a], erp_hw)
            l1_uv_b = _compute_l1_erp_pixel_per_cam(pt, cam_K[cam_b], cam_T_ego_cam[cam_b], erp_hw)
            if np.any(np.isnan(l1_uv_a)) or np.any(np.isnan(l1_uv_b)):
                continue

            # Adaptive filter: skip mild-parallax anchors. If the two cams already
            # project the 3D point to nearly the same ERP location, no warp needed
            # (and applying one introduces unnecessary lateral shifts that hurt
            # alignment in surrounding pixels — Stage 3 Phase C v1 finding on
            # anchors 90/150 where midpoint hurt Pearson vs ideal).
            if min_parallax_px > 0.0:
                du = _shortest_wrap_delta(l1_uv_b[0], l1_uv_a[0], W)
                dv = l1_uv_b[1] - l1_uv_a[1]
                parallax_px = float(np.hypot(du, dv))
                if parallax_px < min_parallax_px:
                    continue

            if target_mode == "ideal":
                # Depth-aware ERP location; same target for both cams in pair
                u_ideal, v_ideal, _ = ego_points_to_erp_uv(pt.reshape(1, 3), erp_hw=erp_hw)
                target_uv = np.array([float(u_ideal[0]), float(v_ideal[0])], dtype=np.float64)
            else:  # midpoint
                target_uv = _midpoint_uv_wrap(l1_uv_a, l1_uv_b, W)

            for cam, l1_uv in [(cam_a, l1_uv_a), (cam_b, l1_uv_b)]:
                delta_u = _shortest_wrap_delta(target_uv[0], l1_uv[0], W)
                delta_uv = np.array([delta_u, target_uv[1] - l1_uv[1]], dtype=np.float64)
                # Anchor at DESTINATION (target_uv); delta tells the warp
                # what to source-shift to put l1_uv content at target_uv.
                # warp_erp_slab_by_displacement does dst[u, v] = src[u - du, v - dv],
                # so dst[target_uv] = src[target_uv - (target_uv - l1_uv)] = src[l1_uv]. ✓
                out[cam].append((target_uv.copy(), delta_uv))
    return out


def interpolate_dense_displacement_field(
    sparse_anchors: list[tuple[np.ndarray, np.ndarray]],
    erp_hw: tuple[int, int],
    regularization: float = 1.0,
    kernel: str = "thin_plate_spline",
    gaussian_width_px: float | None = None,
) -> np.ndarray:
    """Interpolate sparse {(anchor_uv, delta_uv)} into a dense (H, W, 2) field.

    Uses scipy.interpolate.RBFInterpolator. Two kernel choices:

      "thin_plate_spline" (default): smooth, globally-interpolating field.
        Reproduces anchor deltas exactly at anchor locations and smoothly
        varies in between. **BUT extrapolates globally** — far-from-anchor
        regions get non-zero displacement, polluting non-parallax zones.

      "gaussian": locally-decaying field. With explicit `gaussian_width_px`,
        the field strength decays exponentially with distance from each anchor.
        Pixels far from any anchor get ~zero displacement. Use this when
        you want anchor effects to be SPATIALLY LOCAL — Phase C v2 finding:
        TPS smoothing leaks anchor deltas into already-aligned regions,
        hurting metric. gaussian + degree=-1 keeps the field localized.

    gaussian_width_px (used when kernel='gaussian'):
        Sigma-like decay scale in pixels. Field magnitude at distance d
        from an anchor: ~exp(-(d/width)^2). Default = 5% of min(H, W) (i.e.
        51 px on 1024x2048 ERP). Smaller width = tighter localization.

    `regularization` (smoothing param) trades off exact-fit (=0) vs smooth-
    overall (>>0). Higher regularization is more robust when sparse anchors
    are noisy but loses anchor exactness.

    Empty sparse_anchors → all-zero field (no displacement).
    """
    H, W = erp_hw
    if len(sparse_anchors) == 0:
        return np.zeros((H, W, 2), dtype=np.float32)
    anchors_xy = np.array([a[0] for a in sparse_anchors], dtype=np.float64)
    deltas = np.array([a[1] for a in sparse_anchors], dtype=np.float64)

    rbf_kwargs: dict = {"kernel": kernel, "smoothing": float(regularization)}
    # TPS needs ≥3 anchors in 2D; fallback to gaussian if too few or if
    # caller explicitly chose gaussian.
    use_gaussian = (kernel == "gaussian") or (
        anchors_xy.shape[0] < 3 and kernel == "thin_plate_spline"
    )
    if use_gaussian:
        rbf_kwargs["kernel"] = "gaussian"
        # scipy gaussian: exp(-(epsilon*r)^2). Width = 1/epsilon.
        # degree=-1 disables polynomial tail so the field truly decays.
        if gaussian_width_px is None:
            width_px = max(1.0, 0.05 * float(min(H, W)))
        else:
            width_px = max(1.0, float(gaussian_width_px))
        rbf_kwargs["epsilon"] = 1.0 / width_px
        rbf_kwargs["degree"] = -1
    rbf = RBFInterpolator(anchors_xy, deltas, **rbf_kwargs)
    ys, xs = np.mgrid[0:H, 0:W]
    grid = np.stack([xs.ravel(), ys.ravel()], axis=-1).astype(np.float64)
    out = rbf(grid).reshape(H, W, 2).astype(np.float32)
    return out


def warp_erp_slab_by_displacement(
    slab: np.ndarray,
    displacement: np.ndarray,
    wrap_horizontal: bool = True,
) -> np.ndarray:
    """Warp an ERP slab by a dense (H, W, 2) displacement field.

    Convention: displacement[v, u] = (du, dv) tells us that ERP pixel (u, v)
    in the OUTPUT should be sourced from (u - du, v - dv) in the INPUT slab.
    (This matches the "warp slab toward the ideal location" semantic — the
    sparse delta_uv was ideal - L1, so dst[ideal] = src[L1] = src[ideal - delta].)

    Uses cv2.remap with bilinear interpolation. Handles ERP horizontal wrap
    when wrap_horizontal=True via modulo on the source u coordinate.

    Args:
        slab: (H, W, C) float32 ERP slab.
        displacement: (H, W, 2) float32. displacement[..., 0] = du, [..., 1] = dv.
        wrap_horizontal: if True, mod source u into [0, W).

    Returns:
        Warped (H, W, C) float32 slab.
    """
    H, W = slab.shape[:2]
    assert displacement.shape == (H, W, 2)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    src_u = xs - displacement[..., 0]
    src_v = ys - displacement[..., 1]
    if wrap_horizontal:
        src_u = np.mod(src_u, W).astype(np.float32)
    else:
        src_u = np.clip(src_u, 0, W - 1).astype(np.float32)
    src_v = np.clip(src_v, 0, H - 1).astype(np.float32)
    warped = cv2.remap(
        slab, src_u, src_v,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return warped


def build_anchor_confidence_map(
    anchor_positions: list[np.ndarray],
    erp_hw: tuple[int, int],
    sigma_px: float = 20.0,
) -> np.ndarray:
    """Build a (H, W) float32 confidence map: high near anchors, low far.

    For each ERP pixel, confidence = max over anchors of exp(-dist^2 / (2*sigma^2)).
    Used to gate dense displacement: in stereo-free regions, no shift applied.

    Returns float32 in [0, 1], shape erp_hw.
    """
    H, W = erp_hw
    if len(anchor_positions) == 0:
        return np.zeros((H, W), dtype=np.float32)
    out = np.zeros((H, W), dtype=np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    inv_two_sigma_sq = 1.0 / (2.0 * sigma_px * sigma_px)
    for a in anchor_positions:
        u, v = float(a[0]), float(a[1])
        # ERP wrap on u
        du = np.minimum(np.abs(xx - u), W - np.abs(xx - u))
        dv = (yy - v)
        d2 = du * du + dv * dv
        contrib = np.exp(-d2 * inv_two_sigma_sq).astype(np.float32)
        np.maximum(out, contrib, out=out)
    return out


def build_warped_slabs_a2(
    l1_slabs: dict[str, np.ndarray],
    stereo_npz_paths,
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_names: list[str],
    erp_hw: tuple[int, int],
    rbf_regularization: float = 1.0,
    confidence_sigma_px: float = 20.0,
    wrap_horizontal: bool = True,
    target_mode: str = "ideal",
    min_parallax_px: float = 0.0,
    kernel: str = "thin_plate_spline",
    gaussian_width_px: float | None = None,
) -> tuple[dict[str, np.ndarray], dict]:
    """Orchestrator: L1 slabs + stereo cache -> warped slabs.

    Pipeline per cam:
      1. Build sparse {(anchor_uv, delta_uv)} from stereo .npz files involving cam
      2. Interpolate to dense (H, W, 2) displacement field via TPS RBF
      3. Build (H, W) confidence map from anchor positions
      4. Gate displacement: dense_disp * confidence
      5. Warp slab via cv2.remap

    target_mode (passed through to build_per_cam_displacements_from_stereo):
        "ideal"    : original A2 — depth-aware ERP location per 3D point
        "midpoint" : joint per-pair — both cams in pair move halfway toward
                     each other's L1 projection (no depth, no asymmetry)

    Returns:
        (warped_slabs, summary_dict)
        - warped_slabs: same keys as l1_slabs, gated-warp applied
        - summary: n_stereo_files_used, target_mode, per-cam #anchors and max |delta|
    """
    n_stereo_total = len(list(stereo_npz_paths))
    sparse_per_cam = build_per_cam_displacements_from_stereo(
        stereo_npz_paths, cam_K=cam_K, cam_T_ego_cam=cam_T_ego_cam,
        cam_names=cam_names, erp_hw=erp_hw, target_mode=target_mode,
        min_parallax_px=min_parallax_px,
    )
    out_slabs: dict[str, np.ndarray] = {}
    per_cam_stats: dict[str, dict] = {}
    for cam in cam_names:
        slab = l1_slabs[cam]
        anchors_for_cam = sparse_per_cam.get(cam, [])
        n_anchors = len(anchors_for_cam)
        if n_anchors == 0:
            out_slabs[cam] = slab.astype(np.float32)
            per_cam_stats[cam] = {"n_anchors": 0, "max_abs_delta_px": 0.0}
            continue
        dense_disp = interpolate_dense_displacement_field(
            anchors_for_cam, erp_hw=erp_hw, regularization=rbf_regularization,
            kernel=kernel, gaussian_width_px=gaussian_width_px,
        )
        anchor_uvs = [a[0] for a in anchors_for_cam]
        conf = build_anchor_confidence_map(
            anchor_uvs, erp_hw=erp_hw, sigma_px=confidence_sigma_px,
        )
        gated_disp = dense_disp * conf[..., None]
        max_delta = float(np.linalg.norm(gated_disp, axis=-1).max())
        out_slabs[cam] = warp_erp_slab_by_displacement(
            slab.astype(np.float32), gated_disp, wrap_horizontal=wrap_horizontal,
        )
        per_cam_stats[cam] = {
            "n_anchors": int(n_anchors),
            "max_abs_delta_px": max_delta,
        }
    summary = {
        "n_stereo_files_used": n_stereo_total,
        "target_mode": target_mode,
        "min_parallax_px": min_parallax_px,
        "kernel": kernel,
        "gaussian_width_px": gaussian_width_px,
        "per_cam": per_cam_stats,
    }
    return out_slabs, summary
