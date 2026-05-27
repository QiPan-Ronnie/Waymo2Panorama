"""Run our L1 sphere baseline on Xihan's Waymo E2E Driving frame.

Inputs (from parse_waymo_e2ed_frame.py output):
  --extracted-dir : folder with cam_NN_NAME.jpg + frame_meta.json

Pipeline:
  1) Load 8 cam (K, T_ego_cam, image_undistorted) — undistorted via cv2.undistort
     using (k1, k2, k3, p1, p2) from frame_meta.
  2) For each cam: sphere_projection.render_camera_to_erp() → (slab, alpha, weight)
  3) multiband_blend across 8 slabs.
  4) Save ERP output.

Output: --out-png path (e.g., /content/.../l1_waymo_e2ed_8e7373_4096x2048.png)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

# Add code/ to import path
import sys
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code"))

from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.multiband import multiband_blend
from waymo2panorama.blending.hard_hdr_of import apply_hdr, hard_select


# 8-cam Waymo ring pairs (CCW), see waymo_T_to_opencv_T below
# Slab order in runner is RING_CCW = [FRONT, FRONT_LEFT, SIDE_LEFT, REAR_LEFT,
#                                     REAR, REAR_RIGHT, SIDE_RIGHT, FRONT_RIGHT].
# So adjacency:
WAYMO_RING_PAIRS_8 = [
    (0, 1), (1, 2), (2, 3), (3, 4),  # FRONT → ... → REAR (CCW)
    (4, 5), (5, 6), (6, 7),          # REAR → ... → FRONT_RIGHT (CCW continues)
    (7, 0),                           # FRONT_RIGHT → FRONT (closes loop)
]


def compute_hdr_gains_waymo8(slabs, weights, centered: bool = True) -> list[float]:
    """8-cam ring-aware HDR (vs the 7-cam hardcoded one in hard_hdr_of.py).
    Same math as compute_hdr_gains() but with WAYMO_RING_PAIRS_8.
    """
    n = len(slabs)
    assert n == 8, f"this runner assumes 8 cams in CCW ring order, got {n}"
    A_rows, b_rows = [], []
    for (i, j) in WAYMO_RING_PAIRS_8:
        overlap = (weights[i] > 1e-6) & (weights[j] > 1e-6)
        if int(overlap.sum()) < 100:
            continue
        y_i = cv2.cvtColor(slabs[i].astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]
        y_j = cv2.cvtColor(slabs[j].astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]
        m_i = max(float(y_i[overlap].mean()), 1.0)
        m_j = max(float(y_j[overlap].mean()), 1.0)
        row = np.zeros(n)
        row[i] = 1.0
        row[j] = -1.0
        A_rows.append(row)
        b_rows.append(np.log(m_j) - np.log(m_i))
    anchor = np.zeros(n); anchor[0] = 1.0
    A_rows.append(anchor); b_rows.append(0.0)
    A = np.array(A_rows); b = np.array(b_rows)
    log_g, *_ = np.linalg.lstsq(A, b, rcond=None)
    if centered:
        log_g = log_g - log_g.mean()
    g = np.exp(log_g)
    g = np.clip(g, 0.5, 2.0)
    return list(g)


# Waymo proto's extrinsic stores T_ego_cam in WAYMO CAMERA convention:
#   x = forward (out of lens), y = left, z = up
# Our sphere_projection.render_camera_to_erp expects OpenCV camera convention:
#   x = right, y = down, z = forward
# To convert, post-multiply T_ego_waymocam by the rotation R_waymocam_opencvcam.
# Mapping a point P_opencv = (X_o, Y_o, Z_o) to its same-space point P_waymo:
#   X_w (forward) =  Z_o
#   Y_w (left)    = -X_o
#   Z_w (up)      = -Y_o
# So R_waymocam_opencvcam = [[0, 0, 1], [-1, 0, 0], [0, -1, 0]].
R_WAYMOCAM_OPENCVCAM = np.array([
    [0.0,  0.0,  1.0],
    [-1.0, 0.0,  0.0],
    [0.0, -1.0,  0.0],
], dtype=np.float64)


def waymo_T_to_opencv_T(T_ego_waymocam: np.ndarray) -> np.ndarray:
    """T_ego_waymocam (Waymo proto) → T_ego_opencvcam (our sphere_projection)."""
    T = T_ego_waymocam.copy()
    T[:3, :3] = T[:3, :3] @ R_WAYMOCAM_OPENCVCAM
    return T


def undistort_image(img: np.ndarray, K: np.ndarray, distortion: dict) -> np.ndarray:
    """Apply cv2.undistort using Waymo (k1, k2, k3, p1, p2) ordering.

    cv2 expects dist coefs as (k1, k2, p1, p2, k3) — note the order swap.
    """
    dist = np.array([
        distortion["k1"], distortion["k2"],
        distortion["p1"], distortion["p2"],
        distortion["k3"],
    ], dtype=np.float64)
    return cv2.undistort(img, K, dist)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extracted-dir", required=True, type=Path,
                    help="output of parse_waymo_e2ed_frame.py (cam_*.jpg + frame_meta.json)")
    ap.add_argument("--out-png", required=True, type=Path)
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096,
                    help="default 4096 to match Xihan's distance-to-boundary panorama")
    ap.add_argument("--no-undistort", action="store_true",
                    help="skip cv2.undistort (faster, slightly less geometric quality)")
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--blend-mode",
                    choices=["multiband", "hdr_multiband", "hard_hdr", "hard_select_only"],
                    default="multiband",
                    help="multiband=L1 only (no color fix); hdr_multiband=L1+L2 HDR+multiband (RECOMMENDED, smooth seams + color fix); hard_hdr=L1+L2 HDR+hard_select (no OF); hard_select_only=ablation, no HDR")
    args = ap.parse_args()

    meta = json.loads((args.extracted_dir / "frame_meta.json").read_text(encoding="utf-8"))
    cams = meta["cams"]
    # Sort cams into CCW ring order so multiband / hard_hdr_of's "slab[i]
    # overlaps slab[i+1]" assumption matches physical adjacency.
    # Waymo CameraName ring CCW (from above): FRONT, FRONT_LEFT, SIDE_LEFT,
    # REAR_LEFT, REAR, REAR_RIGHT, SIDE_RIGHT, FRONT_RIGHT.
    RING_CCW = ["FRONT", "FRONT_LEFT", "SIDE_LEFT", "REAR_LEFT",
                "REAR", "REAR_RIGHT", "SIDE_RIGHT", "FRONT_RIGHT"]
    name2order = {n: i for i, n in enumerate(RING_CCW)}
    cams = sorted(cams, key=lambda c: name2order.get(c["name"], 999))
    print(f"=== L1 sphere baseline on Waymo E2ED frame {meta['context_name']} ===")
    print(f"  cam ring CCW order: {[c['name'] for c in cams]}")
    print(f"   {len(cams)} cams, ERP target {args.erp_w}x{args.erp_h}, "
          f"undistort={'NO' if args.no_undistort else 'YES'}")

    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for c in cams:
        cam_name = c["name"]
        cam_idx = c["idx"]
        jpg_path = args.extracted_dir / f"cam_{cam_idx:02d}_{cam_name}.jpg"
        bgr = cv2.imread(str(jpg_path))
        if bgr is None:
            raise FileNotFoundError(jpg_path)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        K = np.array(c["K"], dtype=np.float64)
        T_ego_waymocam = np.array(c["T_ego_cam_flat16_rowmajor"], dtype=np.float64).reshape(4, 4)
        # Convert Waymo cam convention (x=forward, y=left, z=up) -> OpenCV (x=right, y=down, z=forward)
        T_ego_cam = waymo_T_to_opencv_T(T_ego_waymocam)

        if not args.no_undistort:
            rgb = undistort_image(rgb, K, c["distortion"])

        slab, alpha, weight = render_camera_to_erp(
            image=rgb, K=K, T_ego_cam=T_ego_cam,
            erp_hw=(args.erp_h, args.erp_w),
            convergence_distance_m=None,   # legacy L1 (rotation-only, no parallax compensation)
        )
        print(f"  cam {cam_idx:2d} {cam_name:18s} valid_px={int(alpha.sum())}  "
              f"weight_sum={float(weight.sum()):.1f}")
        slabs.append(slab)
        weights.append(weight)

    print(f"=== blending {len(slabs)} slabs (mode={args.blend_mode}) ===")
    if args.blend_mode == "multiband":
        erp = multiband_blend(slabs, weights, num_bands=args.num_bands, wrap=True)
    elif args.blend_mode == "hdr_multiband":
        # L1 + 8-cam L2 HDR + multiband (smooth seam + color fix — recommended)
        gains = compute_hdr_gains_waymo8(slabs, weights)
        print(f"  HDR gains: {[round(g,3) for g in gains]}")
        slabs_hdr = apply_hdr(slabs, gains)
        erp = multiband_blend(slabs_hdr, weights, num_bands=args.num_bands, wrap=True)
    elif args.blend_mode == "hard_hdr":
        # L1 + 8-cam L2 HDR + hard_select (sharper seams + color fix)
        gains = compute_hdr_gains_waymo8(slabs, weights)
        print(f"  HDR gains: {[round(g,3) for g in gains]}")
        slabs_hdr = apply_hdr(slabs, gains)
        erp = hard_select(slabs_hdr, weights)
    elif args.blend_mode == "hard_select_only":
        erp = hard_select(slabs, weights)
    else:
        raise ValueError(f"unknown blend_mode {args.blend_mode}")

    args.out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out_png), cv2.cvtColor(erp, cv2.COLOR_RGB2BGR))
    print(f"=== wrote {args.out_png}  ({erp.shape[1]}x{erp.shape[0]}) ===")


if __name__ == "__main__":
    main()
