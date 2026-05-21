"""
T18 — Depth backbone drop-in replacement (Depth Pro / Metric3D / Pi3-baseline).

Question being answered: is L3 forward-splat's −3.15 dB cycle-PSNR loss caused
by the **backbone** (Pi3 metric depth not good enough) or by the **algorithm**
(forward-splat itself is wrong-channel)?

This script swaps Pi3 for a different per-cam metric-depth backbone and re-runs:
  (a) LiDAR-anchored depth eval (abs_rel, RMSE, δ<1.25)  → backbone quality
  (b) L3 forward-splat cycle-consistency PSNR             → headline comparison

If new backbone abs_rel < 0.18 AND L3 PSNR closes the gap → backbone matters.
If new backbone abs_rel ~ 0.20 AND L3 PSNR still loses big → algorithm is wrong.

Backbones supported:
  - depthpro  (default): Apple Depth Pro via HuggingFace transformers
                          (`apple/DepthPro-hf`, pip-only — no git clone).
  - metric3d  (fallback): Metric3D v2 via torch.hub (requires internet).
  - pi3       (sanity baseline): re-runs Pi3 for direct comparison.

Each backbone produces, per cam:
  - Per-pixel metric depth (H, W) at the letterboxed 504×504 input scale
  - Per-pixel 3D points in CAMERA frame: pt = depth * unproject(K_lb, u, v)
  - Per-pixel 3D points in EGO frame: T_ego_cam @ pt

We then reuse the *exact same* downstream as Pi3:
  - LiDAR eval uses the project_lidar_to_cam + compute_depth_metrics from
    scripts/phase2/eval_pi3_vs_lidar.py
  - L3 cycle-consistency uses reconstruct_l3 from
    scripts/phase2/eval_cycle_consistency.py — fed the new per-cam depth via
    points_<cam>.npy in the SAME format as Pi3 outputs.

This is the cleanest "swap one block" experiment we can do without changing
the L3 forward-splat algorithm.

Outputs (under --output-dir):
  <backbone>_points_<cam>.npy      (H, W, 3) — points in EGO frame
  <backbone>_local_points_<cam>.npy(H, W, 3) — points in CAM frame
  <backbone>_depth_<cam>.npy       (H, W)    — per-pixel metric depth
  <backbone>_conf_<cam>.npy        (H, W)    — confidence (default 1.0 for Depth Pro)
  <backbone>_depth_viz_<cam>.png   — colored depth viz
  image_<cam>.png                  — letterboxed 504x504 input (same as Pi3)
  av2_K_letterboxed_<cam>.npy      — same as Pi3
  av2_T_ego_cam_<cam>.npy          — same as Pi3
  summary.json                     — per-cam stats, runtime
  <backbone>_lidar_metrics.json    — LiDAR eval results (the done_marker)
  <backbone>_cycle_metrics.json    — L3 cycle PSNR (optional; on by default)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- repo wiring

DEFAULT_W2P_CODE_REL = "../../code"
DEFAULT_PI3_REPO_REL = "../../../../../01-pi3/code/official/Pi3"


def _wire_imports(w2p_code: Path, pi3_repo: Path | None = None) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"missing waymo2panorama code dir: {w2p_code}")
    sys.path.insert(0, str(w2p_code))
    if pi3_repo is not None and pi3_repo.exists():
        sys.path.insert(0, str(pi3_repo))


# ------------------------------------------------------------- letterbox / K

def letterbox_to_square(img: np.ndarray, target_side: int = 504) -> tuple[np.ndarray, dict]:
    """Pad shorter side with zeros to make square, then resize to target_side."""
    h, w, c = img.shape
    side = max(h, w)
    pad_top = (side - h) // 2
    pad_left = (side - w) // 2
    sq = np.zeros((side, side, c), dtype=img.dtype)
    sq[pad_top:pad_top + h, pad_left:pad_left + w] = img
    pil = Image.fromarray(sq).resize((target_side, target_side), Image.Resampling.LANCZOS)
    out = np.asarray(pil)
    scale = target_side / side
    return out, {"pad_top": pad_top, "pad_left": pad_left, "scale": scale, "side": side}


def rescale_K_for_letterbox(K: np.ndarray, lb: dict) -> np.ndarray:
    K2 = K.astype(np.float64).copy()
    K2[0, 2] += lb["pad_left"]
    K2[1, 2] += lb["pad_top"]
    K2[0, 0] *= lb["scale"]
    K2[1, 1] *= lb["scale"]
    K2[0, 2] *= lb["scale"]
    K2[1, 2] *= lb["scale"]
    return K2


# ------------------------------------------------------------- back-projection

def depth_to_cam_points(depth: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Back-project per-pixel depth to camera-frame 3D points.

    Args:
        depth: (H, W) float32 metric depth (z in cam frame, meters)
        K: (3, 3) intrinsics matching depth resolution (pinhole, undistorted)

    Returns:
        (H, W, 3) float32 points in camera frame; z == depth at valid pixels.
        Points at depth<=0 or non-finite are returned as NaN.
    """
    H, W = depth.shape
    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])
    uu, vv = np.meshgrid(np.arange(W, dtype=np.float64),
                         np.arange(H, dtype=np.float64))
    x_n = (uu - cx) / fx
    y_n = (vv - cy) / fy
    d = depth.astype(np.float64)
    pts = np.stack([x_n * d, y_n * d, d], axis=-1)
    invalid = ~np.isfinite(d) | (d <= 0)
    if invalid.any():
        pts[invalid] = np.nan
    return pts.astype(np.float32)


