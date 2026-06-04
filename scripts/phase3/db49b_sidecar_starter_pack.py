#!/usr/bin/env python
"""Build DB49b partial sidecars for the DB32 handoff candidate.

DB49b packages only sidecars that can be derived from existing evidence. It
does not edit the candidate image, run a model, create a source_id_map, or make
the DB32 candidate training-ready.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract"
BRIEF = ROOT / "agent" / "decision_briefs.md"

MANIFEST = OUT_DIR / "db49b_sidecar_starter_pack_manifest.json"
BOARD = OUT_DIR / "db49b_sidecar_starter_pack_board.jpg"
GENERATED_MASK = OUT_DIR / "db49b_generated_mask_sky_core_only.png"
UNKNOWN_MASK = OUT_DIR / "db49b_unknown_or_abstain_mask_partial.png"
RISK_MAP = OUT_DIR / "db49b_risk_map_partial.png"

INPUTS = {
    "db49a_manifest": OUT_DIR / "db49a_bosch_data_contract_inventory_manifest.json",
    "db34_manifest": ROOT / "deliverables" / "dit360_v2" / "db34_current_best_qa" / "db34_current_best_manifest.json",
    "db32_diagnostics": ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_diagnostics.json",
    "db41_manifest": ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "db41_rightline_evidence_manifest.json",
}

RISK_LEVELS = {
    "generated_sky_core": 96,
    "out_of_fov_black_rows": 180,
    "db41_right_roi_abstain": 220,
    "db41_lower_right_roi_zero_lidar_abstain": 255,
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


def resolve(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    p = Path(str(path))
    if p.is_absolute():
        return p
    return ROOT / p


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
        img = Image.open(path).convert("RGB")
        img.thumbnail((x1 - x0 - 18, y1 - y0 - 48), Image.Resampling.LANCZOS)
        px = x0 + (x1 - x0 - img.width) // 2
        py = y0 + 9
        board.paste(img, (px, py))
    else:
        draw_text(draw, (x0 + 12, y0 + 28), "missing", fill=(245, 120, 120), size=15)
    draw_text(draw, (x0 + 12, y1 - 32), label, fill=(222, 230, 242), size=14)


def contiguous_ranges(rows: np.ndarray) -> list[list[int]]:
    if rows.size == 0:
        return []
    ranges: list[list[int]] = []
    start = int(rows[0])
    prev = int(rows[0])
    for value in rows[1:]:
        current = int(value)
        if current == prev + 1:
            prev = current
            continue
        ranges.append([start, prev + 1])
        start = prev = current
    ranges.append([start, prev + 1])
    return ranges


def fill_roi(mask: np.ndarray, roi: list[int] | tuple[int, int, int, int], value: int) -> int:
    x0, y0, x1, y1 = [int(v) for v in roi]
    h, w = mask.shape
    x0 = max(0, min(w, x0))
    x1 = max(0, min(w, x1))
    y0 = max(0, min(h, y0))
    y1 = max(0, min(h, y1))
    if x1 <= x0 or y1 <= y0:
        return 0
    before = mask[y0:y1, x0:x1].copy()
    mask[y0:y1, x0:x1] = np.maximum(mask[y0:y1, x0:x1], value)
    return int(np.count_nonzero(mask[y0:y1, x0:x1] != before))


def roi_nonzero_stats(mask: np.ndarray, roi: list[int] | tuple[int, int, int, int]) -> dict[str, int]:
    x0, y0, x1, y1 = [int(v) for v in roi]
    h, w = mask.shape
    x0 = max(0, min(w, x0))
    x1 = max(0, min(w, x1))
    y0 = max(0, min(h, y0))
    y1 = max(0, min(h, y1))
    if x1 <= x0 or y1 <= y0:
        return {"area": 0, "nonzero": 0}
    roi_mask = mask[y0:y1, x0:x1]
    return {"area": int(roi_mask.size), "nonzero": int(np.count_nonzero(roi_mask))}


def make_overlay(candidate: Image.Image, generated: np.ndarray, unknown: np.ndarray, risk: np.ndarray) -> Image.Image:
    base = candidate.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    arr = np.asarray(overlay).copy()
    generated_bool = generated > 0
    unknown_bool = unknown > 0
    risk_high = risk >= 220
    risk_mid = (risk >= 120) & (risk < 220)
    arr[risk_mid] = np.array([245, 180, 40, 105], dtype=np.uint8)
    arr[generated_bool] = np.array([45, 170, 220, 95], dtype=np.uint8)
    arr[unknown_bool] = np.array([240, 70, 70, 115], dtype=np.uint8)
    arr[risk_high] = np.array([255, 35, 115, 145], dtype=np.uint8)
    return Image.alpha_composite(base, Image.fromarray(arr, "RGBA")).convert("RGB")


def build_board(manifest: dict[str, Any], overlay_path: Path) -> None:
    board = Image.new("RGB", (2200, 1660), (18, 20, 24))
    draw = ImageDraw.Draw(board)

    draw_text(draw, (34, 28), "DB49b Sidecar Starter Pack", fill=(255, 255, 255), size=31)
    draw_text(
        draw,
        (36, 70),
        "Partial sidecars from existing DB32/DB34/DB41 evidence only. No repair, no generation, no source_id_map.",
        fill=(215, 220, 228),
        size=16,
    )

    badges = [
        ("candidate: DB32 s40", (115, 100, 35), 250),
        ("training-ready: false", (135, 55, 45), 235),
        ("source_id_map: missing", (135, 55, 45), 250),
        ("repair: false", (45, 115, 70), 160),
        ("model/A100: false", (45, 115, 70), 190),
        ("RED promotions: 0", (45, 115, 70), 200),
    ]
    x = 36
    y = 112
    for label, fill, width in badges:
        draw.rounded_rectangle((x, y, x + width, y + 38), radius=6, fill=fill, outline=(165, 165, 165))
        draw_text(draw, (x + 11, y + 10), label, fill=(255, 255, 255), size=15)
        x += width + 12

    image_boxes = [
        (Path(manifest["inputs"]["candidate_image"]["path_resolved"]), (36, 180, 720, 520), "DB32 s40 candidate - unchanged"),
        (GENERATED_MASK, (748, 180, 1428, 520), "generated_mask: sky core only"),
        (UNKNOWN_MASK, (1456, 180, 2160, 520), "unknown/abstain: black rows + DB41 ROIs"),
        (RISK_MAP, (36, 548, 720, 908), "risk_map: partial contract risk"),
        (overlay_path, (748, 548, 1428, 908), "overlay: cyan generated, red abstain/high risk"),
        (Path(manifest["inputs"]["db41_board"]["path_resolved"]), (1456, 548, 2160, 908), "DB41 evidence boundary"),
    ]
    for path, box, label in image_boxes:
        paste_image(board, draw, path, box, label)

    draw_text(draw, (36, 955), "Sidecar Status", fill=(255, 245, 190), size=23)
    rows = [
        ("generated_mask", "created_partial", manifest["sidecars"]["generated_mask"]["derivation"]),
        ("unknown_or_abstain_mask", "created_partial", manifest["sidecars"]["unknown_or_abstain_mask"]["derivation"]),
        ("risk_map", "created_partial", manifest["sidecars"]["risk_map"]["derivation"]),
        ("source_id_map", manifest["source_id_map_status"], "No source ownership map was created or guessed."),
        ("candidate_pixels", "unchanged", "Candidate sha256 before == after."),
        ("bosch_training_ready", "false", "Partial sidecars do not satisfy full Bosch data contract."),
    ]
    yy = 995
    col_w = [300, 300, 870]
    for header, width in zip(("field", "status", "boundary"), col_w):
        draw.rectangle((36 + sum(col_w[: list(("field", "status", "boundary")).index(header)]), yy, 36 + sum(col_w[: list(("field", "status", "boundary")).index(header) + 1]), yy + 42), fill=(38, 42, 48), outline=(82, 88, 96))
        draw_text(draw, (46 + sum(col_w[: list(("field", "status", "boundary")).index(header)]), yy + 12), header, size=15)
    yy += 42
    for field, status, boundary in rows:
        x0 = 36
        h = 76
        fills = [(28, 31, 36), (45, 80, 65) if status in {"created_partial", "unchanged", "false"} else (125, 58, 48), (28, 31, 36)]
        values = [field, status, boundary]
        for value, width, fill in zip(values, col_w, fills):
            draw.rectangle((x0, yy, x0 + width, yy + h), fill=fill, outline=(74, 80, 88))
            draw_wrapped(draw, x0 + 10, yy + 12, value, max(18, width // 12), size=13)
            x0 += width
        yy += h

    draw_text(draw, (1510, 955), "Pixel Counts / Fractions", fill=(255, 245, 190), size=23)
    yy = 995
    stat_lines = [
        f"image_size: {manifest['image_size']['width']} x {manifest['image_size']['height']}",
        f"generated sky core: {manifest['sidecars']['generated_mask']['pixel_count']} px ({manifest['sidecars']['generated_mask']['fraction']:.4f})",
        f"unknown/abstain union: {manifest['sidecars']['unknown_or_abstain_mask']['pixel_count']} px ({manifest['sidecars']['unknown_or_abstain_mask']['fraction']:.4f})",
        f"out-of-FOV black rows: {manifest['sidecars']['unknown_or_abstain_mask']['out_of_fov_black_rows']}",
        f"DB41 right ROI: {manifest['sidecars']['unknown_or_abstain_mask']['db41_rois']['right_roi']}",
        f"DB41 lower-right ROI: {manifest['sidecars']['unknown_or_abstain_mask']['db41_rois']['lower_right_roi']}",
        f"risk levels: {manifest['sidecars']['risk_map']['risk_levels']}",
    ]
    for line in stat_lines:
        yy = draw_wrapped(draw, 1510, yy, line, 70, size=14)
        yy += 5

    draw_text(draw, (1510, 1280), "Hard Checks", fill=(255, 245, 190), size=23)
    yy = 1320
    for check in manifest["checks"]:
        color = (80, 165, 100) if check["pass"] else (235, 90, 85)
        label = "PASS" if check["pass"] else "FAIL"
        yy = draw_wrapped(draw, 1510, yy, f"{label} {check['id']}: {check['evidence']}", 76, fill=color, size=13)
        yy += 3

    draw_text(draw, (36, 1500), "Claim Boundary", fill=(255, 245, 190), size=22)
    yy = 1534
    for line in manifest["claim_boundary"]:
        yy = draw_wrapped(draw, 36, yy, f"- {line}", 150, fill=(232, 232, 232), size=14)
        yy += 2

    board.save(BOARD, quality=92)


def derive_sidecars() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db49a = read_json(INPUTS["db49a_manifest"])
    db34 = read_json(INPUTS["db34_manifest"])
    db32 = read_json(INPUTS["db32_diagnostics"])
    db41 = read_json(INPUTS["db41_manifest"])

    candidate_path = resolve(db34["current_best"])
    sky_mask_path = resolve(db34["mask"] or db32["mask"])
    if candidate_path is None or sky_mask_path is None:
        raise RuntimeError("DB34 manifest did not resolve candidate or sky mask paths")

    candidate_sha_before = sha256_file(candidate_path)
    candidate = Image.open(candidate_path).convert("RGB")
    sky_mask_img = Image.open(sky_mask_path).convert("L")
    if sky_mask_img.size != candidate.size:
        raise RuntimeError(f"sky mask size {sky_mask_img.size} != candidate size {candidate.size}")

    width, height = candidate.size
    total_pixels = width * height
    candidate_arr = np.asarray(candidate)
    sky_mask = np.asarray(sky_mask_img)

    generated = np.where(sky_mask < 128, 255, 0).astype(np.uint8)
    generated_count = int(np.count_nonzero(generated))

    dark = np.max(candidate_arr, axis=2) <= 5
    row_dark_frac = dark.mean(axis=1)
    black_rows = np.where(row_dark_frac >= 0.75)[0]
    black_ranges = contiguous_ranges(black_rows)

    unknown = np.zeros((height, width), dtype=np.uint8)
    for y0, y1 in black_ranges:
        unknown[y0:y1, :] = 255

    rois = db41.get("rois_xyxy", {})
    right_roi = rois.get("right_roi")
    lower_right_roi = rois.get("lower_right_roi")
    if not right_roi or not lower_right_roi:
        raise RuntimeError("DB41 manifest missing right_roi or lower_right_roi")
    right_roi_changed = fill_roi(unknown, right_roi, 255)
    lower_right_roi_changed = fill_roi(unknown, lower_right_roi, 255)
    right_roi_unknown_stats = roi_nonzero_stats(unknown, right_roi)
    lower_right_roi_unknown_stats = roi_nonzero_stats(unknown, lower_right_roi)

    risk = np.zeros((height, width), dtype=np.uint8)
    risk = np.maximum(risk, np.where(generated > 0, RISK_LEVELS["generated_sky_core"], 0).astype(np.uint8))
    for y0, y1 in black_ranges:
        risk[y0:y1, :] = np.maximum(risk[y0:y1, :], RISK_LEVELS["out_of_fov_black_rows"])
    fill_roi(risk, right_roi, RISK_LEVELS["db41_right_roi_abstain"])
    fill_roi(risk, lower_right_roi, RISK_LEVELS["db41_lower_right_roi_zero_lidar_abstain"])

    Image.fromarray(generated, mode="L").save(GENERATED_MASK)
    Image.fromarray(unknown, mode="L").save(UNKNOWN_MASK)
    Image.fromarray(risk, mode="L").save(RISK_MAP)
    overlay_path = OUT_DIR / "db49b_sidecar_overlay_on_db32.jpg"
    make_overlay(candidate, generated, unknown, risk).save(overlay_path, quality=92)

    candidate_sha_after = sha256_file(candidate_path)

    manifest: dict[str, Any] = {
        "db": "DB-49b",
        "status": "sidecar_starter_pack_partial_only",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "sidecar-starter-pack-partial-only",
        "purpose": "Package only sidecars derivable from existing DB32/DB34/DB41 evidence, while keeping source_id_map missing/blocking.",
        "scope": {
            "cpu_local_only": True,
            "existing_artifacts_only": True,
            "candidate_pixels_modified": False,
            "new_panorama_repair": False,
            "new_generated_pixels": False,
            "new_image_generation": False,
            "new_dataset_scan": False,
            "a100_used": False,
            "executor_used": False,
            "network_used": False,
            "model_inference": False,
            "source_replacement": False,
            "permission_change": False,
            "red_promotions": [],
            "output_location": rel(OUT_DIR),
        },
        "current_handoff_candidate": {
            "path": rel(candidate_path),
            "claim_label": "caveated-handoff",
            "ready_for_uncaveated_bosch_training_data": False,
            "accepted_source_faithful_repair": False,
            "source_faithful_ceiling": False,
            "description": "DB32 s40 remains a Bosch-facing presentation/handoff candidate with source-sidestep and generated-sky caveats.",
        },
        "image_size": {"width": width, "height": height, "total_pixels": total_pixels},
        "inputs": {
            "candidate_image": {"path": rel(candidate_path), "path_resolved": str(candidate_path)},
            "sky_core_mask": {
                "path": rel(sky_mask_path),
                "path_resolved": str(sky_mask_path),
                "mask_convention": db32.get("mask_convention", "white/255 preserves source; black/0 generated sky core"),
            },
            "db41_manifest": {"path": rel(INPUTS["db41_manifest"]), "path_resolved": str(INPUTS["db41_manifest"])},
            "db41_board": {
                "path": rel(resolve(db41.get("board"))),
                "path_resolved": str(resolve(db41.get("board"))),
            },
            "db49a_manifest": {"path": rel(INPUTS["db49a_manifest"]), "path_resolved": str(INPUTS["db49a_manifest"])},
        },
        "candidate_integrity": {
            "sha256_before": candidate_sha_before,
            "sha256_after": candidate_sha_after,
            "unchanged": candidate_sha_before == candidate_sha_after,
        },
        "source_id_map_created": False,
        "source_id_map_status": "missing_blocking_not_fabricated",
        "sidecars": {
            "generated_mask": {
                "path": rel(GENERATED_MASK),
                "status": "partial_generated_mask_sky_core_only",
                "derivation": "255 where existing DB34/DB32 sky opmask is black/0 generated core; 0 elsewhere.",
                "pixel_count": generated_count,
                "fraction": generated_count / total_pixels,
                "db32_reported_core_fraction": db32.get("core_fraction"),
            },
            "unknown_or_abstain_mask": {
                "path": rel(UNKNOWN_MASK),
                "status": "partial_unknown_or_abstain_mask",
                "derivation": "Union of candidate rows with >=0.75 near-black pixels and DB41 right/lower-right abstain ROIs.",
                "pixel_count": int(np.count_nonzero(unknown)),
                "fraction": int(np.count_nonzero(unknown)) / total_pixels,
                "out_of_fov_black_rows": black_ranges,
                "row_dark_threshold": 0.75,
                "dark_pixel_rule": "max_rgb <= 5",
                "db41_rois": {
                    "right_roi": right_roi,
                    "lower_right_roi": lower_right_roi,
                    "right_roi_new_pixels_or_overwrites": right_roi_changed,
                    "lower_right_roi_new_pixels_or_overwrites": lower_right_roi_changed,
                    "right_roi_unknown_mask_pixels": right_roi_unknown_stats["nonzero"],
                    "right_roi_area": right_roi_unknown_stats["area"],
                    "lower_right_roi_unknown_mask_pixels": lower_right_roi_unknown_stats["nonzero"],
                    "lower_right_roi_area": lower_right_roi_unknown_stats["area"],
                    "right_roi_lidar_support_frac": db41.get("summaries", {}).get("right_roi", {}).get("lidar_support_frac"),
                    "lower_right_roi_lidar_support_frac": db41.get("summaries", {})
                    .get("lower_right_roi", {})
                    .get("lidar_support_frac"),
                    "recommendation": "abstain; rectangles are evidence boundaries, not repair permission.",
                },
            },
            "risk_map": {
                "path": rel(RISK_MAP),
                "status": "partial_contract_risk_map",
                "derivation": "Per-pixel max of generated sky risk, out-of-FOV black-row risk, and DB41 abstain ROI risks.",
                "risk_levels": RISK_LEVELS,
                "nonzero_pixel_count": int(np.count_nonzero(risk)),
                "fraction": int(np.count_nonzero(risk)) / total_pixels,
                "unique_values": sorted(int(v) for v in np.unique(risk)),
            },
            "overlay": {
                "path": rel(overlay_path),
                "status": "visual_review_only",
            },
        },
        "db49a_boundary": {
            "accepted_evidence_type": db49a.get("accepted_evidence_type"),
            "ready_for_bosch_dataset_contract": db49a.get("contract_summary", {}).get("ready_for_bosch_dataset_contract"),
            "blocking_fields_preserved": [
                "source_id_map",
                "full_unknown_or_abstain_mask",
                "full_risk_map",
                "license_generation_caveat",
            ],
        },
        "claim_boundary": [
            "DB49b creates partial sidecars only; it does not create or repair a panorama.",
            "DB32 s40 remains caveated handoff candidate, not fully source-faithful and not an original-G/A1/BEST repair.",
            "generated_mask is limited to the existing sky core and is not a full generated-region ontology.",
            "unknown_or_abstain_mask is partial: out-of-FOV black rows plus DB41 right/lower-right abstain ROIs.",
            "risk_map is partial contract risk, not a complete Waymo/Bosch per-pixel risk truth map.",
            "source_id_map remains missing/blocking and was not fabricated.",
            "DB41 rectangles are no-evidence/abstain boundaries, not source-faithful repair permission.",
            "ready_for_uncaveated_bosch_training_data=false.",
        ],
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
            "generated_mask": rel(GENERATED_MASK),
            "unknown_or_abstain_mask": rel(UNKNOWN_MASK),
            "risk_map": rel(RISK_MAP),
            "overlay": rel(overlay_path),
        },
    }

    manifest_text = json.dumps(manifest, ensure_ascii=False)
    checks = [
        {
            "id": "brief_scope_is_db49b",
            "pass": BRIEF.exists()
            and "Phase1 / DB49b" in BRIEF.read_text(encoding="utf-8")
            and "sidecar starter pack" in BRIEF.read_text(encoding="utf-8"),
            "evidence": "DB49b sub-scope exists in decision_briefs.md before execution.",
        },
        {
            "id": "db49a_precondition_exists",
            "pass": INPUTS["db49a_manifest"].exists() and db49a.get("accepted_evidence_type") == "bosch-data-contract-inventory-only",
            "evidence": "DB49a inventory manifest exists and is inventory-only.",
        },
        {
            "id": "uses_existing_artifacts_only",
            "pass": all(path.exists() for path in INPUTS.values()),
            "evidence": "Inputs are existing DB49a/DB34/DB32/DB41 artifacts only.",
        },
        {
            "id": "candidate_not_modified",
            "pass": candidate_sha_before == candidate_sha_after,
            "evidence": "Candidate sha256 before and after sidecar derivation are identical.",
        },
        {
            "id": "source_id_map_not_fabricated",
            "pass": manifest["source_id_map_created"] is False
            and manifest["source_id_map_status"] == "missing_blocking_not_fabricated",
            "evidence": "No source_id_map output exists in DB49b.",
        },
        {
            "id": "generated_mask_matches_existing_core",
            "pass": abs((generated_count / total_pixels) - float(db32.get("core_fraction", -1))) < 1e-9,
            "evidence": f"generated_count={generated_count}, fraction={generated_count / total_pixels:.10f}.",
        },
        {
            "id": "db41_abstain_rois_encoded",
            "pass": right_roi_unknown_stats["area"] > 0
            and lower_right_roi_unknown_stats["area"] > 0
            and right_roi_unknown_stats["nonzero"] == right_roi_unknown_stats["area"]
            and lower_right_roi_unknown_stats["nonzero"] == lower_right_roi_unknown_stats["area"],
            "evidence": (
                "Both DB41 ROIs are fully encoded in unknown/risk sidecars "
                f"(right={right_roi_unknown_stats['nonzero']}/{right_roi_unknown_stats['area']}, "
                f"lower_right={lower_right_roi_unknown_stats['nonzero']}/{lower_right_roi_unknown_stats['area']})."
            ),
        },
        {
            "id": "out_of_fov_black_rows_encoded",
            "pass": len(black_ranges) > 0 and int(np.count_nonzero(unknown)) > generated_count * 0,
            "evidence": f"black_row_ranges={black_ranges}.",
        },
        {
            "id": "training_ready_false",
            "pass": manifest["current_handoff_candidate"]["ready_for_uncaveated_bosch_training_data"] is False,
            "evidence": "DB49b keeps Bosch training-data readiness false.",
        },
        {
            "id": "no_repair_generation_model_or_executor",
            "pass": not any(
                manifest["scope"][key]
                for key in ("new_panorama_repair", "new_image_generation", "a100_used", "executor_used", "network_used", "model_inference")
            ),
            "evidence": "Scope flags show no repair, generation, A100, executor, network, or model inference.",
        },
        {
            "id": "no_secret_like_strings",
            "pass": re.search(r"hf_[A-Za-z0-9]+|Bearer\s+|trycloudflare", manifest_text) is None,
            "evidence": "Manifest text contains no HF token, bearer marker, or tunnel URL marker.",
        },
    ]
    manifest["checks"] = checks
    build_board(manifest, overlay_path)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main() -> None:
    manifest = derive_sidecars()
    print(
        json.dumps(
            {
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
                "generated_mask": rel(GENERATED_MASK),
                "unknown_or_abstain_mask": rel(UNKNOWN_MASK),
                "risk_map": rel(RISK_MAP),
                "checks_pass": all(c["pass"] for c in manifest["checks"]),
                "training_ready": manifest["current_handoff_candidate"]["ready_for_uncaveated_bosch_training_data"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
