from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0" / "phase4b_z_visibility_cause"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db64_ltr_v0/phase4b_z_visibility_cause"
REMOTE_RESULT = REMOTE_OUT + "/db64_phase4b_z_visibility_remote_result.json"
REMOTE_SUMMARY = REMOTE_OUT + "/batch_summary.json"

LOCAL_REMOTE_RESULT = OUT_DIR / "db64_phase4b_z_visibility_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "db64_phase4b_batch_summary.json"
MANIFEST = OUT_DIR / "db64_phase4b_z_visibility_manifest.json"
BOARD = OUT_DIR / "db64_phase4b_z_visibility_board.jpg"
FETCH_DIR = OUT_DIR / "fetch"

CASES = ["02a00399:0:bmw", "0bae3b5e:30:clean_far"]
RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]
REQUIRED_MAPS = [
    "z_cause_primary_map",
    "camera_geom_valid_count_map",
    "camera_zbuffer_hit_count_map",
    "camera_z_mismatch_count_map",
    "camera_visible_count_map",
    "z_residual_min_cm_u16",
    "z_repairability_map",
]
DEFAULT_RUNTIME_SECRET_FILES = [
    Path.home() / ".waymo2panorama" / "runtime" / "active_url.json",
    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Waymo2Panorama" / "runtime" / "active_url.json",
]

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "trycloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "json_token": re.compile(r'"token"\s*:\s*"[A-Za-z0-9._\-]{12,}"'),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
}


def rel(path: Path | str | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    if not p.is_absolute():
        return str(p).replace("\\", "/")
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return "<non-repo path omitted>"


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def load_runtime_secret() -> dict[str, str]:
    env_url = os.environ.get("COLAB_URL")
    env_token = os.environ.get("COLAB_TOKEN")
    if env_url and env_token:
        return {"url": env_url, "token": env_token, "source": "process_env"}

    candidates: list[Path] = []
    explicit = os.environ.get("W2P_RUNTIME_SECRET_FILE")
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(DEFAULT_RUNTIME_SECRET_FILES)

    for path in candidates:
        if not path.exists():
            continue
        if inside_repo(path):
            raise RuntimeError("runtime secret file is inside repo and rejected")
        data = json.loads(path.read_text(encoding="utf-8"))
        url = data.get("url")
        token = data.get("token")
        if not url or not token:
            raise RuntimeError("runtime secret file missing url/token")
        return {"url": str(url), "token": str(token), "source": f"non_repo_file:{path}"}
    raise RuntimeError("No approved runtime secret source found.")


def sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        clean: dict[str, Any] = {}
        for key, value in obj.items():
            if str(key).lower() in {"token", "authorization", "headers"}:
                clean[key] = "<redacted>"
            else:
                clean[key] = sanitize(value)
        return clean
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str):
        s = obj
        for pattern in TOKEN_PATTERNS.values():
            s = pattern.sub("<redacted>", s)
        return s
    return obj


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pat in TOKEN_PATTERNS.items():
        found = pat.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


