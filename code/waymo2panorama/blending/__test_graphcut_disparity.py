"""Unit tests for blending/graphcut_disparity.py — B1 module."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.blending.graphcut_disparity import (  # noqa: E402
    build_pair_disparity_magnitude,
)


def test_disparity_zero_when_displacements_equal():
    """If cam_a and cam_b both have the same displacement vector at a point,
    their disparity (relative) is zero — no parallax disagreement."""
    erp_hw = (64, 128)
    cam_a_anchors = [(np.array([60.0, 30.0]), np.array([2.0, -1.0]))]
    cam_b_anchors = [(np.array([60.0, 30.0]), np.array([2.0, -1.0]))]
    disp_mag = build_pair_disparity_magnitude(
        cam_a_anchors, cam_b_anchors, erp_hw=erp_hw, sigma_px=10.0,
    )
    assert disp_mag.shape == erp_hw
    assert disp_mag[30, 60] < 0.5  # ~ 0 at the anchor (equal disp = no disparity)


def test_disparity_nonzero_when_displacements_differ():
    """Different per-cam displacement at the same point → nonzero disparity."""
    erp_hw = (64, 128)
    cam_a_anchors = [(np.array([60.0, 30.0]), np.array([3.0, 0.0]))]
    cam_b_anchors = [(np.array([60.0, 30.0]), np.array([-3.0, 0.0]))]
    disp_mag = build_pair_disparity_magnitude(
        cam_a_anchors, cam_b_anchors, erp_hw=erp_hw, sigma_px=10.0,
    )
    assert disp_mag[30, 60] > 4.0  # |3 - (-3)| = 6 pixels of relative disp
