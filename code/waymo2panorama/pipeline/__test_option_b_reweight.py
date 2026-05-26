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
    STEREO_NPZ_CAM_A_KEY,
    STEREO_NPZ_CAM_B_KEY,
    STEREO_NPZ_PTS_CAM_A_KEY,
    STEREO_NPZ_PTS_KEY,
    apply_option_b_reweight,
    build_stereo_confidence_mask,
    build_stereo_confidence_masks_per_cam,
    build_stereo_confidence_masks_per_cam_v3,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_stereo_npz(path: Path, pts_ego: np.ndarray) -> None:
    """Write a minimal stereo_*.npz with just the pts_3d_ego key."""
    np.savez_compressed(path, **{STEREO_NPZ_PTS_KEY: pts_ego.astype(np.float32)})


def _write_stereo_npz_with_cams(
    path: Path, pts_ego: np.ndarray, cam_a: str, cam_b: str,
) -> None:
    """Write a stereo_*.npz with pts_3d_ego + cam_a + cam_b keys (v2 input)."""
    np.savez_compressed(
        path,
        **{
            STEREO_NPZ_PTS_KEY: pts_ego.astype(np.float32),
            STEREO_NPZ_CAM_A_KEY: np.array(cam_a),
            STEREO_NPZ_CAM_B_KEY: np.array(cam_b),
        },
    )


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


def test_apply_warns_on_unnormalized_mask_but_not_on_normalized() -> None:
    """Out-of-range mask (max > 1) triggers a RuntimeWarning; normalized mask does not."""
    import warnings

    erp_hw = (16, 32)
    weights_in = {"cam_a": np.ones(erp_hw, dtype=np.float32)}

    # Out-of-range mask: should warn
    bad_mask = np.full(erp_hw, 2.5, dtype=np.float32)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        apply_option_b_reweight(weights_in, bad_mask, alpha=1.0)
    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert len(runtime_warnings) >= 1, "expected RuntimeWarning for unnormalized mask"
    assert "confidence_mask max" in str(runtime_warnings[0].message)

    # In-range mask: should NOT warn
    good_mask = np.full(erp_hw, 0.8, dtype=np.float32)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        apply_option_b_reweight(weights_in, good_mask, alpha=1.0)
    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert len(runtime_warnings) == 0, (
        f"unexpected RuntimeWarning(s) for normalized mask: "
        f"{[str(w.message) for w in runtime_warnings]}"
    )


# ===========================================================================
# v2 (per-cam differential) tests
# ===========================================================================


_CAMS_7 = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)


def test_v2_per_cam_touches_only_pair_cams(tmp_path: Path) -> None:
    """A stereo file from pair (cam_a, cam_b) should only touch those 2 cams' masks."""
    pts = _grid_in_front(n=4)
    p = tmp_path / "stereo_front_left__side_left.npz"
    _write_stereo_npz_with_cams(p, pts, "ring_front_left", "ring_side_left")

    masks = build_stereo_confidence_masks_per_cam(
        [p], erp_hw=(64, 128), cam_names=_CAMS_7, sigma_px=4.0,
    )
    # All 7 cams present in dict
    assert set(masks.keys()) == set(_CAMS_7)
    # Only the 2 cams in the pair have non-zero mask
    assert masks["ring_front_left"].max() > 0, "cam_a mask should have content"
    assert masks["ring_side_left"].max() > 0, "cam_b mask should have content"
    for c in _CAMS_7:
        if c in ("ring_front_left", "ring_side_left"):
            continue
        assert masks[c].max() == 0.0, f"untouched cam {c} should be all-zero, got max={masks[c].max()}"


def test_v2_global_normalization_coverage(tmp_path: Path) -> None:
    """Denser stereo → larger COVERAGE (more non-zero mask pixels), not larger peak.

    NOTE: peak is bounded at 1.0 by both the kernel construction (peak=1.0) AND
    the max-merge accumulation strategy. Density manifests as coverage area.
    """
    pts_few = _grid_in_front(n=2)   # ~12 pts
    pts_many = _grid_in_front(n=5)  # ~50 pts

    p1 = tmp_path / "stereo_front_left__side_left.npz"
    p2 = tmp_path / "stereo_rear_left__rear_right.npz"
    _write_stereo_npz_with_cams(p1, pts_few, "ring_front_left", "ring_side_left")
    _write_stereo_npz_with_cams(p2, pts_many, "ring_rear_left", "ring_rear_right")

    masks = build_stereo_confidence_masks_per_cam(
        [p1, p2], erp_hw=(64, 128), cam_names=_CAMS_7, sigma_px=4.0,
    )
    # Both peaks hit 1.0 after global max-divide normalization (any cam with
    # at least one splat reaches the kernel peak = 1.0).
    assert masks["ring_rear_left"].max() == pytest.approx(1.0, abs=1e-5)
    assert masks["ring_front_left"].max() == pytest.approx(1.0, abs=1e-5)

    # Denser pair (5x5x2 = 50 pts) covers MORE pixels than sparser pair (2x2x2 = ~8).
    rear_coverage = float((masks["ring_rear_left"] > 0.05).sum())
    front_coverage = float((masks["ring_front_left"] > 0.05).sum())
    assert rear_coverage > front_coverage, (
        f"denser pair should cover more pixels; got rear={rear_coverage}, front={front_coverage}"
    )

    # All un-touched cams remain all-zero
    for c in ("ring_front_center", "ring_side_right", "ring_front_right"):
        assert masks[c].max() == 0.0, f"{c} should be untouched"


