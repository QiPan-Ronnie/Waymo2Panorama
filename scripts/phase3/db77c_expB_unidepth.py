"""DB-77C EXP-B — UniDepthV2 dense metric depth replaces nearest-neighbour LiDAR densification, re-fed to IBR.

WHY (background): DB-77B hand-rolled IBR (multi-frame LiDAR -> ERP sparse depth -> nearest-neighbour
EDT densification -> ULR depth-correct blend re-projected to the virtual centre) tears at structural
edges. Root cause = nearest-neighbour densification hold-out residual p90 = 12-15 m (a near-edge surface
gets the depth of a far point, so the re-projection lands the wrong texture -> tear). EXP-B hypothesis:
UniDepthV2 dense metric depth (sharp edges, zero-shot on driving, accepts known intrinsics) replaces
the densifier and collapses the edge residual.

EXP-B1 (CORE, cheap, the main value) — hold-out residual:
  Per ring camera, run UniDepth dense depth; at the pixels where LiDAR projects into that camera,
  compare |UniDepth_depth - LiDAR_depth|, report residual p50/p90.
  HARD CRITERION: can p90 drop from 12-15 m to < 2 m?  < 2 m = the geometry wall loosens;
  > 5 m = the wall is confirmed (UniDepth does not fix it either).
  LiDAR is median-ratio scaled onto UniDepth before comparison (UniDepth metric scale can drift).
  Residual is bucketed by structure (image-gradient proxy: edge/curb/wall vs flat road).

EXP-B2 (runs only if B1 passes, flag-gated --exp-b2):
  UniDepth dense depth -> ERP (per-cam depth -> 3D cam -> ego -> ERP, z-buffer) replaces densification
  -> re-fed to IBR (per-cam z-buffer occlusion + depth-correct ULR weights, cf. DB-77B ibr_render)
  -> compare against the hard_select base on 4 ROIs.

=====================================================================================================
UniDepthV2 VERIFICATION (web-checked 2026-06-06; multi-source: GitHub README, scripts/demo.py, HF, arXiv 2502.20110)
=====================================================================================================
INSTALL (no pip-only wheel; editable install of the cloned repo):
    git clone https://github.com/lpiccinelli-eth/UniDepth
    pip install -e ./UniDepth          # repo provides setup; --extra-index-url cu118 only needed if you
                                       # let it pull torch. On Colab torch>=2.4 is preinstalled, so we
                                       # install WITHOUT touching torch (no --extra-index-url) to avoid
                                       # clobbering the Colab CUDA build. xformers is optional (a warning,
                                       # not a hard requirement for .infer()).
    Deps (requirements.txt): einops>=0.7, huggingface-hub>=0.22, timm, triton>=2.4, torch>=2.4,
    torchvision>=0.19, xformers>=0.0.26 (optional). numpy>=2.0 is requested but UniDepth runs fine
    under numpy<2 too; we DO NOT force a numpy bump on Colab (would break av2/pandas).

LOAD API:
    from unidepth.models import UniDepthV2
    model = UniDepthV2.from_pretrained("lpiccinelli/unidepth-v2-vitl14")   # HF namespace is "lpiccinelli/" (NO -eth)
    model = model.to("cuda").eval()
    Weight ids: unidepth-v2-vits14 / -vitb14 / -vitl14 (we use vitl14, the largest/most accurate).

INFER API (V2 recommended path = pass a Camera object so our KNOWN intrinsics are used, not self-estimated):
    from unidepth.utils.camera import Pinhole
    rgb_t = torch.from_numpy(rgb_uint8).permute(2,0,1)        # (3,H,W) uint8; model normalises internally
    cam   = Pinhole(K=torch.from_numpy(K).float().unsqueeze(0))   # (1,3,3)
    pred  = model.infer(rgb_t, cam)                          # also pred=model.infer(rgb_t) self-estimates K
    depth = pred["depth"].squeeze().cpu().numpy()            # (H,W) metric depth; pred also has "points","intrinsics"
  UNCERTAINTY we hedge in code (multi-fallback, never crash): exact Pinhole signature / batch-dim
  expectation has varied across releases. We try, in order:
    (a) Pinhole(K=K_torch.unsqueeze(0)) then model.infer(rgb_t, cam)
    (b) model.infer(rgb_t, intrinsics_torch)   # raw 3x3 tensor (older/simple path)
    (c) model.infer(rgb_t)                      # no intrinsics, self-estimated (always works)
  and record which path succeeded in the JSON markers.

40GB FEASIBILITY: YES, huge headroom. UniDepthV2 vitl14 inference uses ~1.8-2.5 GB (FP32) / ~1.1-1.8 GB
  (FP16) per the paper (measured on A100). 7 ring cams run sequentially -> peak a few GB. 40GB is overkill.

LICENSE: Creative Commons BY-NC 4.0 (non-commercial). Fine for research / this validation; NOT for a
  commercial product without separate licensing. Recorded in manifest.

This file = local driver only. It writes + py_compiles the remote string. It NEVER calls --run-remote
(the main agent runs it). Runtime secret is read only via ColabClient from a non-repo file / process env;
the remote gets no token; strict secret-scan must report 0.
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
OUT_DIR = ROOT / "deliverables" / "db77c_expB_unidepth"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db77c_expB_unidepth"
RESULT = REMOTE_OUT + "/DB77c_expB_remote_result.json"

TH = {
    "depth_min_m": 1.5,
    "depth_max_m": 80.0,
    # EXP-B1 hold-out criterion thresholds (pre-registered):
    "wall_loosens_p90_m": 2.0,    # UniDepth residual p90 < this => geometry wall loosens
    "wall_confirmed_p90_m": 5.0,  # residual p90 > this => wall confirmed (UniDepth does not fix edges)
    # structure bucketing via image gradient (Sobel magnitude on the per-cam image, sampled at LiDAR px):
    "grad_edge_thr": 0.18,        # normalised gradient above this => edge/curb/wall bucket
    "scale_min_ratio": 0.3,       # sane median-ratio scale guard
    "scale_max_ratio": 3.0,
    # EXP-B2 IBR re-feed:
    "ibr_min_weight": 1e-3,
    "occ_tol_m": 0.5,
    "model_id": "lpiccinelli/unidepth-v2-vitl14",
}

FETCH = {
    "summary": ("DB77c_expB_summary.json", 16),
    "remote_result": ("DB77c_expB_remote_result.json", 16),
    "board": ("DB77c_expB_review_board.jpg", 80),
    "claim": ("DB77c_expB_claim.json", 16),
    # per-case B1 boards (depth viz + residual buckets)
    "bmw_depth_board": ("02a00399_a000_bmw_expB1_depth_board.jpg", 80),
    "clean_depth_board": ("0bae3b5e_a030_clean_far_expB1_depth_board.jpg", 80),
    "bmw_resid_json": ("02a00399_a000_bmw_expB1_residual.json", 16),
    "clean_resid_json": ("0bae3b5e_a030_clean_far_expB1_residual.json", 16),
    # per-case B2 ROI boards (only present if --exp-b2 ran)
    "bmw_b2_board": ("02a00399_a000_bmw_expB2_roi_board.jpg", 70),
    "clean_b2_board": ("0bae3b5e_a030_clean_far_expB2_roi_board.jpg", 70),
}


def remote_python(run_b2: bool) -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [pathlib.Path("/content/waymo2panorama"),
                      pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama")]
H, W = 1024, 2048; EPS = 1e-6
RUN_B2 = __RUN_B2__
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"), ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far")]
MARKED_ROIS = {"left_road_patch": (250, 515, 460, 715), "lower_center_road_patch": (740, 595, 1035, 745),
               "center_lane_marking": (1030, 515, 1325, 735), "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760)}
TH = json.loads(r"""__TH__""")
DMIN, DMAX = float(TH["depth_min_m"]), float(TH["depth_max_m"])
P90_LOOSE = float(TH["wall_loosens_p90_m"]); P90_WALL = float(TH["wall_confirmed_p90_m"])
GRAD_EDGE = float(TH["grad_edge_thr"]); SMIN, SMAX = float(TH["scale_min_ratio"]), float(TH["scale_max_ratio"])
WMIN = float(TH["ibr_min_weight"]); OCC_TOL = float(TH["occ_tol_m"]); MODEL_ID = str(TH["model_id"])
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS",
            "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER",
            "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}

OUT = {"phase": "db77c_expB_unidepth_dense_depth", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"hold_out_residual_metric": True, "dense_metric_depth": True, "iterative_diffusion": False,
                 "source_faithful": False, "red_promotion": False, "exp_b2_ibr_refeed": bool(RUN_B2)},
       "unidepth": {"model_id": MODEL_ID, "infer_path": None, "loaded": False}}


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


# ---------------------------------------------------------------------------
# UniDepthV2: load once, infer per camera with KNOWN intrinsics (multi-fallback)
# ---------------------------------------------------------------------------
def install_unidepth():
    """Clone + editable-install UniDepth WITHOUT touching the Colab torch build."""
    info = {"already": import_ok("unidepth"), "steps": []}
    if info["already"]:
        return info
    repo = pathlib.Path("/content/UniDepth")
    if not repo.exists():
        r = subprocess.run(["git", "clone", "--depth", "1", "https://github.com/lpiccinelli-eth/UniDepth", str(repo)],
                           capture_output=True, text=True, timeout=600)
        info["steps"].append({"git_clone_rc": r.returncode, "tail": (r.stderr or r.stdout)[-400:]})
    # editable install with --no-deps first (UniDepth pins torch>=2.4/numpy>=2 which would clobber Colab);
    # then install the light pure-python deps we actually need, leaving torch/torchvision/numpy untouched.
    r1 = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "-e", str(repo)],
                        capture_output=True, text=True, timeout=900)
    info["steps"].append({"pip_e_nodeps_rc": r1.returncode, "tail": (r1.stderr or r1.stdout)[-400:]})
    r2 = subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                         "einops>=0.7.0", "timm", "huggingface-hub>=0.22.0"],
                        capture_output=True, text=True, timeout=600)
    info["steps"].append({"pip_lightdeps_rc": r2.returncode, "tail": (r2.stderr or r2.stdout)[-400:]})
    info["installed_now"] = import_ok("unidepth")
    return info


def load_unidepth():
    import torch
    from unidepth.models import UniDepthV2
    model = UniDepthV2.from_pretrained(MODEL_ID)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(dev).eval()
    return model, dev


def unidepth_infer(model, dev, rgb_uint8, K):
    """Return (depth HxW float32, infer_path str). Multi-fallback over the V2 infer signature."""
    import torch
    rgb_t = torch.from_numpy(np.ascontiguousarray(rgb_uint8)).permute(2, 0, 1).contiguous()
    K_t = torch.from_numpy(np.ascontiguousarray(K)).float()
    last_err = None
    # (a) Pinhole camera object with our known intrinsics (V2 recommended)
    try:
        from unidepth.utils.camera import Pinhole
        cam = Pinhole(K=K_t.unsqueeze(0).to(dev))
        with torch.no_grad():
            pred = model.infer(rgb_t.to(dev), cam)
        return pred["depth"].squeeze().detach().cpu().numpy().astype(np.float32), "pinhole_camera"
    except Exception as e:
        last_err = "pinhole: " + repr(e)[:160]
    # (b) raw 3x3 intrinsics tensor
    try:
        with torch.no_grad():
            pred = model.infer(rgb_t.to(dev), K_t.to(dev))
        return pred["depth"].squeeze().detach().cpu().numpy().astype(np.float32), "raw_intrinsics_tensor"
    except Exception as e:
        last_err = (last_err or "") + " | raw3x3: " + repr(e)[:160]
    # (c) no intrinsics, self-estimated (always works) -- residual still meaningful after median-ratio scale
    with torch.no_grad():
        pred = model.infer(rgb_t.to(dev))
    return pred["depth"].squeeze().detach().cpu().numpy().astype(np.float32), "self_estimated_K|" + str(last_err)


# ---------------------------------------------------------------------------
# data loading (LiDAR in EGO frame; per-cam projection of LiDAR)
# ---------------------------------------------------------------------------
def nearest_lidar_ego(log_dir, anchor_ts):
    import pandas as pd
    sweeps = sorted((log_dir / "sensors" / "lidar").glob("*.feather"))
    if not sweeps:
        return np.zeros((0, 3), np.float64)
    stss = np.array([int(p.stem) for p in sweeps], np.int64)
    ai = int(np.argmin(np.abs(stss - anchor_ts)))
    df = pd.read_feather(sweeps[ai])
    return df[["x", "y", "z"]].to_numpy(np.float64)


def project_lidar_to_cam(xyz_ego, K, T_ego_cam, ww, hh):
    """Return (px, py, z_cam, in_bounds_mask) for LiDAR points in this camera's image."""
    Tci = np.linalg.inv(T_ego_cam)
    Xc = (Tci[:3, :3] @ xyz_ego.T).T + Tci[:3, 3]
    z = Xc[:, 2]
    front = z > EPS
    px = K[0, 0] * Xc[:, 0] / np.maximum(z, EPS) + K[0, 2]
    py = K[1, 1] * Xc[:, 1] / np.maximum(z, EPS) + K[1, 2]
    inb = front & (px >= 0) & (px < ww - 1) & (py >= 0) & (py < hh - 1) & (z > DMIN) & (z < DMAX)
    return px, py, z, inb


