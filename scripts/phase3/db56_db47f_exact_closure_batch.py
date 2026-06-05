from __future__ import annotations

import base64
import hashlib
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
DB47_DIR = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining"
DB28_DIR = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine"
OUT_DIR = DB47_DIR
MANIFEST = OUT_DIR / "db56_db47f_exact_closure_manifest.json"
BOARD = OUT_DIR / "db56_db47f_exact_closure_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
DB47F = DB47_DIR / "db47f_fixed_universe_exact_closure_preflight_manifest.json"
DB53 = ROOT / "deliverables" / "dit360_v2" / "db53_db47f_launch_harness" / "db53_db47f_launch_harness_manifest.json"
DB54 = ROOT / "deliverables" / "dit360_v2" / "db54_local_artifact_recovery" / "db54_local_exact_asset_recovery_manifest.json"
DB55 = ROOT / "deliverables" / "dit360_v2" / "db55_egsr_o3_photometric_operator" / "db55_egsr_o3_photometric_operator_manifest.json"
DB41_BOARD = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_board.jpg"
DB47E_BOARD = DB47_DIR / "db47e_final_candidate_review_board.jpg"
DB32 = ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_generated_sky_harmonize_s40.png"

TARGET_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/seamroute"
REMOTE_WORKDIR_CANDIDATES = [
    "/content/waymo2panorama",
    "/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama",
]

TARGETS = [
    {"candidate_id": "02a00399_a0201", "anchor": 201, "bucket": "strict_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0209", "anchor": 209, "bucket": "strict_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0210", "anchor": 210, "bucket": "strict_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0211", "anchor": 211, "bucket": "strict_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0031", "anchor": 31, "bucket": "relaxed_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0038", "anchor": 38, "bucket": "relaxed_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0040", "anchor": 40, "bucket": "relaxed_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0105", "anchor": 105, "bucket": "strict_review_bucket", "required": ["final"]},
]

