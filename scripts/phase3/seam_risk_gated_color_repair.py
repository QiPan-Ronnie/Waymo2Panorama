"""Risk-gated local Y-channel seam color repair for L1 hard_select.

This is a deliberately conservative ablation after the seam confidence map:
it does not warp, blend views, estimate depth, or call a learned model. It keeps
the hard-select camera choice and only adjusts luminance close to seams where
the source-evidence map says the region is low-structure-risk.

High-structure-risk pixels are left untouched because those are the places where
color smoothing can hide a geometry conflict or create a visible halo.
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
from PIL import Image

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
    _resize_w,
    _save_rgb,
    _stack_named,
    _to_y_u8,
    _overlap_wraps,
    compute_seam_risk_maps,
)
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


def _winner_label(weights: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    stack = np.stack(weights, axis=0)
    label = stack.argmax(axis=0).astype(np.int16)
    valid = stack.max(axis=0) > 1e-6
    return label, valid


def _seam_gap_y(rgb: np.ndarray, label: np.ndarray, valid: np.ndarray) -> dict[str, float | int]:
    y = cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)
    seam = np.zeros(label.shape, dtype=bool)
    seam[:, :-1] = (label[:, :-1] != label[:, 1:]) & valid[:, :-1] & valid[:, 1:]
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
        "p99_delta_y": float(np.percentile(gaps, 99)),
    }


def _repair_local_y(
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    hard: np.ndarray,
    risk_maps: dict[str, np.ndarray],
    band_half_width: int,
    core_half_width: int,
    structure_thresh: float,
    max_abs_delta: float,
    correction_strength: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    H, W = weights[0].shape
    label, valid = _winner_label(weights)

    hard_u8 = np.clip(hard, 0, 255).astype(np.uint8)
    ycrcb = cv2.cvtColor(hard_u8, cv2.COLOR_RGB2YCrCb).astype(np.float32)
    y = ycrcb[..., 0]
    correction_sum = np.zeros((H, W), dtype=np.float32)
    alpha_sum = np.zeros((H, W), dtype=np.float32)
    structure = risk_maps["structure_risk"].astype(np.float32)

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
            structure_p = np.roll(structure, roll, axis=1)
            label_p = np.roll(label, roll, axis=1)
            slab_i = np.roll(slabs[i], roll, axis=1)
            slab_j = np.roll(slabs[j], roll, axis=1)
        else:
            weight_i_p, weight_j_p = weight_i, weight_j
            overlap_p = overlap
            structure_p = structure
            label_p = label
            slab_i = slabs[i]
            slab_j = slabs[j]

        band, signed = build_voronoi_seam_band(
            weight_i_p,
            weight_j_p,
            band_half_width=band_half_width,
            threshold=1e-6,
        )
        band &= overlap_p
        core = band & (np.abs(signed) <= core_half_width)
        low_structure_core = core & (structure_p <= structure_thresh)
        if int(low_structure_core.sum()) < 64:
            pair_diags.append(
                {
                    "pair": [int(i), int(j)],
                    "status": "too_few_low_structure_core_pixels",
                    "core_pixels": int(core.sum()),
                    "low_structure_core_pixels": int(low_structure_core.sum()),
                }
            )
            continue

        yi = _to_y_u8(slab_i).astype(np.float32)
        yj = _to_y_u8(slab_j).astype(np.float32)
        delta = yj[low_structure_core] - yi[low_structure_core]
        med_delta = float(np.median(delta))
        med_delta = float(np.clip(med_delta, -max_abs_delta, max_abs_delta))
        if abs(med_delta) < 1e-3:
            pair_diags.append(
                {
                    "pair": [int(i), int(j)],
                    "status": "near_zero_delta",
                    "median_delta_y_j_minus_i": med_delta,
                    "core_pixels": int(core.sum()),
                    "low_structure_core_pixels": int(low_structure_core.sum()),
                }
            )
            continue

        structure_gate = np.clip((structure_thresh - structure_p) / max(structure_thresh, 1e-6), 0.0, 1.0)
        dist_alpha = np.clip(1.0 - (np.abs(signed) / float(max(1, band_half_width))), 0.0, 1.0)
        alpha = (band.astype(np.float32) * structure_gate * dist_alpha * correction_strength).astype(np.float32)
        alpha[~band] = 0.0
        if not np.any(alpha > 1e-5):
            pair_diags.append(
                {
                    "pair": [int(i), int(j)],
                    "status": "zero_alpha_after_gate",
                    "median_delta_y_j_minus_i": med_delta,
                    "core_pixels": int(core.sum()),
                    "low_structure_core_pixels": int(low_structure_core.sum()),
                }
            )
            continue

        local_corr = np.zeros((H, W), dtype=np.float32)
        side_i = band & (label_p == i)
        side_j = band & (label_p == j)
        local_corr[side_i] = 0.5 * med_delta
        local_corr[side_j] = -0.5 * med_delta
        if roll:
            alpha = np.roll(alpha, -roll, axis=1)
            local_corr = np.roll(local_corr, -roll, axis=1)

        correction_sum += local_corr * alpha
        alpha_sum += alpha
        pair_diags.append(
            {
                "pair": [int(i), int(j)],
                "status": "applied",
                "median_delta_y_j_minus_i": med_delta,
                "core_pixels": int(core.sum()),
                "low_structure_core_pixels": int(low_structure_core.sum()),
                "alpha_pixels": int(np.sum(alpha > 1e-5)),
                "alpha_mean_nonzero": float(alpha[alpha > 1e-5].mean()),
            }
        )

    y_new = np.clip(y + correction_sum, 0, 255)
    out_ycrcb = ycrcb.copy()
    out_ycrcb[..., 0] = y_new
    repaired = cv2.cvtColor(out_ycrcb.astype(np.uint8), cv2.COLOR_YCrCb2RGB)
    diagnostics = {
        "pair_diagnostics": pair_diags,
        "changed_fraction": float(np.mean(np.abs(correction_sum) > 0.25)),
        "mean_abs_delta_y_applied": float(np.mean(np.abs(correction_sum))),
        "p95_abs_delta_y_applied": float(np.percentile(np.abs(correction_sum), 95)),
        "max_abs_delta_y_applied": float(np.max(np.abs(correction_sum))),
    }
    return repaired, correction_sum, diagnostics


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
    ap.add_argument("--structure-thresh", type=float, default=0.35)
    ap.add_argument("--max-abs-delta", type=float, default=28.0)
    ap.add_argument("--correction-strength", type=float, default=0.65)
    ap.add_argument("--review-w", type=int, default=1024)
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
    project_s = time.time() - t0
    print(f"[project] {project_s:.1f}s", flush=True)

    hard = hard_select(slabs, weights)
    risk_maps, risk_diag = compute_seam_risk_maps(
        slabs,
        weights,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        ncc_win=args.ncc_win,
    )
    repaired, correction, repair_diag = _repair_local_y(
        slabs,
        weights,
        hard,
        risk_maps,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        structure_thresh=args.structure_thresh,
        max_abs_delta=args.max_abs_delta,
        correction_strength=args.correction_strength,
    )
    label, valid = _winner_label(weights)
    before_gap = _seam_gap_y(hard, label, valid)
    after_gap = _seam_gap_y(repaired, label, valid)
    if before_gap.get("n", 0):
        mean_improve = 100.0 * (
            float(before_gap["mean_delta_y"]) - float(after_gap["mean_delta_y"])
        ) / max(float(before_gap["mean_delta_y"]), 1.0)
        p95_improve = 100.0 * (
            float(before_gap["p95_delta_y"]) - float(after_gap["p95_delta_y"])
        ) / max(float(before_gap["p95_delta_y"]), 1.0)
    else:
        mean_improve = 0.0
        p95_improve = 0.0

    risk_overlay = _overlay_risk(hard, risk_maps["risk"], risk_maps["seam_core"].astype(bool))
    correction_vis = _heatmap_u8(np.clip(np.abs(correction) / max(args.max_abs_delta, 1e-6), 0, 1))
    diff = np.clip(np.abs(repaired.astype(np.float32) - hard.astype(np.float32)) * 12.0, 0, 255).astype(np.uint8)

    rows = [
        ("hard_select", _resize_w(hard, args.review_w)),
        ("risk_overlay", _resize_w(risk_overlay, args.review_w)),
        ("risk_gated_y_repair", _resize_w(repaired, args.review_w)),
        ("abs_diff_x12", _resize_w(diff, args.review_w)),
        ("correction_abs", _resize_w(correction_vis, args.review_w)),
    ]
    _save_rgb(out_dir / f"{run_name}_repair_review_{args.review_w}.jpg", _stack_named(rows), quality=88)
    _save_rgb(
        out_dir / f"{run_name}_repair_crop_review.jpg",
        _crop_stack(
            [
                ("hard_select", hard),
                ("risk_gated_y_repair", repaired),
                ("abs_diff_x12", diff),
                ("correction_abs", correction_vis),
            ],
            _default_crops(args.erp_h, args.erp_w),
        ),
        quality=88,
    )
    _save_rgb(out_dir / f"{run_name}_repaired_{args.review_w}.jpg", _resize_w(repaired, args.review_w), quality=90)

    diagnostics = {
        "log_short": log_short,
        "anchor_idx": args.anchor_idx,
        "erp_hw": list(erp_hw),
        "params": {
            "band_half_width": args.band_half_width,
            "core_half_width": args.core_half_width,
            "ncc_win": args.ncc_win,
            "structure_thresh": args.structure_thresh,
            "max_abs_delta": args.max_abs_delta,
            "correction_strength": args.correction_strength,
        },
        "risk_global": risk_diag["global"],
        "repair": repair_diag,
        "seam_gap_before": before_gap,
        "seam_gap_after": after_gap,
        "seam_gap_improvement_pct": {
            "mean_delta_y": float(mean_improve),
            "p95_delta_y": float(p95_improve),
        },
        "runtime_s": {"projection": round(project_s, 3)},
    }
    with open(out_dir / f"{run_name}_repair_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, ensure_ascii=False)
    print(json.dumps(diagnostics["seam_gap_improvement_pct"], indent=2), flush=True)
    print(f"[saved] {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
