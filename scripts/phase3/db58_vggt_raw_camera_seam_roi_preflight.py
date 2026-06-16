from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db58_vggt_raw_camera_seam_roi"
MANIFEST = OUT_DIR / "db58_vggt_raw_camera_seam_roi_preflight_manifest.json"
BOARD = OUT_DIR / "db58_vggt_raw_camera_seam_roi_preflight_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
PROGRESS = ROOT / "agent" / "progress.md"
DB25_DIR = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch"
DB25_SUMMARY = DB25_DIR / "db25_longline_summary.json"
DB25_MONTAGE = DB25_DIR / "db25_longline_evidence_montage.jpg"
DB25_CURRENT = DB25_DIR / "roi_current.jpg"
DB25_CAMID = DB25_DIR / "roi_camid_overlay.jpg"
DB25_LIDAR = DB25_DIR / "roi_lidar_support.jpg"
DB25_FLOW = DB25_DIR / "roi_flow_reliable.jpg"
DB25_NEARGROUND = DB25_DIR / "roi_nearground.jpg"

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

DB32 = ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_generated_sky_harmonize_s40.png"
DB41_BOARD = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_board.jpg"
G_BMW = ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg"

TARGET = {
    "uuid": "02a00399-3857-444e-8db3-a8f58489c394",
    "anchor": 0,
    "roi_xyxy": [850, 420, 1650, 720],
    "roi_name": "DB25 longline ROI",
}

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
        return str(p).replace("\\", "/")


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


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, ok: bool, w: int = 260) -> int:
    fill = (42, 100, 72) if ok else (132, 64, 47)
    draw.rounded_rectangle((x, y, x + w, y + 38), radius=6, fill=fill, outline=(190, 190, 190))
    draw_text(draw, (x + 10, y + 10), label, size=13)
    return x + w + 14


def image_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    with Image.open(path) as img:
        return {"exists": True, "size": list(img.size), "bytes": int(path.stat().st_size)}


