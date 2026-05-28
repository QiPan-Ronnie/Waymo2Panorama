"""Build source-evidence seam confidence/risk maps for L1 hard_select.

This is not another blender. It keeps the current L1 hard-select panorama and
asks a narrower question: where is the seam likely a harmless color/texture
transition, and where is it a high-risk geometry conflict caused by different
camera centers?

The map is computed only from existing L1 ERP slabs and weights:
  - color risk: Y-channel disagreement between adjacent source cameras
  - structure risk: strong source edges with poor local cross-camera NCC
  - reliability risk: weak overlap support near FoV boundaries

Output is meant for diagnostics, Bosch data filtering, and future seam routing.
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

from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


def _save_rgb(path: Path, rgb: np.ndarray, quality: int = 92) -> None:
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


def _label_panel(rgb: np.ndarray, label: str, label_h: int = 38) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    band = np.zeros((label_h, rgb.shape[1], 3), dtype=np.uint8)
    cv2.putText(band, label, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([band, rgb])


def _stack_named(rows: list[tuple[str, np.ndarray]]) -> np.ndarray:
    panels = [_label_panel(img, label) for label, img in rows]
    width = max(p.shape[1] for p in panels)
    padded = []
    for panel in panels:
        if panel.shape[1] < width:
            pad = np.zeros((panel.shape[0], width - panel.shape[1], 3), dtype=np.uint8)
            panel = np.hstack([panel, pad])
        padded.append(panel)
    return np.vstack(padded)


def _default_crops(H: int, W: int) -> dict[str, tuple[int, int, int, int]]:
    return {
        "left_lane": (int(0.42 * H), int(0.61 * H), int(0.04 * W), int(0.27 * W)),
        "mid_lane": (int(0.40 * H), int(0.62 * H), int(0.18 * W), int(0.42 * W)),
        "road_center": (int(0.25 * H), int(0.62 * H), int(0.33 * W), int(0.66 * W)),
        "right_suv": (int(0.22 * H), int(0.63 * H), int(0.67 * W), int(0.92 * W)),
    }


def _to_y_u8(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]


def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    gray_f = gray.astype(np.float32)
    gx = cv2.Sobel(gray_f, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray_f, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy).astype(np.float32)


def _robust_norm(x: np.ndarray, mask: np.ndarray, lo_pct: float = 5.0, hi_pct: float = 95.0) -> np.ndarray:
    if mask.any():
        vals = x[mask].astype(np.float32)
    else:
        vals = x.reshape(-1).astype(np.float32)
    if vals.size == 0:
        return np.zeros_like(x, dtype=np.float32)
    lo = float(np.percentile(vals, lo_pct))
    hi = float(np.percentile(vals, hi_pct))
    if hi <= lo + 1e-6:
        return np.zeros_like(x, dtype=np.float32)
    return np.clip((x.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def _local_ncc(a: np.ndarray, b: np.ndarray, valid: np.ndarray, win: int) -> tuple[np.ndarray, np.ndarray]:
    """Masked local NCC in [-1, 1] plus support count."""
    if win % 2 == 0:
        win += 1
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    m = valid.astype(np.float32)
    ksize = (win, win)
    count = cv2.boxFilter(m, cv2.CV_32F, ksize, normalize=False, borderType=cv2.BORDER_REFLECT)
    sum_a = cv2.boxFilter(a * m, cv2.CV_32F, ksize, normalize=False, borderType=cv2.BORDER_REFLECT)
    sum_b = cv2.boxFilter(b * m, cv2.CV_32F, ksize, normalize=False, borderType=cv2.BORDER_REFLECT)
    sum_aa = cv2.boxFilter(a * a * m, cv2.CV_32F, ksize, normalize=False, borderType=cv2.BORDER_REFLECT)
    sum_bb = cv2.boxFilter(b * b * m, cv2.CV_32F, ksize, normalize=False, borderType=cv2.BORDER_REFLECT)
    sum_ab = cv2.boxFilter(a * b * m, cv2.CV_32F, ksize, normalize=False, borderType=cv2.BORDER_REFLECT)

    safe_count = np.maximum(count, 1.0)
    mean_a = sum_a / safe_count
    mean_b = sum_b / safe_count
    var_a = np.maximum(sum_aa / safe_count - mean_a * mean_a, 0.0)
    var_b = np.maximum(sum_bb / safe_count - mean_b * mean_b, 0.0)
    cov = sum_ab / safe_count - mean_a * mean_b
    denom = np.sqrt(var_a * var_b)
    ncc = np.zeros_like(a, dtype=np.float32)
    good = (count >= max(16, win * win // 6)) & (denom > 1e-4)
    ncc[good] = np.clip(cov[good] / denom[good], -1.0, 1.0)
    return ncc, count


def _overlap_wraps(overlap: np.ndarray) -> bool:
    if not overlap.any():
        return False
    cols = overlap.any(axis=0)
    idx = np.flatnonzero(cols)
    if idx.size == 0:
        return False
    return bool(cols[0] and cols[-1] and (int(idx[-1] - idx[0] + 1) > 0.6 * overlap.shape[1]))


def _heatmap_u8(x: np.ndarray) -> np.ndarray:
    u8 = np.clip(x * 255.0, 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.applyColorMap(u8, cv2.COLORMAP_TURBO), cv2.COLOR_BGR2RGB)


def _overlay_risk(rgb: np.ndarray, risk: np.ndarray, seam_core: np.ndarray) -> np.ndarray:
    base = np.clip(rgb, 0, 255).astype(np.uint8)
    heat = _heatmap_u8(risk)
    alpha = np.clip(risk * 0.72, 0.0, 0.72)[..., None].astype(np.float32)
    out = np.clip(base.astype(np.float32) * (1.0 - alpha) + heat.astype(np.float32) * alpha, 0, 255).astype(np.uint8)
    core = seam_core.astype(np.uint8)
    if core.any():
        core = cv2.dilate(core, np.ones((3, 3), np.uint8), iterations=1).astype(bool)
        out[core] = np.array([255, 255, 255], dtype=np.uint8)
    return out


def _winner_label_rgb(weights: Sequence[np.ndarray]) -> np.ndarray:
    palette = np.array(
        [
            [255, 80, 80],
            [255, 180, 60],
            [240, 240, 70],
            [70, 210, 110],
            [80, 200, 255],
            [90, 110, 255],
            [210, 90, 255],
        ],
        dtype=np.uint8,
    )
    stack = np.stack(weights, axis=0)
    label = stack.argmax(axis=0)
    valid = stack.max(axis=0) > 1e-6
    rgb = palette[label % len(palette)].copy()
    rgb[~valid] = 0
    return rgb


def _risk_stats(name: str, values: np.ndarray, mask: np.ndarray) -> dict[str, float | int | str]:
    if not mask.any():
        return {"name": name, "n": 0}
    vals = values[mask].astype(np.float32)
    return {
        "name": name,
        "n": int(vals.size),
        "mean": float(vals.mean()),
        "median": float(np.median(vals)),
        "p90": float(np.percentile(vals, 90)),
        "p95": float(np.percentile(vals, 95)),
        "p99": float(np.percentile(vals, 99)),
        "high_frac_065": float(np.mean(vals >= 0.65)),
        "high_frac_080": float(np.mean(vals >= 0.80)),
    }


def _bbox(mask: np.ndarray, pad: int = 0) -> list[int] | None:
    if not mask.any():
        return None
    yy, xx = np.where(mask)
    return [
        max(0, int(yy.min()) - pad),
        min(mask.shape[0], int(yy.max()) + 1 + pad),
        max(0, int(xx.min()) - pad),
        min(mask.shape[1], int(xx.max()) + 1 + pad),
    ]


def compute_seam_risk_maps(
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    band_half_width: int,
    core_half_width: int,
    ncc_win: int,
    threshold: float = 1e-6,
) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    H, W = weights[0].shape
    risk = np.zeros((H, W), dtype=np.float32)
    color_risk = np.zeros((H, W), dtype=np.float32)
    structure_risk = np.zeros((H, W), dtype=np.float32)
    reliability_risk = np.zeros((H, W), dtype=np.float32)
    seam_band = np.zeros((H, W), dtype=bool)
    seam_core = np.zeros((H, W), dtype=bool)
    high_structure = np.zeros((H, W), dtype=bool)
    high_color = np.zeros((H, W), dtype=bool)
    pair_diags: list[dict[str, object]] = []

    for i, j in RING_PAIRS:
        slab_a = slabs[i].astype(np.float32)
        slab_b = slabs[j].astype(np.float32)
        weight_a = weights[i].astype(np.float32)
        weight_b = weights[j].astype(np.float32)
        overlap = (weight_a > threshold) & (weight_b > threshold)
        roll = W // 2 if _overlap_wraps(overlap) else 0
        if roll:
            slab_a_p = np.roll(slab_a, roll, axis=1)
            slab_b_p = np.roll(slab_b, roll, axis=1)
            weight_a_p = np.roll(weight_a, roll, axis=1)
            weight_b_p = np.roll(weight_b, roll, axis=1)
            overlap_p = np.roll(overlap, roll, axis=1)
        else:
            slab_a_p, slab_b_p = slab_a, slab_b
            weight_a_p, weight_b_p = weight_a, weight_b
            overlap_p = overlap

        band, signed = build_voronoi_seam_band(
            weight_a_p,
            weight_b_p,
            band_half_width=band_half_width,
            threshold=threshold,
        )
        band &= overlap_p
        core = band & (np.abs(signed) <= core_half_width)
        if not band.any():
            pair_diags.append({"pair": [i, j], "status": "no_overlap", "band_pixels": 0})
            continue

        y_a = _to_y_u8(slab_a_p)
        y_b = _to_y_u8(slab_b_p)
        edge_a = _sobel_mag(y_a)
        edge_b = _sobel_mag(y_b)
        edge = np.maximum(edge_a, edge_b)
        edge_norm = _robust_norm(edge, band, lo_pct=20.0, hi_pct=97.0)
        photo = np.abs(y_a.astype(np.float32) - y_b.astype(np.float32))
        photo_norm = _robust_norm(photo, band, lo_pct=10.0, hi_pct=95.0)
        grad_mis = _robust_norm(np.abs(edge_a - edge_b), band, lo_pct=10.0, hi_pct=95.0)
        ncc, support = _local_ncc(y_a, y_b, overlap_p, ncc_win)
        ncc_risk = np.clip(1.0 - ((ncc + 1.0) * 0.5), 0.0, 1.0).astype(np.float32)

        min_w = np.minimum(weight_a_p, weight_b_p)
        min_w_norm = _robust_norm(min_w, band, lo_pct=5.0, hi_pct=95.0)
        rel_risk = 1.0 - min_w_norm

        c_risk = photo_norm * (0.65 + 0.35 * (1.0 - edge_norm))
        s_risk = np.clip(edge_norm * (0.65 * ncc_risk + 0.35 * grad_mis), 0.0, 1.0)
        r_risk = rel_risk
        p_risk = np.clip(0.35 * c_risk + 0.50 * s_risk + 0.15 * r_risk, 0.0, 1.0)

        if roll:
            band = np.roll(band, -roll, axis=1)
            core = np.roll(core, -roll, axis=1)
            c_risk = np.roll(c_risk, -roll, axis=1)
            s_risk = np.roll(s_risk, -roll, axis=1)
            r_risk = np.roll(r_risk, -roll, axis=1)
            p_risk = np.roll(p_risk, -roll, axis=1)
            support = np.roll(support, -roll, axis=1)

        seam_band |= band
        seam_core |= core
        update = band & (p_risk > risk)
        risk[update] = p_risk[update]
        color_risk[update] = c_risk[update]
        structure_risk[update] = s_risk[update]
        reliability_risk[update] = r_risk[update]
        high_structure |= band & (s_risk >= 0.65)
        high_color |= band & (c_risk >= 0.65) & (s_risk < 0.50)

        pair_diags.append(
            {
                "pair": [int(i), int(j)],
                "status": "ok",
                "rolled": bool(roll),
                "band_pixels": int(band.sum()),
                "core_pixels": int(core.sum()),
                "risk": _risk_stats("risk", p_risk, band),
                "color_risk": _risk_stats("color", c_risk, band),
                "structure_risk": _risk_stats("structure", s_risk, band),
                "reliability_risk": _risk_stats("reliability", r_risk, band),
                "mean_ncc": float(np.mean((1.0 - 2.0 * ncc_risk)[band])),
                "mean_ncc_support": float(np.mean(support[band])),
                "high_risk_bbox_y0y1x0x1": _bbox(band & (p_risk >= 0.75), pad=8),
            }
        )

    maps = {
        "risk": risk,
        "color_risk": color_risk,
        "structure_risk": structure_risk,
        "reliability_risk": reliability_risk,
        "seam_band": seam_band.astype(np.float32),
        "seam_core": seam_core.astype(np.float32),
        "high_structure": high_structure.astype(np.float32),
        "high_color": high_color.astype(np.float32),
    }
    diag = {
        "pair_diagnostics": pair_diags,
        "global": {
            "band_pixels": int(seam_band.sum()),
            "core_pixels": int(seam_core.sum()),
            "risk": _risk_stats("global_risk", risk, seam_band),
            "color_risk": _risk_stats("global_color", color_risk, seam_band),
            "structure_risk": _risk_stats("global_structure", structure_risk, seam_band),
            "reliability_risk": _risk_stats("global_reliability", reliability_risk, seam_band),
            "high_structure_pixels": int(high_structure.sum()),
            "high_color_pixels": int(high_color.sum()),
            "high_structure_frac_of_band": float(high_structure.sum() / max(1, seam_band.sum())),
            "high_color_frac_of_band": float(high_color.sum() / max(1, seam_band.sum())),
        },
    }
    return maps, diag


def _crop_stack(rows: list[tuple[str, np.ndarray]], crops: dict[str, tuple[int, int, int, int]]) -> np.ndarray:
    rendered_rows = []
    for label, img in rows:
        parts = []
        for crop_name, (y0, y1, x0, x1) in crops.items():
            crop = np.clip(img[y0:y1, x0:x1], 0, 255).astype(np.uint8)
            parts.append(_label_panel(crop, f"{label} | {crop_name}", label_h=32))
        max_h = max(p.shape[0] for p in parts)
        padded = []
        for p in parts:
            if p.shape[0] < max_h:
                p = np.vstack([p, np.zeros((max_h - p.shape[0], p.shape[1], 3), dtype=np.uint8)])
            padded.append(p)
        rendered_rows.append(np.hstack(padded))
    return np.vstack(rendered_rows)


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
    ap.add_argument("--review-w", type=int, default=1024)
    ap.add_argument("--save-full", action="store_true")
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
    maps, diag = compute_seam_risk_maps(
        slabs,
        weights,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        ncc_win=args.ncc_win,
    )
    risk = maps["risk"]
    core = maps["seam_core"].astype(bool)
    overlay = _overlay_risk(hard, risk, core)
    risk_heat = _heatmap_u8(risk)
    color_heat = _heatmap_u8(maps["color_risk"])
    structure_heat = _heatmap_u8(maps["structure_risk"])
    reliability_heat = _heatmap_u8(maps["reliability_risk"])
    labels = _winner_label_rgb(weights)

    review_rows_full = [
        ("hard_select", hard),
        ("risk_overlay white=seam_core", overlay),
        ("risk_heat", risk_heat),
        ("structure_risk", structure_heat),
        ("color_risk", color_heat),
        ("winner_cam_map", labels),
    ]
    review_rows_small = [(label, _resize_w(img, args.review_w)) for label, img in review_rows_full]
    _save_rgb(out_dir / f"{run_name}_risk_review_{args.review_w}.jpg", _stack_named(review_rows_small), quality=88)

    crops = _default_crops(args.erp_h, args.erp_w)
    _save_rgb(
        out_dir / f"{run_name}_risk_crop_review.jpg",
        _crop_stack(
            [
                ("hard_select", hard),
                ("risk_overlay", overlay),
                ("structure_risk", structure_heat),
                ("color_risk", color_heat),
            ],
            crops,
        ),
        quality=88,
    )
    _save_rgb(out_dir / f"{run_name}_hard_select_{args.review_w}.jpg", _resize_w(hard, args.review_w), quality=90)
    _save_rgb(out_dir / f"{run_name}_risk_overlay_{args.review_w}.jpg", _resize_w(overlay, args.review_w), quality=90)
    _save_rgb(out_dir / f"{run_name}_risk_heat_{args.review_w}.jpg", _resize_w(risk_heat, args.review_w), quality=90)

    if args.save_full:
        _save_rgb(out_dir / f"{run_name}_hard_select.png", hard)
        _save_rgb(out_dir / f"{run_name}_risk_overlay.png", overlay)
        np.savez_compressed(
            out_dir / f"{run_name}_risk_maps.npz",
            **{k: v.astype(np.float32) for k, v in maps.items()},
        )

    diag.update(
        {
            "log_short": log_short,
            "anchor_idx": args.anchor_idx,
            "erp_hw": list(erp_hw),
            "params": {
                "band_half_width": args.band_half_width,
                "core_half_width": args.core_half_width,
                "ncc_win": args.ncc_win,
                "review_w": args.review_w,
                "save_full": bool(args.save_full),
            },
            "runtime_s": {"projection": round(project_s, 3)},
            "outputs": {
                "risk_review": f"{run_name}_risk_review_{args.review_w}.jpg",
                "risk_crop_review": f"{run_name}_risk_crop_review.jpg",
                "risk_overlay": f"{run_name}_risk_overlay_{args.review_w}.jpg",
                "risk_heat": f"{run_name}_risk_heat_{args.review_w}.jpg",
            },
        }
    )
    with open(out_dir / f"{run_name}_risk_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2, ensure_ascii=False)
    print(json.dumps(diag["global"], indent=2, ensure_ascii=False), flush=True)
    print(f"[saved] {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
