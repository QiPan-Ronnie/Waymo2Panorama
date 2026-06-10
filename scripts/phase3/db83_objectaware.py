"""DB-83: object-aware centroid renderer — per-object single-source locking + box-consistent depth.

Fixes the user-flagged dark-sedan doubling (BMW): inconsistent depth inside an object's ERP
footprint (sparse LiDAR on dark body + background EDT fill) projects one car to two azimuths.
Fix: project each annotated 3D box (anchor frame) into the centroid ERP; inside the footprint
force (a) Zd := box-centre distance (consistent), (b) source := the min-b_perp camera at the
box centre direction (one camera per object). Everything else identical to DB-80/81 renderer.
Renders BMW + crowd + downtown: variants cen_depth_b1 (current) vs cen_depth_b1_objlock.
One bounded /exec, CPU. Boards with tight crops on known vehicles.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db83_objectaware"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db83_objectaware"
RESULT = REMOTE_OUT + "/DB83_remote_result.json"

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
OBJ_MAX_DIST = 35.0; OBJ_PAD = 1.10   # lock objects nearer than this; footprint pad factor
MOAT_PX = 30                          # dilate footprint: ring where the per-camera box-occlusion test runs
SIL_PX = 7                            # object silhouette = within this ERP px of the object's own LiDAR
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}
OUT = {"phase": "db83_objectaware", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"renderer_fix_only": True, "generation": False, "a100": False}}

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


def anchor_boxes_all(ann, ts):
    """ALL annotated objects at the anchor frame (static and moving) for footprint locking."""
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


def depth_field(lidar, C, exclude_boxes=None):
    """Background depth field. exclude_boxes: LiDAR points inside annotated boxes are removed
    BEFORE splat/EDT so object depth cannot leak into surrounding background pixels (the leak
    was printing ghost copies of objects next to themselves)."""
    from scipy.ndimage import distance_transform_edt
    if exclude_boxes:
        keep = np.ones(len(lidar), bool)
        for c, sz, Rb in exclude_boxes:
            if np.linalg.norm(c - C) > OBJ_MAX_DIST + 10: continue
            loc = (lidar - c) @ Rb; half = sz / 2 * 1.05
            keep &= ~((np.abs(loc[:, 0]) < half[0]) & (np.abs(loc[:, 1]) < half[1]) & (np.abs(loc[:, 2]) < half[2]))
        lidar = lidar[keep]
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


def object_layers(boxes, C, LIDAR_PTS=None):
    """Per-pixel object silhouette defined by the object's OWN LiDAR points (box = grouping only).
    Returns objmap (-1 = none), per-pixel LiDAR-splat depth, per-object centre dir + dist.
    Nearer objects win overlaps. Coarse azimuth/elevation rect prefilters pixels per box."""
    objmap = np.full((H, W), -1, np.int16)
    objdepth = np.zeros((H, W), np.float32)
    objdist = []
    objdir = []
    oid2box = []
    order = sorted(range(len(boxes)), key=lambda i: -np.linalg.norm(boxes[i][0] - C))  # far first, near overwrites
    for oi in order:
        c, sz, Rb = boxes[oi]
        dist = float(np.linalg.norm(c - C))
        if dist > OBJ_MAX_DIST or dist < 1.0: continue
        half = sz / 2 * OBJ_PAD
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
        if len(cols) == 0 or len(rows) == 0: continue
        # v9: footprint = the coarse box-projection rect itself. It is only a SOFT source-
        # preference zone (no depth override, no hard lock), so covering the box's air margin
        # is harmless — the preferred camera renders its own consistent view of car+background.
        oid = len(objdist)
        objdist.append(dist)
        objdir.append((c - C) / max(dist, 1e-6))
        oid2box.append(oi)
        objmap[np.ix_(rows, cols)] = oid
    # moat: dilate the union footprint; moat pixels inherit the nearest object's id (lock the
    # same source camera around each object so its occlusion edge stays natural — no double print)
    from scipy.ndimage import binary_dilation, distance_transform_edt
    hitmask = objmap >= 0
    moat = binary_dilation(hitmask, iterations=MOAT_PX) & ~hitmask
    if moat.any() and hitmask.any():
        _d, inds = distance_transform_edt(~hitmask, return_distances=True, return_indices=True)
        moatmap = np.full((H, W), -1, np.int16)
        moatmap[moat] = objmap[inds[0][moat], inds[1][moat]]
    else:
        moatmap = np.full((H, W), -1, np.int16)
    return objmap, moatmap, objdepth, np.array(objdist, np.float32), np.array(objdir, np.float64), np.array(oid2box, np.int32)


def render(frame, ring_cams, C, Zd, gains, objmap=None, moatmap=None, objdepth=None, objdist=None, objdir=None, boxes=None, oid2box=None):
    """v9 seam-steering: do NOT touch depth, do NOT hard-lock, do NOT occlusion-test.
    Inside an object footprint, softly prefer the object's centre camera (small score bias).
    Single-source per object + min-b_perp camera => any depth error costs only ~1-2 ERP px
    (uniform shift), and doubling (two cameras each painting the car once) cannot happen."""
    import cv2
    Zuse = Zd
    lock_cam = None
    lockmap = None
    occl_zone = None
    if objmap is not None and objdist is not None and len(objdist):
        lockmap = objmap
        if moatmap is not None:
            mm2 = (lockmap < 0) & (moatmap >= 0)
            lockmap = lockmap.copy(); lockmap[mm2] = moatmap[mm2]
        # per-object locked camera = min b_perp at the object's centre direction
        lock = np.full(len(objdist), -1, np.int8)
        for oi in range(len(objdist)):
            dvec = objdir[oi]
            Xc_pt = C + objdist[oi] * dvec   # object centre in ego frame
            best, bi = 1e9, -1
            for ci, cam in enumerate(ring_cams):
                cal = frame.calibrations[cam]; K = np.asarray(cal.K, float); T = np.asarray(cal.T_ego_cam, float)
                Tci = np.linalg.inv(T)
                Xc = Tci[:3, :3] @ Xc_pt + Tci[:3, 3]
                if Xc[2] <= 0.1: continue                      # camera must actually face the object
                hh, ww = frame.images[cam].shape[:2]
                px = K[0, 0] * Xc[0] / Xc[2] + K[0, 2]; py = K[1, 1] * Xc[1] / Xc[2] + K[1, 2]
                if not (0 <= px < ww and 0 <= py < hh): continue   # centre must project in-bounds
                cv = T[:3, 3] - C
                along = float(dvec @ cv)
                bp = math.sqrt(max(float(cv @ cv) - along * along, 0.0))
                if bp < best: best, bi = bp, ci
            lock[oi] = bi   # -1 = no camera sees it -> footprint falls back to normal competition
        lock_cam = lock
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
        if lock_cam is not None:
            om = lockmap.reshape(-1)   # footprint + moat ring share the soft preference
            has_lock = (om >= 0) & (lock_cam[np.maximum(om, 0)] >= 0)
            preferred = has_lock & (lock_cam[np.maximum(om, 0)] == ci)
            # SOFT steering: non-preferred cameras get +0.5 m (decisive vs b_perp<=0.3 m but
            # finite, so they still backstop wherever the preferred camera has no valid pixel)
            score = np.where(has_lock & ~preferred & ok, score + 0.5, score)
        upd = score < best.reshape(-1)
        if not upd.any(): continue
        mapx = np.where(upd, px, 0).reshape(H, W); mapy = np.where(upd, py, 0).reshape(H, W)
        col = cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR)
        u2 = upd.reshape(H, W); out[u2] = col[u2]
        be = best.reshape(-1); be[upd] = score[upd]; best = be.reshape(H, W)
    return out


def run_case(case_spec, run_name):
    from PIL import Image, ImageDraw, ImageFont
    log_dir, ts, frame, ring_cams, cte, tri, ann = load_all(case_spec)
    cents = np.stack([np.asarray(frame.calibrations[c].T_ego_cam, float)[:3, 3] for c in ring_cams], 0)
    C = cents.mean(axis=0)
    lidar = accumulate_lidar(log_dir, ts, cte, tri, ann)
    gains = solve_gains_for(frame, ring_cams, lidar, C)
    Zd = depth_field(lidar, C)
    base = render(frame, ring_cams, C, Zd, gains)
    boxes = anchor_boxes_all(ann, ts)
    # evidence gate: occluded/absent objects (no LiDAR support) must not steer the seam
    vis = []
    for bc, bsz, bR in boxes:
        loc = (lidar - bc) @ bR; half = bsz / 2 * 1.05
        npts = int(((np.abs(loc[:, 0]) < half[0]) & (np.abs(loc[:, 1]) < half[1]) & (np.abs(loc[:, 2]) < half[2])).sum())
        if npts >= 50: vis.append((bc, bsz, bR))
    boxes = vis
    objmap, moatmap, objdepth, objdist, objdir, oid2box = object_layers(boxes, C, LIDAR_PTS=lidar)
    fixed = render(frame, ring_cams, C, Zd, gains, objmap=objmap, moatmap=moatmap, objdepth=objdepth,
                   objdist=objdist, objdir=objdir, boxes=boxes, oid2box=oid2box)
    save_rgb(REMOTE_OUT / f"{run_name}_objlock.png", fixed)
    save_rgb(REMOTE_OUT / f"{run_name}_noobjlock.png", base)
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    rows = []
    for tag, im in (("cen_depth_b1 (current, no objlock)", base), (f"cen_depth_b1 + OBJLOCK (n_obj={len(objdist)})", fixed)):
        pil = Image.fromarray(im).resize((1400, 700))
        bar = Image.new("RGB", (1400, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), f"{run_name}  {tag}", (235, 235, 245), font=f)
        o = Image.new("RGB", (1400, 724)); o.paste(bar, (0, 0)); o.paste(pil, (0, 24)); rows.append(o)
    board = Image.new("RGB", (1400, 724 * 2 + 12), (8, 8, 12))
    yo = 6
    for o in rows: board.paste(o, (0, yo)); yo += o.height
    board.save(REMOTE_OUT / f"{run_name}_db83_board.jpg", quality=90)
    return {"case": run_name, "n_objects_locked": int(len(objdist))}


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    reports = [run_case(cs, rn) for cs, rn in CASES]
    OUT["status"] = "db83_completed"; OUT["cases"] = reports; OUT["runtime_s"] = round(time.time() - t0, 2)
except Exception as exc:
    OUT["status"] = "db83_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print("DB83_JSON_BEGIN"); print(json.dumps(OUT, separators=(",", ":"))); print("DB83_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT)


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db83_remote>', 'exec'))\nPY"


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
    names = ["DB83_remote_result.json"]
    for n in CASE_NAMES:
        names += [f"{n}_db83_board.jpg", f"{n}_objlock.png", f"{n}_noobjlock.png"]
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
    out = Path.home() / ".waymo2panorama" / "db83_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
