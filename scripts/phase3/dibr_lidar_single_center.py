"""DIBR-on-LiDAR single-center re-render (v2): full-frame, IP-Basic depth, hybrid.

This is the decisive spike for the "single virtual optical center view-synthesis"
direction identified by the 2026-05-29 CV solution-space sweep. It differs from
the earlier seam-band-only `test_lidar_zbuffer_seam.py` (which used crude ERP
kNN depth and was NEG) in three ways:

  1. DEPTH: per-camera image-space LiDAR depth completion (IP-Basic style
     morphological fill, RGB-frame aligned, far denser than ERP kNN-fill).
  2. SCOPE: render the FULL single-center ERP, not only seam bands, so we can
     actually see whether near-field doubling (BMW ghost) is removed.
  3. HYBRID: use the DIBR single-center render wherever a LiDAR-supported camera
     sample is visible (near-field / ground, where LiDAR is dense AND parallax
     is worst); fall back to the legacy sphere `hard_select` elsewhere (sky /
     far, where LiDAR has no returns and parallax is ~0 anyway).

Backward warp + per-camera z-buffer visibility reuse the verified
`render_lidar_surface_to_erp`; we only swap in better depth (per-camera IP-Basic
-> ERP z-buffer) and add the hybrid composite + full-frame metrics.

No optical flow, no learned depth, no generated pixels. Source-faithful: every
output pixel is sampled from a real AV2 camera (sphere or DIBR).
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

from depth_visibility_seam_probe import DEFAULT_CASES, _json_safe, _parse_case  # noqa: E402
from measure_overlap_ncc import score_one_anchor  # noqa: E402
from seam_confidence_map import (  # noqa: E402
    _crop_stack,
    _default_crops,
    _heatmap_u8,
    _resize_w,
    _save_rgb,
    _stack_named,
)
from seam_risk_gated_color_repair import _seam_gap_y  # noqa: E402
from test_lidar_zbuffer_seam import _seam_masks, _winner_label  # noqa: E402
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.depth.lidar_to_erp_depth import (  # noqa: E402
    load_lidar_sweep_nearest_to_ts,
    visualize_depth_map,
)
from waymo2panorama.projection.lidar_zbuffer_layer import (  # noqa: E402
    CameraZBuffer,
    _project_points_to_camera,
    render_lidar_surface_to_erp,
)
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


def ipbasic_complete(
    sparse_z: np.ndarray,
    *,
    max_depth_m: float,
    small_k: int = 5,
    close_k: int = 7,
    fill_k: int = 21,
) -> tuple[np.ndarray, np.ndarray]:
    """IP-Basic-style morphological depth completion on a single camera image.

    `sparse_z` is per-pixel optical depth (meters); <=0 / non-finite = no LiDAR.
    Completion is bounded by `fill_k` so regions far from any LiDAR return
    (e.g. sky) stay empty and later fall back to the sphere baseline.
    Returns (dense_z_with_nan, support_mask).
    """
    d = np.where(np.isfinite(sparse_z) & (sparse_z > 0.0), sparse_z, 0.0).astype(np.float32)
    valid = d > 0.0
    if not valid.any():
        return np.full(d.shape, np.nan, np.float32), np.zeros(d.shape, bool)
    # Invert so near objects are large -> dilation prefers near (correct occlusion).
    inv = np.where(valid, np.float32(max_depth_m) - np.clip(d, 0.0, max_depth_m), 0.0).astype(np.float32)
    k_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (small_k, small_k))
    inv = cv2.dilate(inv, k_small)
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k))
    inv = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, k_close)
    # Bounded large fill only where still empty.
    empty = inv < 1e-3
    k_fill = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (fill_k, fill_k))
    inv_big = cv2.dilate(inv, k_fill)
    inv = np.where(empty, inv_big, inv)
    inv = cv2.medianBlur(inv, 5)
    inv = cv2.GaussianBlur(inv, (5, 5), 0)
    support = inv > 1e-3
    dense = np.where(support, np.float32(max_depth_m) - inv, np.nan).astype(np.float32)
    return dense, support


def build_completed_zbuffers(
    points_ego: np.ndarray,
    images: Sequence[np.ndarray],
    Ks: Sequence[np.ndarray],
    Ts: Sequence[np.ndarray],
    *,
    min_range_m: float,
    max_range_m: float,
    ipbasic_max_depth_m: float,
    fill_k: int,
) -> tuple[list[CameraZBuffer], list[np.ndarray]]:
    """Per-camera dense depth via projection + IP-Basic. Returns zbuffers + dense depths."""
    ranges = np.linalg.norm(points_ego, axis=1)
    keep = (ranges >= float(min_range_m)) & (ranges <= float(max_range_m))
    pts = points_ego[keep].astype(np.float64)

    zbuffers: list[CameraZBuffer] = []
    dense_list: list[np.ndarray] = []
    for image, K, T in zip(images, Ks, Ts):
        h_img, w_img = image.shape[:2]
        u, v, z, _p = _project_points_to_camera(pts, K, T)
        m = 0.5
        valid = (z > 1e-6) & (u >= m) & (u <= w_img - 1 - m) & (v >= m) & (v <= h_img - 1 - m)
        sparse = np.zeros((h_img, w_img), np.float32)
        if valid.any():
            uu = np.round(u[valid]).astype(np.int32)
            vv = np.round(v[valid]).astype(np.int32)
            zz = z[valid].astype(np.float32)
            order = np.argsort(-zz)  # far first, near overwrites
            sparse[vv[order], uu[order]] = zz[order]
        dense, support = ipbasic_complete(sparse, max_depth_m=ipbasic_max_depth_m, fill_k=fill_k)
        depth_z = np.where(support, dense, np.inf).astype(np.float32)
        zbuffers.append(
            CameraZBuffer(
                depth_z_m=depth_z,
                support=support,
                n_projected=int(valid.sum()),
                n_hit_pixels=int((sparse > 0).sum()),
                n_support_pixels=int(support.sum()),
            )
        )
        dense_list.append(depth_z)
    return zbuffers, dense_list


def per_cam_depth_to_erp(
    dense_depths: Sequence[np.ndarray],
    Ks: Sequence[np.ndarray],
    Ts: Sequence[np.ndarray],
    erp_hw: tuple[int, int],
    *,
    min_range_m: float,
    max_range_m: float,
) -> np.ndarray:
    """Project completed per-camera depths into one ego-centered ERP range map (z-buffer min)."""
    H, W = erp_hw
    erp_range = np.full((H, W), np.inf, np.float32)
    for d, K, T in zip(dense_depths, Ks, Ts):
        ys, xs = np.where(np.isfinite(d) & (d > 0) & (d < 1e5))
        if ys.size == 0:
            continue
        z = d[ys, xs].astype(np.float64)
        x = (xs - K[0, 2]) / K[0, 0] * z
        y = (ys - K[1, 2]) / K[1, 1] * z
        pc = np.stack([x, y, z], axis=1)
        R = T[:3, :3].astype(np.float64)
        t = T[:3, 3].astype(np.float64)
        pe = pc @ R.T + t[None, :]
        rng = np.linalg.norm(pe, axis=1)
        keep = (rng >= float(min_range_m)) & (rng <= float(max_range_m))
        pe = pe[keep]
        rng = rng[keep].astype(np.float32)
        theta = np.arctan2(pe[:, 1], pe[:, 0])
        horiz = np.sqrt(pe[:, 0] ** 2 + pe[:, 1] ** 2)
        phi = np.arctan2(pe[:, 2], horiz)
        u = np.mod(np.round((np.pi - theta) / (2.0 * np.pi) * W - 0.5).astype(np.int64), W)
        v = np.round((np.pi / 2.0 - phi) / np.pi * H - 0.5).astype(np.int64)
        inb = (v >= 0) & (v < H)
        idx = (v[inb] * W + u[inb]).astype(np.int64)
        np.minimum.at(erp_range.ravel(), idx, rng[inb])
    return erp_range


def _compose_hybrid(
    sphere_hard: np.ndarray,
    dibr_slabs: Sequence[np.ndarray],
    dibr_weights: Sequence[np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """Use DIBR single-center render where any camera is visible; else sphere hard_select."""
    stack = np.stack(dibr_weights, axis=0)
    dibr_valid = stack.max(axis=0) > 1e-6
    dibr = hard_select(list(dibr_slabs), list(dibr_weights))
    out = sphere_hard.copy()
    out[dibr_valid] = np.clip(dibr[dibr_valid], 0, 255).astype(np.uint8)
    return out, dibr_valid


def _method_stats(name, out, hard, label, valid, slabs, weights, ncc_win, max_sample) -> dict:
    changed = np.any(out.astype(np.int16) != hard.astype(np.int16), axis=-1)
    score = score_one_anchor(out, list(slabs), list(weights), RING_PAIRS, win=ncc_win, max_sample_per_pair=max_sample)
    return {
        "name": name,
        "changed_frac_erp": float(changed.mean()),
        "seam_gap_y": _seam_gap_y(out, label, valid),
        "ncc_aggregate": score.get("aggregate", {}),
    }


def _one_case(case_spec: str, av2_root: Path, out_root: Path, args: argparse.Namespace) -> dict:
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

    slabs, weights, images, Ks, Ts = [], [], [], [], []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _a, w = render_camera_to_erp(
            image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
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
    zbuffers, dense_depths = build_completed_zbuffers(
        pts, images, Ks, Ts,
        min_range_m=args.min_range_m, max_range_m=args.max_range_m,
        ipbasic_max_depth_m=args.ipbasic_max_depth_m, fill_k=args.fill_k,
    )
    erp_depth = per_cam_depth_to_erp(
        dense_depths, Ks, Ts, erp_hw, min_range_m=args.min_range_m, max_range_m=args.max_range_m,
    )
    erp_depth_for_render = np.where(np.isfinite(erp_depth), erp_depth, np.float32(1e9)).astype(np.float32)
    erp_support_frac = float(np.isfinite(erp_depth).mean())

    dibr_render = render_lidar_surface_to_erp(
        images, Ks, Ts, erp_depth_for_render, zbuffers,
        depth_support_max_m=args.depth_support_max_m,
        min_cam_cos=args.min_cam_cos,
        z_tolerance_abs_m=args.z_tolerance_abs_m,
        z_tolerance_rel=args.z_tolerance_rel,
    )
    hybrid, dibr_valid = _compose_hybrid(hard, dibr_render.slabs, dibr_render.weights)

    # full-frame and seam-band DIBR coverage
    dibr_frac_full = float(dibr_valid.mean())
    dibr_frac_seam = float((dibr_valid & seam_band).sum() / max(1, int(seam_band.sum())))
    dibr_frac_seamcore = float((dibr_valid & seam_core).sum() / max(1, int(seam_core.sum())))

    method_stats = {
        "multiband": _method_stats("multiband", multiband, hard, label, valid, slabs, weights, args.ncc_win, args.max_sample_per_pair),
        "hard_select": _method_stats("hard_select", hard, hard, label, valid, slabs, weights, args.ncc_win, args.max_sample_per_pair),
        "dibr_hybrid": _method_stats("dibr_hybrid", hybrid, hard, label, valid, slabs, weights, args.ncc_win, args.max_sample_per_pair),
    }

    # visuals
    dibr_overlay = hard.copy()
    ov = (0.5 * dibr_overlay[dibr_valid].astype(np.float32) + 0.5 * np.array([0, 255, 255], np.float32)).astype(np.uint8)
    dibr_overlay[dibr_valid] = ov
    depth_viz = visualize_depth_map(erp_depth_for_render, log_clip_m=args.max_range_m)
    cov_heat = _heatmap_u8(dibr_valid.astype(np.float32))

    crops = _default_crops(args.erp_h, args.erp_w)
    crop_review = _crop_stack(
        [
            ("multiband (ghost)", multiband),
            ("hard_select (seam)", hard),
            ("DIBR hybrid", hybrid),
            ("DIBR coverage cyan", dibr_overlay),
            ("erp_depth", depth_viz),
        ],
        crops,
    )
    _save_rgb(out_dir / f"{run_name}_dibr_crop_review.jpg", crop_review, quality=90)
    review = _stack_named(
        [
            ("multiband", _resize_w(multiband, args.review_w)),
            ("hard_select", _resize_w(hard, args.review_w)),
            ("dibr_hybrid", _resize_w(hybrid, args.review_w)),
            ("dibr_coverage", _resize_w(cov_heat, args.review_w)),
            ("erp_depth", _resize_w(depth_viz, args.review_w)),
        ]
    )
    _save_rgb(out_dir / f"{run_name}_dibr_review_{args.review_w}.jpg", review, quality=88)
    _save_rgb(out_dir / f"{run_name}_hard_select.jpg", hard, quality=92)
    _save_rgb(out_dir / f"{run_name}_dibr_hybrid.jpg", hybrid, quality=92)

    diag = {
        "case": run_name,
        "log_short": short,
        "anchor_idx": int(anchor_idx),
        "lidar_delta_ms": float(lidar_delta_ms),
        "erp_lidar_support_frac": erp_support_frac,
        "dibr_coverage": {
            "full_frame": dibr_frac_full,
            "seam_band": dibr_frac_seam,
            "seam_core": dibr_frac_seamcore,
        },
        "dibr_surface_render": dibr_render.diagnostics,
        "method_stats": method_stats,
        "params": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "runtime_s": round(time.time() - t0, 3),
        "outputs": {
            "review": f"{run_name}_dibr_review_{args.review_w}.jpg",
            "crop_review": f"{run_name}_dibr_crop_review.jpg",
        },
    }
    with open(out_dir / f"{run_name}_dibr_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(diag), f, indent=2, ensure_ascii=False)
    print(
        json.dumps(
            _json_safe(
                {
                    "case": run_name,
                    "erp_lidar_support_frac": erp_support_frac,
                    "dibr_cov_full": dibr_frac_full,
                    "dibr_cov_seam": dibr_frac_seam,
                    "hard_ncc": method_stats["hard_select"]["ncc_aggregate"].get("mean_ncc_pano_vs_winner"),
                    "dibr_ncc": method_stats["dibr_hybrid"]["ncc_aggregate"].get("mean_ncc_pano_vs_winner"),
                    "hard_dy": method_stats["hard_select"]["seam_gap_y"].get("mean_delta_y"),
                    "dibr_dy": method_stats["dibr_hybrid"]["seam_gap_y"].get("mean_delta_y"),
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
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/dibr_lidar_single_center_v1"))
    ap.add_argument("--cases", nargs="+", default=DEFAULT_CASES)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=48)
    ap.add_argument("--core-half-width", type=int, default=2)
    ap.add_argument("--ncc-win", type=int, default=21)
    ap.add_argument("--max-sample-per-pair", type=int, default=20000)
    ap.add_argument("--min-range-m", type=float, default=0.5)
    ap.add_argument("--max-range-m", type=float, default=80.0)
    ap.add_argument("--ipbasic-max-depth-m", type=float, default=100.0)
    ap.add_argument("--fill-k", type=int, default=21)
    ap.add_argument("--depth-support-max-m", type=float, default=100.0)
    ap.add_argument("--lidar-max-delta-ms", type=float, default=75.0)
    ap.add_argument("--min-cam-cos", type=float, default=0.05)
    ap.add_argument("--z-tolerance-abs-m", type=float, default=0.5)
    ap.add_argument("--z-tolerance-rel", type=float, default=0.04)
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
        _save_rgb(args.out_dir / "dibr_lidar_three_anchor_compact_review.jpg", _stack_named(rows), quality=90)

    def mean_ncc(method):
        vals = [float(d["method_stats"][method]["ncc_aggregate"].get("mean_ncc_pano_vs_winner", np.nan)) for d in all_diags]
        vals = [v for v in vals if np.isfinite(v)]
        return float(np.mean(vals)) if vals else float("nan")

    def mean_dy(method):
        vals = [float(d["method_stats"][method]["seam_gap_y"].get("mean_delta_y", np.nan)) for d in all_diags]
        vals = [v for v in vals if np.isfinite(v)]
        return float(np.mean(vals)) if vals else float("nan")

    summary = {
        "run": args.out_dir.name,
        "cases": all_diags,
        "aggregate": {
            "n_cases": len(all_diags),
            "mean_erp_lidar_support_frac": float(np.mean([d["erp_lidar_support_frac"] for d in all_diags])),
            "mean_dibr_cov_full": float(np.mean([d["dibr_coverage"]["full_frame"] for d in all_diags])),
            "mean_dibr_cov_seam": float(np.mean([d["dibr_coverage"]["seam_band"] for d in all_diags])),
            "mean_ncc_pano_vs_winner": {"hard_select": mean_ncc("hard_select"), "dibr_hybrid": mean_ncc("dibr_hybrid")},
            "mean_seam_dy": {"hard_select": mean_dy("hard_select"), "dibr_hybrid": mean_dy("dibr_hybrid")},
        },
    }
    with open(args.out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(summary), f, indent=2, ensure_ascii=False)
    print(json.dumps(_json_safe(summary["aggregate"]), indent=2), flush=True)
    print(f"[saved] {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