class ColabClient:
    def __init__(self) -> None:
        runtime = load_runtime_secret()
        self.url = runtime["url"].rstrip("/")
        self.token = runtime["token"]
        self.source = runtime["source"]

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        timeout: int = 180,
    ) -> dict[str, Any]:
        url = self.url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get(self, path: str, timeout: int = 180) -> dict[str, Any]:
        return self.request("GET", path, timeout=timeout)

    def post(self, path: str, body: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
        return self.request("POST", path, body=body, timeout=timeout)

    def read_file(self, remote_path: str, max_size_mb: int = 80) -> bytes | None:
        try:
            data = self.request(
                "GET",
                "/read",
                params={"path": remote_path, "base64": "true", "max_size_mb": str(max_size_mb)},
                timeout=240,
            )
        except Exception:
            return None
        if "content" not in data:
            return None
        return base64.b64decode(data["content"])


def safe_status(status: dict[str, Any]) -> dict[str, Any]:
    allowed = {"runtime_type", "version", "gpu_name", "gpu_mem_free_mb", "active_jobs", "uptime_s", "timestamp"}
    return {k: status.get(k) for k in allowed if k in status}


def poll_job(client: ColabClient, job_id: str, timeout_s: int) -> dict[str, Any]:
    t0 = time.time()
    last: dict[str, Any] = {}
    while time.time() - t0 < timeout_s + 90:
        time.sleep(5)
        last = client.get(f"/jobs/{job_id}", timeout=180)
        if last.get("state") != "running":
            return last
    return last or {"state": "poll_timeout", "job_id": job_id}


def remote_python() -> str:
    return r'''
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REMOTE_OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db64_ltr_v0/phase4b_z_visibility_cause")
REMOTE_RESULT = REMOTE_OUT / "db64_phase4b_z_visibility_remote_result.json"
AV2_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [
    Path("/content/waymo2panorama"),
    Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama"),
]
CASES = ["02a00399:0:bmw", "0bae3b5e:30:clean_far"]
RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]
REQUIRED_MAPS = [
    "z_cause_primary_map",
    "camera_geom_valid_count_map",
    "camera_zbuffer_hit_count_map",
    "camera_z_mismatch_count_map",
    "camera_visible_count_map",
    "z_residual_min_cm_u16",
    "z_repairability_map",
]


def tail(text, limit=12000):
    if text is None:
        return ""
    return str(text)[-limit:]


def json_safe(obj):
    import numpy as np
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        return val if np.isfinite(val) else None
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def find_workdir():
    for cand in WORKDIR_CANDIDATES:
        if (cand / "scripts" / "phase3" / "test_lidar_zbuffer_seam.py").exists():
            return cand
    return None


def file_row(path):
    return {
        "exists": path.exists(),
        "bytes": int(path.stat().st_size) if path.exists() and path.is_file() else None,
        "path": str(path),
    }


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


def frac(mask, denom=None):
    import numpy as np
    m = np.asarray(mask).astype(bool)
    if denom is None:
        return float(m.mean())
    d = np.asarray(denom).astype(bool)
    n = int(d.sum())
    if n <= 0:
        return None
    return float((m & d).sum() / n)


def unique_counts(arr):
    import numpy as np
    vals, counts = np.unique(arr.reshape(-1), return_counts=True)
    return {str(int(v)): int(c) for v, c in zip(vals, counts)}


def map_stats(name, mask, denom):
    return {"name": name, "pixels": int(mask.sum()), "frac": frac(mask, denom)}


def one_case(case_spec, av2_root, out_root):
    import cv2
    import numpy as np
    from depth_visibility_seam_probe import _parse_case
    from seam_confidence_map import _heatmap_u8, _resize_w, _save_rgb, _stack_named
    from test_lidar_zbuffer_seam import _seam_masks, _winner_label
    from waymo2panorama.blending.hard_hdr_of import hard_select
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.depth.lidar_to_erp_depth import load_lidar_sweep_nearest_to_ts, project_lidar_to_erp_depth, visualize_depth_map
    from waymo2panorama.projection.lidar_zbuffer_layer import build_ring_zbuffers, erp_dirs_ego
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    t0 = time.time()
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, av2_root)
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    out_dir = out_root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    erp_hw = (1024, 2048)
    loader = AV2RingLoader(log_dir)
    anchor_ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)

    slabs, weights, images, Ks, Ts = [], [], [], [], []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=frame.images[cam],
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
        images.append(frame.images[cam])
        Ks.append(calib.K)
        Ts.append(calib.T_ego_cam)

    hard = hard_select(slabs, weights)
    sphere_label, source_valid = _winner_label(weights)
    seam_band, seam_core, seam_diag = _seam_masks(weights, band_half_width=48, core_half_width=2)
    boundary = source_boundary(sphere_label, source_valid, seam_band)

    pts, sweep_ts, lidar_delta_ms = load_lidar_sweep_nearest_to_ts(log_dir, anchor_ts, max_delta_ms=75.0)
    depth_map, depth_summary = project_lidar_to_erp_depth(
        pts,
        erp_hw=erp_hw,
        min_range_m=0.5,
        max_range_m=80.0,
        densify_radius_px=8,
        fill_far_m=1000.0,
    )
    zbuffers = build_ring_zbuffers(
        pts,
        images,
        Ks,
        Ts,
        min_range_m=0.5,
        max_range_m=80.0,
        dilation_px=5,
    )

    H, W = depth_map.shape
    support = np.isfinite(depth_map) & (depth_map < 120.0)
    dirs = erp_dirs_ego((H, W))
    p_ego = dirs * depth_map.astype(np.float32)[..., None]

    in_front_count = np.zeros((H, W), dtype=np.uint8)
    in_bounds_count = np.zeros((H, W), dtype=np.uint8)
    angle_ok_count = np.zeros((H, W), dtype=np.uint8)
    geom_valid_count = np.zeros((H, W), dtype=np.uint8)
    zbuffer_hit_count = np.zeros((H, W), dtype=np.uint8)
    z_mismatch_count = np.zeros((H, W), dtype=np.uint8)
    visible_count = np.zeros((H, W), dtype=np.uint8)
    min_z_resid = np.full((H, W), np.inf, dtype=np.float32)
    per_cam = []

    for idx, (image, K, T, zbuf) in enumerate(zip(images, Ks, Ts, zbuffers)):
        h_img, w_img = image.shape[:2]
        r_cam_ego = T[:3, :3].T
        t_ego_cam = T[:3, 3].astype(np.float32)
        p_cam = (p_ego - t_ego_cam[None, None, :]) @ r_cam_ego.T.astype(np.float32)
        z = p_cam[..., 2]
        in_front = z > 1e-6
        z_safe = np.where(in_front, z, 1.0)
        u_img = K[0, 0] * (p_cam[..., 0] / z_safe) + K[0, 2]
        v_img = K[1, 1] * (p_cam[..., 1] / z_safe) + K[1, 2]
        margin = 0.5
        in_bounds = (
            (u_img >= margin)
            & (u_img <= w_img - 1 - margin)
            & (v_img >= margin)
            & (v_img <= h_img - 1 - margin)
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
        visible = geom_valid & z_match
        z_mismatch = has_zbuf & (~z_match)

        in_front_count += (support & in_front).astype(np.uint8)
        in_bounds_count += (support & in_front & in_bounds).astype(np.uint8)
        angle_ok_count += (support & in_front & in_bounds & angle_ok).astype(np.uint8)
        geom_valid_count += geom_valid.astype(np.uint8)
        zbuffer_hit_count += has_zbuf.astype(np.uint8)
        z_mismatch_count += z_mismatch.astype(np.uint8)
        visible_count += visible.astype(np.uint8)
        min_z_resid = np.minimum(min_z_resid, np.where(has_zbuf, resid.astype(np.float32), np.inf))
        per_cam.append(
            {
                "cam_index": int(idx),
                "geom_valid_pixels": int(geom_valid.sum()),
                "zbuffer_hit_pixels": int(has_zbuf.sum()),
                "z_mismatch_pixels": int(z_mismatch.sum()),
                "visible_pixels": int(visible.sum()),
            }
        )

    no_surface = source_valid & (~support)
    visible_ge2 = source_valid & support & (visible_count >= 2)
    single_visible = source_valid & support & (visible_count == 1)
    no_geom = source_valid & support & (visible_count == 0) & (geom_valid_count == 0)
    no_zbuf = source_valid & support & (visible_count == 0) & (geom_valid_count > 0) & (zbuffer_hit_count == 0)
    z_conflict = source_valid & support & (visible_count == 0) & (z_mismatch_count > 0)
    mixed_no_visible = source_valid & support & (visible_count == 0) & (~no_geom) & (~no_zbuf) & (~z_conflict)

    cause = np.full((H, W), 255, dtype=np.uint8)
    cause[no_surface] = 20
    cause[no_geom] = 41
    cause[no_zbuf] = 42
    cause[z_conflict] = 43
    cause[mixed_no_visible] = 44
    cause[single_visible] = 1
    cause[visible_ge2] = 0
    cause[boundary & visible_ge2] = 60

    repairability = np.zeros((H, W), dtype=np.uint8)
    target = source_valid & seam_band
    repairability[target & visible_ge2 & (~boundary)] = 1
    repairability[target & single_visible & (~boundary)] = 2
    repairability[target & no_surface & (~boundary)] = 3
    repairability[target & (no_geom | no_zbuf | z_conflict | mixed_no_visible)] = 5
    repairability[target & boundary] = 5

    z_cm = np.where(np.isfinite(min_z_resid), np.clip(min_z_resid * 100.0, 0, 65535), 65535).astype(np.uint16)

    maps_u8 = {
        "z_cause_primary_map": cause,
        "camera_geom_valid_count_map": geom_valid_count,
        "camera_zbuffer_hit_count_map": zbuffer_hit_count,
        "camera_z_mismatch_count_map": z_mismatch_count,
        "camera_visible_count_map": visible_count,
        "in_bounds_count_map": in_bounds_count,
        "angle_ok_count_map": angle_ok_count,
        "source_boundary_risk_proxy_map": boundary.astype(np.uint8) * 255,
        "seam_band_mask": seam_band.astype(np.uint8) * 255,
        "z_repairability_map": repairability,
    }
    for name, arr in maps_u8.items():
        save_u8(out_dir / f"{run_name}_{name}.png", arr)
    save_u16(out_dir / f"{run_name}_z_residual_min_cm_u16.png", z_cm)

    cause_palette = {
        0: (70, 220, 120),
        1: (230, 220, 70),
        20: (245, 170, 60),
        41: (160, 120, 220),
        42: (240, 110, 55),
        43: (210, 70, 230),
        44: (90, 170, 230),
        60: (255, 70, 95),
        255: (60, 64, 72),
    }
    repair_palette = {
        0: (50, 85, 150),
        1: (70, 220, 120),
        2: (230, 215, 70),
        3: (245, 170, 60),
        5: (255, 75, 95),
    }
    cause_viz = colorize(cause, cause_palette)
    repair_viz = colorize(repairability, repair_palette)
    geom_viz = _heatmap_u8(np.clip(geom_valid_count.astype(np.float32) / 7.0, 0.0, 1.0))
    hit_viz = _heatmap_u8(np.clip(zbuffer_hit_count.astype(np.float32) / 7.0, 0.0, 1.0))
    mismatch_viz = _heatmap_u8(np.clip(z_mismatch_count.astype(np.float32) / 7.0, 0.0, 1.0))
    visible_viz = _heatmap_u8(np.clip(visible_count.astype(np.float32) / 3.0, 0.0, 1.0))
    z_resid_viz = _heatmap_u8(np.clip(np.where(np.isfinite(min_z_resid), min_z_resid, 3.0) / 3.0, 0.0, 1.0))
    depth_viz = visualize_depth_map(depth_map, log_clip_m=80.0)
    save_u8(out_dir / f"{run_name}_z_cause_primary_viz.png", cause_viz)
    save_u8(out_dir / f"{run_name}_z_repairability_viz.png", repair_viz)
    save_u8(out_dir / f"{run_name}_z_residual_min_viz.png", z_resid_viz)

    review = _stack_named(
        [
            ("hard_select control", _resize_w(hard, 768)),
            ("z_cause_primary evidence codes", _resize_w(cause_viz, 768)),
            ("geom_valid_count", _resize_w(geom_viz, 768)),
            ("zbuffer_hit_count", _resize_w(hit_viz, 768)),
            ("z_mismatch_count", _resize_w(mismatch_viz, 768)),
            ("visible_count", _resize_w(visible_viz, 768)),
            ("min_z_residual heat <=3m", _resize_w(z_resid_viz, 768)),
            ("z_repairability policy", _resize_w(repair_viz, 768)),
            ("lidar_depth", _resize_w(depth_viz, 768)),
        ]
    )
    _save_rgb(out_dir / f"{run_name}_z_visibility_review_768.jpg", review, quality=88)

    seam_denom = source_valid & seam_band
    supported_no_visible = source_valid & support & (visible_count == 0)
    residual_vals = min_z_resid[np.isfinite(min_z_resid) & seam_denom]
    stats = {
        "case": run_name,
        "status": "z_cause_maps_complete",
        "anchor_idx": int(anchor_idx),
        "anchor_ts_ns": int(anchor_ts),
        "lidar_sweep_ts_ns": int(sweep_ts),
        "lidar_delta_ms": float(lidar_delta_ms),
        "counts": {
            "z_cause_primary": unique_counts(cause),
            "z_repairability": unique_counts(repairability),
        },
        "fractions": {
            "seam_source_valid_frac": frac(source_valid, seam_band),
            "seam_lidar_support_frac": frac(support, seam_denom),
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
        },
        "z_residual_min_m_seam": {
            "n": int(residual_vals.size),
            "mean": float(residual_vals.mean()) if residual_vals.size else None,
            "p50": float(np.percentile(residual_vals, 50)) if residual_vals.size else None,
            "p90": float(np.percentile(residual_vals, 90)) if residual_vals.size else None,
            "p95": float(np.percentile(residual_vals, 95)) if residual_vals.size else None,
        },
        "per_cam": per_cam,
        "seam": seam_diag,
        "depth_summary": depth_summary,
        "map_policy": {
            "0": "multi_source_visible",
            "1": "single_source_visible",
            "20": "no_target_surface_support",
            "41": "no_camera_geom_valid",
            "42": "no_raw_camera_zbuffer_support",
            "43": "z_mismatch_or_occlusion_conflict",
            "44": "mixed_no_visible_source",
            "60": "source_boundary_risk_proxy",
            "255": "invalid_or_unclassified",
        },
        "claim_boundary": [
            "z-cause maps are raw projection/zbuffer evidence only",
            "no RGB repair was created",
            "source-boundary is a proxy, not semantic protected mask",
        ],
        "outputs": {
            "review": f"{run_name}_z_visibility_review_768.jpg",
            **{name: f"{run_name}_{name}.png" for name in REQUIRED_MAPS},
        },
        "runtime_s": round(time.time() - t0, 3),
    }
    (out_dir / f"{run_name}_z_cause_breakdown.json").write_text(json.dumps(json_safe(stats), indent=2), encoding="utf-8")
    return stats


def aggregate(diags):
    import numpy as np

    def mean_frac(key):
        vals = [d["fractions"].get(key) for d in diags]
        vals = [float(v) for v in vals if isinstance(v, (int, float))]
        return float(np.mean(vals)) if vals else None

    by_case = {d["case"]: d["fractions"] for d in diags}
    bmw = by_case.get("02a00399_a000_bmw", {})
    clean = by_case.get("0bae3b5e_a030_clean_far", {})
    diff = {}
    for key in [
        "seam_no_surface_frac",
        "seam_no_camera_geom_valid_frac",
        "seam_no_raw_zbuffer_support_frac",
        "seam_z_mismatch_conflict_frac",
        "seam_single_visible_frac",
        "seam_visible_ge2_frac",
        "seam_source_boundary_proxy_frac",
    ]:
        if isinstance(bmw.get(key), (int, float)) and isinstance(clean.get(key), (int, float)):
            diff[key + "_bmw_minus_clean"] = float(bmw[key] - clean[key])
    return {
        "status": "z_cause_maps_complete" if len(diags) == len(CASES) and all(d.get("status") == "z_cause_maps_complete" for d in diags) else "z_cause_maps_incomplete",
        "n_cases": len(diags),
        "by_case": by_case,
        "mean": {key: mean_frac(key) for key in [
            "seam_no_surface_frac",
            "seam_no_camera_geom_valid_frac",
            "seam_no_raw_zbuffer_support_frac",
            "seam_z_mismatch_conflict_frac",
            "seam_single_visible_frac",
            "seam_visible_ge2_frac",
        ]},
        "bmw_minus_clean": diff,
    }


def main():
    t0 = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_phase4b_start",
        "scope": {
            "fixed_cases_only": CASES,
            "z_visibility_cause_only": True,
            "rgb_repair_created": False,
            "a100_required": False,
            "model_inference": False,
            "vggt": False,
            "dit_flux_generation": False,
            "source_replacement": False,
            "bounded_dependency_bootstrap": "av2>=0.3 only if missing",
        },
        "paths": {"remote_out": str(REMOTE_OUT), "av2_root_exists": AV2_ROOT.exists()},
    }
    workdir = find_workdir()
    result["workdir"] = str(workdir) if workdir else None
    if workdir is None:
        result["status"] = "blocked_remote_script_missing"
        REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("DB64_PHASE4B_JSON_BEGIN")
        print(json.dumps(result, ensure_ascii=False))
        print("DB64_PHASE4B_JSON_END")
        return

    sys.path.insert(0, str(workdir / "scripts" / "phase3"))
    sys.path.insert(0, str(workdir / "code"))

    dep = {"name": "av2", "import_before": False, "install_attempted": False, "import_after": False}
    try:
        import av2  # noqa: F401
        dep["import_before"] = True
        dep["import_after"] = True
    except Exception as exc:
        dep["import_before_error"] = repr(exc)
        dep["install_attempted"] = True
        dep_t0 = time.time()
        proc_dep = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=900,
        )
        dep["install_returncode"] = int(proc_dep.returncode)
        dep["install_duration_s"] = round(time.time() - dep_t0, 2)
        dep["install_stdout_tail"] = tail(proc_dep.stdout, 3000)
        dep["install_stderr_tail"] = tail(proc_dep.stderr, 3000)
        try:
            import av2  # noqa: F401
            dep["import_after"] = True
        except Exception as exc_after:
            dep["import_after_error"] = repr(exc_after)
    result["dependency"] = dep
    if not dep.get("import_after"):
        result["status"] = "blocked_missing_av2_after_bootstrap"
        result["runtime_s"] = round(time.time() - t0, 2)
        REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("DB64_PHASE4B_JSON_BEGIN")
        print(json.dumps(result, ensure_ascii=False))
        print("DB64_PHASE4B_JSON_END")
        return

    os.chdir(str(workdir))
    diags = []
    errors = []
    for case in CASES:
        try:
            diags.append(one_case(case, AV2_ROOT, REMOTE_OUT))
        except Exception as exc:
            errors.append({"case": case, "error": repr(exc)})
    summary = aggregate(diags)
    summary["errors"] = errors
    summary["status"] = "z_cause_maps_complete" if summary["status"] == "z_cause_maps_complete" and not errors else "z_cause_maps_incomplete_or_failed"
    (REMOTE_OUT / "batch_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")

    outputs = {"batch_summary": file_row(REMOTE_OUT / "batch_summary.json")}
    for run_name in RUN_NAMES:
        case_dir = REMOTE_OUT / run_name
        outputs[run_name] = {
            "diagnostics": file_row(case_dir / f"{run_name}_z_cause_breakdown.json"),
            "review": file_row(case_dir / f"{run_name}_z_visibility_review_768.jpg"),
        }
        for name in REQUIRED_MAPS:
            outputs[run_name][name] = file_row(case_dir / f"{run_name}_{name}.png")

    result["batch_summary"] = summary
    result["outputs"] = outputs
    result["status"] = "db64_phase4b_z_visibility_completed" if summary["status"] == "z_cause_maps_complete" else "db64_phase4b_z_visibility_failed_or_blocked"
    result["runtime_s"] = round(time.time() - t0, 2)
    REMOTE_RESULT.write_text(json.dumps(json_safe(result), indent=2), encoding="utf-8")
    print("DB64_PHASE4B_JSON_BEGIN")
    print(json.dumps(json_safe(result), ensure_ascii=False))
    print("DB64_PHASE4B_JSON_END")


if __name__ == "__main__":
    main()
'''


def remote_bash() -> str:
    code_b64 = base64.b64encode(remote_python().encode("utf-8")).decode("ascii")
    return (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{code_b64}').decode('utf-8')\n"
        "exec(compile(code, '<db64_phase4b_z_visibility_remote>', 'exec'))\n"
        "PY"
    )


def parse_json_from_log(log_tail: str) -> dict[str, Any] | None:
    if "DB64_PHASE4B_JSON_BEGIN" not in log_tail or "DB64_PHASE4B_JSON_END" not in log_tail:
        return None
    body = log_tail.split("DB64_PHASE4B_JSON_BEGIN", 1)[1].split("DB64_PHASE4B_JSON_END", 1)[0].strip()
    return json.loads(body)


def fetch_outputs(client: ColabClient) -> dict[str, Any]:
    FETCH_DIR.mkdir(parents=True, exist_ok=True)
    items: list[tuple[str, str, Path, int]] = [
        ("batch_summary", REMOTE_SUMMARY, LOCAL_SUMMARY, 16),
    ]
    for run_name in RUN_NAMES:
        base = REMOTE_OUT + "/" + run_name + "/" + run_name
        local_case = FETCH_DIR / run_name
        items.extend(
            [
                (f"{run_name}_diagnostics", base + "_z_cause_breakdown.json", local_case / f"{run_name}_z_cause_breakdown.json", 16),
                (f"{run_name}_review", base + "_z_visibility_review_768.jpg", local_case / f"{run_name}_z_visibility_review_768.jpg", 40),
                (f"{run_name}_z_cause_primary_viz", base + "_z_cause_primary_viz.png", local_case / f"{run_name}_z_cause_primary_viz.png", 16),
                (f"{run_name}_z_repairability_viz", base + "_z_repairability_viz.png", local_case / f"{run_name}_z_repairability_viz.png", 16),
                (f"{run_name}_z_residual_min_viz", base + "_z_residual_min_viz.png", local_case / f"{run_name}_z_residual_min_viz.png", 16),
            ]
        )
        for name in REQUIRED_MAPS:
            items.append((f"{run_name}_{name}", base + f"_{name}.png", local_case / f"{run_name}_{name}.png", 16))

    fetched: dict[str, Any] = {}
    for key, remote_path, local_path, max_mb in items:
        raw = client.read_file(remote_path, max_size_mb=max_mb)
        if raw is None:
            fetched[key] = {"fetched": False, "path": rel(local_path)}
            continue
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(raw)
        row: dict[str, Any] = {"fetched": True, "path": rel(local_path), "bytes": int(local_path.stat().st_size)}
        if local_path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            try:
                with Image.open(local_path) as img:
                    row["size"] = list(img.size)
            except Exception as exc:
                row["image_error"] = repr(exc)
        fetched[key] = row
    return fetched


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=(236, 236, 236), size=15) -> None:
    draw.text(xy, str(text), fill=fill, font=font(size))


