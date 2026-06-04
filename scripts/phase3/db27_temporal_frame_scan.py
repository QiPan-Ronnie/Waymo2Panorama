"""DB-27: temporal/frame-selection scan for the long horizontal seam risk.

CPU-only diagnostic. It does not edit the panorama.

For nearby anchors in one AV2 log, render lightweight L1/source-label evidence
around the user-marked long-line ROI. The goal is to decide whether frame
selection/filtering is a safer route than repairing a bad anchor.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_a1_streetview_pipeline as a1


ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db27_temporal_frame_scan")
H, W = 1024, 2048


def parse_ints(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


def label(im: np.ndarray, text: str, h: int = 32) -> np.ndarray:
    bar = np.zeros((h, im.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, im])


def fit_panel(im: np.ndarray, w: int, h: int) -> np.ndarray:
    im = np.clip(im, 0, 255).astype(np.uint8)
    ih, iw = im.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    rs = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((h, w, 3), np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = rs
    return out


def overlay_mask(im: np.ndarray, mask: np.ndarray, color: tuple[int, int, int], alpha: float = 0.55) -> np.ndarray:
    out = im.copy().astype(np.float32)
    col = np.zeros_like(out)
    col[..., 0] = color[0]
    col[..., 1] = color[1]
    col[..., 2] = color[2]
    out[mask] = out[mask] * (1.0 - alpha) + col[mask] * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


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


def crop(im: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return im[y0:y1, x0:x1]


def scan_anchor(loader: a1.AV2RingLoader, timestamps: list[int], anchor: int, roi: tuple[int, int, int, int]) -> tuple[dict, list[np.ndarray]]:
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
    lidar_support = nearest_lidar_support(pts)

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
    near_ground_crop = np.arange(y0, y1)[:, None] > H * 0.52
    lidar_frac = float(lidar_support[roi_mask].mean())
    valid_frac = float(valid[roi_mask].mean())
    line_risk = float(max_row_frac * (1.0 - min(lidar_frac, 1.0)) * valid_frac)

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
    roi_img = crop(pano, roi)
    camid = pal[np.clip(label_map, 0, 6)]
    camid_crop = (0.48 * roi_img + 0.52 * crop(camid, roi)).astype(np.uint8)
    edge_vis = overlay_mask(roi_img, crop(h_edge_wide, roi), (255, 0, 0), 0.70)
    lidar_vis = overlay_mask(roi_img, crop(lidar_support, roi), (255, 180, 0), 0.70)

    stem = f"db27_anchor_{anchor:04d}"
    summary = {
        "anchor": int(anchor),
        "timestamp_ns": int(ts),
        "roi": [int(x0), int(y0), int(x1), int(y1)],
        "routed_objects": int(routed),
        "valid_frac": valid_frac,
        "lidar_support_frac": lidar_frac,
        "near_ground_frac": float(near_ground_crop.mean()),
        "camera_label_counts": counts,
        "camera_label_fracs": label_fracs,
        "active_label_count_gt5pct": active_labels,
        "horizontal_edge_frac": float(h_edge[roi_mask].mean()),
        "max_row_horizontal_edge_frac": max_row_frac,
        "max_row_y": int(y0 + max_row_idx),
        "p95_row_horizontal_edge_frac": p95_row_frac,
        "line_risk_score_low_is_better": line_risk,
        "note": "ranking aid only; vision review decides",
    }
    panels = [
        label(fit_panel(cv2.cvtColor(roi_img, cv2.COLOR_RGB2BGR), 360, 138), f"a{anchor} ROI risk={line_risk:.3f}"),
        label(fit_panel(cv2.cvtColor(camid_crop, cv2.COLOR_RGB2BGR), 360, 138), f"camera ids active={active_labels}"),
        label(fit_panel(cv2.cvtColor(edge_vis, cv2.COLOR_RGB2BGR), 360, 138), f"h label-edge max={max_row_frac:.2f}"),
        label(fit_panel(cv2.cvtColor(lidar_vis, cv2.COLOR_RGB2BGR), 360, 138), f"LiDAR support={lidar_frac:.2f}"),
    ]
    debug_images = [roi_img, camid_crop, edge_vis, lidar_vis]
    summary["_stem"] = stem
    return summary, panels, debug_images


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID)
    ap.add_argument("--anchors", default="0,5,10,15,20,30,40")
    ap.add_argument("--roi", default="850,420,1650,720", help="x0,y0,x1,y1 in 1024x2048 ERP")
    ap.add_argument("--out-dir", default=str(OUT_ROOT))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    anchors = parse_ints(args.anchors)
    roi = tuple(int(v) for v in args.roi.split(","))

    loader = a1.AV2RingLoader(ROOT / args.uuid)
    timestamps = loader.anchor_timestamps_ns()
    anchors = [a for a in anchors if 0 <= a < len(timestamps)]
    if not anchors:
        raise ValueError("no valid anchors requested")

    all_rows = []
    summaries = []
    for anchor in anchors:
        summary, panels, debug_images = scan_anchor(loader, timestamps, anchor, roi)
        stem = str(summary.pop("_stem"))
        summaries.append(summary)
        all_rows.append(np.hstack(panels))
        for suffix, im in zip(["roi", "camid", "h_edge", "lidar"], debug_images):
            cv2.imwrite(
                str(out_dir / f"{stem}_{suffix}.jpg"),
                cv2.cvtColor(im, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 94],
            )

    montage = np.vstack(all_rows)
    cv2.imwrite(str(out_dir / "db27_temporal_frame_scan_montage.jpg"), montage, [cv2.IMWRITE_JPEG_QUALITY, 92])

    ranked = sorted(summaries, key=lambda s: s["line_risk_score_low_is_better"])
    payload = {
        "uuid": args.uuid,
        "anchors": anchors,
        "roi": list(roi),
        "ranking_note": "low line_risk_score is only a candidate filter; visual review is authoritative",
        "ranked_by_line_risk": [
            {
                "anchor": s["anchor"],
                "line_risk_score_low_is_better": s["line_risk_score_low_is_better"],
                "max_row_horizontal_edge_frac": s["max_row_horizontal_edge_frac"],
                "lidar_support_frac": s["lidar_support_frac"],
                "active_label_count_gt5pct": s["active_label_count_gt5pct"],
            }
            for s in ranked
        ],
        "summaries": summaries,
    }
    (out_dir / "db27_temporal_frame_scan_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["ranked_by_line_risk"], indent=2), flush=True)
    print(out_dir / "db27_temporal_frame_scan_montage.jpg", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
