"""Render the CURRENT BEST result for vision review: deliverable = align (single-source warp-to-agree
+ hard_select + global gain) + pick (depth-verified single-source de-double, cross-view-agree gate).
Saves: full-pano 3-row compare L1 | view_none | deliverable, and lossless seam zooms at the worst seams.
Run (CPU): python _show_best.py --uuid <UUID> --tag <name>  ->  results/best/"""
from __future__ import annotations
import sys, time, argparse
from pathlib import Path
import cv2
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
import run_a1_streetview_pipeline as a1

ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/best"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
FAR = 1000.0
SPOTS = {"bmw": {"bmw": (1760, 320, 670, 250), "graycar": (720, 310, 620, 250), "wall": (1150, 320, 640, 250)}}
DEFAULT_SPOTS = {"a": (720, 310, 620, 250), "b": (1400, 310, 620, 250), "c": (300, 310, 620, 250)}


def lab(im, t, h=30):
    b = np.zeros((h, im.shape[1], 3), np.uint8); cv2.putText(b, t, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return np.vstack([b, im])


def rw(im, w):
    return cv2.resize(np.clip(im, 0, 255).astype(np.uint8), (w, round(im.shape[0] * w / im.shape[1])), interpolation=cv2.INTER_AREA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID); ap.add_argument("--tag", default="bmw")
    ap.add_argument("--anchor", type=int, default=0); ap.add_argument("--band-hw", type=int, default=80)
    ap.add_argument("--max-disp", type=float, default=60.0); ap.add_argument("--fb-thresh", type=float, default=2.0)
    ap.add_argument("--gate", type=float, default=16.0); ap.add_argument("--knn-max", type=float, default=22.0)
    a = ap.parse_args(); t0 = time.time(); erp_hw = (H, W)

    loader = a1.AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[a.anchor])
    pts, _l, dms = a1.load_lidar_feather(ROOT / a.uuid, ts[a.anchor], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = a1.fit_planes_p3(pts); obj_mask = a1.off_plane_object_erp(pts, ground, facades, erp_hw)
    cams = {cam: frame.calibrations[cam] for cam in a1.RING_CAMS_7}
    l1_slabs, l1_w = [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        r1, _a, w1 = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=None)
        l1_slabs.append(r1); l1_w.append(w1)
    L1 = a1.hard_select(l1_slabs, l1_w)
    w_base, nr = a1.object_coherent_weights(l1_w, obj_mask)

    # view_none (blend, the user's current pref)
    view, vt, _ = a1.view_interp_panorama(l1_slabs, l1_slabs, l1_w, obj_mask, L1, band_half_width=a.band_hw,
                                          max_disp=a.max_disp, fb_thresh=a.fb_thresh, protect_obj=False, struct_thresh=0.0)
    # align (single-source) + gain
    warped = a1.flow_align_chain(l1_slabs, l1_w, band_hw=a.band_hw, max_disp=a.max_disp, fb_thresh=a.fb_thresh)
    try: warped = a1.apply_hdr(warped, a1.compute_hdr_gains([w.astype(np.float32) for w in warped], w_base))
    except Exception: pass
    align = a1.hard_select(warped, w_base)

    # pick de-double (depth-verified, single source) -> deliverable = align + pick
    bandU = np.zeros((H, W), bool)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        b, _s = a1.build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        bandU |= b & ov
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]; th = np.arctan2(y, x); ph = np.arctan2(z, np.hypot(x, y))
    ul = (((np.pi - th) / (2 * np.pi) * W - 0.5) % W); vl = ((np.pi / 2 - ph) / np.pi * H - 0.5); rng = np.linalg.norm(pts, axis=1)
    ui = np.round(ul).astype(int) % W; vi = np.round(vl).astype(int); ok = (vi >= 0) & (vi < H) & (rng > 1.0) & (rng < 80.0)
    ui, vi, rng = ui[ok], vi[ok], rng[ok]; rangemap = np.full((H, W), np.inf, np.float32)
    for k in range(len(ui)):
        if rng[k] < rangemap[vi[k], ui[k]]: rangemap[vi[k], ui[k]] = rng[k]
    ky, kx = np.where(np.isfinite(rangemap)); tree = cKDTree(np.stack([ky, kx], 1)); by, bx = np.where(bandU)
    dist, nn = tree.query(np.stack([by, bx], 1), k=1); dense = np.full((H, W), FAR, np.float64); nmk = dist <= a.knn_max
    dense[by[nmk], bx[nmk]] = rangemap[ky[nn[nmk]], kx[nn[nmk]]]
    reproj, ralpha = [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]; s, al, _w = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=dense)
        reproj.append(s.astype(np.float32)); ralpha.append(al)
    win = np.stack([w.astype(np.float32) for w in l1_w], 0).argmax(0)
    rwin = np.take_along_axis(np.stack(reproj, 0), win[None, ..., None], axis=0)[0]
    deliv = align.astype(np.float32).copy(); fired = np.zeros((H, W), np.float32)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        b, signed = a1.build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        agree = b & ov & ralpha[i] & ralpha[j] & (np.abs(reproj[i] - reproj[j]).mean(2) < a.gate)
        dd = np.clip(np.abs(signed) / a.band_hw, 0, 1); ramp = np.where(b, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
        al = cv2.GaussianBlur((agree.astype(np.float32) * ramp), (0, 0), 2.0)[..., None]
        deliv = deliv * (1 - al) + rwin * al; fired = np.maximum(fired, agree.astype(np.float32) * ramp)
    deliverable = np.clip(deliv, 0, 255).astype(np.uint8)
    print(f"[{a.tag}] view-fired={100*(vt>0).mean():.1f}% align-chg={100*(np.abs(align.astype(np.int16)-L1.astype(np.int16)).sum(2)>3).mean():.1f}% pick-fired={100*(fired>0).mean():.2f}% rt={time.time()-t0:.0f}s", flush=True)

    # full-pano 3-row compare
    cv2.imwrite(str(OUT / f"BEST_{a.tag}_compare.jpg"), cv2.cvtColor(np.vstack([
        lab(rw(L1, 1900), "L1 hard_select (baseline)"),
        lab(rw(view, 1900), "view_none (blend - has ghost)"),
        lab(rw(deliverable, 1900), "DELIVERABLE = align + pick (single-source, no ghost)")]), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 93])
    # standalone best
    cv2.imwrite(str(OUT / f"BEST_{a.tag}_pano.jpg"), cv2.cvtColor(rw(deliverable, 2048), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    # lossless seam zooms L1 | view | deliverable
    spots = SPOTS.get(a.tag, DEFAULT_SPOTS)
    for name, (u, v0, v1, hw) in spots.items():
        roll = W // 2 - u; cc = W // 2
        row = [lab(cv2.cvtColor(np.roll(im, roll, 1)[v0:v1, cc - hw:cc + hw], cv2.COLOR_RGB2BGR), t)
               for im, t in [(L1, "L1"), (view, "view_none"), (deliverable, "deliverable")]]
        cv2.imwrite(str(OUT / f"BEST_{a.tag}_{name}.png"), np.hstack(row))
    print(f"[saved] {OUT}/BEST_{a.tag}_{{compare.jpg, pano.jpg, {list(spots)}}}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
