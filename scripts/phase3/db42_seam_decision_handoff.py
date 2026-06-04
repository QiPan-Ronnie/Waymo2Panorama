#!/usr/bin/env python
"""Build DB42 seam decision / Bosch handoff synthesis artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db42_seam_decision_handoff"
BOARD = OUT_DIR / "db42_seam_decision_handoff_board.jpg"
REPORT = OUT_DIR / "db42_seam_decision_handoff_report.md"
MANIFEST = OUT_DIR / "db42_seam_decision_handoff_manifest.json"

ARTIFACTS = {
    "db32_current_image": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db32_generated_sky_harmonize_v2"
    / "db32_generated_sky_harmonize_s40.png",
    "db38_handoff_board": ROOT / "deliverables" / "dit360_v2" / "db38_bosch_handoff" / "db38_bosch_handoff_board.jpg",
    "db40_keepout_board": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db40_v14_mask_alignment"
    / "db40_a1_keepout_review_board.jpg",
    "db40_longsrc_board": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db40_v14_mask_alignment"
    / "db40_a1_longsrc_review_board.jpg",
    "db41_evidence_board": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "db41_rightline_evidence_board.jpg",
    "db41_manifest": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "db41_rightline_evidence_manifest.json",
    "db38_manifest": ROOT / "deliverables" / "dit360_v2" / "db38_bosch_handoff" / "db38_bosch_handoff_manifest.json",
}


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


def labeled(img: Image.Image, title: str, size: tuple[int, int], h: int = 32) -> Image.Image:
    tile = Image.new("RGB", (size[0], size[1] + h), (0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.text((8, 8), title, fill=(255, 255, 255), font=font(15))
    tile.paste(fit(img, size), (0, h))
    return tile


def draw_text_box(draw: ImageDraw.ImageDraw, xy: tuple[int, int], title: str, lines: list[str], width: int) -> int:
    x, y = xy
    draw.rectangle([x, y, x + width, y + 190], fill=(30, 30, 30), outline=(80, 80, 80))
    draw.text((x + 14, y + 12), title, fill=(255, 245, 190), font=font(18))
    yy = y + 44
    for line in lines:
        draw.text((x + 18, yy), line, fill=(235, 235, 235), font=font(13))
        yy += 28
    return y + 205


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in ARTIFACTS.values():
        if not path.exists():
            raise FileNotFoundError(path)

    db41 = json.loads(ARTIFACTS["db41_manifest"].read_text(encoding="utf-8"))
    db38 = json.loads(ARTIFACTS["db38_manifest"].read_text(encoding="utf-8"))

    board = Image.new("RGB", (1800, 2100), (18, 18, 18))
    d = ImageDraw.Draw(board)
    d.text((18, 14), "DB42 seam decision / Bosch handoff synthesis", fill=(255, 255, 255), font=font(26))
    d.text(
        (18, 48),
        "No new repair image. This board packages the current accepted candidate, rejected seam lanes, and evidence required to reopen.",
        fill=(220, 220, 220),
        font=font(14),
    )

    y = 86
    board.paste(labeled(load(ARTIFACTS["db32_current_image"]), "ACCEPTED current handoff image: DB32 s40", (880, 440)), (18, y))
    decision_lines = [
        "Use DB32 s40 for current Bosch handoff.",
        "Do not claim original G/A1/BEST right seam is fixed.",
        "DiT360 is useful for sky-only/object-gated fill, not ground seam repair.",
        "Reopen only with new raw/depth/correspondence evidence.",
    ]
    draw_text_box(d, (930, y + 20), "Decision", decision_lines, 820)
    y += 520

    board.paste(labeled(load(ARTIFACTS["db38_handoff_board"]), "DB38: Bosch handoff comparison", (860, 520)), (18, y))
    board.paste(labeled(load(ARTIFACTS["db41_evidence_board"]), "DB41: right-white-line source evidence gate", (860, 520)), (920, y))
    y += 600

    board.paste(labeled(load(ARTIFACTS["db40_keepout_board"]), "DB40 positive evidence: A1 BMW ghost root-cause", (860, 420)), (18, y))
    board.paste(labeled(load(ARTIFACTS["db40_longsrc_board"]), "DB40 reject evidence: narrowed mask hallucinates pole", (860, 420)), (920, y))
    y += 500

    route_lines = [
        "Original G/A1/BEST seam repair: closed under current evidence.",
        "DB40: A1 BMW slab explained, but v14 DiT seam repair rejected.",
        "DB41: lower-right white-line lacks LiDAR/source-continuity evidence.",
        "DB37: Google/Meta mechanisms need reliable overlap/depth/flow.",
    ]
    draw_text_box(d, (18, y), "Closed Seam Lanes", route_lines, 850)
    caveat_lines = [
        "DB32 caveats: black car remains; lower out-of-FOV band remains.",
        "Sky panel discontinuity reduced, not eliminated.",
        "This is a source sidestep, not a fix for original G seam.",
        "Avoid fake generated ground for Bosch/world-model data.",
    ]
    draw_text_box(d, (930, y), "Handoff Caveats", caveat_lines, 850)

    board.save(BOARD, quality=92)

    route_table = [
        {
            "route": "DB32 s40",
            "status": "accepted current Bosch handoff candidate",
            "reason": "source sidestep via cleaner DB28/a200 base plus object-gated sky completion/harmonization",
            "artifact": str(ARTIFACTS["db32_current_image"].relative_to(ROOT)),
        },
        {
            "route": "Original G/A1/BEST seam patching",
            "status": "rejected / diagnostic only",
            "reason": "DB35, DB39, DB40, DB41 show seam persists or repair creates ghost/slice/fake geometry",
            "artifact": str(ARTIFACTS["db41_evidence_board"].relative_to(ROOT)),
        },
        {
            "route": "DiT360 ground / v14 trimap seam repair",
            "status": "rejected for ground seam",
            "reason": "object gates can pass while vision fails; generates slabs, pole-like objects, or fake ground",
            "artifact": str(ARTIFACTS["db40_longsrc_board"].relative_to(ROOT)),
        },
        {
            "route": "Google/Meta-style production seam",
            "status": "closed under current evidence",
            "reason": "right-line/long-line ROIs lack enough depth/source-continuity evidence for source-faithful correction",
            "artifact": str(ARTIFACTS["db41_manifest"].relative_to(ROOT)),
        },
    ]

    report = f"""# DB42 Seam Decision / Bosch Handoff Synthesis

