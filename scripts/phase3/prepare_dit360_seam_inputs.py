"""Prepare Waymo2Panorama seam masks for DiT360 completion tests.

DiT360 editing masks use white/1 as "preserve the source image" and black/0 as
"generate with the model". This script renders an AV2 L1 hard-select panorama at
1024x2048, then writes seam-strip masks that preserve non-seam pixels and black
out only the hard-select camera boundaries.
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

from waymo2panorama.blending.hard_hdr_of import hard_select  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


DEFAULT_PROMPT = (
    "This is a 360-degree street panorama captured by an autonomous vehicle in "
    "an urban driving scene, with roads, lane markings, sidewalks, cars, "
    "buildings, signs, and sky."
)


def _save_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path)


def _save_gray(path: Path, gray: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8), mode="L").save(path)


def _hard_label_map(weights: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    w_stack = np.stack(weights, axis=0)
    label = w_stack.argmax(axis=0).astype(np.uint8)
    valid = w_stack.max(axis=0) > 1e-6
    return label, valid


def _boundary_from_labels(label: np.ndarray, valid: np.ndarray) -> np.ndarray:
    boundary = np.zeros(label.shape, dtype=bool)
    same_valid_x = valid[:, 1:] & valid[:, :-1]
    diff_x = (label[:, 1:] != label[:, :-1]) & same_valid_x
    boundary[:, 1:] |= diff_x
    boundary[:, :-1] |= diff_x

    same_valid_y = valid[1:, :] & valid[:-1, :]
    diff_y = (label[1:, :] != label[:-1, :]) & same_valid_y
    boundary[1:, :] |= diff_y
    boundary[:-1, :] |= diff_y

    wrap = (label[:, 0] != label[:, -1]) & valid[:, 0] & valid[:, -1]
    boundary[:, 0] |= wrap
    boundary[:, -1] |= wrap
    return boundary


def _dilate(mask: np.ndarray, radius_px: int) -> np.ndarray:
    if radius_px <= 0:
        return mask.copy()
    k = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (radius_px * 2 + 1, radius_px * 2 + 1),
    )
    return cv2.dilate(mask.astype(np.uint8), k).astype(bool)


def _mask_to_preview(hard_rgb: np.ndarray, preserve_mask: np.ndarray) -> np.ndarray:
    preview = hard_rgb.copy().astype(np.float32)
    generate = ~preserve_mask
    overlay = np.zeros_like(preview)
    overlay[..., 0] = 255.0
    overlay[..., 1] = 48.0
    preview[generate] = 0.55 * preview[generate] + 0.45 * overlay[generate]
    return preview.astype(np.uint8)


def _masked_input(hard_rgb: np.ndarray, preserve_mask: np.ndarray) -> np.ndarray:
    out = hard_rgb.copy()
    out[~preserve_mask] = 0
    return out


def _colorize_labels(label: np.ndarray, valid: np.ndarray, boundary: np.ndarray) -> np.ndarray:
    palette = np.array(
        [
            [255, 90, 70],
            [255, 180, 70],
            [230, 230, 70],
            [80, 210, 100],
            [80, 190, 230],
            [90, 120, 255],
            [210, 100, 255],
        ],
        dtype=np.uint8,
    )
    rgb = palette[label % len(palette)]
    rgb[~valid] = 0
    rgb[boundary] = np.array([255, 255, 255], dtype=np.uint8)
    return rgb


def _parse_radii(text: str) -> list[int]:
    radii = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        radii.append(max(0, int(item)))
    return sorted(set(radii))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, default=0)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--seam-radii", default="20,40,80")
    ap.add_argument(
        "--generate-invalid",
        action="store_true",
        help="If set, invalid black ERP areas are black/generate in the DiT360 mask.",
    )
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    log_short = Path(args.log_dir).name.split("-")[0]
    run_name = f"{log_short}_a{args.anchor_idx:03d}"

    loader = AV2RingLoader(Path(args.log_dir))
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[args.anchor_idx])

    print(f"[load] {Path(args.log_dir).name} anchor={args.anchor_idx}", flush=True)
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
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

    hard_rgb = hard_select(slabs, weights)
    label, valid = _hard_label_map(weights)
    boundary = _boundary_from_labels(label, valid)
    label_preview = _colorize_labels(label, valid, boundary)

    _save_rgb(out_dir / f"{run_name}_hard_select_1024x2048.png", hard_rgb)
    _save_rgb(out_dir / f"{run_name}_label_boundary_preview.png", label_preview)

    manifest: dict[str, object] = {
        "log_dir": str(args.log_dir),
        "log_short": log_short,
        "anchor_idx": args.anchor_idx,
        "erp_hw": [args.erp_h, args.erp_w],
        "prompt": args.prompt,
        "mask_convention": "white/255 = preserve source; black/0 = generate/fill",
        "preserve_invalid_black_regions": not args.generate_invalid,
        "valid_fraction": float(valid.mean()),
        "boundary_pixel_count": int(boundary.sum()),
        "artifacts": [],
    }

    base_preserve = np.ones(valid.shape, dtype=bool)
    if args.generate_invalid:
        base_preserve = valid.copy()

    invalid_outpaint_preserve = valid.copy()
    invalid_stem = f"{run_name}_mask_preserve_valid_outpaint_invalid"
    invalid_mask = np.where(invalid_outpaint_preserve, 255, 0).astype(np.uint8)
    _save_gray(out_dir / f"{invalid_stem}.png", invalid_mask)
    _save_rgb(out_dir / f"{invalid_stem}_masked_input.png", _masked_input(hard_rgb, invalid_outpaint_preserve))
    _save_rgb(out_dir / f"{invalid_stem}_preview.jpg", _mask_to_preview(hard_rgb, invalid_outpaint_preserve))
    manifest["artifacts"].append(
        {
            "type": "invalid_outpaint",
            "mask": f"{invalid_stem}.png",
            "masked_input": f"{invalid_stem}_masked_input.png",
            "preview": f"{invalid_stem}_preview.jpg",
            "generate_fraction": float((~invalid_outpaint_preserve).mean()),
        }
    )

    for radius in _parse_radii(args.seam_radii):
        seam_zone = _dilate(boundary, radius)
        preserve = base_preserve & ~seam_zone
        mask = np.where(preserve, 255, 0).astype(np.uint8)
        stem = f"{run_name}_mask_preserve_nonseam_r{radius:03d}"
        _save_gray(out_dir / f"{stem}.png", mask)
        _save_rgb(out_dir / f"{stem}_masked_input.png", _masked_input(hard_rgb, preserve))
        _save_rgb(out_dir / f"{stem}_preview.jpg", _mask_to_preview(hard_rgb, preserve))
        manifest["artifacts"].append(
            {
                "type": "seam_strip",
                "radius_px": radius,
                "mask": f"{stem}.png",
                "masked_input": f"{stem}_masked_input.png",
                "preview": f"{stem}_preview.jpg",
                "generate_fraction": float((~preserve).mean()),
            }
        )

    alternating_sets = {
        "preserve_cam_1_3_5_7": [0, 2, 4, 6],
        "preserve_cam_2_4_6": [1, 3, 5],
    }
    for name, cams in alternating_sets.items():
        selected = np.isin(label, np.array(cams, dtype=np.uint8)) & valid
        preserve = selected.copy()
        if not args.generate_invalid:
            preserve |= ~valid
        mask = np.where(preserve, 255, 0).astype(np.uint8)
        stem = f"{run_name}_mask_{name}"
        _save_gray(out_dir / f"{stem}.png", mask)
        _save_rgb(out_dir / f"{stem}_masked_input.png", _masked_input(hard_rgb, preserve))
        _save_rgb(out_dir / f"{stem}_preview.jpg", _mask_to_preview(hard_rgb, preserve))
        manifest["artifacts"].append(
            {
                "type": "alternating_camera_preserve",
                "name": name,
                "zero_indexed_cameras": cams,
                "mask": f"{stem}.png",
                "masked_input": f"{stem}_masked_input.png",
                "preview": f"{stem}_preview.jpg",
                "generate_fraction": float((~preserve).mean()),
            }
        )

    with open(out_dir / f"{run_name}_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)
    print(f"[saved] {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
