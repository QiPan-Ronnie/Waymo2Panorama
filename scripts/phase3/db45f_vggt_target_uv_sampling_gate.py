#!/usr/bin/env python
"""DB45f VGGT target-ROI owner-UV sampling gate.

DB45f upgrades DB45e's owner-camera confidence summary into pixel-targeted
diagnostics by sampling official VGGT outputs at the actual raw-camera pixels
used by the ERP seam ROIs. It is still evidence-only: no renderer, no repaired
ERP, no source replacement, and no RED promotion.
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

DB25 = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "db25_longline_summary.json"
DB41 = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_manifest.json"
DB45B = OUT_DIR / "db45b_evidence_permission_calibration_manifest.json"
DB45E = OUT_DIR / "db45e_vggt_roi_probe_gate_manifest.json"

REMOTE_RESULT = OUT_DIR / "db45f_vggt_remote_target_uv_sampling_result.json"
MANIFEST = OUT_DIR / "db45f_vggt_target_uv_sampling_gate_manifest.json"
BOARD = OUT_DIR / "db45f_vggt_target_uv_sampling_gate_board.jpg"

DB25_MONTAGE = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "db25_longline_evidence_montage.jpg"
DB41_RIGHT_MONTAGE = (
    ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "right_roi" / "db25_longline_evidence_montage.jpg"
)
DB41_LOWER_MONTAGE = (
    ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "lower_right_roi"
    / "db25_longline_evidence_montage.jpg"
)

BMW_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
ANCHOR = 0

ROIS = {
    "db25_longline": {
        "segment_id": "db45_db25_longline_abstain",
        "label": "DB25 long-line low-support ROI",
        "roi_xyxy": [850, 420, 1650, 720],
    },
    "db41_right_roi": {
        "segment_id": "db45_db41_right_roi_abstain",
        "label": "DB41 right-white-line ROI",
        "roi_xyxy": [1440, 360, 2048, 720],
    },
    "db41_lower_right_roi": {
        "segment_id": "db45_db41_lower_right_abstain",
        "label": "DB41 lower-right zero-LiDAR ROI",
        "roi_xyxy": [1580, 560, 2048, 790],
    },
}

SECRET_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),
]
SECRET_BYTE_PATTERNS = [
    re.compile(rb"hf_[A-Za-z0-9]{20,}"),
    re.compile(rb"Bearer\s+[A-Za-z0-9._-]+"),
]


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


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
    line_gap: int = 6,
) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw.text((x, y), line, fill=color, font=font(size))
        y += size + line_gap
    return y


def pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], text: str, fill: tuple[int, int, int]) -> None:
    draw.rounded_rectangle(xy, radius=6, fill=fill)
    draw.text((xy[0] + 10, xy[1] + 7), text, fill=(255, 255, 255), font=font(14))


def fit_image(path: Path, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGB", size, (10, 10, 10))
    if not path.exists():
        ImageDraw.Draw(canvas).text((12, 12), f"missing: {path.name}", fill=(220, 120, 120), font=font(14))
        return canvas
    img = Image.open(path).convert("RGB")
    img.thumbnail(size, Image.Resampling.LANCZOS)
    canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    return canvas


def label_tile(path: Path, title: str, size: tuple[int, int]) -> Image.Image:
    tile = Image.new("RGB", (size[0], size[1] + 30), (0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.text((8, 8), title, fill=(255, 255, 255), font=font(14))
    tile.paste(fit_image(path, size), (0, 30))
    return tile


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


def _remote_python() -> str:
    return r'''
import contextlib
import json
import os
import pathlib
import sys
import time
import traceback

OUT = {
    "db": "DB-45f",
    "uuid": "02a00399-3857-444e-8db3-a8f58489c394",
    "anchor": 0,
    "scope": {
        "one_log": True,
        "one_anchor": True,
        "raw_ring_cameras": 7,
        "old_uniform_wrapper_used": False,
        "renderer": False,
        "erp_repair": False,
        "source_replacement": False,
        "generated_image": False
    },
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "secret_policy": "HF token read from environment only; not written to output."
}

UUID = OUT["uuid"]
ANCHOR = OUT["anchor"]
MODEL_ID = "facebook/VGGT-1B-Commercial"
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
HF_HOME = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/cache/hf_vggt_db45d")
OFFICIAL_REPO = pathlib.Path("/content/vggt_db45d/vggt")
LOCAL_REPO = pathlib.Path("/content/waymo2panorama")
WORK = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db45f_vggt_target_uv_sampling")
RAW_DIR = WORK / "raw_cameras"
H, W = 1024, 2048
ROIS = {
    "db25_longline": [850, 420, 1650, 720],
    "db41_right_roi": [1440, 360, 2048, 720],
    "db41_lower_right_roi": [1580, 560, 2048, 790],
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

def downsample_grid(arr, out_h=8, out_w=16):
    import numpy as np
    arr = np.asarray(arr, dtype=np.float32)
    h, w = arr.shape
    grid = []
    for yy in range(out_h):
        row = []
        y0 = int(round(yy * h / out_h)); y1 = int(round((yy + 1) * h / out_h))
        for xx in range(out_w):
            x0 = int(round(xx * w / out_w)); x1 = int(round((xx + 1) * w / out_w))
            block = arr[y0:max(y0 + 1, y1), x0:max(x0 + 1, x1)]
            finite = np.isfinite(block)
            row.append(None if not finite.any() else round(float(np.nanmean(block)), 5))
        grid.append(row)
    return grid

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
    u_idx = np.arange(w_erp, dtype=np.float64)
    v_idx = np.arange(h_erp, dtype=np.float64)
    uu, vv = np.meshgrid(u_idx, v_idx)
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
    valid = (
        in_front
        & (u_img >= 0.5)
        & (u_img <= w_img - 1.5)
        & (v_img >= 0.5)
        & (v_img <= h_img - 1.5)
    )
    return u_img.astype("float32"), v_img.astype("float32"), valid

def preprocess_params(width, height, final_h, final_w):
    target = 518
    new_width = target
    new_height = round(height * (new_width / width) / 14) * 14
    crop_y = (new_height - target) // 2 if new_height > target else 0
    out_h = target if new_height > target else new_height
    out_w = target
    pad_top = (final_h - out_h) // 2
    pad_left = (final_w - out_w) // 2
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
        "pad_top": int(pad_top),
        "pad_left": int(pad_left),
    }

def raw_to_model_xy(u_raw, v_raw, params):
    import numpy as np
    sx = params["new_width"] / params["raw_width"]
    sy = params["new_height"] / params["raw_height"]
    x = u_raw * sx + params["pad_left"]
    y = v_raw * sy - params["crop_y"] + params["pad_top"]
    valid = (
        np.isfinite(u_raw)
        & np.isfinite(v_raw)
        & (u_raw >= 0)
        & (v_raw >= 0)
        & (u_raw <= params["raw_width"] - 1)
        & (v_raw <= params["raw_height"] - 1)
        & (x >= 0)
        & (x <= params["final_width"] - 1)
        & (y >= 0)
        & (y <= params["final_height"] - 1)
    )
    return x.astype("float32"), y.astype("float32"), valid

def sample_scalar(field_by_view, cam, x_model, y_model, valid):
    import cv2
    import numpy as np
    sampled = cv2.remap(
        field_by_view[cam].astype("float32"),
        x_model.astype("float32"),
        y_model.astype("float32"),
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=np.nan,
    )
    return np.where(valid, sampled, np.nan).astype("float32")

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
    import run_a1_streetview_pipeline as a1

    loader = a1.AV2RingLoader(DATA_ROOT / UUID)
    timestamps = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(timestamps[ANCHOR])
    pts, _labels, _dms = a1.load_lidar_feather(DATA_ROOT / UUID, timestamps[ANCHOR], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = a1.fit_planes_p3(pts)
    obj_mask = a1.off_plane_object_erp(pts, ground, facades, (H, W))

    cams = {cam: frame.calibrations[cam] for cam in a1.RING_CAMS_7}
    slabs, weights, uv_maps, raw_shapes = [], [], [], []
    image_paths = []
    for idx, cam in enumerate(a1.RING_CAMS_7):
        img = np.asarray(frame.images[cam])
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        raw_shapes.append([int(img.shape[0]), int(img.shape[1])])
        path = RAW_DIR / f"cam_{idx}_{cam}.jpg"
        Image.fromarray(img).save(path, quality=92)
        image_paths.append(str(path))
        cb = cams[cam]
        slab, _alpha, weight = a1.render_camera_to_erp(img, cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        slabs.append(slab.astype(np.uint8))
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
    depth, depth_shape = views_field(predictions["depth"], n_views, channels=1)
    depth_conf, depth_conf_shape = views_field(predictions["depth_conf"], n_views, channels=1)
    world_points, wp_shape = views_field(predictions["world_points"], n_views, channels=3)
    world_points_conf, wpc_shape = views_field(predictions["world_points_conf"], n_views, channels=1)
    final_h, final_w = int(images.shape[-2]), int(images.shape[-1])
    preprocess = []
    for h_raw, w_raw in raw_shapes:
        preprocess.append(preprocess_params(w_raw, h_raw, final_h, final_w))

    roi_results = {}
    for roi_key, box in ROIS.items():
        x0, y0, x1, y1 = box
        roi_h, roi_w = y1 - y0, x1 - x0
        roi_cov = coverage_valid[y0:y1, x0:x1]
        owners = label_map[y0:y1, x0:x1]
        owner_counts = {str(int(k)): int((owners[roi_cov] == k).sum()) for k in np.unique(owners[roi_cov])}
        owner_depth = np.full((roi_h, roi_w), np.nan, np.float32)
        owner_depth_conf = np.full((roi_h, roi_w), np.nan, np.float32)
        owner_wpc = np.full((roi_h, roi_w), np.nan, np.float32)
        owner_wp = np.full((roi_h, roi_w, 3), np.nan, np.float32)
        owner_uv_valid = np.zeros((roi_h, roi_w), bool)
        owner_pre_valid = np.zeros((roi_h, roi_w), bool)

        per_cam = {}
        for cam_idx in range(n_views):
            mask = (owners == cam_idx) & roi_cov
            if not bool(mask.any()):
                continue
            u_map, v_map, uv_valid_full = uv_maps[cam_idx]
            u_raw = u_map[y0:y1, x0:x1]
            v_raw = v_map[y0:y1, x0:x1]
            uv_valid = uv_valid_full[y0:y1, x0:x1] & mask
            x_model, y_model, pre_valid = raw_to_model_xy(u_raw, v_raw, preprocess[cam_idx])
            valid = uv_valid & pre_valid
            d_s = sample_scalar(depth, cam_idx, x_model, y_model, valid)
            dc_s = sample_scalar(depth_conf, cam_idx, x_model, y_model, valid)
            wpc_s = sample_scalar(world_points_conf, cam_idx, x_model, y_model, valid)
            wp_s = sample_vec3(world_points, cam_idx, x_model, y_model, valid)
            owner_depth[mask] = d_s[mask]
            owner_depth_conf[mask] = dc_s[mask]
            owner_wpc[mask] = wpc_s[mask]
            owner_wp[mask] = wp_s[mask]
            owner_uv_valid |= uv_valid
            owner_pre_valid |= valid
            per_cam[str(cam_idx)] = {
                "owner_px": int(mask.sum()),
                "uv_valid_frac_of_owner": round(float((uv_valid & mask).sum() / max(1, mask.sum())), 6),
                "preprocess_valid_frac_of_owner": round(float((valid & mask).sum() / max(1, mask.sum())), 6),
                "depth_conf": stat(dc_s[mask]),
                "world_points_conf": stat(wpc_s[mask]),
            }

        pair_disagreements = {}
        for i, j in a1.RING_PAIRS:
            overlap = (weights[i][y0:y1, x0:x1] > 1e-6) & (weights[j][y0:y1, x0:x1] > 1e-6) & roi_cov
            if int(overlap.sum()) < 50:
                continue
            ui, vi, uvi = uv_maps[i]
            uj, vj, uvj = uv_maps[j]
            xi, yi, vali = raw_to_model_xy(ui[y0:y1, x0:x1], vi[y0:y1, x0:x1], preprocess[i])
            xj, yj, valj = raw_to_model_xy(uj[y0:y1, x0:x1], vj[y0:y1, x0:x1], preprocess[j])
            valid = overlap & uvi[y0:y1, x0:x1] & uvj[y0:y1, x0:x1] & vali & valj
            if int(valid.sum()) < 50:
                continue
            pi = sample_vec3(world_points, i, xi, yi, valid)
            pj = sample_vec3(world_points, j, xj, yj, valid)
            dist = np.linalg.norm(pi - pj, axis=2)
            pair_disagreements[f"{i}-{j}"] = {"overlap_px": int(overlap.sum()), "valid_px": int(valid.sum()), "world_points_l2": stat(dist[valid])}

        roi_results[roi_key] = {
            "roi_xyxy": box,
            "owner_counts": owner_counts,
            "coverage_valid_frac": round(float(roi_cov.mean()), 6),
            "owner_uv_valid_frac_of_roi": round(float(owner_uv_valid.sum() / max(1, roi_h * roi_w)), 6),
            "owner_preprocess_valid_frac_of_roi": round(float(owner_pre_valid.sum() / max(1, roi_h * roi_w)), 6),
            "target_sampled_stats": {
                "depth": stat(owner_depth),
                "depth_conf": stat(owner_depth_conf),
                "world_points_conf": stat(owner_wpc),
                "world_points_norm": stat(np.linalg.norm(owner_wp, axis=2)),
            },
            "per_owner_camera": per_cam,
            "model_internal_overlap_pair_disagreement": pair_disagreements,
            "heatmap_grids": {
                "depth_conf": downsample_grid(owner_depth_conf),
                "world_points_conf": downsample_grid(owner_wpc),
                "preprocess_valid": downsample_grid(np.where(owner_pre_valid, 1.0, np.nan).astype(np.float32)),
            },
            "admissibility": {
                "target_uv_mapping_available": True,
                "preprocessing_mapping_recorded": True,
                "still_model_diagnostic_only": True,
                "permission_promotion_allowed_by_vggt_alone": False,
            },
        }

    OUT["raw_camera_load"] = {
        "ok": True,
        "camera_names": list(a1.RING_CAMS_7),
        "raw_shapes_hw": raw_shapes,
    }
    OUT["official_preprocess"] = {
        "function": "vggt.utils.load_fn.load_and_preprocess_images",
        "mode": "crop",
        "input_tensor_shape": list(images.shape),
        "params_by_camera": {str(i): preprocess[i] for i in range(n_views)},
        "source": "/content/vggt_db45d/vggt/vggt/utils/load_fn.py",
    }
    OUT["vggt"] = {
        "inference_ok": True,
        "model_id": MODEL_ID,
        "duration_s": round(time.time() - t0, 2),
        "prediction_keys": sorted([str(k) for k in predictions.keys()]),
        "field_shapes": {
            "depth": depth_shape,
            "depth_conf": depth_conf_shape,
            "world_points": wp_shape,
            "world_points_conf": wpc_shape,
        },
        "cuda_free_gb_after": round(torch.cuda.mem_get_info()[0] / 1024**3, 2) if torch.cuda.is_available() else None,
    }
    OUT["target_uv_sampling"] = roi_results
except Exception as exc:
    OUT["error"] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "trace_tail": traceback.format_exc()[-2200:],
    }
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "db45f_remote_target_uv_sampling_result.json").write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print("DB45F_JSON_BEGIN")
    print(json.dumps(OUT, sort_keys=True, separators=(",", ":")))
    print("DB45F_JSON_END")
'''


def _sanitize_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, str):
        text = obj
        for pat in SECRET_PATTERNS:
            text = pat.sub("[REDACTED]", text)
        return text
    return obj


def _extract_remote_json(log: str) -> dict[str, Any]:
    match = re.search(r"DB45F_JSON_BEGIN\s*(\{.*\})\s*DB45F_JSON_END", log, re.S)
    if not match:
        return {
            "db": "DB-45f",
            "error": {
                "type": "MissingRemoteJson",
                "message": "Remote job did not print DB45F_JSON markers in the returned log.",
                "log_tail": log[-3500:],
            },
        }
    return json.loads(match.group(1))


def _extract_recovery_json(log: str) -> dict[str, Any]:
    b64_match = re.search(r"DB45F_RECOVERY_B64_BEGIN\s*([A-Za-z0-9+/=\s]+?)\s*DB45F_RECOVERY_B64_END", log, re.S)
    if b64_match:
        payload = re.sub(r"\s+", "", b64_match.group(1))
        return json.loads(gzip.decompress(base64.b64decode(payload)).decode("utf-8"))
    match = re.search(r"DB45F_RECOVERY_JSON_BEGIN\s*(\{.*\})\s*DB45F_RECOVERY_JSON_END", log, re.S)
    if not match:
        return {
            "db": "DB-45f",
            "error": {
                "type": "MissingRecoveryJson",
                "message": "Recovery job did not print DB45F_RECOVERY_JSON markers in the returned log.",
                "log_tail": log[-3500:],
            },
        }
    return json.loads(match.group(1))


def _remote_recovery_python() -> str:
    return r'''
import base64
import gzip
import json
import math
import pathlib
import time
import traceback

SRC = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db45f_vggt_target_uv_sampling/db45f_remote_target_uv_sampling_result.json")

def finite_number(value):
    return isinstance(value, (int, float)) and math.isfinite(float(value))

def slim_stat(stat):
    if not isinstance(stat, dict):
        return {}
    keys = ["valid", "n", "mean", "med", "p10", "p90", "std"]
    return {k: stat.get(k) for k in keys if k in stat}

def compact_grid(grid, out_h=4, out_w=8):
    if not isinstance(grid, list) or not grid:
        return []
    h = len(grid)
    w = max((len(row) for row in grid if isinstance(row, list)), default=0)
    if h <= 0 or w <= 0:
        return []
    out = []
    for yy in range(out_h):
        row_out = []
        y0 = int(round(yy * h / out_h))
        y1 = max(y0 + 1, int(round((yy + 1) * h / out_h)))
        for xx in range(out_w):
            x0 = int(round(xx * w / out_w))
            x1 = max(x0 + 1, int(round((xx + 1) * w / out_w)))
            vals = []
            for row in grid[y0:y1]:
                if not isinstance(row, list):
                    continue
                for val in row[x0:x1]:
                    if finite_number(val):
                        vals.append(float(val))
            row_out.append(None if not vals else round(sum(vals) / len(vals), 6))
        out.append(row_out)
    return out

def slim_preprocess(preprocess):
    if not isinstance(preprocess, dict):
        return {}
    out = {
        "mode": preprocess.get("mode"),
        "function": preprocess.get("function"),
        "input_tensor_shape": preprocess.get("input_tensor_shape"),
        "source": preprocess.get("source"),
    }
    params = preprocess.get("params_by_camera", {})
    params_out = {}
    if isinstance(params, dict):
        keep = [
            "mode",
            "target_size",
            "raw_width",
            "raw_height",
            "new_width",
            "new_height",
            "crop_y",
            "post_crop_height",
            "post_crop_width",
            "final_height",
            "final_width",
            "pad_top",
            "pad_left",
        ]
        for cam, info in params.items():
            if isinstance(info, dict):
                params_out[str(cam)] = {k: info.get(k) for k in keep if k in info}
    out["params_by_camera"] = params_out
    return out

def slim_roi(roi):
    if not isinstance(roi, dict):
        return {}
    stats = roi.get("target_sampled_stats", {})
    heat = roi.get("heatmap_grids", {})
    pairs = roi.get("model_internal_overlap_pair_disagreement", {})
    pair_out = {}
    if isinstance(pairs, dict):
        for key, value in sorted(pairs.items()):
            if isinstance(value, dict):
                pair_out[str(key)] = {
                    "overlap_px": value.get("overlap_px"),
                    "valid_px": value.get("valid_px"),
                    "world_points_l2": slim_stat(value.get("world_points_l2", {})),
                }
    return {
        "roi_xyxy": roi.get("roi_xyxy"),
        "owner_counts": roi.get("owner_counts"),
        "coverage_valid_frac": roi.get("coverage_valid_frac"),
        "owner_uv_valid_frac_of_roi": roi.get("owner_uv_valid_frac_of_roi"),
        "owner_preprocess_valid_frac_of_roi": roi.get("owner_preprocess_valid_frac_of_roi"),
        "target_sampled_stats": {
            "depth": slim_stat(stats.get("depth", {})),
            "depth_conf": slim_stat(stats.get("depth_conf", {})),
            "world_points_conf": slim_stat(stats.get("world_points_conf", {})),
            "world_points_norm": slim_stat(stats.get("world_points_norm", {})),
        },
        "heatmap_grids": {
            "preprocess_valid": compact_grid(heat.get("preprocess_valid", [])),
            "depth_conf": compact_grid(heat.get("depth_conf", [])),
            "world_points_conf": compact_grid(heat.get("world_points_conf", [])),
        },
        "model_internal_overlap_pair_disagreement": pair_out,
        "admissibility": roi.get("admissibility", {}),
    }

out = {
    "db": "DB-45f",
    "recovered_from": str(SRC),
    "recovered_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
try:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    for key in [
        "db",
        "uuid",
        "anchor",
        "scope",
        "started_utc",
        "ended_utc",
        "raw_camera_load",
        "vggt",
        "secret_policy",
    ]:
        if key in data:
            out[key] = data[key]
    out["official_preprocess"] = slim_preprocess(data.get("official_preprocess", {}))
    target = data.get("target_uv_sampling", {})
    out["target_uv_sampling"] = {
        str(key): slim_roi(value)
        for key, value in sorted(target.items())
        if isinstance(value, dict)
    }
    if "error" in data:
        out["error"] = data["error"]
except Exception as exc:
    out["error"] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "trace_tail": traceback.format_exc()[-1800:],
    }

payload = json.dumps(out, sort_keys=True, separators=(",", ":")).encode("utf-8")
encoded = base64.b64encode(gzip.compress(payload, compresslevel=9)).decode("ascii")
print("DB45F_RECOVERY_B64_BEGIN")
print(encoded)
print("DB45F_RECOVERY_B64_END")
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
            }
            result = _sanitize_json(result)
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        if time.time() - started > timeout_s + 90:
            result = {
                "db": "DB-45f",
                "error": {"type": "LocalPollTimeout", "message": f"Timed out waiting for job {job_id}."},
                "colab_job": {"job_id": job_id, "state": state.get("state")},
            }
            REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result


def run_remote_recovery(timeout_s: int) -> dict[str, Any]:
    url = os.environ["COLAB_URL"].rstrip("/")
    colab_token = os.environ["COLAB_TOKEN"]
    previous = read_json(REMOTE_RESULT) if REMOTE_RESULT.exists() else {}
    remote_code_b64 = base64.b64encode(_remote_recovery_python().encode("utf-8")).decode("ascii")
    bash = (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{remote_code_b64}').decode('utf-8')\n"
        "exec(code, {'__name__': '__main__'})\n"
        "PY"
    )
    job = _post_json(url, colab_token, "/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content", "timeout_s": timeout_s})
    job_id = job["job_id"]
    started = time.time()
    while True:
        time.sleep(3)
        state = _get_json(url, colab_token, f"/jobs/{job_id}")
        if state.get("state") != "running":
            result = _extract_recovery_json(state.get("log_tail", ""))
            recovery_job = {
                "job_id": job_id,
                "state": state.get("state"),
                "exit_code": state.get("exit_code"),
                "duration_s": state.get("duration_s"),
                "purpose": "read existing DB45f Drive JSON only; no VGGT inference",
            }
            previous_job = previous.get("colab_job") if isinstance(previous, dict) else None
            result["colab_job"] = previous_job if isinstance(previous_job, dict) else recovery_job
            result["recovery_colab_job"] = recovery_job
            result = _sanitize_json(result)
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        if time.time() - started > timeout_s + 30:
            result = {
                "db": "DB-45f",
                "error": {"type": "LocalRecoveryPollTimeout", "message": f"Timed out waiting for recovery job {job_id}."},
                "recovery_colab_job": {"job_id": job_id, "state": state.get("state")},
            }
            REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result


def existing_evidence_rows() -> dict[str, dict[str, Any]]:
    db25 = read_json(DB25)
    db41 = read_json(DB41)
    rows = {
        "db25_longline": db25,
        "db41_right_roi": db41.get("summaries", {}).get("right_roi", {}),
        "db41_lower_right_roi": db41.get("summaries", {}).get("lower_right_roi", {}),
    }
    return rows


def owner_count_parity(existing_counts: dict[str, Any] | None, actual_counts: dict[str, Any] | None) -> dict[str, Any]:
    existing_counts = existing_counts or {}
    actual_counts = actual_counts or {}
    keys = sorted({str(k) for k in existing_counts} | {str(k) for k in actual_counts})
    expected = {k: float(existing_counts.get(k, 0.0)) for k in keys}
    actual = {k: float(actual_counts.get(k, 0.0)) for k in keys}
    exp_total = sum(expected.values())
    act_total = sum(actual.values())
    diffs = {}
    for key in keys:
        exp_frac = expected[key] / exp_total if exp_total > 0 else 0.0
        act_frac = actual[key] / act_total if act_total > 0 else 0.0
        diffs[key] = {
            "expected_frac": exp_frac,
            "actual_frac": act_frac,
            "abs_frac_diff": abs(exp_frac - act_frac),
        }
    return {
        "expected_total": int(exp_total),
        "actual_total": int(act_total),
        "max_abs_frac_diff": max((v["abs_frac_diff"] for v in diffs.values()), default=None),
        "l1_frac_diff": sum(v["abs_frac_diff"] for v in diffs.values()),
        "by_camera": diffs,
    }


def source_roi_rows(remote: dict[str, Any]) -> list[dict[str, Any]]:
    existing = existing_evidence_rows()
    uv = remote.get("target_uv_sampling", {})
    rows = []
    for roi_key, roi_meta in ROIS.items():
        ev = existing.get(roi_key, {})
        flow_pair_stats = ev.get("flow_pair_stats", {})
        target = uv.get(roi_key, {})
        parity = owner_count_parity(ev.get("camera_label_counts"), target.get("owner_counts"))
        rows.append(
            {
                **roi_meta,
                "roi_key": roi_key,
                "existing_evidence": {
                    "roi_valid_frac": ev.get("roi_valid_frac"),
                    "near_ground_frac": ev.get("near_ground_frac"),
                    "lidar_support_frac": ev.get("lidar_support_frac"),
                    "best_flow_pair": ev.get("best_flow_pair"),
                    "best_flow_reliable_frac": ev.get("best_flow_reliable_frac"),
                    "key_pair_6_5_flow_frac": flow_pair_stats.get("6-5", {}).get("fb_reliable_frac"),
                    "top_camera_labels": ev.get("top_camera_labels"),
                },
                "target_uv_sampling": target,
                "owner_label_parity": parity,
                "final_permission": {
                    "evidence_state": "RED",
                    "claim": "abstain",
                    "permission_delta": "unchanged",
                    "reason": "Target-ROI owner-UV VGGT sampling is model-diagnostic evidence; existing LiDAR/raw-flow target-surface support still fails DB45b promotion criteria.",
                },
            }
        )
    return rows


def generated_control_rows() -> list[dict[str, Any]]:
    return [
        {
            "segment_id": "db45_db36_fake_redline_reject",
            "label": "DB36 fake red-line DiT negative control",
            "evidence_state": "RED",
            "claim": "reject",
            "vggt_admissible": False,
            "reason": "VGGT sampled on raw cameras cannot validate generated-core slabs, holes, or fake line geometry.",
        },
        {
            "segment_id": "db45_db40_longsrc_fake_pole_reject",
            "label": "DB40 detector-clean fake-pole negative control",
            "evidence_state": "RED",
            "claim": "reject",
            "vggt_admissible": False,
            "reason": "Model confidence cannot launder a generated pole-like artifact.",
        },
    ]


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


def build_checks(
    remote: dict[str, Any],
    db45b: dict[str, Any],
    db45e: dict[str, Any],
    rows: list[dict[str, Any]],
    generated_rows: list[dict[str, Any]],
    secret_hits: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    if secret_hits is None:
        secret_hits = []
    vggt = remote.get("vggt", {})
    uv = remote.get("target_uv_sampling", {})
    preprocess = remote.get("official_preprocess", {})

    def chk(check_id: str, passed: bool, severity: str, evidence: str) -> dict[str, Any]:
        return {"id": check_id, "pass": bool(passed), "severity": severity, "evidence": evidence}

    lower = next((r for r in rows if r["roi_key"] == "db41_lower_right_roi"), {})
    lower_lidar = lower.get("existing_evidence", {}).get("lidar_support_frac")
    return [
        chk(
            "db45e_precondition",
            db45e.get("decision", {}).get("accepted_db45_diagnostic_evidence") is True,
            "precondition",
            "DB45e proved official VGGT inference and real confidence fields.",
        ),
        chk(
            "remote_job_completed",
            remote.get("colab_job", {}).get("exit_code") == 0 and remote.get("error") is None,
            "blocker",
            f"Colab job {remote.get('colab_job', {}).get('job_id')} exit={remote.get('colab_job', {}).get('exit_code')} error={remote.get('error')}.",
        ),
        chk(
            "one_log_anchor_scope",
            remote.get("uuid") == BMW_UUID and remote.get("anchor") == ANCHOR and remote.get("scope", {}).get("one_anchor") is True,
            "scope",
            "The remote job reports the BMW UUID and anchor 0 only.",
        ),
        chk(
            "official_vggt_inference_ran",
            vggt.get("inference_ok") is True and vggt.get("model_id") == "facebook/VGGT-1B-Commercial",
            "blocker",
            "Official VGGT Commercial forward pass ran on raw ring cameras.",
        ),
        chk(
            "preprocess_mapping_recorded",
            preprocess.get("mode") == "crop" and bool(preprocess.get("params_by_camera")),
            "blocker",
            "Official VGGT crop/pad mapping parameters are recorded per camera.",
        ),
        chk(
            "target_uv_sampling_available",
            all(uv.get(r["roi_key"], {}).get("admissibility", {}).get("target_uv_mapping_available") is True for r in rows),
            "blocker",
            "All source-evidence ROIs have derived ERP-to-raw-camera UV sampling.",
        ),
        chk(
            "owner_label_parity_with_existing_evidence",
            all(
                r.get("owner_label_parity", {}).get("max_abs_frac_diff") is not None
                and float(r.get("owner_label_parity", {}).get("max_abs_frac_diff")) <= 0.02
                for r in rows
            ),
            "blocker",
            "Recomputed source-owner labels match existing DB25/DB41 camera-label summaries within 2% fraction tolerance.",
        ),
        chk(
            "sample_validity_nonzero",
            all((uv.get(r["roi_key"], {}).get("owner_preprocess_valid_frac_of_roi") or 0.0) > 0.05 for r in rows),
            "blocker",
            "Each ROI has nonzero owner-UV sampled VGGT support after official preprocessing mapping.",
        ),
        chk(
            "old_uniform_wrapper_not_used",
            remote.get("scope", {}).get("old_uniform_wrapper_used") is False,
            "blocker",
            "The rejected run_vggt_multi_anchor.py uniform confidence wrapper was not used.",
        ),
        chk(
            "no_renderer_or_repair",
            remote.get("scope", {}).get("renderer") is False
            and remote.get("scope", {}).get("erp_repair") is False
            and remote.get("scope", {}).get("source_replacement") is False
            and remote.get("scope", {}).get("generated_image") is False,
            "scope",
            "No renderer, repaired ERP, source replacement, diffusion, or generated image was produced.",
        ),
        chk(
            "db45b_guardrails_active",
            db45b.get("decision", {}).get("gate_pass") is True and not db45b.get("decision", {}).get("red_promotions"),
            "precondition",
            "DB45b guardrails remain active and report no RED promotions.",
        ),
        chk(
            "no_red_promotion",
            all(r.get("final_permission", {}).get("evidence_state") == "RED" for r in rows),
            "blocker",
            "DB25 and DB41 owner-UV sampled ROIs remain RED/abstain.",
        ),
        chk(
            "db41_lower_right_zero_lidar_preserved",
            lower_lidar is not None and float(lower_lidar) == 0.0 and lower.get("final_permission", {}).get("evidence_state") == "RED",
            "blocker",
            "DB41 lower-right remains zero-LiDAR RED/abstain.",
        ),
        chk(
            "generated_fake_controls_not_laundered",
            all(r.get("vggt_admissible") is False and r.get("evidence_state") == "RED" for r in generated_rows),
            "blocker",
            "DB36/DB40 generated fake-geometry controls remain non-admissible rejects.",
        ),
        chk(
            "no_metric_ego_truth_overclaim",
            all(
                uv.get(r["roi_key"], {}).get("admissibility", {}).get("still_model_diagnostic_only") is True
                for r in rows
            ),
            "blocker",
            "VGGT pointmaps are labeled model-diagnostic only, not metric ego truth.",
        ),
        chk(
            "no_token_in_local_artifacts",
            not secret_hits,
            "blocker",
            f"Secret scan hits: {secret_hits}",
        ),
    ]


def heat_color(value: float | None, vmin: float, vmax: float) -> tuple[int, int, int]:
    if value is None:
        return (35, 35, 35)
    t = max(0.0, min(1.0, (float(value) - vmin) / max(1e-6, vmax - vmin)))
    return (int(40 + 210 * t), int(70 + 120 * (1.0 - abs(t - 0.5) * 2)), int(220 - 160 * t))


def draw_heatmap(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    grid: list[list[float | None]],
    title: str,
    vmin: float,
    vmax: float,
    cell: int = 12,
) -> None:
    draw.text((x, y), title, fill=(230, 230, 230), font=font(12))
    y += 18
    for yy, row in enumerate(grid):
        for xx, val in enumerate(row):
            c = heat_color(val, vmin, vmax)
            draw.rectangle([x + xx * cell, y + yy * cell, x + (xx + 1) * cell - 1, y + (yy + 1) * cell - 1], fill=c)


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1900, 1900), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((24, 18), "DB45f VGGT target-ROI owner-UV sampling gate", fill=(255, 255, 255), font=font(28))
    draw.text(
        (24, 54),
        "Samples official VGGT maps at the raw-camera pixels used by ERP seam ROIs. Evidence-only; no repair.",
        fill=(220, 220, 220),
        font=font(15),
    )
    decision = manifest["decision"]
    pill(draw, (24, 94, 342, 130), "owner-UV diagnostic: " + str(decision["accepted_db45_diagnostic_evidence"]).lower(), (38, 128, 76) if decision["accepted_db45_diagnostic_evidence"] else (160, 80, 55))
    pill(draw, (362, 94, 607, 130), "geometry evidence: false", (142, 74, 32))
    pill(draw, (627, 94, 822, 130), "RED promotions: 0", (78, 78, 78))
    pill(draw, (844, 94, 1118, 130), "metric ego truth: false", (88, 88, 88))

    remote = manifest["remote_result"]
    vggt = remote.get("vggt", {})
    y = 154
    draw.text((24, y), "Remote facts", fill=(255, 255, 255), font=font(21))
    y += 30
    for line in [
        f"job={remote.get('colab_job', {}).get('job_id')} exit={remote.get('colab_job', {}).get('exit_code')} duration={remote.get('colab_job', {}).get('duration_s')}",
        f"model={vggt.get('model_id')} inference_ok={vggt.get('inference_ok')} runtime_s={vggt.get('duration_s')}",
        f"field_shapes={vggt.get('field_shapes')} CUDA_free_after={vggt.get('cuda_free_gb_after')} GB",
        f"preprocess={remote.get('official_preprocess', {}).get('function')} mode={remote.get('official_preprocess', {}).get('mode')}",
    ]:
        y = draw_wrapped(draw, 42, y, "- " + line, 118, (235, 235, 235), 13, 5)
    y += 8

    draw.text((24, y), "ROI table", fill=(255, 255, 255), font=font(21))
    y += 34
    xs = [24, 290, 385, 500, 625, 770, 920, 1090]
    headers = ["ROI", "LiDAR", "Best flow", "UV valid", "preproc valid", "depth_conf med", "wp_conf med", "Final"]
    for x, h in zip(xs, headers):
        draw.text((x, y), h, fill=(210, 210, 210), font=font(13))
    y += 26
    for row in manifest["source_roi_rows"]:
        ev = row["existing_evidence"]
        uv = row["target_uv_sampling"]
        st = uv.get("target_sampled_stats", {})
        vals = [
            row["roi_key"],
            fmt(ev.get("lidar_support_frac")),
            fmt(ev.get("best_flow_reliable_frac")),
            fmt(uv.get("owner_uv_valid_frac_of_roi")),
            fmt(uv.get("owner_preprocess_valid_frac_of_roi")),
            fmt(st.get("depth_conf", {}).get("med")),
            fmt(st.get("world_points_conf", {}).get("med")),
            row["final_permission"]["evidence_state"] + "/" + row["final_permission"]["claim"],
        ]
        color = (255, 225, 180) if "lower" in row["roi_key"] else (235, 235, 235)
        for x, val in zip(xs, vals):
            draw.text((x, y), str(val), fill=color, font=font(13))
        y += 30

    y += 18
    draw.text((24, y), "Owner-UV sampled heatmaps", fill=(255, 255, 255), font=font(21))
    y += 34
    xh = 24
    heat_y = y
    for idx, row in enumerate(manifest["source_roi_rows"]):
        if idx == 2:
            xh = 24
            heat_y = y + 118
        uv = row["target_uv_sampling"]
        draw.text((xh, heat_y), row["roi_key"], fill=(255, 255, 255), font=font(14))
        grids = uv.get("heatmap_grids", {})
        draw_heatmap(draw, xh, heat_y + 24, grids.get("preprocess_valid", []), "valid", 0.0, 1.0, 10)
        draw_heatmap(draw, xh + 190, heat_y + 24, grids.get("depth_conf", []), "depth_conf", 0.8, 1.6, 10)
        draw_heatmap(draw, xh + 380, heat_y + 24, grids.get("world_points_conf", []), "wp_conf", 0.95, 1.05, 10)
        xh += 610

    x2 = 1210
    y2 = 154
    draw.text((x2, y2), "Hard checks", fill=(255, 255, 255), font=font(21))
    y2 += 34
    for check in manifest["checks"]:
        fill = (48, 140, 82) if check["pass"] else ((190, 72, 72) if check["severity"] == "blocker" else (150, 112, 52))
        pill(draw, (x2, y2, x2 + 70, y2 + 29), "PASS" if check["pass"] else "STOP", fill)
        y2 = draw_wrapped(draw, x2 + 82, y2 + 2, check["id"], 54, (238, 238, 238), 13, 4)
        y2 += 8

    montage_y = 840
    draw.line((24, montage_y - 22, 1865, montage_y - 22), fill=(75, 75, 75), width=1)
    draw.text((24, montage_y - 8), "Existing source evidence boards reused for visual check", fill=(255, 255, 255), font=font(21))
    tile_size = (595, 430)
    board.paste(label_tile(DB25_MONTAGE, "DB25 long-line evidence montage", tile_size), (24, montage_y + 26))
    board.paste(label_tile(DB41_RIGHT_MONTAGE, "DB41 right ROI evidence montage", tile_size), (645, montage_y + 26))
    board.paste(label_tile(DB41_LOWER_MONTAGE, "DB41 lower-right evidence montage", tile_size), (1266, montage_y + 26))

    y3 = montage_y + 530
    draw.text((24, y3), "Decision boundary", fill=(255, 255, 255), font=font(21))
    y3 += 32
    if decision["accepted_db45_diagnostic_evidence"]:
        boundary_lines = [
            "DB45f accepts owner-UV sampled VGGT metadata only as diagnostic evidence.",
            "Sampling at target-ROI owner pixels is stronger than DB45e owner-camera summaries, but it is still not LiDAR/raw-supported target-surface proof.",
            "Confidence-only RED promotion is killed by the zero/low-LiDAR controls.",
            "DB25/DB41 remain RED/abstain; DB36/DB40 remain generated fake-geometry rejects.",
            "No repaired panorama, renderer, source replacement, or RED promotion was produced.",
        ]
    else:
        boundary_lines = [
            "DB45f has not accepted owner-UV evidence yet; the current local artifact is blocked/pending recovery.",
            "Do not rerun VGGT inference for DB45f just to fix log truncation; recover the saved Drive JSON only.",
            "Until recovery passes the hard checks, DB25/DB41 remain RED/abstain and no permission state changes.",
            "No repaired panorama, renderer, source replacement, or RED promotion was produced.",
        ]
    for line in boundary_lines:
        y3 = draw_wrapped(draw, 42, y3, "- " + line, 118, (255, 235, 180), 13, 5)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def build_manifest() -> dict[str, Any]:
    db45b = read_json(DB45B)
    db45e = read_json(DB45E)
    remote = read_json(REMOTE_RESULT) if REMOTE_RESULT.exists() else {
        "db": "DB-45f",
        "error": {"type": "MissingRemoteResult", "message": "Run with --run-remote first."},
    }
    remote = _sanitize_json(remote)
    rows = source_roi_rows(remote)
    generated_rows = generated_control_rows()
    checks = build_checks(remote, db45b, db45e, rows, generated_rows, secret_hits=[])
    blocker_failures = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    accepted = not blocker_failures
    manifest = {
        "db": "DB-45f",
        "status": "vggt_target_uv_sampling_gate",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Sample official VGGT outputs at source-owner raw-camera pixels used by frozen ERP seam ROIs, while preserving DB45b no-RED-promotion guardrails.",
        "scope": {
            "one_a100_job": True,
            "uuid": BMW_UUID,
            "anchor": ANCHOR,
            "raw_ring_cameras": 7,
            "roi_count": len(ROIS),
            "model_inference": True,
            "panorama_generation": False,
            "panorama_repair": False,
            "source_replacement": False,
            "renderer": False,
            "diffusion_or_refiner": False,
            "permission_promotion_allowed_without_db45b_target_surface_support": False,
        },
        "decision": {
            "accepted_evidence_type": "vggt-target-uv-sampling-diagnostic-only" if accepted else "blocked-or-no-go",
            "accepted_db45_diagnostic_evidence": accepted,
            "accepted_db45_geometry_evidence": False,
            "vggt_target_uv_sampling_ran": remote.get("vggt", {}).get("inference_ok") is True,
            "permission_state_changes": "none",
            "red_promotions": [],
            "db45_status": "running",
            "claim_boundary": "Target-ROI owner-UV VGGT sampling is model-diagnostic evidence, not metric ego truth or seam repair permission without DB45b target-surface support. Confidence-only RED promotion is killed.",
        },
        "refs": {
            "db25_summary": rel(DB25),
            "db41_manifest": rel(DB41),
            "db45b_manifest": rel(DB45B),
            "db45e_manifest": rel(DB45E),
            "remote_result_json": rel(REMOTE_RESULT),
            "board": rel(BOARD),
        },
        "remote_result": remote,
        "source_roi_rows": rows,
        "generated_control_rows": generated_rows,
        "checks": checks,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    secret_hits = scan_secret_hits([REMOTE_RESULT, MANIFEST])
    checks = build_checks(remote, db45b, db45e, rows, generated_rows, secret_hits=secret_hits)
    blocker_failures = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    accepted = not blocker_failures
    manifest["checks"] = checks
    manifest["decision"]["accepted_evidence_type"] = (
        "vggt-target-uv-sampling-diagnostic-only" if accepted else "blocked-or-no-go"
    )
    manifest["decision"]["accepted_db45_diagnostic_evidence"] = accepted
    manifest["secret_scan_hits"] = secret_hits
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    build_board(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true", help="Run the one bounded Colab VGGT target-ROI owner-UV sampling job first.")
    parser.add_argument("--recover-remote", action="store_true", help="Read and compact the already saved DB45f Drive JSON without rerunning VGGT.")
    parser.add_argument("--timeout-s", type=int, default=1200)
    args = parser.parse_args()

    if args.run_remote:
        run_remote(args.timeout_s)
    if args.recover_remote:
        run_remote_recovery(args.timeout_s)
    manifest = build_manifest()
    print(f"wrote {MANIFEST}")
    print(f"wrote {BOARD}")
    print(json.dumps(manifest["decision"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
