"""DB-77B Branch B leashed renderer — Phase 0+1 (geometry skeleton + IBR single-centre render).

Goal: a GENERAL, PLAUSIBLE single-centre ERP where the near-field seam disappears,
without hallucinating salient objects. Geometry owns POSITION, learned owns APPEARANCE,
real pixels + geometry + hard object-protection are the LEASH.

Phase 0 (this file) — geometry skeleton: fuse multi-frame-LiDAR (Battery 4) + forward-stereo
(Battery 3) depth into a per-ERP-pixel dense depth Z(d) + confidence, from the virtual centre.
Phase 1 (this file) — IBR single-centre render: for each ERP pixel, X = Z(d)*d (ego), project
into every ring cam, ULR depth-correct blend (FOV weight x depth-visibility). With CORRECT depth,
two cameras that see the same surface AGREE → blending them does NOT ghost → the near-field seam
reduces where geometry exists. Holes / low-confidence → generated_mask band (the ONLY place the
Phase-2 refiner is allowed to touch).
Phase 2 (separate, needs A100) — band-confined single-step refiner (Difix-style), leashed by real
pixels + geometry + hard object-protection, fills only the generated_mask band.

This is the un-tried wall-breaker (band-confined geometry-leashed IBR). Distinction from prior NEG:
DiT360 seam-completion = naked generation (no leash, invents cars); DrivingForward = leash mis-tuned
(blur). Here the leash = real LiDAR/stereo geometry + ULR real-pixel blend + band confinement.

Phase 0+1 are geometry/numpy/cv2/scipy → L4/CPU sufficient (GPU-light). One bounded /status+/exec.
Runtime secret read only from process env / non-repo file; remote gets no token; secret-scan 0.
PLAUSIBLE, NOT source-faithful: the IBR-blended RGB is a presentation render; holes carry generated_mask.
"""
from __future__ import annotations

import argparse
import base64
import json
import py_compile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, rel, safe_status, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db77b_leashed_renderer"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db77b_leashed_renderer"
P01_RESULT = REMOTE_OUT + "/DB77b_p01_remote_result.json"

TH = {
    "lidar_window_frames": 10,
    "depth_min_m": 1.5, "depth_max_m": 80.0,
    "stereo_scale": 0.5,
    "densify_conf_scale_px": 9.0,     # confidence = exp(-dist_to_nearest_geom / scale)
    "generated_conf_thr": 0.45,       # below this -> hard_select fallback + generated_mask (refiner zone). v0.1: gate IBR to high-conf only.
    "ibr_min_weight": 1e-3,
}

FETCH = {
    "summary": ("DB77b_p01_summary.json", 16),
    "remote_result": ("DB77b_p01_remote_result.json", 16),
    "board": ("DB77b_p01_review_board.jpg", 70),
    "bmw_board": ("02a00399_a000_bmw_p01_board.jpg", 70),
    "bmw_pD_board": ("02a00399_a000_bmw_pD_board.jpg", 70),
    "bmw_pD_attr": ("02a00399_a000_bmw_pD_tear_attribution.png", 30),
    "bmw_pD_densify": ("02a00399_a000_bmw_pD_densify_residual_heat.png", 30),
    "clean_pD_densify": ("0bae3b5e_a030_clean_far_pD_densify_residual_heat.png", 30),
    "bmw_pD_road": ("02a00399_a000_bmw_pD_road_only_ibr.png", 40),
    "clean_pD_board": ("0bae3b5e_a030_clean_far_pD_board.jpg", 70),
    "bmw_ibr": ("02a00399_a000_bmw_p01_ibr_rgb.png", 40),
    "bmw_roi": ("02a00399_a000_bmw_p01_roi_sheet.jpg", 60),
    "bmw_genmask": ("02a00399_a000_bmw_p01_generated_mask.png", 20),
    "bmw_depth": ("02a00399_a000_bmw_p01_depth_heat.png", 30),
    "clean_board": ("0bae3b5e_a030_clean_far_p01_board.jpg", 70),
    "clean_ibr": ("0bae3b5e_a030_clean_far_p01_ibr_rgb.png", 40),
    "clean_roi": ("0bae3b5e_a030_clean_far_p01_roi_sheet.jpg", 60),
    "claim": ("DB77b_p01_claim.json", 16),
}


