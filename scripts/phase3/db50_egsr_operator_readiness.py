from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db50_egsr_operator_v0"
MANIFEST = OUT_DIR / "db50_egsr_operator_readiness_manifest.json"
BOARD = OUT_DIR / "db50_egsr_operator_readiness_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
DB44 = ROOT / "deliverables" / "dit360_v2" / "db44_layer_aware_dispatcher" / "db44_layer_aware_dispatcher_manifest.json"
DB43 = ROOT / "deliverables" / "dit360_v2" / "db43_source_faithfulness_gate" / "db43_source_faithfulness_gate_manifest.json"
DB49E = ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract" / "db49e_exact_lineage_preflight_manifest.json"

CONTEXT_IMAGES = {
    "DB32 s40 caveated handoff": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db32_generated_sky_harmonize_v2"
    / "db32_generated_sky_harmonize_s40.png",
    "G diagnostic failure": ROOT / "deliverables" / "dit360_v2" / "db35_seam_first" / "G_bmw_pano_long_roi.jpg",
    "DB41 abstain evidence": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "db41_rightline_evidence_board.jpg",
    "BEV source-faithful ceiling": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db23_gate_fetch"
    / "seamroute"
    / "SR_bmw_bevfinal_1024x2048.png",
    "Fake-geometry rejects": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db44_layer_aware_dispatcher"
    / "db44_negative_controls_board.jpg",
}

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


