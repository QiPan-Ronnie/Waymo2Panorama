"""
Render N sample ghost-free anchors (per YOLO v2 strict subset) as a single
preview grid for Bosch deliverable preview.

Usage:
    python scripts/phase3/render_clean_subset_preview.py \\
        --strict-list /path/to/strict_clean_anchors.json \\
        --av2-root /content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val \\
        --output /path/to/clean_grid_preview.png \\
        --n 12 --erp-h 256 --erp-w 512
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_W2P_CODE_REL = "../../code"

# Maps short log_id (8 chars) to full log dir name (truncated suffix)
LOG_FULL_MAP = {
    "02a00399": "02a00399-3857-444e-8db3-a8f58489c394",
    "0bae3b5e": "0bae3b5e-417d-3b03-abaa-806b433233b8",
    "2c652f9e": "2c652f9e-8db8-3572-aa49-fae1344a875b",
    "9f871fb4": "9f871fb4-3b8e-34b3-9161-ed961e71a6da",
    "fbee355f": "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict-list", type=Path, required=True)
    ap.add_argument("--av2-root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--erp-h", type=int, default=256)
    ap.add_argument("--erp-w", type=int, default=512)
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent / DEFAULT_W2P_CODE_REL)
    args = ap.parse_args()

    sys.path.insert(0, str(args.w2p_code))
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.multiband import multiband_blend

    with open(args.strict_list) as f:
        entries = json.load(f)
    if not entries:
        print(f"No entries in {args.strict_list}!")
        return 1
    print(f"loaded {len(entries)} strict-clean entries")

    # Pick N spread across logs
    by_log = {}
    for e in entries:
        by_log.setdefault(e["log_id"], []).append(e)
    n_per_log = max(1, args.n // max(len(by_log), 1))
    picks = []
    for log_id, lst in by_log.items():
        for e in lst[:n_per_log]:
            picks.append(e)
            if len(picks) >= args.n:
                break
        if len(picks) >= args.n:
            break

    print(f"picked {len(picks)} samples")

    erp_hw = (args.erp_h, args.erp_w)
    loaders = {}
    tiles = []

    def lbl(arr, text):
        pil = Image.fromarray(arr.copy())
        draw = ImageDraw.Draw(pil)
        try:
            f = ImageFont.truetype("DejaVuSans-Bold.ttf", 14)
        except Exception:
            f = ImageFont.load_default()
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            draw.text((4 + dx, 2 + dy), text, font=f, fill="black")
        draw.text((4, 2), text, font=f, fill="white")
        return np.array(pil)

    for e in picks:
        log_id = e["log_id"]
        if log_id not in loaders:
            full_name = LOG_FULL_MAP.get(log_id)
            if full_name is None:
                print(f"WARN: unknown log_id {log_id}, skip")
                continue
            loaders[log_id] = AV2RingLoader(args.av2_root / full_name)
        loader = loaders[log_id]
        ts_all = loader.anchor_timestamps_ns()
        anchor_idx = e["anchor_index"]
        if anchor_idx >= len(ts_all):
            print(f"WARN: anchor {anchor_idx} >= {len(ts_all)} for {log_id}, skip")
            continue
        frame = loader.load_synced_frame(ts_all[anchor_idx])
        slabs, weights = [], []
        for cam in RING_CAMS_7:
            calib = frame.calibrations[cam]
            rgb, _, w = render_camera_to_erp(
                image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
                erp_hw=erp_hw, convergence_distance_m=None,
            )
            slabs.append(rgb); weights.append(w)
        erp = multiband_blend(slabs, weights, num_bands=5, wrap=True)
        tile = lbl(erp, f"{log_id} a{anchor_idx:03d}")
        tiles.append(tile)

    if not tiles:
        print("no tiles rendered!")
        return 1

    # Grid layout
    cols = args.cols
    rows_of_tiles = []
    for i in range(0, len(tiles), cols):
        row = tiles[i:i + cols]
        max_h = max(t.shape[0] for t in row)
        padded = []
        for t in row:
            if t.shape[0] < max_h:
                t = np.concatenate([t, np.zeros((max_h - t.shape[0], t.shape[1], 3), dtype=t.dtype)], axis=0)
            padded.append(t)
        rows_of_tiles.append(np.concatenate(padded, axis=1))
    max_w = max(r.shape[1] for r in rows_of_tiles)
    final_rows = []
    for r in rows_of_tiles:
        if r.shape[1] < max_w:
            r = np.concatenate([r, np.zeros((r.shape[0], max_w - r.shape[1], 3), dtype=r.dtype)], axis=1)
        final_rows.append(r)
    grid = np.concatenate(final_rows, axis=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(grid).save(args.output)
    print(f"grid {grid.shape} -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
