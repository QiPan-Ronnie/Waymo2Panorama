"""DB-32: source-safe generated-sky chroma harmonization.

This is a CPU-only postprocess for DB-29. It changes only pixels in the existing
generated sky core (mask black/0). All source-preserved pixels must remain byte
identical.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def read_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def write_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR))


def label(im: np.ndarray, text: str, h: int = 34) -> np.ndarray:
    bar = np.zeros((h, im.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, im])


def fit_panel(im: np.ndarray, w: int, h: int) -> np.ndarray:
    im = np.clip(im, 0, 255).astype(np.uint8)
    ih, iw = im.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    rs = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((h, w, 3), np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = rs
    return out


def sky_like_mask(rgb: np.ndarray, core: np.ndarray, horizon_frac: float) -> np.ndarray:
    h, w = rgb.shape[:2]
    rows = np.arange(h)[:, None] * np.ones((1, w))
    upper = rows < h * horizon_frac
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue = hsv[..., 0].astype(np.int16)
    sat = hsv[..., 1].astype(np.int16)
    val = hsv[..., 2].astype(np.int16)
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)

    blue = (
        upper
        & (~core)
        & (val > 105)
        & (sat > 30)
        & (hue >= 82)
        & (hue <= 130)
        & (b > r + 14)
        & (b >= g - 8)
    )
    return blue


def harmonize(
    rgb: np.ndarray,
    core: np.ndarray,
    target_sky: np.ndarray,
    strength: float,
) -> np.ndarray:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    core_idx = core
    target_idx = target_sky
    if int(core_idx.sum()) < 1000:
        raise ValueError("core mask too small")
    if int(target_idx.sum()) < 1000:
        raise ValueError("target source-sky sample too small")

    core_vals = lab[core_idx]
    target_vals = lab[target_idx]
    c_mean = core_vals.mean(axis=0)
    c_std = core_vals.std(axis=0) + 1e-3
    t_mean = target_vals.mean(axis=0)
    t_std = target_vals.std(axis=0) + 1e-3

    matched = (lab[core_idx] - c_mean) * (t_std / c_std) + t_mean
    # Mostly chroma/statistical harmonization; limit L changes to avoid flat white sky.
    delta = matched - lab[core_idx]
    delta[:, 0] = np.clip(delta[:, 0], -28, 28)
    delta[:, 1] = np.clip(delta[:, 1], -18, 18)
    delta[:, 2] = np.clip(delta[:, 2], -18, 18)
    out_lab = lab.copy()
    out_lab[core_idx] = lab[core_idx] + strength * delta
    out = cv2.cvtColor(np.clip(out_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)
    out[~core_idx] = rgb[~core_idx]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db29", required=True, type=Path)
    ap.add_argument("--mask", required=True, type=Path, help="opmask_sky: black/0 = generated core")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--strengths", default="0.35,0.55,0.75")
    ap.add_argument("--horizon-frac", type=float, default=0.42)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rgb = read_rgb(args.db29)
    mask = cv2.imread(str(args.mask), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(args.mask)
    core = mask < 128
    target_sky = sky_like_mask(rgb, core, args.horizon_frac)

    strengths = [float(x.strip()) for x in args.strengths.split(",") if x.strip()]
    variants = []
    diagnostics = {
        "db29": str(args.db29),
        "mask": str(args.mask),
        "mask_convention": "white/255 preserves source; black/0 generated sky core",
        "core_fraction": float(core.mean()),
        "target_source_sky_fraction": float(target_sky.mean()),
        "target_source_sky_pixels": int(target_sky.sum()),
        "strengths": strengths,
        "outputs": [],
    }
    for strength in strengths:
        out = harmonize(rgb, core, target_sky, strength)
        diff = np.abs(out.astype(np.int16) - rgb.astype(np.int16)).max(axis=2)
        noncore_max = int(diff[~core].max()) if int((~core).sum()) else 0
        core_mae = float(diff[core].mean())
        if noncore_max != 0:
            raise AssertionError(f"non-core pixels changed: max={noncore_max}")
        stem = f"db32_generated_sky_harmonize_s{int(round(strength * 100)):02d}.png"
        write_rgb(args.out_dir / stem, out)
        variants.append((strength, out, diff))
        diagnostics["outputs"].append(
            {
                "strength": strength,
                "path": str(args.out_dir / stem),
                "noncore_max_abs_diff": noncore_max,
                "core_mae": core_mae,
            }
        )

    overlay = rgb.copy().astype(np.float32)
    red = np.zeros_like(overlay)
    red[..., 0] = 255
    overlay[core] = 0.45 * overlay[core] + 0.55 * red[core]
    blue = np.zeros_like(overlay)
    blue[..., 2] = 255
    overlay[target_sky] = 0.45 * overlay[target_sky] + 0.55 * blue[target_sky]
    write_rgb(args.out_dir / "db32_core_red_target_blue_overlay.jpg", overlay)

    top = slice(0, 520)
    panels = [label(fit_panel(rgb[top], 520, 190), "DB29 input")]
    for strength, out, _diff in variants:
        panels.append(label(fit_panel(out[top], 520, 190), f"DB32 s={strength:.2f}"))
    montage = np.hstack(panels)
    write_rgb(args.out_dir / "db32_top_montage.jpg", montage)

    full_panels = [label(fit_panel(rgb, 520, 260), "DB29 input")]
    for strength, out, _diff in variants:
        full_panels.append(label(fit_panel(out, 520, 260), f"DB32 s={strength:.2f}"))
    write_rgb(args.out_dir / "db32_full_montage.jpg", np.hstack(full_panels))

    (args.out_dir / "db32_diagnostics.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