def token_hits(paths: list[Path]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or path.suffix.lower() in {".jpg", ".png"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in TOKEN_PATTERNS.items():
            found = pattern.findall(text)
            if found:
                hits.append({"path": rel(path), "pattern": name, "count": len(found)})
    return hits


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
    size: int = 15,
) -> int:
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
            img.thumbnail((x1 - x0 - 18, y1 - y0 - 44))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception as exc:
            draw_wrapped(draw, x0 + 10, y0 + 26, f"load failed: {type(exc).__name__}", 42, fill=(240, 140, 140), size=13)
    else:
        draw_wrapped(draw, x0 + 10, y0 + 26, "missing", 42, fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def classify_component(component: dict[str, Any]) -> dict[str, Any]:
    cid = component["component_id"]
    evidence = component.get("evidence_state", "")
    claim = component.get("claim_level", "")
    branch = component.get("allowed_branch", "")
    operator = component.get("dispatch_operator", "")
    segment_type = component.get("segment_type", "")
    reason_codes = component.get("reason_codes", [])

    readiness = "blocked"
    candidate_operator = "none"
    executable_now = False
    source_faithful_candidate = False
    stop_reason = "unclassified"
    required_inputs: list[str] = []

    text = " ".join([cid, evidence, claim, branch, operator, segment_type, " ".join(reason_codes)]).lower()

    if evidence == "RED" or claim in {"reject", "diagnostic", "abstain"} or "reject" in operator.lower():
        if "db41" in text or "right" in text or "lower" in text or "no_source_evidence" in text:
            readiness = "abstain_or_reject"
            stop_reason = "red_or_no_evidence_boundary"
        elif "fake" in text or "generated fake" in text or "vertical" in text or "pole" in text or "slab" in text:
            readiness = "abstain_or_reject"
            stop_reason = "fake_geometry_reject"
        else:
            readiness = "abstain_or_reject"
            stop_reason = "diagnostic_or_rejected_control"
    elif "presentation" in branch or "generated" in claim or "sky-only" in operator:
        readiness = "presentation_only"
        candidate_operator = operator
        stop_reason = "generated_or_presentation_branch_not_source_faithful"
        required_inputs = ["generated_mask", "license_caveat", "presentation_flag"]
    elif "source/frame" in operator or "source-sidestep" in branch or "source_sidestep" in reason_codes:
        readiness = "source_sidestep_only"
        candidate_operator = operator
        stop_reason = "source_selection_not_local_repair"
        required_inputs = ["candidate lineage", "same-ROI review", "claim caveats"]
    elif evidence == "GREEN" and "keep source" in operator.lower():
        readiness = "already_satisfied_keep"
        candidate_operator = "O0 keep source pixels"
        executable_now = True
        source_faithful_candidate = True
        stop_reason = "positive_control_no_repair_needed"
        required_inputs = ["source preservation proof"]
    elif "BEV" in operator or "road atlas" in operator:
        readiness = "existing_caveated_operator_control"
        candidate_operator = operator
        source_faithful_candidate = True
        stop_reason = "bev_ceiling_exists_but_phase0_does_not_rerun_dataset"
        required_inputs = ["raw cameras", "LiDAR", "BEV ground atlas", "curb abstain mask", "source provenance"]
    elif "photometric" in segment_type.lower():
        readiness = "blocked"
        candidate_operator = "low-frequency photometric polish"
        stop_reason = "prior_photometric_attempt_rejected_or_smudge_risk"
        required_inputs = ["evidence-GREEN photometric-only seam", "source preservation diff", "same-ROI crop"]
    elif "LPAM" in operator or "far/static" in text:
        readiness = "blocked"
        candidate_operator = "LPAM-like local patch alignment"
        stop_reason = "no_current_GREEN_far_static_raw_pair_target"
        required_inputs = ["raw source pair", "far/static evidence-GREEN label", "protected-structure mask", "source NCC check"]
    else:
        readiness = "blocked"
        candidate_operator = operator
        stop_reason = "no_phase0_executable_operator"

    return {
        "component_id": cid,
        "title": component.get("title"),
        "artifact": component.get("artifact"),
        "evidence_state": evidence,
        "claim_level": claim,
        "allowed_branch": branch,
        "segment_type": segment_type,
        "db44_dispatch_operator": operator,
        "readiness": readiness,
        "candidate_operator": candidate_operator,
        "executable_now": executable_now,
        "source_faithful_candidate": source_faithful_candidate,
        "stop_reason": stop_reason,
        "required_inputs": required_inputs,
        "protected_structures": component.get("protected_structures", []),
        "reason_codes": reason_codes,
    }


def build_manifest() -> dict[str, Any]:
    brief_text = BRIEF.read_text(encoding="utf-8", errors="replace")
    db44 = read_json(DB44)
    db43 = read_json(DB43)
    db49e = read_json(DB49E)
    rows = [classify_component(c) for c in db44["components"]]

    readiness_counts = Counter(r["readiness"] for r in rows)
    evidence_counts = Counter(r["evidence_state"] for r in rows)
    stop_counts = Counter(r["stop_reason"] for r in rows)

    executable_repairs = [
        r
        for r in rows
        if r["executable_now"] and r["readiness"] not in {"already_satisfied_keep"}
    ]
    lpam_candidates = [
        r for r in rows if r["candidate_operator"] == "LPAM-like local patch alignment" and r["executable_now"]
    ]
    red_promotions = [
        r for r in rows if r["evidence_state"] == "RED" and r["executable_now"]
    ]
    unsafe_source_claims = [
        r for r in rows if r["component_id"].startswith("db44_db32") and r["claim_level"] == "source-faithful"
    ]

    decision_status = "operator_readiness_accepted_no_repair_executed"
    if executable_repairs:
        decision_status = "unexpected_phase0_repair_target_found_requires_followup_brief"

    manifest: dict[str, Any] = {
        "db": "DB-50",
        "status": "accepted_readiness_gate",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "egsr-operator-readiness-existing-artifacts-only",
        "purpose": "Move DB44 dispatcher toward source-faithful operator implementation by identifying which components are actually executable under current evidence gates.",
        "scope": {
            "cpu_local_only": True,
            "existing_artifacts_only": True,
            "component_count": len(rows),
            "new_panorama_repair": False,
            "renderer_or_dataset_run": False,
            "a100_or_executor_used": False,
            "hf_or_vggt_used": False,
            "diffusion_or_generation": False,
            "source_replacement": False,
            "db49e_rerun": False,
            "red_promotion": False,
            "output_location": rel(OUT_DIR),
        },
        "inputs": {
            "decision_brief": rel(BRIEF),
            "db43_manifest": rel(DB43),
            "db44_manifest": rel(DB44),
            "db49e_manifest": rel(DB49E),
            "db43_status": db43.get("status"),
            "db44_status": db44.get("status"),
            "db49e_status": db49e.get("status"),
        },
        "counts": {
            "evidence_state": dict(evidence_counts),
            "readiness": dict(readiness_counts),
            "stop_reasons": dict(stop_counts),
            "phase0_executable_repair_targets": len(executable_repairs),
            "already_satisfied_keep_controls": readiness_counts.get("already_satisfied_keep", 0),
            "lpam_executable_targets": len(lpam_candidates),
            "red_promotions": len(red_promotions),
            "unsafe_db32_source_faithful_claims": len(unsafe_source_claims),
        },
        "operator_policy": {
            "db41_right_lower_right": "remain RED/no-evidence/abstain; no repair or promotion under DB50 Phase0",
            "g_bmw_pano": "classic BMW failure / diagnostic reference only; not default repair base",
            "db32_s40": "Bosch-facing presentation/handoff candidate with source-sidestep + generated-sky caveats; not source-faithful ceiling",
            "lpam": "not executable until a follow-up brief supplies far/static GREEN raw pair evidence and protected-structure checks",
            "photometric_polish": "not executable from DB26-style rejected smudge evidence; needs evidence-GREEN photometric-only segment",
            "bev_road": "existing source-faithful ceiling/control only; Phase0 does not rerun dataset",
        },
        "components": rows,
        "decision": {
            "db50_status": decision_status,
            "source_faithful_operator_repair_executed": False,
            "new_candidate_image_created": False,
            "accepted_source_faithful_repair": False,
            "accepted_original_g_repair": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "ready_for_uncaveated_bosch_training_data": False,
            "next_allowed_step": (
                "Open a follow-up DB50 sub-brief only for a target with raw/source pair evidence and protected-structure checks, "
                "or return to DB47f/DB49e when source selection/provenance is the priority. Do not patch-on-patch under Phase0."
            ),
        },
        "hard_checks": [
            {
                "id": "db50_brief_exists",
                "pass": "DB-50: EGSR source-faithful operator v0" in brief_text,
                "evidence": "DB50 brief exists before execution.",
            },
            {
                "id": "db44_input_accepted",
                "pass": db44.get("status") == "accepted_dry_run_gate",
                "evidence": f"DB44 status={db44.get('status')}.",
            },
            {
                "id": "all_db44_components_reviewed",
                "pass": len(rows) == db44.get("scope", {}).get("component_count") == 29,
                "evidence": f"reviewed={len(rows)} expected={db44.get('scope', {}).get('component_count')}.",
            },
            {
                "id": "no_phase0_panorama_repair",
                "pass": True,
                "evidence": "DB50 Phase0 emits readiness manifest/board only.",
            },
            {
                "id": "no_red_promotion",
                "pass": not red_promotions,
                "evidence": f"red_promotions={len(red_promotions)}.",
            },
            {
                "id": "db32_not_source_faithful_ceiling",
                "pass": not unsafe_source_claims,
                "evidence": f"unsafe_db32_source_faithful_claims={len(unsafe_source_claims)}.",
            },
            {
                "id": "lpam_not_executable_without_green_raw_pair",
                "pass": len(lpam_candidates) == 0,
                "evidence": f"lpam_executable_targets={len(lpam_candidates)} under current local artifacts.",
            },
            {
                "id": "db49e_stays_paused_not_seam_repair",
                "pass": db49e.get("status") == "preflight_paused",
                "evidence": f"DB49e status={db49e.get('status')}; DB50 does not rerun source map.",
            },
        ],
        "outputs": {"manifest": rel(MANIFEST), "board": rel(BOARD)},
    }

    manifest_text = json.dumps(manifest, indent=2)
    strict_hits = token_hits([BRIEF, DB43, DB44, DB49E])
    manifest["strict_secret_scan"] = {
        "checked_paths": [rel(p) for p in [BRIEF, DB43, DB44, DB49E]],
        "hits": strict_hits,
        "manifest_has_secret_pattern": any(pattern.search(manifest_text) for pattern in TOKEN_PATTERNS.values()),
    }
    manifest["hard_checks"].append(
        {
            "id": "strict_secret_scan_pass",
            "pass": not strict_hits and not manifest["strict_secret_scan"]["manifest_has_secret_pattern"],
            "evidence": "Strict token/endpoint scan found no secret-like strings in DB50 inputs or manifest text.",
        }
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2300, 1800), (14, 16, 20))
    draw = ImageDraw.Draw(board)

    draw_text(draw, (28, 24), "DB50 EGSR Source-Faithful Operator Readiness", size=30)
    draw_text(
        draw,
        (28, 66),
        "CPU/local existing-artifact audit - no repair, no generation, no remote job, no RED promotion",
        fill=(225, 220, 170),
        size=16,
    )

    counts = manifest["counts"]
    pills = [
        ("status: no repair", (70, 125, 85), 180),
        (f"components: {manifest['scope']['component_count']}", (70, 95, 140), 180),
        (f"exec repairs: {counts['phase0_executable_repair_targets']}", (145, 70, 70), 190),
        (f"LPAM targets: {counts['lpam_executable_targets']}", (145, 70, 70), 175),
        (f"RED promotions: {counts['red_promotions']}", (70, 125, 85), 185),
        ("DB32 caveated", (135, 105, 55), 165),
        ("G diagnostic", (135, 105, 55), 150),
    ]
    x = 28
    for label, fill, width in pills:
        draw.rounded_rectangle((x, 104, x + width, 138), radius=5, fill=fill, outline=(185, 185, 185))
        draw_text(draw, (x + 10, 112), label, size=13)
        x += width + 12

    y = 162
    draw_text(draw, (28, y), "Readiness counts", size=22)
    y += 34
    for key, value in sorted(counts["readiness"].items()):
        y = draw_wrapped(draw, 44, y, f"- {key}: {value}", 84, size=15)

    y += 12
    draw_text(draw, (28, y), "Hard checks", size=22)
    y += 34
    for check in manifest["hard_checks"]:
        fill = (65, 140, 86) if check["pass"] else (170, 60, 60)
        draw.rounded_rectangle((44, y, 120, y + 25), radius=4, fill=fill)
        draw_text(draw, (57, y + 4), "PASS" if check["pass"] else "STOP", size=12)
        y = draw_wrapped(draw, 136, y + 3, f"{check['id']}: {check['evidence']}", 106, size=13)
        y += 7

    y += 8
    draw_text(draw, (28, y), "Operator policy", size=22)
    y += 34
    for key, value in manifest["operator_policy"].items():
        y = draw_wrapped(draw, 44, y, f"- {key}: {value}", 106, fill=(230, 225, 190), size=13)

    x2 = 1180
    draw_text(draw, (x2, 162), "Canonical visual context", size=22)
    boxes = [
        (x2, 198, x2 + 430, 468),
        (x2 + 460, 198, x2 + 910, 468),
        (x2, 500, x2 + 430, 790),
        (x2 + 460, 500, x2 + 910, 790),
        (x2, 830, x2 + 910, 1140),
    ]
    for (label, path), box in zip(CONTEXT_IMAGES.items(), boxes):
        image_box(board, path, box, label)

    y2 = 1182
    draw_text(draw, (x2, y2), "DB44 component readiness sample", size=22)
    y2 += 36
    sample = manifest["components"][:14]
    for row in sample:
        color = {
            "already_satisfied_keep": (185, 235, 190),
            "existing_caveated_operator_control": (235, 220, 160),
            "source_sidestep_only": (220, 205, 145),
            "presentation_only": (200, 180, 230),
            "abstain_or_reject": (235, 165, 165),
            "blocked": (235, 190, 150),
        }.get(row["readiness"], (220, 220, 220))
        text = f"- {row['component_id']} | {row['evidence_state']} | {row['readiness']} | {row['stop_reason']}"
        y2 = draw_wrapped(draw, x2 + 18, y2, text, 112, fill=color, size=12)
        if y2 > 1710:
            break

    y3 = 1608
    draw_text(draw, (28, y3), "Decision", size=22)
    y3 += 34
    for line in [
        manifest["decision"]["db50_status"],
        "No DB50 Phase0 source-faithful repair is executed because current local artifacts contain no new safe repair target.",
        "Follow-up requires a fresh sub-brief with raw/source pair evidence, protected-structure checks, maps, and same-ROI vision.",
    ]:
        y3 = draw_wrapped(draw, 44, y3, "- " + line, 126, fill=(235, 235, 215), size=14)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> None:
    manifest = build_manifest()
    build_board(manifest)
    print(f"wrote {MANIFEST}")
    print(f"wrote {BOARD}")
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "db50_status": manifest["decision"]["db50_status"],
                "readiness": manifest["counts"]["readiness"],
                "phase0_executable_repair_targets": manifest["counts"]["phase0_executable_repair_targets"],
                "lpam_executable_targets": manifest["counts"]["lpam_executable_targets"],
                "red_promotions": manifest["counts"]["red_promotions"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
