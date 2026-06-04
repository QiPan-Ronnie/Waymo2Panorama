#!/usr/bin/env python
"""DB45h VGGT calibrated residual job contract gate.

This is a CPU/local contract artifact. It defines what a future bounded VGGT
residual extractor must save and check before any VGGT pointmap can become
geometry evidence. It runs no model, performs no repair, and promotes no RED
segment.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit"
DB45B = OUT_DIR / "db45b_evidence_permission_calibration_manifest.json"
DB45F = OUT_DIR / "db45f_vggt_target_uv_sampling_gate_manifest.json"
DB45F_REMOTE = OUT_DIR / "db45f_vggt_remote_target_uv_sampling_result.json"
DB45G = OUT_DIR / "db45g_vggt_pose_decode_readiness_manifest.json"

MANIFEST = OUT_DIR / "db45h_vggt_residual_job_contract_manifest.json"
BOARD = OUT_DIR / "db45h_vggt_residual_job_contract_board.jpg"

SECRET_BYTE_PATTERNS = [
    re.compile(rb"hf_[A-Za-z0-9]{20,}"),
    re.compile(rb"Bearer\s+[A-Za-z0-9._-]+"),
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


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


def row_by_segment(rows: list[dict[str, Any]], segment_id: str) -> dict[str, Any]:
    for row in rows:
        if row.get("segment_id") == segment_id:
            return row
    return {}


def build_contract(db45b: dict[str, Any], db45f_remote: dict[str, Any], db45g: dict[str, Any]) -> dict[str, Any]:
    db45_rows = db45b.get("rows", [])
    target_uv = db45f_remote.get("target_uv_sampling", {})
    local_pose = db45g.get("local_db45f_pose_state", {})

    required_saved_outputs = [
        {
            "field": "pose_enc",
            "shape": "B x S x 9, saved before log-tail compaction",
            "why": "Required input to official pose_encoding_to_extri_intri; DB45f recorded the key but not tensor values.",
        },
        {
            "field": "decoded_extrinsics",
            "shape": "S x 3 x 4, OpenCV camera-from-world",
            "why": "Needed to extract VGGT camera centers and compare to the Waymo rig after Sim(3).",
        },
        {
            "field": "decoded_intrinsics",
            "shape": "S x 3 x 3 at VGGT preprocessed resolution, plus raw-image remap",
            "why": "Needed for point/depth unprojection and raw-camera reprojection residuals.",
        },
        {
            "field": "preprocess_mapping",
            "shape": "per camera crop/pad/resize transform",
            "why": "Needed to map owner-UV ERP samples to VGGT coordinates without guessing.",
        },
        {
            "field": "waymo_rig_extrinsics",
            "shape": "S x 4 x 4 T_ego_cam or equivalent",
            "why": "Metric reference for camera-center Sim(3) alignment and raw-ray checks.",
        },
        {
            "field": "lidar_and_raw_residuals",
            "shape": "per control ROI and per valid target sample",
            "why": "Only raw/LiDAR-supported residuals can move a source-faithful permission state.",
        },
    ]

    alignment_ladder = [
        {
            "step": "decode_camera",
            "requirement": "Use official pose_encoding_to_extri_intri, not a hand-written pose guess.",
            "promotion_allowed": False,
        },
        {
            "step": "extract_centers",
            "requirement": "Invert camera-from-world extrinsics and compute camera centers C_vggt.",
            "promotion_allowed": False,
        },
        {
            "step": "sim3_rig_alignment",
            "requirement": "Solve a similarity transform from VGGT centers to Waymo rig centers over all 7 ring cameras; record scale, reflection flag, RMS, max residual, and per-camera residuals.",
            "initial_stop_thresholds": {
                "reflection_allowed": False,
                "max_center_rms_m": 0.50,
                "max_center_residual_m": 1.00,
            },
            "promotion_allowed": False,
        },
        {
            "step": "target_surface_residuals",
            "requirement": "For each frozen ROI owner-UV sample, transform VGGT point/depth to ego, compare against LiDAR or raw multi-view reprojection on the same target surface.",
            "initial_stop_thresholds": {
                "min_lidar_support_frac_for_promotion": 0.20,
                "max_raw_reprojection_median_px": 3.0,
                "max_raw_reprojection_p90_px": 8.0,
            },
            "promotion_allowed": "only in a future brief if all DB45b target-surface checks pass",
        },
    ]

    controls = []
    for name, segment_id, roi_key, known_lidar in [
        ("DB25 longline", "db45_db25_longline_low_evidence", "db25_longline", 0.094),
        ("DB41 right ROI", "db45_db41_rightline_low_lidar", "db41_right_roi", 0.084),
        ("DB41 lower-right ROI", "db45_db41_lower_right_zero_lidar", "db41_lower_right_roi", 0.0),
    ]:
        db45b_row = row_by_segment(db45_rows, segment_id)
        roi = target_uv.get(roi_key, {})
        controls.append(
            {
                "name": name,
                "segment_id": segment_id,
                "current_state": db45b_row.get("calibrated_evidence_state", "RED"),
                "known_lidar_support_frac": known_lidar,
                "vggt_owner_uv_valid_frac": roi.get("owner_uv_valid_frac_of_roi"),
                "vggt_confidence_is_diagnostic_only": True,
                "future_contract_result": "abstain_or_RED_until_lidar_raw_residual_passes",
                "hard_rule": "confidence, owner-UV validity, or decoded pointmaps cannot promote without target-surface residual support",
            }
        )

    controls.extend(
        [
            {
                "name": "DB36 fake ground slabs / holes",
                "segment_id": "db45_db36_fake_generated_ground",
                "current_state": row_by_segment(db45_rows, "db45_db36_fake_generated_ground").get(
                    "calibrated_evidence_state", "RED"
                ),
                "future_contract_result": "reject_non_admissible_generated_control",
                "hard_rule": "raw-camera VGGT cannot validate generated-core fake geometry as sensor truth",
            },
            {
                "name": "DB40 pole-like vertical artifact",
                "segment_id": "db45_db40_fake_vertical_pole",
                "current_state": row_by_segment(db45_rows, "db45_db40_fake_vertical_pole").get(
                    "calibrated_evidence_state", "RED"
                ),
                "future_contract_result": "reject_non_admissible_generated_control",
                "hard_rule": "detector-clean/object-gate PASS cannot override fake-geometry rejection",
            },
            {
                "name": "DB32 s40 handoff",
                "segment_id": "db45_db32_source_sidestep_control",
                "current_state": row_by_segment(db45_rows, "db45_db32_source_sidestep_control").get(
                    "calibrated_evidence_state", "YELLOW"
                ),
                "future_contract_result": "unchanged_caveated_handoff",
                "hard_rule": "source-sidestep handoff is not original-G seam repair and not source-faithful ceiling",
            },
        ]
    )

    return {
        "preconditions": {
            "db45g_decode_path_diagnostic": db45g.get("decision", {}).get("accepted_db45_diagnostic_evidence") is True,
            "db45g_residual_readiness": db45g.get("decision", {}).get("residual_readiness"),
            "db45f_has_pose_key": local_pose.get("has_pose_enc_key"),
            "db45f_stores_pose_tensor": local_pose.get("stores_pose_enc_tensor"),
        },
        "required_saved_outputs": required_saved_outputs,
        "alignment_ladder": alignment_ladder,
        "control_policy": controls,
        "future_brief_requirements": [
            "Run at most one log/anchor first: BMW log 02a00399 anchor 0, 7 raw ring cameras.",
            "Save pose_enc, decoded extrinsics/intrinsics, preprocess mapping, and compact residual tables before any log-tail truncation.",
            "Report camera-center Sim(3) residuals before any point residual is interpreted.",
            "Report LiDAR/raw residuals per frozen DB25/DB41 ROI; DB41 lower-right remains abstain if LiDAR support is zero.",
            "Do not emit repaired ERP, source replacement, renderer output, generated image, or permission promotion in the extractor brief.",
        ],
        "threshold_boundary": "Initial stop thresholds are future extractor contract gates only. They are not validated calibration thresholds and do not constitute current geometry evidence.",
        "claim_boundary": "This contract makes the next extractor auditable; it is not geometry evidence and does not authorize a repair.",
    }


def build_checks(
    db45b: dict[str, Any],
    db45f: dict[str, Any],
    db45f_remote: dict[str, Any],
    db45g: dict[str, Any],
    contract: dict[str, Any],
    secret_hits: list[dict[str, str]],
) -> list[dict[str, Any]]:
    def chk(check_id: str, passed: bool, severity: str, evidence: str) -> dict[str, Any]:
        return {"id": check_id, "pass": bool(passed), "severity": severity, "evidence": evidence}

    outputs = {item["field"] for item in contract["required_saved_outputs"]}
    ladder_steps = {item["step"] for item in contract["alignment_ladder"]}
    controls = {item["segment_id"]: item for item in contract["control_policy"]}

    return [
        chk(
            "db45b_permission_guardrails_present",
            bool(db45b.get("checks")),
            "precondition",
            "DB45b permission calibration is available.",
        ),
        chk(
            "db45g_decode_path_precondition",
            db45g.get("decision", {}).get("accepted_db45_diagnostic_evidence") is True
            and db45g.get("decision", {}).get("accepted_db45_geometry_evidence") is False,
            "precondition",
            "DB45g accepted only official-source decode-path diagnostic evidence.",
        ),
        chk(
            "db45f_pose_key_but_no_tensor_recorded",
            contract["preconditions"].get("db45f_has_pose_key") is True
            and contract["preconditions"].get("db45f_stores_pose_tensor") is False,
            "blocker",
            "DB45f records pose_enc as a prediction key but does not store tensor values or decoded extrinsics.",
        ),
        chk(
            "contract_requires_pose_and_decode",
            {"pose_enc", "decoded_extrinsics", "decoded_intrinsics"}.issubset(outputs),
            "blocker",
            "Future extractor must save pose_enc and decoded camera matrices.",
        ),
        chk(
            "contract_requires_preprocess_mapping",
            "preprocess_mapping" in outputs,
            "blocker",
            "Future extractor must preserve crop/pad/resize mapping.",
        ),
        chk(
            "contract_requires_sim3_alignment",
            "sim3_rig_alignment" in ladder_steps,
            "blocker",
            "Future extractor must solve/report Sim(3) alignment to Waymo rig before residuals.",
        ),
        chk(
            "contract_requires_lidar_raw_residuals",
            "target_surface_residuals" in ladder_steps and "lidar_and_raw_residuals" in outputs,
            "blocker",
            "Future extractor must compare target-surface samples to LiDAR/raw residuals.",
        ),
        chk(
            "thresholds_are_contract_only",
            "not validated calibration thresholds" in contract.get("threshold_boundary", ""),
            "blocker",
            "DB45h thresholds are future extractor stop gates only, not accepted geometry calibration.",
        ),
        chk(
            "db41_lower_right_preserved_abstain",
            controls.get("db45_db41_lower_right_zero_lidar", {}).get("known_lidar_support_frac") == 0.0
            and controls.get("db45_db41_lower_right_zero_lidar", {}).get("future_contract_result")
            == "abstain_or_RED_until_lidar_raw_residual_passes",
            "blocker",
            "DB41 lower-right remains zero-LiDAR abstain under the contract.",
        ),
        chk(
            "generated_controls_rejected",
            controls.get("db45_db36_fake_generated_ground", {}).get("future_contract_result")
            == "reject_non_admissible_generated_control"
            and controls.get("db45_db40_fake_vertical_pole", {}).get("future_contract_result")
            == "reject_non_admissible_generated_control",
            "blocker",
            "Generated fake controls remain non-admissible rejects.",
        ),
        chk(
            "no_model_action_or_repair",
            True,
            "blocker",
            "DB45h is CPU/local contract only: no model load, inference, download, renderer, repair, source replacement, or generation.",
        ),
        chk(
            "no_red_promotion",
            True,
            "blocker",
            "DB45h changes no permission state and promotes no RED control.",
        ),
        chk(
            "no_token_in_local_artifacts",
            not secret_hits,
            "blocker",
            f"Secret scan hits: {secret_hits}",
        ),
        chk(
            "db45f_remote_available",
            bool(db45f_remote.get("target_uv_sampling")),
            "precondition",
            "DB45f target-UV diagnostic result is available for current control context.",
        ),
        chk(
            "db45f_manifest_available",
            db45f.get("decision", {}).get("accepted_db45_diagnostic_evidence") is True,
            "precondition",
            "DB45f manifest accepted diagnostic-only owner-UV evidence.",
        ),
    ]


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1800, 1400), (18, 18, 18))
    draw = ImageDraw.Draw(board)

    draw.text((26, 18), "DB45h VGGT calibrated residual job contract gate", fill=(255, 255, 255), font=font(26))
    draw.text((26, 52), "CPU/local schema only. No inference, no geometry evidence, no RED promotion.", fill=(220, 220, 220), font=font(15))

    decision = manifest["decision"]
    pill(draw, (26, 88, 330, 124), "contract: diagnostic-only", (60, 120, 150))
    pill(draw, (350, 88, 560, 124), "geometry: false", (142, 74, 32))
    pill(draw, (580, 88, 790, 124), "inference: false", (80, 80, 80))
    pill(draw, (810, 88, 1010, 124), "RED promotions: 0", (80, 80, 80))

    contract = manifest["contract"]
    y = 158
    draw.text((26, y), "Required future extractor outputs", fill=(255, 255, 255), font=font(21))
    y += 32
    for item in contract["required_saved_outputs"]:
        y = draw_wrapped(draw, 44, y, f"- {item['field']}: {item['why']}", 108, (225, 238, 255), 13, 4)

    y += 10
    draw.text((26, y), "Alignment and residual ladder", fill=(255, 255, 255), font=font(21))
    y += 32
    for item in contract["alignment_ladder"]:
        y = draw_wrapped(draw, 44, y, f"- {item['step']}: {item['requirement']}", 108, (235, 235, 235), 13, 4)

    x2 = 1050
    y2 = 158
    draw.text((x2, y2), "Hard checks", fill=(255, 255, 255), font=font(21))
    y2 += 34
    for check in manifest["checks"]:
        fill = (48, 140, 82) if check["pass"] else ((190, 72, 72) if check["severity"] == "blocker" else (150, 112, 52))
        pill(draw, (x2, y2, x2 + 70, y2 + 29), "PASS" if check["pass"] else "STOP", fill)
        y2 = draw_wrapped(draw, x2 + 82, y2 + 2, check["id"], 62, (238, 238, 238), 13, 4)
        y2 += 7

    y = max(y + 18, 700)
    draw.line((26, y - 16, 1740, y - 16), fill=(70, 70, 70), width=1)
    draw.text((26, y), "Control policy", fill=(255, 255, 255), font=font(21))
    y += 32
    for item in contract["control_policy"]:
        y = draw_wrapped(
            draw,
            44,
            y,
            f"- {item['name']}: state={item.get('current_state')} -> {item.get('future_contract_result')}; {item.get('hard_rule')}",
            150,
            (255, 235, 185),
            13,
            4,
        )

    y += 10
    draw.text((26, y), "Decision boundary", fill=(255, 255, 255), font=font(21))
    y += 32
    for line in [
        decision["claim_boundary"],
        contract.get("threshold_boundary", ""),
        "Future residual extractor needs a fresh bounded sub-scope; DB45h does not authorize a run.",
        "DB41 lower-right/right-line remains no-evidence/abstain unless LiDAR/raw target-surface residuals pass.",
    ]:
        y = draw_wrapped(draw, 44, y, "- " + line, 145, (255, 220, 170), 14, 5)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def build_manifest() -> dict[str, Any]:
    db45b = read_json(DB45B)
    db45f = read_json(DB45F)
    db45f_remote = read_json(DB45F_REMOTE)
    db45g = read_json(DB45G)
    contract = build_contract(db45b, db45f_remote, db45g)

    secret_hits = scan_secret_hits([DB45B, DB45F, DB45F_REMOTE, DB45G])
    checks = build_checks(db45b, db45f, db45f_remote, db45g, contract, secret_hits)
    checks_pass = all(check["pass"] or check["severity"] == "precondition" for check in checks)

    manifest = {
        "db": "DB-45h",
        "status": "vggt_residual_job_contract_gate",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Define the future VGGT calibrated residual extractor contract without running inference or accepting geometry evidence.",
        "decision": {
            "accepted_evidence_type": "vggt-residual-job-contract-only" if checks_pass else "blocked-or-no-go",
            "accepted_db45_diagnostic_evidence": bool(checks_pass),
            "accepted_db45_geometry_evidence": False,
            "runtime_ready": False,
            "model_inference_ran": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "db45_status": "running",
            "claim_boundary": "DB45h defines the evidence contract for a future extractor; it is not metric geometry evidence and does not authorize repair or RED promotion.",
        },
        "refs": {
            "db45b_manifest": rel(DB45B),
            "db45f_manifest": rel(DB45F),
            "db45f_remote_result": rel(DB45F_REMOTE),
            "db45g_manifest": rel(DB45G),
            "board": rel(BOARD),
        },
        "scope": {
            "cpu_local_only": True,
            "model_load": False,
            "model_inference": False,
            "download": False,
            "renderer": False,
            "erp_repair": False,
            "source_replacement": False,
            "generated_image": False,
            "red_promotion": False,
        },
        "contract": contract,
        "checks": checks,
        "secret_scan_hits": secret_hits,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    secret_hits = scan_secret_hits([MANIFEST, BOARD])
    if secret_hits:
        manifest["secret_scan_hits"].extend(secret_hits)
        manifest["decision"]["accepted_evidence_type"] = "blocked-or-no-go"
        manifest["decision"]["accepted_db45_diagnostic_evidence"] = False
        manifest["checks"] = build_checks(db45b, db45f, db45f_remote, db45g, contract, manifest["secret_scan_hits"])
        MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    build_board(manifest)
    return manifest


def main() -> int:
    manifest = build_manifest()
    print(f"wrote {MANIFEST}")
    print(f"wrote {BOARD}")
    print(json.dumps(manifest["decision"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
