"""Compare DP seam routing against hard-select baselines on AV2 anchors."""
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

from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.blending.seam_local_align import blend_seam_local_align  # noqa: E402
from waymo2panorama.blending.seam_routing import (  # noqa: E402
    blend_seam_routing,
    seam_mask_to_rgb,
)
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402
from measure_overlap_ncc import score_one_anchor  # noqa: E402


def _save_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path)


def _resize_w(rgb: np.ndarray, width: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    height = max(1, round(h * width / w))
    return cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)


def _label_panel(rgb: np.ndarray, label: str, label_h: int = 40) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    band = np.zeros((label_h, rgb.shape[1], 3), dtype=np.uint8)
    cv2.putText(
        band,
        label,
        (12, 27),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
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


def _default_crops(H: int, W: int) -> dict[str, tuple[int, int, int, int]]:
    return {
        "front_right_bmw_like": (int(0.38 * H), int(0.72 * H), int(0.70 * W), W),
        "left_mid_ped_like": (int(0.30 * H), int(0.86 * H), int(0.10 * W), int(0.46 * W)),
    }


def _jsonable_diag(diag: dict) -> dict:
    out = dict(diag)
    out.pop("label_map", None)
    out.pop("seam_mask", None)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, default=0)
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--band-half-width", type=int, default=64)
    ap.add_argument("--max-step", type=int, default=3)
    ap.add_argument("--review-w", type=int, default=2048)
    ap.add_argument("--save-full", action="store_true")
    ap.add_argument("--max-sample-per-pair", type=int, default=20000)
    ap.add_argument("--ncc-win", type=int, default=9)
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

    labels = {
        "multiband": "L1 multiband baseline",
        "hard_select": "L1 hard_select",
        "seam_local_align": "L1 hard_select + seam-local align",
        "seam_routing": "L1 hard_select + DP seam routing",
        "seam_routing_path": "DP seam routing path overlay",
    }
    methods: dict[str, np.ndarray] = {}
    diagnostics: dict[str, object] = {
        "log_short": log_short,
        "anchor_idx": args.anchor_idx,
        "erp_hw": list(erp_hw),
        "params": {
            "band_half_width": args.band_half_width,
            "max_step": args.max_step,
            "review_w": args.review_w,
            "save_full": args.save_full,
            "ncc_win": args.ncc_win,
        },
        "runtime_s": {},
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
    methods["seam_local_align"] = timed(
        "seam_local_align",
        lambda: blend_seam_local_align(slabs, weights, band_half_width=48),
    )
    seam_out, seam_diag = timed(
        "seam_routing",
        lambda: blend_seam_routing(
            slabs,
            weights,
            return_diagnostics=True,
            band_half_width=args.band_half_width,
            max_step=args.max_step,
        ),
    )
    methods["seam_routing"] = seam_out
    diagnostics["seam_routing"] = _jsonable_diag(seam_diag)
    methods["seam_routing_path"] = seam_mask_to_rgb(seam_diag["seam_mask"], methods["seam_routing"])

    diagnostics["overlap_ncc"] = {}
    for name, rgb in methods.items():
        if name == "seam_routing_path":
            continue
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

    review_methods = {k: _resize_w(v, args.review_w) for k, v in methods.items()}
    for name, rgb in review_methods.items():
        _save_rgb(out_dir / f"{run_name}_{name}_{args.review_w}.jpg", rgb)
    _save_rgb(out_dir / f"{run_name}_review_stack_{args.review_w}.jpg", _stack_named(review_methods, labels))

    for crop_name, crop in _default_crops(args.erp_h, args.erp_w).items():
        _save_rgb(out_dir / f"{run_name}_{crop_name}_crop_stack.png", _stack_named(methods, labels, crop=crop))

    if args.save_full:
        for name, rgb in methods.items():
            _save_rgb(out_dir / f"{run_name}_{name}.png", rgb)

    with open(out_dir / f"{run_name}_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, ensure_ascii=False)
    print(f"[saved] {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
