"""DB-25: raw-camera/source-evidence pack for the long horizontal seam line.

CPU-only diagnostic. It does not edit the panorama.

Outputs:
  - db25_longline_evidence_montage.jpg
  - db25_longline_summary.json
  - small panel/crop JPGs
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
OUT_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db25_longline_evidence")
H, W = 1024, 2048


def label(im: np.ndarray, text: str, h: int = 34) -> np.ndarray:
    bar = np.zeros((h, im.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
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


def overlay_mask(im: np.ndarray, mask: np.ndarray, color: tuple[int, int, int], alpha: float = 0.45) -> np.ndarray:
    out = im.copy().astype(np.float32)
    col = np.zeros_like(out)
    col[..., 0] = color[0]
    col[..., 1] = color[1]
    col[..., 2] = color[2]
    out[mask] = out[mask] * (1.0 - alpha) + col[mask] * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def nearest_lidar_map(pts: np.ndarray) -> np.ndarray:
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    th = np.arctan2(y, x)
    ph = np.arctan2(z, np.hypot(x, y))
    u = (((np.pi - th) / (2 * np.pi) * W - 0.5) % W)
    v = ((np.pi / 2 - ph) / np.pi * H - 0.5)
    r = np.linalg.norm(pts, axis=1)
    ui = np.round(u).astype(int) % W
    vi = np.round(v).astype(int)
    ok = (vi >= 0) & (vi < H) & (r > 1.0) & (r < 80.0)
    rng = np.full((H, W), np.inf, np.float32)
    for uu, vv, rr in zip(ui[ok], vi[ok], r[ok]):
        if rr < rng[vv, uu]:
            rng[vv, uu] = rr
    return rng


def flow_pair_stats(si: np.ndarray, sj: np.ndarray, wi: np.ndarray, wj: np.ndarray, roi_mask: np.ndarray) -> tuple[dict, np.ndarray]:
    overlap = (wi > 1e-6) & (wj > 1e-6) & roi_mask
    reliable = np.zeros((H, W), bool)
    if int(overlap.sum()) < 200:
        return {
            "overlap_px": int(overlap.sum()),
            "fb_reliable_px": 0,
            "fb_reliable_frac": 0.0,
            "mean_rgb_residual": None,
        }, reliable
    dis = a1._make_dis()
    gi = cv2.cvtColor(si.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gj = cv2.cvtColor(sj.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    kov = cv2.dilate(((wi > 1e-6) & (wj > 1e-6)).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(bool)
    gi = np.where(kov, gi, 0).astype(np.uint8)
    gj = np.where(kov, gj, 0).astype(np.uint8)
    fij = dis.calc(gi, gj, None)
    fji = dis.calc(gj, gi, None)
    np.clip(fij, -60, 60, out=fij)
    np.clip(fji, -60, 60, out=fji)
    fb = a1._fb_consistency(fij, fji, 2.0)
    reliable = overlap & fb
    rgb = np.abs(si.astype(np.float32) - sj.astype(np.float32)).mean(2)
    return {
        "overlap_px": int(overlap.sum()),
        "fb_reliable_px": int(reliable.sum()),
        "fb_reliable_frac": float(reliable.sum() / max(1, overlap.sum())),
        "mean_rgb_residual": float(rgb[overlap].mean()) if overlap.any() else None,
    }, reliable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--out-dir", default=str(OUT_ROOT))
    ap.add_argument("--roi", default="850,420,1650,720", help="x0,y0,x1,y1 in 1024x2048 ERP")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = [int(v) for v in args.roi.split(",")]
    roi_mask = np.zeros((H, W), bool)
    roi_mask[y0:y1, x0:x1] = True

    loader = a1.AV2RingLoader(ROOT / args.uuid)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[args.anchor])
    pts, _labels, _dms = a1.load_lidar_feather(ROOT / args.uuid, ts[args.anchor], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = a1.fit_planes_p3(pts)
    obj_mask = a1.off_plane_object_erp(pts, ground, facades, (H, W))

    cams = {cam: frame.calibrations[cam] for cam in a1.RING_CAMS_7}
    slabs, weights = [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        slab, _alpha, weight = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        slabs.append(slab.astype(np.uint8))
        weights.append(weight)

    pano = a1.hard_select(slabs, weights)
    w_base, _nr = a1.object_coherent_weights(weights, obj_mask)
    label_map = np.stack([w.astype(np.float32) for w in w_base], 0).argmax(0)
    valid = np.stack([w.astype(np.float32) for w in weights], 0).max(0) > 0

    rows = np.arange(H)[:, None] * np.ones((1, W))
    near_ground = (rows > H * 0.52)
    rng = nearest_lidar_map(pts)
    lidar_support = np.isfinite(rng)

    roi_labels = label_map[roi_mask & valid]
    counts = {int(k): int((roi_labels == k).sum()) for k in np.unique(roi_labels)}
    top_labels = sorted(counts, key=counts.get, reverse=True)[:3]

    pair_stats = {}
    reliable_union = np.zeros((H, W), bool)
    for i, j in a1.RING_PAIRS:
        st, rel = flow_pair_stats(slabs[i], slabs[j], weights[i], weights[j], roi_mask)
        if st["overlap_px"] > 0:
            pair_stats[f"{i}-{j}"] = st
            reliable_union |= rel

    roi = pano[y0:y1, x0:x1]
    pal = np.array([[60, 60, 210], [60, 210, 60], [210, 60, 60], [60, 210, 210], [210, 60, 210], [210, 210, 60], [210, 130, 60]], np.uint8)
    camid = pal[np.clip(label_map, 0, 6)]
    camid_crop = (0.45 * roi + 0.55 * camid[y0:y1, x0:x1]).astype(np.uint8)
    near_crop = overlay_mask(roi, near_ground[y0:y1, x0:x1], (0, 255, 0), 0.5)
    lidar_crop = overlay_mask(roi, lidar_support[y0:y1, x0:x1], (255, 180, 0), 0.65)
    flow_crop = overlay_mask(roi, reliable_union[y0:y1, x0:x1], (0, 255, 255), 0.65)

    panels = [
        label(fit_panel(cv2.cvtColor(roi, cv2.COLOR_RGB2BGR), 620, 230), "current pano ROI"),
        label(fit_panel(cv2.cvtColor(camid_crop, cv2.COLOR_RGB2BGR), 620, 230), "camera-id overlay"),
        label(fit_panel(cv2.cvtColor(near_crop, cv2.COLOR_RGB2BGR), 620, 230), "near-ground green"),
        label(fit_panel(cv2.cvtColor(lidar_crop, cv2.COLOR_RGB2BGR), 620, 230), "LiDAR support orange"),
        label(fit_panel(cv2.cvtColor(flow_crop, cv2.COLOR_RGB2BGR), 620, 230), "FB-flow reliable cyan"),
    ]

    for cam in top_labels[:3]:
        slab_crop = slabs[cam][y0:y1, x0:x1]
        panels.append(label(fit_panel(cv2.cvtColor(slab_crop, cv2.COLOR_RGB2BGR), 620, 230), f"ERP slab cam {cam}"))
    for cam in top_labels[:2]:
        raw = frame.images[a1.RING_CAMS_7[cam]]
        panels.append(label(fit_panel(cv2.cvtColor(raw, cv2.COLOR_RGB2BGR), 620, 230), f"raw camera {cam}"))

    while len(panels) % 3:
        panels.append(np.zeros_like(panels[0]))
    rows_img = []
    for k in range(0, len(panels), 3):
        rows_img.append(np.hstack(panels[k:k + 3]))
    montage = np.vstack(rows_img)
    cv2.imwrite(str(out_dir / "db25_longline_evidence_montage.jpg"), montage, [cv2.IMWRITE_JPEG_QUALITY, 92])

    for name, im in [
        ("roi_current.jpg", roi),
        ("roi_camid_overlay.jpg", camid_crop),
        ("roi_nearground.jpg", near_crop),
        ("roi_lidar_support.jpg", lidar_crop),
        ("roi_flow_reliable.jpg", flow_crop),
    ]:
        cv2.imwrite(str(out_dir / name), cv2.cvtColor(im, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 94])

    best_pair = None
    if pair_stats:
        best_pair = max(pair_stats, key=lambda k: pair_stats[k]["fb_reliable_frac"])
    summary = {
        "uuid": args.uuid,
        "anchor": args.anchor,
        "roi": [x0, y0, x1, y1],
        "roi_valid_frac": float(valid[roi_mask].mean()),
        "camera_label_counts": counts,
        "top_camera_labels": top_labels,
        "near_ground_frac": float(near_ground[roi_mask].mean()),
        "lidar_support_frac": float(lidar_support[roi_mask].mean()),
        "flow_pair_stats": pair_stats,
        "best_flow_pair": best_pair,
        "best_flow_reliable_frac": pair_stats[best_pair]["fb_reliable_frac"] if best_pair else 0.0,
        "recommendation": "abstain_unless_followup_finds_stronger_raw_evidence",
    }
    (out_dir / "db25_longline_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    print(out_dir / "db25_longline_evidence_montage.jpg", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
