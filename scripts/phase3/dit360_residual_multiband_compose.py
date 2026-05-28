"""Residual multiband composition for DiT360 seam-completion outputs.

This script treats DiT360 as a proposal generator, not as a replacement for the
source panorama. It decomposes the DiT residual

    residual = raw_dit360 - hard_select_source

into low / mid / high frequency bands and accepts each band under different
source-evidence gates. The goal is to keep the global seam harmonization that
makes raw DiT360 look smoother while rejecting high-frequency hallucination near
lane markings, cars, pedestrians, and building edges.

Mask convention follows the DiT360 runners used in this project:
  white/255 = preserve source
  black/0   = generate/fill
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class Preset:
    name: str
    halo_px: int
    sigma_low: float
    sigma_mid: float
    low_strength: float
    mid_strength: float
    high_strength: float
    edge_strength: float
    diff_strength: float
    budget_mae: float
    gamma: float
    core_raw_floor: float


PRESETS = [
    Preset(
        name="mb_safe",
        halo_px=18,
        sigma_low=20.0,
        sigma_mid=4.0,
        low_strength=0.95,
        mid_strength=0.22,
        high_strength=0.00,
        edge_strength=0.72,
        diff_strength=0.65,
        budget_mae=1.0,
        gamma=1.20,
        core_raw_floor=0.12,
    ),
    Preset(
        name="mb_balanced",
        halo_px=20,
        sigma_low=18.0,
        sigma_mid=4.0,
        low_strength=1.00,
        mid_strength=0.38,
        high_strength=0.08,
        edge_strength=0.70,
        diff_strength=0.60,
        budget_mae=2.0,
        gamma=1.05,
        core_raw_floor=0.22,
    ),
    Preset(
        name="mb_loose",
        halo_px=24,
        sigma_low=16.0,
        sigma_mid=3.5,
        low_strength=1.00,
        mid_strength=0.55,
        high_strength=0.16,
        edge_strength=0.58,
        diff_strength=0.48,
        budget_mae=3.0,
        gamma=0.95,
        core_raw_floor=0.35,
    ),
]


def _load_rgb(path: Path, size: tuple[int, int] | None = None) -> Image.Image:
    img = Image.open(path).convert("RGB")
    if size is not None and img.size != size:
        img = img.resize(size, Image.Resampling.BICUBIC)
    return img


def _load_mask(path: Path, size: tuple[int, int]) -> np.ndarray:
    return np.array(Image.open(path).convert("L").resize(size, Image.Resampling.NEAREST))


def _blur_rgb(rgb: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return rgb.astype(np.float32)
    k = max(3, int(round(sigma * 6.0)) | 1)
    return cv2.GaussianBlur(
        rgb.astype(np.float32),
        (k, k),
        sigmaX=sigma,
        sigmaY=sigma,
        borderType=cv2.BORDER_REFLECT,
    )


def _smoothstep(low: float, high: float, values: np.ndarray) -> np.ndarray:
    if high <= low:
        return (values >= high).astype(np.float32)
    t = np.clip((values - low) / (high - low), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _mask_alpha(mask: np.ndarray, halo_px: int, gamma: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    core = mask < 128
    preserve = ~core
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
    halo = (alpha > 1e-6) & preserve
    safe = alpha <= 1e-6
    return alpha, core, halo, safe


def _edge_gate(init_np: np.ndarray, edge_strength: float, blur_px: int = 3) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(init_np.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    if blur_px > 0:
        k = blur_px * 2 + 1
        gray = cv2.GaussianBlur(gray, (k, k), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    scale = np.percentile(mag, 95.0)
    if scale <= 1e-6:
        edge = np.zeros_like(mag, dtype=np.float32)
    else:
        edge = np.clip(mag / scale, 0.0, 1.0).astype(np.float32)
    keep = np.clip(1.0 - edge_strength * edge, 0.0, 1.0).astype(np.float32)
    return keep, edge


def _diff_gate(raw_delta: np.ndarray, diff_strength: float, diff_low: float = 8.0, diff_high: float = 34.0) -> tuple[np.ndarray, np.ndarray]:
    mag = np.sqrt(np.mean(raw_delta * raw_delta, axis=2))
    diff_risk = _smoothstep(diff_low, diff_high, mag)
    keep = np.clip(1.0 - diff_strength * diff_risk, 0.0, 1.0).astype(np.float32)
    return keep, diff_risk


def _mae(region: np.ndarray, arr: np.ndarray) -> float:
    if not region.any():
        return float("nan")
    return float(np.abs(arr[region]).mean())


def _rmse(region: np.ndarray, arr: np.ndarray) -> float:
    if not region.any():
        return float("nan")
    return float(np.sqrt(np.mean(arr[region] * arr[region])))


def _psnr_from_rmse(rmse: float) -> float:
    if not math.isfinite(rmse):
        return float("nan")
    return float(20.0 * np.log10(255.0 / max(rmse, 1e-6)))


def _budget_scale(delta: np.ndarray, region: np.ndarray, budget_mae: float) -> float:
    if budget_mae <= 0 or not region.any():
        return 1.0
    proposed_mae = _mae(region, delta)
    if not math.isfinite(proposed_mae) or proposed_mae <= budget_mae:
        return 1.0
    return float(max(0.0, min(1.0, budget_mae / max(proposed_mae, 1e-6))))


def _compose_one(init: Image.Image, mask_path: Path, raw_path: Path, preset: Preset) -> tuple[Image.Image, Image.Image, dict]:
    raw = _load_rgb(raw_path, init.size)
    init_np = np.array(init, dtype=np.float32)
    raw_np = np.array(raw, dtype=np.float32)
    mask = _load_mask(mask_path, init.size)
    preserve = mask >= 128

    raw_delta = raw_np - init_np
    alpha, core, halo, safe = _mask_alpha(mask, preset.halo_px, preset.gamma)
    edge_keep, edge = _edge_gate(init_np, preset.edge_strength)
    diff_keep, diff_risk = _diff_gate(raw_delta, preset.diff_strength)
    evidence_keep = np.clip(edge_keep * diff_keep, 0.0, 1.0).astype(np.float32)

    low = _blur_rgb(raw_delta, preset.sigma_low)
    mid_base = _blur_rgb(raw_delta, preset.sigma_mid)
    mid = mid_base - low
    high = raw_delta - mid_base

    low_alpha = alpha * (0.45 + 0.55 * evidence_keep)
    mid_alpha = alpha * np.power(evidence_keep, 1.35)
    high_alpha = alpha * np.power(evidence_keep, 2.6)
    high_alpha[halo] *= 0.25
    high_alpha[safe] = 0.0

    # In the black core, allow a small amount of raw detail even when the edge
    # gate is strict; otherwise narrow r008 masks can leave the original hard
    # seam almost unchanged.
    core_floor = preset.core_raw_floor * core.astype(np.float32)
    low_alpha = np.maximum(low_alpha, core_floor)
    mid_alpha = np.maximum(mid_alpha, core_floor * 0.70)
    high_alpha = np.maximum(high_alpha, core_floor * 0.30)

    proposed = (
        low * (preset.low_strength * low_alpha[..., None])
        + mid * (preset.mid_strength * mid_alpha[..., None])
        + high * (preset.high_strength * high_alpha[..., None])
    )

    # Keep the preserve-region modification under an explicit fidelity budget,
    # without scaling the black generated core.
    preserve_delta = proposed.copy()
    preserve_delta[core] = 0.0
    scale = _budget_scale(preserve_delta, preserve, preset.budget_mae)
    proposed_scaled = proposed.copy()
    proposed_scaled[preserve] *= scale

    out_np = np.clip(init_np + proposed_scaled, 0.0, 255.0).astype(np.uint8)
    out_diff = out_np.astype(np.float32) - init_np
    raw_diff = raw_np - init_np

    final_alpha = np.zeros(alpha.shape, dtype=np.float32)
    denom = np.sqrt(np.mean(raw_delta * raw_delta, axis=2)) + 1e-6
    applied = np.sqrt(np.mean(proposed_scaled * proposed_scaled, axis=2))
    final_alpha = np.clip(applied / denom, 0.0, 1.0)
    final_alpha[denom <= 1e-5] = 0.0

    preserve_rmse = _rmse(preserve, out_diff)
    raw_preserve_rmse = _rmse(preserve, raw_diff)
    edge_region = edge > 0.5
    metrics = {
        "preset": preset.__dict__,
        "mask": str(mask_path),
        "raw_output": str(raw_path),
        "core_fraction": float(core.mean()),
        "halo_fraction": float(halo.mean()),
        "safe_fraction": float(safe.mean()),
        "modified_fraction": float((np.abs(out_diff).mean(axis=2) > 0.5).mean()),
        "budget_scale": float(scale),
        "alpha_mean": float(final_alpha.mean()),
        "alpha_core_mean": float(final_alpha[core].mean()) if core.any() else float("nan"),
        "alpha_halo_mean": float(final_alpha[halo].mean()) if halo.any() else float("nan"),
        "preserve_mae": _mae(preserve, out_diff),
        "preserve_rmse": preserve_rmse,
        "preserve_psnr": _psnr_from_rmse(preserve_rmse),
        "raw_preserve_mae": _mae(preserve, raw_diff),
        "raw_preserve_rmse": raw_preserve_rmse,
        "raw_preserve_psnr": _psnr_from_rmse(raw_preserve_rmse),
        "core_output_vs_source_mae": _mae(core, out_diff),
        "core_raw_vs_source_mae": _mae(core, raw_diff),
        "halo_output_vs_source_mae": _mae(halo, out_diff),
        "halo_raw_vs_source_mae": _mae(halo, raw_diff),
        "safe_output_vs_source_mae": _mae(safe, out_diff),
        "safe_raw_vs_source_mae": _mae(safe, raw_diff),
        "edge_region_output_vs_source_mae": _mae(edge_region, out_diff),
        "edge_region_raw_vs_source_mae": _mae(edge_region, raw_diff),
        "high_edge_fraction_in_core_or_halo": float(((core | halo) & edge_region).sum() / max(1, int((core | halo).sum()))),
        "high_diff_fraction_in_core_or_halo": float(((core | halo) & (diff_risk > 0.5)).sum() / max(1, int((core | halo).sum()))),
    }
    return Image.fromarray(out_np), Image.fromarray(np.clip(final_alpha * 255.0, 0, 255).astype(np.uint8), mode="L"), metrics


def _label_band(width: int, label: str) -> np.ndarray:
    band = np.zeros((34, width, 3), dtype=np.uint8)
    band[:] = (0, 0, 0)
    img = Image.fromarray(band)
    draw = ImageDraw.Draw(img)
    draw.text((10, 9), label, fill=(255, 255, 255))
    return np.array(img)


def _fit_width(img: Image.Image, width: int) -> np.ndarray:
    if img.width == width:
        return np.asarray(img)
    height = max(1, int(round(img.height * width / img.width)))
    return np.asarray(img.resize((width, height), Image.Resampling.BICUBIC))


def _save_overall_review(path: Path, init: Image.Image, rows: list[tuple[str, Image.Image]], width: int, quality: int) -> None:
    panels: list[np.ndarray] = []
    panels.append(_label_band(width, "source hard_select"))
    panels.append(_fit_width(init, width))
    for label, img in rows:
        panels.append(_label_band(width, label))
        panels.append(_fit_width(img, width))
    Image.fromarray(np.vstack(panels)).save(path, quality=quality)


def _find_auto_crops(mask: np.ndarray, n: int = 4, half_w: int = 110) -> list[tuple[int, int, int, int]]:
    h, w = mask.shape
    core = mask < 128
    r0 = int(h * 0.22)
    r1 = int(h * 0.70)
    col = core[r0:r1].sum(axis=0).astype(np.float32)
    if col.max() <= 0:
        return [(0, r0, w, r1)]
    col = cv2.GaussianBlur(col[None, :], (0, 1), sigmaX=8.0).ravel()
    peaks: list[int] = []
    work = col.copy()
    for _ in range(n):
        x = int(work.argmax())
        if work[x] <= 0:
            break
        peaks.append(x)
        lo = max(0, x - half_w)
        hi = min(w, x + half_w + 1)
        work[lo:hi] = 0
    crops = []
    for x in sorted(peaks):
        x0 = max(0, x - half_w)
        x1 = min(w, x + half_w)
        crops.append((x0, r0, x1, r1))
    return crops or [(0, r0, w, r1)]


def _save_crop_review(
    path: Path,
    init: Image.Image,
    raw: Image.Image,
    mask: np.ndarray,
    rows: list[tuple[str, Image.Image]],
    width: int,
    quality: int,
) -> None:
    crops = _find_auto_crops(mask)
    panel_rows: list[np.ndarray] = []
    for idx, box in enumerate(crops):
        x0, y0, x1, y1 = box
        crop_rows: list[tuple[str, Image.Image]] = [("source", init.crop(box)), ("raw", raw.crop(box))]
        crop_rows.extend((label, img.crop(box)) for label, img in rows)
        for label, crop in crop_rows:
            panel_rows.append(_label_band(width, f"crop{idx} x{x0}-{x1} {label}"))
            panel_rows.append(_fit_width(crop, width))
    Image.fromarray(np.vstack(panel_rows)).save(path, quality=quality)


def _load_cases(summary_paths: Iterable[Path]) -> list[dict]:
    cases: list[dict] = []
    for summary_path in summary_paths:
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for run in data.get("runs", []):
            cases.append(
                {
                    "name": run["name"],
                    "init_image": run["init_image"],
                    "mask": run["mask"],
                    "raw_output": run["output"],
                    "source_summary": str(summary_path),
                    "raw_preserve_mae_reported": run.get("preserve_mae"),
                    "raw_preserve_psnr_reported": run.get("preserve_psnr"),
                }
            )
    return cases


def _parse_preset_names(text: str) -> set[str] | None:
    if not text:
        return None
    return {part.strip() for part in text.split(",") if part.strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="append", required=True, help="DiT360 batch_summary.json path. Can repeat.")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--preset", default="", help="Comma-separated preset names. Default: all.")
    ap.add_argument("--overall-width", type=int, default=900)
    ap.add_argument("--crop-width", type=int, default=1300)
    ap.add_argument("--jpg-quality", type=int, default=60)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    preset_filter = _parse_preset_names(args.preset)
    presets = [p for p in PRESETS if preset_filter is None or p.name in preset_filter]
    if not presets:
        raise ValueError(f"No matching presets for {args.preset!r}")

    cases = _load_cases(Path(p) for p in args.summary)
    if not cases:
        raise ValueError("No runs found in summaries")

    all_summaries: list[dict] = []
    for case in cases:
        case_name = case["name"]
        case_dir = out_dir / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        init = _load_rgb(Path(case["init_image"]))
        raw = _load_rgb(Path(case["raw_output"]), init.size)
        mask = _load_mask(Path(case["mask"]), init.size)

        review_rows: list[tuple[str, Image.Image]] = [("raw DiT360", raw)]
        case_runs: list[dict] = []
        for preset in presets:
            out, alpha, metrics = _compose_one(init, Path(case["mask"]), Path(case["raw_output"]), preset)
            out_name = f"{case_name}_{preset.name}"
            out_path = case_dir / f"{out_name}.png"
            alpha_path = case_dir / f"{out_name}_alpha.png"
            diag_path = case_dir / f"{out_name}_diagnostics.json"
            out.save(out_path)
            alpha.save(alpha_path)
            record = {
                "name": out_name,
                "case": case,
                "output": str(out_path),
                "alpha": str(alpha_path),
                **metrics,
            }
            with open(diag_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
            case_runs.append(record)
            all_summaries.append(record)
            review_rows.append((preset.name, out))

        _save_overall_review(
            case_dir / f"{case_name}_residual_multiband_overall_q{args.jpg_quality}_w{args.overall_width}.jpg",
            init,
            review_rows,
            args.overall_width,
            args.jpg_quality,
        )
        _save_crop_review(
            case_dir / f"{case_name}_residual_multiband_crops_q{args.jpg_quality}_w{args.crop_width}.jpg",
            init,
            raw,
            mask,
            [(p.name, Image.open(case_dir / f"{case_name}_{p.name}.png").convert("RGB")) for p in presets],
            args.crop_width,
            args.jpg_quality,
        )
        with open(case_dir / f"{case_name}_residual_multiband_summary.json", "w", encoding="utf-8") as f:
            json.dump({"runs": case_runs}, f, indent=2, ensure_ascii=False)

    with open(out_dir / "residual_multiband_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": all_summaries}, f, indent=2, ensure_ascii=False)

    print(json.dumps({"out_dir": str(out_dir), "n_runs": len(all_summaries)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
