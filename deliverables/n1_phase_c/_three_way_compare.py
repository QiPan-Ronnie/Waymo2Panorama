"""Compare legacy L1 vs N1+LiDAR vs N1+LiDAR+graphcut hard-seam on BMW + Porsche."""
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

in_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c_plus_n2/02a00399/anchor_0")
out_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c_plus_n2/02a00399/anchor_0_panels")
out_dir.mkdir(parents=True, exist_ok=True)

inf = np.asarray(Image.open(in_dir / "l1_inf.png").convert("RGB"))
lidar = np.asarray(Image.open(in_dir / "l1_lidar.png").convert("RGB"))
combo = np.asarray(Image.open(in_dir / "l1_lidar_graphcut.png").convert("RGB"))
print(f"shapes: inf={inf.shape}, lidar={lidar.shape}, combo={combo.shape}")

# Coords scaled to 1024x2048
# BMW is on the right side around col 1750/2048 row 450-650 (1024 high)
# Porsche on left around col 750/2048 row 500-650
porsche_box = (300, 480, 1050, 750)   # wider crop
bmw_box = (1450, 450, 1950, 750)


def lbl(arr, text):
    pil = Image.fromarray(arr.copy())
    draw = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except Exception:
        f = ImageFont.load_default()
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((10 + dx, 6 + dy), text, font=f, fill="black")
    draw.text((10, 6), text, font=f, fill="white")
    return np.array(pil)


def stack(rows):
    mw = max(r.shape[1] for r in rows)
    pad = []
    for r in rows:
        if r.shape[1] < mw:
            r = np.concatenate([r, np.zeros((r.shape[0], mw - r.shape[1], 3), dtype=r.dtype)], axis=1)
        pad.append(r)
    return np.concatenate(pad, axis=0)


metrics = {}
for name, box in [("porsche", porsche_box), ("bmw", bmw_box)]:
    inf_c = inf[box[1]:box[3], box[0]:box[2]]
    lidar_c = lidar[box[1]:box[3], box[0]:box[2]]
    combo_c = combo[box[1]:box[3], box[0]:box[2]]
    rows = [
        lbl(inf_c, "1. legacy L1 (BASELINE with ghost)"),
        lbl(lidar_c, "2. N1+LiDAR (Phase C: per-pixel depth)"),
        lbl(combo_c, "3. N1+LiDAR + graphcut (Phase C+N2: hard seam)"),
    ]
    panel = stack(rows)
    out = out_dir / f"{name}_three_way.png"
    Image.fromarray(panel).save(out)
    # Metric: combo vs inf
    d = np.abs(combo_c.astype(np.int32) - inf_c.astype(np.int32)).sum(axis=-1)
    metrics[name] = {
        "combo_vs_inf_mean_diff": float(d.mean()),
        "combo_vs_inf_max_diff": int(d.max()),
        "combo_vs_inf_pct_changed_gt100": float((d > 100).mean()),
    }
    d_lc = np.abs(combo_c.astype(np.int32) - lidar_c.astype(np.int32)).sum(axis=-1)
    metrics[name]["combo_vs_lidar_mean_diff"] = float(d_lc.mean())

# Also full-ERP thumbnails for nav
for fn in ("l1_inf.png", "l1_lidar.png", "l1_lidar_graphcut.png"):
    im = Image.open(in_dir / fn).copy()
    im.thumbnail((1024, 512))
    im.save(out_dir / fn.replace(".png", "_thumb.png"))

# Also seam_overlay thumbnail
if (in_dir / "seam_overlay.png").exists():
    im = Image.open(in_dir / "seam_overlay.png").copy()
    im.thumbnail((1024, 512))
    im.save(out_dir / "seam_overlay_thumb.png")

print(json.dumps(metrics, indent=2))
print("done")
