#!/usr/bin/env python
"""Build a DB40 A1 mask that keeps only long_source seam components."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment"
MASK_DIR = OUT_DIR / "masks"

A1_INPUT = OUT_DIR / "A1_view_none_bmw_1024x2048.png"
BASE_KEEP_MASK = MASK_DIR / "a1keep_r008_h016_w025_tau5_rightbmw_keepout_preserve_nonseam.png"

LONG_SOURCE_ROI = (820, 360, 1680, 735)

OUT_MASK = MASK_DIR / "a1longsrc_r008_h016_w025_tau5_preserve_nonseam.png"
OUT_PREVIEW = MASK_DIR / "a1longsrc_r008_h016_w025_tau5_preview.jpg"
OUT_MANIFEST = MASK_DIR / "a1longsrc_r008_h016_w025_tau5_manifest.json"


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def components(mask_core: np.ndarray) -> list[dict[str, object]]:
    h, w = mask_core.shape
    seen = np.zeros_like(mask_core, dtype=bool)
    out: list[dict[str, object]] = []
    for y in range(h):
        xs = np.where(mask_core[y] & ~seen[y])[0]
        for x0 in xs:
            if seen[y, x0] or not mask_core[y, x0]:
                continue
            q = [(int(x0), int(y))]
            seen[y, x0] = True
            pts: list[tuple[int, int]] = []
            for x, yy in q:
                pts.append((x, yy))
                for nx, ny in ((x + 1, yy), (x - 1, yy), (x, yy + 1), (x, yy - 1)):
                    if 0 <= nx < w and 0 <= ny < h and mask_core[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((nx, ny))
            xs2 = [p[0] for p in pts]
            ys2 = [p[1] for p in pts]
            out.append(
                {
                    "area": len(pts),
                    "bbox_xyxy": [min(xs2), min(ys2), max(xs2) + 1, max(ys2) + 1],
                    "points": pts,
                }
            )
    out.sort(key=lambda c: int(c["area"]), reverse=True)
    return out


def intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def overlay(base: Image.Image, old_core: np.ndarray, selected_core: np.ndarray) -> Image.Image:
    rgb = np.asarray(base.convert("RGB")).copy()
    old = old_core & ~selected_core
    sel = selected_core
    rgb[old] = (0.55 * rgb[old] + 0.45 * np.array([255, 64, 64])).astype(np.uint8)
    rgb[sel] = (0.35 * rgb[sel] + 0.65 * np.array([0, 220, 255])).astype(np.uint8)
    img = Image.fromarray(rgb, "RGB")
    d = ImageDraw.Draw(img)
    d.rectangle(LONG_SOURCE_ROI, outline=(255, 255, 0), width=4)
    return img


def make_preview(base: Image.Image, old_core: np.ndarray, selected_core: np.ndarray, comps: list[dict[str, object]]) -> Image.Image:
    full = overlay(base, old_core, selected_core)
    roi = full.crop(LONG_SOURCE_ROI)

    full_tile = full.resize((768, 384), Image.Resampling.LANCZOS)
    roi_tile = roi.resize((860, 375), Image.Resampling.LANCZOS)
    board = Image.new("RGB", (900, 770), (18, 18, 18))
    d = ImageDraw.Draw(board)
    d.text((12, 10), "DB40 A1 long_source-only component mask", fill=(255, 255, 255), font=font(22))
    d.text((12, 38), "cyan=selected core kept for A100, red=old keepout core now preserved, yellow=long_source ROI", fill=(220, 220, 220), font=font(14))
    board.paste(full_tile, (12, 70))
    board.paste(roi_tile, (12, 480))
    d.text((790, 78), "selected components:", fill=(255, 255, 255), font=font(14))
    y = 102
    for c in comps:
        label = "KEEP" if c.get("selected") else "DROP"
        d.text((790, y), f"{label} area={c['area']}", fill=(0, 220, 255) if c.get("selected") else (255, 120, 120), font=font(12))
        d.text((790, y + 15), str(c["bbox_xyxy"]), fill=(210, 210, 210), font=font(11))
        y += 42
    return board


def main() -> None:
    base = Image.open(A1_INPUT).convert("RGB")
    keep = np.asarray(Image.open(BASE_KEEP_MASK).convert("L"))
    old_core = keep < 128
    selected_core = np.zeros_like(old_core, dtype=bool)

    comps = components(old_core)
    public_comps = []
    for idx, comp in enumerate(comps):
        bbox = tuple(int(v) for v in comp["bbox_xyxy"])
        selected = intersects(bbox, LONG_SOURCE_ROI)
        if selected:
            for x, y in comp["points"]:
                selected_core[y, x] = True
        public_comps.append(
            {
                "index": idx,
                "area": int(comp["area"]),
                "bbox_xyxy": [int(v) for v in comp["bbox_xyxy"]],
                "selected": bool(selected),
            }
        )

    out_mask = np.full_like(keep, 255)
    out_mask[selected_core] = 0
    Image.fromarray(out_mask, "L").save(OUT_MASK)

    preview = make_preview(base, old_core, selected_core, public_comps)
    preview.save(OUT_PREVIEW, quality=92)

    manifest = {
        "base_keep_mask": str(BASE_KEEP_MASK.relative_to(ROOT)),
        "output_mask": str(OUT_MASK.relative_to(ROOT)),
        "preview": str(OUT_PREVIEW.relative_to(ROOT)),
        "long_source_roi_xyxy": LONG_SOURCE_ROI,
        "old_core_fraction": float(old_core.mean()),
        "selected_core_fraction": float(selected_core.mean()),
        "dropped_core_fraction": float((old_core & ~selected_core).mean()),
        "components": public_comps,
        "mask_convention": "white/255 preserves source; black/0 generates",
        "reason": "Keep only A1 keepout-mask components that intersect the long_source ROI; preserve unrelated vertical strips that created non-BMW edit bands in the first DB40 run.",
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {OUT_MASK}")
    print(f"wrote {OUT_PREVIEW}")
    print(f"wrote {OUT_MANIFEST}")


if __name__ == "__main__":
    main()
