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
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0" / "phase3_sidecar_instrumentation"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db64_ltr_v0/phase3_sidecar_instrumentation"
REMOTE_RESULT = REMOTE_OUT + "/db64_phase3_sidecar_remote_result.json"
REMOTE_SUMMARY = REMOTE_OUT + "/batch_summary.json"

LOCAL_REMOTE_RESULT = OUT_DIR / "db64_phase3_sidecar_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "db64_phase3_sidecar_batch_summary.json"
MANIFEST = OUT_DIR / "db64_ltr_v0_phase3_sidecar_manifest.json"
BOARD = OUT_DIR / "db64_ltr_v0_phase3_sidecar_board.jpg"
FETCH_DIR = OUT_DIR / "fetch"

CASES = ["02a00399:0:bmw", "0bae3b5e:30:clean_far"]
RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]
REQUIRED_SIDECARS = [
    "source_id_map",
    "visibility_count_map",
    "lidar_support_map",
    "risk_map",
    "unknown_mask",
    "disocclusion_mask",
    "layer_id_map",
    "operator_map",
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
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if str(key).lower() in {"token", "authorization", "headers"}:
                out[key] = "<redacted>"
            else:
                out[key] = sanitize(value)
        return out
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

REMOTE_OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db64_ltr_v0/phase3_sidecar_instrumentation")
REMOTE_RESULT = REMOTE_OUT / "db64_phase3_sidecar_remote_result.json"
AV2_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [
    Path("/content/waymo2panorama"),
    Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama"),
]
CASES = ["02a00399:0:bmw", "0bae3b5e:30:clean_far"]
RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]
REQUIRED_SIDECARS = [
    "source_id_map",
    "visibility_count_map",
    "lidar_support_map",
    "risk_map",
    "unknown_mask",
    "disocclusion_mask",
    "layer_id_map",
    "operator_map",
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
        if not np.isfinite(val):
            return None
        return val
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


def map_counts(arr):
    import numpy as np
    vals, counts = np.unique(arr.reshape(-1), return_counts=True)
    return {str(int(v)): int(c) for v, c in zip(vals, counts)}


def frac(mask, denom_mask=None):
    import numpy as np
    m = np.asarray(mask).astype(bool)
    if denom_mask is None:
        return float(m.mean())
    d = np.asarray(denom_mask).astype(bool)
    den = int(d.sum())
    if den <= 0:
        return None
    return float((m & d).sum() / den)


def save_u8(path, arr):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype("uint8")).save(path)


def colorize_label(arr, palette, unknown=(38, 40, 45)):
    import numpy as np
    out = np.zeros((*arr.shape, 3), dtype=np.uint8)
    out[:] = np.array(unknown, dtype=np.uint8)
    for key, color in palette.items():
        out[arr == int(key)] = np.array(color, dtype=np.uint8)
    return out


def mask_rgb(mask, color=(255, 255, 255)):
    import numpy as np
    out = np.zeros((*mask.shape, 3), dtype=np.uint8)
    out[mask.astype(bool)] = np.array(color, dtype=np.uint8)
    return out


def source_boundary(label, valid, seam_band):
    import cv2
    import numpy as np
    label = label.astype(np.int16)
    valid = valid.astype(bool)
    boundary = np.zeros(label.shape, dtype=bool)
    diff_x = (label[:, 1:] != label[:, :-1]) & valid[:, 1:] & valid[:, :-1]
    boundary[:, 1:] |= diff_x
    boundary[:, :-1] |= diff_x
    diff_y = (label[1:, :] != label[:-1, :]) & valid[1:, :] & valid[:-1, :]
    boundary[1:, :] |= diff_y
    boundary[:-1, :] |= diff_y
    boundary &= seam_band.astype(bool)
    kernel = np.ones((5, 5), np.uint8)
    return cv2.dilate(boundary.astype(np.uint8), kernel, iterations=1).astype(bool)


