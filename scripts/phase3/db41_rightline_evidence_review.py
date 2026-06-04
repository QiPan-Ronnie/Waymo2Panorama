#!/usr/bin/env python
"""Build the DB41 right-white-line source evidence review."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate"
RIGHT = OUT_DIR / "right_roi"
LOWER = OUT_DIR / "lower_right_roi"

PANOS = {
    "G_bmw_pano": ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg",
    "A1_view_none": ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment" / "A1_view_none_bmw_1024x2048.png",
    "DB32 current handoff": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db32_generated_sky_harmonize_v2"
    / "db32_generated_sky_harmonize_s40.png",
}

ROIS = {
    "right_roi": (1440, 360, 2048, 720),
    "lower_right_roi": (1580, 560, 2048, 790),
}

RECT = ROOT / "deliverables" / "dit360_v2" / "db22_rectilinear_diag" / "db22_rect_bmw_rightline_montage.jpg"
BOARD = OUT_DIR / "db41_rightline_evidence_board.jpg"
MANIFEST = OUT_DIR / "db41_rightline_evidence_manifest.json"


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


def label_tile(img: Image.Image, title: str, size: tuple[int, int], title_h: int = 28) -> Image.Image:
    tile = Image.new("RGB", (size[0], size[1] + title_h), (0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.text((8, 7), title, fill=(255, 255, 255), font=font(14))
    tile.paste(fit(img, size), (0, title_h))
    return tile


def draw_roi_boxes(img: Image.Image) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    sx = out.width / 2048.0
    sy = out.height / 1024.0
    colors = {"right_roi": (255, 64, 64), "lower_right_roi": (0, 220, 255)}
    for name, box in ROIS.items():
        x0, y0, x1, y1 = box
        d.rectangle([x0 * sx, y0 * sy, x1 * sx, y1 * sy], outline=colors[name], width=3)
    return out


def summary(subdir: Path) -> dict:
    return json.loads((subdir / "db25_longline_summary.json").read_text(encoding="utf-8"))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summaries = {"right_roi": summary(RIGHT), "lower_right_roi": summary(LOWER)}

    board = Image.new("RGB", (1700, 1960), (18, 18, 18))
    d = ImageDraw.Draw(board)
    d.text((18, 12), "DB41 right-white-line evidence gate: REJECT repair", fill=(255, 255, 255), font=font(24))
    d.text(
        (18, 44),
        "red=right ROI, cyan=lower-right ROI. CPU-only source evidence; no generation, no donor blend, no A100 repair.",
        fill=(220, 220, 220),
        font=font(14),
    )

    y = 78
    x = 18
    for name, path in PANOS.items():
        pano = load(path)
        full = draw_roi_boxes(fit(pano, (500, 250)))
        tile = label_tile(full, name, (500, 250))
        board.paste(tile, (x, y))
        x += 540

    y += 310
    x = 18
    for roi_name, box in ROIS.items():
        for name, path in PANOS.items():
            crop = load(path).crop(box)
            board.paste(label_tile(crop, f"{name} | {roi_name}", (245, 150)), (x, y))
            x += 265
        x = 18
        y += 200

    right_montage = load(RIGHT / "db25_longline_evidence_montage.jpg")
    lower_montage = load(LOWER / "db25_longline_evidence_montage.jpg")
    board.paste(label_tile(right_montage, "right_roi evidence montage", (800, 430)), (18, y))
    board.paste(label_tile(lower_montage, "lower_right_roi evidence montage", (800, 430)), (860, y))

    y += 490
    rect = load(RECT)
    board.paste(label_tile(rect, "DB22 rectilinear/right-line diagnostic reused for geometry review", (800, 440)), (18, y))

    text_x = 860
    d.text((text_x, y + 8), "Kill metrics", fill=(255, 255, 255), font=font(20))
    yy = y + 44
    for roi_name, s in summaries.items():
        d.text((text_x, yy), roi_name, fill=(255, 255, 255), font=font(16))
        yy += 24
        lines = [
            f"roi_valid_frac = {s['roi_valid_frac']:.3f}",
            f"near_ground_frac = {s['near_ground_frac']:.3f}",
            f"lidar_support_frac = {s['lidar_support_frac']:.3f}",
            f"best_flow_pair = {s.get('best_flow_pair')}",
            f"best_flow_reliable_frac = {s['best_flow_reliable_frac']:.3f}",
            f"top_camera_labels = {s['top_camera_labels']}",
        ]
        for line in lines:
            d.text((text_x + 14, yy), line, fill=(220, 220, 220), font=font(13))
            yy += 20
        yy += 14

    verdict_lines = [
        "Vision verdict:",
        "- right_roi: multi-camera split; LiDAR is sparse and mostly wall/building support, not a continuous white-line/curb surface.",
        "- lower_right_roi: full near-ground and LiDAR support is 0.0; flow hits vertical vehicle/edge fragments, not continuous road-line geometry.",
        "- Therefore no source-faithful Google/Meta-style micro-route is justified for original G right-line repair.",
    ]
    yy += 12
    for i, line in enumerate(verdict_lines):
        d.text((text_x, yy), line, fill=(255, 235, 180) if i == 0 else (235, 235, 235), font=font(13))
        yy += 24 if i == 0 else 42

    board.save(BOARD, quality=92)

    gate = {
        "right_roi": {
            "passes_flow_threshold": summaries["right_roi"]["best_flow_reliable_frac"] >= 0.50,
            "passes_lidar_threshold": summaries["right_roi"]["lidar_support_frac"] >= 0.20,
        },
        "lower_right_roi": {
            "passes_flow_threshold": summaries["lower_right_roi"]["best_flow_reliable_frac"] >= 0.50,
            "passes_lidar_threshold": summaries["lower_right_roi"]["lidar_support_frac"] >= 0.20,
        },
    }
    for roi_name in gate:
        gate[roi_name]["passes_db41_gate"] = gate[roi_name]["passes_flow_threshold"] and gate[roi_name]["passes_lidar_threshold"]

    manifest = {
        "board": str(BOARD.relative_to(ROOT)),
        "rois_xyxy": ROIS,
        "summaries": summaries,
        "thresholds": {
            "min_best_flow_reliable_frac": 0.50,
            "min_lidar_support_frac": 0.20,
        },
        "threshold_results": gate,
        "inputs": {name: str(path.relative_to(ROOT)) for name, path in PANOS.items()},
        "evidence_files": {
            "right_roi": [str(p.relative_to(ROOT)) for p in sorted(RIGHT.iterdir())],
            "lower_right_roi": [str(p.relative_to(ROOT)) for p in sorted(LOWER.iterdir())],
            "rectilinear_reference": str(RECT.relative_to(ROOT)),
        },
        "vision_verdict": {
            "right_roi": "Fail as repair evidence: flow has local reliable patches, but LiDAR support is only 0.084 and the visible support does not establish a single continuous curb/white-line surface.",
            "lower_right_roi": "Fail as repair evidence: LiDAR support is 0.0 in an all-near-ground ROI; flow reliability is not sufficient because it attaches to vertical vehicle/edge fragments rather than the white-line geometry.",
            "decision": "DB41 rejects original-G right-white-line repair. Future seam work should not edit this band without new raw/depth/correspondence evidence.",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {BOARD}")
    print(f"wrote {MANIFEST}")


if __name__ == "__main__":
    main()
