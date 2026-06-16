from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, rel, safe_status, sanitize


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db67_dense_raw_aligned_surface_audit" / "phase1_vggt_dense_evidence"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db67_dense_raw_aligned_surface_audit/phase1_vggt_dense_evidence"
REMOTE_RESULT_PATH = REMOTE_OUT + "/db67_phase1_vggt_dense_remote_result.json"
REMOTE_SUMMARY_PATH = REMOTE_OUT + "/batch_summary.json"

LOCAL_REMOTE_RESULT = OUT_DIR / "db67_phase1_vggt_dense_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "db67_phase1_vggt_dense_batch_summary.json"
MANIFEST = OUT_DIR / "db67_phase1_vggt_dense_manifest.json"
BOARD = OUT_DIR / "db67_phase1_vggt_dense_board.jpg"
FETCH_DIR = OUT_DIR / "fetch"

CASES = ["02a00399:0:bmw", "0bae3b5e:30:clean_far"]
RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]
REQUIRED_MAPS = [
    "current_z_cause_primary_map",
    "dense_z_cause_primary_map",
    "dense_z_repairability_map",
    "dense_confidence_map",
    "dense_support_map",
    "lidar_agreement_map",
    "dense_depth_metric_cm_u16",
    "raw_projection_valid_count_map",
    "zbuffer_hit_count_map",
    "zbuffer_visible_count_map",
    "before_after_transition_map",
    "source_boundary_risk_proxy_map",
]

DEFAULT_HF_SECRET_FILES = [
    Path.home() / ".waymo2panorama" / "runtime" / "hf token.txt",
    Path.home() / ".waymo2panorama" / "runtime" / "hf_token.txt",
]

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "trycloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "json_token": re.compile(r'"token"\s*:\s*"[A-Za-z0-9._\-]{12,}"'),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
}


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def load_hf_secret() -> dict[str, str] | None:
    env_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if env_token:
        return {"token": env_token.strip(), "source_kind": "process_env"}
    candidates: list[Path] = []
    explicit = os.environ.get("W2P_HF_SECRET_FILE")
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(DEFAULT_HF_SECRET_FILES)
    for path in candidates:
        if not path.exists():
            continue
        if inside_repo(path):
            raise RuntimeError("HF secret file is inside repo and rejected")
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return {"token": token, "source_kind": "non_repo_file"}
    return None


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pattern in TOKEN_PATTERNS.items():
        found = pattern.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


def image_stat(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"exists": path.exists(), "path": rel(path)}
    if path.exists() and path.is_file():
        row["bytes"] = int(path.stat().st_size)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            try:
                with Image.open(path) as img:
                    row["size"] = list(img.size)
            except Exception as exc:  # pragma: no cover - diagnostic only
                row["image_error"] = str(exc)
    return row