def stats_for_case(
    sphere_valid,
    seam_band,
    support,
    visible_count,
    unknown_mask,
    disocclusion_mask,
    boundary_risk,
    risk_map,
    layer_id_map,
    operator_map,
):
    import numpy as np
    supported_visible = sphere_valid & support & (visible_count > 0)
    visible_ge2 = sphere_valid & support & (visible_count >= 2)
    seam = seam_band.astype(bool)
    risk_vals = risk_map[sphere_valid] if np.any(sphere_valid) else risk_map.reshape(-1)
    seam_risk_vals = risk_map[sphere_valid & seam] if np.any(sphere_valid & seam) else np.array([], dtype=np.uint8)
    return {
        "source_valid_frac": frac(sphere_valid),
        "seam_band_frac": frac(seam),
        "lidar_support_frac_total": frac(support, sphere_valid),
        "lidar_support_frac_seam": frac(support, sphere_valid & seam),
        "visible_any_frac_total": frac(supported_visible, sphere_valid),
        "visible_any_frac_seam": frac(supported_visible, sphere_valid & seam),
        "visible_ge2_frac_total": frac(visible_ge2, sphere_valid),
        "visible_ge2_frac_seam": frac(visible_ge2, sphere_valid & seam),
        "unknown_frac_total": frac(unknown_mask, sphere_valid),
        "unknown_frac_seam": frac(unknown_mask, sphere_valid & seam),
        "disocclusion_frac_total": frac(disocclusion_mask, sphere_valid),
        "disocclusion_frac_seam": frac(disocclusion_mask, sphere_valid & seam),
        "boundary_risk_frac_total": frac(boundary_risk, sphere_valid),
        "boundary_risk_frac_seam": frac(boundary_risk, sphere_valid & seam),
        "risk_mean_total_u8": float(np.mean(risk_vals)) if risk_vals.size else None,
        "risk_p90_total_u8": float(np.percentile(risk_vals, 90)) if risk_vals.size else None,
        "risk_mean_seam_u8": float(np.mean(seam_risk_vals)) if seam_risk_vals.size else None,
        "risk_p90_seam_u8": float(np.percentile(seam_risk_vals, 90)) if seam_risk_vals.size else None,
        "layer_id_counts": map_counts(layer_id_map),
        "operator_counts": map_counts(operator_map),
    }


