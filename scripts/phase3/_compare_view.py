"""Show EXACTLY what the Surround360 view-interp changed vs L1: a full diff heatmap + zoomed
before/after crops at the 3 highest-change seams. Lets us vision-judge whether the fired edit is
a clean doubling-singling or a warp/ghost."""
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
    b = np.zeros((26, im.shape[1], 3), np.uint8)
    cv2.putText(b, t, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
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
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        r1, _a, w1 = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                          convergence_distance_m=None)
        l1s.append(r1); l1w.append(w1)
    L1 = hard_select(l1s, l1w)
    _, _, base = _label_and_base(l1s, l1w)
    res, touched, _ = view_interp_panorama(l1s, l1s, l1w, obj, base, band_half_width=80,
                                           max_disp=60.0, fb_thresh=2.0, protect_obj=False)

    diff = np.abs(res.astype(np.int16) - L1.astype(np.int16)).mean(2).astype(np.float32)
    # full diff heatmap on L1
    hm = cv2.applyColorMap(np.clip(diff * 4, 0, 255).astype(np.uint8), cv2.COLORMAP_JET)
    L1b = cv2.cvtColor(L1, cv2.COLOR_RGB2BGR)
    blend = np.where((diff > 2)[..., None], (0.4 * L1b + 0.6 * hm).astype(np.uint8), L1b)
    cv2.imwrite(str(OUT / "VIEWDIFF_heatmap.jpg"),
                cv2.resize(blend, (1400, 700), interpolation=cv2.INTER_AREA),
                [cv2.IMWRITE_JPEG_QUALITY, 92])

    # top-3 change columns (non-adjacent), zoomed before/after
    colscore = (diff > 2).sum(0).astype(np.float32)
    colscore = cv2.GaussianBlur(colscore.reshape(1, -1), (0, 0), 15).ravel()
    picks = []
    cs = colscore.copy()
    for _ in range(3):
        c = int(cs.argmax())
        if cs[c] <= 0:
            break
        picks.append(c)
        lo = max(0, c - 120); hi = min(W, c + 120)
        cs[lo:hi] = 0
    print("change cols:", picks, "edited_frac %.3f%%" % (100 * (touched > 0).mean()))
    rows = []
    for c in picks:
        roll = W // 2 - c
        L1r = np.roll(L1, roll, 1); resr = np.roll(res, roll, 1)
        cc = W // 2
        y0, y1 = 250, 800
        cropL = cv2.cvtColor(L1r[y0:y1, cc - 200:cc + 200], cv2.COLOR_RGB2BGR)
        cropR = cv2.cvtColor(resr[y0:y1, cc - 200:cc + 200], cv2.COLOR_RGB2BGR)
        pair = np.hstack([lbl(cropL, f"L1  col{c}"), lbl(cropR, f"A1_view col{c}")])
        rows.append(pair)
    if rows:
        wmax = max(r.shape[1] for r in rows)
        rows = [cv2.copyMakeBorder(r, 0, 0, 0, wmax - r.shape[1], cv2.BORDER_CONSTANT, value=0)
                if r.shape[1] < wmax else r for r in rows]
        cv2.imwrite(str(OUT / "VIEWDIFF_crops.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 95])
    # fixed TIGHT BMW zoom (near object at seam — check for warp/distortion of the car)
    for name, bc, y0, y1, half in [("bmw", 1760, 360, 560, 170)]:
        roll = W // 2 - bc; cc = W // 2
        L1r = np.roll(L1, roll, 1); resr = np.roll(res, roll, 1)
        zL = cv2.cvtColor(L1r[y0:y1, cc - half:cc + half], cv2.COLOR_RGB2BGR)
        zR = cv2.cvtColor(resr[y0:y1, cc - half:cc + half], cv2.COLOR_RGB2BGR)
        z = np.hstack([lbl(zL, "L1"), lbl(zR, "A1_view")])
        z = cv2.resize(z, (z.shape[1] * 2, z.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(str(OUT / f"VIEWDIFF_{name}.jpg"), z, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("[saved] VIEWDIFF_heatmap.jpg + VIEWDIFF_crops.jpg + VIEWDIFF_bmw.jpg")


if __name__ == "__main__":
    raise SystemExit(main())
