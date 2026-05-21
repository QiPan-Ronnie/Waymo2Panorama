"""
Inverse Perspective Mapping (IPM) ground prior + sphere projection hybrid.

Motivation
----------
L1 sphere projection (sphere_projection.py) assumes infinite depth: every camera
ray is treated as a unit ray from the ego origin. This is correct for far-field
content, but causes parallax ghosts on near-field structure — particularly on
the GROUND, where lane markings, crosswalks, and shadows live close to several
cameras simultaneously.

For AV street scenes the ground is, to excellent approximation, the plane z = 0
in the ego frame. That gives us a closed-form, parallax-free projection for any
ground pixel: knowing the cam's K + T_ego_cam, every ground pixel has an exact
(x, y, 0) ego-frame coordinate, hence an exact (theta, phi) on the ERP sphere
(treating the ego origin as the panorama center).

This module provides:
  * `detect_ground_from_pi3`   — ground mask from a per-cam local-points (H, W, 3)
                                  Pi3 output, by thresholding ego-z near 0.
  * `detect_ground_lower_n`    — fallback heuristic: bottom-N fraction of each cam.
  * `ipm_project_ground`       — forward-warp ground pixels via plane homography
                                  to the ERP canvas (analytical, no depth needed).

Conventions match sphere_projection.py:
  * ERP: 2:1 aspect; u increases CCW->CW (theta decreases as u grows);
    v increases top->bottom (phi from +pi/2 at top to -pi/2 at bottom).
  * Ego (AV2): x forward, y left, z up.  Ground plane = {z = 0}.
  * Camera (OpenCV): x right, y down, z forward. K maps (X/Z, Y/Z, 1) to image.
  * T_ego_cam (4x4) maps a point from cam frame to ego frame.

Both functions are CPU-only and depend on numpy + cv2.
"""
from __future__ import annotations

import cv2
import numpy as np


# --------------------------------------------------------------------------- #
#  Ground detection
# --------------------------------------------------------------------------- #


def detect_ground_from_pi3(
    local_points_cam: np.ndarray,
    T_ego_cam: np.ndarray,
    ego_z_thresh_m: float = 0.3,
    min_forward_m: float = 1.0,
    max_radius_m: float = 60.0,
) -> np.ndarray:
    """Detect ground pixels by transforming Pi3 cam-frame points to ego frame.

    A pixel is "ground" iff its predicted ego_z lies within ±ego_z_thresh_m of
    the road plane (z = 0), and the cam-frame point is in front of the cam
    (local_z > min_forward_m), and lies within a sane radius.

    Args:
        local_points_cam: (H, W, 3) float, Pi3 per-cam-frame 3D points.
                          The third channel is depth-along-optical-axis (z>0 forward).
        T_ego_cam:        (4, 4) SE(3) mapping cam-frame to ego-frame.
        ego_z_thresh_m:   |ego_z| <= this counts as ground.  Default 0.3 m allows
                          for road crown + Pi3 noise without grabbing sidewalks.
        min_forward_m:    cam-frame z must exceed this (rejects the sliver of
                          back-projected sky / NaN points).
        max_radius_m:     ego horizontal radius (sqrt(x^2+y^2)) must be < this.
                          Default 60 m matches our cycle-eval depth window.

    Returns:
        (H, W) bool mask, True where the pixel is judged to be ground.
    """
    if local_points_cam.shape[-1] != 3:
        raise ValueError(f"expected (..., 3) points, got shape {local_points_cam.shape}")
    H, W, _ = local_points_cam.shape

    pts_flat = local_points_cam.reshape(-1, 3).astype(np.float64)
    # Pad to homogeneous, transform to ego, drop the 1.
    pts_h = np.concatenate([pts_flat, np.ones((pts_flat.shape[0], 1))], axis=1)  # (N, 4)
    pts_ego = (T_ego_cam @ pts_h.T).T[:, :3]  # (N, 3)
    pts_ego = pts_ego.reshape(H, W, 3)

    z_cam = local_points_cam[..., 2]
    forward_ok = np.isfinite(z_cam) & (z_cam > min_forward_m)
    z_ego = pts_ego[..., 2]
    on_plane = np.isfinite(z_ego) & (np.abs(z_ego) <= ego_z_thresh_m)
    radius = np.sqrt(pts_ego[..., 0] ** 2 + pts_ego[..., 1] ** 2)
    radius_ok = np.isfinite(radius) & (radius < max_radius_m)

    return forward_ok & on_plane & radius_ok