def one_case(case_spec, av2_root, out_root):
    import cv2
    import numpy as np
    from seam_confidence_map import _crop_stack, _default_crops, _heatmap_u8, _resize_w, _save_rgb, _stack_named
    from depth_visibility_seam_probe import _parse_case
    from test_lidar_zbuffer_seam import _seam_masks, _winner_label
    from waymo2panorama.blending.hard_hdr_of import hard_select
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.depth.lidar_to_erp_depth import load_lidar_sweep_nearest_to_ts, project_lidar_to_erp_depth, visualize_depth_map
    from waymo2panorama.projection.lidar_zbuffer_layer import build_ring_zbuffers, render_lidar_surface_to_erp
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
    sphere_label, sphere_valid = _winner_label(weights)
    seam_band, seam_core, seam_diag = _seam_masks(weights, band_half_width=48, core_half_width=2)

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
    lidar_render = render_lidar_surface_to_erp(
        images,
        Ks,
        Ts,
        depth_map,
        zbuffers,
        depth_support_max_m=120.0,
        min_cam_cos=0.03,
        z_tolerance_abs_m=0.9,
        z_tolerance_rel=0.05,
    )

    support = lidar_render.support_mask.astype(bool)
    visible_count = lidar_render.visible_count.astype(np.uint8)
    lidar_label, lidar_valid = _winner_label(lidar_render.weights)
    source_id_map = np.full(sphere_label.shape, 255, dtype=np.uint8)
    source_id_map[lidar_valid] = np.clip(lidar_label[lidar_valid], 0, 254).astype(np.uint8)

    hard_source_id_map = np.full(sphere_label.shape, 255, dtype=np.uint8)
    hard_source_id_map[sphere_valid] = np.clip(sphere_label[sphere_valid], 0, 254).astype(np.uint8)

    no_lidar_surface = sphere_valid & (~support)
    supported_visible = sphere_valid & support & (visible_count > 0)
    supported_not_visible = sphere_valid & support & (visible_count == 0)
    unknown_mask = sphere_valid & ((~support) | (visible_count == 0))
    disocclusion_mask = supported_not_visible
    boundary_risk = source_boundary(sphere_label, sphere_valid, seam_band)

    risk_map = np.zeros(sphere_label.shape, dtype=np.uint8)
    risk_map[~sphere_valid] = 255
    risk_map[supported_visible] = 45
    risk_map[sphere_valid & support & (visible_count == 1)] = 115
    risk_map[no_lidar_surface] = 190
    risk_map[disocclusion_mask] = 230
    risk_map[boundary_risk] = np.maximum(risk_map[boundary_risk], np.uint8(220))

    layer_id_map = np.full(sphere_label.shape, 255, dtype=np.uint8)
    layer_id_map[~sphere_valid] = 0
    layer_id_map[no_lidar_surface] = 2
    layer_id_map[supported_visible] = 1
    layer_id_map[disocclusion_mask] = 4
    layer_id_map[boundary_risk & (~disocclusion_mask)] = 3

    operator_map = np.full(sphere_label.shape, 2, dtype=np.uint8)
    operator_map[sphere_valid & (~seam_band)] = 0
    operator_map[sphere_valid & seam_band & supported_visible] = 1
    operator_map[sphere_valid & seam_band & unknown_mask] = 2
    operator_map[sphere_valid & seam_band & boundary_risk] = 3

    sidecar_arrays = {
        "source_id_map": source_id_map,
        "hard_select_source_id_map": hard_source_id_map,
        "visibility_count_map": visible_count,
        "lidar_support_map": (support.astype(np.uint8) * 255),
        "unknown_mask": (unknown_mask.astype(np.uint8) * 255),
        "disocclusion_mask": (disocclusion_mask.astype(np.uint8) * 255),
        "source_boundary_risk_mask": (boundary_risk.astype(np.uint8) * 255),
        "risk_map": risk_map,
        "layer_id_map": layer_id_map,
        "operator_map": operator_map,
        "seam_band_mask": (seam_band.astype(np.uint8) * 255),
        "seam_core_mask": (seam_core.astype(np.uint8) * 255),
    }
    for name, arr in sidecar_arrays.items():
        save_u8(out_dir / f"{run_name}_{name}.png", arr)

    source_palette = {
        0: (255, 80, 80),
        1: (255, 180, 60),
        2: (240, 240, 70),
        3: (70, 210, 110),
        4: (80, 200, 255),
        5: (90, 110, 255),
        6: (210, 90, 255),
    }
    layer_palette = {
        0: (0, 0, 0),
        1: (70, 220, 120),
        2: (230, 190, 65),
        3: (255, 80, 110),
        4: (70, 210, 240),
        250: (165, 105, 220),
        255: (80, 84, 92),
    }
    operator_palette = {
        0: (55, 95, 170),
        1: (80, 220, 120),
        2: (245, 170, 60),
        3: (255, 70, 95),
    }
    source_viz = colorize_label(source_id_map, source_palette)
    hard_source_viz = colorize_label(hard_source_id_map, source_palette)
    visibility_viz = _heatmap_u8(np.clip(visible_count.astype(np.float32) / 3.0, 0.0, 1.0))
    support_viz = mask_rgb(support, (70, 220, 120))
    unknown_viz = mask_rgb(unknown_mask, (245, 170, 60))
    disocclusion_viz = mask_rgb(disocclusion_mask, (70, 210, 240))
    boundary_viz = mask_rgb(boundary_risk, (255, 70, 95))
    risk_viz = _heatmap_u8(risk_map.astype(np.float32) / 255.0)
    layer_viz = colorize_label(layer_id_map, layer_palette)
    operator_viz = colorize_label(operator_map, operator_palette)
    depth_viz = visualize_depth_map(depth_map, log_clip_m=80.0)

    visual_arrays = {
        "source_id_map_viz": source_viz,
        "hard_select_source_id_map_viz": hard_source_viz,
        "visibility_count_viz": visibility_viz,
        "lidar_support_viz": support_viz,
        "unknown_mask_viz": unknown_viz,
        "disocclusion_mask_viz": disocclusion_viz,
        "source_boundary_risk_viz": boundary_viz,
        "risk_map_viz": risk_viz,
        "layer_id_map_viz": layer_viz,
        "operator_map_viz": operator_viz,
        "lidar_depth_viz": depth_viz,
    }
    for name, arr in visual_arrays.items():
        save_u8(out_dir / f"{run_name}_{name}.png", arr)

    _save_rgb(out_dir / f"{run_name}_hard_select_reference.jpg", hard, quality=90)

    review = _stack_named(
        [
            ("hard_select_reference control only", _resize_w(hard, 768)),
            ("source_id_map LiDAR-visible camera id", _resize_w(source_viz, 768)),
            ("visibility_count_map raw", _resize_w(visibility_viz, 768)),
            ("lidar_support_map", _resize_w(support_viz, 768)),
            ("risk_map policy evidence", _resize_w(risk_viz, 768)),
            ("layer_id_map evidence classes", _resize_w(layer_viz, 768)),
            ("operator_map sidecar decision", _resize_w(operator_viz, 768)),
            ("unknown orange / disocclusion cyan / boundary red", _resize_w(np.maximum(np.maximum(unknown_viz, disocclusion_viz), boundary_viz), 768)),
        ]
    )
    _save_rgb(out_dir / f"{run_name}_sidecar_review_768.jpg", review, quality=88)

    crops = _default_crops(1024, 2048)
    crop_review = _crop_stack(
        [
            ("hard_select", hard),
            ("source_id", source_viz),
            ("visibility_count", visibility_viz),
            ("risk_map", risk_viz),
            ("layer_id", layer_viz),
            ("operator", operator_viz),
            ("unknown", unknown_viz),
            ("disocclusion", disocclusion_viz),
            ("boundary_risk", boundary_viz),
        ],
        crops,
    )
    _save_rgb(out_dir / f"{run_name}_sidecar_crop_review.jpg", crop_review, quality=88)

    stats = stats_for_case(
        sphere_valid=sphere_valid,
        seam_band=seam_band,
        support=support,
        visible_count=visible_count,
        unknown_mask=unknown_mask,
        disocclusion_mask=disocclusion_mask,
        boundary_risk=boundary_risk,
        risk_map=risk_map,
        layer_id_map=layer_id_map,
        operator_map=operator_map,
    )
    outputs = {
        "hard_select_reference": f"{run_name}_hard_select_reference.jpg",
        "sidecar_review": f"{run_name}_sidecar_review_768.jpg",
        "sidecar_crop_review": f"{run_name}_sidecar_crop_review.jpg",
    }
    for name in REQUIRED_SIDECARS:
        outputs[name] = f"{run_name}_{name}.png"
        outputs[name + "_viz"] = f"{run_name}_{name}_viz.png" if name not in {"lidar_support_map"} else f"{run_name}_lidar_support_viz.png"
    outputs["diagnostics"] = f"{run_name}_sidecar_diagnostics.json"
    required_ok = all((out_dir / outputs[name]).exists() for name in REQUIRED_SIDECARS)

    diag = {
        "case": run_name,
        "log_short": short,
        "anchor_idx": int(anchor_idx),
        "anchor_ts_ns": int(anchor_ts),
        "lidar_sweep_ts_ns": int(sweep_ts),
        "lidar_delta_ms": float(lidar_delta_ms),
        "status": "sidecars_complete" if required_ok else "sidecars_incomplete",
        "claim": {
            "sidecar_evidence_only": True,
            "rgb_repair_created": False,
            "phase2_rgb_copy_rejected_diagnostic": True,
            "semantic_layer_truth": False,
            "source_truth_overclaim": False,
        },
        "map_policy": {
            "source_id_map": "argmax of LiDAR-zbuffer per-camera visibility weights; 255 where no visible source",
            "unknown_mask": "hard-select-valid target ray with no admissible LiDAR-supported visible source",
            "disocclusion_mask": "LiDAR-supported target ray with zero zbuffer-visible source cameras; conservative proxy",
            "risk_map": "policy map from source validity, LiDAR support, visibility count, disocclusion proxy, and source-boundary risk",
            "layer_id_map": {
                "0": "invalid/out-of-FOV",
                "1": "lidar_supported_surface_candidate",
                "2": "hard_select_source_only_no_lidar_surface",
                "3": "source-boundary/protected-structure-risk-proxy",
                "4": "possible_disocclusion",
                "250": "mixed_diagnostic_composite_or_reserved",
                "255": "unknown/no_admissible_support",
            },
            "operator_map": {
                "0": "keep_hard_select_control",
                "1": "evidence_supported_no_rgb_edit",
                "2": "unknown_or_disocclusion_abstain",
                "3": "boundary_or_protected_risk_abstain",
            },
        },
        "seam": seam_diag,
        "lidar_depth_summary": depth_summary,
        "lidar_surface_render": lidar_render.diagnostics,
        "sidecar_stats": stats,
        "outputs": outputs,
        "required_sidecars_complete": bool(required_ok),
        "params": {
            "erp_h": 1024,
            "erp_w": 2048,
            "band_half_width": 48,
            "core_half_width": 2,
            "min_range_m": 0.5,
            "max_range_m": 80.0,
            "densify_radius_px": 8,
            "zbuffer_dilation_px": 5,
            "min_cam_cos": 0.03,
            "z_tolerance_abs_m": 0.9,
            "z_tolerance_rel": 0.05,
        },
        "runtime_s": round(time.time() - t0, 3),
    }
    (out_dir / f"{run_name}_sidecar_diagnostics.json").write_text(json.dumps(json_safe(diag), indent=2), encoding="utf-8")
    return diag


