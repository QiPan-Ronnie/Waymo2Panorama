import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

base = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_full_stack/02a00399/anchor_0")

files_labels = [
    ("l1_inf_thumb.png", "1. legacy L1 (baseline)"),
    ("l1_hdr_thumb.png", "2. + HDR only (shipped 新-E)"),
    ("l1_lidar_thumb.png", "3. + N1+LiDAR (Phase C)"),
    ("l1_hdr_lidar_thumb.png", "4. + HDR + N1+LiDAR"),
    ("l1_hdr_lidar_graphcut_thumb.png", "5. FULL STACK: HDR + N1+LiDAR + graphcut"),
]


def lbl(arr, text):
    pil = Image.fromarray(arr.copy())
    draw = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", 24)
    except Exception:
        f = ImageFont.load_default()
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((10 + dx, 8 + dy), text, font=f, fill="black")
    draw.text((10, 8), text, font=f, fill="white")
    return np.array(pil)


rows = []
for f, label in files_labels:
    img = np.asarray(Image.open(base / f).convert("RGB"))
    rows.append(lbl(img, label))

mw = max(r.shape[1] for r in rows)
pad = []
for r in rows:
    if r.shape[1] < mw:
        r = np.concatenate([r, np.zeros((r.shape[0], mw - r.shape[1], 3), dtype=r.dtype)], axis=1)
    pad.append(r)
stack = np.concatenate(pad, axis=0)
Image.fromarray(stack).save(base / "full_stack_5way.png")
print(f"stack shape: {stack.shape}")

# Also make a thumb < 1 MB
im = Image.open(base / "full_stack_5way.png").copy()
im.thumbnail((1024, 3000))
im.save(base / "full_stack_5way_thumb.png")
print(f"thumb size: {im.size}")
