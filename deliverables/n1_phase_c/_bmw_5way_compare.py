"""BMW tight zoom 5-way compare for final visual gate."""
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

base = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_full_stack/02a00399/anchor_0")
out_dir = base
out_dir.mkdir(parents=True, exist_ok=True)

# BMW crop at 1024x2048 ERP: roughly col 1500-1950, row 470-720
bmw_box = (1500, 470, 1950, 720)

files_labels = [
    ("l1_inf.png", "1. legacy L1"),
    ("l1_hdr.png", "2. +HDR"),
    ("l1_lidar.png", "3. +N1+LiDAR"),
    ("l1_hdr_lidar.png", "4. +HDR+N1"),
    ("l1_hdr_lidar_graphcut.png", "5. FULL +graphcut"),
]


def lbl(arr, text):
    pil = Image.fromarray(arr.copy())
    draw = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except Exception:
        f = ImageFont.load_default()
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((6 + dx, 4 + dy), text, font=f, fill="black")
    draw.text((6, 4), text, font=f, fill="white")
    return np.array(pil)


rows = []
for f, label in files_labels:
    erp = np.asarray(Image.open(base / f).convert("RGB"))
    crop = erp[bmw_box[1]:bmw_box[3], bmw_box[0]:bmw_box[2]]
    rows.append(lbl(crop, label))

mw = max(r.shape[1] for r in rows)
pad = [np.concatenate([r, np.zeros((r.shape[0], mw - r.shape[1], 3), dtype=r.dtype)], axis=1) if r.shape[1] < mw else r for r in rows]
stack = np.concatenate(pad, axis=0)
Image.fromarray(stack).save(out_dir / "bmw_5way_tight.png")
print(f"BMW 5-way: {stack.shape}")

# Also Porsche
porsche_box = (350, 480, 1000, 750)
prows = []
for f, label in files_labels:
    erp = np.asarray(Image.open(base / f).convert("RGB"))
    crop = erp[porsche_box[1]:porsche_box[3], porsche_box[0]:porsche_box[2]]
    prows.append(lbl(crop, label))
mw = max(r.shape[1] for r in prows)
ppad = [np.concatenate([r, np.zeros((r.shape[0], mw - r.shape[1], 3), dtype=r.dtype)], axis=1) if r.shape[1] < mw else r for r in prows]
pstack = np.concatenate(ppad, axis=0)
Image.fromarray(pstack).save(out_dir / "porsche_5way_tight.png")
print(f"Porsche 5-way: {pstack.shape}")
