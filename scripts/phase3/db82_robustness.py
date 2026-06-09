"""DB-82: robustness & graceful-degradation battery for the cen_depth+B1 base.

Per log x 3 anchors, render:
  ego_rot       — legacy rotation-only at ego origin (baseline)
  cen_plane_b1  — NO-LiDAR ablation: Zd = ground-plane + far-field only, B1 gains applied
                  (gains from the anchor-0 LiDAR pairs = per-vehicle calibration constant)
  cen_depth_b1  — full pipeline (DB-80 + DB-81)
Auto-stats per render: black fraction, source-boundary density, |cen_depth - cen_plane| where valid.
Boards per log (3 anchors x 3 variants + ROIs). One bounded /exec, CPU. No generation.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db82_robustness"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db82_robustness"
RESULT = REMOTE_OUT + "/DB82_remote_result.json"

LOG_TAGS = ["02a00399_bmw", "0bae3b5e_clean", "2c652f9e_highway", "9f871fb4_downtown", "fbee355f_crowd"]


def remote_py() -> str:
    code = r'''
import json, math, pathlib, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
H, W = 1024, 2048; EPS = 1e-6
LOGS = [("02a00399", "02a00399_bmw"), ("0bae3b5e", "0bae3b5e_clean"),
        ("2c652f9e-8db8-3572-aa49-fae1344a875b", "2c652f9e_highway"),
        ("9f871fb4-3b8e-34b3-9161-ed961e71a6da", "9f871fb4_downtown"),
        ("fbee355f-8878-31fa-8ac8-b9a45a3f130a", "fbee355f_crowd")]
ANCHOR_REQ = [0, 30, 60]
WINDOW = 10; DMIN, DMAX = 1.5, 80.0; STATIC_DISP_M = 0.5; SAT_LO, SAT_HI = 10, 245
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}
OUT = {"phase": "db82_robustness", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"render_and_stats_only": True, "generation": False, "a100": False}}
ROIS = {"left": (250, 500, 560, 720), "center": (900, 500, 1210, 720), "right": (1350, 500, 1660, 720)}

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


def load_loader(short):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    s, log_dir, _a, _t = _parse_case(short + ":0:x", DATA_ROOT)
    return AV2RingLoader(log_dir), log_dir, list(RING_CAMS_7)


def pose_interp(log_dir):
    import pandas as pd
    from scipy.spatial.transform import Rotation, Slerp
    p = pd.read_feather(log_dir / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
    ti = p["timestamp_ns"].to_numpy(np.int64); t0 = int(ti[0]); tss = (ti - t0).astype(np.float64)
    quat = p[["qx", "qy", "qz", "qw"]].to_numpy(); tx = p[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
    keep = np.concatenate([[True], np.diff(tss) > 0]); tss, quat, tx = tss[keep], quat[keep], tx[keep]
    slerp = Slerp(tss, Rotation.from_quat(quat)); lo, hi = tss.min(), tss.max()
    def cte(t):
        tc = float(np.clip(float(int(t) - t0), lo, hi)); return slerp(tc).as_matrix(), np.array([np.interp(tc, tss, tx[:, i]) for i in range(3)])
    def tri(ta):
        tc = np.clip((np.asarray(ta, np.int64) - t0).astype(np.float64), lo, hi); return np.stack([np.interp(tc, tss, tx[:, i]) for i in range(3)], 1)
    return cte, tri


def moving_tracks(ann, t_lo, t_hi):
    if ann is None or "track_uuid" not in ann.columns: return set()
    sub = ann[(ann["timestamp_ns"] >= t_lo) & (ann["timestamp_ns"] <= t_hi)]
    mv = set()
    for uid, g in sub.groupby("track_uuid"):
        if "category" in g.columns and str(g["category"].iloc[0]).upper() not in DYN_CATS: continue
        c = g[["tx_m", "ty_m", "tz_m"]].to_numpy(float)
        if len(c) >= 2 and float(np.linalg.norm(c.max(0) - c.min(0))) > STATIC_DISP_M: mv.add(uid)
    return mv


def boxes_at(ann, ts, moving):
    from scipy.spatial.transform import Rotation
    if ann is None: return []
    tss = ann["timestamp_ns"].to_numpy(np.int64); nt = np.unique(tss)[np.argmin(np.abs(np.unique(tss) - ts))]
    out = []
    for _, r in ann[ann["timestamp_ns"] == nt].iterrows():
        if "category" in ann.columns and str(r["category"]).upper() not in DYN_CATS: continue
        if "track_uuid" in ann.columns and r["track_uuid"] not in moving: continue
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
        keep = remove_dyn(xyz, boxes_at(ann, sts, moving)); xyz, off = xyz[keep], off[keep]
        Rsw, _ = cte(sts); city = (Rsw @ xyz.T).T + tri((sts + off).astype(np.int64))
        acc.append((city - ta) @ Ra)
    return np.concatenate(acc, 0) if acc else np.zeros((0, 3))


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


def depth_field(lidar, C, use_lidar=True):
    from scipy.ndimage import distance_transform_edt
    dz = DIRS[:, :, 2]
    plane_t = np.where(dz < -0.05, (-C[2] - 0.33) / np.minimum(dz, -1e-3), np.inf).astype(np.float32)
    if not use_lidar:
        Zf = np.where(np.isfinite(plane_t) & (plane_t > DMIN) & (plane_t < DMAX), plane_t, 200.0)
        return Zf.astype(np.float32)
    Q = lidar - C[None, :]; n = np.linalg.norm(Q, axis=1)
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
    use_plane = (dist_px > 12) & np.isfinite(plane_t) & (plane_t > DMIN) & (plane_t < DMAX)
    Zf = np.where(use_plane, plane_t, Zf)
    return np.where(Zf <= 0, 200.0, Zf)


def render(frame, ring_cams, C, Zd, gains=None):
    import cv2
    X = C[None, None, :] + Zd[:, :, None].astype(np.float64) * DIRS
    best = np.full((H, W), np.inf, np.float32)
    out = np.zeros((H, W, 3), np.uint8)
    chosen = np.full((H, W), -1, np.int8)
    for ci, cam in enumerate(ring_cams):
        cal = frame.calibrations[cam]; K = np.asarray(cal.K, float); T = np.asarray(cal.T_ego_cam, float)
        Tci = np.linalg.inv(T); img = frame.images[cam]
        if gains is not None:
            img = np.clip(img.astype(np.float32) * np.exp(gains[ci])[None, None, :].astype(np.float32), 0, 255).astype(np.uint8)
        hh, ww = img.shape[:2]
        Xf = X.reshape(-1, 3)
        Xc = (Tci[:3, :3] @ Xf.T).T + Tci[:3, 3]; z = Xc[:, 2]
        px = (K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]).astype(np.float32)
        py = (K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]).astype(np.float32)
        ok = (z > 0.1) & (px >= 1) & (px < ww - 1) & (py >= 1) & (py < hh - 1)
        cvec = T[:3, 3] - C; df = DIRS.reshape(-1, 3); along = df @ cvec
        bperp = np.sqrt(np.maximum(float(cvec @ cvec) - along * along, 0.0)).astype(np.float32)
        score = np.where(ok, bperp, np.inf)
        upd = score < best.reshape(-1)
        if not upd.any(): continue
        mapx = np.where(upd, px, 0).reshape(H, W); mapy = np.where(upd, py, 0).reshape(H, W)
        col = cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR)
        u2 = upd.reshape(H, W); out[u2] = col[u2]
        be = best.reshape(-1); be[upd] = score[upd]; best = be.reshape(H, W)
        ch = chosen.reshape(-1); ch[upd] = ci; chosen = ch.reshape(H, W)
    return out, chosen


def stats_of(im, chosen):
    black = float((im.max(2) == 0).mean())
    bnd = float((np.abs(np.diff(chosen.astype(np.int16), axis=1)) > 0).mean())
    return {"black_frac": round(black, 4), "boundary_density": round(bnd, 5)}


def run_log(short, tag):
    from PIL import Image, ImageDraw, ImageFont
    import pandas as pd
    loader, log_dir, ring_cams = load_loader(short)
    cte, tri = pose_interp(log_dir)
    ann = pd.read_feather(log_dir / "annotations.feather") if (log_dir / "annotations.feather").exists() else None
    all_ts = loader.anchor_timestamps_ns()
    anchors = sorted(set(min(a, len(all_ts) - 1) for a in ANCHOR_REQ))
    try: f = ImageFont.truetype("DejaVuSans.ttf", 14)
    except Exception: f = ImageFont.load_default()
    rows_im = []; stats = {}
    gains0 = None
    for ai in anchors:
        ts = all_ts[ai]; frame = loader.load_synced_frame(ts)
        cents = np.stack([np.asarray(frame.calibrations[c].T_ego_cam, float)[:3, 3] for c in ring_cams], 0)
        C = cents.mean(axis=0)
        lidar = accumulate_lidar(log_dir, ts, cte, tri, ann)
        if gains0 is None:
            gains0 = solve_gains_for(frame, ring_cams, lidar, C)   # per-vehicle calibration constant (anchor-0)
        ego, ch_e = render(frame, ring_cams, np.zeros(3), np.full((H, W), 500.0, np.float32), gains=None)
        Zp = depth_field(lidar, C, use_lidar=False)
        plane, ch_p = render(frame, ring_cams, C, Zp, gains=gains0)
        Zd = depth_field(lidar, C, use_lidar=True)
        dep, ch_d = render(frame, ring_cams, C, Zd, gains=gains0)
        both = (dep.max(2) > 0) & (plane.max(2) > 0)
        dpd = float(np.abs(dep.astype(np.float32) - plane.astype(np.float32)).mean(2)[both].mean()) if both.any() else None
        stats[f"a{ai:03d}"] = {"ego_rot": stats_of(ego, ch_e), "cen_plane_b1": stats_of(plane, ch_p),
                               "cen_depth_b1": stats_of(dep, ch_d), "mean_absdiff_depth_vs_plane": round(dpd, 2) if dpd else None}
        for vtag, im in (("ego_rot", ego), ("cen_plane_b1", plane), ("cen_depth_b1", dep)):
            pil = Image.fromarray(im).resize((900, 450))
            bar = Image.new("RGB", (900, 20), (15, 15, 22)); ImageDraw.Draw(bar).text((4, 3), f"{tag} a{ai:03d} {vtag}", (235, 235, 245), font=f)
            o = Image.new("RGB", (900, 470)); o.paste(bar, (0, 0)); o.paste(pil, (0, 20)); rows_im.append(o)
        if ai == anchors[0]:
            save_rgb(REMOTE_OUT / f"{tag}_a{ai:03d}_cen_plane_b1.png", plane)
    cols = 3
    nr = (len(rows_im) + cols - 1) // cols
    board = Image.new("RGB", (900 * cols, 470 * nr), (8, 8, 12))
    for i, o in enumerate(rows_im): board.paste(o, ((i % cols) * 900, (i // cols) * 470))
    board.save(REMOTE_OUT / f"{tag}_db82_board.jpg", quality=88)
    return {"log": tag, "anchors": [int(a) for a in anchors], "stats": stats}


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
    reports = [run_log(s, t) for s, t in LOGS]
    OUT["status"] = "db82_completed"; OUT["logs"] = json_safe(reports); OUT["runtime_s"] = round(time.time() - t0, 2)
    (REMOTE_OUT / "DB82_summary.json").write_text(json.dumps(json_safe({"by_log": reports})), encoding="utf-8")
except Exception as exc:
    OUT["status"] = "db82_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB82_JSON_BEGIN"); print(json.dumps(json_safe(OUT), separators=(",", ":"))); print("DB82_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT)


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db82_remote>', 'exec'))\nPY"


def poll_job(client, job_id, timeout_s):
    t0 = time.time(); last = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(8); last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running": return sanitize(last)
    return sanitize(last or {"state": "poll_timeout"})


def run_remote(timeout_s: int = 3000) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient(); status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_py())], "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s)
    fetched = {}
    names = ["DB82_remote_result.json", "DB82_summary.json"]
    for t in LOG_TAGS:
        names += [f"{t}_db82_board.jpg", f"{t}_a000_cen_plane_b1.png"]
    for fname in names:
        raw = client.read_file(REMOTE_OUT + "/" + fname, max_size_mb=95)
        if raw is not None:
            (OUT_DIR / fname).write_bytes(raw); fetched[fname] = True
    report = {"job_state": job.get("state"), "n_fetched": len(fetched), "fetched": sorted(fetched),
              "runtime_status": {k: status.get(k) for k in ("runtime_type", "gpu_name", "active_jobs") if k in status}}
    report["secret_hits"] = secret_hits(json.dumps(report))
    return report


if __name__ == "__main__":
    rep = run_remote()
    out = Path.home() / ".waymo2panorama" / "db82_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
