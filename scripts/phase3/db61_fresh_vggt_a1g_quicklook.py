from __future__ import annotations

import base64
import gzip
import importlib.util
import json
import os
import re
import time
import urllib.parse
import urllib.request
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db61_fresh_vggt_a1g_quicklook"
REMOTE_RESULT = OUT_DIR / "db61_fresh_vggt_remote_result.json"
MANIFEST = OUT_DIR / "db61_fresh_vggt_a1g_quicklook_manifest.json"
BOARD = OUT_DIR / "db61_fresh_vggt_a1g_quicklook_board.jpg"

DB45F_SCRIPT = ROOT / "scripts" / "phase3" / "db45f_vggt_target_uv_sampling_gate.py"
DB60_SCRIPT = ROOT / "scripts" / "phase3" / "db60_vggt_ungated_quicklook.py"
REPO_RUNTIME_SECRET_FILE = ROOT / "runtime" / "active_url.json"

A1_PANO = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment" / "A1_view_none_bmw_1024x2048.png"
G_PANO = ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg"
DB45K_BOARD = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit" / "db45k_vggt_pose_reflection_audit_board.jpg"
DB60_BOARD = ROOT / "deliverables" / "dit360_v2" / "db60_vggt_ungated_quicklook" / "db60_vggt_ungated_quicklook_board.jpg"

TARGET = {
    "uuid": "02a00399-3857-444e-8db3-a8f58489c394",
    "anchor": 0,
    "roi_key": "db25_longline",
    "roi_xyxy": [850, 420, 1650, 720],
}

DEFAULT_RUNTIME_SECRET_FILES = [
    Path.home() / ".waymo2panorama" / "runtime" / "active_url.json",
    Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Waymo2Panorama" / "runtime" / "active_url.json",
]
DEFAULT_HF_SECRET_FILES = [
    Path.home() / ".waymo2panorama" / "runtime" / "hf token.txt",
    Path.home() / ".waymo2panorama" / "runtime" / "hf_token.txt",
]

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "cloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "json_hex_token": re.compile(r'"token"\s*:\s*"[0-9a-fA-F]{32}"'),
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    raise RuntimeError("missing approved runtime source")


def load_hf_secret() -> dict[str, str] | None:
    env_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if env_token:
        return {"token": env_token.strip(), "source": "process_env"}
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
            return {"token": token, "source": f"non_repo_file:{path}"}
    return None


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
        return [sanitize(value) for value in obj]
    if isinstance(obj, str):
        text = obj
        for pattern in TOKEN_PATTERNS.values():
            text = pattern.sub("<redacted>", text)
        return text
    return obj