def aggregate(diags):
    import numpy as np

    def mean_path(path):
        vals = []
        for diag in diags:
            cur = diag
            for key in path:
                cur = cur.get(key, None) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, (int, float)):
                vals.append(float(cur))
        return float(np.mean(vals)) if vals else None

    by_case = {d["case"]: d["sidecar_stats"] for d in diags}
    bmw = next((d["sidecar_stats"] for d in diags if "02a00399" in d["case"]), None)
    clean = next((d["sidecar_stats"] for d in diags if "0bae3b5e" in d["case"]), None)
    diff = {}
    if bmw and clean:
        for key in [
            "unknown_frac_seam",
            "disocclusion_frac_seam",
            "boundary_risk_frac_seam",
            "risk_mean_seam_u8",
            "risk_p90_seam_u8",
            "visible_any_frac_seam",
            "visible_ge2_frac_seam",
        ]:
            if isinstance(bmw.get(key), (int, float)) and isinstance(clean.get(key), (int, float)):
                diff[key + "_bmw_minus_clean"] = float(bmw[key] - clean[key])
    return {
        "n_cases": len(diags),
        "all_required_sidecars_complete": bool(diags) and all(bool(d.get("required_sidecars_complete")) for d in diags),
        "mean_lidar_support_frac_seam": mean_path(["sidecar_stats", "lidar_support_frac_seam"]),
        "mean_visible_any_frac_seam": mean_path(["sidecar_stats", "visible_any_frac_seam"]),
        "mean_visible_ge2_frac_seam": mean_path(["sidecar_stats", "visible_ge2_frac_seam"]),
        "mean_unknown_frac_seam": mean_path(["sidecar_stats", "unknown_frac_seam"]),
        "mean_disocclusion_frac_seam": mean_path(["sidecar_stats", "disocclusion_frac_seam"]),
        "mean_boundary_risk_frac_seam": mean_path(["sidecar_stats", "boundary_risk_frac_seam"]),
        "mean_risk_mean_seam_u8": mean_path(["sidecar_stats", "risk_mean_seam_u8"]),
        "by_case": by_case,
        "bmw_minus_clean": diff,
    }


