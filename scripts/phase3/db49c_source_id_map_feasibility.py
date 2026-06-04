#!/usr/bin/env python
"""Inventory source_id_map feasibility for the DB32 handoff candidate.

DB49c is an evidence inventory only. It does not infer source ownership from
RGB, ROI overlays, or camera-label counts, and it does not create a
source_id_map. The goal is to preserve the blocking gap unless a true complete
per-pixel owner artifact already exists.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract"
BRIEF = ROOT / "agent" / "decision_briefs.md"

MANIFEST = OUT_DIR / "db49c_source_id_map_feasibility_manifest.json"
BOARD = OUT_DIR / "db49c_source_id_map_feasibility_board.jpg"

INPUTS = {
    "db49a_manifest": OUT_DIR / "db49a_bosch_data_contract_inventory_manifest.json",
    "db49a_board": OUT_DIR / "db49a_bosch_data_contract_inventory_board.jpg",
    "db49b_manifest": OUT_DIR / "db49b_sidecar_starter_pack_manifest.json",
    "db49b_board": OUT_DIR / "db49b_sidecar_starter_pack_board.jpg",
    "db49b_overlay": OUT_DIR / "db49b_sidecar_overlay_on_db32.jpg",
    "db32_candidate": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db32_generated_sky_harmonize_v2"
    / "db32_generated_sky_harmonize_s40.png",
    "db32_diagnostics": ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_diagnostics.json",
    "db34_manifest": ROOT / "deliverables" / "dit360_v2" / "db34_current_best_qa" / "db34_current_best_manifest.json",
    "db34_board": ROOT / "deliverables" / "dit360_v2" / "db34_current_best_qa" / "db34_current_best_review_board.jpg",
    "db28_summary": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db28_clean_subset_refine"
    / "db28_strict_clean_source_scan_summary.json",
    "db28_montage": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db28_clean_subset_refine"
    / "db28_strict_clean_source_scan_montage.jpg",
    "db28_source_base": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db28_clean_subset_refine"
    / "SR_bmw_db28_a200_final_1024x2048.png",
    "db29_sky_completion": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db29_sky_clean_a200"
    / "SR_bmw_db28_a200_sky_t50_s0_corecompose.png",
    "db29_sky_mask": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db29_sky_clean_a200"
    / "SR_bmw_db28_a200_opmask_sky.png",
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
    "db43_manifest": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db43_source_faithfulness_gate"
    / "db43_source_faithfulness_gate_manifest.json",
    "seamroute_script": ROOT / "scripts" / "phase3" / "_seamroute.py",
    "db34_script": ROOT / "scripts" / "phase3" / "db34_current_best_qa.py",
}

SEARCH_DIRS = [
    ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine",
    ROOT / "deliverables" / "dit360_v2" / "db29_sky_clean_a200",
    ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2",
    ROOT / "deliverables" / "dit360_v2" / "db34_current_best_qa",
    ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate",
    ROOT / "deliverables" / "dit360_v2" / "db43_source_faithfulness_gate",
    ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract",
]

COMPLETE_MAP_NAME_PATTERNS = [
    "source_id_map",
    "sourceidmap",
    "source_owner_map",
    "source-ownership-map",
    "ownership_map",
    "owner_map",
]

REPORT_NAME_MARKERS = [
    "feasibility",
    "inventory",
    "manifest",
    "board",
    "report",
    "summary",
]

MAP_FILE_SUFFIXES = {".png", ".jpg", ".jpeg", ".npy", ".npz", ".pkl", ".pickle", ".json"}

RELATED_NAME_PATTERNS = [
    "source",
    "owner",
    "ownership",
    "camid",
    "camera_label",
    "label",
    "source_id",
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
            img.thumbnail((x1 - x0 - 18, y1 - y0 - 48), Image.Resampling.LANCZOS)
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 9
            board.paste(img, (px, py))
        except Exception as exc:
            draw_wrapped(draw, x0 + 12, y0 + 28, f"image load failed: {type(exc).__name__}", 44, fill=(240, 140, 140))
    else:
        draw_text(draw, (x0 + 12, y0 + 28), "missing", fill=(245, 120, 120), size=15)
    draw_text(draw, (x0 + 12, y1 - 32), label, fill=(222, 230, 242), size=14)


def status_color(status: str) -> tuple[int, int, int]:
    if "complete_map_candidate" in status:
        return (130, 100, 40)
    if "insufficient" in status or "missing" in status or "not_fabricated" in status:
        return (135, 55, 45)
    if "future" in status or "partial" in status:
        return (130, 100, 40)
    if "available" in status:
        return (45, 115, 70)
    return (55, 62, 72)


def extract_line_snippets(path: Path, needles: list[str], context: int = 1) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hits: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        if any(needle in line for needle in needles):
            lo = max(1, idx - context)
            hi = min(len(lines), idx + context)
            hits.append(
                {
                    "line": idx,
                    "snippet": "\n".join(f"{line_no}: {lines[line_no - 1]}" for line_no in range(lo, hi + 1)),
                }
            )
    return hits


def scan_related_artifacts() -> dict[str, Any]:
    related: list[dict[str, Any]] = []
    complete_candidates: list[dict[str, Any]] = []
    for directory in SEARCH_DIRS:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            name = path.name.lower()
            if not any(pattern in name for pattern in RELATED_NAME_PATTERNS):
                continue
            record = {
                "path": rel(path),
                "name": path.name,
                "size_bytes": path.stat().st_size,
            }
            looks_like_map = (
                any(pattern in name for pattern in COMPLETE_MAP_NAME_PATTERNS)
                and path.suffix.lower() in MAP_FILE_SUFFIXES
                and not any(marker in name for marker in REPORT_NAME_MARKERS)
            )
            if looks_like_map:
                complete_candidates.append(record)
            else:
                related.append(record)
    return {
        "complete_source_id_map_candidates": complete_candidates[:30],
        "related_camera_or_source_artifacts_sample": related[:80],
        "related_camera_or_source_artifacts_count": len(related),
        "complete_source_id_map_candidate_count": len(complete_candidates),
    }


def find_db28_anchor(summary: dict[str, Any], anchor: int) -> dict[str, Any]:
    for item in summary.get("summaries", []):
        if item.get("anchor") == anchor:
            return item
    return {}


def build_evidence_inventory(
    db28: dict[str, Any],
    db34: dict[str, Any],
    db41: dict[str, Any],
    db49b: dict[str, Any],
    seamroute_snippets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    anchor_200 = find_db28_anchor(db28, 200)
    right_roi = db41.get("summaries", {}).get("right_roi", {})
    lower_right = db41.get("summaries", {}).get("lower_right_roi", {})
    return [
        {
            "id": "db28_anchor_200_roi_camera_label_counts",
            "path": rel(INPUTS["db28_summary"]),
            "status": "partial_roi_counts_only_insufficient",
            "evidence": {
                "anchor": 200,
                "roi": anchor_200.get("roi"),
                "camera_label_counts": anchor_200.get("camera_label_counts"),
                "camera_label_fracs": anchor_200.get("camera_label_fracs"),
                "lidar_support_frac": anchor_200.get("lidar_support_frac"),
                "valid_frac": anchor_200.get("valid_frac"),
            },
            "why_insufficient": "ROI aggregate camera-label counts are not a full-panorama per-pixel owner map and do not encode DB32 post-sky-completion lineage.",
        },
        {
            "id": "db41_right_roi_camera_label_counts",
            "path": rel(INPUTS["db41_manifest"]),
            "status": "partial_roi_counts_only_insufficient_abstain_boundary",
            "evidence": {
                "roi": right_roi.get("roi"),
                "camera_label_counts": right_roi.get("camera_label_counts"),
                "lidar_support_frac": right_roi.get("lidar_support_frac"),
                "recommendation": right_roi.get("recommendation"),
            },
            "why_insufficient": "DB41 labels are evidence-boundary diagnostics for a rejected/no-evidence region, not source ownership for the candidate.",
        },
        {
            "id": "db41_lower_right_roi_camera_label_counts",
            "path": rel(INPUTS["db41_manifest"]),
            "status": "partial_roi_counts_only_insufficient_zero_lidar_abstain",
            "evidence": {
                "roi": lower_right.get("roi"),
                "camera_label_counts": lower_right.get("camera_label_counts"),
                "lidar_support_frac": lower_right.get("lidar_support_frac"),
                "recommendation": lower_right.get("recommendation"),
            },
            "why_insufficient": "Lower-right remains zero-LiDAR abstain. Camera labels here cannot be promoted into repair permission or full source_id_map truth.",
        },
        {
            "id": "db34_source_preservation_manifest",
            "path": rel(INPUTS["db34_manifest"]),
            "status": "source_preservation_available_not_owner_map",
            "evidence": db34.get("source_preservation", {}),
            "why_insufficient": "Byte-exact noncore preservation proves what pixels changed versus source masks; it does not name the per-pixel camera/source owner.",
        },
        {
            "id": "db49b_partial_sidecars",
            "path": rel(INPUTS["db49b_manifest"]),
            "status": "partial_sidecars_available_not_owner_map",
            "evidence": {
                "generated_mask": db49b.get("sidecars", {}).get("generated_mask", {}).get("status"),
                "unknown_or_abstain_mask": db49b.get("sidecars", {}).get("unknown_or_abstain_mask", {}).get("status"),
                "risk_map": db49b.get("sidecars", {}).get("risk_map", {}).get("status"),
                "source_id_map_status": db49b.get("source_id_map_status"),
            },
            "why_insufficient": "Generated, unknown, and risk sidecars are not a source-owner sidecar.",
        },
        {
            "id": "seamroute_internal_label_path",
            "path": rel(INPUTS["seamroute_script"]),
            "status": "future_reproducible_path_candidate_not_existing_artifact",
            "evidence": {
                "label_snippet_count": len(seamroute_snippets),
                "snippets": seamroute_snippets[:6],
            },
            "why_insufficient": "_seamroute.py computes a routed label internally, but the inspected path does not save a source_id_map artifact for the exact DB32 lineage.",
        },
    ]


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2200, 1700), (18, 20, 24))
    draw = ImageDraw.Draw(board)

    draw_text(draw, (34, 28), "DB49c Source ID Map Feasibility", fill=(255, 255, 255), size=31)
    draw_text(
        draw,
        (36, 70),
        "Inventory only: source_id_map remains missing/blocking unless a true complete owner artifact is found.",
        fill=(215, 220, 228),
        size=16,
    )

    badges = [
        ("source_id_map: missing", (135, 55, 45), 250),
        ("created: false", (135, 55, 45), 175),
        ("training-ready: false", (135, 55, 45), 235),
        ("repair: false", (45, 115, 70), 160),
        ("model/A100/network: false", (45, 115, 70), 255),
        ("DB41: abstain boundary", (130, 100, 40), 260),
    ]
    x = 36
    y = 112
    for label, fill, width in badges:
        draw.rounded_rectangle((x, y, x + width, y + 38), radius=6, fill=fill, outline=(165, 165, 165))
        draw_text(draw, (x + 11, y + 10), label, fill=(255, 255, 255), size=15)
        x += width + 12

    image_boxes = [
        (INPUTS["db32_candidate"], (36, 180, 720, 520), "DB32 s40 candidate - unchanged"),
        (INPUTS["db28_source_base"], (748, 180, 1428, 520), "DB28 a200 source base - lineage input"),
        (INPUTS["db49b_overlay"], (1456, 180, 2160, 520), "DB49b sidecar overlay - not ownership"),
        (INPUTS["db28_montage"], (36, 548, 720, 908), "DB28 ROI/camera-label scan"),
        (INPUTS["db41_board"], (748, 548, 1428, 908), "DB41 right/lower-right abstain evidence"),
        (INPUTS["db49b_board"], (1456, 548, 2160, 908), "DB49b partial sidecars / source_id_map missing"),
    ]
    for path, box, label in image_boxes:
        paste_image(board, draw, path, box, label)

    draw_text(draw, (36, 955), "Evidence Inventory", fill=(255, 245, 190), size=23)
    yy = 995
    col_w = [330, 360, 1120]
    headers = ["evidence", "status", "why insufficient / boundary"]
    x0 = 36
    for header, width in zip(headers, col_w):
        draw.rectangle((x0, yy, x0 + width, yy + 42), fill=(38, 42, 48), outline=(82, 88, 96))
        draw_text(draw, (x0 + 10, yy + 12), header, size=15)
        x0 += width
    yy += 42
    for row in manifest["evidence_inventory"]:
        h = 90
        values = [row["id"], row["status"], row["why_insufficient"]]
        x0 = 36
        for idx, (value, width) in enumerate(zip(values, col_w)):
            fill = status_color(row["status"]) if idx == 1 else (28, 31, 36)
            draw.rectangle((x0, yy, x0 + width, yy + h), fill=fill, outline=(74, 80, 88))
            draw_wrapped(draw, x0 + 10, yy + 12, value, max(18, width // 12), size=13)
            x0 += width
        yy += h

    draw_text(draw, (36, 1590), "Decision", fill=(255, 245, 190), size=22)
    draw_wrapped(
        draw,
        150,
        1592,
        "No complete per-pixel source_id_map was recovered. DB49c does not create one; future work must rerun or instrument exact source-owner generation under a new bounded brief.",
        150,
        fill=(235, 235, 235),
        size=15,
    )

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=94)


def build_manifest() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db28 = read_json(INPUTS["db28_summary"])
    db34 = read_json(INPUTS["db34_manifest"])
    db41 = read_json(INPUTS["db41_manifest"])
    db49a = read_json(INPUTS["db49a_manifest"])
    db49b = read_json(INPUTS["db49b_manifest"])

    candidate_sha_before = sha256_file(INPUTS["db32_candidate"])
    artifact_scan = scan_related_artifacts()
    seamroute_snippets = extract_line_snippets(
        INPUTS["seamroute_script"],
        ["label = np.stack", "label[m] =", "labcol = PAL", "np.take_along_axis(g_stack, label"],
        context=1,
    )
    evidence_inventory = build_evidence_inventory(db28, db34, db41, db49b, seamroute_snippets)
    name_matched_candidates = artifact_scan["complete_source_id_map_candidate_count"] > 0
    complete_found = False
    source_id_status = (
        "name_matched_map_candidate_requires_manual_validation_not_created"
        if name_matched_candidates
        else "missing_blocking_not_fabricated"
    )
    candidate_sha_after = sha256_file(INPUTS["db32_candidate"])

    manifest: dict[str, Any] = {
        "db": "DB-49c",
        "status": "source_id_map_feasibility_inventory_only",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "source-id-map-feasibility-inventory-only",
        "purpose": "Inventory existing source-ownership evidence for DB32 and preserve the source_id_map blocker without fabricating ownership.",
        "scope": {
            "cpu_local_only": True,
            "existing_artifacts_only": True,
            "candidate_pixels_modified": False,
            "source_id_map_created": False,
            "new_panorama_repair": False,
            "new_generated_pixels": False,
            "new_image_generation": False,
            "new_dataset_scan": False,
            "renderer_ran": False,
            "a100_used": False,
            "executor_used": False,
            "network_used": False,
            "model_inference": False,
            "source_replacement": False,
            "permission_change": False,
            "red_promotions": [],
            "output_location": "deliverables/dit360_v2/db49_bosch_data_contract",
        },
        "current_handoff_candidate": {
            "path": rel(INPUTS["db32_candidate"]),
            "claim_label": "caveated-handoff",
            "ready_for_uncaveated_bosch_training_data": False,
            "accepted_source_faithful_repair": False,
            "source_faithful_ceiling": False,
            "description": "DB32 s40 remains a Bosch-facing presentation/handoff candidate with source-sidestep and generated-sky caveats.",
        },
        "source_id_map": {
            "created": False,
            "status": source_id_status,
            "complete_source_id_map_found": complete_found,
            "name_matched_source_id_map_candidates": artifact_scan["complete_source_id_map_candidates"],
            "name_matched_source_id_map_candidate_count": artifact_scan["complete_source_id_map_candidate_count"],
            "not_created_reason": [
                "No existing complete per-pixel source-owner map was found for the exact DB32 candidate lineage."
                if not name_matched_candidates
                else "Name-matched candidates require content validation before use; DB49c did not create or promote a source_id_map.",
                "DB28 and DB41 camera-label evidence is ROI-level diagnostic evidence, not full-panorama source ownership.",
                "DB34 source preservation proves unchanged pixels versus a source/mask, not per-pixel camera ownership.",
                "_seamroute.py computes a routed label internally, but the inspected path does not save a source_id_map artifact for DB32.",
                "RGB similarity, overlay color, and ROI rectangles are explicitly inadmissible for ownership inference.",
            ],
            "future_admissible_route": "Under a new bounded brief, instrument or rerun the exact DB28/a200 -> DB29 -> DB32 lineage to save the routed label/source owner map alongside the candidate, then validate it against source-preservation and sidecar masks.",
        },
        "candidate_integrity": {
            "sha256_before": candidate_sha_before,
            "sha256_after": candidate_sha_after,
            "unchanged": candidate_sha_before == candidate_sha_after,
        },
        "candidate_lineage": [
            {
                "stage": "DB28_source_base",
                "path": rel(INPUTS["db28_source_base"]),
                "status": "source_sidestep_base",
            },
            {
                "stage": "DB29_sky_completion",
                "path": rel(INPUTS["db29_sky_completion"]),
                "mask": rel(INPUTS["db29_sky_mask"]),
                "status": "generated_sky_core_with_source_preservation_caveat",
            },
            {
                "stage": "DB32_s40_harmonization",
                "path": rel(INPUTS["db32_candidate"]),
                "status": "current_caveated_bosch_handoff_candidate",
            },
            {
                "stage": "DB34_QA",
                "path": rel(INPUTS["db34_manifest"]),
                "status": "noncore_byte_exact_source_preservation_not_owner_map",
                "source_preservation": db34.get("source_preservation", {}),
            },
            {
                "stage": "DB49b_sidecars",
                "path": rel(INPUTS["db49b_manifest"]),
                "status": "partial_sidecars_source_id_map_missing",
            },
        ],
        "evidence_inventory": evidence_inventory,
        "artifact_scan": artifact_scan,
        "preconditions": {
            "db49a_evidence_type": db49a.get("accepted_evidence_type"),
            "db49b_evidence_type": db49b.get("accepted_evidence_type"),
            "db49b_source_id_map_status": db49b.get("source_id_map_status"),
        },
        "claim_boundary": [
            "DB49c is inventory-only and does not create a source_id_map.",
            "DB32 s40 remains caveated handoff, not fully source-faithful and not original-G/A1/BEST repair.",
            "G_bmw_pano remains a classic BMW failure / diagnostic reference, not the default repair base.",
            "DB41 lower-right/right-line remains no-evidence/abstain.",
            "DB28/DB41 camera-label counts are ROI diagnostics, not source ownership truth.",
            "DB34 source preservation is not a source-owner map.",
            "No repair, generation, renderer, A100, executor, network, permission change, or RED promotion occurred.",
            "ready_for_uncaveated_bosch_training_data=false.",
        ],
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
            "source_id_map_png": None,
        },
    }

    manifest_text = json.dumps(manifest, ensure_ascii=False)
    brief_text = BRIEF.read_text(encoding="utf-8") if BRIEF.exists() else ""
    checks = [
        {
            "id": "brief_scope_is_db49c",
            "pass": "Phase2 / DB49c" in brief_text and "source_id_map" in brief_text and "feasibility" in brief_text,
            "evidence": "DB49c sub-scope exists in decision_briefs.md before execution.",
        },
        {
            "id": "uses_existing_artifacts_only",
            "pass": all(path.exists() for path in INPUTS.values() if path.suffix.lower() not in {".py"})
            and INPUTS["seamroute_script"].exists(),
            "evidence": "Inputs are existing DB28/DB29/DB32/DB34/DB41/DB43/DB49 artifacts and local scripts.",
        },
        {
            "id": "candidate_not_modified",
            "pass": candidate_sha_before == candidate_sha_after,
            "evidence": "Candidate sha256 before and after DB49c inventory are identical.",
        },
        {
            "id": "source_id_map_not_created",
            "pass": manifest["source_id_map"]["created"] is False and manifest["outputs"]["source_id_map_png"] is None,
            "evidence": "DB49c created no source_id_map artifact.",
        },
        {
            "id": "no_complete_source_id_map_found_or_not_promoted",
            "pass": (
                (not complete_found and source_id_status == "missing_blocking_not_fabricated")
                or (not complete_found and name_matched_candidates and source_id_status == "name_matched_map_candidate_requires_manual_validation_not_created")
            ),
            "evidence": f"complete_source_id_map_candidate_count={artifact_scan['complete_source_id_map_candidate_count']}.",
        },
        {
            "id": "db28_roi_counts_not_promoted",
            "pass": any(row["id"].startswith("db28_anchor_200") and "insufficient" in row["status"] for row in evidence_inventory),
            "evidence": "DB28 camera-label counts stay ROI-only and insufficient.",
        },
        {
            "id": "db41_roi_labels_not_promoted",
            "pass": all(
                row["status"].endswith("abstain_boundary") or row["status"].endswith("zero_lidar_abstain")
                for row in evidence_inventory
                if row["id"].startswith("db41_")
            ),
            "evidence": "DB41 ROI camera labels stay abstain-boundary diagnostics.",
        },
        {
            "id": "seamroute_label_not_claimed_without_artifact",
            "pass": any("future_reproducible_path_candidate" in row["status"] for row in evidence_inventory),
            "evidence": "_seamroute internal label is treated as a future path, not an existing map.",
        },
        {
            "id": "training_ready_false",
            "pass": manifest["current_handoff_candidate"]["ready_for_uncaveated_bosch_training_data"] is False,
            "evidence": "DB49c keeps Bosch training-data readiness false.",
        },
        {
            "id": "no_repair_generation_model_executor_or_network",
            "pass": not any(
                manifest["scope"][key]
                for key in (
                    "new_panorama_repair",
                    "new_image_generation",
                    "renderer_ran",
                    "a100_used",
                    "executor_used",
                    "network_used",
                    "model_inference",
                    "source_replacement",
                    "permission_change",
                )
            )
            and manifest["scope"]["red_promotions"] == [],
            "evidence": "Scope flags show no repair, generation, renderer, A100, executor, network, model, source replacement, or permission change.",
        },
        {
            "id": "no_secret_like_strings",
            "pass": re.search(r"hf_[A-Za-z0-9]+|Bearer\s+|trycloudflare|api\.ngrok|colab", manifest_text, re.IGNORECASE)
            is None,
            "evidence": "Manifest text contains no HF token, bearer marker, tunnel marker, or Colab endpoint marker.",
        },
    ]
    manifest["checks"] = checks
    build_board(manifest)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main() -> None:
    manifest = build_manifest()
    print(
        json.dumps(
            {
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
                "checks_pass": all(c["pass"] for c in manifest["checks"]),
                "source_id_map_status": manifest["source_id_map"]["status"],
                "source_id_map_created": manifest["source_id_map"]["created"],
                "complete_source_id_map_found": manifest["source_id_map"]["complete_source_id_map_found"],
                "training_ready": manifest["current_handoff_candidate"]["ready_for_uncaveated_bosch_training_data"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
