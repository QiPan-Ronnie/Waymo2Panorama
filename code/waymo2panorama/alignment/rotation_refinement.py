"""
Phase 3 WS2 v3 — Rotation refinement via bundle adjustment.

Why v1/v2 chain-warp NEG-ed: ring cams point in DIFFERENT directions (~60 deg
apart), so warping `cam_side_left`'s image into `cam_front_center`'s image
plane is geometrically impossible — side_left's content lives at angle ~60 deg
from front_center's optical axis, beyond front_center's ~90 deg FOV. Coverage
post-warp collapses to 0%. Chain warp architecture is fundamentally wrong for
ring cams.

What v3 does instead: don't warp the IMAGE at all. From feature matches between
adjacent cams, estimate small ROTATION REFINEMENTS to each cam's calibrated
extrinsic (T_ego_cam). 7 cams x 3 DOF (axis-angle) = 21 unknowns, optimized
jointly via scipy.optimize.least_squares to minimize residual pair rotation
error. One cam is anchored (delta_R = 0) to fix the gauge.

After refinement, each cam still projects to its OWN sphere slab via the L1
sphere projection (no image warping). The only change is each cam's T_ego_cam
shifts by a few degrees -> overlap regions align with sub-pixel accuracy ->
multiband blender produces ghost-reduced ERP.

This is the standard "AutoStitch" / OpenCV stitcher pattern, but applied to
calibrated extrinsics as a REFINEMENT rather than from-scratch estimation.

References
----------
  - Brown & Lowe, "Automatic panoramic image stitching using invariant features",
    IJCV 2007 (the AutoStitch paper that established this bundle adjustment style).
  - OpenCV cv::detail::BundleAdjusterRay (default for cv::Stitcher).
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np

# We reuse the rotation-only Procrustes fit from pair_homography for the
# per-pair observed R. The fit is closed-form and robust.
from waymo2panorama.alignment.pair_homography import _fit_rotation_only_homography
from waymo2panorama.stereo.wide_baseline_stereo import (
    extract_pair_features,
    match_with_lightglue,
)


__all__ = [
    "axis_angle_to_R",
    "R_to_axis_angle",
    "estimate_pair_rotation_observed",
    "bundle_adjust_rotations",
    "apply_rotation_refinements",
]


# ---------------------------------------------------------------------------
# SO(3) helpers (axis-angle <-> rotation matrix, both via Rodrigues)
# ---------------------------------------------------------------------------


def axis_angle_to_R(omega: np.ndarray) -> np.ndarray:
    """Convert (3,) axis-angle to (3, 3) rotation matrix via Rodrigues.

    `omega` direction = rotation axis, magnitude = rotation angle (radians).
    """
    theta = float(np.linalg.norm(omega))
    if theta < 1e-12:
        return np.eye(3, dtype=np.float64)
    k = (omega / theta).astype(np.float64)
    K = np.array([
        [0.0, -k[2],  k[1]],
        [k[2],  0.0, -k[0]],
        [-k[1], k[0],  0.0],
    ], dtype=np.float64)
    return np.eye(3) + np.sin(theta) * K + (1.0 - np.cos(theta)) * (K @ K)


def R_to_axis_angle(R: np.ndarray) -> np.ndarray:
    """Convert (3, 3) rotation matrix to (3,) axis-angle (omega = axis * theta)."""
    R = np.asarray(R, dtype=np.float64)
    cos_theta = (np.trace(R) - 1.0) / 2.0
    cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
    theta = float(np.arccos(cos_theta))
    if theta < 1e-12:
        return np.zeros(3, dtype=np.float64)
    if abs(np.pi - theta) < 1e-6:
        # Special case: theta = pi, axis is recovered from the symmetric part
        M = (R + np.eye(3)) / 2.0
        # axis is the eigenvector with eigenvalue 1; columns of M scale by axis
        # robust pick: column with largest diagonal
        i = int(np.argmax(np.diag(M)))
        v = M[:, i]
        v = v / (np.linalg.norm(v) + 1e-12)
        return v * theta
    s = 2.0 * np.sin(theta)
    axis = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / s
    return axis * theta


# ---------------------------------------------------------------------------
# Per-pair observed rotation: DISK + LightGlue + rotation-only Procrustes
# ---------------------------------------------------------------------------


def estimate_pair_rotation_observed(
    img_a: np.ndarray, img_b: np.ndarray,
    K_a: np.ndarray, K_b: np.ndarray,
    device: str = "cpu",
    max_num_keypoints: int = 2048,
    lightglue_min_confidence: float = 0.2,
    ransac_threshold_px: float = 3.0,
    min_inliers: int = 30,
) -> Optional[dict]:
    """Estimate the rotation R such that ray_b ~= R @ ray_a from feature matches.

    Returns a dict {R_obs_a_to_b, inlier_count, residual_px, n_matches} on
    success, or None if the fit was too weak (no matches / not enough inliers).
    """
    t0 = time.time()
    try:
        kpts_a, desc_a, kpts_b, desc_b = extract_pair_features(
            img_a, img_b, device=device, max_num_keypoints=max_num_keypoints,
        )
        mkpts_a, mkpts_b, _ = match_with_lightglue(
            kpts_a, desc_a, kpts_b, desc_b,
            img_a_hw=img_a.shape[:2], img_b_hw=img_b.shape[:2],
            device=device, min_confidence=lightglue_min_confidence,
        )
    except Exception:
        return None

    n = int(mkpts_a.shape[0])
    if n < min_inliers:
        return None

    H, inlier_mask = _fit_rotation_only_homography(
        mkpts_a, mkpts_b, K_a=K_a, K_b=K_b,
        ransac_threshold_px=ransac_threshold_px,
    )
    if H is None:
        return None
    n_inliers = int(inlier_mask.sum())
    if n_inliers < min_inliers:
        return None

    # H_rot = K_b @ R @ K_a^-1, recover R
    R = np.linalg.inv(K_b.astype(np.float64)) @ H @ K_a.astype(np.float64)
    # Re-orthonormalize (small numerical drift from SVD inverse)
    U, _, Vt = np.linalg.svd(R)
    d = np.linalg.det(U @ Vt)
    S = np.diag([1.0, 1.0, float(np.sign(d))])
    R = U @ S @ Vt

    # Median per-inlier pixel residual under the orthonormalized R
    K_a_inv = np.linalg.inv(K_a.astype(np.float64))
    pix_a_h = np.hstack([mkpts_a[inlier_mask].astype(np.float64),
                          np.ones((n_inliers, 1))])
    ray_a = (K_a_inv @ pix_a_h.T).T
    ray_a /= np.linalg.norm(ray_a, axis=1, keepdims=True).clip(min=1e-12)
    rot = (R @ ray_a.T).T
    z_safe = np.where(np.abs(rot[:, 2]) < 1e-9, 1.0, rot[:, 2])
    u_pred = K_b[0, 0] * rot[:, 0] / z_safe + K_b[0, 2]
    v_pred = K_b[1, 1] * rot[:, 1] / z_safe + K_b[1, 2]
    err = np.sqrt(
        (u_pred - mkpts_b[inlier_mask, 0]) ** 2 +
        (v_pred - mkpts_b[inlier_mask, 1]) ** 2
    )
    residual_px = float(np.median(err))

    return {
        "R_obs_a_to_b": R,
        "inlier_count": n_inliers,
        "residual_px": residual_px,
        "n_matches": n,
        "time_s": round(time.time() - t0, 4),
    }


# ---------------------------------------------------------------------------
# Bundle adjustment over per-cam rotation refinements
# ---------------------------------------------------------------------------


def bundle_adjust_rotations(
    pair_R_observed: dict[tuple[str, str], np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_names: list[str],
    anchor_cam: str,
    pair_weights: Optional[dict[tuple[str, str], float]] = None,
    verbose: bool = False,
    l2_reg_weight: float = 1e-3,
) -> dict[str, np.ndarray]:
    """Estimate per-cam rotation refinements dR_i (3, 3) to minimize pair-wise
    rotation residuals.

    Model
    -----
    For each pair (a, b) observed via features, the OBSERVED rotation in cam
    frames is R_obs_a_to_b. The calibrated rotation (from T_ego_cam) is
        R_cal_a_to_b = R_ego_cam_b^T @ R_ego_cam_a.
    After refinement, each cam's effective ego->cam rotation becomes
        R_ego_cam_i_refined = R_ego_cam_i @ dR_i,
    so the refined predicted pair rotation is
        R_pred_a_to_b = (R_ego_cam_b @ dR_b)^T @ (R_ego_cam_a @ dR_a)
                      = dR_b^T @ R_ego_cam_b^T @ R_ego_cam_a @ dR_a
                      = dR_b^T @ R_cal_a_to_b @ dR_a.

    Residual to minimize (per pair): R_residual = R_obs_a_to_b @ R_pred_a_to_b^T,
    flattened to axis-angle (3 numbers). Anchor cam has dR_anchor fixed to I
    (gauge freedom — otherwise the global rotation is unconstrained).

    Parameters
    ----------
    pair_R_observed
        {(cam_a, cam_b): (3, 3) R_obs_a_to_b from feature matches}.
    cam_T_ego_cam
        {cam: (4, 4) T_ego_cam} calibrated extrinsics.
    cam_names
        Order of cams (must include anchor_cam and all pair members).
    anchor_cam
        The cam whose dR is fixed to identity (gauge anchor).
    pair_weights
        Optional {(cam_a, cam_b): weight} multiplier for residuals. Default 1.0.

    Returns
    -------
    dict {cam_name: (3, 3) dR refinement}. anchor_cam's value is identity.
    """
    try:
        from scipy.optimize import least_squares
    except ImportError as exc:
        raise ImportError(
            "scipy.optimize is required for bundle_adjust_rotations"
        ) from exc

    if anchor_cam not in cam_names:
        raise ValueError(f"anchor_cam {anchor_cam!r} not in cam_names")
    non_anchor = [c for c in cam_names if c != anchor_cam]
    cam_to_idx = {c: i for i, c in enumerate(non_anchor)}
    n_unknowns = 3 * len(non_anchor)

    R_ego_cam = {c: np.asarray(cam_T_ego_cam[c])[:3, :3].astype(np.float64)
                 for c in cam_names}

    pairs_list = []
    for (a, b), R_obs in pair_R_observed.items():
        if a not in cam_names or b not in cam_names:
            continue
        w = float(pair_weights.get((a, b), 1.0)) if pair_weights else 1.0
        if w <= 0:
            continue
        R_cal_a_to_b = R_ego_cam[b].T @ R_ego_cam[a]
        pairs_list.append((a, b, np.asarray(R_obs, dtype=np.float64),
                           R_cal_a_to_b, w))

    if len(pairs_list) == 0:
        return {c: np.eye(3, dtype=np.float64) for c in cam_names}

    def _residuals(omegas_flat: np.ndarray) -> np.ndarray:
        omegas = omegas_flat.reshape(-1, 3)
        dR = {anchor_cam: np.eye(3, dtype=np.float64)}
        for c in non_anchor:
            dR[c] = axis_angle_to_R(omegas[cam_to_idx[c]])
        res = []
        for a, b, R_obs, R_cal, w in pairs_list:
            R_pred = dR[b].T @ R_cal @ dR[a]
            R_residual = R_obs @ R_pred.T
            v = R_to_axis_angle(R_residual) * w
            res.append(v)
        # L2 regularization: bias all dRs toward identity (keeps the BA
        # well-conditioned when the pair graph is under-determined, e.g.
        # held-out cam drops some pair fits). l2_reg_weight=1e-3 means each
        # axis-angle component contributes ~1e-3 to the residual vector --
        # negligible compared to typical pair residuals (~1e-2 to 1e-1) so
        # well-constrained cams move freely; only un-constrained cams get
        # pulled back to identity.
        if l2_reg_weight > 0:
            res.append((omegas_flat * l2_reg_weight).copy())
        return np.concatenate(res)

    x0 = np.zeros(n_unknowns, dtype=np.float64)
    if verbose:
        r0 = _residuals(x0)
        print(f"[BA] initial residual norm = {float(np.linalg.norm(r0)):.6f} "
              f"(over {len(pairs_list)} pairs + {n_unknowns} reg, anchor={anchor_cam})")

    # trf handles under-determined cases (n_residuals < n_vars) safely.
    # With l2_reg we add n_unknowns extra residuals, so it's always
    # well-conditioned even when feature pairs are sparse.
    result = least_squares(_residuals, x0, method="trf", max_nfev=200)
    if verbose:
        rf = _residuals(result.x)
        print(f"[BA] final   residual norm = {float(np.linalg.norm(rf)):.6f} "
              f"after {result.nfev} fn evals, status={result.status}")

    omegas_final = result.x.reshape(-1, 3)
    out = {anchor_cam: np.eye(3, dtype=np.float64)}
    for c in non_anchor:
        out[c] = axis_angle_to_R(omegas_final[cam_to_idx[c]])
    return out


def apply_rotation_refinements(
    cam_T_ego_cam: dict[str, np.ndarray],
    dR_per_cam: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Apply per-cam rotation refinements to the calibrated T_ego_cam matrices.

    Convention: R_ego_cam_refined = R_ego_cam @ dR. Translation unchanged.
    """
    out: dict[str, np.ndarray] = {}
    for cam, T in cam_T_ego_cam.items():
        T = np.asarray(T, dtype=np.float64).copy()
        R_orig = T[:3, :3]
        dR = dR_per_cam.get(cam, np.eye(3))
        T[:3, :3] = R_orig @ dR
        out[cam] = T
    return out