def cam_points_to_ego(pts_cam: np.ndarray, T_ego_cam: np.ndarray) -> np.ndarray:
    """Apply 4×4 SE(3) to (H, W, 3) camera-frame points."""
    R = T_ego_cam[:3, :3].astype(np.float32)
    t = T_ego_cam[:3, 3].astype(np.float32)
    flat = pts_cam.reshape(-1, 3)
    out = flat @ R.T + t
    return out.reshape(pts_cam.shape).astype(np.float32)


# --------------------------------------------------------- depth viz helper

def depth_to_colormap_png(depth: np.ndarray, max_depth: float = 50.0) -> np.ndarray:
    """Cheap matplotlib-free turbo-like colormap for a depth image."""
    d = depth.astype(np.float32)
    valid = np.isfinite(d) & (d > 0)
    norm = np.clip(d / max_depth, 0.0, 1.0)
    norm[~valid] = 0.0
    # crude inferno-ish: dark blue → cyan → yellow → red
    r = np.clip(1.5 * norm - 0.5, 0, 1)
    g = np.clip(1.5 - 1.5 * np.abs(norm - 0.5) * 2, 0, 1)
    b = np.clip(1.5 - 2.0 * norm, 0, 1)
    rgb = np.stack([r, g, b], axis=-1)
    rgb[~valid] = 0
    return (rgb * 255).astype(np.uint8)


# ============================================================ BACKBONES

def run_depthpro(images_lb: dict[str, np.ndarray], device: str = "cuda") -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict]:
    """Apple Depth Pro via HuggingFace transformers. PyPI-only path.

    Returns:
        depth_by_cam: {cam: (H, W) float32 metric depth in meters}
        conf_by_cam:  {cam: (H, W) float32 in [0, 1]}  (placeholder = 1.0 everywhere)
        meta: dict with timing + checkpoint
    """
    import torch
    from transformers import AutoImageProcessor, DepthProForDepthEstimation

    ckpt = "apple/DepthPro-hf"
    t_load = time.time()
    processor = AutoImageProcessor.from_pretrained(ckpt)
    # use_fov_model=False is faster and we already have AV2-truth K; we don't need DepthPro to predict FOV.
    model = DepthProForDepthEstimation.from_pretrained(ckpt, use_fov_model=False)
    device_t = torch.device(device if device == "cpu" or torch.cuda.is_available() else "cpu")
    model = model.to(device_t).eval()
    use_bf16 = device_t.type == "cuda" and torch.cuda.get_device_capability()[0] >= 8
    if use_bf16:
        model = model.to(torch.bfloat16)
    load_s = time.time() - t_load
    print(f"[depthpro] ckpt={ckpt} loaded in {load_s:.1f}s on {device_t} bf16={use_bf16}")

    depth_by_cam: dict[str, np.ndarray] = {}
    conf_by_cam: dict[str, np.ndarray] = {}
    per_cam_fwd: dict[str, float] = {}

    for cam, img_lb in images_lb.items():
        pil_img = Image.fromarray(img_lb)
        inputs = processor(images=pil_img, return_tensors="pt").to(device_t)
        if use_bf16:
            inputs = {k: (v.to(torch.bfloat16) if v.is_floating_point() else v) for k, v in inputs.items()}

        t0 = time.time()
        with torch.no_grad():
            outputs = model(**inputs)
        if device_t.type == "cuda":
            torch.cuda.synchronize()
        fwd_s = time.time() - t0

        H, W = img_lb.shape[:2]
        post = processor.post_process_depth_estimation(outputs, target_sizes=[(H, W)])
        depth = post[0]["predicted_depth"].detach().float().cpu().numpy()  # (H, W)
        # Sanity clamp: DepthPro can predict 0 for sky; we treat <=0 as invalid downstream.
        depth_by_cam[cam] = depth.astype(np.float32)
        conf_by_cam[cam] = np.ones((H, W), dtype=np.float32)  # placeholder; DepthPro has no conf
        per_cam_fwd[cam] = float(fwd_s)
        print(f"[depthpro] {cam} fwd={fwd_s:.2f}s "
              f"depth_min={depth.min():.2f} median={float(np.median(depth)):.2f} max={depth.max():.2f}")

    meta = {
        "backbone": "DepthPro",
        "checkpoint": ckpt,
        "device": str(device_t),
        "autocast_bf16": use_bf16,
        "model_load_s": round(load_s, 3),
        "per_cam_forward_s": per_cam_fwd,
        "mean_forward_s": round(float(np.mean(list(per_cam_fwd.values()))), 3),
    }
    return depth_by_cam, conf_by_cam, meta


