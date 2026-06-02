"""Test the --prealign ground fix on the user's two circled spots.
3-way: L1 | view+none(flow only) | view+ground(flow + LiDAR GROUND-plane prealign).
Crops at (a) the near-road seam (the 'didn't connect' spot) and (b) the gray car. Also prints
edited-fraction + near/far fired breakdown so we can see the near-ground FB-abstain drop.
Optional --uuid for generality checks on other anchors."""
from __future__ import annotations
import sys, argparse
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

ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/a1_streetview_pipeline")
erp_hw = (1024, 2048); H, W = erp_hw


def lbl(im, t):
    b = np.zeros((22, im.shape[1], 3), np.uint8)
    cv2.putText(b, t, (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return np.vstack([b, im])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default="02a00399-3857-444e-8db3-a8f58489c394")
    ap.add_argument("--tag", default="bmw")
    ap.add_argument("--struct", type=float, default=8.0)
    a = ap.parse_args()
    loader = AV2RingLoader(ROOT / a.uuid)
    ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    pts, _, _ = load_lidar_feather(ROOT / a.uuid, ts[0], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = fit_planes_p3(pts)
    obj = off_plane_object_erp(pts, ground, facades, erp_hw)
    conv_g = build_plane_convergence(ground, [], erp_hw) if ground else None

    l1s, l1w, gslabs = [], [], []
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        r1, _x, w1 = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                          convergence_distance_m=None)
        l1s.append(r1); l1w.append(w1)
        if conv_g is not None:
            rp, _y, _z = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                              convergence_distance_m=conv_g)
            m = (rp.astype(np.float32).sum(2) > 0)[..., None]
            gslabs.append(np.where(m, rp.astype(np.float32), r1.astype(np.float32)))
        else:
            gslabs.append(r1)
    L1 = hard_select(l1s, l1w)
    _, _, base = _label_and_base(l1s, l1w)

    none_res, t_none, _ = view_interp_panorama(l1s, l1s, l1w, obj, base, 80, 60.0, 2.0,
                                               protect_obj=False, struct_thresh=0.0)
    grnd_res, t_grnd, _ = view_interp_panorama(gslabs, l1s, l1w, obj, base, 80, 60.0, 2.0,
                                               protect_obj=False, struct_thresh=a.struct)
    print(f"[{a.tag} struct={a.struct}] edited: none {100*(t_none>0).mean():.2f}%  ground {100*(t_grnd>0).mean():.2f}%")

    regions = {"groundseam": (880, 1240, 430, 600), "graycar": (560, 1000, 360, 560)}
    for name, (u0, u1, v0, v1) in regions.items():
        def crop(im):
            return cv2.cvtColor(im[v0:v1, u0:u1], cv2.COLOR_RGB2BGR)
        row = np.hstack([lbl(crop(L1), "L1"), lbl(crop(none_res), "view+none"),
                         lbl(crop(grnd_res), "view+ground")])
        row = cv2.resize(row, (row.shape[1] * 2, row.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(str(OUT / f"GND_{a.tag}_{name}.jpg"), row, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print("[saved]", f"GND_{a.tag}_{name}.jpg")


if __name__ == "__main__":
    raise SystemExit(main())
