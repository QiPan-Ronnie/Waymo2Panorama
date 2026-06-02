"""Vision diagnosis of the FINAL regression the user caught: compare, on the SAME render,
L1 | view+none(flow only) | FINAL(ground+struct15+obj-route+photo). Full-pano stack + the 3
regions where FINAL differs MOST from view+none, so we can SEE the blend/overlap ghost."""
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
from waymo2panorama.blending.seam_confined import _label_and_base, blend_seam_confined
from run_a1_streetview_pipeline import (fit_planes_p3, build_plane_convergence,
                                        off_plane_object_erp, view_interp_panorama,
                                        object_coherent_weights, flow_align_chain)
from waymo2panorama.blending.seam_confined import blend_seam_confined as _bsc
from run_a0_plane_dibr_probe import load_lidar_feather

BMW = "02a00399-3857-444e-8db3-a8f58489c394"
ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/a1_streetview_pipeline")
erp_hw = (1024, 2048); H, W = erp_hw


def rw(im, w=1300):
    return cv2.resize(np.clip(im, 0, 255).astype(np.uint8), (w, round(im.shape[0]*w/im.shape[1])),
                      interpolation=cv2.INTER_AREA)


def lab(im, t):
    b = np.zeros((26, im.shape[1], 3), np.uint8)
    cv2.putText(b, t, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return np.vstack([b, im])


def main():
    loader = AV2RingLoader(ROOT / BMW)
    ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    pts, _, _ = load_lidar_feather(ROOT / BMW, ts[0], max_delta_ms=75.0)
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

    none_res, _, _ = view_interp_panorama(l1s, l1s, l1w, obj, base, 80, 60.0, 2.0,
                                          protect_obj=False, struct_thresh=0.0)
    # NEW: align = warp-to-align + HARD-SELECT + low-freq colour (single-source, cannot ghost)
    warped = flow_align_chain(l1s, l1w, band_hw=80, max_disp=40.0, fb_thresh=2.0)
    final_res = _bsc(warped, l1w, band_half_width=80, lowfreq_cutoff=5)["out"]

    stack = np.vstack([lab(rw(L1), "L1 hard_select (clean baseline)"),
                       lab(rw(none_res), "view+none (flow ALPHA-BLEND -> ghost)"),
                       lab(rw(final_res), "align (warp + HARD-SELECT + lowfreq -> single source)")])
    cv2.imwrite(str(OUT / "VIS_stack3.jpg"), cv2.cvtColor(stack, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 92])

    # FIXED meaningful spots: gray car (front-left), dark-wall/storefront seam, front-center lane
    picks = [(720, "graycar"), (1150, "darkwall-seam"), (1024, "front-lane")]
    rows = []
    for u, name in picks:
        roll = W // 2 - u; cc = W // 2
        def cr(im):
            return cv2.cvtColor(np.roll(im, roll, 1)[330:560, cc-170:cc+170], cv2.COLOR_RGB2BGR)
        rows.append(np.hstack([lab(cr(L1), f"L1 {name}"), lab(cr(none_res), "view+none(blend)"),
                               lab(cr(final_res), "align(single-source)")]))
    if rows:
        rr = [r if r.shape[1] == max(x.shape[1] for x in rows) else
              cv2.copyMakeBorder(r, 0, 0, 0, max(x.shape[1] for x in rows)-r.shape[1], cv2.BORDER_CONSTANT) for r in rows]
        z = np.vstack(rr)
        z = cv2.resize(z, (z.shape[1]*2, z.shape[0]*2), interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(str(OUT / "VIS_align_vs_none_zoom.jpg"), z, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("[saved] VIS_stack3.jpg + VIS_align_vs_none_zoom.jpg")


if __name__ == "__main__":
    raise SystemExit(main())
