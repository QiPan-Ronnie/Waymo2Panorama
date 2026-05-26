"""
Unit tests for alignment/rotation_refinement.py — SO(3) helpers + bundle adjust.

Verifies:
  - axis_angle <-> R round-trip (small and large angles).
  - bundle_adjust_rotations recovers known per-cam refinements from synthetic
    observed pair rotations.
  - Anchor cam stays at identity after BA.
  - apply_rotation_refinements is right-multiplication semantics
    (R_ego_cam_new = R_ego_cam @ dR), translation unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.alignment.rotation_refinement import (  # noqa: E402
    R_to_axis_angle,
    axis_angle_to_R,
    apply_rotation_refinements,
    bundle_adjust_rotations,
)


# ---------------------------------------------------------------------------
# Axis-angle round-trips
# ---------------------------------------------------------------------------


def test_axis_angle_zero_is_identity():
    R = axis_angle_to_R(np.zeros(3))
    assert np.allclose(R, np.eye(3), atol=1e-12)
    ax = R_to_axis_angle(np.eye(3))
    assert np.allclose(ax, np.zeros(3), atol=1e-12)


@pytest.mark.parametrize("angle_deg", [0.1, 1.0, 5.0, 30.0, 89.0, 178.0])
@pytest.mark.parametrize("axis", [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1)])
def test_axis_angle_roundtrip(angle_deg, axis):
    """omega -> R -> omega' should recover the original omega within FP precision."""
    ax = np.array(axis, dtype=np.float64)
    ax = ax / np.linalg.norm(ax)
    omega_true = ax * np.deg2rad(angle_deg)
    R = axis_angle_to_R(omega_true)
    omega_back = R_to_axis_angle(R)
    # Compare via R (axis flips ambiguous near theta=pi)
    R_back = axis_angle_to_R(omega_back)
    assert np.allclose(R, R_back, atol=1e-9), (
        f"axis={axis}, angle={angle_deg}: omega_true={omega_true} -> "
        f"omega_back={omega_back} -> R diff = {np.linalg.norm(R - R_back)}"
    )


def test_R_to_axis_angle_known_matrix():
    """Hand-coded 90 deg around Y -> [0, pi/2, 0]."""
    R = np.array([
        [0, 0, 1],
        [0, 1, 0],
        [-1, 0, 0],
    ], dtype=np.float64)
    omega = R_to_axis_angle(R)
    # Either [0, +pi/2, 0] OR [0, -pi/2, 0] are valid for sign-flipped axis
    assert abs(abs(omega[1]) - np.pi / 2) < 1e-9
    assert abs(omega[0]) < 1e-9
    assert abs(omega[2]) < 1e-9


# ---------------------------------------------------------------------------
# Bundle adjustment — synthetic
# ---------------------------------------------------------------------------


def _make_ring_cam_ego_to_cam(n_cams: int = 7) -> dict[str, np.ndarray]:
    """Synthesize 7 ring cams arranged in a circle, each looking outward.

    Each cam's optical axis (cam +z) points radially outward from ego origin.
    Returns {cam_name: (4, 4) T_ego_cam}.
    """
    cams = {}
    for i in range(n_cams):
        theta = 2 * np.pi * i / n_cams
        # cam +z in ego is (cos(theta), sin(theta), 0)
        z_ego = np.array([np.cos(theta), np.sin(theta), 0.0])
        # cam +x in ego is (-sin(theta), cos(theta), 0)
        x_ego = np.array([-np.sin(theta), np.cos(theta), 0.0])
        y_ego = np.cross(z_ego, x_ego)
        R = np.column_stack([x_ego, y_ego, z_ego])
        T = np.eye(4, dtype=np.float64)
        T[:3, :3] = R
        T[:3, 3] = np.array([np.cos(theta), np.sin(theta), 0]) * 0.3  # cam offset
        cams[f"cam_{i}"] = T
    return cams


def test_bundle_adjust_recovers_zero_refinement_when_observed_equals_calibrated():
    """If observed pair Rs equal calibrated pair Rs, BA should return ~identity refinements."""
    cams = _make_ring_cam_ego_to_cam(5)
    cam_names = list(cams.keys())
    pair_R_observed = {}
    for i in range(len(cam_names) - 1):
        a, b = cam_names[i], cam_names[i + 1]
        R_a = cams[a][:3, :3]
        R_b = cams[b][:3, :3]
        pair_R_observed[(a, b)] = R_b.T @ R_a  # exactly calibrated

    dR = bundle_adjust_rotations(
        pair_R_observed, cams, cam_names=cam_names,
        anchor_cam=cam_names[0], verbose=False,
    )
    # All refinements should be ~ identity
    for c, R in dR.items():
        ang = float(np.linalg.norm(R_to_axis_angle(R)))
        assert ang < 1e-3, f"{c}: |dR| = {np.rad2deg(ang):.4f} deg, expected ~0"