class ColabClient:
    def __init__(self) -> None:
        runtime = load_runtime_secret()
        self.url = runtime["url"].rstrip("/")
        self.token = runtime["token"]
        self.source = runtime["source"]

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get(self, path: str, timeout: int = 180) -> dict[str, Any]:
        return self.request("GET", path, timeout=timeout)

    def post(self, path: str, body: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
        return self.request("POST", path, body=body, timeout=timeout)


def db61_remote_python() -> str:
    db45f = load_module(DB45F_SCRIPT, "db45f_template")
    code = db45f._remote_python()
    replacements = {
        '"DB-45f"': '"DB-61"',
        "DB-45f": "DB-61",
        "db45f_vggt_target_uv_sampling": "db61_fresh_vggt_a1g_quicklook",
        "db45f_remote_target_uv_sampling_result.json": "db61_fresh_vggt_remote_result.json",
        "DB45F_JSON_BEGIN": "DB61_JSON_BEGIN",
        "DB45F_JSON_END": "DB61_JSON_END",
    }
    for old, new in replacements.items():
        code = code.replace(old, new)
    old_rois = '''ROIS = {
    "db25_longline": [850, 420, 1650, 720],
    "db41_right_roi": [1440, 360, 2048, 720],
    "db41_lower_right_roi": [1580, 560, 2048, 790],
}'''
    new_rois = '''ROIS = {
    "db25_longline": [850, 420, 1650, 720],
}'''
    if old_rois not in code:
        raise RuntimeError("DB61 remote template ROI block was not found; refusing broad-scope run")
    code = code.replace(old_rois, new_rois)
    code = code.replace("import pathlib\n", "import pathlib\nimport subprocess\n", 1)
    code = code.replace(
        "RAW_DIR = WORK / \"raw_cameras\"\n",
        '''RAW_DIR = WORK / "raw_cameras"

def db61_run(cmd, timeout=300, cwd=None):
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
        "tail": proc.stdout[-1600:],
    }

def db61_import_ok(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False
''',
        1,
    )
    code = code.replace(
        "    RAW_DIR.mkdir(parents=True, exist_ok=True)\n\n    for p in [OFFICIAL_REPO, LOCAL_REPO / \"code\", LOCAL_REPO / \"scripts\" / \"phase3\", LOCAL_REPO]:\n",
        '''    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if not (OFFICIAL_REPO / ".git").exists():
        OFFICIAL_REPO.parent.mkdir(parents=True, exist_ok=True)
        OUT["official_repo_clone"] = db61_run(
            ["git", "clone", "--depth", "1", "https://github.com/facebookresearch/vggt.git", str(OFFICIAL_REPO)],
            timeout=300,
        )
    else:
        OUT["official_repo_clone"] = {"returncode": 0, "tail": "repo already present", "duration_s": 0.0}
    small_deps = [dep for dep in ("huggingface_hub", "safetensors", "einops") if not db61_import_ok(dep)]
    OUT["deps_before"] = {dep: db61_import_ok(dep) for dep in ("torch", "torchvision", "numpy", "PIL", "huggingface_hub", "safetensors", "einops", "vggt")}
    if small_deps:
        OUT["small_dep_install"] = db61_run([sys.executable, "-m", "pip", "install", "-q"] + small_deps, timeout=360)
    else:
        OUT["small_dep_install"] = {"returncode": 0, "tail": "all small deps already importable", "duration_s": 0.0}
    OUT["editable_install_no_deps"] = db61_run([sys.executable, "-m", "pip", "install", "-q", "-e", str(OFFICIAL_REPO), "--no-deps"], timeout=360)
    OUT["deps_after"] = {dep: db61_import_ok(dep) for dep in ("torch", "torchvision", "numpy", "PIL", "huggingface_hub", "safetensors", "einops", "vggt")}

    for p in [OFFICIAL_REPO, LOCAL_REPO / "code", LOCAL_REPO / "scripts" / "phase3", LOCAL_REPO]:
''',
        1,
    )
    hf = load_hf_secret() if os.environ.get("DB61_FORWARD_HF_SECRET") == "1" else None
    if hf:
        token_b64 = base64.b64encode(hf["token"].encode("utf-8")).decode("ascii")
        inject = (
            "import base64\n"
            "import os\n"
            f"_DB61_HF_TOKEN_B64 = '{token_b64}'\n"
            "os.environ['HF_TOKEN'] = base64.b64decode(_DB61_HF_TOKEN_B64).decode('utf-8').strip()\n"
            "os.environ['HUGGING_FACE_HUB_TOKEN'] = os.environ['HF_TOKEN']\n"
        )
        code = code.replace("import contextlib\n", "import contextlib\n" + inject, 1)
    code = code.replace(
        '"secret_policy": "HF token read from environment only; not written to output."',
        '"secret_policy": "HF token, if needed, is supplied from approved env/non-repo secret source and not written to output."',
    )
    return code


def recovery_python() -> str:
    return r'''
import base64
import gzip
import json
import pathlib
import traceback

SRC = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db61_fresh_vggt_a1g_quicklook/db61_fresh_vggt_remote_result.json")
try:
    data = json.loads(SRC.read_text(encoding="utf-8"))
except Exception as exc:
    data = {"db": "DB-61", "error": {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-1500:]}}
payload = gzip.compress(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"), compresslevel=9)
print("DB61_RECOVERY_B64_BEGIN")
print(base64.b64encode(payload).decode("ascii"))
print("DB61_RECOVERY_B64_END")
'''


def extract_remote_json(log: str) -> dict[str, Any]:
    match = re.search(r"DB61_JSON_BEGIN\s*(\{.*\})\s*DB61_JSON_END", log, re.S)
    if match:
        return json.loads(match.group(1))
    b64_match = re.search(r"DB61_RECOVERY_B64_BEGIN\s*([A-Za-z0-9+/=\s]+?)\s*DB61_RECOVERY_B64_END", log, re.S)
    if b64_match:
        payload = re.sub(r"\s+", "", b64_match.group(1))
        return json.loads(gzip.decompress(base64.b64decode(payload)).decode("utf-8"))
    return {
        "db": "DB-61",
        "error": {
            "type": "MissingRemoteJson",
            "message": "Remote log did not contain DB61 JSON markers.",
            "log_tail": sanitize(log[-2500:]),
        },
    }


def run_exec(client: ColabClient, code: str, timeout_s: int, purpose: str) -> dict[str, Any]:
    remote_code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    bash = (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{remote_code_b64}').decode('utf-8')\n"
        "exec(compile(code, '<db61_remote>', 'exec'))\n"
        "PY"
    )
    job = client.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content", "timeout_s": timeout_s}, timeout=180)
    job_id = job["job_id"]
    started = time.time()
    state: dict[str, Any] = {}
    while True:
        time.sleep(8 if purpose == "fresh_vggt" else 3)
        state = client.get(f"/jobs/{urllib.parse.quote(str(job_id))}", timeout=180)
        if state.get("state") != "running":
            result = extract_remote_json(state.get("log_tail", ""))
            result["colab_job"] = {
                "job_id": job_id,
                "state": state.get("state"),
                "exit_code": state.get("exit_code"),
                "duration_s": state.get("duration_s"),
                "purpose": purpose,
            }
            return sanitize(result)
        if time.time() - started > timeout_s + 120:
            return {
                "db": "DB-61",
                "error": {"type": "LocalPollTimeout", "message": f"timed out waiting for job {job_id}"},
                "colab_job": {"job_id": job_id, "state": state.get("state"), "purpose": purpose},
            }


def run_remote(timeout_s: int) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    status = sanitize(client.get("/status", timeout=180))
    result = run_exec(client, db61_remote_python(), timeout_s, "fresh_vggt")
    if result.get("error", {}).get("type") == "MissingRemoteJson":
        recovery = run_exec(client, recovery_python(), 240, "recover_db61_drive_json")
        if not recovery.get("error"):
            result = recovery
            result["recovered_after_log_truncation"] = True
    result["runtime_status_pre_exec"] = {
        "runtime_type": status.get("runtime_type"),
        "gpu_name": status.get("gpu_name"),
        "gpu_mem_free_mb": status.get("gpu_mem_free_mb"),
        "active_jobs": status.get("active_jobs"),
    }
    result["runtime_secret_source"] = "approved_env_or_non_repo_file"
    REMOTE_RESULT.write_text(json.dumps(sanitize(result), indent=2), encoding="utf-8")
    return result


def image_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    with Image.open(path) as img:
        return {"exists": True, "size": list(img.size), "bytes": int(path.stat().st_size)}


def token_hits(obj: Any) -> list[dict[str, Any]]:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    hits = []
    for name, pattern in TOKEN_PATTERNS.items():
        found = pattern.findall(text)
        if found:
            hits.append({"path": "manifest_preview", "pattern": name, "count": len(found)})
    return hits


def make_quicklook(remote: dict[str, Any]) -> dict[str, Any]:
    db60 = load_module(DB60_SCRIPT, "db60_helpers")
    db60.DB45F_REMOTE = REMOTE_RESULT
    db60.OUT_DIR = OUT_DIR
    db60.MANIFEST = MANIFEST
    db60.BOARD = BOARD
    roi = TARGET["roi_xyxy"]
    a1, g = db60.load_base_images()
    alpha, alpha_stats = db60.build_alpha(a1, g, roi)
    prior, vggt_stats = db60.vggt_prior_for_roi(roi)
    a1_candidate, a1_stats = db60.apply_quicklook(a1, g, alpha, roi)
    g_candidate, g_stats = db60.apply_quicklook(g, a1, alpha, roi)

    alpha_path = OUT_DIR / "db61_fresh_vggt_alpha_mask.png"
    prior_path = OUT_DIR / "db61_fresh_vggt_prior_heatmap.png"
    a1_path = OUT_DIR / "db61_a1_view_none_fresh_vggt_ungated_quicklook.png"
    g_path = OUT_DIR / "db61_g_bmw_pano_fresh_vggt_ungated_quicklook.png"
    a1_crop_path = OUT_DIR / "db61_a1_quicklook_roi_crop.png"
    g_crop_path = OUT_DIR / "db61_g_quicklook_roi_crop.png"
    db60.save_gray(alpha_path, alpha / max(float(alpha.max()), 1e-6))
    db60.heat_color(prior).save(prior_path)
    a1_candidate.save(a1_path)
    g_candidate.save(g_path)
    a1_candidate.crop(tuple(roi)).save(a1_crop_path)
    g_candidate.crop(tuple(roi)).save(g_crop_path)
    return {
        "status": "quicklook_created",
        "a1_candidate": {"path": rel(a1_path), "metrics": a1_stats, **image_stats(a1_path)},
        "g_candidate": {"path": rel(g_path), "metrics": g_stats, **image_stats(g_path)},
        "a1_roi_crop": {"path": rel(a1_crop_path), **image_stats(a1_crop_path)},
        "g_roi_crop": {"path": rel(g_crop_path), **image_stats(g_crop_path)},
        "alpha_mask": {"path": rel(alpha_path), **image_stats(alpha_path)},
        "vggt_prior_heatmap": {"path": rel(prior_path), **image_stats(prior_path)},
        "operator": {
            "description": "DB60 operator reused with DB61 fresh remote result JSON as the only VGGT evidence input.",
            "alpha_stats": alpha_stats,
            "vggt_prior_stats": vggt_stats,
        },
        "_images_for_board": {
            "a1": a1,
            "g": g,
            "a1_candidate": a1_candidate,
            "g_candidate": g_candidate,
            "alpha": alpha,
            "prior": prior,
        },
    }


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=(235, 235, 235), size=16) -> None:
    draw.text(xy, str(text), fill=fill, font=font(size))


