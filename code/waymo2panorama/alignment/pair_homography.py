"""
Phase 3 WS2 — Pair-wise homography estimation between adjacent ring cams.

Goal: pre-warp one camera image so its overlap region aligns (in 2D image
coordinates) with its neighbor's overlap region. Feeding aligned cam images
into the L1 sphere projection + multi-band blender reduces "double-image"
ghosts in overlap regions where parallax shifts the same 3D object onto
slightly different ERP locations from each cam.

Pipeline:
    1. DISK feature extraction on both images (reuses stereo/wide_baseline_stereo)
    2. LightGlue matching (reuses stereo/wide_baseline_stereo)
    3. cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    4. Soft fallback to identity if the fit is weak (no matches, too few
       inliers, or large median residual).

The homography is a 2D-plane projective transform. It implicitly assumes
the overlap content lies on a plane (or is locally well-approximated by
one — true for content at >30 m on AV2 ring overlap wedges). Near-field
3D objects will still mis-warp slightly; the homography is the best 2D
approximation, not a complete parallax fix.

Architecture B (per-cam pre-warp). For the v1 implementation, each
adjacent pair is treated independently — image B is warped into image A's
frame. v2 could chain homographies through a reference cam (`ring_front_
center`) for global consistency. See the `TODO` in `compute_overlap_
homography_chain` placeholder.

The module always returns a valid 3x3 H (identity if fallback). Callers
can blindly do `cv2.warpPerspective(img_b, H, ...)` without checking the
status field first — but they SHOULD log the status for diagnostics.
"""
from __future__ import annotations

import time
from typing import Optional

import cv2
import numpy as np

# Reuse DISK + LightGlue plumbing from the stereo module — do not reimplement.
from waymo2panorama.stereo.wide_baseline_stereo import (
    ADJACENT_PAIRS,
    extract_pair_features,
    match_with_lightglue,
)


__all__ = [
    "ADJACENT_PAIRS",
    "compute_overlap_homography",
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
        }

    # Step 3: cv2.findHomography with RANSAC.
    # src = mkpts_a (points in img_a), dst = mkpts_b (points in img_b).
    # H maps src -> dst: H @ x_a ~= x_b.
    src = mkpts_a.astype(np.float32).reshape(-1, 1, 2)
    dst = mkpts_b.astype(np.float32).reshape(-1, 1, 2)
    H, inlier_mask = cv2.findHomography(
        src, dst,
        method=cv2.RANSAC,
        ransacReprojThreshold=float(ransac_threshold_px),
        maxIters=2000,
        confidence=0.999,
    )

    if H is None or inlier_mask is None:
        return {
            "H": _identity_H(),
            "inlier_count": 0,
            "residual_px": float("nan"),
            "match_count": match_count,
            "status": "low_inliers",
            "time_s": round(time.time() - t0, 4),
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
        }

    return {
        "H": H.astype(np.float64),
        "inlier_count": inlier_count,
        "residual_px": float(residual_px),
        "match_count": match_count,
        "status": "ok",
        "time_s": round(time.time() - t0, 4),
    }
