#!/usr/bin/env python
"""Review the DB40 A1 long_source-only mask replay."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment"
KEEP_FETCH = OUT_DIR / "a1_keepout_strict_fetch" / "a1_keepout_strict" / "a1_keepout_strict_tau5"
LONG_FETCH = OUT_DIR / "a1_longsrc_only_fetch" / "a1_longsrc_only_tau5"

PATHS = {
    "A1 input": OUT_DIR / "A1_view_none_bmw_1024x2048.png",
    "DB14 A1 old-mask raw": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db14_a1_view_none_fetch"
    / "A1_view_none_bmw"
    / "a1view_r008_h016_w025_tau5"
    / "a1view_r008_h016_w025_tau5_raw.png",
    "DB40 keepout core": KEEP_FETCH / "a1_keepout_strict_tau5_corecompose.png",
    "DB40 longsrc raw": LONG_FETCH / "a1_longsrc_only_tau5_raw.png",
    "DB40 longsrc soft": LONG_FETCH / "a1_longsrc_only_tau5_softcompose.png",
    "DB40 longsrc core": LONG_FETCH / "a1_longsrc_only_tau5_corecompose.png",
}

ROIS = {
    "full": (0, 0, 2048, 1024),
    "long_source": (820, 360, 1680, 735),
    "right_bmw": (1320, 300, 1960, 735),
}

SIZES = {
    "full": (540, 270),
    "long_source": (690, 300),
    "right_bmw": (420, 286),
}

BOARD = OUT_DIR / "db40_a1_longsrc_review_board.jpg"
MANIFEST = OUT_DIR / "db40_a1_longsrc_review_manifest.json"


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def load(path: Path) -> Image.Image:
    if not path.exists():
        raise FileNotFoundError(path)
    return Image.open(path).convert("RGB")


def fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGB", size, (0, 0, 0))
    work = img.copy()
    work.thumbnail(size, Image.Resampling.LANCZOS)
    canvas.paste(work, ((size[0] - work.width) // 2, (size[1] - work.height) // 2))
    return canvas


def draw_rois(full: Image.Image) -> Image.Image:
    out = full.copy()
    d = ImageDraw.Draw(out)
    sx = out.width / 2048.0
    sy = out.height / 1024.0
    colors = {"long_source": (0, 220, 255), "right_bmw": (255, 64, 64)}
    for name, box in ROIS.items():
        if name == "full":
            continue
        x0, y0, x1, y1 = box
        d.rectangle([x0 * sx, y0 * sy, x1 * sx, y1 * sy], outline=colors[name], width=2)
    return out


def mae(a: Image.Image, b: Image.Image, box: tuple[int, int, int, int]) -> float:
    aa = np.asarray(a.crop(box), dtype=np.float32)
    bb = np.asarray(b.crop(box), dtype=np.float32)
    return float(np.mean(np.abs(aa - bb)))


def main() -> None:
    imgs = {name: load(path) for name, path in PATHS.items()}
    src = imgs["A1 input"]

    gap = 10
    header_h = 78
    label_w = 190
    row_h = 340
    board_w = label_w + sum(SIZES[k][0] for k in ROIS) + gap * (len(ROIS) + 1)
    board_h = header_h + len(imgs) * row_h + gap
    board = Image.new("RGB", (board_w, board_h), (18, 18, 18))
    d = ImageDraw.Draw(board)
    d.text((12, 10), "DB40 A1 long_source-only mask replay: REJECT", fill=(255, 255, 255), font=font(22))
    d.text(
        (12, 40),
        "Mask support shrank to long_source components only, but DiT generated a pole-like vertical object. right BMW stays clean.",
        fill=(225, 225, 225),
        font=font(14),
    )
    x = label_w + gap
    for roi in ROIS:
        d.text((x, header_h - 20), roi, fill=(230, 230, 230), font=font(13))
        x += SIZES[roi][0] + gap

    metrics = {}
    for i, (name, img) in enumerate(imgs.items()):
        y = header_h + i * row_h
        d.text((12, y + 12), name, fill=(255, 255, 255), font=font(15))
        if name != "A1 input":
            ls = mae(src, img, ROIS["long_source"])
            rb = mae(src, img, ROIS["right_bmw"])
            metrics[name] = {"long_source": ls, "right_bmw": rb}
            d.text((12, y + 36), f"MAE vs input long={ls:.2f} rb={rb:.2f}", fill=(190, 190, 190), font=font(12))
        x = label_w + gap
        for roi, box in ROIS.items():
            tile = img.crop(box)
            tile = fit(tile, SIZES[roi])
            if roi == "full":
                tile = draw_rois(tile)
            board.paste(tile, (x, y + 50))
            d.rectangle([x, y + 50, x + SIZES[roi][0] - 1, y + 50 + SIZES[roi][1] - 1], outline=(70, 70, 70))
            x += SIZES[roi][0] + gap

    board.save(BOARD, quality=92)

    gate_path = LONG_FETCH / "a1_longsrc_only_tau5_gate_gate.json"
    diag_path = LONG_FETCH / "a1_longsrc_only_tau5_diagnostics.json"
    mask_manifest = OUT_DIR / "masks" / "a1longsrc_r008_h016_w025_tau5_manifest.json"

    manifest = {
        "board": str(BOARD.relative_to(ROOT)),
        "inputs": {name: str(path.relative_to(ROOT)) for name, path in PATHS.items()},
        "rois_xyxy": ROIS,
        "object_gate": json.loads(gate_path.read_text(encoding="utf-8")),
        "diagnostics": json.loads(diag_path.read_text(encoding="utf-8")),
        "mask_manifest": json.loads(mask_manifest.read_text(encoding="utf-8")),
        "roi_mae_vs_a1_input": metrics,
        "vision_verdict": {
            "right_bmw": "PASS: BMW remains source-preserved and no old A1 white slab/ghost returns.",
            "long_source": "FAIL: the narrower mask causes a conspicuous pole-like vertical object in raw/soft/core, so the seam edit is not source-faithful.",
            "decision": "Reject DB40 long_source-only rerun. Mask shrinking removed unrelated edit strips but DiT still hallucinates structure inside the remaining seam core.",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {BOARD}")
    print(f"wrote {MANIFEST}")


if __name__ == "__main__":
    main()
