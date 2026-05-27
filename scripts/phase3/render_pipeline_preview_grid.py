"""
Render a Bosch preview grid comparing multiband vs hard_hdr_of panoramas
across all 5 val logs.

For each log, pick N representative anchors (default 3: first, middle, last
of available pre-rendered anchors). Stack them with method labels.

Inputs:
    --hard-hdr-of-root    /content/.../full_pipeline_v1
    --multiband-root      /content/.../multiband_baseline (optional; if missing,
                          renders multiband on the fly via stitch_one_frame)
    --output-png          path to grid PNG

Usage:
    python scripts/phase3/render_pipeline_preview_grid.py \
        --hard-hdr-of-root /content/.../full_pipeline_v1 \
        --multiband-root  /content/.../multiband_baseline \
        --output-png       /content/.../bosch_preview_v1.png \
        --n-per-log 3 --tile-h 320
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def label(arr: np.ndarray, text: str, fontsize: int = 18) -> np.ndarray:
    pil = Image.fromarray(arr.copy()); draw = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", fontsize)
    except Exception:
        f = ImageFont.load_default()
    for dx, dy in [(-2,0),(2,0),(0,-2),(0,2)]:
        draw.text((6+dx, 4+dy), text, font=f, fill="black")
    draw.text((6, 4), text, font=f, fill="white")
    return np.array(pil)


def downsize(im: Image.Image, h: int) -> Image.Image:
    w = int(im.size[0] * h / im.size[1])
    return im.resize((w, h), Image.LANCZOS)


def find_log_dirs(root: Path) -> dict[str, Path]:
    """Find log subdirs (8-char names) under root."""
    if not root.exists():
        return {}
    return {p.name: p for p in sorted(root.iterdir()) if p.is_dir() and len(p.name) == 8}


def find_pngs(log_dir: Path) -> list[Path]:
    """Return sorted list of anchor_*.png in log_dir."""
    return sorted(log_dir.glob("anchor_*.png"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hard-hdr-of-root", type=Path, required=True)
    ap.add_argument("--multiband-root", type=Path, default=None,
                    help="optional; if not given, only hard_hdr_of grid is rendered")
    ap.add_argument("--output-png", type=Path, required=True)
    ap.add_argument("--n-per-log", type=int, default=3)
    ap.add_argument("--tile-h", type=int, default=320,
                    help="height per ERP tile in the grid")
    args = ap.parse_args()

    args.output_png.parent.mkdir(parents=True, exist_ok=True)

    hho_logs = find_log_dirs(args.hard_hdr_of_root)
    if not hho_logs:
        print(f"NO log dirs under {args.hard_hdr_of_root}")
        return 1

    mb_logs = find_log_dirs(args.multiband_root) if args.multiband_root else {}

    rows: list[np.ndarray] = []
    for log_short in sorted(hho_logs.keys()):
        hho_pngs = find_pngs(hho_logs[log_short])
        if not hho_pngs:
            continue
        # Pick N evenly spaced
        n = min(args.n_per_log, len(hho_pngs))
        if n == 1:
            indices = [len(hho_pngs)//2]
        else:
            indices = [int(round(i * (len(hho_pngs)-1) / (n-1))) for i in range(n)]
        for ai, idx in enumerate(indices):
            hho_png = hho_pngs[idx]
            anchor_name = hho_png.stem.replace("anchor_", "")

            tiles_this_row: list[np.ndarray] = []

            # If multiband available, find matching anchor PNG
            if mb_logs and log_short in mb_logs:
                mb_path = mb_logs[log_short] / hho_png.name
                if mb_path.exists():
                    mb_im = downsize(Image.open(mb_path), args.tile_h)
                    mb_arr = label(np.array(mb_im), f"{log_short} a{anchor_name} MULTIBAND")
                    tiles_this_row.append(mb_arr)

            hho_im = downsize(Image.open(hho_png), args.tile_h)
            hho_arr = label(np.array(hho_im), f"{log_short} a{anchor_name} HARD+HDR+OF")
            tiles_this_row.append(hho_arr)

            # Concatenate horizontally
            max_h = max(t.shape[0] for t in tiles_this_row)
            padded = []
            for t in tiles_this_row:
                if t.shape[0] < max_h:
                    t = np.concatenate([t, np.zeros((max_h - t.shape[0], t.shape[1], 3), dtype=t.dtype)], axis=0)
                padded.append(t)
            row = np.concatenate(padded, axis=1)
            rows.append(row)

    if not rows:
        print("no rows rendered")
        return 1

    # Pad rows to same width
    max_w = max(r.shape[1] for r in rows)
    padded_rows = []
    for r in rows:
        if r.shape[1] < max_w:
            r = np.concatenate([r, np.zeros((r.shape[0], max_w - r.shape[1], 3), dtype=r.dtype)], axis=1)
        padded_rows.append(r)
    grid = np.concatenate(padded_rows, axis=0)
    Image.fromarray(grid).save(args.output_png)
    print(f"grid {grid.shape} -> {args.output_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