def run_metric3d(images_lb: dict[str, np.ndarray], device: str = "cuda") -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict]:
    """Metric3D v2 via torch.hub. Fallback backbone if Depth Pro doesn't pip-install."""
    import torch
    t_load = time.time()
    # YOSO Metric3D v2 release; torch.hub will download repo to cache.
    model = torch.hub.load("yvanyin/metric3d", "metric3d_vit_small", pretrain=True)
    device_t = torch.device(device if device == "cpu" or torch.cuda.is_available() else "cpu")
    model = model.to(device_t).eval()
    load_s = time.time() - t_load
    print(f"[metric3d] loaded in {load_s:.1f}s on {device_t}")

    depth_by_cam: dict[str, np.ndarray] = {}
    conf_by_cam: dict[str, np.ndarray] = {}
    per_cam_fwd: dict[str, float] = {}

    # Metric3D wants a normalized RGB tensor; preprocess is in its forward.
    for cam, img_lb in images_lb.items():
        H, W = img_lb.shape[:2]
        rgb = img_lb.astype(np.float32) / 255.0
        rgb_t = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).to(device_t)

        t0 = time.time()
        with torch.no_grad():
            # Metric3D v2 forward signature returns (depth, confidence) or a dict; handle both.
            out = model(rgb_t)
        if device_t.type == "cuda":
            torch.cuda.synchronize()
        fwd_s = time.time() - t0

        if isinstance(out, dict):
            depth = out.get("depth", out.get("predicted_depth"))
            conf = out.get("confidence", None)
        elif isinstance(out, (list, tuple)):
            depth = out[0]
            conf = out[1] if len(out) > 1 else None
        else:
            depth = out
            conf = None

        depth_np = depth.squeeze().detach().float().cpu().numpy()
        if depth_np.shape != (H, W):
            # resize via PIL bilinear
            depth_np = np.asarray(
                Image.fromarray(depth_np).resize((W, H), Image.Resampling.BILINEAR),
                dtype=np.float32,
            )
        if conf is None:
            conf_np = np.ones((H, W), dtype=np.float32)
        else:
            conf_np = conf.squeeze().detach().float().cpu().numpy()
            if conf_np.shape != (H, W):
                conf_np = np.asarray(
                    Image.fromarray(conf_np).resize((W, H), Image.Resampling.BILINEAR),
                    dtype=np.float32,
                )

        depth_by_cam[cam] = depth_np.astype(np.float32)
        conf_by_cam[cam] = conf_np.astype(np.float32)
        per_cam_fwd[cam] = float(fwd_s)
        print(f"[metric3d] {cam} fwd={fwd_s:.2f}s depth_med={float(np.median(depth_np)):.2f}")

    meta = {
        "backbone": "Metric3Dv2",
        "checkpoint": "yvanyin/metric3d::metric3d_vit_small",
        "device": str(device_t),
        "model_load_s": round(load_s, 3),
        "per_cam_forward_s": per_cam_fwd,
        "mean_forward_s": round(float(np.mean(list(per_cam_fwd.values()))), 3),
    }
    return depth_by_cam, conf_by_cam, meta