def remote_p01_python() -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__P01_RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [pathlib.Path("/content/waymo2panorama"), pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama")]
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"), ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far")]
MARKED_ROIS = {"left_road_patch": (250, 515, 460, 715), "lower_center_road_patch": (740, 595, 1035, 745),
               "center_lane_marking": (1030, 515, 1325, 735), "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760)}
TH = json.loads(r"""__TH__""")
WINDOW = int(TH["lidar_window_frames"]); DMIN, DMAX = float(TH["depth_min_m"]), float(TH["depth_max_m"])
SSCALE = float(TH["stereo_scale"]); CONF_SCALE = float(TH["densify_conf_scale_px"])
GEN_THR = float(TH["generated_conf_thr"]); WMIN = float(TH["ibr_min_weight"])
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}

OUT = {"phase": "db77b_phase0_phase1_geometry_skeleton_and_ibr", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"presentation_render": True, "source_faithful": False, "iterative_diffusion": False, "model_inference": False, "red_promotion": False, "refiner": False}}


def import_ok(n):
    try:
        __import__(n); return True
    except Exception:
        return False


def find_workdir():
    for c in WORKDIR_CANDIDATES:
        if (c / "scripts" / "phase3" / "run_a1_streetview_pipeline.py").exists():
            return c
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
    if path.suffix.lower() in (".jpg", ".jpeg"):
        cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), int(q)])
    else:
        cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR))


