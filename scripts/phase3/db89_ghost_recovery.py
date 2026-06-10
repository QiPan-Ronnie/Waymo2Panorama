"""DB-89: ghost-zone temporal recovery — the hardened v7 GENERAL algorithm (L4 for YOLO only).

Four evidence-driven rules, zero scene parameters:
  1. STATIC world <- EMC render (per-camera exposure-time ego poses).
  2. OBJECT BODY <- one (Voronoi-dominant camera, its exposure time); extent = segmentation
     mask UNION own-time-box ray-hit; uniform object-distance projection.
  3. GHOST ZONE (union of the object's positions at EVERY camera's exposure time, minus body)
     <- temporal recovery under a TRIPLE gate: object provably departed (|dframe|>=3) AND
     padded-box-free sightline at that frame AND LiDAR-evidenced background depth.
  4. Gate fails -> keep the EMC pixel. No depth overwrites anywhere.
Sanity asserts closing DB-88 v7's infra failure: (a) skip cameras with |cam_ts-anchor|>=60ms;
(b) skip per-object box regions wider than 2x their expected angular size.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db89_ghost_recovery"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db89_ghost_recovery"
RESULT = REMOTE_OUT + "/DB89_remote_result.json"

CASE_NAMES = ["02a00399_a000_bmw"]


def remote_py() -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw")]
WINDOW = 10; DMIN, DMAX = 1.5, 80.0; STATIC_DISP_M = 0.5; SAT_LO, SAT_HI = 10, 245
OBJ_MAX_DIST = 40.0; IOU_MIN = 0.30
SEG_CLASSES = {1, 2, 3, 5, 7, 0}   # bicycle, car, motorcycle, bus, truck, person (COCO)
OUT = {"phase": "db89_ghost_recovery", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"segmentation_ownership_only": True, "generation": False}}

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
    cam_ts = {}
    for cam in RING_CAMS_7:
        files = sorted(int(p.stem) for p in (log_dir / "sensors" / "cameras" / cam).glob("*.jpg"))
        arr = np.asarray(files, np.int64)
        cam_ts[cam] = int(arr[np.argmin(np.abs(arr - ts))])
    return loader, log_dir, all_ts, anchor_idx, ts, frame, list(RING_CAMS_7), cte, tri, ann, cam_ts


def moving_tracks(ann, t_lo, t_hi):
    if ann is None or "track_uuid" not in ann.columns: return set()
    sub = ann[(ann["timestamp_ns"] >= t_lo) & (ann["timestamp_ns"] <= t_hi)]
    mv = set()
    for uid, g in sub.groupby("track_uuid"):
        if "category" in g.columns and str(g["category"].iloc[0]).upper() not in {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}: continue
        c = g[["tx_m", "ty_m", "tz_m"]].to_numpy(float)
        if len(c) >= 2 and float(np.linalg.norm(c.max(0) - c.min(0))) > STATIC_DISP_M: mv.add(uid)
    return mv


def boxes_at(ann, ts, moving):
    from scipy.spatial.transform import Rotation
    if ann is None: return []
    tss = ann["timestamp_ns"].to_numpy(np.int64); nt = np.unique(tss)[np.argmin(np.abs(np.unique(tss) - ts))]
    out = []
    for _, r in ann[ann["timestamp_ns"] == nt].iterrows():
        if r["track_uuid"] not in moving: continue
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
    return np.where(Zf <= 0, 200.0, Zf), dist_px.astype(np.float32)


def track_pose_at(ann, uid, t_query, cte, anchor_R, anchor_t):
    from scipy.spatial.transform import Rotation
    g = ann[ann["track_uuid"] == uid].sort_values("timestamp_ns")
    tss = g["timestamp_ns"].to_numpy(np.int64)
    if len(tss) == 0: return None
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
    c_a = anchor_R.T @ (c_q - anchor_t)
    R_a = anchor_R.T @ R_q
    return c_a, sz, R_a


def ray_obb_region(c, sz, Rb, C, pad=1.0):
    """Flat ERP indices whose centroid ray hits the box, + entry depth.
    SANITY (assert b): returns empty if the region is wider than 2x the box's expected
    angular size (guards against far-interpolated poses exploding the region)."""
    half = sz / 2 * pad
    dist = float(np.linalg.norm(c - C))
    if dist < 0.5: return np.zeros(0, np.int64), np.zeros(0, np.float32)
    expected_w_px = float(np.linalg.norm(sz)) / dist * (W / (2 * np.pi)) * 1.2
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
    if width > 2 * expected_w_px:                      # assert (b)
        return np.zeros(0, np.int64), np.zeros(0, np.float32)
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


def box_img_bbox(c_a, sz, R_a, K, Rc, tc, hh, ww):
    """Project box corners into a camera (EMC pose); return (x0,y0,x1,y1) or None."""
    half = sz / 2
    corners = np.array([[sx * half[0], sy * half[1], sz_ * half[2]]
                        for sx in (-1, 1) for sy in (-1, 1) for sz_ in (-1, 1)])
    P = (corners @ R_a.T) + c_a[None, :]
    Xc = (Rc.T @ (P - tc[None, :]).T).T
    if (Xc[:, 2] <= 0.2).all(): return None
    vis = Xc[:, 2] > 0.2
    px = K[0, 0] * Xc[vis, 0] / Xc[vis, 2] + K[0, 2]
    py = K[1, 1] * Xc[vis, 1] / Xc[vis, 2] + K[1, 2]
    x0, x1 = float(px.min()), float(px.max()); y0, y1 = float(py.min()), float(py.max())
    if x1 < 0 or x0 > ww or y1 < 0 or y0 > hh: return None
    return (max(x0, 0), max(y0, 0), min(x1, ww), min(y1, hh))


def iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1]); ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(ix1 - ix0, 0), max(iy1 - iy0, 0)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(ua, 1e-6)


def run_case(case_spec, run_name):
    from PIL import Image, ImageDraw, ImageFont
    import cv2
    loader, log_dir, all_ts, anchor_idx, ts, frame, ring_cams, cte, tri, ann, cam_ts = load_all(case_spec)
    Ra, ta = cte(ts)
    lidar, _, moving = accumulate_lidar(log_dir, ts, cte, tri, ann)
    cents = np.stack([np.asarray(frame.calibrations[c].T_ego_cam, float)[:3, 3] for c in ring_cams], 0)
    C = cents.mean(axis=0)
    gains = solve_gains_for(frame, ring_cams, lidar, C)
    Zd, Zsupport = depth_field(lidar, C)
    poses_emc = []
    for cam in ring_cams:
        T = np.asarray(frame.calibrations[cam].T_ego_cam, float)
        Ri, ti_ = cte(cam_ts[cam])
        poses_emc.append((Ra.T @ Ri @ T[:3, :3], Ra.T @ (Ri @ T[:3, 3] + ti_ - ta)))
    cals = [(np.asarray(frame.calibrations[c].K, float), frame.images[c].shape[:2]) for c in ring_cams]
    # ---- YOLO segmentation on all 7 native images ----
    from ultralytics import YOLO
    model = YOLO("yolov8x-seg.pt")
    seg_masks = []   # per camera: full-res bool mask of ALL seg instances (cls in SEG_CLASSES)
    seg_insts = []   # per camera: list of (bbox, mask_lowres, shape)
    for ci, cam in enumerate(ring_cams):
        img = frame.images[cam]
        res = model.predict(img, imgsz=1280, conf=0.25, verbose=False, device=0)[0]
        hh, ww = img.shape[:2]
        full = np.zeros((hh, ww), bool)
        insts = []
        if res.masks is not None:
            for k in range(len(res.boxes)):
                if int(res.boxes.cls[k]) not in SEG_CLASSES: continue
                m = res.masks.data[k].cpu().numpy()
                m = cv2.resize(m, (ww, hh), interpolation=cv2.INTER_NEAREST) > 0.5
                # raw masks: detail gaps (mirrors/pillars/glass) are covered by the mask-UNION-
                # own-time-box rule downstream — no morphology needed (and morphology inflated
                # a mis-matched giant instance into a 530k-px body in the v7 forensics).
                bb = res.boxes.xyxy[k].cpu().numpy().tolist()
                insts.append((bb, m))
                full |= m
        seg_masks.append(full)
        seg_insts.append(insts)
    # ---- per moving object: per-camera matched instance masks (MOVING ONLY) + choose c_own ----
    # poison masks must contain ONLY moving objects: a static car is consistent in every camera
    # and must not invalidate anyone (v1 used the full YOLO union -> 24% of the image got filled).
    n_handled, n_unmatched = 0, 0
    objects = []
    poison_masks = [np.zeros(cals[ci][1], bool) for ci in range(len(ring_cams))]
    # assert (a): a camera whose nearest image timestamp is far from the anchor would make
    # track_pose_at interpolate the box to a far position (the DB-88 v7 smear) — skip it.
    cam_valid = [abs(cam_ts[cam] - ts) < 60_000_000 for cam in ring_cams]
    for uid in sorted(moving):
        g = ann[ann["track_uuid"] == uid]
        nt = g["timestamp_ns"].to_numpy(np.int64)
        if np.abs(nt - ts).min() > 150_000_000: continue
        best = None   # (score, ci, mask, c_a, dist)
        all_boxes = []
        per_cam_pose = {}
        per_cam_mask = {}   # IMAGE evidence per camera (label-position-independent)
        for ci, cam in enumerate(ring_cams):
            if not cam_valid[ci]: continue
            pose = track_pose_at(ann, uid, cam_ts[cam], cte, Ra, ta)
            if pose is None: continue
            c_a, sz, R_a = pose
            dist = float(np.linalg.norm(c_a - C))
            if dist > OBJ_MAX_DIST or dist < 1.0: continue
            per_cam_pose[ci] = (c_a, sz, R_a, dist)
            bflat, _bt = ray_obb_region(c_a, sz, R_a, C, pad=1.0)
            if bflat.size: all_boxes.append(bflat)
            K, (hh, ww) = cals[ci]
            Rc, tc = poses_emc[ci]
            bb = box_img_bbox(c_a, sz, R_a, K, Rc, tc, hh, ww)
            if bb is None: continue
            box_area = max((bb[2] - bb[0]) * (bb[3] - bb[1]), 1.0)
            mbest, mi = 0.0, -1
            for k, (sbb, sm) in enumerate(seg_insts[ci]):
                v = iou(bb, sbb)
                if v <= mbest: continue
                ratio = float(sm.sum()) / box_area
                if not (0.25 <= ratio <= 4.0): continue   # evidence sanity: reject giant/tiny instances
                mbest, mi = v, k
            if mbest < IOU_MIN: continue
            poison_masks[ci] |= seg_insts[ci][mi][1]   # this camera sees THIS moving object here
            per_cam_mask[ci] = (seg_insts[ci][mi][1], dist)   # image evidence: this object in THIS camera
            Xc = Rc.T @ (c_a - tc)
            if Xc[2] <= 0.3: continue
            # c_own = the EMC-Voronoi DOMINANT camera at the object's direction (min b_perp)
            dvec = (c_a - C) / max(dist, 1e-6)
            cvec = tc - C
            along = float(dvec @ cvec)
            neg_bperp = -math.sqrt(max(float(cvec @ cvec) - along * along, 0.0))
            if best is None or neg_bperp > best[0]:
                best = (neg_bperp, ci, seg_insts[ci][mi][1], c_a, dist)
        if best is None:
            n_unmatched += 1
            continue
        n_handled += 1
        # ---- ALL-IMAGE-EVIDENCE ARCHITECTURE (audit conclusion) ----
        # The AV2 box 3D position is ~4 m off on fast tracks (audit: box projects 100 px away
        # from where the camera actually imaged the car; label-time recalibration is killed by
        # track-boundary clipping at anchor 0). Therefore: boxes do IDENTITY matching only;
        # ALL spatial placement comes from image evidence (per-camera instance masks).
        objects.append({"ci": best[1], "mask": best[2], "dist": best[4],
                        "per_cam_mask": dict(per_cam_mask)})
    # ---- composite ----
    # base EMC render with per-pixel chosen-cam + projections retained
    X = C[None, None, :] + Zd[:, :, None].astype(np.float64) * DIRS
    Xf = X.reshape(-1, 3)
    proj = []
    for ci, cam in enumerate(ring_cams):
        K, (hh, ww) = cals[ci]
        Rc, tc = poses_emc[ci]
        Xc = (Rc.T @ (Xf - tc[None, :]).T).T
        z = Xc[:, 2]
        px = (K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]).astype(np.float32)
        py = (K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]).astype(np.float32)
        ok = (z > 0.1) & (px >= 1) & (px < ww - 1) & (py >= 1) & (py < hh - 1)
        # poisoned: projection lands inside this camera's MOVING-object mask (matched instances only)
        pis = np.zeros(len(Xf), bool)
        sel = np.nonzero(ok)[0]
        if sel.size and poison_masks[ci].any():
            xi = np.clip(px[sel].astype(np.int64), 0, ww - 1); yi = np.clip(py[sel].astype(np.int64), 0, hh - 1)
            pis[sel] = poison_masks[ci][yi, xi]
        cvec = tc - C; df = DIRS.reshape(-1, 3); along = df @ cvec
        bperp = np.sqrt(np.maximum(float(cvec @ cvec) - along * along, 0.0)).astype(np.float32)
        proj.append({"px": px, "py": py, "ok": ok, "poison": pis, "bperp": bperp})
    # RULE 2 source choice: valid = ok & ~poison; pick min bperp; record needs_fill where none
    bestscore = np.full(len(Xf), np.inf, np.float32)
    bestcam = np.full(len(Xf), -1, np.int8)
    for ci in range(len(ring_cams)):
        p = proj[ci]
        sc = np.where(p["ok"] & ~p["poison"], p["bperp"], np.inf)
        upd = sc < bestscore
        bestscore[upd] = sc[upd]; bestcam[upd] = ci
    needs_fill = (bestcam < 0)
    # also keep a fallback cam (ok, even if poisoned) for pixels nothing can see
    fbscore = np.full(len(Xf), np.inf, np.float32)
    fbcam = np.full(len(Xf), -1, np.int8)
    for ci in range(len(ring_cams)):
        p = proj[ci]
        sc = np.where(p["ok"], p["bperp"], np.inf)
        upd = sc < fbscore
        fbscore[upd] = sc[upd]; fbcam[upd] = ci
    # RULE 1: object-body rays — evidence UNION (mask reprojection OR own-time box hit),
    # single camera, uniform object distance. Plus collect the GHOST ZONE (this object's
    # position at every camera's exposure time).
    body_cam = np.full(len(Xf), -1, np.int8)
    body_px = np.zeros(len(Xf), np.float32); body_py = np.zeros(len(Xf), np.float32)
    ghost_zone = np.zeros(len(Xf), bool)
    df = DIRS.reshape(-1, 3)
    for ob in objects:
        ci = ob["ci"]; K, (hh, ww) = cals[ci]; Rc, tc = poses_emc[ci]
        Xobj = C[None, :] + ob["dist"] * df
        Xc = (Rc.T @ (Xobj - tc[None, :]).T).T
        z = Xc[:, 2]
        px = (K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]).astype(np.float32)
        py = (K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]).astype(np.float32)
        ok = (z > 0.1) & (px >= 1) & (px < ww - 1) & (py >= 1) & (py < hh - 1)
        sel = np.nonzero(ok)[0]
        xi = np.clip(px[sel].astype(np.int64), 0, ww - 1); yi = np.clip(py[sel].astype(np.int64), 0, hh - 1)
        inbody = np.zeros(len(Xf), bool)
        inbody[sel] = ob["mask"][yi, xi]
        body_cam[inbody] = ci
        body_px[inbody] = px[inbody]; body_py[inbody] = py[inbody]
        # GHOST ZONE from IMAGE evidence: each camera's matched instance mask, back-projected
        # at the object distance = where THAT camera's copy of the object sits in the ERP.
        for ci2, (m2, d2) in ob.get("per_cam_mask", {}).items():
            if ci2 == ci: continue
            K2, (hh2, ww2) = cals[ci2]; Rc2, tc2 = poses_emc[ci2]
            Xo2 = C[None, :] + d2 * df
            Xc2 = (Rc2.T @ (Xo2 - tc2[None, :]).T).T
            z2 = Xc2[:, 2]
            px2 = (K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]).astype(np.float32)
            py2 = (K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]).astype(np.float32)
            ok2 = (z2 > 0.1) & (px2 >= 1) & (px2 < ww2 - 1) & (py2 >= 1) & (py2 < hh2 - 1)
            s2 = np.nonzero(ok2)[0]
            xi2 = np.clip(px2[s2].astype(np.int64), 0, ww2 - 1); yi2 = np.clip(py2[s2].astype(np.int64), 0, hh2 - 1)
            ghost_zone[s2[m2[yi2, xi2]]] = True
    # assemble image
    out = np.zeros((len(Xf), 3), np.uint8)
    for ci, cam in enumerate(ring_cams):
        img = frame.images[cam]
        gimg = np.clip(img.astype(np.float32) * np.exp(gains[ci])[None, None, :].astype(np.float32), 0, 255).astype(np.uint8)
        p = proj[ci]
        sel = np.nonzero((bestcam == ci) & (body_cam < 0))[0]
        if sel.size:
            out[sel] = np.clip(bilinear(gimg, p["px"][sel], p["py"][sel]), 0, 255).astype(np.uint8)
        selb = np.nonzero(body_cam == ci)[0]
        if selb.size:
            out[selb] = np.clip(bilinear(gimg, body_px[selb], body_py[selb]), 0, 255).astype(np.uint8)
        self_fb = np.nonzero(needs_fill & (fbcam == ci) & (body_cam < 0))[0]
        if self_fb.size:
            out[self_fb] = np.clip(bilinear(gimg, p["px"][self_fb], p["py"][self_fb]), 0, 255).astype(np.uint8)
    # GHOST-ZONE TEMPORAL RECOVERY (rule 3): the other cameras' displaced copies live in
    # ghost_zone \ body — recover the occluded background from time. Fill is gated on
    # LiDAR-evidenced background depth; everything else falls back to EMC (rule 4).
    n_filled = 0
    sup_ok = (Zsupport.reshape(-1) <= 4.0)
    zone_flat = np.nonzero(ghost_zone & (body_cam < 0) & sup_ok)[0]
    leftover = np.nonzero(needs_fill & (body_cam < 0) & ~(ghost_zone & sup_ok))[0]
    if leftover.size:
        for ci, cam in enumerate(ring_cams):
            img = frame.images[cam]
            gimg = np.clip(img.astype(np.float32) * np.exp(gains[ci])[None, None, :].astype(np.float32), 0, 255).astype(np.uint8)
            p = proj[ci]
            sel = leftover[(fbcam[leftover] == ci)]
            if sel.size:
                out[sel] = np.clip(bilinear(gimg, p["px"][sel], p["py"][sel]), 0, 255).astype(np.uint8)
    if zone_flat.size:
        zdirs = df[zone_flat]
        Zv = Zd.reshape(-1)[zone_flat].astype(np.float64)
        Xz = C[None, :] + Zv[:, None] * zdirs
        X_city = (Ra @ Xz.T).T + ta
        ai = int(anchor_idx)
        chosen = np.full(zone_flat.size, -1, np.int32)
        chosen_bp = np.full(zone_flat.size, np.inf)
        def seg_blocked2(o, Xq, boxes_q):
            outb = np.zeros(len(Xq), bool)
            for c2, sz2, R2 in boxes_q:
                half2 = sz2 / 2 * 1.05
                o_loc = R2.T @ (o - c2)
                d_loc = (Xq - o[None, :]) @ R2
                with np.errstate(divide="ignore", invalid="ignore"):
                    inv = 1.0 / d_loc
                    t1 = (-half2[None, :] - o_loc[None, :]) * inv
                    t2 = (half2[None, :] - o_loc[None, :]) * inv
                tmin = np.nanmax(np.minimum(t1, t2), axis=1)
                tmax = np.nanmin(np.maximum(t1, t2), axis=1)
                outb |= (tmax >= np.maximum(tmin, 0.0)) & (tmin < 0.97) & (tmin > 0.02)
            return outb
        for fi in range(max(0, ai - 10), min(len(all_ts) - 1, ai + 10) + 1):
            if abs(fi - ai) < 3: continue   # gate: object provably departed
            tsf = int(all_ts[fi])
            Rf, tf = cte(tsf)
            Xq = (X_city - tf[None, :]) @ Rf
            fboxes = [(c2, sz2 * 1.3, R2) for (c2, sz2, R2) in boxes_at(ann, tsf, moving)]   # padded
            for ci2, cam in enumerate(ring_cams):
                K2, (hh2, ww2) = cals[ci2]
                T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float)
                Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                okq = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2)
                if not okq.any(): continue
                blocked = np.zeros(zone_flat.size, bool)
                blocked[okq] = seg_blocked2(T2[:3, 3], Xq[okq], fboxes)
                visq = okq & ~blocked
                if not visq.any(): continue
                cam_city = Rf @ T2[:3, 3] + tf
                cam_anchor = Ra.T @ (cam_city - ta)
                cvec2 = cam_anchor - C
                along2 = zdirs @ cvec2
                bp2 = np.sqrt(np.maximum(float(cvec2 @ cvec2) - along2 * along2, 0.0))
                cand = visq & (bp2 < chosen_bp)
                chosen[cand] = fi * 10 + ci2
                chosen_bp[cand] = bp2[cand]
        for code in np.unique(chosen[chosen >= 0]):
            fi, ci2 = int(code) // 10, int(code) % 10
            sel = chosen == code
            fr2 = loader.load_synced_frame(int(all_ts[fi]))
            Rf, tf = cte(int(all_ts[fi]))
            Xq = (X_city[sel] - tf[None, :]) @ Rf
            K2, _s2 = cals[ci2]
            T2 = np.asarray(frame.calibrations[ring_cams[ci2]].T_ego_cam, float)
            Tci2 = np.linalg.inv(T2)
            Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
            px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
            py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
            img2 = fr2.images[ring_cams[ci2]]
            g2 = np.exp(gains[ci2])[None, :]
            col = np.clip(bilinear(img2, px2, py2) * g2, 0, 255)
            out[zone_flat[sel]] = col.astype(np.uint8)
            n_filled += int(sel.sum())
    comp = out.reshape(H, W, 3)
    # plain EMC base for the A/B
    embase = np.zeros((len(Xf), 3), np.uint8)
    for ci, cam in enumerate(ring_cams):
        img = frame.images[cam]
        gimg = np.clip(img.astype(np.float32) * np.exp(gains[ci])[None, None, :].astype(np.float32), 0, 255).astype(np.uint8)
        p = proj[ci]
        sel = np.nonzero(fbcam == ci)[0]
        if sel.size:
            embase[sel] = np.clip(bilinear(gimg, p["px"][sel], p["py"][sel]), 0, 255).astype(np.uint8)
    emc = embase.reshape(H, W, 3)
    save_rgb(REMOTE_OUT / f"{run_name}_emc.png", emc)
    save_rgb(REMOTE_OUT / f"{run_name}_segcomposite.png", comp)
    from PIL import Image as I
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    rows = []
    for tag, im in (("EMC base", emc), (f"SEG-COMPOSITE (objs={n_handled} unmatched={n_unmatched} filled={n_filled}px)", comp)):
        pil = I.fromarray(im).resize((1400, 700))
        bar = I.new("RGB", (1400, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), f"{run_name}  {tag}", (235, 235, 245), font=f)
        o = I.new("RGB", (1400, 724)); o.paste(bar, (0, 0)); o.paste(pil, (0, 24)); rows.append(o)
    board = I.new("RGB", (1400, 724 * 2 + 12), (8, 8, 12))
    yo = 6
    for o in rows: board.paste(o, (0, yo)); yo += o.height
    board.save(REMOTE_OUT / f"{run_name}_db89_board.jpg", quality=90)
    return {"case": run_name, "n_objects_composited": int(n_handled), "n_unmatched": int(n_unmatched),
            "n_temporal_filled_px": int(n_filled)}


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ultralytics"], timeout=600, check=False)
    reports = [run_case(cs, rn) for cs, rn in CASES]
    OUT["status"] = "db89_completed"; OUT["cases"] = reports; OUT["runtime_s"] = round(time.time() - t0, 2)
except Exception as exc:
    OUT["status"] = "db89_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print("DB89_JSON_BEGIN"); print(json.dumps(OUT, separators=(",", ":"))); print("DB89_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT)


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db89_remote>', 'exec'))\nPY"


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
    names = ["DB89_remote_result.json"]
    for n in CASE_NAMES:
        names += [f"{n}_db89_board.jpg", f"{n}_emc.png", f"{n}_segcomposite.png"]
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
    out = Path.home() / ".waymo2panorama" / "db89_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