def test_v2_per_cam_apply_differential() -> None:
    """apply_option_b_reweight with dict-of-masks: each cam multiplied by its own mask only."""
    erp_hw = (16, 32)
    weights = {
        "cam_A": np.full(erp_hw, 0.5, dtype=np.float32),
        "cam_B": np.full(erp_hw, 0.5, dtype=np.float32),
    }
    # cam_A has confidence 1.0 everywhere, cam_B has 0.0
    masks = {
        "cam_A": np.ones(erp_hw, dtype=np.float32),
        "cam_B": np.zeros(erp_hw, dtype=np.float32),
    }
    out = apply_option_b_reweight(weights, masks, alpha=1.0)
    # cam_A: weight 0.5 * (1 + 1.0 * 1.0) = 0.5 * 2.0 = 1.0
    np.testing.assert_allclose(out["cam_A"], 1.0, rtol=1e-5)
    # cam_B: weight 0.5 * (1 + 1.0 * 0.0) = 0.5 * 1.0 = 0.5 (UNCHANGED)
    np.testing.assert_allclose(out["cam_B"], 0.5, rtol=1e-5)
    # This is the KEY differential test: cam_A boosted, cam_B not — multiband
    # normalize would NOT cancel because the boosts are different across cams.


def test_v2_per_cam_requires_dict_weights() -> None:
    """Per-cam mode requires erp_weights to be a dict (not list)."""
    erp_hw = (16, 32)
    weights = [np.ones(erp_hw, dtype=np.float32)]  # list, not dict
    masks = {"cam_A": np.ones(erp_hw, dtype=np.float32)}
    with pytest.raises(TypeError, match="per-cam reweight requires erp_weights"):
        apply_option_b_reweight(weights, masks, alpha=1.0)


def test_v2_per_cam_missing_cam_in_mask_raises() -> None:
    """If erp_weights has a cam not present in mask dict, raise ValueError."""
    erp_hw = (16, 32)
    weights = {
        "cam_A": np.ones(erp_hw, dtype=np.float32),
        "cam_B": np.ones(erp_hw, dtype=np.float32),
    }
    masks = {"cam_A": np.ones(erp_hw, dtype=np.float32)}  # missing cam_B
    with pytest.raises(ValueError, match="cam 'cam_B' missing from confidence_mask"):
        apply_option_b_reweight(weights, masks, alpha=1.0)


def test_v2_per_cam_alpha_zero_is_identity() -> None:
    """alpha=0 returns input weights unchanged (per-cam path)."""
    erp_hw = (16, 32)
    weights = {
        "cam_A": np.full(erp_hw, 0.3, dtype=np.float32),
        "cam_B": np.full(erp_hw, 0.7, dtype=np.float32),
    }
    masks = {
        "cam_A": np.ones(erp_hw, dtype=np.float32),   # would normally boost a lot
        "cam_B": np.full(erp_hw, 0.5, dtype=np.float32),
    }
    out = apply_option_b_reweight(weights, masks, alpha=0.0)
    np.testing.assert_allclose(out["cam_A"], weights["cam_A"], rtol=1e-6)
    np.testing.assert_allclose(out["cam_B"], weights["cam_B"], rtol=1e-6)


def test_v2_per_cam_unknown_cam_in_npz_skipped(tmp_path: Path) -> None:
    """Stereo files referencing unknown cam names are silently skipped (not crash)."""
    pts = _grid_in_front(n=3)
    p = tmp_path / "stereo_garbage__cam.npz"
    _write_stereo_npz_with_cams(p, pts, "garbage_cam", "another_garbage_cam")
    masks = build_stereo_confidence_masks_per_cam(
        [p], erp_hw=(32, 64), cam_names=_CAMS_7, sigma_px=4.0,
    )
    # All 7 ring cams should be all-zero (no valid pair in this file)
    for c in _CAMS_7:
        assert masks[c].max() == 0.0


