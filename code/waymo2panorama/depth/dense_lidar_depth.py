"""LiDAR-anchored DENSE metric depth on the ERP, for E2's N1 reprojection.

Why: raw 64-beam LiDAR + kNN-fill is too sparse on mid-range surfaces (building facades 10-30 m)
— the very surfaces that dominate the seam doubling — so N1 reprojection on that blocky depth
SMEARS them. This module makes a DENSE depth by running Depth Anything V2 per camera (dense but
affine-ambiguous relative disparity) and SCALE-ALIGNING it to the sparse LiDAR hits (metric),
per camera region, in inverse-depth space. Output is a dense per-ERP-pixel ego-range map usable
directly as `convergence_distance_m`.

    1/r_ego(pixel)  =  a_cam * disp_DA(pixel) + b_cam      (robust per-camera affine fit on LiDAR hits)

DA-V2 `predicted_depth` is inverse-depth (disparity): larger = nearer, so a_cam > 0.
"""
from __future__ import annotations

import numpy as np

from waymo2panorama.data_io.av2_loader import RING_CAMS_7


def _project_float_rotonly(arr, K, T_ego_cam, erp_hw):
    """Rotation-only (L1) projection of a FLOAT array to ERP (sphere_projection convention).

    render_camera_to_erp only accepts uint8; we need full-precision disparity, so replicate the
    rotation-only geometry here and cv2.remap the float values. Returns (sampled float w/ NaN
    outside, cos^2 weight) — weight matches render_camera_to_erp's legacy mode.
    """
    import cv2
    H, W = erp_hw
    h_img, w_img = arr.shape[:2]
    uu, vv = np.meshgrid(np.arange(W), np.arange(H))
    theta = np.pi - (uu + 0.5) / W * 2 * np.pi
    phi = np.pi / 2 - (vv + 0.5) / H * np.pi
    cph = np.cos(phi)
    d_ego = np.stack([cph * np.cos(theta), cph * np.sin(theta), np.sin(phi)], -1)
    R_cam_ego = np.asarray(T_ego_cam)[:3, :3].T
    d_cam = d_ego @ R_cam_ego.T
    z = d_cam[..., 2]
    in_front = z > 1e-6
    zs = np.where(in_front, z, 1.0)
    u_img = K[0, 0] * (d_cam[..., 0] / zs) + K[0, 2]
    v_img = K[1, 1] * (d_cam[..., 1] / zs) + K[1, 2]
    m = 0.5
    valid = in_front & (u_img >= m) & (u_img <= w_img - 1 - m) & (v_img >= m) & (v_img <= h_img - 1 - m)
    mapx = np.where(valid, u_img, -1).astype(np.float32)
    mapy = np.where(valid, v_img, -1).astype(np.float32)
    samp = cv2.remap(arr.astype(np.float32), mapx, mapy, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    weight = np.where(valid, np.clip(z, 0, 1) ** 2, 0.0).astype(np.float32)
    samp = np.where(valid, samp, np.nan).astype(np.float32)
    return samp, weight


def _davv2_pipe(model_id, device):
    import torch
    from transformers import pipeline
    dtype = torch.float16 if (torch.cuda.is_available() and device >= 0) else torch.float32
    return pipeline("depth-estimation", model=model_id, device=device, dtype=dtype)


def _disp_of(pipe, image_rgb):
    import cv2
    from PIL import Image
    out = pipe(Image.fromarray(image_rgb))
    pred = out.get("predicted_depth")
    if pred is not None:
        try:
            arr = pred.detach().float().cpu().numpy()
        except AttributeError:
            arr = np.asarray(pred, dtype=np.float32)
        arr = np.squeeze(arr).astype(np.float32)
    else:
        arr = np.asarray(out["depth"], dtype=np.float32)
    if arr.shape[:2] != image_rgb.shape[:2]:
        arr = cv2.resize(arr, (image_rgb.shape[1], image_rgb.shape[0]), interpolation=cv2.INTER_CUBIC)
    return arr.astype(np.float32)


def _lidar_sparse_erp(points_ego, erp_hw, min_range_m=0.5, max_range_m=100.0):
    """Sparse LiDAR ego-range splat to ERP (no densify). Returns (range map with NaN, hit mask)."""
    H, W = erp_hw
    r = np.linalg.norm(points_ego, axis=1)
    keep = (r >= min_range_m) & (r <= max_range_m)
    p = points_ego[keep]; r = r[keep].astype(np.float32)
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    theta = np.arctan2(y, x)
    phi = np.arctan2(z, np.sqrt(x * x + y * y))
    u = np.mod(np.round((np.pi - theta) / (2 * np.pi) * W - 0.5).astype(np.int64), W)
    v = np.round((np.pi / 2 - phi) / np.pi * H - 0.5).astype(np.int64)
    ok = (v >= 0) & (v < H)
    u, v, r = u[ok], v[ok], r[ok]
    order = np.argsort(-r)  # nearer overwrites
    dep = np.full((H, W), np.nan, np.float32)
    dep[v[order], u[order]] = r[order]
    return dep, np.isfinite(dep)


def _robust_affine(disp, inv_r, iters=2):
    """Fit inv_r = a*disp + b robustly (drop >2.5 MAD residuals). Returns (a, b, n_used)."""
    d, y = disp.astype(np.float64), inv_r.astype(np.float64)
    keep = np.ones(len(d), bool)
    a, b = 0.0, 0.0
    for _ in range(iters + 1):
        if keep.sum() < 12:
            break
        A = np.stack([d[keep], np.ones(keep.sum())], 1)
        sol, *_ = np.linalg.lstsq(A, y[keep], rcond=None)
        a, b = float(sol[0]), float(sol[1])
        res = np.abs((a * d + b) - y)
        mad = np.median(np.abs(res - np.median(res))) + 1e-9
        keep = res < 2.5 * 1.4826 * mad
    return a, b, int(keep.sum())


def dense_metric_depth_erp(frame, points_ego, erp_hw, model_id="depth-anything/Depth-Anything-V2-Small-hf",
                           device=-1, min_r=0.5, max_r=100.0, fill_far_m=1000.0):
    """Return (dense_depth (H,W) ego-range metres, diag dict).

    Pixels with a valid per-camera fit get DA-V2-derived dense metric depth; pixels whose camera
    had too few LiDAR hits to fit fall back to fill_far_m (-> rotation-only/L1 in N1).
    """
    H, W = erp_hw
    pipe = _davv2_pipe(model_id, device)
    disp_slabs, weights = [], []
    for cam in RING_CAMS_7:
        disp = _disp_of(pipe, frame.images[cam])
        c = frame.calibrations[cam]
        d_erp, w = _project_float_rotonly(disp, c.K, c.T_ego_cam, erp_hw)
        disp_slabs.append(d_erp); weights.append(w)
    wstack = np.stack(weights, 0)
    label = wstack.argmax(0)
    valid = wstack.max(0) > 0
    disp_erp = np.choose(label, [np.nan_to_num(d, nan=0.0) for d in disp_slabs])  # disp from selected cam

    lidar_dep, lidar_hit = _lidar_sparse_erp(points_ego, erp_hw, min_r, max_r)

    dense = np.full((H, W), float(fill_far_m), np.float32)
    diag = {"model_id": model_id, "per_cam_fit": {}}
    for i, cam in enumerate(RING_CAMS_7):
        region = (label == i) & valid
        fit_px = region & lidar_hit & np.isfinite(disp_erp)
        n = int(fit_px.sum())
        if n < 30:
            diag["per_cam_fit"][cam] = {"n": n, "status": "too_few_hits->fallback_far"}
            continue
        a, b, n_used = _robust_affine(disp_erp[fit_px], 1.0 / lidar_dep[fit_px])
        denom = a * disp_erp + b
        rr = np.where(denom > 1e-4, 1.0 / np.maximum(denom, 1e-4), float(fill_far_m))
        rr = np.clip(rr, min_r, max_r)
        sel = region & np.isfinite(disp_erp) & (denom > 1e-4)
        dense[sel] = rr[sel].astype(np.float32)
        diag["per_cam_fit"][cam] = {"n": n, "n_used": n_used, "a": round(a, 6), "b": round(b, 6)}
    diag["dense_support_pct"] = round(100.0 * float((dense < fill_far_m).mean()), 2)
    return dense, diag
