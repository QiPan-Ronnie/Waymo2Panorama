from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db59_vggt_a1g_diagnostic"
MANIFEST = OUT_DIR / "db59_vggt_a1g_diagnostic_preflight_manifest.json"
BOARD = OUT_DIR / "db59_vggt_a1g_diagnostic_preflight_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
PROGRESS = ROOT / "agent" / "progress.md"

A1_DIR = ROOT / "deliverables" / "a1_streetview_pipeline"
A1_PANO = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment" / "A1_view_none_bmw_1024x2048.png"
A1_L1 = A1_DIR / "A1_view_none_L1_vs_result.jpg"
A1_SEAMS = A1_DIR / "A1_view_none_seam_crops.jpg"
A1_EDIT = A1_DIR / "A1_view_none_editmask.jpg"
A1_ABSTAIN = A1_DIR / "ABSTAIN_overlay.jpg"
G_PANO = ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg"
G_COMPARE = ROOT / "deliverables" / "ghostkill" / "SR_bmw_compare.jpg"
G_DB24_MONTAGE = ROOT / "deliverables" / "dit360_v2" / "db24_google_meta_line_diag" / "db24_longline_source_diag_montage.jpg"

RAW_DIR = ROOT / "deliverables" / "raw_av2_anchor0"
RAW_GRID = RAW_DIR / "7cam_grid.jpg"
RAW_CAMERA_GLOB = "ring_*_full.jpg"

DB25_DIR = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch"
DB25_SUMMARY = DB25_DIR / "db25_longline_summary.json"
DB25_MONTAGE = DB25_DIR / "db25_longline_evidence_montage.jpg"
DB25_CURRENT = DB25_DIR / "roi_current.jpg"
DB25_LIDAR = DB25_DIR / "roi_lidar_support.jpg"
DB25_FLOW = DB25_DIR / "roi_flow_reliable.jpg"

DB41_DIR = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate"
DB41_MANIFEST = DB41_DIR / "db41_rightline_evidence_manifest.json"
DB41_BOARD = DB41_DIR / "db41_rightline_evidence_board.jpg"

DB45_DIR = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit"
DB45F = DB45_DIR / "db45f_vggt_target_uv_sampling_gate_manifest.json"
DB45F_BOARD = DB45_DIR / "db45f_vggt_target_uv_sampling_gate_board.jpg"
DB45K = DB45_DIR / "db45k_vggt_pose_reflection_audit_manifest.json"
DB45K_BOARD = DB45_DIR / "db45k_vggt_pose_reflection_audit_board.jpg"

DB49_DIR = ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract"
DB49C = DB49_DIR / "db49c_source_id_map_feasibility_manifest.json"
DB49C_BOARD = DB49_DIR / "db49c_source_id_map_feasibility_board.jpg"
DB49D = DB49_DIR / "db49d_seamroute_source_map_instrumentation_manifest.json"
DB49E = DB49_DIR / "db49e_exact_lineage_preflight_manifest.json"

TARGET_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
REPO_RUNTIME_SECRET_FILE = ROOT / "runtime" / "active_url.json"

