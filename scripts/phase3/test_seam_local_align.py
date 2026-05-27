"""Compare seam-local alignment against L1 hard-select baselines.

Outputs:
  - full ERP PNG per method
  - stacked full/thumb/crop panels
  - diagnostics JSON with tile acceptance and NCC gains
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))

from waymo2panorama.blending.hard_hdr_of import (  # noqa: E402
    RING_PAIRS,
    blend_hard_hdr_of,
    hard_select,
)
from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.blending.seam_local_align import blend_seam_local_align  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402
from measure_overlap_ncc import score_one_anchor  # noqa: E402


def _label_panel(rgb: np.ndarray, label: str, label_h: int = 36) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    band = np.zeros((label_h, rgb.shape[1], 3), dtype=np.uint8)
    cv2.putText(
        band,
        label,
        (12, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return np.vstack([band, rgb])


def _stack_named(methods: dict[str, np.ndarray], labels: dict[str, str], crop=None) -> np.ndarray:
    panels = []
    for name, rgb in methods.items():
        view = rgb
        if crop is not None:
            y0, y1, x0, x1 = crop
            view = view[y0:y1, x0:x1]
        panels.append(_label_panel(view, labels.get(name, name)))
    return np.vstack(panels)


def _save_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path)


def _default_crops(H: int, W: int) -> dict[str, tuple[int, int, int, int]]:
    return {
        "front_right_bmw_like": (
            int(0.38 * H),
            int(0.72 * H),
            int(0.70 * W),
            W,
        ),
        "left_mid_ped_like": (
            int(0.30 * H),
            int(0.86 * H),
            int(0.10 * W),
            int(0.46 * W),
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, default=0)
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--band-half-width", type=int, default=48)
    ap.add_argument("--tile-h", type=int, default=128)
    ap.add_argument("--tile-w", type=int, default=96)
    ap.add_argument("--stride-h", type=int, default=64)
    ap.add_argument("--stride-w", type=int, default=48)
    ap.add_argument("--max-dx", type=int, default=24)
    ap.add_argument("--max-dy", type=int, default=8)
    ap.add_argument("--min-ncc-gain", type=float, default=0.03)
    ap.add_argument("--ncc-win", type=int, default=9)
    ap.add_argument("--max-sample-per-pair", type=int, default=20000)
    ap.add_argument("--save-full", action="store_true",
                    help="Also save one full-resolution ERP PNG per method.")
    args = ap.parse_args()
    if args.ncc_win % 2 == 0:
        args.ncc_win += 1

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
    t0 = time.time()
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
    print(f"[project] {time.time() - t0:.1f}s", flush=True)

    methods: dict[str, np.ndarray] = {}
    diagnostics: dict[str, object] = {
        "log_short": log_short,
        "anchor_idx": args.anchor_idx,
        "erp_hw": list(erp_hw),
        "params": {
            "band_half_width": args.band_half_width,
            "tile_hw": [args.tile_h, args.tile_w],
            "stride_hw": [args.stride_h, args.stride_w],
            "max_dx": args.max_dx,
            "max_dy": args.max_dy,
            "min_ncc_gain": args.min_ncc_gain,
            "ncc_win": args.ncc_win,
            "save_full": args.save_full,
        },
        "runtime_s": {},
    }

    labels = {
        "multiband": "L1 multiband baseline",
        "hard_select": "L1 hard_select",
        "hard_hdr": "L1 hard_select + centered Y-HDR",
        "hard_localalign": "L1 hard_select + seam-local align",
        "hard_hdr_localalign": "L1 hard_select + HDR + seam-local align",
        "hard_hdr_of": "L1 + HDR + full OF chain (known risky)",
    }

    def timed(name: str, fn):
        print(f"[run] {name}", flush=True)
        s0 = time.time()
        out = fn()
        diagnostics["runtime_s"][name] = round(time.time() - s0, 3)
        print(f"[done] {name}: {diagnostics['runtime_s'][name]}s", flush=True)
        return out

    methods["multiband"] = timed("multiband", lambda: multiband_blend(slabs, weights))
    methods["hard_select"] = timed("hard_select", lambda: hard_select(slabs, weights))
    methods["hard_hdr"] = timed(
        "hard_hdr",
        lambda: blend_hard_hdr_of(slabs, weights, apply_of=False),
    )
    methods["hard_localalign"], diagnostics["hard_localalign"] = timed(
        "hard_localalign",
        lambda: blend_seam_local_align(
            slabs,
            weights,
            apply_hdr_pre=False,
            return_diagnostics=True,
            band_half_width=args.band_half_width,
            tile_hw=(args.tile_h, args.tile_w),
            stride_hw=(args.stride_h, args.stride_w),
            max_dx=args.max_dx,
            max_dy=args.max_dy,
            min_ncc_gain=args.min_ncc_gain,
        ),
    )
    methods["hard_hdr_localalign"], diagnostics["hard_hdr_localalign"] = timed(
        "hard_hdr_localalign",
        lambda: blend_seam_local_align(
            slabs,
            weights,
            apply_hdr_pre=True,
            return_diagnostics=True,
            band_half_width=args.band_half_width,
            tile_hw=(args.tile_h, args.tile_w),
            stride_hw=(args.stride_h, args.stride_w),
            max_dx=args.max_dx,
            max_dy=args.max_dy,
            min_ncc_gain=args.min_ncc_gain,
        ),
    )
    methods["hard_hdr_of"] = timed(
        "hard_hdr_of",
        lambda: blend_hard_hdr_of(slabs, weights, apply_of=True),
    )

    diagnostics["overlap_ncc"] = {}
    for name, rgb in methods.items():
        scores = score_one_anchor(
            rgb,
            slabs,
            weights,
            RING_PAIRS,
            win=args.ncc_win,
            max_sample_per_pair=args.max_sample_per_pair,
        )
        diagnostics["overlap_ncc"][name] = scores.get("aggregate", {})
        agg = diagnostics["overlap_ncc"][name]
        if agg:
            print(
                f"[metric] {name}: NCC={agg['mean_ncc_pano_vs_winner']:.4f} "
                f"SSD={agg['mean_ssd_pano_vs_winner']:.2f}",
                flush=True,
            )

    if args.save_full:
        for name, rgb in methods.items():
            _save_rgb(out_dir / f"{run_name}_{name}.png", rgb)

    full_stack = _stack_named(methods, labels)
    _save_rgb(out_dir / f"{run_name}_full_stack.png", full_stack)

    thumb_methods = {
        k: cv2.resize(v, (args.erp_w // 4, args.erp_h // 4), interpolation=cv2.INTER_AREA)
        for k, v in methods.items()
    }
    _save_rgb(out_dir / f"{run_name}_thumb_stack.png", _stack_named(thumb_methods, labels))

    for crop_name, crop in _default_crops(args.erp_h, args.erp_w).items():
        _save_rgb(out_dir / f"{run_name}_{crop_name}_crop_stack.png", _stack_named(methods, labels, crop=crop))

    with open(out_dir / f"{run_name}_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, ensure_ascii=False)
    print(f"[saved] {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