def sobel_grad_norm(rgb_uint8):
    import cv2
    g = cv2.cvtColor(rgb_uint8, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return np.hypot(gx, gy)  # ~[0, ~3]; normalised image so a strong edge ~ 0.2-0.6


# ---------------------------------------------------------------------------
# EXP-B1: per-camera hold-out residual (the core, cheap criterion)
# ---------------------------------------------------------------------------
def expB1_residual(model, dev, frame, ring_cams, run_name):
    import cv2
    per_cam = {}
    all_resid = []; all_bucket = []  # bucket: 1=edge/curb/wall, 0=flat road
    depth_tiles = []
    for cam in ring_cams:
        cal = frame.calibrations[cam]; rgb = frame.images[cam]; K = cal.K.astype(np.float64)
        hh, ww = rgb.shape[:2]
        try:
            depth, ipath = unidepth_infer(model, dev, rgb, K)
            OUT["unidepth"]["infer_path"] = ipath
        except Exception as e:
            per_cam[cam] = {"error": repr(e)[:200]}; continue
        # resize UniDepth depth to image size if the model returned a different HxW
        if depth.shape[:2] != (hh, ww):
            depth = cv2.resize(depth, (ww, hh), interpolation=cv2.INTER_NEAREST)
        # project LiDAR into this cam
        xyz = OUT.get("_xyz_ego_cache")
        px, py, z, inb = project_lidar_to_cam(xyz, K, cal.T_ego_cam, ww, hh)
        if int(inb.sum()) < 64:
            per_cam[cam] = {"n_lidar_in_cam": int(inb.sum()), "note": "too few LiDAR points"}
        else:
            ui = np.clip(np.round(px[inb]).astype(np.int64), 0, ww - 1)
            vi = np.clip(np.round(py[inb]).astype(np.int64), 0, hh - 1)
            d_uni = depth[vi, ui].astype(np.float64); d_lid = z[inb].astype(np.float64)
            ok = np.isfinite(d_uni) & (d_uni > 1e-3) & np.isfinite(d_lid) & (d_lid > DMIN)
            d_uni, d_lid, ui2, vi2 = d_uni[ok], d_lid[ok], ui[ok], vi[ok]
            # median-ratio scale: bring LiDAR onto UniDepth's metric scale (UniDepth metric, but guard drift)
            ratio = float(np.median(d_uni / np.maximum(d_lid, EPS))) if d_uni.size else 1.0
            ratio = float(np.clip(ratio, SMIN, SMAX))
            d_lid_s = d_lid * ratio
            resid = np.abs(d_uni - d_lid_s)
            # structure bucket from image gradient at the LiDAR pixel
            gradmap = sobel_grad_norm(rgb)
            gv = gradmap[vi2, ui2]
            edge = gv >= GRAD_EDGE
            per_cam[cam] = {
                "n_lidar_in_cam": int(inb.sum()), "n_compared": int(resid.size),
                "median_ratio_scale": ratio,
                "p50_m": float(np.percentile(resid, 50)), "p90_m": float(np.percentile(resid, 90)),
                "edge": {"n": int(edge.sum()),
                          "p50_m": (float(np.percentile(resid[edge], 50)) if int(edge.sum()) else None),
                          "p90_m": (float(np.percentile(resid[edge], 90)) if int(edge.sum()) else None)},
                "flat": {"n": int((~edge).sum()),
                          "p50_m": (float(np.percentile(resid[~edge], 50)) if int((~edge).sum()) else None),
                          "p90_m": (float(np.percentile(resid[~edge], 90)) if int((~edge).sum()) else None)},
            }
            all_resid.append(resid); all_bucket.append(edge.astype(np.int32))
        # depth viz tile: raw | UniDepth depth heat | LiDAR-sparse-on-raw
        try:
            dv = np.clip(depth, DMIN, DMAX)
            heatd = heat(dv, np.isfinite(dv) & (dv > 0))
            spr = rgb.copy()
            pxs = np.round(px[inb]).astype(np.int64); pys = np.round(py[inb]).astype(np.int64)
            zb = z[inb]
            col = (np.clip((zb - DMIN) / max(DMAX - DMIN, 1e-6), 0, 1) * 255).astype(np.uint8)
            cmap = cv2.applyColorMap(col.reshape(-1, 1), cv2.COLORMAP_JET)[:, 0, ::-1]  # to RGB
            for (xx, yy, cc) in zip(pxs, pys, cmap):
                cv2.circle(spr, (int(xx), int(yy)), 2, (int(cc[0]), int(cc[1]), int(cc[2])), -1)
            tile = np.concatenate([cv2.resize(rgb, (480, 360)), cv2.resize(heatd, (480, 360)),
                                   cv2.resize(spr, (480, 360))], 1)
            depth_tiles.append((cam, tile))
        except Exception as e:
            depth_tiles.append((cam, np.zeros((360, 1440, 3), np.uint8)))
    # overall + per-bucket aggregate
    overall = {}
    if all_resid:
        R = np.concatenate(all_resid); B = np.concatenate(all_bucket).astype(bool)
        overall = {"n_compared": int(R.size),
                   "p50_m": float(np.percentile(R, 50)), "p90_m": float(np.percentile(R, 90)),
                   "edge_p50_m": (float(np.percentile(R[B], 50)) if int(B.sum()) else None),
                   "edge_p90_m": (float(np.percentile(R[B], 90)) if int(B.sum()) else None),
                   "flat_p50_m": (float(np.percentile(R[~B], 50)) if int((~B).sum()) else None),
                   "flat_p90_m": (float(np.percentile(R[~B], 90)) if int((~B).sum()) else None)}
        p90 = overall["p90_m"]
        overall["verdict"] = ("wall_loosens_lt2m" if p90 < P90_LOOSE
                              else ("wall_confirmed_gt5m" if p90 > P90_WALL else "intermediate_2to5m"))
        overall["edge_verdict"] = None
        ep90 = overall.get("edge_p90_m")
        if ep90 is not None:
            overall["edge_verdict"] = ("wall_loosens_lt2m" if ep90 < P90_LOOSE
                                       else ("wall_confirmed_gt5m" if ep90 > P90_WALL else "intermediate_2to5m"))
    # depth board
    try:
        from PIL import Image, ImageDraw, ImageFont
        try: f = ImageFont.truetype("DejaVuSans.ttf", 15)
        except Exception: f = ImageFont.load_default()
        if depth_tiles:
            wsh = max(t[1].shape[1] for t in depth_tiles)
            board = Image.new("RGB", (wsh, sum(t[1].shape[0] + 22 for t in depth_tiles) + 30), (10, 10, 14))
            d = ImageDraw.Draw(board)
            ovr = overall.get("p90_m"); evd = overall.get("verdict")
            d.text((6, 6), f"{run_name} EXP-B1 depth: raw | UniDepth depth | LiDAR-sparse  overall p90={ovr} verdict={evd}",
                   (235, 235, 245), font=f)
            yo = 30
            for cam, tile in depth_tiles:
                bar = Image.new("RGB", (wsh, 22), (22, 22, 30)); ImageDraw.Draw(bar).text((6, 3), cam, (220, 220, 235), font=f)
                board.paste(bar, (0, yo)); yo += 22
                board.paste(Image.fromarray(tile), (0, yo)); yo += tile.shape[0]
            board.save(REMOTE_OUT / f"{run_name}_expB1_depth_board.jpg", quality=88)
    except Exception:
        pass
    resid_doc = {"case": run_name, "per_cam": per_cam, "overall": overall}
    (REMOTE_OUT / f"{run_name}_expB1_residual.json").write_text(json.dumps(json_safe(resid_doc), indent=2), encoding="utf-8")
    return resid_doc


# ---------------------------------------------------------------------------
# EXP-B2: UniDepth dense depth -> ERP -> IBR re-feed (flag-gated; only if B1 passes)
# ---------------------------------------------------------------------------
def unidepth_depth_to_erp(model, dev, frame, ring_cams):
    """Per-cam UniDepth depth -> 3D cam -> ego -> ERP, z-buffered into a dense ERP depth Z(d)."""
    import cv2
    erp_depth = np.full((H, W), np.inf, np.float32)
    for cam in ring_cams:
        cal = frame.calibrations[cam]; rgb = frame.images[cam]; K = cal.K.astype(np.float64)
        hh, ww = rgb.shape[:2]
        depth, _ip = unidepth_infer(model, dev, rgb, K)
        if depth.shape[:2] != (hh, ww):
            depth = cv2.resize(depth, (ww, hh), interpolation=cv2.INTER_NEAREST)
        yy, xx = np.mgrid[0:hh, 0:ww]
        z = depth.astype(np.float64); good = np.isfinite(z) & (z > DMIN) & (z < DMAX)
        Xc = np.stack([(xx - K[0, 2]) / K[0, 0] * z, (yy - K[1, 2]) / K[1, 1] * z, z], -1)
        Xc = Xc[good].reshape(-1, 3)
        Xe = (cal.T_ego_cam[:3, :3] @ Xc.T).T + cal.T_ego_cam[:3, 3]
        u, v, n = ego_to_uv(Xe)
        ui = np.round(u).astype(np.int64); vi = np.round(v).astype(np.int64)
        ib = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
        ui, vi, nn = ui[ib], vi[ib], n[ib].astype(np.float32)
        flat = vi * W + ui; order = np.argsort(-nn)  # far first -> nearest overwrites (z-buffer)
        sd = erp_depth.reshape(-1); sd[flat[order]] = nn[order]
    erp_depth[~np.isfinite(erp_depth)] = 0.0
    return erp_depth


def ibr_render(Zd, dirs, frame, ring_cams, base):
    import cv2
    valid_z = Zd > DMIN
    Zfill = np.where(valid_z, Zd, 8.0).astype(np.float64)
    X = (Zfill[..., None] * dirs).astype(np.float64)
    acc = np.zeros((H, W, 3), np.float32); wsum = np.zeros((H, W), np.float32)
    for cam in ring_cams:
        cal = frame.calibrations[cam]; img = frame.images[cam].astype(np.float32); K = cal.K
        Tci = np.linalg.inv(cal.T_ego_cam)
        Xc = np.einsum('ij,hwj->hwi', Tci[:3, :3], X) + Tci[:3, 3]; z = Xc[..., 2]
        px = K[0, 0] * (Xc[..., 0] / np.maximum(z, EPS)) + K[0, 2]
        py = K[1, 1] * (Xc[..., 1] / np.maximum(z, EPS)) + K[1, 2]
        hh, ww = img.shape[:2]
        inb = (z > 0.1) & (px >= 0) & (px < ww - 1) & (py >= 0) & (py < hh - 1)
        yi = np.clip(py.astype(np.int64), 0, hh - 1); xi = np.clip(px.astype(np.int64), 0, ww - 1)
        zbuf = np.full(hh * ww, np.inf, np.float32)
        flat = (yi * ww + xi).reshape(-1); zf = z.reshape(-1).astype(np.float32); inf_ = inb.reshape(-1)
        order = np.argsort(-zf); zbuf[flat[order][inf_[order]]] = zf[order][inf_[order]]
        nearest = zbuf[(yi * ww + xi)]
        vis = inb & valid_z & (z <= nearest + OCC_TOL)
        rad = np.hypot((px - K[0, 2]) / (0.5 * ww), (py - K[1, 2]) / (0.5 * hh))
        wv = (np.clip(1.0 - rad, 0.0, 1.0) ** 2 * vis).astype(np.float32)
        col = cv2.remap(img, px.astype(np.float32), py.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        acc += wv[..., None] * col; wsum += wv
    has = wsum > WMIN; ibr = base.astype(np.float32).copy()
    ibr[has] = (acc[has] / wsum[has][..., None])
    return np.clip(ibr, 0, 255).astype(np.uint8), has


def hard_select_base(frame, ring_cams):
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    weights, labs = [], []
    for cam in ring_cams:
        cal = frame.calibrations[cam]
        rgb, _a, w = render_camera_to_erp(image=frame.images[cam], K=cal.K, T_ego_cam=cal.T_ego_cam,
                                          erp_hw=(H, W), convergence_distance_m=None)
        weights.append(w.astype(np.float32)); labs.append(rgb.astype(np.uint8))
    weights = np.stack(weights, 0); valid_any = weights.max(0) > EPS
    owner = np.argsort(-weights, axis=0)[0].astype(np.int32)
    base = np.take_along_axis(np.stack(labs, 0), np.where(valid_any, owner, 0)[None, :, :, None], axis=0)[0]
    base[~valid_any] = 0
    return base, valid_any


def expB2_roi_board(model, dev, frame, ring_cams, run_name, base):
    import cv2
    from PIL import Image, ImageDraw, ImageFont
    dirs = erp_dirs()
    Zd = unidepth_depth_to_erp(model, dev, frame, ring_cams)
    ibr, has = ibr_render(Zd, dirs, frame, ring_cams, base)
    save_rgb(REMOTE_OUT / f"{run_name}_expB2_ibr_unidepth.png", ibr)
    save_rgb(REMOTE_OUT / f"{run_name}_expB2_erp_depth_heat.png", heat(Zd, Zd > DMIN))
    try: f = ImageFont.truetype("DejaVuSans.ttf", 15)
    except Exception: f = ImageFont.load_default()
    rows = []
    for name, (x0, y0, x1, y1) in MARKED_ROIS.items():
        b = base[y0:y1, x0:x1]; r = ibr[y0:y1, x0:x1]
        strip = np.concatenate([b, r], 1)
        strip = cv2.resize(strip, (900, int(strip.shape[0] * 900 / max(strip.shape[1], 1))))
        rows.append((name, strip))
    wsh = max(r[1].shape[1] for r in rows)
    sheet = Image.new("RGB", (wsh, sum(r[1].shape[0] + 22 for r in rows) + 26), (12, 12, 16)); d = ImageDraw.Draw(sheet)
    d.text((6, 4), f"{run_name} EXP-B2 ROI: hard_select base | IBR(UniDepth dense depth)", (235, 235, 245), font=f)
    yo = 26
    for name, strip in rows:
        bar = Image.new("RGB", (wsh, 22), (22, 22, 30)); ImageDraw.Draw(bar).text((6, 3), name, (220, 220, 235), font=f)
        sheet.paste(bar, (0, yo)); yo += 22
        sheet.paste(Image.fromarray(strip), (0, yo)); yo += strip.shape[0]
    sheet.save(REMOTE_OUT / f"{run_name}_expB2_roi_board.jpg", quality=90)
    return {"ibr_covered_frac": float((has & (base.sum(-1) > 0)).sum() / max((base.sum(-1) > 0).sum(), 1)),
            "erp_depth_valid_frac": float((Zd > DMIN).mean())}


# ---------------------------------------------------------------------------
# per-case driver
# ---------------------------------------------------------------------------
def run_case(case_spec, run_name, model, dev):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    loader = AV2RingLoader(log_dir); ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(ts)
    ring_cams = list(RING_CAMS_7)
    OUT["_xyz_ego_cache"] = nearest_lidar_ego(log_dir, ts)
    rep = {"case": run_name, "n_lidar_pts": int(len(OUT["_xyz_ego_cache"]))}
    rep["expB1"] = expB1_residual(model, dev, frame, ring_cams, run_name)["overall"]
    if RUN_B2:
        base, _va = hard_select_base(frame, ring_cams)
        rep["expB2"] = expB2_roi_board(model, dev, frame, ring_cams, run_name, base)
    return rep


def build_review_board(reports):
    from PIL import Image
    ps = [REMOTE_OUT / "02a00399_a000_bmw_expB1_depth_board.jpg",
          REMOTE_OUT / "0bae3b5e_a030_clean_far_expB1_depth_board.jpg"]
    if RUN_B2:
        ps += [REMOTE_OUT / "02a00399_a000_bmw_expB2_roi_board.jpg",
               REMOTE_OUT / "0bae3b5e_a030_clean_far_expB2_roi_board.jpg"]
    ims = [Image.open(p) for p in ps if p.exists()]
    if not ims: return
    w = max(i.width for i in ims); board = Image.new("RGB", (w, sum(i.height for i in ims) + 24), (8, 8, 12))
    yo = 24
    for im in ims: board.paste(im, (0, yo)); yo += im.height
    board.save(REMOTE_OUT / "DB77c_expB_review_board.jpg", quality=88)


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    if not import_ok("av2"):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=600, check=False)
    workdir = find_workdir(); OUT["workdir_found"] = bool(workdir)
    if workdir is None: raise RuntimeError("workdir not found among WORKDIR_CANDIDATES")
    sys.path.insert(0, str(workdir / "scripts" / "phase3")); sys.path.insert(0, str(workdir / "code"))
    OUT["install"] = install_unidepth()
    try:
        model, dev = load_unidepth(); OUT["unidepth"]["loaded"] = True; OUT["unidepth"]["device"] = dev
    except Exception as e:
        OUT["unidepth"]["load_error"] = {"type": type(e).__name__, "msg": repr(e)[:300],
                                          "trace_tail": traceback.format_exc()[-1500:]}
        model, dev = None, None
    reports = []
    if model is not None:
        for cs, rn in CASES:
            try:
                reports.append(run_case(cs, rn, model, dev))
            except Exception as e:
                reports.append({"case": rn, "error": {"type": type(e).__name__, "msg": repr(e)[:300],
                                                       "trace_tail": traceback.format_exc()[-1500:]}})
        build_review_board(reports)
    summary = {"status": "db77c_expB_complete" if model is not None else "db77c_expB_unidepth_load_failed",
               "exp_b2_ran": bool(RUN_B2), "pre_registered": TH,
               "unidepth": OUT["unidepth"], "by_case": {r["case"]: r for r in reports},
               "criterion": "EXP-B1 hold-out residual: p90<2m=wall loosens, p90>5m=wall confirmed",
               "runtime_s": round(time.time() - t0, 2)}
    (REMOTE_OUT / "DB77c_expB_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    (REMOTE_OUT / "DB77c_expB_claim.json").write_text(json.dumps({
        "brief": "DB-77C EXP-B UniDepthV2 dense depth replaces densification",
        "classification": "hold_out_residual_diagnostic" + ("_plus_ibr_refeed" if RUN_B2 else ""),
        "source_faithful_repair": False, "red_promotion": False,
        "license_note": "UniDepthV2 weights are CC BY-NC 4.0 (non-commercial)",
        "note": "EXP-B1 is the primary value: does UniDepth dense depth drop the hold-out residual p90 from 12-15m to <2m? EXP-B2 IBR re-feed is a presentation render, not sensor truth."},
        indent=2), encoding="utf-8")
    OUT["status"] = "db77c_expB_completed"; OUT["summary"] = summary
    OUT.pop("_xyz_ego_cache", None)
