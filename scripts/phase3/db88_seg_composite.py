"""DB-88: segmentation-bounded moving-object compositing on EMC (L4 GPU for YOLO-seg only).

The image-level silhouette (instance segmentation matched to the annotation box) provides the
one input all six prior failures lacked. Two rules:
  RULE 1 (object body): ERP rays that project (at the object's box distance) inside the chosen
    camera's object mask -> forced to that camera. One camera + one uniform depth = consistent.
  RULE 2 (poisoned background): background rays whose responsible camera's projection lands in
    ANY of that camera's moving-object masks would sample the car -> that camera is invalid;
    take the next visible min-b_perp camera; if none sees it, temporal fill (DB-84 machinery,
    per-frame box occlusion tests).
Segmentation decides pixel OWNERSHIP only; every output pixel remains a real sensor pixel.
Renders BMW: emc base vs seg-composite. One bounded /exec.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db88_seg_composite"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db88_seg_composite"
RESULT = REMOTE_OUT + "/DB88_remote_result.json"

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
OUT = {"phase": "db88_seg_composite", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
            kclose = np.ones((15, 15), np.uint8); kdil = np.ones((9, 9), np.uint8)
            for k in range(len(res.boxes)):
                if int(res.boxes.cls[k]) not in SEG_CLASSES: continue
                m = res.masks.data[k].cpu().numpy()
                m = cv2.resize(m, (ww, hh), interpolation=cv2.INTER_NEAREST) > 0.5
                # close+dilate: seg masks miss mirrors/pillars/window glass -> those details
                # would render from another camera/time = detail-level double images. The cost
                # of over-covering is ~1px of single-camera-consistent background shift.
                m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE, kclose)
                m = cv2.dilate(m, kdil) > 0
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
    mov_rect = np.zeros(H * W, bool)   # ERP neighbourhood of moving objects: fill is allowed only here
    for uid in sorted(moving):
        g = ann[ann["track_uuid"] == uid]
        nt = g["timestamp_ns"].to_numpy(np.int64)
        if np.abs(nt - ts).min() > 150_000_000: continue
        best = None   # (margin, ci, mask, c_a, dist)
        u_lo = u_hi = v_lo = v_hi = None
        for ci, cam in enumerate(ring_cams):
            pose = track_pose_at(ann, uid, cam_ts[cam], cte, Ra, ta)
            if pose is None: continue
            c_a, sz, R_a = pose
            dist = float(np.linalg.norm(c_a - C))
            if dist > OBJ_MAX_DIST or dist < 1.0: continue
            K, (hh, ww) = cals[ci]
            Rc, tc = poses_emc[ci]
            bb = box_img_bbox(c_a, sz, R_a, K, Rc, tc, hh, ww)
            if bb is None: continue
            # ERP rect union for the fill-allowed neighbourhood
            half = sz / 2
            corners = np.array([[sx * half[0], sy * half[1], sz_ * half[2]]
                                for sx in (-1, 1) for sy in (-1, 1) for sz_ in (-1, 1)])
            P = (corners @ R_a.T) + c_a[None, :]
            Q = P - C[None, :]
            nrm = np.linalg.norm(Q, axis=1); dq = Q / nrm[:, None]
            th = np.arctan2(dq[:, 1], dq[:, 0]); ph = np.arcsin(np.clip(dq[:, 2], -1, 1))
            uu = (np.pi - th) / (2 * np.pi) * W; vv = (np.pi / 2 - ph) / np.pi * H
            if uu.max() - uu.min() <= W / 2:
                u_lo = uu.min() if u_lo is None else min(u_lo, uu.min())
                u_hi = uu.max() if u_hi is None else max(u_hi, uu.max())
                v_lo = vv.min() if v_lo is None else min(v_lo, vv.min())
                v_hi = vv.max() if v_hi is None else max(v_hi, vv.max())
            mbest, mi = 0.0, -1
            for k, (sbb, sm) in enumerate(seg_insts[ci]):
                v = iou(bb, sbb)
                if v > mbest: mbest, mi = v, k
            if mbest < IOU_MIN: continue
            poison_masks[ci] |= seg_insts[ci][mi][1]   # this camera sees THIS moving object here
            Xc = Rc.T @ (c_a - tc)
            if Xc[2] <= 0.3: continue
            # c_own = the EMC-Voronoi DOMINANT camera at the object's direction (min b_perp),
            # so the body's capture time matches the penumbra remnants' time and they join
            # naturally instead of being displaced by the shutter offset (v4 lesson).
            dvec = (c_a - C) / max(dist, 1e-6)
            cvec = tc - C
            along = float(dvec @ cvec)
            neg_bperp = -math.sqrt(max(float(cvec @ cvec) - along * along, 0.0))
            if best is None or neg_bperp > best[0]:
                best = (neg_bperp, ci, seg_insts[ci][mi][1], c_a, dist)
        if u_lo is not None:
            r0 = max(int(v_lo) - 10, 0); r1 = min(int(v_hi) + 10, H - 1)
            c0 = max(int(u_lo) - 16, 0); c1 = min(int(u_hi) + 16, W - 1)
            rr, cc = np.meshgrid(np.arange(r0, r1 + 1), np.arange(c0, c1 + 1), indexing="ij")
            mov_rect[(rr * W + cc).reshape(-1)] = True
        if best is None:
            n_unmatched += 1
            continue
        n_handled += 1
        objects.append({"ci": best[1], "mask": best[2], "dist": best[4]})
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
    # RULE 1: object-body rays override — project at the object's distance into c_own, inside mask
    body_cam = np.full(len(Xf), -1, np.int8)
    body_px = np.zeros(len(Xf), np.float32); body_py = np.zeros(len(Xf), np.float32)
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
        inmask = np.zeros(len(Xf), bool)
        inmask[sel] = ob["mask"][yi, xi]
        body_cam[inmask] = ci
        body_px[inmask] = px[inmask]; body_py[inmask] = py[inmask]
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
    # TEMPORAL FILL for needs_fill & not body (poisoned everywhere) — restricted to the moving-
    # object neighbourhood; anywhere else falls back to the EMC behaviour (fbcam) instead.
    n_filled = 0
    # fill only where the background depth is LiDAR-evidenced (guessed EDT depth + 1s-away
    # frame = tens-of-px reprojection error = colour shards); everything else -> EMC fallback
    # v4: temporal fill DISABLED for the residual penumbra — cross-time sampling at these few
    # hundred px yields colour shards worse than the EMC fallback's soft edge. All penumbra
    # pixels take the EMC fallback camera (a slightly soft object edge, visually benign).
    zone_flat = np.zeros(0, np.int64)
    leftover = np.nonzero(needs_fill & (body_cam < 0))[0]
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
            if fi == ai: continue
            tsf = int(all_ts[fi])
            Rf, tf = cte(tsf)
            Xq = (X_city - tf[None, :]) @ Rf
            fboxes = boxes_at(ann, tsf, moving)
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
    board.save(REMOTE_OUT / f"{run_name}_db88_board.jpg", quality=90)
    return {"case": run_name, "n_objects_composited": int(n_handled), "n_unmatched": int(n_unmatched),
            "n_temporal_filled_px": int(n_filled)}


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ultralytics"], timeout=600, check=False)
    reports = [run_case(cs, rn) for cs, rn in CASES]
    OUT["status"] = "db88_completed"; OUT["cases"] = reports; OUT["runtime_s"] = round(time.time() - t0, 2)
except Exception as exc:
    OUT["status"] = "db88_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print("DB88_JSON_BEGIN"); print(json.dumps(OUT, separators=(",", ":"))); print("DB88_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT)


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db88_remote>', 'exec'))\nPY"


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
    names = ["DB88_remote_result.json"]
    for n in CASE_NAMES:
        names += [f"{n}_db88_board.jpg", f"{n}_emc.png", f"{n}_segcomposite.png"]
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
    out = Path.home() / ".waymo2panorama" / "db88_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
