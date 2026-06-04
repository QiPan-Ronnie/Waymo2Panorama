#!/usr/bin/env python
"""Build a DB40 A1 keepout visual review board and manifest."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment"
FETCH = OUT_DIR / "a1_keepout_strict_fetch" / "a1_keepout_strict" / "a1_keepout_strict_tau5"

PATHS = {
    "A1 input": OUT_DIR / "A1_view_none_bmw_1024x2048.png",
    "DB14 A1 old-mask raw": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db14_a1_view_none_fetch"
    / "A1_view_none_bmw"
    / "a1view_r008_h016_w025_tau5"
    / "a1view_r008_h016_w025_tau5_raw.png",
    "DB40 keepout raw": FETCH / "a1_keepout_strict_tau5_raw.png",
    "DB40 keepout soft": FETCH / "a1_keepout_strict_tau5_softcompose.png",
    "DB40 keepout core": FETCH / "a1_keepout_strict_tau5_corecompose.png",
    "Old v14 reference raw": ROOT
    / "deliverables"
    / "dit360_seam_completion"
    / "runs_v14_trimap_clamp_bmw"
    / "trimap_r008_h016_w025_tau5"
    / "trimap_r008_h016_w025_tau5_raw_fullres_1024x2048.png",
}

ROIS = {
    "full": (0, 0, 2048, 1024),
    "right_bmw_user_roi": (1320, 300, 1960, 735),
    "right_seam": (1530, 300, 1900, 735),
    "long_source": (820, 360, 1680, 735),
}

SIZES = {
    "full": (512, 256),
    "right_bmw_user_roi": (420, 286),
    "right_seam": (320, 376),
    "long_source": (620, 270),
}

BOARD = OUT_DIR / "db40_a1_keepout_review_board.jpg"
MANIFEST = OUT_DIR / "db40_a1_keepout_review_manifest.json"


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def load_rgb(path: Path) -> Image.Image:
    if not path.exists():
        raise FileNotFoundError(path)
    return Image.open(path).convert("RGB")


def fit_tile(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGB", size, (0, 0, 0))
    work = img.copy()
    work.thumbnail(size, Image.Resampling.LANCZOS)
    x = (size[0] - work.width) // 2
    y = (size[1] - work.height) // 2
    canvas.paste(work, (x, y))
    return canvas


def draw_full_boxes(img: Image.Image) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    sx = out.width / 2048.0
    sy = out.height / 1024.0
    colors = {
        "right_bmw_user_roi": (255, 64, 64),
        "right_seam": (255, 200, 0),
        "long_source": (0, 220, 255),
    }
    for name, box in ROIS.items():
        if name == "full":
            continue
        x0, y0, x1, y1 = box
        d.rectangle(
            [x0 * sx, y0 * sy, x1 * sx, y1 * sy],
            outline=colors[name],
            width=2,
        )
    return out


def roi_mae(a: Image.Image, b: Image.Image, box: tuple[int, int, int, int]) -> float:
    aa = np.asarray(a.crop(box), dtype=np.float32)
    bb = np.asarray(b.crop(box), dtype=np.float32)
    return float(np.mean(np.abs(aa - bb)))


def main() -> None:
    images = {name: load_rgb(path) for name, path in PATHS.items()}
    input_img = images["A1 input"]

    gap = 10
    header_h = 70
    row_label_w = 190
    row_h = max(SIZES.values(), key=lambda s: s[1])[1] + 42
    board_w = row_label_w + sum(SIZES[k][0] for k in ROIS) + gap * (len(ROIS) + 1)
    board_h = header_h + row_h * len(images) + gap
    board = Image.new("RGB", (board_w, board_h), (18, 18, 18))
    d = ImageDraw.Draw(board)
    title_font = font(20)
    label_font = font(16)
    small_font = font(13)

    verdict = (
        "DB40 A1 keepout: right BMW ghost/slice fixed; "
        "global seam still not accepted because vertical edit bands remain."
    )
    d.text((12, 10), verdict, fill=(255, 255, 255), font=title_font)
    d.text(
        (12, 38),
        "ROI colors on full tile: red=right BMW, yellow=right seam, cyan=long seam/source boundary.",
        fill=(210, 210, 210),
        font=small_font,
    )

    x = row_label_w + gap
    for roi_name in ROIS:
        d.text((x, header_h - 22), roi_name, fill=(230, 230, 230), font=small_font)
        x += SIZES[roi_name][0] + gap

    for row_i, (name, img) in enumerate(images.items()):
        y = header_h + row_i * row_h
        d.text((12, y + 12), name, fill=(255, 255, 255), font=label_font)
        if name != "A1 input":
            rb = roi_mae(input_img, img, ROIS["right_bmw_user_roi"])
            ls = roi_mae(input_img, img, ROIS["long_source"])
            d.text((12, y + 38), f"MAE vs input rb={rb:.2f} long={ls:.2f}", fill=(190, 190, 190), font=small_font)
        x = row_label_w + gap
        for roi_name, box in ROIS.items():
            tile = img.crop(box)
            if roi_name == "full":
                tile = draw_full_boxes(fit_tile(tile, SIZES[roi_name]))
            else:
                tile = fit_tile(tile, SIZES[roi_name])
            board.paste(tile, (x, y + 30))
            d.rectangle(
                [x, y + 30, x + SIZES[roi_name][0] - 1, y + 30 + SIZES[roi_name][1] - 1],
                outline=(70, 70, 70),
                width=1,
            )
            x += SIZES[roi_name][0] + gap

    board.save(BOARD, quality=92)

    gate_path = FETCH / "a1_keepout_strict_tau5_gate_gate.json"
    diag_path = FETCH / "a1_keepout_strict_tau5_diagnostics.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8")) if gate_path.exists() else {}
    diag = json.loads(diag_path.read_text(encoding="utf-8")) if diag_path.exists() else {}

    metrics = {}
    for name, img in images.items():
        if name == "A1 input":
            continue
        metrics[name] = {roi: roi_mae(input_img, img, box) for roi, box in ROIS.items() if roi != "full"}

    manifest = {
        "board": str(BOARD.relative_to(ROOT)),
        "inputs": {name: str(path.relative_to(ROOT)) for name, path in PATHS.items()},
        "rois_xyxy": ROIS,
        "object_gate": gate,
        "diagnostics_subset": {
            k: diag.get(k)
            for k in [
                "core_fraction",
                "halo_fraction",
                "far_fraction",
                "raw_core_mae_vs_init",
                "soft_halo_mae_vs_init",
                "corecompose_halo_mae_vs_init",
                "case_runtime_s",
            ]
        },
        "roi_mae_vs_a1_input": metrics,
        "vision_verdict": {
            "right_bmw_user_roi": "PASS as root-cause evidence: the white BMW is preserved and the user-marked vertical slab/ghost is removed.",
            "global_seam": "FAIL as final seam solution: raw/soft/core still show visible vertical edit bands away from the BMW, especially in the long_source/dark-wall region.",
            "interpretation": "Keepout proves the A1 BMW artifact was mask intrusion / candidate-mask mismatch, but prompt tuning alone is not enough. Next tests should shrink or reroute the edited seam support, not repeat the same broad v14 strip.",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {BOARD}")
    print(f"wrote {MANIFEST}")


if __name__ == "__main__":
    main()
