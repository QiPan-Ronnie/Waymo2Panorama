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


def test_find_seam_picks_min_disparity_column():
    """Seam should pass through low-disparity column, avoid high-disparity column."""
    from waymo2panorama.blending.graphcut_disparity import find_min_disparity_seam
    H, W = 32, 64
    # Setup: uniform high disp inside overlap EXCEPT a low-disp valley at col 34-36.
    # This forces the DP to route through the valley (since uniform background is
    # higher cost everywhere else inside the overlap mask).
    disp = np.full((H, W), 10.0, dtype=np.float32)
    disp[:, 34:36] = 0.1  # low-disp valley
    overlap_mask = np.zeros((H, W), dtype=bool)
    overlap_mask[:, 15:45] = True
    seam_u = find_min_disparity_seam(disp, overlap_mask, u_smoothness=0.5)
    assert seam_u.shape == (H,)
    # Seam should be near columns 34-35 (the only low-cost region inside overlap).
    assert np.all((seam_u >= 32) & (seam_u <= 40)), f"seam {seam_u} should be near 35"


def test_apply_seam_zero_one_mask():
    """Apply seam: left of seam = cam_a only, right of seam = cam_b only."""
    from waymo2panorama.blending.graphcut_disparity import apply_seam_to_pair_weights
    H, W = 16, 32
    w_a = np.ones((H, W), dtype=np.float32) * 0.5
    w_b = np.ones((H, W), dtype=np.float32) * 0.5
    seam_u = np.full(H, 16, dtype=np.int64)
    overlap_mask = np.zeros((H, W), dtype=bool)
    overlap_mask[:, 10:22] = True
    w_a_new, w_b_new = apply_seam_to_pair_weights(
        w_a, w_b, seam_u, overlap_mask, soft_px=0,
    )
    # In overlap, left of seam (10-15) → cam_a=1.0, cam_b=0
    # Right of seam (17-21) → cam_a=0, cam_b=1.0
    # Outside overlap: weights unchanged (0.5 each)
    assert w_a_new[5, 12] > 0.9 and w_b_new[5, 12] < 0.1
    assert w_a_new[5, 19] < 0.1 and w_b_new[5, 19] > 0.9
    assert abs(w_a_new[5, 5] - 0.5) < 0.01  # outside overlap, unchanged
