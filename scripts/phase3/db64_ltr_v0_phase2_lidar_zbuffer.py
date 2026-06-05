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
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db64_ltr_v0/phase2_lidar_zbuffer"
REMOTE_RESULT = REMOTE_OUT + "/db64_phase2_lidar_zbuffer_remote_result.json"
REMOTE_SUMMARY = REMOTE_OUT + "/batch_summary.json"
REMOTE_COMPACT = REMOTE_OUT + "/lidar_zbuffer_three_anchor_compact_review.jpg"

LOCAL_REMOTE_RESULT = OUT_DIR / "db64_phase2_lidar_zbuffer_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "db64_phase2_lidar_zbuffer_batch_summary.json"
MANIFEST = OUT_DIR / "db64_ltr_v0_phase2_lidar_zbuffer_manifest.json"
BOARD = OUT_DIR / "db64_ltr_v0_phase2_lidar_zbuffer_board.jpg"
FETCH_DIR = OUT_DIR / "phase2_lidar_zbuffer_fetch"

CASES = ["02a00399:0:bmw", "0bae3b5e:30:clean_far"]
RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]
REMOTE_WORKDIR_CANDIDATES = [
    "/content/waymo2panorama",
    "/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama",
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

REMOTE_OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db64_ltr_v0/phase2_lidar_zbuffer")
REMOTE_RESULT = REMOTE_OUT / "db64_phase2_lidar_zbuffer_remote_result.json"
AV2_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [
    Path("/content/waymo2panorama"),
    Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama"),
]
CASES = ["02a00399:0:bmw", "0bae3b5e:30:clean_far"]
RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]

def tail(text, limit=16000):
    if text is None:
        return ""
    return text[-limit:]

def find_workdir():
    for cand in WORKDIR_CANDIDATES:
        script = cand / "scripts" / "phase3" / "test_lidar_zbuffer_seam.py"
        if script.exists():
            return cand
    return None

def file_row(path):
    return {
        "exists": path.exists(),
        "bytes": int(path.stat().st_size) if path.exists() and path.is_file() else None,
        "path": str(path),
    }