def main():
    t0 = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_phase3_sidecar_start",
        "scope": {
            "fixed_cases_only": CASES,
            "sidecar_only": True,
            "rgb_repair_created": False,
            "phase2_rgb_copy_rejected_diagnostic": True,
            "a100_required": False,
            "model_inference": False,
            "vggt": False,
            "dit_flux_generation": False,
            "source_replacement": False,
            "bounded_dependency_bootstrap": "av2>=0.3 only if missing",
        },
        "paths": {
            "remote_out": str(REMOTE_OUT),
            "av2_root_exists": AV2_ROOT.exists(),
        },
    }
    workdir = find_workdir()
    result["workdir"] = str(workdir) if workdir else None
    if workdir is None:
        result["status"] = "blocked_remote_script_missing"
        REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("DB64_PHASE3_JSON_BEGIN")
        print(json.dumps(result, ensure_ascii=False))
        print("DB64_PHASE3_JSON_END")
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
        print("DB64_PHASE3_JSON_BEGIN")
        print(json.dumps(result, ensure_ascii=False))
        print("DB64_PHASE3_JSON_END")
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
    summary["status"] = "sidecars_complete" if summary["all_required_sidecars_complete"] and not errors else "sidecars_incomplete_or_failed"
    (REMOTE_OUT / "batch_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")

    outputs = {"batch_summary": file_row(REMOTE_OUT / "batch_summary.json")}
    for run_name in RUN_NAMES:
        case_dir = REMOTE_OUT / run_name
        outputs[run_name] = {
            "diagnostics": file_row(case_dir / f"{run_name}_sidecar_diagnostics.json"),
            "hard_select_reference": file_row(case_dir / f"{run_name}_hard_select_reference.jpg"),
            "sidecar_review": file_row(case_dir / f"{run_name}_sidecar_review_768.jpg"),
            "sidecar_crop_review": file_row(case_dir / f"{run_name}_sidecar_crop_review.jpg"),
        }
        for name in REQUIRED_SIDECARS:
            outputs[run_name][name] = file_row(case_dir / f"{run_name}_{name}.png")

    result["batch_summary"] = summary
    result["outputs"] = outputs
    result["status"] = "db64_phase3_sidecar_completed" if summary["status"] == "sidecars_complete" else "db64_phase3_sidecar_failed_or_blocked"
    result["runtime_s"] = round(time.time() - t0, 2)
    REMOTE_RESULT.write_text(json.dumps(json_safe(result), indent=2), encoding="utf-8")
    print("DB64_PHASE3_JSON_BEGIN")
    print(json.dumps(json_safe(result), ensure_ascii=False))
    print("DB64_PHASE3_JSON_END")


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
        "exec(compile(code, '<db64_phase3_sidecar_remote>', 'exec'))\n"
        "PY"
    )


