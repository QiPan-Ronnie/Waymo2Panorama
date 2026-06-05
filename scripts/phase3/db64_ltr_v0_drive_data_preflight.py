from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0"
REMOTE_RESULT_LOCAL = OUT_DIR / "db64_drive_data_preflight_remote_result.json"
MANIFEST = OUT_DIR / "db64_ltr_v0_drive_data_preflight_manifest.json"
BOARD = OUT_DIR / "db64_ltr_v0_drive_data_preflight_board.jpg"

TARGET_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
CLEAN_UUID = "0bae3b5e-417d-3b03-abaa-806b433233b8"
REMOTE_RESULT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db64_ltr_v0/db64_drive_data_preflight.json"
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


REMOTE_CODE = r'''
import json
import os
from pathlib import Path
from datetime import datetime, timezone

BASE = Path("/content/drive/MyDrive/koi_waymo2pano_colab")
TARGET_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
CLEAN_UUID = "0bae3b5e-417d-3b03-abaa-806b433233b8"
RING_CAMS_7 = [
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
]

def stat_file(path):
    return {"exists": path.exists(), "path": str(path), "bytes": path.stat().st_size if path.exists() and path.is_file() else None}

def count_files(path, suffix):
    if not path.exists() or not path.is_dir():
        return 0
    return len(list(path.glob("*" + suffix)))

def inspect_log(uuid, label):
    log = BASE / "data" / "argoverse2" / "val" / uuid
    cams_root = log / "sensors" / "cameras"
    cam_rows = {}
    for cam in RING_CAMS_7:
        cam_dir = cams_root / cam
        jpgs = sorted(cam_dir.glob("*.jpg")) if cam_dir.exists() else []
        cam_rows[cam] = {
            "dir_exists": cam_dir.exists(),
            "jpg_count": len(jpgs),
            "first_stem": jpgs[0].stem if jpgs else None,
            "last_stem": jpgs[-1].stem if jpgs else None,
        }
    lidar_dir = log / "sensors" / "lidar"
    lidar_files = sorted(lidar_dir.glob("*.feather")) if lidar_dir.exists() else []
    calib = log / "calibration"
    intr = calib / "intrinsics.feather"
    extr = calib / "egovehicle_SE3_sensor.feather"
    return {
        "label": label,
        "uuid": uuid,
        "log_exists": log.exists(),
        "log_path": str(log),
        "calibration_dir_exists": calib.exists(),
        "intrinsics": stat_file(intr),
        "extrinsics": stat_file(extr),
        "camera_root_exists": cams_root.exists(),
        "camera_rows": cam_rows,
        "all_7_camera_dirs_exist": all(v["dir_exists"] for v in cam_rows.values()),
        "min_camera_jpg_count": min([v["jpg_count"] for v in cam_rows.values()] or [0]),
        "lidar_dir_exists": lidar_dir.exists(),
        "lidar_feather_count": len(lidar_files),
        "first_lidar_stem": lidar_files[0].stem if lidar_files else None,
        "last_lidar_stem": lidar_files[-1].stem if lidar_files else None,
        "ltr_min_inputs_present": (
            log.exists()
            and intr.exists()
            and extr.exists()
            and all(v["dir_exists"] and v["jpg_count"] > 0 for v in cam_rows.values())
            and len(lidar_files) > 0
        ),
    }

def main():
    out = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_drive_data_preflight_remote",
        "scope": {
            "drive_data_presence_only": True,
            "fixed_logs_only": [TARGET_UUID, CLEAN_UUID],
            "model_inference": False,
            "renderer_or_ltr_prototype": False,
            "a100_required": False,
            "secrets_read": False,
        },
        "workspace": {
            "base": str(BASE),
            "base_exists": BASE.exists(),
            "top_level": {name: (BASE / name).exists() for name in ["data", "outputs", "results", "runtime", "hf_cache", "cache"]},
        },
        "logs": [
            inspect_log(TARGET_UUID, "bmw_near_field_target"),
            inspect_log(CLEAN_UUID, "clean_far_control"),
        ],
    }
    out["decision"] = {
        "target_log_ready_for_ltr_data_stage": out["logs"][0]["ltr_min_inputs_present"],
        "clean_control_ready_for_ltr_data_stage": out["logs"][1]["ltr_min_inputs_present"],
        "a100_needed_now": False,
        "next": "If target log is ready, the next DB64 sub-brief may run a CPU/GPU bounded LTR prototype; do not run model/refiner/generation here.",
    }
    result_path = Path("''' + REMOTE_RESULT + r'''")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("DB64_RESULT_PATH=" + str(result_path))
    print("DB64_JSON_BEGIN")
    print(json.dumps(out, ensure_ascii=False))
    print("DB64_JSON_END")

if __name__ == "__main__":
    main()
'''


