"""Build a thumbnail grid of legacy vs Phase C+N2 across 5 val logs."""
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

logs = [
    ("02a00399", "/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c_plus_n2/02a00399/anchor_0"),
    ("0bae3b5e", "/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c_plus_n2/0bae3b5e/anchor_0"),
    ("2c652f9e", "/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c_plus_n2/2c652f9e/anchor_0"),
    ("9f871fb4", "/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c_plus_n2/9f871fb4/anchor_0"),
    ("fbee355f", "/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c_plus_n2/fbee355f/anchor_0"),
]

out_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c_plus_n2/_xlog_grid")
out_dir.mkdir(parents=True, exist_ok=True)


def lbl(arr, text):
    pil = Image.fromarray(arr.copy())
    draw = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", 24)
    except Exception:
        f = ImageFont.load_default()
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((10 + dx, 6 + dy), text, font=f, fill="black")
    draw.text((10, 6), text, font=f, fill="white")
    return np.array(pil)


def thumb(path, size=(900, 450)):
    im = Image.open(path).convert("RGB")
    im.thumbnail(size)
    return np.array(im)


# 5 rows: each log = inf | lidar_graphcut horizontally side-by-side
rows = []
for log_id, base in logs:
    base_p = Path(base)
    inf = thumb(base_p / "l1_inf.png")
    combo = thumb(base_p / "l1_lidar_graphcut.png")
    # Match heights
    h = min(inf.shape[0], combo.shape[0])
    inf = inf[:h]; combo = combo[:h]
    side = np.concatenate([
        lbl(inf, f"{log_id} legacy L1"),
        lbl(combo, f"{log_id} N1+LiDAR+graphcut"),
    ], axis=1)
    rows.append(side)

# Stack
mw = max(r.shape[1] for r in rows)
pad_rows = []
for r in rows:
    if r.shape[1] < mw:
        r = np.concatenate([r, np.zeros((r.shape[0], mw - r.shape[1], 3), dtype=r.dtype)], axis=1)
    pad_rows.append(r)
grid = np.concatenate(pad_rows, axis=0)
Image.fromarray(grid).save(out_dir / "xlog_grid.png")
print(f"grid shape: {grid.shape}")
print(f"saved {out_dir / 'xlog_grid.png'}")
