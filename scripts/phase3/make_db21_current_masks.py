from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def _load_rgb(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def _polygon_mask(size: tuple[int, int], polygon: list[tuple[int, int]]) -> np.ndarray:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).polygon(polygon, fill=255)
    return np.array(m) > 0


def _save_candidate(
    image: Image.Image,
    out_dir: Path,
    name: str,
    polygons: list[list[tuple[int, int]]],
    content_threshold: int,
    crop_box: tuple[int, int, int, int],
) -> None:
    rgb = np.array(image)
    content = rgb.max(axis=2) > content_threshold

    core = np.zeros((image.height, image.width), dtype=bool)
    for poly in polygons:
        core |= _polygon_mask(image.size, poly)
    core &= content

    preserve = np.full((image.height, image.width), 255, dtype=np.uint8)
    preserve[core] = 0
    Image.fromarray(preserve, mode="L").save(out_dir / f"{name}_mask_preserve_nonseam.png")

    overlay = image.copy()
    od = ImageDraw.Draw(overlay, "RGBA")
    for poly in polygons:
        od.line(poly + [poly[0]], fill=(255, 190, 0, 160), width=18)
    core_rgba = Image.new("RGBA", image.size, (0, 0, 0, 0))
    c = np.zeros((image.height, image.width, 4), dtype=np.uint8)
    c[core] = (255, 0, 80, 115)
    core_rgba = Image.fromarray(c, mode="RGBA")
    overlay = Image.alpha_composite(overlay.convert("RGBA"), core_rgba).convert("RGB")
    overlay.save(out_dir / f"{name}_overlay.jpg", quality=94)
    overlay.crop(crop_box).save(out_dir / f"{name}_overlay_right_bmw.jpg", quality=96)

    diag = {
        "name": name,
        "core_fraction": float(core.mean()),
        "core_pixels": int(core.sum()),
        "content_threshold": int(content_threshold),
        "polygons": polygons,
    }
    (out_dir / f"{name}_diagnostics.txt").write_text(str(diag) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--content-threshold", type=int, default=20)
    args = ap.parse_args()

    image = _load_rgb(Path(args.image))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    crop_box = (1420, 360, 2048, 760)
    image.crop(crop_box).save(out_dir / "right_bmw_wide.jpg", quality=96)
    image.crop((1480, 560, 2048, 760)).save(out_dir / "right_ground.jpg", quality=96)

    candidates = {
        # Last-resort ultra-thin line/curb candidate: much less canvas than rg_line_narrow.
        "rg_line_ultra": [[(1810, 574), (2047, 558), (2047, 612), (1810, 634)]],
        # Only the visible right-side white-line / curb kink, excluding the BMW body and black lower band.
        "rg_line_narrow": [[(1780, 564), (2047, 548), (2047, 638), (1780, 668)]],
        # Slightly broader right-ground candidate if the narrow line misses the discontinuity.
        "rg_line_mid": [[(1695, 578), (2047, 552), (2047, 662), (1695, 694)]],
        # Separate dark-wall lower seam; not mixed with the BMW/right-line test.
        "darkwall_lower": [[(1110, 585), (1500, 605), (1500, 716), (1110, 690)]],
    }

    for name, polys in candidates.items():
        _save_candidate(image, out_dir, name, polys, args.content_threshold, crop_box)

    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