def heat(x, valid=None):
    import cv2
    a = x.astype(np.float32); vals = a[valid] if valid is not None and bool(np.any(valid)) else a.reshape(-1)
    if vals.size == 0: return np.zeros((*x.shape, 3), np.uint8)
    lo, hi = np.percentile(vals, [2, 98]); u = np.clip((a - lo) * 255.0 / max(hi - lo, 1e-6), 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.applyColorMap(u, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)


def overlay(rgb, mask, color, alpha=0.6):
    out = rgb.astype(np.float32).copy(); m = mask.astype(bool)
    for c in range(3):
        out[..., c][m] = (1 - alpha) * out[..., c][m] + alpha * color[c]
    return np.clip(out, 0, 255).astype(np.uint8)


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
    return log_dir, ts, frame, list(RING_CAMS_7), weights, valid_any, base


# ---- geometry sources (multi-frame LiDAR + forward stereo) ----
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


def stereo_depth(log_dir, anchor_ts):
    import cv2, pandas as pd
    from scipy.spatial.transform import Rotation
    cams = log_dir / "sensors" / "cameras"; intr = pd.read_feather(log_dir / "calibration" / "intrinsics.feather"); extr = pd.read_feather(log_dir / "calibration" / "egovehicle_SE3_sensor.feather")
    def cal(n):
        ri = intr[intr.sensor_name == n].iloc[0]; re = extr[extr.sensor_name == n].iloc[0]
        K = np.array([[ri.fx_px, 0, ri.cx_px], [0, ri.fy_px, ri.cy_px], [0, 0, 1]], float)
        _k = (float(ri.get("k1", 0)), float(ri.get("k2", 0)), float(ri.get("k3", 0)))
        # B3: avoid double-undistort — AV2 imagery is delivered rectified; only pass distortion if k's are non-trivial
        dist = np.array([_k[0], _k[1], 0, 0, _k[2]], float) if max(abs(v) for v in _k) > 0.01 else np.zeros(5, float)
        T = np.eye(4); T[:3, :3] = Rotation.from_quat([re.qx, re.qy, re.qz, re.qw]).as_matrix(); T[:3, 3] = [re.tx_m, re.ty_m, re.tz_m]
        return K, dist, T, int(ri.width_px), int(ri.height_px)
    KL, dL, TL, w, h = cal("stereo_front_left"); KR, dR, TR, _, _ = cal("stereo_front_right")
    def near(cam):
        ps = sorted((cams / cam).glob("*.jpg")); ts = np.array([int(p.stem) for p in ps]); i = int(np.argmin(np.abs(ts - anchor_ts)))
        return cv2.cvtColor(cv2.imread(str(ps[i])), cv2.COLOR_BGR2RGB)
    il, ir = near("stereo_front_left"), near("stereo_front_right")
    M = np.linalg.inv(TR) @ TL; R1, R2, P1, P2, Q, _a, _b = cv2.stereoRectify(KL, dL, KR, dR, (w, h), M[:3, :3], M[:3, 3], flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    mlx, mly = cv2.initUndistortRectifyMap(KL, dL, R1, P1, (w, h), cv2.CV_32FC1); mrx, mry = cv2.initUndistortRectifyMap(KR, dR, R2, P2, (w, h), cv2.CV_32FC1)
    rl = cv2.remap(il, mlx, mly, cv2.INTER_LINEAR); rr = cv2.remap(ir, mrx, mry, cv2.INTER_LINEAR)
    ds = (int(w * SSCALE), int(h * SSCALE)); gl = cv2.cvtColor(cv2.resize(rl, ds), cv2.COLOR_RGB2GRAY); gr = cv2.cvtColor(cv2.resize(rr, ds), cv2.COLOR_RGB2GRAY)
    sg = cv2.StereoSGBM_create(minDisparity=0, numDisparities=128, blockSize=5, P1=8 * 3 * 25, P2=32 * 3 * 25, disp12MaxDiff=1, uniquenessRatio=10, speckleWindowSize=100, speckleRange=2, mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
    disp = cv2.resize(sg.compute(gl, gr).astype(np.float32) / 16.0, (w, h), interpolation=cv2.INTER_NEAREST) / SSCALE
    pts = cv2.reprojectImageTo3D(disp, Q); Z = pts[..., 2]; ok = (disp > 0.5) & np.isfinite(Z) & (Z > DMIN) & (Z < DMAX)
    pr = pts[ok].reshape(-1, 3); p_origL = (R1.T @ pr.T).T; ego = (TL[:3, :3] @ p_origL.T).T + TL[:3, 3]
    return ego


def scatter_depth(pts):
    depth = np.full((H, W), 0.0, np.float32)
    if len(pts) == 0: return depth
    u, v, d = ego_to_uv(pts); m = (d > DMIN) & (d < DMAX)
    ui = np.round(u[m]).astype(np.int64); vi = np.round(v[m]).astype(np.int64); dd = d[m]
    ib = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H); ui, vi, dd = ui[ib], vi[ib], dd[ib]
    flat = vi * W + ui; order = np.argsort(-dd); sd = depth.reshape(-1); sd[flat[order]] = dd[order]
    return depth


def fuse_and_densify(lidar_depth, stereo_depth_map):
    from scipy.ndimage import distance_transform_edt
    Zsp = lidar_depth.copy(); sm = stereo_depth_map > 0; Zsp[sm] = stereo_depth_map[sm]  # stereo (more accurate near-forward) wins
    valid = Zsp > 0
    if not valid.any():
        return np.full((H, W), 8.0, np.float32), np.zeros((H, W), np.float32), valid
    dist, inds = distance_transform_edt(~valid, return_distances=True, return_indices=True)
    Zd = Zsp[inds[0], inds[1]].astype(np.float32); conf = np.exp(-dist / CONF_SCALE).astype(np.float32)
    return Zd, conf, valid


def ibr_render(Zd, dirs, frame, ring_cams, weights, base):
    # bug-fixed IBR: (B1) per-camera z-buffer occlusion test — drop a camera for an ERP ray
    # if a nearer surface occludes it in that camera; (B2) depth-correct ULR weight — FOV-center
    # falloff on the actual depth-correct projection, NOT the L1 infinite-radius cos2 feather.
    import cv2
    X = (Zd[..., None] * dirs).astype(np.float64)  # (H,W,3) ego surface point per ERP ray
    acc = np.zeros((H, W, 3), np.float32); wsum = np.zeros((H, W), np.float32); viscount = np.zeros((H, W), np.int32)
    OCC_TOL = 0.5  # m: a ray is occluded if a surface >0.5m nearer projects to the same camera pixel
    for ci, cam in enumerate(ring_cams):
        cal = frame.calibrations[cam]; img = frame.images[cam].astype(np.float32); K = cal.K; Tci = np.linalg.inv(cal.T_ego_cam)
        Xc = np.einsum('ij,hwj->hwi', Tci[:3, :3], X) + Tci[:3, 3]; z = Xc[..., 2]
        px = K[0, 0] * (Xc[..., 0] / np.maximum(z, EPS)) + K[0, 2]; py = K[1, 1] * (Xc[..., 1] / np.maximum(z, EPS)) + K[1, 2]
        hh, ww = img.shape[:2]
        inb = (z > 0.1) & (px >= 0) & (px < ww - 1) & (py >= 0) & (py < hh - 1)
        yi = np.clip(py.astype(np.int64), 0, hh - 1); xi = np.clip(px.astype(np.int64), 0, ww - 1)
        # B1: per-camera z-buffer — nearest surface per camera pixel; ERP ray occluded if it sits behind it
        zbuf = np.full(hh * ww, np.inf, np.float32)
        flat = (yi * ww + xi).reshape(-1); zf = z.reshape(-1).astype(np.float32); inf_ = inb.reshape(-1)
        order = np.argsort(-zf)  # far first -> nearest overwrites on duplicate camera pixels
        of = flat[order]; oz = zf[order]; oi = inf_[order]
        zbuf[of[oi]] = oz[oi]
        nearest = zbuf[(yi * ww + xi)]
        vis = inb & (z <= nearest + OCC_TOL)
        # B2: depth-correct ULR weight — radial FOV falloff from the principal point of the actual projection
        rad = np.hypot((px - K[0, 2]) / (0.5 * ww), (py - K[1, 2]) / (0.5 * hh))
        wv = (np.clip(1.0 - rad, 0.0, 1.0) ** 2 * vis).astype(np.float32)
        col = cv2.remap(img, px.astype(np.float32), py.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        acc += wv[..., None] * col; wsum += wv; viscount += vis.astype(np.int32)
    has = wsum > WMIN; ibr = base.astype(np.float32).copy()
    ibr[has] = (acc[has] / wsum[has][..., None])
    return np.clip(ibr, 0, 255).astype(np.uint8), has, viscount


def roi_sheet(run_name, base, ibr, gen):
    import cv2
    from PIL import Image
    rows = []
    for name, (x0, y0, x1, y1) in MARKED_ROIS.items():
        b = base[y0:y1, x0:x1]; r = ibr[y0:y1, x0:x1]; g = overlay(ibr[y0:y1, x0:x1], gen[y0:y1, x0:x1], (255, 40, 220), 0.5)
        strip = np.concatenate([b, r, g], 1)
        strip = cv2.resize(strip, (900, int(strip.shape[0] * 900 / strip.shape[1])))
        rows.append((name, strip))
    return rows


def run_case(case_spec, run_name, workdir):
    import cv2
    from PIL import Image, ImageDraw, ImageFont
    log_dir, ts, frame, ring_cams, weights, valid_any, base = ring_skeleton(case_spec, workdir)
    cte, tri = load_pose_interp(log_dir)
    try:
        import pandas as pd; ann = pd.read_feather(log_dir / "annotations.feather") if (log_dir / "annotations.feather").exists() else None
    except Exception:
        ann = None
    lidar = accumulate_lidar(log_dir, ts, cte, tri, ann); ster = stereo_depth(log_dir, ts)
    ld = scatter_depth(lidar); sd = scatter_depth(ster)
    Zd, conf, geomvalid = fuse_and_densify(ld, sd)
    dirs = erp_dirs()
    ibr_raw, has, viscount = ibr_render(Zd, dirs, frame, ring_cams, weights, base)
    # v0.1: gate IBR to HIGH-confidence (reliable geometry) only; low-conf -> hard_select
    # fallback to kill the near-ground smear seen in v0. Keep IBR where it demonstrably
    # closes the seam (dense geometry), fall back to hard_select where depth is unreliable.
    highconf = has & (conf >= GEN_THR)
    ibr = np.where(highconf[..., None], ibr_raw, base).astype(np.uint8)
    gen = (~highconf) & valid_any     # generated_mask = IBR not trusted -> refiner zone (Phase 2)
    # ---- Option-D baseline + HONEST tear attribution (A: hold-out LiDAR densification residual) ----
    Xh = (Zd * dirs[..., 2]).astype(np.float32)
    road_mask = valid_any & has & (conf >= GEN_THR) & (Xh < 0.5)     # near-ground planar surface
    ibr_D = base.copy(); ibr_D[road_mask] = ibr_raw[road_mask]       # IBR only on road; facade/others = hard_select
    gb = 0.299 * base[..., 0] + 0.587 * base[..., 1] + 0.114 * base[..., 2]
    gi = 0.299 * ibr_raw[..., 0] + 0.587 * ibr_raw[..., 1] + 0.114 * ibr_raw[..., 2]
    tear = (np.abs(gi - gb) > 18) & valid_any & (~road_mask)         # IBR changed a non-road region = candidate tear
    # A (fix the invalid gate metric): densify from HALF the LiDAR, measure |densified - true depth| on the OTHER half.
    # This measures whether nearest-densification actually recovers the true surface depth — NOT 4px ERP proximity.
    rfield = np.zeros((H, W), np.float32); densify_resid = {}
    if len(lidar) > 2000:
        ev = (np.arange(len(lidar)) % 2 == 0)
        Zd_tr, _c2, _v2 = fuse_and_densify(scatter_depth(lidar[ev]), sd)
        tu, tv, td = ego_to_uv(lidar[~ev]); mm = (td > 1.5) & (td < 80)
        tui = np.clip(np.round(tu[mm]).astype(np.int64), 0, W - 1); tvi = np.clip(np.round(tv[mm]).astype(np.int64), 0, H - 1)
        resid = np.abs(Zd_tr[tvi, tui] - td[mm].astype(np.float32))
        densify_resid = {"p50_m": float(np.percentile(resid, 50)), "p90_m": float(np.percentile(resid, 90)),
                         "fixable_share_lt0p5m": float((resid < 0.5).mean()), "n_test": int(resid.size)}
        fl = tvi * W + tui; acc_r = np.zeros(H * W, np.float32); cnt_r = np.zeros(H * W, np.float32)
        np.add.at(acc_r, fl, resid); np.add.at(cnt_r, fl, 1.0); rfield = (acc_r / np.maximum(cnt_r, 1)).reshape(H, W)
    has_resid = rfield > 0
    tear_unfixable = tear & (rfield > 0.5)                          # densify residual LARGE -> nearest-densify can't hold the surface (denser geom / abstain)
    tear_fixable = tear & has_resid & (rfield <= 0.5)              # densify residual SMALL -> a better densifier/renderer can fix it
    attr = overlay(overlay(base, tear_fixable, (40, 220, 90), 0.7), tear_unfixable, (255, 40, 40), 0.7)
    geomcov = overlay(base, cv2.dilate(geomvalid.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0, (40, 255, 120), 0.6)
    save_rgb(REMOTE_OUT / f"{run_name}_pD_road_only_ibr.png", ibr_D)
    save_rgb(REMOTE_OUT / f"{run_name}_pD_tear_attribution.png", attr)
    save_rgb(REMOTE_OUT / f"{run_name}_pD_geom_coverage.png", geomcov)
    save_rgb(REMOTE_OUT / f"{run_name}_pD_densify_residual_heat.png", heat(rfield, has_resid))
    # metrics
    co = (viscount >= 2) & valid_any
    metrics = {
        "n_lidar_pts": int(len(lidar)), "n_stereo_pts": int(len(ster)),
        "geom_valid_sparse_frac": float(geomvalid.mean()),
        "ibr_covered_frac_of_band": float((has & valid_any).sum() / max(valid_any.sum(), 1)),
        "generated_band_frac_of_band": float(gen.sum() / max(valid_any.sum(), 1)),
        "multi_source_blend_frac_of_band": float((co).sum() / max(valid_any.sum(), 1)),
        "mean_confidence_in_band": float(conf[valid_any].mean()),
        "road_only_ibr_frac_of_band": float(road_mask.sum() / max(valid_any.sum(), 1)),
        "tear_frac_of_band": float(tear.sum() / max(valid_any.sum(), 1)),
        "densify_residual_m": densify_resid,
        "tear_fixable_share": float(tear_fixable.sum() / max(tear.sum(), 1)),
        "tear_unfixable_share": float(tear_unfixable.sum() / max(tear.sum(), 1)),
    }
    save_rgb(REMOTE_OUT / f"{run_name}_p01_ibr_rgb.png", ibr)
    save_rgb(REMOTE_OUT / f"{run_name}_p01_depth_heat.png", heat(Zd, valid_any))
    cv2.imwrite(str(REMOTE_OUT / f"{run_name}_p01_generated_mask.png"), (gen.astype(np.uint8) * 255))
    save_rgb(REMOTE_OUT / f"{run_name}_p01_hard_select_base.png", base)
    # roi sheet
    rows = roi_sheet(run_name, base, ibr, gen)
    try: f = ImageFont.truetype("DejaVuSans.ttf", 15)
    except Exception: f = ImageFont.load_default()
    wsh = max(r[1].shape[1] for r in rows); sheet = Image.new("RGB", (wsh, sum(r[1].shape[0] + 22 for r in rows) + 26), (12, 12, 16)); d = ImageDraw.Draw(sheet)
    d.text((6, 4), f"{run_name} ROI: hard_select | IBR single-centre | IBR+generated_mask(magenta)", (235, 235, 245), font=f)
    yo = 26
    for name, strip in rows:
        bar = Image.new("RGB", (wsh, 22), (22, 22, 30)); ImageDraw.Draw(bar).text((6, 3), name, (220, 220, 235), font=f); sheet.paste(bar, (0, yo)); yo += 22
        sheet.paste(Image.fromarray(strip), (0, yo)); yo += strip.shape[0]
    sheet.save(REMOTE_OUT / f"{run_name}_p01_roi_sheet.jpg", quality=92)
    # full board
    tiles = [("hard_select base", base), ("IBR single-centre render", ibr), ("fused depth (m)", heat(Zd, valid_any)),
             ("generated_mask band (magenta) = refiner zone", overlay(ibr, gen, (255, 40, 220), 0.55))]
    ims = []
    for t, a in tiles:
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).resize((900, 450))
        bar = Image.new("RGB", (900, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), t, (235, 235, 245), font=f)
        o = Image.new("RGB", (900, 474)); o.paste(bar, (0, 0)); o.paste(im, (0, 24)); ims.append(o)
    board = Image.new("RGB", (900, 474 * 4 + 72), (10, 10, 14)); d = ImageDraw.Draw(board)
    d.text((8, 6), f"{run_name} DB-77B Phase 0+1 leashed IBR (presentation render, NOT source-faithful)", (240, 240, 250), font=f)
    d.text((8, 28), f"IBR covered={metrics['ibr_covered_frac_of_band']:.3f} multi-blend={metrics['multi_source_blend_frac_of_band']:.3f} generated band={metrics['generated_band_frac_of_band']:.3f}", (210, 230, 210), font=f)
    d.text((8, 50), f"geom_valid_sparse={metrics['geom_valid_sparse_frac']:.3f} mean_conf={metrics['mean_confidence_in_band']:.3f} | leash=LiDAR+stereo+ULR real-pixel blend", (210, 230, 210), font=f)
    yo = 72
    for o in ims: board.paste(o, (0, yo)); yo += o.height
    board.save(REMOTE_OUT / f"{run_name}_p01_board.jpg", quality=90)
    tilesD = [("hard_select base", base), ("OPTION-D: road-only IBR + facade hard_select", ibr_D),
              ("tear: GREEN=densify-fixable(resid<0.5m) RED=unfixable(resid>0.5m)", attr),
              ("real LiDAR/stereo geometry coverage (green)", geomcov)]
    imsD = []
    for t, a in tilesD:
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).resize((900, 450))
        bar = Image.new("RGB", (900, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), t, (235, 235, 245), font=f)
        o = Image.new("RGB", (900, 474)); o.paste(bar, (0, 0)); o.paste(im, (0, 24)); imsD.append(o)
    boardD = Image.new("RGB", (900, 474 * 4 + 50), (10, 10, 14)); dd = ImageDraw.Draw(boardD)
    dd.text((8, 6), f"{run_name} DB-77B bug-fixed IBR + honest tear  tear={metrics['tear_frac_of_band']:.3f} densify_resid_p50={(metrics.get('densify_residual_m') or {}).get('p50_m')} fixable_share={metrics['tear_fixable_share']:.2f}", (240, 240, 250), font=f)
    yo = 50
    for o in imsD: boardD.paste(o, (0, yo)); yo += o.height
    boardD.save(REMOTE_OUT / f"{run_name}_pD_board.jpg", quality=90)
    return {"case": run_name, "metrics": metrics}


def build_batch_board(summary):
    from PIL import Image, ImageDraw, ImageFont
    ps = [REMOTE_OUT / "02a00399_a000_bmw_p01_board.jpg", REMOTE_OUT / "0bae3b5e_a030_clean_far_p01_board.jpg"]
    ims = [Image.open(p) for p in ps if p.exists()]
    if not ims: return
    w = max(i.width for i in ims); board = Image.new("RGB", (w, sum(i.height for i in ims) + 30), (8, 8, 12))
    yo = 30
    for im in ims: board.paste(im, (0, yo)); yo += im.height
    board.save(REMOTE_OUT / "DB77b_p01_review_board.jpg", quality=90)


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    if not import_ok("av2"): subprocess.run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=600, check=False)
    workdir = find_workdir(); OUT["workdir_found"] = bool(workdir)
    if workdir is None: raise RuntimeError("workdir not found")
    sys.path.insert(0, str(workdir / "scripts" / "phase3")); sys.path.insert(0, str(workdir / "code"))
    reports = [run_case(cs, rn, workdir) for cs, rn in CASES]
    summary = {"status": "db77b_p01_complete", "presentation_render": True, "source_faithful": False, "pre_registered": TH,
               "by_case": {r["case"]: r for r in reports}, "runtime_s": round(time.time() - t0, 2)}
    build_batch_board(summary)
    (REMOTE_OUT / "DB77b_p01_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    (REMOTE_OUT / "DB77b_p01_claim.json").write_text(json.dumps({"brief": "DB-77B Phase 0+1", "classification": "presentation_render_leashed_IBR_no_refiner", "source_faithful_repair": False, "generated_mask_present": True, "red_promotion": False, "note": "IBR blend RGB is a presentation render; generated_mask band reserved for Phase-2 refiner (A100); NOT sensor truth"}, indent=2), encoding="utf-8")
    OUT["status"] = "db77b_p01_completed"; OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "db77b_p01_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB77BP01_JSON_BEGIN"); print(json.dumps(json_safe(OUT), separators=(",", ":"))); print("DB77BP01_JSON_END")
'''
    code = code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__P01_RESULT__", P01_RESULT).replace("__TH__", json.dumps(TH))
    return code


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db77b_remote>', 'exec'))\nPY"


def poll_job(client, job_id, timeout_s):
    t0 = time.time(); last = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(7); last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running": return sanitize(last)
    return sanitize(last or {"state": "poll_timeout"})


def run_phase01(timeout_s: int) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient(); status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_p01_python())], "cwd": "/content", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s)
    remote_result = None
    raw = client.read_file(P01_RESULT, max_size_mb=12)
    if raw is not None:
        try: remote_result = json.loads(raw.decode("utf-8"))
        except Exception: remote_result = None
    if remote_result is None:
        rr = job.get("log_tail", "")
        if "DB77BP01_JSON_BEGIN" in rr:
            try: remote_result = json.loads(rr.split("DB77BP01_JSON_BEGIN", 1)[1].split("DB77BP01_JSON_END", 1)[0].strip())
            except Exception: remote_result = None
    if remote_result is None: remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    (OUT_DIR / "DB77b_p01_remote_result.json").write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")
    fetched = {}
    for key, (rn, mb) in FETCH.items():
        local = (OUT_DIR if key in {"summary", "remote_result", "board"} else (OUT_DIR / "fetch")) / rn
        local.parent.mkdir(parents=True, exist_ok=True)
        rb = client.read_file(REMOTE_OUT + "/" + rn, max_size_mb=mb)
        if rb is None: fetched[key] = {"path": rel(local), "exists": False}
        else: local.write_bytes(rb); fetched[key] = {"path": rel(local), "exists": True, "bytes": len(rb)}
    summary = {}
    sp = OUT_DIR / "DB77b_p01_summary.json"
    if sp.exists():
        try: summary = json.loads(sp.read_text(encoding="utf-8"))
        except Exception: summary = {}
    if not summary: summary = remote_result.get("summary") or {}
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "status": "db77b_phase0_phase1",
        "brief": "DB-77B Branch B leashed renderer Phase 0+1 (geometry skeleton + IBR single-centre render)",
        "scope": {"presentation_render": True, "source_faithful": False, "exec_count": 1, "runtime_type": status.get("runtime_type"),
                   "iterative_diffusion": False, "model_inference": False, "refiner": False, "red_promotion": False, "gpu_bound": False},
        "runtime": {"secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file", "status": safe_status(status)},
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail", "cmd"}}),
        "remote_status": remote_result.get("status"), "summary": summary, "fetched_outputs": fetched,
        "pre_registered": TH, "output_location": rel(OUT_DIR), "drive_output_location": "results/db77b_leashed_renderer/",
        "decision": {"vision_check_required": True, "accepted_as_source_faithful_repair": False,
                      "phase2_refiner_needs_a100": True},
        "claim_boundary": ["DB-77B Phase 0+1 = geometry-skeleton fusion + IBR single-centre render (presentation, NOT source-faithful).",
                            "IBR-blended RGB is a presentation render; generated_mask band reserved for the Phase-2 refiner (A100).",
                            "No iterative diffusion, no model inference, no RED. Vision review decides if the seam improves / any smear."],
    }
    scan = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan); manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    (OUT_DIR / "DB77b_p01_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "db77b_phase01", "remote_status": remote_result.get("status"), "secret_hits": manifest["strict_secret_scan"]["hit_count"], "manifest": rel(OUT_DIR / "DB77b_p01_manifest.json")}


def check_compile() -> None:
    py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    compile(remote_p01_python(), "<db77b_p01_remote>", "exec")
    print(json.dumps({"status": "compile_ok", "wrapper": True, "p01_remote": True}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--phase01", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=1500)
    args = parser.parse_args()
    if args.check: check_compile(); return
    if args.phase01: print(json.dumps(run_phase01(args.timeout_s), indent=2)); return
    print(json.dumps({"status": "ready", "message": "Use --check or --phase01 (DB-77B; Phase 2 refiner needs A100)."}, indent=2))


if __name__ == "__main__":
    main()