def remote_python(hf_secret: dict[str, str] | None) -> str:
    hf_inject = ""
    if hf_secret:
        token_b64 = base64.b64encode(hf_secret["token"].encode("utf-8")).decode("ascii")
        hf_inject = f'''
_DB67_HF_TOKEN_B64 = "{token_b64}"
os.environ["HF_TOKEN"] = base64.b64decode(_DB67_HF_TOKEN_B64).decode("utf-8").strip()
os.environ["HUGGING_FACE_HUB_TOKEN"] = os.environ["HF_TOKEN"]
'''
    return rf'''
import base64
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import time
import traceback

REMOTE_OUT = pathlib.Path("{REMOTE_OUT}")
REMOTE_RESULT = pathlib.Path("{REMOTE_RESULT_PATH}")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
HF_HOME = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/cache/hf_vggt_db45d")
OFFICIAL_REPO = pathlib.Path("/content/vggt_db45d/vggt")
LOCAL_REPO = pathlib.Path("/content/waymo2panorama")
MODEL_ID = "facebook/VGGT-1B-Commercial"
CASES = {json.dumps(CASES)}
RUN_NAMES = {json.dumps(RUN_NAMES)}
REQUIRED_MAPS = {json.dumps(REQUIRED_MAPS)}
H, W = 1024, 2048

OUT = {{
    "db": "DB-67",
    "phase": "phase1_vggt_dense_raw_aligned_evidence",
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "scope": {{
        "fixed_cases_only": CASES,
        "a100_dense_inference": True,
        "rgb_repair_created": False,
        "source_replacement": False,
        "generation": False,
        "db32_edit": False,
        "red_promotion": False
    }},
    "secret_policy": "runtime/HF secrets are read only from approved env or non-repo files and are not written to outputs"
}}

{hf_inject}


def run(cmd, timeout=360, cwd=None):
    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    return {{
        "cmd": cmd[:3] + ["..."] if len(cmd) > 3 else cmd,
        "returncode": int(proc.returncode),
        "duration_s": round(time.time() - t0, 2),
        "tail": proc.stdout[-1600:],
    }}


def import_ok(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def json_safe(obj):
    import numpy as np
    if isinstance(obj, dict):
        return {{str(k): json_safe(v) for k, v in obj.items()}}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        return val if np.isfinite(val) else None
    return obj


def stat(arr):
    import numpy as np
    arr = np.asarray(arr, dtype=np.float32)
    finite = np.isfinite(arr)
    if not bool(finite.any()):
        return {{"valid": 0.0, "n": 0, "mean": None, "med": None, "p10": None, "p90": None, "p95": None, "std": None}}
    vals = arr[finite]
    return {{
        "valid": round(float(finite.mean()), 6),
        "n": int(vals.size),
        "mean": round(float(vals.mean()), 6),
        "med": round(float(np.percentile(vals, 50)), 6),
        "p10": round(float(np.percentile(vals, 10)), 6),
        "p90": round(float(np.percentile(vals, 90)), 6),
        "p95": round(float(np.percentile(vals, 95)), 6),
        "std": round(float(vals.std()), 6),
    }}


def frac(mask, denom):
    import numpy as np
    m = np.asarray(mask).astype(bool)
    d = np.asarray(denom).astype(bool)
    n = int(d.sum())
    if n <= 0:
        return None
    return float((m & d).sum() / n)


def unique_counts(arr):
    import numpy as np
    vals, counts = np.unique(arr.reshape(-1), return_counts=True)
    return {{str(int(v)): int(c) for v, c in zip(vals, counts)}}


def save_u8(path, arr):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype("uint8")).save(path)


def save_u16(path, arr):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype("uint16")).save(path)


def colorize(arr, palette, default=(40, 42, 48)):
    import numpy as np
    out = np.zeros((*arr.shape, 3), dtype=np.uint8)
    out[:] = np.array(default, dtype=np.uint8)
    for key, color in palette.items():
        out[arr == int(key)] = np.array(color, dtype=np.uint8)
    return out


def norm01(arr, valid):
    import numpy as np
    a = np.asarray(arr, dtype=np.float32)
    finite = np.asarray(valid).astype(bool) & np.isfinite(a)
    if not bool(finite.any()):
        return np.zeros_like(a, dtype=np.float32)
    vals = a[finite]
    lo = float(np.percentile(vals, 10))
    hi = float(np.percentile(vals, 90))
    if hi <= lo + 1e-6:
        return np.zeros_like(a, dtype=np.float32)
    out = (a - lo) / (hi - lo)
    return np.clip(np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0).astype(np.float32)


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
    return {{
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
    }}


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


def source_boundary(label, valid, seam_band):
    import cv2
    import numpy as np
    boundary = np.zeros(label.shape, dtype=bool)
    diff_x = (label[:, 1:] != label[:, :-1]) & valid[:, 1:] & valid[:, :-1]
    boundary[:, 1:] |= diff_x
    boundary[:, :-1] |= diff_x
    diff_y = (label[1:, :] != label[:-1, :]) & valid[1:, :] & valid[:-1, :]
    boundary[1:, :] |= diff_y
    boundary[:-1, :] |= diff_y
    boundary &= seam_band.astype(bool)
    return cv2.dilate(boundary.astype("uint8"), np.ones((5, 5), "uint8"), iterations=1).astype(bool)


def longest_component_frac(mask, seam_denom):
    import cv2
    import numpy as np
    m = (mask & seam_denom).astype("uint8")
    n, labels, stats, _cent = cv2.connectedComponentsWithStats(m, connectivity=8)
    cols = np.where(seam_denom.any(axis=0))[0]
    seam_len = int(cols.size)
    if seam_len <= 0 or n <= 1:
        return {{"frac": 0.0, "px": 0, "seam_length_px": seam_len}}
    best_w = 0
    best_area = 0
    for lab in range(1, n):
        area = int(stats[lab, cv2.CC_STAT_AREA])
        width = int(stats[lab, cv2.CC_STAT_WIDTH])
        if width > best_w or (width == best_w and area > best_area):
            best_w = width
            best_area = area
    return {{"frac": float(best_w / max(1, seam_len)), "px": int(best_w), "area": int(best_area), "seam_length_px": seam_len}}


def z_cause_from_depth(depth_map, support, source_valid, seam_band, boundary, images, Ks, Ts, zbuffers):
    import cv2
    import numpy as np
    from waymo2panorama.projection.lidar_zbuffer_layer import erp_dirs_ego
    H0, W0 = depth_map.shape
    dirs = erp_dirs_ego((H0, W0))
    safe_depth = np.where(support, depth_map, 1000.0).astype(np.float32)
    p_ego = dirs * safe_depth[..., None]
    geom_valid_count = np.zeros((H0, W0), dtype=np.uint8)
    zbuffer_hit_count = np.zeros((H0, W0), dtype=np.uint8)
    z_mismatch_count = np.zeros((H0, W0), dtype=np.uint8)
    visible_count = np.zeros((H0, W0), dtype=np.uint8)
    min_z_resid = np.full((H0, W0), np.inf, dtype=np.float32)
    for image, K, T, zbuf in zip(images, Ks, Ts, zbuffers):
        h_img, w_img = image.shape[:2]
        r_cam_ego = T[:3, :3].T
        t_ego_cam = T[:3, 3].astype(np.float32)
        p_cam = (p_ego - t_ego_cam[None, None, :]) @ r_cam_ego.T.astype(np.float32)
        z = p_cam[..., 2]
        in_front = z > 1e-6
        z_safe = np.where(in_front, z, 1.0)
        u_img = K[0, 0] * (p_cam[..., 0] / z_safe) + K[0, 2]
        v_img = K[1, 1] * (p_cam[..., 1] / z_safe) + K[1, 2]
        in_bounds = (
            (u_img >= 0.5)
            & (u_img <= w_img - 1.5)
            & (v_img >= 0.5)
            & (v_img <= h_img - 1.5)
        )
        norm = np.linalg.norm(p_cam, axis=-1)
        cam_cos = np.where(norm > 1e-9, z / np.maximum(norm, 1e-9), 0.0)
        angle_ok = cam_cos >= 0.03
        geom_valid = support & in_front & in_bounds & angle_ok
        map_x = np.where(geom_valid, u_img, -1.0).astype(np.float32)
        map_y = np.where(geom_valid, v_img, -1.0).astype(np.float32)
        sampled_z = cv2.remap(
            zbuf.depth_z_m,
            map_x,
            map_y,
            interpolation=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=1.0e6,
        )
        has_zbuf = geom_valid & (sampled_z < 1.0e6)
        tol = np.maximum(0.9, 0.05 * np.maximum(z, 0.0))
        resid = np.abs(z - sampled_z)
        z_match = has_zbuf & (resid <= tol)
        z_mismatch = has_zbuf & (~z_match)
        geom_valid_count += geom_valid.astype(np.uint8)
        zbuffer_hit_count += has_zbuf.astype(np.uint8)
        z_mismatch_count += z_mismatch.astype(np.uint8)
        visible_count += z_match.astype(np.uint8)
        min_z_resid = np.minimum(min_z_resid, np.where(has_zbuf, resid.astype(np.float32), np.inf))

    no_surface = source_valid & (~support)
    visible_ge2 = source_valid & support & (visible_count >= 2)
    single_visible = source_valid & support & (visible_count == 1)
    no_geom = source_valid & support & (visible_count == 0) & (geom_valid_count == 0)
    no_zbuf = source_valid & support & (visible_count == 0) & (geom_valid_count > 0) & (zbuffer_hit_count == 0)
    z_conflict = source_valid & support & (visible_count == 0) & (z_mismatch_count > 0)
    mixed_no_visible = source_valid & support & (visible_count == 0) & (~no_geom) & (~no_zbuf) & (~z_conflict)
    cause = np.full((H0, W0), 255, dtype=np.uint8)
    cause[no_surface] = 20
    cause[no_geom] = 41
    cause[no_zbuf] = 42
    cause[z_conflict] = 43
    cause[mixed_no_visible] = 44
    cause[single_visible] = 1
    cause[visible_ge2] = 0
    cause[boundary & visible_ge2] = 60
    repairability = np.zeros((H0, W0), dtype=np.uint8)
    target = source_valid & seam_band
    repairability[target & visible_ge2 & (~boundary)] = 1
    repairability[target & single_visible & (~boundary)] = 2
    repairability[target & no_surface & (~boundary)] = 3
    repairability[target & (no_geom | no_zbuf | z_conflict | mixed_no_visible)] = 5
    repairability[target & boundary] = 5
    seam_denom = source_valid & seam_band
    supported_no_visible = source_valid & support & (visible_count == 0)
    residual_vals = min_z_resid[np.isfinite(min_z_resid) & seam_denom]
    fractions = {{
        "seam_source_valid_frac": frac(source_valid, seam_band),
        "seam_support_frac": frac(support, seam_denom),
        "seam_visible_any_frac": frac(support & (visible_count > 0), seam_denom),
        "seam_visible_ge2_frac": frac(visible_ge2, seam_denom),
        "seam_single_visible_frac": frac(single_visible, seam_denom),
        "seam_no_surface_frac": frac(no_surface, seam_denom),
        "seam_no_camera_geom_valid_frac": frac(no_geom, seam_denom),
        "seam_no_raw_zbuffer_support_frac": frac(no_zbuf, seam_denom),
        "seam_z_mismatch_conflict_frac": frac(z_conflict, seam_denom),
        "seam_mixed_no_visible_frac": frac(mixed_no_visible, seam_denom),
        "seam_source_boundary_proxy_frac": frac(boundary, seam_denom),
        "supported_no_visible_no_zbuf_frac": frac(no_zbuf, supported_no_visible),
        "supported_no_visible_z_conflict_frac": frac(z_conflict, supported_no_visible),
        "supported_no_visible_no_geom_frac": frac(no_geom, supported_no_visible),
    }}
    return {{
        "cause": cause,
        "repairability": repairability,
        "geom_valid_count": geom_valid_count,
        "zbuffer_hit_count": zbuffer_hit_count,
        "z_mismatch_count": z_mismatch_count,
        "visible_count": visible_count,
        "min_z_resid": min_z_resid,
        "fractions": fractions,
        "counts": {{"z_cause_primary": unique_counts(cause), "z_repairability": unique_counts(repairability)}},
        "z_residual_min_m_seam": {{
            "n": int(residual_vals.size),
            "mean": float(residual_vals.mean()) if residual_vals.size else None,
            "p50": float(np.percentile(residual_vals, 50)) if residual_vals.size else None,
            "p90": float(np.percentile(residual_vals, 90)) if residual_vals.size else None,
            "p95": float(np.percentile(residual_vals, 95)) if residual_vals.size else None,
        }},
        "longest_visible_component": longest_component_frac(support & (visible_count > 0), seam_denom),
        "longest_support_component": longest_component_frac(support, seam_denom),
    }}


def one_case(case_spec, out_root, model, load_and_preprocess_images, torch):
    import cv2
    import numpy as np
    from PIL import Image
    from depth_visibility_seam_probe import _parse_case
    from seam_confidence_map import _heatmap_u8, _resize_w, _save_rgb, _stack_named
    from test_lidar_zbuffer_seam import _seam_masks, _winner_label
    from waymo2panorama.blending.hard_hdr_of import hard_select
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.depth.lidar_to_erp_depth import load_lidar_sweep_nearest_to_ts, project_lidar_to_erp_depth, visualize_depth_map
    from waymo2panorama.projection.lidar_zbuffer_layer import build_ring_zbuffers
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    t0 = time.time()
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    run_name = f"{{short}}_a{{anchor_idx:03d}}_{{tag}}"
    out_dir = out_root / run_name
    raw_dir = out_dir / "raw_cameras"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    loader = AV2RingLoader(log_dir)
    anchor_ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)
    erp_hw = (H, W)
    slabs, weights, images, Ks, Ts, uv_maps, raw_shapes, image_paths = [], [], [], [], [], [], [], []
    for idx, cam in enumerate(RING_CAMS_7):
        calib = frame.calibrations[cam]
        img = np.asarray(frame.images[cam])
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        raw_shapes.append([int(img.shape[0]), int(img.shape[1])])
        raw_path = raw_dir / f"cam_{{idx}}_{{cam}}.jpg"
        Image.fromarray(img).save(raw_path, quality=92)
        image_paths.append(str(raw_path))
        rgb, _alpha, w = render_camera_to_erp(
            image=img,
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w.astype(np.float32))
        images.append(img)
        Ks.append(calib.K)
        Ts.append(calib.T_ego_cam)
        uv_maps.append(render_uv(calib.K, calib.T_ego_cam, img.shape[:2], erp_hw=erp_hw))

    hard = hard_select(slabs, weights)
    source_label, source_valid = _winner_label(weights)
    seam_band, seam_core, seam_diag = _seam_masks(weights, band_half_width=48, core_half_width=2)
    boundary = source_boundary(source_label, source_valid, seam_band)

    pts, sweep_ts, lidar_delta_ms = load_lidar_sweep_nearest_to_ts(log_dir, anchor_ts, max_delta_ms=75.0)
    depth_map, depth_summary = project_lidar_to_erp_depth(
        pts,
        erp_hw=erp_hw,
        min_range_m=0.5,
        max_range_m=80.0,
        densify_radius_px=8,
        fill_far_m=1000.0,
    )
    current_support = np.isfinite(depth_map) & (depth_map < 120.0)
    zbuffers = build_ring_zbuffers(
        pts,
        images,
        Ks,
        Ts,
        min_range_m=0.5,
        max_range_m=80.0,
        dilation_px=5,
    )
    current_z = z_cause_from_depth(depth_map.astype(np.float32), current_support, source_valid, seam_band, boundary, images, Ks, Ts, zbuffers)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    t_vggt = time.time()
    input_images = load_and_preprocess_images(image_paths).to(device)
    autocast_ctx = torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16) if device == "cuda" else contextlib.nullcontext()
    with torch.no_grad(), autocast_ctx:
        predictions = model(input_images)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    n_views = len(image_paths)
    depth, depth_shape = views_field(predictions["depth"], n_views, channels=1)
    depth_conf, depth_conf_shape = views_field(predictions["depth_conf"], n_views, channels=1)
    world_points_conf, wpc_shape = views_field(predictions["world_points_conf"], n_views, channels=1)
    final_h, final_w = int(input_images.shape[-2]), int(input_images.shape[-1])
    preprocess = [preprocess_params(w_raw, h_raw, final_h, final_w) for h_raw, w_raw in raw_shapes]

    owner_depth = np.full((H, W), np.nan, dtype=np.float32)
    owner_dc = np.full((H, W), np.nan, dtype=np.float32)
    owner_wpc = np.full((H, W), np.nan, dtype=np.float32)
    owner_pre_valid = np.zeros((H, W), dtype=bool)
    for cam_idx in range(n_views):
        mask = (source_label == cam_idx) & source_valid
        if not bool(mask.any()):
            continue
        u_map, v_map, uv_valid_full = uv_maps[cam_idx]
        x_model, y_model, pre_valid = raw_to_model_xy(u_map, v_map, preprocess[cam_idx])
        valid = uv_valid_full & pre_valid & mask
        d_s = sample_scalar(depth, cam_idx, x_model, y_model, valid)
        dc_s = sample_scalar(depth_conf, cam_idx, x_model, y_model, valid)
        wpc_s = sample_scalar(world_points_conf, cam_idx, x_model, y_model, valid)
        owner_depth[mask] = d_s[mask]
        owner_dc[mask] = dc_s[mask]
        owner_wpc[mask] = wpc_s[mask]
        owner_pre_valid |= valid

    seam_denom = source_valid & seam_band
    fit_mask = seam_denom & current_support & owner_pre_valid & np.isfinite(owner_depth) & (owner_depth > 1e-6)
    ratios = depth_map[fit_mask] / np.maximum(owner_depth[fit_mask], 1e-6)
    ratios = ratios[np.isfinite(ratios) & (ratios > 0)]
    if ratios.size >= 200:
        scale = float(np.median(np.clip(ratios, np.percentile(ratios, 5), np.percentile(ratios, 95))))
        scale_source = "seam_lidar_overlap"
    else:
        broad = source_valid & current_support & owner_pre_valid & np.isfinite(owner_depth) & (owner_depth > 1e-6)
        ratios = depth_map[broad] / np.maximum(owner_depth[broad], 1e-6)
        ratios = ratios[np.isfinite(ratios) & (ratios > 0)]
        scale = float(np.median(np.clip(ratios, np.percentile(ratios, 5), np.percentile(ratios, 95)))) if ratios.size else 1.0
        scale_source = "broad_lidar_overlap" if ratios.size else "fallback_1"
    dense_depth = (owner_depth * scale).astype(np.float32)
    conf_valid = owner_pre_valid & np.isfinite(owner_dc) & np.isfinite(owner_wpc) & np.isfinite(owner_depth)
    dc_norm = norm01(owner_dc, conf_valid & seam_denom)
    wpc_norm = norm01(owner_wpc, conf_valid & seam_denom)
    dense_conf = np.clip(0.55 * dc_norm + 0.45 * wpc_norm, 0.0, 1.0).astype(np.float32)
    lidar_resid = np.abs(dense_depth - depth_map)
    lidar_agree = current_support & conf_valid & (lidar_resid <= np.maximum(1.5, 0.12 * np.maximum(depth_map, 1.0)))
    lidar_disagree = current_support & conf_valid & (~lidar_agree)
    dense_candidate = (
        source_valid
        & conf_valid
        & (dense_conf >= 0.42)
        & np.isfinite(dense_depth)
        & (dense_depth >= 0.5)
        & (dense_depth <= 120.0)
        & (~lidar_disagree)
    )
    dense_z = z_cause_from_depth(dense_depth, dense_candidate, source_valid, seam_band, boundary, images, Ks, Ts, zbuffers)

    transition = np.zeros((H, W), dtype=np.uint8)
    cur_no_surface = current_z["cause"] == 20
    dense_visible = dense_candidate & (dense_z["visible_count"] > 0)
    transition[cur_no_surface & dense_candidate & dense_visible] = 1
    transition[cur_no_surface & dense_candidate & (~dense_visible)] = 2
    transition[(current_z["visible_count"] > 0) & (~dense_candidate)] = 3
    transition[boundary & seam_denom] = 60
    lidar_agreement_map = np.zeros((H, W), dtype=np.uint8)
    lidar_agreement_map[dense_candidate & (~current_support)] = 90
    lidar_agreement_map[lidar_agree] = 255
    lidar_agreement_map[lidar_disagree & seam_denom] = 40

    z_cm = np.where(np.isfinite(dense_depth), np.clip(dense_depth * 100.0, 0, 65535), 65535).astype(np.uint16)
    save_u8(out_dir / f"{{run_name}}_current_z_cause_primary_map.png", current_z["cause"])
    save_u8(out_dir / f"{{run_name}}_dense_z_cause_primary_map.png", dense_z["cause"])
    save_u8(out_dir / f"{{run_name}}_dense_z_repairability_map.png", dense_z["repairability"])
    save_u8(out_dir / f"{{run_name}}_dense_confidence_map.png", np.clip(dense_conf * 255.0, 0, 255).astype(np.uint8))
    save_u8(out_dir / f"{{run_name}}_dense_support_map.png", dense_candidate.astype(np.uint8) * 255)
    save_u8(out_dir / f"{{run_name}}_lidar_agreement_map.png", lidar_agreement_map)
    save_u16(out_dir / f"{{run_name}}_dense_depth_metric_cm_u16.png", z_cm)
    save_u8(out_dir / f"{{run_name}}_raw_projection_valid_count_map.png", dense_z["geom_valid_count"])
    save_u8(out_dir / f"{{run_name}}_zbuffer_hit_count_map.png", dense_z["zbuffer_hit_count"])
    save_u8(out_dir / f"{{run_name}}_zbuffer_visible_count_map.png", dense_z["visible_count"])
    save_u8(out_dir / f"{{run_name}}_before_after_transition_map.png", transition)
    save_u8(out_dir / f"{{run_name}}_source_boundary_risk_proxy_map.png", boundary.astype(np.uint8) * 255)

    cause_palette = {{
        0: (70, 220, 120),
        1: (230, 220, 70),
        20: (245, 170, 60),
        41: (160, 120, 220),
        42: (240, 110, 55),
        43: (210, 70, 230),
        44: (90, 170, 230),
        60: (255, 70, 95),
        255: (60, 64, 72),
    }}
    transition_palette = {{0: (40, 40, 45), 1: (40, 220, 120), 2: (240, 210, 80), 3: (230, 70, 70), 60: (255, 80, 120)}}
    dense_cause_viz = colorize(dense_z["cause"], cause_palette)
    current_cause_viz = colorize(current_z["cause"], cause_palette)
    transition_viz = colorize(transition, transition_palette)
    conf_viz = _heatmap_u8(dense_conf)
    visible_viz = _heatmap_u8(np.clip(dense_z["visible_count"].astype(np.float32) / 3.0, 0.0, 1.0))
    support_overlay = hard.copy()
    support_overlay[dense_candidate & seam_band] = (0.55 * support_overlay[dense_candidate & seam_band] + 0.45 * np.array([50, 230, 120])).astype(np.uint8)
    agreement_viz = colorize(lidar_agreement_map, {{0: (40, 40, 45), 40: (230, 70, 70), 90: (240, 210, 80), 255: (60, 220, 120)}})
    save_u8(out_dir / f"{{run_name}}_current_z_cause_viz.png", current_cause_viz)
    save_u8(out_dir / f"{{run_name}}_dense_z_cause_viz.png", dense_cause_viz)
    save_u8(out_dir / f"{{run_name}}_before_after_transition_viz.png", transition_viz)
    save_u8(out_dir / f"{{run_name}}_phase1_support_overlay.png", support_overlay)
    save_u8(out_dir / f"{{run_name}}_lidar_agreement_viz.png", agreement_viz)

    review = _stack_named(
        [
            ("hard_select control", _resize_w(hard, 768)),
            ("current DB64-style z cause", _resize_w(current_cause_viz, 768)),
            ("VGGT dense z cause", _resize_w(dense_cause_viz, 768)),
            ("transition green=surface+visible yellow=surface/no-visible", _resize_w(transition_viz, 768)),
            ("dense confidence", _resize_w(conf_viz, 768)),
            ("dense support overlay", _resize_w(support_overlay, 768)),
            ("LiDAR agreement green/agap yellow/disagree red", _resize_w(agreement_viz, 768)),
            ("dense raw visible count", _resize_w(visible_viz, 768)),
            ("LiDAR depth baseline", _resize_w(visualize_depth_map(depth_map, log_clip_m=80.0), 768)),
        ]
    )
    _save_rgb(out_dir / f"{{run_name}}_phase1_vggt_dense_review_768.jpg", review, quality=88)

    current_f = current_z["fractions"]
    dense_f = dense_z["fractions"]
    improvements = {{
        "delta_no_surface_frac": None if current_f.get("seam_no_surface_frac") is None or dense_f.get("seam_no_surface_frac") is None else float(current_f["seam_no_surface_frac"] - dense_f["seam_no_surface_frac"]),
        "gain_visible_any_frac": None if current_f.get("seam_visible_any_frac") is None or dense_f.get("seam_visible_any_frac") is None else float(dense_f["seam_visible_any_frac"] - current_f["seam_visible_any_frac"]),
        "gain_visible_ge2_frac": None if current_f.get("seam_visible_ge2_frac") is None or dense_f.get("seam_visible_ge2_frac") is None else float(dense_f["seam_visible_ge2_frac"] - current_f["seam_visible_ge2_frac"]),
        "delta_no_raw_zbuffer_support_frac": None if current_f.get("seam_no_raw_zbuffer_support_frac") is None or dense_f.get("seam_no_raw_zbuffer_support_frac") is None else float(current_f["seam_no_raw_zbuffer_support_frac"] - dense_f["seam_no_raw_zbuffer_support_frac"]),
        "delta_z_mismatch_conflict_frac": None if current_f.get("seam_z_mismatch_conflict_frac") is None or dense_f.get("seam_z_mismatch_conflict_frac") is None else float(dense_f["seam_z_mismatch_conflict_frac"] - current_f["seam_z_mismatch_conflict_frac"]),
        "dense_longest_visible_component_frac": dense_z["longest_visible_component"]["frac"],
        "dense_longest_support_component_frac": dense_z["longest_support_component"]["frac"],
    }}
    success_checks = {{
        "no_surface_drop_or_threshold": bool((improvements["delta_no_surface_frac"] is not None and improvements["delta_no_surface_frac"] >= 0.15) or (dense_f.get("seam_no_surface_frac") is not None and dense_f["seam_no_surface_frac"] <= 0.40)),
        "visible_any_gain": bool(improvements["gain_visible_any_frac"] is not None and improvements["gain_visible_any_frac"] >= 0.10),
        "visible_ge2_gain_or_coherent_single": bool(improvements["gain_visible_ge2_frac"] is not None and improvements["gain_visible_ge2_frac"] >= 0.05),
        "no_zbuffer_not_worse": bool(dense_f.get("seam_no_raw_zbuffer_support_frac") is not None and current_f.get("seam_no_raw_zbuffer_support_frac") is not None and dense_f["seam_no_raw_zbuffer_support_frac"] <= current_f["seam_no_raw_zbuffer_support_frac"]),
        "no_zbuffer_drop_or_threshold": bool((improvements["delta_no_raw_zbuffer_support_frac"] is not None and improvements["delta_no_raw_zbuffer_support_frac"] >= 0.07) or (dense_f.get("seam_no_raw_zbuffer_support_frac") is not None and dense_f["seam_no_raw_zbuffer_support_frac"] <= 0.12)),
        "visible_component_continuity": bool(dense_z["longest_visible_component"]["frac"] >= 0.25),
        "support_component_continuity": bool(dense_z["longest_support_component"]["frac"] >= 0.25),
        "z_conflict_not_up_gt_0p03": bool(improvements["delta_z_mismatch_conflict_frac"] is not None and improvements["delta_z_mismatch_conflict_frac"] <= 0.03),
    }}
    diag = {{
        "case": run_name,
        "status": "phase1_dense_maps_complete",
        "case_spec": case_spec,
        "anchor_idx": int(anchor_idx),
        "anchor_ts_ns": int(anchor_ts),
        "lidar_sweep_ts_ns": int(sweep_ts),
        "lidar_delta_ms": float(lidar_delta_ms),
        "vggt": {{
            "duration_s": round(time.time() - t_vggt, 2),
            "input_tensor_shape": list(input_images.shape),
            "field_shapes": {{"depth": depth_shape, "depth_conf": depth_conf_shape, "world_points_conf": wpc_shape}},
            "owner_preprocess_valid_frac": frac(owner_pre_valid, source_valid),
            "dense_confidence_stats_seam": stat(dense_conf[seam_denom]),
            "owner_depth_raw_stats_seam": stat(owner_depth[seam_denom]),
            "metric_scale": scale,
            "metric_scale_source": scale_source,
            "metric_fit_pairs": int(ratios.size),
        }},
        "current": current_f,
        "dense": dense_f,
        "dense_extra": {{
            "seam_dense_candidate_frac": frac(dense_candidate, seam_denom),
            "seam_dense_only_no_lidar_frac": frac(dense_candidate & (~current_support), seam_denom),
            "seam_lidar_agreed_dense_frac": frac(dense_candidate & lidar_agree, seam_denom),
            "seam_lidar_disagree_frac": frac(lidar_disagree, seam_denom),
            "lidar_residual_m_seam": stat(lidar_resid[seam_denom & current_support & conf_valid]),
            "longest_visible_component": dense_z["longest_visible_component"],
            "longest_support_component": dense_z["longest_support_component"],
        }},
        "improvements": improvements,
        "success_checks": success_checks,
        "current_counts": current_z["counts"],
        "dense_counts": dense_z["counts"],
        "seam": seam_diag,
        "depth_summary": depth_summary,
        "claim_boundary": [
            "VGGT dense maps are target-surface evidence hypotheses only",
            "current LiDAR zbuffer remains the raw visibility gate",
            "no RGB repair, source replacement, or renderer output was created",
            "confidence/support without raw-zbuffer visibility is diagnostic, not source-faithful permission",
        ],
        "outputs": {{
            "review": f"{{run_name}}_phase1_vggt_dense_review_768.jpg",
            **{{name: f"{{run_name}}_{{name}}.png" for name in REQUIRED_MAPS}},
            "dense_z_cause_viz": f"{{run_name}}_dense_z_cause_viz.png",
            "current_z_cause_viz": f"{{run_name}}_current_z_cause_viz.png",
            "transition_viz": f"{{run_name}}_before_after_transition_viz.png",
            "support_overlay": f"{{run_name}}_phase1_support_overlay.png",
            "lidar_agreement_viz": f"{{run_name}}_lidar_agreement_viz.png",
        }},
        "runtime_s": round(time.time() - t0, 2),
    }}
    (out_dir / f"{{run_name}}_phase1_vggt_dense_breakdown.json").write_text(json.dumps(json_safe(diag), indent=2), encoding="utf-8")
    del predictions, input_images
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return diag


def aggregate(diags):
    def mean_key(section, key):
        vals = []
        for d in diags:
            val = d.get(section, {{}}).get(key)
            if isinstance(val, (int, float)):
                vals.append(float(val))
        return sum(vals) / len(vals) if vals else None

    bmw = next((d for d in diags if d.get("case") == "02a00399_a000_bmw"), None)
    clean = next((d for d in diags if d.get("case") == "0bae3b5e_a030_clean_far"), None)
    clean_degraded = False
    if clean:
        imp = clean.get("improvements", {{}})
        clean_degraded = bool(
            (imp.get("gain_visible_any_frac") is not None and imp["gain_visible_any_frac"] < -0.03)
            or (imp.get("delta_no_raw_zbuffer_support_frac") is not None and imp["delta_no_raw_zbuffer_support_frac"] < -0.03)
        )
    bmw_checks = bmw.get("success_checks", {{}}) if bmw else {{}}
    bmw_success = bool(
        bmw_checks.get("no_surface_drop_or_threshold")
        and bmw_checks.get("visible_any_gain")
        and bmw_checks.get("no_zbuffer_not_worse")
        and bmw_checks.get("visible_component_continuity")
        and bmw_checks.get("z_conflict_not_up_gt_0p03")
        and not clean_degraded
    )
    return {{
        "status": "phase1_vggt_dense_maps_complete" if len(diags) == len(CASES) else "phase1_incomplete",
        "n_cases": len(diags),
        "aggregate_success": bmw_success,
        "phase2_renderer_allowed": False,
        "clean_control_degraded": clean_degraded,
        "by_case": {{d["case"]: {{"current": d["current"], "dense": d["dense"], "dense_extra": d["dense_extra"], "improvements": d["improvements"], "success_checks": d["success_checks"]}} for d in diags}},
        "mean_improvements": {{
            "delta_no_surface_frac": mean_key("improvements", "delta_no_surface_frac"),
            "gain_visible_any_frac": mean_key("improvements", "gain_visible_any_frac"),
            "gain_visible_ge2_frac": mean_key("improvements", "gain_visible_ge2_frac"),
            "delta_no_raw_zbuffer_support_frac": mean_key("improvements", "delta_no_raw_zbuffer_support_frac"),
            "delta_z_mismatch_conflict_frac": mean_key("improvements", "delta_z_mismatch_conflict_frac"),
        }},
        "route_verdict": "phase1_evidence_passed_renderer_still_requires_new_brief" if bmw_success else "phase1_evidence_failed_or_diagnostic_only",
    }}


try:
    t_all = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(HF_HOME)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    if not (OFFICIAL_REPO / ".git").exists():
        OFFICIAL_REPO.parent.mkdir(parents=True, exist_ok=True)
        OUT["official_repo_clone"] = run(["git", "clone", "--depth", "1", "https://github.com/facebookresearch/vggt.git", str(OFFICIAL_REPO)], timeout=300)
    else:
        OUT["official_repo_clone"] = {{"returncode": 0, "tail": "repo already present", "duration_s": 0.0}}
    small_deps = [dep for dep in ("huggingface_hub", "safetensors", "einops") if not import_ok(dep)]
    OUT["deps_before"] = {{dep: import_ok(dep) for dep in ("torch", "torchvision", "numpy", "PIL", "huggingface_hub", "safetensors", "einops", "vggt")}}
    if small_deps:
        OUT["small_dep_install"] = run([sys.executable, "-m", "pip", "install", "-q"] + small_deps, timeout=360)
    else:
        OUT["small_dep_install"] = {{"returncode": 0, "tail": "all small deps already importable", "duration_s": 0.0}}
    OUT["editable_install_no_deps"] = run([sys.executable, "-m", "pip", "install", "-q", "-e", str(OFFICIAL_REPO), "--no-deps"], timeout=360)
    OUT["deps_after"] = {{dep: import_ok(dep) for dep in ("torch", "torchvision", "numpy", "PIL", "huggingface_hub", "safetensors", "einops", "vggt")}}
    OUT["av2_before"] = import_ok("av2")
    if not OUT["av2_before"]:
        OUT["av2_install"] = run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=900)
    else:
        OUT["av2_install"] = {{"returncode": 0, "tail": "av2 already importable", "duration_s": 0.0}}
    OUT["av2_after"] = import_ok("av2")

    for p in [OFFICIAL_REPO, LOCAL_REPO / "code", LOCAL_REPO / "scripts" / "phase3", LOCAL_REPO]:
        sys.path.insert(0, str(p))
    import torch
    from vggt.models.vggt import VGGT
    from vggt.utils.load_fn import load_and_preprocess_images

    device = "cuda" if torch.cuda.is_available() else "cpu"
    t_model = time.time()
    model = VGGT.from_pretrained(MODEL_ID).to(device).eval()
    OUT["model"] = {{
        "model_id": MODEL_ID,
        "load_s": round(time.time() - t_model, 2),
        "device": device,
        "cuda_free_gb_after_load": round(torch.cuda.mem_get_info()[0] / 1024**3, 2) if torch.cuda.is_available() else None,
    }}
    diags = []
    errors = []
    for case in CASES:
        try:
            diags.append(one_case(case, REMOTE_OUT, model, load_and_preprocess_images, torch))
        except Exception as exc:
            errors.append({{"case": case, "error": repr(exc), "trace_tail": traceback.format_exc()[-2400:]}})
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    summary = aggregate(diags)
    summary["errors"] = errors
    summary["status"] = "phase1_vggt_dense_maps_complete" if summary["status"] == "phase1_vggt_dense_maps_complete" and not errors else "phase1_vggt_dense_incomplete_or_failed"
    (REMOTE_OUT / "batch_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    OUT["batch_summary"] = summary
    OUT["status"] = "db67_phase1_vggt_dense_completed" if summary["status"] == "phase1_vggt_dense_maps_complete" else "db67_phase1_vggt_dense_failed_or_blocked"
    OUT["runtime_s"] = round(time.time() - t_all, 2)
except Exception as exc:
    OUT["status"] = "db67_phase1_vggt_dense_failed_or_blocked"
    OUT["error"] = {{"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB67_JSON_BEGIN")
    print(json.dumps(json_safe(OUT), sort_keys=True, separators=(",", ":")))
    print("DB67_JSON_END")
'''


