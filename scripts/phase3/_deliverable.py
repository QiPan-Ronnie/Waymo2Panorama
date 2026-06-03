"""Full codex single-source recipe + the CPU "squeeze": enlarge the clean de-double fraction.
  base    = align (flow warp-to-agree -> HARD-SELECT one source + global gain)  [no ghost]
  + pick  = reproject both cams to ego-centre at dense-LiDAR depth, PICK the higher-cos²-weight cam's
            reproj (single source, never average -> CANNOT ghost). De-doubles the near object at TRUE pos.
KEY INSIGHT: the cross-view-agree gate (<16) was needed to stop AVERAGING from ghosting. PICK never
averages, so it can fire on the FULL LiDAR-supported band (~60%), guarded only against depth-edge
kNN-bleed (codex). We A/B two pick gates so vision decides which de-doubles more, cleanly:
  pick_agree = fire where the two reprojections AGREE (resid<gate)         [tight, ~2%]
  pick_lidar = fire where LiDAR-supported + smooth depth + winner valid    [loose, up to ~60%]
Outputs L1 | align | pick_agree | pick_lidar (downscaled overview + LOSSLESS native crops).

Run (CPU): python _deliverable.py --uuid <UUID> --tag <name>  ->  results/deliverable/DLV_<tag>_*"""
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
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/deliverable"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
FAR = 1000.0
SPOTS = {"bmw": {"bmw": (1760, 330, 660, 230), "graycar": (720, 320, 610, 230), "wall": (1150, 330, 630, 230)}}
DEFAULT_SPOTS = {"a": (720, 320, 610, 230), "b": (1400, 320, 610, 230), "c": (300, 320, 610, 230)}
NAMES = ["L1", "align", "pick_agree", "pick_lidar"]