def panel(board: Image.Image, image: Image.Image | Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(24, 27, 32), outline=(86, 91, 101), width=2)
    if isinstance(image, Path):
        if not image.exists():
            draw_text(draw, (x0 + 12, y0 + 30), "missing", fill=(246, 142, 142), size=15)
            draw_text(draw, (x0 + 12, y1 - 31), label, fill=(220, 230, 245), size=13)
            return
        img = Image.open(image).convert("RGB")
    else:
        img = image.convert("RGB")
    img.thumbnail((x1 - x0 - 20, y1 - y0 - 48))
    board.paste(img, (x0 + (x1 - x0 - img.width) // 2, y0 + 10))
    draw_text(draw, (x0 + 12, y1 - 31), label, fill=(220, 230, 245), size=13)


def build_board(manifest: dict[str, Any], quick: dict[str, Any] | None) -> None:
    board = Image.new("RGB", (2400, 1700), (15, 17, 22))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (40, 28), "DB61 fresh A100 VGGT rerun + ungated A1/G quick-look", size=28)
    draw_text(draw, (40, 70), "Fresh official VGGT result only. Presentation-only; not source-faithful repair.", fill=(246, 214, 150), size=16)
    remote = manifest["remote_result"]
    vggt = remote.get("vggt", {})
    job = remote.get("colab_job", {})
    status = remote.get("runtime_status_pre_exec", {})
    lines = [
        f"runtime={status.get('runtime_type')} gpu={status.get('gpu_name')} free_mb={status.get('gpu_mem_free_mb')}",
        f"job_state={job.get('state')} exit={job.get('exit_code')} duration={job.get('duration_s')}",
        f"model={vggt.get('model_id')} inference_ok={vggt.get('inference_ok')} duration={vggt.get('duration_s')}",
        f"remote_error={remote.get('error')}",
        f"secret_hits={len(manifest['token_scan_hits'])} source_faithful=false presentation_only=true",
    ]
    y = 112
    for line in lines:
        draw_text(draw, (52, y), line, fill=(224, 232, 245), size=15)
        y += 25
    if quick and "_images_for_board" in quick:
        imgs = quick["_images_for_board"]
        roi = TARGET["roi_xyxy"]
        db60 = load_module(DB60_SCRIPT, "db60_board_helpers")
        panel(board, imgs["a1"].crop(tuple(roi)), (40, 270, 600, 515), "A1 original DB25 ROI")
        panel(board, imgs["a1_candidate"].crop(tuple(roi)), (620, 270, 1180, 515), "A1 DB61 quick-look")
        panel(board, db60.diff_crop(imgs["a1"], imgs["a1_candidate"], roi), (1200, 270, 1760, 515), "A1 diff x5")
        panel(board, imgs["g"].crop(tuple(roi)), (1780, 270, 2340, 515), "G original DB25 ROI")
        panel(board, imgs["g_candidate"].crop(tuple(roi)), (40, 540, 600, 785), "G DB61 quick-look")
        panel(board, db60.diff_crop(imgs["g"], imgs["g_candidate"], roi), (620, 540, 1180, 785), "G diff x5")
        panel(board, db60.heat_color(imgs["prior"]), (1200, 540, 1760, 785), "Fresh DB61 VGGT prior")
        panel(board, db60.heat_color(imgs["alpha"] / max(float(imgs["alpha"].max()), 1e-6)), (1780, 540, 2340, 785), "DB61 alpha")
        panel(board, Image.open(quick["a1_candidate"]["path"]).convert("RGB").resize((512, 256)), (40, 810, 600, 1055), "A1 full candidate")
        panel(board, Image.open(quick["g_candidate"]["path"]).convert("RGB").resize((512, 256)), (620, 810, 1180, 1055), "G full candidate")
    panel(board, DB45K_BOARD, (1200, 810, 1760, 1055), "DB45k blocker context")
    panel(board, DB60_BOARD, (1780, 810, 2340, 1055), "DB60 prior quick-look context")
    y = 1110
    draw_text(draw, (40, y), "Claim boundary", size=22)
    y += 38
    for key, value in manifest["claim_boundaries"].items():
        draw_text(draw, (58, y), f"{key}: {value}", fill=(246, 214, 150), size=15)
        y += 25
    draw_text(draw, (40, 1640), f"Manifest: {rel(MANIFEST)}", fill=(185, 190, 200), size=13)
    board.save(BOARD, quality=92)


def build_manifest(remote: dict[str, Any]) -> dict[str, Any]:
    quick: dict[str, Any] | None = None
    if remote.get("vggt", {}).get("inference_ok") is True and remote.get("target_uv_sampling", {}).get(TARGET["roi_key"]):
        quick = make_quicklook(remote)
    target_keys = sorted(list((remote.get("target_uv_sampling") or {}).keys()))
    job_id = remote.get("colab_job", {}).get("job_id")
    vggt_ok = remote.get("vggt", {}).get("inference_ok") is True
    a100_job_submitted = bool(job_id)
    manifest: dict[str, Any] = {
        "db": "DB-61",
        "status": "fresh_vggt_quicklook_created" if quick else "fresh_vggt_blocked_or_failed",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "target": TARGET,
        "scope": {
            "fresh_a100_vggt_run": vggt_ok,
            "a100_job_submitted": a100_job_submitted,
            "uses_db45f_result_json_as_evidence": False,
            "db45f_script_used_as_template_only": True,
            "remote_status_or_exec": bool(remote.get("runtime_status_pre_exec")) or remote.get("scope", {}).get("remote_status_or_exec_attempted") is True,
            "new_vggt_inference": vggt_ok,
            "target_roi_keys": target_keys,
            "db25_only_remote_scope": set(target_keys).issubset({TARGET["roi_key"]}),
            "dit_flux_prompt_generation": False,
            "inpainting": False,
            "source_replacement": False,
            "source_id_map_created": False,
            "db41_edited": False,
            "permission_change": False,
        },
        "remote_result": sanitize(remote),
        "quicklook": {k: v for k, v in quick.items() if k != "_images_for_board"} if quick else None,
        "inputs": {
            "remote_vggt_json": rel(REMOTE_RESULT),
            "a1_view_none": rel(A1_PANO),
            "g_bmw_pano": rel(G_PANO),
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
            "output_dir": rel(OUT_DIR),
        },
        "claim_boundaries": {
            "source_faithful": False,
            "raw_camera_backed_repair": False,
            "presentation_only": True,
            "diagnostic_stress_test": True,
            "a1_g_repaired": False,
            "db32_changed": False,
            "source_id_map": False,
            "red_promotion": False,
            "bosch_training_ready": False,
        },
    }
    manifest["token_scan_hits"] = token_hits(manifest)
    manifest["hard_checks_passed"] = all(
        [
            len(manifest["token_scan_hits"]) == 0,
            manifest["scope"]["uses_db45f_result_json_as_evidence"] is False,
            manifest["scope"]["db25_only_remote_scope"] is True,
            manifest["scope"]["source_replacement"] is False,
            manifest["claim_boundaries"]["source_faithful"] is False,
            manifest["claim_boundaries"]["presentation_only"] is True,
        ]
    )
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    build_board(manifest, quick)
    return manifest


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=1800)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.run_remote:
        try:
            remote = run_remote(args.timeout_s)
        except Exception as exc:
            remote = {
                "db": "DB-61",
                "error": {
                    "type": type(exc).__name__,
                    "message": sanitize(str(exc)),
                    "trace_tail": sanitize(traceback.format_exc()[-1800:]),
                    "stage": "local_status_or_exec_submission_before_a100_job",
                },
                "scope": {
                    "remote_status_or_exec_attempted": True,
                    "a100_job_submitted": False,
                    "new_vggt_inference": False,
                },
            }
            REMOTE_RESULT.write_text(json.dumps(sanitize(remote), indent=2), encoding="utf-8")
    else:
        remote = read_json(REMOTE_RESULT) if REMOTE_RESULT.exists() else {"db": "DB-61", "error": {"type": "MissingRemoteResult"}}
    manifest = build_manifest(remote)
    print(json.dumps({
        "status": manifest["status"],
        "manifest": rel(MANIFEST),
        "board": rel(BOARD),
        "remote_exit": manifest["remote_result"].get("colab_job", {}).get("exit_code"),
        "vggt_inference_ok": manifest["remote_result"].get("vggt", {}).get("inference_ok"),
        "hard_checks_passed": manifest["hard_checks_passed"],
        "token_scan_hits": len(manifest["token_scan_hits"]),
        "claim": "presentation-only; not source-faithful repair",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
