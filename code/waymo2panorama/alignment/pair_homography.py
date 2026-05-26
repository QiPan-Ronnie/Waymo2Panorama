"""
Phase 3 WS2 — Pair-wise homography estimation + chain warp on the ring.

Goal: pre-warp every camera image so the overlap regions on the ring share
a single image-plane frame (the "reference cam"). Feeding aligned cam images
into the L1 sphere projection + multi-band blender reduces "double-image"
ghosts in overlap regions where parallax shifts the same 3D object onto
slightly different ERP locations from each cam ("2-wheel artifact").

Two-stage pipeline:
    1. PER-PAIR homography (this module's `compute_overlap_homography`):
         a. DISK feature extraction on the overlap region (reuses stereo/wide_baseline_stereo)
         b. LightGlue matching (reuses stereo/wide_baseline_stereo)
         c. cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
         d. Soft fallback to identity if the fit is weak (no matches, too few
            inliers, or large median residual).
    2. CHAIN WARP (this module's `ring_path_homography` + `compose_homographies`):
         Given the 7 adjacent-pair homographies that close the ring, compose
         them along the SHORTEST ring path from each cam to the global
         reference cam (default = ring_front_center). The chain warp puts
         every cam in a common image-plane frame, which is what the L1
         multiband blender then merges.

Correctness caveats (CRITICAL — document for downstream callers):

    1. 2D approximation, not full 3D parallax fix. A homography assumes the
       overlap content lies on a plane (or is locally well-approximated by
       one — true for content at >30 m on AV2 ring overlap wedges). Near-
       field 3D objects (cars / pedestrians at <10 m) WILL still mis-warp;
       the homography is the best 2D approximation, not a complete parallax
       fix.

    2. Chain drift on rear cams. The composed homography H = H_n @ ... @ H_1
       accumulates per-pair residual errors. For AV2 with the reference at
       ring_front_center, the worst case is the rear cams (3 hops via
       front_left / side_left / rear_left, OR symmetrically the other
       direction). Expect 2-5 px of additional registration error on the
       rear-vs-rear overlap vs the front overlap. v2 could fix this with
       a global bundle adjustment (Levenberg-Marquardt over the cycle
       constraint H_n @ ... @ H_1 == identity).

    3. Warp outside overlap can degrade. The homography is FIT on the
       overlap region only; applied to the full image, content far from
       the overlap region may shift unnaturally. In practice, the L1
       sphere projection's cos^2 feather weight (peaks at the cam's
       optical axis) cleanly suppresses the warped fringe — the prewarp
       only "moves pixels" near the overlap, where it matters.

The module always returns a valid 3x3 H (identity if fallback). Callers
can blindly do `cv2.warpPerspective(img_b, H, ...)` without checking the
status field first — but they SHOULD log the status for diagnostics.

Stacking compatibility (with the rest of the 新-X stack):
    - Prewarp goes BEFORE 新-E HDR-compensation (HDR samples the overlap
      region AFTER alignment -> more reliable exposure ratios).
    - After prewarp, 新-B graphcut routes seams around aligned content
      rather than fighting parallax.
    - Compatible with WS3 Option B reweight (stack:
        prewarp -> confidence reweight -> multiband blend).
"""
from __future__ import annotations

import time
from typing import Optional

import cv2
import numpy as np

# Reuse DISK + LightGlue plumbing from the stereo module — do not reimplement.
from waymo2panorama.stereo.wide_baseline_stereo import (
    extract_pair_features,
    match_with_lightglue,
)


# ---------------------------------------------------------------------------
# Ring topology constants — single source of truth for downstream callers.
# ---------------------------------------------------------------------------
# 7 ring cams in traversal order. The ring CLOSES BACK to ring_front_center
# (so the 7th adjacent pair is ring_front_right -> ring_front_center).
RING_ORDER: list[str] = [
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
]

# 7 adjacent pairs derived from RING_ORDER. Each pair (a, b) means "cam a's
# image is the source space; the homography maps points in a -> b".
# Derived as the closure of RING_ORDER (ring_front_right -> ring_front_center
# closes the wrap-around).
ADJACENT_PAIRS: list[tuple[str, str]] = [
    (RING_ORDER[i], RING_ORDER[(i + 1) % len(RING_ORDER)])
    for i in range(len(RING_ORDER))
]