def run_pi3_baseline(images_lb: dict[str, np.ndarray],
                     cams_order: list[str],
                     device: str = "cuda") -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict]:
    """Pi3X sanity baseline. Re-runs the same forward used in Phase 3 W1."""
    import torch
    from pi3.models.pi3x import Pi3X

    t_load = time.time()
    model = Pi3X.from_pretrained("yyfz233/Pi3X").eval()
    model.disable_multimodal()
    device_t = torch.device(device if device == "cpu" or torch.cuda.is_available() else "cpu")
    model = model.to(device_t)
    use_bf16 = device_t.type == "cuda" and torch.cuda.get_device_capability()[0] >= 8
    load_s = time.time() - t_load
    print(f"[pi3] loaded in {load_s:.1f}s on {device_t} bf16={use_bf16}")

    imgs = np.stack([images_lb[c] for c in cams_order], axis=0).astype(np.float32) / 255.0
    imgs = np.transpose(imgs, (0, 3, 1, 2))  # (V, 3, H, W)
    imgs_t = torch.from_numpy(imgs).unsqueeze(0).to(device_t)

    t0 = time.time()
    with torch.no_grad():
        if use_bf16:
            with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                res = model(imgs=imgs_t)
        else:
            res = model(imgs=imgs_t)
    if device_t.type == "cuda":
        torch.cuda.synchronize()
    fwd_s = time.time() - t0

    local_points = res["local_points"][0].detach().float().cpu().numpy()  # (V, H, W, 3)
    conf = res["conf"][0].detach().float().cpu().numpy()
    if conf.ndim == 4 and conf.shape[-1] == 1:
        conf = conf[..., 0]
    conf_prob = 1.0 / (1.0 + np.exp(-conf))

    depth_by_cam: dict[str, np.ndarray] = {}
    conf_by_cam: dict[str, np.ndarray] = {}
    for i, cam in enumerate(cams_order):
        depth_by_cam[cam] = local_points[i, ..., 2].astype(np.float32)
        conf_by_cam[cam] = conf_prob[i].astype(np.float32)

    meta = {
        "backbone": "Pi3X (baseline)",
        "checkpoint": "yyfz233/Pi3X",
        "device": str(device_t),
        "autocast_bf16": use_bf16,
        "model_load_s": round(load_s, 3),
        "forward_s_7cam_joint": round(fwd_s, 3),
    }
    return depth_by_cam, conf_by_cam, meta


# ============================================================ LiDAR EVAL

