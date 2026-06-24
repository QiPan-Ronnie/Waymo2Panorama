"""DB-89: ghost-zone temporal recovery — the hardened v7 GENERAL algorithm (L4 for YOLO only).

Five evidence-driven rules, zero scene parameters:
  1. STATIC world <- EMC render (per-camera exposure-time ego poses).
  2. OBJECT BODY <- ONE camera c_own at ONE exposure time, chosen by EVIDENCE
     COMPLETENESS first (mask not truncated by its image border = that camera saw the
     WHOLE object; splitting a straddler across two exposure times necessarily tears
     it open by object_speed * dt at the FOV boundary), Voronoi dominance among
     equals. Identity matching is ONE-TO-ONE (greedy by IoU; a camera instance is
     evidence for exactly one object). Extent = matched mask under TOPOLOGICAL
     CLOSURE (binary_fill_holes: parameter-free, boundary-preserving); uniform
     object-distance projection.
  3. SECONDARY BODY with OBJECT-MOTION SHUTTER COMPENSATION (OMC, the object-side
     symmetric piece to DB-86's ego EMC): when no camera sees the whole object, the
     split across exposure times is unavoidable — measure the object's ERP
     displacement between the two exposures from the masks themselves (binary
     alignment inside the overlap strip both cameras see) and shift the secondary
     camera's contribution to c_own's exposure-time position before compositing.
     Without OMC the halves tear open by object_speed * dt at the FOV boundary;
     without secondary body at all, temporal fill erases the truncated half.
  4. GHOST ZONE (other cameras' unshifted copies, minus ALL cameras' body evidence)
     <- temporal recovery as the LAST RESORT: only where NO camera cleanly sees the
     background at anchor time (all views poisoned, true mutual disocclusion), under
     a TRIPLE gate: object provably departed (|dframe|>=3) AND padded-box-free
     sightline at that frame AND LiDAR-evidenced background depth. Where a clean
     camera exists, RULE 2 renders the true anchor-time background instead.
  5. Gate fails -> keep the EMC pixel. No depth overwrites anywhere.
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

CASE_NAMES = ["02a00399_a000_bmw", "9f871fb4_a030_downtown", "fbee355f_a030_crowd",
              "0bae3b5e_a030_clean", "2c652f9e_a030_highway"]


def remote_py() -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
GROUND_MODE = "fill"   # "fill"=STAGE-4 nadir reconstruction; "off"=middle-only base stitch (skip ground outpaint entirely -> BLACK nadir, like the Fable-5 board). ("mask" gray branch is deprecated/dead.) "funnel"=DB-109 Stage-1 diagnostic: runs fill + dumps a per-pixel gate-funnel npy (_funnel_cls.npy) + counts in OUT["funnel"]; no main-path change.
SEAM_OBJDEPTH = False   # DB-103 isolation test (default OFF, never ships): force close-object ERP regions to their box depth before scene-band reproject, to isolate the near-car seam-shear cause (depth-field smoothing vs occlusion). Does NOT touch the Fable-5 core when False.
SEAM_MASK_FILL = False  # DB-104 robust mask (default OFF, gated): fill ENCLOSED holes (windows) in each YOLO object mask via binary_fill_holes (NOT dilation -> cannot inflate the boundary or merge instances, so it does NOT reintroduce the v7 giant-instance bug). A complete object body also gives the flow-morph more registration signal. Off = pure Fable-5 mask.
SEAM_FLOWMORPH = True   # DB-103 fix (SHIPPED 2026-06-19, validated: a309 shear gone 32->8.6px, crowd a50 helped, clean seams byte-identical, 6-frame temporal stable): when the view-morph ECC-AFFINE residual is large (close-object depth-varying parallax), replace the affine displacement with dense Farneback optical flow INSIDE the object body. GATED on max_reg_px>8 -> fires ONLY on the rare near-object-break seams, never touches the well-registered ones (clean frames byte-identical). Pristine core in _baseline_fable5/. Set False to revert to pure affine.
SEAM_SINGLE_SOURCE = False  # DB-105 (diagnostic-validated on a309): when c_own sees the object COMPLETE and a secondary contributes only a small grazing sliver (mask << c_own area), DROP the secondary body-fill + SKIP the view-morph -> pure single-source. The near-car seam's CAUSE is the morph FUSING a complete car (side_left 1610 LiDAR pts) with a 149-pt grazing sliver (front_left). Gated, default OFF; pristine core in _baseline_fable5/.
GROUND_RESID = "plate"  # DB-108 (AUDIT 2026-06-22): how the evidence-INSUFFICIENT nadir (spread>30 or no source) is filled. "plate"=DB-99 gray DC plate (DEFAULT, honest-but-gray). "inpaint"=video-era NS-inpaint (cv2.INPAINT_NS extends real edges into the blind cap) -> ground-FEEL (the ground_video_v1 look; blurry/白团 on bare asphalt). COMBO (audit-verified, recovers ground-feel + keeps near car) = "inpaint" + the DB-106 boundary. Gated, default unchanged (gray).
MOVING_GATE = True  # DB-109 Stage-1b (diagnostic, default True = shipped behavior): STAGE-4 ground-source moving-object occlusion gate. Set False to isolate whether a309's 94% gate3 is OVER-AGGRESSIVE box-occlusion (real recovers when off) vs GENUINE car blocking (newly-admitted sources read as car-body -> spread>30, real stays low).
MOVING_SCALE = 1.3  # DB-109 Stage-1c: moving-box inflation factor (default 1.3 = shipped). 1.0 = precise box. The 1.3x inflation + whole-grazing-ray test over-blocks ~76% of good ground sources on traffic frames (a309 5.6%->81.9% when off); shrinking toward 1.0 recovers them, the spread gate backstops genuine car-body.
WORLDBEV_WIN = (0, 92)  # DB-109 B1 (GROUND_MODE="worldbev"): FIXED anchor window [lo,hi] the world ground map is built over. Fixed (NOT anchor-relative) so neighbouring target anchors sample the SAME map -> temporal-coherence test. Driver sets it per scene.
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"),
         ("9f871fb4-3b8e-34b3-9161-ed961e71a6da:30:downtown", "9f871fb4_a030_downtown"),
         ("fbee355f-8878-31fa-8ac8-b9a45a3f130a:30:crowd", "fbee355f_a030_crowd"),
         ("0bae3b5e-417d-3b03-abaa-806b433233b8:30:clean", "0bae3b5e_a030_clean"),
         ("2c652f9e-8db8-3572-aa49-fae1344a875b:30:highway", "2c652f9e_a030_highway")]
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
    # nearest-neighbour is a 1-SAMPLE depth estimator: on thin/sparse structures
    # (poles, glass mullions) it flips per pixel between foreground and background
    # returns, and every flip times the camera baseline becomes a sampling jump =
    # GRAIN (user-confirmed vs the L1 baseline, present already in the EMC base).
    # A neighbourhood MEDIAN keeps true depth edges but kills the bimodal jitter.
    import cv2 as _cvd
    Zf = _cvd.medianBlur(Zf, 5)
    dz = DIRS[:, :, 2]
    plane_t = np.where(dz < -0.05, (-C[2] - 0.33) / np.minimum(dz, -1e-3), np.inf).astype(np.float32)
    use_plane = (dist_px > 12) & np.isfinite(plane_t) & (plane_t > DMIN) & (plane_t < DMAX)
    Zf = np.where(use_plane, plane_t, Zf)
    # DEPTH-EVIDENCE GATING (rule 8): per-pixel reprojection is only legal where the
    # depth evidence is trustworthy. On specular/transmissive surfaces (glass facades:
    # LiDAR punches through or mirror-bounces -> WHOLE-PATCH garbage, not salt noise)
    # and at discontinuity edges, per-pixel depth shatters the render. There the
    # region degrades to a LARGE-SCALE robust depth (the L1-style locally-flat render:
    # coherent even if a few px displaced — coherence over absolute position).
    # Trust = close LiDAR support AND agreement with the large-scale median.
    small = _cvd.resize(Zf, (W // 8, H // 8), interpolation=_cvd.INTER_NEAREST)
    Zsmooth = _cvd.resize(_cvd.medianBlur(small, 5), (W, H), interpolation=_cvd.INTER_LINEAR)
    conf = (dist_px <= 4) & (np.abs(Zf - Zsmooth) < 0.05 * Zsmooth)
    Zf = np.where(conf, Zf, Zsmooth)
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
    # GRACEFUL NO-LiDAR DEGRADATION (evidence-insufficiency fallbacks): without LiDAR
    # the gains stay identity, depth degrades to ground-plane + far shell, and the
    # LiDAR-gated temporal fill disarms itself (Zsupport=inf -> sup_ok empty).
    if len(lidar) < 1000:
        gains = np.zeros((len(ring_cams), 3))
        dz0 = DIRS[:, :, 2]
        Zd = np.where(dz0 < -0.05, np.clip((-C[2] - 0.33) / np.minimum(dz0, -1e-3), DMIN, DMAX), 100.0).astype(np.float32)
        Zsupport = np.full((H, W), 1e9, np.float32)
    else:
        gains = solve_gains_for(frame, ring_cams, lidar, C)
        Zd, Zsupport = depth_field(lidar, C)
    if SEAM_OBJDEPTH and ann is not None and "track_uuid" in ann.columns:
        # DB-103 isolation test: force CLOSE-object ERP regions to the object's OWN box
        # depth (not the smoothed depth field) BEFORE the scene-band reprojection, to test
        # whether the near-car seam shear (front_left/side_left max_reg_px=32) comes from
        # depth_field smoothing the car's depth at the car/background discontinuity.
        _au = set(ann["track_uuid"].unique()); _nov = 0
        for _bc, _bsz, _bR in boxes_at(ann, ts, _au):
            if np.linalg.norm(_bc - C) > 12.0: continue
            _reg, _dent = ray_obb_region(_bc, _bsz, _bR, C, pad=1.0)
            if len(_reg):
                Zd.reshape(-1)[_reg] = _dent.astype(np.float32); _nov += len(_reg)
        print("SEAM_OBJDEPTH overrode", _nov, "ERP px with object-box depth", flush=True)
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
                if SEAM_MASK_FILL:   # DB-104: fill ENCLOSED holes (windows) only — NOT dilation, so
                    from scipy.ndimage import binary_fill_holes as _bfh   # the boundary can't inflate
                    m = _bfh(m)
                bb = res.boxes.xyxy[k].cpu().numpy().tolist()
                insts.append((bb, m))
                full |= m
        seg_masks.append(full)
        seg_insts.append(insts)
    # ---- per moving object: per-camera matched instance masks (MOVING ONLY) + choose c_own ----
    # poison masks must contain ONLY moving objects: a static car is consistent in every camera
    # and must not invalidate anyone (v1 used the full YOLO union -> 24% of the image got filled).
    # ---- ALL-IMAGE-EVIDENCE ARCHITECTURE (audit conclusion) ----
    # The AV2 box 3D position is ~4 m off on fast tracks (audit: box projects 100 px away
    # from where the camera actually imaged the car; label-time recalibration is killed by
    # track-boundary clipping at anchor 0). Therefore: boxes do IDENTITY matching only;
    # ALL spatial placement comes from image evidence (per-camera instance masks).
    # assert (a): a camera whose nearest image timestamp is far from the anchor would make
    # track_pose_at interpolate the box to a far position (the DB-88 v7 smear) — skip it.
    cam_valid = [abs(cam_ts[cam] - ts) < 60_000_000 for cam in ring_cams]
    # pass 1: candidate (object, camera, instance) matches
    obj_meta = []   # per moving uid passing the time gate: {"uid", "per_cam_pose"}
    cand = []       # (iou, obj_idx, ci, mi)
    for uid in sorted(moving):
        g = ann[ann["track_uuid"] == uid]
        nt = g["timestamp_ns"].to_numpy(np.int64)
        if np.abs(nt - ts).min() > 150_000_000: continue
        per_cam_pose = {}
        for ci, cam in enumerate(ring_cams):
            if not cam_valid[ci]: continue
            pose = track_pose_at(ann, uid, cam_ts[cam], cte, Ra, ta)
            if pose is None: continue
            c_a, sz, R_a = pose
            dist = float(np.linalg.norm(c_a - C))
            if dist > OBJ_MAX_DIST or dist < 1.0: continue
            K, (hh, ww) = cals[ci]
            Rc, tc = poses_emc[ci]
            bb = box_img_bbox(c_a, sz, R_a, K, Rc, tc, hh, ww)
            if bb is None: continue
            Xc = Rc.T @ (c_a - tc)
            if Xc[2] <= 0.3: continue
            per_cam_pose[ci] = (c_a, sz, R_a, dist)
            box_area = max((bb[2] - bb[0]) * (bb[3] - bb[1]), 1.0)
            for k, (sbb, sm) in enumerate(seg_insts[ci]):
                v = iou(bb, sbb)
                if v < IOU_MIN: continue
                ratio = float(sm.sum()) / box_area
                if not (0.25 <= ratio <= 4.0): continue   # evidence sanity: reject giant/tiny instances
                cand.append((v, len(obj_meta), ci, k))
        obj_meta.append({"uid": uid, "per_cam_pose": per_cam_pose})
    # pass 2: ONE-TO-ONE greedy by IoU — a camera instance is evidence for exactly ONE
    # object. An instance claimed by >=2 tracks is a MERGED BLOB (adjacent vehicles
    # fused into one mask; identity unresolvable): ambiguous evidence may VETO
    # (poison — conservative, keeps contaminated backgrounds out) but cannot ASSERT
    # (a fused silhouette painted at one object's uniform distance drags the
    # neighbour's pixels onto it — seen on the 6.1 m X3).
    poison_masks = [np.zeros(cals[ci][1], bool) for ci in range(len(ring_cams))]
    claims = {}
    for v, oidx, ci, k in cand:
        claims.setdefault((ci, k), set()).add(oidx)
    ambiguous = {key for key, s in claims.items() if len(s) >= 2}
    for ci, k in ambiguous:
        poison_masks[ci] |= seg_insts[ci][k][1]
    cand.sort(key=lambda t: -t[0])
    taken_inst = set(); taken_slot = set()
    assign = {}   # (obj_idx, ci) -> instance index
    for v, oidx, ci, k in cand:
        if (ci, k) in ambiguous: continue
        if (ci, k) in taken_inst or (oidx, ci) in taken_slot: continue
        taken_inst.add((ci, k)); taken_slot.add((oidx, ci))
        assign[(oidx, ci)] = k
    # pass 3: c_own choice — EVIDENCE COMPLETENESS first (a mask not truncated by its
    # image border means this camera saw the WHOLE object: single-time render, no seam;
    # splitting a straddler across two exposure times necessarily tears it open by
    # object_speed * dt at the FOV boundary), Voronoi dominance among equals.
    n_handled, n_unmatched = 0, 0
    objects = []
    for oidx, meta in enumerate(obj_meta):
        per_cam_mask = {}   # IMAGE evidence per camera (label-position-independent)
        best = None   # (key, ci, mask, dist, complete, area)
        cands_ci = []   # (ci, m, dist, complete, area) — for the DB-105 dominant-coverage flip
        for ci in sorted(meta["per_cam_pose"]):
            k = assign.get((oidx, ci))
            if k is None: continue
            m = seg_insts[ci][k][1]
            c_a, sz, R_a, dist = meta["per_cam_pose"][ci]
            poison_masks[ci] |= m   # this camera sees THIS moving object here
            per_cam_mask[ci] = (m, dist)
            # completeness margin: 1% of the image dimension — YOLO mask edges are ragged,
            # a truncated mask can stop a few px short of the border (seen: x_min=4 on a
            # nose cut off at x=0). False-incomplete is mild (falls back to the split
            # path); false-complete tears the object. Asymmetric costs -> conservative.
            mh, mw = m.shape; mgy, mgx = max(4, mh // 100), max(4, mw // 100)
            complete = not (m[:mgy, :].any() or m[-mgy:, :].any() or m[:, :mgx].any() or m[:, -mgx:].any())
            tc = poses_emc[ci][1]
            dvec = (c_a - C) / max(dist, 1e-6)
            cvec = tc - C
            along = float(dvec @ cvec)
            neg_bperp = -math.sqrt(max(float(cvec @ cvec) - along * along, 0.0))
            area_ci = int(m.sum())
            cands_ci.append((ci, m, dist, complete, area_ci))
            key = (1 if complete else 0, neg_bperp)
            if best is None or key > best[0]:
                best = (key, ci, m, dist, complete, area_ci)
        # DB-105: dominant-coverage flip — completeness mis-ranks a VERY close object (the grazing
        # SLIVER is "complete"; the whole-object camera is "incomplete"). If ONE camera sees the
        # object MUCH more than the completeness-winner (>2.5x mask area), it is the true single-
        # source owner -> flip c_own to it. Fires ONLY on a real dominant (a309 side_left ~10.8x);
        # a genuinely cross-camera object (crowd RAM, comparable areas) is UNCHANGED -> still morphs.
        if SEAM_SINGLE_SOURCE and best is not None and cands_ci:
            dom = max(cands_ci, key=lambda t: t[4])
            if dom[0] != best[1] and dom[4] > 2.5 * max(best[5], 1):
                best = ((1 if dom[3] else 0, 0.0), dom[0], dom[1], dom[2], dom[3], dom[4])
        if best is None:
            n_unmatched += 1
            continue
        n_handled += 1
        objects.append({"ci": best[1], "mask": best[2], "dist": best[3],
                        "per_cam_mask": dict(per_cam_mask),
                        "complete": bool(best[4]), "own_area": int(best[5])})
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
    n_secondary = 0
    omc = []   # per (object, camera-pair) measured shutter displacement
    morph_jobs = []   # straddle objects: (ci_own, ci_sec, d_own, d_sec, shift, body_flat)
    df = DIRS.reshape(-1, 3)
    from scipy.ndimage import binary_fill_holes
    def close_region(flat_bool):
        """Topological closure (parameter-free, boundary-preserving): a hole strictly
        enclosed by an object's silhouette at uniform distance IS the object — YOLO
        masks lose thin dark structures (A-pillars) and the ghost ledger would
        otherwise temporally fill real background INTO the car. Roll by the region's
        circular mean to respect the ERP u-wrap before hole-filling."""
        ib2 = flat_bool.reshape(H, W)
        cols = np.nonzero(ib2.any(0))[0]
        if cols.size == 0: return flat_bool
        ang = cols / W * 2 * np.pi
        cmean = math.atan2(float(np.sin(ang).mean()), float(np.cos(ang).mean())) % (2 * np.pi)
        shift = W // 2 - int(round(cmean / (2 * np.pi) * W))
        ib2 = binary_fill_holes(np.roll(ib2, shift, axis=1))
        return np.roll(ib2, -shift, axis=1).reshape(-1)
    import cv2 as _cv
    gimgs = [np.clip(frame.images[cam].astype(np.float32) * np.exp(gains[ci_]).astype(np.float32)[None, None, :], 0, 255).astype(np.uint8)
             for ci_, cam in enumerate(ring_cams)]

    def sample_cam_patch(ci_s, dist_s, rows_s, cols_s, shift=(0, 0)):
        """ERP patch (rows x cols grid at uniform distance) rendered from one camera.
        shift=(dv,du): sample the source at y-shift (OMC: content appears moved +shift)."""
        rr, cc = np.meshgrid(rows_s, cols_s, indexing="ij")
        rr2 = np.clip(rr - shift[0], 0, H - 1)
        cc2 = (cc - shift[1]) % W
        dirs_p = DIRS[rr2, cc2]
        Xp = (C[None, None, :] + dist_s * dirs_p).reshape(-1, 3)
        K_, (hh_, ww_) = cals[ci_s]
        Rc_, tc_ = poses_emc[ci_s]
        Xc_ = (Xp - tc_[None, :]) @ Rc_
        z_ = Xc_[:, 2]
        px_ = (K_[0, 0] * Xc_[:, 0] / np.maximum(z_, 1e-6) + K_[0, 2]).astype(np.float32)
        py_ = (K_[1, 1] * Xc_[:, 1] / np.maximum(z_, 1e-6) + K_[1, 2]).astype(np.float32)
        valid = (z_ > 0.1) & (px_ >= 1) & (px_ < ww_ - 1) & (py_ >= 1) & (py_ < hh_ - 1)
        col = np.zeros((len(z_), 3), np.float32)
        if valid.any():
            col[valid] = bilinear(gimgs[ci_s], px_[valid], py_[valid]).astype(np.float32)
        return col.reshape(rr.shape + (3,)), valid.reshape(rr.shape)
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
        inbody = close_region(inbody) & ok   # & ok: hole pixels must still project into c_own
        body_cam[inbody] = ci
        body_px[inbody] = px[inbody]; body_py[inbody] = py[inbody]
        # SECONDARY BODY with OBJECT-MOTION SHUTTER COMPENSATION (OMC) + GHOST LEDGER.
        # Each camera's mask covers only the PART of a boundary-straddling object it
        # sees, AND imaged it at a DIFFERENT exposure time — naively butting the two
        # halves tears the object open by object_speed * dt at the FOV boundary.
        # OMC is the object-side symmetric piece to DB-86's EMC: measure the object's
        # ERP displacement between the two exposures FROM THE MASKS THEMSELVES (the
        # overlap strip both cameras see images the same physical part twice), shift
        # the secondary camera's contribution to c_own's exposure-time position, THEN
        # composite. The secondary camera's UNSHIFTED copy becomes ghost (-> temporal
        # background recovery). Zero scene parameters: the shift is measured per
        # object per camera pair from image evidence alone.
        # own_cover = where c_own's evidence is AUTHORITATIVE. Negative evidence
        # (absence of mask = "background here") is unreliable within the ragged border
        # margin of c_own's own image (seen: a truncated mask starting at x=4 left a
        # 4-column "background" strip at the FOV edge that temporal fill painted with
        # the real background INSIDE the car). Positive evidence (inbody) keeps the
        # full 1-px bounds; the authority region shrinks by the same 1% margin as the
        # completeness test.
        mgy2, mgx2 = max(4, hh // 100), max(4, ww // 100)
        own_cover = (z > 0.1) & (px >= mgx2) & (px < ww - mgx2) & (py >= mgy2) & (py < hh - mgy2)
        obj_body = inbody.copy()
        best_sec = None   # (n_px, ci2, d2, (dv,du))
        others = [c2 for c2 in ob.get("per_cam_mask", {}) if c2 != ci]
        others.sort(key=lambda c2: abs(cam_ts[ring_cams[c2]] - cam_ts[ring_cams[ci]]))
        for ci2 in others:
            m2, d2 = ob["per_cam_mask"][ci2]
            K2, (hh2, ww2) = cals[ci2]; Rc2, tc2 = poses_emc[ci2]
            Xo2 = C[None, :] + d2 * df
            Xc2 = (Rc2.T @ (Xo2 - tc2[None, :]).T).T
            z2 = Xc2[:, 2]
            px2 = (K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]).astype(np.float32)
            py2 = (K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]).astype(np.float32)
            ok2 = (z2 > 0.1) & (px2 >= 1) & (px2 < ww2 - 1) & (py2 >= 1) & (py2 < hh2 - 1)
            s2 = np.nonzero(ok2)[0]
            xi2 = np.clip(px2[s2].astype(np.int64), 0, ww2 - 1); yi2 = np.clip(py2[s2].astype(np.int64), 0, hh2 - 1)
            rep2f = np.zeros(len(Xf), bool)
            rep2f[s2[m2[yi2, xi2]]] = True   # where THIS camera's copy of the object sits in the ERP
            # the DONOR's positive evidence inside its own border margin is rectification
            # junk (black border columns get pulled in by mask raggedness) — same 1%
            # border rule as c_own's negative evidence, applied symmetrically.
            mgy2c, mgx2c = max(4, hh2 // 100), max(4, ww2 // 100)
            ok2m = (z2 > 0.1) & (px2 >= mgx2c) & (px2 < ww2 - mgx2c) & (py2 >= mgy2c) & (py2 < hh2 - mgy2c)
            rep2b = close_region(rep2f) & ok2m
            # OMC shift estimate: binary alignment of the two masks inside the overlap strip
            strip = own_cover & ok2
            A2 = (inbody & strip).reshape(H, W); B2 = (rep2b & strip).reshape(H, W)
            du_best, dv_best, sc_best, ncc_best = 0, 0, -1.0, -9.0
            if min(int(A2.sum()), int(B2.sum())) >= 50:   # evidence-sufficiency gate
                yy, xx = np.nonzero(A2 | B2)
                y0c = max(0, int(yy.min()) - 12); y1c = min(H, int(yy.max()) + 13)
                x0c = max(0, int(xx.min()) - 70); x1c = min(W, int(xx.max()) + 71)
                Ac = A2[y0c:y1c, x0c:x1c]; Bc = B2[y0c:y1c, x0c:x1c]
                for dv2 in range(-8, 9, 2):
                    for du2 in range(-60, 61, 2):
                        Bs = np.roll(np.roll(Bc, dv2, axis=0), du2, axis=1)
                        sc = float((Ac & Bs).sum()) / max(float((Ac | Bs).sum()), 1.0)
                        if sc > sc_best: sc_best, du_best, dv_best = sc, du2, dv2
                for dv2 in range(dv_best - 1, dv_best + 2):
                    for du2 in range(du_best - 1, du_best + 2):
                        Bs = np.roll(np.roll(Bc, dv2, axis=0), du2, axis=1)
                        sc = float((Ac & Bs).sum()) / max(float((Ac | Bs).sum()), 1.0)
                        if sc > sc_best: sc_best, du_best, dv_best = sc, du2, dv2
                # ECC IMAGE refinement: mask-IoU is blind to ~15 px shifts (coarse ragged
                # silhouettes barely change IoU), while the physics budget says
                # object_speed * dt / dist is exactly that order (17.7 m/s * 35 ms at
                # 13 m ~ 15 px). Estimate pure translation on the RENDERED GRAYS inside
                # the masks; arbitrate candidates (coarse, +ecc, -ecc) by masked NCC so
                # a sign mistake cannot slip through.
                rows_e = np.arange(y0c, y1c); cols_e = np.arange(x0c, x1c)
                Ae, Aev = sample_cam_patch(ci, ob["dist"], rows_e, cols_e)
                Be, Bev = sample_cam_patch(ci2, d2, rows_e, cols_e)
                gAe = Ae.mean(2).astype(np.float32) / 255.0
                gBe = Be.mean(2).astype(np.float32) / 255.0
                def ncc_at(du_c, dv_c):
                    Bs2 = np.roll(np.roll(gBe, dv_c, 0), du_c, 1)
                    Ms2 = np.roll(np.roll(Bc & Bev, dv_c, 0), du_c, 1) & Ac & Aev
                    if Ms2.sum() < 50: return -2.0
                    a_ = gAe[Ms2] - gAe[Ms2].mean(); b_ = Bs2[Ms2] - Bs2[Ms2].mean()
                    return float((a_ * b_).sum() / max(np.sqrt((a_ * a_).sum() * (b_ * b_).sum()), 1e-9))
                cand = [(du_best, dv_best)]
                Mt = np.eye(2, 3, dtype=np.float32); Mt[0, 2] = -du_best; Mt[1, 2] = -dv_best
                try:
                    _cce, Mt = _cv.findTransformECC(gAe, gBe, Mt, _cv.MOTION_TRANSLATION,
                                                    (_cv.TERM_CRITERIA_EPS | _cv.TERM_CRITERIA_COUNT, 80, 1e-5),
                                                    ((Ac | Bc) & Aev & Bev).astype(np.uint8) * 255, 3)
                    cand += [(int(round(-Mt[0, 2])), int(round(-Mt[1, 2]))),
                             (int(round(Mt[0, 2])), int(round(Mt[1, 2])))]
                except _cv.error:
                    pass
                scored = sorted(((ncc_at(dc, vc), dc, vc) for dc, vc in cand), reverse=True)
                ncc_best, du_best, dv_best = scored[0]
            omc.append({"cam_pair": [ring_cams[ci], ring_cams[ci2]], "du": int(du_best),
                        "dv": int(dv_best), "score": round(sc_best, 3),
                        "ncc": round(float(ncc_best) if isinstance(ncc_best, float) else -9.0, 3)})
            # secondary body = the OMC-shifted copy, wherever no body evidence exists yet.
            # POSITIVE evidence (the NCC-verified shifted copy says car) outranks
            # NEGATIVE evidence (c_own's mask gap says background) — same hierarchy as
            # the border-margin rule; without it a shift opens a sliver between the
            # halves that temporal fill paints with true (dark) background.
            shifted = np.roll(np.roll(rep2b.reshape(H, W), dv_best, axis=0), du_best, axis=1).reshape(-1)
            ti = np.nonzero(shifted & (body_cam < 0))[0]
            sv = ti // W - dv_best; su = (ti % W - du_best) % W
            keep = (sv >= 0) & (sv < H)
            ti = ti[keep]; si = sv[keep] * W + su[keep]
            if ti.size:
                body_cam[ti] = ci2
                body_px[ti] = px2[si]; body_py[ti] = py2[si]
                n_secondary += int(ti.size)
                obj_body[ti] = True
                if best_sec is None or ti.size > best_sec[0]:
                    best_sec = (int(ti.size), ci2, d2, (dv_best, du_best))
            # ghost: this camera's UNSHIFTED copy anywhere not claimed as body — but a
            # ghost only EXISTS when the measured displacement exceeds the measurement
            # quantisation (~2 px): at zero displacement the "displaced copy" IS the
            # body, and rep2-minus-body is pure mask-edge noise (filling it paints
            # shadowless road over the contact shadow). Anchor-time cameras already
            # render those pixels correctly.
            if abs(du_best) > 2 or abs(dv_best) > 2:
                ghost_zone |= rep2b & (body_cam < 0)
        # Temporal recovery must NEVER target the INTERIOR of a solid object's
        # silhouette closure (mask notches between the halves would get painted with
        # true dark background INSIDE the car) — interior holes fall back to RULE 2/EMC.
        ghost_zone &= ~close_region(obj_body)
        if best_sec is not None and best_sec[0] >= 50:
            sec_area = int(ob["per_cam_mask"].get(best_sec[1], (np.zeros((1, 1), bool),))[0].sum())
            if SEAM_SINGLE_SOURCE and sec_area < 0.4 * max(ob.get("own_area", 1), 1):
                pass   # DB-105: secondary is a grazing sliver -> KEEP its disocclusion fill (the leading edge c_own genuinely can't see) but SKIP the morph. Fusing a complete c_own body with a sliver IS the shear; no fusion -> no shear, and the sliver still patches the few px c_own lacks.
            else:
                morph_jobs.append((ci, best_sec[1], ob["dist"], best_sec[2], best_sec[3], obj_body))
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
    # TEMPORAL RECOVERY IS THE LAST RESORT: only where NO camera cleanly sees the
    # background at anchor time (needs_fill = all views poisoned, true mutual
    # disocclusion). Where a clean camera exists, RULE 2 already renders the true
    # anchor-time background (e.g. the shadowed road under the car — a fill from
    # another TIME paints shadowless road and breaks the contact shadow).
    zone_flat = np.nonzero(ghost_zone & needs_fill & (body_cam < 0) & sup_ok)[0]
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
        # TEMPORAL CONSENSUS: keep the 3 best independent (frame, camera) sources per
        # pixel and fill with their per-channel MEDIAN. The sightline gate tests
        # LAGGED boxes (the dataset's ~0.2 s annotation lag), so a single source can
        # leak a moving object (the user's green protrusion above the Porsche roof);
        # an outlier among 3 independent times is voted out. Zero thresholds.
        chosen = np.full((3, zone_flat.size), -1, np.int32)
        chosen_bp = np.full((3, zone_flat.size), np.inf)
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
                code_new = fi * 10 + ci2
                c0 = visq & (bp2 < chosen_bp[0])
                c1 = visq & ~c0 & (bp2 < chosen_bp[1])
                c2 = visq & ~c0 & ~c1 & (bp2 < chosen_bp[2])
                # slot insertion (best three by b_perp)
                chosen[2][c0] = chosen[1][c0]; chosen_bp[2][c0] = chosen_bp[1][c0]
                chosen[1][c0] = chosen[0][c0]; chosen_bp[1][c0] = chosen_bp[0][c0]
                chosen[0][c0] = code_new; chosen_bp[0][c0] = bp2[c0]
                chosen[2][c1] = chosen[1][c1]; chosen_bp[2][c1] = chosen_bp[1][c1]
                chosen[1][c1] = code_new; chosen_bp[1][c1] = bp2[c1]
                chosen[2][c2] = code_new; chosen_bp[2][c2] = bp2[c2]
        colbuf = np.full((3, zone_flat.size, 3), np.nan, np.float32)
        frame_cache = {}
        for slot in range(3):
            for code in np.unique(chosen[slot][chosen[slot] >= 0]):
                fi, ci2 = int(code) // 10, int(code) % 10
                sel = chosen[slot] == code
                if int(all_ts[fi]) not in frame_cache:
                    frame_cache[int(all_ts[fi])] = loader.load_synced_frame(int(all_ts[fi]))
                fr2 = frame_cache[int(all_ts[fi])]
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
                colbuf[slot][sel] = np.clip(bilinear(img2, px2, py2) * g2, 0, 255).astype(np.float32)
        have = ~np.isnan(colbuf[:, :, 0])
        anyv = have.any(0)
        if anyv.any():
            pre_fill = out[zone_flat].copy()
            med = np.nanmedian(colbuf[:, anyv], axis=0)
            out[zone_flat[anyv]] = np.clip(med, 0, 255).astype(np.uint8)
            n_filled += int(anyv.sum())
            # NEIGHBOURHOOD-CONSISTENCY ABSTAIN (rule 8 for time): specular content is
            # view-dependent — a fill sourced from one camera's future frames can be
            # "true background" yet clash with the surrounding render from another
            # viewpoint (the green reflection blob beside the Porsche nose). A filled
            # blob whose colour departs from its surrounding ring by far more than the
            # ring's own spread is unverifiable -> abstain back to the EMC pixel.
            from scipy.ndimage import label as _lbl, binary_dilation as _bdl, distance_transform_edt as _edt2
            ffm = np.zeros(len(Xf), bool); ffm[zone_flat[anyv]] = True
            ffm2 = ffm.reshape(H, W)
            labarr, nlab = _lbl(ffm2)
            out2v = out.reshape(H, W, 3)
            n_abstained = 0
            for li in range(1, nlab + 1):
                blob = labarr == li
                ring = _bdl(blob, iterations=4) & ~ffm2 & (body_cam.reshape(H, W) < 0)
                if int(ring.sum()) < 30: continue
                bpx = out2v[blob].astype(np.float32)
                rpx = out2v[ring].astype(np.float32)
                bmed = np.median(bpx, 0); rmed = np.median(rpx, 0)
                rmad = np.median(np.abs(rpx - rmed[None, :]), 0).mean() + 4.0
                if float(np.abs(bmed - rmed).mean()) > 5.0 * rmad:
                    # content of a different CLASS entirely -> abstain to the EMC pixel
                    sel_b = blob.reshape(-1)[zone_flat]
                    out[zone_flat[sel_b]] = pre_fill[sel_b]
                    n_abstained += int(sel_b.sum())
                    continue
                # PHOTOMETRIC ALIGNMENT (seamless-cloning lite, Perez'03): the fill is
                # content from another time/viewpoint — geometrically right but lit
                # differently (and the object's cast shadow, absent from all evidence,
                # falls on this band at anchor time). Match the blob's per-channel
                # colour statistics to its surrounding ring so structure is kept and
                # the ambient light (incl. shadow) transfers. Zero scene parameters.
                # PER-PIXEL boundary-driven offset (harmonic-lite Poisson approx):
                # each fill pixel takes the photometric offset of its NEAREST ring
                # pixels, smoothed. Global blob shifts cannot serve heterogeneous
                # rings (shadow on one side, sunlit kerb on the other): locally, the
                # under-car pixels inherit the shadow falloff, the outer pixels stay
                # sunlit, and wherever the fill already matches its surroundings the
                # offset is ~0 so no foreign tint is introduced.
                ys_b, xs_b = np.nonzero(blob)
                y0b, y1b = max(0, int(ys_b.min()) - 12), min(H, int(ys_b.max()) + 13)
                x0b, x1b = max(0, int(xs_b.min()) - 12), min(W, int(xs_b.max()) + 13)
                bl = blob[y0b:y1b, x0b:x1b]; rg = ring[y0b:y1b, x0b:x1b]
                patch = out2v[y0b:y1b, x0b:x1b].astype(np.float32)
                if rg.any() and bl.any():
                    _di, idx_in = _edt2(~bl, return_distances=True, return_indices=True)
                    offs = np.zeros(bl.shape + (3,), np.float32)
                    offs[rg] = patch[rg] - patch[idx_in[0][rg], idx_in[1][rg]]
                    _dr, idx_rg = _edt2(~rg, return_distances=True, return_indices=True)
                    field = _cv.GaussianBlur(offs[idx_rg[0], idx_rg[1]], (0, 0), 3.0)
                    patch[bl] = np.clip(patch[bl] + field[bl], 0, 255)
                    out2v[y0b:y1b, x0b:x1b] = patch.astype(np.uint8)
            n_filled -= n_abstained
    # ---- STAGE 3.5: VIEW-MORPH the straddle seam (Surround360/Megastereo-style) ----
    # A hard butt-joint between two cameras' halves of one object leaves a 1-2 px
    # registration step + a photometric step that the eye integrates as DOUBLING
    # (user-confirmed at 16x: roofline/sill/shoulder lines all step at the seam).
    # Selection answers WHO/WHERE (evidence calculus); interpolation answers HOW to
    # transition: ECC-affine registration (rigid object, small view change) + an
    # alpha-ramp Beier-Neely morph across the evidence-bounded overlap strip.
    out2 = out.reshape(H, W, 3)
    morph_report = []
    for ci_o, ci_s, d_o, d_s, shift_s, body_flat in morph_jobs:
        m2d = body_flat.reshape(H, W)
        rows_any = np.nonzero(m2d.any(1))[0]; cols_any = np.nonzero(m2d.any(0))[0]
        if rows_any.size == 0 or cols_any.size > W // 2: continue   # skip wrap/degenerate
        v0o, v1o = int(rows_any.min()), int(rows_any.max())
        u0o, u1o = int(cols_any.min()), int(cols_any.max())
        rows_p = np.arange(max(0, v0o - 8), min(H, v1o + 9))
        cols_p = np.arange(u0o - 8, u1o + 9)   # may exceed [0,W); helper wraps
        A_patch, A_val = sample_cam_patch(ci_o, d_o, rows_p, cols_p)
        B_patch, B_val = sample_cam_patch(ci_s, d_s, rows_p, cols_p, shift_s)
        # invalid patch pixels are literal zeros — bilinear remap across the validity
        # edge would bleed BLACK into the blend. Cross-fill so black never exists.
        A_patch[~A_val] = B_patch[~A_val]
        B_patch[~B_val] = A_patch[~B_val]
        body_p = m2d[np.ix_(rows_p, cols_p % W)]
        # overlap strip: columns where BOTH cameras cover the object's rows
        colA = (A_val & body_p).sum(0); colB = (B_val & body_p).sum(0); colN = body_p.sum(0)
        both = (colN > 0) & (colA >= 0.9 * colN) & (colB >= 0.9 * colN)
        bi = np.nonzero(both)[0]
        if bi.size < 4: continue
        # B-pure end = the side where A loses coverage beyond the strip
        left_A = colA[:bi[0]].sum(); right_A = colA[bi[-1] + 1:].sum()
        b_side_left = left_A <= right_A
        # clamp strip to 32 cols hugging the B side
        if bi.size > 32: bi = bi[:32] if b_side_left else bi[-32:]
        # ECC affine registration B->A on the strip (gray, masked)
        gA = _cv.cvtColor(A_patch.astype(np.uint8), _cv.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        gB = _cv.cvtColor(B_patch.astype(np.uint8), _cv.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        mask_ecc = np.zeros(body_p.shape, np.uint8)
        mask_ecc[:, bi] = (body_p[:, bi] & A_val[:, bi] & B_val[:, bi]).astype(np.uint8) * 255
        M = np.eye(2, 3, dtype=np.float32)
        cc_ecc = 0.0
        try:
            cc_ecc, M = _cv.findTransformECC(gA, gB, M, _cv.MOTION_AFFINE,
                                             (_cv.TERM_CRITERIA_EPS | _cv.TERM_CRITERIA_COUNT, 60, 1e-5),
                                             mask_ecc, 3)
        except _cv.error:
            M = np.eye(2, 3, dtype=np.float32)   # fallback: pure cross-fade
        # alpha ramp across the strip (1.0 at the B-pure end)
        alpha_col = np.zeros(body_p.shape[1], np.float32)
        ramp = np.linspace(1.0, 0.0, bi.size, dtype=np.float32)
        alpha_col[bi] = ramp if b_side_left else ramp[::-1]
        if b_side_left: alpha_col[:bi[0]] = 1.0
        else: alpha_col[bi[-1] + 1:] = 1.0
        # Beier-Neely with the affine displacement field d(y) = M·y - y
        yy, xx = np.meshgrid(np.arange(body_p.shape[0], dtype=np.float32),
                             np.arange(body_p.shape[1], dtype=np.float32), indexing="ij")
        dx = M[0, 0] * xx + M[0, 1] * yy + M[0, 2] - xx
        dy = M[1, 0] * xx + M[1, 1] * yy + M[1, 2] - yy
        if SEAM_FLOWMORPH and (mask_ecc > 0).any() and float(np.hypot(dx, dy)[mask_ecc > 0].max()) > 8.0:
            # DB-103: a CLOSE straddling object's parallax is depth-varying (non-affine);
            # the single ECC-affine shears it. Inside the OBJECT body (where both cameras
            # see the SAME surface in the overlap), use dense optical flow (A->B) instead
            # of the affine displacement. Gated on the affine residual so well-registered
            # seams are untouched; flow only overrides where it is sane (magnitude-clamped).
            _gA8 = (np.clip(gA, 0, 1) * 255).astype(np.uint8)
            _gB8 = (np.clip(gB, 0, 1) * 255).astype(np.uint8)
            _fl = _cv.calcOpticalFlowFarneback(_gA8, _gB8, None, 0.5, 4, 25, 5, 7, 1.5, 0)
            _use = (body_p & A_val & B_val) & (np.hypot(_fl[:, :, 0], _fl[:, :, 1]) < 80.0)
            dx = np.where(_use, _fl[:, :, 0], dx).astype(np.float32)
            dy = np.where(_use, _fl[:, :, 1], dy).astype(np.float32)
        al = np.broadcast_to(alpha_col[None, :], body_p.shape).astype(np.float32)
        A_w = _cv.remap(A_patch, xx + al * dx, yy + al * dy, _cv.INTER_LINEAR, borderMode=_cv.BORDER_REPLICATE)
        B_w = _cv.remap(B_patch, xx - (1 - al) * dx, yy - (1 - al) * dy, _cv.INTER_LINEAR, borderMode=_cv.BORDER_REPLICATE)
        Av_w = _cv.remap(A_val.astype(np.uint8), xx + al * dx, yy + al * dy, _cv.INTER_NEAREST) > 0
        Bv_w = _cv.remap(B_val.astype(np.uint8), xx - (1 - al) * dx, yy - (1 - al) * dy, _cv.INTER_NEAREST) > 0
        # CONTENT seam (Photomontage-style): geometry is interpolated by the alpha ramp
        # above (continuous), but CONTENT must be winner-take-all where the two views
        # disagree — glass reflections are VIEW-DEPENDENT (the mirrored storefront's
        # parallax follows the reflected source's depth, not the body's), so any
        # alpha-blend double-exposes them. A min-difference DP seam picks the switch
        # path through whatever agrees (paint, pillars); 2 px feather.
        diff_ab = np.abs(A_w.astype(np.float32) - B_w.astype(np.float32)).sum(2)
        cost = np.where(body_p & Av_w & Bv_w, diff_ab, 0.0)
        lo_c, hi_c = int(bi[0]), int(bi[-1])
        ncol = hi_c - lo_c + 1
        nrow = body_p.shape[0]
        BIG = np.float32(1e9)
        D = np.zeros((nrow, ncol), np.float32)
        back = np.zeros((nrow, ncol), np.int32)
        D[0] = cost[0, lo_c:hi_c + 1]
        for r_ in range(1, nrow):
            prev = D[r_ - 1]
            s_l = np.concatenate([[BIG], prev[:-1]])
            s_r = np.concatenate([prev[1:], [BIG]])
            stacked = np.stack([s_l, prev, s_r])
            arg = stacked.argmin(0)
            D[r_] = cost[r_, lo_c:hi_c + 1] + stacked[arg, np.arange(ncol)]
            back[r_] = arg - 1
        s_col = np.zeros(nrow, np.int32)
        s_col[-1] = int(D[-1].argmin())
        for r_ in range(nrow - 2, -1, -1):
            s_col[r_] = s_col[r_ + 1] + back[r_ + 1, s_col[r_ + 1]]
        seam_x = (lo_c + s_col)[:, None].astype(np.float32)
        xs_g = np.arange(body_p.shape[1], dtype=np.float32)[None, :]
        if b_side_left:
            w_cont = np.clip((seam_x + 2 - xs_g) / 4.0, 0, 1)
        else:
            w_cont = np.clip((xs_g - seam_x + 2) / 4.0, 0, 1)
        w_a = (1 - w_cont) * Av_w; w_b = w_cont * Bv_w
        den = np.maximum(w_a + w_b, 1e-6)
        blend = (A_w * w_a[:, :, None] + B_w * w_b[:, :, None]) / den[:, :, None]
        write = body_p & ((Av_w | Bv_w))
        write[:, ~((alpha_col > 0) & (alpha_col < 1))] &= False   # only strip interior
        tgt_r = rows_p[:, None] * np.ones_like(cols_p)[None, :]
        tgt_c = np.ones_like(rows_p)[:, None] * (cols_p % W)[None, :]
        out2[tgt_r[write], tgt_c[write]] = np.clip(blend[write], 0, 255).astype(np.uint8)
        seam_diff = diff_ab[np.arange(nrow), lo_c + s_col]
        morph_report.append({"cam_pair": [ring_cams[ci_o], ring_cams[ci_s]],
                             "strip_cols": int(bi.size), "ecc_cc": round(float(cc_ecc), 3),
                             "max_reg_px": round(float(np.hypot(dx, dy)[mask_ecc > 0].max()) if (mask_ecc > 0).any() else 0.0, 2),
                             "seam_diff_med": round(float(np.median(seam_diff[body_p[np.arange(nrow), lo_c + s_col]])) if body_p[np.arange(nrow), lo_c + s_col].any() else 0.0, 1),
                             "n_px": int(write.sum())})
    comp = out.reshape(H, W, 3)
    # CHROMA-FRINGE SUPPRESSION (final polish): the source cameras carry purple
    # fringing on high-contrast edges (native-confirmed). Desaturate only pixels in
    # the magenta band (Cr>136 AND Cb>136 in YCrCb) toward neutral chroma, keeping
    # luminance untouched. Verified surgical: ~0.5% of pixels change; genuinely
    # purple content (the locustprojects sign) survives.
    _ycc = _cv.cvtColor(comp, _cv.COLOR_RGB2YCrCb).astype(np.float32)
    _fr = _cv.GaussianBlur(((_ycc[:, :, 1] > 136) & (_ycc[:, :, 2] > 136)).astype(np.float32), (5, 5), 0)
    _w = np.clip(_fr * 1.5, 0, 1) * 0.75
    _ycc[:, :, 1] = _ycc[:, :, 1] * (1 - _w) + 128 * _w
    _ycc[:, :, 2] = _ycc[:, :, 2] * (1 - _w) + 128 * _w
    comp = _cv.cvtColor(np.clip(_ycc, 0, 255).astype(np.uint8), _cv.COLOR_YCrCb2RGB)
    # ---- STAGE 4: GROUND TEMPORAL FILL (deterministic, real pixels) ----
    # The nadir cap and the ego-occluded zone are a REPROJECTION problem, not a
    # generation problem: the road under/around the ego was fully visible to the
    # cameras seconds before/after. Ego zone = rays intersecting the ego 3D box
    # (slab test; the hood occludes ground out to ~5-8 m, footprint alone misses it).
    # Sources gated by ego-distance 5-28 m (no ego shadow/body) and lagged-box
    # occlusion; candidates = WHOLE-LOG geometry search (ego displacement 5-58 m,
    # displacement-stratified), never a time window — a stationary ego (red light)
    # defeats any fixed window; 6-source median VALIDATES, the nearest-to-median single source
    # RENDERS (blending smears misaligned markings). Residual (never-visible) px
    # get small-area diffusion inpaint from the surrounding real road.
    def gseg_blocked(o, Xq_, boxes_q):
        outb = np.zeros(len(Xq_), bool)
        for c2_, sz2_, R2_ in boxes_q:
            half2 = sz2_ / 2 * 1.05
            o_loc = R2_.T @ (o - c2_)
            d_loc = (Xq_ - o[None, :]) @ R2_
            with np.errstate(divide="ignore", invalid="ignore"):
                inv_ = 1.0 / d_loc
                t1_ = (-half2[None, :] - o_loc[None, :]) * inv_
                t2_ = (half2[None, :] - o_loc[None, :]) * inv_
            tmin_ = np.nanmax(np.minimum(t1_, t2_), axis=1)
            tmax_ = np.nanmin(np.maximum(t1_, t2_), axis=1)
            outb |= (tmax_ >= np.maximum(tmin_, 0.0)) & (tmin_ < 0.97) & (tmin_ > 0.02)
        return outb
    df3 = DIRS.reshape(-1, 3)
    dzf = df3[:, 2]
    bmin_e = np.array([-2.2, -1.6, -C[2] - 0.33])
    bmax_e = np.array([4.6, 1.6, -0.35])
    with np.errstate(divide="ignore", invalid="ignore"):
        invd = 1.0 / df3
        ta_e = bmin_e[None, :] * invd
        tb_e = bmax_e[None, :] * invd
    tmin_e = np.nanmax(np.minimum(ta_e, tb_e), axis=1)
    tmax_e = np.nanmin(np.maximum(ta_e, tb_e), axis=1)
    egoproj = (tmax_e >= np.maximum(tmin_e, 0.0)) & (tmax_e > 0) & (dzf < -0.02)
    # DB-106 (user-found ground/scene-band boundary bug): ground must fill ONLY where the
    # scene band rendered NOTHING (comp black). Do NOT union egoproj: a NEAR car's lower body
    # has rays that pass through the ego box (egoproj=True) yet comp there is the REAL car —
    # unioning egoproj let ground (footprint-shadow + bev/plate) OVERWRITE the real car's lower
    # body ("ground eats the car"). egoproj's genuine blind region (under-hood/under-ego) is
    # comp-black and already included by the sum<12 term, so nothing real is lost.
    blackg = (comp.astype(np.int32).sum(2) < 12)
    capg = blackg.copy()
    capg[:H // 2] = False
    _capfull = capg.copy()   # DB-101: full unseen nadir cap (before the target-gate prunes capg) — for middle-only mask mode
    flat_g = np.nonzero(capg.reshape(-1))[0]
    dirs_g = df3[flat_g]
    okd = dirs_g[:, 2] < -0.08
    flat_g = flat_g[okd]; dirs_g = dirs_g[okd]
    t_g = (-C[2] - 0.33) / dirs_g[:, 2]
    keepn = (t_g > 0) & (t_g < 30.0)
    flat_g = flat_g[keepn]; dirs_g = dirs_g[keepn]; t_g = t_g[keepn]
    Xg = C[None, :] + t_g[:, None] * dirs_g
    # GROUND HEIGHT FROM LiDAR, not a flat plane (DB-98): the flat-plane assumption is
    # wrong at curbs/slopes, and at grazing angles a small height error becomes a
    # metre-scale horizontal sampling error -> every source samples a DIFFERENT real
    # surface -> they disagree -> the per-pixel pick jumps -> radial black streaks.
    # March each cap ray onto the measured LiDAR ground surface so every source samples
    # the SAME real-world point -> agreement -> real texture. General (any scene, uses
    # the LiDAR we already have, zero scene params); falls back to the plane where no
    # LiDAR is nearby. (The residual softness at the near-nadir-behind pole is the
    # genuine evidence limit — extreme grazing + ERP pole undersampling — left honest.)
    from scipy.spatial import cKDTree as _CKD
    _gpts = lidar[(lidar[:, 2] > -0.33 - 0.5) & (lidar[:, 2] < -0.33 + 2.5)]   # ground + curb band
    if len(_gpts) > 200:
        _tr = _CKD(_gpts[:, :2])
        for _it in range(3):
            _dd, _ii = _tr.query(Xg[:, :2], k=1)
            _gz = np.where(_dd < 1.2, _gpts[_ii, 2], -0.33)
            _t = np.clip((_gz - C[2]) / dirs_g[:, 2], 0.1, 40.0)
            Xg = C[None, :] + _t[:, None] * dirs_g
    Xg_city = (Ra @ Xg.T).T + ta
    # ---- DB-101 TARGET-side visibility gate (object FOOTPRINT) ----
    # A cap ground cell directly UNDER an annotated object (any tracked box footprint:
    # parked OR moving vehicle, etc.) is not clear road -> render an honest contact-shadow
    # there instead of fake road climbing over the car ("car eaten by the road"). Use the
    # object FOOTPRINT, NOT the ray-occlusion shadow from C (that abstains the whole ground
    # BEHIND the car -> giant dark blob), and NOT a LiDAR-tall test (it fires on building
    # walls -> false road-shadow near buildings). Buildings/walls are NOT annotated, so a
    # box-footprint gate leaves road next to them as road. General, zero scene params.
    occ_t = np.zeros(len(Xg), bool)
    _allu = set(ann["track_uuid"].unique()) if (ann is not None and "track_uuid" in ann.columns) else set()
    for _c, _sz, _R in boxes_at(ann, ts, _allu):
        _loc = (Xg - _c) @ _R; _hf = _sz / 2.0
        occ_t |= (np.abs(_loc[:, 0]) < _hf[0] + 0.3) & (np.abs(_loc[:, 1]) < _hf[1] + 0.3)
    fg_occ = np.zeros(H * W, bool)
    if occ_t.any():
        _drop = flat_g[occ_t]; fg_occ[_drop] = True
        capg.reshape(-1)[_drop] = False
        _kv = ~occ_t
        flat_g, dirs_g, t_g, Xg, Xg_city = flat_g[_kv], dirs_g[_kv], t_g[_kv], Xg[_kv], Xg_city[_kv]
    fg_occ = fg_occ.reshape(H, W)
    NSLOT = 6
    chosen_g = np.full((NSLOT, len(flat_g)), -1, np.int64)
    score_g = np.full((NSLOT, len(flat_g)), np.inf)
    ai_g = int(anchor_idx)
    # CANDIDATES: displacement-BUCKETED, time-nearest WITHIN each bucket. Physics:
    # all 7 ring cameras sit in one front-roof pod, so a source ego self-occludes
    # ground 0-20 m behind itself (ray must clear its own trunk) and 0-9 m ahead
    # (hood) — the INNER cap is only ever visible from sources 20-28 m away, while
    # the outer ring prefers near ones. A pure time-nearest list misses the 20-28 m
    # band entirely (v3f: 4% coverage); a pure displacement-stratified list drags
    # in +-15 s frames whose auto-exposure drifted (v3: lavender wash). Buckets of
    # 5 m over the eligible 5-58 m range (58 = 28 + 30 m point reach), 3 frames per
    # bucket nearest in TIME = every viewing geometry present, freshest exposure
    # available for each. Whole-log search (a fixed window yields ZERO eligible
    # frames when the ego idles at a light — downtown 9.5 s stationary).
    disp_g = np.array([np.linalg.norm(cte(int(t_))[1] - ta) for t_ in all_ts])
    fis_all = np.arange(len(all_ts))
    elig_g = (np.abs(fis_all - ai_g) >= 5) & (disp_g > 5.0) & (disp_g < 58.0)
    cand_fis = []
    for b0_ in np.arange(5.0, 58.0, 5.0):
        inb_ = np.where(elig_g & (disp_g >= b0_) & (disp_g < b0_ + 5.0))[0]
        cand_fis.extend(int(x_) for x_ in inb_[np.argsort(np.abs(inb_ - ai_g))][:3])
    cand_fis = sorted(set(cand_fis))
    if GROUND_MODE == "off": cand_fis = []   # middle-only base stitch: NO ground outpaint -> nadir stays black
    # EMC FOR GROUND SOURCES: each ring camera fires up to +-22.5 ms off the sync
    # timestamp; at source-frame speeds (highway: >10 m/s) the SYNC pose is ~0.3 m
    # wrong along travel, so 6 slots land the same stripe at 6 offsets and the
    # per-pixel median pick interleaves them into a smeared multi-ghost band. Use
    # each camera's OWN capture-time pose (same principle as the scene-band EMC).
    cam_ts_arr = {ci2: np.array([int(p_.stem) for p_ in loader._image_paths[cam]], np.int64)
                  for ci2, cam in enumerate(ring_cams)}
    if GROUND_MODE == "bevaudit":
        # ---- DB-102 NO-RENDER metric-domain audit ----
        # Build a LOCAL BEV ground grid around the ego, project each cell into the SAME
        # bucketed candidates+cams with the SAME gates (FOV, egod 5-28, moving-box, two-box
        # ego self-occ), and dump per-cell radial stats {nvalid, best_grazing, az_spread,
        # lum_std}. Answers "is the determinable 3-7 m annulus recoverable in BEV?" before
        # building the renderer — measure before build ([[feedback-isolate-input-variable]]).
        HALF, CELL = 9.0, 0.08
        _gx = np.arange(-HALF, HALF, CELL)
        _GX, _GY = np.meshgrid(_gx, _gx)
        cell_xy = np.stack([_GX.ravel(), _GY.ravel()], 1).astype(np.float64)
        rr = np.linalg.norm(cell_xy, axis=1)
        _kc = (rr >= 1.0) & (rr <= 8.0)
        cell_xy = cell_xy[_kc]; rr = rr[_kc]; NC = len(cell_xy)
        cz = np.full(NC, -0.33)
        if len(_gpts) > 200:
            from scipy.spatial import cKDTree as _CKD2
            _tr2 = _CKD2(_gpts[:, :2])
            _dd2, _ii2 = _tr2.query(cell_xy, k=1)
            cz = np.where(_dd2 < 1.2, _gpts[_ii2, 2], -0.33)
        Xcell = np.concatenate([cell_xy, cz[:, None]], 1)
        Xcell_city = (Ra @ Xcell.T).T + ta
        ncount = np.zeros(NC, np.int32)
        graze_max = np.full(NC, -1.0, np.float64)
        ssin = np.zeros(NC); scos = np.zeros(NC); sl = np.zeros(NC); sl2 = np.zeros(NC)
        _gc = {}
        for fi in cand_fis:
            tsf = int(all_ts[fi])
            fboxes = [(c2_, sz2_ * 1.3, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, tsf, moving)]
            for ci2, cam in enumerate(ring_cams):
                cts_ = cam_ts_arr[ci2]
                Rf, tf = cte(int(cts_[np.argmin(np.abs(cts_ - tsf))]))
                egod = np.linalg.norm(Xcell_city - tf[None, :], axis=1)
                Xq = (Xcell_city - tf[None, :]) @ Rf
                K2, (hh2, ww2) = cals[ci2]
                T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float); Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                okq = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2) & (egod > 5.0) & (egod < 28.0)
                if not okq.any(): continue
                blocked = np.zeros(NC, bool)
                if fboxes: blocked[okq] = gseg_blocked(T2[:3, 3], Xq[okq], fboxes)
                body_lo = np.array([-2.2, -1.6, -C[2] - 0.33]); body_hi = np.array([4.6, 1.6, -C[2] + 0.67])
                cab_lo = np.array([-1.7, -1.6, -C[2] - 0.33]); cab_hi = np.array([1.0, 1.6, -0.35])
                ego_boxes = [(C + (bn_ + bx_) / 2.0, bx_ - bn_, np.eye(3)) for bn_, bx_ in ((body_lo, body_hi), (cab_lo, cab_hi))]
                selfocc = np.zeros(NC, bool); selfocc[okq] = gseg_blocked(T2[:3, 3], Xq[okq], ego_boxes)
                visq = okq & ~blocked & ~selfocc
                if not visq.any(): continue
                if tsf not in _gc: _gc[tsf] = loader.load_synced_frame(tsf)
                img2 = _gc[tsf].images[cam]
                col = bilinear(img2, px2, py2) * np.exp(gains[ci2])[None, :]
                lum = 0.299 * col[:, 0] + 0.587 * col[:, 1] + 0.114 * col[:, 2]
                horiz = np.linalg.norm((Xcell_city - tf[None, :])[:, :2], axis=1)
                graze = np.degrees(np.arctan2(np.maximum(tf[2] - Xcell_city[:, 2], 0.0), np.maximum(horiz, 1e-3)))
                az = np.arctan2(tf[1] - Xcell_city[:, 1], tf[0] - Xcell_city[:, 0])
                v = visq
                ncount += v
                graze_max = np.where(v & (graze > graze_max), graze, graze_max)
                ssin += np.where(v, np.sin(az), 0.0); scos += np.where(v, np.cos(az), 0.0)
                sl += np.where(v, lum, 0.0); sl2 += np.where(v, lum * lum, 0.0)
        n = np.maximum(ncount, 1)
        lum_std = np.sqrt(np.maximum(sl2 / n - (sl / n) ** 2, 0.0))
        az_R = np.sqrt(ssin ** 2 + scos ** 2) / n
        out = np.stack([cell_xy[:, 0], cell_xy[:, 1], rr, ncount.astype(np.float64),
                        graze_max, 1.0 - az_R, lum_std], 1).astype(np.float32)
        np.save(str(REMOTE_OUT / (run_name + "_bevaudit.npy")), out)
        print("BEVAUDIT", run_name, "NC", NC, "cols=x,y,rr,ncount,graze_max,az_spread,lum_std")
        cand_fis = []   # skip the normal per-pixel render loop
    bev_sel_px = bev_spread = bev_anyg = None
    if GROUND_MODE in ("bev", "bevdirect"):
        # ---- DB-102 metric-domain (BEV) ground reconstruction (bevdirect = DB-107: same metric selection, DIRECT ERP sampling) ----
        # Fuse the determinable annulus on a UNIFORM metric raster (no ERP pole
        # singularity, no per-pixel source jump) with the SAME gates, gate per-cell by
        # source agreement, then RESAMPLE into the cap. Audit (STEP 0) found coverage is
        # plentiful (nvalid 7-17) and the discriminator is AGREEMENT (lum_std: highway 2-3
        # =recoverable, bmw near-nadir 50 =genuine blind). Coherent raster => speckle gone
        # by construction; resampling makes the pole a smooth magnification, not noise.
        HALF, CELL = 12.0, 0.06   # ~24 m tile covers the whole cap ground (cap ~0-10 m); 160k cells < 900k cap px
        _bgx = np.arange(-HALF, HALF, CELL); BW = len(_bgx)
        _BGX, _BGY = np.meshgrid(_bgx, _bgx)               # xy indexing: [row=y, col=x]
        bev_xy = np.stack([_BGX.ravel(), _BGY.ravel()], 1).astype(np.float64)
        bev_z = np.full(len(bev_xy), -0.33)
        if len(_gpts) > 200:
            from scipy.spatial import cKDTree as _CKD3
            _tr3 = _CKD3(_gpts[:, :2]); _dd3, _ii3 = _tr3.query(bev_xy, k=1)
            bev_z = np.where(_dd3 < 1.2, _gpts[_ii3, 2], -0.33)
        Xb_city = (Ra @ np.concatenate([bev_xy, bev_z[:, None]], 1).T).T + ta
        NB = len(bev_xy); NS2 = 6
        bchosen = np.full((NS2, NB), -1, np.int64); bscore = np.full((NS2, NB), np.inf)
        for fi in cand_fis:
            tsf = int(all_ts[fi])
            fboxes = [(c2_, sz2_ * 1.3, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, tsf, moving)]
            for ci2, cam in enumerate(ring_cams):
                cts_ = cam_ts_arr[ci2]; Rf, tf = cte(int(cts_[np.argmin(np.abs(cts_ - tsf))]))
                egod = np.linalg.norm(Xb_city - tf[None, :], axis=1)
                Xq = (Xb_city - tf[None, :]) @ Rf
                K2, (hh2, ww2) = cals[ci2]; T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float); Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                okq = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2) & (egod > 5.0) & (egod < 28.0)
                if not okq.any(): continue
                blocked = np.zeros(NB, bool)
                if fboxes: blocked[okq] = gseg_blocked(T2[:3, 3], Xq[okq], fboxes)
                body_lo = np.array([-2.2, -1.6, -C[2] - 0.33]); body_hi = np.array([4.6, 1.6, -C[2] + 0.67])
                cab_lo = np.array([-1.7, -1.6, -C[2] - 0.33]); cab_hi = np.array([1.0, 1.6, -0.35])
                ego_boxes = [(C + (bn_ + bx_) / 2.0, bx_ - bn_, np.eye(3)) for bn_, bx_ in ((body_lo, body_hi), (cab_lo, cab_hi))]
                selfocc = np.zeros(NB, bool); selfocc[okq] = gseg_blocked(T2[:3, 3], Xq[okq], ego_boxes)
                visq = okq & ~blocked & ~selfocc
                if not visq.any(): continue
                code_b = fi * 10 + ci2; sc = egod.copy(); rem = visq.copy()
                for s_ in range(NS2):
                    better = rem & (sc < bscore[s_])
                    if not better.any(): continue
                    for t_ in range(NS2 - 1, s_, -1):
                        bchosen[t_][better] = bchosen[t_ - 1][better]; bscore[t_][better] = bscore[t_ - 1][better]
                    bchosen[s_][better] = code_b; bscore[s_][better] = sc[better]; rem = rem & ~better
        bcol = np.full((NS2, NB, 3), np.nan, np.float32); _bc = {}
        for slot in range(NS2):
            for code in np.unique(bchosen[slot][bchosen[slot] >= 0]):
                fi, ci2 = int(code) // 10, int(code) % 10; sel = bchosen[slot] == code; tsf = int(all_ts[fi])
                if tsf not in _bc: _bc[tsf] = loader.load_synced_frame(tsf)
                fr2 = _bc[tsf]; Rf, tf = cte(int(fr2.timestamps_ns[ring_cams[ci2]]))
                Xq = (Xb_city[sel] - tf[None, :]) @ Rf
                K2, _s2 = cals[ci2]; T2 = np.asarray(frame.calibrations[ring_cams[ci2]].T_ego_cam, float); Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                bcol[slot][sel] = np.clip(bilinear(fr2.images[ring_cams[ci2]], px2, py2) * np.exp(gains[ci2])[None, :], 0, 255).astype(np.float32)
        bhave = ~np.isnan(bcol[:, :, 0]); bany = bhave.any(0)
        bmed = np.nanmedian(bcol, axis=0); bd = np.abs(bcol - bmed[None]).sum(2)
        _bn = np.maximum(bhave.sum(0), 1); bspread_c = np.where(bhave, bd, 0.0).sum(0) / _bn; bspread_c[~bany] = 1e9
        bd[~bhave] = np.inf; bpick = np.argmin(bd, axis=0); bev_rgb = bcol[bpick, np.arange(NB)]
        bev_rgb = np.where(np.isnan(bev_rgb), 0.0, bev_rgb).astype(np.float32)
        # RESAMPLE the BEV raster into the cap (flat_g) — bilinear colour on the ego grid,
        # nearest agreement/coverage (1e9 must not bleed). col=x, row=y (xy meshgrid).
        col_f = (Xg[:, 0] + HALF) / CELL; row_f = (Xg[:, 1] + HALF) / CELL
        i0 = np.clip(np.floor(col_f).astype(int), 0, BW - 2); j0 = np.clip(np.floor(row_f).astype(int), 0, BW - 2)
        fa = np.clip(col_f - i0, 0, 1)[:, None]; fb = np.clip(row_f - j0, 0, 1)[:, None]
        R3 = bev_rgb.reshape(BW, BW, 3)
        bev_sel_px = (R3[j0, i0] * (1 - fa) * (1 - fb) + R3[j0, i0 + 1] * fa * (1 - fb)
                      + R3[j0 + 1, i0] * (1 - fa) * fb + R3[j0 + 1, i0 + 1] * fa * fb)
        ic = np.clip(np.round(col_f).astype(int), 0, BW - 1); jr = np.clip(np.round(row_f).astype(int), 0, BW - 1)
        bev_spread = bspread_c.reshape(BW, BW)[jr, ic]
        bev_anyg = bany.reshape(BW, BW)[jr, ic] & (np.linalg.norm(Xg[:, :2], axis=1) <= HALF - CELL)
        if GROUND_MODE == "bevdirect":
            # DB-107: keep bev's METRIC-CONSISTENT source choice (bchosen) but render by DIRECT ERP
            # sampling of that source per cap pixel — no raster round-trip. Kills fill's per-pixel
            # radial (neighbour cap pixels share a metric cell -> same source) AND bev's softness
            # (no source->raster->ERP double resample). Agreement gate reused from the raster.
            _icd = np.clip(np.round((Xg[:, 0] + HALF) / CELL).astype(int), 0, BW - 1)
            _jrd = np.clip(np.round((Xg[:, 1] + HALF) / CELL).astype(int), 0, BW - 1)
            cap_code = bchosen[0].reshape(BW, BW)[_jrd, _icd]
            cap_col = np.full((len(flat_g), 3), np.nan, np.float32); _bdc = {}
            for code in np.unique(cap_code[cap_code >= 0]):
                fi_, ci2_ = int(code) // 10, int(code) % 10; sel_ = cap_code == code; tsf_ = int(all_ts[fi_])
                if tsf_ not in _bdc: _bdc[tsf_] = loader.load_synced_frame(tsf_)
                fr2_ = _bdc[tsf_]; Rf_, tf_ = cte(int(fr2_.timestamps_ns[ring_cams[ci2_]]))
                Xq_ = (Xg_city[sel_] - tf_[None, :]) @ Rf_
                K2_, _s_ = cals[ci2_]; T2_ = np.asarray(frame.calibrations[ring_cams[ci2_]].T_ego_cam, float); Tci2_ = np.linalg.inv(T2_)
                Xc2_ = (Tci2_[:3, :3] @ Xq_.T).T + Tci2_[:3, 3]; z2_ = Xc2_[:, 2]
                px2_ = K2_[0, 0] * Xc2_[:, 0] / np.maximum(z2_, 1e-6) + K2_[0, 2]; py2_ = K2_[1, 1] * Xc2_[:, 1] / np.maximum(z2_, 1e-6) + K2_[1, 2]
                cap_col[sel_] = np.clip(bilinear(fr2_.images[ring_cams[ci2_]], px2_, py2_) * np.exp(gains[ci2_])[None, :], 0, 255).astype(np.float32)
            bev_sel_px = np.where(np.isnan(cap_col), 0.0, cap_col).astype(np.float32)
            bev_anyg = (cap_code >= 0) & (np.linalg.norm(Xg[:, :2], axis=1) <= HALF - CELL)
            bev_spread = bspread_c.reshape(BW, BW)[_jrd, _icd]
        _rad = np.linalg.norm(Xg[:, :2], axis=1)   # DB-102 diag: coverage/agreement by cap-pixel radius
        for _lo, _hi in [(0, 1), (1, 3), (3, 5), (5, 7), (7, 9), (9, 12), (12, 99)]:
            _mm = (_rad >= _lo) & (_rad < _hi)
            if _mm.any():
                print("BEVDIAG r%d-%d npx=%d anyg=%.2f spread_ok=%.2f rendered=%.2f" % (
                    _lo, _hi, int(_mm.sum()), float(bev_anyg[_mm].mean()),
                    float((bev_spread[_mm] <= 30).mean()),
                    float((bev_anyg[_mm] & (bev_spread[_mm] <= 30)).mean())), flush=True)
        cand_fis = []   # the per-pixel loop no-ops; bev_* override the pick below
    if GROUND_MODE == "worldbev":
        # ---- DB-109 B1: ONE world-fixed BEV ground map over a FIXED anchor window ----
        # Accumulate ALL window frames (spread-dominant, NO moving box-gate -> Stage-1c folds in;
        # nvalid>=2 guard backstops single-source car-body). Built over a FIXED window so every
        # target anchor samples the SAME deterministic map -> temporal coherence by construction.
        _wlo, _whi = WORLDBEV_WIN
        _wfis = list(range(max(0, _wlo), min(len(all_ts) - 1, _whi) + 1))
        _egos = np.array([cte(int(all_ts[_t]))[1] for _t in _wfis])
        _LAT, _CW = 18.0, 0.06
        _xmin, _ymin = _egos[:, 0].min() - _LAT, _egos[:, 1].min() - _LAT
        _xmax, _ymax = _egos[:, 0].max() + _LAT, _egos[:, 1].max() + _LAT
        _gx = np.arange(_xmin, _xmax, _CW); _gy = np.arange(_ymin, _ymax, _CW)
        _GW, _GH = len(_gx), len(_gy)
        _GXX, _GYY = np.meshgrid(_gx, _gy)
        _gzw = float(ta[2] - C[2] - 0.33)   # anchor ground world-Z (AV2 ~flat ±0.1m/60m); first cut
        _wxyz = np.stack([_GXX.ravel(), _GYY.ravel(), np.full(_GW * _GH, _gzw)], 1)
        _NWC = len(_wxyz); _NSW = 8
        _wchosen = np.full((_NSW, _NWC), -1, np.int64); _wscore = np.full((_NSW, _NWC), np.inf)
        for _fi in _wfis:
            _tsf = int(all_ts[_fi])
            for _ci, _cam in enumerate(ring_cams):
                _cts = cam_ts_arr[_ci]; _Rf, _tf = cte(int(_cts[np.argmin(np.abs(_cts - _tsf))]))
                _egod = np.linalg.norm(_wxyz - _tf[None, :], axis=1)
                _Xq = (_wxyz - _tf[None, :]) @ _Rf
                _K, (_hh, _ww) = cals[_ci]; _T = np.asarray(frame.calibrations[_cam].T_ego_cam, float); _Tc = np.linalg.inv(_T)
                _Xc = (_Tc[:3, :3] @ _Xq.T).T + _Tc[:3, 3]; _z = _Xc[:, 2]
                _px = _K[0, 0] * _Xc[:, 0] / np.maximum(_z, 1e-6) + _K[0, 2]
                _py = _K[1, 1] * _Xc[:, 1] / np.maximum(_z, 1e-6) + _K[1, 2]
                _ok = (_z > 0.5) & (_px >= 2) & (_px < _ww - 2) & (_py >= 2) & (_py < _hh - 2) & (_egod > 5.0) & (_egod < 28.0)
                if not _ok.any(): continue
                _code = _fi * 10 + _ci; _sc = _egod.copy(); _rem = _ok.copy()
                for _s in range(_NSW):
                    _b = _rem & (_sc < _wscore[_s])
                    if not _b.any(): continue
                    for _tt in range(_NSW - 1, _s, -1):
                        _wchosen[_tt][_b] = _wchosen[_tt - 1][_b]; _wscore[_tt][_b] = _wscore[_tt - 1][_b]
                    _wchosen[_s][_b] = _code; _wscore[_s][_b] = _sc[_b]; _rem = _rem & ~_b
        _wcol = np.full((_NSW, _NWC, 3), np.nan, np.float32); _wcache = {}
        for _s in range(_NSW):
            for _code in np.unique(_wchosen[_s][_wchosen[_s] >= 0]):
                _fi, _ci = int(_code) // 10, int(_code) % 10; _sel = _wchosen[_s] == _code; _tsf = int(all_ts[_fi])
                if _tsf not in _wcache: _wcache[_tsf] = loader.load_synced_frame(_tsf)
                _fr = _wcache[_tsf]; _Rf, _tf = cte(int(_fr.timestamps_ns[ring_cams[_ci]]))
                _Xq = (_wxyz[_sel] - _tf[None, :]) @ _Rf
                _K, _ = cals[_ci]; _T = np.asarray(frame.calibrations[ring_cams[_ci]].T_ego_cam, float); _Tc = np.linalg.inv(_T)
                _Xc = (_Tc[:3, :3] @ _Xq.T).T + _Tc[:3, 3]; _z = _Xc[:, 2]
                _px = _K[0, 0] * _Xc[:, 0] / np.maximum(_z, 1e-6) + _K[0, 2]
                _py = _K[1, 1] * _Xc[:, 1] / np.maximum(_z, 1e-6) + _K[1, 2]
                _wcol[_s][_sel] = np.clip(bilinear(_fr.images[ring_cams[_ci]], _px, _py) * np.exp(gains[_ci])[None, :], 0, 255).astype(np.float32)
        _wh = ~np.isnan(_wcol[:, :, 0]); _wnv = _wh.sum(0); _wany = _wnv >= 2   # nvalid>=2 guard (single-source car-body backstop)
        _wmed = np.nanmedian(_wcol, axis=0); _wdd = np.abs(_wcol - _wmed[None]).sum(2)
        _wn = np.maximum(_wh.sum(0), 1); _wspr = np.where(_wh, _wdd, 0.0).sum(0) / _wn; _wspr[~_wany] = 1e9
        _wdd2 = _wdd.copy(); _wdd2[~_wh] = np.inf; _wpick = np.argmin(_wdd2, axis=0)
        _wmap = _wcol[_wpick, np.arange(_NWC)]; _wmap = np.where(np.isnan(_wmap), 0.0, _wmap).astype(np.float32)
        _wok = _wany & (_wspr <= 30.0)
        np.save(str(REMOTE_OUT / (run_name + "_worldmap.npy")), np.where(_wok[:, None], _wmap, 0).reshape(_GH, _GW, 3).astype(np.uint8))
        np.save(str(REMOTE_OUT / (run_name + "_worldcov.npy")), (_wok.reshape(_GH, _GW) * 255).astype(np.uint8))
        print("WORLDBEV", run_name, "grid", _GW, "x", _GH, "win", _wlo, _whi, "ok_pct", round(100.0 * float(_wok.mean()), 1), flush=True)
        # sample the shared world map at each cap ground point (Xg_city world XY)
        _cf = (Xg_city[:, 0] - _xmin) / _CW; _rf = (Xg_city[:, 1] - _ymin) / _CW
        _i0 = np.clip(np.floor(_cf).astype(int), 0, _GW - 2); _j0 = np.clip(np.floor(_rf).astype(int), 0, _GH - 2)
        _fa = np.clip(_cf - _i0, 0, 1)[:, None]; _fb = np.clip(_rf - _j0, 0, 1)[:, None]
        _Wm = _wmap.reshape(_GH, _GW, 3); _Wok = _wok.reshape(_GH, _GW)
        _cap = (_Wm[_j0, _i0] * (1 - _fa) * (1 - _fb) + _Wm[_j0, _i0 + 1] * _fa * (1 - _fb)
                + _Wm[_j0 + 1, _i0] * (1 - _fa) * _fb + _Wm[_j0 + 1, _i0 + 1] * _fa * _fb)
        _ic = np.clip(np.round(_cf).astype(int), 0, _GW - 1); _jr = np.clip(np.round(_rf).astype(int), 0, _GH - 1)
        bev_sel_px = _cap.astype(np.float32); bev_anyg = _Wok[_jr, _ic]; bev_spread = np.where(_Wok[_jr, _ic], 0.0, 1e9)
        _inb = (_cf >= 0) & (_cf < _GW) & (_rf >= 0) & (_rf < _GH)
        print("WBEVCAP %s capX[%.1f,%.1f] capY[%.1f,%.1f] | gridX[%.1f,%.1f] gridY[%.1f,%.1f] | ingrid%%=%.1f onok%%=%.1f | ncap=%d" % (
            run_name, float(Xg_city[:, 0].min()), float(Xg_city[:, 0].max()), float(Xg_city[:, 1].min()), float(Xg_city[:, 1].max()),
            _xmin, _xmax, _ymin, _ymax, 100.0 * float(_inb.mean()), 100.0 * float(bev_anyg.mean()), len(Xg_city)), flush=True)
        cand_fis = []
    for fi in cand_fis:
        tsf = int(all_ts[fi])
        fboxes = ([(c2_, sz2_ * MOVING_SCALE, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, tsf, moving)] if MOVING_GATE else [])   # DB-109 Stage-1b/1c: MOVING_GATE off = isolation; MOVING_SCALE shrinks the box toward a precise gate (1.3=shipped, 1.0=precise)
        for ci2, cam in enumerate(ring_cams):
            cts_ = cam_ts_arr[ci2]
            Rf, tf = cte(int(cts_[np.argmin(np.abs(cts_ - tsf))]))
            egod = np.linalg.norm(Xg_city - tf[None, :], axis=1)
            Xq = (Xg_city - tf[None, :]) @ Rf
            K2, (hh2, ww2) = cals[ci2]
            T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float)
            Tci2 = np.linalg.inv(T2)
            Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
            px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
            py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
            okq = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2) & (egod > 5.0) & (egod < 28.0)
            if not okq.any(): continue
            blocked = np.zeros(len(flat_g), bool)
            if fboxes:
                blocked[okq] = gseg_blocked(T2[:3, 3], Xq[okq], fboxes)
            # SOURCE-EGO SELF-OCCLUSION (proven by single-source isolation): rays
            # from a source camera to ground points ~5-9 m ahead graze the source's
            # OWN hood, so the sample is hood sky-reflection (bluish smears), not
            # road. egod is the wrong geometry (point distance, not ray clearance).
            # TWO-BOX ego model: a roof-height single box over the full length
            # blocks legal over-the-trunk rear views (downtown's only inner-cap
            # sources, 15-19.6 m, collapsed to 22% coverage) — the real vehicle is
            # cabin-high only mid-body; hood and trunk are ~1.0 m. Full-length low
            # box + cabin-height short box, gseg's internal 1.05 the only margin.
            body_lo = np.array([-2.2, -1.6, -C[2] - 0.33]); body_hi = np.array([4.6, 1.6, -C[2] + 0.67])
            cab_lo = np.array([-1.7, -1.6, -C[2] - 0.33]); cab_hi = np.array([1.0, 1.6, -0.35])
            ego_boxes = [(C + (bn_ + bx_) / 2.0, bx_ - bn_, np.eye(3))
                         for bn_, bx_ in ((body_lo, body_hi), (cab_lo, cab_hi))]
            selfocc = np.zeros(len(flat_g), bool)
            selfocc[okq] = gseg_blocked(T2[:3, 3], Xq[okq], ego_boxes)
            visq = okq & ~blocked & ~selfocc
            if not visq.any(): continue
            code_g = fi * 10 + ci2
            sc = egod.copy()
            rem = visq.copy()
            for s_ in range(NSLOT):
                better = rem & (sc < score_g[s_])
                if not better.any(): continue
                for t_ in range(NSLOT - 1, s_, -1):
                    chosen_g[t_][better] = chosen_g[t_ - 1][better]; score_g[t_][better] = score_g[t_ - 1][better]
                chosen_g[s_][better] = code_g; score_g[s_][better] = sc[better]
                rem = rem & ~better
    colg = np.full((NSLOT, len(flat_g), 3), np.nan, np.float32)
    gcache = {}
    for slot in range(NSLOT):
        for code in np.unique(chosen_g[slot][chosen_g[slot] >= 0]):
            fi, ci2 = int(code) // 10, int(code) % 10
            sel = chosen_g[slot] == code
            tsf = int(all_ts[fi])
            if tsf not in gcache:
                gcache[tsf] = loader.load_synced_frame(tsf)
            fr2 = gcache[tsf]
            Rf, tf = cte(int(fr2.timestamps_ns[ring_cams[ci2]]))   # capture-time pose (EMC)
            Xq = (Xg_city[sel] - tf[None, :]) @ Rf
            K2, _s2 = cals[ci2]
            T2 = np.asarray(frame.calibrations[ring_cams[ci2]].T_ego_cam, float)
            Tci2 = np.linalg.inv(T2)
            Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
            px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
            py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
            img2 = fr2.images[ring_cams[ci2]]
            g2 = np.exp(gains[ci2])[None, :]
            colg[slot][sel] = np.clip(bilinear(img2, px2, py2) * g2, 0, 255).astype(np.float32)
    haveg = ~np.isnan(colg[:, :, 0])
    anyg = haveg.any(0)
    medg = np.nanmedian(colg, axis=0)
    dist_s = np.abs(colg - medg[None]).sum(2)
    # SOURCE-AGREEMENT GATE (DB-98): the near-nadir-behind corners are seen only by
    # far, grazing sources that DISAGREE wildly (each grazing ray skims different
    # content); the per-pixel pick then jumps between them -> radial black streaks.
    # spread = mean abs deviation of the valid sources from their median; high spread
    # = views don't agree = unreliable. We render real pixels only where they AGREE
    # (spread small) and abstain elsewhere -> smooth fill. (Isolation-verified: the
    # spread map co-locates exactly with the streak wedges; nvalid/t_g gates did not.)
    _ns_count = np.maximum(haveg.sum(0), 1)
    spread = np.where(haveg, dist_s, 0.0).sum(0) / _ns_count
    spread[~anyg] = 1e9
    if GROUND_MODE == "funnel":   # DB-109 Stage-1: per-pixel GATE FUNNEL — split "no-source" into geometry-blind (N1=0, TRUE wall) vs rule-rejected (egod / self-occ / moving / spread). Diagnostic-only; runs ON TOP of the normal fill path, does NOT change fill/bev output.
        NF = len(flat_g)
        f_fov = np.zeros(NF, bool)        # N1: ray lands in SOME ring-cam FOV (egod ignored)
        f_noselfocc = np.zeros(NF, bool)  # N2: + not ego-self-occluded
        f_egod = np.zeros(NF, bool)       # N3: + egod in [5,28]
        _ebx = [(C + (bn_ + bx_) / 2.0, bx_ - bn_, np.eye(3))
                for bn_, bx_ in ((np.array([-2.2, -1.6, -C[2] - 0.33]), np.array([4.6, 1.6, -C[2] + 0.67])),
                                 (np.array([-1.7, -1.6, -C[2] - 0.33]), np.array([1.0, 1.6, -0.35])))]
        for fi in cand_fis:
            tsf = int(all_ts[fi])
            for ci2, cam in enumerate(ring_cams):
                cts_ = cam_ts_arr[ci2]
                Rf, tf = cte(int(cts_[np.argmin(np.abs(cts_ - tsf))]))
                egod_f = np.linalg.norm(Xg_city - tf[None, :], axis=1)
                Xq = (Xg_city - tf[None, :]) @ Rf
                K2, (hh2, ww2) = cals[ci2]
                T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float)
                Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                infov = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2)
                if not infov.any(): continue
                so = np.zeros(NF, bool); so[infov] = gseg_blocked(T2[:3, 3], Xq[infov], _ebx)
                ok_egod = (egod_f > 5.0) & (egod_f < 28.0)
                f_fov |= infov
                f_noselfocc |= infov & ~so
                f_egod |= infov & ~so & ok_egod
        cls = np.zeros(NF, np.uint8)   # highest gate each blind pixel reaches (0=geom-blind ... 5=real)
        cls[f_fov] = 1; cls[f_noselfocc] = 2; cls[f_egod] = 3
        cls[anyg] = 4; cls[anyg & (spread <= 30.0)] = 5
        _cm = np.full(H * W, 255, np.uint8); _cm[flat_g] = cls   # 255 = not-a-cap pixel; 0..5 = highest gate the blind cap pixel reaches (0=geom-blind ... 5=real)
        np.save(str(REMOTE_OUT / (run_name + "_funnel_cls.npy")), _cm.reshape(H, W))
        _counts = {int(k): int((cls == k).sum()) for k in range(6)}
        _funnel = {
            "run": run_name, "n_blind": int(NF), "counts_by_gate": _counts,
            "pct": {int(k): round(100.0 * v / max(NF, 1), 1) for k, v in _counts.items()},
            "gate_legend": {0: "geometry-blind: no cam EVER saw it (N1=0, TRUE wall, generation-only)",
                            1: "FOV-hit but fully ego-self-occluded (killed at self-occ)",
                            2: "passed self-occ but egod out of [5,28] (RULE-REJECTED by the 28m cut)",
                            3: "passed egod but moving-box occluded",
                            4: "had source(s) but they disagree (spread>30)",
                            5: "REAL (written)"},
            "candidates": [{"fi": int(_f), "disp_m": round(float(disp_g[_f]), 1), "dt_frames": int(_f - ai_g)} for _f in cand_fis]}
        (REMOTE_OUT / (run_name + "_funnel_counts.json")).write_text(json.dumps(_funnel, indent=1), encoding="utf-8")
        print("FUNNEL", run_name, _funnel["pct"], flush=True)
    if GROUND_MODE == "diag":   # DATA EVIDENCE of the blind spot: per cap pixel -> #valid sources + nearest-source distance
        _nv = np.full(H * W, np.nan, np.float32); _nv[flat_g] = haveg.sum(0).astype(np.float32)
        _eg = np.full(H * W, np.nan, np.float32); _eg[flat_g] = np.where(np.isfinite(score_g[0]), score_g[0], np.nan).astype(np.float32)
        np.save(str(REMOTE_OUT / (run_name + "_diag_nvalid.npy")), _nv.reshape(H, W))
        np.save(str(REMOTE_OUT / (run_name + "_diag_nearestegod.npy")), _eg.reshape(H, W))
        _spd = np.full(H * W, np.nan, np.float32); _spd[flat_g] = np.asarray(spread, np.float32); np.save(str(REMOTE_OUT / (run_name + "_diag_spread.npy")), _spd.reshape(H, W))   # DB-108 AUDIT: per-cap spread map -> real-write(spread<=30) vs sources-disagree(>30) for the real-vs-inpaint overlay
    dist_s[~haveg] = np.inf
    pick = np.argmin(dist_s, axis=0)
    sel_px = colg[pick, np.arange(len(flat_g))]
    if GROUND_MODE in ("bev", "bevdirect", "worldbev") and bev_sel_px is not None:   # DB-102/107 + DB-109 B1: metric-fused cap overrides the per-pixel pick
        anyg = bev_anyg; spread = bev_spread; sel_px = bev_sel_px.astype(np.float32)
    # GLOBAL cast correction to the anchor truth ring: the inner cap is only ever
    # visible at 4-6 deg grazing (front-pod rig blocks all steeper views), where
    # asphalt specularly reflects the SKY — sunny scene -> blue-lavender cast that
    # clashes with the steep-view road in the scene band directly above. ONE global
    # per-channel gain to the median of the anchor's own lowest scene-band rows:
    # no regional boundaries (per-region clipped gains quilted, tested NEG), the
    # within-fill texture untouched, only the cast removed.
    nonb_r = comp.astype(np.int32).sum(2) >= 12
    ring_px = []
    for u_ in range(0, W, 4):
        rs_ = np.nonzero(nonb_r[H // 2:, u_])[0]
        if len(rs_) >= 4:
            ring_px.append(comp[H // 2 + rs_[-10:], u_])
    if ring_px and anyg.any():
        ref_med = np.median(np.concatenate(ring_px).reshape(-1, 3).astype(np.float32), axis=0)
        fill_med = np.median(sel_px[anyg], axis=0)
        gn_glob = np.clip(ref_med / np.maximum(fill_med, 1.0), 0.7, 1.5)
        sel_px[anyg] = sel_px[anyg] * gn_glob[None, :]
    cflat = comp.reshape(-1, 3).copy()
    SPREAD_MAX = 30.0   # abstain where the sources disagree more than this (units: sum-abs-channel dev)
    _gm = anyg & (spread <= SPREAD_MAX)
    cflat[flat_g[_gm]] = np.clip(sel_px[_gm], 0, 255).astype(np.uint8)
    comp = cflat.reshape(H, W, 3)
    # DB-99 nadir floor (replaces the NS-inpaint + heavy wv low-pass that produced the
    # 白团 swirl): the abstain/empty cap cells get a STRUCTURELESS per-anchor truth-ring
    # DC plate (reuse ref_med, the road tone just above the cap) — no invented low-freq
    # structure => no swirl, no radial NS streak. The agreeing REAL cap pixels keep the
    # SAME resolution-matched low-pass as before (kills grazing speckle). Honest:
    # real where evidence agrees, flat-honest where it does not. No NS, no grain, no
    # cross-anchor fusion. (Round-2 workflow DB-99; see agent/decision_briefs.md.)
    resid_m = (comp.astype(np.int32).sum(2) < 12)   # DB-106: residual fill = ONLY scene-band-black px (dropped the egoproj term — it painted plate-dark over the real near-car lower body)
    resid_m[:H // 2] = False
    resid_m &= ~fg_occ   # foreground-occluded handled separately (shadow), not as normal ground abstain
    fillzone = capg | resid_m
    # truth-ring asphalt tone (per-anchor, view-dependent -> Fresnel-safe)
    plate_rgb = locals().get('ref_med', None)
    if plate_rgb is None:
        _low = comp[H // 2:].reshape(-1, 3).astype(np.float32); _low = _low[_low.sum(1) >= 12]
        plate_rgb = np.median(_low, axis=0) if len(_low) else np.float32([60, 60, 60])
    plate_rgb = np.asarray(plate_rgb, np.float32)
    _rows = np.arange(H, dtype=np.float32); _r0 = H * 0.55
    _dark = 1.0 - 0.10 * np.clip((_rows - _r0) / max(H - _r0, 1.0), 0.0, 1.0)
    if resid_m.any() and GROUND_MODE != "off":   # evidence-insufficient ground (DB-108): "plate"=honest gray (default) / "inpaint"=NS-inpaint ground-feel (combo, video-era look)
        if GROUND_RESID == "inpaint":
            comp = _cv.inpaint(comp, resid_m.astype(np.uint8) * 255, 8, _cv.INPAINT_NS)
        else:
            _rr = np.nonzero(resid_m)[0]
            comp[resid_m] = np.clip(plate_rgb[None, :] * _dark[_rr][:, None], 0, 255).astype(np.uint8)
    if fg_occ.any() and GROUND_MODE != "off":    # ground occluded by a near object -> honest SHADOW (not black hole, not fake road)
        comp[fg_occ] = np.clip(plate_rgb * 0.55, 0, 255).astype(np.uint8)
    real_cap = capg & ~resid_m
    if real_cap.any():
        comp_f = comp.astype(np.float32)
        b1_ = _cv.GaussianBlur(comp_f, (0, 0), 3)
        b2_ = _cv.GaussianBlur(comp_f, (0, 0), 9)
        wv_ = np.clip((np.arange(H, dtype=np.float32) - H * 0.55) / (H * 0.45), 0, 1) ** 1.5
        low_ = b1_ * (1 - wv_[:, None, None]) + b2_ * wv_[:, None, None]
        sm_ = comp_f * (1 - wv_[:, None, None]) + low_ * wv_[:, None, None]
        comp[real_cap] = np.clip(sm_[real_cap], 0, 255).astype(np.uint8)
    vismask = comp.copy()
    vismask[fg_occ] = np.array([255, 0, 0], np.uint8)   # DB-101 debug: target-gated foreground (red)
    # DB-101 MIDDLE-ONLY mode: do NOT outpaint the under-determined nadir cap; mask it honestly.
    # The determinable scene band (incl. directly-seen near-ground) is untouched; only the unseen
    # cap becomes a clean neutral abstain (standalone) + an explicit alpha mask (for Cosmos outpaint).
    nadir_alpha = (_capfull.astype(np.uint8) * 255)
    if GROUND_MODE == "mask":
        comp[_capfull] = np.array([48, 48, 48], np.uint8)
        vismask = comp.copy()
    ground_stats = {"cap_px": int(capg.sum()), "filled_px": int(anyg.sum()),
                    "coverage_pct": round(float(anyg.mean() * 100), 1) if len(flat_g) else 0.0,
                    "residual_inpaint_px": int(resid_m.sum()),
                    "cand_frames": len(cand_fis),
                    "cand_disp_m": [round(float(disp_g[cand_fis[0]]), 1), round(float(disp_g[cand_fis[-1]]), 1)] if cand_fis else None,
                    "low_coverage_warning": bool(len(flat_g) and anyg.mean() < 0.5)}
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
    save_rgb(REMOTE_OUT / f"{run_name}_vismask.png", vismask)
    save_rgb(REMOTE_OUT / f"{run_name}_nadirmask.png", np.dstack([nadir_alpha] * 3))
    from PIL import Image as I
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    rows = []
    for tag, im in (("EMC base", emc), (f"SEG-COMPOSITE (objs={n_handled} unmatched={n_unmatched} secondary={n_secondary}px filled={n_filled}px)", comp)):
        pil = I.fromarray(im).resize((1400, 700))
        bar = I.new("RGB", (1400, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), f"{run_name}  {tag}", (235, 235, 245), font=f)
        o = I.new("RGB", (1400, 724)); o.paste(bar, (0, 0)); o.paste(pil, (0, 24)); rows.append(o)
    board = I.new("RGB", (1400, 724 * 2 + 12), (8, 8, 12))
    yo = 6
    for o in rows: board.paste(o, (0, yo)); yo += o.height
    board.save(REMOTE_OUT / f"{run_name}_db89_board.jpg", quality=90)
    return {"case": run_name, "n_objects_composited": int(n_handled), "n_unmatched": int(n_unmatched),
            "n_secondary_body_px": int(n_secondary), "n_temporal_filled_px": int(n_filled),
            "omc_shifts": omc, "view_morph": morph_report, "ground_fill": ground_stats}


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
