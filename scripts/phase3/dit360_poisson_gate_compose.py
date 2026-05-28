"""Gradient-domain gated composition for DiT360 seam-completion outputs.

DiT360 raw edits can look smoother than strict post-compose because the model
also changes context outside the black mask. This script tests whether that
visual advantage survives a more principled composition: use DiT360 only as a
gradient-domain proposal, then gate the proposal by source structure, raw-vs-
source change, and an explicit fidelity budget.

Mask convention follows the project DiT360 runners:
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
    clone_mode: str
    y_only: bool
    halo_px: int
    clone_halo_px: int
    edge_strength: float
    diff_strength: float
    budget_mae: float
    gamma: float
    core_floor: float
    residual_blur_sigma: float


PRESETS = [
    Preset(
        name="poisson_y_safe",
        clone_mode="mixed",
        y_only=True,
        halo_px=16,
        clone_halo_px=6,
        edge_strength=0.82,
        diff_strength=0.76,
        budget_mae=0.70,
        gamma=1.35,
        core_floor=0.18,
        residual_blur_sigma=0.9,
    ),
    Preset(
        name="poisson_rgb_balanced",
        clone_mode="mixed",
        y_only=False,
        halo_px=20,
        clone_halo_px=8,
        edge_strength=0.68,
        diff_strength=0.58,
        budget_mae=1.50,
        gamma=1.05,
        core_floor=0.30,
        residual_blur_sigma=0.55,
    ),
    Preset(
        name="poisson_mixed_loose",
        clone_mode="normal",
        y_only=False,
        halo_px=24,
        clone_halo_px=10,
        edge_strength=0.55,
        diff_strength=0.45,
        budget_mae=2.50,
        gamma=0.90,
        core_floor=0.42,
        residual_blur_sigma=0.35,
    ),
]


def _load_rgb(path: Path, size: tuple[int, int] | None = None) -> Image.Image:
    img = Image.open(path).convert("RGB")
    if size is not None and img.size != size:
        img = img.resize(size, Image.Resampling.BICUBIC)
    return img


def _load_mask(path: Path, size: tuple[int, int]) -> np.ndarray:
    return np.array(Image.open(path).convert("L").resize(size, Image.Resampling.NEAREST))


def _dilate(mask: np.ndarray, radius_px: int) -> np.ndarray:
    if radius_px <= 0:
        return mask.copy()
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius_px * 2 + 1, radius_px * 2 + 1))
    return cv2.dilate(mask.astype(np.uint8), k).astype(bool)


def _smoothstep(low: float, high: float, values: np.ndarray) -> np.ndarray:
    if high <= low:
        return (values >= high).astype(np.float32)
    t = np.clip((values - low) / (high - low), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _mask_alpha(core: np.ndarray, halo_px: int, gamma: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
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
    return alpha, core, halo


def _edge_gate(init_np: np.ndarray, edge_strength: float) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(init_np.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    scale = np.percentile(mag, 95.0)
    edge = np.zeros_like(mag, dtype=np.float32) if scale <= 1e-6 else np.clip(mag / scale, 0.0, 1.0)
    keep = np.clip(1.0 - edge_strength * edge, 0.0, 1.0).astype(np.float32)
    return keep, edge.astype(np.float32)


def _diff_gate(raw_delta: np.ndarray, diff_strength: float) -> tuple[np.ndarray, np.ndarray]:
    mag = np.sqrt(np.mean(raw_delta * raw_delta, axis=2))
    risk = _smoothstep(7.0, 32.0, mag)
    keep = np.clip(1.0 - diff_strength * risk, 0.0, 1.0).astype(np.float32)
    return keep, risk


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


def _component_clone(src_rgb: np.ndarray, dst_rgb: np.ndarray, mask_bool: np.ndarray, mode: str) -> tuple[np.ndarray, dict[str, int]]:
    if not mask_bool.any():
        return dst_rgb.copy(), {"components": 0, "cloned_components": 0, "failed_components": 0}

    clone_flag = cv2.MIXED_CLONE if mode == "mixed" else cv2.NORMAL_CLONE
    src_bgr = cv2.cvtColor(src_rgb.astype(np.uint8), cv2.COLOR_RGB2BGR)
    out_bgr = cv2.cvtColor(dst_rgb.astype(np.uint8), cv2.COLOR_RGB2BGR)
    labels_n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask_bool.astype(np.uint8), 8)
    cloned = 0
    failed = 0
    for label in range(1, labels_n):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < 16:
            continue
        comp = labels == label
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        if x <= 0 or y <= 0 or x + w >= mask_bool.shape[1] or y + h >= mask_bool.shape[0]:
            comp = comp.copy()
            comp[:1, :] = False
            comp[-1:, :] = False
            comp[:, :1] = False
            comp[:, -1:] = False
            if comp.sum() < 16:
                continue
        cx, cy = centroids[label]
        center = (int(round(cx)), int(round(cy)))
        comp_u8 = np.where(comp, 255, 0).astype(np.uint8)
        try:
            out_bgr = cv2.seamlessClone(src_bgr, out_bgr, comp_u8, center, clone_flag)
            cloned += 1
        except cv2.error:
            failed += 1
    out_rgb = cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB)
    return out_rgb, {"components": int(labels_n - 1), "cloned_components": cloned, "failed_components": failed}


def _poisson_proposal(init_np: np.ndarray, raw_np: np.ndarray, clone_mask: np.ndarray, preset: Preset) -> tuple[np.ndarray, dict[str, int]]:
    if not preset.y_only:
        return _component_clone(raw_np, init_np, clone_mask, preset.clone_mode)

    init_ycc = cv2.cvtColor(init_np.astype(np.uint8), cv2.COLOR_RGB2YCrCb)
    raw_ycc = cv2.cvtColor(raw_np.astype(np.uint8), cv2.COLOR_RGB2YCrCb)
    src = np.repeat(raw_ycc[..., :1], 3, axis=2)
    dst = np.repeat(init_ycc[..., :1], 3, axis=2)
    cloned_y3, diag = _component_clone(src, dst, clone_mask, preset.clone_mode)
    out_ycc = init_ycc.copy()
    out_ycc[..., 0] = cloned_y3[..., 0]
    return cv2.cvtColor(out_ycc, cv2.COLOR_YCrCb2RGB), diag


def _compose_one(init: Image.Image, mask_path: Path, raw_path: Path, preset: Preset) -> tuple[Image.Image, Image.Image, dict]:
    raw = _load_rgb(raw_path, init.size)
    init_np = np.asarray(init, dtype=np.float32)
    raw_np = np.asarray(raw, dtype=np.float32)
    mask = _load_mask(mask_path, init.size)
    preserve = mask >= 128
    core = ~preserve
    clone_mask = _dilate(core, preset.clone_halo_px)

    proposal_np, clone_diag = _poisson_proposal(init_np, raw_np, clone_mask, preset)
    proposal_np = proposal_np.astype(np.float32)
    if preset.residual_blur_sigma > 0:
        k = max(3, int(round(preset.residual_blur_sigma * 6.0)) | 1)
        proposal_np = cv2.GaussianBlur(proposal_np, (k, k), preset.residual_blur_sigma, borderType=cv2.BORDER_REFLECT)

    raw_delta = raw_np - init_np
    prop_delta = proposal_np - init_np
    alpha, core, halo = _mask_alpha(core, preset.halo_px, preset.gamma)
    edge_keep, edge = _edge_gate(init_np, preset.edge_strength)
    diff_keep, diff_risk = _diff_gate(raw_delta, preset.diff_strength)
    evidence_keep = np.clip(edge_keep * diff_keep, 0.0, 1.0).astype(np.float32)
    applied_alpha = alpha * evidence_keep
    applied_alpha = np.maximum(applied_alpha, preset.core_floor * core.astype(np.float32))

    proposed = prop_delta * applied_alpha[..., None]
    preserve_delta = proposed.copy()
    preserve_delta[core] = 0.0
    scale = _budget_scale(preserve_delta, preserve, preset.budget_mae)
    proposed_scaled = proposed.copy()
    proposed_scaled[preserve] *= scale
    out_np = np.clip(init_np + proposed_scaled, 0.0, 255.0).astype(np.uint8)

    out_diff = out_np.astype(np.float32) - init_np
    raw_diff = raw_np - init_np
    prop_diff = proposal_np - init_np
    edge_region = edge > 0.5
    boundary = (cv2.dilate(core.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) & (~core)
    final_alpha = np.clip(np.sqrt(np.mean(proposed_scaled * proposed_scaled, axis=2)) / (np.sqrt(np.mean(prop_delta * prop_delta, axis=2)) + 1e-6), 0.0, 1.0)
    final_alpha[np.sqrt(np.mean(prop_delta * prop_delta, axis=2)) <= 1e-5] = 0.0

    preserve_rmse = _rmse(preserve, out_diff)
    raw_preserve_rmse = _rmse(preserve, raw_diff)
    metrics = {
        "preset": preset.__dict__,
        "mask": str(mask_path),
        "raw_output": str(raw_path),
        **clone_diag,
        "core_fraction": float(core.mean()),
        "halo_fraction": float(halo.mean()),
        "clone_mask_fraction": float(clone_mask.mean()),
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
        "proposal_preserve_mae": _mae(preserve, prop_diff),
        "core_output_vs_source_mae": _mae(core, out_diff),
        "core_raw_vs_source_mae": _mae(core, raw_diff),
        "core_proposal_vs_source_mae": _mae(core, prop_diff),
        "halo_output_vs_source_mae": _mae(halo, out_diff),
        "halo_raw_vs_source_mae": _mae(halo, raw_diff),
        "edge_region_output_vs_source_mae": _mae(edge_region, out_diff),
        "edge_region_raw_vs_source_mae": _mae(edge_region, raw_diff),
        "boundary_output_vs_source_mae": _mae(boundary, out_diff),
        "boundary_raw_vs_source_mae": _mae(boundary, raw_diff),
        "high_edge_fraction_in_core_or_halo": float(((core | halo) & edge_region).sum() / max(1, int((core | halo).sum()))),
        "high_diff_fraction_in_core_or_halo": float(((core | halo) & (diff_risk > 0.5)).sum() / max(1, int((core | halo).sum()))),
    }
    alpha_img = Image.fromarray(np.clip(final_alpha * 255.0, 0, 255).astype(np.uint8), mode="L")
    return Image.fromarray(out_np), alpha_img, metrics


def _label_band(width: int, label: str) -> np.ndarray:
    band = np.zeros((34, width, 3), dtype=np.uint8)
    img = Image.fromarray(band)
    draw = ImageDraw.Draw(img)
    draw.text((10, 9), label, fill=(255, 255, 255))
    return np.asarray(img)


def _fit_width(img: Image.Image, width: int) -> np.ndarray:
    if img.width == width:
        return np.asarray(img)
    height = max(1, int(round(img.height * width / img.width)))
    return np.asarray(img.resize((width, height), Image.Resampling.BICUBIC))


def _save_overall_review(path: Path, init: Image.Image, rows: list[tuple[str, Image.Image]], width: int, quality: int) -> None:
    panels: list[np.ndarray] = [_label_band(width, "source hard_select"), _fit_width(init, width)]
    for label, img in rows:
        panels.append(_label_band(width, label))
        panels.append(_fit_width(img, width))
    Image.fromarray(np.vstack(panels)).save(path, quality=quality)


def _find_auto_crops(mask: np.ndarray, n: int = 3, half_w: int = 260) -> list[tuple[int, int, int, int]]:
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
    return [(max(0, x - half_w), r0, min(w, x + half_w), r1) for x in sorted(peaks)]


def _save_crop_review(
    path: Path,
    init: Image.Image,
    raw: Image.Image,
    mask: np.ndarray,
    rows: list[tuple[str, Image.Image]],
    width: int,
    quality: int,
) -> None:
    panel_rows: list[np.ndarray] = []
    for idx, box in enumerate(_find_auto_crops(mask)):
        x0, _y0, x1, _y1 = box
        crop_rows: list[tuple[str, Image.Image]] = [("source", init.crop(box)), ("raw", raw.crop(box))]
        crop_rows.extend((label, img.crop(box)) for label, img in rows)
        for label, crop in crop_rows:
            panel_rows.append(_label_band(width, f"crop{idx} x{x0}-{x1} {label}"))
            panel_rows.append(_fit_width(crop, width))
    stacked = np.vstack(panel_rows)
    max_jpeg_dim = 64000
    if stacked.shape[0] > max_jpeg_dim:
        scale = max_jpeg_dim / float(stacked.shape[0])
        new_size = (max(1, int(round(stacked.shape[1] * scale))), max_jpeg_dim)
        stacked = np.asarray(Image.fromarray(stacked).resize(new_size, Image.Resampling.BICUBIC))
    Image.fromarray(stacked).save(path, quality=quality)


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

        rows: list[tuple[str, Image.Image]] = []
        case_summaries: list[dict] = []
        for preset in presets:
            out_img, alpha_img, metrics = _compose_one(init, Path(case["mask"]), Path(case["raw_output"]), preset)
            stem = f"{case_name}_{preset.name}"
            out_path = case_dir / f"{stem}.png"
            alpha_path = case_dir / f"{stem}_alpha.png"
            diag_path = case_dir / f"{stem}_diagnostics.json"
            out_img.save(out_path)
            alpha_img.save(alpha_path)
            summary = {
                **case,
                "preset_name": preset.name,
                "output": str(out_path),
                "alpha": str(alpha_path),
                **metrics,
            }
            with open(diag_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            case_summaries.append(summary)
            all_summaries.append(summary)
            rows.append((preset.name, out_img))

        with open(case_dir / f"{case_name}_poisson_gate_summary.json", "w", encoding="utf-8") as f:
            json.dump({"runs": case_summaries}, f, indent=2, ensure_ascii=False)
        _save_overall_review(
            case_dir / f"{case_name}_poisson_gate_overall_q{args.jpg_quality}_w{args.overall_width}.jpg",
            init,
            [("raw", raw), *rows],
            args.overall_width,
            args.jpg_quality,
        )
        _save_crop_review(
            case_dir / f"{case_name}_poisson_gate_crops_q{args.jpg_quality}_w{args.crop_width}.jpg",
            init,
            raw,
            mask,
            rows,
            args.crop_width,
            args.jpg_quality,
        )

    with open(out_dir / "poisson_gate_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": all_summaries}, f, indent=2, ensure_ascii=False)
    print(json.dumps({"out_dir": str(out_dir), "n_runs": len(all_summaries)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
