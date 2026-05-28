"""Use DiT360 only as a low-frequency seam harmonization prior.

The raw DiT360 output sometimes makes seams look smoother, but its high
frequency content can hallucinate lane markings, vehicle contours, and wall
edges. This post-process keeps high-frequency evidence from the original
hard_select panorama and transfers only a blurred RGB residual from the DiT360
output near the mask.

It is intentionally conservative:
  output = source + alpha * edge_gate * (blur(raw) - blur(source))

No raw DiT high-frequency pixels are copied into the result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def _parse_kv(text: str, required: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in text.split(","):
        key, value = part.split("=", 1)
        out[key.strip()] = value.strip()
    for key in required:
        if key not in out:
            raise ValueError(f"item must include {key}: {text}")
    return out


def _parse_case(text: str) -> dict[str, str]:
    return _parse_kv(text, ("name", "mask", "raw"))


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
        alpha = np.clip(1.0 - dist / float(max(1, halo_px)), 0.0, 1.0).astype(np.float32)
        alpha[core] = 1.0
    if gamma != 1.0:
        alpha = np.power(alpha, gamma).astype(np.float32)
        alpha[core] = 1.0
    halo = (alpha > 1e-6) & (~core)
    safe = alpha <= 1e-6
    return alpha, halo, safe


def _edge_keep(init_np: np.ndarray, blur_px: int, edge_strength: float) -> np.ndarray:
    gray = cv2.cvtColor(init_np.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    if blur_px > 0:
        k = blur_px * 2 + 1
        gray = cv2.GaussianBlur(gray, (k, k), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    scale = float(np.percentile(mag, 97.0))
    if scale < 1e-6:
        return np.ones_like(mag, dtype=np.float32)
    edge = np.clip(mag / scale, 0.0, 1.0).astype(np.float32)
    return np.clip(1.0 - edge_strength * edge, 0.0, 1.0).astype(np.float32)


def _blur_rgb(rgb: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return rgb.astype(np.float32)
    k = int(round(sigma * 6.0)) | 1
    return cv2.GaussianBlur(rgb.astype(np.float32), (k, k), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_REFLECT)


def _mae(region: np.ndarray, arr: np.ndarray) -> float:
    if not region.any():
        return float("nan")
    return float(np.mean(np.abs(arr[region])))


def _compose(
    init: Image.Image,
    mask_path: Path,
    raw_path: Path,
    halo_px: int,
    sigma: float,
    strength: float,
    edge_strength: float,
    gamma: float,
    edge_blur_px: int,
) -> tuple[Image.Image, Image.Image, dict[str, float | int | str]]:
    raw = _load_rgb(raw_path, init.size)
    init_np = np.array(init, dtype=np.float32)
    raw_np = np.array(raw, dtype=np.float32)
    mask = _load_mask(mask_path, init.size)
    alpha, halo, safe = _alpha_from_mask(mask, halo_px, gamma)
    core = mask < 128
    preserve = mask >= 128
    keep = _edge_keep(init_np, edge_blur_px, edge_strength)

    low_delta = _blur_rgb(raw_np, sigma) - _blur_rgb(init_np, sigma)
    final_alpha = np.clip(alpha * keep * strength, 0.0, 1.0).astype(np.float32)
    out_np = np.clip(init_np + low_delta * final_alpha[..., None], 0.0, 255.0).astype(np.uint8)
    diff = out_np.astype(np.float32) - init_np
    raw_diff = raw_np - init_np
    low_diff = low_delta * final_alpha[..., None]
    alpha_vis = np.clip(final_alpha * 255.0, 0, 255).astype(np.uint8)

    metrics: dict[str, float | int | str] = {
        "halo_px": int(halo_px),
        "sigma": float(sigma),
        "strength": float(strength),
        "edge_strength": float(edge_strength),
        "gamma": float(gamma),
        "edge_blur_px": int(edge_blur_px),
        "core_fraction": float(core.mean()),
        "halo_fraction": float(halo.mean()),
        "safe_fraction": float(safe.mean()),
        "modified_fraction": float((final_alpha > 1e-6).mean()),
        "alpha_mean": float(final_alpha.mean()),
        "alpha_core_mean": float(final_alpha[core].mean()) if core.any() else float("nan"),
        "alpha_halo_mean": float(final_alpha[halo].mean()) if halo.any() else float("nan"),
        "preserve_mae": _mae(preserve, diff),
        "safe_mae": _mae(safe, diff),
        "core_output_vs_source_mae": _mae(core, diff),
        "core_raw_vs_source_mae": _mae(core, raw_diff),
        "core_lowfreq_applied_mae": _mae(core, low_diff),
        "edge_region_output_vs_source_mae": _mae(keep < 0.5, diff),
        "edge_region_raw_vs_source_mae": _mae(keep < 0.5, raw_diff),
    }
    return Image.fromarray(out_np), Image.fromarray(alpha_vis, mode="L"), metrics


def _label_band(width: int, label: str) -> np.ndarray:
    band = np.zeros((34, width, 3), dtype=np.uint8)
    cv2.putText(band, label, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    return band


def _fit_width(img: Image.Image, width: int) -> np.ndarray:
    if img.width == width:
        return np.array(img.convert("RGB"))
    h = max(1, round(img.height * width / img.width))
    return np.array(img.convert("RGB").resize((width, h), Image.Resampling.BICUBIC))


def _save_review(path: Path, init: Image.Image, rows: list[tuple[str, Image.Image]], width: int) -> None:
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
        parts = []
        for crop_name, box in crops:
            crop = np.array(img.crop(box).convert("RGB"))
            parts.append(np.vstack([_label_band(crop.shape[1], f"{label} | {crop_name}"), crop]))
        max_h = max(p.shape[0] for p in parts)
        padded = []
        for part in parts:
            if part.shape[0] < max_h:
                part = np.vstack([part, np.zeros((max_h - part.shape[0], part.shape[1], 3), dtype=np.uint8)])
            padded.append(part)
        rendered_rows.append(np.hstack(padded))
    Image.fromarray(np.vstack(rendered_rows)).save(path, quality=92)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-image", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--case", action="append", required=True)
    ap.add_argument("--halo-px", action="append", type=int, required=True)
    ap.add_argument("--sigma", action="append", type=float, required=True)
    ap.add_argument("--strength", action="append", type=float, required=True)
    ap.add_argument("--edge-strength", type=float, default=0.70)
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--edge-blur-px", type=int, default=3)
    ap.add_argument("--crop", action="append", default=[])
    ap.add_argument("--overall-width", type=int, default=1024)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    init = _load_rgb(Path(args.init_image))
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
            for sigma in args.sigma:
                for strength in args.strength:
                    out, alpha, metrics = _compose(
                        init,
                        mask_path,
                        raw_path,
                        halo_px=halo_px,
                        sigma=sigma,
                        strength=strength,
                        edge_strength=args.edge_strength,
                        gamma=args.gamma,
                        edge_blur_px=args.edge_blur_px,
                    )
                    setting = f"h{halo_px:03d}_s{int(round(sigma)):03d}_a{int(round(strength * 100)):03d}"
                    out_name = f"{name}_{setting}"
                    case_dir = out_dir / out_name
                    case_dir.mkdir(parents=True, exist_ok=True)
                    out_path = case_dir / f"{out_name}_lowfreq.png"
                    alpha_path = case_dir / f"{out_name}_alpha.png"
                    diag_path = case_dir / f"{out_name}_diagnostics.json"
                    out.save(out_path)
                    alpha.save(alpha_path)
                    summary = {
                        "name": name,
                        "setting": setting,
                        "init_image": str(args.init_image),
                        "mask": str(mask_path),
                        "raw_output": str(raw_path),
                        "lowfreq_output": str(out_path),
                        "alpha_image": str(alpha_path),
                        "composition": "source high-frequency plus DiT360 low-frequency residual near mask",
                        **metrics,
                    }
                    with open(diag_path, "w", encoding="utf-8") as f:
                        json.dump(summary, f, indent=2, ensure_ascii=False)
                    summaries.append(summary)
                    review_rows.append((f"{name} lowfreq {setting}", out))

    with open(out_dir / "lowfreq_harmonize_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": summaries}, f, indent=2, ensure_ascii=False)
    _save_review(out_dir / "lowfreq_harmonize_overall_review.jpg", init, review_rows, args.overall_width)
    _save_crop_review(out_dir / "lowfreq_harmonize_crop_review.jpg", init, review_rows, crops)
    print(json.dumps({"runs": summaries}, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