__all__ = [
    "ADJACENT_PAIRS",
    "RING_ORDER",
    "compute_overlap_homography",
    "compose_homographies",
    "ring_path_homography",
    "validate_warp_corners",
]


# ---------------------------------------------------------------------------
# Soft-fallback thresholds
# ---------------------------------------------------------------------------
#
# These are tuned for AV2 504x504 letterboxed ring images with DISK+LightGlue.
# They can be relaxed at call-site via kwargs.

_DEFAULT_MIN_MATCHES = 50          # fewer raw matches -> "no_matches" fallback
_DEFAULT_MIN_INLIERS = 30          # fewer RANSAC inliers -> "low_inliers" fallback
_DEFAULT_MAX_RESIDUAL_PX = 2.0     # median reprojection error above -> "high_residual" fallback
_DEFAULT_RANSAC_THRESHOLD_PX = 3.0 # cv2.findHomography RANSAC inlier threshold


# Allowed warp models (v2). "similarity" + "rotation_only" added to fix v1
# chain-compose drift NEG (full perspective compounds badly across 3+ hops).
_WARP_MODELS = ("homography", "similarity", "rotation_only")


def _identity_H() -> np.ndarray:
    """3x3 identity homography (no-op warp)."""
    return np.eye(3, dtype=np.float64)


def _median_reproj_error(
    H: np.ndarray, src: np.ndarray, dst: np.ndarray
) -> float:
    """Median L2 distance between H @ src and dst, in pixels."""
    if src.shape[0] == 0:
        return float("inf")
    src_h = np.hstack([src.astype(np.float64), np.ones((src.shape[0], 1))])
    proj_h = (H @ src_h.T).T  # (N, 3)
    z = proj_h[:, 2:3]
    z_safe = np.where(np.abs(z) < 1e-12, 1.0, z)
    proj_xy = proj_h[:, :2] / z_safe
    err = np.linalg.norm(proj_xy - dst.astype(np.float64), axis=1)
    return float(np.median(err))


def _crop_with_offset(
    img: np.ndarray, roi: Optional[tuple[int, int, int, int]]
) -> tuple[np.ndarray, tuple[int, int]]:
    """Optionally crop `img` to roi=(x, y, w, h). Returns (cropped, (ox, oy)).

    If roi is None, returns the original image with offset (0, 0).
    Kpts in cropped coords are mapped back by `kpts + (ox, oy)`.
    """
    if roi is None:
        return img, (0, 0)
    x, y, w, h = roi
    x = max(0, int(x))
    y = max(0, int(y))
    w = max(1, int(w))
    h = max(1, int(h))
    x2 = min(img.shape[1], x + w)
    y2 = min(img.shape[0], y + h)
    if x2 <= x or y2 <= y:
        # Degenerate ROI; fall back to full image.
        return img, (0, 0)
    return img[y:y2, x:x2], (x, y)


