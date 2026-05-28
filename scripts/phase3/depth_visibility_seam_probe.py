"""LiDAR depth-visibility seam probe for L1 hard_select.

This is intentionally not another depth renderer. Earlier N1 attempts used
depth as the projection surface and produced FOV holes or pasted blocks. This
probe uses depth only as seam metadata:

  - near-parallax risk: adjacent camera baseline / LiDAR depth, in ERP pixels
  - depth-discontinuity risk: local LiDAR range jumps near the seam
  - unknown-depth mask: pixels with no nearby LiDAR support

It then runs the existing conservative Y-only seam repair twice:

  1. structure-only gate, as in seam_risk_gated_color_repair.py
  2. depth+structure gate, where high depth-visibility risk vetoes repair

The output answers a narrower question: can explicit depth help decide where a
2D seam fix is allowed, without directly warping/blending the scene geometry?
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

from seam_confidence_map import (  # noqa: E402
    _crop_stack,
    _default_crops,
    _heatmap_u8,
    _label_panel,
    _overlay_risk,
    _overlap_wraps,
    _resize_w,
    _risk_stats,
    _save_rgb,
    _stack_named,
    compute_seam_risk_maps,
)
from seam_risk_gated_color_repair import (  # noqa: E402
    _repair_local_y,
    _seam_gap_y,
    _winner_label,
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


LOG_UUIDS = {
    "02a00399": "02a00399-3857-444e-8db3-a8f58489c394",
    "fbee355f": "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
    "0bae3b5e": "0bae3b5e-417d-3b03-abaa-806b433233b8",
}

DEFAULT_CASES = ["02a00399:0:bmw", "fbee355f:95:ped_obj", "0bae3b5e:30:clean_far"]


def _parse_case(spec: str, av2_root: Path) -> tuple[str, Path, int, str]:
    parts = spec.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"case must be shortlog:anchor[:tag], got {spec!r}")
    short = parts[0]
    anchor = int(parts[1])
    tag = parts[2] if len(parts) == 3 else "case"
    uuid = LOG_UUIDS.get(short, short)
    return short, av2_root / uuid, anchor, tag


def _json_safe(x):
    if isinstance(x, dict):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    return x


def _safe_corr(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float | None:
    good = mask & np.isfinite(a) & np.isfinite(b)
    if int(good.sum()) < 64:
        return None
    av = a[good].astype(np.float32)
    bv = b[good].astype(np.float32)
    av = av - float(av.mean())
    bv = bv - float(bv.mean())
    denom = float(np.sqrt(np.sum(av * av) * np.sum(bv * bv)))
    if denom <= 1e-8:
        return None
    return float(np.sum(av * bv) / denom)


def _depth_discontinuity(depth: np.ndarray, support: np.ndarray, win: int) -> tuple[np.ndarray, np.ndarray]:
    """Local relative range span plus local support count."""
    if win % 2 == 0:
        win += 1
    kernel = np.ones((win, win), dtype=np.uint8)
    d = depth.astype(np.float32)
    dmin_src = np.where(support, d, 1.0e6).astype(np.float32)
    dmax_src = np.where(support, d, 0.0).astype(np.float32)
    dmin = cv2.erode(dmin_src, kernel, borderType=cv2.BORDER_REPLICATE)
    dmax = cv2.dilate(dmax_src, kernel, borderType=cv2.BORDER_REPLICATE)
    count = cv2.boxFilter(
        support.astype(np.float32),
        cv2.CV_32F,
        (win, win),
        normalize=False,
        borderType=cv2.BORDER_REFLECT,
    )
    valid = count >= max(3, win)
    rel_span = np.zeros_like(d, dtype=np.float32)
    rel_span[valid] = (dmax[valid] - dmin[valid]) / np.maximum(dmin[valid], 1.0)
    return rel_span, count


def compute_depth_visibility_maps(
    depth_map: np.ndarray,
    weights: Sequence[np.ndarray],
    baselines_m: dict[tuple[int, int], float],
    band_half_width: int,
    core_half_width: int,
    depth_support_max_m: float,
    parallax_low_px: float,
    parallax_high_px: float,
    discontinuity_low: float,
    discontinuity_high: float,
    discontinuity_win: int,
) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    H, W = depth_map.shape
    px_per_rad = W / (2.0 * np.pi)
    support = np.isfinite(depth_map) & (depth_map < depth_support_max_m)
    rel_span, support_count = _depth_discontinuity(depth_map, support, discontinuity_win)

    depth_risk = np.zeros((H, W), dtype=np.float32)
    parallax_px_map = np.zeros((H, W), dtype=np.float32)
    near_risk_map = np.zeros((H, W), dtype=np.float32)
    discontinuity_risk_map = np.zeros((H, W), dtype=np.float32)
    seam_band = np.zeros((H, W), dtype=bool)
    seam_core = np.zeros((H, W), dtype=bool)
    seam_support = np.zeros((H, W), dtype=bool)
    seam_unknown = np.zeros((H, W), dtype=bool)
    pair_diags: list[dict[str, object]] = []

    for i, j in RING_PAIRS:
        weight_i = weights[i].astype(np.float32)
        weight_j = weights[j].astype(np.float32)
        overlap = (weight_i > 1e-6) & (weight_j > 1e-6)
        roll = W // 2 if _overlap_wraps(overlap) else 0
        if roll:
            weight_i_p = np.roll(weight_i, roll, axis=1)
            weight_j_p = np.roll(weight_j, roll, axis=1)
            overlap_p = np.roll(overlap, roll, axis=1)
            depth_p = np.roll(depth_map, roll, axis=1)
            support_p = np.roll(support, roll, axis=1)
            rel_span_p = np.roll(rel_span, roll, axis=1)
            count_p = np.roll(support_count, roll, axis=1)
        else:
            weight_i_p, weight_j_p = weight_i, weight_j
            overlap_p = overlap
            depth_p = depth_map
            support_p = support
            rel_span_p = rel_span
            count_p = support_count

        band, signed = build_voronoi_seam_band(
            weight_i_p,
            weight_j_p,
            band_half_width=band_half_width,
            threshold=1e-6,
        )
        band &= overlap_p
        core = band & (np.abs(signed) <= core_half_width)
        if not band.any():
            pair_diags.append({"pair": [int(i), int(j)], "status": "no_band"})
            continue

        baseline = float(baselines_m[(i, j)])
        parallax_px = np.zeros((H, W), dtype=np.float32)
        parallax_px[support_p] = baseline / np.maximum(depth_p[support_p], 0.5) * px_per_rad
        near_risk = np.clip(
            (parallax_px - parallax_low_px) / max(parallax_high_px - parallax_low_px, 1e-6),
            0.0,
            1.0,
        ).astype(np.float32)
        disc_risk = np.clip(
            (rel_span_p - discontinuity_low) / max(discontinuity_high - discontinuity_low, 1e-6),
            0.0,
            1.0,
        ).astype(np.float32)
        pair_risk = np.clip(0.72 * near_risk + 0.28 * disc_risk, 0.0, 1.0)
        pair_risk[~support_p] = 0.0
        pair_parallax = parallax_px

        if roll:
            band = np.roll(band, -roll, axis=1)
            core = np.roll(core, -roll, axis=1)
            support_u = np.roll(support_p, -roll, axis=1)
            unknown_u = np.roll(~support_p, -roll, axis=1)
            pair_risk = np.roll(pair_risk, -roll, axis=1)
            pair_parallax = np.roll(pair_parallax, -roll, axis=1)
            near_risk = np.roll(near_risk, -roll, axis=1)
            disc_risk = np.roll(disc_risk, -roll, axis=1)
            count_u = np.roll(count_p, -roll, axis=1)
        else:
            support_u = support_p
            unknown_u = ~support_p
            count_u = count_p

        seam_band |= band
        seam_core |= core
        seam_support |= band & support_u
        seam_unknown |= band & unknown_u

        update = band & support_u & (pair_risk > depth_risk)
        depth_risk[update] = pair_risk[update]
        parallax_px_map[update] = pair_parallax[update]
        near_risk_map[update] = near_risk[update]
        discontinuity_risk_map[update] = disc_risk[update]

        band_support = band & support_u
        depth_vals = depth_map[band_support]
        par_vals = pair_parallax[band_support]
        pair_diags.append(
            {
                "pair": [int(i), int(j)],
                "status": "ok",
                "rolled": bool(roll),
                "baseline_m": baseline,
                "band_pixels": int(band.sum()),
                "core_pixels": int(core.sum()),
                "lidar_supported_pixels": int(band_support.sum()),
                "lidar_supported_frac": float(band_support.sum() / max(1, band.sum())),
                "local_support_count_mean": float(count_u[band].mean()) if band.any() else 0.0,
                "depth_m": {
                    "median": float(np.median(depth_vals)) if depth_vals.size else None,
                    "p10": float(np.percentile(depth_vals, 10)) if depth_vals.size else None,
                    "p90": float(np.percentile(depth_vals, 90)) if depth_vals.size else None,
                },
                "parallax_px": {
                    "median": float(np.median(par_vals)) if par_vals.size else None,
                    "p90": float(np.percentile(par_vals, 90)) if par_vals.size else None,
                    "p95": float(np.percentile(par_vals, 95)) if par_vals.size else None,
                },
                "risk": _risk_stats("depth_visibility", pair_risk, band_support),
                "high_depth_risk_frac_supported": float(
                    np.mean(pair_risk[band_support] >= 0.65)
                )
                if int(band_support.sum()) > 0
                else None,
            }
        )

    maps = {
        "depth_risk": depth_risk,
        "parallax_px": parallax_px_map,
        "near_risk": near_risk_map,
        "depth_discontinuity_risk": discontinuity_risk_map,
        "seam_band": seam_band.astype(np.float32),
        "seam_core": seam_core.astype(np.float32),
        "seam_lidar_support": seam_support.astype(np.float32),
        "seam_lidar_unknown": seam_unknown.astype(np.float32),
        "lidar_depth": depth_map.astype(np.float32),
    }
    diag = {
        "pair_diagnostics": pair_diags,
        "global": {
            "band_pixels": int(seam_band.sum()),
            "core_pixels": int(seam_core.sum()),
            "lidar_supported_pixels": int(seam_support.sum()),
            "lidar_supported_frac_of_band": float(seam_support.sum() / max(1, seam_band.sum())),
            "unknown_frac_of_band": float(seam_unknown.sum() / max(1, seam_band.sum())),
            "depth_risk": _risk_stats("global_depth_visibility", depth_risk, seam_support),
            "high_depth_risk_pixels": int(((depth_risk >= 0.65) & seam_support).sum()),
            "high_depth_risk_frac_supported": float(
                ((depth_risk >= 0.65) & seam_support).sum() / max(1, seam_support.sum())
            ),
            "parallax_px": _risk_stats("parallax_px", parallax_px_map, seam_support),
        },
    }
    return maps, diag


def _overlay_mask(rgb: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    out = np.clip(rgb, 0, 255).astype(np.uint8).copy()
    m = mask.astype(bool)
    if not m.any():
        return out
    overlay = np.zeros_like(out)
    overlay[m] = np.array(color, dtype=np.uint8)
    alpha = np.zeros((*m.shape, 1), dtype=np.float32)
    alpha[m] = 0.55
    out = np.clip(out.astype(np.float32) * (1.0 - alpha) + overlay.astype(np.float32) * alpha, 0, 255)
    return out.astype(np.uint8)


def _one_case(
    case_spec: str,
    av2_root: Path,
    out_root: Path,
    args: argparse.Namespace,
) -> dict[str, object]:
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, av2_root)
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    case_out = out_root / run_name
    case_out.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    print(f"[case] {run_name}", flush=True)
    loader = AV2RingLoader(log_dir)
    ts = loader.anchor_timestamps_ns()
    if not 0 <= anchor_idx < len(ts):
        raise IndexError(f"{run_name}: anchor {anchor_idx} out of range n={len(ts)}")
    anchor_ts = ts[anchor_idx]
    t0 = time.time()
    frame = loader.load_synced_frame(anchor_ts)
    load_frame_s = time.time() - t0

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

    t0 = time.time()
    source_maps, source_diag = compute_seam_risk_maps(
        slabs,
        weights,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        ncc_win=args.ncc_win,
    )
    source_risk_s = time.time() - t0

    t0 = time.time()
    pts, sweep_ts, lidar_delta_ms = load_lidar_sweep_nearest_to_ts(
        log_dir,
        anchor_ts,
        max_delta_ms=args.lidar_max_delta_ms,
    )
    depth_map, depth_summary = project_lidar_to_erp_depth(
        pts,
        erp_hw=erp_hw,
        min_range_m=args.min_range_m,
        max_range_m=args.max_range_m,
        densify_radius_px=args.densify_radius_px,
        fill_far_m=args.fill_far_m,
    )
    lidar_s = time.time() - t0

    baselines = {}
    for i, j in RING_PAIRS:
        ti = frame.calibrations[RING_CAMS_7[i]].T_ego_cam[:3, 3]
        tj = frame.calibrations[RING_CAMS_7[j]].T_ego_cam[:3, 3]
        baselines[(i, j)] = float(np.linalg.norm(ti - tj))

    depth_maps, depth_diag = compute_depth_visibility_maps(
        depth_map=depth_map,
        weights=weights,
        baselines_m=baselines,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        depth_support_max_m=args.depth_support_max_m,
        parallax_low_px=args.parallax_low_px,
        parallax_high_px=args.parallax_high_px,
        discontinuity_low=args.discontinuity_low,
        discontinuity_high=args.discontinuity_high,
        discontinuity_win=args.discontinuity_win,
    )
    seam_band = source_maps["seam_band"].astype(bool)
    seam_support = depth_maps["seam_lidar_support"].astype(bool)

    t0 = time.time()
    repaired_source, corr_source, diag_repair_source = _repair_local_y(
        slabs,
        weights,
        hard,
        source_maps,
        band_half_width=args.repair_band_half_width,
        core_half_width=args.core_half_width,
        structure_thresh=args.structure_thresh,
        max_abs_delta=args.max_abs_delta,
        correction_strength=args.correction_strength,
    )
    depth_gate_maps = dict(source_maps)
    depth_gate_maps["structure_risk"] = np.maximum(
        source_maps["structure_risk"].astype(np.float32),
        depth_maps["depth_risk"].astype(np.float32) * args.depth_veto_strength,
    ).astype(np.float32)
    repaired_depth, corr_depth, diag_repair_depth = _repair_local_y(
        slabs,
        weights,
        hard,
        depth_gate_maps,
        band_half_width=args.repair_band_half_width,
        core_half_width=args.core_half_width,
        structure_thresh=args.structure_thresh,
        max_abs_delta=args.max_abs_delta,
        correction_strength=args.correction_strength,
    )
    repair_s = time.time() - t0

    hard_gap = _seam_gap_y(hard, label, valid)
    source_gap = _seam_gap_y(repaired_source, label, valid)
    depth_gap = _seam_gap_y(repaired_depth, label, valid)

    depth_risk = depth_maps["depth_risk"].astype(np.float32)
    depth_overlay = _overlay_risk(hard, depth_risk, depth_maps["seam_core"].astype(bool))
    depth_unknown_overlay = _overlay_mask(
        depth_overlay,
        seam_band & ~seam_support,
        color=(255, 0, 255),
    )
    high_depth_overlay = _overlay_mask(
        hard,
        seam_support & (depth_risk >= 0.65),
        color=(255, 40, 40),
    )
    source_overlay = _overlay_risk(hard, source_maps["risk"], source_maps["seam_core"].astype(bool))
    depth_heat = _heatmap_u8(depth_risk)
    parallax_vis = _heatmap_u8(np.clip(depth_maps["parallax_px"] / max(args.parallax_high_px, 1e-6), 0, 1))
    depth_viz = visualize_depth_map(depth_map, log_clip_m=args.max_range_m)

    review_rows_full = [
        ("hard_select", hard),
        ("source_risk_overlay", source_overlay),
        ("depth_visibility_overlay magenta=unknown", depth_unknown_overlay),
        ("high_depth_risk_overlay", high_depth_overlay),
        ("Y_repair_source_gate", repaired_source),
        ("Y_repair_depth_gate", repaired_depth),
        ("lidar_depth_viz", depth_viz),
        ("depth_risk_heat", depth_heat),
        ("parallax_px_scaled", parallax_vis),
    ]
    review_rows_small = [(name, _resize_w(img, args.review_w)) for name, img in review_rows_full]
    review = _stack_named(review_rows_small)
    review_path = case_out / f"{run_name}_depth_visibility_review_{args.review_w}.jpg"
    _save_rgb(review_path, review, quality=88)

    crops = _default_crops(args.erp_h, args.erp_w)
    crop_review = _crop_stack(
        [
            ("hard_select", hard),
            ("source_risk", source_overlay),
            ("depth_risk", depth_unknown_overlay),
            ("Y_source_gate", repaired_source),
            ("Y_depth_gate", repaired_depth),
        ],
        crops,
    )
    crop_path = case_out / f"{run_name}_depth_visibility_crop_review.jpg"
    _save_rgb(crop_path, crop_review, quality=88)
    _save_rgb(case_out / f"{run_name}_hard_select_{args.review_w}.jpg", _resize_w(hard, args.review_w), quality=90)
    _save_rgb(case_out / f"{run_name}_depth_risk_overlay_{args.review_w}.jpg", _resize_w(depth_unknown_overlay, args.review_w), quality=90)
    _save_rgb(case_out / f"{run_name}_depth_risk_heat_{args.review_w}.jpg", _resize_w(depth_heat, args.review_w), quality=90)

    if args.save_full:
        _save_rgb(case_out / f"{run_name}_hard_select.png", hard)
        _save_rgb(case_out / f"{run_name}_Y_repair_source_gate.png", repaired_source)
        _save_rgb(case_out / f"{run_name}_Y_repair_depth_gate.png", repaired_depth)
        _save_rgb(case_out / f"{run_name}_depth_visibility_overlay.png", depth_unknown_overlay)
        np.savez_compressed(
            case_out / f"{run_name}_depth_visibility_maps.npz",
            **{k: v.astype(np.float32) for k, v in depth_maps.items()},
            source_risk=source_maps["risk"].astype(np.float32),
            source_structure_risk=source_maps["structure_risk"].astype(np.float32),
            source_color_risk=source_maps["color_risk"].astype(np.float32),
        )

    def _gap_delta(after: dict[str, float | int]) -> float | None:
        if not hard_gap.get("n") or not after.get("n"):
            return None
        before = float(hard_gap["mean_delta_y"])
        if abs(before) <= 1e-9:
            return None
        return float((before - float(after["mean_delta_y"])) / before)

    corr_mask = seam_band & seam_support
    diag = {
        "case": run_name,
        "log_short": short,
        "log_dir": str(log_dir),
        "anchor_idx": int(anchor_idx),
        "anchor_ts_ns": int(anchor_ts),
        "lidar_sweep_ts_ns": int(sweep_ts),
        "lidar_delta_ms": float(lidar_delta_ms),
        "erp_hw": list(erp_hw),
        "params": {
            "band_half_width": args.band_half_width,
            "repair_band_half_width": args.repair_band_half_width,
            "core_half_width": args.core_half_width,
            "ncc_win": args.ncc_win,
            "densify_radius_px": args.densify_radius_px,
            "depth_support_max_m": args.depth_support_max_m,
            "parallax_low_px": args.parallax_low_px,
            "parallax_high_px": args.parallax_high_px,
            "discontinuity_win": args.discontinuity_win,
            "depth_veto_strength": args.depth_veto_strength,
        },
        "lidar_depth_summary": depth_summary,
        "source_risk_global": source_diag["global"],
        "depth_visibility_global": depth_diag["global"],
        "depth_source_correlations_on_supported_seam": {
            "depth_vs_source_risk": _safe_corr(depth_risk, source_maps["risk"], corr_mask),
            "depth_vs_structure_risk": _safe_corr(depth_risk, source_maps["structure_risk"], corr_mask),
            "depth_vs_color_risk": _safe_corr(depth_risk, source_maps["color_risk"], corr_mask),
        },
        "seam_gap_y": {
            "hard_select": hard_gap,
            "source_gate_repair": source_gap,
            "depth_gate_repair": depth_gap,
            "source_gate_mean_reduction": _gap_delta(source_gap),
            "depth_gate_mean_reduction": _gap_delta(depth_gap),
        },
        "repair_diagnostics": {
            "source_gate": diag_repair_source,
            "depth_gate": diag_repair_depth,
            "depth_gate_changed_fraction_vs_hard": float(np.mean(np.any(repaired_depth != hard, axis=2))),
            "source_gate_changed_fraction_vs_hard": float(np.mean(np.any(repaired_source != hard, axis=2))),
            "source_vs_depth_gate_mae": float(np.mean(np.abs(repaired_source.astype(np.float32) - repaired_depth.astype(np.float32)))),
            "source_corr_abs_mean": float(np.mean(np.abs(corr_source))),
            "depth_corr_abs_mean": float(np.mean(np.abs(corr_depth))),
        },
        "pair_diagnostics": {
            "source": source_diag["pair_diagnostics"],
            "depth_visibility": depth_diag["pair_diagnostics"],
        },
        "runtime_s": {
            "load_frame": round(load_frame_s, 3),
            "project": round(project_s, 3),
            "source_risk": round(source_risk_s, 3),
            "lidar_depth": round(lidar_s, 3),
            "repair": round(repair_s, 3),
        },
        "outputs": {
            "review": review_path.name,
            "crop_review": crop_path.name,
            "hard_select": f"{run_name}_hard_select_{args.review_w}.jpg",
            "depth_risk_overlay": f"{run_name}_depth_risk_overlay_{args.review_w}.jpg",
            "depth_risk_heat": f"{run_name}_depth_risk_heat_{args.review_w}.jpg",
        },
    }
    diag_path = case_out / f"{run_name}_depth_visibility_diagnostics.json"
    with open(diag_path, "w", encoding="utf-8") as f:
        json.dump(_json_safe(diag), f, indent=2, ensure_ascii=False)

    print(
        json.dumps(
            {
                "case": run_name,
                "supported_frac": diag["depth_visibility_global"]["lidar_supported_frac_of_band"],
                "high_depth_frac_supported": diag["depth_visibility_global"]["high_depth_risk_frac_supported"],
                "hard_mean_dY": hard_gap.get("mean_delta_y"),
                "source_reduction": diag["seam_gap_y"]["source_gate_mean_reduction"],
                "depth_reduction": diag["seam_gap_y"]["depth_gate_mean_reduction"],
            },
            indent=2,
        ),
        flush=True,
    )
    return diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/depth_visibility_seam_probe_v1"))
    ap.add_argument("--cases", nargs="+", default=DEFAULT_CASES)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=48)
    ap.add_argument("--repair-band-half-width", type=int, default=48)
    ap.add_argument("--core-half-width", type=int, default=2)
    ap.add_argument("--ncc-win", type=int, default=21)
    ap.add_argument("--min-range-m", type=float, default=0.5)
    ap.add_argument("--max-range-m", type=float, default=80.0)
    ap.add_argument("--densify-radius-px", type=int, default=10)
    ap.add_argument("--fill-far-m", type=float, default=1000.0)
    ap.add_argument("--depth-support-max-m", type=float, default=120.0)
    ap.add_argument("--lidar-max-delta-ms", type=float, default=75.0)
    ap.add_argument("--parallax-low-px", type=float, default=2.0)
    ap.add_argument("--parallax-high-px", type=float, default=12.0)
    ap.add_argument("--discontinuity-low", type=float, default=0.12)
    ap.add_argument("--discontinuity-high", type=float, default=0.55)
    ap.add_argument("--discontinuity-win", type=int, default=11)
    ap.add_argument("--structure-thresh", type=float, default=0.35)
    ap.add_argument("--max-abs-delta", type=float, default=28.0)
    ap.add_argument("--correction-strength", type=float, default=0.65)
    ap.add_argument("--depth-veto-strength", type=float, default=1.0)
    ap.add_argument("--review-w", type=int, default=1024)
    ap.add_argument("--save-full", action="store_true")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_diags = []
    for case in args.cases:
        all_diags.append(_one_case(case, args.av2_root, args.out_dir, args))

    compact_rows = []
    for d in all_diags:
        case_dir = args.out_dir / str(d["case"])
        img_path = case_dir / d["outputs"]["crop_review"]
        if img_path.exists():
            img = cv2.cvtColor(cv2.imread(str(img_path), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            compact_rows.append((str(d["case"]), _resize_w(img, args.review_w)))
    if compact_rows:
        _save_rgb(args.out_dir / "depth_visibility_three_anchor_compact_review.jpg", _stack_named(compact_rows), quality=88)

    summary = {
        "run": args.out_dir.name,
        "cases": all_diags,
        "aggregate": {
            "n_cases": len(all_diags),
            "mean_lidar_supported_frac_of_band": float(
                np.mean([d["depth_visibility_global"]["lidar_supported_frac_of_band"] for d in all_diags])
            )
            if all_diags
            else None,
            "mean_high_depth_risk_frac_supported": float(
                np.mean([d["depth_visibility_global"]["high_depth_risk_frac_supported"] for d in all_diags])
            )
            if all_diags
            else None,
            "mean_source_gate_dY_reduction": float(
                np.mean([d["seam_gap_y"]["source_gate_mean_reduction"] for d in all_diags if d["seam_gap_y"]["source_gate_mean_reduction"] is not None])
            )
            if any(d["seam_gap_y"]["source_gate_mean_reduction"] is not None for d in all_diags)
            else None,
            "mean_depth_gate_dY_reduction": float(
                np.mean([d["seam_gap_y"]["depth_gate_mean_reduction"] for d in all_diags if d["seam_gap_y"]["depth_gate_mean_reduction"] is not None])
            )
            if any(d["seam_gap_y"]["depth_gate_mean_reduction"] is not None for d in all_diags)
            else None,
        },
    }
    with open(args.out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(summary), f, indent=2, ensure_ascii=False)
    print(json.dumps(_json_safe(summary["aggregate"]), indent=2), flush=True)
    print(f"[saved] {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