def parse_json_from_log(log_tail: str) -> dict[str, Any] | None:
    if "DB64_PHASE3_JSON_BEGIN" not in log_tail or "DB64_PHASE3_JSON_END" not in log_tail:
        return None
    body = log_tail.split("DB64_PHASE3_JSON_BEGIN", 1)[1].split("DB64_PHASE3_JSON_END", 1)[0].strip()
    return json.loads(body)


def fetch_outputs(client: ColabClient) -> dict[str, Any]:
    FETCH_DIR.mkdir(parents=True, exist_ok=True)
    fetched: dict[str, Any] = {}
    items: list[tuple[str, str, Path, int]] = [
        ("batch_summary", REMOTE_SUMMARY, LOCAL_SUMMARY, 16),
    ]
    for run_name in RUN_NAMES:
        base = REMOTE_OUT + "/" + run_name + "/" + run_name
        local_case = FETCH_DIR / run_name
        items.extend(
            [
                (f"{run_name}_diagnostics", base + "_sidecar_diagnostics.json", local_case / f"{run_name}_sidecar_diagnostics.json", 16),
                (f"{run_name}_hard_select_reference", base + "_hard_select_reference.jpg", local_case / f"{run_name}_hard_select_reference.jpg", 20),
                (f"{run_name}_sidecar_review", base + "_sidecar_review_768.jpg", local_case / f"{run_name}_sidecar_review_768.jpg", 40),
                (f"{run_name}_sidecar_crop_review", base + "_sidecar_crop_review.jpg", local_case / f"{run_name}_sidecar_crop_review.jpg", 40),
            ]
        )
        for name in REQUIRED_SIDECARS:
            items.append((f"{run_name}_{name}", base + f"_{name}.png", local_case / f"{run_name}_{name}.png", 12))
            viz_name = "lidar_support_viz" if name == "lidar_support_map" else f"{name}_viz"
            items.append((f"{run_name}_{viz_name}", base + f"_{viz_name}.png", local_case / f"{run_name}_{viz_name}.png", 20))
        for extra in ["hard_select_source_id_map", "source_boundary_risk_mask", "seam_band_mask", "seam_core_mask"]:
            items.append((f"{run_name}_{extra}", base + f"_{extra}.png", local_case / f"{run_name}_{extra}.png", 12))
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
        draw_wrapped(draw, x0 + 12, y0 + 12, f"missing: {rel(path)}", 40, fill=(255, 170, 145), size=14)
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


