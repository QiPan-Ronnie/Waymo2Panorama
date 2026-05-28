"""Metric parallax-budget maps for AV2 ring-camera seams.

This script quantifies the physical seam problem rather than trying another
stitcher. For each hard-select adjacent-camera seam, it uses AV2 LiDAR depth
and the two real camera centers to estimate how far the same 3D point would
move in ERP pixels when seen from camera A versus camera B.

If the budget is 10-40 px, a 2D seam/blend method can only hide or choose one
view; it cannot make both views geometrically correct on a single panorama.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))

from depth_visibility_seam_probe import DEFAULT_CASES, LOG_UUIDS, _json_safe, _parse_case  # noqa: E402
from seam_confidence_map import (  # noqa: E402
    _crop_stack,
    _default_crops,
    _heatmap_u8,
    _overlay_risk,
    _resize_w,
    _risk_stats,
    _save_rgb,
    _stack_named,
    compute_seam_risk_maps,
)
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.depth.lidar_to_erp_depth import (  # noqa: E402
    load_lidar_sweep_nearest_to_ts,
    project_lidar_to_erp_depth,
    visualize_depth_map,
)
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


def _safe_corr(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float | None:
    good = mask & np.isfinite(a) & np.isfinite(b)
    if int(good.sum()) < 64:
        return None
    av = a[good].astype(np.float32)
    bv = b[good].astype(np.float32)
    av -= float(av.mean())
    bv -= float(bv.mean())
    denom = float(np.sqrt(np.sum(av * av) * np.sum(bv * bv)))
    if denom <= 1e-8:
        return None
    return float(np.sum(av * bv) / denom)


def _erp_rays(erp_hw: tuple[int, int]) -> np.ndarray:
    H, W = erp_hw
    uu, vv = np.meshgrid(np.arange(W, dtype=np.float64), np.arange(H, dtype=np.float64))
    theta = np.pi - (uu + 0.5) / W * 2.0 * np.pi
    phi = np.pi / 2.0 - (vv + 0.5) / H * np.pi
    cos_phi = np.cos(phi)
    return np.stack([cos_phi * np.cos(theta), cos_phi * np.sin(theta), np.sin(phi)], axis=-1)


def _unit_to_erp_px(unit: np.ndarray, erp_hw: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    H, W = erp_hw
    x, y, z = unit[..., 0], unit[..., 1], unit[..., 2]
    theta = np.arctan2(y, x)
    horiz = np.sqrt(np.maximum(x * x + y * y, 1e-12))
    phi = np.arctan2(z, horiz)
    u = (np.pi - theta) / (2.0 * np.pi) * W - 0.5
    v = (np.pi / 2.0 - phi) / np.pi * H - 0.5
    return u.astype(np.float32), v.astype(np.float32)


def _pair_exact_parallax_px(
    depth_map: np.ndarray,
    ray_ego: np.ndarray,
    cam_center_a: np.ndarray,
    cam_center_b: np.ndarray,
    erp_hw: tuple[int, int],
    support: np.ndarray,
) -> np.ndarray:
    """Exact ERP displacement of a LiDAR-supported point from two camera centers."""
    out = np.zeros(depth_map.shape, dtype=np.float32)
    yy, xx = np.where(support)
    if yy.size == 0:
        return out
    rays = ray_ego[yy, xx].astype(np.float64)
    P = rays * depth_map[yy, xx, None].astype(np.float64)
    va = P - cam_center_a[None, :]
    vb = P - cam_center_b[None, :]
    va /= np.maximum(np.linalg.norm(va, axis=1, keepdims=True), 1e-9)
    vb /= np.maximum(np.linalg.norm(vb, axis=1, keepdims=True), 1e-9)
    ua, va_px = _unit_to_erp_px(va, erp_hw)
    ub, vb_px = _unit_to_erp_px(vb, erp_hw)
    W = erp_hw[1]
    du = np.abs(ua - ub)
    du = np.minimum(du, W - du)
    dv = np.abs(va_px - vb_px)
    out[yy, xx] = np.sqrt(du * du + dv * dv).astype(np.float32)
    return out


def _band_stats(values: np.ndarray, mask: np.ndarray) -> dict[str, object]:
    if not mask.any():
        return {"n": 0}
    vals = values[mask].astype(np.float32)
    return {
        "n": int(vals.size),
        "mean": float(vals.mean()),
        "median": float(np.median(vals)),
        "p75": float(np.percentile(vals, 75)),
        "p90": float(np.percentile(vals, 90)),
        "p95": float(np.percentile(vals, 95)),
        "p99": float(np.percentile(vals, 99)),
        "frac_ge_2px": float(np.mean(vals >= 2.0)),
        "frac_ge_5px": float(np.mean(vals >= 5.0)),
        "frac_ge_10px": float(np.mean(vals >= 10.0)),
        "frac_ge_20px": float(np.mean(vals >= 20.0)),
        "frac_ge_40px": float(np.mean(vals >= 40.0)),
    }


def compute_parallax_budget_maps(
    depth_map: np.ndarray,
    weights: Sequence[np.ndarray],
    cam_centers: Sequence[np.ndarray],
    erp_hw: tuple[int, int],
    band_half_width: int,
    core_half_width: int,
    depth_support_max_m: float,
    risk_high_px: float,
) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    H, W = erp_hw
    ray_ego = _erp_rays(erp_hw)
    support = np.isfinite(depth_map) & (depth_map < depth_support_max_m)
    parallax = np.zeros((H, W), dtype=np.float32)
    seam_band = np.zeros((H, W), dtype=bool)
    seam_core = np.zeros((H, W), dtype=bool)
    seam_support = np.zeros((H, W), dtype=bool)
    pair_diags: list[dict[str, object]] = []

    for i, j in RING_PAIRS:
        wi = weights[i].astype(np.float32)
        wj = weights[j].astype(np.float32)
        overlap = (wi > 1e-6) & (wj > 1e-6)
        band, signed = build_voronoi_seam_band(wi, wj, band_half_width=band_half_width, threshold=1e-6)
        band &= overlap
        core = band & np.isfinite(signed) & (np.abs(signed) <= float(core_half_width))
        if not band.any():
            pair_diags.append({"pair": [int(i), int(j)], "status": "no_band"})
            continue
        pair_support = band & support
        pair_px = _pair_exact_parallax_px(
            depth_map,
            ray_ego,
            cam_centers[i].astype(np.float64),
            cam_centers[j].astype(np.float64),
            erp_hw,
            pair_support,
        )
        update = pair_support & (pair_px > parallax)
        parallax[update] = pair_px[update]
        seam_band |= band
        seam_core |= core
        seam_support |= pair_support
        baseline = float(np.linalg.norm(cam_centers[i] - cam_centers[j]))
        pair_diags.append(
            {
                "pair": [int(i), int(j)],
                "baseline_m": baseline,
                "band_pixels": int(band.sum()),
                "supported_pixels": int(pair_support.sum()),
                "supported_frac": float(pair_support.sum() / max(1, band.sum())),
                "parallax_px": _band_stats(pair_px, pair_support),
                "status": "ok",
            }
        )

    risk = np.clip(parallax / max(risk_high_px, 1e-6), 0.0, 1.0).astype(np.float32)
    maps = {
        "parallax_px": parallax,
        "parallax_risk": risk,
        "seam_band": seam_band.astype(np.float32),
        "seam_core": seam_core.astype(np.float32),
        "seam_support": seam_support.astype(np.float32),
        "seam_unknown": (seam_band & ~seam_support).astype(np.float32),
        "lidar_depth": depth_map.astype(np.float32),
    }
    diag = {
        "pair_diagnostics": pair_diags,
        "global": {
            "band_pixels": int(seam_band.sum()),
            "supported_pixels": int(seam_support.sum()),
            "supported_frac_of_band": float(seam_support.sum() / max(1, seam_band.sum())),
            "unknown_frac_of_band": float((seam_band & ~seam_support).sum() / max(1, seam_band.sum())),
            "parallax_px": _band_stats(parallax, seam_support),
            "severe_pixels_ge_10px": int(((parallax >= 10.0) & seam_support).sum()),
            "severe_frac_supported_ge_10px": float(((parallax >= 10.0) & seam_support).sum() / max(1, seam_support.sum())),
            "extreme_pixels_ge_20px": int(((parallax >= 20.0) & seam_support).sum()),
            "extreme_frac_supported_ge_20px": float(((parallax >= 20.0) & seam_support).sum() / max(1, seam_support.sum())),
        },
    }
    return maps, diag


def _overlay_unknown(rgb: np.ndarray, unknown: np.ndarray) -> np.ndarray:
    out = np.clip(rgb, 0, 255).astype(np.uint8).copy()
    m = unknown.astype(bool)
    if m.any():
        out[m] = (0.45 * out[m].astype(np.float32) + 0.55 * np.array([255, 0, 255], dtype=np.float32)).astype(np.uint8)
    return out


def _one_case(case_spec: str, av2_root: Path, out_root: Path, args: argparse.Namespace) -> dict[str, object]:
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, av2_root)
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    out_dir = out_root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    print(f"[case] {run_name}", flush=True)

    loader = AV2RingLoader(log_dir)
    ts = loader.anchor_timestamps_ns()
    anchor_ts = ts[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)

    t0 = time.time()
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    centers: list[np.ndarray] = []
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
        centers.append(calib.T_ego_cam[:3, 3].astype(np.float64))
    project_s = time.time() - t0
    hard = hard_select(slabs, weights)

    source_maps, source_diag = compute_seam_risk_maps(
        slabs,
        weights,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        ncc_win=args.ncc_win,
    )
    pts, sweep_ts, lidar_delta_ms = load_lidar_sweep_nearest_to_ts(log_dir, anchor_ts, max_delta_ms=args.lidar_max_delta_ms)
    depth_map, depth_summary = project_lidar_to_erp_depth(
        pts,
        erp_hw=erp_hw,
        min_range_m=args.min_range_m,
        max_range_m=args.max_range_m,
        densify_radius_px=args.densify_radius_px,
        fill_far_m=args.fill_far_m,
    )
    maps, diag = compute_parallax_budget_maps(
        depth_map,
        weights,
        centers,
        erp_hw=erp_hw,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        depth_support_max_m=args.depth_support_max_m,
        risk_high_px=args.risk_high_px,
    )
    seam_support = maps["seam_support"].astype(bool)
    source_risk = source_maps["risk"].astype(np.float32)
    structure_risk = source_maps["structure_risk"].astype(np.float32)
    color_risk = source_maps["color_risk"].astype(np.float32)
    parallax = maps["parallax_px"].astype(np.float32)
    risk = maps["parallax_risk"].astype(np.float32)

    parallax_overlay = _overlay_unknown(
        _overlay_risk(hard, risk, maps["seam_core"].astype(bool)),
        maps["seam_unknown"].astype(bool),
    )
    source_overlay = _overlay_risk(hard, source_risk, source_maps["seam_core"].astype(bool))
    depth_viz = visualize_depth_map(depth_map, log_clip_m=args.max_range_m)
    heat = _heatmap_u8(risk)

    crops = _default_crops(args.erp_h, args.erp_w)
    crop_review = _crop_stack(
        [
            ("hard_select", hard),
            ("source_risk", source_overlay),
            ("parallax_budget magenta=unknown", parallax_overlay),
            ("parallax_heat", heat),
            ("lidar_depth", depth_viz),
        ],
        crops,
    )
    _save_rgb(out_dir / f"{run_name}_parallax_budget_crop_review.jpg", crop_review, quality=88)
    review = _stack_named(
        [
            ("hard_select", _resize_w(hard, args.review_w)),
            ("source_risk_overlay", _resize_w(source_overlay, args.review_w)),
            ("parallax_budget_overlay", _resize_w(parallax_overlay, args.review_w)),
            ("parallax_heat", _resize_w(heat, args.review_w)),
            ("lidar_depth", _resize_w(depth_viz, args.review_w)),
        ]
    )
    _save_rgb(out_dir / f"{run_name}_parallax_budget_review_{args.review_w}.jpg", review, quality=88)

    diag.update(
        {
            "case": run_name,
            "log_short": short,
            "anchor_idx": int(anchor_idx),
            "lidar_delta_ms": float(lidar_delta_ms),
            "lidar_sweep_ts_ns": int(sweep_ts),
            "lidar_depth_summary": depth_summary,
            "source_risk_global": source_diag["global"],
            "correlations_on_supported_seam": {
                "parallax_vs_source_risk": _safe_corr(parallax, source_risk, seam_support),
                "parallax_vs_structure_risk": _safe_corr(parallax, structure_risk, seam_support),
                "parallax_vs_color_risk": _safe_corr(parallax, color_risk, seam_support),
            },
            "params": {
                "band_half_width": args.band_half_width,
                "core_half_width": args.core_half_width,
                "densify_radius_px": args.densify_radius_px,
                "risk_high_px": args.risk_high_px,
            },
            "runtime_s": {"project_rgb": round(project_s, 3)},
            "outputs": {
                "review": f"{run_name}_parallax_budget_review_{args.review_w}.jpg",
                "crop_review": f"{run_name}_parallax_budget_crop_review.jpg",
            },
        }
    )
    with open(out_dir / f"{run_name}_parallax_budget_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(diag), f, indent=2, ensure_ascii=False)
    print(
        json.dumps(
            {
                "case": run_name,
                "support_frac": diag["global"]["supported_frac_of_band"],
                "median_px": diag["global"]["parallax_px"]["median"],
                "p90_px": diag["global"]["parallax_px"]["p90"],
                "p95_px": diag["global"]["parallax_px"]["p95"],
                "frac_ge_10px": diag["global"]["parallax_px"]["frac_ge_10px"],
                "frac_ge_20px": diag["global"]["parallax_px"]["frac_ge_20px"],
                "corr": diag["correlations_on_supported_seam"],
            },
            indent=2,
        ),
        flush=True,
    )
    return diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/parallax_budget_map_v1"))
    ap.add_argument("--cases", nargs="+", default=DEFAULT_CASES)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=48)
    ap.add_argument("--core-half-width", type=int, default=2)
    ap.add_argument("--ncc-win", type=int, default=21)
    ap.add_argument("--min-range-m", type=float, default=0.5)
    ap.add_argument("--max-range-m", type=float, default=80.0)
    ap.add_argument("--densify-radius-px", type=int, default=10)
    ap.add_argument("--fill-far-m", type=float, default=1000.0)
    ap.add_argument("--depth-support-max-m", type=float, default=120.0)
    ap.add_argument("--lidar-max-delta-ms", type=float, default=75.0)
    ap.add_argument("--risk-high-px", type=float, default=20.0)
    ap.add_argument("--review-w", type=int, default=1024)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_diags = [_one_case(case, args.av2_root, args.out_dir, args) for case in args.cases]

    compact_rows = []
    for d in all_diags:
        p = args.out_dir / d["case"] / d["outputs"]["crop_review"]
        if p.exists():
            img = cv2.cvtColor(cv2.imread(str(p), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            compact_rows.append((d["case"], _resize_w(img, args.review_w)))
    if compact_rows:
        _save_rgb(args.out_dir / "parallax_budget_three_anchor_compact_review.jpg", _stack_named(compact_rows), quality=88)

    summary = {
        "run": args.out_dir.name,
        "cases": all_diags,
        "aggregate": {
            "n_cases": len(all_diags),
            "mean_supported_frac_of_band": float(np.mean([d["global"]["supported_frac_of_band"] for d in all_diags])),
            "mean_p90_parallax_px": float(np.mean([d["global"]["parallax_px"]["p90"] for d in all_diags])),
            "mean_frac_ge_10px": float(np.mean([d["global"]["parallax_px"]["frac_ge_10px"] for d in all_diags])),
            "mean_frac_ge_20px": float(np.mean([d["global"]["parallax_px"]["frac_ge_20px"] for d in all_diags])),
        },
    }
    with open(args.out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(summary), f, indent=2, ensure_ascii=False)
    print(json.dumps(_json_safe(summary["aggregate"]), indent=2), flush=True)
    print(f"[saved] {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
