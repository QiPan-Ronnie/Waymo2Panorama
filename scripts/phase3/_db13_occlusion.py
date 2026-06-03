"""DB-13 decisive test: is the grazing curb OCCLUSION-limited (two cams see different geometry -> no
depth fixes it) or DEPTH-limited (same surface, just misregistered -> better depth would fix it)?
Render the two cameras that meet at the curb, reproject BOTH to the ego centre at the dense-LiDAR depth,
and show them SEPARATELY + their abs residual. If the two reprojections show DIFFERENT curb geometry
(high residual, different structure) -> occlusion floor (GPU/learned depth won't help). If they show the
SAME curb just offset -> depth-limited (a dedicated near-ground depth train could help)."""
from __future__ import annotations
import sys, argparse
from pathlib import Path
import numpy as np
import cv2
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
for p in [str(HERE.parent.parent / "code"), str(HERE)]:
    sys.path.insert(0, p)
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.hard_hdr_of import hard_select, RING_PAIRS
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band
from run_a0_plane_dibr_probe import load_lidar_feather

ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db13"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
FAR = 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default="02a00399-3857-444e-8db3-a8f58489c394")
    ap.add_argument("--tag", default="bmw"); ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--u", type=int, default=1085); ap.add_argument("--v0", type=int, default=540)
    ap.add_argument("--v1", type=int, default=730); ap.add_argument("--hw", type=int, default=220)
    a = ap.parse_args(); erp_hw = (H, W)
    loader = AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns()[a.anchor]
    frame = loader.load_synced_frame(ts); cams = {c: frame.calibrations[c] for c in RING_CAMS_7}
    l1s, l1w = [], []
    for c in RING_CAMS_7:
        cb = cams[c]; r, _x, wt = render_camera_to_erp(frame.images[c], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=None)
        l1s.append(r); l1w.append(wt)
    L1 = hard_select(l1s, l1w)

    # the two cams covering the curb region (highest summed weight in the crop window)
    cu = a.u
    win = slice(a.v0, a.v1), slice((cu - a.hw) % W, (cu + a.hw) % W)
    sums = [float(l1w[k][win].sum()) for k in range(len(RING_CAMS_7))]
    order = np.argsort(sums)[::-1]; i, j = int(order[0]), int(order[1])
    print(f"[{a.tag}] curb cams: {RING_CAMS_7[i]} + {RING_CAMS_7[j]}", flush=True)

    # dense LiDAR depth across the i-j seam band
    band, _s = build_voronoi_seam_band(l1w[i].astype(np.float32), l1w[j].astype(np.float32), band_half_width=120, threshold=1e-6)
    bm = band & (l1w[i] > 1e-6) & (l1w[j] > 1e-6)
    pts, _l, _d = load_lidar_feather(ROOT / a.uuid, ts, max_delta_ms=75.0); pts = np.asarray(pts)[:, :3].astype(np.float64)
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]; th = np.arctan2(y, x); ph = np.arctan2(z, np.hypot(x, y))
    ul = np.round((np.pi - th) / (2 * np.pi) * W - 0.5).astype(int) % W; vl = np.round((np.pi / 2 - ph) / np.pi * H - 0.5).astype(int)
    rng = np.linalg.norm(pts, axis=1); ok = (vl >= 0) & (vl < H) & (rng > 1) & (rng < 80)
    ul, vl, rng = ul[ok], vl[ok], rng[ok]; rmap = np.full((H, W), np.inf, np.float32)
    for k in range(len(ul)):
        if rng[k] < rmap[vl[k], ul[k]]: rmap[vl[k], ul[k]] = rng[k]
    ky, kx = np.where(np.isfinite(rmap)); by, bx = np.where(bm)
    tree = cKDTree(np.stack([ky, kx], 1)); dist, nn = tree.query(np.stack([by, bx], 1), k=1)
    dense = np.full((H, W), FAR, np.float64); g = dist <= 30
    dense[by[g], bx[g]] = rmap[ky[nn[g]], kx[nn[g]]]

    # reproject BOTH cams to ego at the dense depth
    ri, _ai, _ = render_camera_to_erp(frame.images[RING_CAMS_7[i]], cams[RING_CAMS_7[i]].K, cams[RING_CAMS_7[i]].T_ego_cam, erp_hw=erp_hw, convergence_distance_m=dense)
    rj, _aj, _ = render_camera_to_erp(frame.images[RING_CAMS_7[j]], cams[RING_CAMS_7[j]].K, cams[RING_CAMS_7[j]].T_ego_cam, erp_hw=erp_hw, convergence_distance_m=dense)
    resid = np.abs(ri.astype(np.float32) - rj.astype(np.float32)).mean(2)

    roll = W // 2 - cu; cc = W // 2
    def crp(im): return np.roll(im, roll, 1)[a.v0:a.v1, cc - a.hw:cc + a.hw]
    def lab(im, t):
        b = np.zeros((24, im.shape[1], 3), np.uint8); cv2.putText(b, t, (5, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1); return np.vstack([b, im])
    rc = cv2.applyColorMap((np.clip(crp(resid), 0, 60) / 60 * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    row = np.hstack([
        lab(cv2.cvtColor(crp(L1), cv2.COLOR_RGB2BGR), "L1"),
        lab(cv2.cvtColor(crp(ri), cv2.COLOR_RGB2BGR), f"cam {RING_CAMS_7[i]} reproj"),
        lab(cv2.cvtColor(crp(rj), cv2.COLOR_RGB2BGR), f"cam {RING_CAMS_7[j]} reproj"),
        lab(rc, "cross-view residual (red=disagree)")])
    row = cv2.resize(row, (row.shape[1] * 2, row.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT / f"DB13_{a.tag}_occlusion.png"), row)
    bandresid = resid[bm]
    print(f"[{a.tag}] curb-band cross-view residual: median={np.median(bandresid):.1f} p75={np.percentile(bandresid,75):.1f} frac>20={100*(bandresid>20).mean():.0f}%", flush=True)
    print(f"[saved] DB13_{a.tag}_occlusion.png", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
