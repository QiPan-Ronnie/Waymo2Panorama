import json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

in_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_a/02a00399/anchor_0_hires")
out_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_a/02a00399/anchor_0_tight_wheel")
out_dir.mkdir(parents=True, exist_ok=True)

with open(in_dir/"summary.json") as f:
    summary = json.load(f)

# Tight crop on BMW rear wheel area: col 3400-3700, row 1150-1350 (300 wide x 200 tall)
wheel_box = (3400, 1150, 3700, 1350)
# Also crop where Porsche likely is — col 800-1100, row 1050-1300
porsche_wheel_box = (800, 1050, 1100, 1300)

def lbl(arr, text):
    pil = Image.fromarray(arr)
    draw = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except Exception:
        f = ImageFont.load_default()
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((8 + dx, 6 + dy), text, font=f, fill="black")
    draw.text((8, 6), text, font=f, fill="white")
    return np.array(pil)

def stack_rows(rows):
    mw = max(r.shape[1] for r in rows)
    pad = []
    for r in rows:
        if r.shape[1] < mw:
            r = np.concatenate([r, np.zeros((r.shape[0], mw - r.shape[1], 3), dtype=r.dtype)], axis=1)
        pad.append(r)
    return np.concatenate(pad, axis=0)

# Only consider rows where r is None, 3, 5, 7, 10 — the most informative
target_labels = {"inf", "r3m", "r5m", "r7m", "r10m"}

bmw_rows = []
porsche_rows = []
ref_bmw = None
ref_porsche = None
for entry in summary["results"]:
    if entry["label"] not in target_labels:
        continue
    label_str = entry["label"] + " (r=" + str(entry["r"]) + ")"
    erp = np.asarray(Image.open(in_dir / entry["out_file"]).convert("RGB"))
    bmw_crop = erp[wheel_box[1]:wheel_box[3], wheel_box[0]:wheel_box[2]]
    p_crop = erp[porsche_wheel_box[1]:porsche_wheel_box[3], porsche_wheel_box[0]:porsche_wheel_box[2]]
    if entry["label"] == "inf":
        ref_bmw = bmw_crop.copy()
        ref_porsche = p_crop.copy()
    bmw_rows.append(lbl(bmw_crop, label_str))
    porsche_rows.append(lbl(p_crop, label_str))

# Compute ghost-bbox metrics: max diff vs inf in the bbox
def metric(img, ref):
    if img.shape != ref.shape:
        return {"shape_mismatch": True}
    d = np.abs(img.astype(np.int32) - ref.astype(np.int32)).sum(axis=-1)  # H,W
    return {
        "max_diff": int(d.max()),
        "mean_diff": float(d.mean()),
        "frac_changed_gt30": float((d > 30).mean()),
        "frac_changed_gt100": float((d > 100).mean()),
    }

metrics = {"bmw": [], "porsche": []}
for entry in summary["results"]:
    if entry["label"] not in target_labels:
        continue
    erp = np.asarray(Image.open(in_dir / entry["out_file"]).convert("RGB"))
    bmw_crop = erp[wheel_box[1]:wheel_box[3], wheel_box[0]:wheel_box[2]]
    p_crop = erp[porsche_wheel_box[1]:porsche_wheel_box[3], porsche_wheel_box[0]:porsche_wheel_box[2]]
    m_bmw = metric(bmw_crop, ref_bmw)
    m_p = metric(p_crop, ref_porsche)
    m_bmw["label"] = entry["label"]
    m_p["label"] = entry["label"]
    metrics["bmw"].append(m_bmw)
    metrics["porsche"].append(m_p)

Image.fromarray(stack_rows(bmw_rows)).save(out_dir / "bmw_wheel_tight.png")
Image.fromarray(stack_rows(porsche_rows)).save(out_dir / "porsche_wheel_tight.png")

with open(out_dir / "wheel_metrics.json", "w") as f:
    json.dump(metrics, f, indent=2)

print("bmw_wheel_tight:", stack_rows(bmw_rows).shape)
print("porsche_wheel_tight:", stack_rows(porsche_rows).shape)
print(json.dumps(metrics, indent=2))
