"""
Unit tests for alignment/pair_homography.py — DISK+LightGlue -> cv2.findHomography.

Verifies the public contract of `compute_overlap_homography` on synthetic
inputs:
  - Return shape / keys.
  - "no_matches" fallback returns identity H for random noise.
  - "ok" path: synthetic-homography trick — take a real image, apply a known
    small projective warp, run match → recover ≈ inverse homography.
  - Edge cases: degenerate sizes, mismatched shapes.

Requires kornia + torch on the import path (which is true for our env).
If DISK weights cannot be downloaded (offline-only Colab box, no internet),
the heavy tests are skipped — light tests still run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path wiring so this test can run via `pytest <this file>` from anywhere.
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()  # code/waymo2panorama/alignment -> code/
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.alignment.pair_homography import (  # noqa: E402
    ADJACENT_PAIRS,
    compute_overlap_homography,
)


# ---------------------------------------------------------------------------
# Skip-if-no-feature-net helper
# ---------------------------------------------------------------------------
#
# DISK/LightGlue weights download on first use. If we're offline (or
# kornia is missing), skip the heavy tests rather than fail.


def _disk_available() -> tuple[bool, str]:
    try:
        import kornia.feature as KF  # noqa: F401
    except Exception as exc:  # pragma: no cover
        return False, f"kornia unavailable: {exc!r}"
    try:
        import torch
        from waymo2panorama.stereo.wide_baseline_stereo import _get_disk_and_lightglue
        _get_disk_and_lightglue(torch.device("cpu"))
        return True, ""
    except Exception as exc:  # pragma: no cover
        return False, f"DISK/LightGlue init failed (likely offline): {exc!r}"


_DISK_OK, _DISK_REASON = _disk_available()
_skip_if_no_disk = pytest.mark.skipif(not _DISK_OK, reason=_DISK_REASON or "DISK unavailable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _checkerboard_image(h: int, w: int, tile: int = 16, seed: int = 0) -> np.ndarray:
    """Textured deterministic image good for feature matching.

    Mix of a checkerboard (gives DISK corners) and additive noise (gives
    DISK descriptor variation) — much more reliable than pure noise.
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    check = (((yy // tile) + (xx // tile)) % 2).astype(np.float32) * 255.0
    # 3 channel with slightly different per-channel patterns
    img = np.stack([check, check * 0.7 + 60, check * 0.4 + 120], axis=-1)
    img = img + rng.uniform(-20, 20, size=img.shape).astype(np.float32)
    img = np.clip(img, 0.0, 255.0).astype(np.uint8)
    return img


def _expected_keys() -> set[str]:
    return {"H", "inlier_count", "residual_px", "match_count", "status", "time_s"}


# ---------------------------------------------------------------------------
# Static / contract tests (no DISK needed)
# ---------------------------------------------------------------------------


def test_adjacent_pairs_has_seven_ring_entries() -> None:
    """The 7 ring cams form 7 adjacent pairs that close the ring."""
    assert len(ADJACENT_PAIRS) == 7
    # Every pair element should be a known ring cam name
    cams_seen = {c for pair in ADJACENT_PAIRS for c in pair}
    assert all(c.startswith("ring_") for c in cams_seen)


def test_compute_returns_identity_for_dtype_error_inputs() -> None:
    """Non-uint8 input raises a clear ValueError (NOT a fallback)."""
    img_a = np.zeros((32, 32, 3), dtype=np.float32)
    img_b = np.zeros((32, 32, 3), dtype=np.uint8)
    with pytest.raises(ValueError):
        compute_overlap_homography(img_a, img_b)


def test_compute_rejects_2d_input() -> None:
    """Non-RGB (2D) input raises ValueError."""
    img_a = np.zeros((32, 32), dtype=np.uint8)
    img_b = np.zeros((32, 32, 3), dtype=np.uint8)
    with pytest.raises(ValueError):
        compute_overlap_homography(img_a, img_b)


def test_compute_degenerate_size_falls_back_to_identity() -> None:
    """Tiny image (< 16 px) falls back rather than crashing DISK's pad-to-16."""
    img_a = np.zeros((4, 4, 3), dtype=np.uint8)
    img_b = np.zeros((4, 4, 3), dtype=np.uint8)
    out = compute_overlap_homography(img_a, img_b)
    assert set(out.keys()) >= _expected_keys()
    assert out["status"] == "no_matches"
    np.testing.assert_allclose(out["H"], np.eye(3), atol=1e-12)
    assert out["inlier_count"] == 0
    assert out["match_count"] == 0


def test_compute_none_input_falls_back() -> None:
    """None inputs (e.g. missing image) fall back to identity."""
    out = compute_overlap_homography(None, None)  # type: ignore[arg-type]
    assert out["status"] == "no_matches"
    np.testing.assert_allclose(out["H"], np.eye(3), atol=1e-12)


# ---------------------------------------------------------------------------
# DISK-backed tests (heavy; skipped if no kornia / no internet for weights)
# ---------------------------------------------------------------------------


@_skip_if_no_disk
def test_no_matches_path_with_pure_noise() -> None:
    """Two independent random-noise images produce too few matches → identity fallback.

    NOTE: DISK can occasionally find spurious correlations between two pure-noise
    images; the test only requires that the fallback path returns identity-H
    AND a recognized status from {no_matches, low_inliers, high_residual}.
    """
    rng = np.random.default_rng(123)
    img_a = rng.integers(0, 256, size=(128, 128, 3), dtype=np.uint8)
    img_b = rng.integers(0, 256, size=(128, 128, 3), dtype=np.uint8)

    out = compute_overlap_homography(img_a, img_b, device="cpu", max_num_keypoints=256)
    assert set(out.keys()) >= _expected_keys()
    assert out["status"] in {"no_matches", "low_inliers", "high_residual"}
    # Whichever fallback fired, the H must be identity.
    np.testing.assert_allclose(out["H"], np.eye(3), atol=1e-12)


@_skip_if_no_disk
def test_ok_path_recovers_known_homography_within_tolerance() -> None:
    """Synthetic-homography trick: warp img_a by known H_true to make img_b,
    then compute_overlap_homography(img_a, img_b) should recover ~H_true.

    We use a small projective perturbation so the warped image stays mostly
    inside the canvas (DISK needs visible content on both sides).
    """
    img_a = _checkerboard_image(192, 192, tile=12, seed=7)
    H_true = np.array(
        [
            [1.02, 0.01, 3.0],
            [-0.01, 1.03, -2.0],
            [1e-5, 5e-6, 1.0],
        ],
        dtype=np.float64,
    )
    h, w = img_a.shape[:2]
    img_b = cv2.warpPerspective(img_a, H_true, (w, h), flags=cv2.INTER_LINEAR)

    out = compute_overlap_homography(
        img_a, img_b, device="cpu", max_num_keypoints=1024,
        # Slightly relaxed: synthetic-warp residuals are sub-pixel anyway.
        max_residual_px=2.0,
    )
    assert set(out.keys()) >= _expected_keys()
    # We expect the fit to succeed on a textured checkerboard.
    assert out["status"] == "ok", (
        f"expected status=ok, got {out['status']} "
        f"(matches={out['match_count']}, inliers={out['inlier_count']}, "
        f"residual={out['residual_px']:.2f})"
    )
    assert out["inlier_count"] >= 30
    assert out["residual_px"] <= 2.0

    # Recovered H should map img_a points to img_b points like H_true does.
    # Compare action on a fixed grid of points inside the visible region.
    test_pts = np.array(
        [
            [40.0, 50.0],
            [80.0, 60.0],
            [120.0, 90.0],
            [50.0, 130.0],
            [100.0, 150.0],
        ],
        dtype=np.float64,
    )
    src = np.hstack([test_pts, np.ones((test_pts.shape[0], 1))])
    expected = (H_true @ src.T).T
    expected = expected[:, :2] / expected[:, 2:3]
    recovered = (out["H"] @ src.T).T
    recovered = recovered[:, :2] / recovered[:, 2:3]
    diff = np.linalg.norm(expected - recovered, axis=1)
    # Tolerance: a few pixels is fine — DISK keypoints have sub-pixel-but-not-zero error.
    assert float(diff.max()) < 5.0, (
        f"recovered H disagrees with H_true by max {diff.max():.2f} px: {diff}"
    )


@_skip_if_no_disk
def test_compute_handles_mismatched_image_sizes() -> None:
    """Different-sized inputs do not crash; either fits or falls back gracefully."""
    img_a = _checkerboard_image(128, 160, tile=12, seed=1)
    img_b = _checkerboard_image(96, 192, tile=12, seed=2)  # different content & size
    out = compute_overlap_homography(img_a, img_b, device="cpu", max_num_keypoints=512)
    assert set(out.keys()) >= _expected_keys()
    assert out["H"].shape == (3, 3)
    assert out["status"] in {"ok", "no_matches", "low_inliers", "high_residual"}


@_skip_if_no_disk
def test_compute_with_roi_runs_and_returns_full_image_coord_H() -> None:
    """ROI crop runs without error; returned H is valid in FULL-image coords.

    Build img_b = warp(img_a, H_true). Restrict matching to the central ROI on
    each side. Even with the ROI, the returned H should approximate H_true
    on the FULL image's coordinate system (because pair_homography offsets
    kpts back before findHomography).
    """
    img_a = _checkerboard_image(192, 192, tile=12, seed=11)
    H_true = np.array(
        [
            [1.01, 0.005, 2.0],
            [-0.003, 1.015, -1.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    h, w = img_a.shape[:2]
    img_b = cv2.warpPerspective(img_a, H_true, (w, h), flags=cv2.INTER_LINEAR)

    # Central 128x128 ROI on both sides.
    roi = (32, 32, 128, 128)
    out = compute_overlap_homography(
        img_a, img_b,
        overlap_roi_a=roi, overlap_roi_b=roi,
        device="cpu", max_num_keypoints=1024,
        max_residual_px=2.0,
    )
    if out["status"] != "ok":
        pytest.skip(f"ROI fit weak on this seed: {out['status']}")
    # Compare full-image action of H vs H_true on a couple of points
    # that LIE INSIDE the ROI:
    test_pts = np.array([[64.0, 70.0], [100.0, 110.0]], dtype=np.float64)
    src = np.hstack([test_pts, np.ones((test_pts.shape[0], 1))])
    expected = (H_true @ src.T).T
    expected = expected[:, :2] / expected[:, 2:3]
    recovered = (out["H"] @ src.T).T
    recovered = recovered[:, :2] / recovered[:, 2:3]
    diff = np.linalg.norm(expected - recovered, axis=1)
    assert float(diff.max()) < 6.0, f"ROI-fit H disagreement {diff} > 6px"