FROZEN_ROIS = [
    {
        "roi_key": "db25_longline",
        "role": "primary_a1g_diagnostic_target_no_repair",
        "roi_xyxy": [850, 420, 1650, 720],
        "source": "DB25",
        "promotion_allowed_under_db59": False,
    },
    {
        "roi_key": "db41_right_roi",
        "role": "negative_control_context_only",
        "roi_xyxy": [1440, 360, 2048, 720],
        "source": "DB41",
        "promotion_allowed_under_db59": False,
    },
    {
        "roi_key": "db41_lower_right_roi",
        "role": "negative_control_context_only_zero_lidar",
        "roi_xyxy": [1580, 560, 2048, 790],
        "source": "DB41",
        "promotion_allowed_under_db59": False,
    },
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


def get_path(obj: Any, *keys: str, default: Any = None) -> Any:
    cur = obj
    for key in keys:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def recursive_find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = recursive_find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = recursive_find_key(value, key)
            if found is not None:
                return found
    return None


def find_mapping_with_keys(obj: Any, required: set[str]) -> dict[str, Any]:
    if isinstance(obj, dict):
        if required.issubset(set(obj.keys())):
            return obj
        for value in obj.values():
            found = find_mapping_with_keys(value, required)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_mapping_with_keys(value, required)
            if found:
                return found
    return {}


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def runtime_secret_file_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[tuple[str, Path]] = []
    explicit = os.environ.get("W2P_RUNTIME_SECRET_FILE")
    if explicit:
        candidates.append(("W2P_RUNTIME_SECRET_FILE", Path(explicit)))
    candidates.extend(
        [
            ("default_user_home", Path.home() / ".waymo2panorama" / "runtime" / "active_url.json"),
            (
                "default_localappdata",
                Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                / "Waymo2Panorama"
                / "runtime"
                / "active_url.json",
            ),
            ("repo_runtime_rejected", REPO_RUNTIME_SECRET_FILE),
        ]
    )
    for source, path in candidates:
        exists = path.exists()
        in_repo = inside_repo(path)
        rows.append(
            {
                "source": source,
                "path": rel(path),
                "exists": exists,
                "inside_repo": in_repo,
                "approved_as_secret_source": exists and not in_repo,
                "value_read": False,
                "notes": "repo-local runtime secrets are rejected" if in_repo else "file existence only; content not read",
            }
        )
    return rows


def image_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    with Image.open(path) as img:
        return {"exists": True, "size": list(img.size), "bytes": int(path.stat().st_size)}


def summarize_assets(paths: dict[str, Path]) -> dict[str, Any]:
    return {name: {"path": rel(path), **image_stats(path)} for name, path in paths.items()}


def target_uv_summary(db45f: dict[str, Any], roi_key: str) -> dict[str, Any]:
    sampling = get_path(db45f, "remote_result", "target_uv_sampling", roi_key, default={})
    row = {}
    for item in db45f.get("source_roi_rows", []):
        if item.get("roi_key") == roi_key:
            row = item
            break
    src = sampling or row.get("target_uv_sampling", {}) or {}
    admiss = src.get("admissibility", {})
    existing = row.get("existing_evidence", {})
    return {
        "roi_key": roi_key,
        "roi_xyxy": src.get("roi_xyxy") or row.get("roi_xyxy"),
        "target_uv_mapping_available": bool(admiss.get("target_uv_mapping_available", False)),
        "still_model_diagnostic_only": bool(admiss.get("still_model_diagnostic_only", True)),
        "permission_promotion_allowed_by_vggt_alone": bool(
            admiss.get("permission_promotion_allowed_by_vggt_alone", False)
        ),
        "owner_uv_valid_frac_of_roi": src.get("owner_uv_valid_frac_of_roi"),
        "owner_preprocess_valid_frac_of_roi": src.get("owner_preprocess_valid_frac_of_roi"),
        "existing_lidar_support_frac": existing.get("lidar_support_frac"),
        "existing_best_flow_reliable_frac": existing.get("best_flow_reliable_frac"),
        "existing_best_flow_pair": existing.get("best_flow_pair"),
        "claim": "diagnostic-only VGGT raw-owner UV sampling; not source truth or repair permission",
    }


def roi_boundary_summary(db45k: dict[str, Any], roi_key: str) -> dict[str, Any]:
    for row in db45k.get("target_roi_boundary", []):
        if row.get("roi") == roi_key:
            return row
    return {}


def runtime_availability() -> dict[str, Any]:
    files = runtime_secret_file_candidates()
    env_pair = bool(os.environ.get("COLAB_URL")) and bool(os.environ.get("COLAB_TOKEN"))
    approved_file = any(row["approved_as_secret_source"] for row in files)
    return {
        "env_runtime_pair_present": env_pair,
        "env_values_read": False,
        "runtime_secret_file_candidates": files,
        "approved_runtime_secret_source_present": env_pair or approved_file,
        "accepted_sources": [
            "COLAB_URL and COLAB_TOKEN process environment variables",
            "W2P_RUNTIME_SECRET_FILE pointing to a non-repo runtime secret file",
            "documented default non-repo runtime secret file locations, existence only",
        ],
        "rejected_sources": [
            "chat-pasted endpoint/token JSON",
            "repo-local runtime/active_url.json",
            "any URL/token value written into repo outputs",
        ],
    }


def token_hits(manifest_preview: dict[str, Any]) -> list[dict[str, Any]]:
    text = json.dumps(manifest_preview, ensure_ascii=False, sort_keys=True)
    hits: list[dict[str, Any]] = []
    for name, pattern in TOKEN_PATTERNS.items():
        found = pattern.findall(text)
        if found:
            hits.append({"path": "manifest_preview", "pattern": name, "count": len(found)})
    return hits


def build_manifest() -> dict[str, Any]:
    brief_text = BRIEF.read_text(encoding="utf-8", errors="replace")
    db25 = read_json(DB25_SUMMARY)
    db41 = read_json(DB41_MANIFEST)
    db45f = read_json(DB45F)
    db45k = read_json(DB45K)
    db49c = read_json(DB49C)
    db49d = read_json(DB49D)
    db49e = read_json(DB49E)

    raw_camera_files = sorted(RAW_DIR.glob(RAW_CAMERA_GLOB))
    runtime = runtime_availability()
    accepted_geometry = bool(recursive_find_key(db45k, "accepted_db45_geometry_evidence"))
    official_center = find_mapping_with_keys(db45k, {"reflection_preferred_by_svd", "mean_residual_m"})
    translation_diag = get_path(db45k, "alignment_fits", "translation_column_as_center_diagnostic_only", default={})
    if not translation_diag:
        translation_diag = find_mapping_with_keys(db45k, {"reflection_preferred_by_svd", "mean_residual_m", "det_R"})

    db49_source = db49c.get("source_id_map", {})
    complete_source_id_found = bool(db49_source.get("complete_source_id_map_found", False))

    roi_evidence = []
    for roi in FROZEN_ROIS:
        key = roi["roi_key"]
        summary = {
            **roi,
            "db45f_target_uv": target_uv_summary(db45f, key),
            "db45k_boundary": roi_boundary_summary(db45k, key),
        }
        if key == "db25_longline":
            summary["db25_existing"] = {
                "lidar_support_frac": db25.get("lidar_support_frac"),
                "best_flow_pair": db25.get("best_flow_pair"),
                "best_flow_reliable_frac": db25.get("best_flow_reliable_frac"),
                "recommendation": db25.get("recommendation"),
            }
        else:
            name = "right_roi" if key == "db41_right_roi" else "lower_right_roi"
            summary["db41_existing"] = db41.get("summaries", {}).get(name, {})
            summary["db41_threshold_result"] = db41.get("threshold_results", {}).get(name, {})
        roi_evidence.append(summary)

    secure_runtime = bool(runtime["approved_runtime_secret_source_present"])
    db59_brief_present = "DB-59: VGGT-assisted A1/G diagnostic geometry evidence audit" in brief_text
    a1g_assets = summarize_assets(
        {
            "A1_view_none_actual_pano": A1_PANO,
            "A1_view_none_L1_vs_result": A1_L1,
            "A1_view_none_seam_crops": A1_SEAMS,
            "A1_view_none_editmask": A1_EDIT,
            "A1_abstain_overlay": A1_ABSTAIN,
            "G_bmw_pano": G_PANO,
            "G_SR_compare": G_COMPARE,
            "DB24_longline_source_diag_montage": G_DB24_MONTAGE,
        }
    )
    evidence_assets = summarize_assets(
        {
            "raw_7cam_grid": RAW_GRID,
            "DB25_evidence_montage": DB25_MONTAGE,
            "DB25_current": DB25_CURRENT,
            "DB25_lidar": DB25_LIDAR,
            "DB25_flow": DB25_FLOW,
            "DB41_board": DB41_BOARD,
            "DB45f_vggt_target_uv_board": DB45F_BOARD,
            "DB45k_pose_reflection_board": DB45K_BOARD,
            "DB49c_source_id_map_board": DB49C_BOARD,
        }
    )

    gates = [
        {
            "id": "db59_brief_open_and_audit_logged",
            "state": "pass" if db59_brief_present else "fail",
            "evidence": "DB59 brief exists with CPU preflight, ROI freeze, and adversarial audit requirements.",
        },
        {
            "id": "a1g_roi_list_frozen_before_model_action",
            "state": "pass",
            "evidence": f"{len(FROZEN_ROIS)} fixed ROIs: DB25 primary diagnostic plus DB41 right/lower-right negative controls.",
        },
        {
            "id": "a1g_not_source_truth",
            "state": "pass",
            "evidence": "A1/G are diagnostic display references only. VGGT input remains raw 7-camera BMW anchor 0.",
        },
        {
            "id": "raw_7_camera_source_inventory",
            "state": "pass" if len(raw_camera_files) == 7 and RAW_GRID.exists() else "partial_blocked",
            "evidence": f"raw_camera_file_count={len(raw_camera_files)}; raw_grid_exists={RAW_GRID.exists()}; exact raw-source crops are not fabricated.",
        },
        {
            "id": "existing_vggt_raw_anchor_evidence_available",
            "state": "pass",
            "evidence": "DB45f already ran official VGGT on raw 7-camera BMW anchor 0 and sampled DB25/DB41 owner-UV rows.",
        },
        {
            "id": "vggt_pose_coordinate_admissibility",
            "state": "fail" if not accepted_geometry else "pass",
            "evidence": (
                "DB45k accepted_db45_geometry_evidence=false; "
                f"official_center_reflection_preferred={official_center.get('reflection_preferred_by_svd')}; "
                f"official_center_mean_residual_m={official_center.get('mean_residual_m')}; "
                "translation-column behavior remains diagnostic-only."
            ),
            "kill_for_repair_or_promotion": not accepted_geometry,
        },
        {
            "id": "target_surface_lidar_flow_support",
            "state": "fail",
            "evidence": (
                f"DB25 lidar={db25.get('lidar_support_frac')}; "
                f"DB25 best_flow={db25.get('best_flow_reliable_frac')}; "
                "DB41 right/lower-right both fail lidar threshold and remain negative controls."
            ),
            "kill_for_repair_or_promotion": True,
        },
        {
            "id": "source_id_map_and_protected_masks",
            "state": "blocked_not_fabricated" if not complete_source_id_found else "pass",
            "evidence": (
                f"DB49c complete_source_id_map_found={complete_source_id_found}; "
                f"DB49d_sidecar_instrumentation_only={bool(db49d.get('scope', {}).get('candidate_pixels_modified') is False)}; "
                f"DB49e_status={db49e.get('status')}"
            ),
        },
        {
            "id": "secure_runtime_secret_source",
            "state": "pass" if secure_runtime else "blocked_no_approved_secret_source",
            "evidence": f"env_pair_present={runtime['env_runtime_pair_present']}; approved_non_repo_or_env_source={secure_runtime}; values_read=false",
        },
        {
            "id": "no_remote_model_repair_generation_or_source_replacement",
            "state": "pass",
            "evidence": "CPU-local preflight only; no /status, /exec, A100, VGGT inference, repair, generation, source replacement, source_id_map creation, or RED promotion.",
        },
    ]

    failed_ids = [g["id"] for g in gates if g["state"] == "fail"]
    blocked_ids = [g["id"] for g in gates if "blocked" in g["state"] or g["state"].startswith("partial")]
    may_run_remote = secure_runtime and not failed_ids and not blocked_ids
    new_a100_needed_for_frozen_rois = False

    manifest: dict[str, Any] = {
        "db": "DB-59",
        "status": "cpu_local_preflight_no_remote_no_repair",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "goal": "Test whether VGGT can supplement A1/G diagnostic geometry evidence without treating VGGT, A1, or G as renderer/source truth.",
        "scope": {
            "cpu_local_only": True,
            "fixed_uuid": TARGET_UUID,
            "fixed_anchor": 0,
            "a1g_diagnostic_references_only": True,
            "raw_7_camera_source_truth_only": True,
            "remote_status_or_exec": False,
            "a100_used": False,
            "network_used": False,
            "new_vggt_inference": False,
            "vggt_on_pano_pixels": False,
            "renderer": False,
            "repair_candidate_created": False,
            "raw_camera_warp_or_composite": False,
            "generation_or_refiner": False,
            "source_replacement": False,
            "source_id_map_created": False,
            "red_promotion": False,
            "permission_change": False,
        },
        "frozen_diagnostic_roi_set": FROZEN_ROIS,
        "a1g_assets": a1g_assets,
        "raw_source_assets": {
            "raw_dir": rel(RAW_DIR),
            "raw_camera_file_count": len(raw_camera_files),
            "raw_camera_file_names": [path.name for path in raw_camera_files],
            "raw_7cam_grid": {"path": rel(RAW_GRID), **image_stats(RAW_GRID)},
            "exact_raw_crop_policy": "do not fabricate raw crops without owner-UV projection; DB45f board is cited as prior owner-UV diagnostic evidence.",
        },
        "existing_evidence_assets": evidence_assets,
        "roi_evidence": roi_evidence,
        "existing_vggt_prior": {
            "db45f": {
                "path": rel(DB45F),
                "accepted_evidence_type": get_path(db45f, "decision", "accepted_evidence_type"),
                "accepted_db45_geometry_evidence": get_path(db45f, "decision", "accepted_db45_geometry_evidence"),
                "model_input": "raw 7-camera BMW anchor 0 only",
                "claim_boundary": get_path(db45f, "decision", "claim_boundary"),
            },
            "db45k": {
                "path": rel(DB45K),
                "accepted_db45_geometry_evidence": accepted_geometry,
                "official_center_reflection_preferred": official_center.get("reflection_preferred_by_svd"),
                "official_center_mean_residual_m": official_center.get("mean_residual_m"),
                "translation_column_diagnostic_only": True,
                "claim_boundary": "coordinate/reflection evidence remains diagnostic-only; no repair or RED promotion permission",
            },
        },
        "runtime_source_availability": runtime,
        "gates": gates,
        "failed_gate_ids": failed_ids,
        "blocked_gate_ids": blocked_ids,
        "remote_decision": {
            "may_run_remote_or_model_next": may_run_remote,
            "new_a100_inference_needed_for_current_frozen_rois": new_a100_needed_for_frozen_rois,
            "reason": (
                "No A100 under DB59 now: existing official VGGT raw-anchor evidence already covers the frozen DB25/DB41 ROI set; "
                "DB45k coordinate/reflection remains non-admissible; target-surface/source-id/protected-mask gates are not passed; "
                "and secure runtime source must come from env/non-repo file rather than chat-pasted JSON."
            ),
        },
        "repair_decision": {
            "repair_allowed_under_db59": False,
            "result_claim": "diagnostic/no-promotion/no-repair",
            "follow_up_required_for_any_repair": "fresh decision brief with raw-camera-backed local warp/composite gates and same-ROI before/after vision",
        },
        "claim_boundaries": {
            "source_faithful": False,
            "diagnostic": True,
            "presentation_only": False,
            "generated": False,
            "abstain": True,
            "rejected_repair": True,
            "db32_s40": "caveated source-sidestep/generated-sky handoff candidate only",
            "g_bmw_pano": "classic BMW failure diagnostic reference only",
            "a1_view_none": "Google/Meta-style diagnostic reference only",
            "db25_db41": "context/negative controls only under DB59",
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
            "output_dir": rel(OUT_DIR),
        },
    }
    manifest["token_scan_hits"] = token_hits(manifest)
    manifest["hard_checks_passed"] = all(
        [
            db59_brief_present,
            len(FROZEN_ROIS) == 3,
            all(not roi["promotion_allowed_under_db59"] for roi in FROZEN_ROIS),
            all(asset["exists"] for asset in a1g_assets.values()),
            len(manifest["token_scan_hits"]) == 0,
            not manifest["scope"]["remote_status_or_exec"],
            not manifest["scope"]["repair_candidate_created"],
            not manifest["scope"]["generation_or_refiner"],
            manifest["remote_decision"]["may_run_remote_or_model_next"] is False,
        ]
    )
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


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    width: int,
    fill=(235, 235, 235),
    size: int = 14,
) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + 6
    return y