def extract_remote_json(log: str) -> dict[str, Any]:
    match = re.search(r"DB67_JSON_BEGIN\s*(\{.*\})\s*DB67_JSON_END", log, re.S)
    if match:
        return sanitize(json.loads(match.group(1)))
    return {
        "db": "DB-67",
        "status": "missing_remote_json_marker",
        "error": {"type": "MissingRemoteJson", "message": "remote log did not contain DB67 JSON markers", "log_tail": sanitize(log[-2500:])},
    }


def run_exec(client: ColabClient, code: str, timeout_s: int) -> dict[str, Any]:
    remote_code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    bash = (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{remote_code_b64}').decode('utf-8')\n"
        "exec(compile(code, '<db67_phase1_remote>', 'exec'))\n"
        "PY"
    )
    job = client.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content", "timeout_s": timeout_s}, timeout=180)
    job_id = str(job["job_id"])
    started = time.time()
    while True:
        time.sleep(8)
        state = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if state.get("state") != "running":
            result = extract_remote_json(state.get("log_tail", ""))
            result["colab_job"] = {
                "job_id": job_id,
                "state": state.get("state"),
                "exit_code": state.get("exit_code"),
                "duration_s": state.get("duration_s"),
            }
            return sanitize(result)
        if time.time() - started > timeout_s + 180:
            return {
                "db": "DB-67",
                "status": "local_poll_timeout",
                "error": {"type": "LocalPollTimeout", "message": f"timed out waiting for job {job_id}"},
                "colab_job": {"job_id": job_id, "state": state.get("state")},
            }


