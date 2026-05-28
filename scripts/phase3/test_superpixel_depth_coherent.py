"""Superpixel + dense-depth coherent source selection near hard seams.

This is a source-faithful layer-like probe. Instead of moving a 1-pixel seam
path, it segments the hard_select panorama into RGB+Depth-Anything superpixels
and only changes source labels for superpixels that are split by a hard seam.

Final pixels are copied from the original L1 camera slabs. The test asks
whether a region-level unit is a better abstraction than row-wise DP paths.
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

from dense_depth_edge_seam_probe import (  # noqa: E402
    DEFAULT_CASES,
    _json_safe,
    _parse_case,
    build_dense_depth_slabs,
    compute_dense_depth_edge_risk,
)
from seam_confidence_map import _crop_stack, _default_crops, _overlay_risk, _resize_w, _save_rgb, _stack_named  # noqa: E402
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402
from measure_overlap_ncc import score_one_anchor  # noqa: E402


def _save(path: Path, rgb: np.ndarray, quality: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        img.save(path, quality=quality)
    else:
        img.save(path)


def _winner_label(weights: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    stack = np.stack(weights, axis=0)
    label = stack.argmax(axis=0).astype(np.int16)
    valid = stack.max(axis=0) > 1e-6
    return label, valid


def _compose(slabs: Sequence[np.ndarray], label: np.ndarray, valid: np.ndarray) -> np.ndarray:
    out = np.zeros((*label.shape, 3), dtype=np.float32)
    for idx, slab in enumerate(slabs):
        m = (label == idx) & valid
        if m.any():
            out[m] = slab[m]
    return np.clip(out, 0, 255).astype(np.uint8)


def _to_y(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)


def _label_to_y(label: np.ndarray, slab_y: Sequence[np.ndarray]) -> np.ndarray:
    out = np.zeros(label.shape, dtype=np.float32)
    for idx, y in enumerate(slab_y):
        m = label == idx
        if m.any():
            out[m] = y[m]
    return out


def _seam_gap_y(rgb: np.ndarray, label: np.ndarray, valid: np.ndarray) -> dict[str, float | int]:
    y = _to_y(rgb)
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
    }


def _boundary_score(candidate_y: np.ndarray, current_y: np.ndarray, region: np.ndarray, valid: np.ndarray) -> tuple[float, int]:
    vals: list[np.ndarray] = []
    m = region[:-1, :] & (~region[1:, :]) & valid[1:, :]
    if m.any():
        vals.append(np.abs(candidate_y[:-1, :][m] - current_y[1:, :][m]))
    m = region[1:, :] & (~region[:-1, :]) & valid[:-1, :]
    if m.any():
        vals.append(np.abs(candidate_y[1:, :][m] - current_y[:-1, :][m]))
    m = region[:, :-1] & (~region[:, 1:]) & valid[:, 1:]
    if m.any():
        vals.append(np.abs(candidate_y[:, :-1][m] - current_y[:, 1:][m]))
    m = region[:, 1:] & (~region[:, :-1]) & valid[:, :-1]
    if m.any():
        vals.append(np.abs(candidate_y[:, 1:][m] - current_y[:, :-1][m]))
    if not vals:
        return 0.0, 0
    all_vals = np.concatenate([v.reshape(-1).astype(np.float32) for v in vals])
    return float(all_vals.mean()), int(all_vals.size)


def _segment_boundaries(segments: np.ndarray, base: np.ndarray) -> np.ndarray:
    out = np.clip(base, 0, 255).astype(np.uint8).copy()
    edge = np.zeros(segments.shape, dtype=bool)
    edge[:, 1:] |= segments[:, 1:] != segments[:, :-1]
    edge[1:, :] |= segments[1:, :] != segments[:-1, :]
    out[edge] = np.array([255, 255, 255], dtype=np.uint8)
    return out


def _protected_overlay(rgb: np.ndarray, protected: np.ndarray) -> np.ndarray:
    out = np.clip(rgb, 0, 255).astype(np.uint8).copy()
    if protected.any():
        mask = cv2.dilate(protected.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1).astype(bool)
        out[mask] = (0.45 * out[mask].astype(np.float32) + 0.55 * np.array([255, 0, 0], dtype=np.float32)).astype(np.uint8)
    return out


def _make_superpixels(
    hard: np.ndarray,
    depth_hard: np.ndarray,
    valid: np.ndarray,
    n_segments: int,
    compactness: float,
    depth_weight: float,
) -> np.ndarray:
    from skimage.segmentation import slic

    rgb = np.clip(hard, 0, 255).astype(np.float32) / 255.0
    depth = np.clip(depth_hard, 0, 1).astype(np.float32)
    feat = np.dstack([rgb, depth[..., None] * float(depth_weight)])
    feat[~valid] = 0.0
    return slic(
        feat,
        n_segments=n_segments,
        compactness=compactness,
        start_label=1,
        channel_axis=-1,
        convert2lab=False,
        enforce_connectivity=True,
    ).astype(np.int32)


def apply_superpixel_source_coherence(
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    dense_depth_risk: np.ndarray,
    depth_hard: np.ndarray,
    cfg: argparse.Namespace,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, object]]:
    base_label, valid = _winner_label(weights)
    label = base_label.copy()
    hard = _compose(slabs, base_label, valid)
    segments = _make_superpixels(
        hard,
        depth_hard,
        valid,
        n_segments=cfg.n_segments,
        compactness=cfg.compactness,
        depth_weight=cfg.segment_depth_weight,
    )
    slab_y = [_to_y(s) for s in slabs]
    protected = np.zeros_like(base_label, dtype=bool)
    pair_diags: list[dict[str, object]] = []

    for i, j in RING_PAIRS:
        wi = weights[i].astype(np.float32)
        wj = weights[j].astype(np.float32)
        overlap = (wi > 1e-6) & (wj > 1e-6)
        band, signed = build_voronoi_seam_band(wi, wj, band_half_width=cfg.band_half_width, threshold=1e-6)
        band &= overlap
        core = band & (np.abs(signed) <= cfg.core_half_width)
        if not band.any() or not core.any():
            pair_diags.append({"pair": [int(i), int(j)], "status": "no_band"})
            continue

        candidate_ids = np.unique(segments[core])
        current_y = _label_to_y(label, slab_y)
        accepted: list[dict[str, object]] = []
        rejected = 0
        for sid in candidate_ids:
            region = (segments == sid) & band & overlap & ((base_label == i) | (base_label == j))
            area = int(region.sum())
            if area < cfg.min_area or area > cfg.max_area:
                rejected += 1
                continue
            labs = base_label[region]
            if not (np.any(labs == i) and np.any(labs == j)):
                rejected += 1
                continue
            mean_depth_risk = float(dense_depth_risk[region].mean()) if region.any() else 0.0
            if mean_depth_risk < cfg.min_depth_risk:
                rejected += 1
                continue

            scores: dict[int, float] = {}
            details: dict[int, dict[str, float | int]] = {}
            for cam in (i, j):
                boundary, n_boundary = _boundary_score(slab_y[cam], current_y, region, valid)
                mean_weight = float(weights[cam][region].mean())
                changed_frac = float(np.mean(label[region] != cam))
                score = (
                    cfg.boundary_weight * boundary
                    + cfg.source_weight * (1.0 - mean_weight)
                    + cfg.change_weight * changed_frac
                    - cfg.depth_risk_gain * mean_depth_risk
                )
                scores[cam] = float(score)
                details[cam] = {
                    "boundary": float(boundary),
                    "boundary_n": int(n_boundary),
                    "mean_weight": float(mean_weight),
                    "changed_frac": float(changed_frac),
                    "score": float(score),
                }
            owner = i if scores[i] <= scores[j] else j
            current_major = i if np.mean(label[region] == i) >= np.mean(label[region] == j) else j
            if scores[owner] + cfg.min_switch_gain > scores[current_major]:
                owner = current_major
            label[region] = owner
            protected |= region
            yy, xx = np.where(region)
            accepted.append(
                {
                    "segment": int(sid),
                    "owner": int(owner),
                    "area": int(area),
                    "bbox": [int(yy.min()), int(yy.max()) + 1, int(xx.min()), int(xx.max()) + 1],
                    "mean_depth_risk": mean_depth_risk,
                    "score_i": scores[i],
                    "score_j": scores[j],
                    "detail_i": details[i],
                    "detail_j": details[j],
                }
            )
            current_y = _label_to_y(label, slab_y)

        pair_diags.append(
            {
                "pair": [int(i), int(j)],
                "status": "ok",
                "candidate_segments": int(len(candidate_ids)),
                "accepted_segments": int(len(accepted)),
                "rejected_segments": int(rejected),
                "protected_pixels": int(sum(a["area"] for a in accepted)),
                "accepted": accepted[:48],
            }
        )

    out = _compose(slabs, label, valid)
    diag = {
        "segments": {
            "n_segments_requested": int(cfg.n_segments),
            "n_segments_actual": int(segments.max()),
            "compactness": float(cfg.compactness),
            "segment_depth_weight": float(cfg.segment_depth_weight),
        },
        "pairs": pair_diags,
        "changed_pixels": int((label != base_label).sum()),
        "changed_fraction": float(np.mean(label != base_label)),
        "protected_pixels": int(protected.sum()),
    }
    return out, segments, protected, diag


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
    hard_label, valid = _winner_label(weights)
    hard = _compose(slabs, hard_label, valid)

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
    depth_hard = np.zeros(hard_label.shape, dtype=np.float32)
    for idx, z in enumerate(depth_slabs):
        m = hard_label == idx
        depth_hard[m] = z[m]

    sp_out, segments, protected, sp_diag = apply_superpixel_source_coherence(
        slabs,
        weights,
        dense_maps["dense_depth_risk"].astype(np.float32),
        depth_hard,
        args,
    )

    methods = {"hard_select": hard, "superpixel_depth": sp_out}
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
    seam_gap = {name: _seam_gap_y(rgb, hard_label, valid) for name, rgb in methods.items()}

    dense_overlay = _overlay_risk(hard, dense_maps["dense_depth_risk"], dense_maps["seam_core"].astype(bool))
    seg_overlay = _segment_boundaries(segments, hard)
    protected_overlay = _protected_overlay(sp_out, protected)
    review_rows = [
        ("hard_select", hard),
        ("superpixel_depth", sp_out),
        ("changed/protected", protected_overlay),
        ("dense_depth_risk", dense_overlay),
        ("slic_boundaries", seg_overlay),
    ]
    review = _stack_named([(name, _resize_w(img, args.review_w)) for name, img in review_rows])
    _save(out_dir / f"{run_name}_superpixel_depth_review_{args.review_w}.jpg", review, quality=88)
    crops = _default_crops(args.erp_h, args.erp_w)
    crop_review = _crop_stack(review_rows, crops)
    _save(out_dir / f"{run_name}_superpixel_depth_crop_review.jpg", crop_review, quality=88)

    diag = {
        "case": run_name,
        "params": {
            "model_id": args.model_id,
            "n_segments": args.n_segments,
            "compactness": args.compactness,
            "segment_depth_weight": args.segment_depth_weight,
            "min_depth_risk": args.min_depth_risk,
        },
        "dense_depth_global": dense_diag["global"],
        "overlap_ncc": ncc,
        "seam_gap_y": seam_gap,
        "superpixel": sp_diag,
        "depth_model": depth_model_diag,
        "runtime_s": {"project_rgb": round(project_s, 3)},
        "outputs": {
            "review": f"{run_name}_superpixel_depth_review_{args.review_w}.jpg",
            "crop_review": f"{run_name}_superpixel_depth_crop_review.jpg",
        },
    }
    with open(out_dir / f"{run_name}_superpixel_depth_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(diag), f, indent=2, ensure_ascii=False)
    print(
        json.dumps(
            {
                "case": run_name,
                "changed_fraction": sp_diag["changed_fraction"],
                "protected_pixels": sp_diag["protected_pixels"],
                "hard_ncc": ncc["hard_select"].get("mean_ncc_pano_vs_winner"),
                "sp_ncc": ncc["superpixel_depth"].get("mean_ncc_pano_vs_winner"),
                "hard_dY": seam_gap["hard_select"].get("mean_delta_y"),
                "sp_dY": seam_gap["superpixel_depth"].get("mean_delta_y"),
            },
            indent=2,
        ),
        flush=True,
    )
    return diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/superpixel_depth_coherent_v1"))
    ap.add_argument("--cases", nargs="+", default=DEFAULT_CASES)
    ap.add_argument("--model-id", default="depth-anything/Depth-Anything-V2-Small-hf")
    ap.add_argument("--device", type=int, default=0)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=72)
    ap.add_argument("--core-half-width", type=int, default=3)
    ap.add_argument("--n-segments", type=int, default=1800)
    ap.add_argument("--compactness", type=float, default=14.0)
    ap.add_argument("--segment-depth-weight", type=float, default=0.75)
    ap.add_argument("--min-area", type=int, default=72)
    ap.add_argument("--max-area", type=int, default=24000)
    ap.add_argument("--min-depth-risk", type=float, default=0.12)
    ap.add_argument("--boundary-weight", type=float, default=1.0)
    ap.add_argument("--source-weight", type=float, default=20.0)
    ap.add_argument("--change-weight", type=float, default=6.0)
    ap.add_argument("--depth-risk-gain", type=float, default=0.0)
    ap.add_argument("--min-switch-gain", type=float, default=0.0)
    ap.add_argument("--review-w", type=int, default=1024)
    ap.add_argument("--ncc-win", type=int, default=9)
    ap.add_argument("--max-sample-per-pair", type=int, default=20000)
    args = ap.parse_args()
    if args.ncc_win % 2 == 0:
        args.ncc_win += 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_diags = [_one_case(case, args.av2_root, args.out_dir, args) for case in args.cases]

    compact_rows = []
    for d in all_diags:
        p = args.out_dir / d["case"] / d["outputs"]["crop_review"]
        if p.exists():
            img = cv2.cvtColor(cv2.imread(str(p), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            compact_rows.append((d["case"], _resize_w(img, args.review_w)))
    if compact_rows:
        _save_rgb(args.out_dir / "superpixel_depth_three_anchor_compact_review.jpg", _stack_named(compact_rows), quality=88)

    summary = {
        "run": args.out_dir.name,
        "cases": all_diags,
        "aggregate": {
            "n_cases": len(all_diags),
            "mean_changed_fraction": float(np.mean([d["superpixel"]["changed_fraction"] for d in all_diags])),
            "mean_hard_ncc": float(np.mean([d["overlap_ncc"]["hard_select"]["mean_ncc_pano_vs_winner"] for d in all_diags])),
            "mean_sp_ncc": float(np.mean([d["overlap_ncc"]["superpixel_depth"]["mean_ncc_pano_vs_winner"] for d in all_diags])),
            "mean_hard_dY": float(np.mean([d["seam_gap_y"]["hard_select"]["mean_delta_y"] for d in all_diags])),
            "mean_sp_dY": float(np.mean([d["seam_gap_y"]["superpixel_depth"]["mean_delta_y"] for d in all_diags])),
        },
    }
    with open(args.out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(summary), f, indent=2, ensure_ascii=False)
    print(json.dumps(_json_safe(summary["aggregate"]), indent=2), flush=True)
    print(f"[saved] {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
