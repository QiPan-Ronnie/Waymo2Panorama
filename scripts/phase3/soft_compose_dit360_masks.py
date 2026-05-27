"""Soft bounded composition for DiT360 mask-edit outputs.

Raw DiT360 seam edits often look smoother because the model changes pixels just
outside the black generation mask. Hard post-compose restores those pixels
exactly, but it can also reintroduce a visible hard boundary.

This script keeps a strict evidence boundary while allowing a small local halo:
  - black mask core: use DiT360 output
  - halo around the core: feather DiT360 output into the source panorama
  - outside halo: restore the source panorama exactly

Mask convention:
  white/255 = preserve source
  black/0   = generate/fill with DiT360
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


def _alpha_from_mask(mask: np.ndarray, halo_px: int, gamma: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    core = mask < 128
    if halo_px <= 0:
        alpha = core.astype(np.float32)
    else:
        outside_core = (~core).astype(np.uint8)
        dist = cv2.distanceTransform(outside_core, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        alpha = np.clip(1.0 - (dist / float(max(1, halo_px))), 0.0, 1.0).astype(np.float32)
        alpha[core] = 1.0
    if gamma != 1.0:
        alpha = np.power(alpha, gamma).astype(np.float32)
        alpha[core] = 1.0
    halo = (alpha > 1e-6) & (~core)
    safe = alpha <= 1e-6
    return alpha, halo, safe


def _compose(
    init: Image.Image,
    mask_path: Path,
    raw_path: Path,
    halo_px: int,
    gamma: float,
) -> tuple[Image.Image, Image.Image, dict[str, float]]:
    raw = _load_rgb(raw_path, init.size)
    init_np = np.array(init, dtype=np.float32)
    raw_np = np.array(raw, dtype=np.float32)
    mask = _load_mask(mask_path, init.size)
    alpha, halo, safe = _alpha_from_mask(mask, halo_px, gamma)
    core = mask < 128

    alpha3 = alpha[..., None]
    comp = np.clip(init_np * (1.0 - alpha3) + raw_np * alpha3, 0.0, 255.0).astype(np.uint8)
    alpha_vis = np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8)

    raw_diff = raw_np - init_np
    comp_diff = comp.astype(np.float32) - init_np

    def mae(region: np.ndarray, arr: np.ndarray) -> float:
        if not region.any():
            return float("nan")
        return float(np.mean(np.abs(arr[region])))

    def rmse(region: np.ndarray, arr: np.ndarray) -> float:
        if not region.any():
            return float("nan")
        return float(np.sqrt(np.mean(arr[region] * arr[region])))

    safe_rmse = rmse(safe, comp_diff)
    preserve = mask >= 128
    metrics = {
        "halo_px": int(halo_px),
        "gamma": float(gamma),
        "core_fraction": float(core.mean()),
        "halo_fraction": float(halo.mean()),
        "safe_fraction": float(safe.mean()),
        "modified_fraction": float((alpha > 1e-6).mean()),
        "alpha_mean": float(alpha.mean()),
        "alpha_max": float(alpha.max()),
        "safe_compose_mae": mae(safe, comp_diff),
        "safe_compose_rmse": safe_rmse,
        "safe_compose_psnr": float(20.0 * np.log10(255.0 / max(safe_rmse, 1e-6))) if safe.any() else float("nan"),
        "white_mask_compose_mae": mae(preserve, comp_diff),
        "core_raw_vs_init_mae": mae(core, raw_diff),
        "halo_raw_vs_init_mae": mae(halo, raw_diff),
        "halo_compose_vs_init_mae": mae(halo, comp_diff),
    }
    return Image.fromarray(comp), Image.fromarray(alpha_vis, mode="L"), metrics


def _label_band(width: int, label: str) -> np.ndarray:
    band = np.zeros((34, width, 3), dtype=np.uint8)
    cv2.putText(band, label, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    return band


def _fit_width(img: Image.Image, width: int) -> np.ndarray:
    if img.width == width:
        return np.array(img.convert("RGB"))
    h = max(1, round(img.height * width / img.width))
    return np.array(img.convert("RGB").resize((width, h), Image.Resampling.BICUBIC))


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
            crop = np.array(img.crop(box).convert("RGB"))
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
    ap.add_argument("--halo-px", action="append", type=int, required=True)
    ap.add_argument("--gamma", action="append", type=float, default=None)
    ap.add_argument("--crop", action="append", default=[])
    ap.add_argument("--overall-width", type=int, default=1024)
    args = ap.parse_args()
    gammas = args.gamma if args.gamma is not None else [1.0]

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
        review_rows.append((f"{name} raw", raw))

        for halo_px in args.halo_px:
            for gamma in gammas:
                comp, alpha_vis, metrics = _compose(init, mask_path, raw_path, halo_px, gamma)
                setting = f"h{halo_px:03d}_g{int(round(gamma * 100)):03d}"
                out_name = f"{name}_{setting}"
                case_dir = out_dir / out_name
                case_dir.mkdir(parents=True, exist_ok=True)

                comp_path = case_dir / f"{out_name}_softcompose.png"
                alpha_path = case_dir / f"{out_name}_alpha.png"
                diag_path = case_dir / f"{out_name}_softcompose_diagnostics.json"
                comp.save(comp_path)
                alpha_vis.save(alpha_path)

                summary = {
                    "name": name,
                    "setting": setting,
                    "init_image": str(init_path),
                    "mask": str(mask_path),
                    "raw_output": str(raw_path),
                    "softcompose_output": str(comp_path),
                    "alpha_image": str(alpha_path),
                    "mask_convention": "white/255 preserves source; black/0 uses DiT360 output",
                    "composition": "raw in black core, distance-feathered raw in halo, source outside halo",
                    **metrics,
                }
                with open(diag_path, "w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2, ensure_ascii=False)
                summaries.append(summary)
                review_rows.append((f"{name} soft {setting}", comp))

    with open(out_dir / "softcompose_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": summaries}, f, indent=2, ensure_ascii=False)
    _save_overall_review(out_dir / "softcompose_overall_review.jpg", init, review_rows, args.overall_width)
    _save_crop_review(out_dir / "softcompose_crop_review.jpg", init, review_rows, crops)
    print(json.dumps({"runs": summaries}, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
