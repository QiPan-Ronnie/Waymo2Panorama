"""One figure per dataset showing the chain: source cameras -> ERP -> mask.

koi is reviewing how each dataset is CONSTRUCTED, so the figure has to show the
inputs, not only the result: the raw per-camera frames on top, the stitched ERP
band below them, and the mask under that, all from the same timestep.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

A = r"D:\BaiduSyncdisk\2024 to future\koi chen\w2p-db236\agent"
for p in ("db241_multids_production", "db238_screening", "db239_seam_mask",
          "db240_rule_dataset"):
    sys.path.insert(0, os.path.join(A, p))
import db238_screen as SC          # noqa: E402
import db240_rule_dataset as DS    # noqa: E402

W = 2048
BAND = slice(330, 700)
K = 46                              # the timestep to show


def font(sz):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", sz)
    except Exception:
        return ImageFont.load_default()


def build(name, log_dir, sample_dir, out_png):
    import json
    with open(os.path.join(sample_dir, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    cams = DS.present_cameras(log_dir)
    idx = DS.uniform_time_indices(log_dir, cams, man["window"][0], man["frames"])
    if idx is None:
        idx = list(range(man["window"][0], man["window"][0] + man["frames"]))
    m = SC.manifest_from_dir(log_dir, idx[K], 0, cams)

    # ---- row 1: the raw camera frames that feed this timestep
    thumbs = []
    for cam in cams:
        p = os.path.join(log_dir, "sensors", "cameras", cam, "%d.jpg" % m["cam_ts"][cam])
        if not os.path.isfile(p):
            continue
        im = Image.open(p).convert("RGB")
        tw = W // max(len(cams), 1) - 6
        th = int(im.height * tw / im.width)
        thumbs.append((cam, im.resize((tw, th), Image.BILINEAR)))
    row1_h = max(t.height for _, t in thumbs) if thumbs else 200

    fr = np.asarray(Image.open(os.path.join(sample_dir, "frames",
                                            "fr_%04d.png" % K)).convert("RGB"))
    mk = np.asarray(Image.open(os.path.join(sample_dir, "masks",
                                            "mk_%04d.png" % K)).convert("L"))
    band_h = BAND.stop - BAND.start
    HDR, GAP = 46, 34
    total = HDR + row1_h + GAP + band_h + GAP + band_h + 24
    c = Image.new("RGB", (W, total), (14, 16, 20))
    d = ImageDraw.Draw(c)
    f, fs = font(26), font(19)

    d.text((14, 8), name, font=f, fill=(235, 238, 242))
    y = HDR
    x = 3
    for cam, t in thumbs:
        c.paste(t, (x, y))
        d.text((x + 4, y + 3), cam.replace("ring_", ""), font=fs, fill=(255, 230, 120))
        x += t.width + 6
    d.text((14, y + row1_h + 6),
           "^ %d 路原始相机图（这一帧实际用到的）" % len(thumbs), font=fs,
           fill=(180, 200, 220))

    y += row1_h + GAP
    c.paste(Image.fromarray(fr[BAND]), (0, y))
    d.text((14, y + 4), "成品 ERP 第 %d 帧 —— 接缝已按规则涂黑" % K, font=fs,
           fill=(255, 255, 255))

    y += band_h + GAP
    rgb = np.repeat(mk[BAND][:, :, None], 3, 2)
    c.paste(Image.fromarray(rgb), (0, y))
    d.text((14, y + 4),
           "对应 mask：白 = 严格真实像素（算 loss）  黑 = 无可信像素（交生成模型）",
           font=fs, fill=(255, 255, 255))
    c.save(out_png)
    return out_png


if __name__ == "__main__":
    name, log_dir, sample_dir, out_png = sys.argv[1:5]
    print(build(name, log_dir, sample_dir, out_png))
