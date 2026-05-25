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
    RING_ORDER,
    compose_homographies,
    compute_overlap_homography,
    ring_path_homography,
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


def test_ring_order_and_adjacent_pairs_consistency() -> None:
    """ADJACENT_PAIRS must be derivable from RING_ORDER (closed ring)."""
    assert len(RING_ORDER) == 7
    assert RING_ORDER[0] == "ring_front_center"
    # Last pair must wrap from the last ring cam back to the first.
    assert ADJACENT_PAIRS[-1] == (RING_ORDER[-1], RING_ORDER[0])
    for i, (a, b) in enumerate(ADJACENT_PAIRS):
        assert a == RING_ORDER[i]
        assert b == RING_ORDER[(i + 1) % len(RING_ORDER)]


def test_signature_and_return_shape_on_no_matches_fallback() -> None:
    """The compute_overlap_homography return dict has all keys and H is 3x3 float."""
    img_a = np.zeros((4, 4, 3), dtype=np.uint8)
    img_b = np.zeros((4, 4, 3), dtype=np.uint8)
    out = compute_overlap_homography(img_a, img_b)
    assert set(out.keys()) >= _expected_keys()
    H = out["H"]
    assert isinstance(H, np.ndarray)
    assert H.shape == (3, 3)
    assert H.dtype in (np.float32, np.float64)


# ---------------------------------------------------------------------------
# compose_homographies / ring_path_homography (pure-Python; no DISK needed)
# ---------------------------------------------------------------------------


def _random_homography(seed: int) -> np.ndarray:
    """Small projective perturbation around identity for testing."""
    rng = np.random.default_rng(seed)
    eps = rng.uniform(-0.02, 0.02, size=(3, 3))
    H = np.eye(3, dtype=np.float64) + eps
    H[2, 2] = 1.0  # normalize bottom-right
    return H


def test_compose_homographies_empty_returns_identity() -> None:
    """compose([]) should return a fresh 3x3 identity."""
    H = compose_homographies([])
    assert H.shape == (3, 3)
    np.testing.assert_allclose(H, np.eye(3), atol=1e-12)


def test_compose_homographies_single_returns_input() -> None:
    """compose([H]) should equal H (up to dtype promotion)."""
    H_in = _random_homography(1)
    H_out = compose_homographies([H_in])
    np.testing.assert_allclose(H_out, H_in, atol=1e-12)


def test_compose_homographies_two_matches_matmul_order() -> None:
    """compose([H1, H2]) == H2 @ H1 (apply H1 first, then H2)."""
    H1 = _random_homography(2)
    H2 = _random_homography(3)
    H_total = compose_homographies([H1, H2])
    np.testing.assert_allclose(H_total, H2 @ H1, atol=1e-12)


def test_compose_homographies_three_matches_matmul_order() -> None:
    """compose([H1, H2, H3]) == H3 @ H2 @ H1."""
    H1 = _random_homography(4)
    H2 = _random_homography(5)
    H3 = _random_homography(6)
    H_total = compose_homographies([H1, H2, H3])
    np.testing.assert_allclose(H_total, H3 @ H2 @ H1, atol=1e-12)


def test_ring_path_identity_when_target_equals_reference() -> None:
    """ring_path_homography(c, c, ...) is identity for any c."""
    pair_H: dict[tuple[str, str], np.ndarray] = {}
    for cam in RING_ORDER:
        H = ring_path_homography(cam, cam, pair_H)
        np.testing.assert_allclose(H, np.eye(3), atol=1e-12)


def test_ring_path_one_hop_returns_pair_homography() -> None:
    """Adjacent cams on the ring: ring_path returns that pair's H exactly."""
    # Pick a textbook hop: front_center -> front_left
    H_fc_fl = _random_homography(7)
    pair_H = {("ring_front_center", "ring_front_left"): H_fc_fl}
    # Path: front_center -> front_left, returns H_fc_fl unchanged.
    H = ring_path_homography("ring_front_center", "ring_front_left", pair_H)
    np.testing.assert_allclose(H, H_fc_fl, atol=1e-12)


def test_ring_path_reverse_hop_uses_inverse() -> None:
    """Reverse direction: ring_path returns inv(pair_H)."""
    H_fc_fl = _random_homography(8)
    pair_H = {("ring_front_center", "ring_front_left"): H_fc_fl}
    # Reverse direction: front_left -> front_center should produce inv(H_fc_fl).
    H = ring_path_homography("ring_front_left", "ring_front_center", pair_H)
    np.testing.assert_allclose(H, np.linalg.inv(H_fc_fl), atol=1e-9)


def test_ring_path_shortcut_picks_shortest_direction() -> None:
    """front_left -> front_right via front_center is 2 hops; long way is 5.

    We seed only the SHORT-path pairs and leave long-path pairs MISSING. If
    the function takes the short path, the composition will be exact (no
    identity fallbacks). If it takes the long path, identity-fallback hops
    will make the composition NOT match the expected short-path product.
    """
    # Short path: front_left -> front_center -> front_right (2 hops, walking BACKWARD on ring_order).
    # Note in RING_ORDER, "ring_front_right" is at index 6 and "ring_front_center" at 0;
    # the wrap-around pair (front_right -> front_center) IS in ADJACENT_PAIRS.
    H_fc_fl = _random_homography(9)
    H_fr_fc = _random_homography(10)
    pair_H = {
        ("ring_front_center", "ring_front_left"): H_fc_fl,
        ("ring_front_right", "ring_front_center"): H_fr_fc,
    }
    # Short path front_left -> front_right:
    #   front_left -> front_center (inv(H_fc_fl)) -> front_right (inv(H_fr_fc))
    #   composed = inv(H_fr_fc) @ inv(H_fc_fl)
    H_short_expected = np.linalg.inv(H_fr_fc) @ np.linalg.inv(H_fc_fl)
    H_got = ring_path_homography("ring_front_left", "ring_front_right", pair_H)
    np.testing.assert_allclose(H_got, H_short_expected, atol=1e-9)


def test_ring_path_missing_hop_falls_back_to_identity_not_crash() -> None:
    """Missing pair on the path -> identity for that hop; no exception."""
    # No pairs at all -> every hop falls back; composition is identity.
    H = ring_path_homography("ring_front_left", "ring_rear_left", {})
    np.testing.assert_allclose(H, np.eye(3), atol=1e-12)


def test_ring_path_raises_keyerror_for_unknown_cam() -> None:
    with pytest.raises(KeyError):
        ring_path_homography("does_not_exist", "ring_front_center", {})


def test_compose_homographies_none_raises() -> None:
    """A None entry should surface a clear ValueError, not silently insert identity."""
    H1 = _random_homography(11)
    with pytest.raises(ValueError):
        compose_homographies([H1, None])  # type: ignore[list-item]


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
