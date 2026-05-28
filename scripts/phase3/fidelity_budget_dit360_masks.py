"""Fidelity-budget composition for DiT360 seam completion outputs.

Raw DiT360 often looks smoother than hard post-compose because it changes a
small context region outside the black generation mask. Hard post-compose is
perfectly faithful outside the mask, but it can recreate a hard seam between
the generated strip and the original panorama.

This utility tests a middle ground:
  - black mask core uses DiT360 strongly
  - a local halo feathers the generated strip
  - outside the halo, a small raw residual can be kept if it stays under a
    measured preserve-region MAE budget
  - an optional source-edge gate suppresses residuals near strong evidence

Mask convention:
  white/255 = preserve source
  black/0   = generate/fill
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


def _smoothstep(low: float, high: float, values: np.ndarray) -> np.ndarray:
    if high <= low:
        return (values >= high).astype(np.float32)
    x = np.clip((values - low) / (high - low), 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def _edge_gate(init_np: np.ndarray, blur_px: int, edge_power: float) -> np.ndarray:
    gray = cv2.cvtColor(init_np.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    if blur_px > 0:
        k = blur_px * 2 + 1
        gray = cv2.GaussianBlur(gray, (k, k), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    scale = float(np.percentile(mag, 97.0))
    if scale < 1e-6:
        return np.zeros_like(mag, dtype=np.float32)
    gate = np.clip(mag / scale, 0.0, 1.0).astype(np.float32)
    if edge_power != 1.0:
        gate = np.power(gate, edge_power).astype(np.float32)
    return gate


def _mae(region: np.ndarray, arr: np.ndarray) -> float:
    if not region.any():
        return float("nan")
    return float(np.mean(np.abs(arr[region])))


def _rmse(region: np.ndarray, arr: np.ndarray) -> float:
    if not region.any():
        return float("nan")
    return float(np.sqrt(np.mean(arr[region] * arr[region])))


def _budget_scale(delta: np.ndarray, region: np.ndarray, alpha: np.ndarray, budget_mae: float) -> float:
    if not region.any():
        return 0.0
    proposed = delta * alpha[..., None]
    proposed_mae = _mae(region, proposed)
    if not np.isfinite(proposed_mae) or proposed_mae <= 1e-6:
        return 1.0
    return float(min(1.0, max(0.0, budget_mae / proposed_mae)))


def _compose(
    init: Image.Image,
    mask_path: Path,
    raw_path: Path,
    halo_px: int,
    gamma: float,
    budget_mae: float,
    residual_cap: float,
    edge_strength: float,
    diff_low: float,
    diff_high: float,
    diff_strength: float,
    blur_px: int,
) -> tuple[Image.Image, Image.Image, Image.Image, dict[str, float | int | str]]:
    raw = _load_rgb(raw_path, init.size)
    init_np = np.array(init, dtype=np.float32)
    raw_np = np.array(raw, dtype=np.float32)
    mask = _load_mask(mask_path, init.size)
    core_alpha, halo, safe = _alpha_from_mask(mask, halo_px, gamma)
    core = mask < 128
    preserve = mask >= 128

    edge = _edge_gate(init_np, blur_px=blur_px, edge_power=1.0)
    raw_delta = raw_np - init_np
    rgb_diff = np.mean(np.abs(raw_delta), axis=2).astype(np.float32)
    diff_gate = _smoothstep(diff_low, diff_high, rgb_diff)

    evidence_keep = np.ones_like(core_alpha, dtype=np.float32)
    evidence_keep *= 1.0 - edge_strength * edge
    evidence_keep *= 1.0 - diff_strength * diff_gate
    evidence_keep = np.clip(evidence_keep, 0.0, 1.0)

    residual_alpha = residual_cap * evidence_keep
    residual_alpha[~safe] = 0.0
    scale = _budget_scale(raw_delta, preserve & safe, residual_alpha, budget_mae)
    residual_alpha *= scale

    local_alpha = core_alpha.copy()
    local_alpha[halo] *= np.maximum(0.20, evidence_keep[halo])
    final_alpha = np.maximum(local_alpha, residual_alpha).astype(np.float32)

    comp_np = np.clip(init_np + raw_delta * final_alpha[..., None], 0.0, 255.0).astype(np.uint8)
    comp_diff = comp_np.astype(np.float32) - init_np
    raw_diff = raw_np - init_np
    preserve_rmse = _rmse(preserve, comp_diff)
    safe_rmse = _rmse(safe, comp_diff)

    alpha_vis = np.clip(final_alpha * 255.0, 0, 255).astype(np.uint8)
    reject_vis = np.clip((1.0 - evidence_keep) * 255.0, 0, 255).astype(np.uint8)
    metrics: dict[str, float | int | str] = {
        "halo_px": int(halo_px),
        "gamma": float(gamma),
        "budget_mae": float(budget_mae),
        "residual_cap": float(residual_cap),
        "budget_scale": float(scale),
        "edge_strength": float(edge_strength),
        "diff_low": float(diff_low),
        "diff_high": float(diff_high),
        "diff_strength": float(diff_strength),
        "blur_px": int(blur_px),
        "core_fraction": float(core.mean()),
        "halo_fraction": float(halo.mean()),
        "safe_fraction": float(safe.mean()),
        "modified_fraction": float((final_alpha > 1e-6).mean()),
        "alpha_mean": float(final_alpha.mean()),
        "alpha_safe_mean": float(final_alpha[safe].mean()) if safe.any() else float("nan"),
        "alpha_halo_mean": float(final_alpha[halo].mean()) if halo.any() else float("nan"),
        "preserve_compose_mae": _mae(preserve, comp_diff),
        "preserve_compose_rmse": preserve_rmse,
        "preserve_compose_psnr": float(20.0 * np.log10(255.0 / max(preserve_rmse, 1e-6))) if preserve.any() else float("nan"),
        "safe_compose_mae": _mae(safe, comp_diff),
        "safe_compose_rmse": safe_rmse,
        "safe_compose_psnr": float(20.0 * np.log10(255.0 / max(safe_rmse, 1e-6))) if safe.any() else float("nan"),
        "core_raw_vs_init_mae": _mae(core, raw_diff),
        "halo_raw_vs_init_mae": _mae(halo, raw_diff),
        "safe_raw_vs_init_mae": _mae(safe, raw_diff),
        "edge_region_comp_vs_init_mae": _mae(edge > 0.5, comp_diff),
        "edge_region_raw_vs_init_mae": _mae(edge > 0.5, raw_diff),
        "candidate_high_edge_fraction": float(((core | halo) & (edge > 0.5)).sum() / max(1, (core | halo).sum())),
        "candidate_high_diff_fraction": float(((core | halo) & (diff_gate > 0.5)).sum() / max(1, (core | halo).sum())),
    }
    return Image.fromarray(comp_np), Image.fromarray(alpha_vis, mode="L"), Image.fromarray(reject_vis, mode="L"), metrics


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
            crop_imgs.append(np.vstack([_label_band(crop.shape[1], f"{label} | {crop_name}"), crop]))
        max_h = max(c.shape[0] for c in crop_imgs)
        padded = []
        for crop in crop_imgs:
            if crop.shape[0] < max_h:
                crop = np.vstack([crop, np.zeros((max_h - crop.shape[0], crop.shape[1], 3), dtype=np.uint8)])
            padded.append(crop)
        rendered_rows.append(np.hstack(padded))
    Image.fromarray(np.vstack(rendered_rows)).save(path, quality=92)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-image", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--case", action="append", required=True)
    ap.add_argument("--halo-px", action="append", type=int, required=True)
    ap.add_argument("--budget-mae", action="append", type=float, required=True)
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--residual-cap", type=float, default=0.35)
    ap.add_argument("--edge-strength", type=float, default=0.65)
    ap.add_argument("--diff-low", type=float, default=8.0)
    ap.add_argument("--diff-high", type=float, default=32.0)
    ap.add_argument("--diff-strength", type=float, default=0.65)
    ap.add_argument("--blur-px", type=int, default=3)
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
            for budget_mae in args.budget_mae:
                comp, alpha, reject, metrics = _compose(
                    init,
                    mask_path,
                    raw_path,
                    halo_px=halo_px,
                    gamma=args.gamma,
                    budget_mae=budget_mae,
                    residual_cap=args.residual_cap,
                    edge_strength=args.edge_strength,
                    diff_low=args.diff_low,
                    diff_high=args.diff_high,
                    diff_strength=args.diff_strength,
                    blur_px=args.blur_px,
                )
                setting = f"h{halo_px:03d}_b{int(round(budget_mae * 10)):03d}_rc{int(round(args.residual_cap * 100)):03d}"
                out_name = f"{name}_{setting}"
                case_dir = out_dir / out_name
                case_dir.mkdir(parents=True, exist_ok=True)
                comp_path = case_dir / f"{out_name}_fidelity_budget.png"
                alpha_path = case_dir / f"{out_name}_alpha.png"
                reject_path = case_dir / f"{out_name}_rejection_gate.png"
                diag_path = case_dir / f"{out_name}_diagnostics.json"
                comp.save(comp_path)
                alpha.save(alpha_path)
                reject.save(reject_path)
                summary = {
                    "name": name,
                    "setting": setting,
                    "init_image": str(args.init_image),
                    "mask": str(mask_path),
                    "raw_output": str(raw_path),
                    "fidelity_budget_output": str(comp_path),
                    "alpha_image": str(alpha_path),
                    "rejection_gate_image": str(reject_path),
                    "mask_convention": "white/255 preserves source; black/0 uses DiT360 output",
                    "composition": "mask core + halo plus source-edge-gated raw residual under preserve MAE budget",
                    **metrics,
                }
                with open(diag_path, "w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2, ensure_ascii=False)
                summaries.append(summary)
                review_rows.append((f"{name} budget {setting}", comp))

    with open(out_dir / "fidelity_budget_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": summaries}, f, indent=2, ensure_ascii=False)
    _save_overall_review(out_dir / "fidelity_budget_overall_review.jpg", init, review_rows, args.overall_width)
    _save_crop_review(out_dir / "fidelity_budget_crop_review.jpg", init, review_rows, crops)
    print(json.dumps({"runs": summaries}, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