def dependency_summary(manifest: dict[str, Any]) -> str:
    dep = manifest.get("dependency") or {}
    if not dep:
        return "dependency=not recorded"
    return (
        "av2 "
        f"before={dep.get('import_before')} "
        f"install={dep.get('install_attempted')} "
        f"after={dep.get('import_after')} "
        f"install_rc={dep.get('install_returncode')} "
        f"install_s={dep.get('install_duration_s')}"
    )


def write_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1900, 1600), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 24), "DB64 Phase3 LTR sidecar instrumentation", size=28)
    draw_text(
        draw,
        (28, 62),
        "CPU-only, fixed BMW target + clean control. Sidecar evidence only; no RGB repair. Phase2 RGB copy remains rejected diagnostic.",
        fill=(218, 224, 235),
        size=15,
    )

    decision = manifest["decision"]
    summary = manifest.get("aggregate") or {}
    y = 102
    lines = [
        f"status={manifest['status']} run_ok={decision['run_ok']} complete_maps={decision['complete_required_maps']} a100_needed={decision['a100_needed_now']} secret_hits={manifest['strict_secret_scan']['hit_count']}",
        f"runtime={manifest['runtime']['status'].get('runtime_type')} active_jobs={manifest['runtime']['status'].get('active_jobs')} version={manifest['runtime']['status'].get('version')}",
        f"job state={manifest['job'].get('state')} exit={manifest['job'].get('exit_code')} duration={manifest['job'].get('duration_s')}",
        dependency_summary(manifest),
        f"cases={', '.join(CASES)}",
        f"mean seam support={fmt(summary.get('mean_lidar_support_frac_seam'))} visible_any={fmt(summary.get('mean_visible_any_frac_seam'))} visible_ge2={fmt(summary.get('mean_visible_ge2_frac_seam'))}",
        f"mean seam unknown={fmt(summary.get('mean_unknown_frac_seam'))} disocclusion={fmt(summary.get('mean_disocclusion_frac_seam'))} boundary_risk={fmt(summary.get('mean_boundary_risk_frac_seam'))} risk_mean_u8={fmt(summary.get('mean_risk_mean_seam_u8'))}",
    ]
    for line in lines:
        y = draw_wrapped(draw, 36, y, "- " + line, 150, size=14)

    diff = summary.get("bmw_minus_clean") or {}
    y += 8
    draw_text(draw, (28, y), "BMW Minus Clean Profile", size=20)
    y += 28
    for key in [
        "unknown_frac_seam_bmw_minus_clean",
        "disocclusion_frac_seam_bmw_minus_clean",
        "boundary_risk_frac_seam_bmw_minus_clean",
        "risk_mean_seam_u8_bmw_minus_clean",
        "visible_any_frac_seam_bmw_minus_clean",
    ]:
        y = draw_wrapped(draw, 36, y, f"- {key}={fmt(diff.get(key))}", 120, fill=(235, 235, 205), size=13)

    y += 8
    draw_text(draw, (28, y), "Claim Boundary", size=20)
    y += 28
    for line in [
        "source_id_map is LiDAR-zbuffer visible-source evidence; 255 means no visible source.",
        "layer_id_map is evidence-class policy, not semantic segmentation or source truth.",
        "operator_map is abstain/keep/evidence state only; it does not edit pixels.",
    ]:
        y = draw_wrapped(draw, 36, y, "- " + line, 150, fill=(255, 235, 185), size=13)

    x0, x1 = 28, 940
    x2, x3 = 970, 1870
    paste_thumb(board, FETCH_DIR / RUN_NAMES[0] / f"{RUN_NAMES[0]}_sidecar_review_768.jpg", (x0, 520, x1, 1010))
    draw_text(draw, (x0, 490), "BMW target full sidecar review", size=18)
    paste_thumb(board, FETCH_DIR / RUN_NAMES[1] / f"{RUN_NAMES[1]}_sidecar_review_768.jpg", (x2, 520, x3, 1010))
    draw_text(draw, (x2, 490), "Clean control full sidecar review", size=18)
    paste_thumb(board, FETCH_DIR / RUN_NAMES[0] / f"{RUN_NAMES[0]}_sidecar_crop_review.jpg", (x0, 1080, x1, 1550))
    draw_text(draw, (x0, 1050), "BMW crop review", size=18)
    paste_thumb(board, FETCH_DIR / RUN_NAMES[1] / f"{RUN_NAMES[1]}_sidecar_crop_review.jpg", (x2, 1080, x3, 1550))
    draw_text(draw, (x2, 1050), "Clean crop review", size=18)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if "--board-only" in sys.argv:
        manifest = load_json_if_exists(MANIFEST)
        if manifest is None:
            raise FileNotFoundError(MANIFEST)
        write_board(manifest)
        print(json.dumps({"status": "board_redrawn", "board": rel(BOARD)}, indent=2))
        return

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
    run_ok = bool(remote_result.get("status") == "db64_phase3_sidecar_completed")
    complete_required_maps = bool((aggregate or {}).get("all_required_sidecars_complete"))

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_ltr_v0_phase3_sidecar_instrumentation",
        "accepted_evidence_type": "sidecar_only_target_ray_evidence_instrumentation",
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
            "phase2_rgb_copy_rejected_diagnostic": True,
            "bounded_dependency_bootstrap_allowed": True,
            "red_promotion": False,
            "sidecar_only": True,
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
        "drive_output_location": "results/layered_target_raycaster/db64_ltr_v0/phase3_sidecar_instrumentation/",
        "decision": {
            "run_ok": run_ok,
            "complete_required_maps": complete_required_maps,
            "a100_needed_now": False,
            "accepted_as_repair": False,
            "accepted_as_source_truth": False,
            "accepted_as_semantic_layer_truth": False,
            "accepted_as_sidecar_evidence": bool(run_ok and complete_required_maps),
            "kill_criteria_hit": not bool(run_ok and complete_required_maps),
            "vision_check_required": True,
        },
        "claim_boundary": [
            "DB64 Phase3 is sidecar evidence only.",
            "No RGB repair, renderer, inpainting, source replacement, VGGT, A100, DiT/FLUX, or 3DGS was used.",
            "Hard-select images are controls only.",
            "source_id_map is LiDAR-zbuffer visible-source evidence, not full source truth.",
            "layer_id_map/operator_map/risk_map are policy-bearing evidence maps, not semantic segmentation or repair permission.",
            "Phase2 LiDAR RGB-copy variants remain rejected diagnostic output.",
        ],
    }
    scan_text = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    write_board(manifest)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "run_ok": run_ok,
                "complete_required_maps": complete_required_maps,
                "secret_hits": manifest["strict_secret_scan"]["hit_count"],
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
                "remote_result": rel(LOCAL_REMOTE_RESULT),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
