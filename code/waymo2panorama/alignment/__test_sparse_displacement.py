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