def fetch_file(client: ColabClient, remote_path: str, local_path: Path, max_size_mb: int) -> dict[str, Any]:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    raw = client.read_file(remote_path, max_size_mb=max_size_mb)
    if raw is None:
        return {"remote_path": "<remote path omitted>", "path": rel(local_path), "exists": False}
    local_path.write_bytes(raw)
    return {"remote_path": "<remote path omitted>", **image_stat(local_path)}


def fetch_outputs(client: ColabClient) -> dict[str, Any]:
    fetched: dict[str, Any] = {}
    fetched["remote_result"] = fetch_file(client, REMOTE_RESULT_PATH, LOCAL_REMOTE_RESULT, 20)
    fetched["summary"] = fetch_file(client, REMOTE_SUMMARY_PATH, LOCAL_SUMMARY, 10)
    for run_name in RUN_NAMES:
        case_dir = FETCH_DIR / run_name
        base = REMOTE_OUT + "/" + run_name + "/" + run_name
        fetched[run_name] = {
            "breakdown": fetch_file(client, base + "_phase1_vggt_dense_breakdown.json", case_dir / f"{run_name}_phase1_vggt_dense_breakdown.json", 20),
            "review": fetch_file(client, base + "_phase1_vggt_dense_review_768.jpg", case_dir / f"{run_name}_phase1_vggt_dense_review_768.jpg", 40),
            "dense_z_cause_viz": fetch_file(client, base + "_dense_z_cause_viz.png", case_dir / f"{run_name}_dense_z_cause_viz.png", 16),
            "current_z_cause_viz": fetch_file(client, base + "_current_z_cause_viz.png", case_dir / f"{run_name}_current_z_cause_viz.png", 16),
            "transition_viz": fetch_file(client, base + "_before_after_transition_viz.png", case_dir / f"{run_name}_before_after_transition_viz.png", 16),
            "support_overlay": fetch_file(client, base + "_phase1_support_overlay.png", case_dir / f"{run_name}_phase1_support_overlay.png", 16),
            "lidar_agreement_viz": fetch_file(client, base + "_lidar_agreement_viz.png", case_dir / f"{run_name}_lidar_agreement_viz.png", 16),
        }
        for name in REQUIRED_MAPS:
            fetched[run_name][name] = fetch_file(client, base + f"_{name}.png", case_dir / f"{run_name}_{name}.png", 16)
    return fetched