def test_bundle_adjust_recovers_known_per_cam_delta():
    """Inject a known per-cam delta_R, derive consistent pair observations, BA recovers."""
    cams = _make_ring_cam_ego_to_cam(5)
    cam_names = list(cams.keys())

    # Inject SMALL known per-cam refinements (~1 deg around varied axes).
    np.random.seed(0)
    true_dR = {cam_names[0]: np.eye(3, dtype=np.float64)}  # anchor
    for c in cam_names[1:]:
        axis = np.random.randn(3)
        axis = axis / np.linalg.norm(axis)
        ang = np.deg2rad(0.8)
        true_dR[c] = axis_angle_to_R(axis * ang)

    # Derive consistent pair Rs from cal + true refinements.
    R_ego_cam_refined = {
        c: cams[c][:3, :3] @ true_dR[c] for c in cam_names
    }
    pair_R_observed = {}
    for i in range(len(cam_names) - 1):
        a, b = cam_names[i], cam_names[i + 1]
        pair_R_observed[(a, b)] = R_ego_cam_refined[b].T @ R_ego_cam_refined[a]

    dR_recovered = bundle_adjust_rotations(
        pair_R_observed, cams, cam_names=cam_names,
        anchor_cam=cam_names[0], verbose=False,
    )

    # Recovered dR should match true_dR within numerical precision.
    for c in cam_names:
        R_diff = dR_recovered[c] @ true_dR[c].T
        ang = float(np.linalg.norm(R_to_axis_angle(R_diff)))
        assert ang < np.deg2rad(0.01), (
            f"{c}: recovery off by {np.rad2deg(ang):.4f} deg, want < 0.01 deg"
        )


def test_bundle_adjust_anchor_stays_identity():
    """The anchor cam's dR must be identity exactly (gauge fix)."""
    cams = _make_ring_cam_ego_to_cam(5)
    cam_names = list(cams.keys())
    pair_R_observed = {}
    np.random.seed(1)
    for i in range(len(cam_names) - 1):
        a, b = cam_names[i], cam_names[i + 1]
        # Add random noise (small)
        noise = axis_angle_to_R(np.random.randn(3) * 0.005)
        R_cal = cams[b][:3, :3].T @ cams[a][:3, :3]
        pair_R_observed[(a, b)] = noise @ R_cal

    dR = bundle_adjust_rotations(
        pair_R_observed, cams, cam_names=cam_names,
        anchor_cam=cam_names[2], verbose=False,
    )
    assert np.allclose(dR[cam_names[2]], np.eye(3), atol=1e-12)


def test_apply_rotation_refinements_right_multiplies():
    """R_ego_cam_new = R_ego_cam @ dR, translation unchanged."""
    cams = _make_ring_cam_ego_to_cam(3)
    dR = {
        "cam_0": np.eye(3),
        "cam_1": axis_angle_to_R(np.array([0.0, 0.0, np.deg2rad(2.0)])),
        "cam_2": axis_angle_to_R(np.array([np.deg2rad(1.0), 0.0, 0.0])),
    }
    refined = apply_rotation_refinements(cams, dR)
    for c in cams:
        # Translation unchanged
        assert np.allclose(refined[c][:3, 3], cams[c][:3, 3])
        # Rotation = original @ dR
        expected_R = cams[c][:3, :3] @ dR[c]
        assert np.allclose(refined[c][:3, :3], expected_R)


def test_bundle_adjust_no_pairs_returns_all_identity():
    """Edge case: empty pair dict -> all refinements are identity."""
    cams = _make_ring_cam_ego_to_cam(3)
    cam_names = list(cams.keys())
    dR = bundle_adjust_rotations(
        {}, cams, cam_names=cam_names, anchor_cam=cam_names[0], verbose=False,
    )
    assert set(dR.keys()) == set(cam_names)
    for c, R in dR.items():
        assert np.allclose(R, np.eye(3), atol=1e-12)


def test_bundle_adjust_invalid_anchor_raises():
    cams = _make_ring_cam_ego_to_cam(3)
    with pytest.raises(ValueError, match="anchor_cam"):
        bundle_adjust_rotations(
            {}, cams, cam_names=list(cams.keys()), anchor_cam="not_a_cam",
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
