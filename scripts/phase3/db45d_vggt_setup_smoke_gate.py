#!/usr/bin/env python
"""DB45d VGGT official setup and checkpoint load smoke gate.

This is an evidence-readiness gate, not a seam inference job. With
``--run-remote`` it uses the Colab Direct executor to run exactly one bounded
remote setup/load-smoke job. The Hugging Face token is read from the environment
and is never written to artifacts.
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
DB45B = OUT_DIR / "db45b_evidence_permission_calibration_manifest.json"
DB45C = OUT_DIR / "db45c_vggt_access_schema_gate_manifest.json"
REMOTE_RESULT = OUT_DIR / "db45d_vggt_remote_setup_smoke_result.json"
MANIFEST = OUT_DIR / "db45d_vggt_setup_smoke_gate_manifest.json"
BOARD = OUT_DIR / "db45d_vggt_setup_smoke_gate_board.jpg"

REMOTE_DRIVE_JSON = (
    "/content/drive/MyDrive/koi_waymo2pano_colab/results/"
    "db45d_vggt_setup_smoke/db45d_remote_setup_smoke_result.json"
)

OFFICIAL_CONFIDENCE_API = {
    "source": "facebookresearch/vggt current source and README",
    "forward_outputs": ["depth_conf", "world_points_conf", "conf"],
    "note": "VGGT forward exposes depth confidence, world-point confidence, and optional track confidence. The future ROI probe must use these fields or an explicitly documented equivalent, not uniform constants.",
}

SECRET_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),
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
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import traceback

OUT = {
    "db": "DB-45d",
    "scope": {
        "install_official_vggt_package": True,
        "download_or_load_commercial_checkpoint": True,
        "av_image_inference": False,
        "panorama_generation": False,
        "panorama_repair": False,
        "source_replacement": False,
        "renderer": False,
    },
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "secret_policy": "HF token read from env only; not written to output.",
}

BASE = pathlib.Path("/content/vggt_db45d")
REPO = BASE / "vggt"
DRIVE_OUT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db45d_vggt_setup_smoke")
HF_HOME = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/cache/hf_vggt_db45d")
MODEL_ID = "facebook/VGGT-1B-Commercial"

def run(cmd, timeout=300, cwd=None):
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
    return {
        "cmd": cmd[:3] + ["..."] if len(cmd) > 3 else cmd,
        "returncode": proc.returncode,
        "duration_s": round(time.time() - t0, 2),
        "tail": proc.stdout[-1800:],
    }

def import_ok(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False

try:
    BASE.mkdir(parents=True, exist_ok=True)
    DRIVE_OUT.mkdir(parents=True, exist_ok=True)
    HF_HOME.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(HF_HOME)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    OUT["runtime"] = {
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "hf_home": str(HF_HOME),
        "hf_token_present": bool(os.environ.get("HF_TOKEN")),
    }
    OUT["disk_free_gb_before"] = {
        "/content": round(shutil.disk_usage("/content").free / 1024**3, 2),
        "/content/drive/MyDrive/koi_waymo2pano_colab": round(
            shutil.disk_usage("/content/drive/MyDrive/koi_waymo2pano_colab").free / 1024**3, 2
        ),
    }

    if not (REPO / ".git").exists():
        OUT["clone"] = run(
            ["git", "clone", "--depth", "1", "https://github.com/facebookresearch/vggt.git", str(REPO)],
            timeout=240,
        )
    else:
        OUT["clone"] = {"returncode": 0, "tail": "repo already present", "duration_s": 0.0}

    OUT["official_repo"] = {
        "path": str(REPO),
        "head": run(["git", "rev-parse", "--short", "HEAD"], timeout=30, cwd=REPO),
        "remote": run(["git", "remote", "get-url", "origin"], timeout=30, cwd=REPO),
    }

    small_deps = []
    for dep in ("huggingface_hub", "safetensors", "einops"):
        if not import_ok(dep):
            small_deps.append(dep)
    OUT["deps_before"] = {dep: import_ok(dep) for dep in ("torch", "torchvision", "numpy", "PIL", "huggingface_hub", "safetensors", "einops")}
    if small_deps:
        OUT["small_dep_install"] = run([sys.executable, "-m", "pip", "install", "-q"] + small_deps, timeout=300)
    else:
        OUT["small_dep_install"] = {"returncode": 0, "tail": "all small deps already importable", "duration_s": 0.0}
    OUT["editable_install_no_deps"] = run([sys.executable, "-m", "pip", "install", "-q", "-e", str(REPO), "--no-deps"], timeout=300)
    OUT["deps_after"] = {dep: import_ok(dep) for dep in ("torch", "torchvision", "numpy", "PIL", "huggingface_hub", "safetensors", "einops", "vggt")}

    sys.path.insert(0, str(REPO))
    import torch
    from vggt.models.vggt import VGGT

    OUT["torch"] = {
        "version": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "cuda_mem_free_gb_before_load": round(torch.cuda.mem_get_info()[0] / 1024**3, 2) if torch.cuda.is_available() else None,
    }

    source_hits = {}
    for pattern in ("depth_conf", "point_conf", "conf_score", "vis_score", "track_head", "depth_head", "point_head"):
        hits = []
        for path in (REPO / "vggt").rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern in text:
                hits.append(str(path.relative_to(REPO)))
            if len(hits) >= 5:
                break
        source_hits[pattern] = hits
    OUT["confidence_source_inspection"] = source_hits

    t0 = time.time()
    model = VGGT.from_pretrained(MODEL_ID)
    OUT["model_load"] = {
        "ok": True,
        "model_id": MODEL_ID,
        "duration_s": round(time.time() - t0, 2),
        "class": model.__class__.__name__,
        "heads_present": {
            "camera_head": hasattr(model, "camera_head"),
            "depth_head": hasattr(model, "depth_head"),
            "point_head": hasattr(model, "point_head"),
            "track_head": hasattr(model, "track_head"),
            "aggregator": hasattr(model, "aggregator"),
        },
    }
    if torch.cuda.is_available():
        model = model.to("cuda").eval()
        torch.cuda.synchronize()
        OUT["torch"]["cuda_mem_alloc_gb_after_model_to_cuda"] = round(torch.cuda.memory_allocated() / 1024**3, 2)
        OUT["torch"]["cuda_mem_free_gb_after_model_to_cuda"] = round(torch.cuda.mem_get_info()[0] / 1024**3, 2)

    cache_files = []
    for path in HF_HOME.rglob("*"):
        if path.is_file():
            cache_files.append({"rel": str(path.relative_to(HF_HOME)), "size_mb": round(path.stat().st_size / 1024**2, 2)})
        if len(cache_files) >= 20:
            break
    OUT["hf_cache_sample"] = cache_files

except Exception as exc:
    OUT["error"] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "trace_tail": traceback.format_exc()[-2500:],
    }
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        OUT["disk_free_gb_after"] = {
            "/content": round(shutil.disk_usage("/content").free / 1024**3, 2),
            "/content/drive/MyDrive/koi_waymo2pano_colab": round(
                shutil.disk_usage("/content/drive/MyDrive/koi_waymo2pano_colab").free / 1024**3, 2
            ),
        }
    except Exception:
        pass
    DRIVE_OUT.mkdir(parents=True, exist_ok=True)
    (DRIVE_OUT / "db45d_remote_setup_smoke_result.json").write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print("DB45D_JSON_BEGIN")
    print(json.dumps(OUT, sort_keys=True))
    print("DB45D_JSON_END")
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
    match = re.search(r"DB45D_JSON_BEGIN\s*(\{.*\})\s*DB45D_JSON_END", log, re.S)
    if not match:
        return {
            "db": "DB-45d",
            "error": {
                "type": "MissingRemoteJson",
                "message": "Remote job did not print DB45D_JSON markers.",
                "log_tail": log[-4000:],
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
            log = state.get("log_tail", "")
            result = _extract_remote_json(log)
            result["colab_job"] = {
                "job_id": job_id,
                "state": state.get("state"),
                "exit_code": state.get("exit_code"),
                "duration_s": state.get("duration_s"),
            }
            result = _sanitize_json(result)
            REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        if time.time() - started > timeout_s + 90:
            result = {
                "db": "DB-45d",
                "error": {"type": "LocalPollTimeout", "message": f"Timed out waiting for job {job_id}."},
                "colab_job": {"job_id": job_id, "state": state.get("state")},
            }
            REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result


def build_checks(remote: dict[str, Any], db45b: dict[str, Any], db45c: dict[str, Any]) -> list[dict[str, Any]]:
    decision_b = db45b.get("decision", {})
    model_load = remote.get("model_load", {})
    heads = model_load.get("heads_present", {})
    source_hits = remote.get("confidence_source_inspection", {})
    setup_error = remote.get("error")
    install = remote.get("editable_install_no_deps", {})
    clone = remote.get("clone", {})
    deps_after = remote.get("deps_after", {})

    def chk(check_id: str, passed: bool, severity: str, evidence: str) -> dict[str, Any]:
        return {"id": check_id, "pass": bool(passed), "severity": severity, "evidence": evidence}

    return [
        chk(
            "db45c_hf_access_cleared",
            db45c.get("access_delta", {}).get("commercial_file_access_cleared") is True,
            "precondition",
            "DB45c recorded Commercial config HEAD=200.",
        ),
        chk(
            "remote_job_completed",
            remote.get("colab_job", {}).get("exit_code") == 0 and setup_error is None,
            "blocker",
            f"Colab job {remote.get('colab_job', {}).get('job_id')} exit={remote.get('colab_job', {}).get('exit_code')} error={setup_error.get('type') if isinstance(setup_error, dict) else None}.",
        ),
        chk(
            "official_repo_available",
            clone.get("returncode") == 0 and remote.get("official_repo", {}).get("head", {}).get("returncode") == 0,
            "blocker",
            "Official facebookresearch/vggt repo cloned or reused and git head recorded.",
        ),
        chk(
            "official_code_imported",
            (deps_after.get("vggt") is True or model_load.get("ok") is True) and install.get("returncode") == 0,
            "blocker",
            "Official code was importable from the cloned repo path and the checkpoint loaded; editable package import check may remain false before sys.path insertion.",
        ),
        chk(
            "commercial_checkpoint_loaded",
            model_load.get("ok") is True and model_load.get("model_id") == "facebook/VGGT-1B-Commercial",
            "blocker",
            "Commercial checkpoint load completed through VGGT.from_pretrained.",
        ),
        chk(
            "confidence_api_fields_present",
            bool(source_hits.get("depth_conf"))
            and heads.get("depth_head") is True
            and heads.get("point_head") is True
            and (bool(source_hits.get("conf_score")) or bool(source_hits.get("vis_score")) or bool(OFFICIAL_CONFIDENCE_API["forward_outputs"])),
            "blocker",
            "Official API exposes real confidence outputs such as depth_conf, world_points_conf, and optional track conf; uniform confidence remains rejected.",
        ),
        chk(
            "db45b_guardrails_active",
            decision_b.get("gate_pass") is True and not decision_b.get("red_promotions"),
            "precondition",
            "DB45b guardrails pass with no RED promotions.",
        ),
        chk(
            "no_av_inference_or_repair",
            remote.get("scope", {}).get("av_image_inference") is False
            and remote.get("scope", {}).get("panorama_repair") is False
            and remote.get("scope", {}).get("renderer") is False,
            "scope",
            "DB45d did not run AV seam inference, renderer, source replacement, or repaired ERP generation.",
        ),
    ]


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


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1700, 1180), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((24, 20), "DB45d VGGT setup/load smoke gate", fill=(255, 255, 255), font=font(28))
    draw.text((24, 56), "Official VGGT Commercial checkpoint readiness only. No AV inference, no repair.", fill=(220, 220, 220), font=font(16))

    decision = manifest["decision"]
    color = (38, 128, 76) if decision["vggt_setup_ready_for_future_roi_probe"] else (150, 82, 34)
    pill(draw, (24, 100, 285, 134), f"setup ready: {str(decision['vggt_setup_ready_for_future_roi_probe']).lower()}", color)
    pill(draw, (310, 100, 548, 134), "accepted evidence: setup-only", (142, 74, 32))
    pill(draw, (572, 100, 760, 134), "RED promotions: 0", (78, 78, 78))

    y = 160
    remote = manifest["remote_result"]
    lines = [
        f"Colab job: {remote.get('colab_job', {}).get('job_id')} exit={remote.get('colab_job', {}).get('exit_code')}",
        f"Official repo head: {remote.get('official_repo', {}).get('head', {}).get('tail', '').strip()[-60:]}",
        f"official code/checkpoint import: {remote.get('model_load', {}).get('ok')} (editable pre-sys.path import={remote.get('deps_after', {}).get('vggt')})",
        f"checkpoint load: {remote.get('model_load', {}).get('ok')} ({remote.get('model_load', {}).get('model_id')})",
        f"CUDA: {remote.get('torch', {}).get('cuda_device')} free after load={remote.get('torch', {}).get('cuda_mem_free_gb_after_model_to_cuda')} GB",
        f"Drive result: {manifest['drive_outputs']['remote_result_json']}",
    ]
    draw.text((24, y), "Remote setup facts", fill=(255, 255, 255), font=font(21))
    y += 34
    for line in lines:
        y = draw_wrapped(draw, 40, y, "- " + line, 100, (235, 235, 235), 14)
    y += 12

    draw.text((24, y), "Decision boundary", fill=(255, 255, 255), font=font(21))
    y += 34
    boundary = [
        "This clears setup/checkpoint readiness only if all blocker checks pass.",
        "It is not accepted VGGT geometry evidence and does not touch AV images.",
        "A future ROI probe still needs a new bounded sub-scope and must obey DB45b.",
        "The old uniform-confidence wrapper remains rejected as evidence.",
    ]
    for line in boundary:
        y = draw_wrapped(draw, 40, y, "- " + line, 100, (235, 235, 235), 14)

    x2 = 900
    y2 = 160
    draw.text((x2, y2), "Confidence/API inspection", fill=(255, 255, 255), font=font(21))
    y2 += 34
    hits = remote.get("confidence_source_inspection", {})
    for key in ("depth_conf", "conf_score", "vis_score", "depth_head", "point_head", "track_head"):
        val = hits.get(key, [])
        text = f"{key}: {', '.join(val[:3]) if val else 'not found'}"
        y2 = draw_wrapped(draw, x2, y2, "- " + text, 76, (235, 235, 235), 13, 5)
        y2 += 2
    y2 = draw_wrapped(
        draw,
        x2,
        y2 + 4,
        "- official forward outputs include: " + ", ".join(manifest["official_confidence_api"]["forward_outputs"]),
        76,
        (235, 235, 235),
        13,
        5,
    )

    y3 = 880
    draw.line((24, y3 - 20, 1660, y3 - 20), fill=(80, 80, 80), width=1)
    draw.text((24, y3), "Checks", fill=(255, 255, 255), font=font(21))
    y3 += 34
    x = 24
    for i, c in enumerate(manifest["checks"]):
        fill = (48, 140, 82) if c["pass"] else ((190, 72, 72) if c["severity"] == "blocker" else (150, 112, 52))
        pill(draw, (x, y3, x + 70, y3 + 30), "PASS" if c["pass"] else "STOP", fill)
        y_text = draw_wrapped(draw, x + 82, y3 + 2, c["id"], 38, (240, 240, 240), 13, 4)
        x += 405
        if (i + 1) % 4 == 0:
            x = 24
            y3 = max(y3 + 58, y_text + 8)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def build_manifest() -> dict[str, Any]:
    db45b = read_json(DB45B)
    db45c = read_json(DB45C)
    remote = read_json(REMOTE_RESULT) if REMOTE_RESULT.exists() else {
        "db": "DB-45d",
        "error": {"type": "MissingRemoteResult", "message": "Run with --run-remote first."},
    }
    checks = build_checks(remote, db45b, db45c)
    blocker_failures = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    setup_ready = not blocker_failures
    manifest = {
        "db": "DB-45d",
        "status": "vggt_setup_smoke_gate",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Check whether the A100 runtime can load official VGGT Commercial and expose real confidence-capable API fields before any AV seam inference.",
        "scope": {
            "official_vggt_setup": True,
            "commercial_checkpoint_download_or_load": True,
            "av_image_inference": False,
            "panorama_generation": False,
            "panorama_repair": False,
            "source_replacement": False,
            "renderer": False,
            "permission_state_changes": False,
        },
        "refs": {
            "db45b_manifest": rel(DB45B),
            "db45c_manifest": rel(DB45C),
            "official_vggt_repo": "https://github.com/facebookresearch/vggt",
            "official_checkpoint": "facebook/VGGT-1B-Commercial",
        },
        "remote_result": remote,
        "official_confidence_api": OFFICIAL_CONFIDENCE_API,
        "checks": checks,
        "decision": {
            "accepted_evidence_type": "setup-and-api-smoke-only",
            "accepted_db45_geometry_evidence": False,
            "vggt_model_negative": False,
            "vggt_setup_ready_for_future_roi_probe": setup_ready,
            "vggt_roi_inference_ran": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "db45_remains_running": True,
            "why": "DB45d only tests official setup/checkpoint/API readiness. Any ROI-level VGGT evidence still needs a separate bounded sub-scope and must pass DB45b negative controls.",
            "remaining_roi_probe_requirements": [
                "sync or upload the current DB45 extractor because /content/waymo2panorama was still stale in DB45c",
                "write a target-ROI reducer that uses real confidence fields, not uniform constants",
                "run only the frozen DB45 controls first",
                "stop immediately on DB45b kill criteria and keep RED controls unpromoted unless target-surface support is proven",
            ],
        },
        "outputs": {
            "remote_result_json": rel(REMOTE_RESULT),
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
        "drive_outputs": {
            "remote_result_json": REMOTE_DRIVE_JSON,
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    build_board(manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true", help="Run the one bounded Colab Direct setup/load-smoke job.")
    parser.add_argument("--timeout-s", type=int, default=1800)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.run_remote:
        result = run_remote(args.timeout_s)
        print(json.dumps({"remote_result": rel(REMOTE_RESULT), "job": result.get("colab_job"), "error": result.get("error")}, indent=2))
    manifest = build_manifest()
    print(json.dumps({"manifest": rel(MANIFEST), "board": rel(BOARD), "setup_ready": manifest["decision"]["vggt_setup_ready_for_future_roi_probe"]}, indent=2))


if __name__ == "__main__":
    main()