def lidar_eval_one_backbone(
    log_dir: Path,
    anchor_ts_ns: int,
    cams: list[str],
    cam_K_lb: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    depth_by_cam: dict[str, np.ndarray],
    conf_by_cam: dict[str, np.ndarray],
    img_side: int = 504,
    min_dist: float = 0.5,
    max_dist: float = 60.0,
    conf_threshold: float = 0.1,
) -> dict[str, Any]:
    """Reuse project_lidar_to_cam + compute_depth_metrics from eval_pi3_vs_lidar.py."""
    from eval_pi3_vs_lidar import (
        find_closest_lidar_sweep, load_lidar_points_ego,
        project_lidar_to_cam, compute_depth_metrics,
    )

    sweep_path, sweep_ts = find_closest_lidar_sweep(log_dir, anchor_ts_ns)
    sweep_dt_ms = abs(sweep_ts - anchor_ts_ns) / 1e6
    points_ego = load_lidar_points_ego(sweep_path)
    print(f"[lidar] sweep={sweep_path.name} dt={sweep_dt_ms:.1f}ms n_pts={points_ego.shape[0]}")

    overall_lidar: list[np.ndarray] = []
    overall_pred: list[np.ndarray] = []
    per_cam: dict[str, dict] = {}

    for cam in cams:
        depth_cam = depth_by_cam[cam]
        conf_cam = conf_by_cam[cam]
        proj = project_lidar_to_cam(
            points_ego, cam_T_ego_cam[cam], cam_K_lb[cam],
            img_side=img_side, min_dist=min_dist, max_dist=max_dist,
        )
        valid = proj["valid_mask"]
        u_v = proj["u"][valid]
        v_v = proj["v"][valid]
        lidar_d = proj["z_cam"][valid]

        # Nearest-neighbor sample our depth + conf at each projected LiDAR point.
        H, W = depth_cam.shape
        ui = np.clip(np.round(u_v).astype(np.int64), 0, W - 1)
        vi = np.clip(np.round(v_v).astype(np.int64), 0, H - 1)
        pred_d = depth_cam[vi, ui]
        c = conf_cam[vi, ui]
        has = np.isfinite(pred_d) & (pred_d > 0.0) & (c > conf_threshold)
        lidar_m = lidar_d[has]
        pred_m = pred_d[has]
        metrics = compute_depth_metrics(lidar_m, pred_m)
        metrics["n_lidar_in_fov"] = int(u_v.size)
        metrics["n_matched"] = int(lidar_m.size)
        per_cam[cam] = metrics
        overall_lidar.append(lidar_m)
        overall_pred.append(pred_m)
        print(f"[lidar] {cam:22s} matched={lidar_m.size:6d} "
              f"abs_rel={metrics['abs_rel']:.3f} rmse={metrics['rmse']:.2f}m "
              f"d1.25={metrics['delta_1_25']:.3f}")

    all_lidar = np.concatenate(overall_lidar) if overall_lidar else np.empty(0)
    all_pred = np.concatenate(overall_pred) if overall_pred else np.empty(0)
    overall = compute_depth_metrics(all_lidar, all_pred)
    print(f"[lidar] OVERALL matched={all_lidar.size:6d} "
          f"abs_rel={overall['abs_rel']:.3f} rmse={overall['rmse']:.2f}m "
          f"d1.25={overall['delta_1_25']:.3f}")

    return {
        "config": {
            "log_dir": str(log_dir),
            "anchor_timestamp_ns": int(anchor_ts_ns),
            "lidar_sweep_timestamp_ns": int(sweep_ts),
            "lidar_sweep_delta_ms": float(sweep_dt_ms),
            "lidar_sweep_file": sweep_path.name,
            "img_side": img_side,
            "min_dist_m": min_dist,
            "max_dist_m": max_dist,
            "conf_threshold": conf_threshold,
        },
        "overall": overall,
        "per_cam": per_cam,
    }


# ============================================================ CYCLE EVAL (L3)

def l3_cycle_eval_one_backbone(
    cams: list[str],
    cam_K_lb: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_rgb: dict[str, np.ndarray],
    cam_points_ego: dict[str, np.ndarray],
    cam_conf: dict[str, np.ndarray],
    conf_threshold: float = 0.1,
    min_dist: float = 0.5,
    max_dist: float = 60.0,
) -> dict[str, Any]:
    """Reuse reconstruct_l3 + reconstruct_l1 + psnr from eval_cycle_consistency.py."""
    from eval_cycle_consistency import (
        reconstruct_l1, reconstruct_l3, psnr, l1_mae,
    )

    rows: list[dict] = []
    print(f"[cycle] holding out each cam → L1 vs L3 PSNR on intersection mask:")
    for holdout in cams:
        others = [c for c in cams if c != holdout]
        gt = cam_rgb[holdout]

        l1_rgb, l1_mask = reconstruct_l1(holdout, others, cam_K_lb, cam_T_ego_cam, cam_rgb)
        l3_rgb, l3_mask = reconstruct_l3(
            holdout, others, cam_K_lb, cam_T_ego_cam, cam_rgb,
            cam_points_ego, cam_conf,
            conf_threshold=conf_threshold,
            min_dist=min_dist,
            max_dist=max_dist,
        )
        l1_rgb_u8 = np.clip(l1_rgb, 0, 255).astype(np.uint8)
        l3_rgb_u8 = np.clip(l3_rgb, 0, 255).astype(np.uint8)
        intersect = l1_mask & l3_mask
        psnr_l1 = psnr(gt, l1_rgb_u8, mask=intersect)
        psnr_l3 = psnr(gt, l3_rgb_u8, mask=intersect)
        mae_l1 = l1_mae(gt, l1_rgb_u8, mask=intersect)
        mae_l3 = l1_mae(gt, l3_rgb_u8, mask=intersect)
        rows.append({
            "cam": holdout,
            "coverage_L1": float(l1_mask.mean()),
            "coverage_L3": float(l3_mask.mean()),
            "coverage_intersection": float(intersect.mean()),
            "PSNR_L1": psnr_l1, "PSNR_L3": psnr_l3,
            "PSNR_delta_L3_minus_L1": psnr_l3 - psnr_l1,
            "MAE_L1": mae_l1, "MAE_L3": mae_l3,
        })
        print(f"[cycle] {holdout:22s} L1={psnr_l1:6.2f}  L3={psnr_l3:6.2f}  Δ={psnr_l3 - psnr_l1:+5.2f}")

    finite_l1 = [r["PSNR_L1"] for r in rows if np.isfinite(r["PSNR_L1"])]
    finite_l3 = [r["PSNR_L3"] for r in rows if np.isfinite(r["PSNR_L3"])]
    mean_l1 = float(np.mean(finite_l1)) if finite_l1 else float("nan")
    mean_l3 = float(np.mean(finite_l3)) if finite_l3 else float("nan")
    print(f"[cycle] MEAN  L1={mean_l1:6.2f}  L3={mean_l3:6.2f}  Δ={mean_l3 - mean_l1:+5.2f}")
    return {
        "per_cam": rows,
        "mean_PSNR_L1": mean_l1,
        "mean_PSNR_L3": mean_l3,
        "mean_PSNR_delta_L3_minus_L1": mean_l3 - mean_l1,
        "config": {
            "conf_threshold": conf_threshold,
            "min_dist_m": min_dist,
            "max_dist_m": max_dist,
        },
    }


