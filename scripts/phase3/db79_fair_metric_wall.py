"""DB-79: Fair-metric wall settlement (measurement-only, CPU; NO RGB repair, NO A100).

Removes the two verified DB-77B/DB-76a measurement confounds and re-asks whether the
source-faithful near-field depth route is walled on SURFACES or only at occlusion SILHOUETTES.

STEP 1+2 (this file): layered/LDI hold-out + LiDAR-ONLY densify + surface/silhouette split.
  - OLD metric (reproduce the artifact): single-near-wins densified value scored at held-out pts.
  - FAIR metric: each held-out LiDAR point scored vs the NEAREST training depth LAYER within a small
    ERP radius (kills the far-vs-near scoring artifact); stereo-SGBM EXCLUDED (LiDAR-only); split
    test pixels into SURFACE (low local depth span in the window) vs SILHOUETTE (window straddles a
    depth step) and report p90 SEPARATELY (the headline). Strict dynamic removal (boxes) before accum.
Pre-registered thresholds locked below; kill/confirm verdict computed remotely.
Self-orchestrating (clone of db77b pattern): embeds a remote source, runs ONE bounded /exec on the
CPU runtime, fetches results to a non-repo local dir; caller Read-verifies. Secrets: env/non-repo only.
"""
from __future__ import annotations
import argparse, base64, json, py_compile, time, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, rel, safe_status, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db79_fair_metric_wall"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db79_fair_metric_wall"
RESULT = REMOTE_OUT + "/DB79_remote_result.json"

# ---- PRE-REGISTERED THRESHOLDS (SET BEFORE RUN; do NOT relax on failure) ----
TH = {
    "lidar_window_frames": 10,
    "depth_min_m": 1.5,
    "depth_max_m": 80.0,
    "train_test_split": "even_odd_index",
    "fair_radius_px": 4.0,                 # nearest-layer search radius in ERP px
    "silhouette_depth_span_m": 1.0,        # window depth span > this => occlusion silhouette pixel
    "reopen_surface_p90_lt_m": 1.0,        # "reopened on surfaces" requires surface p90 < 1m
    "kill_surface_p90_gt_m": 2.0,          # surface p90 stays > 2m => wall CONFIRMED with a fair metric
    "lidar_only": True,
    "stereo_excluded": True,
    "dynamic_removed_by_boxes": True,
}

FETCH = {
    "summary": ("DB79_summary.json", 8),
    "result": ("DB79_remote_result.json", 8),
    "board": ("DB79_review_board.jpg", 90),
    "bmw_board": ("02a00399_a000_bmw_db79_board.jpg", 80),
    "clean_board": ("0bae3b5e_a030_clean_far_db79_board.jpg", 80),
    "bmw_surf": ("02a00399_a000_bmw_surface_resid_heat.png", 30),
    "bmw_sil": ("02a00399_a000_bmw_silhouette_resid_heat.png", 30),
    "clean_surf": ("0bae3b5e_a030_clean_far_surface_resid_heat.png", 30),
    "clean_sil": ("0bae3b5e_a030_clean_far_silhouette_resid_heat.png", 30),
}


def remote_db79_python() -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [pathlib.Path("/content/waymo2panorama"), pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama")]
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"), ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far")]
MARKED_ROIS = {"left_road_patch": (250, 515, 460, 715), "lower_center_road_patch": (740, 595, 1035, 745),
               "center_lane_marking": (1030, 515, 1325, 735), "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760)}
CURBWALL_ROI = MARKED_ROIS["right_curb_sidewalk_wall_base"]
TH = json.loads(r"""__TH__""")
WINDOW = int(TH["lidar_window_frames"]); DMIN, DMAX = float(TH["depth_min_m"]), float(TH["depth_max_m"])
RADPX = float(TH["fair_radius_px"]); SILSPAN = float(TH["silhouette_depth_span_m"])
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}
OUT = {"phase": "db79_fair_metric_wall_step12", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"measurement_only": True, "rgb_repair": False, "generation": False, "model_inference": False, "a100": False}}


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


def draw_rois(img):
    import cv2
    o = img.copy()
    for name, (x0, y0, x1, y1) in MARKED_ROIS.items():
        cv2.rectangle(o, (x0, y0), (x1, y1), (60, 220, 255), 2)
    return o


