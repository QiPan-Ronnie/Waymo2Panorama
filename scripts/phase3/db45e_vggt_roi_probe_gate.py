#!/usr/bin/env python
"""DB45e bounded VGGT ROI confidence probe.

This is evidence-only. It runs official VGGT once on the BMW anchor raw
7-camera ring and reduces real confidence fields into ROI diagnostics by
combining them with the existing DB25/DB41 camera-owner summaries. It does not
render, repair, replace sources, or promote RED seam regions.
"""

from __future__ import annotations

import argparse
import base64
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
DB45D = OUT_DIR / "db45d_vggt_setup_smoke_gate_manifest.json"

REMOTE_RESULT = OUT_DIR / "db45e_vggt_remote_roi_probe_result.json"
MANIFEST = OUT_DIR / "db45e_vggt_roi_probe_gate_manifest.json"
BOARD = OUT_DIR / "db45e_vggt_roi_probe_gate_board.jpg"

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
        d = ImageDraw.Draw(canvas)
        d.text((12, 12), f"missing: {path.name}", fill=(220, 120, 120), font=font(14))
        return canvas
    img = Image.open(path).convert("RGB")
    img.thumbnail(size, Image.Resampling.LANCZOS)
    canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    return canvas


def label_tile(path: Path, title: str, size: tuple[int, int]) -> Image.Image:
    title_h = 30
    tile = Image.new("RGB", (size[0], size[1] + title_h), (0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.text((8, 8), title, fill=(255, 255, 255), font=font(14))
    tile.paste(fit_image(path, size), (0, title_h))
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
    "db": "DB-45e",
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
WORK = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db45e_vggt_roi_probe")
RAW_DIR = WORK / "raw_cameras"

def _stat(arr):
    import numpy as np
    arr = np.asarray(arr, dtype=np.float32)
    finite = np.isfinite(arr)
    if not bool(finite.any()):
        return {"valid": 0.0, "mean": None, "med": None, "p10": None, "p90": None, "std": None}
    vals = arr[finite]
    return {
        "valid": round(float(finite.mean()), 6),
        "mean": round(float(vals.mean()), 6),
        "med": round(float(np.percentile(vals, 50)), 6),
        "p10": round(float(np.percentile(vals, 10)), 6),
        "p90": round(float(np.percentile(vals, 90)), 6),
        "std": round(float(vals.std()), 6),
    }

def _views_map(tensor, n_views):
    import numpy as np
    arr = tensor.detach().float().cpu().numpy()
    if arr.ndim >= 4 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 4 and arr.shape[0] == n_views and arr.shape[-1] == 1:
        arr = arr[..., 0]
    elif arr.ndim == 4 and arr.shape[0] == n_views and arr.shape[1] == 1:
        arr = arr[:, 0]
    elif arr.ndim == 4 and arr.shape[1] == n_views and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim != 3 or arr.shape[0] != n_views:
        return None, list(arr.shape)
    return np.asarray(arr, dtype=np.float32), list(arr.shape)

try:
    os.environ["HF_HOME"] = str(HF_HOME)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    WORK.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for p in [
        OFFICIAL_REPO,
        LOCAL_REPO / "code",
        LOCAL_REPO / "scripts" / "phase3",
        LOCAL_REPO,
    ]:
        sys.path.insert(0, str(p))

    import numpy as np
    import torch
    from PIL import Image
    from vggt.models.vggt import VGGT
    from vggt.utils.load_fn import load_and_preprocess_images

    try:
        from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    except Exception:
        import run_a1_streetview_pipeline as a1
        AV2RingLoader = a1.AV2RingLoader
        RING_CAMS_7 = a1.RING_CAMS_7

    loader = AV2RingLoader(DATA_ROOT / UUID)
    timestamps = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(timestamps[ANCHOR])

    image_paths = []
    image_shapes = []
    for idx, cam in enumerate(RING_CAMS_7):
        arr = np.asarray(frame.images[cam])
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        path = RAW_DIR / f"cam_{idx}_{cam}.jpg"
        Image.fromarray(arr).save(path, quality=92)
        image_paths.append(str(path))
        image_shapes.append([idx, cam, int(arr.shape[0]), int(arr.shape[1])])

    OUT["raw_camera_load"] = {
        "ok": True,
        "camera_names": list(RING_CAMS_7),
        "image_shapes_hwc": image_shapes
    }

    device = "cuda" if torch.cuda.is_available() else "cpu"
    OUT["torch"] = {
        "version": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
        "device": device,
        "cuda_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "cuda_free_gb_before": round(torch.cuda.mem_get_info()[0] / 1024**3, 2) if torch.cuda.is_available() else None,
    }

    t0 = time.time()
    model = VGGT.from_pretrained(MODEL_ID).to(device).eval()
    images = load_and_preprocess_images(image_paths).to(device)
    autocast_ctx = torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16) if device == "cuda" else contextlib.nullcontext()
    with torch.no_grad(), autocast_ctx:
        predictions = model(images)
    if torch.cuda.is_available():
        torch.cuda.synchronize()

    conf_fields = {}
    field_candidates = {
        "depth_conf": ["depth_conf"],
        "world_points_conf": ["world_points_conf", "point_conf", "points_conf", "conf"]
    }
    for canonical, keys in field_candidates.items():
        for key in keys:
            if key not in predictions:
                continue
            arr, shape = _views_map(predictions[key], len(image_paths))
            if arr is None:
                conf_fields[canonical] = {"source_key": key, "shape_after_squeeze": shape, "map_error": "not_view_hw_map"}
                break
            conf_fields[canonical] = {
                "source_key": key,
                "shape_vhw": list(arr.shape),
                "global": _stat(arr),
                "per_cam": {str(i): _stat(arr[i]) for i in range(arr.shape[0])}
            }
            break

    OUT["vggt"] = {
        "inference_ok": True,
        "model_id": MODEL_ID,
        "duration_s": round(time.time() - t0, 2),
        "input_tensor_shape": list(images.shape),
        "prediction_keys": sorted([str(k) for k in predictions.keys()]),
        "confidence_fields": conf_fields,
        "cuda_free_gb_after": round(torch.cuda.mem_get_info()[0] / 1024**3, 2) if torch.cuda.is_available() else None,
    }
except Exception as exc:
    OUT["error"] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "trace_tail": traceback.format_exc()[-1800:]
    }
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "db45e_remote_roi_probe_result.json").write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print("DB45E_JSON_BEGIN")
    print(json.dumps(OUT, sort_keys=True, separators=(",", ":")))
    print("DB45E_JSON_END")
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
    match = re.search(r"DB45E_JSON_BEGIN\s*(\{.*\})\s*DB45E_JSON_END", log, re.S)
    if not match:
        return {
            "db": "DB-45e",
            "error": {
                "type": "MissingRemoteJson",
                "message": "Remote job did not print DB45E_JSON markers in the returned log tail.",
                "log_tail": log[-3500:],
            },
        }
    return json.loads(match.group(1))


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
                "db": "DB-45e",
                "error": {"type": "LocalPollTimeout", "message": f"Timed out waiting for job {job_id}."},
                "colab_job": {"job_id": job_id, "state": state.get("state")},
            }
            REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result


