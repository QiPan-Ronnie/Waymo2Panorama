"""Evidence-gated composition for DiT360 seam completion outputs.

DiT360 raw seam edits can look smoother than strict post-compose because the
model changes a small halo around the mask. For driving data, that freedom is
dangerous near lane markings, vehicle contours, signs, and building edges.

This utility keeps the same bounded-composition idea as
``soft_compose_dit360_masks.py`` but gates the DiT360 delta using only source
evidence:

  - black mask core: candidate area for DiT360 output
  - halo around core: optional feather region
  - strong source edges: reduce or reject DiT360 edits
  - large raw-vs-source changes: reduce or reject DiT360 edits
  - outside halo: restore source exactly

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


def _parse_setting(text: str) -> dict[str, float | int | str]:
    item = _parse_kv(text, ("name",))
    return {
        "name": item["name"],
        "halo_px": int(item.get("halo", 8)),
        "gamma": float(item.get("gamma", 1.0)),
        "edge_strength": float(item.get("edge", 0.65)),
        "edge_power": float(item.get("edge_power", 1.0)),
        "diff_low": float(item.get("diff_low", 8.0)),
        "diff_high": float(item.get("diff_high", 32.0)),
        "diff_strength": float(item.get("diff", 0.85)),
        "blur_px": int(item.get("blur", 3)),
    }


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


def _smoothstep(low: float, high: float, values: np.ndarray) -> np.ndarray:
    if high <= low:
        return (values >= high).astype(np.float32)
    x = np.clip((values - low) / (high - low), 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def _source_edge_gate(init_np: np.ndarray, blur_px: int, edge_power: float) -> np.ndarray:
    gray = cv2.cvtColor(init_np.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    if blur_px > 0:
        k = blur_px * 2 + 1
        gray = cv2.GaussianBlur(gray, (k, k), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    scale = float(np.percentile(mag, 97.0))
    if scale < 1e-6:
        return np.zeros_like(mag, dtype=np.float32)
    gate = np.clip(mag / scale, 0.0, 1.0).astype(np.float32)
    if edge_power != 1.0:
        gate = np.power(gate, edge_power).astype(np.float32)
    return gate


def _evidence_gate_compose(
    init: Image.Image,
    mask_path: Path,
    raw_path: Path,
    setting: dict[str, float | int | str],
) -> tuple[Image.Image, Image.Image, Image.Image, dict[str, float | int | str]]:
    raw = _load_rgb(raw_path, init.size)
    init_np = np.array(init, dtype=np.float32)
    raw_np = np.array(raw, dtype=np.float32)
    mask = _load_mask(mask_path, init.size)

    base_alpha, halo, safe = _alpha_from_mask(
        mask,
        int(setting["halo_px"]),
        float(setting["gamma"]),
    )
    core = mask < 128
    candidate = base_alpha > 1e-6

    edge_gate = _source_edge_gate(
        init_np,
        int(setting["blur_px"]),
        float(setting["edge_power"]),
    )
    rgb_diff = np.mean(np.abs(raw_np - init_np), axis=2).astype(np.float32)
    diff_gate = _smoothstep(float(setting["diff_low"]), float(setting["diff_high"]), rgb_diff)

    keep_from_raw = np.ones_like(base_alpha, dtype=np.float32)
    keep_from_raw *= 1.0 - float(setting["edge_strength"]) * edge_gate
    keep_from_raw *= 1.0 - float(setting["diff_strength"]) * diff_gate
    keep_from_raw = np.clip(keep_from_raw, 0.0, 1.0)
    final_alpha = (base_alpha * keep_from_raw).astype(np.float32)

    alpha3 = final_alpha[..., None]
    comp = np.clip(init_np * (1.0 - alpha3) + raw_np * alpha3, 0.0, 255.0).astype(np.uint8)
    alpha_vis = np.clip(final_alpha * 255.0, 0.0, 255.0).astype(np.uint8)
    gate_vis = np.clip((1.0 - keep_from_raw) * 255.0, 0.0, 255.0).astype(np.uint8)

    raw_diff = raw_np - init_np
    comp_diff = comp.astype(np.float32) - init_np
    preserve = mask >= 128

    def mae(region: np.ndarray, arr: np.ndarray) -> float:
        if not region.any():
            return float("nan")
        return float(np.mean(np.abs(arr[region])))

    def mean(region: np.ndarray, arr: np.ndarray) -> float:
        if not region.any():
            return float("nan")
        return float(np.mean(arr[region]))

    def frac(region: np.ndarray) -> float:
        return float(region.mean())

    metrics: dict[str, float | int | str] = {
        "setting_name": str(setting["name"]),
        "halo_px": int(setting["halo_px"]),
        "gamma": float(setting["gamma"]),
        "edge_strength": float(setting["edge_strength"]),
        "edge_power": float(setting["edge_power"]),
        "diff_low": float(setting["diff_low"]),
        "diff_high": float(setting["diff_high"]),
        "diff_strength": float(setting["diff_strength"]),
        "blur_px": int(setting["blur_px"]),
        "core_fraction": frac(core),
        "halo_fraction": frac(halo),
        "candidate_fraction": frac(candidate),
        "safe_fraction": frac(safe),
        "modified_fraction": frac(final_alpha > 1e-6),
        "effective_alpha_mean": float(final_alpha.mean()),
        "candidate_alpha_mean": mean(candidate, final_alpha),
        "candidate_base_alpha_mean": mean(candidate, base_alpha),
        "candidate_alpha_retention": mean(candidate, final_alpha) / max(mean(candidate, base_alpha), 1e-6),
        "candidate_edge_gate_mean": mean(candidate, edge_gate),
        "candidate_diff_gate_mean": mean(candidate, diff_gate),
        "candidate_high_edge_fraction": frac(candidate & (edge_gate > 0.5)),
        "candidate_high_diff_fraction": frac(candidate & (diff_gate > 0.5)),
        "safe_compose_mae": mae(safe, comp_diff),
        "white_mask_compose_mae": mae(preserve, comp_diff),
        "core_raw_vs_init_mae": mae(core, raw_diff),
        "core_comp_vs_init_mae": mae(core, comp_diff),
        "halo_raw_vs_init_mae": mae(halo, raw_diff),
        "halo_comp_vs_init_mae": mae(halo, comp_diff),
        "edge_region_comp_vs_init_mae": mae(edge_gate > 0.5, comp_diff),
    }
    return Image.fromarray(comp), Image.fromarray(alpha_vis, mode="L"), Image.fromarray(gate_vis, mode="L"), metrics


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
    ap.add_argument("--setting", action="append", required=True)
    ap.add_argument("--crop", action="append", default=[])
    ap.add_argument("--overall-width", type=int, default=1024)
    args = ap.parse_args()

    init_path = Path(args.init_image)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    init = _load_rgb(init_path)
    crops = [_parse_crop(item) for item in args.crop]
    settings = [_parse_setting(item) for item in args.setting]

    summaries: list[dict[str, object]] = []
    review_rows: list[tuple[str, Image.Image]] = []
    for item in args.case:
        case = _parse_case(item)
        name = case["name"]
        mask_path = Path(case["mask"])
        raw_path = Path(case["raw"])
        raw = _load_rgb(raw_path, init.size)
        review_rows.append((f"{name} raw", raw))

        for setting in settings:
            setting_name = str(setting["name"])
            out_name = f"{name}_{setting_name}"
            case_dir = out_dir / out_name
            case_dir.mkdir(parents=True, exist_ok=True)

            comp, alpha_vis, gate_vis, metrics = _evidence_gate_compose(init, mask_path, raw_path, setting)
            comp_path = case_dir / f"{out_name}_evidence_gate.png"
            alpha_path = case_dir / f"{out_name}_alpha.png"
            gate_path = case_dir / f"{out_name}_rejection_gate.png"
            diag_path = case_dir / f"{out_name}_diagnostics.json"

            comp.save(comp_path)
            alpha_vis.save(alpha_path)
            gate_vis.save(gate_path)
            summary = {
                "name": name,
                "setting": setting_name,
                "init_image": str(init_path),
                "mask": str(mask_path),
                "raw_output": str(raw_path),
                "evidence_gate_output": str(comp_path),
                "alpha_image": str(alpha_path),
                "rejection_gate_image": str(gate_path),
                "mask_convention": "white/255 preserves source; black/0 uses DiT360 output",
                "composition": "DiT360 delta is bounded by mask halo, then downweighted by source edge strength and raw-vs-source change",
                **metrics,
            }
            with open(diag_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            summaries.append(summary)
            review_rows.append((f"{name} gate {setting_name}", comp))

    with open(out_dir / "evidence_gate_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": summaries}, f, indent=2, ensure_ascii=False)
    _save_overall_review(out_dir / "evidence_gate_overall_review.jpg", init, review_rows, args.overall_width)
    _save_crop_review(out_dir / "evidence_gate_crop_review.jpg", init, review_rows, crops)
    print(json.dumps({"runs": summaries}, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
