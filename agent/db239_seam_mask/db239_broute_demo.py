"""DB-239 B-route demo - koi's literal instruction, rendered end to end.

    "七张就是纯拼接投影回去 ... 只有接缝处会有问题, 把接缝处稍微 mask 掉"
    (koi 2026-08-07 00:44:59)

One frame of 00a6ffc1 (the koi-named acceptance scene), built as:

  1. per-camera territory (EMC poses, max-facing owner among supporting cameras)
  2. ROTATION-ONLY sampling inside each territory - the depth field never touches
     a pixel, so the warp field is identically zero by construction (the truck
     wheel stays round; text keeps its shape)
  3. adaptive seam mask: wherever two adjacent cameras' rotation-only views
     photometrically contradict each other (that is parallax, b/Z), the pixels
     are blacked and demoted to RECONSTRUCT.  Far content agrees -> no mask;
     near content disagrees -> exactly-wide-enough mask.
  4. domain = the shipped v15 band KEEP mask, so every comparison against the
     shipped product is apples to apples.

No gain solve is applied: per-camera exposure differences stay as recorded
(koi 08-07 08:24: view-to-view GT inconsistency is expected and acceptable).

Outputs full ERP + contract mask + labeled comparison crops vs the shipped v15.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, "/content")
import cv2  # noqa: E402
from PIL import Image  # noqa: E402

import db238_screen as SC  # noqa: E402
import db239_seam_mask as SM  # noqa: E402

LOG = ("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/"
       "00a6ffc1-6ce9-3bc3-a060-6006e9893a1a")
V15 = ("/content/drive/MyDrive/koi_waymo2pano_colab/datasets/av2_1plus92_v15/"
       "train/00a6ffc1_w1")
OUT = ("/content/drive/MyDrive/koi_waymo2pano_colab/results/db239_seam_mask/"
       "broute_demo_a100")
ANCHOR, FRAME = 100, 37
TAU = 16


def rot_sample(cam, pose, imgs, ys, xs):
    """Rotation-only sample: the point at infinity removes translation."""
    X = 1e6 * SC.DIRS[ys, xs]
    px, py, ok = SC._project(pose[cam], X)
    v = np.zeros((len(ys), 3), np.float32)
    v[ok] = SM.bilinear_rgb(imgs[cam], px[ok], py[ok])
    return v, ok


def main():
    os.makedirs(OUT, exist_ok=True)
    man = SC.manifest_from_dir(LOG, ANCHOR, 5)
    cal = SC.load_calibration(LOG)
    imgs = SC.load_images(LOG, man["cam_ts"])
    cte = SM.load_ego_interp(LOG)
    pose = SM.emc_poses(cal, man["cam_ts"], man["anchor_ts"], cte)
    sup = SM.camera_support_emc(pose)
    domain = np.asarray(Image.open(
        "%s/A/masks/mk_%04d.png" % (V15, FRAME)).convert("L")) > 127
    v15 = np.asarray(Image.open(
        "%s/A/frames/fr_%04d.png" % (V15, FRAME)).convert("RGB"))

    # territory: max facing among supporting cameras (EMC rotations)
    face = np.full((SC.H, SC.W), -2.0, np.float32)
    own = np.full((SC.H, SC.W), -1, np.int16)
    for i, cam in enumerate(SC.CAMERAS):
        f = (SC.DIRS_FLAT @ pose[cam]["R"])[:, 2].reshape(SC.H, SC.W)
        u = sup[cam] & (f > face)
        face[u] = f[u]
        own[u] = i

    # 1+2. pure rotational composite inside the shipped band domain
    erp = np.zeros((SC.H, SC.W, 3), np.float32)
    for i, cam in enumerate(SC.CAMERAS):
        m = (own == i) & domain
        if not m.any():
            continue
        ys, xs = np.nonzero(m)
        v, ok = rot_sample(cam, pose, imgs, ys, xs)
        erp[ys[ok], xs[ok]] = v[ok]

    # 3. adaptive parallax mask from cross-camera photometric contradiction
    D = np.zeros((SC.H, SC.W), np.float32)
    OV = np.zeros((SC.H, SC.W), bool)
    pair_stats = {}
    for a, b in SC.ADJACENT:
        m = sup[a] & sup[b] & domain
        if int(m.sum()) < 300:
            continue
        ys, xs = np.nonzero(m)
        va, oka = rot_sample(a, pose, imgs, ys, xs)
        vb, okb = rot_sample(b, pose, imgs, ys, xs)
        ok = oka & okb
        if int(ok.sum()) < 300:
            continue
        d = np.abs(SM.affine_match(va[ok], vb[ok]) - vb[ok]).mean(1)
        yy, xx = ys[ok], xs[ok]
        np.maximum.at(D, (yy, xx), d.astype(np.float32))
        OV[yy, xx] = True
        pair_stats["%s|%s" % (a, b)] = {
            "n": int(ok.sum()), "median": round(float(np.median(d)), 2),
            "p90": round(float(np.percentile(d, 90)), 2)}
    seam = SM.seam_mask(D, OV, TAU)

    out = erp.copy()
    out[seam] = 0
    keep = domain & ~seam & (own >= 0)

    rec = {"anchor": ANCHOR, "frame": FRAME, "tau": TAU,
           "domain_px": int(domain.sum()),
           "masked_px": int((seam & domain).sum()),
           "masked_frac_of_domain": round(float((seam & domain).sum())
                                          / max(int(domain.sum()), 1), 5),
           "keep_frac_of_domain": round(float(keep.sum())
                                        / max(int(domain.sum()), 1), 5),
           "pairs": pair_stats}
    with open(os.path.join(OUT, "broute_a100.json"), "w") as fh:
        json.dump(rec, fh, indent=1)
    print(json.dumps(rec, indent=1), flush=True)

    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(
        os.path.join(OUT, "broute_frame.png"))
    Image.fromarray((keep * 255).astype(np.uint8)).save(
        os.path.join(OUT, "broute_mask.png"))

    # labeled crops: shipped v15 vs B-route
    CROPS = [("truck_wheel", 400, 430, 700, 640, 3),
             ("column", 1360, 430, 1530, 630, 4),
             ("pedestrian_text", 1590, 380, 1790, 570, 4)]
    for name, x0, y0, x1, y1, S in CROPS:
        def T(img, lab, col):
            t = cv2.resize(np.clip(img[y0:y1, x0:x1], 0, 255).astype(np.uint8),
                           ((x1 - x0) * S, (y1 - y0) * S),
                           interpolation=cv2.INTER_NEAREST)
            bar = np.zeros((64, t.shape[1], 3), np.uint8)
            cv2.putText(bar, lab, (12, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        col, 2, cv2.LINE_AA)
            return np.concatenate([bar, t], 0)
        ps = [T(v15.astype(np.float32), "v15 SHIPPED", (120, 120, 255)),
              T(out, "B-route: rotation-only + seam mask", (120, 255, 120))]
        gap = np.full((ps[0].shape[0], 10, 3), (40, 40, 40), np.uint8)
        row = np.concatenate([ps[0], gap, ps[1]], 1)
        Image.fromarray(row).save("/content/zoom/FIG_broute_%s.png" % name)
        print("saved FIG_broute_%s.png" % name, flush=True)

    # full band strip: v15 on top, B-route below
    band = slice(330, 700)
    strip = np.concatenate([v15[band],
                            np.full((6, SC.W, 3), (255, 255, 0), np.uint8),
                            np.clip(out[band], 0, 255).astype(np.uint8)], 0)
    Image.fromarray(strip).save("/content/zoom/FIG_broute_strip.png")
    print("saved FIG_broute_strip.png", flush=True)
    print("DB239_BROUTE_DEMO_DONE", flush=True)


if __name__ == "__main__":
    main()