def run_remote(timeout_s: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hf_secret = load_hf_secret()
    client = ColabClient()
    status = sanitize(client.get("/status", timeout=180))
    result = run_exec(client, remote_python(hf_secret), timeout_s)
    result["runtime_status_pre_exec"] = safe_status(status)
    result["runtime_secret_source"] = "approved_env_or_non_repo_file"
    result["hf_secret_source_kind"] = hf_secret["source_kind"] if hf_secret else "not_forwarded_or_not_found"
    LOCAL_REMOTE_RESULT.write_text(json.dumps(sanitize(result), indent=2, sort_keys=True), encoding="utf-8")
    fetched = fetch_outputs(client)
    return result, fetched, status, result["hf_secret_source_kind"]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def panel(board: Image.Image, src: Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle([x0, y0, x1, y1], outline=(80, 86, 96), width=1)
    draw.text((x0 + 8, y0 + 8), label, fill=(235, 235, 235), font=ImageFont.load_default())
    if not src.exists():
        draw.text((x0 + 12, y0 + 30), "missing", fill=(255, 120, 120), font=ImageFont.load_default())
        return
    with Image.open(src) as img:
        im = img.convert("RGB")
        im.thumbnail((x1 - x0 - 18, y1 - y0 - 34), Image.Resampling.LANCZOS)
    board.paste(im, (x0 + (x1 - x0 - im.width) // 2, y0 + 28))


def fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2300, 1750), (18, 20, 24))
    draw = ImageDraw.Draw(board)
    draw.text((36, 24), "DB67 Phase1 - VGGT Dense Raw-Aligned Target-Surface Evidence Audit", fill=(245, 245, 245), font=ImageFont.load_default())
    draw.text((36, 48), "A100 VGGT evidence only: no RGB repair, no source replacement, no generation.", fill=(235, 205, 145), font=ImageFont.load_default())
    summary = manifest.get("summary", {})
    lines = [
        f"remote_status={manifest.get('remote_status')} job={manifest.get('job', {}).get('state')} exit={manifest.get('job', {}).get('exit_code')}",
        f"aggregate_success={summary.get('aggregate_success')} phase2_renderer_allowed={summary.get('phase2_renderer_allowed')} clean_degraded={summary.get('clean_control_degraded')}",
        f"mean improvements: {summary.get('mean_improvements')}",
        "Green transition means current no-surface became dense surface + raw visible; yellow means dense surface but no raw visible.",
    ]
    y = 78
    for line in lines:
        draw.text((36, y), line[:220], fill=(220, 228, 238), font=ImageFont.load_default())
        y += 18
    panel(board, FETCH_DIR / "02a00399_a000_bmw" / "02a00399_a000_bmw_phase1_vggt_dense_review_768.jpg", (36, 160, 1110, 820), "BMW dense evidence review")
    panel(board, FETCH_DIR / "0bae3b5e_a030_clean_far" / "0bae3b5e_a030_clean_far_phase1_vggt_dense_review_768.jpg", (1140, 160, 2264, 820), "Clean-control dense evidence review")
    panel(board, FETCH_DIR / "02a00399_a000_bmw" / "02a00399_a000_bmw_before_after_transition_viz.png", (36, 850, 550, 1160), "BMW transition")
    panel(board, FETCH_DIR / "02a00399_a000_bmw" / "02a00399_a000_bmw_lidar_agreement_viz.png", (580, 850, 1094, 1160), "BMW LiDAR agreement")
    panel(board, FETCH_DIR / "02a00399_a000_bmw" / "02a00399_a000_bmw_phase1_support_overlay.png", (1124, 850, 1660, 1160), "BMW support overlay")
    panel(board, FETCH_DIR / "02a00399_a000_bmw" / "02a00399_a000_bmw_dense_z_cause_viz.png", (1690, 850, 2264, 1160), "BMW dense z-cause")
    by_case = summary.get("by_case", {})
    text_blocks = [
        ("BMW", by_case.get("02a00399_a000_bmw", {})),
        ("Clean", by_case.get("0bae3b5e_a030_clean_far", {})),
    ]
    x = 50
    for title, row in text_blocks:
        draw.text((x, 1210), title, fill=(245, 245, 245), font=ImageFont.load_default())
        yy = 1235
        for section in ["current", "dense", "dense_extra", "improvements", "success_checks"]:
            value = row.get(section, {})
            draw.text((x, yy), section + ":", fill=(235, 210, 160), font=ImageFont.load_default())
            yy += 16
            for k, v in list(value.items())[:10]:
                draw.text((x + 12, yy), f"{k}={fmt(v)}"[:120], fill=(220, 226, 232), font=ImageFont.load_default())
                yy += 15
        x += 1120
    draw.rectangle([32, 1660, 2268, 1728], outline=(230, 90, 90), width=2)
    draw.text((50, 1678), "Claim boundary: this is dense geometry evidence only. If sidecars fail thresholds, no source-faithful repair or renderer follows.", fill=(255, 220, 190), font=ImageFont.load_default())
    board.save(BOARD, quality=92)


def build_manifest(remote: dict[str, Any], fetched: dict[str, Any], status: dict[str, Any], hf_source_kind: str) -> dict[str, Any]:
    summary = read_json(LOCAL_SUMMARY)
    run_ok = bool(remote.get("status") == "db67_phase1_vggt_dense_completed" and summary.get("status") == "phase1_vggt_dense_maps_complete")
    manifest: dict[str, Any] = {
        "db": "DB-67",
        "phase": "phase1_vggt_dense_evidence",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "remote_status": remote.get("status"),
        "job": remote.get("colab_job", {}),
        "runtime": {"status": safe_status(status), "secret_source_kind": "approved_env_or_non_repo_file", "hf_secret_source_kind": hf_source_kind},
        "scope": {
            "remote_status_used": True,
            "remote_exec_count": 1,
            "fixed_cases_only": CASES,
            "a100_used": True,
            "backend": "VGGT",
            "rgb_repair_created": False,
            "source_replacement_used": False,
            "generation_used": False,
            "db32_edited": False,
            "red_promotion": False,
        },
        "summary": summary,
        "fetched": fetched,
        "hard_checks": {
            "remote_completed": run_ok,
            "required_maps_fetched": all(
                bool((fetched.get(run_name, {}).get(name, {}) or {}).get("exists"))
                for run_name in RUN_NAMES
                for name in REQUIRED_MAPS
            ),
            "accepted_as_repair": False,
            "renderer_allowed": False,
            "source_faithful_permission_changed": False,
        },
        "outputs": {"output_dir": rel(OUT_DIR), "manifest": rel(MANIFEST), "board": rel(BOARD), "remote_result": rel(LOCAL_REMOTE_RESULT), "summary": rel(LOCAL_SUMMARY)},
        "claim_boundary": [
            "VGGT dense geometry is evidence only.",
            "Dense confidence is not source truth or repair permission.",
            "No RGB repair/source replacement/generation occurred.",
            "A later renderer would require a fresh decision brief and only if evidence thresholds pass.",
        ],
    }
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    hits = secret_hits(text)
    manifest["secret_scan"] = {"strict_secret_like_hit_count": len(hits), "hits": hits}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    build_board(manifest)
    final_hits = secret_hits(MANIFEST.read_text(encoding="utf-8"))
    if final_hits:
        raise RuntimeError(f"secret-like value detected in DB67 manifest: {final_hits}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=3600)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.run_remote:
        remote, fetched, status, hf_source_kind = run_remote(args.timeout_s)
    else:
        remote = read_json(LOCAL_REMOTE_RESULT)
        status = remote.get("runtime_status_pre_exec", {})
        fetched = {}
        hf_source_kind = remote.get("hf_secret_source_kind", "unknown")
    manifest = build_manifest(remote, fetched, status, hf_source_kind)
    print(json.dumps({
        "status": manifest["remote_status"],
        "summary_status": manifest.get("summary", {}).get("status"),
        "aggregate_success": manifest.get("summary", {}).get("aggregate_success"),
        "board": rel(BOARD),
        "manifest": rel(MANIFEST),
        "secret_hits": manifest["secret_scan"]["strict_secret_like_hit_count"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
