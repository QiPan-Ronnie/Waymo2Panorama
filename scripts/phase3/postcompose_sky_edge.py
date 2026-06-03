from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def _border_connected(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    ff = mask.astype(np.uint8).copy()
    fmask = np.zeros((h + 2, w + 2), np.uint8)
    seeds = [(0, 0), (0, w - 1), (0, w // 2), (h // 2, 0), (h // 2, w - 1)]
    for sy, sx in seeds:
        if ff[sy, sx] == 1:
            cv2.floodFill(ff, fmask, (sx, sy), 2)
    return ff == 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--threshold", type=int, action="append", default=[18, 30, 45])
    args = ap.parse_args()

    init = cv2.imread(args.init, cv2.IMREAD_COLOR)
    raw = cv2.imread(args.raw, cv2.IMREAD_COLOR)
    if init is None:
        raise FileNotFoundError(args.init)
    if raw is None:
        raise FileNotFoundError(args.raw)
    if init.shape != raw.shape:
        raise ValueError(f"shape mismatch init={init.shape} raw={raw.shape}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    h, w = init.shape[:2]
    rows = np.arange(h)[:, None] * np.ones((1, w))

    for thr in args.threshold:
        low = init.max(axis=2) < thr
        sky_edge = _border_connected(low) & (rows < h * 0.52)
        out = init.copy()
        out[sky_edge] = raw[sky_edge]
        stem = f"sky_edge_thr{thr:02d}"
        cv2.imwrite(str(out_dir / f"{stem}.png"), out)
        pv = init.copy().astype(np.float32)
        red = np.zeros_like(pv)
        red[..., 2] = 255
        pv[sky_edge] = 0.55 * pv[sky_edge] + 0.45 * red[sky_edge]
        cv2.imwrite(str(out_dir / f"{stem}_mask_preview.jpg"), np.clip(pv, 0, 255).astype(np.uint8))
        print(f"{stem}: replace={100.0 * sky_edge.mean():.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