def _fit_rotation_only_homography(
    mkpts_a: np.ndarray, mkpts_b: np.ndarray,
    K_a: np.ndarray, K_b: np.ndarray,
    ransac_threshold_px: float,
) -> tuple[Optional[np.ndarray], np.ndarray]:
    """Fit a rotation-only homography H = K_b @ R @ K_a^-1 via robust Procrustes.

    Geometric model: each cam sees a 3D scene; with infinite-distance assumption
    (or rotation-around-shared-center), pixel matches relate by a pure 3D rotation
    R between cam_a and cam_b coordinate frames. R has 3 DOF (vs 8 for full
    homography), so it's much more stable to chain compose across the ring.

    Algorithm:
      1. Backproject each match to a unit ray in cam frame: r = normalize(K^-1 @ [u, v, 1])
      2. Find R minimizing sum ||R @ r_a - r_b||^2 via SVD (Procrustes / Wahba).
      3. RANSAC inlier loop: sample 3 matches, fit R, count inliers, repeat.

    Returns:
        (H_3x3, inlier_mask) where H = K_b @ R @ K_a^-1. None if degenerate.
    """
    n = mkpts_a.shape[0]
    if n < 3:
        return None, np.zeros(n, dtype=bool)

    # Backproject all matches to unit rays in respective cam frames.
    K_a_inv = np.linalg.inv(K_a.astype(np.float64))
    K_b_inv = np.linalg.inv(K_b.astype(np.float64))
    ones = np.ones((n, 1), dtype=np.float64)
    pix_a_h = np.hstack([mkpts_a.astype(np.float64), ones])  # (N, 3)
    pix_b_h = np.hstack([mkpts_b.astype(np.float64), ones])
    ray_a = (K_a_inv @ pix_a_h.T).T  # (N, 3)
    ray_b = (K_b_inv @ pix_b_h.T).T
    ray_a /= np.linalg.norm(ray_a, axis=1, keepdims=True).clip(min=1e-12)
    ray_b /= np.linalg.norm(ray_b, axis=1, keepdims=True).clip(min=1e-12)

    def _fit_R(idx: np.ndarray) -> np.ndarray:
        """Closed-form Procrustes: R = U @ diag(1,1,det(U V^T)) @ V^T for cross-cov M = b^T a."""
        a = ray_a[idx]; b = ray_b[idx]
        M = b.T @ a  # (3, 3)
        U, _, Vt = np.linalg.svd(M)
        d = np.linalg.det(U @ Vt)
        S = np.diag([1.0, 1.0, float(np.sign(d))])
        return U @ S @ Vt

    # RANSAC over rotation hypotheses. Inlier: reprojection error < threshold.
    # Project ray_a through R, reproject to pixel via K_b, compare to mkpts_b.
    rng = np.random.default_rng(seed=0xC0FFEE)
    best_inliers: np.ndarray = np.zeros(n, dtype=bool)
    best_R: Optional[np.ndarray] = None
    n_iter = 200
    for _ in range(n_iter):
        idx = rng.choice(n, size=3, replace=False)
        try:
            R_hyp = _fit_R(idx)
        except np.linalg.LinAlgError:
            continue
        # Score: reproject all rays through R, compare to mkpts_b in pixel space.
        rot = (R_hyp @ ray_a.T).T  # (N, 3) in cam_b frame
        z = rot[:, 2]
        in_front = z > 1e-6
        z_safe = np.where(in_front, z, 1.0)
        u_pred = K_b[0, 0] * rot[:, 0] / z_safe + K_b[0, 2]
        v_pred = K_b[1, 1] * rot[:, 1] / z_safe + K_b[1, 2]
        err = np.sqrt((u_pred - mkpts_b[:, 0]) ** 2 + (v_pred - mkpts_b[:, 1]) ** 2)
        inliers = in_front & (err < ransac_threshold_px)
        if inliers.sum() > best_inliers.sum():
            best_inliers = inliers
            best_R = R_hyp

    if best_R is None or best_inliers.sum() < 3:
        return None, np.zeros(n, dtype=bool)

    # Refit R on all inliers for the final answer.
    R_final = _fit_R(np.where(best_inliers)[0])
    H = K_b.astype(np.float64) @ R_final @ K_a_inv
    return H, best_inliers


