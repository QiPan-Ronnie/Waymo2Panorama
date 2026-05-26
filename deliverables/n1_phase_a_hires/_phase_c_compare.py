"""Phase C visual A/B: legacy L1 (l1_inf.png) vs N1 LiDAR (l1_lidar.png)."""
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

in_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c/02a00399/anchor_0")
out_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c/02a00399/anchor_0_panels")
out_dir.mkdir(parents=True, exist_ok=True)

inf = np.asarray(Image.open(in_dir / "l1_inf.png").convert("RGB"))
lidar = np.asarray(Image.open(in_dir / "l1_lidar.png").convert("RGB"))
print(f"inf shape={inf.shape}, lidar shape={lidar.shape}")

# Wide crops (col 500-2100, row 950-1500) covering Porsche area
porsche_box = (500, 950, 2100, 1500)
bmw_box = (2900, 900, 3900, 1500)


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


def amp_diff(a, b, amp=4.0):
    d = np.abs(a.astype(np.float32) - b.astype(np.float32)) * amp
    return np.clip(d, 0, 255).astype(np.uint8)


def stack(rows):
    mw = max(r.shape[1] for r in rows)
    pad = []
    for r in rows:
        if r.shape[1] < mw:
            r = np.concatenate([r, np.zeros((r.shape[0], mw - r.shape[1], 3), dtype=r.dtype)], axis=1)
        pad.append(r)
    return np.concatenate(pad, axis=0)


for name, box in [("porsche", porsche_box), ("bmw", bmw_box)]:
    inf_crop = inf[box[1]:box[3], box[0]:box[2]]
    lidar_crop = lidar[box[1]:box[3], box[0]:box[2]]
    diff_crop = amp_diff(inf_crop, lidar_crop, amp=4.0)
    rows = [
        lbl(inf_crop, "legacy L1 (inf): BASELINE with ghost"),
        lbl(lidar_crop, "N1 + LiDAR per-pixel r"),
        lbl(diff_crop, "diff (amp x4) — bright = differs"),
    ]
    panel = stack(rows)
    out = out_dir / f"{name}_phase_c_compare.png"
    Image.fromarray(panel).save(out)
    # Also pixel-diff metric
    d = np.abs(inf_crop.astype(np.int32) - lidar_crop.astype(np.int32)).sum(axis=-1)
    metric = {
        "name": name,
        "box": list(box),
        "max_diff": int(d.max()),
        "mean_diff": float(d.mean()),
        "frac_changed_gt30": float((d > 30).mean()),
        "frac_changed_gt100": float((d > 100).mean()),
        "panel_shape": list(panel.shape),
    }
    print(json.dumps(metric, indent=2))

# Downsample full ERPs for navigation
for fn in ("l1_inf.png", "l1_lidar.png"):
    im = Image.open(in_dir / fn).copy()
    im.thumbnail((1024, 512))
    im.save(out_dir / fn.replace(".png", "_thumb.png"))
print("thumbnails done")