DEFAULT_RUNTIME_SECRET_FILES = [
    Path.home() / ".waymo2panorama" / "runtime" / "active_url.json",
    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Waymo2Panorama" / "runtime" / "active_url.json",
]

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "cloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com"),
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
        return str(p).replace("\\", "/")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        clean: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in {"token", "authorization", "headers"}:
                clean[k] = "<redacted>"
            else:
                clean[k] = sanitize(v)
        return clean
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str):
        s = obj
        s = re.sub(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", "<redacted-url>", s)
        s = re.sub(r"hf_[A-Za-z0-9]{20,}", "<redacted-hf-token>", s)
        s = re.sub(r"Bearer\s+[A-Za-z0-9._\-]{20,}", "Bearer <redacted>", s, flags=re.IGNORECASE)
        s = re.sub(r'"token"\s*:\s*"[0-9a-fA-F]{32}"', '"token":"<redacted>"', s)
        return s
    return obj


def token_hits_text(label: str, text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pattern in TOKEN_PATTERNS.items():
        found = pattern.findall(text)
        if found:
            hits.append({"path": label, "pattern": name, "count": len(found)})
    return hits


def token_hits_files(paths: list[Path]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # The script itself contains the literal header string "Bearer " but no token.
        text = re.sub(r'Bearer "\s*\+\s*token', "Bearer <code>", text)
        for name, pattern in TOKEN_PATTERNS.items():
            found = pattern.findall(text)
            if found:
                hits.append({"path": rel(path), "pattern": name, "count": len(found)})
    return hits


class ColabClient:
    def __init__(self) -> None:
        runtime = load_runtime_secret()
        self.url = runtime["url"].rstrip("/")
        self.token = runtime["token"]
        self.source = runtime["source"]

    def _req(self, method: str, path: str, body: dict[str, Any] | None = None, params: dict[str, str] | None = None, timeout: int = 180) -> dict[str, Any]:
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
        return self._req("GET", path, timeout=timeout)

    def post(self, path: str, body: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
        return self._req("POST", path, body=body, timeout=timeout)

    def read_file(self, remote_path: str, max_size_mb: int = 80) -> bytes | None:
        resp = self._req(
            "GET",
            "/read",
            params={"path": remote_path, "base64": "true", "max_size_mb": str(max_size_mb)},
            timeout=240,
        )
        if "content" not in resp:
            return None
        return base64.b64decode(resp["content"])


def remote_path(anchor: int, kind: str) -> str:
    tag = f"bmw_db28_a{anchor}"
    if kind == "compare":
        return f"{REMOTE_OUT}/SR_{tag}_compare.jpg"
    if kind == "final":
        return f"{REMOTE_OUT}/SR_{tag}_final_1024x2048.png"
    raise ValueError(kind)


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

    raise RuntimeError("missing approved runtime source: set COLAB_URL/COLAB_TOKEN or W2P_RUNTIME_SECRET_FILE")


def local_path(anchor: int, kind: str) -> Path:
    if kind == "compare":
        return DB28_DIR / f"SR_bmw_db28_a{anchor}_compare.jpg"
    if kind == "final":
        return DB28_DIR / f"SR_bmw_db28_a{anchor}_final_1024x2048.png"
    raise ValueError(kind)


def image_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        with Image.open(path) as img:
            return {"exists": True, "size": list(img.size), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    except Exception as exc:
        return {"exists": True, "image_read_error": type(exc).__name__, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def remote_bash() -> str:
    workdirs = " ".join(REMOTE_WORKDIR_CANDIDATES)
    runs = "\n".join(
        f"python scripts/phase3/_seamroute.py --uuid {TARGET_UUID} --anchor {row['anchor']} --tag bmw_db28_a{row['anchor']}"
        for row in TARGETS
    )
    targets_json = json.dumps([{"anchor": row["anchor"], "required": row["required"]} for row in TARGETS])
    return f"""set -euo pipefail
echo "[db56] locating repo"
WORKDIR=""
for d in {workdirs}; do
  if [ -f "$d/scripts/phase3/_seamroute.py" ]; then WORKDIR="$d"; break; fi
done
if [ -z "$WORKDIR" ]; then echo "[db56] missing _seamroute.py"; exit 31; fi
cd "$WORKDIR"
if [ ! -d "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/{TARGET_UUID}" ]; then
  echo "[db56] missing target AV2 log"; exit 32
fi
echo "[db56] workdir=$WORKDIR"
git rev-parse --short HEAD 2>/dev/null || true
mkdir -p "{REMOTE_OUT}"
{runs}
python - <<'PY'
import hashlib, json, os
from pathlib import Path

targets = {targets_json!r}
targets = json.loads(targets)
out = Path("{REMOTE_OUT}")
rows = []
for row in targets:
    anchor = int(row["anchor"])
    tag = f"bmw_db28_a{{anchor}}"
    assets = {{}}
    for kind, suffix in [("compare", "compare.jpg"), ("final", "final_1024x2048.png")]:
        p = out / f"SR_{{tag}}_{{suffix}}"
        if p.exists():
            h = hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            assets[kind] = {{"path": str(p), "exists": True, "bytes": p.stat().st_size, "sha256": h.hexdigest()}}
        else:
            assets[kind] = {{"path": str(p), "exists": False}}
    rows.append({{"anchor": anchor, "tag": tag, "required": row["required"], "assets": assets}})
try:
    import subprocess
    git_head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
except Exception:
    git_head = None
result = {{"workdir": os.getcwd(), "git_head": git_head, "remote_output_root": str(out), "targets": rows}}
print("DB56_RESULT_JSON_START")
print(json.dumps(result, sort_keys=True))
print("DB56_RESULT_JSON_END")
PY
"""


def extract_remote_result(log: str) -> dict[str, Any] | None:
    m = re.search(r"DB56_RESULT_JSON_START\s*(\{.*?\})\s*DB56_RESULT_JSON_END", log, flags=re.S)
    if not m:
        return None
    return json.loads(m.group(1))


def fetch_required_assets(client: ColabClient, result: dict[str, Any]) -> dict[str, Any]:
    remote_result = result.get("remote_result") or {}
    remote_targets = {int(row["anchor"]): row for row in remote_result.get("targets", [])}
    has_remote_result = bool(remote_targets)
    for row in TARGETS:
        anchor = int(row["anchor"])
        remote_row = remote_targets.get(anchor, {})
        for kind in row["required"]:
            rp = remote_path(anchor, kind)
            remote_asset = (remote_row.get("assets") or {}).get(kind, {})
            remote_exists = remote_asset.get("exists") if has_remote_result else None
            remote_sha = remote_asset.get("sha256")
            fetch_record: dict[str, Any] = {
                "candidate_id": row["candidate_id"],
                "anchor": anchor,
                "asset": kind,
                "remote_path": rp,
                "remote_exists": remote_exists,
                "remote_sha256": remote_sha,
                "remote_existence_source": "remote_result_json" if has_remote_result else "direct_read_probe",
                "local_path": rel(local_path(anchor, kind)),
                "fetched": False,
            }
            should_read = remote_exists is True or not has_remote_result
            if should_read:
                try:
                    raw = client.read_file(rp, max_size_mb=80)
                    if raw is not None:
                        lp = local_path(anchor, kind)
                        lp.parent.mkdir(parents=True, exist_ok=True)
                        lp.write_bytes(raw)
                        local_sha = sha256_bytes(raw)
                        fetch_record.update(
                            {
                                "remote_exists": True,
                                "fetched": True,
                                "local_bytes": lp.stat().st_size,
                                "local_sha256": local_sha,
                                "sha256_match": (local_sha == remote_sha) if remote_sha else None,
                            }
                        )
                    else:
                        fetch_record["remote_exists"] = False
                        fetch_record["error"] = "read_response_missing_content"
                except Exception as exc:
                    fetch_record["remote_exists"] = False
                    fetch_record["error"] = f"{type(exc).__name__}: {sanitize(str(exc))}"
            result["fetches"].append(fetch_record)
    return result


def run_remote(timeout_s: int) -> dict[str, Any]:
    client = ColabClient()
    result: dict[str, Any] = {
        "runtime_secret_source": client.source,
        "fetch_mode": "exec_then_fetch",
        "remote_status": None,
        "job": None,
        "remote_result": None,
        "remote_result_parse_status": None,
        "fetches": [],
        "errors": [],
    }
    try:
        status = client.get("/status", timeout=30)
        result["remote_status"] = sanitize(status)
        active_jobs = status.get("active_jobs")
        if isinstance(active_jobs, int) and active_jobs > 0:
            result["errors"].append({"stage": "status", "error": "active_jobs_nonzero", "active_jobs": active_jobs})
            return result
        job = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash()], "cwd": "/content", "timeout_s": timeout_s}, timeout=120)
        job_id = job["job_id"]
        started = time.time()
        while True:
            time.sleep(8)
            state = client.get(f"/jobs/{job_id}", timeout=60)
            if state.get("state") != "running":
                log_tail = state.get("log_tail", "")
                result["job"] = {
                    "job_id": job_id,
                    "state": state.get("state"),
                    "exit_code": state.get("exit_code"),
                    "duration_s": state.get("duration_s"),
                    "log_tail_sanitized": sanitize(log_tail),
                }
                result["remote_result"] = extract_remote_result(log_tail)
                result["remote_result_parse_status"] = "parsed" if result["remote_result"] else "missing_marker_or_truncated_log_tail"
                break
            if time.time() - started > timeout_s + 120:
                result["job"] = {"job_id": job_id, "state": "local_poll_timeout", "elapsed_s": round(time.time() - started, 1)}
                result["errors"].append({"stage": "poll", "error": "local_poll_timeout"})
                break
    except Exception as exc:
        result["errors"].append({"stage": "remote", "error": type(exc).__name__, "message": sanitize(str(exc))})
        return result

    return fetch_required_assets(client, result)


def run_fetch_only() -> dict[str, Any]:
    client = ColabClient()
    previous: dict[str, Any] = {}
    if MANIFEST.exists():
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        previous = (data.get("remote") or {}).get("job") or {}
    result: dict[str, Any] = {
        "runtime_secret_source": client.source,
        "fetch_mode": "fetch_only_existing_remote_paths",
        "remote_status": None,
        "job": previous,
        "remote_result": None,
        "remote_result_parse_status": "not_required_for_fetch_only",
        "fetches": [],
        "errors": [],
    }
    try:
        result["remote_status"] = client.get("/status", timeout=60)
        if not previous:
            result["errors"].append({"stage": "fetch_only", "error": "missing_previous_remote_job"})
            return result
        if previous.get("exit_code") != 0:
            result["errors"].append({"stage": "fetch_only", "error": "previous_remote_job_not_successful", "exit_code": previous.get("exit_code")})
            return result
    except Exception as exc:
        result["errors"].append({"stage": "fetch_only", "error": type(exc).__name__, "message": sanitize(str(exc))})
        return result
    return fetch_required_assets(client, result)


def build_targets(remote: dict[str, Any] | None) -> list[dict[str, Any]]:
    fetches = {(int(f["anchor"]), f["asset"]): f for f in (remote or {}).get("fetches", [])}
    rows: list[dict[str, Any]] = []
    for target in TARGETS:
        anchor = int(target["anchor"])
        assets: dict[str, Any] = {}
        missing_required: list[str] = []
        for kind in ["compare", "final"]:
            lp = local_path(anchor, kind)
            stats = image_stats(lp)
            f = fetches.get((anchor, kind), {})
            assets[kind] = {
                "local_path": rel(lp),
                "required": kind in target["required"],
                "exists": stats.get("exists", False),
                "stats": stats,
                "fetch": f or None,
            }
            if kind in target["required"] and not stats.get("exists", False):
                missing_required.append(kind)
        rows.append(
            {
                **target,
                "tag": f"bmw_db28_a{anchor}",
                "assets": assets,
                "missing_required": missing_required,
                "closure_asset_status": "complete" if not missing_required else "missing_required_assets",
                "accepted_final_candidate": False,
                "claim_boundary": "exact source-selection evidence only; not local seam repair and not original-G repair",
            }
        )
    return rows


def hard_checks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    brief = BRIEF.read_text(encoding="utf-8", errors="replace")
    targets = manifest["targets"]
    job = manifest.get("remote", {}).get("job") or {}
    scope = manifest["scope"]
    missing = [row for row in targets if row["missing_required"]]
    target_ids = [row["candidate_id"] for row in targets]
    expected_ids = [row["candidate_id"] for row in TARGETS]
    checks = [
        {
            "id": "db56_brief_exists",
            "pass": "# DB-56: DB47f exact closure batch execution" in brief,
            "evidence": "DB56 brief exists; its status may be running or paused after a kill criterion.",
        },
        {
            "id": "fixed_universe_eight_targets",
            "pass": target_ids == expected_ids and len(targets) == 8,
            "evidence": f"target_ids={target_ids}",
        },
        {
            "id": "one_remote_job_at_most",
            "pass": bool(job) and scope["remote_exec"] is True and scope["remote_exec_count"] == 1,
            "evidence": f"job_id={job.get('job_id')}; state={job.get('state')}; exit={job.get('exit_code')}",
        },
        {
            "id": "remote_job_succeeded",
            "pass": job.get("exit_code") == 0,
            "evidence": f"state={job.get('state')}; exit={job.get('exit_code')}",
        },
        {
            "id": "all_required_assets_present",
            "pass": not missing,
            "evidence": f"missing={[(r['candidate_id'], r['missing_required']) for r in missing]}",
        },
        {
            "id": "no_disallowed_models_or_generation",
            "pass": all(scope[k] is False for k in ["hf_or_vggt", "model_inference", "diffusion_or_generation", "source_replacement", "source_id_map_created", "permission_change", "red_promotion"]),
            "evidence": "Scope flags preserve no model/generation/source-map/RED boundary.",
        },
        {
            "id": "secret_scan_pass",
            "pass": manifest.get("secret_scan_hits") == [],
            "evidence": f"hits={manifest.get('secret_scan_hits')}",
        },
    ]
    return checks


def build_manifest(remote: dict[str, Any] | None = None) -> dict[str, Any]:
    targets = build_targets(remote)
    remote = remote or {}
    required_total = sum(len(row["required"]) for row in TARGETS)
    required_present = sum(
        1
        for row in targets
        for kind in row["required"]
        if row["assets"][kind]["exists"]
    )
    remote_job = remote.get("job") or {}
    remote_status = remote.get("remote_status") or {}
    scope = {
        "remote_status": bool(remote_status),
        "remote_exec": bool(remote_job),
        "remote_exec_count": 1 if remote_job else 0,
        "a100_executor_used": bool(remote_status),
        "hf_or_vggt": False,
        "model_inference": False,
        "diffusion_or_generation": False,
        "dataset_scan_beyond_fixed_log": False,
        "seamroute_fixed_batch_executed": bool(remote_job),
        "exact_asset_fetch_or_copy": bool(remote.get("fetches")),
        "panorama_repair_claim": False,
        "source_replacement": False,
        "source_id_map_created": False,
        "permission_change": False,
        "red_promotion": False,
        "output_location": rel(OUT_DIR),
    }
    status = "accepted_exact_closure_assets_complete" if required_present == required_total and remote_job.get("exit_code") == 0 else "paused_or_incomplete_exact_closure"
    manifest: dict[str, Any] = {
        "db": "DB56",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "evidence_type": "db47f-exact-source-selection-closure-batch-only",
        "purpose": "Run one bounded DB47f closure batch to fetch exact compare/final source-selection assets for the fixed 8-anchor universe.",
        "scope": scope,
        "inputs": {
            "decision_brief": rel(BRIEF),
            "db47f_preflight_manifest": rel(DB47F),
            "db53_launch_harness_manifest": rel(DB53),
            "db54_local_recovery_manifest": rel(DB54),
            "db55_o3_manifest": rel(DB55),
        },
        "runtime_observed_sanitized": {
            "runtime_type": remote_status.get("runtime_type"),
            "version": remote_status.get("version"),
            "gpu_name": remote_status.get("gpu_name"),
            "active_jobs_before": remote_status.get("active_jobs"),
            "runtime_secret_source": remote.get("runtime_secret_source"),
        },
        "remote": sanitize({k: v for k, v in remote.items() if k in {"fetch_mode", "job", "remote_result", "remote_result_parse_status", "fetches", "errors"}}),
        "fixed_target_contract": {
            "target_uuid": TARGET_UUID,
            "target_ids": [row["candidate_id"] for row in targets],
            "anchors": [row["anchor"] for row in targets],
            "required_asset_count": required_total,
            "required_asset_present_count": required_present,
            "all_required_assets_present": required_present == required_total,
        },
        "targets": targets,
        "decision": {
            "accepted_exact_source_selection_closure_assets": required_present == required_total,
            "accepted_final_candidate": False,
            "accepted_source_faithful_repair": False,
            "accepted_original_g_family_repair": False,
            "source_id_map_created": False,
            "db41_or_db25_promoted": False,
            "ready_for_uncaveated_bosch_training_data": False,
            "claim_boundary": "DB56 closes or pauses exact source-selection assets only; visual final-candidate selection remains a separate review step.",
        },
    }
    preview = json.dumps(sanitize(manifest), sort_keys=True)
    hits = token_hits_text("manifest_preview", preview) + token_hits_files([Path(__file__), BRIEF])
    manifest["secret_scan_hits"] = hits
    manifest["hard_checks"] = hard_checks(manifest)
    manifest["hard_checks_pass"] = all(row["pass"] for row in manifest["hard_checks"])
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(sanitize(manifest), indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=(235, 235, 235), size=16) -> None:
    draw.text(xy, str(text), fill=fill, font=font(size))


def draw_wrapped(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, width: int, fill=(235, 235, 235), size: int = 14) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + 6
    return y


def image_box(board: Image.Image, path: Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((x1 - x0 - 16, y1 - y0 - 44))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception as exc:
            draw_wrapped(draw, x0 + 10, y0 + 26, f"load failed: {type(exc).__name__}", 42, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, ok: bool, w: int = 230) -> int:
    fill = (38, 104, 70) if ok else (128, 68, 54)
    draw.rounded_rectangle((x, y, x + w, y + 34), radius=5, fill=fill, outline=(180, 180, 180))
    draw_text(draw, (x + 10, y + 8), text, size=13)
    return x + w + 12


def build_board(manifest: dict[str, Any]) -> None:
    W, H = 2400, 2500
    board = Image.new("RGB", (W, H), (15, 17, 22))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (40, 30), "DB56 DB47f Exact Closure Batch", size=30)
    draw_text(draw, (40, 72), "source-selection exact closure only / no repair / no source_id_map / no RED promotion / no token in artifacts", fill=(250, 220, 160), size=17)

    x = 40
    y = 112
    x = pill(draw, x, y, f"status: {manifest['status']}", manifest["status"].startswith("accepted"), 390)
    x = pill(draw, x, y, f"required {manifest['fixed_target_contract']['required_asset_present_count']}/{manifest['fixed_target_contract']['required_asset_count']}", manifest["fixed_target_contract"]["all_required_assets_present"], 250)
    x = pill(draw, x, y, f"job exit {((manifest.get('remote') or {}).get('job') or {}).get('exit_code')}", ((manifest.get("remote") or {}).get("job") or {}).get("exit_code") == 0, 170)
    x = pill(draw, x, y, f"secret hits {len(manifest.get('secret_scan_hits', []))}", len(manifest.get("secret_scan_hits", [])) == 0, 190)
    x = pill(draw, x, y, "no model/gen/source-map", True, 245)

    y = 168
    rt = manifest.get("runtime_observed_sanitized", {})
    draw_wrapped(
        draw,
        40,
        y,
        f"Runtime observed: type={rt.get('runtime_type')}, gpu={rt.get('gpu_name')}, active_jobs_before={rt.get('active_jobs_before')}. "
        f"Remote job: {((manifest.get('remote') or {}).get('job') or {}).get('job_id')} state={((manifest.get('remote') or {}).get('job') or {}).get('state')}. "
        "Endpoint and token are intentionally absent from this board/manifest.",
        180,
        fill=(210, 225, 245),
        size=15,
    )

    y = 245
    draw_text(draw, (40, y), "Fixed 8-anchor required assets", size=20)
    y += 34
    col_w = 560
    row_h = 230
    for idx, row in enumerate(manifest["targets"]):
        cx = 40 + (idx % 4) * (col_w + 20)
        cy = y + (idx // 4) * (row_h + 36)
        draw.rectangle((cx, cy, cx + col_w, cy + row_h), fill=(24, 27, 34), outline=(72, 78, 92), width=2)
        ok = not row["missing_required"]
        draw_text(draw, (cx + 12, cy + 10), f"{row['candidate_id']}  anchor={row['anchor']}  {row['bucket']}", fill=(245, 245, 245), size=14)
        draw_text(draw, (cx + 12, cy + 34), f"status={row['closure_asset_status']}  missing={row['missing_required']}", fill=(160, 235, 175) if ok else (245, 150, 130), size=13)
        image_box(board, local_path(int(row["anchor"]), "compare"), (cx + 12, cy + 60, cx + 270, cy + row_h - 10), "compare")
        image_box(board, local_path(int(row["anchor"]), "final"), (cx + 286, cy + 60, cx + col_w - 12, cy + row_h - 10), "final")

    y = 245 + 2 * (row_h + 36) + 26
    draw_text(draw, (40, y), "Context and boundaries", size=20)
    y += 34
    image_box(board, DB47E_BOARD, (40, y, 590, y + 340), "DB47e a200/source-sidestep context")
    image_box(board, DB32, (620, y, 1170, y + 340), "DB32 s40 caveated handoff")
    image_box(board, DB41_BOARD, (1200, y, 1750, y + 340), "DB41 right/lower-right abstain")
    text_x = 1790
    text_y = y + 10
    text_y = draw_wrapped(draw, text_x, text_y, "Allowed claim: DB56 may close exact source-selection asset availability for the fixed DB47f universe.", 54, fill=(220, 235, 245), size=15)
    text_y = draw_wrapped(draw, text_x, text_y + 14, "Forbidden claims: source-faithful local repair, original G/A1/BEST repair, DB41/DB25 repair, source_id_map, RED promotion, uncaveated Bosch training data.", 54, fill=(250, 200, 160), size=15)
    draw_wrapped(draw, text_x, text_y + 14, "Next step after asset closure: visual final-candidate review in a separate decision state; no patch-on-patch if a candidate fails.", 54, fill=(210, 225, 245), size=15)

    y += 380
    draw_text(draw, (40, y), "Hard checks", size=20)
    y += 34
    for check in manifest["hard_checks"]:
        color = (155, 235, 170) if check["pass"] else (245, 145, 125)
        draw_text(draw, (60, y), f"{'PASS' if check['pass'] else 'FAIL'} {check['id']}: {check['evidence']}", fill=color, size=14)
        y += 25

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--run-remote", action="store_true", help="Run exactly one DB47f remote closure batch using COLAB_URL/COLAB_TOKEN.")
    ap.add_argument("--fetch-only", action="store_true", help="Fetch deterministic DB56 assets from an existing successful remote job without submitting /exec.")
    ap.add_argument("--timeout-s", type=int, default=3600)
    args = ap.parse_args()

    remote: dict[str, Any] | None = None
    if args.run_remote and args.fetch_only:
        raise SystemExit("--run-remote and --fetch-only are mutually exclusive")
    if args.run_remote:
        remote = run_remote(args.timeout_s)
    elif args.fetch_only:
        remote = run_fetch_only()
    manifest = build_manifest(remote)
    build_board(manifest)
    print(json.dumps({"manifest": rel(MANIFEST), "board": rel(BOARD), "status": manifest["status"], "hard_checks_pass": manifest["hard_checks_pass"]}, sort_keys=True))
    return 0 if manifest["hard_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
