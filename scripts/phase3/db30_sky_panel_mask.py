"""DB-30: prepare a sky-only harmonization mask.

Mask convention matches run_dit360_trimap_clamp.py:
  white/255 = preserve source
  black/0   = generate

The target is the upper border-connected black band plus sky-like pixels in the
captured upper panorama. It intentionally avoids dark objects, buildings, road,
and vehicles by using a conservative HSV/blue-sky test plus morphology.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def border_connected(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    ff = mask.astype(np.uint8).copy()
    fmask = np.zeros((h + 2, w + 2), np.uint8)
    seeds = [(0, 0), (0, w - 1), (0, w // 2), (0, w // 4), (0, 3 * w // 4), (h // 2, 0), (h // 2, w - 1)]
    for sy, sx in seeds:
        if ff[sy, sx] == 1:
            cv2.floodFill(ff, fmask, (sx, sy), 2)
    return ff == 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("init")
    ap.add_argument("out_dir")
    ap.add_argument("--horizon-frac", type=float, default=0.54)
    ap.add_argument("--min-area", type=int, default=80)
    ap.add_argument("--dilate", type=int, default=5)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bgr = cv2.imread(args.init, cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(args.init)
    h, w = bgr.shape[:2]
    rows = np.arange(h)[:, None] * np.ones((1, w))
    upper = rows < h * args.horizon_frac

    content = (bgr.sum(2) > 12).astype(np.uint8)
    content = cv2.morphologyEx(content, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
    outer_black = border_connected((content == 0)) & upper

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue = hsv[..., 0].astype(np.int16)
    sat = hsv[..., 1].astype(np.int16)
    val = hsv[..., 2].astype(np.int16)
    b = bgr[..., 0].astype(np.int16)
    g = bgr[..., 1].astype(np.int16)
    r = bgr[..., 2].astype(np.int16)

    blue_sky = (
        upper
        & (val > 95)
        & (sat > 22)
        & (hue >= 82)
        & (hue <= 128)
        & (b > r + 8)
        & (b >= g - 18)
    )
    # Include bright low-saturation cloud pixels only when adjacent to blue sky.
    cloud_like = upper & (val > 178) & (sat < 80) & (b > 130) & (g > 130) & (r > 120)
    sky_seed = blue_sky | outer_black
    if args.dilate > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (args.dilate * 2 + 1, args.dilate * 2 + 1))
        near_sky = cv2.dilate(sky_seed.astype(np.uint8), k).astype(bool)
        sky_seed |= cloud_like & near_sky

    # Clean tiny speckles; do not bridge aggressively across buildings/trees.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(sky_seed.astype(np.uint8), connectivity=8)
    sky = np.zeros((h, w), bool)
    for idx in range(1, n):
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area >= args.min_area:
            sky |= labels == idx
    sky |= outer_black
    sky &= upper

    preserve = ~sky
    mask = np.where(preserve, 255, 0).astype(np.uint8)
    cv2.imwrite(str(out_dir / "opmask_sky_panel.png"), mask)

    preview = bgr.copy().astype(np.float32)
    red = np.zeros_like(preview)
    red[..., 2] = 255
    preview[sky] = 0.45 * preview[sky] + 0.55 * red[sky]
    cv2.imwrite(str(out_dir / "opmask_sky_panel_preview.jpg"), np.clip(preview, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 92])

    sky_only = np.zeros_like(bgr)
    sky_only[sky] = bgr[sky]
    cv2.imwrite(str(out_dir / "opmask_sky_panel_source_pixels.jpg"), sky_only, [cv2.IMWRITE_JPEG_QUALITY, 92])

    print(
        {
            "init": args.init,
            "generate_fraction": float(sky.mean()),
            "outer_black_fraction": float(outer_black.mean()),
            "blue_sky_fraction": float(blue_sky.mean()),
            "cloud_like_fraction": float((cloud_like & sky).mean()),
            "horizon_frac": args.horizon_frac,
            "mask": str(out_dir / "opmask_sky_panel.png"),
        },
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