def compute_overlap_homography(
    img_a: np.ndarray,
    img_b: np.ndarray,
    K_a: Optional[np.ndarray] = None,
    K_b: Optional[np.ndarray] = None,
    T_ego_a: Optional[np.ndarray] = None,
    T_ego_b: Optional[np.ndarray] = None,
    overlap_roi_a: Optional[tuple[int, int, int, int]] = None,
    overlap_roi_b: Optional[tuple[int, int, int, int]] = None,
    device: str = "cpu",
    max_num_keypoints: int = 2048,
    lightglue_min_confidence: float = 0.2,
    ransac_threshold_px: float = _DEFAULT_RANSAC_THRESHOLD_PX,
    min_matches: int = _DEFAULT_MIN_MATCHES,
    min_inliers: int = _DEFAULT_MIN_INLIERS,
    max_residual_px: float = _DEFAULT_MAX_RESIDUAL_PX,
    warp_model: str = "rotation_only",
) -> dict:
    """Estimate a 2D homography H mapping points in img_a to img_b.

    Geometric semantics (very important for callers):
        x_b_homog ~= H @ x_a_homog
    so to *warp img_a into img_b's frame* you do:
        cv2.warpPerspective(img_a, H, (w_b, h_b))
    and to *warp img_b into img_a's frame* you do:
        cv2.warpPerspective(img_b, np.linalg.inv(H), (w_a, h_a))

    K_a, K_b, T_ego_a, T_ego_b are currently UNUSED (kept in the signature
    so that a v2 implementation can switch to a guided-RANSAC variant that
    seeds the homography from the known relative pose without breaking
    callers). Pass them when available so v2 won't need an API change.

    Args:
        img_a, img_b: H x W x 3 uint8 RGB camera images.
        K_*, T_ego_*: optional, reserved for future pose-guided estimation.
        overlap_roi_a, overlap_roi_b: optional (x, y, w, h) crops to focus
            DISK on the suspected overlap region (faster + more inliers).
            If None, run DISK on the full image.
        device: "cpu" or "cuda".
        max_num_keypoints: passed to DISK.
        lightglue_min_confidence: drop matches below this LightGlue score.
        ransac_threshold_px: cv2.findHomography RANSAC inlier threshold.
        min_matches, min_inliers, max_residual_px: soft-fallback thresholds.

    Returns:
        dict with keys:
            H              : (3, 3) float64 — homography (identity if fallback)
            inlier_count   : int            — RANSAC inliers (0 if fallback)
            residual_px    : float          — median reprojection error of inliers
                                              (nan if fallback / no matches)
            match_count    : int            — LightGlue matches before RANSAC
            status         : str            — "ok" | "low_inliers" | "high_residual"
                                              | "no_matches"
            time_s         : float          — wall time of this call

    Notes:
        - Always returns a valid H (3x3 identity if fallback) so callers can
          blindly use cv2.warpPerspective without status checks.
        - This is a 2D image-plane homography. It is the best 2D approximation
          to a 3D scene-aligned warp; it will not perfectly fix near-field
          parallax (true depth varies within the overlap region). For AV2
          ring overlap wedges (~5-15 deg of angular FOV) it works well for
          content >30 m and degrades gracefully for closer content.
    """
    t0 = time.time()
    if img_a is None or img_b is None:
        return {
            "H": _identity_H(),
            "inlier_count": 0,
            "residual_px": float("nan"),
            "match_count": 0,
            "status": "no_matches",
            "time_s": 0.0,
            "warp_model": warp_model,
        }
    if img_a.dtype != np.uint8 or img_b.dtype != np.uint8:
        raise ValueError(
            f"expected uint8 RGB, got {img_a.dtype} and {img_b.dtype}"
        )
    if img_a.ndim != 3 or img_a.shape[2] != 3 or img_b.ndim != 3 or img_b.shape[2] != 3:
        raise ValueError(
            f"expected HxWx3 RGB, got shapes {img_a.shape} and {img_b.shape}"
        )
    # Edge case: degenerate (zero-area) image — fall back rather than crash DISK.
    if img_a.shape[0] < 16 or img_a.shape[1] < 16 or img_b.shape[0] < 16 or img_b.shape[1] < 16:
        return {
            "H": _identity_H(),
            "inlier_count": 0,
            "residual_px": float("nan"),
            "match_count": 0,
            "status": "no_matches",
            "time_s": round(time.time() - t0, 4),
            "warp_model": warp_model,
        }

    # Optionally crop to ROI (saves DISK time on full image).
    crop_a, (ox_a, oy_a) = _crop_with_offset(img_a, overlap_roi_a)
    crop_b, (ox_b, oy_b) = _crop_with_offset(img_b, overlap_roi_b)
    h_a_crop, w_a_crop = crop_a.shape[:2]
    h_b_crop, w_b_crop = crop_b.shape[:2]

    # Step 1+2: DISK features + LightGlue matching (delegated to stereo module).
    try:
        kpts_a, desc_a, kpts_b, desc_b = extract_pair_features(
            crop_a, crop_b, device=device, max_num_keypoints=max_num_keypoints,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "H": _identity_H(),
            "inlier_count": 0,
            "residual_px": float("nan"),
            "match_count": 0,
            "status": "no_matches",
            "time_s": round(time.time() - t0, 4),
            "error": f"DISK failed: {exc!r}",
            "warp_model": warp_model,
        }

    try:
        mkpts_a, mkpts_b, _scores = match_with_lightglue(
            kpts_a, desc_a, kpts_b, desc_b,
            img_a_hw=(h_a_crop, w_a_crop), img_b_hw=(h_b_crop, w_b_crop),
            device=device, min_confidence=lightglue_min_confidence,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "H": _identity_H(),
            "inlier_count": 0,
            "residual_px": float("nan"),
            "match_count": 0,
            "status": "no_matches",
            "time_s": round(time.time() - t0, 4),
            "error": f"LightGlue failed: {exc!r}",
            "warp_model": warp_model,
        }

    match_count = int(mkpts_a.shape[0])

    # Map kpts back to full-image coords (so the returned H is valid for the
    # FULL images, not the crops).
    if ox_a != 0 or oy_a != 0:
        mkpts_a = mkpts_a + np.array([[ox_a, oy_a]], dtype=mkpts_a.dtype)
    if ox_b != 0 or oy_b != 0:
        mkpts_b = mkpts_b + np.array([[ox_b, oy_b]], dtype=mkpts_b.dtype)

    if match_count < min_matches:
        return {
            "H": _identity_H(),
            "inlier_count": 0,
            "residual_px": float("nan"),
            "match_count": match_count,
            "status": "no_matches",
            "time_s": round(time.time() - t0, 4),
            "warp_model": warp_model,
        }

    # Step 3: estimate warp via the requested model.
    if warp_model not in _WARP_MODELS:
        raise ValueError(
            f"warp_model must be one of {_WARP_MODELS}, got {warp_model!r}"
        )
    src = mkpts_a.astype(np.float32).reshape(-1, 1, 2)
    dst = mkpts_b.astype(np.float32).reshape(-1, 1, 2)

    if warp_model == "homography":
        # v1 behavior: full perspective (8 DOF). Compounds badly across chain.
        H, inlier_mask = cv2.findHomography(
            src, dst,
            method=cv2.RANSAC,
            ransacReprojThreshold=float(ransac_threshold_px),
            maxIters=2000,
            confidence=0.999,
        )
    elif warp_model == "similarity":
        # 4 DOF: rotation + scale + 2D translation. Bounded under chain compose
        # (similarity x similarity = similarity). cv2 returns 2x3, we lift to 3x3.
        H_aff, inlier_mask = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC,
            ransacReprojThreshold=float(ransac_threshold_px),
            maxIters=2000, confidence=0.999,
        )
        if H_aff is not None:
            H = np.eye(3, dtype=np.float64)
            H[:2] = H_aff.astype(np.float64)
        else:
            H = None
    else:  # "rotation_only"
        # 3 DOF: pure 3D rotation between cam frames. Requires K_a + K_b. If
        # caller didn't pass K, gracefully degrade to similarity (also 3-DOF-ish
        # for nearly-rectified ring cams).
        if K_a is None or K_b is None:
            H_aff, inlier_mask = cv2.estimateAffinePartial2D(
                src, dst, method=cv2.RANSAC,
                ransacReprojThreshold=float(ransac_threshold_px),
                maxIters=2000, confidence=0.999,
            )
            if H_aff is not None:
                H = np.eye(3, dtype=np.float64); H[:2] = H_aff.astype(np.float64)
            else:
                H = None
        else:
            H_rot, mask_rot = _fit_rotation_only_homography(
                mkpts_a, mkpts_b,
                K_a=np.asarray(K_a, dtype=np.float64),
                K_b=np.asarray(K_b, dtype=np.float64),
                ransac_threshold_px=float(ransac_threshold_px),
            )
            H = H_rot
            inlier_mask = mask_rot.reshape(-1, 1) if H_rot is not None else None

    if H is None or inlier_mask is None:
        return {
            "H": _identity_H(),
            "inlier_count": 0,
            "residual_px": float("nan"),
            "match_count": match_count,
            "status": "low_inliers",
            "time_s": round(time.time() - t0, 4),
            "warp_model": warp_model,
        }

    inlier_mask = inlier_mask.ravel().astype(bool)
    inlier_count = int(inlier_mask.sum())

    if inlier_count < min_inliers:
        return {
            "H": _identity_H(),
            "inlier_count": inlier_count,
            "residual_px": float("nan"),
            "match_count": match_count,
            "status": "low_inliers",
            "time_s": round(time.time() - t0, 4),
            "warp_model": warp_model,
        }

    # Step 4: residual check on the RANSAC inliers.
    residual_px = _median_reproj_error(
        H, mkpts_a[inlier_mask], mkpts_b[inlier_mask]
    )
    if not np.isfinite(residual_px) or residual_px > max_residual_px:
        return {
            "H": _identity_H(),
            "inlier_count": inlier_count,
            "residual_px": float(residual_px) if np.isfinite(residual_px) else float("nan"),
            "match_count": match_count,
            "status": "high_residual",
            "time_s": round(time.time() - t0, 4),
            "warp_model": warp_model,
        }

    return {
        "H": H.astype(np.float64),
        "inlier_count": inlier_count,
        "residual_px": float(residual_px),
        "match_count": match_count,
        "status": "ok",
        "time_s": round(time.time() - t0, 4),
        "warp_model": warp_model,
    }


