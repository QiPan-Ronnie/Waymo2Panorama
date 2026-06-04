#!/usr/bin/env python
"""Build DB45b existing-evidence permission calibration artifacts.

DB45b turns the already-available LiDAR/flow/depth/parallax/fake-geometry
signals into explicit EGSR permission rules. It does not run a model, render a
panorama, repair pixels, or replace sources.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit"
DB45_V0 = OUT_DIR / "db45_geometry_evidence_audit_manifest.json"
DB45A = OUT_DIR / "db45a_vggt_feasibility_manifest.json"
DB25 = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "db25_longline_summary.json"
DB41 = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_manifest.json"
DB36 = ROOT / "deliverables" / "dit360_v2" / "db36_user_redline_mask" / "db36_reject_review_manifest.json"
DB40 = (
    ROOT
    / "deliverables"
    / "dit360_v2"
    / "db40_v14_mask_alignment"
    / "db40_a1_longsrc_review_manifest.json"
)
DEPTH_VIS = ROOT / "deliverables" / "depth_visibility_seam_probe" / "batch_summary.json"
PARALLAX = ROOT / "deliverables" / "parallax_budget_map" / "batch_summary.json"

MANIFEST = OUT_DIR / "db45b_evidence_permission_calibration_manifest.json"
PERMISSION_BOARD = OUT_DIR / "db45b_permission_calibration_board.jpg"
FALSE_POS_BOARD = OUT_DIR / "db45b_false_positive_controls_board.jpg"


THRESHOLDS = {
    "min_lidar_support_frac": 0.20,
    "min_flow_reliable_frac": 0.50,
    "flow_only_can_promote": False,
    "detector_clean_can_promote": False,
    "case_level_depth_can_promote": False,
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def fmt(x: object) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, bool):
        return "true" if x else "false"
    try:
        return f"{float(x):.3f}"
    except (TypeError, ValueError):
        return str(x)


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    width: int,
    color: tuple[int, int, int],
    size: int = 14,
    line_gap: int = 6,
) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw.text((x, y), line, fill=color, font=font(size))
        y += size + line_gap
    return y


def load_fit(rel_path: str, size: tuple[int, int]) -> Image.Image:
    path = ROOT / rel_path
    img = Image.open(path).convert("RGB")
    canvas = Image.new("RGB", size, (18, 18, 18))
    work = img.copy()
    work.thumbnail(size, Image.Resampling.LANCZOS)
    canvas.paste(work, ((size[0] - work.width) // 2, (size[1] - work.height) // 2))
    return canvas


def db45_segments_by_id(db45: dict) -> dict[str, dict]:
    return {seg["segment_id"]: seg for seg in db45["segments"]}


def first_case(summary: dict, case_name: str) -> dict:
    for case in summary.get("cases", []):
        if case.get("case") == case_name:
            return case
    return {}


def build_rows() -> tuple[list[dict], dict]:
    db45 = read_json(DB45_V0)
    db45a = read_json(DB45A)
    db25 = read_json(DB25)
    db41 = read_json(DB41)
    db36 = read_json(DB36)
    db40 = read_json(DB40)
    depth = read_json(DEPTH_VIS)
    parallax = read_json(PARALLAX)
    seg = db45_segments_by_id(db45)

    depth_bmw = first_case(depth, "02a00399_a000_bmw")
    parallax_bmw = first_case(parallax, "02a00399_a000_bmw")
    case_level_depth = {
        "depth_visibility_lidar_supported_frac": depth_bmw.get("depth_visibility_global", {}).get(
            "lidar_supported_frac_of_band"
        ),
        "depth_visibility_parallax_p90_px": depth_bmw.get("depth_visibility_global", {})
        .get("parallax_px", {})
        .get("p90"),
        "parallax_budget_supported_frac": parallax_bmw.get("global", {}).get("supported_frac_of_band"),
        "parallax_budget_p90_px": parallax_bmw.get("global", {}).get("parallax_px", {}).get("p90"),
        "case_level_only": True,
    }

    def row(
        segment_id: str,
        label: str,
        signal_summary: dict[str, object],
        blocker_codes: list[str],
        false_positive_modes: list[str],
        calibration_note: str,
    ) -> dict:
        base = seg[segment_id]
        before = base["evidence_state_before"]
        after = base["evidence_state_after"]
        claim = base["claim_after"]
        calibrated_after = after
        permission_delta = "unchanged"
        if after == "RED":
            calibrated_after = "RED"
        return {
            "segment_id": segment_id,
            "label": label,
            "role": base["role"],
            "layer": base["layer"],
            "claim_after_db45_v0": claim,
            "evidence_state_db45_v0": after,
            "calibrated_evidence_state": calibrated_after,
            "permission_delta": permission_delta,
            "signal_summary": signal_summary,
            "blocker_codes": blocker_codes,
            "false_positive_modes": false_positive_modes,
            "calibration_note": calibration_note,
            "artifact": base["artifact"],
        }

    right = db41["summaries"]["right_roi"]
    lower = db41["summaries"]["lower_right_roi"]
    right_thr = db41["threshold_results"]["right_roi"]
    lower_thr = db41["threshold_results"]["lower_right_roi"]

    rows = [
        row(
            "db45_clean_source_preservation",
            "DB34 source preservation positive",
            {
                "source_preservation": True,
                "noncore_byte_exact": True,
                "generated_mask_excluded": True,
            },
            [],
            [],
            "GREEN control remains GREEN because the claim is only byte-exact source preservation outside generated sky.",
        ),
        row(
            "db45_bev_planar_road_control",
            "BEV planar-road source-faithful control",
            {
                "planar_road_only": True,
                "curb_or_right_line_promoted": False,
                "case_level_depth": case_level_depth,
            },
            ["curb_floor_not_solved", "case_level_depth_cannot_promote_target_roi"],
            ["case_level_depth_overgeneralization"],
            "YELLOW remains YELLOW: planar-road evidence does not transfer to off-plane curb/right-line repair.",
        ),
        row(
            "db45_db32_source_sidestep_control",
            "DB32 source-sidestep handoff",
            {
                "source_sidestep": True,
                "generated_sky_caveat": True,
                "original_g_repair": False,
            },
            ["not_original_g_repair", "not_fully_source_faithful"],
            ["source_sidestep_overclaim"],
            "YELLOW remains YELLOW: useful Bosch-facing handoff, not a source-faithful ceiling or G repair.",
        ),
        row(
            "db45_db25_longline_abstain",
            "DB25 long-line low-support ROI",
            {
                "near_ground_frac": db25["near_ground_frac"],
                "lidar_support_frac": db25["lidar_support_frac"],
                "best_flow_pair": db25["best_flow_pair"],
                "best_flow_reliable_frac": db25["best_flow_reliable_frac"],
                "key_pair_6_5_flow_frac": db25["flow_pair_stats"]["6-5"]["fb_reliable_frac"],
                "passes_lidar_threshold": db25["lidar_support_frac"] >= THRESHOLDS["min_lidar_support_frac"],
                "case_level_depth": case_level_depth,
            },
            ["low_lidar_support", "low_key_pair_flow", "flow_only_cannot_promote"],
            ["best_flow_pair_overclaim", "case_level_depth_overgeneralization"],
            "RED abstain holds: best flow is not the target surface, and key-pair flow plus LiDAR support are weak.",
        ),
        row(
            "db45_db41_right_roi_abstain",
            "DB41 right ROI",
            {
                "near_ground_frac": right["near_ground_frac"],
                "lidar_support_frac": right["lidar_support_frac"],
                "best_flow_pair": right["best_flow_pair"],
                "best_flow_reliable_frac": right["best_flow_reliable_frac"],
                "target_pair_5_4_flow_frac": right["flow_pair_stats"]["5-4"]["fb_reliable_frac"],
                "right_pair_6_5_flow_frac": right["flow_pair_stats"]["6-5"]["fb_reliable_frac"],
                "passes_flow_threshold": right_thr["passes_flow_threshold"],
                "passes_lidar_threshold": right_thr["passes_lidar_threshold"],
                "passes_db41_gate": right_thr["passes_db41_gate"],
            },
            ["low_lidar_support", "flow_only_cannot_promote", "target_surface_not_continuous"],
            ["flow_only_false_positive"],
            "RED abstain holds even though best-flow passes; sparse LiDAR and visual support do not establish a continuous right-line/curb surface.",
        ),
        row(
            "db45_db41_lower_right_abstain",
            "DB41 lower-right ROI",
            {
                "near_ground_frac": lower["near_ground_frac"],
                "lidar_support_frac": lower["lidar_support_frac"],
                "best_flow_pair": lower["best_flow_pair"],
                "best_flow_reliable_frac": lower["best_flow_reliable_frac"],
                "target_pair_5_4_flow_frac": lower["flow_pair_stats"]["5-4"]["fb_reliable_frac"],
                "passes_flow_threshold": lower_thr["passes_flow_threshold"],
                "passes_lidar_threshold": lower_thr["passes_lidar_threshold"],
                "passes_db41_gate": lower_thr["passes_db41_gate"],
            },
            ["zero_lidar_support", "near_ground_no_target_surface", "flow_only_cannot_promote"],
            ["flow_only_false_positive"],
            "RED abstain is locked: all-near-ground ROI has zero LiDAR support despite flow patches.",
        ),
        row(
            "db45_db36_fake_redline_reject",
            "DB36 fake right-line DiT control",
            {
                "outside_mask_max_abs_diff": db36["outside_mask_max_abs_diff"],
                "core_fraction": db36["core_fraction"],
                "core_mean_abs_diff": db36["core_mean_abs_diff"],
                "vision_verdict": db36["vision_verdict"],
            },
            ["fake_ground_slab", "fake_hole", "detector_clean_cannot_promote", "generated_core_not_source_truth"],
            ["detector_clean_false_positive", "mask_preservation_overclaim"],
            "RED reject holds: outside-mask preservation does not make fake generated ground source-faithful.",
        ),
        row(
            "db45_db40_longsrc_fake_pole_reject",
            "DB40 detector-clean fake-pole control",
            {
                "object_gate_pass": db40["object_gate"]["PASS"],
                "netnew_count": db40["object_gate"]["netnew_count"],
                "selected_core_fraction": db40["diagnostics"]["core_fraction"],
                "vision_long_source": db40["vision_verdict"]["long_source"],
                "vision_decision": db40["vision_verdict"]["decision"],
            },
            ["pole_like_artifact", "vertical_slice", "detector_clean_cannot_promote", "object_gate_pass_insufficient"],
            ["detector_clean_false_positive"],
            "RED reject holds: object-gate PASS with netnew=0 still generated a pole-like seam artifact.",
        ),
    ]

    context = {
        "db45_status": db45["status"],
        "db45a_route_state": db45a["route_state"],
        "db45a_hf_head_config_status": db45a["remote_checks"]["hf_access_check"]["head_config_json_status"],
        "case_level_depth": case_level_depth,
    }
    return rows, context


def build_checks(rows: list[dict]) -> list[dict[str, object]]:
    by_id = {row["segment_id"]: row for row in rows}

    def check(check_id: str, passed: bool, evidence: str) -> dict[str, object]:
        return {"id": check_id, "pass": bool(passed), "evidence": evidence}

    red_rows = [row for row in rows if row["evidence_state_db45_v0"] == "RED"]
    return [
        check(
            "no_model_or_repair",
            True,
            "DB45b uses existing JSON/images only: no A100, no model download/inference, no panorama repair.",
        ),
        check(
            "no_permission_delta",
            all(row["permission_delta"] == "unchanged" for row in rows),
            "All 8 frozen controls keep their DB45 v0 permission state.",
        ),
        check(
            "no_red_promotion",
            all(row["calibrated_evidence_state"] == "RED" for row in red_rows),
            "DB25/DB41/DB36/DB40 RED controls remain RED.",
        ),
        check(
            "flow_only_cannot_promote_db41_right",
            "flow_only_cannot_promote" in by_id["db45_db41_right_roi_abstain"]["blocker_codes"],
            "DB41 right ROI has best-flow pass but low LiDAR support, so flow-only is a false-positive mode.",
        ),
        check(
            "db41_gate_required",
            by_id["db45_db41_right_roi_abstain"]["signal_summary"]["passes_db41_gate"] is False
            and by_id["db45_db41_lower_right_abstain"]["signal_summary"]["passes_db41_gate"] is False,
            "DB41 ROIs can only pass through the DB41 gate; current right/lower-right thresholds both fail.",
        ),
        check(
            "target_pair_required",
            by_id["db45_db25_longline_abstain"]["signal_summary"]["key_pair_6_5_flow_frac"]
            < THRESHOLDS["min_flow_reliable_frac"]
            and by_id["db45_db41_right_roi_abstain"]["signal_summary"]["right_pair_6_5_flow_frac"]
            < THRESHOLDS["min_flow_reliable_frac"],
            "Best-pair flow cannot override weak target-pair support in DB25/DB41.",
        ),
        check(
            "lidar_floor_required",
            by_id["db45_db25_longline_abstain"]["signal_summary"]["lidar_support_frac"]
            < THRESHOLDS["min_lidar_support_frac"]
            and by_id["db45_db41_right_roi_abstain"]["signal_summary"]["lidar_support_frac"]
            < THRESHOLDS["min_lidar_support_frac"],
            "ROIs below the 0.20 LiDAR support floor remain abstain/reject.",
        ),
        check(
            "db41_lower_right_zero_lidar_locked",
            by_id["db45_db41_lower_right_abstain"]["signal_summary"]["lidar_support_frac"] == 0.0,
            "DB41 lower-right remains all-near-ground with zero LiDAR target support.",
        ),
        check(
            "zero_lidar_hard_fail",
            by_id["db45_db41_lower_right_abstain"]["calibrated_evidence_state"] == "RED",
            "Zero-LiDAR target support is a hard fail for source-faithful geometry repair.",
        ),
        check(
            "db25_key_pair_low_flow_locked",
            by_id["db45_db25_longline_abstain"]["signal_summary"]["key_pair_6_5_flow_frac"] < 0.2,
            "DB25 key right/dark-wall pair 6-5 remains low-flow despite another pair having higher flow.",
        ),
        check(
            "db36_fake_geometry_rejected",
            by_id["db45_db36_fake_redline_reject"]["calibrated_evidence_state"] == "RED",
            "DB36 fake slabs/holes are rejected even with outside-mask preservation.",
        ),
        check(
            "outside_mask_not_core_evidence",
            by_id["db45_db36_fake_redline_reject"]["signal_summary"]["outside_mask_max_abs_diff"] == 0,
            "Outside-mask byte-exact preservation proves containment only, not generated-core geometry.",
        ),
        check(
            "generated_core_reject",
            by_id["db45_db36_fake_redline_reject"]["calibrated_evidence_state"] == "RED",
            "Generated fake seam cores remain rejected instead of becoming source truth.",
        ),
        check(
            "db40_detector_clean_rejected",
            by_id["db45_db40_longsrc_fake_pole_reject"]["signal_summary"]["object_gate_pass"] is True
            and by_id["db45_db40_longsrc_fake_pole_reject"]["calibrated_evidence_state"] == "RED",
            "DB40 object-gate PASS remains a fake-geometry reject.",
        ),
        check(
            "detector_clean_not_geometry",
            by_id["db45_db40_longsrc_fake_pole_reject"]["signal_summary"]["object_gate_pass"] is True
            and "detector_clean_cannot_promote" in by_id["db45_db40_longsrc_fake_pole_reject"]["blocker_codes"],
            "Detector clean means no new detected salient object, not source-faithful geometry.",
        ),
        check(
            "case_depth_diagnostic_only",
            "case_level_depth_overgeneralization" in by_id["db45_db25_longline_abstain"]["false_positive_modes"]
            and "case_level_depth_overgeneralization" in by_id["db45_bev_planar_road_control"]["false_positive_modes"],
            "Case-level depth/parallax remains diagnostic unless ROI-specific target-surface evidence exists.",
        ),
        check(
            "db32_not_promoted",
            by_id["db45_db32_source_sidestep_control"]["calibrated_evidence_state"] == "YELLOW"
            and by_id["db45_db32_source_sidestep_control"]["claim_after_db45_v0"] == "source-sidestep",
            "DB32 remains source-sidestep/handoff, not fully source-faithful or original-G repair.",
        ),
    ]


def write_manifest(rows: list[dict], context: dict, checks: list[dict]) -> dict:
    red_promotions = [
        row["segment_id"]
        for row in rows
        if row["evidence_state_db45_v0"] == "RED" and row["calibrated_evidence_state"] != "RED"
    ]
    manifest = {
        "db": "DB-45b",
        "status": "existing_evidence_permission_calibration",
        "purpose": "Convert existing LiDAR/flow/depth/parallax/fake-geometry evidence into explicit EGSR permission rules.",
        "scope": {
            "existing_artifacts_only": True,
            "cpu_local_only": True,
            "a100_used": False,
            "network_used": False,
            "model_download": False,
            "model_inference": False,
            "panorama_generation": False,
            "panorama_repair": False,
            "source_replacement": False,
            "fixed_controls": 8,
        },
        "thresholds": THRESHOLDS,
        "context": context,
        "refs": {
            "db45_v0_manifest": rel(DB45_V0),
            "db45a_manifest": rel(DB45A),
            "db25_summary": rel(DB25),
            "db41_manifest": rel(DB41),
            "db36_manifest": rel(DB36),
            "db40_manifest": rel(DB40),
            "depth_visibility_summary": rel(DEPTH_VIS),
            "parallax_budget_summary": rel(PARALLAX),
        },
        "calibration_rules": [
            {
                "rule_id": "target_surface_support_required",
                "text": "Geometry repair permission requires target-surface evidence, not only a case-level seam-band metric.",
            },
            {
                "rule_id": "flow_only_no_promotion",
                "text": "High forward/backward flow reliability may support a candidate, but cannot promote RED without LiDAR/raw target-surface support and visual continuity.",
            },
            {
                "rule_id": "detector_clean_no_promotion",
                "text": "Object-gate PASS or netnew=0 cannot promote generated seam geometry if the seam-local surface is visually fake.",
            },
            {
                "rule_id": "source_sidestep_not_repair",
                "text": "A cleaner source-selected candidate is labeled source-sidestep/handoff unless it repairs the original source seam with evidence.",
            },
            {
                "rule_id": "case_level_depth_no_roi_transfer",
                "text": "Case-level depth/parallax metadata can calibrate risk, but cannot promote a DB41-like target ROI by itself.",
            },
            {
                "rule_id": "target_pair_required",
                "text": "A best-flow pair cannot launder a weak target pair; target ROI and target pair support must be reported.",
            },
        ],
        "rows": rows,
        "checks": checks,
        "decision": {
            "gate_pass": all(c["pass"] for c in checks),
            "permission_state_changes": "none",
            "red_promotions": red_promotions,
            "accepted_db45_evidence": True,
            "accepted_evidence_type": "permission-calibration-only",
            "db45_remains_running": True,
            "why": "Existing evidence is sufficient to formalize RED-preserving kill checks, but not sufficient to promote any RED seam.",
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "permission_board": rel(PERMISSION_BOARD),
            "false_positive_board": rel(FALSE_POS_BOARD),
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def state_color(state: str) -> tuple[int, int, int]:
    return {
        "GREEN": (110, 230, 135),
        "YELLOW": (235, 205, 90),
        "RED": (255, 130, 120),
    }.get(state, (220, 220, 220))


def draw_permission_board(rows: list[dict], checks: list[dict], manifest: dict) -> None:
    board = Image.new("RGB", (1900, 2280), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((28, 24), "DB45b existing-evidence permission calibration", fill=(255, 255, 255), font=font(28))
    draw.text(
        (28, 60),
        "No A100, no model, no repair. Existing evidence only. Goal: prevent false RED promotion before EGSR operators.",
        fill=(225, 225, 225),
        font=font(15),
    )

    x = 28
    y = 105
    col_w = [330, 140, 160, 300, 420, 430]
    headers = ["Control", "Before", "After", "Key signals", "Blockers", "Calibration note"]
    cx = x
    for w, h in zip(col_w, headers):
        draw.rectangle((cx, y, cx + w - 8, y + 34), fill=(38, 38, 38))
        draw.text((cx + 8, y + 8), h, fill=(255, 255, 255), font=font(14))
        cx += w
    y += 46

    for row in rows:
        row_h = 148
        cx = x
        draw.rectangle((x, y - 6, 1870, y + row_h - 10), outline=(55, 55, 55), width=1)
        draw_wrapped(draw, cx + 8, y, row["label"], 34, (240, 240, 240), 14)
        cx += col_w[0]
        draw.text((cx + 8, y), row["evidence_state_db45_v0"], fill=state_color(row["evidence_state_db45_v0"]), font=font(15))
        draw_wrapped(draw, cx + 8, y + 28, row["claim_after_db45_v0"], 16, (200, 200, 200), 12)
        cx += col_w[1]
        draw.text((cx + 8, y), row["calibrated_evidence_state"], fill=state_color(row["calibrated_evidence_state"]), font=font(15))
        draw_wrapped(draw, cx + 8, y + 28, row["permission_delta"], 16, (200, 200, 200), 12)
        cx += col_w[2]

        signals = []
        for key, val in row["signal_summary"].items():
            if isinstance(val, dict):
                continue
            signals.append(f"{key}={fmt(val)}")
        draw_wrapped(draw, cx + 8, y, "; ".join(signals[:6]), 39, (220, 220, 220), 12)
        cx += col_w[3]
        draw_wrapped(draw, cx + 8, y, "; ".join(row["blocker_codes"]) or "none", 52, (255, 175, 165), 12)
        cx += col_w[4]
        draw_wrapped(draw, cx + 8, y, row["calibration_note"], 58, (220, 220, 220), 12)
        y += row_h

    y += 8
    draw.text((28, y), "Hard checks", fill=(255, 255, 255), font=font(20))
    y += 30
    for c in checks:
        color = (130, 245, 150) if c["pass"] else (255, 115, 115)
        draw.text((44, y), ("PASS " if c["pass"] else "FAIL ") + c["id"], fill=color, font=font(14))
        y = draw_wrapped(draw, 66, y + 22, c["evidence"], 145, (220, 220, 220), 12)
        y += 6

    draw.text((1040, 2040), "Decision", fill=(255, 255, 255), font=font(21))
    decision_lines = [
        f"gate_pass={manifest['decision']['gate_pass']}",
        "permission_state_changes=none",
        "red_promotions=[]",
        "accepted_evidence_type=permission-calibration-only",
        "DB45 remains running; this does not solve/repair seams.",
    ]
    y2 = 2070
    for line in decision_lines:
        draw.text((1040, y2), line, fill=(235, 235, 235), font=font(14))
        y2 += 25

    board.save(PERMISSION_BOARD)


def draw_false_positive_board(rows: list[dict], manifest: dict) -> None:
    board = Image.new("RGB", (1800, 1500), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((24, 20), "DB45b false-positive controls: why RED cannot be promoted", fill=(255, 255, 255), font=font(26))
    draw.text(
        (24, 54),
        "Flow-only, detector-clean, and case-level depth/parallax are useful diagnostics, not enough for source-faithful repair permission.",
        fill=(225, 225, 225),
        font=font(15),
    )

    tiles = [
        (
            "DB25 long-line: best flow exists, key pair weak",
            "deliverables/dit360_v2/db25_longline_evidence_fetch/db25_longline_evidence_montage.jpg",
            "LiDAR=0.094, best flow=0.682, key pair 6-5 flow=0.105 -> RED/abstain.",
        ),
        (
            "DB41 right ROI: flow-only false positive",
            "deliverables/dit360_v2/db41_rightline_evidence_gate/right_roi/db25_longline_evidence_montage.jpg",
            "Best flow=0.863 but LiDAR=0.084 and no continuous right-line/curb surface -> RED/abstain.",
        ),
        (
            "DB41 lower-right: zero target support",
            "deliverables/dit360_v2/db41_rightline_evidence_gate/lower_right_roi/db25_longline_evidence_montage.jpg",
            "All near-ground, LiDAR=0.000, flow patches attach to fragments -> RED/abstain.",
        ),
        (
            "DB36: mask preservation does not save fake ground",
            "deliverables/dit360_v2/db36_user_redline_mask/db36_reject_review_board.jpg",
            "Outside-mask diff=0 but seam core has fake slabs/holes -> RED/reject.",
        ),
        (
            "DB40: detector-clean output can still be fake",
            "deliverables/dit360_v2/db40_v14_mask_alignment/db40_a1_longsrc_review_board.jpg",
            "Object gate PASS/netnew=0 but pole-like vertical artifact appears -> RED/reject.",
        ),
        (
            "DB45 rule summary",
            "deliverables/dit360_v2/db45_geometry_evidence_audit/db45_negative_controls_board.jpg",
            "No RED promotion. Target-surface support is required before any source-faithful geometry operator.",
        ),
    ]

    x0, y0 = 24, 95
    tile_w, tile_h = 570, 430
    img_h = 270
    for idx, (title, img_path, note) in enumerate(tiles):
        col = idx % 3
        row = idx // 3
        x = x0 + col * 595
        y = y0 + row * 520
        draw.rectangle((x, y, x + tile_w, y + tile_h), fill=(28, 28, 28), outline=(70, 70, 70), width=1)
        draw.text((x + 14, y + 12), title, fill=(255, 255, 255), font=font(16))
        try:
            img = load_fit(img_path, (tile_w - 28, img_h))
            board.paste(img, (x + 14, y + 42))
        except FileNotFoundError:
            draw.rectangle((x + 14, y + 42, x + tile_w - 14, y + 42 + img_h), fill=(45, 25, 25))
            draw_wrapped(draw, x + 25, y + 60, f"missing: {img_path}", 56, (255, 160, 160), 13)
        draw_wrapped(draw, x + 14, y + 330, note, 68, (225, 225, 225), 13)

    y = 1125
    draw.text((24, y), "Accepted DB45b evidence", fill=(255, 255, 255), font=font(21))
    y += 36
    lines = [
        "Accepted: permission-calibration-only evidence, not repair evidence.",
        "Formal EGSR guard: flow-only, detector-clean, and case-level depth/parallax cannot promote RED.",
        "DB41 lower-right/right-line remains no-evidence/abstain; DB36/DB40 remain fake-geometry rejects.",
        "DB32 remains Bosch-facing caveated handoff/source-sidestep, not fully source-faithful and not original-G repair.",
        f"gate_pass={manifest['decision']['gate_pass']}; red_promotions={manifest['decision']['red_promotions']}",
    ]
    for line in lines:
        y = draw_wrapped(draw, 44, y, line, 170, (230, 230, 230), 15)
        y += 8

    board.save(FALSE_POS_BOARD)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, context = build_rows()
    checks = build_checks(rows)
    manifest = write_manifest(rows, context, checks)
    draw_permission_board(rows, checks, manifest)
    draw_false_positive_board(rows, manifest)
    print(f"wrote {MANIFEST}")
    print(f"gate_pass={manifest['decision']['gate_pass']} red_promotions={manifest['decision']['red_promotions']}")


if __name__ == "__main__":
    main()
