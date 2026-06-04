"""DB-26: source-safe low-frequency attenuation for the long horizontal seam.

CPU-only diagnostic. It does not move pixels or generate content.
It smooths only low-frequency RGB inside a narrow band around horizontal
camera-label boundaries in the user-marked ROI.
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
OUT_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db26_photometric_attenuate")
H, W = 1024, 2048


def label(im: np.ndarray, text: str, h: int = 34) -> np.ndarray:
    bar = np.zeros((h, im.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, im])


def crop(im: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return im[y0:y1, x0:x1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--input", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/seamroute/SR_bmw_bevfinal_1024x2048.png")
    ap.add_argument("--out-dir", default=str(OUT_ROOT))
    ap.add_argument("--roi", default="850,420,1650,720")
    ap.add_argument("--dilate-w", type=int, default=41)
    ap.add_argument("--dilate-h", type=int, default=17)
    ap.add_argument("--alpha", type=float, default=0.55)
    ap.add_argument("--sigma-low", type=float, default=7.0)
    ap.add_argument("--sigma-smooth", type=float, default=31.0)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    roi = tuple(int(v) for v in args.roi.split(","))
    x0, y0, x1, y1 = roi

    inp_bgr = cv2.imread(args.input, cv2.IMREAD_COLOR)
    if inp_bgr is None:
        raise FileNotFoundError(args.input)
    inp = cv2.cvtColor(inp_bgr, cv2.COLOR_BGR2RGB)

    loader = a1.AV2RingLoader(ROOT / args.uuid)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[args.anchor])
    pts, _labels, _dms = a1.load_lidar_feather(ROOT / args.uuid, ts[args.anchor], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = a1.fit_planes_p3(pts)
    obj_mask = a1.off_plane_object_erp(pts, ground, facades, (H, W))

    weights = []
    for cam in a1.RING_CAMS_7:
        cb = frame.calibrations[cam]
        _slab, _alpha, weight = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        weights.append(weight)
    w_base, _nr = a1.object_coherent_weights(weights, obj_mask)
    label_map = np.stack([w.astype(np.float32) for w in w_base], 0).argmax(0)
    valid = np.stack([w.astype(np.float32) for w in weights], 0).max(0) > 0

    roi_mask = np.zeros((H, W), bool)
    roi_mask[y0:y1, x0:x1] = True
    # Horizontal source boundary: label changes across neighboring rows.
    h_edge = ((label_map != np.roll(label_map, 1, 0)) | (label_map != np.roll(label_map, -1, 0))) & valid & roi_mask
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (args.dilate_w, args.dilate_h))
    band = cv2.dilate(h_edge.astype(np.uint8), ker).astype(bool) & roi_mask
    # Keep away from highly salient object interiors; this is color seam handling, not object editing.
    obj_d = cv2.dilate(obj_mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))).astype(bool)
    band &= ~obj_d

    dist_in = cv2.distanceTransform(band.astype(np.uint8), cv2.DIST_L2, 3)
    dist_out = cv2.distanceTransform((~band).astype(np.uint8), cv2.DIST_L2, 3)
    feather = np.clip(dist_in / np.maximum(dist_in + dist_out, 1e-6), 0, 1)
    feather = cv2.GaussianBlur(feather, (0, 0), 3.0)
    feather = np.clip(feather * args.alpha, 0, args.alpha)[..., None].astype(np.float32)

    imf = inp.astype(np.float32)
    low = cv2.GaussianBlur(imf, (0, 0), args.sigma_low)
    low_s = cv2.GaussianBlur(imf, (0, 0), args.sigma_smooth)
    high = imf - low
    out = high + low * (1.0 - feather) + low_s * feather
    out = np.where(valid[..., None], out, imf)
    out = np.clip(out, 0, 255).astype(np.uint8)

    diff = np.clip(np.abs(out.astype(np.int16) - inp.astype(np.int16)) * 5, 0, 255).astype(np.uint8)
    mask_vis = inp.copy()
    mask_vis[band] = (0.45 * mask_vis[band] + np.array([255, 0, 0]) * 0.55).astype(np.uint8)

    panels = [
        label(crop(inp, roi), "before ROI"),
        label(crop(out, roi), "after ROI"),
        label(crop(diff, roi), "abs diff x5"),
        label(crop(mask_vis, roi), "edit band red"),
    ]
    montage = np.vstack([np.hstack(panels[:2]), np.hstack(panels[2:])])

    cv2.imwrite(str(out_dir / "db26_attenuated_full.png"), cv2.cvtColor(out, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(out_dir / "db26_attenuated_roi_montage.jpg"), cv2.cvtColor(montage, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 94])
    cv2.imwrite(str(out_dir / "db26_edit_mask.png"), (band.astype(np.uint8) * 255))

    summary = {
        "uuid": args.uuid,
        "anchor": args.anchor,
        "input": args.input,
        "roi": [x0, y0, x1, y1],
        "edit_band_frac_pano": float(band.mean()),
        "edit_band_frac_roi": float(band[roi_mask].mean()),
        "changed_mean_abs_rgb_in_band": float(np.abs(out.astype(np.int16) - inp.astype(np.int16))[band].mean()) if band.any() else 0.0,
        "alpha": args.alpha,
        "sigma_low": args.sigma_low,
        "sigma_smooth": args.sigma_smooth,
        "note": "diagnostic candidate only; no geometry warp and no generation",
    }
    (out_dir / "db26_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    print(out_dir / "db26_attenuated_roi_montage.jpg", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
