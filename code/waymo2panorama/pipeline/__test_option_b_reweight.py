"""
Unit tests for option_b_reweight.py — 新-D stereo confidence -> L1 weight boost.

These tests verify the *contract* of the two public functions on synthetic
inputs (no real stereo cache or Pi3 cache needed). End-to-end correctness
(does the actual cycle-PSNR improve?) is the job of the Colab eval run.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path wiring — allow running from repo root without installation
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()  # code/waymo2panorama/pipeline -> code/
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.pipeline.lift_and_project import ego_points_to_erp_uv  # noqa: E402
from waymo2panorama.pipeline.option_b_reweight import (  # noqa: E402
    STEREO_NPZ_PTS_KEY,
    apply_option_b_reweight,
    build_stereo_confidence_mask,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_stereo_npz(path: Path, pts_ego: np.ndarray) -> None:
    """Write a minimal stereo_*.npz with just the pts_3d_ego key."""
    np.savez_compressed(path, **{STEREO_NPZ_PTS_KEY: pts_ego.astype(np.float32)})


def _grid_in_front(n: int = 6) -> np.ndarray:
    """A small dense-ish 3D grid in front of ego (+x), all at distance ~10-30 m."""
    xs = np.linspace(8.0, 24.0, n)        # forward
    ys = np.linspace(-3.0, 3.0, n)        # left/right
    zs = np.linspace(-1.0, 1.5, max(2, n // 2))  # up/down (small range)
    xx, yy, zz = np.meshgrid(xs, ys, zs, indexing="ij")
    return np.stack([xx, yy, zz], axis=-1).reshape(-1, 3).astype(np.float32)


# ---------------------------------------------------------------------------
# build_stereo_confidence_mask
# ---------------------------------------------------------------------------


def test_mask_shape_and_range_with_real_points(tmp_path: Path) -> None:
    """Mask has correct shape, dtype, and values in [0, 1]."""
    erp_hw = (128, 256)
    pts = _grid_in_front(n=5)
    npz_path = tmp_path / "stereo_a__b.npz"
    _write_stereo_npz(npz_path, pts)

    mask = build_stereo_confidence_mask([npz_path], erp_hw=erp_hw, sigma_px=4.0)

    assert mask.shape == erp_hw
    assert mask.dtype == np.float32
    assert float(mask.min()) >= 0.0
    assert float(mask.max()) <= 1.0
    # With nonempty input, max should be exactly 1.0 (the normalization divisor)
    assert float(mask.max()) == pytest.approx(1.0, abs=1e-6)
    # Coverage should be non-trivial but well below 100% (sparse splats)
    assert (mask > 0.1).mean() > 0.0
    assert (mask > 0.1).mean() < 0.5


def test_mask_peaks_align_with_projected_points(tmp_path: Path) -> None:
    """Splatted points should produce mask peaks at the expected ERP coords."""
    erp_hw = (128, 256)
    # Single point straight ahead at distance 10 m: should land at the ERP center column
    pts = np.array([[10.0, 0.0, 0.0]], dtype=np.float32)
    npz_path = tmp_path / "stereo_a__b.npz"
    _write_stereo_npz(npz_path, pts)

    mask = build_stereo_confidence_mask([npz_path], erp_hw=erp_hw, sigma_px=4.0)

    # Compute expected projection
    u_f, v_f, valid = ego_points_to_erp_uv(pts, erp_hw=erp_hw)
    assert bool(valid[0])
    u_expect = int(round(float(u_f[0]))) % erp_hw[1]
    v_expect = int(round(float(v_f[0])))

    # Mask max should be at (or within sigma of) the expected pixel
    vy, vx = np.unravel_index(int(np.argmax(mask)), mask.shape)
    # Distance from peak to expected (with wrap on u)
    du = min(abs(vx - u_expect), erp_hw[1] - abs(vx - u_expect))
    dv = abs(vy - v_expect)
    # Should be within 1 pixel of the rounded projection
    assert du <= 1, f"peak u-offset {du} > 1 (got vx={vx}, expected {u_expect})"
    assert dv <= 1, f"peak v-offset {dv} > 1 (got vy={vy}, expected {v_expect})"


def test_mask_empty_input_returns_zero_mask() -> None:
    """No paths -> all-zero mask of correct shape."""
    mask = build_stereo_confidence_mask([], erp_hw=(64, 128), sigma_px=4.0)
    assert mask.shape == (64, 128)
    assert mask.dtype == np.float32
    assert float(mask.max()) == 0.0


def test_mask_handles_zero_inliers_file(tmp_path: Path) -> None:
    """A .npz with 0 inliers gets skipped, not raised."""
    npz_path = tmp_path / "stereo_empty.npz"
    _write_stereo_npz(npz_path, np.zeros((0, 3), dtype=np.float32))

    mask = build_stereo_confidence_mask([npz_path], erp_hw=(64, 128), sigma_px=4.0)
    assert mask.shape == (64, 128)
    assert float(mask.max()) == 0.0


def test_mask_wraps_horizontally(tmp_path: Path) -> None:
    """A point near the back of ego (-x) should splat near the ERP horizontal seam.

    With our ERP convention `u = (pi - theta) * W / (2pi) - 0.5`, theta=pi maps
    to u~=0 and theta=-pi maps to u~=W. A point directly behind ego (-x, 0, 0)
    has theta = atan2(0, -1) = pi -> u near 0, with the splat needing to wrap.
    The test passes if there are NO unexpected zero columns inside the splat
    footprint (i.e. wrap actually carried the kernel across u=0).
    """
    erp_hw = (64, 256)
    pts = np.array([[-10.0, 0.0, 0.0]], dtype=np.float32)
    npz_path = tmp_path / "stereo_back.npz"
    _write_stereo_npz(npz_path, pts)

    mask = build_stereo_confidence_mask([npz_path], erp_hw=erp_hw, sigma_px=6.0)
    # Mass should be positive on BOTH sides of u=0 (within sigma_px)
    left_mass = float(mask[:, -10:].sum())
    right_mass = float(mask[:, :10].sum())
    assert left_mass > 0 or right_mass > 0, "no mass near horizontal seam"


# ---------------------------------------------------------------------------
# apply_option_b_reweight
# ---------------------------------------------------------------------------


def test_apply_does_not_mutate_input_dict() -> None:
    """Returned dict is a new object; inputs are unchanged."""
    erp_hw = (32, 64)
    weights_in = {
        "ring_front_center": np.ones(erp_hw, dtype=np.float32) * 0.5,
        "ring_front_left":   np.full(erp_hw, 0.3, dtype=np.float32),
    }
    snapshot = {k: v.copy() for k, v in weights_in.items()}
    mask = np.full(erp_hw, 0.4, dtype=np.float32)

    out = apply_option_b_reweight(weights_in, mask, alpha=1.0)

    assert isinstance(out, dict)
    assert out is not weights_in
    for k in weights_in:
        assert out[k] is not weights_in[k], f"{k} weight array was reused"
        np.testing.assert_array_equal(weights_in[k], snapshot[k])


def test_apply_formula_correct() -> None:
    """Output equals input * (1 + alpha * confidence_mask)."""
    erp_hw = (16, 32)
    rng = np.random.default_rng(42)
    weights_in = {
        "ring_front_center": rng.random(erp_hw).astype(np.float32),
        "ring_side_left":    rng.random(erp_hw).astype(np.float32),
    }
    mask = rng.random(erp_hw).astype(np.float32) * 0.7  # values in [0, 0.7]
    alpha = 1.5

    out = apply_option_b_reweight(weights_in, mask, alpha=alpha)

    expected_boost = (1.0 + alpha * mask).astype(np.float32)
    for k, w in weights_in.items():
        np.testing.assert_allclose(out[k], w * expected_boost, rtol=1e-6, atol=1e-6)


def test_apply_alpha_zero_is_identity() -> None:
    """alpha=0 returns weights equal to input (modulo float casting)."""
    erp_hw = (16, 32)
    rng = np.random.default_rng(0)
    weights_in = {
        "cam_a": rng.random(erp_hw).astype(np.float32),
        "cam_b": rng.random(erp_hw).astype(np.float32),
    }
    mask = rng.random(erp_hw).astype(np.float32)

    out = apply_option_b_reweight(weights_in, mask, alpha=0.0)
    for k, w in weights_in.items():
        np.testing.assert_allclose(out[k], w, rtol=1e-6, atol=1e-6)


def test_apply_list_input_preserves_order() -> None:
    """List input returns list of the same length and order."""
    erp_hw = (16, 32)
    weights_in = [
        np.full(erp_hw, 0.1, dtype=np.float32),
        np.full(erp_hw, 0.7, dtype=np.float32),
        np.full(erp_hw, 0.3, dtype=np.float32),
    ]
    snapshot = [w.copy() for w in weights_in]
    mask = np.full(erp_hw, 0.5, dtype=np.float32)

    out = apply_option_b_reweight(weights_in, mask, alpha=2.0)
    assert isinstance(out, list)
    assert len(out) == len(weights_in)
    # Original list not mutated
    for w, s in zip(weights_in, snapshot):
        np.testing.assert_array_equal(w, s)
    # Boost applied
    expected_boost = 1.0 + 2.0 * 0.5  # = 2.0
    for w_in, w_out in zip(weights_in, out):
        np.testing.assert_allclose(w_out, w_in * expected_boost, rtol=1e-6, atol=1e-6)


def test_apply_shape_mismatch_raises() -> None:
    """Mismatched weight / mask shapes raise ValueError."""
    weights_in = {"cam_a": np.ones((16, 32), dtype=np.float32)}
    mask = np.ones((8, 16), dtype=np.float32)
    with pytest.raises(ValueError):
        apply_option_b_reweight(weights_in, mask, alpha=1.0)


def test_apply_negative_alpha_raises() -> None:
    weights_in = {"cam_a": np.ones((16, 32), dtype=np.float32)}
    mask = np.zeros((16, 32), dtype=np.float32)
    with pytest.raises(ValueError):
        apply_option_b_reweight(weights_in, mask, alpha=-0.5)