## Decision

Use `deliverables/dit360_v2/db32_generated_sky_harmonize_v2/db32_generated_sky_harmonize_s40.png` as the current Bosch handoff candidate.

Do not claim that the original `G_bmw_pano` / `A1_view_none` / `BEST_bmw_pano` right-ground seam has been repaired. The current evidence says that local seam repair should stop unless new raw/depth/correspondence evidence appears.

## Why

- DB38 accepted DB32 as the current handoff candidate with caveats: it sidesteps the original G seam by using the cleaner DB28/a200 source and only applies object-gated sky fill/harmonization.
- DB40 explains the A1 right-BMW ghost as candidate/mask mismatch, but rejects v14 DiT360 seam repair because the narrowed long-source mask hallucinates a pole-like vertical object.
- DB41 rejects lower-right/right-white-line repair evidence: `right_roi` LiDAR support is `{db41['summaries']['right_roi']['lidar_support_frac']:.3f}` and `lower_right_roi` LiDAR support is `{db41['summaries']['lower_right_roi']['lidar_support_frac']:.3f}`.
- DB37/DB41 together answer the Google/Meta question: production stitchers rely on reliable overlap/depth/flow and abstain or choose a better source when evidence is insufficient.

## Handoff Caveats

- DB32 is a source-sidestep candidate, not a repair of the original G seam.
- The foreground black car remains.
- The lower out-of-FOV band remains.
- The center sky panel discontinuity is reduced, not eliminated.
- Fake generated ground/curb is worse for Bosch/world-model data than an honest capture caveat.

## Artifacts

- Board: `deliverables/dit360_v2/db42_seam_decision_handoff/db42_seam_decision_handoff_board.jpg`
- Manifest: `deliverables/dit360_v2/db42_seam_decision_handoff/db42_seam_decision_handoff_manifest.json`
- DB38 board: `deliverables/dit360_v2/db38_bosch_handoff/db38_bosch_handoff_board.jpg`
- DB40 boards: `deliverables/dit360_v2/db40_v14_mask_alignment/db40_a1_keepout_review_board.jpg`, `deliverables/dit360_v2/db40_v14_mask_alignment/db40_a1_longsrc_review_board.jpg`
- DB41 board: `deliverables/dit360_v2/db41_rightline_evidence_gate/db41_rightline_evidence_board.jpg`
"""
    REPORT.write_text(report, encoding="utf-8")

    manifest = {
        "board": str(BOARD.relative_to(ROOT)),
        "report": str(REPORT.relative_to(ROOT)),
        "accepted_current_handoff": str(ARTIFACTS["db32_current_image"].relative_to(ROOT)),
        "route_table": route_table,
        "db38_manifest": db38,
        "db41_threshold_results": db41["threshold_results"],
        "decision": {
            "use": "DB32 s40 as current Bosch handoff candidate",
            "do_not_use_as_final": ["G_bmw_pano seam patches", "A1_view_none v14 seam patches", "BEST_bmw_pano donor/v14 seam patches"],
            "reopen_condition": "new source/depth/correspondence evidence that passes a brief with kill criteria",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {BOARD}")
    print(f"wrote {REPORT}")
    print(f"wrote {MANIFEST}")


if __name__ == "__main__":
    main()