def status_pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, ok: bool, w: int = 300) -> int:
    fill = (42, 100, 72) if ok else (132, 64, 47)
    draw.rounded_rectangle((x, y, x + w, y + 38), radius=6, fill=fill, outline=(190, 190, 190))
    draw_text(draw, (x + 10, y + 10), label, size=13)
    return x + w + 14


def image_panel(board: Image.Image, path: Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(24, 27, 32), outline=(86, 91, 101), width=2)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((x1 - x0 - 20, y1 - y0 - 48))
            px = x0 + (x1 - x0 - img.width) // 2
            board.paste(img, (px, y0 + 10))
        except Exception as exc:
            draw_wrapped(draw, x0 + 12, y0 + 30, f"load failed: {type(exc).__name__}", 45, fill=(246, 142, 142))
    else:
        draw_text(draw, (x0 + 12, y0 + 30), "missing", fill=(246, 142, 142), size=15)
    draw_text(draw, (x0 + 12, y1 - 31), label, fill=(220, 230, 245), size=13)


def roi_crop_panel(board: Image.Image, path: Path, roi: list[int], box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(24, 27, 32), outline=(86, 91, 101), width=2)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            crop = img.crop(tuple(roi))
            crop.thumbnail((x1 - x0 - 20, y1 - y0 - 48))
            px = x0 + (x1 - x0 - crop.width) // 2
            board.paste(crop, (px, y0 + 10))
        except Exception as exc:
            draw_wrapped(draw, x0 + 12, y0 + 30, f"crop failed: {type(exc).__name__}", 45, fill=(246, 142, 142))
    else:
        draw_text(draw, (x0 + 12, y0 + 30), "missing", fill=(246, 142, 142), size=15)
    draw_text(draw, (x0 + 12, y1 - 31), label, fill=(220, 230, 245), size=13)


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2400, 2050), (15, 17, 22))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (40, 28), "DB59 VGGT-assisted A1/G diagnostic geometry evidence audit - CPU/local preflight", size=28)
    y = 76
    y = draw_wrapped(
        draw,
        40,
        y,
        "Raw 7-camera BMW anchor 0 is the only source-truth input. A1_view_none and G_bmw_pano are diagnostic display references only. No A100, remote, VGGT rerun, repair, generation, source replacement, source_id_map, or RED promotion occurred.",
        170,
        fill=(214, 222, 236),
        size=15,
    )
    y += 14
    x = 40
    x = status_pill(draw, x, y, f"hard checks: {manifest['hard_checks_passed']}", manifest["hard_checks_passed"])
    x = status_pill(draw, x, y, f"secret hits: {len(manifest['token_scan_hits'])}", len(manifest["token_scan_hits"]) == 0)
    x = status_pill(draw, x, y, "remote allowed: false", False)
    x = status_pill(draw, x, y, "repair allowed: false", False)
    status_pill(draw, x, y, "ROI list frozen", True)

    roi_main = FROZEN_ROIS[0]["roi_xyxy"]
    panels = [
        (40, 170, 600, 430, A1_PANO, "A1_view_none DB25 ROI crop - diagnostic"),
        (620, 170, 1180, 430, G_PANO, "G_bmw_pano DB25 ROI crop - diagnostic"),
        (1200, 170, 1760, 430, A1_SEAMS, "A1_view_none seam crops"),
        (1780, 170, 2340, 430, G_COMPARE, "G/SR BMW compare - diagnostic"),
    ]
    roi_crop_panel(board, panels[0][4], roi_main, panels[0][:4], panels[0][5])
    roi_crop_panel(board, panels[1][4], roi_main, panels[1][:4], panels[1][5])
    image_panel(board, panels[2][4], panels[2][:4], panels[2][5])
    image_panel(board, panels[3][4], panels[3][:4], panels[3][5])

    row2 = [
        (40, 455, 600, 715, A1_EDIT, "A1 edit mask"),
        (620, 455, 1180, 715, A1_ABSTAIN, "A1 abstain overlay"),
        (1200, 455, 1760, 715, RAW_GRID, "Raw 7-camera grid - source truth overview"),
        (1780, 455, 2340, 715, DB25_MONTAGE, "DB25 longline evidence"),
    ]
    for x0, y0, x1, y1, path, label in row2:
        image_panel(board, path, (x0, y0, x1, y1), label)

    row3 = [
        (40, 740, 600, 1000, DB41_BOARD, "DB41 negative-control evidence"),
        (620, 740, 1180, 1000, DB45F_BOARD, "DB45f VGGT owner-UV diagnostic"),
        (1200, 740, 1760, 1000, DB45K_BOARD, "DB45k coordinate/reflection blocker"),
        (1780, 740, 2340, 1000, DB49C_BOARD, "DB49c source_id_map blocker"),
    ]
    for x0, y0, x1, y1, path, label in row3:
        image_panel(board, path, (x0, y0, x1, y1), label)

    draw_text(draw, (40, 1040), "Frozen DB59 ROI set", fill=(245, 245, 245), size=22)
    y = 1080
    for roi in manifest["frozen_diagnostic_roi_set"]:
        y = draw_wrapped(
            draw,
            58,
            y,
            f"{roi['roi_key']}: {roi['roi_xyxy']} - {roi['role']} - promotion_allowed={roi['promotion_allowed_under_db59']}",
            96,
            fill=(224, 232, 245),
            size=15,
        )
    draw_text(draw, (1220, 1040), "Gate result", fill=(245, 245, 245), size=22)
    y2 = 1080
    for gate in manifest["gates"]:
        color = (190, 238, 203) if gate["state"] == "pass" else ((250, 178, 150) if gate["state"] == "fail" else (246, 214, 150))
        y2 = draw_wrapped(draw, 1238, y2, f"{gate['id']}: {gate['state']} - {gate['evidence']}", 118, fill=color, size=14)
        y2 += 4
        if y2 > 1800:
            break

    draw_text(draw, (40, 1285), "Decision", fill=(245, 245, 245), size=22)
    y = draw_wrapped(draw, 58, 1324, manifest["remote_decision"]["reason"], 120, fill=(246, 214, 150), size=15)
    y = draw_wrapped(
        draw,
        58,
        y + 10,
        "Result claim: diagnostic/no-promotion/no-repair. Any actual raw-camera-backed repair requires a fresh follow-up brief with protected masks and same-ROI before/after vision.",
        120,
        fill=(230, 230, 230),
        size=15,
    )
    y = draw_wrapped(
        draw,
        58,
        y + 10,
        f"Runtime source: env_pair_present={manifest['runtime_source_availability']['env_runtime_pair_present']}; approved_runtime_secret_source_present={manifest['runtime_source_availability']['approved_runtime_secret_source_present']}; values_read=false.",
        120,
        fill=(214, 222, 236),
        size=15,
    )

    draw_text(draw, (40, 1988), f"Manifest: {rel(MANIFEST)}", fill=(185, 190, 200), size=13)
    board.save(BOARD, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    build_board(manifest)
    print(
        json.dumps(
            {
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
                "status": manifest["status"],
                "hard_checks_passed": manifest["hard_checks_passed"],
                "failed_gate_ids": manifest["failed_gate_ids"],
                "blocked_gate_ids": manifest["blocked_gate_ids"],
                "may_run_remote_or_model_next": manifest["remote_decision"]["may_run_remote_or_model_next"],
                "repair_allowed_under_db59": manifest["repair_decision"]["repair_allowed_under_db59"],
                "token_scan_hits": len(manifest["token_scan_hits"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
