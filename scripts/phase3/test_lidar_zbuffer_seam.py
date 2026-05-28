"""LiDAR z-buffer seam rendering probe.

This is the first depth route that treats depth as actual visibility evidence
rather than as a 2D seam cost. For seam-band pixels only, it renders the
LiDAR-supported 3D surface into the real cameras with per-camera z-buffer
visibility tests, then copies pixels from real AV2 images. No generated pixels,
no optical flow, and no learned depth are used.
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
from measure_overlap_ncc import score_one_anchor  # noqa: E402
from seam_confidence_map import (  # noqa: E402
    _crop_stack,
    _default_crops,
    _heatmap_u8,
    _label_panel,
    _resize_w,
    _save_rgb,
    _stack_named,
)
from seam_risk_gated_color_repair import _seam_gap_y  # noqa: E402
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.depth.lidar_to_erp_depth import (  # noqa: E402
    load_lidar_sweep_nearest_to_ts,
    project_lidar_to_erp_depth,
    visualize_depth_map,
)
from waymo2panorama.projection.lidar_zbuffer_layer import (  # noqa: E402
    build_ring_zbuffers,
    render_lidar_surface_to_erp,
)
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


def _winner_label(weights: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    stack = np.stack(weights, axis=0)
    label = stack.argmax(axis=0).astype(np.int16)
    valid = stack.max(axis=0) > 1e-6
    return label, valid


def _compose_from_label(slabs: Sequence[np.ndarray], label: np.ndarray, valid: np.ndarray) -> np.ndarray:
    out = np.zeros((*label.shape, 3), dtype=np.float32)
    for idx, slab in enumerate(slabs):
        m = (label == idx) & valid
        if m.any():
            out[m] = slab[m]
    return np.clip(out, 0, 255).astype(np.uint8)


def _seam_masks(
    weights: Sequence[np.ndarray],
    band_half_width: int,
    core_half_width: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    h, w = weights[0].shape
    band_all = np.zeros((h, w), dtype=bool)
    core_all = np.zeros((h, w), dtype=bool)
    pair_diags: list[dict[str, object]] = []
    for i, j in RING_PAIRS:
        wi = weights[i].astype(np.float32)
        wj = weights[j].astype(np.float32)
        overlap = (wi > 1e-6) & (wj > 1e-6)
        band, signed = build_voronoi_seam_band(wi, wj, band_half_width=band_half_width, threshold=1e-6)
        band &= overlap
        core = band & np.isfinite(signed) & (np.abs(signed) <= float(core_half_width))
        band_all |= band
        core_all |= core
        pair_diags.append(
            {
                "pair": [int(i), int(j)],
                "band_pixels": int(band.sum()),
                "core_pixels": int(core.sum()),
                "overlap_pixels": int(overlap.sum()),
            }
        )
    return band_all, core_all, {
        "band_pixels": int(band_all.sum()),
        "core_pixels": int(core_all.sum()),
        "pair_diags": pair_diags,
    }


def _to_y(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]


def _overlay_mask(rgb: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    out = np.clip(rgb, 0, 255).astype(np.uint8).copy()
    m = mask.astype(bool)
    if m.any():
        c = np.array(color, dtype=np.float32)
        out[m] = (0.45 * out[m].astype(np.float32) + 0.55 * c).astype(np.uint8)
    return out


def _variant_stats(
    name: str,
    out: np.ndarray,
    replace_mask: np.ndarray,
    hard: np.ndarray,
    label: np.ndarray,
    valid: np.ndarray,
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    ncc_win: int,
    max_sample_per_pair: int,
) -> dict[str, object]:
    changed = np.any(out.astype(np.int16) != hard.astype(np.int16), axis=-1)
    score = score_one_anchor(
        out,
        list(slabs),
        list(weights),
        RING_PAIRS,
        win=ncc_win,
        max_sample_per_pair=max_sample_per_pair,
    )
    return {
        "name": name,
        "replace_pixels": int(replace_mask.sum()),
        "replace_frac_erp": float(replace_mask.mean()),
        "changed_pixels": int(changed.sum()),
        "changed_frac_erp": float(changed.mean()),
        "seam_gap_y": _seam_gap_y(out, label, valid),
        "ncc_aggregate": score.get("aggregate", {}),
    }


def _compose_winner(
    hard: np.ndarray,
    label: np.ndarray,
    seam_band: np.ndarray,
    support: np.ndarray,
    lidar_slabs: Sequence[np.ndarray],
    lidar_visible: Sequence[np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    out = hard.copy()
    replace = np.zeros(label.shape, dtype=bool)
    for idx, slab in enumerate(lidar_slabs):
        m = seam_band & support & (label == idx) & lidar_visible[idx]
        if m.any():
            out[m] = np.clip(slab[m], 0, 255).astype(np.uint8)
            replace |= m
    return out, replace


def _compose_best(
    hard: np.ndarray,
    seam_band: np.ndarray,
    support: np.ndarray,
    lidar_slabs: Sequence[np.ndarray],
    lidar_weights: Sequence[np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    stack = np.stack(lidar_weights, axis=0)
    valid = stack.max(axis=0) > 1e-6
    lidar_best = hard_select(list(lidar_slabs), list(lidar_weights))
    replace = seam_band & support & valid
    out = hard.copy()
    out[replace] = lidar_best[replace]
    return out, replace


def _compose_consensus(
    hard: np.ndarray,
    label: np.ndarray,
    seam_band: np.ndarray,
    support: np.ndarray,
    sphere_weights: Sequence[np.ndarray],
    lidar_slabs: Sequence[np.ndarray],
    lidar_visible: Sequence[np.ndarray],
    *,
    band_half_width: int,
    y_agree_thresh: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    out = hard.copy()
    replace = np.zeros(label.shape, dtype=bool)
    pair_diags: list[dict[str, object]] = []
    lidar_y = [_to_y(s) for s in lidar_slabs]
    for i, j in RING_PAIRS:
        wi = sphere_weights[i].astype(np.float32)
        wj = sphere_weights[j].astype(np.float32)
        overlap = (wi > 1e-6) & (wj > 1e-6)
        band, _signed = build_voronoi_seam_band(wi, wj, band_half_width=band_half_width, threshold=1e-6)
        band &= overlap & seam_band & support
        both = band & lidar_visible[i] & lidar_visible[j]
        ydiff = np.abs(lidar_y[i].astype(np.float32) - lidar_y[j].astype(np.float32))
        ok = both & (ydiff <= float(y_agree_thresh))
        mi = ok & (label == i)
        mj = ok & (label == j)
        if mi.any():
            out[mi] = np.clip(lidar_slabs[i][mi], 0, 255).astype(np.uint8)
        if mj.any():
            out[mj] = np.clip(lidar_slabs[j][mj], 0, 255).astype(np.uint8)
        replace |= mi | mj
        vals = ydiff[both]
        pair_diags.append(
            {
                "pair": [int(i), int(j)],
                "band_pixels": int(band.sum()),
                "both_visible_pixels": int(both.sum()),
                "replace_pixels": int((mi | mj).sum()),
                "replace_frac_band": float((mi | mj).sum() / max(1, int(band.sum()))),
                "ydiff_visible_mean": float(vals.mean()) if vals.size else None,
                "ydiff_visible_p90": float(np.percentile(vals, 90)) if vals.size else None,
            }
        )
    return out, replace, {
        "pair_diags": pair_diags,
        "band_half_width": int(band_half_width),
        "y_agree_thresh": float(y_agree_thresh),
    }


def _one_case(case_spec: str, av2_root: Path, out_root: Path, args: argparse.Namespace) -> dict[str, object]:
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, av2_root)
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    out_dir = out_root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    print(f"[case] {run_name}", flush=True)
    t0 = time.time()

    loader = AV2RingLoader(log_dir)
    anchor_ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)

    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    images: list[np.ndarray] = []
    Ks: list[np.ndarray] = []
    Ts: list[np.ndarray] = []
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
        images.append(frame.images[cam])
        Ks.append(calib.K)
        Ts.append(calib.T_ego_cam)

    hard = hard_select(slabs, weights)
    multiband = multiband_blend(slabs, weights)
    label, valid = _winner_label(weights)
    seam_band, seam_core, seam_diag = _seam_masks(weights, args.band_half_width, args.core_half_width)

    pts, sweep_ts, lidar_delta_ms = load_lidar_sweep_nearest_to_ts(log_dir, anchor_ts, max_delta_ms=args.lidar_max_delta_ms)
    depth_map, depth_summary = project_lidar_to_erp_depth(
        pts,
        erp_hw=erp_hw,
        min_range_m=args.min_range_m,
        max_range_m=args.max_range_m,
        densify_radius_px=args.densify_radius_px,
        fill_far_m=args.fill_far_m,
    )
    zbuffers = build_ring_zbuffers(
        pts,
        images,
        Ks,
        Ts,
        min_range_m=args.min_range_m,
        max_range_m=args.max_range_m,
        dilation_px=args.zbuffer_dilation_px,
    )
    lidar_render = render_lidar_surface_to_erp(
        images,
        Ks,
        Ts,
        depth_map,
        zbuffers,
        depth_support_max_m=args.depth_support_max_m,
        min_cam_cos=args.min_cam_cos,
        z_tolerance_abs_m=args.z_tolerance_abs_m,
        z_tolerance_rel=args.z_tolerance_rel,
    )
    support = lidar_render.support_mask

    winner, mask_winner = _compose_winner(hard, label, seam_band, support, lidar_render.slabs, lidar_render.visible)
    best, mask_best = _compose_best(hard, seam_band, support, lidar_render.slabs, lidar_render.weights)
    consensus, mask_consensus, consensus_diag = _compose_consensus(
        hard,
        label,
        seam_band,
        support,
        weights,
        lidar_render.slabs,
        lidar_render.visible,
        band_half_width=args.band_half_width,
        y_agree_thresh=args.consensus_y_thresh,
    )

    variants = {
        "multiband": (multiband, np.zeros(seam_band.shape, dtype=bool)),
        "hard_select": (hard, np.zeros(seam_band.shape, dtype=bool)),
        "lidar_winner": (winner, mask_winner),
        "lidar_consensus": (consensus, mask_consensus),
        "lidar_best": (best, mask_best),
    }

    method_stats = {}
    for name, (rgb, mask) in variants.items():
        method_stats[name] = _variant_stats(
            name,
            rgb,
            mask,
            hard,
            label,
            valid,
            slabs,
            weights,
            args.ncc_win,
            args.max_sample_per_pair,
        )

    visibility_heat = _heatmap_u8(np.clip(lidar_render.visible_count.astype(np.float32) / 3.0, 0.0, 1.0))
    depth_viz = visualize_depth_map(depth_map, log_clip_m=args.max_range_m)
    seam_overlay = _overlay_mask(hard, seam_core, (255, 255, 255))
    winner_overlay = _overlay_mask(winner, mask_winner, (0, 255, 255))
    consensus_overlay = _overlay_mask(consensus, mask_consensus, (0, 255, 0))
    best_overlay = _overlay_mask(best, mask_best, (255, 80, 80))

    crops = _default_crops(args.erp_h, args.erp_w)
    crop_review = _crop_stack(
        [
            ("hard_select", hard),
            ("lidar_winner cyan=replace", winner_overlay),
            ("lidar_consensus green=replace", consensus_overlay),
            ("lidar_best red=replace", best_overlay),
            ("visible_count", visibility_heat),
            ("lidar_depth", depth_viz),
        ],
        crops,
    )
    _save_rgb(out_dir / f"{run_name}_lidar_zbuffer_crop_review.jpg", crop_review, quality=88)
    review = _stack_named(
        [
            ("multiband", _resize_w(multiband, args.review_w)),
            ("hard_select seam_core", _resize_w(seam_overlay, args.review_w)),
            ("lidar_winner", _resize_w(winner_overlay, args.review_w)),
            ("lidar_consensus", _resize_w(consensus_overlay, args.review_w)),
            ("lidar_best", _resize_w(best_overlay, args.review_w)),
            ("visible_count", _resize_w(visibility_heat, args.review_w)),
            ("lidar_depth", _resize_w(depth_viz, args.review_w)),
        ]
    )
    _save_rgb(out_dir / f"{run_name}_lidar_zbuffer_review_{args.review_w}.jpg", review, quality=88)
    for name, (rgb, _mask) in variants.items():
        if name != "multiband":
            _save_rgb(out_dir / f"{run_name}_{name}.jpg", rgb, quality=90)

    diag: dict[str, object] = {
        "case": run_name,
        "log_short": short,
        "anchor_idx": int(anchor_idx),
        "anchor_ts_ns": int(anchor_ts),
        "lidar_sweep_ts_ns": int(sweep_ts),
        "lidar_delta_ms": float(lidar_delta_ms),
        "seam": seam_diag,
        "lidar_depth_summary": depth_summary,
        "lidar_surface_render": lidar_render.diagnostics,
        "consensus": consensus_diag,
        "method_stats": method_stats,
        "params": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "runtime_s": round(time.time() - t0, 3),
        "outputs": {
            "review": f"{run_name}_lidar_zbuffer_review_{args.review_w}.jpg",
            "crop_review": f"{run_name}_lidar_zbuffer_crop_review.jpg",
            "hard_select": f"{run_name}_hard_select.jpg",
            "lidar_winner": f"{run_name}_lidar_winner.jpg",
            "lidar_consensus": f"{run_name}_lidar_consensus.jpg",
            "lidar_best": f"{run_name}_lidar_best.jpg",
        },
    }
    with open(out_dir / f"{run_name}_lidar_zbuffer_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(diag), f, indent=2, ensure_ascii=False)
    print(
        json.dumps(
            _json_safe(
                {
                    "case": run_name,
                    "visible_any_support_frac": lidar_render.diagnostics["visible_any_support_frac"],
                    "winner_changed": method_stats["lidar_winner"]["changed_frac_erp"],
                    "consensus_changed": method_stats["lidar_consensus"]["changed_frac_erp"],
                    "best_changed": method_stats["lidar_best"]["changed_frac_erp"],
                    "hard_ncc": method_stats["hard_select"]["ncc_aggregate"].get("mean_ncc_pano_vs_winner"),
                    "winner_ncc": method_stats["lidar_winner"]["ncc_aggregate"].get("mean_ncc_pano_vs_winner"),
                    "consensus_ncc": method_stats["lidar_consensus"]["ncc_aggregate"].get("mean_ncc_pano_vs_winner"),
                    "best_ncc": method_stats["lidar_best"]["ncc_aggregate"].get("mean_ncc_pano_vs_winner"),
                }
            ),
            indent=2,
        ),
        flush=True,
    )
    return diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/lidar_zbuffer_seam_v1"))
    ap.add_argument("--cases", nargs="+", default=DEFAULT_CASES)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=48)
    ap.add_argument("--core-half-width", type=int, default=2)
    ap.add_argument("--ncc-win", type=int, default=21)
    ap.add_argument("--max-sample-per-pair", type=int, default=20000)
    ap.add_argument("--min-range-m", type=float, default=0.5)
    ap.add_argument("--max-range-m", type=float, default=80.0)
    ap.add_argument("--densify-radius-px", type=int, default=8)
    ap.add_argument("--fill-far-m", type=float, default=1000.0)
    ap.add_argument("--depth-support-max-m", type=float, default=120.0)
    ap.add_argument("--lidar-max-delta-ms", type=float, default=75.0)
    ap.add_argument("--zbuffer-dilation-px", type=int, default=5)
    ap.add_argument("--min-cam-cos", type=float, default=0.03)
    ap.add_argument("--z-tolerance-abs-m", type=float, default=0.9)
    ap.add_argument("--z-tolerance-rel", type=float, default=0.05)
    ap.add_argument("--consensus-y-thresh", type=float, default=28.0)
    ap.add_argument("--review-w", type=int, default=1024)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_diags = [_one_case(case, args.av2_root, args.out_dir, args) for case in args.cases]

    rows = []
    for d in all_diags:
        p = args.out_dir / d["case"] / d["outputs"]["crop_review"]
        if p.exists():
            img = cv2.cvtColor(cv2.imread(str(p), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            rows.append((d["case"], _resize_w(img, args.review_w)))
    if rows:
        _save_rgb(args.out_dir / "lidar_zbuffer_three_anchor_compact_review.jpg", _stack_named(rows), quality=88)

    def mean_stat(method: str, key: str) -> float:
        vals = [float(d["method_stats"][method][key]) for d in all_diags]
        return float(np.mean(vals)) if vals else 0.0

    def mean_ncc(method: str) -> float:
        vals = [
            float(d["method_stats"][method]["ncc_aggregate"].get("mean_ncc_pano_vs_winner", np.nan))
            for d in all_diags
        ]
        vals = [v for v in vals if np.isfinite(v)]
        return float(np.mean(vals)) if vals else float("nan")

    def mean_dy(method: str) -> float:
        vals = [
            float(d["method_stats"][method]["seam_gap_y"].get("mean_delta_y", np.nan))
            for d in all_diags
        ]
        vals = [v for v in vals if np.isfinite(v)]
        return float(np.mean(vals)) if vals else float("nan")

    summary = {
        "run": args.out_dir.name,
        "cases": all_diags,
        "aggregate": {
            "n_cases": len(all_diags),
            "mean_visible_any_support_frac": float(
                np.mean([d["lidar_surface_render"]["visible_any_support_frac"] for d in all_diags])
            ),
            "mean_visible_ge2_support_frac": float(
                np.mean([d["lidar_surface_render"]["visible_ge2_support_frac"] for d in all_diags])
            ),
            "mean_changed_frac": {
                "lidar_winner": mean_stat("lidar_winner", "changed_frac_erp"),
                "lidar_consensus": mean_stat("lidar_consensus", "changed_frac_erp"),
                "lidar_best": mean_stat("lidar_best", "changed_frac_erp"),
            },
            "mean_ncc_pano_vs_winner": {
                "hard_select": mean_ncc("hard_select"),
                "lidar_winner": mean_ncc("lidar_winner"),
                "lidar_consensus": mean_ncc("lidar_consensus"),
                "lidar_best": mean_ncc("lidar_best"),
            },
            "mean_seam_dy": {
                "hard_select": mean_dy("hard_select"),
                "lidar_winner": mean_dy("lidar_winner"),
                "lidar_consensus": mean_dy("lidar_consensus"),
                "lidar_best": mean_dy("lidar_best"),
            },
        },
    }
    with open(args.out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(summary), f, indent=2, ensure_ascii=False)
    print(json.dumps(_json_safe(summary["aggregate"]), indent=2), flush=True)
    print(f"[saved] {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
