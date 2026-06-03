"""Isolate the WHITE-SPOT artifact the user caught at align seams: is it the WARP, or the E1.5
low-freq multiband blend? Per seam, show L1 | hard_select(warped)[no lowfreq] | align[+lowfreq],
high zoom. If the spot appears only in the last column → it's the multiband overshoot; if already in
the middle → it's the warp pulling bright content."""
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
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select
from waymo2panorama.blending.seam_confined import blend_seam_confined
from run_a1_streetview_pipeline import flow_align_chain, _circular_center_col
from run_a0_plane_dibr_probe import load_lidar_feather

BMW = "02a00399-3857-444e-8db3-a8f58489c394"
ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/a1_streetview_pipeline")
erp_hw = (1024, 2048); H, W = erp_hw


def lab(im, t):
    b = np.zeros((22, im.shape[1], 3), np.uint8)
    cv2.putText(b, t, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return np.vstack([b, im])


def main():
    loader = AV2RingLoader(ROOT / BMW)
    ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    l1s, l1w = [], []
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        r1, _x, w1 = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                          convergence_distance_m=None)
        l1s.append(r1); l1w.append(w1)
    L1 = hard_select(l1s, l1w)
    warped = flow_align_chain(l1s, l1w, band_hw=80, max_disp=40.0, fb_thresh=2.0)
    align_nolf = hard_select(warped, l1w)                                   # warp + hard_select only
    align_full = blend_seam_confined(warped, l1w, band_half_width=80, lowfreq_cutoff=5)["out"]  # + E1.5

    # crop every seam, stack; spot the white patch
    rows = []
    for (i, j) in RING_PAIRS:
        ov = (l1w[i] > 1e-6) & (l1w[j] > 1e-6)
        cc = _circular_center_col(ov, W)
        if cc is None:
            continue
        roll = W // 2 - cc; c = W // 2
        def cr(im):
            return cv2.cvtColor(np.roll(im, roll, 1)[260:820, c-150:c+150], cv2.COLOR_RGB2BGR)
        rows.append(np.hstack([lab(cr(L1), f"L1 s{i}{j}"), lab(cr(align_nolf), "warp+hardsel"),
                               lab(cr(align_full), "align(+lowfreq)")]))
    grid = np.vstack(rows)
    cv2.imwrite(str(OUT / "WHITESPOT_allseams.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("[saved] WHITESPOT_allseams.jpg  (rows=7 seams; cols: L1 | warp+hardsel | align+lowfreq)")


if __name__ == "__main__":
    raise SystemExit(main())
