"""Dense monocular-depth edge seam probe using Depth Anything V2.

This is a learned-depth metadata test, not a panorama renderer. It runs a
monocular depth estimator on each raw AV2 camera, projects the relative depth
maps to the same ERP slabs as L1, and builds a dense seam risk map from:

  - depth edges near the hard_select seam
  - cross-camera normalized depth disagreement inside overlap

The final image remains L1 hard_select. The only repair tested is the existing
conservative Y-only seam polish, once with source-risk gates and once with an
additional dense-depth veto.

Rationale: sparse LiDAR can flag some near/unknown strips, but it covers only
about half the seam band. This script tests whether a modern dense depth prior
adds useful boundary metadata without falling back to failed depth rendering.
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
    return short, av2_root / LOG_UUIDS.get(short, short), anchor, tag


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
    av -= float(av.mean())
    bv -= float(bv.mean())
    denom = float(np.sqrt(np.sum(av * av) * np.sum(bv * bv)))
    if denom <= 1e-8:
        return None
    return float(np.sum(av * bv) / denom)


def _robust_unit(x: np.ndarray, valid: np.ndarray | None = None) -> np.ndarray:
    vals = x[valid].astype(np.float32) if valid is not None and valid.any() else x.reshape(-1).astype(np.float32)
    vals = vals[np.isfinite(vals)]
    if vals.size < 16:
        return np.zeros_like(x, dtype=np.float32)
    lo = float(np.percentile(vals, 2.0))
    hi = float(np.percentile(vals, 98.0))
    if hi <= lo + 1e-6:
        return np.zeros_like(x, dtype=np.float32)
    return np.clip((x.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)


def _sobel_unit(x: np.ndarray, valid: np.ndarray) -> np.ndarray:
    xf = x.astype(np.float32)
    gx = cv2.Sobel(xf, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(xf, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    return _robust_unit(mag, valid)


def _depth_to_uint8(depth: np.ndarray) -> np.ndarray:
    unit = _robust_unit(depth)
    u8 = np.clip(unit * 255.0, 0, 255).astype(np.uint8)
    return np.dstack([u8, u8, u8])


def _extract_depth_array(pipe, image_rgb: np.ndarray) -> np.ndarray:
    out = pipe(Image.fromarray(image_rgb))
    pred = out.get("predicted_depth")
    if pred is not None:
        try:
            arr = pred.detach().float().cpu().numpy()
        except AttributeError:
            arr = np.asarray(pred, dtype=np.float32)
        arr = np.squeeze(arr).astype(np.float32)
    else:
        arr = np.asarray(out["depth"], dtype=np.float32)
    if arr.shape[:2] != image_rgb.shape[:2]:
        arr = cv2.resize(arr, (image_rgb.shape[1], image_rgb.shape[0]), interpolation=cv2.INTER_CUBIC)
    return arr.astype(np.float32)


def build_dense_depth_slabs(
    frame,
    erp_hw: tuple[int, int],
    model_id: str,
    device: int,
) -> tuple[list[np.ndarray], list[np.ndarray], dict[str, object]]:
    import torch
    from transformers import pipeline

    t0 = time.time()
    dtype = torch.float16 if torch.cuda.is_available() and device >= 0 else torch.float32
    pipe = pipeline("depth-estimation", model=model_id, device=device, dtype=dtype)
    load_s = time.time() - t0

    depth_slabs: list[np.ndarray] = []
    depth_weights: list[np.ndarray] = []
    per_cam: dict[str, object] = {}
    t_infer = 0.0
    t_project = 0.0
    for cam in RING_CAMS_7:
        img = frame.images[cam]
        t0 = time.time()
        depth = _extract_depth_array(pipe, img)
        t_infer += time.time() - t0
        depth_u8 = _depth_to_uint8(depth)
        calib = frame.calibrations[cam]
        t0 = time.time()
        depth_rgb, _alpha, w = render_camera_to_erp(
            image=depth_u8,
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=None,
        )
        t_project += time.time() - t0
        depth_unit = np.clip(depth_rgb[..., 0] / 255.0, 0.0, 1.0).astype(np.float32)
        depth_slabs.append(depth_unit)
        depth_weights.append(w.astype(np.float32))
        per_cam[cam] = {
            "depth_min": float(np.min(depth)),
            "depth_max": float(np.max(depth)),
            "depth_median": float(np.median(depth)),
            "valid_erp_frac": float(np.mean(w > 1e-6)),
        }
    diag = {
        "model_id": model_id,
        "device": int(device),
        "load_s": round(load_s, 3),
        "infer_s": round(t_infer, 3),
        "project_depth_s": round(t_project, 3),
        "per_cam": per_cam,
    }
    return depth_slabs, depth_weights, diag


def compute_dense_depth_edge_risk(
    depth_slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    band_half_width: int,
    core_half_width: int,
) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    H, W = weights[0].shape
    risk = np.zeros((H, W), dtype=np.float32)
    edge_risk = np.zeros((H, W), dtype=np.float32)
    mismatch_risk = np.zeros((H, W), dtype=np.float32)
    seam_band = np.zeros((H, W), dtype=bool)
    seam_core = np.zeros((H, W), dtype=bool)
    pair_diags: list[dict[str, object]] = []

    for i, j in RING_PAIRS:
        wi = weights[i].astype(np.float32)
        wj = weights[j].astype(np.float32)
        overlap = (wi > 1e-6) & (wj > 1e-6)
        roll = W // 2 if _overlap_wraps(overlap) else 0
        if roll:
            wi_p = np.roll(wi, roll, axis=1)
            wj_p = np.roll(wj, roll, axis=1)
            overlap_p = np.roll(overlap, roll, axis=1)
            di = np.roll(depth_slabs[i], roll, axis=1)
            dj = np.roll(depth_slabs[j], roll, axis=1)
        else:
            wi_p, wj_p = wi, wj
            overlap_p = overlap
            di, dj = depth_slabs[i], depth_slabs[j]

        band, signed = build_voronoi_seam_band(
            wi_p,
            wj_p,
            band_half_width=band_half_width,
            threshold=1e-6,
        )
        band &= overlap_p
        core = band & (np.abs(signed) <= core_half_width)
        if not band.any():
            pair_diags.append({"pair": [int(i), int(j)], "status": "no_band"})
            continue

        ei = _sobel_unit(di, overlap_p)
        ej = _sobel_unit(dj, overlap_p)
        e = np.maximum(ei, ej)

        # Relative monocular depth has arbitrary per-camera affine scale. Align
        # each depth slab by robust z-score inside overlap before comparing.
        def zscore(x: np.ndarray) -> np.ndarray:
            vals = x[overlap_p]
            med = float(np.median(vals))
            iqr = float(np.percentile(vals, 75) - np.percentile(vals, 25))
            return (x - med) / max(iqr, 1e-3)

        zi = zscore(di)
        zj = zscore(dj)
        mismatch = _robust_unit(np.abs(zi - zj), band)
        pair_risk = np.clip(0.65 * e + 0.35 * mismatch, 0.0, 1.0)

        if roll:
            band = np.roll(band, -roll, axis=1)
            core = np.roll(core, -roll, axis=1)
            pair_risk = np.roll(pair_risk, -roll, axis=1)
            e = np.roll(e, -roll, axis=1)
            mismatch = np.roll(mismatch, -roll, axis=1)

        seam_band |= band
        seam_core |= core
        update = band & (pair_risk > risk)
        risk[update] = pair_risk[update]
        edge_risk[update] = e[update]
        mismatch_risk[update] = mismatch[update]
        pair_diags.append(
            {
                "pair": [int(i), int(j)],
                "status": "ok",
                "rolled": bool(roll),
                "band_pixels": int(band.sum()),
                "core_pixels": int(core.sum()),
                "dense_depth_risk": _risk_stats("dense_depth", pair_risk, band),
                "high_dense_depth_frac": float(np.mean(pair_risk[band] >= 0.65)),
            }
        )

    maps = {
        "dense_depth_risk": risk,
        "dense_depth_edge_risk": edge_risk,
        "dense_depth_mismatch_risk": mismatch_risk,
        "seam_band": seam_band.astype(np.float32),
        "seam_core": seam_core.astype(np.float32),
    }
    diag = {
        "pair_diagnostics": pair_diags,
        "global": {
            "band_pixels": int(seam_band.sum()),
            "core_pixels": int(seam_core.sum()),
            "dense_depth_risk": _risk_stats("global_dense_depth", risk, seam_band),
            "high_dense_depth_pixels": int(((risk >= 0.65) & seam_band).sum()),
            "high_dense_depth_frac_of_band": float(((risk >= 0.65) & seam_band).sum() / max(1, seam_band.sum())),
        },
    }
    return maps, diag


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

    source_maps, source_diag = compute_seam_risk_maps(
        slabs,
        weights,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        ncc_win=args.ncc_win,
    )
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
        dense_maps["dense_depth_risk"].astype(np.float32) * args.depth_veto_strength,
    ).astype(np.float32)
    repaired_dense, corr_dense, diag_repair_dense = _repair_local_y(
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

    hard_gap = _seam_gap_y(hard, label, valid)
    source_gap = _seam_gap_y(repaired_source, label, valid)
    dense_gap = _seam_gap_y(repaired_dense, label, valid)

    def reduction(after: dict[str, object]) -> float | None:
        if not hard_gap.get("n") or not after.get("n"):
            return None
        before = float(hard_gap["mean_delta_y"])
        return float((before - float(after["mean_delta_y"])) / before) if before > 1e-9 else None

    source_overlay = _overlay_risk(hard, source_maps["risk"], source_maps["seam_core"].astype(bool))
    dense_overlay = _overlay_risk(hard, dense_maps["dense_depth_risk"], dense_maps["seam_core"].astype(bool))
    dense_heat = _heatmap_u8(dense_maps["dense_depth_risk"])
    depth_slab_vis = []
    for z, w in zip(depth_slabs, weights):
        u8 = np.clip(z * 255, 0, 255).astype(np.uint8)
        rgb = cv2.cvtColor(cv2.applyColorMap(u8, cv2.COLORMAP_TURBO), cv2.COLOR_BGR2RGB)
        rgb[w <= 1e-6] = 0
        depth_slab_vis.append(rgb)
    # Hard-select the depth visualization itself, just for review.
    depth_vis = hard_select(depth_slab_vis, weights)

    crops = _default_crops(args.erp_h, args.erp_w)
    crop_review = _crop_stack(
        [
            ("hard_select", hard),
            ("source_risk", source_overlay),
            ("dense_depth_risk", dense_overlay),
            ("Y_source_gate", repaired_source),
            ("Y_dense_depth_gate", repaired_dense),
            ("dense_depth_hard_vis", depth_vis),
        ],
        crops,
    )
    _save_rgb(out_dir / f"{run_name}_dense_depth_edge_crop_review.jpg", crop_review, quality=88)
    review = _stack_named(
        [
            ("hard_select", _resize_w(hard, args.review_w)),
            ("source_risk_overlay", _resize_w(source_overlay, args.review_w)),
            ("dense_depth_risk_overlay", _resize_w(dense_overlay, args.review_w)),
            ("dense_depth_risk_heat", _resize_w(dense_heat, args.review_w)),
            ("Y_source_gate", _resize_w(repaired_source, args.review_w)),
            ("Y_dense_depth_gate", _resize_w(repaired_dense, args.review_w)),
            ("dense_depth_hard_vis", _resize_w(depth_vis, args.review_w)),
        ]
    )
    _save_rgb(out_dir / f"{run_name}_dense_depth_edge_review_{args.review_w}.jpg", review, quality=88)

    seam_band = source_maps["seam_band"].astype(bool)
    diag = {
        "case": run_name,
        "model_id": args.model_id,
        "log_short": short,
        "anchor_idx": int(anchor_idx),
        "source_risk_global": source_diag["global"],
        "dense_depth_global": dense_diag["global"],
        "dense_source_correlations_on_seam": {
            "dense_vs_source_risk": _safe_corr(dense_maps["dense_depth_risk"], source_maps["risk"], seam_band),
            "dense_vs_structure_risk": _safe_corr(dense_maps["dense_depth_risk"], source_maps["structure_risk"], seam_band),
            "dense_vs_color_risk": _safe_corr(dense_maps["dense_depth_risk"], source_maps["color_risk"], seam_band),
        },
        "seam_gap_y": {
            "hard_select": hard_gap,
            "source_gate_repair": source_gap,
            "dense_depth_gate_repair": dense_gap,
            "source_gate_mean_reduction": reduction(source_gap),
            "dense_depth_gate_mean_reduction": reduction(dense_gap),
        },
        "repair_diagnostics": {
            "source_gate": diag_repair_source,
            "dense_depth_gate": diag_repair_dense,
            "source_gate_changed_fraction_vs_hard": float(np.mean(np.any(repaired_source != hard, axis=2))),
            "dense_depth_gate_changed_fraction_vs_hard": float(np.mean(np.any(repaired_dense != hard, axis=2))),
            "source_vs_dense_gate_mae": float(np.mean(np.abs(repaired_source.astype(np.float32) - repaired_dense.astype(np.float32)))),
            "source_corr_abs_mean": float(np.mean(np.abs(corr_source))),
            "dense_corr_abs_mean": float(np.mean(np.abs(corr_dense))),
        },
        "depth_model": depth_model_diag,
        "pair_diagnostics": {
            "source": source_diag["pair_diagnostics"],
            "dense_depth": dense_diag["pair_diagnostics"],
        },
        "runtime_s": {
            "project_rgb": round(project_s, 3),
        },
        "outputs": {
            "crop_review": f"{run_name}_dense_depth_edge_crop_review.jpg",
            "review": f"{run_name}_dense_depth_edge_review_{args.review_w}.jpg",
        },
    }
    with open(out_dir / f"{run_name}_dense_depth_edge_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(diag), f, indent=2, ensure_ascii=False)
    print(
        json.dumps(
            {
                "case": run_name,
                "high_dense_depth_frac": dense_diag["global"]["high_dense_depth_frac_of_band"],
                "hard_mean_dY": hard_gap.get("mean_delta_y"),
                "source_reduction": diag["seam_gap_y"]["source_gate_mean_reduction"],
                "dense_depth_reduction": diag["seam_gap_y"]["dense_depth_gate_mean_reduction"],
                "corr": diag["dense_source_correlations_on_seam"],
            },
            indent=2,
        ),
        flush=True,
    )
    return diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/dense_depth_edge_seam_probe_v1"))
    ap.add_argument("--cases", nargs="+", default=DEFAULT_CASES)
    ap.add_argument("--model-id", default="depth-anything/Depth-Anything-V2-Small-hf")
    ap.add_argument("--device", type=int, default=0)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=48)
    ap.add_argument("--repair-band-half-width", type=int, default=48)
    ap.add_argument("--core-half-width", type=int, default=2)
    ap.add_argument("--ncc-win", type=int, default=21)
    ap.add_argument("--structure-thresh", type=float, default=0.35)
    ap.add_argument("--max-abs-delta", type=float, default=28.0)
    ap.add_argument("--correction-strength", type=float, default=0.65)
    ap.add_argument("--depth-veto-strength", type=float, default=1.0)
    ap.add_argument("--review-w", type=int, default=1024)
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
        _save_rgb(args.out_dir / "dense_depth_edge_three_anchor_compact_review.jpg", _stack_named(compact_rows), quality=88)

    summary = {
        "run": args.out_dir.name,
        "cases": all_diags,
        "aggregate": {
            "n_cases": len(all_diags),
            "mean_high_dense_depth_frac_of_band": float(
                np.mean([d["dense_depth_global"]["high_dense_depth_frac_of_band"] for d in all_diags])
            )
            if all_diags
            else None,
            "mean_source_gate_dY_reduction": float(
                np.mean([d["seam_gap_y"]["source_gate_mean_reduction"] for d in all_diags if d["seam_gap_y"]["source_gate_mean_reduction"] is not None])
            )
            if any(d["seam_gap_y"]["source_gate_mean_reduction"] is not None for d in all_diags)
            else None,
            "mean_dense_depth_gate_dY_reduction": float(
                np.mean([d["seam_gap_y"]["dense_depth_gate_mean_reduction"] for d in all_diags if d["seam_gap_y"]["dense_depth_gate_mean_reduction"] is not None])
            )
            if any(d["seam_gap_y"]["dense_depth_gate_mean_reduction"] is not None for d in all_diags)
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