# ============================================================ MAIN

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--backbone", choices=["depthpro", "metric3d", "pi3"], default="depthpro")
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, default=60)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--pi3-repo", default=None,
                    help="Only required for --backbone pi3 (sanity baseline mode)")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--target-side", type=int, default=504,
                    help="Letterbox side; 504 to match Pi3 outputs for apples-to-apples")
    ap.add_argument("--skip-cycle", action="store_true",
                    help="Run only LiDAR eval (skip L3 cycle-consistency)")
    ap.add_argument("--conf-threshold", type=float, default=0.1,
                    help="Confidence threshold for both LiDAR sampling and L3 splat")
    ap.add_argument("--min-distance-m", type=float, default=0.5)
    ap.add_argument("--max-distance-m", type=float, default=60.0)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    pi3_repo = Path(args.pi3_repo) if args.pi3_repo else (here / DEFAULT_PI3_REPO_REL).resolve()
    _wire_imports(w2p_code, pi3_repo if args.backbone == "pi3" else None)
    # Also wire up scripts/phase2 so we can import eval helpers.
    sys.path.insert(0, str((here / "../phase2").resolve()))

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- load AV2 frame ---
    loader = AV2RingLoader(Path(args.log_dir))
    ts_all = loader.anchor_timestamps_ns()
    if args.anchor_idx >= len(ts_all):
        raise IndexError(f"anchor_idx={args.anchor_idx} >= {len(ts_all)} anchors")
    anchor_ts = ts_all[args.anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)
    cams = list(RING_CAMS_7)
    print(f"[t18] backbone={args.backbone} log={Path(args.log_dir).name} "
          f"anchor_idx={args.anchor_idx} ts={anchor_ts}")

    # --- letterbox + save artifacts that downstream eval will read ---
    images_lb: dict[str, np.ndarray] = {}
    cam_K_lb: dict[str, np.ndarray] = {}
    cam_T_ego_cam: dict[str, np.ndarray] = {}
    cam_letterbox: dict[str, dict] = {}
    for cam in cams:
        sq, lb = letterbox_to_square(frame.images[cam], target_side=args.target_side)
        K2 = rescale_K_for_letterbox(frame.calibrations[cam].K, lb)
        images_lb[cam] = sq
        cam_K_lb[cam] = K2
        cam_T_ego_cam[cam] = frame.calibrations[cam].T_ego_cam
        cam_letterbox[cam] = lb
        Image.fromarray(sq).save(out_dir / f"image_{cam}.png")
        np.save(out_dir / f"av2_K_letterboxed_{cam}.npy", K2)
        np.save(out_dir / f"av2_T_ego_cam_{cam}.npy", cam_T_ego_cam[cam])

    # --- run backbone ---
    if args.backbone == "depthpro":
        depth_by_cam, conf_by_cam, backbone_meta = run_depthpro(images_lb, device=args.device)
    elif args.backbone == "metric3d":
        depth_by_cam, conf_by_cam, backbone_meta = run_metric3d(images_lb, device=args.device)
    elif args.backbone == "pi3":
        depth_by_cam, conf_by_cam, backbone_meta = run_pi3_baseline(images_lb, cams, device=args.device)
    else:
        raise ValueError(f"unknown backbone {args.backbone}")

    # --- back-project to cam frame, lift to ego frame ---
    cam_points: dict[str, np.ndarray] = {}
    ego_points: dict[str, np.ndarray] = {}
    per_cam_summary: dict[str, dict] = {}
    for cam in cams:
        depth = depth_by_cam[cam]
        K = cam_K_lb[cam]
        pts_cam = depth_to_cam_points(depth, K)
        pts_ego = cam_points_to_ego(pts_cam, cam_T_ego_cam[cam])
        cam_points[cam] = pts_cam
        ego_points[cam] = pts_ego

        bk = args.backbone
        np.save(out_dir / f"{bk}_depth_{cam}.npy", depth)
        np.save(out_dir / f"{bk}_conf_{cam}.npy", conf_by_cam[cam])
        np.save(out_dir / f"{bk}_local_points_{cam}.npy", pts_cam)
        np.save(out_dir / f"{bk}_points_{cam}.npy", pts_ego)
        viz = depth_to_colormap_png(depth, max_depth=50.0)
        Image.fromarray(viz).save(out_dir / f"{bk}_depth_viz_{cam}.png")

        valid = np.isfinite(depth) & (depth > 0)
        per_cam_summary[cam] = {
            "depth_min": float(depth[valid].min()) if valid.any() else None,
            "depth_median": float(np.median(depth[valid])) if valid.any() else None,
            "depth_p90": float(np.percentile(depth[valid], 90)) if valid.any() else None,
            "depth_max": float(depth[valid].max()) if valid.any() else None,
            "valid_frac": float(valid.mean()),
            "letterbox": cam_letterbox[cam],
        }

    # --- run-summary ---
    summary = {
        "anchor_idx": args.anchor_idx,
        "anchor_timestamp_ns": int(anchor_ts),
        "log_dir": str(args.log_dir),
        "cameras": cams,
        "target_side": args.target_side,
        "backbone_meta": backbone_meta,
        "per_cam": per_cam_summary,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # --- LiDAR eval ---
    lidar_metrics = lidar_eval_one_backbone(
        log_dir=Path(args.log_dir),
        anchor_ts_ns=anchor_ts,
        cams=cams,
        cam_K_lb=cam_K_lb,
        cam_T_ego_cam=cam_T_ego_cam,
        depth_by_cam=depth_by_cam,
        conf_by_cam=conf_by_cam,
        img_side=args.target_side,
        min_dist=args.min_distance_m,
        max_dist=args.max_distance_m,
        conf_threshold=args.conf_threshold,
    )
    lidar_out_name = f"{args.backbone}_lidar_metrics.json"
    (out_dir / lidar_out_name).write_text(json.dumps(lidar_metrics, indent=2), encoding="utf-8")
    print(f"[t18] wrote {out_dir / lidar_out_name}")

    # --- L3 cycle-consistency ---
    if not args.skip_cycle:
        # Reconstruct_l3 expects (H, W, 3) ego points + (H, W) conf — exactly what we built.
        # cam_rgb wants uint8 H W 3 — we have images_lb.
        cycle_metrics = l3_cycle_eval_one_backbone(
            cams=cams,
            cam_K_lb=cam_K_lb,
            cam_T_ego_cam=cam_T_ego_cam,
            cam_rgb={c: images_lb[c].astype(np.uint8) for c in cams},
            cam_points_ego=ego_points,
            cam_conf=conf_by_cam,
            conf_threshold=args.conf_threshold,
            min_dist=args.min_distance_m,
            max_dist=args.max_distance_m,
        )
        cycle_out_name = f"{args.backbone}_cycle_metrics.json"
        (out_dir / cycle_out_name).write_text(json.dumps(cycle_metrics, indent=2), encoding="utf-8")
        print(f"[t18] wrote {out_dir / cycle_out_name}")

    print(f"[t18] DONE backbone={args.backbone} → {out_dir}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    raise SystemExit(main())