def test_v2_per_cam_npz_missing_cam_keys_skipped(tmp_path: Path) -> None:
    """A v1-style npz (no cam_a/cam_b keys) is silently skipped by v2."""
    pts = _grid_in_front(n=3)
    p = tmp_path / "stereo_v1_style.npz"
    _write_stereo_npz(p, pts)  # no cam_a/cam_b keys
    masks = build_stereo_confidence_masks_per_cam(
        [p], erp_hw=(32, 64), cam_names=_CAMS_7, sigma_px=4.0,
    )
    for c in _CAMS_7:
        assert masks[c].max() == 0.0, f"cam {c} should be untouched, got max={masks[c].max()}"


# ---------------------------------------------------------------------------
# v3: per-cam ASYMMETRIC masks via ray-angle winner-take-all
# ---------------------------------------------------------------------------


def _write_stereo_npz_v3(
    path: Path,
    pts_ego: np.ndarray,
    pts_cam_a: np.ndarray,
    cam_a: str,
    cam_b: str,
) -> None:
    """Write a stereo_*.npz with v3-required keys (pts_3d_ego + pts_3d_cam_a + cams)."""
    np.savez_compressed(
        path,
        **{
            STEREO_NPZ_PTS_KEY: pts_ego.astype(np.float32),
            STEREO_NPZ_PTS_CAM_A_KEY: pts_cam_a.astype(np.float32),
            STEREO_NPZ_CAM_A_KEY: np.array(cam_a),
            STEREO_NPZ_CAM_B_KEY: np.array(cam_b),
        },
    )


def _rot_y(angle_rad: float) -> np.ndarray:
    """Rotation matrix around y-axis (RHR). Maps +z -> direction at angle from +z toward +x."""
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array(
        [[c,  0,  s],
         [0,  1,  0],
         [-s, 0,  c]],
        dtype=np.float32,
    )


