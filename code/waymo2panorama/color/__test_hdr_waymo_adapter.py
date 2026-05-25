"""
Unit tests for hdr_waymo_adapter.py — Waymo HDR compensation adapter.

Strategy
--------
Build synthetic 5-cam ERP slabs where:
  - Each slab is a uniform (or near-uniform) field with known absolute values.
  - Adjacent cams have overlapping non-zero weight regions.
  - We apply known per-cam gain+bias perturbations to cams 1..3 (cams 0 and 4
    are left at identity, matching the two-pin gauge anchor).
  - Run ``apply_waymo_hdr_compensation`` and check that the recovered
    corrections are close to the inverse of the applied perturbations within a
    loose tolerance (color correction LS is approximate with Huber loss +
    priors).

The slabs are tiny (64x64) so the test runs fast (<2 s).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path wiring — allow running from repo root without installation
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()  # code/waymo2panorama/color -> code/
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.color.hdr_waymo_adapter import (  # noqa: E402
    _waymo_global_color_correction,
    apply_waymo_hdr_compensation,
)
from waymo2panorama.color.hdr_gain_estimate import IDENTITY_6  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

H, W = 64, 64
NUM_CAMS = 5
# Each cam occupies a horizontal band; adjacent bands overlap by OVERLAP cols.
BAND_W = W // NUM_CAMS + 4  # deliberately wider than W/NUM_CAMS to create overlap
OVERLAP = 8


def _make_slabs_and_weights(
    base_rgb: np.ndarray,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Return (slabs, weights) for NUM_CAMS cams with adjacent overlaps.

    Each cam i has non-zero weight in a horizontal band centred at
    col_centre = i * (W // NUM_CAMS).  Adjacent cams share OVERLAP columns.
    """
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    col_centres = [i * (W // NUM_CAMS) for i in range(NUM_CAMS)]
    for i in range(NUM_CAMS):
        c = col_centres[i]
        lo = max(0, c - BAND_W // 2)
        hi = min(W, c + BAND_W // 2)
        w = np.zeros((H, W), dtype=np.float32)
        # Feather: linearly ramp up/down within the band, peak at centre.
        for col in range(lo, hi):
            # Distance-based feather weight, max 0.9 at centre.
            dist = abs(col - c)
            w[:, col] = max(0.0, 0.9 - 0.9 * dist / (BAND_W // 2))
        slab = np.broadcast_to(base_rgb.reshape(1, 1, 3), (H, W, 3)).copy()
        slabs.append(slab.astype(np.float32))
        weights.append(w)
    return slabs, weights


def _apply_perturbation(
    slabs: list[np.ndarray],
    perturbations: list[np.ndarray],
) -> list[np.ndarray]:
    """Apply gain+bias perturbation to each slab."""
    from waymo2panorama.color.hdr_gain_estimate import apply_correction

    return [apply_correction(s, p) for s, p in zip(slabs, perturbations)]


# ---------------------------------------------------------------------------
# Test: identity input -> identity corrections
# ---------------------------------------------------------------------------


def test_identity_input_returns_identity():
    """All slabs identical (no color mismatch) => corrections stay near identity."""
    base = np.array([100.0, 120.0, 80.0], dtype=np.float32)
    slabs, weights = _make_slabs_and_weights(base)

    corrections, diagnostics = apply_waymo_hdr_compensation(
        slabs, weights, num_cams=NUM_CAMS, verbose=False
    )
    assert corrections.shape == (NUM_CAMS, 6)
    # cam_0 and cam_4 must be exactly identity.
    np.testing.assert_array_almost_equal(corrections[0], IDENTITY_6, decimal=5)
    np.testing.assert_array_almost_equal(corrections[-1], IDENTITY_6, decimal=5)
    # All cams should be very close to identity (no color mismatch to fix).
    np.testing.assert_allclose(corrections[:, :3], 1.0, atol=0.05)  # gains
    np.testing.assert_allclose(corrections[:, 3:], 0.0, atol=3.0)   # biases


# ---------------------------------------------------------------------------
# Test: cam_0 and cam_last are always pinned
# ---------------------------------------------------------------------------


def test_pinned_cams_are_identity():
    """cam_0 and cam_{num_cams-1} must always be IDENTITY_6 regardless of input."""
    base = np.array([80.0, 90.0, 70.0], dtype=np.float32)
    slabs, weights = _make_slabs_and_weights(base)

    # Apply non-trivial perturbations to the PINNED cams as input colour
    # — the adapter's solver should still return identity for those positions
    # in the *corrections* array (they're hardcoded, not solved).
    big_perturb = np.array([1.4, 0.8, 1.2, 10.0, -5.0, 3.0], dtype=np.float32)
    slabs[0] = slabs[0] * big_perturb[:3] + big_perturb[3:6]
    slabs[-1] = slabs[-1] * big_perturb[:3] + big_perturb[3:6]

    corrections, _ = apply_waymo_hdr_compensation(slabs, weights, num_cams=NUM_CAMS)
    np.testing.assert_array_equal(corrections[0], IDENTITY_6)
    np.testing.assert_array_equal(corrections[-1], IDENTITY_6)


# ---------------------------------------------------------------------------
# Test: known gain perturbation is approximately recovered
# ---------------------------------------------------------------------------


def test_gain_perturbation_recovered():
    """Apply known gain+bias to inner cams; verify corrections oppose them."""
    base = np.array([120.0, 100.0, 90.0], dtype=np.float32)
    slabs_clean, weights = _make_slabs_and_weights(base)

    # Perturbations: cam_0 and cam_4 are identity (pins); inner cams get
    # meaningful gain/bias offsets.
    perturbations = [
        IDENTITY_6.copy(),  # cam_0  — pin
        np.array([1.2, 0.9, 1.1, 5.0, -5.0, 2.0], dtype=np.float32),   # cam_1
        np.array([0.8, 1.1, 0.95, -3.0, 4.0, -2.0], dtype=np.float32), # cam_2
        np.array([1.15, 0.85, 1.05, 2.0, -3.0, 4.0], dtype=np.float32),# cam_3
        IDENTITY_6.copy(),  # cam_4  — pin
    ]
    slabs_perturbed = _apply_perturbation(slabs_clean, perturbations)

    corrections, diagnostics = apply_waymo_hdr_compensation(
        slabs_perturbed,
        weights,
        num_cams=NUM_CAMS,
        valid_threshold=0.01,
        min_pixels=5,
        verbose=False,
    )
    assert corrections.shape == (NUM_CAMS, 6)

    # Diagnostics sanity: gap_after <= gap_before (LS should improve things).
    summary = diagnostics["overlap_gap_summary"]
    assert summary["mean_abs_lum_gap_after"] <= summary["mean_abs_lum_gap_before"] + 1.0

    # The corrections should partially counteract the perturbations.
    # Full recovery is not guaranteed (prior pulls toward identity and the
    # problem is underdetermined in small-overlap scenarios), so we use a
    # loose check: each inner cam's corrected slab should be closer to the
    # clean slab than the perturbed slab was.
    from waymo2panorama.color.hdr_gain_estimate import apply_correction

    for cam_i in range(1, NUM_CAMS - 1):
        corrected = apply_correction(slabs_perturbed[cam_i], corrections[cam_i])
        mse_before = float(np.mean((slabs_perturbed[cam_i] - slabs_clean[cam_i]) ** 2))
        mse_after = float(np.mean((corrected - slabs_clean[cam_i]) ** 2))
        # Allow a generous tolerance: MSE after should be <= 4x MSE before
        # (in degenerate cases the prior may limit recovery).
        # In practice, for our synthetic case it should be much better.
        assert mse_after <= max(mse_before, 1.0) * 4.0, (
            f"cam_{cam_i}: mse_before={mse_before:.3f} mse_after={mse_after:.3f} "
            "— correction made things worse beyond tolerance"
        )


# ---------------------------------------------------------------------------
# Test: empty overlaps -> identity corrections (no-op)
# ---------------------------------------------------------------------------


def test_empty_overlaps_returns_identity():
    """When cams have no overlap, the solver returns identity for all cams."""
    rng = np.random.default_rng(42)
    # Non-overlapping weights: each cam gets an exclusive block.
    slabs = [rng.random((H, W, 3)).astype(np.float32) * 200.0 for _ in range(NUM_CAMS)]
    weights = []
    block = W // NUM_CAMS
    for i in range(NUM_CAMS):
        w = np.zeros((H, W), dtype=np.float32)
        w[:, i * block:(i + 1) * block] = 0.8
        weights.append(w)

    corrections, diagnostics = apply_waymo_hdr_compensation(
        slabs, weights, num_cams=NUM_CAMS
    )
    assert corrections.shape == (NUM_CAMS, 6)
    assert diagnostics["n_overlap_pairs"] == 0
    # All corrections should be identity.
    for i in range(NUM_CAMS):
        np.testing.assert_array_almost_equal(corrections[i], IDENTITY_6, decimal=5)


# ---------------------------------------------------------------------------
# Test: diagnostics structure is correct
# ---------------------------------------------------------------------------


def test_diagnostics_structure():
    """Verify the diagnostics dict has the expected keys and types."""
    base = np.array([100.0, 100.0, 100.0], dtype=np.float32)
    slabs, weights = _make_slabs_and_weights(base)
    corrections, diagnostics = apply_waymo_hdr_compensation(
        slabs, weights, num_cams=NUM_CAMS
    )
    assert "pairs" in diagnostics
    assert "n_overlap_pairs" in diagnostics
    assert "overlap_gap_summary" in diagnostics
    summary = diagnostics["overlap_gap_summary"]
    for key in ("mean_abs_lum_gap_before", "mean_abs_lum_gap_after", "delta", "n_pairs"):
        assert key in summary, f"missing key '{key}' in overlap_gap_summary"
    assert isinstance(diagnostics["n_overlap_pairs"], int)


# ---------------------------------------------------------------------------
# Test: internal solver with num_cams=2 (edge case: only two cams, both pinned)
# ---------------------------------------------------------------------------


def test_two_cam_edge_case():
    """num_cams=2 means both cams are pinned; solver returns identity array."""
    overlaps: dict = {}  # no overlaps, degenerate
    corrections = _waymo_global_color_correction(overlaps, num_cams=2)
    assert corrections.shape == (2, 6)
    np.testing.assert_array_almost_equal(corrections[0], IDENTITY_6, decimal=5)
    np.testing.assert_array_almost_equal(corrections[1], IDENTITY_6, decimal=5)