# ---------------------------------------------------------------------------
# Chain warp helpers (compose / shortest-ring-path)
# ---------------------------------------------------------------------------
#
# `compute_overlap_homography` returns a 3x3 H such that
#     x_b_homog ~= H_a_to_b @ x_a_homog
# i.e. H maps points in cam_a to cam_b. To put a chain of cams all into
# ONE reference cam's image-plane frame (so the L1 multiband blender can
# merge aligned content), we need:
#
#     H_target_to_ref = H_n @ ... @ H_2 @ H_1
#
# where each H_k is the pair homography along the ring path from
# cam_target to cam_reference. The composition order matters: H_k must
# map cam_k -> cam_{k+1}, so applying them left-to-right takes a point
# from cam_target's image plane all the way to cam_reference's image plane.


def compose_homographies(homographies: list[np.ndarray]) -> np.ndarray:
    """Compose H_total = H_n @ ... @ H_2 @ H_1 from a list of homographies.

    Convention: each H_k maps points in coord-frame-k to coord-frame-k+1
    (left multiplication on column-vector pixel coords). The first element
    is applied first; the last element is applied last; so the composition
    is right-to-left matrix multiplication (np matmul order).

    Args:
        homographies: list of (3, 3) np.ndarrays. Empty list returns identity.

    Returns:
        (3, 3) float64 ndarray. Identity if list is empty.

    Edge cases:
        - Empty list -> identity (3x3 float64).
        - Single H   -> H.copy() (defensive against caller mutation).
    """
    if len(homographies) == 0:
        return np.eye(3, dtype=np.float64)
    H_total = np.eye(3, dtype=np.float64)
    for H in homographies:
        if H is None:
            raise ValueError("compose_homographies: None entry in list")
        H_total = np.asarray(H, dtype=np.float64) @ H_total
    return H_total


