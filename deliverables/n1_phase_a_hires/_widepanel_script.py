import json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

in_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_a/02a00399/anchor_0_hires")
out_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_a/02a00399/anchor_0_widepanel")
out_dir.mkdir(parents=True, exist_ok=True)

with open(in_dir/"summary.json") as f:
    summary = json.load(f)

porsche_box = (600, 950, 2000, 1400)
bmw_box = (2900, 900, 3900, 1400)

def lbl(arr, text):
    pil = Image.fromarray(arr)
    draw = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", 32)
    except Exception:
        f = ImageFont.load_default()
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((10 + dx, 8 + dy), text, font=f, fill="black")
    draw.text((10, 8), text, font=f, fill="white")
    return np.array(pil)

porsche_rows = []
bmw_rows = []
for entry in summary["results"]:
    label_str = entry["label"] + " (r=" + str(entry["r"]) + ")"
    erp = np.asarray(Image.open(in_dir / entry["out_file"]).convert("RGB"))
    p = erp[porsche_box[1]:porsche_box[3], porsche_box[0]:porsche_box[2]]
    b = erp[bmw_box[1]:bmw_box[3], bmw_box[0]:bmw_box[2]]
    porsche_rows.append(lbl(p, label_str))
    bmw_rows.append(lbl(b, label_str))

def stk(rows):
    mw = max(r.shape[1] for r in rows)
    pad = []
    for r in rows:
        if r.shape[1] < mw:
            r = np.concatenate([r, np.zeros((r.shape[0], mw - r.shape[1], 3), dtype=r.dtype)], axis=1)
        pad.append(r)
    return np.concatenate(pad, axis=0)

p_stack = stk(porsche_rows)
b_stack = stk(bmw_rows)
Image.fromarray(p_stack).save(out_dir / "porsche_wide.png")
Image.fromarray(b_stack).save(out_dir / "bmw_wide.png")
print("porsche_wide:", p_stack.shape)
print("bmw_wide:", b_stack.shape)
