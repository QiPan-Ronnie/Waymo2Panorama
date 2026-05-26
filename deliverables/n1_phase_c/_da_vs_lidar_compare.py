"""Compare legacy vs N1+LiDAR vs N1+DA on BMW close-up."""
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

# Sources
legacy = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_d_da/02a00399/anchor_0/l1_inf.png")
da = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_d_da/02a00399/anchor_0/l1_da_depth.png")
lidar = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c_plus_n2/02a00399/anchor_0/l1_lidar.png")
out_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_d_da/02a00399/anchor_0")

inf_arr = np.asarray(Image.open(legacy).convert("RGB"))
da_arr = np.asarray(Image.open(da).convert("RGB"))
lidar_arr = np.asarray(Image.open(lidar).convert("RGB"))
print(f"shapes: inf={inf_arr.shape}, da={da_arr.shape}, lidar={lidar_arr.shape}")

bmw_box = (1500, 470, 1950, 720)
porsche_box = (350, 480, 1000, 750)


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


def stack(rows):
    mw = max(r.shape[1] for r in rows)
    pad = []
    for r in rows:
        if r.shape[1] < mw:
            r = np.concatenate([r, np.zeros((r.shape[0], mw - r.shape[1], 3), dtype=r.dtype)], axis=1)
        pad.append(r)
    return np.concatenate(pad, axis=0)


for name, box in [("bmw", bmw_box), ("porsche", porsche_box)]:
    inf_c = inf_arr[box[1]:box[3], box[0]:box[2]]
    da_c = da_arr[box[1]:box[3], box[0]:box[2]]
    lidar_c = lidar_arr[box[1]:box[3], box[0]:box[2]]

    rows = [
        lbl(inf_c, "1. legacy L1 (baseline)"),
        lbl(da_c, "2. N1 + Depth Anything V2 (dense)"),
        lbl(lidar_c, "3. N1 + LiDAR (sparse, kNN-fill)"),
    ]
    panel = stack(rows)
    Image.fromarray(panel).save(out_dir / f"{name}_da_vs_lidar.png")

    # Metric: DA vs inf, LiDAR vs inf
    def diff_metric(a, b):
        d = np.abs(a.astype(np.int32) - b.astype(np.int32)).sum(axis=-1)
        return {
            "mean": float(d.mean()),
            "max": int(d.max()),
            "frac_gt100": float((d > 100).mean()),
        }

    print(f"\n{name}:")
    print(f"  DA vs inf:    {diff_metric(da_c, inf_c)}")
    print(f"  LiDAR vs inf: {diff_metric(lidar_c, inf_c)}")
    print(f"  DA vs LiDAR:  {diff_metric(da_c, lidar_c)}")
