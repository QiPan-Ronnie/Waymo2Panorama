"""Evaluate raw-AV2 ground-plane seam layer against L1 hard_select.

This is a projection-geometry experiment, not another seam-routing tweak:
road-plane candidates are sampled by intersecting virtual ERP rays with z=0
and projecting those 3D points into each true camera. Final pixels remain
source-faithful: every output pixel is copied from one original AV2 camera.
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

from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.ground_plane_layer import (  # noqa: E402
    compose_ground_plane_hybrid,
    estimate_panorama_center_z,
    render_ground_plane_to_erp,
    replace_mask_overlay,
)
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402
from measure_overlap_ncc import score_one_anchor  # noqa: E402


DEFAULT_LOGS = {
    "02a00399": "02a00399-3857-444e-8db3-a8f58489c394",
    "fbee355f": "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
    "0bae3b5e": "0bae3b5e-417d-3b03-abaa-806b433233b8",
}
DEFAULT_CASES = ["02a00399:0:bmw", "fbee355f:95:ped_obj", "0bae3b5e:30:clean_far"]


def _save_rgb(path: Path, rgb: np.ndarray, quality: int = 88) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        img.save(path, quality=quality)
    else:
        img.save(path)


def _resize_w(rgb: np.ndarray, width: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    height = max(1, round(h * width / w))
    return cv2.resize(np.clip(rgb, 0, 255).astype(np.uint8), (width, height), interpolation=cv2.INTER_AREA)


def _label_panel(rgb: np.ndarray, label: str, label_h: int = 40) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    band = np.zeros((label_h, rgb.shape[1], 3), dtype=np.uint8)
    cv2.putText(band, label, (12, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([band, rgb])


def _stack_named(methods: dict[str, np.ndarray], labels: dict[str, str], crop=None) -> np.ndarray:
    rows = []
    for name, rgb in methods.items():
        view = rgb
        if crop is not None:
            y0, y1, x0, x1 = crop
            view = view[y0:y1, x0:x1]
        rows.append(_label_panel(view, labels.get(name, name)))
    width = max(row.shape[1] for row in rows)
    padded = []
    for row in rows:
        if row.shape[1] < width:
            pad = np.zeros((row.shape[0], width - row.shape[1], 3), dtype=np.uint8)
            row = np.hstack([row, pad])
        padded.append(row)
    return np.vstack(padded)


def _default_crops(H: int, W: int) -> dict[str, tuple[int, int, int, int]]:
    return {
        "left_lane": (int(0.38 * H), int(0.66 * H), int(0.03 * W), int(0.30 * W)),
        "mid_lane": (int(0.36 * H), int(0.66 * H), int(0.20 * W), int(0.50 * W)),
        "right_suv": (int(0.20 * H), int(0.65 * H), int(0.66 * W), int(0.94 * W)),
        "center_front": (int(0.18 * H), int(0.68 * H), int(0.39 * W), int(0.70 * W)),
    }


def _parse_case(case: str, av2_root: Path) -> tuple[Path, int, str]:
    parts = case.split(":")
    if len(parts) < 2:
        raise ValueError(f"case must be LOGSHORT:ANCHOR[:NAME], got {case!r}")
    log_key = parts[0]
    anchor = int(parts[1])
    name = parts[2] if len(parts) > 2 else f"a{anchor:03d}"
    if Path(log_key).exists():
        log_dir = Path(log_key)
        short = log_dir.name.split("-")[0]
    else:
        log_name = DEFAULT_LOGS.get(log_key, log_key)
        log_dir = av2_root / log_name
        short = log_key.split("-")[0]
    return log_dir, anchor, f"{short}_a{anchor:03d}_{name}"


def _project_sphere_and_ground(log_dir: Path, anchor_idx: int, erp_hw: tuple[int, int], args) -> tuple[list, list, list, list, dict]:
    loader = AV2RingLoader(log_dir)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[anchor_idx])
    center_z = args.center_z_m
    if center_z <= 0:
        center_z = estimate_panorama_center_z([frame.calibrations[c].T_ego_cam for c in RING_CAMS_7])
    center = np.array([0.0, 0.0, center_z], dtype=np.float64)
    sphere_slabs: list[np.ndarray] = []
    sphere_weights: list[np.ndarray] = []
    ground_slabs: list[np.ndarray] = []
    ground_weights: list[np.ndarray] = []
    ground_stats: list[dict] = []

    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=frame.images[cam],
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=None,
        )
        sphere_slabs.append(rgb)
        sphere_weights.append(w)
        gp = render_ground_plane_to_erp(
            image=frame.images[cam],
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            panorama_center_ego=center,
            ground_z_m=args.ground_z_m,
            min_distance_m=args.min_ground_dist_m,
            max_distance_m=args.max_ground_dist_m,
            min_cam_cos=args.min_cam_cos,
        )
        ground_slabs.append(gp.rgb)
        ground_weights.append(gp.weight)
        valid_dist = gp.distance_m[np.isfinite(gp.distance_m)]
        ground_stats.append(
            {
                "cam": cam,
                "ground_valid_pixels": int(gp.alpha.sum()),
                "ground_valid_frac": float(gp.alpha.mean()),
                "ground_weight_mean": float(gp.weight[gp.alpha].mean()) if gp.alpha.any() else 0.0,
                "ground_dist_p50": float(np.percentile(valid_dist, 50)) if valid_dist.size else None,
                "ground_dist_p90": float(np.percentile(valid_dist, 90)) if valid_dist.size else None,
            }
        )
    meta = {
        "panorama_center_ego": [0.0, 0.0, float(center_z)],
        "ground_stats": ground_stats,
        "anchor_timestamp_ns": int(frame.anchor_timestamp_ns),
    }
    return sphere_slabs, sphere_weights, ground_slabs, ground_weights, meta


def _score_methods(methods: dict[str, np.ndarray], slabs, weights, args) -> dict[str, dict]:
    scores: dict[str, dict] = {}
    for name, rgb in methods.items():
        if name.endswith("_overlay"):
            continue
        one = score_one_anchor(
            rgb,
            slabs,
            weights,
            RING_PAIRS,
            win=args.ncc_win,
            max_sample_per_pair=args.max_sample_per_pair,
        )
        scores[name] = one.get("aggregate", {})
    return scores


def _jsonable_diag(diag: dict) -> dict:
    out = dict(diag)
    out.pop("replace_mask", None)
    return out


def run_case(log_dir: Path, anchor_idx: int, run_name: str, args) -> dict:
    out_dir = Path(args.out_dir)
    erp_hw = (args.erp_h, args.erp_w)
    print(f"[case] {run_name} log={log_dir} anchor={anchor_idx}", flush=True)
    t0 = time.time()
    sphere_slabs, sphere_weights, ground_slabs, ground_weights, meta = _project_sphere_and_ground(
        log_dir, anchor_idx, erp_hw, args
    )
    project_s = time.time() - t0
    print(f"[project sphere+ground] {project_s:.1f}s", flush=True)

    labels = {
        "multiband": "L1 multiband",
        "hard_select": "L1 hard_select",
        "ground_strict": "ground-plane seam strict",
        "ground_balanced": "ground-plane seam balanced",
        "ground_loose": "ground-plane seam loose",
        "ground_strict_overlay": "strict replace mask",
        "ground_balanced_overlay": "balanced replace mask",
        "ground_loose_overlay": "loose replace mask",
    }
    methods: dict[str, np.ndarray] = {}
    runtime_s: dict[str, float] = {"project_sphere_ground": round(project_s, 3)}
    diagnostics: dict[str, object] = {"projection": meta}

    def timed(name: str, fn):
        print(f"[run] {name}", flush=True)
        s0 = time.time()
        out = fn()
        runtime_s[name] = round(time.time() - s0, 3)
        print(f"[done] {name}: {runtime_s[name]:.2f}s", flush=True)
        return out

    methods["multiband"] = timed("multiband", lambda: multiband_blend(sphere_slabs, sphere_weights))
    methods["hard_select"] = timed("hard_select", lambda: hard_select(sphere_slabs, sphere_weights))

    variants = {
        "ground_strict": {
            "band_half_width": args.band_half_width,
            "agree_y_thresh": args.strict_agree_y,
            "min_ground_weight": args.strict_min_ground_weight,
        },
        "ground_balanced": {
            "band_half_width": args.band_half_width,
            "agree_y_thresh": args.balanced_agree_y,
            "min_ground_weight": args.balanced_min_ground_weight,
        },
        "ground_loose": {
            "band_half_width": args.loose_band_half_width,
            "agree_y_thresh": args.loose_agree_y,
            "min_ground_weight": args.loose_min_ground_weight,
        },
    }
    for name, kwargs in variants.items():
        out, diag = timed(
            name,
            lambda kwargs=kwargs: compose_ground_plane_hybrid(
                sphere_slabs,
                sphere_weights,
                ground_slabs,
                ground_weights,
                core_half_width=args.core_half_width,
                y_min_frac=args.y_min_frac,
                return_diagnostics=True,
                **kwargs,
            ),
        )
        methods[name] = out
        diagnostics[name] = _jsonable_diag(diag)
        methods[f"{name}_overlay"] = replace_mask_overlay(methods["hard_select"], diag["replace_mask"])

    scores = _score_methods(methods, sphere_slabs, sphere_weights, args)
    for name, agg in scores.items():
        if agg:
            print(
                f"[metric] {name}: NCC={agg.get('mean_ncc_pano_vs_winner', 0):.4f} "
                f"SSD={agg.get('mean_ssd_pano_vs_winner', 0):.2f}",
                flush=True,
            )

    review_methods = {k: _resize_w(v, args.review_w) for k, v in methods.items()}
    for name, rgb in review_methods.items():
        _save_rgb(out_dir / f"{run_name}_{name}_w{args.review_w}.jpg", rgb, args.jpg_quality)
    _save_rgb(out_dir / f"{run_name}_review_stack_w{args.review_w}.jpg", _stack_named(review_methods, labels), args.jpg_quality)

    crop_methods = {
        k: v
        for k, v in methods.items()
        if k in {"multiband", "hard_select", "ground_strict", "ground_balanced", "ground_loose"}
    }
    for crop_name, crop in _default_crops(args.erp_h, args.erp_w).items():
        _save_rgb(out_dir / f"{run_name}_{crop_name}_crop_stack.png", _stack_named(crop_methods, labels, crop=crop))

    if args.save_full:
        for name in ["hard_select", "ground_strict", "ground_balanced", "ground_loose"]:
            _save_rgb(out_dir / f"{run_name}_{name}.png", methods[name])

    diag_out = {
        "run_name": run_name,
        "log_dir": str(log_dir),
        "anchor_idx": anchor_idx,
        "erp_hw": list(erp_hw),
        "params": vars(args),
        "runtime_s": runtime_s,
        "overlap_ncc_vs_sphere_winner": scores,
        "diagnostics": diagnostics,
    }
    with open(out_dir / f"{run_name}_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diag_out, f, indent=2, ensure_ascii=False)
    return diag_out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", default="/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
    ap.add_argument("--case", action="append", help="LOGSHORT:ANCHOR[:NAME]. Defaults to three seam anchors.")
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--center-z-m", type=float, default=-1.0, help="<=0 means median ring-camera z.")
    ap.add_argument("--ground-z-m", type=float, default=0.0)
    ap.add_argument("--min-ground-dist-m", type=float, default=1.5)
    ap.add_argument("--max-ground-dist-m", type=float, default=55.0)
    ap.add_argument("--min-cam-cos", type=float, default=0.04)
    ap.add_argument("--y-min-frac", type=float, default=0.42)
    ap.add_argument("--band-half-width", type=int, default=96)
    ap.add_argument("--loose-band-half-width", type=int, default=132)
    ap.add_argument("--core-half-width", type=int, default=3)
    ap.add_argument("--strict-agree-y", type=float, default=14.0)
    ap.add_argument("--balanced-agree-y", type=float, default=22.0)
    ap.add_argument("--loose-agree-y", type=float, default=34.0)
    ap.add_argument("--strict-min-ground-weight", type=float, default=0.020)
    ap.add_argument("--balanced-min-ground-weight", type=float, default=0.012)
    ap.add_argument("--loose-min-ground-weight", type=float, default=0.006)
    ap.add_argument("--review-w", type=int, default=1400)
    ap.add_argument("--jpg-quality", type=int, default=84)
    ap.add_argument("--save-full", action="store_true")
    ap.add_argument("--max-sample-per-pair", type=int, default=20000)
    ap.add_argument("--ncc-win", type=int, default=9)
    args = ap.parse_args()
    if args.ncc_win % 2 == 0:
        args.ncc_win += 1
    if args.erp_w != 2 * args.erp_h:
        raise ValueError("ERP width should be 2x height")

    cases = args.case if args.case else DEFAULT_CASES
    av2_root = Path(args.av2_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_diags = []
    for case in cases:
        log_dir, anchor, run_name = _parse_case(case, av2_root)
        all_diags.append(run_case(log_dir, anchor, run_name, args))

    summary = {
        "cases": [
            {
                "run_name": d["run_name"],
                "anchor_idx": d["anchor_idx"],
                "runtime_s": d["runtime_s"],
                "overlap_ncc_vs_sphere_winner": d["overlap_ncc_vs_sphere_winner"],
                "ground_strict": d["diagnostics"]["ground_strict"],
                "ground_balanced": d["diagnostics"]["ground_balanced"],
                "ground_loose": d["diagnostics"]["ground_loose"],
            }
            for d in all_diags
        ]
    }
    with open(out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"[saved] {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
