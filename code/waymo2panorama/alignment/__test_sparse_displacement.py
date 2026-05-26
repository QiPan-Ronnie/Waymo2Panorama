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