def draw_wrapped(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, chars: int, fill=(236, 236, 236), size: int = 14) -> int:
    for line in wrap(str(text), width=chars, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + 6
    return y


def paste_thumb(board: Image.Image, path: Path, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    if not path.exists():
        draw.rectangle(box, outline=(100, 100, 100), fill=(34, 36, 42))
        draw_wrapped(draw, x0 + 12, y0 + 12, f"missing: {rel(path)}", 44, fill=(255, 170, 145), size=14)
        return
    with Image.open(path) as img:
        thumb = img.convert("RGB")
        thumb.thumbnail((x1 - x0, y1 - y0))
        px = x0 + ((x1 - x0) - thumb.width) // 2
        py = y0 + ((y1 - y0) - thumb.height) // 2
        board.paste(thumb, (px, py))
        draw.rectangle((px, py, px + thumb.width, py + thumb.height), outline=(185, 185, 185))


def fmt(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return "n/a"


def write_board(manifest: dict[str, Any]) -> None:
    summary = manifest.get("aggregate") or {}
    board = Image.new("RGB", (1900, 1400), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 24), "DB64 Phase4b z-visibility cause instrumentation", size=27)
    draw_text(draw, (28, 60), "CPU Colab, fixed two cases. Raw projection/z-buffer cause maps only; no RGB repair, no VGGT/model.", fill=(218, 224, 235), size=15)

    y = 100
    lines = [
        f"status={manifest['status']} run_ok={manifest['decision']['run_ok']} complete_maps={manifest['decision']['complete_required_maps']} secret_hits={manifest['strict_secret_scan']['hit_count']}",
        f"runtime={manifest['runtime']['status'].get('runtime_type')} job_state={manifest['job'].get('state')} exit={manifest['job'].get('exit_code')}",
        f"mean no_surface={fmt((summary.get('mean') or {}).get('seam_no_surface_frac'))} no_geom={fmt((summary.get('mean') or {}).get('seam_no_camera_geom_valid_frac'))} no_zbuf={fmt((summary.get('mean') or {}).get('seam_no_raw_zbuffer_support_frac'))} z_conflict={fmt((summary.get('mean') or {}).get('seam_z_mismatch_conflict_frac'))}",
    ]
    for line in lines:
        y = draw_wrapped(draw, 36, y, "- " + line, 145, size=14)

    diff = summary.get("bmw_minus_clean") or {}
    y += 8
    draw_text(draw, (28, y), "BMW Minus Clean", size=20)
    y += 28
    for key in [
        "seam_no_surface_frac_bmw_minus_clean",
        "seam_no_camera_geom_valid_frac_bmw_minus_clean",
        "seam_no_raw_zbuffer_support_frac_bmw_minus_clean",
        "seam_z_mismatch_conflict_frac_bmw_minus_clean",
        "seam_single_visible_frac_bmw_minus_clean",
        "seam_visible_ge2_frac_bmw_minus_clean",
    ]:
        y = draw_wrapped(draw, 36, y, f"- {key}={fmt(diff.get(key))}", 145, fill=(224, 232, 255), size=12)

    y += 8
    draw_text(draw, (28, y), "Boundary", size=20)
    y += 28
    for line in [
        "z-cause maps are raw projection/z-buffer evidence, not semantic layer truth",
        "source-boundary risk is still only a proxy, not object/lane/curb protected masks",
        "no repair image or source replacement was created",
    ]:
        y = draw_wrapped(draw, 36, y, "- " + line, 145, fill=(255, 235, 185), size=13)

    x0, x1 = 28, 940
    x2, x3 = 970, 1870
    paste_thumb(board, FETCH_DIR / RUN_NAMES[0] / f"{RUN_NAMES[0]}_z_visibility_review_768.jpg", (x0, 500, x1, 1320))
    draw_text(draw, (x0, 470), "BMW z-cause review", size=18)
    paste_thumb(board, FETCH_DIR / RUN_NAMES[1] / f"{RUN_NAMES[1]}_z_visibility_review_768.jpg", (x2, 500, x3, 1320))
    draw_text(draw, (x2, 470), "Clean z-cause review", size=18)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    status = client.get("/status", timeout=180)
    submit = client.post(
        "/exec",
        {"cmd": ["bash", "-lc", remote_bash()], "cwd": "/content", "timeout_s": 3600},
        timeout=180,
    )
    job_id = submit["job_id"]
    job = poll_job(client, job_id, timeout_s=3600)

    remote_result: dict[str, Any] | None = None
    raw = client.read_file(REMOTE_RESULT, max_size_mb=24)
    if raw is not None:
        remote_result = json.loads(raw.decode("utf-8"))
    if remote_result is None:
        remote_result = parse_json_from_log(job.get("log_tail", ""))
    if remote_result is None:
        remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    LOCAL_REMOTE_RESULT.write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")

    fetched = fetch_outputs(client)
    batch_summary = load_json_if_exists(LOCAL_SUMMARY)
    aggregate = batch_summary or remote_result.get("batch_summary") or {}
    run_ok = bool(remote_result.get("status") == "db64_phase4b_z_visibility_completed")
    complete_required_maps = bool((aggregate or {}).get("status") == "z_cause_maps_complete")

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_phase4b_z_visibility_cause",
        "accepted_evidence_type": "raw_projection_zbuffer_cause_maps",
        "scope": {
            "remote_status_used": True,
            "remote_exec_used": True,
            "exec_count": 1,
            "fixed_cases_only": CASES,
            "a100_used_or_needed": False,
            "model_inference_used": False,
            "vggt_used": False,
            "dit_flux_generation_used": False,
            "source_replacement_used": False,
            "rgb_repair_created": False,
            "semantic_protected_mask_created": False,
            "red_promotion": False,
        },
        "runtime": {
            "secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file",
            "status": safe_status(status),
        },
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail"}}),
        "dependency": remote_result.get("dependency"),
        "remote_result": rel(LOCAL_REMOTE_RESULT),
        "remote_status": remote_result.get("status"),
        "aggregate": aggregate,
        "fetched_outputs": fetched,
        "output_location": rel(OUT_DIR),
        "drive_output_location": "results/layered_target_raycaster/db64_ltr_v0/phase4b_z_visibility_cause/",
        "decision": {
            "run_ok": run_ok,
            "complete_required_maps": complete_required_maps,
            "accepted_as_z_cause_evidence": bool(run_ok and complete_required_maps),
            "accepted_as_repair": False,
            "accepted_as_source_truth": False,
            "accepted_as_semantic_protected_mask": False,
            "kill_criteria_hit": not bool(run_ok and complete_required_maps),
            "a100_needed_now": False,
        },
        "claim_boundary": [
            "Phase4b z-cause maps are raw projection/z-buffer evidence only.",
            "No RGB repair or source replacement was created.",
            "Source-boundary remains a proxy; semantic object/lane/curb protected masks are still missing.",
            "Next repair-related work must still pass protected-mask and continuous-surface gates.",
        ],
    }
    scan_text = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    write_board(manifest)
    manifest["outputs"] = {"board": rel(BOARD), "manifest": rel(MANIFEST), "batch_summary": rel(LOCAL_SUMMARY)}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "run_ok": run_ok,
                "complete_required_maps": complete_required_maps,
                "secret_hits": manifest["strict_secret_scan"]["hit_count"],
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