def owner_fractions(summary: dict[str, Any]) -> list[dict[str, Any]]:
    counts = {}
    for key, value in summary.get("camera_label_counts", {}).items():
        try:
            counts[int(key)] = int(value)
        except (TypeError, ValueError):
            continue
    total = max(1, sum(counts.values()))
    rows = []
    for cam, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        rows.append({"cam": cam, "px": count, "frac": count / total})
    return rows


def combine_owner_confidence(summary: dict[str, Any], conf_fields: dict[str, Any]) -> dict[str, Any]:
    owners = owner_fractions(summary)
    weighted: dict[str, float | None] = {}
    owner_rows = []
    for owner in owners:
        row = dict(owner)
        row["confidence"] = {}
        for field_name, field in conf_fields.items():
            cam_stats = field.get("per_cam", {}).get(str(owner["cam"]), {})
            row["confidence"][field_name] = {
                "median": cam_stats.get("med"),
                "p10": cam_stats.get("p10"),
                "p90": cam_stats.get("p90"),
            }
        owner_rows.append(row)

    for field_name, field in conf_fields.items():
        acc = 0.0
        denom = 0.0
        for owner in owners:
            cam_stats = field.get("per_cam", {}).get(str(owner["cam"]), {})
            med = cam_stats.get("med")
            if med is None:
                continue
            acc += owner["frac"] * float(med)
            denom += owner["frac"]
        weighted[field_name + "_owner_weighted_full_camera_median"] = acc / denom if denom > 0 else None

    return {
        "owner_rows": owner_rows,
        "owner_weighted_confidence": weighted,
        "admissibility": "diagnostic-owner-camera-only",
        "target_surface_mapping_available": False,
        "target_surface_mapping_note": "Existing ERP renderer/evidence pack exposes ROI camera owner labels, not pixel-exact raw-camera coordinates for VGGT confidence. These stats cannot promote RED by themselves.",
    }