def _build_pair_lookup(
    pair_homographies: dict[tuple[str, str], np.ndarray],
) -> dict[tuple[str, str], np.ndarray]:
    """Index pair_homographies by BOTH (a, b) and (b, a) for bidirectional lookup.

    The reverse direction uses np.linalg.inv(H). If a pair value is None
    or the inverse is singular, the pair is silently dropped from the lookup
    (callers will fall back to identity along that hop).
    """
    lookup: dict[tuple[str, str], np.ndarray] = {}
    for (a, b), H in pair_homographies.items():
        if H is None:
            continue
        H_arr = np.asarray(H, dtype=np.float64)
        if H_arr.shape != (3, 3):
            continue
        lookup[(a, b)] = H_arr
        try:
            lookup[(b, a)] = np.linalg.inv(H_arr)
        except np.linalg.LinAlgError:
            # Singular H — keep forward only; reverse hop will fall back to identity.
            pass
    return lookup


def _shortest_ring_path(
    start: str, end: str, ring_order: list[str]
) -> list[str]:
    """Shortest sequence of cam names walking around the ring from start to end.

    Returns [start, ..., end]. If start == end, returns [start].

    The ring is treated as undirected and CYCLIC: with N cams, there are
    two paths between any pair, of lengths d and N-d. We pick the shorter
    one (ties broken by going FORWARD in ring_order, which matches the
    intuitive "front_left -> side_left" direction for AV2).
    """
    if start == end:
        return [start]
    if start not in ring_order or end not in ring_order:
        raise KeyError(
            f"_shortest_ring_path: unknown cam {start!r} or {end!r} "
            f"(known: {ring_order})"
        )
    n = len(ring_order)
    i = ring_order.index(start)
    j = ring_order.index(end)
    # Forward (CCW): start -> start+1 -> ... -> end
    forward_steps = (j - i) % n
    # Backward (CW):  start -> start-1 -> ... -> end
    backward_steps = (i - j) % n

    if forward_steps <= backward_steps:
        path = [ring_order[(i + k) % n] for k in range(forward_steps + 1)]
    else:
        path = [ring_order[(i - k) % n] for k in range(backward_steps + 1)]
    return path


