#!/usr/bin/env python
"""DB45i VGGT calibrated residual extractor gate.

Evidence-only VGGT residual extractor. It may run one bounded A100 job on the
BMW anchor if the Colab executor is reachable. It saves/decode pose evidence,
fits VGGT camera centers to the AV2/Waymo-style rig by Sim(3), and reduces
owner-UV pointmaps into residual diagnostics. It never renders or repairs an
ERP and never promotes a RED seam without DB45b/DB45h target-surface support.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit"
DB45B = OUT_DIR / "db45b_evidence_permission_calibration_manifest.json"
DB45F = OUT_DIR / "db45f_vggt_target_uv_sampling_gate_manifest.json"
DB45G = OUT_DIR / "db45g_vggt_pose_decode_readiness_manifest.json"
DB45H = OUT_DIR / "db45h_vggt_residual_job_contract_manifest.json"

REMOTE_RESULT = OUT_DIR / "db45i_vggt_calibrated_residual_remote_result.json"
MANIFEST = OUT_DIR / "db45i_vggt_calibrated_residual_manifest.json"
BOARD = OUT_DIR / "db45i_vggt_calibrated_residual_board.jpg"

BMW_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
ANCHOR = 0

ROIS = {
    "db25_longline": {
        "segment_id": "db45_db25_longline_low_evidence",
        "label": "DB25 long-line low-support ROI",
        "roi_xyxy": [850, 420, 1650, 720],
        "known_lidar_support_frac": 0.094,
    },
    "db41_right_roi": {
        "segment_id": "db45_db41_rightline_low_lidar",
        "label": "DB41 right-white-line ROI",
        "roi_xyxy": [1440, 360, 2048, 720],
        "known_lidar_support_frac": 0.084,
    },
    "db41_lower_right_roi": {
        "segment_id": "db45_db41_lower_right_zero_lidar",
        "label": "DB41 lower-right zero-LiDAR ROI",
        "roi_xyxy": [1580, 560, 2048, 790],
        "known_lidar_support_frac": 0.0,
    },
}

SECRET_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
]
SECRET_BYTE_PATTERNS = [
    re.compile(rb"hf_[A-Za-z0-9]{20,}"),
    re.compile(rb"Bearer\s+[A-Za-z0-9._-]{20,}"),
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def fmt(x: object, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, bool):
        return "true" if x else "false"
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    width: int,
    color: tuple[int, int, int],
    size: int = 14,
    line_gap: int = 5,
) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw.text((x, y), line, fill=color, font=font(size))
        y += size + line_gap
    return y


def pill(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fill: tuple[int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=6, fill=fill)
    draw.text((box[0] + 10, box[1] + 7), text, fill=(255, 255, 255), font=font(14))


def scan_secret_hits(paths: list[Path]) -> list[dict[str, str]]:
    hits = []
    for path in paths:
        if not path.exists() or path.is_dir():
            continue
        data = path.read_bytes()
        for pat in SECRET_BYTE_PATTERNS:
            if pat.search(data):
                hits.append({"path": rel(path), "pattern": pat.pattern.decode("ascii", errors="ignore")})
                break
    return hits


def sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items() if k.lower() not in {"token", "authorization"}}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str):
        out = obj
        for pat in SECRET_PATTERNS:
            out = pat.sub("<REDACTED_SECRET>", out)
        return out
    return obj


def _post_json(url: str, token: str, path: str, body: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, token: str, path: str, timeout: int = 180) -> dict[str, Any]:
    req = urllib.request.Request(url.rstrip("/") + path, method="GET")
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_remote_json(log_tail: str) -> dict[str, Any]:
    match = re.search(r"DB45I_RESULT_JSON_START\n(.*?)\nDB45I_RESULT_JSON_END", log_tail, re.S)
    if match:
        return json.loads(match.group(1))
    match = re.search(r"DB45I_RESULT_B64_START\n(.*?)\nDB45I_RESULT_B64_END", log_tail, re.S)
    if match:
        raw = base64.b64decode("".join(match.group(1).split()))
        return json.loads(gzip.decompress(raw).decode("utf-8"))
    return {"db": "DB-45i", "error": {"type": "MissingRemoteJson", "message": "Could not parse DB45i JSON markers."}}


def _remote_python() -> str:
    return r'''
import base64
import contextlib
import gzip
import json
import math
import os
import pathlib
import sys
import time
import traceback

OUT = {
    "db": "DB-45i",
    "uuid": "02a00399-3857-444e-8db3-a8f58489c394",
    "anchor": 0,
    "scope": {
        "one_log": True,
        "one_anchor": True,
        "raw_ring_cameras": 7,
        "model_inference": True,
        "renderer": False,
        "erp_repair": False,
        "source_replacement": False,
        "generated_image": False,
        "diffusion_or_refiner": False,
        "red_promotion": False,
    },
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "secret_policy": "HF token read from environment only; not written to output.",
}

UUID = OUT["uuid"]
ANCHOR = OUT["anchor"]
MODEL_ID = "facebook/VGGT-1B-Commercial"
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
HF_HOME = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/cache/hf_vggt_db45d")
OFFICIAL_REPO = pathlib.Path("/content/vggt_db45d/vggt")
LOCAL_REPO = pathlib.Path("/content/waymo2panorama")
WORK = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db45i_vggt_calibrated_residual")
RAW_DIR = WORK / "raw_cameras"
H, W = 1024, 2048
ROIS = {
    "db25_longline": {"roi_xyxy": [850, 420, 1650, 720], "known_lidar_support_frac": 0.094},
    "db41_right_roi": {"roi_xyxy": [1440, 360, 2048, 720], "known_lidar_support_frac": 0.084},
    "db41_lower_right_roi": {"roi_xyxy": [1580, 560, 2048, 790], "known_lidar_support_frac": 0.0},
}


def stat(arr):
    import numpy as np
    arr = np.asarray(arr, dtype=np.float32)
    finite = np.isfinite(arr)
    if not bool(finite.any()):
        return {"valid": 0.0, "n": 0, "mean": None, "med": None, "p10": None, "p90": None, "std": None}
    vals = arr[finite]
    return {
        "valid": round(float(finite.mean()), 6),
        "n": int(vals.size),
        "mean": round(float(vals.mean()), 6),
        "med": round(float(np.percentile(vals, 50)), 6),
        "p10": round(float(np.percentile(vals, 10)), 6),
        "p90": round(float(np.percentile(vals, 90)), 6),
        "std": round(float(vals.std()), 6),
    }


def round_nested(x, nd=6):
    import numpy as np
    arr = np.asarray(x)
    return np.round(arr.astype(float), nd).tolist()


def views_field(tensor, n_views, channels=None):
    import numpy as np
    arr = tensor.detach().float().cpu().numpy()
    if arr.ndim >= 4 and arr.shape[0] == 1:
        arr = arr[0]
    if channels == 1:
        if arr.ndim == 4 and arr.shape[0] == n_views and arr.shape[-1] == 1:
            arr = arr[..., 0]
        elif arr.ndim == 4 and arr.shape[0] == n_views and arr.shape[1] == 1:
            arr = arr[:, 0]
        if arr.ndim != 3 or arr.shape[0] != n_views:
            return None, list(arr.shape)
        return np.asarray(arr, dtype=np.float32), list(arr.shape)
    if channels == 3:
        if arr.ndim == 4 and arr.shape[0] == n_views and arr.shape[-1] == 3:
            return np.asarray(arr, dtype=np.float32), list(arr.shape)
        if arr.ndim == 4 and arr.shape[0] == n_views and arr.shape[1] == 3:
            return np.asarray(arr.transpose(0, 2, 3, 1), dtype=np.float32), list(arr.shape)
        return None, list(arr.shape)
    return None, list(arr.shape)


def render_uv(K, T_ego_cam, image_hw, erp_hw=(1024, 2048)):
    import numpy as np
    h_erp, w_erp = erp_hw
    h_img, w_img = image_hw
    uu, vv = np.meshgrid(np.arange(w_erp, dtype=np.float64), np.arange(h_erp, dtype=np.float64))
    theta = np.pi - (uu + 0.5) / w_erp * (2.0 * np.pi)
    phi = (np.pi / 2.0) - (vv + 0.5) / h_erp * np.pi
    cos_phi = np.cos(phi)
    d_ego = np.stack([cos_phi * np.cos(theta), cos_phi * np.sin(theta), np.sin(phi)], axis=-1)
    R_cam_ego = T_ego_cam[:3, :3].T
    d_cam = d_ego @ R_cam_ego.T
    z_cam = d_cam[..., 2]
    in_front = z_cam > 1e-6
    z_safe = np.where(in_front, z_cam, 1.0)
    u_img = K[0, 0] * (d_cam[..., 0] / z_safe) + K[0, 2]
    v_img = K[1, 1] * (d_cam[..., 1] / z_safe) + K[1, 2]
    valid = in_front & (u_img >= 0.5) & (u_img <= w_img - 1.5) & (v_img >= 0.5) & (v_img <= h_img - 1.5)
    return u_img.astype("float32"), v_img.astype("float32"), valid


def preprocess_params(width, height, final_h, final_w):
    target = 518
    new_width = target
    new_height = round(height * (new_width / width) / 14) * 14
    crop_y = (new_height - target) // 2 if new_height > target else 0
    out_h = target if new_height > target else new_height
    out_w = target
    return {
        "mode": "crop",
        "target_size": target,
        "raw_width": int(width),
        "raw_height": int(height),
        "new_width": int(new_width),
        "new_height": int(new_height),
        "crop_y": int(crop_y),
        "post_crop_height": int(out_h),
        "post_crop_width": int(out_w),
        "final_height": int(final_h),
        "final_width": int(final_w),
        "pad_top": int((final_h - out_h) // 2),
        "pad_left": int((final_w - out_w) // 2),
    }


def raw_to_model_xy(u_raw, v_raw, params):
    import numpy as np
    sx = params["new_width"] / params["raw_width"]
    sy = params["new_height"] / params["raw_height"]
    x = u_raw * sx + params["pad_left"]
    y = v_raw * sy - params["crop_y"] + params["pad_top"]
    valid = (
        np.isfinite(u_raw) & np.isfinite(v_raw)
        & (u_raw >= 0) & (v_raw >= 0)
        & (u_raw <= params["raw_width"] - 1) & (v_raw <= params["raw_height"] - 1)
        & (x >= 0) & (x <= params["final_width"] - 1)
        & (y >= 0) & (y <= params["final_height"] - 1)
    )
    return x.astype("float32"), y.astype("float32"), valid


def sample_vec3(field_by_view, cam, x_model, y_model, valid):
    import cv2
    import numpy as np
    sampled = cv2.remap(
        field_by_view[cam].astype("float32"),
        x_model.astype("float32"),
        y_model.astype("float32"),
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(np.nan, np.nan, np.nan),
    )
    return np.where(valid[..., None], sampled, np.nan).astype("float32")


def erp_uv_from_points(points_ego, erp_hw=(1024, 2048)):
    import numpy as np
    h_erp, w_erp = erp_hw
    pts = np.asarray(points_ego, dtype=np.float64)
    x, y, z = pts[..., 0], pts[..., 1], pts[..., 2]
    r = np.linalg.norm(pts, axis=-1)
    valid = np.isfinite(r) & (r > 1e-6)
    r_safe = np.where(valid, r, 1.0)
    phi = np.arcsin(np.clip(np.where(valid, z / r_safe, 0.0), -1.0, 1.0))
    theta = np.arctan2(y, x)
    u = np.mod((np.pi - theta) * w_erp / (2.0 * np.pi) - 0.5, w_erp)
    v = (np.pi / 2.0 - phi) * h_erp / np.pi - 0.5
    valid &= (v >= 0.0) & (v < h_erp)
    return u.astype("float32"), v.astype("float32"), valid


def umeyama(src, dst):
    import numpy as np
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = src.shape[0]
    mu_src = src.mean(axis=0)
    mu_dst = dst.mean(axis=0)
    src_c = src - mu_src
    dst_c = dst - mu_dst
    var_src = (src_c ** 2).sum() / n
    Hm = (dst_c.T @ src_c) / n
    U, sigma, Vt = np.linalg.svd(Hm)
    reflection_detected = bool(np.linalg.det(U) * np.linalg.det(Vt) < 0)
    D = np.eye(3)
    if reflection_detected:
        D[2, 2] = -1.0
    R = U @ D @ Vt
    scale = float((sigma * np.diag(D)).sum() / var_src)
    t = mu_dst - scale * R @ mu_src
    aligned = scale * src @ R.T + t
    residual = np.linalg.norm(aligned - dst, axis=1)
    return {
        "scale": scale,
        "R": R,
        "t": t,
        "aligned": aligned,
        "reflection_detected": reflection_detected,
        "det_R": float(np.linalg.det(R)),
        "residual": residual,
    }


def project_ego_to_cam(points_ego, K, T_ego_cam):
    import numpy as np
    pts = np.asarray(points_ego, dtype=np.float64)
    R_cam_ego = T_ego_cam[:3, :3].T
    t_cam_ego = -R_cam_ego @ T_ego_cam[:3, 3]
    pts_cam = pts @ R_cam_ego.T + t_cam_ego[None, :]
    z = pts_cam[:, 2]
    valid = np.isfinite(z) & (z > 1e-6)
    z_safe = np.where(valid, z, 1.0)
    u = K[0, 0] * (pts_cam[:, 0] / z_safe) + K[0, 2]
    v = K[1, 1] * (pts_cam[:, 1] / z_safe) + K[1, 2]
    return u.astype("float32"), v.astype("float32"), valid


def build_lidar_grid(lidar_pts, erp_hw=(1024, 2048)):
    import numpy as np
    u, v, valid = erp_uv_from_points(lidar_pts, erp_hw)
    grid = {}
    for idx in np.where(valid)[0]:
        ui, vi = int(round(float(u[idx]))), int(round(float(v[idx])))
        if 0 <= vi < erp_hw[0]:
            grid.setdefault((ui % erp_hw[1], vi), []).append(idx)
    return u, v, valid, grid


try:
    os.environ["HF_HOME"] = str(HF_HOME)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    WORK.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for p in [OFFICIAL_REPO, LOCAL_REPO / "code", LOCAL_REPO / "scripts" / "phase3", LOCAL_REPO]:
        sys.path.insert(0, str(p))

    import cv2
    import numpy as np
    import torch
    from PIL import Image
    from vggt.models.vggt import VGGT
    from vggt.utils.load_fn import load_and_preprocess_images
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri
    import run_a1_streetview_pipeline as a1

    loader = a1.AV2RingLoader(DATA_ROOT / UUID)
    timestamps = loader.anchor_timestamps_ns()
    anchor_ts = timestamps[ANCHOR]
    frame = loader.load_synced_frame(anchor_ts)
    lidar_pts, _labels, _dms = a1.load_lidar_feather(DATA_ROOT / UUID, anchor_ts, max_delta_ms=75.0)
    lidar_pts = np.asarray(lidar_pts)[:, :3].astype(np.float64)
    ground, facades = a1.fit_planes_p3(lidar_pts)
    obj_mask = a1.off_plane_object_erp(lidar_pts, ground, facades, (H, W))

    image_paths, raw_shapes, uv_maps, weights = [], [], [], []
    av2_centers, av2_T = {}, {}
    cams = list(a1.RING_CAMS_7)
    for idx, cam in enumerate(cams):
        img = np.asarray(frame.images[cam])
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        raw_shapes.append([int(img.shape[0]), int(img.shape[1])])
        path = RAW_DIR / f"cam_{idx}_{cam}.jpg"
        Image.fromarray(img).save(path, quality=92)
        image_paths.append(str(path))
        cb = frame.calibrations[cam]
        av2_centers[cam] = cb.T_ego_cam[:3, 3].astype(float)
        av2_T[cam] = cb.T_ego_cam
        _slab, _alpha, weight = a1.render_camera_to_erp(img, cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        weights.append(weight.astype(np.float32))
        uv_maps.append(render_uv(cb.K, cb.T_ego_cam, img.shape[:2], erp_hw=(H, W)))

    w_base, _nr = a1.object_coherent_weights(weights, obj_mask)
    label_map = np.stack([w.astype(np.float32) for w in w_base], 0).argmax(0)
    coverage_valid = np.stack([w.astype(np.float32) for w in weights], 0).max(0) > 0

    device = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.time()
    model = VGGT.from_pretrained(MODEL_ID).to(device).eval()
    images = load_and_preprocess_images(image_paths).to(device)
    autocast_ctx = torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16) if device == "cuda" else contextlib.nullcontext()
    with torch.no_grad(), autocast_ctx:
        predictions = model(images)
    if torch.cuda.is_available():
        torch.cuda.synchronize()

    n_views = len(image_paths)
    world_points, wp_shape = views_field(predictions["world_points"], n_views, channels=3)
    pose_enc_t = predictions.get("pose_enc", None)
    if pose_enc_t is None:
        raise RuntimeError("VGGT predictions did not include pose_enc.")
    pose_tensor = pose_enc_t.detach().float()
    if pose_tensor.ndim == 2:
        pose_tensor = pose_tensor.unsqueeze(0)
    if pose_tensor.ndim != 3 or pose_tensor.shape[-1] != 9:
        raise RuntimeError(f"Unexpected pose_enc shape: {tuple(pose_tensor.shape)}")
    final_h, final_w = int(images.shape[-2]), int(images.shape[-1])
    decoded_extri, decoded_intri = pose_encoding_to_extri_intri(pose_tensor, image_size_hw=(final_h, final_w))
    extri_np = decoded_extri.detach().float().cpu().numpy()[0]
    intri_np = decoded_intri.detach().float().cpu().numpy()[0]
    pose_np = pose_tensor.detach().float().cpu().numpy()[0]

    vggt_centers = {}
    for idx, cam in enumerate(cams):
        Rcw = extri_np[idx, :3, :3]
        tcw = extri_np[idx, :3, 3]
        vggt_centers[cam] = (-Rcw.T @ tcw).astype(np.float64)

    src = np.stack([vggt_centers[c] for c in cams], axis=0)
    dst = np.stack([av2_centers[c] for c in cams], axis=0)
    sim = umeyama(src, dst)
    per_cam_res = {c: round(float(r), 6) for c, r in zip(cams, sim["residual"])}
    sim3_pass = (
        (not sim["reflection_detected"])
        and math.isfinite(sim["scale"])
        and sim["scale"] > 0
        and float(np.mean(sim["residual"])) <= 0.50
        and float(np.max(sim["residual"])) <= 1.00
    )

    lidar_u, lidar_v, lidar_valid, lidar_grid = build_lidar_grid(lidar_pts, (H, W))
    preprocess = [preprocess_params(w_raw, h_raw, final_h, final_w) for h_raw, w_raw in raw_shapes]

    roi_results = {}
    max_samples = 5000
    sample_radius_px = 3
    for roi_key, meta in ROIS.items():
        x0, y0, x1, y1 = meta["roi_xyxy"]
        roi_h, roi_w = y1 - y0, x1 - x0
        roi_cov = coverage_valid[y0:y1, x0:x1]
        owners = label_map[y0:y1, x0:x1]
        xs_full, ys_full = np.meshgrid(np.arange(x0, x1, dtype=np.float32), np.arange(y0, y1, dtype=np.float32))

        owner_points = np.full((roi_h, roi_w, 3), np.nan, np.float32)
        owner_u_raw = np.full((roi_h, roi_w), np.nan, np.float32)
        owner_v_raw = np.full((roi_h, roi_w), np.nan, np.float32)
        owner_valid = np.zeros((roi_h, roi_w), bool)
        owner_cam = np.full((roi_h, roi_w), -1, np.int32)

        for cam_idx, cam in enumerate(cams):
            mask = (owners == cam_idx) & roi_cov
            if not bool(mask.any()):
                continue
            u_map, v_map, uv_valid_full = uv_maps[cam_idx]
            u_raw = u_map[y0:y1, x0:x1]
            v_raw = v_map[y0:y1, x0:x1]
            uv_valid = uv_valid_full[y0:y1, x0:x1] & mask
            x_model, y_model, pre_valid = raw_to_model_xy(u_raw, v_raw, preprocess[cam_idx])
            valid = uv_valid & pre_valid
            wp_s = sample_vec3(world_points, cam_idx, x_model, y_model, valid)
            owner_points[mask] = wp_s[mask]
            owner_u_raw[mask] = u_raw[mask]
            owner_v_raw[mask] = v_raw[mask]
            owner_valid |= valid
            owner_cam[mask] = cam_idx

        valid_idx = np.argwhere(owner_valid & np.isfinite(owner_points).all(axis=2))
        stride = max(1, int(math.ceil(len(valid_idx) / max_samples))) if len(valid_idx) else 1
        valid_idx = valid_idx[::stride]
        if len(valid_idx):
            pts_vggt = owner_points[valid_idx[:, 0], valid_idx[:, 1]].astype(np.float64)
            pts_ego = sim["scale"] * pts_vggt @ sim["R"].T + sim["t"]
            xs = xs_full[valid_idx[:, 0], valid_idx[:, 1]]
            ys = ys_full[valid_idx[:, 0], valid_idx[:, 1]]
            u_erp, v_erp, erp_valid = erp_uv_from_points(pts_ego, (H, W))
            erp_err = np.sqrt((u_erp - xs) ** 2 + (v_erp - ys) ** 2)
            erp_err = np.where(erp_valid, erp_err, np.nan)

            raw_errs = []
            lidar_dists = []
            for k in range(len(valid_idx)):
                cam_idx = int(owner_cam[valid_idx[k, 0], valid_idx[k, 1]])
                if cam_idx < 0:
                    raw_errs.append(np.nan)
                    lidar_dists.append(np.nan)
                    continue
                cam = cams[cam_idx]
                cb = frame.calibrations[cam]
                ur, vr, rv = project_ego_to_cam(pts_ego[k:k+1], cb.K, cb.T_ego_cam)
                if bool(rv[0]):
                    du = float(ur[0] - owner_u_raw[valid_idx[k, 0], valid_idx[k, 1]])
                    dv = float(vr[0] - owner_v_raw[valid_idx[k, 0], valid_idx[k, 1]])
                    raw_errs.append(math.sqrt(du * du + dv * dv))
                else:
                    raw_errs.append(np.nan)

                if bool(erp_valid[k]):
                    ui, vi = int(round(float(u_erp[k]))) % W, int(round(float(v_erp[k])))
                    candidates = []
                    for dy in range(-sample_radius_px, sample_radius_px + 1):
                        for dx in range(-sample_radius_px, sample_radius_px + 1):
                            candidates.extend(lidar_grid.get(((ui + dx) % W, vi + dy), []))
                    if candidates:
                        cand = lidar_pts[np.asarray(candidates, dtype=np.int64)]
                        d = np.linalg.norm(cand - pts_ego[k][None, :], axis=1)
                        lidar_dists.append(float(d.min()))
                    else:
                        lidar_dists.append(np.nan)
                else:
                    lidar_dists.append(np.nan)
            raw_errs = np.asarray(raw_errs, dtype=np.float32)
            lidar_dists = np.asarray(lidar_dists, dtype=np.float32)
        else:
            erp_err = np.asarray([], dtype=np.float32)
            raw_errs = np.asarray([], dtype=np.float32)
            lidar_dists = np.asarray([], dtype=np.float32)

        finite_lidar = np.isfinite(lidar_dists)
        roi_results[roi_key] = {
            "roi_xyxy": meta["roi_xyxy"],
            "known_lidar_support_frac": meta["known_lidar_support_frac"],
            "coverage_valid_frac": round(float(roi_cov.mean()), 6),
            "owner_vggt_valid_frac_of_roi": round(float(owner_valid.sum() / max(1, roi_h * roi_w)), 6),
            "sampled_point_count": int(len(valid_idx)),
            "sim3_applied": True,
            "erp_reprojection_error_px": stat(erp_err),
            "owner_raw_reprojection_error_px": stat(raw_errs),
            "nearest_lidar_radius_px": sample_radius_px,
            "nearest_lidar_match_frac_of_samples": round(float(finite_lidar.mean()), 6) if len(lidar_dists) else 0.0,
            "nearest_lidar_3d_residual_m": stat(lidar_dists),
            "admissibility": {
                "target_surface_lidar_gate_pass": bool(
                    sim3_pass
                    and len(lidar_dists)
                    and float(finite_lidar.mean()) >= 0.20
                    and np.isfinite(lidar_dists).any()
                    and float(np.nanpercentile(lidar_dists, 50)) <= 0.75
                ),
                "permission_promotion_allowed": False,
                "reason": "candidate residual diagnostic only; DB45b permission update must be decided locally and DB41 lower-right remains zero-LiDAR abstain",
            },
        }

    OUT["vggt"] = {
        "inference_ok": True,
        "model_id": MODEL_ID,
        "duration_s": round(time.time() - t0, 2),
        "input_tensor_shape": list(images.shape),
        "prediction_keys": sorted([str(k) for k in predictions.keys()]),
        "world_points_shape": wp_shape,
        "pose_enc_shape": list(pose_np.shape),
        "decoded_extrinsics_shape": list(extri_np.shape),
        "decoded_intrinsics_shape": list(intri_np.shape),
        "cuda_free_gb_after": round(torch.cuda.mem_get_info()[0] / 1024**3, 2) if torch.cuda.is_available() else None,
    }
    OUT["saved_outputs"] = {
        "pose_enc_values": round_nested(pose_np),
        "decoded_extrinsics": round_nested(extri_np),
        "decoded_intrinsics": round_nested(intri_np),
        "preprocess_mapping": preprocess,
        "vggt_camera_centers": {c: round_nested(vggt_centers[c]) for c in cams},
        "waymo_rig_camera_centers": {c: round_nested(av2_centers[c]) for c in cams},
    }
    OUT["sim3_alignment"] = {
        "available": True,
        "pass_contract_initial_thresholds": bool(sim3_pass),
        "scale": round(float(sim["scale"]), 6),
        "reflection_detected": bool(sim["reflection_detected"]),
        "det_R": round(float(sim["det_R"]), 6),
        "mean_residual_m": round(float(np.mean(sim["residual"])), 6),
        "max_residual_m": round(float(np.max(sim["residual"])), 6),
        "per_camera_residual_m": per_cam_res,
        "thresholds": {"max_center_rms_m": 0.50, "max_center_residual_m": 1.00, "reflection_allowed": False},
    }
    OUT["target_surface_residuals"] = roi_results
    OUT["db45b_permission_boundary"] = {
        "accepted_db45_geometry_evidence": False,
        "permission_state_changes": "none",
        "red_promotions": [],
        "claim": "DB45i remote result is residual evidence candidate only until local DB45b/DB45h hard checks pass.",
    }
    OUT["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

except Exception as exc:
    OUT["error"] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "trace_tail": traceback.format_exc()[-3000:],
    }
    OUT["db45b_permission_boundary"] = {
        "accepted_db45_geometry_evidence": False,
        "permission_state_changes": "none",
        "red_promotions": [],
    }

WORK.mkdir(parents=True, exist_ok=True)
out_path = WORK / "db45i_vggt_calibrated_residual_remote_result.json"
out_path.write_text(json.dumps(OUT, indent=2), encoding="utf-8")
payload = json.dumps(OUT, separators=(",", ":")).encode("utf-8")
packed = base64.b64encode(gzip.compress(payload)).decode("ascii")
print("DB45I_RESULT_B64_START")
print(packed)
print("DB45I_RESULT_B64_END")
'''


def run_remote(timeout_s: int) -> dict[str, Any]:
    url = os.environ["COLAB_URL"].rstrip("/")
    colab_token = os.environ["COLAB_TOKEN"]
    hf_token = os.environ["HF_TOKEN"]
    remote_code_b64 = base64.b64encode(_remote_python().encode("utf-8")).decode("ascii")
    bash = (
        "set +x\n"
        f"export HF_TOKEN='{hf_token}'\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{remote_code_b64}').decode('utf-8')\n"
        "exec(code, {'__name__': '__main__'})\n"
        "PY"
    )
    try:
        status = _get_json(url, colab_token, "/status", timeout=30)
        job = _post_json(url, colab_token, "/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content", "timeout_s": timeout_s})
        job_id = job["job_id"]
        started = time.time()
        while True:
            time.sleep(8)
            state = _get_json(url, colab_token, f"/jobs/{job_id}")
            if state.get("state") != "running":
                result = _extract_remote_json(state.get("log_tail", ""))
                result["colab_job"] = {
                    "job_id": job_id,
                    "state": state.get("state"),
                    "exit_code": state.get("exit_code"),
                    "duration_s": state.get("duration_s"),
                    "status_runtime_type": status.get("runtime_type"),
                    "status_gpu_name": status.get("gpu_name"),
                }
                result = sanitize(result)
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
                return result
            if time.time() - started > timeout_s + 90:
                result = {
                    "db": "DB-45i",
                    "error": {"type": "LocalPollTimeout", "message": f"Timed out waiting for job {job_id}."},
                    "colab_job": {"job_id": job_id, "state": state.get("state")},
                }
                REMOTE_RESULT.write_text(json.dumps(sanitize(result), indent=2), encoding="utf-8")
                return result
    except Exception as exc:
        result = {
            "db": "DB-45i",
            "error": {"type": type(exc).__name__, "message": str(exc), "stage": "status_or_submit_exec"},
            "scope": {
                "model_inference": False,
                "renderer": False,
                "erp_repair": False,
                "source_replacement": False,
                "generated_image": False,
                "red_promotion": False,
            },
            "db45b_permission_boundary": {
                "accepted_db45_geometry_evidence": False,
                "permission_state_changes": "none",
                "red_promotions": [],
                "claim": "Connectivity failed before model action; no permission state changed.",
            },
        }
        result = sanitize(result)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result


def build_checks(remote: dict[str, Any], secret_hits: list[dict[str, str]]) -> list[dict[str, Any]]:
    def chk(check_id: str, passed: bool, severity: str, evidence: str) -> dict[str, Any]:
        return {"id": check_id, "pass": bool(passed), "severity": severity, "evidence": evidence}

    decision = remote.get("db45b_permission_boundary", {"permission_state_changes": "none", "red_promotions": []})
    sim3 = remote.get("sim3_alignment", {})
    vggt = remote.get("vggt", {})
    saved = remote.get("saved_outputs", {})
    rois = remote.get("target_surface_residuals", {})
    lr = rois.get("db41_lower_right_roi", {})
    scopes = remote.get("scope", {})

    return [
        chk("db45b_precondition", DB45B.exists(), "precondition", "DB45b permission calibration manifest is present."),
        chk("db45h_contract_precondition", DB45H.exists(), "precondition", "DB45h residual contract manifest is present."),
        chk(
            "remote_job_completed",
            remote.get("colab_job", {}).get("exit_code") == 0 and remote.get("error") is None,
            "blocker",
            f"job={remote.get('colab_job', {}).get('job_id')} exit={remote.get('colab_job', {}).get('exit_code')} error={remote.get('error')}",
        ),
        chk(
            "one_log_anchor_scope",
            remote.get("uuid") == BMW_UUID and remote.get("anchor") == ANCHOR and scopes.get("one_anchor") is True,
            "scope",
            "Remote result is constrained to BMW log anchor 0.",
        ),
        chk("official_vggt_inference", vggt.get("inference_ok") is True, "blocker", "Official VGGT inference must run exactly once under DB45i."),
        chk("pose_enc_saved", bool(saved.get("pose_enc_values")) and vggt.get("pose_enc_shape") == [7, 9], "blocker", f"pose_enc_shape={vggt.get('pose_enc_shape')}"),
        chk("decoded_cameras_saved", bool(saved.get("decoded_extrinsics")) and bool(saved.get("decoded_intrinsics")), "blocker", "Decoded camera matrices are required."),
        chk("preprocess_mapping_saved", len(saved.get("preprocess_mapping", [])) == 7, "blocker", "Preprocess mapping must be recorded for all 7 views."),
        chk(
            "sim3_alignment_recorded",
            sim3.get("available") is True and sim3.get("scale") is not None and sim3.get("per_camera_residual_m") is not None,
            "blocker",
            f"scale={sim3.get('scale')} mean={sim3.get('mean_residual_m')} max={sim3.get('max_residual_m')}",
        ),
        chk(
            "sim3_contract_thresholds_pass",
            sim3.get("pass_contract_initial_thresholds") is True,
            "blocker",
            f"reflection={sim3.get('reflection_detected')} mean={sim3.get('mean_residual_m')} max={sim3.get('max_residual_m')}",
        ),
        chk("target_surface_residuals_present", set(rois.keys()) >= set(ROIS.keys()), "blocker", "All frozen DB25/DB41 ROIs need residual summaries."),
        chk(
            "db41_lower_right_zero_lidar_preserved",
            (
                not rois
                and decision.get("permission_state_changes") == "none"
                and decision.get("red_promotions") == []
            )
            or (
                lr.get("known_lidar_support_frac") == 0.0
                and lr.get("admissibility", {}).get("permission_promotion_allowed") is False
            ),
            "blocker",
            "DB41 lower-right remains zero-LiDAR abstain; if remote is blocked, no residual or promotion was produced.",
        ),
        chk(
            "no_red_promotion",
            decision.get("permission_state_changes") == "none" and decision.get("red_promotions") == [],
            "blocker",
            "DB45i does not promote RED controls.",
        ),
        chk(
            "no_repair_or_generation",
            scopes.get("renderer") is False
            and scopes.get("erp_repair") is False
            and scopes.get("source_replacement") is False
            and scopes.get("generated_image") is False
            and scopes.get("red_promotion") is False,
            "blocker",
            "DB45i scope forbids render/repair/source replacement/generation.",
        ),
        chk("no_token_in_artifacts", not secret_hits, "blocker", f"secret_scan_hits={secret_hits}"),
    ]


def build_manifest() -> dict[str, Any]:
    remote = read_json(REMOTE_RESULT) if REMOTE_RESULT.exists() else {
        "db": "DB-45i",
        "error": {"type": "MissingRemoteResult", "message": "Run with --run-remote when executor is reachable."},
        "scope": {
            "model_inference": False,
            "renderer": False,
            "erp_repair": False,
            "source_replacement": False,
            "generated_image": False,
            "red_promotion": False,
        },
        "db45b_permission_boundary": {"permission_state_changes": "none", "red_promotions": []},
    }
    remote = sanitize(remote)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp = {
        "db": "DB-45i",
        "status": "vggt_calibrated_residual_extractor_gate",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Run or prepare one bounded VGGT calibrated residual extractor without repair or RED promotion.",
        "scope": {
            "one_a100_job_if_reachable": True,
            "uuid": BMW_UUID,
            "anchor": ANCHOR,
            "raw_ring_cameras": 7,
            "roi_count": len(ROIS),
            "renderer": False,
            "erp_repair": False,
            "source_replacement": False,
            "generated_image": False,
            "diffusion_or_refiner": False,
            "permission_promotion_allowed_without_db45b_target_surface_support": False,
        },
        "decision": {
            "accepted_evidence_type": "pending",
            "accepted_db45_diagnostic_evidence": False,
            "accepted_db45_geometry_evidence": False,
            "runtime_ready": remote.get("error") is None and remote.get("colab_job", {}).get("exit_code") == 0,
            "model_inference_ran": remote.get("vggt", {}).get("inference_ok") is True,
            "permission_state_changes": "none",
            "red_promotions": [],
            "db45_status": "running",
            "claim_boundary": "DB45i can accept decoded residual diagnostics only; geometry evidence and permission changes require DB45b/DB45h target-surface gates.",
        },
        "refs": {
            "db45b_manifest": rel(DB45B),
            "db45f_manifest": rel(DB45F),
            "db45g_manifest": rel(DB45G),
            "db45h_manifest": rel(DB45H),
            "remote_result_json": rel(REMOTE_RESULT),
            "board": rel(BOARD),
        },
        "remote_result": remote,
        "roi_policy": ROIS,
    }
    MANIFEST.write_text(json.dumps(temp, indent=2), encoding="utf-8")
    secret_hits = scan_secret_hits([REMOTE_RESULT, MANIFEST])
    checks = build_checks(remote, secret_hits)
    blocker_failures = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    sim3_pass = next((c["pass"] for c in checks if c["id"] == "sim3_contract_thresholds_pass"), False)
    target_present = next((c["pass"] for c in checks if c["id"] == "target_surface_residuals_present"), False)
    diagnostic_accepted = remote.get("vggt", {}).get("inference_ok") is True and not secret_hits
    geometry_accepted = diagnostic_accepted and sim3_pass and target_present and not blocker_failures

    temp["checks"] = checks
    temp["secret_scan_hits"] = secret_hits
    temp["decision"]["accepted_db45_diagnostic_evidence"] = diagnostic_accepted
    temp["decision"]["accepted_db45_geometry_evidence"] = False
    if diagnostic_accepted:
        temp["decision"]["accepted_evidence_type"] = "vggt-calibrated-residual-diagnostic-only"
    else:
        temp["decision"]["accepted_evidence_type"] = "blocked-or-paused"
    if geometry_accepted:
        temp["decision"]["geometry_candidate_note"] = "All extractor hard checks passed, but DB45i still records no permission promotion; a separate permission update would be required."
    temp["decision"]["permission_state_changes"] = "none"
    temp["decision"]["red_promotions"] = []
    MANIFEST.write_text(json.dumps(temp, indent=2), encoding="utf-8")
    build_board(temp)
    return temp


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1800, 1120), (18, 20, 24))
    draw = ImageDraw.Draw(board)
    decision = manifest["decision"]
    remote = manifest["remote_result"]
    sim3 = remote.get("sim3_alignment", {})

    draw.text((28, 24), "DB45i VGGT Calibrated Residual Extractor", fill=(255, 255, 255), font=font(28))
    pill(draw, (28, 66, 310, 102), decision["accepted_evidence_type"], (58, 94, 150) if decision["accepted_db45_diagnostic_evidence"] else (150, 92, 48))
    pill(draw, (330, 66, 520, 102), f"geometry={decision['accepted_db45_geometry_evidence']}", (150, 70, 70))
    pill(draw, (540, 66, 720, 102), f"inference={decision['model_inference_ran']}", (80, 120, 84) if decision["model_inference_ran"] else (150, 92, 48))
    pill(draw, (740, 66, 920, 102), "RED promotions=0", (65, 120, 88))

    y = 135
    draw.text((28, y), "Runtime / decode / Sim(3)", fill=(255, 255, 255), font=font(21))
    y += 32
    facts = [
        f"remote_error={remote.get('error')}",
        f"job={remote.get('colab_job', {}).get('job_id')} exit={remote.get('colab_job', {}).get('exit_code')}",
        f"pose_shape={remote.get('vggt', {}).get('pose_enc_shape')} extri_shape={remote.get('vggt', {}).get('decoded_extrinsics_shape')}",
        f"sim3_pass={sim3.get('pass_contract_initial_thresholds')} scale={fmt(sim3.get('scale'))} mean={fmt(sim3.get('mean_residual_m'))} max={fmt(sim3.get('max_residual_m'))}",
        "claim boundary: residual diagnostics only; no ERP render/repair/source replacement/generated pixels",
    ]
    for line in facts:
        y = draw_wrapped(draw, 32, y, "- " + line, 122, (235, 235, 235), 14, 5)

    y += 18
    draw.text((28, y), "ROI residual table", fill=(255, 255, 255), font=font(21))
    y += 32
    headers = ["roi", "known LiDAR", "samples", "raw med/p90 px", "LiDAR match", "permission"]
    xs = [32, 300, 460, 620, 780, 900]
    for x, h in zip(xs, headers):
        draw.text((x, y), h, fill=(180, 205, 240), font=font(13))
    y += 24
    rois = remote.get("target_surface_residuals", {})
    for roi_key in ROIS:
        row = rois.get(roi_key, {})
        raw = row.get("owner_raw_reprojection_error_px", {})
        lidar = row.get("nearest_lidar_3d_residual_m", {})
        adm = row.get("admissibility", {})
        values = [
            roi_key,
            fmt(row.get("known_lidar_support_frac")),
            str(row.get("sampled_point_count", 0)),
            f"{fmt(raw.get('med'))}/{fmt(raw.get('p90'))}",
            fmt(row.get("nearest_lidar_match_frac_of_samples")),
            "no-promotion"
            if adm.get("permission_promotion_allowed") is False
            else ("n/a" if not adm else str(adm.get("permission_promotion_allowed"))),
        ]
        color = (255, 225, 180) if "lower" in roi_key else (235, 235, 235)
        for x, value in zip(xs, values):
            draw.text((x, y), value, fill=color, font=font(13))
        y += 28
    if not rois:
        y = draw_wrapped(draw, 32, y, "No residual table yet. Executor/tunnel is blocked or the remote run has not completed.", 120, (255, 225, 180), 14)

    x2, y2 = 1030, 135
    draw.text((x2, y2), "Hard checks", fill=(255, 255, 255), font=font(21))
    y2 += 32
    for check in manifest["checks"]:
        row_y = y2
        fill = (72, 150, 92) if check["pass"] else ((180, 70, 70) if check["severity"] == "blocker" else (160, 118, 55))
        pill(draw, (x2, row_y, x2 + 86, row_y + 25), "PASS" if check["pass"] else "STOP", fill)
        text_y = draw_wrapped(draw, x2 + 100, row_y + 2, f"{check['id']}: {check['evidence']}", 78, (235, 235, 235), 12, 4)
        y2 = max(text_y + 7, row_y + 34)
        if y2 > 1010:
            break

    y3 = 760
    draw.text((28, y3), "Decision boundary", fill=(255, 255, 255), font=font(21))
    y3 += 32
    for line in [
        "DB45i is not a repair brief and produces no panorama output.",
        "Decoded VGGT pose plus Sim(3) is necessary but not sufficient for source-faithful geometry evidence.",
        "DB41 lower-right remains zero-LiDAR abstain; DB36/DB40 generated fake geometry remains rejected.",
        "If executor DNS/status is unreachable, DB45i pauses as blocked-or-paused and must not continue patch-on-patch.",
    ]:
        y3 = draw_wrapped(draw, 32, y3, "- " + line, 128, (255, 235, 180), 14, 5)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true", help="Run the one bounded Colab VGGT calibrated residual extractor job first.")
    parser.add_argument("--timeout-s", type=int, default=1200)
    args = parser.parse_args()

    if args.run_remote:
        run_remote(args.timeout_s)
    manifest = build_manifest()
    print(f"wrote {MANIFEST}")
    print(f"wrote {BOARD}")
    print(json.dumps(manifest["decision"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
