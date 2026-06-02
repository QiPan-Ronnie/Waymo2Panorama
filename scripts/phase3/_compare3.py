"""Decisive 3-way A/B: L1 | view+none(flow only) | view+plane(flow + LiDAR-plane struct trust).
Tight crops at (a) the textureless DARK WALL seam and (b) a TEXTURED storefront seam, to settle
whether the LiDAR plane edge cleanly extends the win to the wall or introduces artifacts."""
from __future__ import annotations
import sys
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")

from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.hard_hdr_of import hard_select
from waymo2panorama.blending.seam_confined import _label_and_base
from run_a1_streetview_pipeline import (fit_planes_p3, build_plane_convergence,
                                        off_plane_object_erp, view_interp_panorama)
from run_a0_plane_dibr_probe import load_lidar_feather

BMW = "02a00399-3857-444e-8db3-a8f58489c394"
ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/a1_streetview_pipeline")
erp_hw = (1024, 2048); H, W = erp_hw


def lbl(im, t):
    b = np.zeros((24, im.shape[1], 3), np.uint8)
    cv2.putText(b, t, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return np.vstack([b, im])


def main():
    loader = AV2RingLoader(ROOT / BMW)
    ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    pts, _, _ = load_lidar_feather(ROOT / BMW, ts[0], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = fit_planes_p3(pts)
    conv = build_plane_convergence(ground, facades, erp_hw)
    obj = off_plane_object_erp(pts, ground, facades, erp_hw)

    l1s, l1w = [], []
    pls = []
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        r1, _a, w1 = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                          convergence_distance_m=None)
        rp, _ap, _wp = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                            convergence_distance_m=conv)
        l1s.append(r1); l1w.append(w1); pls.append(rp)
    L1 = hard_select(l1s, l1w)
    _, _, base = _label_and_base(l1s, l1w)
    filled = []
    for ps, ls in zip(pls, l1s):
        m = (ps.astype(np.float32).sum(2) > 0)[..., None]
        filled.append(np.where(m, ps.astype(np.float32), ls.astype(np.float32)))

    none_res, _, _ = view_interp_panorama(l1s, l1s, l1w, obj, base, 80, 60.0, 2.0,
                                          protect_obj=False, struct_thresh=0.0)
    plane_res, _, _ = view_interp_panorama(filled, l1s, l1w, obj, base, 80, 60.0, 2.0,
                                           protect_obj=False, struct_thresh=8.0)

    # crop regions (col center, rows). wall ~ behind/right (dark facade); storefront ~ front-left.
    regions = {"darkwall": (980, 360, 620, 200), "storefront": (1640, 330, 560, 180)}
    for name, (bc, y0, y1, half) in regions.items():
        roll = W // 2 - bc; cc = W // 2
        def crop(im):
            r = np.roll(im, roll, 1)
            return cv2.cvtColor(r[y0:y1, cc - half:cc + half], cv2.COLOR_RGB2BGR)
        row = np.hstack([lbl(crop(L1), "L1"), lbl(crop(none_res), "view+none(flow)"),
                         lbl(crop(plane_res), "view+plane(flow+LiDAR)")])
        row = cv2.resize(row, (row.shape[1] * 2, row.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(str(OUT / f"CMP3_{name}.jpg"), row, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print("[saved]", f"CMP3_{name}.jpg")


if __name__ == "__main__":
    raise SystemExit(main())
