"""
v2 test: multi-R per-pixel with HDR pre-step + window NCC + smoothed R map.

Compares 5 methods at 2048x4096:
  1. L1 multiband (R=inf, baseline)
  2. L1 hard_select (R=inf, current shipped without L2/L3)
  3. Multi-R hard_select v1 (Y diff, no smoothing)
  4. Multi-R hard_select v2 (HDR + window NCC + median R smoothing)
  5. R-index colormap for v2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.pipeline.stitch_frame import stitch_one_frame
from waymo2panorama.blending.multi_radius_select import (
    render_multi_radius_select,
    render_multi_radius_select_v2,
)
from waymo2panorama.blending.hard_hdr_of import hard_select
from waymo2panorama.projection.sphere_projection import render_camera_to_erp


def stack_panels(panels, labels, crop=None):
    if crop is not None:
        y0, y1, x0, x1 = crop
        panels = [p[y0:y1, x0:x1] for p in panels]
    h, w = panels[0].shape[:2]
    label_h = 36
    out = []
    for p, lab in zip(panels, labels):
        band = np.zeros((label_h, w, 3), dtype=np.uint8)
        cv2.putText(band, lab, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        out.append(np.vstack([band, p.astype(np.uint8)]))
    return np.vstack(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, default=0)
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--ncc-window", type=int, default=9)
    ap.add_argument("--smooth-r", type=int, default=11)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    loader = AV2RingLoader(Path(args.log_dir))
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[args.anchor_idx])
    log_short = Path(args.log_dir).name.split("-")[0]

    print("[1/5] L1 multiband baseline", flush=True)
    erp_mb = stitch_one_frame(frame, erp_hw=erp_hw, blend_mode="multiband")

    print("[2/5] L1 hard_select R=inf", flush=True)
    slabs, weights = [], []
    for cam in RING_CAMS_7:
        img = frame.images[cam]
        calib = frame.calibrations[cam]
        s, _, w = render_camera_to_erp(img, calib.K, calib.T_ego_cam, erp_hw=erp_hw)
        slabs.append(s); weights.append(w)
    erp_hard_inf = hard_select(slabs, weights).astype(np.uint8)

    print("[3/5] Multi-R v1 (Y diff, no smoothing)", flush=True)
    erp_v1, r_idx_v1 = render_multi_radius_select(
        frame, erp_hw=erp_hw,
        R_values=[None, 30.0, 10.0, 5.0, 3.0],
        blend_mode="hard_select",
    )

    print(f"[4/5] Multi-R v2 (HDR pre + NCC win={args.ncc_window} + median R k={args.smooth_r})", flush=True)
    erp_v2, r_idx_v2 = render_multi_radius_select_v2(
        frame, erp_hw=erp_hw,
        R_values=[None, 30.0, 10.0, 5.0, 3.0],
        blend_mode="hard_select",
        apply_hdr_pre=True,
        ncc_window=args.ncc_window,
        smooth_R_kernel=args.smooth_r,
    )

    print("[5/5] R-index colormaps", flush=True)
    cmap = np.array([
        [40, 40, 200],   # inf - blue
        [40, 200, 200],  # 30  - cyan
        [40, 200, 40],   # 10  - green
        [200, 200, 40],  # 5   - yellow
        [200, 40, 40],   # 3   - red
    ], dtype=np.uint8)
    r_viz_v1 = cmap[r_idx_v1]
    r_viz_v2 = cmap[r_idx_v2]

    panels = [erp_mb, erp_hard_inf, erp_v1, erp_v2, r_viz_v2]
    labels = [
        "L1 multiband (R=inf)",
        "L1 hard_select (R=inf)",
        "v1: per-pixel Y diff (NEG, no smooth)",
        f"v2: HDR + NCC{args.ncc_window} + medR{args.smooth_r}",
        "v2 R-index per pixel (blue=inf -> red=3m)",
    ]

    full = stack_panels(panels, labels)
    fp = out_dir / f"{log_short}_a{args.anchor_idx:03d}_v2_full_stack.png"
    cv2.imwrite(str(fp), cv2.cvtColor(full, cv2.COLOR_RGB2BGR))
    print(f"[saved] {fp}", flush=True)

    H, W = erp_hw
    crop = (int(H * 0.30), int(H * 0.85), int(W * 0.10), int(W * 0.45))
    bmw = stack_panels(panels, labels, crop=crop)
    fb = out_dir / f"{log_short}_a{args.anchor_idx:03d}_v2_bmw_crop.png"
    cv2.imwrite(str(fb), cv2.cvtColor(bmw, cv2.COLOR_RGB2BGR))
    print(f"[saved] {fb}", flush=True)


if __name__ == "__main__":
    main()
