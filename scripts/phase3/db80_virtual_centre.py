"""DB-80 Step A: Virtual-centre relocation — re-run the DB-79 render-back battery with
c* = ring-camera centroid vs c* = ego origin, on the SAME LiDAR points (measurement-only, CPU).

The single changed variable is the ERP sphere centre. Everything else (LiDAR accumulation,
even/odd hold-out split, layered densify, depth-aware LOO reprojection into the real cameras)
is the DB-79 protocol. The curb/wall ROI bucket is decided ONCE from ego-origin ERP coordinates
(a fixed physical region) and applied to both passes, so the comparison is point-for-point.

Pre-registered (decision_briefs.md DB-80): model CONFIRMED if centroid curb/wall DEPTH reproj
p90 <= 15 cam px AND centroid silhouette p90 <= 30 cam px on BOTH cases; KILL if curb/wall
p90 > 30 cam px on either case. Also reports per-point b_perp and the depth-tolerance map
(max |dZ| for <=2 ERP px error, using the best-b_perp seeing camera).

Self-orchestrating (db79 pattern): ONE bounded /exec on the CPU/L4 runtime, results fetched to
this repo's deliverables dir + a non-repo result file; caller Read-verifies. Secrets: env/non-repo only.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db80_virtual_centre"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db80_virtual_centre"
RESULT = REMOTE_OUT + "/DB80_remote_result.json"

# ---- PRE-REGISTERED THRESHOLDS (SET BEFORE RUN; do NOT relax on failure) ----
TH = {
    "lidar_window_frames": 10,
    "depth_min_m": 1.5,
    "depth_max_m": 80.0,
    "train_test_split": "even_odd_index_fixed_across_centres",
    "fair_radius_px": 4.0,
    "silhouette_depth_span_m": 1.0,
    "confirm_curbwall_depth_p90_le_px": 15.0,
    "confirm_silhouette_depth_p90_le_px": 30.0,
    "kill_curbwall_depth_p90_gt_px": 30.0,
    "tolerance_erp_px": 2.0,
    "lidar_only": True,
    "dynamic_removed_by_boxes": True,
}

FETCH = {
    "summary": ("DB80_summary.json", 8),
    "result": ("DB80_remote_result.json", 8),
    "board": ("DB80_review_board.jpg", 90),
    "bmw_board": ("02a00399_a000_bmw_db80_board.jpg", 80),
    "clean_board": ("0bae3b5e_a030_clean_far_db80_board.jpg", 80),
    "bmw_ego_heat": ("02a00399_a000_bmw_ego_depth_reproj_heat.png", 30),
    "bmw_cen_heat": ("02a00399_a000_bmw_centroid_depth_reproj_heat.png", 30),
    "clean_ego_heat": ("0bae3b5e_a030_clean_far_ego_depth_reproj_heat.png", 30),
    "clean_cen_heat": ("0bae3b5e_a030_clean_far_centroid_depth_reproj_heat.png", 30),
    "bmw_tol": ("02a00399_a000_bmw_centroid_tolerance_map.png", 30),
    "clean_tol": ("0bae3b5e_a030_clean_far_centroid_tolerance_map.png", 30),
}


def remote_db80_python() -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [pathlib.Path("/content/waymo2panorama"), pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama")]
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"), ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far")]
CURBWALL_ROI = (1300, 500, 1575, 760)   # ego-origin ERP px (DB-79 marked ROI; fixed physical bucket)
TH = json.loads(r"""__TH__""")
WINDOW = int(TH["lidar_window_frames"]); DMIN, DMAX = float(TH["depth_min_m"]), float(TH["depth_max_m"])
RADPX = float(TH["fair_radius_px"]); SILSPAN = float(TH["silhouette_depth_span_m"])
TOL_ERP_PX = float(TH["tolerance_erp_px"])
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}
OUT = {"phase": "db80_virtual_centre_stepA", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"measurement_only": True, "rgb_repair": False, "generation": False, "a100": False}}


def import_ok(n):
    try: __import__(n); return True
    except Exception: return False


def find_workdir():
    for c in WORKDIR_CANDIDATES:
        if (c / "scripts" / "phase3" / "run_a1_streetview_pipeline.py").exists(): return c
    return None


def json_safe(o):
    if isinstance(o, dict): return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [json_safe(v) for v in o]
    if isinstance(o, np.ndarray): return o.tolist()
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o); return v if math.isfinite(v) else None
    if isinstance(o, (np.bool_,)): return bool(o)
    return o


def save_rgb(path, arr, q=92):
    import cv2
    a = np.clip(arr, 0, 255).astype("uint8")
    if path.suffix.lower() in (".jpg", ".jpeg"): cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), int(q)])
    else: cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR))


def heat(x, valid=None, vmax=None):
    import cv2
    a = x.astype(np.float32)
    if vmax is not None: u = np.clip(a * 255.0 / max(vmax, 1e-6), 0, 255).astype(np.uint8)
    else:
        vals = a[valid] if valid is not None and bool(np.any(valid)) else a.reshape(-1)
        if vals.size == 0: return np.zeros((*x.shape, 3), np.uint8)
        lo, hi = np.percentile(vals, [2, 98]); u = np.clip((a - lo) * 255.0 / max(hi - lo, 1e-6), 0, 255).astype(np.uint8)
    h = cv2.cvtColor(cv2.applyColorMap(u, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)
    if valid is not None: h[~valid] = 0
    return h


def to_uv(P, C):
    Q = P - C[None, :]
    n = np.linalg.norm(Q, axis=1); d = Q / np.maximum(n[:, None], EPS)
    theta = np.arctan2(d[:, 1], d[:, 0]); phi = np.arcsin(np.clip(d[:, 2], -1, 1))
    return (np.pi - theta) / (2 * np.pi) * W - 0.5, (np.pi / 2 - phi) / np.pi * H - 0.5, n, d


def load_frame(case_spec, workdir):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    loader = AV2RingLoader(log_dir); ts = loader.anchor_timestamps_ns()[anchor_idx]; frame = loader.load_synced_frame(ts)
    return log_dir, ts, frame, list(RING_CAMS_7)


def load_pose_interp(log_dir):
    import pandas as pd
    from scipy.spatial.transform import Rotation, Slerp
    p = pd.read_feather(log_dir / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
    ti = p["timestamp_ns"].to_numpy(np.int64); t0 = int(ti[0]); ts = (ti - t0).astype(np.float64)
    quat = p[["qx", "qy", "qz", "qw"]].to_numpy(); tx = p[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
    keep = np.concatenate([[True], np.diff(ts) > 0]); ts, quat, tx = ts[keep], quat[keep], tx[keep]
    slerp = Slerp(ts, Rotation.from_quat(quat)); lo, hi = ts.min(), ts.max()
    def cte(t):
        tc = float(np.clip(float(int(t) - t0), lo, hi)); return slerp(tc).as_matrix(), np.array([np.interp(tc, ts, tx[:, i]) for i in range(3)])
    def tri(ta):
        tc = np.clip((np.asarray(ta, np.int64) - t0).astype(np.float64), lo, hi); return np.stack([np.interp(tc, ts, tx[:, i]) for i in range(3)], 1)
    return cte, tri


def boxes_at(ann, ts):
    from scipy.spatial.transform import Rotation
    if ann is None: return []
    tss = ann["timestamp_ns"].to_numpy(np.int64); nt = np.unique(tss)[np.argmin(np.abs(np.unique(tss) - ts))]
    out = []
    for _, r in ann[ann["timestamp_ns"] == nt].iterrows():
        if "category" in ann.columns and str(r["category"]).upper() not in DYN_CATS: continue
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
    Ra, ta = cte(anchor_ts); acc = []
    for si in range(max(0, ai - WINDOW), min(len(sweeps), ai + WINDOW + 1)):
        sts = int(stss[si]); df = pd.read_feather(sweeps[si]); xyz = df[["x", "y", "z"]].to_numpy(np.float64)
        off = df["offset_ns"].to_numpy(np.int64) if "offset_ns" in df.columns else np.zeros(len(df), np.int64)
        keep = remove_dyn(xyz, boxes_at(ann, sts)); xyz, off = xyz[keep], off[keep]
        Rsw, _ = cte(sts); city = (Rsw @ xyz.T).T + tri((sts + off).astype(np.int64))
        acc.append((city - ta) @ Ra)
    return np.concatenate(acc, 0) if acc else np.zeros((0, 3))


def prepare_points(lidar):
    """Fixed train/test split + curbwall bucket, decided ONCE in ego-origin coordinates so both
    centre passes score the exact same physical points."""
    C0 = np.zeros(3)
    u0, v0, d0, _dir0 = to_uv(lidar, C0)
    m = (d0 > DMIN) & (d0 < DMAX)
    ui0 = np.round(u0).astype(np.int64); vi0 = np.round(v0).astype(np.int64)
    ib = m & (ui0 >= 0) & (ui0 < W) & (vi0 >= 0) & (vi0 < H)
    idx = np.nonzero(ib)[0]
    if idx.size < 2000: return None
    ev = (np.arange(idx.size) % 2 == 0)
    train_idx = idx[ev]; test_idx = idx[~ev]
    CAP = 400000
    if test_idx.size > CAP:
        sel = np.random.RandomState(0).choice(test_idx.size, CAP, replace=False)
        test_idx = test_idx[sel]
    x0, y0, x1, y1 = CURBWALL_ROI
    tu0 = ui0[test_idx]; tv0 = vi0[test_idx]
    curb = (tu0 >= x0) & (tu0 < x1) & (tv0 >= y0) & (tv0 < y1)
    return train_idx, test_idx, curb


def densify_pass(lidar, C, train_idx, test_idx):
    """Layered FAIR hold-out densify + surface/silhouette split, in the ERP of centre C.
    Returns per-test residual/labels + the densified Zold field + test ERP coords for centre C."""
    from scipy.spatial import cKDTree
    from scipy.ndimage import distance_transform_edt
    u, v, d, _ = to_uv(lidar, C)
    ui = np.round(u).astype(np.int64); vi = np.round(v).astype(np.int64)
    np.clip(ui, 0, W - 1, out=ui); np.clip(vi, 0, H - 1, out=vi)
    utr, vtr, dtr = u[train_idx], v[train_idx], d[train_idx].astype(np.float32)
    uitr, vitr = ui[train_idx], vi[train_idx]
    tu, tv, td = u[test_idx], v[test_idx], d[test_idx].astype(np.float32)
    tui, tvi = ui[test_idx], vi[test_idx]
    Zsp = np.zeros((H, W), np.float32)
    order = np.argsort(-dtr); flat = (vitr * W + uitr); sd = Zsp.reshape(-1); sd[flat[order]] = dtr[order]
    validpix = Zsp > 0
    _dist, inds = distance_transform_edt(~validpix, return_distances=True, return_indices=True)
    Zold = Zsp[inds[0], inds[1]].astype(np.float32)
    gx = np.abs(np.diff(Zold, axis=1, prepend=Zold[:, :1])); gy = np.abs(np.diff(Zold, axis=0, prepend=Zold[:1, :]))
    grad = np.maximum(gx, gy)
    tree = cKDTree(np.stack([utr, vtr], 1))
    K = int(min(8, dtr.size))
    dists, idx = tree.query(np.stack([tu, tv], 1), k=K)
    dists = dists.astype(np.float32).reshape(td.size, K); idx = idx.reshape(td.size, K)
    dn = dtr[idx]; within = dists <= RADPX
    absd = np.where(within, np.abs(dn - td[:, None]), np.inf)
    fair_min = absd.min(axis=1); has_nb = np.isfinite(fair_min)
    old_res = np.abs(Zold[tvi, tui] - td).astype(np.float32)
    fair_res = np.where(has_nb, fair_min, old_res).astype(np.float32)
    dn_max = np.where(within, dn, -np.inf).max(axis=1); dn_min = np.where(within, dn, np.inf).min(axis=1)
    span = dn_max - dn_min
    is_sil = np.where(has_nb, span > SILSPAN, grad[tvi, tui] > SILSPAN)
    return {"fair_res": fair_res, "surf": ~is_sil, "sil": is_sil, "Zold": Zold,
            "tu": tu, "tv": tv, "td": td, "tui": tui, "tvi": tvi}


def reproj_pass(frame, ring_cams, lidar, C, dp, curb):
    """Depth-aware LOO render-back at centre C: project X_true / X_zd / X_far into every seeing
    camera; also collect per-pair b_perp and the depth tolerance for <=TOL_ERP_PX ERP px."""
    td = dp["td"]; tui = dp["tui"]; tvi = dp["tvi"]
    u = np.arange(W); vv = np.arange(H)
    # unit dirs for the test pixels at centre C (recompute from stored continuous coords for accuracy)
    theta = np.pi - (dp["tu"] + 0.5) / W * 2 * np.pi; phi = np.pi / 2 - (dp["tv"] + 0.5) / H * np.pi
    cph = np.cos(phi)
    d = np.stack([cph * np.cos(theta), cph * np.sin(theta), np.sin(phi)], -1)
    Zd_here = np.maximum(dp["Zold"][tvi, tui].astype(np.float64), 0.1)
    X_true = C[None, :] + td[:, None].astype(np.float64) * d
    X_zd = C[None, :] + Zd_here[:, None] * d
    X_far = C[None, :] + 1000.0 * d
    cams = []
    for cam in ring_cams:
        cal = frame.calibrations[cam]; K = np.asarray(cal.K, float); T = np.asarray(cal.T_ego_cam, float)
        Tci = np.linalg.inv(T); hh, ww = frame.images[cam].shape[:2]
        def proj(X):
            Xc = (Tci[:3, :3] @ X.T).T + Tci[:3, 3]; z = Xc[:, 2]
            px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
            return px, py, z
        pxt, pyt, zt = proj(X_true); inb = (zt > 0.1) & (pxt >= 0) & (pxt < ww) & (pyt >= 0) & (pyt < hh)
        pxd, pyd, _a = proj(X_zd); pxr, pyr, _b = proj(X_far)
        cvec = T[:3, 3] - C
        along = d @ cvec
        bperp = np.sqrt(np.maximum(np.sum(cvec * cvec) - along * along, 0.0))
        cams.append((inb, np.hypot(pxd - pxt, pyd - pyt).astype(np.float32),
                     np.hypot(pxr - pxt, pyr - pyt).astype(np.float32), bperp.astype(np.float32)))
    seen = np.sum([c[0].astype(np.int32) for c in cams], axis=0); co = seen >= 2
    dl, rl, sfl, sll, cwl, bpl, il = [], [], [], [], [], [], []
    for inb, de, re, bp in cams:
        sel = inb & co
        dl.append(de[sel]); rl.append(re[sel]); bpl.append(bp[sel])
        sfl.append(dp["surf"][sel]); sll.append(dp["sil"][sel]); cwl.append(curb[sel])
        il.append(np.nonzero(sel)[0])
    dep = np.concatenate(dl); rot = np.concatenate(rl); bper = np.concatenate(bpl)
    sf = np.concatenate(sfl); sl = np.concatenate(sll); cw = np.concatenate(cwl)
    tidx = np.concatenate(il)
    # depth tolerance for <= TOL_ERP_PX ERP px, via the BEST (min b_perp) seeing camera per point
    bp_min = np.full(td.size, np.inf, np.float32)
    for inb, _de, _re, bp in cams:
        sel = inb & co
        bp_min[sel] = np.minimum(bp_min[sel], bp[sel])
    has = np.isfinite(bp_min)
    tol = np.full(td.size, np.nan, np.float32)
    tol[has] = (TOL_ERP_PX * 2 * np.pi / W) * (td[has] ** 2) / np.maximum(bp_min[has], 1e-3)
    def pct(a, p): return float(np.percentile(a, p)) if a.size else None
    res = {
        "n_loo_pairs": int(dep.size), "n_co_observed_pts": int(co.sum()),
        "ROT_reproj_px_p50": pct(rot, 50), "ROT_reproj_px_p90": pct(rot, 90),
        "DEPTH_reproj_px_p50": pct(dep, 50), "DEPTH_reproj_px_p90": pct(dep, 90),
        "DEPTH_surface_reproj_px_p90": pct(dep[sf], 90), "DEPTH_silhouette_reproj_px_p90": pct(dep[sl], 90),
        "curbwall_DEPTH_reproj_px_p90": pct(dep[cw], 90), "curbwall_ROT_reproj_px_p90": pct(rot[cw], 90),
        "curbwall_n_pairs": int(cw.sum()),
        "false_green_DEPTH_gt3px": float((dep > 3).mean()) if dep.size else None,
        "b_perp_p50_m": pct(bper, 50), "b_perp_p90_m": pct(bper, 90),
        "tolerance_m_p50": pct(tol[has & (td < 15)], 50),
        "near15m_frac_tolerance_gt_0p5m": float((tol[has & (td < 15)] > 0.5).mean()) if bool((has & (td < 15)).any()) else None,
        "near15m_frac_tolerance_gt_1m": float((tol[has & (td < 15)] > 1.0).mean()) if bool((has & (td < 15)).any()) else None,
    }
    def field2(vals, idx):
        rf = np.zeros(H * W, np.float32); cf = np.zeros(H * W, np.float32)
        fl = tvi[idx] * W + tui[idx]; np.add.at(rf, fl, vals); np.add.at(cf, fl, 1.0)
        return (rf / np.maximum(cf, 1)).reshape(H, W), (cf.reshape(H, W) > 0)
    rf_dep, h_dep = field2(dep, tidx)
    tol_f = np.zeros(H * W, np.float32); tol_c = np.zeros(H * W, np.float32)
    hs = np.nonzero(has)[0]; fl = tvi[hs] * W + tui[hs]
    np.add.at(tol_f, fl, np.nan_to_num(tol[hs], nan=0.0)); np.add.at(tol_c, fl, 1.0)
    tol_map = (tol_f / np.maximum(tol_c, 1)).reshape(H, W); tol_has = tol_c.reshape(H, W) > 0
    return res, rf_dep, h_dep, tol_map, tol_has


def run_case(case_spec, run_name, workdir):
    from PIL import Image, ImageDraw, ImageFont
    import pandas as pd
    log_dir, ts, frame, ring_cams = load_frame(case_spec, workdir)
    cte, tri = load_pose_interp(log_dir)
    ann = pd.read_feather(log_dir / "annotations.feather") if (log_dir / "annotations.feather").exists() else None
    lidar = accumulate_lidar(log_dir, ts, cte, tri, ann)
    prep = prepare_points(lidar)
    if prep is None: return {"case": run_name, "error": "too_few_lidar"}
    train_idx, test_idx, curb = prep
    cents = np.stack([np.asarray(frame.calibrations[c].T_ego_cam, float)[:3, 3] for c in ring_cams], 0)
    centroid = cents.mean(axis=0)
    metrics = {"ring_centroid_ego_m": centroid.tolist(),
               "cam_dist_to_ego_origin_m": [float(np.linalg.norm(c)) for c in cents],
               "cam_dist_to_centroid_m": [float(np.linalg.norm(c - centroid)) for c in cents],
               "n_lidar_accumulated": int(len(lidar)), "n_test": int(test_idx.size), "n_curbwall_test": int(curb.sum())}
    boards = {}
    for tag, C in (("ego", np.zeros(3)), ("centroid", centroid)):
        dp = densify_pass(lidar, C, train_idx, test_idx)
        def pct(a, p): return float(np.percentile(a, p)) if a.size else None
        dres = {"FAIR_surface_p90_m": pct(dp["fair_res"][dp["surf"]], 90),
                "FAIR_silhouette_p90_m": pct(dp["fair_res"][dp["sil"]], 90),
                "silhouette_fraction": float(dp["sil"].mean())}
        r3, rf_dep, h_dep, tol_map, tol_has = reproj_pass(frame, ring_cams, lidar, C, dp, curb)
        metrics[tag] = {"densify": dres, "step3": r3}
        save_rgb(REMOTE_OUT / f"{run_name}_{tag}_depth_reproj_heat.png", heat(rf_dep, h_dep, vmax=3.0))
        boards[tag] = (rf_dep, h_dep)
        if tag == "centroid":
            save_rgb(REMOTE_OUT / f"{run_name}_centroid_tolerance_map.png", heat(np.clip(tol_map, 0, 2.0), tol_has, vmax=2.0))
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    e3 = metrics["ego"]["step3"]; c3 = metrics["centroid"]["step3"]
    tiles = [(f"EGO-ORIGIN depth-aware LOO reproj (0..3px) p90={e3['DEPTH_reproj_px_p90']:.1f} curbwall={e3['curbwall_DEPTH_reproj_px_p90']} sil={e3['DEPTH_silhouette_reproj_px_p90']:.1f} bperp_p50={e3['b_perp_p50_m']:.2f}m", heat(boards["ego"][0], boards["ego"][1], vmax=3.0)),
             (f"CENTROID depth-aware LOO reproj (0..3px, SAME scale) p90={c3['DEPTH_reproj_px_p90']:.1f} curbwall={c3['curbwall_DEPTH_reproj_px_p90']} sil={c3['DEPTH_silhouette_reproj_px_p90']:.1f} bperp_p50={c3['b_perp_p50_m']:.2f}m", heat(boards["centroid"][0], boards["centroid"][1], vmax=3.0))]
    ims = []
    for t, a in tiles:
        from PIL import Image as I
        im = I.fromarray(np.clip(a, 0, 255).astype(np.uint8)).resize((1000, 500))
        bar = I.new("RGB", (1000, 26), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 5), t, (235, 235, 245), font=f)
        o = I.new("RGB", (1000, 526)); o.paste(bar, (0, 0)); o.paste(im, (0, 26)); ims.append(o)
    from PIL import Image as I
    board = I.new("RGB", (1000, 526 * 2 + 56), (10, 10, 14)); dr = ImageDraw.Draw(board)
    dr.text((8, 6), f"{run_name} DB-80 stepA: ego vs centroid (same points, same depth, only the sphere centre changes)", (240, 240, 250), font=f)
    dr.text((8, 30), f"centroid={['%.3f' % x for x in metrics['ring_centroid_ego_m']]} | curbwall DEPTH p90: ego={e3['curbwall_DEPTH_reproj_px_p90']} -> centroid={c3['curbwall_DEPTH_reproj_px_p90']}", (210, 230, 210), font=f)
    yo = 56
    for o in ims: board.paste(o, (0, yo)); yo += o.height
    board.save(REMOTE_OUT / f"{run_name}_db80_board.jpg", quality=88)
    return {"case": run_name, "metrics": metrics}


def verdict(by_case):
    ct = float(TH["confirm_curbwall_depth_p90_le_px"]); st = float(TH["confirm_silhouette_depth_p90_le_px"])
    kt = float(TH["kill_curbwall_depth_p90_gt_px"])
    cw, sil, ratio = [], [], []
    for c in by_case.values():
        m = c.get("metrics", {})
        c3 = (m.get("centroid", {}) or {}).get("step3", {}) or {}
        e3 = (m.get("ego", {}) or {}).get("step3", {}) or {}
        if c3.get("curbwall_DEPTH_reproj_px_p90") is not None: cw.append(c3["curbwall_DEPTH_reproj_px_p90"])
        if c3.get("DEPTH_silhouette_reproj_px_p90") is not None: sil.append(c3["DEPTH_silhouette_reproj_px_p90"])
        if e3.get("DEPTH_reproj_px_p90") and c3.get("DEPTH_reproj_px_p90"):
            ratio.append(e3["DEPTH_reproj_px_p90"] / max(c3["DEPTH_reproj_px_p90"], 1e-6))
    v = {"centroid_curbwall_p90_px": cw, "centroid_silhouette_p90_px": sil, "ego_over_centroid_p90_ratio": ratio,
         "confirm_thr": {"curbwall_le": ct, "silhouette_le": st}, "kill_thr_curbwall_gt": kt}
    if cw and all(x <= ct for x in cw) and sil and all(x <= st for x in sil):
        v["call"] = "BPERP_MODEL_CONFIRMED: the DB-79 seam wall was dominated by the ego-origin virtual-centre amplification; at c*=ring centroid the depth-aware render-back lands in thin-band repair range -> proceed to step B (full N1 render at centroid)."
    elif cw and any(x > kt for x in cw):
        v["call"] = "WALL_CONFIRMED_beyond_centre_choice: curb/wall depth-aware reproj stays > %.0fpx even at the ring centroid -> the seam residual is NOT a c* artifact; abstain remains the honest ceiling; close the c* route." % kt
    else:
        v["call"] = "INTERMEDIATE (between confirm and kill) — report numbers, vision-check the heatmaps, do NOT auto-proceed."
    return v


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    if not import_ok("av2"): subprocess.run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=600, check=False)
    workdir = find_workdir(); OUT["workdir_found"] = bool(workdir)
    if workdir is None: raise RuntimeError("workdir not found")
    sys.path.insert(0, str(workdir / "scripts" / "phase3")); sys.path.insert(0, str(workdir / "code"))
    reports = [run_case(cs, rn, workdir) for cs, rn in CASES]
    by_case = {r["case"]: r for r in reports}
    summary = {"status": "db80_stepA_complete", "measurement_only": True, "pre_registered": TH,
               "by_case": by_case, "verdict": verdict(by_case), "runtime_s": round(time.time() - t0, 2)}
    (REMOTE_OUT / "DB80_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    try:
        from PIL import Image
        ps = [REMOTE_OUT / "02a00399_a000_bmw_db80_board.jpg", REMOTE_OUT / "0bae3b5e_a030_clean_far_db80_board.jpg"]
        ims = [Image.open(p) for p in ps if p.exists()]
        if ims:
            w = max(i.width for i in ims); bd = Image.new("RGB", (w, sum(i.height for i in ims) + 20), (8, 8, 12)); yo = 10
            for im in ims: bd.paste(im, (0, yo)); yo += im.height
            bd.save(REMOTE_OUT / "DB80_review_board.jpg", quality=88)
    except Exception: pass
    OUT["status"] = "db80_stepA_completed"; OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "db80_stepA_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB80_JSON_BEGIN"); print(json.dumps(json_safe(OUT), separators=(",", ":"))); print("DB80_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT).replace("__TH__", json.dumps(TH))


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db80_remote>', 'exec'))\nPY"


def poll_job(client, job_id, timeout_s):
    t0 = time.time(); last = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(7); last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running": return sanitize(last)
    return sanitize(last or {"state": "poll_timeout"})


def run_remote(timeout_s: int = 1800) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient(); status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_db80_python())], "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s)
    fetched = {}
    for key, (fname, mb) in FETCH.items():
        raw = client.read_file(REMOTE_OUT + "/" + fname, max_size_mb=mb)
        if raw is not None:
            (OUT_DIR / fname).write_bytes(raw); fetched[key] = fname
    report = {"job_state": job.get("state"), "fetched": fetched,
              "runtime_status": {k: status.get(k) for k in ("runtime_type", "gpu_name", "active_jobs") if k in status}}
    txt = json.dumps(report)
    report["secret_hits"] = secret_hits(txt)
    return report


if __name__ == "__main__":
    rep = run_remote()
    out = Path.home() / ".waymo2panorama" / "db80_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