def detect_ground_lower_n(image_hw: tuple[int, int], lower_fraction: float = 0.3) -> np.ndarray:
    """Fallback heuristic ground mask: bottom `lower_fraction` rows = ground.

    Args:
        image_hw:        (H, W) of the camera image.
        lower_fraction:  fraction of rows (counting from the bottom) treated as
                          ground; e.g. 0.3 means rows v >= 0.7 * H.

    Returns:
        (H, W) bool mask.
    """
    H, W = image_hw
    if not (0.0 < lower_fraction <= 1.0):
        raise ValueError(f"lower_fraction must be in (0, 1]; got {lower_fraction}")
    mask = np.zeros((H, W), dtype=bool)
    cut = int(round(H * (1.0 - lower_fraction)))
    mask[cut:, :] = True
    return mask


# --------------------------------------------------------------------------- #
#  IPM ground projection: cam image -> ERP slab via z=0 plane
# --------------------------------------------------------------------------- #


def _erp_uv_from_dir_ego(
    d_ego: np.ndarray,
    erp_hw: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Convert ego-frame unit direction(s) (..., 3) to ERP (u, v) pixel coords.

    Uses the same convention as sphere_projection.render_camera_to_erp:
        theta = pi - (u + 0.5)/W * 2pi   -> u = (pi - theta) / (2 pi) * W - 0.5
        phi   = pi/2 - (v + 0.5)/H * pi  -> v = (pi/2 - phi) / pi * H - 0.5
    """
    h_erp, w_erp = erp_hw
    x, y, z = d_ego[..., 0], d_ego[..., 1], d_ego[..., 2]
    norm = np.sqrt(x * x + y * y + z * z)
    norm = np.where(norm > 1e-12, norm, 1.0)
    xu, yu, zu = x / norm, y / norm, z / norm
    theta = np.arctan2(yu, xu)            # ego y = left -> theta CCW in ground plane
    phi = np.arcsin(np.clip(zu, -1.0, 1.0))
    u = (np.pi - theta) / (2.0 * np.pi) * w_erp - 0.5
    v = (np.pi / 2.0 - phi) / np.pi * h_erp - 0.5
    return u, v


def ipm_project_ground(
    image: np.ndarray,
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    ground_mask: np.ndarray,
    erp_hw: tuple[int, int] = (1024, 2048),
    max_distance_m: float = 50.0,
    min_distance_m: float = 0.5,
    panorama_center_z_m: float = 1.5,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Forward-warp ground pixels from one camera onto the ERP canvas.

    For each ground pixel (u_img, v_img) in the source image:
      1) Build the cam-frame ray d_cam = K^-1 @ (u, v, 1).
      2) Rotate + translate to ego: ray origin = T_ego_cam[:3,3], direction = R @ d_cam.
      3) Solve t such that origin_z + t * dir_z = 0  ->  the point hits the road.
      4) Intersection ego point = (x, y, 0).
      5) Compute outgoing direction from the *panorama center* (0, 0, h_pano)
         to that ground point: dir = (x, y, -h_pano), and convert to ERP (u, v).
      6) Splat the source RGB into the ERP canvas at (u_erp, v_erp).

    Because the plane has zero parallax (every camera observing the same 3D
    point hits the same ERP pixel modulo discretization), the resulting ground
    region is geometrically consistent across cameras — no ghosts.

    Note on `panorama_center_z_m`: the AV2 ego frame puts z=0 at the ground.
    If the ERP viewer is also at z=0, then EVERY ground point projects to the
    equator (phi=0) — degenerate. We instead place the panorama viewer at the
    typical ring-camera height (≈1.5 m above the road), which is also a sensible
    choice for the sphere-projection L1 (which is angle-only and unchanged by
    translation, so the choice is consistent for far-field content).

    Args:
        image:        (H_src, W_src, 3) uint8.
        K:            (3, 3) intrinsics for `image`.
        T_ego_cam:    (4, 4) SE(3) cam->ego.
        ground_mask:  (H_src, W_src) bool. Pixels treated as ground (forward-warped).
                      Non-ground pixels are ignored.
        erp_hw:       (H_erp, W_erp) target canvas size.
        max_distance_m: pixels whose ground intersection lies farther than this
                       (ego horizontal radius) are dropped — IPM error grows ~r^2.
        min_distance_m: pixels closer than this (effectively under the car) are
                        dropped to avoid the singularity at the wheel.
        panorama_center_z_m: ego-z of the virtual panorama viewer (default 1.5).

    Returns:
        erp_rgb     (H_erp, W_erp, 3) float32, RGB in [0, 255] where this cam
                     contributed (zeros elsewhere).
        erp_alpha   (H_erp, W_erp) bool, True where this cam splatted at least
                     one ground pixel.
        erp_weight  (H_erp, W_erp) float32 in [0, 1], analogous to the sphere-
                     projection feather, used by downstream multi-band blending.
    """
    if image.dtype != np.uint8:
        raise ValueError(f"expected uint8 image, got {image.dtype}")
    if image.shape[:2] != ground_mask.shape:
        raise ValueError(
            f"image shape {image.shape[:2]} != ground mask shape {ground_mask.shape}"
        )

    H_src, W_src = image.shape[:2]
    H_erp, W_erp = erp_hw

    # ---- 1. Source pixel grid -> cam-frame rays ----
    ys, xs = np.where(ground_mask)
    if ys.size == 0:
        return (
            np.zeros((H_erp, W_erp, 3), dtype=np.float32),
            np.zeros((H_erp, W_erp), dtype=bool),
            np.zeros((H_erp, W_erp), dtype=np.float32),
        )

    u_img = xs.astype(np.float64) + 0.5
    v_img = ys.astype(np.float64) + 0.5
    pix = np.stack([u_img, v_img, np.ones_like(u_img)], axis=1)  # (N, 3)
    K_inv = np.linalg.inv(K)
    d_cam = pix @ K_inv.T                                         # (N, 3) cam-frame
    # Reject pixels with non-positive cam-frame z (above the optical-axis horizon).
    # In cam frame y points DOWN, so ground rays have positive y component, but the
    # validity criterion is in_front (z_cam > 0).
    in_front = d_cam[:, 2] > 1e-6
    if not in_front.any():
        return (
            np.zeros((H_erp, W_erp, 3), dtype=np.float32),
            np.zeros((H_erp, W_erp), dtype=bool),
            np.zeros((H_erp, W_erp), dtype=np.float32),
        )

    d_cam = d_cam[in_front]
    xs_keep = xs[in_front]
    ys_keep = ys[in_front]

    # ---- 2. Rotate to ego frame ----
    R = T_ego_cam[:3, :3]
    t = T_ego_cam[:3, 3]                                         # cam origin in ego
    d_ego = d_cam @ R.T                                          # (N, 3)

    # ---- 3. Plane intersection: t * dz + cam_origin_z = 0 -> t = -cam_origin_z / dz
    # We need dir_z < 0 if cam is above the plane (typical), so t > 0.
    dz = d_ego[:, 2]
    # Avoid divide-by-zero (rays parallel to ground).
    plane_ok = np.abs(dz) > 1e-6
    if not plane_ok.any():
        return (
            np.zeros((H_erp, W_erp, 3), dtype=np.float32),
            np.zeros((H_erp, W_erp), dtype=bool),
            np.zeros((H_erp, W_erp), dtype=np.float32),
        )

    d_ego = d_ego[plane_ok]
    xs_keep = xs_keep[plane_ok]
    ys_keep = ys_keep[plane_ok]
    dz = dz[plane_ok]
    t_hit = -t[2] / dz                                           # (N,)

    # Valid: t > 0 (in front of cam along ego), and intersection within radius window.
    in_front_world = t_hit > 1e-6
    p_x = t[0] + t_hit * d_ego[:, 0]
    p_y = t[1] + t_hit * d_ego[:, 1]
    r = np.sqrt(p_x ** 2 + p_y ** 2)
    radius_ok = (r >= min_distance_m) & (r <= max_distance_m)
    keep = in_front_world & radius_ok & np.isfinite(p_x) & np.isfinite(p_y)
    if not keep.any():
        return (
            np.zeros((H_erp, W_erp, 3), dtype=np.float32),
            np.zeros((H_erp, W_erp), dtype=bool),
            np.zeros((H_erp, W_erp), dtype=np.float32),
        )

    p_x = p_x[keep]
    p_y = p_y[keep]
    xs_keep = xs_keep[keep]
    ys_keep = ys_keep[keep]
    r = r[keep]

    # ---- 4. Ground point -> ERP (u, v) ----
    # Direction from panorama center (0, 0, h_pano) to ground (p_x, p_y, 0).
    p_dir = np.stack([p_x, p_y, -np.full_like(p_x, panorama_center_z_m)], axis=1)
    u_erp, v_erp = _erp_uv_from_dir_ego(p_dir, erp_hw)

    # Clip and round to valid ERP coords (wrap u horizontally for ERP topology).
    u_idx = np.mod(np.round(u_erp).astype(np.int64), W_erp)
    v_idx = np.clip(np.round(v_erp).astype(np.int64), 0, H_erp - 1)

    # ---- 5. Splat: nearest cam wins (smaller radius -> closer to ego, higher weight)
    # We use radius as the "depth" key and pick min-radius per ERP pixel.
    weight = np.exp(-r / max(max_distance_m, 1.0)).astype(np.float32)  # 1 close, ~0 far

    # Sort by radius ascending so the first hit per (v, u) wins.
    order = np.argsort(r, kind="stable")
    v_s = v_idx[order]
    u_s = u_idx[order]
    rgb_s = image[ys_keep[order], xs_keep[order]].astype(np.float32)
    w_s = weight[order]

    flat = v_s.astype(np.int64) * W_erp + u_s
    _, first = np.unique(flat, return_index=True)

    erp_rgb = np.zeros((H_erp, W_erp, 3), dtype=np.float32)
    erp_weight = np.zeros((H_erp, W_erp), dtype=np.float32)
    erp_rgb[v_s[first], u_s[first]] = rgb_s[first]
    erp_weight[v_s[first], u_s[first]] = w_s[first]
    erp_alpha = np.zeros((H_erp, W_erp), dtype=bool)
    erp_alpha[v_s[first], u_s[first]] = True

    # ---- 6. Densify: forward-splat leaves stippled gaps because cam pixels are
    # much sparser than ERP pixels in the near field. Close those gaps with a
    # small morphological dilation + Gaussian smoothing on RGB and weight, then
    # restrict to the convex hull of contributing pixels (approximated as the
    # dilated alpha) to avoid bleeding past the camera's actual ground footprint.
    if erp_alpha.any():
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        alpha_u8 = erp_alpha.astype(np.uint8) * 255
        alpha_dil = cv2.dilate(alpha_u8, kernel, iterations=1)
        # For each splatted channel, fill nearby gaps by per-channel dilation
        # then mask back to alpha_dil.
        rgb_dil = np.zeros_like(erp_rgb)
        for c in range(3):
            rgb_dil[..., c] = cv2.dilate(erp_rgb[..., c], kernel, iterations=1)
        w_dil = cv2.dilate(erp_weight, kernel, iterations=1)
        fill = (alpha_dil > 0) & (~erp_alpha)
        erp_rgb[fill] = rgb_dil[fill]
        erp_weight[fill] = w_dil[fill]
        erp_alpha = alpha_dil > 0

    return erp_rgb, erp_alpha, erp_weight


# --------------------------------------------------------------------------- #
#  Self-test
# --------------------------------------------------------------------------- #


def _self_test() -> None:
    """A trivial round-trip sanity check.

    Build a synthetic downward-pointing camera 1.5 m above the ground, project
    a known image pattern, and confirm that:
      (a) detect_ground_lower_n returns a sane mask shape,
      (b) ipm_project_ground hits the expected ERP latitude band
          (below the equator, i.e. v > H_erp/2 since the ground is below ego).
    """
    H_src, W_src = 64, 64
    image = np.zeros((H_src, W_src, 3), dtype=np.uint8)
    image[:] = (200, 100, 50)

    # 90-deg HFOV at 1.0x focal length scaling
    fx = fy = W_src / 2.0
    cx = W_src / 2.0
    cy = H_src / 2.0
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])

    # T_ego_cam: camera at ego origin, pitched down 90 deg so it looks straight at ground.
    # In ego frame the cam optical axis (cam +z) should be ego -z (pointing down).
    # Cam frame: x right, y down, z forward.  We want cam-frame +z -> ego -z;
    # cam-frame +x -> ego +y (left); cam-frame +y -> ego +x (forward, since "y down" in cam = "forward then down" if pitched down).
    # Simpler: build by Euler. R aligns cam->ego given a 90deg pitch-down.
    # R such that R @ [0,0,1]_cam = [0,0,-1]_ego, R @ [1,0,0]_cam = [0,1,0]_ego, R @ [0,1,0]_cam = [1,0,0]_ego
    R = np.array([
        [0.0, 1.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, -1.0],
    ])
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = [0.0, 0.0, 1.5]  # cam 1.5 m above ground

    # Test ground detection (heuristic):
    mask = detect_ground_lower_n((H_src, W_src), lower_fraction=0.5)
    assert mask.shape == (H_src, W_src)
    assert mask[H_src - 1, 0] and not mask[0, 0]

    # Test IPM projection
    rgb, alpha, weight = ipm_project_ground(
        image=image,
        K=K,
        T_ego_cam=T,
        ground_mask=np.ones((H_src, W_src), dtype=bool),
        erp_hw=(256, 512),
        max_distance_m=20.0,
        min_distance_m=0.1,
    )
    # The camera looks straight down, so all ground projects to phi <= 0 (v >= H_erp/2)
    ys, _ = np.where(alpha)
    assert ys.size > 0, "IPM should have produced some ERP pixels"
    # Most projected pixels should lie below the equator (downward-looking cam).
    assert (ys > 128).mean() > 0.8, "downward cam should produce ground below equator"
    print(
        f"[ipm_ground self-test] OK — projected {alpha.sum()} px, "
        f"mean v={ys.mean():.1f} (equator at 128)"
    )


if __name__ == "__main__":
    _self_test()