def rel(path: Path | str | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    if not p.is_absolute():
        return str(p).replace("\\", "/")
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


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
        s = re.sub(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", "<redacted-url>", obj, flags=re.IGNORECASE)
        s = re.sub(r"hf_[A-Za-z0-9]{20,}", "<redacted-hf-token>", s)
        s = re.sub(r"Bearer\s+[A-Za-z0-9._\-]{20,}", "Bearer <redacted>", s, flags=re.IGNORECASE)
        s = re.sub(r'"token"\s*:\s*"[A-Za-z0-9._\-]{12,}"', '"token":"<redacted>"', s)
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

    def read_remote_json(self, remote_path: str) -> dict[str, Any] | None:
        resp = self.request(
            "GET",
            "/read",
            params={"path": remote_path, "base64": "true", "max_size_mb": "4"},
            timeout=180,
        )
        if "content" not in resp:
            return None
        raw = base64.b64decode(resp["content"])
        return json.loads(raw.decode("utf-8"))


def safe_status(status: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "runtime_type",
        "version",
        "gpu_name",
        "gpu_mem_free_mb",
        "active_jobs",
        "uptime_s",
        "timestamp",
    }
    return {k: status.get(k) for k in allowed if k in status}


def poll_job(client: ColabClient, job_id: str, timeout_s: int) -> dict[str, Any]:
    t0 = time.time()
    last: dict[str, Any] = {}
    while time.time() - t0 < timeout_s + 60:
        time.sleep(3)
        last = client.get(f"/jobs/{job_id}", timeout=180)
        if last.get("state") != "running":
            return last
    return last or {"state": "poll_timeout", "job_id": job_id}


def parse_json_from_log(log_tail: str) -> dict[str, Any] | None:
    if "DB64_JSON_BEGIN" not in log_tail or "DB64_JSON_END" not in log_tail:
        return None
    body = log_tail.split("DB64_JSON_BEGIN", 1)[1].split("DB64_JSON_END", 1)[0].strip()
    return json.loads(body)


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


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, ok: bool, w: int) -> int:
    fill = (48, 108, 74) if ok else (140, 63, 54)
    draw.rounded_rectangle((x, y, x + w, y + 36), radius=6, fill=fill, outline=(185, 185, 185))
    draw_text(draw, (x + 10, y + 9), label, size=13)
    return x + w + 12


def write_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1600, 1080), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 24), "DB64 Phase1 CPU Colab Drive-data preflight", size=28)
    draw_text(draw, (28, 62), "Fixed-log data presence only. No model, no renderer, no A100, no repair.", fill=(218, 224, 235), size=15)

    decision = manifest["decision"]
    x = 28
    x = pill(draw, x, 98, f"target data={decision['target_log_ready_for_ltr_data_stage']}", decision["target_log_ready_for_ltr_data_stage"], 205)
    x = pill(draw, x, 98, f"clean control={decision['clean_control_ready_for_ltr_data_stage']}", decision["clean_control_ready_for_ltr_data_stage"], 205)
    x = pill(draw, x, 98, f"A100 needed={decision['a100_needed_now']}", not decision["a100_needed_now"], 170)
    x = pill(draw, x, 98, f"secret hits={manifest['strict_secret_scan']['hit_count']}", manifest["strict_secret_scan"]["hit_count"] == 0, 155)
    pill(draw, x, 98, "no LTR render", True, 150)

    y = 155
    draw_text(draw, (28, y), "Runtime", size=21)
    y += 30
    rt = manifest["runtime"]
    runtime_lines = [
        f"secret source: {rt['secret_source_kind']}",
        f"status: runtime_type={rt['status'].get('runtime_type')} active_jobs={rt['status'].get('active_jobs')} version={rt['status'].get('version')}",
        f"job: state={manifest['job']['state']} exit={manifest['job'].get('exit_code')} duration={manifest['job'].get('duration_s')}",
    ]
    for line in runtime_lines:
        y = draw_wrapped(draw, 36, y, "- " + line, 118, size=14)

    y += 10
    draw_text(draw, (28, y), "Drive workspace", size=21)
    y += 30
    workspace = manifest["remote_result"].get("workspace", {})
    y = draw_wrapped(draw, 36, y, f"base_exists={workspace.get('base_exists')} top_level={workspace.get('top_level')}", 128, size=14)

    y += 12
    draw_text(draw, (28, y), "Fixed logs", size=21)
    y += 32
    for log in manifest["remote_result"].get("logs", []):
        color = (175, 245, 195) if log.get("ltr_min_inputs_present") else (255, 170, 145)
        y = draw_wrapped(
            draw,
            36,
            y,
            (
                f"{log.get('label')}: ready={log.get('ltr_min_inputs_present')} "
                f"log={log.get('log_exists')} calib={log.get('intrinsics', {}).get('exists')}/{log.get('extrinsics', {}).get('exists')} "
                f"cams7={log.get('all_7_camera_dirs_exist')} min_jpg={log.get('min_camera_jpg_count')} "
                f"lidar_count={log.get('lidar_feather_count')}"
            ),
            128,
            fill=color,
            size=14,
        )
    y += 12
    draw_text(draw, (28, y), "Decision", size=21)
    y += 30
    for line in [
        manifest["decision"]["summary"],
        manifest["decision"]["next_allowed_step"],
        "Remote full JSON was also written to Drive results/layered_target_raycaster/db64_ltr_v0/.",
    ]:
        y = draw_wrapped(draw, 36, y, "- " + line, 128, fill=(255, 235, 185), size=14)

    y = 740
    draw_text(draw, (28, y), "Claim boundary", size=21)
    y += 30
    claims = [
        "This is data-presence evidence only, not LTR-v0 rendering evidence.",
        "No source_id/layer/risk sidecars were created yet.",
        "Do not use this to promote DB25/DB41 or repair A1/G/G/DB32.",
        "A100 is not needed for this preflight; later GPU need depends on the chosen prototype.",
    ]
    for claim in claims:
        y = draw_wrapped(draw, 36, y, "- " + claim, 128, fill=(225, 230, 240), size=14)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["python", "-c", REMOTE_CODE], "cwd": "/content", "timeout_s": 180}, timeout=180)
    job_id = submit["job_id"]
    job = poll_job(client, job_id, timeout_s=180)
    remote_result = None
    try:
        remote_result = client.read_remote_json(REMOTE_RESULT)
    except Exception:
        remote_result = None
    if remote_result is None:
        remote_result = parse_json_from_log(job.get("log_tail", ""))
    if remote_result is None:
        remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}

    REMOTE_RESULT_LOCAL.write_text(json.dumps(sanitize(remote_result), indent=2, ensure_ascii=False), encoding="utf-8")
    target_ready = bool(remote_result.get("decision", {}).get("target_log_ready_for_ltr_data_stage"))
    clean_ready = bool(remote_result.get("decision", {}).get("clean_control_ready_for_ltr_data_stage"))
    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_ltr_v0_drive_data_preflight",
        "accepted_evidence_type": "cpu_colab_drive_data_presence_only",
        "scope": {
            "remote_status_used": True,
            "remote_exec_used": True,
            "exec_count": 1,
            "fixed_logs_only": [TARGET_UUID, CLEAN_UUID],
            "a100_used_or_needed": False,
            "model_inference_used": False,
            "renderer_or_ltr_prototype_ran": False,
            "generation_or_inpainting_used": False,
            "source_replacement_used": False,
            "sidecars_created": False,
            "red_promotion": False,
        },
        "runtime": {
            "secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file",
            "status": safe_status(status),
        },
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail"}}),
        "remote_result": sanitize(remote_result),
        "local_outputs": {
            "remote_result_json": rel(REMOTE_RESULT_LOCAL),
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
        "decision": {
            "target_log_ready_for_ltr_data_stage": target_ready,
            "clean_control_ready_for_ltr_data_stage": clean_ready,
            "a100_needed_now": False,
            "summary": (
                "Drive target data is ready for a bounded LTR prototype preflight."
                if target_ready
                else "Drive target data is still not confirmed ready for LTR prototype."
            ),
            "next_allowed_step": (
                "Open or extend DB64 with a prototype sub-scope before rendering sidecars. "
                "A CPU/GPU choice should be based on expected runtime; no model/refiner/generation."
                if target_ready
                else "Fix Drive target data or local sync first; do not run LTR renderer."
            ),
        },
    }
    hits = secret_hits(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    manifest["strict_secret_scan"] = {"hits": hits, "hit_count": len(hits)}
    write_board(manifest)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "target_ready": target_ready,
                "clean_ready": clean_ready,
                "a100_needed_now": False,
                "secret_hits": len(hits),
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
