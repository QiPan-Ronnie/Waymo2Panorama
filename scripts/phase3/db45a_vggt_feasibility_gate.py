#!/usr/bin/env python
"""Build DB45a VGGT evidence-route feasibility gate artifacts.

This is not a VGGT model run. It records whether the current Colab/A100
runtime can safely execute the DB45 evidence-only VGGT subtrack without
violating the DB45 scope. If the environment/cache/schema is not ready, the
route stops here instead of patching through installs or gated downloads.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DB45_V0 = (
    ROOT
    / "deliverables"
    / "dit360_v2"
    / "db45_geometry_evidence_audit"
    / "db45_geometry_evidence_audit_manifest.json"
)
RUN_VGGT = ROOT / "scripts" / "phase3" / "run_vggt_multi_anchor.py"
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit"
MANIFEST = OUT_DIR / "db45a_vggt_feasibility_manifest.json"
BOARD = OUT_DIR / "db45a_vggt_feasibility_board.jpg"


REMOTE_CHECKS = {
    "status_job": {
        "source": "Colab Direct /status",
        "runtime_type": "colab-gpu",
        "gpu_name": "NVIDIA A100-SXM4-40GB",
        "gpu_mem_free_gb": 39.49,
        "active_jobs": 0,
    },
    "repo_data_cache_job": {
        "job_id": "728fcd3554fd41cd9c38b506f2a199dc",
        "repo_path": "/content/waymo2panorama",
        "repo_head": "d544214",
        "drive_root_exists": True,
        "data_logs_seen": [
            "02a00399-3857-444e-8db3-a8f58489c394",
            "2c652f9e-8db8-3572-aa49-fae1344a875b",
            "0bae3b5e-417d-3b03-abaa-806b433233b8",
            "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
            "9f871fb4-3b8e-34b3-9161-ed961e71a6da",
        ],
        "cache_hits": [
            "/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt",
            "/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt/tar_cache_log.txt",
            "/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt/vggt-repo.tar.zst",
            "/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt/restore.sh",
            "/content/drive/MyDrive/koi_waymo2pano_colab/cache/df_env_torch22cu121.tar.zst",
        ],
    },
    "vggt_cache_env_job": {
        "job_id": "8e655d85a1b840a9a716b670452a9d0b",
        "run_vggt_script_remote_exists": True,
        "av2_loader_remote_exists": True,
        "restore_script_exists": True,
        "restore_log_evidence": [
            "vggt-repo.tar.zst is 0 bytes in tar_cache_log",
            "zstd was not found during cache creation",
            "HF VGGT cache tarball is not listed",
        ],
        "python_imports": {
            "av2": False,
            "torch": True,
            "vggt": False,
            "numpy": True,
            "cv2": True,
            "PIL": True,
            "pandas": True,
            "pyarrow": True,
            "scipy": True,
        },
        "loader_dependency_note": "The repo AV2RingLoader reads filesystem images and feather calibration directly. Official av2 package is not required for this loader, but pandas/pyarrow/scipy are required and present.",
        "disk_free_gb": {
            "/content": 64.35,
            "/content/drive/MyDrive/koi_waymo2pano_colab": 61.13,
        },
    },
    "hf_access_check": {
        "source": "local Hugging Face API/resolve HEAD check with user-provided token; token was not stored",
        "whoami_status": 200,
        "token_valid": True,
        "commercial_model_api_status": 200,
        "commercial_gated": "manual",
        "commercial_private": False,
        "commercial_disabled": False,
        "commercial_sibling_count": 6,
        "commercial_has_config": True,
        "commercial_has_model_index": False,
        "commercial_large_file_sample": ["model.safetensors"],
        "head_config_json_status": 403,
        "head_model_safetensors_index_json_status": 403,
        "conclusion": "Token is valid and model metadata is visible, but gated Commercial checkpoint file access is not approved yet.",
    },
}


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def draw_wrapped(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, width: int, color, size: int = 13) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw.text((x, y), line, fill=color, font=font(size))
        y += size + 6
    return y


def build_checks(db45_v0: dict) -> list[dict[str, object]]:
    imports = REMOTE_CHECKS["vggt_cache_env_job"]["python_imports"]
    restore = REMOTE_CHECKS["vggt_cache_env_job"]["restore_log_evidence"]
    hf = REMOTE_CHECKS["hf_access_check"]
    v0_gate = bool(db45_v0.get("gate_pass"))
    script_text = RUN_VGGT.read_text(encoding="utf-8") if RUN_VGGT.exists() else ""
    uniform_conf = "np.ones" in script_text and "VGGT: no conf" in script_text

    def chk(check_id: str, passed: bool, severity: str, evidence: str) -> dict[str, object]:
        return {
            "id": check_id,
            "pass": bool(passed),
            "severity": severity,
            "evidence": evidence,
        }

    return [
        chk("db45_v0_gate_available", v0_gate, "info", "DB45 v0 fixed 8 controls and gate_pass=true."),
        chk("a100_live", True, "info", "A100 runtime is reachable with 0 active jobs."),
        chk("repo_and_data_present", True, "info", "Remote repo and five AV2 logs are present."),
        chk(
            "remote_repo_not_current_db45",
            False,
            "blocker",
            "Remote repo head is d544214, while local DB45 commits are newer; DB45a script is not present remotely.",
        ),
        chk(
            "filesystem_loader_deps_present",
            imports.get("pandas") is True and imports.get("pyarrow") is True and imports.get("scipy") is True,
            "info",
            "Official av2 is missing, but the repo's filesystem AV2RingLoader uses pandas/pyarrow/scipy; those dependencies are present.",
        ),
        chk(
            "vggt_import_missing",
            imports.get("vggt") is True,
            "blocker",
            "Base Python cannot import vggt.",
        ),
        chk(
            "vggt_cache_invalid",
            not any("0 bytes" in s or "zstd was not found" in s for s in restore),
            "blocker",
            "VGGT cache is not usable: tar log records a 0-byte repo tarball and missing zstd.",
        ),
        chk(
            "hf_token_valid",
            hf.get("token_valid") is True and hf.get("whoami_status") == 200,
            "info",
            "User-provided HF token is valid by whoami-v2 status 200; token is not stored in artifacts.",
        ),
        chk(
            "hf_commercial_file_access_granted",
            hf.get("head_config_json_status") in (200, 302, 307),
            "blocker",
            "facebook/VGGT-1B-Commercial metadata is visible, but config.json resolve HEAD returned 403 under this token; gated Commercial file access is not approved yet.",
        ),
        chk(
            "hf_cache_missing",
            False,
            "blocker",
            "No HF VGGT checkpoint cache tarball was observed; without approved Commercial access or a verified cache, running VGGT would require a gated/heavy download that DB45a does not permit.",
        ),
        chk(
            "confidence_is_evidential",
            not uniform_conf,
            "blocker",
            "Existing run_vggt_multi_anchor.py writes uniform confidence, which cannot support DB45 permission promotion.",
        ),
        chk(
            "no_install_or_download_in_phase1",
            True,
            "scope",
            "DB45 phase1 feasibility scope forbids install/download/inference; no model was run.",
        ),
    ]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db45_v0 = read_json(DB45_V0)
    checks = build_checks(db45_v0)
    blockers = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    route_state = "no_go_current_runtime" if blockers else "ready_for_scoped_evidence_job"

    manifest = {
        "db": "DB-45a",
        "status": "vggt_feasibility_gate",
        "purpose": "Decide whether VGGT can be run as DB45 evidence-only geometry source in the current Colab runtime.",
        "scope": {
            "install_packages": False,
            "download_models": False,
            "model_inference": False,
            "panorama_generation": False,
            "panorama_repair": False,
            "source_replacement": False,
            "uses_existing_remote_state_only": True,
        },
        "refs": {
            "db45_v0_manifest": str(DB45_V0.relative_to(ROOT)),
            "run_vggt_script": str(RUN_VGGT.relative_to(ROOT)),
        },
        "remote_checks": REMOTE_CHECKS,
        "checks": checks,
        "route_state": route_state,
        "decision": {
            "vggt_evidence_job_runnable_now": False,
            "accepted_db45_evidence": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "db45_remains_running": True,
            "why": "Current runtime has a stale repo, cannot import vggt, has an invalid VGGT repo cache, has no verified checkpoint cache, still lacks approved Commercial checkpoint file access, and the existing VGGT script's uniform confidence is not evidential.",
            "future_reopen_requirements": [
                "sync or upload the DB45 evidence extractor to the remote runtime",
                "prepare VGGT dependencies without hidden broad install drift",
                "obtain approved HF Commercial checkpoint file access or provide a verified nonzero cached checkpoint",
                "replace uniform confidence with auditable validity/occlusion/consistency fields",
                "run only on the frozen DB45 8-control schema and kill on high confidence for DB25/DB41/DB36/DB40 negatives",
            ],
        },
        "outputs": {
            "manifest": str(MANIFEST.relative_to(ROOT)),
            "board": str(BOARD.relative_to(ROOT)),
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    board = Image.new("RGB", (1600, 1120), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((24, 18), "DB45a VGGT evidence feasibility gate: no-go in current runtime", fill=(255, 255, 255), font=font(26))
    draw.text((24, 52), "No install, no model download, no inference, no renderer, no repaired ERP.", fill=(225, 225, 225), font=font(15))

    y = 92
    draw.text((24, y), "Remote facts", fill=(255, 255, 255), font=font(20))
    y += 34
    facts = [
        f"A100: {REMOTE_CHECKS['status_job']['gpu_name']}, free={REMOTE_CHECKS['status_job']['gpu_mem_free_gb']} GB, jobs=0",
        f"repo=/content/waymo2panorama head={REMOTE_CHECKS['repo_data_cache_job']['repo_head']}",
        f"AV2 logs visible={len(REMOTE_CHECKS['repo_data_cache_job']['data_logs_seen'])}",
        "python imports: " + json.dumps(REMOTE_CHECKS["vggt_cache_env_job"]["python_imports"], sort_keys=True),
        REMOTE_CHECKS["vggt_cache_env_job"]["loader_dependency_note"],
        "cache problem: " + "; ".join(REMOTE_CHECKS["vggt_cache_env_job"]["restore_log_evidence"]),
        "HF access: token valid, Commercial model metadata visible, but config.json HEAD=403; gated checkpoint file access is not approved yet.",
    ]
    for fact in facts:
        y = draw_wrapped(draw, 42, y, fact, 132, (225, 225, 225), 13)
        y += 4

    y += 10
    draw.text((24, y), "Checks", fill=(255, 255, 255), font=font(20))
    y += 34
    for check in checks:
        color = (150, 255, 170) if check["pass"] else (255, 130, 130)
        label = "PASS" if check["pass"] else "FAIL"
        draw.text((42, y), f"{label} {check['id']} [{check['severity']}]", fill=color, font=font(14))
        y += 24
        y = draw_wrapped(draw, 70, y, check["evidence"], 125, (220, 220, 220), 12)
        y += 6

    x = 850
    y2 = 760
    draw.text((x, y2), "Decision", fill=(255, 255, 255), font=font(20))
    y2 += 34
    decision_lines = [
        f"route_state={route_state}",
        "VGGT is not accepted as DB45 evidence in this runtime.",
        "This is not a VGGT negative result; it is an environment/schema no-go.",
        "DB45 remains running, but the VGGT subtrack must stop until the reopen requirements are met.",
        "No RED seam is promoted; DB41 lower-right/right-line remains abstain.",
    ]
    for line in decision_lines:
        y2 = draw_wrapped(draw, x, y2, line, 70, (240, 220, 180), 13)
        y2 += 8
    board.save(BOARD, quality=92)
    print(f"wrote {MANIFEST}")
    print(f"route_state={route_state} blockers={len(blockers)}")


if __name__ == "__main__":
    main()
