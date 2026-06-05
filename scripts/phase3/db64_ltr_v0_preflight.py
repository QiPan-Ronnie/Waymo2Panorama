from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0"
MANIFEST = OUT_DIR / "db64_ltr_v0_preflight_manifest.json"
BOARD = OUT_DIR / "db64_ltr_v0_preflight_board.jpg"

TARGET_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
TARGET_ANCHOR = 0
TARGET_LOG = ROOT / "data" / "argoverse2" / "val" / TARGET_UUID
NON_REPO_RUNTIME_DEFAULT = Path.home() / ".waymo2panorama" / "runtime" / "active_url.json"
NON_REPO_RUNTIME_ENV = os.environ.get("W2P_RUNTIME_SECRET_FILE")
REPO_RUNTIME_FILE = ROOT / "runtime" / "active_url.json"

DRIVE_WORKSPACE = {
    "checked_by_connector_in_current_session": True,
    "workspace_title": "koi_waymo2pano_colab",
    "workspace_url": "https://drive.google.com/drive/folders/1o0Ewp6tTXjH_C0g8wv2mJPh2MHt7mpJ1",
    "top_level_folders_seen": [
        "data",
        "outputs",
        "results",
        "runtime",
        "hf_cache",
        "cache",
        "deliverables_share",
        "worker",
        "external",
    ],
    "secrets_folder_seen_but_not_read": True,
    "expected_target_log_drive_path": (
        "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/"
        + TARGET_UUID
    ),
    "future_db64_drive_output_path": (
        "/content/drive/MyDrive/koi_waymo2pano_colab/results/"
        "layered_target_raycaster/db64_ltr_v0/"
    ),
}

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
        return str(p).replace("\\", "/")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_path(obj: Any, dotted: str, default: Any = None) -> Any:
    cur = obj
    for key in dotted.split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def image_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": rel(path)}
    try:
        with Image.open(path) as img:
            return {
                "exists": True,
                "path": rel(path),
                "size": list(img.size),
                "bytes": int(path.stat().st_size),
            }
    except Exception as exc:
        return {"exists": True, "path": rel(path), "bytes": int(path.stat().st_size), "error": type(exc).__name__}


def file_stats(path: Path) -> dict[str, Any]:
    return {"exists": path.exists(), "path": rel(path), "bytes": int(path.stat().st_size) if path.exists() else 0}


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=(238, 238, 238), size=15) -> None:
    draw.text(xy, str(text), fill=fill, font=font(size))


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    chars: int,
    fill=(238, 238, 238),
    size: int = 14,
    leading: int = 5,
) -> int:
    for line in wrap(str(text), width=chars, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + leading
    return y


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, state: str, w: int) -> int:
    colors = {
        "pass": (48, 108, 74),
        "warn": (145, 103, 44),
        "fail": (140, 63, 54),
        "info": (58, 82, 125),
    }
    fill = colors.get(state, colors["info"])
    draw.rounded_rectangle((x, y, x + w, y + 36), radius=6, fill=fill, outline=(185, 185, 185))
    draw_text(draw, (x + 10, y + 9), label, size=13)
    return x + w + 12


def image_box(board: Image.Image, path: Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(24, 26, 31), outline=(86, 90, 98), width=2)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((x1 - x0 - 18, y1 - y0 - 48))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception as exc:
            draw_wrapped(draw, x0 + 12, y0 + 24, f"load failed: {type(exc).__name__}", 38, fill=(245, 140, 130), size=13)
    else:
        draw_text(draw, (x0 + 12, y0 + 30), "missing", fill=(245, 140, 130), size=14)
    draw_text(draw, (x0 + 10, y1 - 30), label, fill=(224, 232, 245), size=13)


