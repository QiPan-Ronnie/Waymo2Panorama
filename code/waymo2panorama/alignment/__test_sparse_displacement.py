"""Unit tests for alignment/sparse_displacement.py — A2 module."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.alignment.sparse_displacement import (  # noqa: E402
    _compute_l1_erp_pixel_per_cam,
)


def test_l1_erp_pixel_for_distant_point_matches_ideal():
    """For a point far from cam center, L1 ERP location ≈ ideal ERP location."""
    K = np.array([[500.0, 0, 252.0], [0, 500.0, 252.0], [0, 0, 1.0]], dtype=np.float64)
    # cam looks forward (+x in ego, so cam +z = ego +x)
    R_ego_cam = np.array([
        [0, 0, 1],
        [-1, 0, 0],
        [0, -1, 0],
    ], dtype=np.float64)
    t_ego_cam = np.array([0.5, 0, 0], dtype=np.float64)  # cam 0.5m in front of ego
    T_ego_cam = np.eye(4); T_ego_cam[:3, :3] = R_ego_cam; T_ego_cam[:3, 3] = t_ego_cam
    pt_far = np.array([100.0, 0.0, 0.0], dtype=np.float64)  # 100m forward
    erp_hw = (1024, 2048)
    l1_uv = _compute_l1_erp_pixel_per_cam(pt_far, K, T_ego_cam, erp_hw)
    # Ideal: point at +x direction lands at theta=0 → u ≈ W/2
    assert abs(l1_uv[0] - erp_hw[1] / 2.0) < 2.0, f"u off: {l1_uv}"


def test_displacements_zero_for_distant_synthetic_pts(tmp_path):
    """If all stereo pts are far away, per-cam displacements ~ 0."""
    from waymo2panorama.alignment.sparse_displacement import (
        build_per_cam_displacements_from_stereo,
    )
    from waymo2panorama.pipeline.option_b_reweight import (
        STEREO_NPZ_PTS_KEY, STEREO_NPZ_CAM_A_KEY, STEREO_NPZ_CAM_B_KEY,
    )
    # Same K + T_ego_cam as task 1.1
    K = np.array([[500.0, 0, 252.0], [0, 500.0, 252.0], [0, 0, 1.0]])
    R = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]], dtype=np.float64)
    T_a = np.eye(4); T_a[:3, :3] = R; T_a[:3, 3] = [0.5, 0, 0]
    T_b = np.eye(4); T_b[:3, :3] = R; T_b[:3, 3] = [0.5, 0.3, 0]
    # Write a synthetic stereo npz with FAR points
    pts_far = np.array([
        [100.0, 0.0, 0.0],
        [100.0, 5.0, 0.0],
        [100.0, -5.0, 0.0],
    ], dtype=np.float32)
    npz = tmp_path / "stereo_cam_a__cam_b.npz"
    np.savez_compressed(npz, **{
        STEREO_NPZ_PTS_KEY: pts_far,
        STEREO_NPZ_CAM_A_KEY: np.array("cam_a"),
        STEREO_NPZ_CAM_B_KEY: np.array("cam_b"),
    })
    cam_K = {"cam_a": K, "cam_b": K}
    cam_T = {"cam_a": T_a, "cam_b": T_b}
    disps = build_per_cam_displacements_from_stereo(
        [npz], cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=["cam_a", "cam_b"],
        erp_hw=(1024, 2048),
    )
    # disps["cam_a"] should be a list of (ideal_uv, delta_uv) tuples
    assert "cam_a" in disps and "cam_b" in disps
    assert len(disps["cam_a"]) == 3  # 3 input points
    # All delta magnitudes should be < 5 ERP pixels for 100m points
    for ideal_uv, delta_uv in disps["cam_a"]:
        assert np.linalg.norm(delta_uv) < 5.0, (
            f"far-point delta should be ~0, got {delta_uv}"
        )


def test_dense_field_at_anchors_matches_sparse():
    """The dense field at exactly the sparse anchor positions equals the sparse delta."""
    from waymo2panorama.alignment.sparse_displacement import (
        interpolate_dense_displacement_field,
    )
    erp_hw = (256, 512)
    sparse = [
        (np.array([100.0, 50.0]), np.array([3.0, -2.0])),
        (np.array([300.0, 100.0]), np.array([-1.0, 4.0])),
        (np.array([400.0, 150.0]), np.array([2.0, 1.0])),
    ]
    dense = interpolate_dense_displacement_field(
        sparse, erp_hw=erp_hw, regularization=1e-3,
    )
    assert dense.shape == (erp_hw[0], erp_hw[1], 2)
    # At the anchor pixels, value should be ~ the sparse delta (within reg tolerance)
    for ideal_uv, delta_uv in sparse:
        u, v = int(round(ideal_uv[0])), int(round(ideal_uv[1]))
        d = dense[v, u]
        assert np.linalg.norm(d - delta_uv) < 0.5, (
            f"anchor at {ideal_uv} delta={delta_uv} but dense[v,u]={d}"
        )


def test_dense_field_decays_to_zero_far_from_anchors():
    """Outside the anchor support, displacement should decay to ~0."""
    from waymo2panorama.alignment.sparse_displacement import (
        interpolate_dense_displacement_field,
    )
    erp_hw = (256, 512)
    sparse = [
        (np.array([100.0, 50.0]), np.array([3.0, -2.0])),
    ]
    dense = interpolate_dense_displacement_field(
        sparse, erp_hw=erp_hw, regularization=1.0,
    )
    # 200 px away from the anchor, displacement should be small
    far_d = dense[150, 300]
    assert np.linalg.norm(far_d) < 1.0, f"far field should decay, got {far_d}"


def test_zero_displacement_returns_identical_slab():
    """All-zero displacement field => warped slab == original slab."""
    from waymo2panorama.alignment.sparse_displacement import (
        warp_erp_slab_by_displacement,
    )
    slab = (np.random.RandomState(0).rand(64, 128, 3) * 255).astype(np.float32)
    zero_disp = np.zeros((64, 128, 2), dtype=np.float32)
    warped = warp_erp_slab_by_displacement(slab, zero_disp, wrap_horizontal=True)
    assert warped.shape == slab.shape
    assert np.allclose(warped, slab, atol=0.5)


def test_constant_displacement_shifts_slab():
    """Constant (+10, 0) displacement => slab content shifts by 10 px in -u dir."""
    from waymo2panorama.alignment.sparse_displacement import (
        warp_erp_slab_by_displacement,
    )
    H, W = 32, 64
    slab = np.zeros((H, W, 3), dtype=np.float32)
    slab[:, 20:25] = 255.0  # vertical white stripe at u=20..24
    disp = np.zeros((H, W, 2), dtype=np.float32)
    disp[..., 0] = 10.0  # "ERP pixel at u sources from u + 10" ... convention check
    warped = warp_erp_slab_by_displacement(slab, disp, wrap_horizontal=True)
    # The stripe should now appear at u-10 = 10..14 (or u+10 = 30..34 — depends on convention)
    # Document the convention via the test:
    has_stripe_at_10 = np.any(warped[:, 10:15] > 100)
    has_stripe_at_30 = np.any(warped[:, 30:35] > 100)
    assert has_stripe_at_10 ^ has_stripe_at_30, (
        f"stripe should shift by 10 px in ONE direction. "
        f"left? {has_stripe_at_10}, right? {has_stripe_at_30}"
    )


def test_confidence_map_high_near_anchors_zero_far():
    """Pixels near sparse anchors get high confidence; far pixels get zero."""
    from waymo2panorama.alignment.sparse_displacement import (
        build_anchor_confidence_map,
    )
    erp_hw = (256, 512)
    anchors = [np.array([100.0, 50.0]), np.array([300.0, 100.0])]
    conf = build_anchor_confidence_map(anchors, erp_hw=erp_hw, sigma_px=20.0)
    assert conf.shape == erp_hw
    assert conf.dtype == np.float32
    # At anchor pixel, confidence ~ 1
    assert conf[50, 100] > 0.9
    # Far from any anchor, confidence ~ 0
    assert conf[200, 400] < 0.1
    assert float(conf.max()) <= 1.0 + 1e-6
    assert float(conf.min()) >= 0.0


def test_midpoint_target_mode_symmetric_displacement(tmp_path):
    """Stage 3 Phase C: target_mode='midpoint' produces symmetric deltas.

    For a single 3D point seen by 2 cams (cam_a, cam_b):
      - cam_a's delta = midpoint - L1_uv_a
      - cam_b's delta = midpoint - L1_uv_b
    These should be exactly opposite in the v-axis (linear, no wrap) and
    sum to zero on the u-axis modulo W (handled by shortest-wrap delta).
    Both anchors should land at the SAME u-target (the midpoint).
    """
    from waymo2panorama.alignment.sparse_displacement import (
        build_per_cam_displacements_from_stereo,
    )
    from waymo2panorama.pipeline.option_b_reweight import (
        STEREO_NPZ_PTS_KEY, STEREO_NPZ_CAM_A_KEY, STEREO_NPZ_CAM_B_KEY,
    )
    K = np.array([[500.0, 0, 252.0], [0, 500.0, 252.0], [0, 0, 1.0]])
    R = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]], dtype=np.float64)
    # cam_a 0.5m forward, cam_b 0.5m forward + 0.3m to ego-left
    T_a = np.eye(4); T_a[:3, :3] = R; T_a[:3, 3] = [0.5, 0.0, 0.0]
    T_b = np.eye(4); T_b[:3, :3] = R; T_b[:3, 3] = [0.5, 0.3, 0.0]
    # Near-field point (5m forward) - measurable parallax
    pts_near = np.array([[5.0, 0.0, 0.0]], dtype=np.float32)
    npz = tmp_path / "stereo_cam_a__cam_b.npz"
    np.savez_compressed(npz, **{
        STEREO_NPZ_PTS_KEY: pts_near,
        STEREO_NPZ_CAM_A_KEY: np.array("cam_a"),
        STEREO_NPZ_CAM_B_KEY: np.array("cam_b"),
    })
    cam_K = {"cam_a": K, "cam_b": K}
    cam_T = {"cam_a": T_a, "cam_b": T_b}
    disps = build_per_cam_displacements_from_stereo(
        [npz], cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=["cam_a", "cam_b"],
        erp_hw=(1024, 2048), target_mode="midpoint",
    )
    assert len(disps["cam_a"]) == 1
    assert len(disps["cam_b"]) == 1
    anchor_a, delta_a = disps["cam_a"][0]
    anchor_b, delta_b = disps["cam_b"][0]
    # Both anchors should be at the SAME midpoint location (within float tol)
    assert np.allclose(anchor_a, anchor_b, atol=1e-6), (
        f"midpoint mode: both cams should have anchor at same target. "
        f"a={anchor_a}, b={anchor_b}"
    )
    # v-axis deltas should be opposite-sign and equal magnitude (linear, no wrap)
    assert abs(delta_a[1] + delta_b[1]) < 1e-6, (
        f"v-delta should sum to 0. a={delta_a[1]}, b={delta_b[1]}"
    )
    # u-axis: each delta is target_u - L1_u, summed = target_u - L1_u_a + target_u - L1_u_b
    # but target = midpoint(L1_u_a, L1_u_b), so each delta is HALF the L1 difference.
    # That means delta_a == -delta_b on u-axis (assuming no wrap).
    assert abs(delta_a[0] + delta_b[0]) < 0.5, (  # 0.5 tolerance for wrap interaction
        f"u-delta should sum near 0. a={delta_a[0]}, b={delta_b[0]}"
    )


def test_midpoint_vs_ideal_targets_differ_for_near_field(tmp_path):
    """For near-field point with parallax, ideal target (depth-aware ERP) ≠ midpoint target."""
    from waymo2panorama.alignment.sparse_displacement import (
        build_per_cam_displacements_from_stereo,
    )
    from waymo2panorama.pipeline.option_b_reweight import (
        STEREO_NPZ_PTS_KEY, STEREO_NPZ_CAM_A_KEY, STEREO_NPZ_CAM_B_KEY,
    )
    K = np.array([[500.0, 0, 252.0], [0, 500.0, 252.0], [0, 0, 1.0]])
    R = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]], dtype=np.float64)
    T_a = np.eye(4); T_a[:3, :3] = R; T_a[:3, 3] = [0.5, 0.0, 0.0]
    T_b = np.eye(4); T_b[:3, :3] = R; T_b[:3, 3] = [0.5, 0.3, 0.0]
    pts = np.array([[3.0, 0.0, 0.0]], dtype=np.float32)  # 3m, strong parallax
    npz = tmp_path / "stereo_cam_a__cam_b.npz"
    np.savez_compressed(npz, **{
        STEREO_NPZ_PTS_KEY: pts,
        STEREO_NPZ_CAM_A_KEY: np.array("cam_a"),
        STEREO_NPZ_CAM_B_KEY: np.array("cam_b"),
    })
    cam_K = {"cam_a": K, "cam_b": K}
    cam_T = {"cam_a": T_a, "cam_b": T_b}
    disps_ideal = build_per_cam_displacements_from_stereo(
        [npz], cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=["cam_a", "cam_b"],
        erp_hw=(1024, 2048), target_mode="ideal",
    )
    disps_mid = build_per_cam_displacements_from_stereo(
        [npz], cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=["cam_a", "cam_b"],
        erp_hw=(1024, 2048), target_mode="midpoint",
    )
    anchor_a_ideal, _ = disps_ideal["cam_a"][0]
    anchor_a_mid, _ = disps_mid["cam_a"][0]
    # The two target_modes should produce different anchor locations
    diff = float(np.linalg.norm(anchor_a_ideal - anchor_a_mid))
    assert diff > 0.1, f"ideal vs midpoint targets should differ for near-field, got diff={diff}"


def test_target_mode_invalid_raises():
    """Invalid target_mode should raise ValueError immediately."""
    from waymo2panorama.alignment.sparse_displacement import (
        build_per_cam_displacements_from_stereo,
    )
    with pytest.raises(ValueError, match="target_mode"):
        build_per_cam_displacements_from_stereo(
            [], cam_K={}, cam_T_ego_cam={}, cam_names=[],
            erp_hw=(64, 128), target_mode="bogus",
        )


def test_orchestrator_no_stereo_returns_unchanged_slabs(tmp_path):
    """If no stereo files provided, orchestrator returns slabs unchanged."""
    from waymo2panorama.alignment.sparse_displacement import build_warped_slabs_a2
    slabs = {f"cam_{i}": (np.random.RandomState(i).rand(32, 64, 3) * 255).astype(np.float32)
             for i in range(3)}
    cam_K = {c: np.eye(3) for c in slabs}
    cam_T = {c: np.eye(4) for c in slabs}
    out_slabs, summary = build_warped_slabs_a2(
        l1_slabs=slabs, stereo_npz_paths=[],
        cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=list(slabs),
        erp_hw=(32, 64),
    )
    for c in slabs:
        assert np.allclose(out_slabs[c], slabs[c], atol=0.5)
    assert summary["n_stereo_files_used"] == 0
