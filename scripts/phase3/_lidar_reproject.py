"""★ LiDAR-depth reproject + CROSS-VIEW GATE (the DB-13 positive, generalized to full pano + 3 anchors).
The faithful op (reproject BOTH cams to the true ego-centre at an ACCURATE dense depth) singles the
near object — IF the depth is right. Ingredient E2 lacked = a CONFIDENCE GATE: densify sparse LiDAR
depth across the seam band (kNN), reproject both cams at it, and KEEP the de-doubled colour ONLY where
the two reprojections AGREE (cross-view residual < thresh = depth verified); elsewhere fall back to L1
(no smear). Source-faithful (real LiDAR + real camera pixels, NO generation). Far field byte-exact L1.

Run: python _lidar_reproject.py --uuid <UUID> --tag <name>. Vision-judge: does it single the near
objects + merge the seams cleanly, beating L1, WITHOUT smearing where depth is wrong?"""
from __future__ import annotations
import sys, time, argparse
from pathlib import Path
import cv2
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")

from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band
from run_a0_plane_dibr_probe import load_lidar_feather

ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/killtest"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
FAR = 1000.0


def lab(im, t):
    b = np.zeros((22, im.shape[1], 3), np.uint8); cv2.putText(b, t, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return np.vstack([b, im])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default="02a00399-3857-444e-8db3-a8f58489c394")
    ap.add_argument("--tag", default="bmw")
    ap.add_argument("--bhw", type=int, default=90)
    ap.add_argument("--gate", type=float, default=16.0)          # cross-view RGB residual gate (0..255)
    ap.add_argument("--knn-max", type=float, default=22.0)       # max px dist to a LiDAR pt to trust the dense depth
    a = ap.parse_args()
    t0 = time.time()
    loader = AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    cams = {cam: frame.calibrations[cam] for cam in RING_CAMS_7}
    l1s, l1w = [], []
    for cam in RING_CAMS_7:
        cb = cams[cam]; r, _x, wt = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        l1s.append(r); l1w.append(wt)
    L1 = hard_select(l1s, l1w)

    # union seam band over all ring pairs (where we attempt the reproject)
    bandU = np.zeros((H, W), bool)
    feU = np.zeros((H, W), np.float32)
    for (i, j) in RING_PAIRS:
        ov = (l1w[i] > 1e-6) & (l1w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        band, signed = build_voronoi_seam_band(l1w[i].astype(np.float32), l1w[j].astype(np.float32), band_half_width=a.bhw, threshold=1e-6)
        bm = band & ov
        bandU |= bm
        dd = np.clip(np.abs(signed) / a.bhw, 0, 1); ramp = np.where(band, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
        feU = np.maximum(feU, ramp * bm)

    # LiDAR -> per-pixel ego range (z-buffer nearest), then DENSIFY across the band (kNN nearest)
    pts, _l, ldelta = load_lidar_feather(ROOT / a.uuid, ts[0], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3]
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    th = np.arctan2(y, x); ph = np.arctan2(z, np.hypot(x, y))
    ul = (((np.pi - th) / (2 * np.pi) * W - 0.5) % W); vl = ((np.pi / 2 - ph) / np.pi * H - 0.5)
    rng = np.linalg.norm(pts, axis=1)
    ui = np.round(ul).astype(int) % W; vi = np.round(vl).astype(int)
    ok = (vi >= 0) & (vi < H) & (rng > 1.0) & (rng < 80.0)
    ui, vi, rng = ui[ok], vi[ok], rng[ok]
    rangemap = np.full((H, W), np.inf, np.float32)
    for k in range(len(ui)):  # z-buffer nearest per pixel
        if rng[k] < rangemap[vi[k], ui[k]]: rangemap[vi[k], ui[k]] = rng[k]
    known = np.isfinite(rangemap) & (rangemap < np.inf)
    ky, kx = np.where(known)
    tree = cKDTree(np.stack([ky, kx], 1))
    by, bx = np.where(bandU)
    dist, nn = tree.query(np.stack([by, bx], 1), k=1)
    dense_depth = np.full((H, W), FAR, np.float64)               # FAR (=rotation-only=L1) outside support
    near = dist <= a.knn_max
    dense_depth[by[near], bx[near]] = rangemap[ky[nn[near]], kx[nn[near]]]
    print(f"[{a.tag}] lidar N={len(pts)} dt={ldelta:.1f}ms; band px={int(bandU.sum())}, LiDAR-supported(in {a.knn_max:.0f}px)={100*near.mean():.0f}%", flush=True)

    # reproject EVERY cam to ego-centre at the dense depth (FAR outside band -> ~L1 there)
    reproj, ralpha = [], []
    for cam in RING_CAMS_7:
        cb = cams[cam]
        s, al, _w = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=dense_depth)
        reproj.append(s.astype(np.float32)); ralpha.append(al)

    # per seam: keep the de-doubled (averaged) reproject ONLY where the two reprojections AGREE
    out = L1.astype(np.float32).copy()
    fired = np.zeros((H, W), np.float32)
    for (i, j) in RING_PAIRS:
        ov = (l1w[i] > 1e-6) & (l1w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        band, signed = build_voronoi_seam_band(l1w[i].astype(np.float32), l1w[j].astype(np.float32), band_half_width=a.bhw, threshold=1e-6)
        bm = band & ov & ralpha[i] & ralpha[j]
        resid = np.abs(reproj[i] - reproj[j]).mean(2)
        agree = bm & (resid < a.gate)
        dd = np.clip(np.abs(signed) / a.bhw, 0, 1); ramp = np.where(band, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
        al = cv2.GaussianBlur((agree.astype(np.float32) * ramp), (0, 0), 2.0)[..., None]
        avg = 0.5 * (reproj[i] + reproj[j])
        out = out * (1 - al) + avg * al
        fired = np.maximum(fired, agree.astype(np.float32) * ramp)
    out = np.clip(out, 0, 255).astype(np.uint8)
    print(f"[{a.tag}] reproject-gate FIRED on {100*(fired>0).mean():.2f}% of pano (de-doubled); rest = L1. runtime {time.time()-t0:.0f}s", flush=True)

    def rw(im, w=1500): return cv2.resize(im, (w, round(im.shape[0]*w/im.shape[1])), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / f"LR_{a.tag}_full.jpg"), cv2.cvtColor(np.vstack([lab(rw(L1), "L1"), lab(rw(out), "LiDAR-depth reproject + cross-view gate")]), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    spots = {"bmw": (1760, 360, 600), "graycar": (720, 330, 560), "wall": (1150, 330, 560)} if a.tag == "bmw" else {"a": (720, 330, 560), "b": (1400, 330, 560), "c": (300, 330, 560)}
    rows = []
    for tag, (u, v0, v1) in spots.items():
        roll = W // 2 - u; cc = W // 2
        def crp(im): return cv2.cvtColor(np.roll(im, roll, 1)[v0:v1, cc-180:cc+180], cv2.COLOR_RGB2BGR)
        rows.append(np.hstack([lab(crp(L1), "L1 "+tag), lab(crp(out), "LiDAR-reproj-gate")]))
    g = np.vstack(rows); g = cv2.resize(g, (g.shape[1]*2, g.shape[0]*2), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT / f"LR_{a.tag}_zoom.jpg"), g, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"[saved] LR_{a.tag}_full.jpg + LR_{a.tag}_zoom.jpg", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
