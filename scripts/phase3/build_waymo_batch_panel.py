"""Stack our L1+HDR+multiband output for frames 0, 100, 300, 500, 700 in one panel.

Each row labels frame idx + context_name + HDR gain spread (to show that
HDR adapts per-frame). Runs on Colab where the per-frame PNGs live in Drive.

Output: deliverables/xihan/l1_on_waymo/batch_frames_5way.png (+ thumb)
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


BATCH_DIR = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/waymo_e2ed/batch_frames")
F0_OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/waymo_e2ed/l1_waymo_8e7373_hdr_multiband_4096x2048.png")
F0_EXTRACT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/waymo_e2ed/frame0_extracted")
OUT_DIR = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/waymo_e2ed/batch_panel")


def annotate(img, text, h=44):
    H, W = img.shape[:2]
    bar = np.zeros((h, W, 3), dtype=np.uint8)
    cv2.putText(bar, text, (12, h-14), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (255,255,255), 2, cv2.LINE_AA)
    return np.concatenate([bar, img], axis=0)


def fit_w(img, w):
    h = int(img.shape[0] * w / img.shape[1])
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def context_name(extract_dir: Path) -> str:
    meta = json.loads((extract_dir / "frame_meta.json").read_text(encoding="utf-8"))
    return meta.get("context_name", "unknown")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    frames = [
        (0,   F0_OUT, F0_EXTRACT),
        (100, BATCH_DIR / "frame_100_l1_hdr_multiband.png", BATCH_DIR / "frame_100_extracted"),
        (300, BATCH_DIR / "frame_300_l1_hdr_multiband.png", BATCH_DIR / "frame_300_extracted"),
        (500, BATCH_DIR / "frame_500_l1_hdr_multiband.png", BATCH_DIR / "frame_500_extracted"),
        (700, BATCH_DIR / "frame_700_l1_hdr_multiband.png", BATCH_DIR / "frame_700_extracted"),
    ]

    panel_w = 2048
    rows = []
    for idx, png, extract_dir in frames:
        if not png.exists():
            print(f"WARN: {png} missing, skipping frame {idx}")
            continue
        ctx = context_name(extract_dir)
        bgr = cv2.imread(str(png))
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        rgb = fit_w(rgb, panel_w)
        lbl = f"frame {idx:3d}  ctx={ctx}  (L1 + 8-cam L2 HDR + multiband)"
        rows.append(annotate(rgb, lbl, h=40))

    panel = np.concatenate(rows, axis=0)
    cv2.imwrite(str(OUT_DIR / "batch_frames_5way.png"),
                cv2.cvtColor(panel, cv2.COLOR_RGB2BGR))
    print(f"wrote batch_frames_5way.png  ({panel.shape[1]}x{panel.shape[0]})")

    thumb_w = 1200
    th_h = int(panel.shape[0] * thumb_w / panel.shape[1])
    th = cv2.resize(panel, (thumb_w, th_h), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT_DIR / "batch_frames_5way_thumb.png"),
                cv2.cvtColor(th, cv2.COLOR_RGB2BGR))
    print(f"wrote thumb {thumb_w}x{th_h}")


if __name__ == "__main__":
    main()
