#!/usr/bin/env python
"""Build DB49a Bosch-facing data-contract inventory artifacts.

DB49a is a packaging/inventory pass over existing evidence only. It does not
create a new candidate image, repair image, generated mask, abstain mask, or
risk map.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract"
MANIFEST = OUT_DIR / "db49a_bosch_data_contract_inventory_manifest.json"
BOARD = OUT_DIR / "db49a_bosch_data_contract_inventory_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"

INPUTS = {
    "db32_diagnostics": ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_diagnostics.json",
    "db32_candidate": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db32_generated_sky_harmonize_v2"
    / "db32_generated_sky_harmonize_s40.png",
    "db34_manifest": ROOT / "deliverables" / "dit360_v2" / "db34_current_best_qa" / "db34_current_best_manifest.json",
    "db34_board": ROOT / "deliverables" / "dit360_v2" / "db34_current_best_qa" / "db34_current_best_review_board.jpg",
    "db38_manifest": ROOT / "deliverables" / "dit360_v2" / "db38_bosch_handoff" / "db38_bosch_handoff_manifest.json",
    "db38_board": ROOT / "deliverables" / "dit360_v2" / "db38_bosch_handoff" / "db38_bosch_handoff_board.jpg",
    "db41_manifest": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "db41_rightline_evidence_manifest.json",
    "db41_board": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "db41_rightline_evidence_board.jpg",
    "db42_manifest": ROOT / "deliverables" / "dit360_v2" / "db42_seam_decision_handoff" / "db42_seam_decision_handoff_manifest.json",
    "db42_board": ROOT / "deliverables" / "dit360_v2" / "db42_seam_decision_handoff" / "db42_seam_decision_handoff_board.jpg",
    "db42_report": ROOT / "deliverables" / "dit360_v2" / "db42_seam_decision_handoff" / "db42_seam_decision_handoff_report.md",
    "db43_manifest": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db43_source_faithfulness_gate"
    / "db43_source_faithfulness_gate_manifest.json",
    "db43_summary_board": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db43_source_faithfulness_gate"
    / "db43_reason_code_summary.jpg",
    "db45i_manifest": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db45_geometry_evidence_audit"
    / "db45i_vggt_calibrated_residual_manifest.json",
    "db45i_board": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db45_geometry_evidence_audit"
    / "db45i_vggt_calibrated_residual_board.jpg",
    "db47d_manifest": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db47_source_candidate_mining"
    / "db47d_exact_same_log_review_manifest.json",
    "db47d_board": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db47_source_candidate_mining"
    / "db47d_exact_same_log_review_board.jpg",
}

REQUIRED_CONTRACT_FIELDS = [
    "candidate_image",
    "source_id_map",
    "generated_mask",
    "unknown_or_abstain_mask",
    "risk_map",
    "eval_report",
    "caveat_table",
    "presentation_flag",
    "license_generation_caveat",
]


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


def resolve(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    p = Path(str(path))
    if p.is_absolute():
        return p
    return ROOT / p


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
    leading: int = 5,
) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + leading
    return y


def status_color(status: str) -> tuple[int, int, int]:
    if status.startswith("available"):
        return (45, 115, 70)
    if status.startswith("partial") or status.startswith("required"):
        return (130, 100, 40)
    if status.startswith("missing"):
        return (135, 55, 45)
    return (75, 80, 90)


def paste_image(
    board: Image.Image,
    draw: ImageDraw.ImageDraw,
    path: Path,
    box: tuple[int, int, int, int],
    label: str,
) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(24, 26, 30), outline=(80, 84, 92), width=1)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((x1 - x0 - 14, y1 - y0 - 44), Image.Resampling.LANCZOS)
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception as exc:
            draw_wrapped(draw, x0 + 10, y0 + 24, f"image load failed: {type(exc).__name__}", 46, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def artifact_exists(path: str | Path | None) -> bool:
    p = resolve(path)
    return bool(p and p.exists())


def current_handoff_path(db42: dict[str, Any], db34: dict[str, Any]) -> str:
    handoff = db42.get("accepted_current_handoff")
    if isinstance(handoff, dict):
        value = handoff.get("artifact") or handoff.get("path")
        if value:
            return str(value)
    if isinstance(handoff, str) and handoff:
        return handoff
    value = db34.get("current_best")
    if isinstance(value, str) and value:
        return value
    return rel(INPUTS["db32_candidate"]) or ""


def db43_claim_counts(db43: dict[str, Any]) -> dict[str, Any]:
    if isinstance(db43.get("claim_label_counts"), dict):
        return db43["claim_label_counts"]
    aggregate = db43.get("aggregate", {})
    if isinstance(aggregate, dict) and isinstance(aggregate.get("by_claim_label"), dict):
        return aggregate["by_claim_label"]
    return {}


def db43_reason_counts(db43: dict[str, Any]) -> dict[str, Any]:
    if isinstance(db43.get("reason_code_counts"), dict):
        return db43["reason_code_counts"]
    aggregate = db43.get("aggregate", {})
    if isinstance(aggregate, dict) and isinstance(aggregate.get("by_reason_code"), dict):
        return aggregate["by_reason_code"]
    return {}


def existing_inputs() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "path": rel(path),
            "exists": path.exists(),
        }
        for name, path in INPUTS.items()
    }


def contract_fields(
    db32: dict[str, Any],
    db34: dict[str, Any],
    db42: dict[str, Any],
    db43: dict[str, Any],
    db45i: dict[str, Any],
    db47d: dict[str, Any],
) -> list[dict[str, Any]]:
    mask_path = db34.get("mask") or db32.get("mask")
    current_candidate = current_handoff_path(db42, db34)
    label_defs = db43.get("claim_label_definitions", {})
    claim_counts = db43_claim_counts(db43)
    reason_counts = db43_reason_counts(db43)

    return [
        {
            "field": "candidate_image",
            "status": "available_current_caveated_handoff",
            "evidence": current_candidate,
            "gap_or_caveat": "DB32 s40 only; Bosch-facing handoff candidate with source-sidestep and generated-sky caveats.",
            "claim_label": "caveated-handoff",
        },
        {
            "field": "source_id_map",
            "status": "missing_blocking_for_dataset_contract",
            "evidence": [
                {
                    "type": "source_preservation_qa",
                    "path": rel(INPUTS["db34_manifest"]),
                    "db32_noncore_vs_source": db34.get("source_preservation", {}).get("db32_noncore_vs_source"),
                },
                {
                    "type": "source_sidestep_context",
                    "path": rel(INPUTS["db38_manifest"]),
                },
            ],
            "gap_or_caveat": "No standalone per-pixel source_id_map artifact exists for Bosch data-product handoff.",
            "claim_label": "missing",
        },
        {
            "field": "generated_mask",
            "status": "partial_available_as_existing_sky_mask",
            "evidence": {
                "mask": mask_path,
                "db32_mask_convention": db32.get("mask_convention", "white/255 preserves source; black/0 generated sky core"),
                "core_fraction": db32.get("core_fraction"),
                "core_overlay": db34.get("core_overlay"),
            },
            "gap_or_caveat": "Sky-core mask exists, but DB49a does not package a final generated_mask sidecar.",
            "claim_label": "generated",
        },
        {
            "field": "unknown_or_abstain_mask",
            "status": "missing_blocking_per_pixel_mask",
            "evidence": {
                "db43_abstain_count": claim_counts.get("abstain"),
                "db43_reason_codes": {
                    k: v
                    for k, v in reason_counts.items()
                    if k in {"unknown_or_abstain", "no_source_evidence", "zero_lidar_support", "right_line_boundary"}
                },
                "db41_manifest": rel(INPUTS["db41_manifest"]),
            },
            "gap_or_caveat": "DB41 lower-right/right-line remains no-evidence/abstain; no per-pixel unknown/abstain mask is packaged.",
            "claim_label": "abstain",
        },
        {
            "field": "risk_map",
            "status": "missing_blocking_per_pixel_risk_map",
            "evidence": {
                "db43_reason_code_summary": rel(INPUTS["db43_manifest"]),
                "db47d_review_pack": rel(INPUTS["db47d_manifest"]),
                "db45i_status": db45i.get("db45_status") or db45i.get("status"),
            },
            "gap_or_caveat": "Risk exists as reason-coded reviews, not as a per-pixel Bosch risk_map sidecar.",
            "claim_label": "partial",
        },
        {
            "field": "eval_report",
            "status": "available_existing_reports",
            "evidence": [
                rel(INPUTS["db42_report"]),
                rel(INPUTS["db43_manifest"]),
                rel(INPUTS["db47d_manifest"]),
            ],
            "gap_or_caveat": "Reports support claim boundaries; they do not make the candidate training-ready.",
            "claim_label": "report",
        },
        {
            "field": "caveat_table",
            "status": "available_existing_route_and_caveat_tables",
            "evidence": {
                "db42_route_table": rel(INPUTS["db42_manifest"]),
                "db34_accepted_caveats": db34.get("accepted_caveats", []),
                "db47d_claim_boundary": db47d.get("reviewed_rows", [{}])[0].get("claim_boundary") if db47d.get("reviewed_rows") else None,
            },
            "gap_or_caveat": "Caveats must be carried into any Bosch-facing packet; DB49a does not reduce them.",
            "claim_label": "caveated-handoff",
        },
        {
            "field": "presentation_flag",
            "status": "available_in_claim_label_schema",
            "evidence": {
                "label_definition": label_defs.get("presentation-only"),
                "source": rel(INPUTS["db43_manifest"]),
            },
            "gap_or_caveat": "Need explicit sidecar flag when packaged as dataset records.",
            "claim_label": "presentation-only",
        },
        {
            "field": "license_generation_caveat",
            "status": "required_manual_review",
            "evidence": {
                "generation_model": "DiT360 / sky-only generated core in existing artifacts",
                "license_source": "not packaged in DB49a artifact set",
            },
            "gap_or_caveat": "Manual license/model-use review is required before any Bosch dataset or training-data release.",
            "claim_label": "required",
        },
    ]


def build_checks(fields: list[dict[str, Any]], db38: dict[str, Any], db42: dict[str, Any], db47d: dict[str, Any]) -> list[dict[str, Any]]:
    field_names = {row["field"] for row in fields}
    missing = [row["field"] for row in fields if str(row["status"]).startswith("missing")]
    candidate_text = json.dumps(db42.get("accepted_current_handoff", db42), ensure_ascii=False).lower()
    db38_text = json.dumps(db38, ensure_ascii=False).lower()
    db47d_text = json.dumps(db47d, ensure_ascii=False).lower()
    brief_text = BRIEF.read_text(encoding="utf-8") if BRIEF.exists() else ""

    return [
        {
            "id": "brief_scope_is_db49a",
            "pass": "Phase0 / DB49a" in brief_text and "Existing-artifact data-contract inventory" in brief_text,
            "evidence": "DB49a sub-scope exists in decision_briefs.md before execution.",
        },
        {
            "id": "uses_existing_artifacts_only",
            "pass": all(row["exists"] for row in existing_inputs().values()),
            "evidence": "Inputs are existing DB32/DB34/DB38/DB41/DB42/DB43/DB45i/DB47d artifacts only.",
        },
        {
            "id": "candidate_is_db32_caveated",
            "pass": "db32" in candidate_text and "does not fix original g seam" in db38_text,
            "evidence": "DB32 is current handoff candidate, while DB38 explicitly rejects an original-G-repair claim.",
        },
        {
            "id": "contract_fields_reported",
            "pass": set(REQUIRED_CONTRACT_FIELDS).issubset(field_names),
            "evidence": f"reported={sorted(field_names)}.",
        },
        {
            "id": "missing_fields_not_hidden",
            "pass": {"source_id_map", "unknown_or_abstain_mask", "risk_map"}.issubset(set(missing)),
            "evidence": f"missing_blocking_fields={missing}.",
        },
        {
            "id": "db47d_not_final_preserved",
            "pass": "not final candidate" in db47d_text and "no_final_candidate_selection" in db47d_text,
            "evidence": "DB47d remains exact/same-log review pack only, not a final candidate selection.",
        },
        {
            "id": "db41_abstain_preserved",
            "pass": any(row["field"] == "unknown_or_abstain_mask" and "DB41" in row["gap_or_caveat"] for row in fields),
            "evidence": "DB41 lower-right/right-line no-evidence/abstain is a blocking contract gap.",
        },
        {
            "id": "no_generation_repair_or_mask_creation",
            "pass": True,
            "evidence": "DB49a creates only an inventory manifest and board; no panorama, repair, generated mask, abstain mask, or risk map is created.",
        },
        {
            "id": "license_caveat_present_or_required",
            "pass": any(row["field"] == "license_generation_caveat" and row["status"] == "required_manual_review" for row in fields),
            "evidence": "Generation/model license review remains required before data release.",
        },
    ]


def draw_contract_table(board: Image.Image, draw: ImageDraw.ImageDraw, fields: list[dict[str, Any]]) -> None:
    x, y = 36, 210
    row_h = 118
    draw_text(draw, (x, y - 36), "Required Contract Fields", fill=(255, 245, 190), size=22)
    headers = [("field", 250), ("status", 380), ("evidence / blocking gap", 570)]
    xx = x
    for title, width in headers:
        draw.rectangle((xx, y, xx + width, y + 42), fill=(38, 42, 48), outline=(90, 96, 105))
        draw_text(draw, (xx + 10, y + 12), title, fill=(235, 235, 235), size=15)
        xx += width
    y += 42
    for row in fields:
        xx = x
        status = str(row["status"])
        fill = status_color(status)
        draw.rectangle((xx, y, xx + 250, y + row_h), fill=(28, 31, 36), outline=(72, 76, 84))
        draw_text(draw, (xx + 10, y + 14), row["field"], fill=(235, 235, 235), size=15)
        draw_wrapped(draw, xx + 10, y + 44, row["claim_label"], 25, fill=(185, 195, 210), size=13)
        xx += 250
        draw.rectangle((xx, y, xx + 380, y + row_h), fill=fill, outline=(72, 76, 84))
        draw_wrapped(draw, xx + 10, y + 14, status, 40, fill=(255, 255, 255), size=14)
        xx += 380
        draw.rectangle((xx, y, xx + 570, y + row_h), fill=(28, 31, 36), outline=(72, 76, 84))
        draw_wrapped(draw, xx + 10, y + 12, row["gap_or_caveat"], 64, fill=(225, 225, 225), size=13)
        y += row_h


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2200, 1840), (18, 20, 24))
    draw = ImageDraw.Draw(board)

    draw_text(draw, (34, 28), "DB49a Bosch Data Contract Inventory", fill=(255, 255, 255), size=30)
    draw_text(
        draw,
        (36, 70),
        "Existing artifacts only. No new panorama, repair, generated mask, abstain mask, risk map, executor job, or dataset scan.",
        fill=(215, 220, 228),
        size=16,
    )

    badges = [
        ("candidate: DB32 s40 caveated", (115, 100, 35), 330),
        ("training-ready: false", (135, 55, 45), 240),
        ("DB47 final: false", (135, 55, 45), 210),
        ("source-faithful repair: false", (135, 55, 45), 310),
        ("RED promotions: 0", (45, 115, 70), 210),
    ]
    x = 36
    y = 112
    for label, fill, width in badges:
        draw.rounded_rectangle((x, y, x + width, y + 38), radius=6, fill=fill, outline=(170, 170, 170))
        draw_text(draw, (x + 12, y + 10), label, fill=(255, 255, 255), size=15)
        x += width + 14

    draw_contract_table(board, draw, manifest["contract_fields"])

    image_boxes = [
        (INPUTS["db32_candidate"], (1280, 205, 2160, 520), "candidate image: DB32 s40"),
        (INPUTS["db34_board"], (1280, 548, 1715, 840), "DB34 source preservation / caveats"),
        (INPUTS["db42_board"], (1725, 548, 2160, 840), "DB42 handoff synthesis"),
        (INPUTS["db43_summary_board"], (1280, 868, 1715, 1160), "DB43 claim/reason gate"),
        (INPUTS["db47d_board"], (1725, 868, 2160, 1160), "DB47d review pack only"),
        (INPUTS["db45i_board"], (1280, 1188, 1715, 1480), "DB45i geometry evidence pause"),
        (INPUTS["db41_board"], (1725, 1188, 2160, 1480), "DB41 right-line abstain"),
    ]
    for path, box, label in image_boxes:
        paste_image(board, draw, path, box, label)

    draw_text(draw, (1280, 1520), "Claim Boundary", fill=(255, 245, 190), size=22)
    yy = 1554
    for line in manifest["claim_boundary"]:
        yy = draw_wrapped(draw, 1280, yy, f"- {line}", 104, fill=(235, 235, 235), size=15)
        yy += 2

    board.save(BOARD, quality=92)


def build_manifest() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db32 = read_json(INPUTS["db32_diagnostics"])
    db34 = read_json(INPUTS["db34_manifest"])
    db38 = read_json(INPUTS["db38_manifest"])
    db42 = read_json(INPUTS["db42_manifest"])
    db43 = read_json(INPUTS["db43_manifest"])
    db45i = read_json(INPUTS["db45i_manifest"])
    db47d = read_json(INPUTS["db47d_manifest"])

    fields = contract_fields(db32, db34, db42, db43, db45i, db47d)
    checks = build_checks(fields, db38, db42, db47d)

    missing_or_required = [
        row["field"]
        for row in fields
        if str(row["status"]).startswith("missing") or str(row["status"]).startswith("required")
    ]
    partial = [row["field"] for row in fields if str(row["status"]).startswith("partial")]
    available = [row["field"] for row in fields if str(row["status"]).startswith("available")]

    manifest: dict[str, Any] = {
        "db": "DB-49a",
        "status": "data_contract_inventory_only",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "bosch-data-contract-inventory-only",
        "purpose": "Report Bosch-facing data-contract availability and blocking gaps from existing artifacts without creating new images or masks.",
        "scope": {
            "cpu_local_only": True,
            "existing_artifacts_only": True,
            "new_candidate_image": False,
            "new_panorama_repair": False,
            "new_generated_mask": False,
            "new_unknown_or_abstain_mask": False,
            "new_risk_map": False,
            "new_dataset_scan": False,
            "a100_used": False,
            "executor_used": False,
            "model_inference": False,
            "permission_change": False,
            "red_promotions": [],
            "output_location": rel(OUT_DIR),
        },
        "current_handoff_candidate": {
            "path": rel(INPUTS["db32_candidate"]),
            "claim_label": "caveated-handoff",
            "ready_for_uncaveated_bosch_training_data": False,
            "accepted_source_faithful_repair": False,
            "selected_final_candidate_from_db47": False,
            "source_faithful_ceiling": False,
            "description": "DB32 s40 is Bosch-facing presentation/handoff candidate with source-sidestep and generated-sky caveats.",
        },
        "contract_summary": {
            "required_fields": REQUIRED_CONTRACT_FIELDS,
            "available_fields": available,
            "partial_fields": partial,
            "missing_or_required_fields": missing_or_required,
            "blocking_fields_for_dataset_contract": [
                "source_id_map",
                "unknown_or_abstain_mask",
                "risk_map",
                "license_generation_caveat",
            ],
            "ready_for_bosch_dataset_contract": False,
        },
        "contract_fields": fields,
        "evidence_inputs": existing_inputs(),
        "db47_boundary": {
            "manifest": rel(INPUTS["db47d_manifest"]),
            "accepted_evidence_type": db47d.get("accepted_evidence_type"),
            "selected_final_candidate": False,
            "claim_boundary": "DB47d is same-log exact review pack only; exact rows are not final candidates.",
        },
        "db41_boundary": {
            "manifest": rel(INPUTS["db41_manifest"]),
            "status": "no-evidence/abstain boundary preserved",
            "claim_boundary": "Lower-right/right-line lacks enough source/depth evidence for source-faithful repair under current evidence.",
        },
        "db45_boundary": {
            "manifest": rel(INPUTS["db45i_manifest"]),
            "status": db45i.get("db45_status") or db45i.get("status"),
            "claim_boundary": "VGGT/geometry evidence is not a current accepted source-faithful repair input for DB49a.",
        },
        "claim_boundary": [
            "DB49a is packaging/inventory only, not a new experiment result or repair.",
            "DB32 s40 remains caveated handoff candidate; not fully source-faithful and not an original-G/A1/BEST repair.",
            "DB47d exact-review rows are review evidence only, not final source candidates.",
            "DB41 lower-right/right-line remains no-evidence/abstain under current evidence.",
            "Per-pixel source_id_map, unknown_or_abstain_mask, and risk_map are blocking gaps for a Bosch dataset contract.",
            "Generated-sky/model/license caveats remain required before any training-data release.",
        ],
        "checks": checks,
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
    }
    return manifest


def main() -> None:
    manifest = build_manifest()
    build_board(manifest)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"manifest": rel(MANIFEST), "board": rel(BOARD), "checks_pass": all(c["pass"] for c in manifest["checks"])}, indent=2))


if __name__ == "__main__":
    main()
