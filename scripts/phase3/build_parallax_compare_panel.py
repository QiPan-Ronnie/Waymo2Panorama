"""Build side-by-side comparison panel for parallax-fix approaches.

For one anchor, stacks: plain L1 / A2 / B1 / (C1) / (hybrid) ERPs vertically
with labels. Used for visual verification of which approach best eliminates
white traces / 2-wheel ghost.

Usage:
    python scripts/phase3/build_parallax_compare_panel.py \
        --anchor 60 \
        --plain-l1 outputs/phase3/p3.X_parallax/anchor_060_a2_plainL1/l1_sparse_disp.png \
        --a2 outputs/phase3/p3.X_parallax/anchor_060_a2/l1_sparse_disp.png \
        --b1 outputs/phase3/p3.X_parallax/anchor_060_b1/l1_graphcut_disp.png \
        --output-png outputs/phase3/p3.X_parallax/compare_anchor_060.png
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor", type=int, required=True)
    ap.add_argument("--plain-l1", type=Path, required=True)
    ap.add_argument("--a2", type=Path, default=None)
    ap.add_argument("--b1", type=Path, default=None)
    ap.add_argument("--c1", type=Path, default=None)
    ap.add_argument("--hybrid", type=Path, default=None)
    ap.add_argument("--output-png", type=Path, required=True)
    ap.add_argument("--max-display-h", type=int, default=512,
                    help="Resize each row to this max height for compact panel.")
    args = ap.parse_args()

    rows: list[tuple[str, np.ndarray]] = []
    for label, path in [
        ("plain L1 (baseline)", args.plain_l1),
        ("A2 — sparse stereo displacement", args.a2),
        ("B1 — disparity-aware graphcut seam", args.b1),
        ("C1 — RAFT optical flow", args.c1),
        ("HYBRID", args.hybrid),
    ]:
        if path is None: continue
        if not path.exists():
            print(f"[warn] missing: {path}, skipping {label}")
            continue
        img = np.asarray(Image.open(path).convert("RGB"))
        # Resize to compact
        H, W = img.shape[:2]
        scale = args.max_display_h / H
        new_W = int(W * scale)
        img_small = cv2.resize(img, (new_W, args.max_display_h),
                                interpolation=cv2.INTER_AREA)
        rows.append((label, img_small))

    if not rows:
        print("[error] no input images found")
        return 1

    label_h = 30
    row_w = rows[0][1].shape[1]
    panel = np.zeros(
        (sum(r[1].shape[0] + label_h for r in rows), row_w, 3),
        dtype=np.uint8,
    )
    y = 0
    for label, img in rows:
        cv2.rectangle(panel, (0, y), (row_w, y + label_h), (40, 40, 40), -1)
        cv2.putText(panel, f"anchor {args.anchor}: {label}", (8, y + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        y += label_h
        panel[y:y + img.shape[0], :img.shape[1]] = img
        y += img.shape[0]

    args.output_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(panel).save(args.output_png)
    print(f"wrote {args.output_png} ({panel.shape[1]}x{panel.shape[0]}), {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