def source_roi_rows(remote: dict[str, Any]) -> list[dict[str, Any]]:
    db25 = read_json(DB25)
    db41 = read_json(DB41)
    conf_fields = remote.get("vggt", {}).get("confidence_fields", {})
    summaries = {
        "db25_longline": db25,
        "db41_right_roi": db41.get("summaries", {}).get("right_roi", {}),
        "db41_lower_right_roi": db41.get("summaries", {}).get("lower_right_roi", {}),
    }
    rows = []
    for key, roi_meta in ROIS.items():
        summary = summaries[key]
        best_pair = summary.get("best_flow_pair")
        flow_pair_stats = summary.get("flow_pair_stats", {})
        row = {
            **roi_meta,
            "roi_key": key,
            "existing_evidence": {
                "roi_valid_frac": summary.get("roi_valid_frac"),
                "near_ground_frac": summary.get("near_ground_frac"),
                "lidar_support_frac": summary.get("lidar_support_frac"),
                "best_flow_pair": best_pair,
                "best_flow_reliable_frac": summary.get("best_flow_reliable_frac"),
                "key_pair_6_5_flow_frac": flow_pair_stats.get("6-5", {}).get("fb_reliable_frac"),
                "top_camera_labels": summary.get("top_camera_labels"),
            },
            "vggt_confidence": combine_owner_confidence(summary, conf_fields),
            "final_permission": {
                "evidence_state": "RED",
                "claim": "abstain",
                "permission_delta": "unchanged",
                "reason": "VGGT confidence is not target-surface mapped and existing LiDAR/raw-flow support does not pass DB45b target-surface promotion criteria.",
            },
        }
        rows.append(row)
    return rows