def runtime_secret_status() -> dict[str, Any]:
    env_pair = bool(os.environ.get("COLAB_URL")) and bool(os.environ.get("COLAB_TOKEN"))
    candidates: list[dict[str, Any]] = []
    if NON_REPO_RUNTIME_ENV:
        candidates.append({"kind": "env_W2P_RUNTIME_SECRET_FILE", "path": NON_REPO_RUNTIME_ENV})
    candidates.append({"kind": "default_non_repo_file", "path": str(NON_REPO_RUNTIME_DEFAULT)})

    usable_file: dict[str, Any] | None = None
    for cand in candidates:
        p = Path(cand["path"])
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            usable_file = {
                "kind": cand["kind"],
                "path": rel(p),
                "exists": True,
                "parse_ok": False,
                "parse_error": type(exc).__name__,
            }
            break
        usable_file = {
            "kind": cand["kind"],
            "path": rel(p),
            "exists": True,
            "parse_ok": True,
            "has_url": bool(data.get("url")),
            "has_token": bool(data.get("token")),
            "runtime_type": data.get("runtime_type"),
            "version": data.get("version"),
            "active_jobs": data.get("active_jobs"),
            "timestamp_present": "timestamp" in data,
            "uptime_present": "uptime_s" in data,
        }
        break

    return {
        "env_colab_url_and_token_present": env_pair,
        "non_repo_runtime_file_present": usable_file is not None,
        "repo_runtime_file_present_but_not_used": REPO_RUNTIME_FILE.exists(),
        "usable_secure_runtime_source_present": env_pair
        or bool(usable_file and usable_file.get("parse_ok") and usable_file.get("has_url") and usable_file.get("has_token")),
        "safe_runtime_file_summary": usable_file,
        "secret_policy": (
            "Runtime URL/token values may be read only from env or non-repo files. "
            "Values are never written to repo artifacts."
        ),
    }


