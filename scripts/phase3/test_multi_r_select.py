"""
Test per-pixel multi-R selection vs L1 baseline + L1 hard_select.

Renders the same anchor under 4 methods, saves a vertical stack panel
with BMW crop for visual comparison.
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
from waymo2panorama.data_io.av2_loader import AV2RingLoader
from waymo2panorama.pipeline.stitch_frame import stitch_one_frame
from waymo2panorama.blending.multi_radius_select import render_multi_radius_select


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
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    loader = AV2RingLoader(Path(args.log_dir))
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[args.anchor_idx])
    log_short = Path(args.log_dir).name.split("-")[0]

    print("[1/4] L1 multiband baseline (R=inf)")
    erp_mb = stitch_one_frame(frame, erp_hw=erp_hw, blend_mode="multiband")

    print("[2/4] L1 hard_select (R=inf, no L2/L3)")
    erp_hs_inf = stitch_one_frame(frame, erp_hw=erp_hw, blend_mode="hard_hdr_of")  # has L2+L3 too — not isolated
    # Actually that's not pure hard_select. Let me also make a pure-hard one.
    # Use a manual pipeline:
    from waymo2panorama.blending.hard_hdr_of import hard_select
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    slabs, weights = [], []
    for cam in RING_CAMS_7:
        img = frame.images[cam]
        calib = frame.calibrations[cam]
        s, _, w = render_camera_to_erp(img, calib.K, calib.T_ego_cam, erp_hw=erp_hw)
        slabs.append(s); weights.append(w)
    erp_hard_inf = hard_select(slabs, weights).astype(np.uint8)

    print("[3/4] Multi-R hard_select (per-pixel R)")
    erp_multi_hard, r_idx = render_multi_radius_select(
        frame, erp_hw=erp_hw,
        R_values=[None, 30.0, 10.0, 5.0, 3.0],
        blend_mode="hard_select",
    )

    print("[4/4] Multi-R weighted (per-pixel R)")
    erp_multi_w, _ = render_multi_radius_select(
        frame, erp_hw=erp_hw,
        R_values=[None, 30.0, 10.0, 5.0, 3.0],
        blend_mode="weighted",
    )

    # R-index colormap: 0=inf(blue), 1=30m(cyan), 2=10m(green), 3=5m(yellow), 4=3m(red)
    cmap = np.array([
        [40, 40, 200],   # inf
        [40, 200, 200],  # 30
        [40, 200, 40],   # 10
        [200, 200, 40],  # 5
        [200, 40, 40],   # 3
    ], dtype=np.uint8)
    r_viz = cmap[r_idx]

    panels = [erp_mb, erp_hard_inf, erp_multi_hard, erp_multi_w, r_viz]
    labels = ["L1 multiband (R=inf)", "L1 hard_select (R=inf)",
              "MULTI-R hard_select (per-pixel R)", "MULTI-R weighted",
              "R index per pixel (blue=inf -> red=3m)"]

    # Full stack
    full = stack_panels(panels, labels)
    fp = out_dir / f"{log_short}_a{args.anchor_idx:03d}_full_stack.png"
    cv2.imwrite(str(fp), cv2.cvtColor(full, cv2.COLOR_RGB2BGR))
    print(f"[saved] {fp}")

    # BMW crop (right-front area)
    H, W = erp_hw
    crop = (int(H * 0.30), int(H * 0.85), int(W * 0.10), int(W * 0.45))
    bmw = stack_panels(panels, labels, crop=crop)
    fb = out_dir / f"{log_short}_a{args.anchor_idx:03d}_bmw_crop.png"
    cv2.imwrite(str(fb), cv2.cvtColor(bmw, cv2.COLOR_RGB2BGR))
    print(f"[saved] {fb}")


if __name__ == "__main__":
    main()
