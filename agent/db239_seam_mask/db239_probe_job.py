"""DB-239 probe: one frame of 00a6ffc1 (the blue-shirt scene koi named).

Measures the cross-camera disagreement on the delivered v15 ERP, sweeps the
threshold, and writes a panel for human eye verification.  Chooses nothing.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, "/content")
import db238_screen as SC  # noqa: E402
import db239_seam_mask as SM  # noqa: E402

D = "/content/drive/MyDrive/koi_waymo2pano_colab"
LOG = D + "/data/argoverse2/val/00a6ffc1-6ce9-3bc3-a060-6006e9893a1a"
V15 = D + "/datasets/av2_1plus92_v15/train/00a6ffc1_w1"
OUT = D + "/results/db239_seam_mask/probe_00a6ffc1"
WIN0 = 63
TAUS = [8, 12, 16, 20, 25, 30, 40]


def main(frame_idx=37, n_lidar=5):
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    anchor = WIN0 + frame_idx
    man = SC.manifest_from_dir(LOG, anchor, n_lidar)
    cal = SC.load_calibration(LOG)
    C = np.stack([cal[c]["t"] for c in SC.CAMERAS], 0).mean(0)
    lidar = SC.load_lidar_at(LOG, man["lidar_ts"])
    imgs = SC.load_images(LOG, man["cam_ts"])
    Zd, dist_px = SC.depth_field(lidar, C)
    sup = SC.camera_support(cal)

    frame = np.asarray(Image.open(
        "%s/A/frames/fr_%04d.png" % (V15, frame_idx)).convert("RGB"))
    keep = np.asarray(Image.open(
        "%s/A/masks/mk_%04d.png" % (V15, frame_idx)).convert("L")) > 127

    Dm, OV, per_pair = SM.seam_disagreement(cal, imgs, sup, Zd, C, domain=keep)

    rec = {"anchor": anchor, "frame_idx": frame_idx, "n_lidar": n_lidar,
           "lidar_pts": int(len(lidar)),
           "keep_frac": round(float(keep.mean()), 5),
           "overlap_frac_of_frame": round(float(OV.mean()), 5),
           "overlap_frac_of_keep": round(float(OV.sum() / max(keep.sum(), 1)), 5),
           "pairs": per_pair, "sweep": {}}

    for tau in TAUS:
        bad = SM.seam_mask(Dm, OV, tau)
        rec["sweep"][str(tau)] = {
            "bad_frac_of_frame": round(float(bad.mean()), 5),
            "bad_frac_of_keep": round(float(bad.sum() / max(keep.sum(), 1)), 5),
            "bad_frac_of_overlap": round(float((bad & OV).sum() / max(OV.sum(), 1)), 5),
            "bad_px": int(bad.sum())}
        Image.fromarray((bad * 255).astype(np.uint8)).save(
            "%s/badmask_tau%02d.png" % (OUT, tau))
        mf, nk = SM.apply_seam_mask(frame, keep, bad)
        Image.fromarray(mf).save("%s/frame_masked_tau%02d.png" % (OUT, tau))
        Image.fromarray((nk * 255).astype(np.uint8)).save(
            "%s/mask_tau%02d.png" % (OUT, tau))

    # raw disagreement, as 16-bit so nothing is lost, plus an 8-bit view
    Image.fromarray(np.clip(Dm * 256, 0, 65535).astype(np.uint16)).save(
        "%s/disagreement_u16.png" % OUT)
    Image.fromarray(np.clip(Dm * 4, 0, 255).astype(np.uint8)).save(
        "%s/disagreement_view.png" % OUT)
    Image.fromarray((OV * 255).astype(np.uint8)).save("%s/overlap_domain.png" % OUT)
    Image.fromarray(frame).save("%s/frame_v15_A.png" % OUT)

    # where is the worst contradiction?  report it in ERP coords and azimuth.
    ys, xs = np.nonzero(SM.seam_mask(Dm, OV, 20))
    if len(ys):
        rec["worst_region_bbox_tau20"] = [int(xs.min()), int(ys.min()),
                                          int(xs.max()), int(ys.max())]
    yi, xi = np.unravel_index(int(np.argmax(Dm)), Dm.shape)
    rec["peak"] = {"x": int(xi), "y": int(yi), "value": round(float(Dm.max()), 2),
                   "azimuth_deg": round(180.0 - (xi + 0.5) / SC.W * 360.0, 2),
                   "elev_deg": round(90.0 - (yi + 0.5) / SC.H * 180.0, 2)}
    rec["total_s"] = round(time.time() - t0, 1)
    with open("%s/probe.json" % OUT, "w") as fh:
        json.dump(rec, fh, indent=1)
    print(json.dumps(rec, indent=1), flush=True)
    print("DB239_PROBE_DONE", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 37)
