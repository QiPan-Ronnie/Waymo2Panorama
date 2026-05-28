"""Prepare risk-aware adaptive seam masks for DiT360 seam completion.

The fixed r004/r008/r012 masks treat every seam pixel the same. That is too
simple for AV ring-camera seams: low-structure color seams need only a tiny
fill strip, while lane/car/building edges either need to be protected or need a
wider coherent region. This script renders the existing L1 hard_select input
and writes a small set of adaptive DiT360 masks from source-only seam risk maps.

Mask convention follows DiT360:
  white/255 = preserve source
  black/0   = generate/fill
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))

from seam_confidence_map import compute_seam_risk_maps, _heatmap_u8, _overlay_risk  # noqa: E402
from waymo2panorama.blending.hard_hdr_of import hard_select  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


DEFAULT_PROMPT = (
    "This is a 360-degree street panorama captured by an autonomous vehicle in "
    "an urban driving scene, with roads, lane markings, sidewalks, cars, "
    "buildings, signs, and sky."
)


def _save_rgb(path: Path, rgb: np.ndarray, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        img.save(path, quality=quality)
    else:
        img.save(path)


def _save_gray(path: Path, gray: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8), mode="L").save(path)


def _dilate(mask: np.ndarray, radius_px: int) -> np.ndarray:
    if radius_px <= 0:
        return mask.copy()
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius_px * 2 + 1, radius_px * 2 + 1))
    return cv2.dilate(mask.astype(np.uint8), k).astype(bool)


def _masked_input(rgb: np.ndarray, preserve: np.ndarray) -> np.ndarray:
    out = rgb.copy()
    out[~preserve] = 0
    return out


def _mask_preview(rgb: np.ndarray, preserve: np.ndarray) -> np.ndarray:
    preview = rgb.copy().astype(np.float32)
    generate = ~preserve
    red = np.zeros_like(preview)
    red[..., 0] = 255.0
    red[..., 1] = 48.0
    preview[generate] = 0.55 * preview[generate] + 0.45 * red[generate]
    return np.clip(preview, 0, 255).astype(np.uint8)


def _label_band(width: int, label: str) -> np.ndarray:
    band = np.zeros((34, width, 3), dtype=np.uint8)
    cv2.putText(band, label, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    return band


def _fit_width(rgb: np.ndarray, width: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    if w == width:
        return np.clip(rgb, 0, 255).astype(np.uint8)
    height = max(1, round(h * width / w))
    return cv2.resize(np.clip(rgb, 0, 255).astype(np.uint8), (width, height), interpolation=cv2.INTER_AREA)


def _stack_rows(rows: list[tuple[str, np.ndarray]], width: int) -> np.ndarray:
    rendered = []
    for label, rgb in rows:
        small = _fit_width(rgb, width)
        rendered.append(np.vstack([_label_band(width, label), small]))
    return np.vstack(rendered)


def _case(
    name: str,
    generate: np.ndarray,
    valid: np.ndarray,
    preserve_invalid: bool,
    hard: np.ndarray,
    out_dir: Path,
    run_name: str,
) -> dict[str, object]:
    preserve = ~generate
    if preserve_invalid:
        preserve |= ~valid
    mask = np.where(preserve, 255, 0).astype(np.uint8)
    stem = f"{run_name}_mask_{name}"
    _save_gray(out_dir / f"{stem}.png", mask)
    _save_rgb(out_dir / f"{stem}_masked_input.png", _masked_input(hard, preserve))
    _save_rgb(out_dir / f"{stem}_preview.jpg", _mask_preview(hard, preserve), quality=88)
    return {
        "name": name,
        "mask": f"{stem}.png",
        "masked_input": f"{stem}_masked_input.png",
        "preview": f"{stem}_preview.jpg",
        "generate_fraction": float((~preserve).mean()),
        "valid_generate_fraction": float(((~preserve) & valid).sum() / max(1, valid.sum())),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=48)
    ap.add_argument("--core-half-width", type=int, default=2)
    ap.add_argument("--ncc-win", type=int, default=21)
    ap.add_argument("--low-radius", type=int, default=6)
    ap.add_argument("--base-radius", type=int, default=8)
    ap.add_argument("--high-radius", type=int, default=24)
    ap.add_argument("--structure-thresh", type=float, default=0.62)
    ap.add_argument("--risk-thresh", type=float, default=0.72)
    ap.add_argument("--review-w", type=int, default=1024)
    ap.add_argument("--generate-invalid", action="store_true")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_short = Path(args.log_dir).name.split("-")[0]
    run_name = f"{log_short}_a{args.anchor_idx:03d}"
    erp_hw = (args.erp_h, args.erp_w)

    loader = AV2RingLoader(Path(args.log_dir))
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[args.anchor_idx])

    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    print(f"[load] {Path(args.log_dir).name} anchor={args.anchor_idx}", flush=True)
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=frame.images[cam],
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)

    hard = hard_select(slabs, weights)
    valid = np.stack(weights, axis=0).max(axis=0) > 1e-6
    maps, diag = compute_seam_risk_maps(
        slabs,
        weights,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        ncc_win=args.ncc_win,
    )
    seam_core = maps["seam_core"].astype(bool)
    seam_band = maps["seam_band"].astype(bool)
    risk = maps["risk"]
    structure = maps["structure_risk"]
    color = maps["color_risk"]

    low_struct = structure < args.structure_thresh
    high_struct = structure >= args.structure_thresh
    high_risk = risk >= args.risk_thresh
    high_color_low_struct = (color >= 0.65) & low_struct

    cases: list[dict[str, object]] = []
    preserve_invalid = not args.generate_invalid
    cases.append(
        _case(
            "adaptive_lowstruct_r006",
            _dilate(seam_core, args.low_radius) & low_struct & valid,
            valid,
            preserve_invalid,
            hard,
            out_dir,
            run_name,
        )
    )
    cases.append(
        _case(
            "adaptive_color_r008_guardstruct",
            (_dilate(seam_core, args.base_radius) & low_struct & valid)
            | (_dilate(high_color_low_struct, args.base_radius) & valid),
            valid,
            preserve_invalid,
            hard,
            out_dir,
            run_name,
        )
    )
    cases.append(
        _case(
            "adaptive_expand_histruct_r024",
            (_dilate(seam_core, args.base_radius) | _dilate(high_struct | high_risk, args.high_radius))
            & seam_band
            & valid,
            valid,
            preserve_invalid,
            hard,
            out_dir,
            run_name,
        )
    )

    risk_overlay = _overlay_risk(hard, risk, seam_core)
    structure_heat = _heatmap_u8(structure)
    color_heat = _heatmap_u8(color)
    _save_rgb(out_dir / f"{run_name}_hard_select_1024x2048.png", hard)
    _save_rgb(out_dir / f"{run_name}_risk_overlay.jpg", risk_overlay, quality=90)
    _save_rgb(out_dir / f"{run_name}_structure_risk.jpg", structure_heat, quality=90)
    _save_rgb(out_dir / f"{run_name}_color_risk.jpg", color_heat, quality=90)

    review_rows = [
        ("hard_select", hard),
        ("risk_overlay", risk_overlay),
        ("structure_risk", structure_heat),
        ("mask adaptive_lowstruct_r006", _mask_preview(hard, ~( _dilate(seam_core, args.low_radius) & low_struct & valid))),
        (
            "mask adaptive_color_r008_guardstruct",
            _mask_preview(
                hard,
                ~(
                    (_dilate(seam_core, args.base_radius) & low_struct & valid)
                    | (_dilate(high_color_low_struct, args.base_radius) & valid)
                ),
            ),
        ),
        (
            "mask adaptive_expand_histruct_r024",
            _mask_preview(
                hard,
                ~(((_dilate(seam_core, args.base_radius) | _dilate(high_struct | high_risk, args.high_radius)) & seam_band & valid)),
            ),
        ),
    ]
    _save_rgb(out_dir / f"{run_name}_adaptive_mask_review_w{args.review_w}.jpg", _stack_rows(review_rows, args.review_w), quality=88)

    manifest = {
        "log_dir": str(args.log_dir),
        "log_short": log_short,
        "anchor_idx": args.anchor_idx,
        "erp_hw": [args.erp_h, args.erp_w],
        "prompt": args.prompt,
        "mask_convention": "white/255 = preserve source; black/0 = generate/fill",
        "preserve_invalid_black_regions": preserve_invalid,
        "params": {
            "band_half_width": args.band_half_width,
            "core_half_width": args.core_half_width,
            "ncc_win": args.ncc_win,
            "low_radius": args.low_radius,
            "base_radius": args.base_radius,
            "high_radius": args.high_radius,
            "structure_thresh": args.structure_thresh,
            "risk_thresh": args.risk_thresh,
        },
        "risk_global": diag["global"],
        "artifacts": cases,
        "outputs": {
            "hard_select": f"{run_name}_hard_select_1024x2048.png",
            "risk_overlay": f"{run_name}_risk_overlay.jpg",
            "mask_review": f"{run_name}_adaptive_mask_review_w{args.review_w}.jpg",
        },
    }
    with open(out_dir / f"{run_name}_adaptive_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)
    print(f"[saved] {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