def erp_dirs():
    u = np.arange(W); v = np.arange(H); uu, vv = np.meshgrid(u, v)
    theta = np.pi - (uu + 0.5) / W * 2 * np.pi; phi = np.pi / 2 - (vv + 0.5) / H * np.pi
    cph = np.cos(phi)
    return np.stack([cph * np.cos(theta), cph * np.sin(theta), np.sin(phi)], -1).astype(np.float64)


def ego_to_uv(P):
    n = np.linalg.norm(P, axis=1); d = P / np.maximum(n[:, None], EPS)
    theta = np.arctan2(d[:, 1], d[:, 0]); phi = np.arcsin(np.clip(d[:, 2], -1, 1))
    return (np.pi - theta) / (2 * np.pi) * W - 0.5, (np.pi / 2 - phi) / np.pi * H - 0.5, n


def ring_skeleton(case_spec, workdir):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    loader = AV2RingLoader(log_dir); ts = loader.anchor_timestamps_ns()[anchor_idx]; frame = loader.load_synced_frame(ts)
    weights, labs = [], []
    for cam in RING_CAMS_7:
        cal = frame.calibrations[cam]
        rgb, _a, w = render_camera_to_erp(image=frame.images[cam], K=cal.K, T_ego_cam=cal.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        weights.append(w.astype(np.float32)); labs.append(rgb.astype(np.uint8))
    weights = np.stack(weights, 0); valid_any = weights.max(0) > EPS
    owner = np.argsort(-weights, axis=0)[0].astype(np.int32)
    base = np.take_along_axis(np.stack(labs, 0), np.where(valid_any, owner, 0)[None, :, :, None], axis=0)[0]; base[~valid_any] = 0
    return log_dir, ts, frame, valid_any, base


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


def fair_densify_loo(lidar):
    # LiDAR-only hold-out densify residual, scored OLD (single-near-wins) vs FAIR (nearest layer in radius),
    # split SURFACE vs SILHOUETTE by local depth span. Returns metrics + per-pixel residual fields.
    from scipy.spatial import cKDTree
    from scipy.ndimage import distance_transform_edt
    u, v, d = ego_to_uv(lidar); m = (d > DMIN) & (d < DMAX)
    uf = u[m]; vf = v[m]; dd = d[m].astype(np.float32)
    ui = np.round(uf).astype(np.int64); vi = np.round(vf).astype(np.int64)
    ib = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
    uf, vf, dd, ui, vi = uf[ib], vf[ib], dd[ib], ui[ib], vi[ib]
    if dd.size < 2000: return None
    ev = (np.arange(dd.size) % 2 == 0)               # train=even, test=odd
    # OLD single-near-wins densified field (LiDAR-only): nearest depth overwrites per pixel, EDT fill
    Zsp = np.zeros((H, W), np.float32)
    order = np.argsort(-dd[ev]); flat = (vi[ev] * W + ui[ev]); sd = Zsp.reshape(-1); sd[flat[order]] = dd[ev][order]
    validpix = Zsp > 0
    _dist, inds = distance_transform_edt(~validpix, return_distances=True, return_indices=True)
    Zold = Zsp[inds[0], inds[1]].astype(np.float32)
    gx = np.abs(np.diff(Zold, axis=1, prepend=Zold[:, :1])); gy = np.abs(np.diff(Zold, axis=0, prepend=Zold[:1, :]))
    grad = np.maximum(gx, gy)
    # test points (cap for speed; deterministic representative subsample)
    tu, tv, td = uf[~ev], vf[~ev], dd[~ev]; tui, tvi = ui[~ev], vi[~ev]
    CAP = 400000
    if td.size > CAP:
        sel = np.random.RandomState(0).choice(td.size, CAP, replace=False)
        tu, tv, td, tui, tvi = tu[sel], tv[sel], td[sel], tui[sel], tvi[sel]
    old_res = np.abs(Zold[tvi, tui] - td).astype(np.float32)
    # FAIR: nearest train-layer depth within RADPX, vectorized k-NN (no python loop)
    de = dd[ev].astype(np.float32); tree = cKDTree(np.stack([uf[ev], vf[ev]], 1))
    K = int(min(8, de.size))
    dists, idx = tree.query(np.stack([tu, tv], 1), k=K)
    dists = dists.astype(np.float32).reshape(td.size, K); idx = idx.reshape(td.size, K)
    dn = de[idx]                                          # (N,K) train-neighbor depths
    within = dists <= RADPX
    absd = np.where(within, np.abs(dn - td[:, None]), np.inf)
    fair_min = absd.min(axis=1); has_nb = np.isfinite(fair_min)
    fair_res = np.where(has_nb, fair_min, old_res).astype(np.float32)
    dn_max = np.where(within, dn, -np.inf).max(axis=1); dn_min = np.where(within, dn, np.inf).min(axis=1)
    span = dn_max - dn_min
    is_sil = np.where(has_nb, span > SILSPAN, grad[tvi, tui] > SILSPAN)
    surf = ~is_sil
    def pct(a, p): return float(np.percentile(a, p)) if a.size else None

    def roi_mask(tvi_, tui_):
        x0, y0, x1, y1 = CURBWALL_ROI
        return (tui_ >= x0) & (tui_ < x1) & (tvi_ >= y0) & (tvi_ < y1)
    rm = roi_mask(tvi, tui)
    res = {
        "n_test_pts": int(td.size), "n_train_pts": int(ev.sum()), "fair_has_neighbor_frac": float(has_nb.mean()),
        "OLD_single_near_p50_m": pct(old_res, 50), "OLD_single_near_p90_m": pct(old_res, 90),
        "FAIR_overall_p90_m": pct(fair_res, 90),
        "FAIR_surface_p50_m": pct(fair_res[surf], 50), "FAIR_surface_p90_m": pct(fair_res[surf], 90),
        "FAIR_silhouette_p50_m": pct(fair_res[is_sil], 50), "FAIR_silhouette_p90_m": pct(fair_res[is_sil], 90),
        "silhouette_fraction": float(is_sil.mean()), "surface_fraction": float(surf.mean()),
        "curbwall_FAIR_surface_p90_m": pct(fair_res[surf & rm], 90), "curbwall_FAIR_silhouette_p90_m": pct(fair_res[is_sil & rm], 90),
        "curbwall_OLD_single_near_p90_m": pct(old_res[rm], 90), "curbwall_n_test": int(rm.sum()),
    }
    # residual fields for boards
    def field(mask):
        rf = np.zeros(H * W, np.float32); cf = np.zeros(H * W, np.float32)
        fl = tvi[mask] * W + tui[mask]; np.add.at(rf, fl, fair_res[mask]); np.add.at(cf, fl, 1.0)
        return (rf / np.maximum(cf, 1)).reshape(H, W), (cf.reshape(H, W) > 0)
    rf_surf, h_surf = field(surf); rf_sil, h_sil = field(is_sil)
    return res, rf_surf, h_surf, rf_sil, h_sil


def run_case(case_spec, run_name, workdir):
    from PIL import Image, ImageDraw, ImageFont
    import pandas as pd
    log_dir, ts, frame, valid_any, base = ring_skeleton(case_spec, workdir)
    cte, tri = load_pose_interp(log_dir)
    ann = pd.read_feather(log_dir / "annotations.feather") if (log_dir / "annotations.feather").exists() else None
    lidar = accumulate_lidar(log_dir, ts, cte, tri, ann)
    out = fair_densify_loo(lidar)
    if out is None: return {"case": run_name, "error": "too_few_lidar"}
    res, rf_surf, h_surf, rf_sil, h_sil, = (*out,)
    res["n_lidar_accumulated"] = int(len(lidar))
    # boards: base+ROIs | surface resid heat (vmax=2m) | silhouette resid heat (vmax=2m)
    surf_h = heat(rf_surf, h_surf, vmax=2.0); sil_h = heat(rf_sil, h_sil, vmax=2.0)
    save_rgb(REMOTE_OUT / f"{run_name}_surface_resid_heat.png", surf_h)
    save_rgb(REMOTE_OUT / f"{run_name}_silhouette_resid_heat.png", sil_h)
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    tiles = [("hard_select base + 4 marked ROIs", draw_rois(base)),
             (f"FAIR SURFACE residual (0..2m) p90={res['FAIR_surface_p90_m']}", surf_h),
             (f"FAIR SILHOUETTE residual (0..2m) p90={res['FAIR_silhouette_p90_m']}", sil_h)]
    ims = []
    for t, a in tiles:
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).resize((1000, 500))
        bar = Image.new("RGB", (1000, 26), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 5), t, (235, 235, 245), font=f)
        o = Image.new("RGB", (1000, 526)); o.paste(bar, (0, 0)); o.paste(im, (0, 26)); ims.append(o)
    board = Image.new("RGB", (1000, 526 * 3 + 56), (10, 10, 14)); d = ImageDraw.Draw(board)
    d.text((8, 6), f"{run_name} DB-79 fair metric: OLD single-near p90={res['OLD_single_near_p90_m']}m -> FAIR surface p90={res['FAIR_surface_p90_m']}m | silhouette p90={res['FAIR_silhouette_p90_m']}m", (240, 240, 250), font=f)
    d.text((8, 30), f"sil_frac={res['silhouette_fraction']:.3f}  curbwall: OLD p90={res['curbwall_OLD_single_near_p90_m']}m surf p90={res['curbwall_FAIR_surface_p90_m']}m sil p90={res['curbwall_FAIR_silhouette_p90_m']}m", (210, 230, 210), font=f)
    yo = 56
    for o in ims: board.paste(o, (0, yo)); yo += o.height
    board.save(REMOTE_OUT / f"{run_name}_db79_board.jpg", quality=88)
    return {"case": run_name, "metrics": res}