def lab(im, t, h=26):
    b = np.zeros((h, im.shape[1], 3), np.uint8); cv2.putText(b, t, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return np.vstack([b, im])


def rw(im, w=1280):
    return cv2.resize(np.clip(im, 0, 255).astype(np.uint8), (w, round(im.shape[0] * w / im.shape[1])), interpolation=cv2.INTER_AREA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID); ap.add_argument("--tag", default="bmw")
    ap.add_argument("--anchor", type=int, default=0); ap.add_argument("--band-hw", type=int, default=80)
    ap.add_argument("--max-disp", type=float, default=60.0); ap.add_argument("--fb-thresh", type=float, default=2.0)
    ap.add_argument("--gate", type=float, default=16.0); ap.add_argument("--knn-max", type=float, default=22.0)
    ap.add_argument("--depth-edge", type=float, default=0.10, help="reject pick where |grad log-depth| exceeds this (kNN-bleed/occlusion guard)")
    a = ap.parse_args(); t0 = time.time(); erp_hw = (H, W)

    loader = a1.AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[a.anchor])
    pts, _l, dms = a1.load_lidar_feather(ROOT / a.uuid, ts[a.anchor], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = a1.fit_planes_p3(pts)
    obj_mask = a1.off_plane_object_erp(pts, ground, facades, erp_hw)
    cams = {cam: frame.calibrations[cam] for cam in a1.RING_CAMS_7}
    l1_slabs, l1_w = [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        r1, _a, w1 = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=None)
        l1_slabs.append(r1); l1_w.append(w1)
    L1 = a1.hard_select(l1_slabs, l1_w)
    w_base, n_routed = a1.object_coherent_weights(l1_w, obj_mask)
    print(f"[{a.tag}] lidar={pts.shape[0]} ground={'y' if ground else 'N'} obj={obj_mask.mean()*100:.2f}% routed={n_routed}", flush=True)

    # base = align (single-source warp-to-agree -> hard_select + global gain)
    warped = a1.flow_align_chain(l1_slabs, l1_w, band_hw=a.band_hw, max_disp=a.max_disp, fb_thresh=a.fb_thresh)
    try:
        warped = a1.apply_hdr(warped, a1.compute_hdr_gains([w.astype(np.float32) for w in warped], w_base))
    except Exception as e:
        print(f"[gain] skip ({e})", flush=True)
    align = a1.hard_select(warped, w_base)

    # dense LiDAR depth across the union band
    bandU = np.zeros((H, W), bool)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        band, _s = a1.build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        bandU |= band & ov
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    th = np.arctan2(y, x); ph = np.arctan2(z, np.hypot(x, y))
    ul = (((np.pi - th) / (2 * np.pi) * W - 0.5) % W); vl = ((np.pi / 2 - ph) / np.pi * H - 0.5)
    rng = np.linalg.norm(pts, axis=1); ui = np.round(ul).astype(int) % W; vi = np.round(vl).astype(int)
    ok = (vi >= 0) & (vi < H) & (rng > 1.0) & (rng < 80.0); ui, vi, rng = ui[ok], vi[ok], rng[ok]
    rangemap = np.full((H, W), np.inf, np.float32)
    for k in range(len(ui)):
        if rng[k] < rangemap[vi[k], ui[k]]: rangemap[vi[k], ui[k]] = rng[k]
    ky, kx = np.where(np.isfinite(rangemap)); tree = cKDTree(np.stack([ky, kx], 1)); by, bx = np.where(bandU)
    dist, nn = tree.query(np.stack([by, bx], 1), k=1); dense = np.full((H, W), FAR, np.float64); nm = dist <= a.knn_max
    dense[by[nm], bx[nm]] = rangemap[ky[nn[nm]], kx[nn[nm]]]
    lidar_support = np.zeros((H, W), bool); lidar_support[by[nm], bx[nm]] = True
    # EDGE-AWARE SMOOTH the (jagged) kNN inverse-depth so reproject is clean over surface interiors:
    # normalized-convolution Gaussian on inverse depth (fills + de-jags within support). The gradient
    # guard below (on the SMOOTHED field) still rejects true object/occlusion edges (where it smears).
    idep = np.where(lidar_support, 1.0 / np.clip(dense, 1.0, FAR), 0.0).astype(np.float32)
    wsup = lidar_support.astype(np.float32)
    sig = 4.0
    idep_s = cv2.GaussianBlur(idep, (0, 0), sig) / np.maximum(cv2.GaussianBlur(wsup, (0, 0), sig), 1e-6)
    sup_s = cv2.GaussianBlur(wsup, (0, 0), sig) > 0.15
    dense_s = np.where(sup_s & (idep_s > 1e-6), 1.0 / np.maximum(idep_s, 1e-6), FAR).astype(np.float64)
    logd = np.log(np.clip(dense_s, 1.0, FAR)).astype(np.float32)
    gmag = np.hypot(cv2.Sobel(logd, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(logd, cv2.CV_32F, 0, 1, ksize=3))
    smooth_depth = (gmag < a.depth_edge) & sup_s

    # reproject TWICE: jagged dense (for the tight agree gate, unchanged baseline) + smoothed dense_s (loose)
    reproj, ralpha, reproj_s, ralpha_s = [], [], [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        s, al, _w = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=dense)
        reproj.append(s.astype(np.float32)); ralpha.append(al)
        s2, al2, _w2 = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=dense_s)
        reproj_s.append(s2.astype(np.float32)); ralpha_s.append(al2)
    win = np.stack([w.astype(np.float32) for w in l1_w], 0).argmax(0)
    reproj_win = np.take_along_axis(np.stack(reproj, 0), win[None, ..., None], axis=0)[0]
    reproj_win_s = np.take_along_axis(np.stack(reproj_s, 0), win[None, ..., None], axis=0)[0]
    ralpha_win = np.take_along_axis(np.stack([r.astype(np.uint8) for r in ralpha_s], 0), win[None, ...], axis=0)[0] > 0

    out_agree = align.astype(np.float32).copy(); out_lidar = align.astype(np.float32).copy()
    fa = np.zeros((H, W), np.float32); fl = np.zeros((H, W), np.float32)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        band, signed = a1.build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        bm = band & ov & ralpha[i] & ralpha[j]
        dd = np.clip(np.abs(signed) / a.band_hw, 0, 1); ramp = np.where(band, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
        # tight: cross-view agree
        ag = bm & (np.abs(reproj[i] - reproj[j]).mean(2) < a.gate)
        al_a = cv2.GaussianBlur((ag.astype(np.float32) * ramp), (0, 0), 2.0)[..., None]
        out_agree = out_agree * (1 - al_a) + reproj_win * al_a; fa = np.maximum(fa, ag.astype(np.float32) * ramp)
        # loose: smoothed-depth-supported + smooth-field + winner reproj valid (single-source PICK can't ghost)
        li = (band & ov & smooth_depth & ralpha_win & (dense_s < FAR))
        al_l = cv2.GaussianBlur((li.astype(np.float32) * ramp), (0, 0), 2.0)[..., None]
        out_lidar = out_lidar * (1 - al_l) + reproj_win_s * al_l; fl = np.maximum(fl, li.astype(np.float32) * ramp)
    res = {"L1": L1, "align": align, "pick_agree": np.clip(out_agree, 0, 255).astype(np.uint8), "pick_lidar": np.clip(out_lidar, 0, 255).astype(np.uint8)}
    print(f"[{a.tag}] align-chg={100*(np.abs(align.astype(np.int16)-L1.astype(np.int16)).sum(2)>3).mean():.1f}%  "
          f"pick_agree={100*(fa>0).mean():.2f}%  pick_lidar={100*(fl>0).mean():.2f}%  runtime={time.time()-t0:.0f}s", flush=True)

    cv2.imwrite(str(OUT / f"DLV_{a.tag}_overview.jpg"), cv2.cvtColor(np.vstack([lab(rw(res[m]), m) for m in NAMES]), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    spots = SPOTS.get(a.tag, DEFAULT_SPOTS)
    for name, (u, v0, v1, hw) in spots.items():
        roll = W // 2 - u; cc = W // 2
        row = [lab(cv2.cvtColor(np.roll(res[m], roll, 1)[v0:v1, cc - hw:cc + hw], cv2.COLOR_RGB2BGR), m) for m in NAMES]
        cv2.imwrite(str(OUT / f"DLV_{a.tag}_{name}.png"), np.hstack(row))
    print(f"[saved] {OUT}/DLV_{a.tag}_overview.jpg + {list(spots)} (lossless 4-up)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
