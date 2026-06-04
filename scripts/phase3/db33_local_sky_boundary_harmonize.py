"""DB-33: Cube/rectilinear-inspired local generated-sky harmonization.

CPU-only. Starts from the accepted DB-32 s40 candidate and changes only pixels
inside the DB-29 generated sky core. Source-preserved pixels must remain byte
identical to DB-29/DB-32.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt


def read_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def write_rgb(path: Path, rgb: np.ndarray, quality: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bgr = cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    if quality is None or path.suffix.lower() == ".png":
        cv2.imwrite(str(path), bgr)
    else:
        cv2.imwrite(str(path), bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])


def label(im: np.ndarray, text: str, h: int = 34) -> np.ndarray:
    bar = np.zeros((h, im.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (255, 255, 255), 2, cv2.LINE_AA)
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


def erp_to_rect(
    img: np.ndarray,
    yaw_deg: float,
    pitch_deg: float,
    fov_deg: float,
    out_w: int,
    out_h: int,
    interp: int = cv2.INTER_LINEAR,
) -> np.ndarray:
    h, w = img.shape[:2]
    yaw = np.deg2rad(yaw_deg)
    pitch = np.deg2rad(pitch_deg)
    fov = np.deg2rad(fov_deg)
    xs = (np.arange(out_w, dtype=np.float32) + 0.5 - out_w / 2.0) / (out_w / 2.0)
    ys = (np.arange(out_h, dtype=np.float32) + 0.5 - out_h / 2.0) / (out_h / 2.0)
    xx, yy = np.meshgrid(xs, ys)
    z = np.ones_like(xx) / np.tan(fov / 2.0)
    dirs = np.stack([xx, -yy, z], axis=-1)
    dirs /= np.linalg.norm(dirs, axis=-1, keepdims=True)
    cy, sy = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], dtype=np.float32)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
    dirs = dirs @ (ry @ rx).T
    lon = np.arctan2(dirs[..., 0], dirs[..., 2])
    lat = np.arcsin(np.clip(dirs[..., 1], -1.0, 1.0))
    map_x = ((lon / (2 * np.pi) + 0.5) * w).astype(np.float32)
    map_y = ((0.5 - lat / np.pi) * h).astype(np.float32)
    return cv2.remap(img, map_x, map_y, interp, borderMode=cv2.BORDER_WRAP)


def strict_source_sky(rgb: np.ndarray, core: np.ndarray, horizon_frac: float) -> np.ndarray:
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
    return (
        upper
        & (~core)
        & (val > 105)
        & (sat > 30)
        & (hue >= 82)
        & (hue <= 130)
        & (b > r + 14)
        & (b >= g - 8)
    )


def local_harmonize(
    db29: np.ndarray,
    start: np.ndarray,
    core: np.ndarray,
    source_sky: np.ndarray,
    strength: float,
    field_sigma: float,
    boundary_sigma: float,
) -> np.ndarray:
    start_lab = cv2.cvtColor(start, cv2.COLOR_RGB2LAB).astype(np.float32)
    if int(source_sky.sum()) < 1000:
        raise ValueError("not enough strict source-sky samples")

    # For every pixel, find the nearest strict source-sky pixel and blur that
    # sampled color field. This approximates a local cube-face/perspective
    # neighborhood without rewriting source content.
    _dist_to_sky, nearest = distance_transform_edt(~source_sky, return_indices=True)
    target_field = start_lab[nearest[0], nearest[1]]
    if field_sigma > 0:
        for c in range(3):
            target_field[..., c] = cv2.GaussianBlur(target_field[..., c], (0, 0), field_sigma)

    low_start = start_lab.copy()
    for c in range(3):
        low_start[..., c] = cv2.GaussianBlur(low_start[..., c], (0, 0), field_sigma)

    dist_from_source = distance_transform_edt(~source_sky)
    spatial_w = 0.18 + 0.82 * np.exp(-dist_from_source / max(boundary_sigma, 1.0))

    hsv = cv2.cvtColor(cv2.cvtColor(start, cv2.COLOR_RGB2BGR), cv2.COLOR_BGR2HSV)
    sat = hsv[..., 1].astype(np.float32)
    val = hsv[..., 2].astype(np.float32)
    cloud_like = (val > 175) & (sat < 85)
    cloud_w = np.where(cloud_like, 0.35, 1.0).astype(np.float32)

    delta = target_field - low_start
    delta[..., 0] = np.clip(delta[..., 0], -22, 22)
    delta[..., 1] = np.clip(delta[..., 1], -15, 15)
    delta[..., 2] = np.clip(delta[..., 2], -15, 15)

    out_lab = start_lab.copy()
    weight = (strength * spatial_w * cloud_w).astype(np.float32)
    out_lab[core] = start_lab[core] + weight[core, None] * delta[core]
    out = cv2.cvtColor(np.clip(out_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)
    out[~core] = db29[~core]
    return out


def make_rect_montage(images: list[tuple[str, np.ndarray]]) -> np.ndarray:
    yaws = [-135, -45, 45, 135]
    rows = []
    for yaw in yaws:
        panels = []
        for name, img in images:
            rect = erp_to_rect(img, yaw_deg=yaw, pitch_deg=-28.0, fov_deg=82.0, out_w=340, out_h=210)
            panels.append(label(rect, f"{name} yaw={yaw}", h=30))
        rows.append(np.hstack(panels))
    return np.vstack(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db29", required=True, type=Path)
    ap.add_argument("--db32", required=True, type=Path)
    ap.add_argument("--mask", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--strengths", default="0.35,0.55,0.75")
    ap.add_argument("--field-sigma", type=float, default=38.0)
    ap.add_argument("--boundary-sigma", type=float, default=220.0)
    ap.add_argument("--horizon-frac", type=float, default=0.42)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    db29 = read_rgb(args.db29)
    db32 = read_rgb(args.db32)
    mask = cv2.imread(str(args.mask), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(args.mask)
    core = mask < 128
    noncore_diff = np.abs(db32.astype(np.int16) - db29.astype(np.int16)).max(axis=2)
    if int(noncore_diff[~core].max()) != 0:
        raise AssertionError("DB32 baseline is not byte-exact outside core")

    source_sky = strict_source_sky(db29, core, args.horizon_frac)
    overlay = db29.copy().astype(np.float32)
    red = np.zeros_like(overlay)
    red[..., 0] = 255
    blue = np.zeros_like(overlay)
    blue[..., 2] = 255
    overlay[core] = 0.45 * overlay[core] + 0.55 * red[core]
    overlay[source_sky] = 0.45 * overlay[source_sky] + 0.55 * blue[source_sky]
    write_rgb(args.out_dir / "db33_core_red_source_sky_blue_overlay.jpg", overlay, quality=94)

    variants: list[tuple[str, np.ndarray]] = []
    diagnostics = {
        "db29": str(args.db29),
        "db32": str(args.db32),
        "mask": str(args.mask),
        "mask_convention": "white/255 preserves source; black/0 generated sky core",
        "core_fraction": float(core.mean()),
        "source_sky_fraction": float(source_sky.mean()),
        "source_sky_pixels": int(source_sky.sum()),
        "field_sigma": args.field_sigma,
        "boundary_sigma": args.boundary_sigma,
        "strengths": [],
        "outputs": [],
    }
    for strength in [float(x.strip()) for x in args.strengths.split(",") if x.strip()]:
        out = local_harmonize(db29, db32, core, source_sky, strength, args.field_sigma, args.boundary_sigma)
        diff_db29 = np.abs(out.astype(np.int16) - db29.astype(np.int16)).max(axis=2)
        diff_db32 = np.abs(out.astype(np.int16) - db32.astype(np.int16)).max(axis=2)
        noncore_max = int(diff_db29[~core].max())
        if noncore_max != 0:
            raise AssertionError(f"non-core pixels changed: {noncore_max}")
        tag = f"s{int(round(strength * 100)):02d}"
        path = args.out_dir / f"db33_local_sky_boundary_harmonize_{tag}.png"
        write_rgb(path, out)
        variants.append((tag, out))
        diagnostics["strengths"].append(strength)
        diagnostics["outputs"].append(
            {
                "strength": strength,
                "path": str(path),
                "noncore_max_abs_diff_vs_db29": noncore_max,
                "core_mae_vs_db32": float(diff_db32[core].mean()),
                "core_max_vs_db32": int(diff_db32[core].max()),
            }
        )

    top = slice(0, 520)
    top_panels = [label(fit_panel(db29[top], 470, 180), "DB29"), label(fit_panel(db32[top], 470, 180), "DB32 s40")]
    full_panels = [label(fit_panel(db29, 470, 245), "DB29"), label(fit_panel(db32, 470, 245), "DB32 s40")]
    rect_images: list[tuple[str, np.ndarray]] = [("DB29", db29), ("DB32", db32)]
    for tag, out in variants:
        top_panels.append(label(fit_panel(out[top], 470, 180), f"DB33 {tag}"))
        full_panels.append(label(fit_panel(out, 470, 245), f"DB33 {tag}"))
        rect_images.append((tag, out))
    write_rgb(args.out_dir / "db33_top_montage.jpg", np.hstack(top_panels), quality=94)
    write_rgb(args.out_dir / "db33_full_montage.jpg", np.hstack(full_panels), quality=94)
    write_rgb(args.out_dir / "db33_rect_sky_montage.jpg", make_rect_montage(rect_images), quality=94)
    (args.out_dir / "db33_diagnostics.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
