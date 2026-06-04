"""DB-31: multi-log relaxed-clean source candidate scan.

CPU-only diagnostic. It does not edit panoramas or load generative weights.

The goal is to move upstream from repairing a bad seam toward Google/Meta-style
source selection: prefer anchors whose source-label boundaries and LiDAR support
make a faithful panorama plausible before any constrained DiT360 sky completion.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_a1_streetview_pipeline as a1


ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
CLEAN_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/bosch_clean_subset")
OUT_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db31_multilog_candidate_scan")
H, W = 1024, 2048
BMW_PREFIX = "02a00399"


def label(im: np.ndarray, text: str, h: int = 32) -> np.ndarray:
    bar = np.zeros((h, im.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.54, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, im])


def fit_panel(im: np.ndarray, w: int, h: int) -> np.ndarray:
    im = np.clip(im, 0, 255).astype(np.uint8)
    ih, iw = im.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    rs = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((h, w, 3), np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = rs
    return out


def overlay_mask(im: np.ndarray, mask: np.ndarray, color: tuple[int, int, int], alpha: float = 0.58) -> np.ndarray:
    out = im.copy().astype(np.float32)
    col = np.zeros_like(out)
    col[..., 0] = color[0]
    col[..., 1] = color[1]
    col[..., 2] = color[2]
    out[mask] = out[mask] * (1.0 - alpha) + col[mask] * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def crop(im: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return im[y0:y1, x0:x1]


def nearest_lidar_support(pts: np.ndarray, dilate_px: int = 3) -> np.ndarray:
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    th = np.arctan2(y, x)
    ph = np.arctan2(z, np.hypot(x, y))
    u = (((np.pi - th) / (2 * np.pi) * W - 0.5) % W)
    v = ((np.pi / 2 - ph) / np.pi * H - 0.5)
    r = np.linalg.norm(pts, axis=1)
    ui = np.round(u).astype(int) % W
    vi = np.round(v).astype(int)
    ok = (vi >= 0) & (vi < H) & (r > 1.0) & (r < 80.0)
    support = np.zeros((H, W), np.uint8)
    support[vi[ok], ui[ok]] = 1
    if dilate_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px * 2 + 1, dilate_px * 2 + 1))
        support = cv2.dilate(support, k)
    return support.astype(bool)


def horizontal_label_edges(label_map: np.ndarray, valid: np.ndarray) -> np.ndarray:
    up = label_map != np.roll(label_map, 1, axis=0)
    down = label_map != np.roll(label_map, -1, axis=0)
    return (up | down) & valid


def region_metrics(
    label_map: np.ndarray,
    valid: np.ndarray,
    h_edge: np.ndarray,
    lidar: np.ndarray,
    roi: tuple[int, int, int, int],
) -> dict:
    x0, y0, x1, y1 = roi
    roi_mask = np.zeros((H, W), bool)
    roi_mask[y0:y1, x0:x1] = True
    roi_valid = roi_mask & valid
    roi_labels = label_map[roi_valid]
    counts = {int(k): int((roi_labels == k).sum()) for k in np.unique(roi_labels)}
    label_fracs = {str(k): float(v / max(1, len(roi_labels))) for k, v in counts.items()}
    active_labels = int(sum(frac > 0.05 for frac in label_fracs.values()))
    edge_crop = h_edge[y0:y1, x0:x1]
    row_edge_frac = edge_crop.mean(axis=1) if edge_crop.size else np.zeros(1, np.float32)
    max_row_idx = int(np.argmax(row_edge_frac)) if row_edge_frac.size else 0
    max_row_frac = float(row_edge_frac[max_row_idx]) if row_edge_frac.size else 0.0
    p95_row_frac = float(np.percentile(row_edge_frac, 95)) if row_edge_frac.size else 0.0
    lidar_frac = float(lidar[roi_mask].mean())
    valid_frac = float(valid[roi_mask].mean())
    risk = float(max_row_frac * (1.0 - min(lidar_frac, 1.0)) * valid_frac)
    return {
        "roi": [int(x0), int(y0), int(x1), int(y1)],
        "valid_frac": valid_frac,
        "lidar_support_frac": lidar_frac,
        "camera_label_counts": counts,
        "camera_label_fracs": label_fracs,
        "active_label_count_gt5pct": active_labels,
        "horizontal_edge_frac": float(h_edge[roi_mask].mean()),
        "max_row_horizontal_edge_frac": max_row_frac,
        "max_row_y": int(y0 + max_row_idx),
        "p95_row_horizontal_edge_frac": p95_row_frac,
        "line_risk_score_low_is_better": risk,
    }


def resolve_logs(root: Path) -> dict[str, str]:
    out = {}
    for p in root.iterdir():
        if p.is_dir() and "-" in p.name:
            out[p.name[:8]] = p.name
    return out


def select_candidates(
    relaxed: list[dict],
    per_log_limit: int,
    bmw_limit: int,
    global_limit: int,
) -> list[dict]:
    by_log: dict[str, list[dict]] = defaultdict(list)
    for row in relaxed:
        by_log[str(row["log_id"])].append(row)

    selected: list[dict] = []
    for log_id in sorted(by_log):
        rows = sorted(by_log[log_id], key=lambda r: (int(r["score"]), int(r["anchor_index"])))
        limit = bmw_limit if log_id == BMW_PREFIX else per_log_limit
        selected.extend(rows[:limit])

    non_bmw = [r for r in selected if r["log_id"] != BMW_PREFIX]
    bmw = [r for r in selected if r["log_id"] == BMW_PREFIX]
    if len(non_bmw) + len(bmw) > global_limit:
        bmw = bmw[: max(0, global_limit - len(non_bmw))]
    return non_bmw + bmw


def scan_one(log_uuid: str, cand: dict, roi: tuple[int, int, int, int], out_dir: Path) -> tuple[dict, np.ndarray, np.ndarray]:
    anchor = int(cand["anchor_index"])
    loader = a1.AV2RingLoader(ROOT / log_uuid)
    timestamps = loader.anchor_timestamps_ns()
    ts = timestamps[anchor]
    frame = loader.load_synced_frame(ts)
    pts, _labels, _dms = a1.load_lidar_feather(loader.log_dir, ts, max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)

    ground, facades = a1.fit_planes_p3(pts)
    obj_mask = a1.off_plane_object_erp(pts, ground, facades, (H, W))

    slabs, weights = [], []
    for cam in a1.RING_CAMS_7:
        cb = frame.calibrations[cam]
        slab, _alpha, weight = a1.render_camera_to_erp(
            frame.images[cam],
            cb.K,
            cb.T_ego_cam,
            erp_hw=(H, W),
            convergence_distance_m=None,
        )
        slabs.append(slab.astype(np.uint8))
        weights.append(weight)

    pano = a1.hard_select(slabs, weights)
    w_base, routed = a1.object_coherent_weights(weights, obj_mask)
    stack_w = np.stack([w.astype(np.float32) for w in weights], 0)
    label_map = np.stack([w.astype(np.float32) for w in w_base], 0).argmax(0)
    valid = stack_w.max(0) > 0
    h_edge = horizontal_label_edges(label_map, valid)
    h_edge_wide = cv2.dilate(h_edge.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3))).astype(bool)
    lidar = nearest_lidar_support(pts)

    mid_roi = (0, 360, W, 780)
    roi_metrics = region_metrics(label_map, valid, h_edge, lidar, roi)
    mid_metrics = region_metrics(label_map, valid, h_edge, lidar, mid_roi)
    yolo_score = int(cand["score"])
    rank_score = float(
        roi_metrics["line_risk_score_low_is_better"]
        + 0.45 * mid_metrics["line_risk_score_low_is_better"]
        + 0.018 * yolo_score
        + 0.006 * max(0, roi_metrics["active_label_count_gt5pct"] - 3)
    )

    pal = np.array(
        [
            [60, 60, 210],
            [60, 210, 60],
            [210, 60, 60],
            [60, 210, 210],
            [210, 60, 210],
            [210, 210, 60],
            [210, 130, 60],
        ],
        np.uint8,
    )
    camid = pal[np.clip(label_map, 0, 6)]
    camid_vis = (0.48 * pano + 0.52 * camid).astype(np.uint8)
    edge_vis = overlay_mask(pano, h_edge_wide, (255, 0, 0), 0.70)
    lidar_vis = overlay_mask(pano, lidar, (255, 180, 0), 0.70)

    stem = f"db31_{cand['log_id']}_a{anchor:04d}"
    cv2.imwrite(str(out_dir / f"{stem}_source.jpg"), cv2.cvtColor(pano, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 94])
    cv2.imwrite(str(out_dir / f"{stem}_roi.jpg"), cv2.cvtColor(crop(pano, roi), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 94])

    full_panels = [
        label(fit_panel(cv2.cvtColor(pano, cv2.COLOR_RGB2BGR), 360, 180), f"{cand['log_id']} a{anchor} score={yolo_score}"),
        label(fit_panel(cv2.cvtColor(camid_vis, cv2.COLOR_RGB2BGR), 360, 180), f"ROI labels={roi_metrics['active_label_count_gt5pct']}"),
        label(fit_panel(cv2.cvtColor(edge_vis, cv2.COLOR_RGB2BGR), 360, 180), f"ROI risk={roi_metrics['line_risk_score_low_is_better']:.3f}"),
        label(fit_panel(cv2.cvtColor(lidar_vis, cv2.COLOR_RGB2BGR), 360, 180), f"ROI LiDAR={roi_metrics['lidar_support_frac']:.2f}"),
    ]
    roi_img = crop(pano, roi)
    roi_panels = [
        label(fit_panel(cv2.cvtColor(roi_img, cv2.COLOR_RGB2BGR), 360, 138), f"{cand['log_id']} a{anchor} ROI"),
        label(fit_panel(cv2.cvtColor(crop(camid_vis, roi), cv2.COLOR_RGB2BGR), 360, 138), f"labels={roi_metrics['active_label_count_gt5pct']}"),
        label(fit_panel(cv2.cvtColor(crop(edge_vis, roi), cv2.COLOR_RGB2BGR), 360, 138), f"h-edge={roi_metrics['max_row_horizontal_edge_frac']:.2f}"),
        label(fit_panel(cv2.cvtColor(crop(lidar_vis, roi), cv2.COLOR_RGB2BGR), 360, 138), f"LiDAR={roi_metrics['lidar_support_frac']:.2f}"),
    ]

    summary = {
        "log_id": str(cand["log_id"]),
        "uuid": log_uuid,
        "anchor": anchor,
        "timestamp_ns": int(ts),
        "yolo_edge_object_score": yolo_score,
        "routed_objects": int(routed),
        "valid_frac_full": float(valid.mean()),
        "rank_score_low_is_better": rank_score,
        "roi_metrics": roi_metrics,
        "midband_metrics": mid_metrics,
        "source_image": str(out_dir / f"{stem}_source.jpg"),
        "roi_image": str(out_dir / f"{stem}_roi.jpg"),
        "note": "ranking aid only; exact seamroute plus vision decides",
    }
    return summary, np.hstack(full_panels), np.hstack(roi_panels)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--relaxed-json", default=str(CLEAN_ROOT / "relaxed_clean_anchors.json"))
    ap.add_argument("--out-dir", default=str(OUT_ROOT))
    ap.add_argument("--roi", default="850,420,1650,720")
    ap.add_argument("--per-log-limit", type=int, default=8)
    ap.add_argument("--bmw-limit", type=int, default=12)
    ap.add_argument("--global-limit", type=int, default=32)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    relaxed = json.loads(Path(args.relaxed_json).read_text(encoding="utf-8"))
    uuid_map = resolve_logs(ROOT)
    candidates = select_candidates(relaxed, args.per_log_limit, args.bmw_limit, args.global_limit)
    roi = tuple(int(v) for v in args.roi.split(","))

    summaries = []
    full_rows = []
    roi_rows = []
    for cand in candidates:
        prefix = str(cand["log_id"])
        if prefix not in uuid_map:
            print(f"skip {prefix}: no uuid dir", flush=True)
            continue
        summary, full_row, roi_row = scan_one(uuid_map[prefix], cand, roi, out_dir)
        summaries.append(summary)
        full_rows.append(full_row)
        roi_rows.append(roi_row)
        print(
            f"{summary['log_id']} a{summary['anchor']} rank={summary['rank_score_low_is_better']:.4f} "
            f"roi={summary['roi_metrics']['line_risk_score_low_is_better']:.4f} "
            f"mid={summary['midband_metrics']['line_risk_score_low_is_better']:.4f}",
            flush=True,
        )

    ranked = sorted(summaries, key=lambda s: (s["rank_score_low_is_better"], s["yolo_edge_object_score"], s["anchor"]))
    if full_rows:
        cv2.imwrite(str(out_dir / "db31_full_montage.jpg"), np.vstack(full_rows), [cv2.IMWRITE_JPEG_QUALITY, 92])
    if roi_rows:
        cv2.imwrite(str(out_dir / "db31_roi_montage.jpg"), np.vstack(roi_rows), [cv2.IMWRITE_JPEG_QUALITY, 92])

    payload = {
        "selected_count": len(summaries),
        "selection": {
            "per_log_limit": args.per_log_limit,
            "bmw_limit": args.bmw_limit,
            "global_limit": args.global_limit,
            "roi": list(roi),
        },
        "ranked_by_source_risk": [
            {
                "log_id": s["log_id"],
                "uuid": s["uuid"],
                "anchor": s["anchor"],
                "rank_score_low_is_better": s["rank_score_low_is_better"],
                "yolo_edge_object_score": s["yolo_edge_object_score"],
                "roi_line_risk": s["roi_metrics"]["line_risk_score_low_is_better"],
                "roi_lidar_support": s["roi_metrics"]["lidar_support_frac"],
                "roi_active_labels": s["roi_metrics"]["active_label_count_gt5pct"],
                "midband_line_risk": s["midband_metrics"]["line_risk_score_low_is_better"],
                "source_image": s["source_image"],
            }
            for s in ranked
        ],
        "summaries": summaries,
        "ranking_note": "candidate filter only; exact seamroute and vision review are authoritative",
    }
    (out_dir / "db31_multilog_candidate_scan_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["ranked_by_source_risk"][:8], indent=2), flush=True)
    print(out_dir / "db31_full_montage.jpg", flush=True)
    print(out_dir / "db31_roi_montage.jpg", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
