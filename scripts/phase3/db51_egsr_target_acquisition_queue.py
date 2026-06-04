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
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db51_egsr_target_acquisition"
MANIFEST = OUT_DIR / "db51_egsr_target_acquisition_manifest.json"
BOARD = OUT_DIR / "db51_egsr_target_acquisition_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
DB44 = ROOT / "deliverables" / "dit360_v2" / "db44_layer_aware_dispatcher" / "db44_layer_aware_dispatcher_manifest.json"
DB47D = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining" / "db47d_exact_same_log_review_manifest.json"
DB47E = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining" / "db47e_final_candidate_review_manifest.json"
DB50 = ROOT / "deliverables" / "dit360_v2" / "db50_egsr_operator_v0" / "db50_egsr_operator_readiness_manifest.json"
DB25 = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "db25_longline_summary.json"
DB41 = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_manifest.json"
DB49E = ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract" / "db49e_exact_lineage_preflight_manifest.json"

CONTEXT_IMAGES = {
    "DB47e source-selection review": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db47_source_candidate_mining"
    / "db47e_final_candidate_review_board.jpg",
    "DB50 no executable repair target": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db50_egsr_operator_v0"
    / "db50_egsr_operator_readiness_board.jpg",
    "DB25 longline evidence": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db25_longline_evidence_fetch"
    / "db25_longline_evidence_montage.jpg",
    "DB41 right/lower-right abstain": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "db41_rightline_evidence_board.jpg",
    "DB44 fake-geometry rejects": ROOT
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


def db47_asset_gaps(db47d: dict[str, Any], db47e: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reviewed = {r["candidate_id"]: r for r in db47d.get("reviewed_rows", [])}
    for cid in db47e.get("scope", {}).get("missing_exact_holds_preserved", []):
        row = reviewed.get(cid, {})
        rows.append(
            {
                "candidate_id": cid,
                "anchor": row.get("anchor"),
                "bucket": row.get("db47b_bucket"),
                "missing": "compare_and_final",
                "available": [],
                "reason": "DB47d/DB47e hold: exact same-log assets absent locally.",
            }
        )
    for r in db47e.get("candidate_review", []):
        if r.get("candidate_id") == "02a00399_a0105":
            rows.append(
                {
                    "candidate_id": r.get("candidate_id"),
                    "anchor": r.get("anchor"),
                    "bucket": "strict_review_bucket",
                    "missing": "final",
                    "available": [r.get("exact_assets", {}).get("compare")],
                    "reason": "a105 has exact compare only; no final image exists locally.",
                }
            )
    return rows


def build_acquisition_queue(
    db47d: dict[str, Any],
    db47e: dict[str, Any],
    db50: dict[str, Any],
    db25: dict[str, Any],
    db41: dict[str, Any],
    db49e: dict[str, Any],
) -> list[dict[str, Any]]:
    gaps = db47_asset_gaps(db47d, db47e)
    db25_not_eligible = db25.get("lidar_support_frac", 0.0) < 0.2 or db25.get("near_ground_frac", 1.0) > 0.5
    db41_right = db41.get("threshold_results", {}).get("right_roi", {})
    db41_lower = db41.get("threshold_results", {}).get("lower_right_roi", {})
    db41_not_eligible = not db41_right.get("passes_db41_gate", False) and not db41_lower.get("passes_db41_gate", False)

    return [
        {
            "rank": 1,
            "queue_id": "db47f_fixed_universe_exact_source_selection_closure",
            "category": "source-selection",
            "claim_if_successful": "source-sidestep candidate evidence only",
            "why_ranked_here": "DB50 found no local repair target; source/frame selection is the only currently accepted seam-quality sidestep path.",
            "available_evidence": [
                "DB47b fixed 22-row candidate universe",
                "DB47c same-ROI visual accounting",
                "DB47d exact same-log review pack",
                "DB47e a200/a204/a105 final-candidate accounting",
            ],
            "missing_or_blocked": gaps,
            "allowed_next_action": (
                "Open DB47f with max 8 anchors (7 missing exact holds plus a105 final gap) to fetch or rerun exact compare/final assets, "
                "only after secure runtime/data preconditions are available."
            ),
            "kill_criteria_pointer": [
                "unbounded dataset scan",
                "cherry-pick by pretty montage",
                "source selection described as original-G repair",
                "DB41/no-evidence promotion",
                "pasted-token command or artifact use",
            ],
            "can_improve_seam_quality": True,
            "can_be_source_faithful_repair": False,
            "requires_remote_or_data": True,
            "requires_secure_secret_source": True,
            "expected_output_location": "deliverables/dit360_v2/db47_source_candidate_mining/",
        },
        {
            "rank": 2,
            "queue_id": "db50b_lpam_or_local_alignment_target_evidence",
            "category": "operator-target",
            "claim_if_successful": "possible source-faithful operator target, not yet repair",
            "why_ranked_here": "This is the direct path from EGSR dispatcher to an algorithmic operator, but DB50 found no GREEN/far-static raw-pair target locally.",
            "available_evidence": [
                "DB50 readiness says lpam_executable_targets=0",
                "DB25/DB41 provide negative controls and flow/LiDAR boundaries",
            ],
            "missing_or_blocked": [
                "far/static GREEN segment with raw source-pair crops",
                "owner/source support at target pixels",
                "protected-structure mask",
                "occlusion/flow consistency check",
                "same-ROI before/after target for vision review",
            ],
            "negative_controls": {
                "db25_longline_not_eligible": db25_not_eligible,
                "db25_lidar_support_frac": db25.get("lidar_support_frac"),
                "db25_near_ground_frac": db25.get("near_ground_frac"),
                "db25_key_pair_6_5_flow_reliable_frac": db25.get("flow_pair_stats", {}).get("6-5", {}).get("fb_reliable_frac"),
                "db41_not_eligible": db41_not_eligible,
                "db41_right_lidar_gate": db41_right,
                "db41_lower_right_lidar_gate": db41_lower,
            },
            "allowed_next_action": (
                "Open a target-specific DB50b/DB51b evidence brief only after selecting a fixed segment and acquiring raw/source-pair evidence; "
                "do not run LPAM on DB41/DB25 RED controls."
            ),
            "kill_criteria_pointer": [
                "flow-only or confidence-only promotion",
                "object/curb/lane/topology deformation",
                "blocky source swap",
                "no protected-structure mask",
                "no same-ROI vision board",
            ],
            "can_improve_seam_quality": True,
            "can_be_source_faithful_repair": True,
            "requires_remote_or_data": True,
            "requires_secure_secret_source": True,
            "expected_output_location": "deliverables/dit360_v2/db50_egsr_operator_v0/",
        },
        {
            "rank": 3,
            "queue_id": "db49e_exact_lineage_source_provenance",
            "category": "provenance",
            "claim_if_successful": "data-contract source/provenance sidecars only",
            "why_ranked_here": "Important for Bosch packaging, but DB49e does not improve seam quality and remains preflight-paused.",
            "available_evidence": [
                "DB47e confirms a200 as current DB32 source-sidestep base",
                "DB49d sidecar instrumentation exists",
                "DB49e lineage preflight passed non-runtime checks",
            ],
            "missing_or_blocked": db49e.get("pause_reasons", []),
            "allowed_next_action": "Resume only after COLAB_URL/COLAB_TOKEN env or non-repo runtime secret file and exact data path are available.",
            "kill_criteria_pointer": [
                "chat-pasted token used in command/artifact",
                "generated/VC/out-of-FOV pixels labeled as camera-owned",
                "source map described as seam repair",
            ],
            "can_improve_seam_quality": False,
            "can_be_source_faithful_repair": False,
            "requires_remote_or_data": True,
            "requires_secure_secret_source": True,
            "expected_output_location": "deliverables/dit360_v2/db49_bosch_data_contract/",
        },
        {
            "rank": 4,
            "queue_id": "db45_fixed_target_geometry_evidence",
            "category": "geometry-evidence",
            "claim_if_successful": "diagnostic/evidence only unless calibrated target-surface support passes",
            "why_ranked_here": "Could support future EGSR permission, but DB45k blocks VGGT residual promotion and broad model chasing would be patch-on-patch.",
            "available_evidence": [
                "DB45j real VGGT inference diagnostics",
                "DB45k reflection/coordinate audit",
                "DB25/DB41 residual no-promotion controls",
            ],
            "missing_or_blocked": [
                "new official-source convention evidence",
                "fixed target selected before model run",
                "raw/LiDAR target-surface residual gate that can pass",
            ],
            "allowed_next_action": "Only open a fixed-target geometry evidence brief if it directly serves DB51/DB50 target eligibility.",
            "kill_criteria_pointer": [
                "VGGT residual patch-on-patch",
                "model confidence treated as source truth",
                "DB41 lower-right promoted despite zero LiDAR",
            ],
            "can_improve_seam_quality": False,
            "can_be_source_faithful_repair": False,
            "requires_remote_or_data": True,
            "requires_secure_secret_source": True,
            "expected_output_location": "deliverables/dit360_v2/db45_geometry_evidence_audit/",
        },
        {
            "rank": 5,
            "queue_id": "db46_db48_presentation_only_cleanup",
            "category": "presentation-only",
            "claim_if_successful": "presentation/demo only with generated/edit masks",
            "why_ranked_here": "Useful only if the priority switches to a meeting/demo image; it must not contaminate source-faithful or Bosch training-data claims.",
            "available_evidence": [
                "DB43/DB44 presentation branch separation",
                "DB48/DiT360 prior negative data claims",
            ],
            "missing_or_blocked": [
                "explicit user priority switch to presentation",
                "base-selection decision before generation",
                "generated/edit masks and license caveats",
            ],
            "allowed_next_action": "Keep parked unless the user explicitly asks for meeting/demo presentation.",
            "kill_criteria_pointer": [
                "invented road/curb/lane/salient objects",
                "shown as Bosch/source-faithful data",
                "base silently defaults to G_bmw_pano",
            ],
            "can_improve_seam_quality": True,
            "can_be_source_faithful_repair": False,
            "requires_remote_or_data": True,
            "requires_secure_secret_source": True,
            "expected_output_location": "deliverables/dit360_v2/db46_presentation_only_bmw_cleanup/",
        },
    ]


def build_manifest() -> dict[str, Any]:
    brief_text = BRIEF.read_text(encoding="utf-8", errors="replace")
    db44 = read_json(DB44)
    db47d = read_json(DB47D)
    db47e = read_json(DB47E)
    db50 = read_json(DB50)
    db25 = read_json(DB25)
    db41 = read_json(DB41)
    db49e = read_json(DB49E)

    queue = build_acquisition_queue(db47d, db47e, db50, db25, db41, db49e)
    categories = Counter(item["category"] for item in queue)
    requires_remote = sum(1 for item in queue if item["requires_remote_or_data"])
    seam_quality_items = sum(1 for item in queue if item["can_improve_seam_quality"])
    source_faithful_items = sum(1 for item in queue if item["can_be_source_faithful_repair"])
    db47_gaps = db47_asset_gaps(db47d, db47e)

    manifest: dict[str, Any] = {
        "db": "DB-51",
        "status": "accepted_acquisition_queue",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "egsr-target-source-pair-acquisition-queue-only",
        "purpose": "Translate DB50's zero executable repair target into a fixed evidence-acquisition queue for the next single brief.",
        "scope": {
            "cpu_local_only": True,
            "existing_artifacts_only": True,
            "new_panorama_repair": False,
            "renderer_or_dataset_run": False,
            "exact_asset_fetch": False,
            "a100_or_executor_used": False,
            "hf_or_vggt_used": False,
            "diffusion_or_generation": False,
            "source_replacement": False,
            "db49e_rerun": False,
            "permission_change": False,
            "red_promotion": False,
            "output_location": rel(OUT_DIR),
        },
        "inputs": {
            "decision_brief": rel(BRIEF),
            "db44_manifest": rel(DB44),
            "db47d_manifest": rel(DB47D),
            "db47e_manifest": rel(DB47E),
            "db50_manifest": rel(DB50),
            "db25_summary": rel(DB25),
            "db41_manifest": rel(DB41),
            "db49e_manifest": rel(DB49E),
        },
        "acquisition_queue": queue,
        "counts": {
            "queue_items": len(queue),
            "categories": dict(categories),
            "requires_remote_or_data": requires_remote,
            "can_improve_seam_quality": seam_quality_items,
            "can_be_source_faithful_repair": source_faithful_items,
            "db47_missing_exact_or_final_gaps": len(db47_gaps),
            "db50_phase0_executable_repair_targets": db50.get("counts", {}).get("phase0_executable_repair_targets"),
            "db50_lpam_executable_targets": db50.get("counts", {}).get("lpam_executable_targets"),
            "db41_lower_right_lidar_support": db41.get("summaries", {}).get("lower_right_roi", {}).get("lidar_support_frac"),
            "db25_lidar_support": db25.get("lidar_support_frac"),
        },
        "decision": {
            "db51_status": "accepted_target_acquisition_queue_only",
            "recommended_next_single_brief": "DB47f fixed-universe exact source-selection closure, if secure runtime/data preconditions are satisfied; otherwise keep DB50 paused and do not run operators.",
            "source_faithful_repair_executed": False,
            "new_candidate_image_created": False,
            "accepted_source_faithful_repair": False,
            "accepted_original_g_repair": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "ready_for_uncaveated_bosch_training_data": False,
            "claim_boundary": (
                "DB51 is an acquisition queue only. It does not repair seams, does not create source maps, "
                "does not change DB32/DB41/G boundaries, and does not authorize pasted-token remote execution."
            ),
        },
        "hard_checks": [
            {
                "id": "db51_brief_exists",
                "pass": "DB-51: EGSR target/source-pair evidence acquisition queue" in brief_text,
                "evidence": "DB51 brief exists before execution.",
            },
            {
                "id": "db50_zero_target_carried_forward",
                "pass": db50.get("counts", {}).get("phase0_executable_repair_targets") == 0
                and db50.get("counts", {}).get("lpam_executable_targets") == 0,
                "evidence": (
                    "DB50 phase0_executable_repair_targets="
                    f"{db50.get('counts', {}).get('phase0_executable_repair_targets')}, "
                    f"lpam_executable_targets={db50.get('counts', {}).get('lpam_executable_targets')}."
                ),
            },
            {
                "id": "db47_missing_exact_gaps_explicit",
                "pass": len(db47_gaps) == 8,
                "evidence": f"DB47 gaps counted={len(db47_gaps)} including seven missing exact holds plus a105 final gap.",
            },
            {
                "id": "db25_db41_not_promoted",
                "pass": db25.get("recommendation") == "abstain_unless_followup_finds_stronger_raw_evidence"
                and not db41.get("threshold_results", {}).get("lower_right_roi", {}).get("passes_db41_gate", False),
                "evidence": "DB25 recommendation remains abstain; DB41 lower-right gate remains false.",
            },
            {
                "id": "no_remote_or_model_action",
                "pass": True,
                "evidence": "DB51 creates only a local acquisition manifest/board.",
            },
            {
                "id": "db49e_not_reclassified_as_seam_quality",
                "pass": db49e.get("status") == "preflight_paused",
                "evidence": f"DB49e status={db49e.get('status')}; queue class is provenance, not seam-quality repair.",
            },
        ],
        "outputs": {"manifest": rel(MANIFEST), "board": rel(BOARD)},
    }

    manifest_text = json.dumps(manifest, indent=2)
    strict_hits = token_hits([BRIEF, DB44, DB47D, DB47E, DB50, DB25, DB41, DB49E])
    manifest["strict_secret_scan"] = {
        "checked_paths": [rel(p) for p in [BRIEF, DB44, DB47D, DB47E, DB50, DB25, DB41, DB49E]],
        "hits": strict_hits,
        "manifest_has_secret_pattern": any(pattern.search(manifest_text) for pattern in TOKEN_PATTERNS.values()),
    }
    manifest["hard_checks"].append(
        {
            "id": "strict_secret_scan_pass",
            "pass": not strict_hits and not manifest["strict_secret_scan"]["manifest_has_secret_pattern"],
            "evidence": "Strict token/endpoint scan found no secret-like strings in DB51 inputs or manifest text.",
        }
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2300, 1800), (14, 16, 20))
    draw = ImageDraw.Draw(board)

    draw_text(draw, (28, 24), "DB51 EGSR Target / Source-Pair Evidence Acquisition Queue", size=28)
    draw_text(
        draw,
        (28, 64),
        "CPU/local existing-artifact queue - no repair, no remote, no token use, no RED promotion",
        fill=(225, 220, 170),
        size=16,
    )

    counts = manifest["counts"]
    pills = [
        (f"queue items: {counts['queue_items']}", (70, 95, 140), 160),
        (f"DB47 gaps: {counts['db47_missing_exact_or_final_gaps']}", (145, 105, 55), 155),
        (f"DB50 repair targets: {counts['db50_phase0_executable_repair_targets']}", (145, 70, 70), 210),
        (f"LPAM targets: {counts['db50_lpam_executable_targets']}", (145, 70, 70), 170),
        ("DB41 abstain", (70, 125, 85), 150),
        ("no remote/token", (70, 125, 85), 160),
    ]
    x = 28
    for label, fill, width in pills:
        draw.rounded_rectangle((x, 102, x + width, 136), radius=5, fill=fill, outline=(185, 185, 185))
        draw_text(draw, (x + 10, 110), label, size=13)
        x += width + 12

    y = 162
    draw_text(draw, (28, y), "Ranked acquisition queue", size=22)
    y += 36
    for item in manifest["acquisition_queue"]:
        color = {
            "source-selection": (220, 215, 165),
            "operator-target": (180, 220, 190),
            "provenance": (170, 205, 230),
            "geometry-evidence": (200, 185, 230),
            "presentation-only": (230, 180, 210),
        }.get(item["category"], (220, 220, 220))
        y = draw_wrapped(
            draw,
            44,
            y,
            f"{item['rank']}. {item['queue_id']} [{item['category']}] -> {item['claim_if_successful']}",
            118,
            fill=color,
            size=14,
        )
        y = draw_wrapped(draw, 66, y, "why: " + item["why_ranked_here"], 116, fill=(225, 225, 215), size=12)
        missing = item.get("missing_or_blocked", [])
        if isinstance(missing, list):
            miss_text = "; ".join(str(m.get("candidate_id", m)) if isinstance(m, dict) else str(m) for m in missing[:4])
        else:
            miss_text = str(missing)
        y = draw_wrapped(draw, 66, y, "blocked/missing: " + miss_text, 116, fill=(235, 195, 160), size=12)
        y += 10
        if y > 1110:
            break

    y += 10
    draw_text(draw, (28, y), "Hard checks", size=22)
    y += 34
    for check in manifest["hard_checks"]:
        fill = (65, 140, 86) if check["pass"] else (170, 60, 60)
        draw.rounded_rectangle((44, y, 120, y + 25), radius=4, fill=fill)
        draw_text(draw, (57, y + 4), "PASS" if check["pass"] else "STOP", size=12)
        y = draw_wrapped(draw, 136, y + 3, f"{check['id']}: {check['evidence']}", 106, size=13)
        y += 7

    y += 10
    draw_text(draw, (28, y), "Decision", size=22)
    y += 34
    for line in [
        manifest["decision"]["db51_status"],
        manifest["decision"]["recommended_next_single_brief"],
        manifest["decision"]["claim_boundary"],
    ]:
        y = draw_wrapped(draw, 44, y, "- " + line, 116, fill=(235, 235, 215), size=13)

    x2 = 1190
    draw_text(draw, (x2, 162), "Evidence context", size=22)
    boxes = [
        (x2, 198, x2 + 430, 470),
        (x2 + 460, 198, x2 + 910, 470),
        (x2, 502, x2 + 430, 790),
        (x2 + 460, 502, x2 + 910, 790),
        (x2, 830, x2 + 910, 1140),
    ]
    for (label, path), box in zip(CONTEXT_IMAGES.items(), boxes):
        image_box(board, path, box, label)

    y2 = 1185
    draw_text(draw, (x2, y2), "DB47 exact gaps", size=22)
    y2 += 34
    source_item = manifest["acquisition_queue"][0]
    for gap in source_item["missing_or_blocked"]:
        y2 = draw_wrapped(
            draw,
            x2 + 18,
            y2,
            f"- {gap['candidate_id']} anchor={gap['anchor']} missing={gap['missing']}",
            78,
            fill=(235, 220, 170),
            size=13,
        )

    y2 += 10
    draw_text(draw, (x2, y2), "Negative target boundaries", size=22)
    y2 += 34
    op_item = manifest["acquisition_queue"][1]
    neg = op_item["negative_controls"]
    for line in [
        f"DB25: lidar={neg['db25_lidar_support_frac']}, near_ground={neg['db25_near_ground_frac']}, pair6-5 flow={neg['db25_key_pair_6_5_flow_reliable_frac']}",
        f"DB41 lower-right: {neg['db41_lower_right_lidar_gate']}",
        "These remain acquisition blockers, not repair permissions.",
    ]:
        y2 = draw_wrapped(draw, x2 + 18, y2, "- " + line, 82, fill=(235, 185, 165), size=13)

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
                "recommended_next_single_brief": manifest["decision"]["recommended_next_single_brief"],
                "counts": manifest["counts"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