except Exception as exc:
    OUT.pop("_xyz_ego_cache", None)
    OUT["status"] = "db77c_expB_failed"
    OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT.pop("_xyz_ego_cache", None)
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB77CEXPB_JSON_BEGIN"); print(json.dumps(json_safe(OUT), separators=(",", ":"))); print("DB77CEXPB_JSON_END")
'''
    code = (code
            .replace("__REMOTE_OUT__", REMOTE_OUT)
            .replace("__RESULT__", RESULT)
            .replace("__RUN_B2__", "True" if run_b2 else "False")
            .replace("__TH__", json.dumps(TH)))
    return code


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return ("set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b
            + "').decode('utf-8')\nexec(compile(code, '<db77c_expB_remote>', 'exec'))\nPY")


def poll_job(client, job_id, timeout_s):
    t0 = time.time(); last = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(7); last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running":
            return sanitize(last)
    return sanitize(last or {"state": "poll_timeout"})


def run_remote(timeout_s: int, exp_b2: bool) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient(); status = client.get("/status", timeout=180)
    py = remote_python(run_b2=exp_b2)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(py)], "cwd": "/content", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s)
    remote_result = None
    raw = client.read_file(RESULT, max_size_mb=12)
    if raw is not None:
        try: remote_result = json.loads(raw.decode("utf-8"))
        except Exception: remote_result = None
    if remote_result is None:
        rr = job.get("log_tail", "")
        if "DB77CEXPB_JSON_BEGIN" in rr:
            try: remote_result = json.loads(rr.split("DB77CEXPB_JSON_BEGIN", 1)[1].split("DB77CEXPB_JSON_END", 1)[0].strip())
            except Exception: remote_result = None
    if remote_result is None:
        remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    (OUT_DIR / "DB77c_expB_remote_result.json").write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")
    fetched = {}
    for key, (rn, mb) in FETCH.items():
        local = (OUT_DIR if key in {"summary", "remote_result", "board", "claim"} else (OUT_DIR / "fetch")) / rn
        local.parent.mkdir(parents=True, exist_ok=True)
        rb = client.read_file(REMOTE_OUT + "/" + rn, max_size_mb=mb)
        if rb is None: fetched[key] = {"path": rel(local), "exists": False}
        else: local.write_bytes(rb); fetched[key] = {"path": rel(local), "exists": True, "bytes": len(rb)}
    summary = {}
    sp = OUT_DIR / "DB77c_expB_summary.json"
    if sp.exists():
        try: summary = json.loads(sp.read_text(encoding="utf-8"))
        except Exception: summary = {}
    if not summary: summary = remote_result.get("summary") or {}
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "status": "db77c_expB_unidepth",
        "brief": "DB-77C EXP-B: UniDepthV2 dense metric depth replaces nearest-neighbour LiDAR densification, re-fed to IBR",
        "scope": {"hold_out_residual_metric": True, "dense_metric_depth": True, "exec_count": 1,
                   "runtime_type": status.get("runtime_type"), "exp_b2_ibr_refeed": bool(exp_b2),
                   "iterative_diffusion": False, "source_faithful": False, "red_promotion": False, "gpu_bound": True},
        "unidepth_verification": {
            "install": "git clone https://github.com/lpiccinelli-eth/UniDepth ; pip install --no-deps -e ./UniDepth (keep Colab torch)",
            "load": 'UniDepthV2.from_pretrained("lpiccinelli/unidepth-v2-vitl14")  (HF namespace lpiccinelli/, no -eth)',
            "infer": "model.infer(rgb_CHW_uint8, Pinhole(K=K_torch.unsqueeze(0)))  -> pred['depth'] (HxW). Fallbacks: raw 3x3 tensor; no-K self-estimate.",
            "vram_40gb_ok": "YES — vitl14 inference ~1.8-2.5GB FP32; 40GB ample",
            "license": "CC BY-NC 4.0 (non-commercial) — research OK, not for commercial product",
        },
        "runtime": {"secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file",
                     "status": safe_status(status)},
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail", "cmd"}}),
        "remote_status": remote_result.get("status"), "summary": summary, "fetched_outputs": fetched,
        "pre_registered": TH, "output_location": rel(OUT_DIR), "drive_output_location": "results/db77c_expB_unidepth/",
        "decision": {"vision_check_required": True,
                      "exp_b1_criterion": "p90<2m => geometry wall loosens (run EXP-B2); p90>5m => wall confirmed (UniDepth does not fix edges)"},
        "claim_boundary": [
            "EXP-B1 = hold-out residual diagnostic (cheap, primary value): |UniDepth_depth - LiDAR_depth| at LiDAR px, p50/p90, bucketed edge vs flat.",
            "EXP-B2 (if --exp-b2) = UniDepth dense depth -> ERP -> IBR re-feed; presentation render, NOT sensor truth.",
            "UniDepthV2 weights are CC BY-NC 4.0 (non-commercial). No iterative diffusion, no RED.",
        ],
    }
    scan = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan); manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    (OUT_DIR / "DB77c_expB_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "db77c_expB", "remote_status": remote_result.get("status"),
            "secret_hits": manifest["strict_secret_scan"]["hit_count"],
            "manifest": rel(OUT_DIR / "DB77c_expB_manifest.json")}


def check_compile() -> None:
    py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    compile(remote_python(run_b2=False), "<db77c_expB_remote_b1>", "exec")
    compile(remote_python(run_b2=True), "<db77c_expB_remote_b2>", "exec")
    print(json.dumps({"status": "compile_ok", "wrapper": True, "remote_b1": True, "remote_b2": True}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--run-remote", action="store_true")
    parser.add_argument("--exp-b2", action="store_true", help="also run EXP-B2 IBR re-feed (run only after B1 passes)")
    parser.add_argument("--timeout-s", type=int, default=1800)
    args = parser.parse_args()
    if args.check:
        check_compile(); return
    if args.run_remote:
        print(json.dumps(run_remote(args.timeout_s, args.exp_b2), indent=2)); return
    print(json.dumps({"status": "ready", "message": "Use --check (compile) or --run-remote [--exp-b2] (main agent runs remote)."}, indent=2))


if __name__ == "__main__":
    main()
