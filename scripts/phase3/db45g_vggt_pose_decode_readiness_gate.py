#!/usr/bin/env python
"""DB45g VGGT pose/pointmap metric-residual readiness gate.

This gate does not run VGGT inference. It only inspects the official VGGT
source/API already present on the Colab runtime to decide whether a future
pointmap-to-LiDAR residual job can be specified without guessing coordinates.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit"
DB45F = OUT_DIR / "db45f_vggt_target_uv_sampling_gate_manifest.json"
REMOTE_RESULT = OUT_DIR / "db45g_vggt_pose_decode_readiness_remote_result.json"
MANIFEST = OUT_DIR / "db45g_vggt_pose_decode_readiness_manifest.json"
BOARD = OUT_DIR / "db45g_vggt_pose_decode_readiness_board.jpg"

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


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    width: int,
    color: tuple[int, int, int],
    size: int = 14,
    line_gap: int = 5,
) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw.text((x, y), line, fill=color, font=font(size))
        y += size + line_gap
    return y


def pill(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fill: tuple[int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=6, fill=fill)
    draw.text((box[0] + 10, box[1] + 7), text, fill=(255, 255, 255), font=font(14))


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
import base64
import gzip
import importlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback

OFFICIAL_REPO = pathlib.Path("/content/vggt_db45d/vggt")
DB45F_DRIVE = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db45f_vggt_target_uv_sampling/db45f_remote_target_uv_sampling_result.json")

OUT = {
    "db": "DB-45g",
    "scope": {
        "source_api_inspection_only": True,
        "model_load": False,
        "model_inference": False,
        "download": False,
        "renderer": False,
        "erp_repair": False,
        "source_replacement": False,
        "generated_image": False,
        "red_promotion": False,
    },
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}

def short_run(cmd, cwd=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        return {"returncode": p.returncode, "stdout": p.stdout.strip()[-1000:], "stderr": p.stderr.strip()[-1000:]}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

def callable_names(module):
    names = []
    for name in dir(module):
        low = name.lower()
        if any(k in low for k in ["pose", "camera", "extr", "intr", "enc", "world"]):
            obj = getattr(module, name, None)
            if callable(obj):
                sig = None
                try:
                    sig = str(inspect.signature(obj))
                except Exception:
                    pass
                names.append({"name": name, "signature": sig})
    return names[:30]

try:
    OUT["official_repo"] = {
        "path": str(OFFICIAL_REPO),
        "exists": OFFICIAL_REPO.exists(),
        "git_head": short_run(["git", "rev-parse", "--short", "HEAD"], cwd=str(OFFICIAL_REPO)).get("stdout") if OFFICIAL_REPO.exists() else None,
    }
    if OFFICIAL_REPO.exists():
        sys.path.insert(0, str(OFFICIAL_REPO))

    modules = [
        "vggt.utils.pose_enc",
        "vggt.utils.geometry",
        "vggt.utils.camera",
        "vggt.utils.load_fn",
        "vggt.models.vggt",
    ]
    imports = {}
    for mod_name in modules:
        try:
            mod = importlib.import_module(mod_name)
            imports[mod_name] = {"ok": True, "callables": callable_names(mod)}
        except Exception as exc:
            imports[mod_name] = {"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]}
    OUT["module_imports"] = imports

    patterns = [
        "pose_encoding_to_extri_intri",
        "pose_enc",
        "extrinsic",
        "intrinsic",
        "world_points",
        "camera",
    ]
    source_hits = []
    if OFFICIAL_REPO.exists():
        for path in sorted(OFFICIAL_REPO.rglob("*.py")):
            rel = str(path.relative_to(OFFICIAL_REPO))
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            low = text.lower()
            if not any(p.lower() in low for p in patterns):
                continue
            lines = text.splitlines()
            hits = []
            for idx, line in enumerate(lines, start=1):
                line_low = line.lower()
                if any(p.lower() in line_low for p in patterns):
                    stripped = re.sub(r"\s+", " ", line.strip())
                    hits.append({"line": idx, "text": stripped[:180]})
                if len(hits) >= 8:
                    break
            if hits:
                source_hits.append({"file": rel, "hits": hits})
            if len(source_hits) >= 18:
                break
    OUT["source_hits"] = source_hits

    db45f = {}
    if DB45F_DRIVE.exists():
        try:
            raw = json.loads(DB45F_DRIVE.read_text(encoding="utf-8"))
            db45f = {
                "exists": True,
                "has_pose_enc": "pose_enc" in (raw.get("vggt", {}).get("prediction_keys") or []),
                "prediction_keys": raw.get("vggt", {}).get("prediction_keys"),
                "field_shapes": raw.get("vggt", {}).get("field_shapes"),
                "target_uv_sampling_keys": sorted((raw.get("target_uv_sampling") or {}).keys()),
            }
        except Exception as exc:
            db45f = {"exists": True, "error": type(exc).__name__, "message": str(exc)[:300]}
    else:
        db45f = {"exists": False}
    OUT["db45f_drive_result"] = db45f

    decode_names = []
    for mod_name, info in imports.items():
        if not info.get("ok"):
            continue
        for item in info.get("callables", []):
            name = item.get("name", "")
            low = name.lower()
            if ("pose" in low and ("extri" in low or "intr" in low)) or ("camera" in low and "decode" in low):
                decode_names.append({"module": mod_name, **item})
    source_decode_hit = any("pose_encoding_to_extri_intri" in h["text"] for entry in source_hits for h in entry.get("hits", []))
    OUT["readiness"] = {
        "official_pose_decode_candidate_found": bool(decode_names or source_decode_hit),
        "decode_candidates": decode_names[:10],
        "db45f_has_pose_enc_key": bool(db45f.get("has_pose_enc")),
        "future_residual_job_allowed_if_new_brief": bool(decode_names or source_decode_hit) and bool(db45f.get("has_pose_enc")),
        "claim": "readiness-only; no geometry evidence accepted",
    }
except Exception as exc:
    OUT["error"] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "trace_tail": traceback.format_exc()[-1800:],
    }
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = json.dumps(OUT, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(gzip.compress(payload, compresslevel=9)).decode("ascii")
    print("DB45G_B64_BEGIN")
    print(encoded)
    print("DB45G_B64_END")
'''


def _extract_remote_json(log: str) -> dict[str, Any]:
    match = re.search(r"DB45G_B64_BEGIN\s*([A-Za-z0-9+/=\s]+?)\s*DB45G_B64_END", log, re.S)
    if not match:
        return {
            "db": "DB-45g",
            "error": {
                "type": "MissingRemoteJson",
                "message": "Remote job did not print DB45G_B64 markers in the returned log.",
                "log_tail": log[-3000:],
            },
        }
    payload = re.sub(r"\s+", "", match.group(1))
    return json.loads(gzip.decompress(base64.b64decode(payload)).decode("utf-8"))


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


def run_remote(timeout_s: int) -> dict[str, Any]:
    url = os.environ["COLAB_URL"].rstrip("/")
    token = os.environ["COLAB_TOKEN"]
    code_b64 = base64.b64encode(_remote_python().encode("utf-8")).decode("ascii")
    bash = (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{code_b64}').decode('utf-8')\n"
        "exec(code, {'__name__': '__main__'})\n"
        "PY"
    )
    try:
        job = _post_json(url, token, "/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content", "timeout_s": timeout_s})
    except Exception as exc:
        result = {
            "db": "DB-45g",
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "stage": "submit_exec",
            },
            "scope": {
                "source_api_inspection_only": True,
                "model_load": False,
                "model_inference": False,
                "download": False,
                "renderer": False,
                "erp_repair": False,
                "source_replacement": False,
                "generated_image": False,
                "red_promotion": False,
            },
        }
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        REMOTE_RESULT.write_text(json.dumps(_sanitize_json(result), indent=2), encoding="utf-8")
        return result
    job_id = job["job_id"]
    started = time.time()
    while True:
        time.sleep(3)
        state = _get_json(url, token, f"/jobs/{job_id}")
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
        if time.time() - started > timeout_s + 30:
            result = {
                "db": "DB-45g",
                "error": {"type": "LocalPollTimeout", "message": f"Timed out waiting for job {job_id}."},
                "colab_job": {"job_id": job_id, "state": state.get("state")},
            }
            REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result


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


def build_checks(remote: dict[str, Any], db45f: dict[str, Any], secret_hits: list[dict[str, str]]) -> list[dict[str, Any]]:
    readiness = remote.get("readiness", {})
    scope = remote.get("scope", {})

    def chk(check_id: str, passed: bool, severity: str, evidence: str) -> dict[str, Any]:
        return {"id": check_id, "pass": bool(passed), "severity": severity, "evidence": evidence}

    return [
        chk(
            "db45f_precondition",
            db45f.get("decision", {}).get("accepted_db45_diagnostic_evidence") is True
            and db45f.get("decision", {}).get("accepted_db45_geometry_evidence") is False,
            "precondition",
            "DB45f accepted diagnostic-only owner-UV evidence and no geometry evidence.",
        ),
        chk(
            "remote_job_completed",
            remote.get("colab_job", {}).get("exit_code") == 0 and remote.get("error") is None,
            "blocker",
            f"Colab job {remote.get('colab_job', {}).get('job_id')} exit={remote.get('colab_job', {}).get('exit_code')} error={remote.get('error')}.",
        ),
        chk(
            "official_repo_present",
            remote.get("official_repo", {}).get("exists") is True,
            "blocker",
            f"Official repo state: {remote.get('official_repo')}",
        ),
        chk(
            "pose_decode_candidate_found",
            readiness.get("official_pose_decode_candidate_found") is True,
            "blocker",
            f"Decode candidates: {readiness.get('decode_candidates')}",
        ),
        chk(
            "db45f_pose_key_available",
            readiness.get("db45f_has_pose_enc_key") is True,
            "blocker",
            "Remote source/API inspection must read the saved DB45f Drive result and confirm its VGGT prediction keys include pose_enc; a remote-unavailable STOP is not evidence that local DB45f lacks pose_enc.",
        ),
        chk(
            "future_residual_requires_new_brief",
            (
                readiness.get("future_residual_job_allowed_if_new_brief") in {True, False}
                and readiness.get("claim") == "readiness-only; no geometry evidence accepted"
            )
            or (
                remote.get("error") is not None
                and scope.get("model_inference") is False
                and scope.get("renderer") is False
                and scope.get("erp_repair") is False
                and scope.get("source_replacement") is False
                and scope.get("generated_image") is False
            ),
            "scope",
            "DB45g only decides readiness; any residual inference needs a new bounded sub-scope. Remote-unavailable states do not grant residual permission.",
        ),
        chk(
            "no_model_action_or_repair",
            scope.get("source_api_inspection_only") is True
            and scope.get("model_load") is False
            and scope.get("model_inference") is False
            and scope.get("renderer") is False
            and scope.get("erp_repair") is False
            and scope.get("source_replacement") is False
            and scope.get("generated_image") is False,
            "blocker",
            "DB45g ran no model load, inference, renderer, repair, source replacement, or generation.",
        ),
        chk(
            "no_red_promotion",
            scope.get("red_promotion") is False,
            "blocker",
            "DB45g is readiness-only and does not change any permission state.",
        ),
        chk(
            "no_token_in_local_artifacts",
            not secret_hits,
            "blocker",
            f"Secret scan hits: {secret_hits}",
        ),
    ]


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1700, 1300), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((24, 18), "DB45g VGGT pose/pointmap residual readiness gate", fill=(255, 255, 255), font=font(26))
    draw.text((24, 52), "Source/API inspection only. No inference, no repair, no RED promotion.", fill=(220, 220, 220), font=font(15))

    decision = manifest["decision"]
    pill(draw, (24, 88, 270, 124), "readiness: " + str(decision["residual_readiness"]).lower(), (38, 128, 76) if decision["residual_readiness"] else (160, 80, 55))
    pill(draw, (290, 88, 520, 124), "geometry evidence: false", (142, 74, 32))
    pill(draw, (540, 88, 720, 124), "inference: false", (80, 80, 80))
    pill(draw, (740, 88, 930, 124), "RED promotions: 0", (80, 80, 80))

    remote = manifest["remote_result"]
    y = 154
    draw.text((24, y), "Remote/source facts", fill=(255, 255, 255), font=font(21))
    y += 30
    err = remote.get("error")
    if err:
        y = draw_wrapped(
            draw,
            42,
            y,
            f"- remote_error stage={err.get('stage')} type={err.get('type')} message={err.get('message')}",
            120,
            (255, 190, 160),
            13,
            4,
        )
    for line in [
        f"job={remote.get('colab_job', {}).get('job_id')} exit={remote.get('colab_job', {}).get('exit_code')} duration={remote.get('colab_job', {}).get('duration_s')}",
        f"official_repo={remote.get('official_repo', {}).get('exists')} head={remote.get('official_repo', {}).get('git_head')}",
        f"db45f_has_pose_enc={remote.get('readiness', {}).get('db45f_has_pose_enc_key')}",
        f"decode_candidate_found={remote.get('readiness', {}).get('official_pose_decode_candidate_found')}",
    ]:
        y = draw_wrapped(draw, 42, y, "- " + line, 120, (235, 235, 235), 13, 4)

    y += 12
    draw.text((24, y), "Decode candidates", fill=(255, 255, 255), font=font(21))
    y += 30
    candidates = remote.get("readiness", {}).get("decode_candidates") or []
    if not candidates:
        y = draw_wrapped(draw, 42, y, "- none found", 116, (255, 190, 160), 13, 4)
    for item in candidates[:8]:
        y = draw_wrapped(draw, 42, y, f"- {item.get('module')}::{item.get('name')}{item.get('signature') or ''}", 120, (235, 235, 235), 13, 4)

    x2 = 1050
    y2 = 154
    draw.text((x2, y2), "Hard checks", fill=(255, 255, 255), font=font(21))
    y2 += 34
    for check in manifest["checks"]:
        fill = (48, 140, 82) if check["pass"] else ((190, 72, 72) if check["severity"] == "blocker" else (150, 112, 52))
        pill(draw, (x2, y2, x2 + 70, y2 + 29), "PASS" if check["pass"] else "STOP", fill)
        y2 = draw_wrapped(draw, x2 + 82, y2 + 2, check["id"], 55, (238, 238, 238), 13, 4)
        y2 += 7

    y = max(y + 20, 680)
    draw.line((24, y - 18, 1660, y - 18), fill=(70, 70, 70), width=1)
    draw.text((24, y), "Decision boundary", fill=(255, 255, 255), font=font(21))
    y += 32
    for line in [
        "DB45g accepts only readiness if an official pose/camera decode path exists.",
        "It does not accept geometry evidence or permission changes.",
        "A future residual job must be a new brief and must compare calibrated pointmaps against LiDAR/raw evidence.",
        "DB41 lower-right remains zero-LiDAR abstain; confidence and owner-UV evidence cannot promote RED.",
    ]:
        y = draw_wrapped(draw, 42, y, "- " + line, 130, (255, 235, 180), 14, 5)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def build_manifest() -> dict[str, Any]:
    db45f = read_json(DB45F)
    remote = read_json(REMOTE_RESULT) if REMOTE_RESULT.exists() else {
        "db": "DB-45g",
        "error": {"type": "MissingRemoteResult", "message": "Run with --run-remote first."},
    }
    remote = _sanitize_json(remote)
    secret_hits = scan_secret_hits([REMOTE_RESULT])
    checks = build_checks(remote, db45f, secret_hits)
    blocker_failures = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    readiness = remote.get("readiness", {})
    accepted = not blocker_failures
    manifest = {
        "db": "DB-45g",
        "status": "vggt_pose_decode_readiness_gate",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Inspect official VGGT pose/camera decode readiness for a future metric pointmap/LiDAR residual job without running inference.",
        "decision": {
            "accepted_evidence_type": "vggt-pose-decode-readiness-only" if accepted else "blocked-or-no-go",
            "residual_readiness": accepted and readiness.get("future_residual_job_allowed_if_new_brief") is True,
            "accepted_db45_geometry_evidence": False,
            "model_inference_ran": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "db45_status": "running",
            "claim_boundary": "Readiness-only: official pose decode may permit a future residual brief; no metric geometry evidence is accepted here.",
        },
        "refs": {
            "db45f_manifest": rel(DB45F),
            "remote_result_json": rel(REMOTE_RESULT),
            "board": rel(BOARD),
        },
        "remote_result": remote,
        "checks": checks,
        "secret_scan_hits": secret_hits,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    secret_hits = scan_secret_hits([REMOTE_RESULT, MANIFEST])
    checks = build_checks(remote, db45f, secret_hits)
    blocker_failures = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    accepted = not blocker_failures
    manifest["checks"] = checks
    manifest["secret_scan_hits"] = secret_hits
    manifest["decision"]["accepted_evidence_type"] = "vggt-pose-decode-readiness-only" if accepted else "blocked-or-no-go"
    manifest["decision"]["residual_readiness"] = accepted and readiness.get("future_residual_job_allowed_if_new_brief") is True
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    build_board(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true", help="Run the one bounded source/API inspection job.")
    parser.add_argument("--timeout-s", type=int, default=240)
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