def validate_warp_corners(
    H: np.ndarray,
    image_hw: tuple[int, int],
    max_outside_frac: float = 0.5,
) -> tuple[bool, float]:
    """Check that warping a (H, W) image with H keeps corners within a sane box.

    Many v1 failures were rear-cam chain-warps that pushed all 4 image corners
    far outside the canvas, producing an all-black warped image. This helper
    catches that case before the warp is applied (caller falls back to identity).

    Args:
        H: (3, 3) homography.
        image_hw: (H, W) of the source image.
        max_outside_frac: how far (fraction of image dimension) corners are
            allowed to fly past the canvas. E.g. 0.5 means "up to 50% of W
            outside the left/right edge" is OK. Above that -> reject.

    Returns:
        (is_safe, max_outside_frac_observed). is_safe == False means the
        chain warp would push image content too far off-canvas; caller should
        fall back to identity for that cam.
    """
    h_img, w_img = image_hw
    corners = np.array(
        [[0, 0], [w_img - 1, 0], [w_img - 1, h_img - 1], [0, h_img - 1]],
        dtype=np.float64,
    )
    corners_h = np.hstack([corners, np.ones((4, 1))])
    proj_h = (H @ corners_h.T).T
    z = proj_h[:, 2:3]
    z_safe = np.where(np.abs(z) < 1e-12, 1.0, z)
    proj_xy = proj_h[:, :2] / z_safe

    # Per-axis "fraction outside canvas" (negative = inside; positive = outside)
    outside_left = (-proj_xy[:, 0]) / w_img
    outside_right = (proj_xy[:, 0] - (w_img - 1)) / w_img
    outside_top = (-proj_xy[:, 1]) / h_img
    outside_bot = (proj_xy[:, 1] - (h_img - 1)) / h_img
    max_out = float(
        max(outside_left.max(), outside_right.max(),
            outside_top.max(), outside_bot.max(), 0.0)
    )
    return (max_out <= max_outside_frac), max_out


def ring_path_homography(
    cam_target: str,
    cam_reference: str,
    pair_homographies: dict[tuple[str, str], np.ndarray],
    ring_order: list[str] | None = None,
) -> np.ndarray:
    """Compose per-pair homographies along the shortest ring path.

    Returns H_target_to_ref such that
        x_ref_homog ~= H_target_to_ref @ x_target_homog
    and therefore:
        warped_target = cv2.warpPerspective(img_target, H_target_to_ref, (w_ref, h_ref))
    puts img_target into cam_reference's image-plane frame.

    Args:
        cam_target: name of cam being warped.
        cam_reference: name of the global reference cam (anchor).
        pair_homographies: dict (cam_a, cam_b) -> (3, 3) H mapping a -> b,
            as returned by compute_overlap_homography.
            Reverse hops are computed on the fly via np.linalg.inv.
            Missing hops fall back to identity (so a single missing pair
            doesn't break the whole chain — but the result is degraded).
        ring_order: defaults to module-level RING_ORDER.

    Returns:
        (3, 3) float64 H. Identity if cam_target == cam_reference.

    Raises:
        KeyError if cam_target or cam_reference is not in ring_order.
    """
    order = ring_order if ring_order is not None else RING_ORDER
    if cam_target == cam_reference:
        return np.eye(3, dtype=np.float64)

    path = _shortest_ring_path(cam_target, cam_reference, order)
    lookup = _build_pair_lookup(pair_homographies)

    # Walk the path, composing pair homographies hop by hop.
    Hs: list[np.ndarray] = []
    for k in range(len(path) - 1):
        a, b = path[k], path[k + 1]
        H_hop = lookup.get((a, b))
        if H_hop is None:
            # Missing pair on this hop — fall back to identity and keep going.
            # The chain will be partially degraded but won't crash.
            H_hop = np.eye(3, dtype=np.float64)
        Hs.append(H_hop)
    return compose_homographies(Hs)
