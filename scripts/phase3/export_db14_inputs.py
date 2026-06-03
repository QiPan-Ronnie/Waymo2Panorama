"""Export exact full-resolution DB-14 BMW input panos.

This prepares candidate init images for the DiT360 thin-trimap seam test.
It intentionally exports the raw 1024x2048 pano, not the review montages.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")

import run_a1_streetview_pipeline as a1  # noqa: E402


ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db14_inputs")
H, W = 1024, 2048


def save_rgb(path: Path, image_rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), cv2.cvtColor(np.clip(image_rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError(f"failed to write {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--tag", default="bmw")
    ap.add_argument("--band-hw", type=int, default=80)
    ap.add_argument("--max-disp", type=float, default=60.0)
    ap.add_argument("--fb-thresh", type=float, default=2.0)
    args = ap.parse_args()
    t0 = time.time()

    loader = a1.AV2RingLoader(ROOT / args.uuid)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[args.anchor])
    pts, _labels, _delta_ms = a1.load_lidar_feather(ROOT / args.uuid, ts[args.anchor], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = a1.fit_planes_p3(pts)
    obj_mask = a1.off_plane_object_erp(pts, ground, facades, (H, W))
    cams = {cam: frame.calibrations[cam] for cam in a1.RING_CAMS_7}

    l1_slabs, l1_w = [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        slab, _alpha, weight = a1.render_camera_to_erp(
            frame.images[cam],
            cb.K,
            cb.T_ego_cam,
            erp_hw=(H, W),
            convergence_distance_m=None,
        )
        l1_slabs.append(slab)
        l1_w.append(weight)

    l1 = a1.hard_select(l1_slabs, l1_w)
    view_none, touched, n_pairs = a1.view_interp_panorama(
        l1_slabs,
        l1_slabs,
        l1_w,
        obj_mask,
        l1,
        band_half_width=args.band_hw,
        max_disp=args.max_disp,
        fb_thresh=args.fb_thresh,
        protect_obj=False,
        struct_thresh=0.0,
    )

    save_rgb(OUT / f"A1_view_none_{args.tag}_1024x2048.png", view_none)
    save_rgb(OUT / f"L1_{args.tag}_1024x2048.png", l1)
    print(
        f"[exported] A1_view_none_{args.tag}_1024x2048.png "
        f"pairs={n_pairs} edited_frac={100 * (touched > 0).mean():.2f}% "
        f"rt={time.time() - t0:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
