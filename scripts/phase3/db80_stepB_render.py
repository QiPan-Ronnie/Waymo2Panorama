"""DB-80 Step B: full-ERP depth-aware render at c* = ring-camera centroid (CPU/L4, no A100).

Renders three panoramas per case for the vision gate:
  1. ego_rot      — rotation-only at ego origin (the legacy L1 hard_select geometry; baseline)
  2. cen_rot      — rotation-only at ring centroid (isolates the composition change from depth)
  3. cen_depth    — depth-aware at ring centroid: X = C + Zd(d)*d projected into the
                    min-perpendicular-baseline camera (single source, never averaged)
Depth field Zd: multi-frame LiDAR accumulated to the anchor (STATIC-aware: boxes whose track
moves <0.5 m inside the window are KEPT, so parked vehicles keep their LiDAR), splatted
near-wins into the centroid ERP (near-wins IS the correct z-buffer semantics for rendering),
EDT-filled, then ground-plane fill below the horizon where EDT support is far.
No generation, no learning, no averaging. v0 limitation (logged): no per-camera occlusion
z-buffer — acceptable because the min-b_perp source is near-collinear with the ERP ray.
Self-orchestrating (db79/db80 pattern): ONE bounded /exec, fetch, caller Read-verifies.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db80_virtual_centre"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db80_virtual_centre"
RESULT = REMOTE_OUT + "/DB80B_remote_result.json"

FETCH = {
    "result": ("DB80B_remote_result.json", 8),
    "bmw_board": ("02a00399_a000_bmw_db80B_board.jpg", 95),
    "clean_board": ("0bae3b5e_a030_clean_far_db80B_board.jpg", 95),
    "bmw_ego": ("02a00399_a000_bmw_ego_rot.png", 95),
    "bmw_cenrot": ("02a00399_a000_bmw_cen_rot.png", 95),
    "bmw_cendep": ("02a00399_a000_bmw_cen_depth.png", 95),
    "clean_ego": ("0bae3b5e_a030_clean_far_ego_rot.png", 95),
    "clean_cenrot": ("0bae3b5e_a030_clean_far_cen_rot.png", 95),
    "clean_cendep": ("0bae3b5e_a030_clean_far_cen_depth.png", 95),
}


def remote_py() -> str:
    code = r'''
import json, math, pathlib, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"), ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far")]
WINDOW = 10; DMIN, DMAX = 1.5, 80.0
STATIC_DISP_M = 0.5
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}
OUT = {"phase": "db80_stepB_render", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"render_only": True, "generation": False, "averaging": False, "a100": False}}
ROIS = {"left_road": (250, 515, 460, 715), "lower_center": (740, 595, 1035, 745),
        "center_lane": (1030, 515, 1325, 735), "right_curbwall": (1300, 500, 1575, 760)}

sys.path.insert(0, "/content/waymo2panorama/scripts/phase3"); sys.path.insert(0, "/content/waymo2panorama/code")


def save_rgb(path, arr, q=92):
    import cv2
    a = np.clip(arr, 0, 255).astype("uint8")
    cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR))


def erp_dirs():
    u = np.arange(W); v = np.arange(H); uu, vv = np.meshgrid(u, v)
    theta = np.pi - (uu + 0.5) / W * 2 * np.pi; phi = np.pi / 2 - (vv + 0.5) / H * np.pi
    cph = np.cos(phi)
    return np.stack([cph * np.cos(theta), cph * np.sin(theta), np.sin(phi)], -1).astype(np.float64)


def load_all(case_spec):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    import pandas as pd
    from scipy.spatial.transform import Rotation, Slerp
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    loader = AV2RingLoader(log_dir); ts = loader.anchor_timestamps_ns()[anchor_idx]; frame = loader.load_synced_frame(ts)
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
    return log_dir, ts, frame, list(RING_CAMS_7), cte, tri, ann


def moving_tracks(ann, t_lo, t_hi):
    """track_uuids whose city-frame box centre moves > STATIC_DISP_M inside [t_lo, t_hi]."""
    import pandas as pd
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
        if "track_uuid" in ann.columns and r["track_uuid"] not in moving: continue   # keep static
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
    return np.concatenate(acc, 0) if acc else np.zeros((0, 3)), len(moving)


def depth_field(lidar, C):
    """Near-wins splat (correct z-buffer semantics for rendering) + EDT fill + ground-plane fill."""
    from scipy.ndimage import distance_transform_edt
    Q = lidar - C[None, :]
    n = np.linalg.norm(Q, axis=1)
    m = (n > DMIN) & (n < DMAX)
    Qm = Q[m]; nm = n[m]
    d = Qm / nm[:, None]
    theta = np.arctan2(d[:, 1], d[:, 0]); phi = np.arcsin(np.clip(d[:, 2], -1, 1))
    ui = np.clip(np.round((np.pi - theta) / (2 * np.pi) * W - 0.5).astype(np.int64), 0, W - 1)
    vi = np.clip(np.round((np.pi / 2 - phi) / np.pi * H - 0.5).astype(np.int64), 0, H - 1)
    Z = np.zeros((H, W), np.float32)
    order = np.argsort(-nm)   # far first, near overwrites = near-wins
    flat = vi * W + ui; zf = Z.reshape(-1); zf[flat[order]] = nm[order].astype(np.float32)
    valid = Z > 0
    dist_px, inds = distance_transform_edt(~valid, return_distances=True, return_indices=True)
    Zf = Z[inds[0], inds[1]].astype(np.float32)
    # ground-plane fill where EDT support is far and the ray points below horizon
    dirs = erp_dirs()
    dz = dirs[:, :, 2]
    plane_t = np.where(dz < -0.05, (-C[2] - 0.33) / np.minimum(dz, -1e-3), np.inf).astype(np.float32)  # ego ground ~ -0.33 m
    use_plane = (dist_px > 12) & np.isfinite(plane_t) & (plane_t > DMIN) & (plane_t < DMAX)
    Zf = np.where(use_plane, plane_t, Zf)
    Zf = np.where(Zf <= 0, 200.0, Zf)
    return Zf, valid


def render(frame, ring_cams, C, Zd):
    """Single-source ERP render: per pixel choose the min-b_perp camera whose projection is valid."""
    import cv2
    dirs = erp_dirs()
    X = C[None, None, :] + Zd[:, :, None].astype(np.float64) * dirs
    best_err = np.full((H, W), np.inf, np.float32)
    out = np.zeros((H, W, 3), np.uint8)
    chosen = np.full((H, W), -1, np.int8)
    for ci, cam in enumerate(ring_cams):
        cal = frame.calibrations[cam]; K = np.asarray(cal.K, float); T = np.asarray(cal.T_ego_cam, float)
        Tci = np.linalg.inv(T); img = frame.images[cam]; hh, ww = img.shape[:2]
        Xf = X.reshape(-1, 3)
        Xc = (Tci[:3, :3] @ Xf.T).T + Tci[:3, 3]
        z = Xc[:, 2]
        px = (K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]).astype(np.float32)
        py = (K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]).astype(np.float32)
        ok = (z > 0.1) & (px >= 1) & (px < ww - 1) & (py >= 1) & (py < hh - 1)
        cvec = T[:3, 3] - C
        df = dirs.reshape(-1, 3)
        along = df @ cvec
        bperp = np.sqrt(np.maximum(float(cvec @ cvec) - along * along, 0.0)).astype(np.float32)
        score = np.where(ok, bperp, np.inf)
        upd = score < best_err.reshape(-1)
        if not upd.any(): continue
        mapx = np.where(upd, px, 0).reshape(H, W); mapy = np.where(upd, py, 0).reshape(H, W)
        col = cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR)
        u2 = upd.reshape(H, W)
        out[u2] = col[u2]
        be = best_err.reshape(-1); be[upd] = score[upd]; best_err = be.reshape(H, W)
        ch = chosen.reshape(-1); ch[upd] = ci; chosen = ch.reshape(H, W)
    out[chosen < 0] = 0
    return out, chosen


def run_case(case_spec, run_name):
    from PIL import Image, ImageDraw, ImageFont
    log_dir, ts, frame, ring_cams, cte, tri, ann = load_all(case_spec)
    cents = np.stack([np.asarray(frame.calibrations[c].T_ego_cam, float)[:3, 3] for c in ring_cams], 0)
    C = cents.mean(axis=0)
    lidar, n_moving = accumulate_lidar(log_dir, ts, cte, tri, ann)
    info = {"n_lidar": int(len(lidar)), "n_moving_tracks_removed": int(n_moving), "centroid": C.tolist()}
    # three renders
    FARZ = np.full((H, W), 500.0, np.float32)
    ego_rot, _ = render(frame, ring_cams, np.zeros(3), FARZ)          # rotation-only at ego (legacy geometry)
    cen_rot, _ = render(frame, ring_cams, C, FARZ)                    # rotation-only at centroid
    Zd, lidar_valid = depth_field(lidar, C)
    cen_dep, chosen = render(frame, ring_cams, C, Zd)                 # depth-aware at centroid
    info["lidar_pixel_coverage"] = float(lidar_valid.mean())
    for tag, im in (("ego_rot", ego_rot), ("cen_rot", cen_rot), ("cen_depth", cen_dep)):
        save_rgb(REMOTE_OUT / f"{run_name}_{tag}.png", im)
    # board: 3 full ERPs + ROI strip (4 ROIs x 3 variants)
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    rows = []
    for tag, im in (("EGO rotation-only (legacy L1 geometry)", ego_rot), ("CENTROID rotation-only", cen_rot), ("CENTROID depth-aware (LiDAR Zd + min-b_perp single source)", cen_dep)):
        pil = Image.fromarray(im).resize((1400, 700))
        bar = Image.new("RGB", (1400, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), f"{run_name}  {tag}", (235, 235, 245), font=f)
        o = Image.new("RGB", (1400, 724)); o.paste(bar, (0, 0)); o.paste(pil, (0, 24)); rows.append(o)
    # ROI strip
    roi_tiles = []
    for rn, (x0, y0, x1, y1) in ROIS.items():
        for tag, im in (("ego", ego_rot), ("cen_rot", cen_rot), ("cen_dep", cen_dep)):
            crop = Image.fromarray(im[y0:y1, x0:x1]); crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
            bar = Image.new("RGB", (crop.width, 20), (25, 25, 32)); ImageDraw.Draw(bar).text((4, 3), f"{rn} | {tag}", (230, 230, 240), font=f)
            t = Image.new("RGB", (crop.width, crop.height + 20)); t.paste(bar, (0, 0)); t.paste(crop, (0, 20)); roi_tiles.append(t)
    rw = max(t.width for t in roi_tiles); cols = 3
    nrows = (len(roi_tiles) + cols - 1) // cols
    rh = max(t.height for t in roi_tiles)
    strip = Image.new("RGB", (rw * cols, rh * nrows), (8, 8, 12))
    for i, t in enumerate(roi_tiles): strip.paste(t, ((i % cols) * rw, (i // cols) * rh))
    bw = max(1400, strip.width)
    board = Image.new("RGB", (bw, 724 * 3 + strip.height + 30), (8, 8, 12))
    yo = 10
    for o in rows: board.paste(o, (0, yo)); yo += o.height
    board.paste(strip, (0, yo))
    board.save(REMOTE_OUT / f"{run_name}_db80B_board.jpg", quality=90)
    return {"case": run_name, "info": info}


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    reports = [run_case(cs, rn) for cs, rn in CASES]
    OUT["status"] = "db80_stepB_completed"; OUT["cases"] = reports; OUT["runtime_s"] = round(time.time() - t0, 2)
except Exception as exc:
    OUT["status"] = "db80_stepB_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print("DB80B_JSON_BEGIN"); print(json.dumps(OUT, separators=(",", ":"))); print("DB80B_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT)


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db80b_remote>', 'exec'))\nPY"


def poll_job(client, job_id, timeout_s):
    t0 = time.time(); last = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(7); last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running": return sanitize(last)
    return sanitize(last or {"state": "poll_timeout"})


def run_remote(timeout_s: int = 1800) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient(); status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_py())], "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s)
    fetched = {}
    for key, (fname, mb) in FETCH.items():
        raw = client.read_file(REMOTE_OUT + "/" + fname, max_size_mb=mb)
        if raw is not None:
            (OUT_DIR / fname).write_bytes(raw); fetched[key] = fname
    report = {"job_state": job.get("state"), "fetched": fetched,
              "runtime_status": {k: status.get(k) for k in ("runtime_type", "gpu_name", "active_jobs") if k in status}}
    report["secret_hits"] = secret_hits(json.dumps(report))
    return report


if __name__ == "__main__":
    rep = run_remote()
    out = Path.home() / ".waymo2panorama" / "db80b_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