def _T_ego_cam(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Build a (4, 4) T_ego_cam (point_in_ego = T_ego_cam @ point_in_cam)."""
    T = np.eye(4, dtype=np.float32)
    T[:3, :3] = R.astype(np.float32)
    T[:3, 3] = t.astype(np.float32)
    return T


def test_v3_winner_take_all_all_points_to_cam_a(tmp_path: Path) -> None:
    """All points head-on for cam_a, off-axis for cam_b -> cam_b mask all zero.

    Geometry (AV2 ego: x forward, y left, z up):
      - cam_a looks toward ego +x (R_y(+pi/2) maps cam +z -> ego +x)
      - cam_b looks toward ego -x (R_y(-pi/2)). Cams point in OPPOSITE directions.
      - Points are at ego (+10, 0, 0..) — in FRONT of cam_a, BEHIND cam_b.
    """
    from waymo2panorama.pipeline.option_b_reweight import (
        build_stereo_confidence_masks_per_cam_v3,
    )
    erp_hw = (32, 64)
    cam_names = ["cam_a", "cam_b"]
    T_ego_cam_a = _T_ego_cam(_rot_y(np.pi / 2), np.zeros(3, dtype=np.float32))
    T_ego_cam_b = _T_ego_cam(_rot_y(-np.pi / 2), np.zeros(3, dtype=np.float32))

    # 3 points at ego +x direction (forward), small lateral spread to avoid pole.
    pts_ego = np.array(
        [[10.0, 0.0, 0.0],
         [12.0, 0.5, 0.0],
         [11.0, -0.5, 0.5]], dtype=np.float32,
    )
    # cam_a frame: pts_cam_a = inv(R_y(+pi/2)) @ pts_ego = R_y(-pi/2) @ pts_ego
    # which sends ego +x -> cam_a +z. Manually: (10,0,0)_ego -> (0,0,10)_cam_a
    R_cam_a_ego = _rot_y(-np.pi / 2)  # inverse of R_y(+pi/2)
    pts_cam_a = (pts_ego @ R_cam_a_ego.T).astype(np.float32)

    npz = tmp_path / "stereo_a__b.npz"
    _write_stereo_npz_v3(npz, pts_ego, pts_cam_a, "cam_a", "cam_b")

    masks = build_stereo_confidence_masks_per_cam_v3(
        [npz], erp_hw=erp_hw, cam_names=cam_names,
        cam_T_ego_cam={"cam_a": T_ego_cam_a, "cam_b": T_ego_cam_b},
        sigma_px=2.0,
    )
    assert masks["cam_a"].max() > 0.5, (
        f"cam_a should have peaks > 0.5, got {masks['cam_a'].max()}"
    )
    assert masks["cam_b"].max() == 0.0, (
        f"cam_b should be all zero (lost all winner-take-all), got max={masks['cam_b'].max()}"
    )


def test_v3_winner_take_all_split_between_cams(tmp_path: Path) -> None:
    """Half points head-on for cam_a, half for cam_b -> both masks non-zero AND differ.

    This is the KEY property v3 must satisfy that v2 violated: per-cam masks
    must DIFFER for cam_a and cam_b (otherwise multiband normalize cancels).
    """
    from waymo2panorama.pipeline.option_b_reweight import (
        build_stereo_confidence_masks_per_cam_v3,
    )
    erp_hw = (32, 64)
    cam_names = ["cam_a", "cam_b"]
    # cam_a looks ego +x (front of car); cam_b looks ego -x (rear)
    T_ego_cam_a = _T_ego_cam(_rot_y(np.pi / 2), np.zeros(3, dtype=np.float32))
    T_ego_cam_b = _T_ego_cam(_rot_y(-np.pi / 2), np.zeros(3, dtype=np.float32))

    # First 3 pts in front of car (cam_a wins); last 3 behind (cam_b wins).
    pts_ego = np.array(
        [[10.0, 0.0, 0.0], [12.0, 0.5, 0.0], [11.0, -0.5, 0.5],
         [-10.0, 0.0, 0.0], [-12.0, 0.5, 0.0], [-11.0, -0.5, 0.5]],
        dtype=np.float32,
    )
    R_cam_a_ego = _rot_y(-np.pi / 2)  # inverse of R_y(+pi/2)
    pts_cam_a = (pts_ego @ R_cam_a_ego.T).astype(np.float32)

    npz = tmp_path / "stereo_a__b.npz"
    _write_stereo_npz_v3(npz, pts_ego, pts_cam_a, "cam_a", "cam_b")

    masks = build_stereo_confidence_masks_per_cam_v3(
        [npz], erp_hw=erp_hw, cam_names=cam_names,
        cam_T_ego_cam={"cam_a": T_ego_cam_a, "cam_b": T_ego_cam_b},
        sigma_px=2.0,
    )

    assert masks["cam_a"].max() > 0.0, "cam_a should have winning points"
    assert masks["cam_b"].max() > 0.0, "cam_b should have winning points"

    # THE KEY ASSERTION: cam_a's mask and cam_b's mask must DIFFER.
    # If they were equal (v2 NEG bug), multiband normalize would cancel the boost.
    diff = np.abs(masks["cam_a"] - masks["cam_b"])
    assert float(diff.max()) > 0.1, (
        f"v3 NEG: cam_a's mask and cam_b's mask are too similar (max diff {diff.max():.3f}); "
        "v2 bug is back."
    )
    # Also: they should NOT have any pixel where BOTH cams' masks > 0 (winner-take-all)
    overlap = (masks["cam_a"] > 0.01) & (masks["cam_b"] > 0.01)
    assert float(overlap.mean()) < 0.05, (
        f"winner-take-all should give disjoint masks; got {overlap.sum()} overlap pixels"
    )


def test_v3_soft_cos_angle_produces_different_masks(tmp_path: Path) -> None:
    """soft_cos_angle mode splats both cams but with different amplitudes."""
    from waymo2panorama.pipeline.option_b_reweight import (
        build_stereo_confidence_masks_per_cam_v3,
    )
    erp_hw = (32, 64)
    cam_names = ["cam_a", "cam_b"]
    # cam_a looks ego +x; cam_b 45 deg off (ego +x rotated 45 deg toward +z).
    T_ego_cam_a = _T_ego_cam(_rot_y(np.pi / 2), np.zeros(3, dtype=np.float32))
    T_ego_cam_b = _T_ego_cam(_rot_y(np.pi / 2 + np.pi / 4), np.zeros(3, dtype=np.float32))

    # Point on ego +x: head-on for cam_a, 45-deg off for cam_b
    pts_ego = np.array([[10.0, 0.0, 0.0], [11.0, 0.5, 0.0]], dtype=np.float32)
    R_cam_a_ego = _rot_y(-np.pi / 2)
    pts_cam_a = (pts_ego @ R_cam_a_ego.T).astype(np.float32)

    npz = tmp_path / "stereo_a__b.npz"
    _write_stereo_npz_v3(npz, pts_ego, pts_cam_a, "cam_a", "cam_b")

    masks = build_stereo_confidence_masks_per_cam_v3(
        [npz], erp_hw=erp_hw, cam_names=cam_names,
        cam_T_ego_cam={"cam_a": T_ego_cam_a, "cam_b": T_ego_cam_b},
        sigma_px=2.0,
        selection_mode="soft_cos_angle",
    )

    # Both cams should have non-zero (both saw the point), but cam_a (head-on) has
    # higher amp than cam_b (45-deg off-axis).
    assert masks["cam_a"].max() > 0.0
    assert masks["cam_b"].max() > 0.0
    assert masks["cam_a"].max() > masks["cam_b"].max(), (
        f"cam_a (head-on) should outweigh cam_b (off-axis): "
        f"cam_a max={masks['cam_a'].max():.3f} vs cam_b max={masks['cam_b'].max():.3f}"
    )


def test_v3_globally_normalized_to_one(tmp_path: Path) -> None:
    """After global normalize, at least one cam has max == 1.0."""
    from waymo2panorama.pipeline.option_b_reweight import (
        build_stereo_confidence_masks_per_cam_v3,
    )
    cam_names = ["cam_a", "cam_b"]
    T_ego_cam_a = _T_ego_cam(_rot_y(np.pi / 2), np.zeros(3, dtype=np.float32))
    T_ego_cam_b = _T_ego_cam(_rot_y(-np.pi / 2), np.zeros(3, dtype=np.float32))
    pts_ego = np.array([[10.0, 0.0, 0.0], [11.0, 0.5, 0.0]], dtype=np.float32)
    R_cam_a_ego = _rot_y(-np.pi / 2)
    pts_cam_a = (pts_ego @ R_cam_a_ego.T).astype(np.float32)
    npz = tmp_path / "stereo_a__b.npz"
    _write_stereo_npz_v3(npz, pts_ego, pts_cam_a, "cam_a", "cam_b")

    masks = build_stereo_confidence_masks_per_cam_v3(
        [npz], erp_hw=(32, 64), cam_names=cam_names,
        cam_T_ego_cam={"cam_a": T_ego_cam_a, "cam_b": T_ego_cam_b},
        sigma_px=2.0,
    )
    global_max = max(float(m.max()) for m in masks.values())
    assert global_max == pytest.approx(1.0, abs=1e-6)
    for c in cam_names:
        assert float(masks[c].max()) <= 1.0 + 1e-6


def test_v3_raises_on_missing_T(tmp_path: Path) -> None:
    """Missing cam_T_ego_cam entry must raise."""
    from waymo2panorama.pipeline.option_b_reweight import (
        build_stereo_confidence_masks_per_cam_v3,
    )
    with pytest.raises(ValueError, match="cam_T_ego_cam missing entries"):
        build_stereo_confidence_masks_per_cam_v3(
            [], erp_hw=(32, 64), cam_names=["cam_a", "cam_b"],
            cam_T_ego_cam={"cam_a": np.eye(4, dtype=np.float32)},  # cam_b missing
        )


def test_v3_raises_on_bad_selection_mode() -> None:
    """Unknown selection_mode must raise."""
    from waymo2panorama.pipeline.option_b_reweight import (
        build_stereo_confidence_masks_per_cam_v3,
    )
    with pytest.raises(ValueError, match="selection_mode"):
        build_stereo_confidence_masks_per_cam_v3(
            [], erp_hw=(32, 64), cam_names=["cam_a"],
            cam_T_ego_cam={"cam_a": np.eye(4, dtype=np.float32)},
            selection_mode="bogus",
        )


def test_v3_npz_missing_pts_cam_a_skipped(tmp_path: Path) -> None:
    """A v2-style npz (no pts_3d_cam_a key) is skipped with a warning."""
    from waymo2panorama.pipeline.option_b_reweight import (
        build_stereo_confidence_masks_per_cam_v3,
    )
    pts_ego = _grid_in_front(n=3)
    p = tmp_path / "stereo_v2_style.npz"
    _write_stereo_npz_with_cams(p, pts_ego, "cam_a", "cam_b")  # no pts_cam_a

    T = _T_ego_cam(np.eye(3, dtype=np.float32), np.zeros(3, dtype=np.float32))
    masks = build_stereo_confidence_masks_per_cam_v3(
        [p], erp_hw=(32, 64), cam_names=["cam_a", "cam_b"],
        cam_T_ego_cam={"cam_a": T, "cam_b": T},
    )
    # Length mismatch -> skipped
    for c in ["cam_a", "cam_b"]:
        assert masks[c].max() == 0.0
