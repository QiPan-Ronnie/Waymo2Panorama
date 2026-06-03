"""★ GHOST kill-test: avg vs PICK vs single-source-align, vision-judged side-by-side (CPU).

The user sees GHOSTING (虚影) in both view-interp (alpha-blend two warped copies) and gated-LiDAR
(0.5·(reproj_i+reproj_j) — average two reprojections). Hypothesis: the ghost is caused by AVERAGING two
copies that aren't pixel-perfectly aligned; "never average" (single-source) removes it. This renders all
five on ONE anchor so we can vision-judge ghost-vs-sharp directly:
  L1        : rotation-only argmax (one cam/pixel; near-field DOUBLED across seam, but never ghosts)
  view      : Surround360 flow blend novel = warp_i·(1-shift)+warp_j·shift  (the user's view_none; MIXES)
  align     : warp losing slab to AGREE then HARD-SELECT one source + global-gain colour (single-source)
  lidar_avg : reproject both to ego-centre at dense-LiDAR depth, gate by cross-view agree, AVERAGE (current)
  lidar_pick: same gate, but PICK the higher-cos²-weight cam's reprojection (single-source, can't ghost)

If align + lidar_pick are ghost-free AND de-double better than view → the user was right (it was an
implementation bug: averaging). If they instead show a hard STEP/tear where the two views genuinely
disagree → that is the real fundamental tradeoff (faint ghost vs visible step). Vision decides.

Run (CPU): python _ghostkill_compare.py --uuid <UUID> --tag <name>
Out → results/ghostkill/: GK_<tag>_overview.jpg + GK_<tag>_<spot>.png (LOSSLESS native-res 5-up rows)."""
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
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/ghostkill"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
FAR = 1000.0
# (centre_col_u, row0, row1, half_width_cols) native-res crop windows
SPOTS = {"bmw": {"bmw": (1760, 330, 660, 230), "graycar": (720, 320, 610, 230), "wall": (1150, 330, 630, 230)}}
DEFAULT_SPOTS = {"a": (720, 320, 610, 230), "b": (1400, 320, 610, 230), "c": (300, 320, 610, 230)}
METHODS = ["L1", "view", "align", "lidar_avg", "lidar_pick"]


