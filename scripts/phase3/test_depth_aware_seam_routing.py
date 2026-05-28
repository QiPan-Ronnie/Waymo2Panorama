"""Compare RGB-only DP seam routing vs dense-depth-aware DP seam routing.

This is the first test where dense depth affects the seam path itself, not only
post-hoc color repair gating. Final pixels still come from real L1 source
slabs, so any visual improvement is source-faithful.
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
sys.path.insert(0, str(HERE))

from dense_depth_edge_seam_probe import (  # noqa: E402
    DEFAULT_CASES,
    _json_safe,
    _parse_case,
    build_dense_depth_slabs,
    compute_dense_depth_edge_risk,
)
from seam_confidence_map import _crop_stack, _default_crops, _heatmap_u8, _overlay_risk, _resize_w, _save_rgb, _stack_named  # noqa: E402
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.blending.seam_routing import blend_seam_routing, seam_mask_to_rgb  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402
from measure_overlap_ncc import score_one_anchor  # noqa: E402


def _label(rgb: np.ndarray, text: str, label_h: int = 34) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    band = np.zeros((label_h, rgb.shape[1], 3), dtype=np.uint8)
    cv2.putText(band, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([band, rgb])


def _save(path: Path, rgb: np.ndarray, quality: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        img.save(path, quality=quality)
    else:
        img.save(path)


def _seam_gap_y(rgb: np.ndarray, label_map: np.ndarray, valid: np.ndarray) -> dict[str, float | int]:
    y = cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)
    seam = np.zeros(label_map.shape, dtype=bool)
    seam[:, :-1] = (label_map[:, :-1] != label_map[:, 1:]) & valid[:, :-1] & valid[:, 1:]
    rows, cols = np.where(seam[:, :-1])
    if rows.size == 0:
        return {"n": 0}
    gaps = np.abs(y[rows, cols] - y[rows, cols + 1])
    return {
        "n": int(gaps.size),
        "mean_delta_y": float(gaps.mean()),
        "median_delta_y": float(np.median(gaps)),
        "p90_delta_y": float(np.percentile(gaps, 90)),
        "p95_delta_y": float(np.percentile(gaps, 95)),
    }


def _winner_label(weights: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    stack = np.stack(weights, axis=0)
    label = stack.argmax(axis=0).astype(np.int16)
    valid = stack.max(axis=0) > 1e-6
    return label, valid


def _changed_fraction(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.any(a.astype(np.uint8) != b.astype(np.uint8), axis=2)))


def _one_case(case_spec: str, av2_root: Path, out_root: Path, args: argparse.Namespace) -> dict[str, object]:
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, av2_root)
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    out_dir = out_root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    print(f"[case] {run_name}", flush=True)
    loader = AV2RingLoader(log_dir)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[anchor_idx])

    t0 = time.time()
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
    project_s = time.time() - t0

    hard = hard_select(slabs, weights)
    label, valid = _winner_label(weights)
    multiband = multiband_blend(slabs, weights)

    depth_slabs, _depth_weights, depth_model_diag = build_dense_depth_slabs(
        frame,
        erp_hw=erp_hw,
        model_id=args.model_id,
        device=args.device,
    )
    dense_maps, dense_diag = compute_dense_depth_edge_risk(
        depth_slabs,
        weights,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
    )
    dense_risk = dense_maps["dense_depth_risk"].astype(np.float32)

    rgb_route, rgb_diag = blend_seam_routing(
        slabs,
        weights,
        return_diagnostics=True,
        band_half_width=args.band_half_width,
        max_step=args.max_step,
    )
    depth_route, depth_diag = blend_seam_routing(
        slabs,
        weights,
        return_diagnostics=True,
        band_half_width=args.band_half_width,
        max_step=args.max_step,
        external_cost=dense_risk,
        external_weight=args.external_weight,
    )

    methods = {
        "multiband": multiband,
        "hard_select": hard,
        "rgb_route": rgb_route,
        "depth_route": depth_route,
    }
    ncc = {
        name: score_one_anchor(
            rgb,
            slabs,
            weights,
            RING_PAIRS,
            win=args.ncc_win,
            max_sample_per_pair=args.max_sample_per_pair,
        ).get("aggregate", {})
        for name, rgb in methods.items()
    }
    seam_gap = {name: _seam_gap_y(rgb, label, valid) for name, rgb in methods.items()}

    dense_overlay = _overlay_risk(hard, dense_risk, dense_maps["seam_core"].astype(bool))
    depth_path = seam_mask_to_rgb(depth_diag["seam_mask"], depth_route)
    rgb_path = seam_mask_to_rgb(rgb_diag["seam_mask"], rgb_route)
    changed_overlay = hard.copy()
    changed = np.any(depth_route.astype(np.uint8) != hard.astype(np.uint8), axis=2)
    if changed.any():
        mask = cv2.dilate(changed.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1).astype(bool)
        changed_overlay[mask] = np.array([255, 0, 0], dtype=np.uint8)

    review_rows = [
        ("hard_select", hard),
        ("rgb_route", rgb_route),
        ("depth_route", depth_route),
        ("rgb_route_path", rgb_path),
        ("depth_route_path", depth_path),
        ("dense_depth_risk", dense_overlay),
        ("depth_route_changed_vs_hard", changed_overlay),
    ]
    review = _stack_named([(name, _resize_w(img, args.review_w)) for name, img in review_rows])
    _save(out_dir / f"{run_name}_depth_aware_route_review_{args.review_w}.jpg", review, quality=88)

    crops = _default_crops(args.erp_h, args.erp_w)
    crop_review = _crop_stack(
        [
            ("hard_select", hard),
            ("rgb_route", rgb_route),
            ("depth_route", depth_route),
            ("depth_path", depth_path),
            ("dense_risk", dense_overlay),
        ],
        crops,
    )
    _save(out_dir / f"{run_name}_depth_aware_route_crop_review.jpg", crop_review, quality=88)
    _save(out_dir / f"{run_name}_hard_select_{args.review_w}.jpg", _resize_w(hard, args.review_w), quality=90)
    _save(out_dir / f"{run_name}_depth_route_{args.review_w}.jpg", _resize_w(depth_route, args.review_w), quality=90)

    def strip_diag(diag: dict) -> dict:
        out = dict(diag)
        out.pop("label_map", None)
        out.pop("seam_mask", None)
        return out

    diag = {
        "case": run_name,
        "params": {
            "model_id": args.model_id,
            "band_half_width": args.band_half_width,
            "max_step": args.max_step,
            "external_weight": args.external_weight,
        },
        "dense_depth_global": dense_diag["global"],
        "overlap_ncc": ncc,
        "seam_gap_y": seam_gap,
        "changed_fraction_vs_hard": {
            "rgb_route": _changed_fraction(rgb_route, hard),
            "depth_route": _changed_fraction(depth_route, hard),
            "depth_route_vs_rgb_route": _changed_fraction(depth_route, rgb_route),
        },
        "routing": {
            "rgb_route": strip_diag(rgb_diag),
            "depth_route": strip_diag(depth_diag),
        },
        "depth_model": depth_model_diag,
        "runtime_s": {
            "project_rgb": round(project_s, 3),
        },
        "outputs": {
            "review": f"{run_name}_depth_aware_route_review_{args.review_w}.jpg",
            "crop_review": f"{run_name}_depth_aware_route_crop_review.jpg",
            "hard_select": f"{run_name}_hard_select_{args.review_w}.jpg",
            "depth_route": f"{run_name}_depth_route_{args.review_w}.jpg",
        },
    }
    with open(out_dir / f"{run_name}_depth_aware_route_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(diag), f, indent=2, ensure_ascii=False)

    print(
        json.dumps(
            {
                "case": run_name,
                "changed_depth_vs_hard": diag["changed_fraction_vs_hard"]["depth_route"],
                "changed_depth_vs_rgb": diag["changed_fraction_vs_hard"]["depth_route_vs_rgb_route"],
                "hard_ncc": ncc["hard_select"].get("mean_ncc_pano_vs_winner"),
                "rgb_ncc": ncc["rgb_route"].get("mean_ncc_pano_vs_winner"),
                "depth_ncc": ncc["depth_route"].get("mean_ncc_pano_vs_winner"),
                "hard_dY": seam_gap["hard_select"].get("mean_delta_y"),
                "depth_dY": seam_gap["depth_route"].get("mean_delta_y"),
            },
            indent=2,
        ),
        flush=True,
    )
    return diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/depth_aware_seam_routing_v1"))
    ap.add_argument("--cases", nargs="+", default=DEFAULT_CASES)
    ap.add_argument("--model-id", default="depth-anything/Depth-Anything-V2-Small-hf")
    ap.add_argument("--device", type=int, default=0)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=64)
    ap.add_argument("--core-half-width", type=int, default=2)
    ap.add_argument("--max-step", type=int, default=3)
    ap.add_argument("--external-weight", type=float, default=4.0)
    ap.add_argument("--ncc-win", type=int, default=9)
    ap.add_argument("--max-sample-per-pair", type=int, default=20000)
    ap.add_argument("--review-w", type=int, default=1024)
    args = ap.parse_args()
    if args.ncc_win % 2 == 0:
        args.ncc_win += 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_diags = [_one_case(case, args.av2_root, args.out_dir, args) for case in args.cases]

    compact_rows = []
    for d in all_diags:
        case_dir = args.out_dir / d["case"]
        p = case_dir / d["outputs"]["crop_review"]
        if p.exists():
            img = cv2.cvtColor(cv2.imread(str(p), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            compact_rows.append((d["case"], _resize_w(img, args.review_w)))
    if compact_rows:
        _save_rgb(args.out_dir / "depth_aware_route_three_anchor_compact_review.jpg", _stack_named(compact_rows), quality=88)

    def mean_metric(method: str, key: str) -> float | None:
        vals = [d["overlap_ncc"][method].get(key) for d in all_diags if d["overlap_ncc"][method].get(key) is not None]
        return float(np.mean(vals)) if vals else None

    summary = {
        "run": args.out_dir.name,
        "cases": all_diags,
        "aggregate": {
            "n_cases": len(all_diags),
            "mean_changed_depth_vs_hard": float(np.mean([d["changed_fraction_vs_hard"]["depth_route"] for d in all_diags])),
            "mean_changed_depth_vs_rgb": float(np.mean([d["changed_fraction_vs_hard"]["depth_route_vs_rgb_route"] for d in all_diags])),
            "mean_hard_ncc": mean_metric("hard_select", "mean_ncc_pano_vs_winner"),
            "mean_rgb_route_ncc": mean_metric("rgb_route", "mean_ncc_pano_vs_winner"),
            "mean_depth_route_ncc": mean_metric("depth_route", "mean_ncc_pano_vs_winner"),
            "mean_hard_dY": float(np.mean([d["seam_gap_y"]["hard_select"].get("mean_delta_y", 0.0) for d in all_diags])),
            "mean_depth_route_dY": float(np.mean([d["seam_gap_y"]["depth_route"].get("mean_delta_y", 0.0) for d in all_diags])),
        },
    }
    with open(args.out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(summary), f, indent=2, ensure_ascii=False)
    print(json.dumps(_json_safe(summary["aggregate"]), indent=2), flush=True)
    print(f"[saved] {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