def verdict(by_case):
    # pre-registered: reopen needs surface p90 < reopen_thr on BOTH cases; kill if any surface p90 > kill_thr
    rt = float(TH["reopen_surface_p90_lt_m"]); kt = float(TH["kill_surface_p90_gt_m"])
    surf = [c["metrics"]["FAIR_surface_p90_m"] for c in by_case.values() if "metrics" in c and c["metrics"].get("FAIR_surface_p90_m") is not None]
    sil = [c["metrics"]["FAIR_silhouette_p90_m"] for c in by_case.values() if "metrics" in c and c["metrics"].get("FAIR_silhouette_p90_m") is not None]
    cw = [c["metrics"].get("curbwall_FAIR_surface_p90_m") for c in by_case.values() if "metrics" in c]
    cw = [x for x in cw if x is not None]
    v = {"surface_p90_all_m": surf, "silhouette_p90_all_m": sil, "curbwall_surface_p90_all_m": cw,
         "reopen_thr_m": rt, "kill_thr_m": kt}
    if surf and all(s < rt for s in surf) and (not cw or all(s < 2.0 for s in cw)):
        v["call"] = "SURFACES_REOPEN_candidate (surface p90 < %.1fm on all cases) — needs depth-aware LOO <3px (step3) + EXP-B cross-check before declaring" % rt
    elif surf and any(s > kt for s in surf):
        v["call"] = "WALL_CONFIRMED_fair (surface p90 > %.1fm) — close depth route, ship DB-78+abstain" % kt
    else:
        v["call"] = "INTERMEDIATE (surface p90 in [%.1f,%.1f]m) — report, do NOT declare reopened" % (rt, kt)
    return v


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    if not import_ok("av2"): subprocess.run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=600, check=False)
    workdir = find_workdir(); OUT["workdir_found"] = bool(workdir)
    if workdir is None: raise RuntimeError("workdir not found")
    sys.path.insert(0, str(workdir / "scripts" / "phase3")); sys.path.insert(0, str(workdir / "code"))
    reports = [run_case(cs, rn, workdir) for cs, rn in CASES]
    by_case = {r["case"]: r for r in reports}
    summary = {"status": "db79_step12_complete", "measurement_only": True, "pre_registered": TH,
               "by_case": by_case, "verdict": verdict(by_case), "runtime_s": round(time.time() - t0, 2)}
    (REMOTE_OUT / "DB79_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    # combined board
    try:
        from PIL import Image
        ps = [REMOTE_OUT / "02a00399_a000_bmw_db79_board.jpg", REMOTE_OUT / "0bae3b5e_a030_clean_far_db79_board.jpg"]
        ims = [Image.open(p) for p in ps if p.exists()]
        if ims:
            w = max(i.width for i in ims); bd = Image.new("RGB", (w, sum(i.height for i in ims) + 20), (8, 8, 12)); yo = 10
            for im in ims: bd.paste(im, (0, yo)); yo += im.height
            bd.save(REMOTE_OUT / "DB79_review_board.jpg", quality=88)
    except Exception: pass
    OUT["status"] = "db79_step12_completed"; OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "db79_step12_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB79_JSON_BEGIN"); print(json.dumps(json_safe(OUT), separators=(",", ":"))); print("DB79_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT).replace("__TH__", json.dumps(TH))


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db79_remote>', 'exec'))\nPY"


def poll_job(client, job_id, timeout_s):
    t0 = time.time(); last = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(7); last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running": return sanitize(last)
    return sanitize(last or {"state": "poll_timeout"})


def run_remote(timeout_s: int) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient(); status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_db79_python())], "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s)
    remote_result = None
    raw = client.read_file(RESULT, max_size_mb=12)
    if raw is not None:
        try: remote_result = json.loads(raw.decode("utf-8"))
        except Exception: remote_result = None
    if remote_result is None:
        rr = job.get("log_tail", "")
        if "DB79_JSON_BEGIN" in rr:
            try: remote_result = json.loads(rr.split("DB79_JSON_BEGIN", 1)[1].split("DB79_JSON_END", 1)[0].strip())
            except Exception: remote_result = None
    if remote_result is None: remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    (OUT_DIR / "DB79_remote_result.json").write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")
    fetched = {}
    for key, (rn, mb) in FETCH.items():
        local = (OUT_DIR if key in {"summary", "result", "board"} else (OUT_DIR / "fetch")) / rn
        local.parent.mkdir(parents=True, exist_ok=True)
        rb = client.read_file(REMOTE_OUT + "/" + rn, max_size_mb=mb)
        if rb is None: fetched[key] = {"path": rel(local), "exists": False}
        else: local.write_bytes(rb); fetched[key] = {"path": rel(local), "exists": True, "bytes": len(rb)}
    summary = {}
    sp = OUT_DIR / "DB79_summary.json"
    if sp.exists():
        try: summary = json.loads(sp.read_text(encoding="utf-8"))
        except Exception: summary = {}
    if not summary: summary = remote_result.get("summary") or {}
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "status": "db79_fair_metric_wall_step12",
        "brief": "DB-79 fair-metric wall settlement (layered/LDI hold-out + LiDAR-only densify + surface/silhouette split)",
        "scope": {"measurement_only": True, "rgb_repair": False, "generation": False, "a100": False, "exec_count": 1, "runtime_type": status.get("runtime_type")},
        "runtime": {"secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file", "status": safe_status(status)},
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail", "cmd"}}),
        "remote_status": remote_result.get("status"), "summary": summary, "fetched_outputs": fetched,
        "pre_registered": TH, "output_location": rel(OUT_DIR), "drive_output_location": "results/db79_fair_metric_wall/",
        "decision": {"vision_check_required": True, "note": "a lower number with a visibly smeared curb/wall is still a FAIL"},
    }
    scan = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan); manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    (OUT_DIR / "DB79_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "DB79_prereg_thresholds.json").write_text(json.dumps(TH, indent=2), encoding="utf-8")
    return {"status": "db79_step12", "remote_status": remote_result.get("status"), "secret_hits": manifest["strict_secret_scan"]["hit_count"], "verdict": (summary.get("verdict") or {}).get("call"), "manifest": rel(OUT_DIR / "DB79_manifest.json")}


def check_compile() -> None:
    src = remote_db79_python()
    py_compile.compile(__file__, doraise=True)
    compile(src, "<db79_remote>", "exec")
    print("OK db79 local + remote source compile; remote bytes:", len(src))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run-remote", action="store_true")
    ap.add_argument("--timeout-s", type=int, default=900)
    a = ap.parse_args()
    if a.check: check_compile(); return
    if a.run_remote: print(json.dumps(run_remote(a.timeout_s), indent=2)); return
    ap.print_help()


if __name__ == "__main__":
    main()
