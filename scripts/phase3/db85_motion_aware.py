"""DB-85: motion-aware object rendering on the centroid base (CPU/L4, no generation).

For each MOVING annotated track (window displacement > 0.5 m):
  - interpolate its box pose (centre linear, yaw from nearest label) to EACH ring camera's
    actual exposure timestamp at the anchor synced frame (AV2 cams are offset up to +-22.5 ms);
  - footprint_i = ray-OBB projection of box@t_i into the centroid ERP;
  - choose c_obj = camera whose own-exposure-time box centre projects in-FOV with min b_perp
    and maximal corner-visibility;
  - inside U footprint_i: source locked to c_obj (others heavily penalized but finite);
    depth inside footprint_{c_obj} = ray-OBB entry depth of box@t_{c_obj}; elsewhere Zd.
Static scene path identical to DB-80/81 (cen_depth_b1). Renders 3 scenes, A/B boards.
One bounded /exec; results fetched + Read-verified.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db85_motion_aware"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db85_motion_aware"
RESULT = REMOTE_OUT + "/DB85_remote_result.json"

CASE_NAMES = ["02a00399_a000_bmw", "9f871fb4_a030_downtown", "fbee355f_a030_crowd"]


def remote_py() -> str:
    code = r'''
import json, math, pathlib, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"),
         ("9f871fb4-3b8e-34b3-9161-ed961e71a6da:30:downtown", "9f871fb4_a030_downtown"),
         ("fbee355f-8878-31fa-8ac8-b9a45a3f130a:30:crowd", "fbee355f_a030_crowd")]
WINDOW = 10; DMIN, DMAX = 1.5, 80.0; STATIC_DISP_M = 0.5; SAT_LO, SAT_HI = 10, 245
OBJ_MAX_DIST = 40.0
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}
OUT = {"phase": "db85_motion_aware", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"annotations_only": True, "generation": False, "a100": False}}

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
    loader = AV2RingLoader(log_dir); all_ts = loader.anchor_timestamps_ns()
    globals()["LOADER"] = loader; globals()["ALL_TS"] = all_ts; globals()["ANCHOR_IDX"] = anchor_idx
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
    # per-camera EXPOSURE timestamps nearest to the anchor synced time
    cam_ts = {}
    for cam in RING_CAMS_7:
        files = sorted(int(p.stem) for p in (log_dir / "sensors" / "cameras" / cam).glob("*.jpg"))
        arr = np.asarray(files, np.int64)
        cam_ts[cam] = int(arr[np.argmin(np.abs(arr - ts))])
    return log_dir, ts, frame, list(RING_CAMS_7), cte, tri, ann, cam_ts


def moving_tracks(ann, t_lo, t_hi):
    if ann is None or "track_uuid" not in ann.columns: return set()
    sub = ann[(ann["timestamp_ns"] >= t_lo) & (ann["timestamp_ns"] <= t_hi)]
    mv = set()
    for uid, g in sub.groupby("track_uuid"):
        if "category" in g.columns and str(g["category"].iloc[0]).upper() not in DYN_CATS: continue
        c = g[["tx_m", "ty_m", "tz_m"]].to_numpy(float)
        if len(c) >= 2 and float(np.linalg.norm(c.max(0) - c.min(0))) > STATIC_DISP_M: mv.add(uid)
    return mv


def boxes_at(ann, ts, moving, only_moving=True):
    from scipy.spatial.transform import Rotation
    if ann is None: return []
    tss = ann["timestamp_ns"].to_numpy(np.int64); nt = np.unique(tss)[np.argmin(np.abs(np.unique(tss) - ts))]
    out = []
    for _, r in ann[ann["timestamp_ns"] == nt].iterrows():
        if "category" in ann.columns and str(r["category"]).upper() not in DYN_CATS: continue
        if only_moving and r["track_uuid"] not in moving: continue
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


def depth_field(lidar, C):
    from scipy.ndimage import distance_transform_edt
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
    dz = DIRS[:, :, 2]
    plane_t = np.where(dz < -0.05, (-C[2] - 0.33) / np.minimum(dz, -1e-3), np.inf).astype(np.float32)
    use_plane = (dist_px > 12) & np.isfinite(plane_t) & (plane_t > DMIN) & (plane_t < DMAX)
    Zf = np.where(use_plane, plane_t, Zf)
    return np.where(Zf <= 0, 200.0, Zf)


def track_pose_at(ann, uid, t_query, cte, anchor_R, anchor_t):
    """Interpolate track 'uid' box pose (CITY frame) to time t_query, return in ANCHOR ego coords."""
    from scipy.spatial.transform import Rotation
    g = ann[ann["track_uuid"] == uid].sort_values("timestamp_ns")
    tss = g["timestamp_ns"].to_numpy(np.int64)
    if len(tss) == 0: return None
    # convert each label pose to city frame
    centers_city = []
    Rs_city = []
    for _, r in g.iterrows():
        Re, te = cte(int(r["timestamp_ns"]))
        c_ego = np.array([r["tx_m"], r["ty_m"], r["tz_m"]])
        Rb = Rotation.from_quat([r["qx"], r["qy"], r["qz"], r["qw"]]).as_matrix()
        centers_city.append(Re @ c_ego + te)
        Rs_city.append(Re @ Rb)
    centers_city = np.stack(centers_city)
    t_rel = (tss - tss[0]).astype(np.float64)
    tq = float(np.clip(t_query - tss[0], t_rel.min(), t_rel.max()))
    c_q = np.array([np.interp(tq, t_rel, centers_city[:, i]) for i in range(3)])
    ni = int(np.argmin(np.abs(t_rel - tq)))
    R_q = Rs_city[ni]
    sz = g[["length_m", "width_m", "height_m"]].iloc[ni].to_numpy(float)
    # to anchor ego coords
    c_a = anchor_R.T @ (c_q - anchor_t)
    R_a = anchor_R.T @ R_q
    return c_a, sz, R_a


def ray_obb_region(c, sz, Rb, C, pad=1.06):
    """Pixels (flat indices) whose centroid ray hits box, + entry depth."""
    half = sz / 2 * pad
    corners = np.array([[sx * half[0], sy * half[1], sz_ * half[2]]
                        for sx in (-1, 1) for sy in (-1, 1) for sz_ in (-1, 1)])
    P = (corners @ Rb.T) + c[None, :]
    Q = P - C[None, :]
    n = np.linalg.norm(Q, axis=1); d = Q / n[:, None]
    theta = np.arctan2(d[:, 1], d[:, 0]); phi = np.arcsin(np.clip(d[:, 2], -1, 1))
    u = (np.pi - theta) / (2 * np.pi) * W - 0.5
    v = (np.pi / 2 - phi) / np.pi * H - 0.5
    v0 = max(int(np.floor(v.min())) - 1, 0); v1 = min(int(np.ceil(v.max())) + 1, H - 1)
    us = np.sort(u % W)
    gaps = np.diff(np.concatenate([us, us[:1] + W]))
    gi = int(np.argmax(gaps))
    u_start = us[(gi + 1) % len(us)]
    width = (us[gi] - u_start) % W if len(us) > 1 else 0
    cols = (np.arange(int(np.floor(u_start)) - 1, int(np.floor(u_start)) + int(np.ceil(width)) + 2)) % W
    rows = np.arange(v0, v1 + 1)
    if len(cols) == 0 or len(rows) == 0: return np.zeros(0, np.int64), np.zeros(0, np.float32)
    sub = DIRS[np.ix_(rows, cols)].reshape(-1, 3)
    o_loc = Rb.T @ (C - c)
    d_loc = sub @ Rb
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / d_loc
        t1 = (-half[None, :] - o_loc[None, :]) * inv
        t2 = (half[None, :] - o_loc[None, :]) * inv
    tmin = np.nanmax(np.minimum(t1, t2), axis=1)
    tmax = np.nanmin(np.maximum(t1, t2), axis=1)
    hit = (tmax >= np.maximum(tmin, 0.0)) & (tmax > 0)
    tent = np.where(tmin > 0, tmin, tmax).astype(np.float32)
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    flat = (rr.reshape(-1) * W + cc.reshape(-1))
    return flat[hit], tent[hit]


def render(frame, ring_cams, C, Zd, gains, mov_lock=None):
    """mov_lock: dict with 'lockmap' (HxW int16, -1 none, else obj id), 'lockcam' (per obj id),
    'objdepth' (HxW float32: box-surface depth valid where lockmap>=0 and pixel in c_obj's own
    exposure-time footprint, else 0 -> use Zd)."""
    import cv2
    Zuse = Zd
    if mov_lock is not None:
        od = mov_lock["objdepth"]
        Zuse = np.where(od > 0, od, Zd)
    X = C[None, None, :] + Zuse[:, :, None].astype(np.float64) * DIRS
    best = np.full((H, W), np.inf, np.float32)
    out = np.zeros((H, W, 3), np.uint8)
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
        if mov_lock is not None:
            om = mov_lock["lockmap"].reshape(-1)
            lc = mov_lock["lockcam"]
            has = om >= 0
            mine = has & (lc[np.maximum(om, 0)] == ci)
            score = np.where(has & ~mine & ok, score + 50.0, score)
            score = np.where(mine & ok, 0.0, score)
        upd = score < best.reshape(-1)
        if not upd.any(): continue
        mapx = np.where(upd, px, 0).reshape(H, W); mapy = np.where(upd, py, 0).reshape(H, W)
        col = cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR)
        u2 = upd.reshape(H, W); out[u2] = col[u2]
        be = best.reshape(-1); be[upd] = score[upd]; best = be.reshape(H, W)
    return out


def run_case(case_spec, run_name):
    from PIL import Image, ImageDraw, ImageFont
    log_dir, ts, frame, ring_cams, cte, tri, ann, cam_ts = load_all(case_spec)
    cents = np.stack([np.asarray(frame.calibrations[c].T_ego_cam, float)[:3, 3] for c in ring_cams], 0)
    C = cents.mean(axis=0)
    lidar, (Ra, ta), moving = accumulate_lidar(log_dir, ts, cte, tri, ann)
    gains = solve_gains_for(frame, ring_cams, lidar, C)
    Zd = depth_field(lidar, C)
    base = render(frame, ring_cams, C, Zd, gains)
    # build motion-aware lock structures
    lockmap = np.full(H * W, -1, np.int16)
    objdepth = np.zeros(H * W, np.float32)
    lockcam = []
    n_handled = 0
    # moving tracks present at anchor (nearest label within 150 ms) and near enough
    tss_all = ann["timestamp_ns"].to_numpy(np.int64) if ann is not None else np.zeros(0, np.int64)
    for uid in sorted(moving):
        g = ann[ann["track_uuid"] == uid]
        nt = g["timestamp_ns"].to_numpy(np.int64)
        if np.abs(nt - ts).min() > 150_000_000: continue
        # per-camera exposure-time footprints
        pads = []
        oid = len(lockcam)
        best_ci, best_score = -1, -1.0
        per_cam = {}
        for ci, cam in enumerate(ring_cams):
            pose = track_pose_at(ann, uid, cam_ts[cam], cte, Ra, ta)
            if pose is None: continue
            c_a, sz, R_a = pose
            dist = float(np.linalg.norm(c_a - C))
            if dist > OBJ_MAX_DIST or dist < 1.0: continue
            flat, tent = ray_obb_region(c_a, sz, R_a, C)
            if flat.size == 0: continue
            per_cam[ci] = (flat, tent)
            # visibility score for choosing c_obj: fraction of box-centre projection validity + low b_perp
            cal = frame.calibrations[cam]; K = np.asarray(cal.K, float); T = np.asarray(cal.T_ego_cam, float)
            Tci = np.linalg.inv(T)
            Xc = Tci[:3, :3] @ c_a + Tci[:3, 3]
            if Xc[2] <= 0.3: continue
            hh, ww = frame.images[cam].shape[:2]
            px = K[0, 0] * Xc[0] / Xc[2] + K[0, 2]; py = K[1, 1] * Xc[1] / Xc[2] + K[1, 2]
            if not (0 <= px < ww and 0 <= py < hh): continue
            margin = min(px, ww - px) / ww  # how central horizontally
            if margin > best_score:
                best_score = margin; best_ci = ci
        if best_ci < 0 or not per_cam: continue
        n_handled += 1
        lockcam.append(best_ci)
        for ci, (flat, tent) in per_cam.items():
            lockmap[flat] = oid
            if ci == best_ci:
                objdepth[flat] = tent
        # ensure c_obj's own footprint depth wins (write after union loop)
        flat_b, tent_b = per_cam.get(best_ci, (np.zeros(0, np.int64), np.zeros(0, np.float32)))
        objdepth[flat_b] = tent_b
    mov_lock = {"lockmap": lockmap.reshape(H, W), "lockcam": np.array(lockcam, np.int8) if lockcam else np.zeros(0, np.int8),
                "objdepth": objdepth.reshape(H, W)}
    fixed = render(frame, ring_cams, C, Zd, gains, mov_lock=mov_lock if lockcam else None)
    # --- TEMPORAL FILL of the moving-object disocclusion penumbra (DB-84 machinery) ---
    # zone = union footprint minus the chosen camera's own exposure-time footprint:
    # there the chosen camera's sightline crosses its own image of the car -> ghost.
    # The car MOVES AWAY within a few frames, so other timestamps see the true background.
    n_filled = 0
    if lockcam:
        zone_flat = np.nonzero((lockmap >= 0) & (objdepth == 0))[0]
        if zone_flat.size:
            zdirs = DIRS.reshape(-1, 3)[zone_flat]
            Zv = Zd.reshape(-1)[zone_flat].astype(np.float64)
            Xz = C[None, :] + Zv[:, None] * zdirs
            X_city = (Ra @ Xz.T).T + ta
            lo_i = max(0, ANCHOR_IDX - 10); hi_i = min(len(ALL_TS) - 1, ANCHOR_IDX + 10)
            chosen = np.full(zone_flat.size, -1, np.int32)
            chosen_bp = np.full(zone_flat.size, np.inf)
            cals = [(np.asarray(frame.calibrations[c].K, float), np.asarray(frame.calibrations[c].T_ego_cam, float),
                     frame.images[c].shape[:2]) for c in ring_cams]
            def seg_blocked(o, Xq, boxes_q, lo_t=0.02, hi_t=0.97):
                outb = np.zeros(len(Xq), bool)
                for c2, sz2, R2 in boxes_q:
                    half2 = sz2 / 2 * 1.0
                    o_loc = R2.T @ (o - c2)
                    d_loc = (Xq - o[None, :]) @ R2
                    with np.errstate(divide="ignore", invalid="ignore"):
                        inv = 1.0 / d_loc
                        t1 = (-half2[None, :] - o_loc[None, :]) * inv
                        t2 = (half2[None, :] - o_loc[None, :]) * inv
                    tmin = np.nanmax(np.minimum(t1, t2), axis=1)
                    tmax = np.nanmin(np.maximum(t1, t2), axis=1)
                    outb |= (tmax >= np.maximum(tmin, 0.0)) & (tmin < hi_t) & (tmin > lo_t)
                return outb
            for fi in range(lo_i, hi_i + 1):
                if fi == ANCHOR_IDX: continue
                tsf = ALL_TS[fi]
                Rf, tf = cte(tsf)
                Xf = (X_city - tf[None, :]) @ Rf
                fboxes = boxes_at(ann, tsf, moving, only_moving=False)
                for ci, (K, T, (hh, ww)) in enumerate(cals):
                    Tci = np.linalg.inv(T)
                    Xc = (Tci[:3, :3] @ Xf.T).T + Tci[:3, 3]; z = Xc[:, 2]
                    px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
                    okq = (z > 0.5) & (px >= 2) & (px < ww - 2) & (py >= 2) & (py < hh - 2)
                    if not okq.any(): continue
                    blocked = np.zeros(zone_flat.size, bool)
                    blocked[okq] = seg_blocked(T[:3, 3], Xf[okq], fboxes)
                    visq = okq & ~blocked
                    if not visq.any(): continue
                    cam_city = Rf @ T[:3, 3] + tf
                    cam_anchor = Ra.T @ (cam_city - ta)
                    cvec2 = cam_anchor - C
                    along2 = zdirs @ cvec2
                    bp2 = np.sqrt(np.maximum(float(cvec2 @ cvec2) - along2 * along2, 0.0))
                    cand = visq & (bp2 < chosen_bp)
                    chosen[cand] = fi * 10 + ci
                    chosen_bp[cand] = bp2[cand]
            ffix = fixed.reshape(-1, 3)
            for code in np.unique(chosen[chosen >= 0]):
                fi, ci = int(code) // 10, int(code) % 10
                sel = chosen == code
                fr2 = LOADER.load_synced_frame(ALL_TS[fi])
                Rf, tf = cte(ALL_TS[fi])
                Xf = (X_city[sel] - tf[None, :]) @ Rf
                K, T, (hh, ww) = cals[ci]
                Tci = np.linalg.inv(T)
                Xc = (Tci[:3, :3] @ Xf.T).T + Tci[:3, 3]; z = Xc[:, 2]
                px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
                img2 = fr2.images[ring_cams[ci]]
                g2 = np.exp(gains[ci])[None, :]
                col = np.clip(bilinear(img2, px, py) * g2, 0, 255)
                ffix[zone_flat[sel]] = col.astype(np.uint8)
                n_filled += int(sel.sum())
    save_rgb(REMOTE_OUT / f"{run_name}_motionaware.png", fixed)
    save_rgb(REMOTE_OUT / f"{run_name}_base.png", base)
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    rows = []
    for tag, im in (("cen_depth_b1 (base)", base), (f"motion-aware+temporal (n_mov={n_handled} n_filled_px={n_filled})", fixed)):
        pil = Image.fromarray(im).resize((1400, 700))
        bar = Image.new("RGB", (1400, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), f"{run_name}  {tag}", (235, 235, 245), font=f)
        o = Image.new("RGB", (1400, 724)); o.paste(bar, (0, 0)); o.paste(pil, (0, 24)); rows.append(o)
    board = Image.new("RGB", (1400, 724 * 2 + 12), (8, 8, 12))
    yo = 6
    for o in rows: board.paste(o, (0, yo)); yo += o.height
    board.save(REMOTE_OUT / f"{run_name}_db85_board.jpg", quality=90)
    return {"case": run_name, "n_moving_handled": int(n_handled), "n_temporal_filled_px": int(n_filled)}


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    reports = [run_case(cs, rn) for cs, rn in CASES]
    OUT["status"] = "db85_completed"; OUT["cases"] = reports; OUT["runtime_s"] = round(time.time() - t0, 2)
except Exception as exc:
    OUT["status"] = "db85_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print("DB85_JSON_BEGIN"); print(json.dumps(OUT, separators=(",", ":"))); print("DB85_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT)


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db85_remote>', 'exec'))\nPY"


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
    names = ["DB85_remote_result.json"]
    for n in CASE_NAMES:
        names += [f"{n}_db85_board.jpg", f"{n}_motionaware.png", f"{n}_base.png"]
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
    out = Path.home() / ".waymo2panorama" / "db85_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