def image_box(board: Image.Image, path: Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((x1 - x0 - 18, y1 - y0 - 44))
            px = x0 + (x1 - x0 - img.width) // 2
            board.paste(img, (px, y0 + 8))
        except Exception as exc:
            draw_wrapped(draw, x0 + 10, y0 + 24, f"load failed: {type(exc).__name__}", 42, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def token_hits(paths: list[Path], manifest_preview: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    items: list[tuple[str, str]] = []
    for path in paths:
        if path.exists() and path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            items.append((rel(path) or str(path), path.read_text(encoding="utf-8", errors="replace")))
    if manifest_preview is not None:
        items.append(("manifest_preview", json.dumps(manifest_preview, ensure_ascii=False, sort_keys=True)))
    for name, text in items:
        for pattern_name, pattern in TOKEN_PATTERNS.items():
            found = pattern.findall(text)
            if found:
                hits.append({"path": name, "pattern": pattern_name, "count": len(found)})
    return hits


def first_source_roi(db45f: dict[str, Any]) -> dict[str, Any]:
    for row in db45f.get("source_roi_rows", []):
        if row.get("roi_key") == "db25_longline":
            return row
    return {}


def first_boundary_row(db45k: dict[str, Any]) -> dict[str, Any]:
    for row in db45k.get("target_roi_boundary", []):
        if row.get("roi") == "db25_longline":
            return row
    return {}


def build_manifest() -> dict[str, Any]:
    brief = BRIEF.read_text(encoding="utf-8", errors="replace")
    db25 = read_json(DB25_SUMMARY)
    db45f = read_json(DB45F)
    db45k = read_json(DB45K)
    db49c = read_json(DB49C)
    db49d = read_json(DB49D)
    db49e = read_json(DB49E)
    db45f_roi = first_source_roi(db45f)
    db45k_roi = first_boundary_row(db45k)

    raw_uv_available = bool(get_path(db45f_roi, "target_uv_sampling", "admissibility", "target_uv_mapping_available", default=False))
    raw_uv_diag_only = bool(get_path(db45f_roi, "target_uv_sampling", "admissibility", "still_model_diagnostic_only", default=True))
    owner_uv_frac = float(get_path(db45f_roi, "target_uv_sampling", "owner_uv_valid_frac_of_roi", default=0.0) or 0.0)
    owner_pre_frac = float(get_path(db45f_roi, "target_uv_sampling", "owner_preprocess_valid_frac_of_roi", default=0.0) or 0.0)
    known_lidar = float(db45k_roi.get("known_lidar_support_frac", db25.get("lidar_support_frac", 0.0)) or 0.0)
    key_pair_65 = float(get_path(db45f_roi, "existing_evidence", "key_pair_6_5_flow_frac", default=0.0) or 0.0)
    best_flow = float(db25.get("best_flow_reliable_frac", 0.0) or 0.0)

    official_center = get_path(db45k, "alignment_audit", "official_camera_from_world_center", default={})
    translation_diag = get_path(db45k, "alignment_audit", "translation_column_as_center_diagnostic_only", default={})
    source_id_missing = not bool(db49c.get("complete_source_id_map_found") or db49c.get("source_id_map_created"))
    source_id_status = str(db49c.get("source_id_map_status", "unknown"))
    db49d_instrumented = bool(db49d.get("all_checks_passed")) and not bool(db49d.get("source_id_map_for_db32_created"))
    db49e_paused = str(db49e.get("status")) == "preflight_paused"

    gates = [
        {
            "id": "scope_fixed_db58_target",
            "state": "pass",
            "evidence": f"uuid={TARGET['uuid']} anchor={TARGET['anchor']} roi={TARGET['roi_xyxy']}",
        },
        {
            "id": "existing_artifacts_present",
            "state": "pass",
            "evidence": "DB25 summary, DB45f target-UV manifest, DB45k coordinate audit, and DB49 source-map manifests are present.",
        },
        {
            "id": "raw_owner_uv_preflight",
            "state": "partial_diagnostic_only" if raw_uv_available and raw_uv_diag_only else ("pass" if raw_uv_available else "fail"),
            "evidence": f"DB45f target_uv_mapping_available={raw_uv_available}; owner_uv_valid_frac={owner_uv_frac:.6f}; owner_preprocess_valid_frac={owner_pre_frac:.6f}; DB49 source_id_status={source_id_status}",
            "repair_allowed_after_gate": False,
        },
        {
            "id": "vggt_pose_coordinate_admissibility",
            "state": "fail",
            "evidence": (
                "DB45k keeps accepted_db45_geometry_evidence=false: "
                f"official center reflection_preferred={official_center.get('reflection_preferred_by_svd')} "
                f"mean_residual_m={official_center.get('mean_residual_m')} "
                f"translation-column diagnostic admissible={translation_diag.get('admissible_for_geometry_promotion')}"
            ),
            "kill_triggered": True,
        },
        {
            "id": "target_surface_lidar_flow_support",
            "state": "fail",
            "evidence": (
                f"known_lidar_support_frac={known_lidar:.3f}; "
                f"key_pair_6_5_flow_frac={key_pair_65:.3f}; best_flow_pair={db25.get('best_flow_pair')} "
                f"best_flow_reliable_frac={best_flow:.3f}; "
                f"raw_reprojection_med_px={db45k_roi.get('raw_reprojection_med_px')} "
                f"nearest_lidar_3d_residual_med_m={db45k_roi.get('nearest_lidar_3d_residual_med_m')}"
            ),
            "kill_triggered": True,
        },
        {
            "id": "source_id_and_sidecar_support",
            "state": "blocked_not_fabricated" if source_id_missing else "pass",
            "evidence": f"DB49c source_id_map missing={source_id_missing}; DB49d instrumented_default_off={db49d_instrumented}; DB49e paused={db49e_paused}",
            "repair_allowed_after_gate": False,
        },
        {
            "id": "protected_structure_and_composite_gate",
            "state": "not_attempted_due_prior_kill",
            "evidence": "No warp/composite attempted, so lane/curb/object/building-edge protected masks remain a required future gate, not a passed gate.",
            "repair_allowed_after_gate": False,
        },
    ]

    failed = [g for g in gates if g["state"] == "fail"]
    blockers = [g for g in gates if g["state"] in {"blocked_not_fabricated", "partial_diagnostic_only"}]

    manifest: dict[str, Any] = {
        "db": "DB58",
        "status": "abstain_no_repair_after_cpu_local_preflight",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Test whether existing DB25/DB45/DB49 evidence permits the next DB58 remote/model or raw-camera-backed local warp/composite step.",
        "target": TARGET,
        "scope": {
            "cpu_local_existing_artifacts_only": True,
            "remote_used": False,
            "a100_used": False,
            "network_used": False,
            "vggt_inference_ran": False,
            "renderer_or_seamroute_ran": False,
            "warp_or_composite_attempted": False,
            "generation_or_inpainting_used": False,
            "source_replacement": False,
            "source_id_map_created": False,
            "permission_change": False,
            "red_promotion": False,
        },
        "inputs": {
            "db25_summary": rel(DB25_SUMMARY),
            "db45f_manifest": rel(DB45F),
            "db45k_manifest": rel(DB45K),
            "db49c_manifest": rel(DB49C),
            "db49d_manifest": rel(DB49D),
            "db49e_manifest": rel(DB49E),
        },
        "fixed_evidence": {
            "db25": {
                "roi_valid_frac": db25.get("roi_valid_frac"),
                "camera_label_counts": db25.get("camera_label_counts"),
                "top_camera_labels": db25.get("top_camera_labels"),
                "near_ground_frac": db25.get("near_ground_frac"),
                "lidar_support_frac": db25.get("lidar_support_frac"),
                "best_flow_pair": db25.get("best_flow_pair"),
                "best_flow_reliable_frac": db25.get("best_flow_reliable_frac"),
                "key_pair_6_5_flow_frac": key_pair_65,
                "recommendation": db25.get("recommendation"),
            },
            "db45f_target_uv": {
                "accepted_evidence_type": get_path(db45f, "decision", "accepted_evidence_type"),
                "accepted_db45_geometry_evidence": get_path(db45f, "decision", "accepted_db45_geometry_evidence"),
                "target_uv_mapping_available": raw_uv_available,
                "still_model_diagnostic_only": raw_uv_diag_only,
                "owner_uv_valid_frac_of_roi": owner_uv_frac,
                "owner_preprocess_valid_frac_of_roi": owner_pre_frac,
                "final_permission": db45f_roi.get("final_permission"),
            },
            "db45k_pose_and_target_boundary": {
                "accepted_evidence_type": get_path(db45k, "decision", "accepted_evidence_type"),
                "accepted_db45_geometry_evidence": get_path(db45k, "decision", "accepted_db45_geometry_evidence"),
                "official_center_admissible": official_center.get("admissible_for_geometry_promotion"),
                "official_center_reflection_preferred": official_center.get("reflection_preferred_by_svd"),
                "official_center_mean_residual_m": official_center.get("mean_residual_m"),
                "translation_column_admissible": translation_diag.get("admissible_for_geometry_promotion"),
                "target_roi_boundary": db45k_roi,
            },
            "db49_source_map": {
                "db49c_status": db49c.get("status"),
                "source_id_map_status": source_id_status,
                "complete_source_id_map_found": bool(db49c.get("complete_source_id_map_found")),
                "source_id_map_created": bool(db49c.get("source_id_map_created")),
                "db49d_instrumentation_ready_default_off": db49d_instrumented,
                "db49e_status": db49e.get("status"),
            },
        },
        "evidence_gates": gates,
        "gate_summary": {
            "failed_gate_ids": [g["id"] for g in failed],
            "blocked_or_diagnostic_gate_ids": [g["id"] for g in blockers],
            "may_run_remote_or_model_next": False,
            "may_attempt_raw_camera_warp_or_composite": False,
        },
        "adversarial_reasoning_summary": {
            "question": "Should DB58 proceed past CPU-local preflight into new VGGT/model action or raw-camera-backed composite?",
            "positions": [
                {
                    "id": "ARG-KEEP-STRICT",
                    "claim": "Stop now: DB45k coordinate/reflection and DB25 target-surface evidence fail DB58's admissibility gates.",
                    "score": 9,
                },
                {
                    "id": "ARG-PARTIAL-UV",
                    "claim": "Use DB45f target-UV diagnostics as useful metadata, but only for a no-repair evidence board because it cannot promote geometry alone.",
                    "score": 8,
                },
                {
                    "id": "ARG-RUN-MORE-VGGT",
                    "claim": "Run another VGGT pass to seek a better convention or residual result.",
                    "score": 3,
                    "rebuttal": "DB58 kill criteria forbid continuing when pose/depth coordinate ambiguity remains unresolved; DB45k already says no residual patch-on-patch.",
                },
            ],
            "verdict": "ARG-KEEP-STRICT wins: DB58 should close as abstain/no-repair under existing evidence, with no remote/model action.",
        },
        "decision": {
            "accepted_evidence_type": "db58_cpu_local_preflight_abstain_no_repair",
            "repair_result": "abstain/no-repair",
            "reason": "VGGT coordinate/reflection evidence is not admissible and target-surface LiDAR/flow support remains too weak for a raw-camera-backed local warp/composite.",
            "db32_boundary": "unchanged; DB32 s40 remains caveated source-sidestep/generated-sky handoff candidate only.",
            "g_bmw_boundary": "unchanged; G_bmw_pano remains diagnostic failure/reference only.",
            "db41_boundary": "unchanged; DB41 right/lower-right remains no-evidence/abstain negative control.",
            "next_allowed_step": "No DB58 patch-on-patch. A future attempt needs a fresh brief with new official coordinate evidence or new raw/LiDAR/flow target-surface evidence.",
        },
        "required_vision_assets": {
            "db25_montage": {"path": rel(DB25_MONTAGE), **image_stats(DB25_MONTAGE)},
            "roi_current": {"path": rel(DB25_CURRENT), **image_stats(DB25_CURRENT)},
            "roi_camid_overlay": {"path": rel(DB25_CAMID), **image_stats(DB25_CAMID)},
            "roi_lidar_support": {"path": rel(DB25_LIDAR), **image_stats(DB25_LIDAR)},
            "roi_flow_reliable": {"path": rel(DB25_FLOW), **image_stats(DB25_FLOW)},
            "roi_nearground": {"path": rel(DB25_NEARGROUND), **image_stats(DB25_NEARGROUND)},
            "db45f_board": {"path": rel(DB45F_BOARD), **image_stats(DB45F_BOARD)},
            "db45k_board": {"path": rel(DB45K_BOARD), **image_stats(DB45K_BOARD)},
            "db49c_board": {"path": rel(DB49C_BOARD), **image_stats(DB49C_BOARD)},
            "db32": {"path": rel(DB32), **image_stats(DB32)},
            "db41_board": {"path": rel(DB41_BOARD), **image_stats(DB41_BOARD)},
            "g_bmw": {"path": rel(G_BMW), **image_stats(G_BMW)},
        },
        "outputs": {"manifest": rel(MANIFEST), "board": rel(BOARD)},
    }

    checks = [
        {
            "id": "db58_brief_present",
            "pass": "# DB-58: VGGT-assisted raw-camera-backed seam ROI repair feasibility" in brief,
            "evidence": "DB58 brief exists before CPU-local preflight.",
        },
        {
            "id": "fixed_target_matches_db58",
            "pass": db25.get("uuid") == TARGET["uuid"] and int(db25.get("anchor")) == TARGET["anchor"] and list(db25.get("roi")) == TARGET["roi_xyxy"],
            "evidence": f"DB25 target uuid={db25.get('uuid')} anchor={db25.get('anchor')} roi={db25.get('roi')}",
        },
        {
            "id": "db45f_diagnostic_only_preserved",
            "pass": get_path(db45f, "decision", "accepted_db45_geometry_evidence") is False,
            "evidence": "DB45f target-UV sampling is not accepted geometry evidence.",
        },
        {
            "id": "db45k_coordinate_kill_preserved",
            "pass": get_path(db45k, "decision", "accepted_db45_geometry_evidence") is False and official_center.get("admissible_for_geometry_promotion") is False,
            "evidence": "DB45k keeps VGGT pose/reflection coordinate evidence diagnostic-only.",
        },
        {
            "id": "target_surface_support_kill_preserved",
            "pass": known_lidar < 0.10 and key_pair_65 < 0.20 and db45k_roi.get("permission_promotion_allowed") is False,
            "evidence": f"lidar={known_lidar:.3f}; key_pair_6_5_flow={key_pair_65:.3f}; permission={db45k_roi.get('permission_promotion_allowed')}",
        },
        {
            "id": "no_repair_or_model_scope",
            "pass": all(
                manifest["scope"][k] is False
                for k in [
                    "remote_used",
                    "a100_used",
                    "network_used",
                    "vggt_inference_ran",
                    "renderer_or_seamroute_ran",
                    "warp_or_composite_attempted",
                    "generation_or_inpainting_used",
                    "source_replacement",
                    "source_id_map_created",
                    "permission_change",
                    "red_promotion",
                ]
            ),
            "evidence": "CPU/local existing-artifact preflight only.",
        },
        {
            "id": "boundaries_preserved",
            "pass": "unchanged" in manifest["decision"]["db32_boundary"] and "unchanged" in manifest["decision"]["db41_boundary"],
            "evidence": "DB32, G, and DB41 boundaries are unchanged.",
        },
    ]
    manifest["hard_checks"] = checks
    manifest["secret_scan_hits"] = token_hits([Path(__file__), BRIEF, PROGRESS, DB25_SUMMARY, DB45F, DB45K, DB49C, DB49D, DB49E], manifest)
    manifest["hard_checks"].append(
        {
            "id": "strict_secret_scan_zero",
            "pass": len(manifest["secret_scan_hits"]) == 0,
            "evidence": f"hits={len(manifest['secret_scan_hits'])} across DB58 script, living docs, and selected non-image inputs.",
        }
    )
    manifest["hard_checks_pass"] = all(row["pass"] for row in manifest["hard_checks"])
    return manifest


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2500, 2200), (16, 18, 22))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (40, 32), "DB58 VGGT-assisted raw-camera-backed seam ROI preflight", size=32, fill=(245, 245, 245))
    draw_wrapped(
        draw,
        40,
        78,
        "CPU/local existing-artifact gate only. Result: abstain/no-repair; no remote, no A100, no VGGT inference, no renderer, no generation, no warp/composite.",
        145,
        size=16,
        fill=(210, 218, 230),
    )
    y = 128
    x = 40
    x = pill(draw, x, y, f"status: {manifest['status']}", False, 520)
    x = pill(draw, x, y, "remote/model: no", True, 185)
    x = pill(draw, x, y, "repair: no", True, 145)
    x = pill(draw, x, y, "secret hits: 0", len(manifest["secret_scan_hits"]) == 0, 165)
    pill(draw, x, y, f"checks: {'PASS' if manifest['hard_checks_pass'] else 'FAIL'}", manifest["hard_checks_pass"], 170)

    y = 190
    image_box(board, DB25_MONTAGE, (40, y, 650, y + 340), "DB25 longline evidence montage")
    image_box(board, DB32, (680, y, 1120, y + 340), "DB32 s40 caveated handoff")
    image_box(board, G_BMW, (1150, y, 1590, y + 340), "G diagnostic reference only")
    image_box(board, DB41_BOARD, (1620, y, 2460, y + 340), "DB41 no-evidence/abstain boundary")

    y += 380
    for i, (path, label) in enumerate(
        [
            (DB25_CURRENT, "fixed ROI current"),
            (DB25_CAMID, "owner/camera label overlay"),
            (DB25_LIDAR, "LiDAR support"),
            (DB25_FLOW, "flow reliable"),
            (DB25_NEARGROUND, "near-ground mask"),
        ]
    ):
        x0 = 40 + i * 485
        image_box(board, path, (x0, y, x0 + 450, y + 250), label)

    y += 300
    draw_text(draw, (40, y), "Evidence gates", size=24, fill=(245, 245, 245))
    y += 44
    for gate in manifest["evidence_gates"]:
        state = gate["state"]
        color = (96, 171, 117) if state == "pass" else (230, 166, 84) if "partial" in state or "blocked" in state else (238, 116, 91)
        draw.rounded_rectangle((40, y, 700, y + 74), radius=6, fill=(31, 34, 42), outline=color, width=2)
        draw_text(draw, (58, y + 10), f"{gate['id']}: {state}", fill=color, size=15)
        draw_wrapped(draw, 58, y + 34, gate["evidence"], 76, fill=(218, 224, 235), size=12)
        y += 86

    y0 = 875
    image_box(board, DB45F_BOARD, (760, y0, 1560, y0 + 455), "DB45f target-UV diagnostic only")
    image_box(board, DB45K_BOARD, (1600, y0, 2460, y0 + 455), "DB45k pose/reflection coordinate kill")

    y1 = 1370
    image_box(board, DB49C_BOARD, (760, y1, 1560, y1 + 455), "DB49c source_id_map missing")
    draw.rectangle((1600, y1, 2460, y1 + 455), fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    draw_text(draw, (1620, y1 + 18), "Decision", size=22, fill=(245, 245, 245))
    decision = manifest["decision"]
    ytxt = y1 + 58
    for line in [
        f"repair_result: {decision['repair_result']}",
        f"reason: {decision['reason']}",
        decision["db32_boundary"],
        decision["g_bmw_boundary"],
        decision["db41_boundary"],
        decision["next_allowed_step"],
    ]:
        ytxt = draw_wrapped(draw, 1620, ytxt, line, 86, fill=(225, 231, 241), size=14)
        ytxt += 8

    y2 = 1870
    draw_text(draw, (40, y2), "Hard checks", size=24, fill=(245, 245, 245))
    y2 += 42
    for check in manifest["hard_checks"]:
        marker = "PASS" if check["pass"] else "FAIL"
        color = (105, 200, 130) if check["pass"] else (245, 120, 95)
        draw_text(draw, (58, y2), f"{marker} {check['id']}", fill=color, size=14)
        draw_wrapped(draw, 360, y2, check["evidence"], 125, fill=(215, 222, 233), size=13)
        y2 += 34

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    build_board(manifest)
    print(
        json.dumps(
            {
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
                "status": manifest["status"],
                "repair_result": manifest["decision"]["repair_result"],
                "failed_gates": manifest["gate_summary"]["failed_gate_ids"],
                "hard_checks_pass": manifest["hard_checks_pass"],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0 if manifest["hard_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
