"""Unit tests for projection/sphere_projection.py — N1 cam-translation-aware mode.

Covers:
  - Backward compatibility: convergence_distance_m=None byte-identical to omitting param.
  - Numerical sanity: convergence_distance_m=1e6 ≈ legacy (translation negligible).
  - Validation: r <= 0 / NaN / inf raises ValueError; wrong-shape array raises.
  - Synthetic 2-cam convergence: a 3D point at distance r appears at the same ERP
    pixel from two cams in N1 mode (r=|P|), but at different ERP pixels in legacy
    mode. This is the geometric correctness check.
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

from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


# -------------------- helpers --------------------


def make_cam(t: tuple[float, float, float], yaw_deg: float = 0.0,
             img_hw: tuple[int, int] = (504, 504),
             fx: float = 252.0, fy: float = 252.0):
    """Build (image, K, T_ego_cam) for a synthetic forward-facing cam at translation t.

    yaw_deg=0 → cam optical axis (+z_cam) aligned with +x_ego (forward).
    OpenCV cam convention: x_cam=right, y_cam=down, z_cam=forward.
    AV2 ego frame: x_forward, y_left, z_up. Right-handed.

    R_ego_cam columns are the cam basis vectors expressed in ego frame:
      x_cam_ego = y_cam × z_cam (right-hand rule)
                = (sin yaw, -cos yaw, 0)        [y_cam_ego = (0, 0, -1) always]
      y_cam_ego = (0, 0, -1)                    [cam +y = down = -z_ego]
      z_cam_ego = (cos yaw, sin yaw, 0)         [cam +z = forward direction]
    """
    yaw = np.deg2rad(yaw_deg)
    R_ego_cam = np.array([
        [ np.sin(yaw),  0.0, np.cos(yaw)],
        [-np.cos(yaw),  0.0, np.sin(yaw)],
        [ 0.0,         -1.0, 0.0       ],
    ], dtype=np.float64)
    T_ego_cam = np.eye(4, dtype=np.float64)
    T_ego_cam[:3, :3] = R_ego_cam
    T_ego_cam[:3, 3] = np.array(t, dtype=np.float64)
    h, w = img_hw
    K = np.array([[fx, 0, w / 2.0],
                  [0, fy, h / 2.0],
                  [0, 0, 1.0]], dtype=np.float64)
    image = np.full((h, w, 3), 128, dtype=np.uint8)  # mid-gray background
    return image, K, T_ego_cam


def fwd_project_ego_pt_to_cam(pt_ego: np.ndarray, K: np.ndarray,
                              T_ego_cam: np.ndarray) -> tuple[float, float]:
    """Standard forward projection of an ego-frame 3D point to cam image pixel."""
    R_ego_cam = T_ego_cam[:3, :3]
    t_ego_cam = T_ego_cam[:3, 3]
    P_cam = R_ego_cam.T @ (pt_ego - t_ego_cam)
    if P_cam[2] <= 1e-6:
        raise ValueError("point behind camera")
    u = K[0, 0] * P_cam[0] / P_cam[2] + K[0, 2]
    v = K[1, 1] * P_cam[1] / P_cam[2] + K[1, 2]
    return float(u), float(v)


# -------------------- tests --------------------


def test_legacy_omit_equals_explicit_none():
    """Omitting convergence_distance_m == passing None == byte-identical output."""
    image, K, T_ego_cam = make_cam(t=(1.5, 0.0, 1.4))
    out_omit = render_camera_to_erp(image, K, T_ego_cam, erp_hw=(128, 256))
    out_none = render_camera_to_erp(image, K, T_ego_cam, erp_hw=(128, 256),
                                    convergence_distance_m=None)
    for a, b in zip(out_omit, out_none):
        assert a.dtype == b.dtype
        assert a.shape == b.shape
        assert np.array_equal(a, b), "legacy and explicit-None must be byte-identical"


def test_large_r_approximates_legacy():
    """convergence_distance_m=1e6 ≈ legacy mode (translation negligible vs r)."""
    image, K, T_ego_cam = make_cam(t=(1.5, 0.0, 1.4))
    rgb_leg, alpha_leg, w_leg = render_camera_to_erp(image, K, T_ego_cam, erp_hw=(128, 256))
    rgb_n1, alpha_n1, w_n1 = render_camera_to_erp(image, K, T_ego_cam, erp_hw=(128, 256),
                                                  convergence_distance_m=1e6)
    # Alpha masks should be nearly identical (>99% match)
    assert (alpha_leg == alpha_n1).mean() > 0.99
    # Weights close to identical where both valid
    both = alpha_leg & alpha_n1
    if both.sum() > 0:
        assert np.abs(w_leg[both] - w_n1[both]).max() < 1e-3


def test_invalid_r_raises():
    """r <= 0 or non-finite raises ValueError."""
    image, K, T_ego_cam = make_cam(t=(1.5, 0.0, 1.4))
    for bad in [-1.0, 0.0, np.nan, np.inf, -np.inf]:
        with pytest.raises(ValueError):
            render_camera_to_erp(image, K, T_ego_cam, erp_hw=(64, 128),
                                 convergence_distance_m=bad)


def test_array_r_wrong_shape_raises():
    """Per-pixel r map with shape != erp_hw raises ValueError."""
    image, K, T_ego_cam = make_cam(t=(1.5, 0.0, 1.4))
    bad_r = np.full((100, 100), 10.0, dtype=np.float64)
    with pytest.raises(ValueError):
        render_camera_to_erp(image, K, T_ego_cam, erp_hw=(64, 128),
                             convergence_distance_m=bad_r)


def test_array_r_correct_shape_runs():
    """Per-pixel r map with shape == erp_hw runs without error."""
    image, K, T_ego_cam = make_cam(t=(1.5, 0.0, 1.4))
    erp_hw = (64, 128)
    r_map = np.full(erp_hw, 10.0, dtype=np.float64)
    rgb, alpha, weight = render_camera_to_erp(image, K, T_ego_cam, erp_hw=erp_hw,
                                              convergence_distance_m=r_map)
    assert rgb.shape == (*erp_hw, 3)
    assert alpha.shape == erp_hw
    assert weight.shape == erp_hw


def test_n1_two_cams_converge_at_r():
    """Geometric correctness check (the key test for N1).

    Setup: two cams at y=±0.3 facing forward (yaw=0). 3D point at (5, 0, 0)
    (5m forward of ego, exactly on the +x axis).

    Forward-project the 3D point into each cam's image, paint a red marker.
    Then render to ERP in N1 mode (r=5).

    Claim: in N1 mode with r matching the point's distance, the marker
    appears at theta=0 (ERP horizontal center) for BOTH cams. This is
    geometric correctness — both cams "agree" on where the point is on ERP.

    In legacy mode (None), the cams' marker positions diverge by the parallax
    angle (here ~6.9° for baseline 0.6m at 5m distance).
    """
    erp_h, erp_w = 256, 512  # higher res for accurate measurement

    img_a, K_a, T_a = make_cam(t=(0.0, 0.3, 0.0))
    img_b, K_b, T_b = make_cam(t=(0.0, -0.3, 0.0))

    pt_ego = np.array([5.0, 0.0, 0.0])  # 5m forward, on +x axis

    # Paint a 5x5 red marker at the forward projection of pt_ego in each cam.
    for img, K, T in [(img_a, K_a, T_a), (img_b, K_b, T_b)]:
        u, v = fwd_project_ego_pt_to_cam(pt_ego, K, T)
        iu, iv = int(round(u)), int(round(v))
        h_img, w_img = img.shape[:2]
        assert 0 <= iu < w_img and 0 <= iv < h_img, "marker outside image"
        img[max(0, iv - 2):iv + 3, max(0, iu - 2):iu + 3] = [255, 0, 0]

    def red_score(rgb: np.ndarray, u_erp: int, v_erp: int, win: int = 2) -> float:
        """Average (R - max(G, B)) in a window around (u_erp, v_erp). >0 means red-ish."""
        u0, u1 = max(0, u_erp - win), min(rgb.shape[1], u_erp + win + 1)
        v0, v1 = max(0, v_erp - win), min(rgb.shape[0], v_erp + win + 1)
        win_px = rgb[v0:v1, u0:u1].astype(np.float32)
        r = win_px[..., 0]
        gb_max = np.maximum(win_px[..., 1], win_px[..., 2])
        return float((r - gb_max).mean())

    # N1 mode with r = |pt_ego| = 5
    r = float(np.linalg.norm(pt_ego))
    rgb_a_n1, _, _ = render_camera_to_erp(img_a, K_a, T_a, erp_hw=(erp_h, erp_w),
                                          convergence_distance_m=r)
    rgb_b_n1, _, _ = render_camera_to_erp(img_b, K_b, T_b, erp_hw=(erp_h, erp_w),
                                          convergence_distance_m=r)
    # Legacy mode (None)
    rgb_a_leg, _, _ = render_camera_to_erp(img_a, K_a, T_a, erp_hw=(erp_h, erp_w))
    rgb_b_leg, _, _ = render_camera_to_erp(img_b, K_b, T_b, erp_hw=(erp_h, erp_w))

    # ERP coordinates for theta=0, phi=0
    u_center = erp_w // 2
    v_center = erp_h // 2

    # In N1 mode, both cams should show RED at the ERP center (where theta=0 lands).
    r_a_n1 = red_score(rgb_a_n1, u_center, v_center)
    r_b_n1 = red_score(rgb_b_n1, u_center, v_center)
    assert r_a_n1 > 50, f"N1 mode: cam_a should be red at center, got red_score={r_a_n1:.1f}"
    assert r_b_n1 > 50, f"N1 mode: cam_b should be red at center, got red_score={r_b_n1:.1f}"

    # In legacy mode, the ERP center should NOT be red (cams sample their own
    # image center for theta=0, but the markers are offset by ±15 px from center).
    r_a_leg = red_score(rgb_a_leg, u_center, v_center)
    r_b_leg = red_score(rgb_b_leg, u_center, v_center)
    assert r_a_leg < 30, f"legacy mode: cam_a unexpectedly red at center, got {r_a_leg:.1f}"
    assert r_b_leg < 30, f"legacy mode: cam_b unexpectedly red at center, got {r_b_leg:.1f}"


def test_n1_far_point_close_to_legacy():
    """For a far point (50m), N1 and legacy should agree within ~1 px."""
    erp_h, erp_w = 128, 256

    image, K, T = make_cam(t=(0.0, 0.3, 0.0))
    pt_far = np.array([50.0, 0.0, 0.0])  # 50m forward
    u, v = fwd_project_ego_pt_to_cam(pt_far, K, T)
    iu, iv = int(round(u)), int(round(v))
    image[max(0, iv - 2):iv + 3, max(0, iu - 2):iu + 3] = [255, 0, 0]

    r_far = float(np.linalg.norm(pt_far))
    rgb_n1, _, _ = render_camera_to_erp(image, K, T, erp_hw=(erp_h, erp_w),
                                        convergence_distance_m=r_far)
    rgb_leg, _, _ = render_camera_to_erp(image, K, T, erp_hw=(erp_h, erp_w))

    # Both should be very similar (red marker close to same ERP location)
    diff = np.abs(rgb_n1.astype(np.int32) - rgb_leg.astype(np.int32)).sum(axis=-1)
    # Allow at most a few percent of pixels to differ significantly (bilinear edges)
    frac_diff = (diff > 30).mean()
    assert frac_diff < 0.02, f"far-point N1 vs legacy disagree on {frac_diff*100:.1f}% pixels"