def generated_control_rows() -> list[dict[str, Any]]:
    return [
        {
            "segment_id": "db45_db36_fake_redline_reject",
            "label": "DB36 fake red-line DiT negative control",
            "evidence_state": "RED",
            "claim": "reject",
            "vggt_admissible": False,
            "reason": "Raw-camera VGGT confidence cannot validate generated-core slabs, holes, or fake right-line geometry.",
        },
        {
            "segment_id": "db45_db40_longsrc_fake_pole_reject",
            "label": "DB40 detector-clean fake-pole negative control",
            "evidence_state": "RED",
            "claim": "reject",
            "vggt_admissible": False,
            "reason": "Object-gate PASS and raw-camera confidence cannot launder a generated pole-like seam artifact.",
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
    db45d: dict[str, Any],
    rows: list[dict[str, Any]],
    generated_rows: list[dict[str, Any]],
    secret_hits: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    if secret_hits is None:
        secret_hits = []
    vggt = remote.get("vggt", {})
    conf_fields = vggt.get("confidence_fields", {})

    def chk(check_id: str, passed: bool, severity: str, evidence: str) -> dict[str, Any]:
        return {"id": check_id, "pass": bool(passed), "severity": severity, "evidence": evidence}

    def field_is_real(name: str) -> bool:
        field = conf_fields.get(name, {})
        glob = field.get("global", {})
        return field.get("shape_vhw") is not None and glob.get("std") is not None and float(glob.get("std", 0.0)) > 1e-8

    lower = next((r for r in rows if r["roi_key"] == "db41_lower_right_roi"), {})
    lower_lidar = lower.get("existing_evidence", {}).get("lidar_support_frac")
    return [
        chk(
            "db45d_setup_ready_precondition",
            db45d.get("decision", {}).get("vggt_setup_ready_for_future_roi_probe") is True,
            "precondition",
            "DB45d setup/load smoke accepted setup-ready for a future ROI probe.",
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
            "Official VGGT Commercial forward pass ran on the raw 7-camera ring.",
        ),
        chk(
            "real_confidence_fields_present",
            field_is_real("depth_conf") and field_is_real("world_points_conf"),
            "blocker",
            "Both depth_conf and world_points_conf are view-shaped maps with non-uniform finite values.",
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
            "DB25 and DB41 source-evidence ROIs remain RED/abstain.",
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
            "no_target_surface_mapping_overclaim",
            all(r.get("vggt_confidence", {}).get("target_surface_mapping_available") is False for r in rows),
            "blocker",
            "Owner-camera confidence is explicitly not claimed as pixel-exact target-surface confidence.",
        ),
        chk(
            "no_token_in_local_artifacts",
            not secret_hits,
            "blocker",
            f"Secret scan hits: {secret_hits}",
        ),
    ]


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1900, 1840), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((24, 18), "DB45e VGGT frozen-ROI confidence probe", fill=(255, 255, 255), font=font(28))
    draw.text(
        (24, 54),
        "One official VGGT raw-camera inference. Confidence is diagnostic owner-camera evidence, not target-surface repair permission.",
        fill=(220, 220, 220),
        font=font(15),
    )

    decision = manifest["decision"]
    pill(
        draw,
        (24, 94, 310, 130),
        "diagnostic accepted: " + str(decision["accepted_db45_diagnostic_evidence"]).lower(),
        (38, 128, 76) if decision["accepted_db45_diagnostic_evidence"] else (160, 80, 55),
    )
    pill(draw, (330, 94, 575, 130), "geometry evidence: false", (142, 74, 32))
    pill(draw, (595, 94, 790, 130), "RED promotions: 0", (78, 78, 78))
    pill(draw, (812, 94, 1120, 130), "target-surface mapping: false", (88, 88, 88))

    remote = manifest["remote_result"]
    vggt = remote.get("vggt", {})
    y = 154
    draw.text((24, y), "Remote VGGT facts", fill=(255, 255, 255), font=font(21))
    y += 30
    facts = [
        f"job={remote.get('colab_job', {}).get('job_id')} exit={remote.get('colab_job', {}).get('exit_code')} duration={remote.get('colab_job', {}).get('duration_s')}",
        f"model={vggt.get('model_id')} inference_ok={vggt.get('inference_ok')} runtime_s={vggt.get('duration_s')}",
        f"input_tensor_shape={vggt.get('input_tensor_shape')} prediction_keys={vggt.get('prediction_keys')}",
        f"CUDA={remote.get('torch', {}).get('cuda_name')} free_after={vggt.get('cuda_free_gb_after')} GB",
    ]
    for line in facts:
        y = draw_wrapped(draw, 42, y, "- " + line, 112, (235, 235, 235), 13, 5)
    y += 8

    draw.text((24, y), "Confidence bands", fill=(255, 255, 255), font=font(21))
    y += 30
    for name, field in manifest["confidence_field_summary"].items():
        glob = field.get("global", {})
        line = (
            f"{name}: source={field.get('source_key')} shape={field.get('shape_vhw')} "
            f"valid={fmt(glob.get('valid'))} mean={fmt(glob.get('mean'))} med={fmt(glob.get('med'))} "
            f"p10={fmt(glob.get('p10'))} p90={fmt(glob.get('p90'))} std={fmt(glob.get('std'))}"
        )
        y = draw_wrapped(draw, 42, y, "- " + line, 112, (235, 235, 235), 13, 5)
    y += 8

    table_y = y
    draw.text((24, table_y), "ROI decision table", fill=(255, 255, 255), font=font(21))
    table_y += 34
    header = ["ROI", "LiDAR", "Best flow", "Top cams", "depth med", "world med", "Final"]
    xs = [24, 330, 430, 560, 780, 930, 1080]
    for x, h in zip(xs, header):
        draw.text((x, table_y), h, fill=(210, 210, 210), font=font(14))
    table_y += 26
    for row in manifest["source_roi_rows"]:
        ev = row["existing_evidence"]
        conf = row["vggt_confidence"]["owner_weighted_confidence"]
        top_cams = ",".join(str(c) for c in (ev.get("top_camera_labels") or []))
        values = [
            row["roi_key"],
            fmt(ev.get("lidar_support_frac")),
            fmt(ev.get("best_flow_reliable_frac")),
            top_cams,
            fmt(conf.get("depth_conf_owner_weighted_full_camera_median")),
            fmt(conf.get("world_points_conf_owner_weighted_full_camera_median")),
            row["final_permission"]["evidence_state"] + "/" + row["final_permission"]["claim"],
        ]
        color = (255, 225, 180) if "lower" in row["roi_key"] else (235, 235, 235)
        for x, value in zip(xs, values):
            draw.text((x, table_y), str(value), fill=color, font=font(13))
        table_y += 30
    table_y += 10
    note = (
        "No permission promotion: VGGT confidence is summarized over camera owners only. "
        "The current evidence pack does not provide pixel-exact raw-camera target-surface mapping."
    )
    draw_wrapped(draw, 42, table_y, note, 116, (255, 235, 180), 13, 5)

    x2 = 1210
    y2 = 154
    draw.text((x2, y2), "Hard checks", fill=(255, 255, 255), font=font(21))
    y2 += 34
    for check in manifest["checks"]:
        fill = (48, 140, 82) if check["pass"] else ((190, 72, 72) if check["severity"] == "blocker" else (150, 112, 52))
        pill(draw, (x2, y2, x2 + 70, y2 + 29), "PASS" if check["pass"] else "STOP", fill)
        y2 = draw_wrapped(draw, x2 + 82, y2 + 2, check["id"], 54, (238, 238, 238), 13, 4)
        y2 += 8

    montage_y = 760
    draw.line((24, montage_y - 22, 1865, montage_y - 22), fill=(75, 75, 75), width=1)
    draw.text((24, montage_y - 8), "Existing source evidence boards reused for visual check", fill=(255, 255, 255), font=font(21))
    tile_size = (595, 430)
    board.paste(label_tile(DB25_MONTAGE, "DB25 long-line evidence montage", tile_size), (24, montage_y + 26))
    board.paste(label_tile(DB41_RIGHT_MONTAGE, "DB41 right ROI evidence montage", tile_size), (645, montage_y + 26))
    board.paste(label_tile(DB41_LOWER_MONTAGE, "DB41 lower-right evidence montage", tile_size), (1266, montage_y + 26))

    y3 = montage_y + 520
    draw.text((24, y3), "Generated-control boundary", fill=(255, 255, 255), font=font(21))
    y3 += 32
    for row in manifest["generated_control_rows"]:
        y3 = draw_wrapped(draw, 42, y3, f"- {row['segment_id']}: {row['claim']}; {row['reason']}", 118, (235, 235, 235), 13, 5)

    y3 += 10
    draw.text((24, y3), "Decision", fill=(255, 255, 255), font=font(21))
    y3 += 32
    for line in [
        "Accept diagnostic-only VGGT confidence metadata if all blocker checks pass.",
        "Do not accept it as DB45 geometry evidence, target-surface proof, or a seam repair permission.",
        "DB25/DB41 remain RED/abstain; DB36/DB40 remain rejected generated fake-geometry controls.",
    ]:
        y3 = draw_wrapped(draw, 42, y3, "- " + line, 118, (255, 235, 180), 13, 5)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def build_manifest() -> dict[str, Any]:
    db45b = read_json(DB45B)
    db45d = read_json(DB45D)
    remote = read_json(REMOTE_RESULT) if REMOTE_RESULT.exists() else {
        "db": "DB-45e",
        "error": {"type": "MissingRemoteResult", "message": "Run with --run-remote first."},
    }
    remote = _sanitize_json(remote)
    rows = source_roi_rows(remote)
    generated_rows = generated_control_rows()

    checks = build_checks(remote, db45b, db45d, rows, generated_rows, secret_hits=[])
    blocker_failures = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    diagnostic_accepted = not blocker_failures
    manifest = {
        "db": "DB-45e",
        "status": "vggt_roi_confidence_probe",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Run official VGGT once on raw BMW ring cameras and reduce real confidence fields into frozen DB45 ROI diagnostics without repair or RED promotion.",
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
            "accepted_evidence_type": "vggt-roi-confidence-diagnostic-only" if diagnostic_accepted else "blocked-or-no-go",
            "accepted_db45_diagnostic_evidence": diagnostic_accepted,
            "accepted_db45_geometry_evidence": False,
            "vggt_roi_inference_ran": remote.get("vggt", {}).get("inference_ok") is True,
            "permission_state_changes": "none",
            "red_promotions": [],
            "db45_status": "running",
            "claim_boundary": "Owner-camera confidence is not pixel-exact target-surface support and cannot repair or promote DB25/DB41 RED controls.",
        },
        "refs": {
            "db25_summary": rel(DB25),
            "db41_manifest": rel(DB41),
            "db45b_manifest": rel(DB45B),
            "db45d_manifest": rel(DB45D),
            "remote_result_json": rel(REMOTE_RESULT),
            "board": rel(BOARD),
        },
        "remote_result": remote,
        "confidence_field_summary": remote.get("vggt", {}).get("confidence_fields", {}),
        "source_roi_rows": rows,
        "generated_control_rows": generated_rows,
        "checks": checks,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    secret_hits = scan_secret_hits([REMOTE_RESULT, MANIFEST])
    checks = build_checks(remote, db45b, db45d, rows, generated_rows, secret_hits=secret_hits)
    blocker_failures = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    diagnostic_accepted = not blocker_failures
    manifest["checks"] = checks
    manifest["decision"]["accepted_evidence_type"] = (
        "vggt-roi-confidence-diagnostic-only" if diagnostic_accepted else "blocked-or-no-go"
    )
    manifest["decision"]["accepted_db45_diagnostic_evidence"] = diagnostic_accepted
    manifest["secret_scan_hits"] = secret_hits
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    build_board(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true", help="Run the one bounded Colab VGGT ROI probe job first.")
    parser.add_argument("--timeout-s", type=int, default=900)
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
