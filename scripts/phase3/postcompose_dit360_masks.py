"""Post-compose DiT360 mask-edit outputs with the original panorama.

DiT360 edits can modify pixels outside the black/generate mask. For driving-data
stitching, those edits are undesirable: the source cameras are the evidence.
This utility keeps the DiT360 prediction only inside the black mask and restores
the original hard-select panorama everywhere else.

Mask convention:
  white/255 = preserve source
  black/0   = use generated DiT360 output
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def _parse_case(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in text.split(","):
        key, value = part.split("=", 1)
        out[key.strip()] = value.strip()
    for required in ("name", "mask", "raw"):
        if required not in out:
            raise ValueError(f"case must include {required}: {text}")
    return out


def _parse_crop(text: str) -> tuple[str, tuple[int, int, int, int]]:
    name, coords = text.split("=", 1)
    vals = [int(v) for v in coords.split(",")]
    if len(vals) != 4:
        raise ValueError(f"crop must be name=x0,y0,x1,y1: {text}")
    x0, y0, x1, y1 = vals
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"invalid crop box: {text}")
    return name, (x0, y0, x1, y1)


def _load_rgb(path: Path, size: tuple[int, int] | None = None) -> Image.Image:
    img = Image.open(path).convert("RGB")
    if size is not None and img.size != size:
        img = img.resize(size, Image.Resampling.BICUBIC)
    return img


def _load_mask(path: Path, size: tuple[int, int]) -> np.ndarray:
    return np.array(Image.open(path).convert("L").resize(size, Image.Resampling.NEAREST))


def _postcompose(init: Image.Image, mask_path: Path, raw_path: Path) -> tuple[Image.Image, dict[str, float]]:
    raw = _load_rgb(raw_path, init.size)
    init_np = np.array(init, dtype=np.uint8)
    raw_np = np.array(raw, dtype=np.uint8)
    mask = _load_mask(mask_path, init.size)
    preserve = mask >= 128
    generate = ~preserve

    comp = init_np.copy()
    comp[generate] = raw_np[generate]

    raw_diff_preserve = raw_np[preserve].astype(np.float32) - init_np[preserve].astype(np.float32)
    comp_diff_preserve = comp[preserve].astype(np.float32) - init_np[preserve].astype(np.float32)
    gen_diff = raw_np[generate].astype(np.float32) - init_np[generate].astype(np.float32)
    raw_rmse = float(np.sqrt(np.mean(raw_diff_preserve * raw_diff_preserve))) if preserve.any() else float("nan")
    comp_rmse = float(np.sqrt(np.mean(comp_diff_preserve * comp_diff_preserve))) if preserve.any() else float("nan")
    gen_mae = float(np.mean(np.abs(gen_diff))) if generate.any() else float("nan")

    metrics = {
        "preserve_fraction": float(preserve.mean()),
        "generate_fraction": float(generate.mean()),
        "raw_preserve_mae": float(np.mean(np.abs(raw_diff_preserve))) if preserve.any() else float("nan"),
        "raw_preserve_rmse": raw_rmse,
        "raw_preserve_psnr": float(20.0 * np.log10(255.0 / max(raw_rmse, 1e-6))) if preserve.any() else float("nan"),
        "postcompose_preserve_mae": float(np.mean(np.abs(comp_diff_preserve))) if preserve.any() else float("nan"),
        "postcompose_preserve_rmse": comp_rmse,
        "postcompose_preserve_psnr": float(20.0 * np.log10(255.0 / max(comp_rmse, 1e-6))) if preserve.any() else float("nan"),
        "generated_region_raw_vs_init_mae": gen_mae,
    }
    return Image.fromarray(comp), metrics


def _label_band(width: int, label: str) -> np.ndarray:
    band = np.zeros((34, width, 3), dtype=np.uint8)
    cv2.putText(band, label, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    return band


def _fit_width(img: Image.Image, width: int) -> np.ndarray:
    if img.width == width:
        return np.array(img)
    h = max(1, round(img.height * width / img.width))
    return np.array(img.resize((width, h), Image.Resampling.BICUBIC))


def _save_overall_review(path: Path, init: Image.Image, rows: list[tuple[str, Image.Image]], width: int) -> None:
    panels = []
    for label, img in [("input", init), *rows]:
        arr = _fit_width(img, width)
        panels.append(np.vstack([_label_band(width, label), arr]))
    Image.fromarray(np.vstack(panels)).save(path, quality=92)


def _save_crop_review(
    path: Path,
    init: Image.Image,
    rows: list[tuple[str, Image.Image]],
    crops: list[tuple[str, tuple[int, int, int, int]]],
) -> None:
    if not crops:
        return

    rendered_rows = []
    for label, img in [("input", init), *rows]:
        crop_imgs = []
        for crop_name, box in crops:
            crop = np.array(img.crop(box))
            band = _label_band(crop.shape[1], f"{label} | {crop_name}")
            crop_imgs.append(np.vstack([band, crop]))
        max_h = max(c.shape[0] for c in crop_imgs)
        padded = []
        for crop in crop_imgs:
            if crop.shape[0] < max_h:
                pad = np.zeros((max_h - crop.shape[0], crop.shape[1], 3), dtype=np.uint8)
                crop = np.vstack([crop, pad])
            padded.append(crop)
        rendered_rows.append(np.hstack(padded))
    Image.fromarray(np.vstack(rendered_rows)).save(path, quality=92)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-image", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--case", action="append", required=True)
    ap.add_argument("--crop", action="append", default=[])
    ap.add_argument("--overall-width", type=int, default=1024)
    args = ap.parse_args()

    init_path = Path(args.init_image)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    init = _load_rgb(init_path)
    crops = [_parse_crop(item) for item in args.crop]

    summaries: list[dict[str, object]] = []
    review_rows: list[tuple[str, Image.Image]] = []
    for item in args.case:
        case = _parse_case(item)
        name = case["name"]
        mask_path = Path(case["mask"])
        raw_path = Path(case["raw"])
        raw = _load_rgb(raw_path, init.size)
        comp, metrics = _postcompose(init, mask_path, raw_path)

        case_dir = out_dir / name
        case_dir.mkdir(parents=True, exist_ok=True)
        comp_path = case_dir / f"{name}_postcompose.png"
        raw_copy_path = case_dir / f"{name}_raw_resized.png"
        diag_path = case_dir / f"{name}_postcompose_diagnostics.json"
        comp.save(comp_path)
        raw.save(raw_copy_path)

        summary = {
            "name": name,
            "init_image": str(init_path),
            "mask": str(mask_path),
            "raw_output": str(raw_path),
            "postcompose_output": str(comp_path),
            "mask_convention": "white/255 preserves source; black/0 uses DiT360 output",
            **metrics,
        }
        with open(diag_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        summaries.append(summary)
        review_rows.extend([(f"{name} raw", raw), (f"{name} postcompose", comp)])

    with open(out_dir / "postcompose_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": summaries}, f, indent=2, ensure_ascii=False)
    _save_overall_review(out_dir / "postcompose_overall_review.jpg", init, review_rows, args.overall_width)
    _save_crop_review(out_dir / "postcompose_crop_review.jpg", init, review_rows, crops)
    print(json.dumps({"runs": summaries}, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