def git_status_summary() -> dict[str, Any]:
    try:
        out = subprocess.run(
            ["git", "status", "--short"],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
        lines = [line for line in out.stdout.splitlines() if line.strip()]
        relevant_prefixes = (
            " M agent/",
            "?? scripts/phase3/db64",
            "?? deliverables/layered_target_raycaster/",
            " M scripts/phase3/db64",
        )
        return {
            "git_status_exit": out.returncode,
            "dirty_count_total": len(lines),
            "relevant_lines": [line for line in lines if line.startswith(relevant_prefixes)],
            "note": "GitHub retention requires committing code/docs/small evidence after user-approved result packaging.",
        }
    except Exception as exc:
        return {"git_status_error": type(exc).__name__}


def raw_thumbnail_pack() -> dict[str, Any]:
    raw_dir = ROOT / "deliverables" / "raw_av2_anchor0"
    files = sorted(raw_dir.glob("*.jpg")) if raw_dir.exists() else []
    return {
        "exists": raw_dir.exists(),
        "path": rel(raw_dir),
        "jpg_count": len(files),
        "files": [rel(p) for p in files[:12]],
        "is_raw_executable_data": False,
        "why_not_raw_executable": "These are review/export JPGs, not the AV2 log with calibration and LiDAR sweeps.",
    }


def db25_summary() -> dict[str, Any]:
    path = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "db25_longline_summary.json"
    if not path.exists():
        return {"exists": False, "path": rel(path)}
    data = read_json(path)
    return {
        "exists": True,
        "path": rel(path),
        "roi": data.get("roi"),
        "near_ground_frac": data.get("near_ground_frac"),
        "lidar_support_frac": data.get("lidar_support_frac"),
        "best_flow_pair": data.get("best_flow_pair"),
        "best_flow_reliable_frac": data.get("best_flow_reliable_frac"),
        "key_dark_wall_pair_6_5_flow_frac": get_path(data, "flow_pair_stats.6-5.fb_reliable_frac"),
        "recommendation": data.get("recommendation"),
        "target_surface_permission": False,
    }


def depth_visibility_summary() -> dict[str, Any]:
    path = ROOT / "deliverables" / "depth_visibility_seam_probe" / "batch_summary.json"
    if not path.exists():
        return {"exists": False, "path": rel(path)}
    data = read_json(path)
    cases = []
    for row in data.get("cases", []):
        cases.append(
            {
                "case": row.get("case"),
                "log_dir": row.get("log_dir"),
                "anchor_idx": row.get("anchor_idx"),
                "lidar_delta_ms": row.get("lidar_delta_ms"),
            }
        )
    return {
        "exists": True,
        "path": rel(path),
        "run": data.get("run"),
        "cases": cases,
        "aggregate": data.get("aggregate", {}),
        "role_for_db64": "prior Drive-run evidence and metadata; not a current local executable LTR sidecar pack.",
    }


def manifest_status(path: Path, keys: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": rel(path)}
    data = read_json(path)
    out: dict[str, Any] = {"exists": True, "path": rel(path)}
    for key in keys:
        out[key.replace(".", "_")] = get_path(data, key)
    return out


def build_preflight() -> dict[str, Any]:
    local_data = {
        "target_log": file_stats(TARGET_LOG),
        "calibration_intrinsics": file_stats(TARGET_LOG / "calibration" / "intrinsics.feather"),
        "calibration_extrinsics": file_stats(TARGET_LOG / "calibration" / "egovehicle_SE3_sensor.feather"),
        "lidar_dir": file_stats(TARGET_LOG / "sensors" / "lidar"),
        "camera_root": file_stats(TARGET_LOG / "sensors" / "cameras"),
        "raw_thumbnail_pack": raw_thumbnail_pack(),
    }

    code_components = {
        "av2_loader": file_stats(ROOT / "code" / "waymo2panorama" / "data_io" / "av2_loader.py"),
        "lidar_to_erp_depth": file_stats(ROOT / "code" / "waymo2panorama" / "depth" / "lidar_to_erp_depth.py"),
        "lidar_zbuffer_layer": file_stats(ROOT / "code" / "waymo2panorama" / "projection" / "lidar_zbuffer_layer.py"),
        "sphere_projection": file_stats(ROOT / "code" / "waymo2panorama" / "projection" / "sphere_projection.py"),
        "old_lidar_zbuffer_probe": file_stats(ROOT / "scripts" / "phase3" / "test_lidar_zbuffer_seam.py"),
        "dibr_single_center_probe": file_stats(ROOT / "scripts" / "phase3" / "dibr_lidar_single_center.py"),
    }

    control_images = {
        "l1_baseline_blend": image_stats(ROOT / "deliverables" / "e1_seam_confined" / "bmw_L1.png"),
        "l1_hard_select": image_stats(ROOT / "deliverables" / "freqhybrid" / "erp_hard_select.png"),
        "a1_view_none": image_stats(ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment" / "A1_view_none_bmw_1024x2048.png"),
        "g_bmw_pano": image_stats(ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg"),
        "db63_vggt_no_repair_board": image_stats(
            ROOT / "deliverables" / "dit360_v2" / "db63_vggt_component_gate" / "db63_vggt_component_gate_board.jpg"
        ),
    }

    sidecar_state = {
        "db49b_partial_generated_mask": image_stats(
            ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract" / "db49b_generated_mask_sky_core_only.png"
        ),
        "db49b_partial_unknown_mask": image_stats(
            ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract" / "db49b_unknown_or_abstain_mask_partial.png"
        ),
        "db49b_partial_risk_map": image_stats(
            ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract" / "db49b_risk_map_partial.png"
        ),
        "db49c_source_id_status": manifest_status(
            ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract" / "db49c_source_id_map_feasibility_manifest.json",
            ["status", "decision.source_id_map_status", "decision.ready_for_uncaveated_bosch_training_data"],
        ),
        "db49d_future_instrumentation": manifest_status(
            ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract" / "db49d_seamroute_source_map_instrumentation_manifest.json",
            ["status", "source_id_map_status", "ready_for_uncaveated_bosch_training_data"],
        ),
    }

    geometry_evidence = {
        "db25_longline": db25_summary(),
        "depth_visibility_prior": depth_visibility_summary(),
        "db41_negative_boundary": manifest_status(
            ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_manifest.json",
            ["status", "decision", "right_roi.lidar_support_frac", "lower_right_roi.lidar_support_frac"],
        ),
        "db45k_vggt_coordinate_audit": manifest_status(
            ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit" / "db45k_vggt_pose_reflection_audit_manifest.json",
            [
                "status",
                "decision.accepted_evidence_type",
                "decision.accepted_db45_geometry_evidence",
                "decision.permission_state_changes",
                "decision.route_recommendation",
            ],
        ),
        "db62_vggt_source_composite": manifest_status(
            ROOT / "deliverables" / "dit360_v2" / "db62_vggt_raw_source_composite" / "db62_vggt_raw_source_composite_manifest.json",
            ["status", "vision_verdict", "hard_checks.secret_scan_hits", "operator_stats.alpha_gt_0_05_frac"],
        ),
        "db63_component_gate": manifest_status(
            ROOT / "deliverables" / "dit360_v2" / "db63_vggt_component_gate" / "db63_vggt_component_gate_manifest.json",
            ["status", "vision_verdict", "component_gate.selected_component_fraction"],
        ),
    }

    local_target_present = TARGET_LOG.exists()
    local_calib_present = (
        (TARGET_LOG / "calibration" / "intrinsics.feather").exists()
        and (TARGET_LOG / "calibration" / "egovehicle_SE3_sensor.feather").exists()
    )
    local_lidar_present = (TARGET_LOG / "sensors" / "lidar").exists()
    local_cameras_present = (TARGET_LOG / "sensors" / "cameras").exists()
    source_id_ready = False
    protected_masks_ready = False

    executable_local_now = bool(local_target_present and local_calib_present and local_lidar_present and local_cameras_present)
    needs_drive_or_remote_data = not executable_local_now and DRIVE_WORKSPACE["checked_by_connector_in_current_session"]
    preflight_state = "executable_local_now" if executable_local_now else "paused_needs_drive_or_remote_target_data"

    checks = [
        {
            "id": "local_av2_target_log_present",
            "pass": local_target_present,
            "severity": "blocker",
            "evidence": rel(TARGET_LOG),
        },
        {
            "id": "local_calibration_present",
            "pass": local_calib_present,
            "severity": "blocker",
            "evidence": "intrinsics.feather and egovehicle_SE3_sensor.feather required.",
        },
        {
            "id": "local_lidar_present",
            "pass": local_lidar_present,
            "severity": "blocker",
            "evidence": "LTR-v0 needs target-ray depth/visibility evidence.",
        },
        {
            "id": "local_raw_cameras_present",
            "pass": local_cameras_present,
            "severity": "blocker",
            "evidence": "Raw camera images are required; review thumbnails are not enough.",
        },
        {
            "id": "drive_workspace_visible",
            "pass": DRIVE_WORKSPACE["checked_by_connector_in_current_session"],
            "severity": "info",
            "evidence": "Drive workspace and top-level data/outputs/results/runtime folders were visible via connector; secrets folder was not read.",
        },
        {
            "id": "source_id_map_not_ready",
            "pass": not source_id_ready,
            "severity": "info",
            "evidence": "DB49c says exact DB32 source_id_map is missing; DB64 must generate its own sidecars only from a true LTR run.",
        },
        {
            "id": "protected_masks_not_complete",
            "pass": not protected_masks_ready,
            "severity": "warning",
            "evidence": "Existing partial masks/risk sidecars are not full lane/curb/object protected masks.",
        },
        {
            "id": "no_generation_or_repair_ran",
            "pass": True,
            "severity": "scope",
            "evidence": "DB64 preflight only; no renderer, no model, no A100, no image repair.",
        },
    ]

    decision = {
        "preflight_state": preflight_state,
        "executable_local_now": executable_local_now,
        "needs_drive_or_remote_data": needs_drive_or_remote_data,
        "a100_needed_now": False,
        "cpu_colab_may_be_useful_next": needs_drive_or_remote_data,
        "why": (
            "Local repo has review artifacts and reusable LTR components, but not the AV2 target log with calibration, "
            "raw cameras, and LiDAR sweeps. Drive has the project workspace and data folder, so the next execution "
            "step should be a bounded Drive/Colab data preflight or a local target-log sync, not an A100 model run."
        ),
        "next_allowed_step": (
            "Use the approved non-repo runtime source only if needed to query/run a CPU Colab Drive data preflight. "
            "Do not run model inference or LTR prototype until the target log/cameras/calibration/LiDAR are confirmed."
        ),
    }

    manifest_preview: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_ltr_v0_cpu_local_preflight",
        "accepted_evidence_type": "route_preflight_only_no_ltr_run",
        "target": {"uuid": TARGET_UUID, "anchor": TARGET_ANCHOR},
        "scope": {
            "cpu_local_preflight": True,
            "remote_status_or_exec_used": False,
            "a100_used": False,
            "model_inference_used": False,
            "renderer_or_ltr_prototype_ran": False,
            "generation_or_inpainting_used": False,
            "source_replacement_used": False,
            "sidecars_created": False,
            "red_promotion": False,
        },
        "artifact_retention_contract": {
            "local_output_dir": rel(OUT_DIR),
            "git_expected_for_small_artifacts": [
                "scripts/phase3/db64_ltr_v0_preflight.py",
                "agent/decision_briefs.md",
                "agent/progress.md",
                "agent/handoff.md",
                "agent/README.md",
                "agent/plans/2026-06-04-egsr-seam-and-route-roadmap.md",
                "deliverables/layered_target_raycaster/db64_ltr_v0/db64_ltr_v0_preflight_manifest.json",
                "deliverables/layered_target_raycaster/db64_ltr_v0/db64_ltr_v0_preflight_board.jpg",
            ],
            "drive_expected_for_large_or_remote_artifacts": DRIVE_WORKSPACE["future_db64_drive_output_path"],
            "drive_raw_data_expected": DRIVE_WORKSPACE["expected_target_log_drive_path"],
            "note": "This preflight creates local/git-sized artifacts only. A real LTR run should write full outputs to Drive/results and commit code/docs/small boards.",
        },
        "drive_workspace": DRIVE_WORKSPACE,
        "runtime_secret_status_sanitized": runtime_secret_status(),
        "local_data": local_data,
        "code_components": code_components,
        "control_images": control_images,
        "sidecar_state": sidecar_state,
        "geometry_evidence": geometry_evidence,
        "hard_checks": checks,
        "decision": decision,
        "git_status_summary": git_status_summary(),
    }
    hits = secret_hits(json.dumps(manifest_preview, ensure_ascii=False, sort_keys=True))
    manifest_preview["strict_secret_scan"] = {"hits": hits, "hit_count": len(hits)}
    return manifest_preview


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pat in TOKEN_PATTERNS.items():
        found = pat.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


def write_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1800, 1500), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 24), "DB64 LTR-v0 preflight: target-ray ownership route, no LTR run yet", size=28)
    draw_text(
        draw,
        (28, 62),
        "CPU/local manifest only. Checks local/Git/Drive readiness for raw cameras + calibration + LiDAR + sidecars.",
        fill=(218, 224, 235),
        size=15,
    )

    decision = manifest["decision"]
    x = 28
    x = pill(draw, x, 98, f"local executable={decision['executable_local_now']}", "pass" if decision["executable_local_now"] else "fail", 210)
    x = pill(draw, x, 98, f"Drive visible={manifest['drive_workspace']['checked_by_connector_in_current_session']}", "pass", 185)
    x = pill(draw, x, 98, f"A100 needed now={decision['a100_needed_now']}", "pass", 190)
    x = pill(draw, x, 98, f"secret hits={manifest['strict_secret_scan']['hit_count']}", "pass" if manifest["strict_secret_scan"]["hit_count"] == 0 else "fail", 150)
    pill(draw, x, 98, "no repair/no generation", "pass", 210)

    y = 150
    draw_text(draw, (28, y), "Preflight decision", size=22)
    y += 32
    y = draw_wrapped(draw, 34, y, f"state: {decision['preflight_state']}", 118, fill=(255, 238, 180), size=15)
    y = draw_wrapped(draw, 34, y, decision["why"], 118, fill=(230, 230, 230), size=14)
    y = draw_wrapped(draw, 34, y, "next: " + decision["next_allowed_step"], 118, fill=(205, 225, 255), size=14)

    y += 18
    draw_text(draw, (28, y), "Local vs Drive retention", size=22)
    y += 32
    drive = manifest["drive_workspace"]
    rows = [
        f"local output: {manifest['artifact_retention_contract']['local_output_dir']}",
        f"Drive workspace: {drive['workspace_title']} visible via connector",
        f"Drive data expected: {drive['expected_target_log_drive_path']}",
        f"Drive DB64 results target: {drive['future_db64_drive_output_path']}",
        "Git expected: script + living docs + manifest + board after user-approved packaging",
        "Secrets folder was visible but not read.",
    ]
    for row in rows:
        y = draw_wrapped(draw, 34, y, "- " + row, 118, fill=(225, 225, 225), size=14)

    y += 18
    draw_text(draw, (28, y), "Blocking local inputs", size=22)
    y += 32
    for chk in manifest["hard_checks"]:
        state = "PASS" if chk["pass"] else "FAIL"
        color = (170, 245, 190) if chk["pass"] else (255, 165, 140)
        y = draw_wrapped(draw, 34, y, f"{state} {chk['id']}: {chk['evidence']}", 118, fill=color, size=14)
        if y > 720:
            break

    panel_y = 760
    image_box(board, ROOT / "deliverables" / "freqhybrid" / "erp_hard_select.png", (30, panel_y, 450, panel_y + 250), "HardSelect control")
    image_box(board, ROOT / "deliverables" / "e1_seam_confined" / "bmw_L1.png", (470, panel_y, 890, panel_y + 250), "L1 blend baseline")
    image_box(
        board,
        ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "db25_longline_evidence_montage.jpg",
        (910, panel_y, 1330, panel_y + 250),
        "DB25 evidence pack",
    )
    image_box(
        board,
        ROOT / "deliverables" / "dit360_v2" / "db63_vggt_component_gate" / "db63_vggt_component_gate_board.jpg",
        (1350, panel_y, 1770, panel_y + 250),
        "DB63 VGGT no-repair",
    )

    panel_y += 280
    image_box(
        board,
        ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract" / "db49c_source_id_map_feasibility_board.jpg",
        (30, panel_y, 590, panel_y + 270),
        "DB49c source_id missing",
    )
    image_box(
        board,
        ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit" / "db45k_vggt_pose_reflection_audit_board.jpg",
        (620, panel_y, 1180, panel_y + 270),
        "DB45k VGGT coordinate audit",
    )
    image_box(
        board,
        ROOT / "deliverables" / "depth_visibility_seam_probe" / "depth_visibility_three_anchor_compact_review.jpg",
        (1210, panel_y, 1770, panel_y + 270),
        "Prior depth visibility evidence",
    )

    y = 1340
    draw_text(draw, (28, y), "Claim boundary", size=21)
    y += 30
    claims = [
        "DB64 preflight did not run LTR-v0, did not repair A1/G/G/DB32, and did not create source_id/layer/risk sidecars.",
        "Current local machine lacks the target AV2 log with calibration/raw cameras/LiDAR; Drive has the data workspace.",
        "Next step is a bounded CPU Colab Drive data preflight or local target-log sync, not A100/model inference.",
    ]
    for claim in claims:
        y = draw_wrapped(draw, 34, y, "- " + claim, 140, fill=(255, 235, 185), size=14)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_preflight()
    write_board(manifest)
    manifest["outputs"] = {
        "manifest": file_stats(MANIFEST),
        "board": image_stats(BOARD),
    }
    manifest["strict_secret_scan"] = {
        "hits": secret_hits(json.dumps(manifest, ensure_ascii=False, sort_keys=True)),
    }
    manifest["strict_secret_scan"]["hit_count"] = len(manifest["strict_secret_scan"]["hits"])
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "status": manifest["status"],
        "preflight_state": manifest["decision"]["preflight_state"],
        "executable_local_now": manifest["decision"]["executable_local_now"],
        "needs_drive_or_remote_data": manifest["decision"]["needs_drive_or_remote_data"],
        "a100_needed_now": manifest["decision"]["a100_needed_now"],
        "secret_hits": manifest["strict_secret_scan"]["hit_count"],
        "manifest": rel(MANIFEST),
        "board": rel(BOARD),
    }, indent=2))


if __name__ == "__main__":
    main()