def lab(im, t, h=26):
    b = np.zeros((h, im.shape[1], 3), np.uint8); cv2.putText(b, t, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return np.vstack([b, im])


def rw(im, w=1280):
    return cv2.resize(np.clip(im, 0, 255).astype(np.uint8), (w, round(im.shape[0] * w / im.shape[1])), interpolation=cv2.INTER_AREA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID)
    ap.add_argument("--tag", default="bmw")
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--band-hw", type=int, default=80)
    ap.add_argument("--max-disp", type=float, default=60.0)
    ap.add_argument("--fb-thresh", type=float, default=2.0)
    ap.add_argument("--gate", type=float, default=16.0)
    ap.add_argument("--knn-max", type=float, default=22.0)
    a = ap.parse_args()
    t0 = time.time(); erp_hw = (H, W)

    loader = a1.AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[a.anchor])
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
    print(f"[{a.tag}] lidar={pts.shape[0]} dt={dms:.0f}ms ground={'y' if ground else 'N'} fac={len(facades)} "
          f"obj={obj_mask.mean()*100:.2f}% routed={n_routed}", flush=True)

    res = {"L1": L1}

    # --- view (the user's view_none: blend, no prealign, no obj-route, struct off) ---
    view_res, vt, _np = a1.view_interp_panorama(l1_slabs, l1_slabs, l1_w, obj_mask, L1,
                                                band_half_width=a.band_hw, max_disp=a.max_disp,
                                                fb_thresh=a.fb_thresh, protect_obj=False, struct_thresh=0.0)
    res["view"] = view_res
    print(f"[{a.tag}] view blend-fired={100*(vt>0).mean():.2f}%", flush=True)

    # --- align (single-source: warp-to-agree -> hard_select + global gain colour) ---
    warped = a1.flow_align_chain(l1_slabs, l1_w, band_hw=a.band_hw, max_disp=a.max_disp, fb_thresh=a.fb_thresh)
    try:
        gains = a1.compute_hdr_gains([w.astype(np.float32) for w in warped], w_base)
        warped_c = a1.apply_hdr(warped, gains)
    except Exception as e:
        print(f"[align] gain failed ({e}); using uncorrected", flush=True); warped_c = warped
    align_res = a1.hard_select(warped_c, w_base)
    res["align"] = align_res
    align_fired = (np.abs(align_res.astype(np.int16) - L1.astype(np.int16)).sum(2) > 3).mean()
    print(f"[{a.tag}] align changed={100*align_fired:.2f}%", flush=True)

    # --- LiDAR depth reproject (densify across union band) ---
    bandU = np.zeros((H, W), bool)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        band, _s = a1.build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        bandU |= band & ov
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    th = np.arctan2(y, x); ph = np.arctan2(z, np.hypot(x, y))
    ul = (((np.pi - th) / (2 * np.pi) * W - 0.5) % W); vl = ((np.pi / 2 - ph) / np.pi * H - 0.5)
    rng = np.linalg.norm(pts, axis=1)
    ui = np.round(ul).astype(int) % W; vi = np.round(vl).astype(int)
    ok = (vi >= 0) & (vi < H) & (rng > 1.0) & (rng < 80.0)
    ui, vi, rng = ui[ok], vi[ok], rng[ok]
    rangemap = np.full((H, W), np.inf, np.float32)
    for k in range(len(ui)):
        if rng[k] < rangemap[vi[k], ui[k]]: rangemap[vi[k], ui[k]] = rng[k]
    known = np.isfinite(rangemap); ky, kx = np.where(known)
    tree = cKDTree(np.stack([ky, kx], 1)); by, bx = np.where(bandU)
    dist, nn = tree.query(np.stack([by, bx], 1), k=1)
    dense_depth = np.full((H, W), FAR, np.float64); nearm = dist <= a.knn_max
    dense_depth[by[nearm], bx[nearm]] = rangemap[ky[nn[nearm]], kx[nn[nearm]]]

    reproj, ralpha = [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        s, al, _w = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=dense_depth)
        reproj.append(s.astype(np.float32)); ralpha.append(al)
    wstack = np.stack([w.astype(np.float32) for w in l1_w], 0)
    win = wstack.argmax(0)
    reproj_win = np.take_along_axis(np.stack(reproj, 0), win[None, ..., None], axis=0)[0]  # per-pixel winner's reproj

    out_avg = L1.astype(np.float32).copy(); out_pick = L1.astype(np.float32).copy()
    fired = np.zeros((H, W), np.float32)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        band, signed = a1.build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        bm = band & ov & ralpha[i] & ralpha[j]
        resid = np.abs(reproj[i] - reproj[j]).mean(2)
        agree = bm & (resid < a.gate)
        dd = np.clip(np.abs(signed) / a.band_hw, 0, 1); ramp = np.where(band, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
        al = cv2.GaussianBlur((agree.astype(np.float32) * ramp), (0, 0), 2.0)[..., None]
        out_avg = out_avg * (1 - al) + (0.5 * (reproj[i] + reproj[j])) * al
        out_pick = out_pick * (1 - al) + reproj_win * al
        fired = np.maximum(fired, agree.astype(np.float32) * ramp)
    res["lidar_avg"] = np.clip(out_avg, 0, 255).astype(np.uint8)
    res["lidar_pick"] = np.clip(out_pick, 0, 255).astype(np.uint8)
    print(f"[{a.tag}] lidar gate-fired={100*(fired>0).mean():.2f}%  band-LiDAR-support={100*nearm.mean():.0f}%  runtime={time.time()-t0:.0f}s", flush=True)

    # overview (downscaled stack of full panos) + LOSSLESS native-res 5-up crop rows
    cv2.imwrite(str(OUT / f"GK_{a.tag}_overview.jpg"),
                cv2.cvtColor(np.vstack([lab(rw(res[m]), m) for m in METHODS]), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    spots = SPOTS.get(a.tag, DEFAULT_SPOTS)
    for name, (u, v0, v1, hw) in spots.items():
        roll = W // 2 - u; cc = W // 2
        row = [lab(cv2.cvtColor(np.roll(res[m], roll, 1)[v0:v1, cc - hw:cc + hw], cv2.COLOR_RGB2BGR), m) for m in METHODS]
        cv2.imwrite(str(OUT / f"GK_{a.tag}_{name}.png"), np.hstack(row))
    print(f"[saved] {OUT}/GK_{a.tag}_overview.jpg + {list(spots)} (lossless 5-up png)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