def main():
    t0 = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_phase2_lidar_zbuffer_start",
        "scope": {
            "fixed_cases_only": CASES,
            "a100_required": False,
            "model_inference": False,
            "vggt": False,
            "dit_flux_generation": False,
            "source_replacement": False,
            "diagnostic_lidar_zbuffer_only": True,
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
        result["message"] = "No test_lidar_zbuffer_seam.py found in allowed remote workdir candidates."
        REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("DB64_PHASE2_JSON_BEGIN")
        print(json.dumps(result, ensure_ascii=False))
        print("DB64_PHASE2_JSON_END")
        return

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
        print("DB64_PHASE2_JSON_BEGIN")
        print(json.dumps(result, ensure_ascii=False))
        print("DB64_PHASE2_JSON_END")
        return

    cmd = [
        sys.executable,
        "scripts/phase3/test_lidar_zbuffer_seam.py",
        "--av2-root",
        str(AV2_ROOT),
        "--out-dir",
        str(REMOTE_OUT),
        "--cases",
        *CASES,
        "--review-w",
        "768",
        "--max-sample-per-pair",
        "8000",
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["MPLBACKEND"] = "Agg"
    run_t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3300,
        )
        result["run"] = {
            "returncode": int(proc.returncode),
            "duration_s": round(time.time() - run_t0, 2),
            "stdout_tail": tail(proc.stdout),
            "stderr_tail": tail(proc.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        result["run"] = {
            "returncode": None,
            "timeout": True,
            "duration_s": round(time.time() - run_t0, 2),
            "stdout_tail": tail(exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout),
            "stderr_tail": tail(exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr),
        }

    summary_path = REMOTE_OUT / "batch_summary.json"
    if summary_path.exists():
        try:
            result["batch_summary"] = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as exc:
            result["batch_summary_error"] = repr(exc)

    outputs = {
        "batch_summary": file_row(summary_path),
        "compact_review": file_row(REMOTE_OUT / "lidar_zbuffer_three_anchor_compact_review.jpg"),
    }
    for run_name in RUN_NAMES:
        case_dir = REMOTE_OUT / run_name
        outputs[run_name] = {
            "diagnostics": file_row(case_dir / f"{run_name}_lidar_zbuffer_diagnostics.json"),
            "crop_review": file_row(case_dir / f"{run_name}_lidar_zbuffer_crop_review.jpg"),
            "review": file_row(case_dir / f"{run_name}_lidar_zbuffer_review_768.jpg"),
            "hard_select": file_row(case_dir / f"{run_name}_hard_select.jpg"),
            "lidar_winner": file_row(case_dir / f"{run_name}_lidar_winner.jpg"),
            "lidar_consensus": file_row(case_dir / f"{run_name}_lidar_consensus.jpg"),
            "lidar_best": file_row(case_dir / f"{run_name}_lidar_best.jpg"),
        }
    result["outputs"] = outputs
    result["status"] = "db64_phase2_lidar_zbuffer_completed" if result.get("run", {}).get("returncode") == 0 else "db64_phase2_lidar_zbuffer_failed_or_blocked"
    result["runtime_s"] = round(time.time() - t0, 2)
    REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("DB64_PHASE2_JSON_BEGIN")
    print(json.dumps(result, ensure_ascii=False))
    print("DB64_PHASE2_JSON_END")

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
        "exec(compile(code, '<db64_phase2_lidar_zbuffer_remote>', 'exec'))\n"
        "PY"
    )


def parse_json_from_log(log_tail: str) -> dict[str, Any] | None:
    if "DB64_PHASE2_JSON_BEGIN" not in log_tail or "DB64_PHASE2_JSON_END" not in log_tail:
        return None
    body = log_tail.split("DB64_PHASE2_JSON_BEGIN", 1)[1].split("DB64_PHASE2_JSON_END", 1)[0].strip()
    return json.loads(body)


def fetch_outputs(client: ColabClient) -> dict[str, Any]:
    FETCH_DIR.mkdir(parents=True, exist_ok=True)
    fetched: dict[str, Any] = {}
    items: list[tuple[str, str, Path, int]] = [
        ("batch_summary", REMOTE_SUMMARY, LOCAL_SUMMARY, 16),
        ("compact_review", REMOTE_COMPACT, FETCH_DIR / "lidar_zbuffer_two_case_compact_review.jpg", 30),
    ]
    for run_name in RUN_NAMES:
        base = REMOTE_OUT + "/" + run_name + "/" + run_name
        local_case = FETCH_DIR / run_name
        items.extend(
            [
                (f"{run_name}_diagnostics", base + "_lidar_zbuffer_diagnostics.json", local_case / f"{run_name}_lidar_zbuffer_diagnostics.json", 8),
                (f"{run_name}_crop_review", base + "_lidar_zbuffer_crop_review.jpg", local_case / f"{run_name}_lidar_zbuffer_crop_review.jpg", 30),
                (f"{run_name}_review", base + "_lidar_zbuffer_review_768.jpg", local_case / f"{run_name}_lidar_zbuffer_review_768.jpg", 30),
                (f"{run_name}_hard_select", base + "_hard_select.jpg", local_case / f"{run_name}_hard_select.jpg", 20),
                (f"{run_name}_lidar_winner", base + "_lidar_winner.jpg", local_case / f"{run_name}_lidar_winner.jpg", 20),
                (f"{run_name}_lidar_consensus", base + "_lidar_consensus.jpg", local_case / f"{run_name}_lidar_consensus.jpg", 20),
                (f"{run_name}_lidar_best", base + "_lidar_best.jpg", local_case / f"{run_name}_lidar_best.jpg", 20),
            ]
        )
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


def fmt_float(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except Exception:
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
    board = Image.new("RGB", (1800, 1380), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 24), "DB64 Phase2 LiDAR-zbuffer diagnostic prototype", size=28)
    draw_text(draw, (28, 62), "CPU Colab, fixed BMW target + clean control. Diagnostic only; not complete LTR sidecars or source-faithful repair.", fill=(218, 224, 235), size=15)

    decision = manifest["decision"]
    y = 102
    lines = [
        f"status={manifest['status']} run_ok={decision['run_ok']} a100_needed={decision['a100_needed_now']} secret_hits={manifest['strict_secret_scan']['hit_count']}",
        f"remote runtime={manifest['runtime']['status'].get('runtime_type')} active_jobs={manifest['runtime']['status'].get('active_jobs')} version={manifest['runtime']['status'].get('version')}",
        f"job state={manifest['job'].get('state')} exit={manifest['job'].get('exit_code')} duration={manifest['job'].get('duration_s')}",
        dependency_summary(manifest),
        f"cases={', '.join(CASES)}",
    ]
    for line in lines:
        y = draw_wrapped(draw, 36, y, "- " + line, 142, size=14)

    aggregate = manifest.get("aggregate") or {}
    y += 8
    draw_text(draw, (28, y), "Aggregate", size=21)
    y += 30
    if aggregate:
        changed = aggregate.get("mean_changed_frac") or {}
        ncc = aggregate.get("mean_ncc_pano_vs_winner") or {}
        seam_dy = aggregate.get("mean_seam_dy") or {}
        for line in [
            f"n_cases={aggregate.get('n_cases')} visible_any_support={fmt_float(aggregate.get('mean_visible_any_support_frac'))} visible_ge2_support={fmt_float(aggregate.get('mean_visible_ge2_support_frac'))}",
            f"changed_frac winner={fmt_float(changed.get('lidar_winner'))} consensus={fmt_float(changed.get('lidar_consensus'))} best={fmt_float(changed.get('lidar_best'))}",
            f"ncc hard={fmt_float(ncc.get('hard_select'))} winner={fmt_float(ncc.get('lidar_winner'))} consensus={fmt_float(ncc.get('lidar_consensus'))} best={fmt_float(ncc.get('lidar_best'))}",
            f"seam_dy hard={fmt_float(seam_dy.get('hard_select'))} winner={fmt_float(seam_dy.get('lidar_winner'))} consensus={fmt_float(seam_dy.get('lidar_consensus'))} best={fmt_float(seam_dy.get('lidar_best'))}",
        ]:
            y = draw_wrapped(draw, 36, y, "- " + line, 142, size=14)
    else:
        y = draw_wrapped(draw, 36, y, "- no aggregate summary available", 142, fill=(255, 170, 145), size=14)

    y += 8
    draw_text(draw, (28, y), "Claim Boundary", size=21)
    y += 30
    for line in [
        "LiDAR-zbuffer RGB variants are a diagnostic target-ray visibility precursor.",
        "No source_id_map/layer_id_map/risk_map/unknown/disocclusion sidecars were created here.",
        "Do not call this A1/G/G/DB32 repair; DB41/RED/no-evidence regions are not promoted.",
    ]:
        y = draw_wrapped(draw, 36, y, "- " + line, 142, fill=(255, 235, 185), size=14)

    paste_thumb(board, FETCH_DIR / "lidar_zbuffer_two_case_compact_review.jpg", (28, 470, 880, 750))
    draw_text(draw, (28, 440), "Two-case compact crop review", size=18)
    paste_thumb(board, FETCH_DIR / RUN_NAMES[0] / f"{RUN_NAMES[0]}_lidar_zbuffer_crop_review.jpg", (920, 470, 1770, 750))
    draw_text(draw, (920, 440), "BMW target crop review", size=18)
    paste_thumb(board, FETCH_DIR / RUN_NAMES[0] / f"{RUN_NAMES[0]}_lidar_zbuffer_review_768.jpg", (28, 820, 880, 1340))
    draw_text(draw, (28, 790), "BMW full review", size=18)
    paste_thumb(board, FETCH_DIR / RUN_NAMES[1] / f"{RUN_NAMES[1]}_lidar_zbuffer_review_768.jpg", (920, 820, 1770, 1340))
    draw_text(draw, (920, 790), "Clean control full review", size=18)

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
    raw = client.read_file(REMOTE_RESULT, max_size_mb=12)
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
    aggregate = (batch_summary or {}).get("aggregate")
    run_ok = bool(remote_result.get("status") == "db64_phase2_lidar_zbuffer_completed")

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_ltr_v0_phase2_lidar_zbuffer",
        "accepted_evidence_type": "diagnostic_lidar_zbuffer_target_ray_visibility_precursor",
        "scope": {
            "remote_status_used": True,
            "remote_exec_used": True,
            "exec_count": 1,
            "fixed_cases_only": CASES,
            "a100_used_or_needed": False,
            "model_inference_used": False,
            "vggt_used": False,
            "dit_flux_generation_used": False,
            "bounded_dependency_bootstrap_allowed": True,
            "source_replacement_used": False,
            "complete_ltr_sidecars_created": False,
            "red_promotion": False,
            "diagnostic_only": True,
        },
        "runtime": {
            "secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file",
            "status": safe_status(status),
        },
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail"}}),
        "remote_result": remote_result,
        "dependency": remote_result.get("dependency") if isinstance(remote_result, dict) else None,
        "aggregate": aggregate,
        "fetched_outputs": fetched,
        "local_outputs": {
            "remote_result_json": rel(LOCAL_REMOTE_RESULT),
            "batch_summary": rel(LOCAL_SUMMARY),
            "fetch_dir": rel(FETCH_DIR),
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
        "decision": {
            "run_ok": run_ok,
            "a100_needed_now": False,
            "source_faithful_repair_claim_allowed": False,
            "complete_ltr_v0_sidecar_claim_allowed": False,
            "summary": (
                "CPU LiDAR-zbuffer diagnostic completed; use it only as target-ray visibility precursor evidence."
                if run_ok
                else "CPU LiDAR-zbuffer diagnostic failed or was blocked; do not expand scope without updating DB64."
            ),
            "next_allowed_step": (
                "Inspect visual board and diagnostics; if useful, open a later sidecar-instrumentation sub-scope."
                if run_ok
                else "Archive failure/blocker in progress.md; do not request A100 unless a new brief justifies it."
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
                "run_ok": run_ok,
                "a100_needed_now": False,
                "secret_hits": len(hits),
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
                "fetch_dir": rel(FETCH_DIR),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
