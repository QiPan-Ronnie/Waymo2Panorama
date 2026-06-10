"""DB-84: temporal disocclusion repair — fill the no-evidence zone beside near objects with
REAL pixels from other timestamps (CPU/L4, no generation).

Per ROI pixel:
  1. X = C + Zd_bg * dir, where Zd_bg is the multi-frame LiDAR background field (anchor-frame
     boxes excluded). Fill candidates are ONLY pixels whose X has direct LiDAR support
     (splat distance <= SUPPORT_PX) — guessed/EDT depth never drives a fill.
  2. disocclusion test (anchor): the anchor-time min-b_perp camera's segment to X crosses an
     anchor-frame box -> the pixel is in the no-evidence zone.
  3. temporal search: over +-WINDOW synced frames x 7 cams, find pairs whose segment to X is
     box-free AT THAT FRAME and in-FOV; choose min perpendicular baseline to the anchor ray.
  4. sample RGB there (bilinear, DB-81 anchor gains); remaining gaps stay abstain.
Outputs: visibility stats JSON + A/B ROI boards (base vs temporally filled).
One bounded /exec; results fetched + Read-verified.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db84_temporal_fill"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db84_temporal_fill"
RESULT = REMOTE_OUT + "/DB84_remote_result.json"

FETCH = ["DB84_remote_result.json", "DB84_summary.json",
         "bmw_sedan_AB.png", "crowd_truck_AB.png",
         "bmw_sedan_zone.png", "crowd_truck_zone.png"]


def remote_py() -> str:
    code = r'''
import json, math, pathlib, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "bmw_sedan", (380, 460, 720, 610)),
         ("fbee355f-8878-31fa-8ac8-b9a45a3f130a:30:crowd", "crowd_truck", (1280, 380, 1750, 640))]
WINDOW = 10; DMIN, DMAX = 1.5, 80.0; STATIC_DISP_M = 0.5; SAT_LO, SAT_HI = 10, 245
SUPPORT_PX = 4.0
TEMP_FRAMES = 10          # +- synced frames searched
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}
OUT = {"phase": "db84_temporal_fill", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"evidence_only_fill": True, "generation": False, "a100": False}}

sys.path.insert(0, "/content/waymo2panorama/scripts/phase3"); sys.path.insert(0, "/content/waymo2panorama/code")


def save_rgb(path, arr):
    import cv2
    cv2.imwrite(str(path), cv2.cvtColor(np.clip(arr, 0, 255).astype("uint8"), cv2.COLOR_RGB2BGR))


def erp_dirs():
    u = np.arange(W); v = np.arange(H); uu, vv = np.meshgrid(u, v)
    theta = np.pi - (uu + 0.5) / W * 2 * np.pi; phi = np.pi / 2 - (vv + 0.5) / H * np.pi
    cph = np.cos(phi)
    return np.stack([cph * np.cos(theta), cph * np.sin(theta), np.sin(phi)], -1).astype(np.float64)


DIRS = erp_dirs()


def load_all(case_spec):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    import pandas as pd
    from scipy.spatial.transform import Rotation, Slerp
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    loader = AV2RingLoader(log_dir)
    all_ts = loader.anchor_timestamps_ns()
    ts = all_ts[anchor_idx]; frame = loader.load_synced_frame(ts)
    p = pd.read_feather(log_dir / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
    ti = p["timestamp_ns"].to_numpy(np.int64); t0 = int(ti[0]); tss = (ti - t0).astype(np.float64)
    quat = p[["qx", "qy", "qz", "qw"]].to_numpy(); tx = p[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
    keep = np.concatenate([[True], np.diff(tss) > 0]); tss, quat, tx = tss[keep], quat[keep], tx[keep]
    slerp = Slerp(tss, Rotation.from_quat(quat)); lo, hi = tss.min(), tss.max()
    def cte(t):
        tc = float(np.clip(float(int(t) - t0), lo, hi)); return slerp(tc).as_matrix(), np.array([np.interp(tc, tss, tx[:, i]) for i in range(3)])
    def tri(ta):
        tc = np.clip((np.asarray(ta, np.int64) - t0).astype(np.float64), lo, hi); return np.stack([np.interp(tc, tss, tx[:, i]) for i in range(3)], 1)
    ann = pd.read_feather(log_dir / "annotations.feather") if (log_dir / "annotations.feather").exists() else None
    return loader, log_dir, all_ts, anchor_idx, ts, frame, list(RING_CAMS_7), cte, tri, ann


def moving_tracks(ann, t_lo, t_hi):
    if ann is None or "track_uuid" not in ann.columns: return set()
    sub = ann[(ann["timestamp_ns"] >= t_lo) & (ann["timestamp_ns"] <= t_hi)]
    mv = set()
    for uid, g in sub.groupby("track_uuid"):
        if "category" in g.columns and str(g["category"].iloc[0]).upper() not in DYN_CATS: continue
        c = g[["tx_m", "ty_m", "tz_m"]].to_numpy(float)
        if len(c) >= 2 and float(np.linalg.norm(c.max(0) - c.min(0))) > STATIC_DISP_M: mv.add(uid)
    return mv


def boxes_at(ann, ts, moving=None, only_moving=True):
    from scipy.spatial.transform import Rotation
    if ann is None: return []
    tss = ann["timestamp_ns"].to_numpy(np.int64); nt = np.unique(tss)[np.argmin(np.abs(np.unique(tss) - ts))]
    out = []
    for _, r in ann[ann["timestamp_ns"] == nt].iterrows():
        if "category" in ann.columns and str(r["category"]).upper() not in DYN_CATS: continue
        if only_moving and moving is not None and "track_uuid" in ann.columns and r["track_uuid"] not in moving: continue
        out.append((np.array([r["tx_m"], r["ty_m"], r["tz_m"]], float), np.array([r["length_m"], r["width_m"], r["height_m"]], float),
                    Rotation.from_quat([r["qx"], r["qy"], r["qz"], r["qw"]]).as_matrix()))
    return out


def remove_dyn(pts, boxes, pad=0.3):
    if not boxes or len(pts) == 0: return np.ones(len(pts), bool)
    keep = np.ones(len(pts), bool)
    for c, sz, Rb in boxes:
        loc = (pts - c) @ Rb; half = sz / 2 + pad
        keep &= ~((np.abs(loc[:, 0]) < half[0]) & (np.abs(loc[:, 1]) < half[1]) & (np.abs(loc[:, 2]) < half[2]))
    return keep


def accumulate_lidar(log_dir, anchor_ts, cte, tri, ann):
    import pandas as pd
    sweeps = sorted((log_dir / "sensors" / "lidar").glob("*.feather"))
    stss = np.array([int(p.stem) for p in sweeps], np.int64); ai = int(np.argmin(np.abs(stss - anchor_ts)))
    t_lo, t_hi = int(stss[max(0, ai - WINDOW)]), int(stss[min(len(stss) - 1, ai + WINDOW)])
    moving = moving_tracks(ann, t_lo, t_hi)
    Ra, ta = cte(anchor_ts); acc = []
    for si in range(max(0, ai - WINDOW), min(len(sweeps), ai + WINDOW + 1)):
        sts = int(stss[si]); df = pd.read_feather(sweeps[si]); xyz = df[["x", "y", "z"]].to_numpy(np.float64)
        off = df["offset_ns"].to_numpy(np.int64) if "offset_ns" in df.columns else np.zeros(len(df), np.int64)
        keep = remove_dyn(xyz, boxes_at(ann, sts, moving, only_moving=True)); xyz, off = xyz[keep], off[keep]
        Rsw, _ = cte(sts); city = (Rsw @ xyz.T).T + tri((sts + off).astype(np.int64))
        acc.append((city - ta) @ Ra)
    return np.concatenate(acc, 0) if acc else np.zeros((0, 3)), (Ra, ta), moving


def bilinear(img, px, py):
    x0 = np.floor(px).astype(np.int64); y0 = np.floor(py).astype(np.int64)
    fx = (px - x0)[:, None]; fy = (py - y0)[:, None]
    hh, ww = img.shape[:2]
    x0c = np.clip(x0, 0, ww - 2); y0c = np.clip(y0, 0, hh - 2)
    a = img[y0c, x0c].astype(np.float64); b = img[y0c, x0c + 1].astype(np.float64)
    c = img[y0c + 1, x0c].astype(np.float64); d = img[y0c + 1, x0c + 1].astype(np.float64)
    return a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy


def solve_gains_for(frame, ring_cams, lidar, C):
    sub = lidar[np.random.RandomState(0).choice(len(lidar), min(len(lidar), 150000), replace=False)]
    Q = sub - C[None, :]; n = np.linalg.norm(Q, axis=1)
    sub = sub[(n > DMIN) & (n < DMAX)]
    obs = []
    for cam in ring_cams:
        cal = frame.calibrations[cam]; K = np.asarray(cal.K, float); T = np.asarray(cal.T_ego_cam, float)
        Tci = np.linalg.inv(T); img = frame.images[cam]; hh, ww = img.shape[:2]
        Xc = (Tci[:3, :3] @ sub.T).T + Tci[:3, 3]; z = Xc[:, 2]
        px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
        ok = (z > 0.5) & (px >= 2) & (px < ww - 2) & (py >= 2) & (py < hh - 2)
        rgb = np.zeros((len(sub), 3)); rgb[ok] = bilinear(img, px[ok], py[ok])
        obs.append((ok & (rgb.min(1) > SAT_LO) & (rgb.max(1) < SAT_HI), rgb))
    nc = len(ring_cams)
    gains = np.zeros((nc, 3))
    for ch in range(3):
        A = np.zeros((nc, nc)); b = np.zeros(nc)
        for i in range(nc):
            for j in range(i + 1, nc):
                both = obs[i][0] & obs[j][0]
                if both.sum() < 50: continue
                li = np.log(np.maximum(obs[i][1][both, ch], 1.0)); lj = np.log(np.maximum(obs[j][1][both, ch], 1.0))
                wgt = both.sum(); dm = float(np.median(lj - li))
                A[i, i] += wgt; A[j, j] += wgt; A[i, j] -= wgt; A[j, i] -= wgt
                b[i] += wgt * dm; b[j] -= wgt * dm
        A += np.ones((nc, nc))
        c = np.linalg.solve(A, b); gains[:, ch] = c - c.mean()
    return gains


def bg_depth_with_support(lidar, C, anchor_boxes):
    """Background field with anchor-frame box points excluded; also return per-pixel splat
    support distance (px) so fills can be restricted to evidence-backed depth."""
    from scipy.ndimage import distance_transform_edt
    keep = np.ones(len(lidar), bool)
    for c, sz, Rb in anchor_boxes:
        if np.linalg.norm(c - C) > 45: continue
        loc = (lidar - c) @ Rb; half = sz / 2 * 1.05
        keep &= ~((np.abs(loc[:, 0]) < half[0]) & (np.abs(loc[:, 1]) < half[1]) & (np.abs(loc[:, 2]) < half[2]))
    pts = lidar[keep]
    Q = pts - C[None, :]; n = np.linalg.norm(Q, axis=1)
    m = (n > DMIN) & (n < DMAX); Qm = Q[m]; nm = n[m]
    d = Qm / nm[:, None]
    theta = np.arctan2(d[:, 1], d[:, 0]); phi = np.arcsin(np.clip(d[:, 2], -1, 1))
    ui = np.clip(np.round((np.pi - theta) / (2 * np.pi) * W - 0.5).astype(np.int64), 0, W - 1)
    vi = np.clip(np.round((np.pi / 2 - phi) / np.pi * H - 0.5).astype(np.int64), 0, H - 1)
    Z = np.zeros((H, W), np.float32)
    order = np.argsort(-nm); flat = vi * W + ui; zf = Z.reshape(-1); zf[flat[order]] = nm[order].astype(np.float32)
    valid = Z > 0
    dist_px, inds = distance_transform_edt(~valid, return_distances=True, return_indices=True)
    Zf = Z[inds[0], inds[1]].astype(np.float32)
    return np.where(Zf <= 0, 200.0, Zf), dist_px.astype(np.float32)


def seg_blocked(o, X, boxes, pad=1.0):
    """Vectorized: does segment o->X[i] cross any box? Returns bool per point."""
    n = len(X)
    out = np.zeros(n, bool)
    for c, sz, Rb in boxes:
        half = sz / 2 * pad
        o_loc = Rb.T @ (o - c)
        d_loc = (X - o[None, :]) @ Rb
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / d_loc
            t1 = (-half[None, :] - o_loc[None, :]) * inv
            t2 = (half[None, :] - o_loc[None, :]) * inv
        tmin = np.nanmax(np.minimum(t1, t2), axis=1)
        tmax = np.nanmin(np.maximum(t1, t2), axis=1)
        out |= (tmax >= np.maximum(tmin, 0.0)) & (tmin < 0.97) & (tmin > 0.02)
    return out


def run_case(case_spec, run_name, roi):
    from PIL import Image, ImageDraw, ImageFont
    loader, log_dir, all_ts, anchor_idx, ts, frame, ring_cams, cte, tri, ann = load_all(case_spec)
    cents = np.stack([np.asarray(frame.calibrations[c].T_ego_cam, float)[:3, 3] for c in ring_cams], 0)
    C = cents.mean(axis=0)
    lidar, (Ra, ta), moving = accumulate_lidar(log_dir, ts, cte, tri, ann)
    gains = solve_gains_for(frame, ring_cams, lidar, C)
    anchor_boxes = boxes_at(ann, ts, moving=None, only_moving=False)   # ALL annotated objects
    Zbg, support = bg_depth_with_support(lidar, C, anchor_boxes)
    Zfull, _supf = bg_depth_with_support(lidar, C, [])                 # full field incl. object points
    x0, y0, x1, y1 = roi
    rows = np.arange(y0, y1); cols = np.arange(x0, x1)
    dirs = DIRS[np.ix_(rows, cols)].reshape(-1, 3)
    Z = Zbg[np.ix_(rows, cols)].reshape(-1)
    sup = support[np.ix_(rows, cols)].reshape(-1)
    X = C[None, :] + Z[:, None] * dirs
    npx = len(X)
    # anchor winner camera per pixel (min b_perp among valid projections)
    cals = [(np.asarray(frame.calibrations[c].K, float), np.asarray(frame.calibrations[c].T_ego_cam, float),
             frame.images[c].shape[:2]) for c in ring_cams]
    best = np.full(npx, np.inf); win = np.full(npx, -1, np.int8)
    for ci, (K, T, (hh, ww)) in enumerate(cals):
        Tci = np.linalg.inv(T)
        Xc = (Tci[:3, :3] @ X.T).T + Tci[:3, 3]; z = Xc[:, 2]
        px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
        ok = (z > 0.1) & (px >= 1) & (px < ww - 1) & (py >= 1) & (py < hh - 1)
        cvec = T[:3, 3] - C; along = dirs @ cvec
        bperp = np.sqrt(np.maximum(float(cvec @ cvec) - along * along, 0.0))
        sc = np.where(ok, bperp, np.inf)
        upd = sc < best; best[upd] = sc[upd]; win[upd] = ci
    # disocclusion zone: winner camera's segment to X crosses an anchor box, AND depth has support
    zone = np.zeros(npx, bool)
    for ci, (K, T, _s) in enumerate(cals):
        sel = win == ci
        if not sel.any(): continue
        zone[sel] = seg_blocked(T[:3, 3], X[sel], anchor_boxes)
    zone &= sup <= SUPPORT_PX * 3     # X at least loosely evidence-backed for zone stats
    # CRITICAL: pixels whose ERP ray HITS an object box are the OBJECT itself (base already
    # shows the right content there) — only box-free blocked pixels are true disocclusion.
    # Conservative: the box air margin stays unfilled (residual ghost possible there).
    box_hit = np.zeros(npx, bool)
    for bc, bsz, bR in anchor_boxes:
        if np.linalg.norm(bc - C) > 45: continue
        half = bsz / 2 * 1.05
        o_loc = bR.T @ (C - bc)
        d_loc = dirs @ bR
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / d_loc
            t1 = (-half[None, :] - o_loc[None, :]) * inv
            t2 = (half[None, :] - o_loc[None, :]) * inv
        tmin = np.nanmax(np.minimum(t1, t2), axis=1)
        tmax = np.nanmin(np.maximum(t1, t2), axis=1)
        box_hit |= (tmax >= np.maximum(tmin, 0.0)) & (tmax > 0)
    zone &= ~box_hit
    fill_ok_depth = sup <= SUPPORT_PX  # strict evidence for actual fills
    # temporal search over synced frames
    lo_i = max(0, anchor_idx - TEMP_FRAMES); hi_i = min(len(all_ts) - 1, anchor_idx + TEMP_FRAMES)
    cand_frames = [i for i in range(lo_i, hi_i + 1) if i != anchor_idx]
    zi = np.nonzero(zone)[0]
    Xz = X[zi]
    X_city = (Ra @ Xz.T).T + ta
    chosen = np.full(len(zi), -1, np.int32)        # encoded fi*10+ci
    chosen_bp = np.full(len(zi), np.inf)
    frame_info = {}
    for fi in cand_frames:
        tsf = all_ts[fi]
        Rf, tf = cte(tsf)
        Xf = (X_city - tf[None, :]) @ Rf            # points in frame-fi ego coords
        fboxes = boxes_at(ann, tsf, moving=None, only_moving=False)
        for ci, (K, T, (hh, ww)) in enumerate(cals):
            Tci = np.linalg.inv(T)
            Xc = (Tci[:3, :3] @ Xf.T).T + Tci[:3, 3]; z = Xc[:, 2]
            px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
            ok = (z > 0.5) & (px >= 2) & (px < ww - 2) & (py >= 2) & (py < hh - 2)
            if not ok.any(): continue
            blocked = np.zeros(len(zi), bool)
            blocked[ok] = seg_blocked(T[:3, 3], Xf[ok], fboxes)
            vis = ok & ~blocked
            if not vis.any(): continue
            cam_city = Rf @ T[:3, 3] + tf
            cam_anchor = Ra.T @ (cam_city - ta)     # this camera's centre in ANCHOR ego coords
            cvec = cam_anchor - C
            along = dirs[zi] @ cvec
            bperp = np.sqrt(np.maximum(float(cvec @ cvec) - along * along, 0.0))
            cand = vis & (bperp < chosen_bp)
            chosen[cand] = fi * 10 + ci
            chosen_bp[cand] = bperp[cand]
    visible = chosen >= 0
    stats = {
        "n_roi_px": int(npx), "n_zone_px": int(zone.sum()),
        "zone_frac_of_roi": float(zone.mean()),
        "temporal_visibility_fraction": float(visible.mean()) if len(zi) else None,
        "fillable_fraction": float((visible & fill_ok_depth[zi]).mean()) if len(zi) else None,
        "chosen_bperp_p50": float(np.median(chosen_bp[visible])) if visible.any() else None,
        "n_frames_searched": len(cand_frames),
    }
    # render base ROI (cen_depth_b1-equivalent: anchor min-bperp + Zbg + gains)
    def render_roi(fill=False):
        import cv2
        out = np.zeros((len(rows), len(cols), 3), np.uint8)
        flat = out.reshape(-1, 3)
        for ci, (K, T, (hh, ww)) in enumerate(cals):
            sel = win == ci
            if not sel.any(): continue
            Tci = np.linalg.inv(T)
            Xc = (Tci[:3, :3] @ X[sel].T).T + Tci[:3, 3]; z = Xc[:, 2]
            px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
            img = frame.images[ring_cams[ci]]
            g = np.exp(gains[ci])[None, :]
            col = np.clip(bilinear(img, px, py) * g, 0, 255)
            flat[sel] = col.astype(np.uint8)
        if fill:
            # group fills by (frame, cam) to load each frame once
            fz = zi[visible & fill_ok_depth[zi]]
            fc = chosen[visible & fill_ok_depth[zi]]
            for code in np.unique(fc):
                fi, ci = int(code) // 10, int(code) % 10
                tsf = all_ts[fi]
                fr2 = loader.load_synced_frame(tsf)
                Rf, tf = cte(tsf)
                idx = fz[fc == code]
                Xf = (X_city[np.searchsorted(zi, idx)] - tf[None, :]) @ Rf
                K, T, (hh, ww) = cals[ci]
                Tci = np.linalg.inv(T)
                Xc = (Tci[:3, :3] @ Xf.T).T + Tci[:3, 3]; z = Xc[:, 2]
                px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
                img = fr2.images[ring_cams[ci]]
                g = np.exp(gains[ci])[None, :]
                col = np.clip(bilinear(img, px, py) * g, 0, 255)
                flat[idx] = col.astype(np.uint8)
        return out
    base = render_roi(fill=False)
    filled = render_roi(fill=True)
    zone_vis = base.copy(); zv = zone.reshape(len(rows), len(cols)); zone_vis[zv] = (zone_vis[zv] * 0.4 + np.array([255, 40, 40]) * 0.6).astype(np.uint8)
    s = 3
    from PIL import Image as I
    def up(a): return I.fromarray(a).resize((a.shape[1] * s, a.shape[0] * s), I.LANCZOS)
    try: f = ImageFont.truetype("DejaVuSans.ttf", 18)
    except Exception: f = ImageFont.load_default()
    tiles = [("BASE (anchor only)", up(base)), (f"TEMPORAL FILL (vis={stats['temporal_visibility_fraction']:.2f} fillable={stats['fillable_fraction']:.2f})", up(filled))]
    cv = I.new("RGB", (tiles[0][1].width, sum(t[1].height + 26 for t in tiles)), (10, 10, 14))
    y = 0
    for lab, im in tiles:
        bar = I.new("RGB", (im.width, 26), (18, 18, 24)); ImageDraw.Draw(bar).text((6, 4), f"{run_name} {lab}", (235, 235, 245), font=f)
        cv.paste(bar, (0, y)); cv.paste(im, (0, y + 26)); y += im.height + 26
    cv.save(REMOTE_OUT / f"{run_name}_AB.png")
    I.fromarray(zone_vis).resize((zone_vis.shape[1] * s, zone_vis.shape[0] * s), I.NEAREST).save(REMOTE_OUT / f"{run_name}_zone.png")
    return {"case": run_name, "stats": stats}


def json_safe(o):
    if isinstance(o, dict): return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [json_safe(v) for v in o]
    if isinstance(o, np.ndarray): return o.tolist()
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o); return v if math.isfinite(v) else None
    if isinstance(o, (np.bool_,)): return bool(o)
    return o


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    reports = [run_case(cs, rn, roi) for cs, rn, roi in CASES]
    OUT["status"] = "db84_completed"; OUT["cases"] = json_safe(reports); OUT["runtime_s"] = round(time.time() - t0, 2)
    (REMOTE_OUT / "DB84_summary.json").write_text(json.dumps(json_safe({"by_case": reports}), indent=1), encoding="utf-8")
except Exception as exc:
    OUT["status"] = "db84_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB84_JSON_BEGIN"); print(json.dumps(json_safe(OUT), separators=(",", ":"))); print("DB84_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT)


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db84_remote>', 'exec'))\nPY"


def poll_job(client, job_id, timeout_s):
    t0 = time.time(); last = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(8); last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running": return sanitize(last)
    return sanitize(last or {"state": "poll_timeout"})


def run_remote(timeout_s: int = 2400) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient(); status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_py())], "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s)
    fetched = {}
    for fname in FETCH:
        raw = client.read_file(REMOTE_OUT + "/" + fname, max_size_mb=95)
        if raw is not None:
            (OUT_DIR / fname).write_bytes(raw); fetched[fname] = True
    report = {"job_state": job.get("state"), "n_fetched": len(fetched), "fetched": sorted(fetched),
              "runtime_status": {k: status.get(k) for k in ("runtime_type", "gpu_name", "active_jobs") if k in status}}
    report["secret_hits"] = secret_hits(json.dumps(report))
    return report


if __name__ == "__main__":
    rep = run_remote()
    out = Path.home() / ".waymo2panorama" / "db84_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
