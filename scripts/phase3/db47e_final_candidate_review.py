from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining"
MANIFEST = OUT_DIR / "db47e_final_candidate_review_manifest.json"
BOARD = OUT_DIR / "db47e_final_candidate_review_board.jpg"

DB47D = OUT_DIR / "db47d_exact_same_log_review_manifest.json"
DB28_DIR = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine"
DB32 = ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_generated_sky_harmonize_s40.png"
DB34 = ROOT / "deliverables" / "dit360_v2" / "db34_current_best_qa" / "db34_current_best_manifest.json"
DB41 = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_manifest.json"
DB41_BOARD = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_board.jpg"
G_BMW = ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg"
BRIEF = ROOT / "agent" / "decision_briefs.md"

ANCHORS = [105, 200, 204]
SOURCE_REVIEW_ROI = (850, 420, 1650, 720)
RIGHT_ROI = (1440, 360, 2048, 720)
EXPECTED_DB32_SHA256 = "ade90f2bb629abac88e6516d6a2abd0d6785619024c0be4d5a01ea23dc4a8930"


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


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def as_float(row: dict[str, Any], key: str, default: float = 999.0) -> float:
    val = row.get(key)
    if isinstance(val, (int, float)):
        return float(val)
    return default


def as_int(row: dict[str, Any], key: str, default: int = -1) -> int:
    val = row.get(key)
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    return default


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


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, fill: tuple[int, int, int], w: int) -> int:
    draw.rounded_rectangle((x, y, x + w, y + 34), radius=5, fill=fill, outline=(185, 185, 185))
    draw_text(draw, (x + 10, y + 8), label, size=14)
    return x + w + 12


def image_box(
    board: Image.Image,
    path: Path,
    box: tuple[int, int, int, int],
    label: str,
    crop: tuple[int, int, int, int] | None = None,
    outline: tuple[int, int, int] = (80, 84, 92),
) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(25, 27, 32), outline=outline, width=2)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            if crop is not None:
                img = img.crop(crop)
            img.thumbnail((x1 - x0 - 16, y1 - y0 - 44))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception as exc:
            draw_wrapped(draw, x0 + 10, y0 + 24, f"image load failed: {type(exc).__name__}", 38, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def exact_asset_paths(anchor: int) -> dict[str, Path]:
    return {
        "compare": DB28_DIR / f"SR_bmw_db28_a{anchor}_compare.jpg",
        "final": DB28_DIR / f"SR_bmw_db28_a{anchor}_final_1024x2048.png",
    }


def image_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    with Image.open(path) as img:
        return {
            "exists": True,
            "size": list(img.size),
            "sha256": sha256_file(path),
        }


def mean_abs_diff(path_a: Path, path_b: Path, crop: tuple[int, int, int, int] | None = None) -> float | None:
    if not path_a.exists() or not path_b.exists():
        return None
    with Image.open(path_a).convert("RGB") as ia, Image.open(path_b).convert("RGB") as ib:
        if ia.size != ib.size:
            return None
        if crop is not None:
            ia = ia.crop(crop)
            ib = ib.crop(crop)
        pa = ia.tobytes()
        pb = ib.tobytes()
    if len(pa) != len(pb):
        return None
    return sum(abs(a - b) for a, b in zip(pa, pb)) / float(len(pa))


def classify_candidate(anchor: int, row: dict[str, Any] | None, db34: dict[str, Any]) -> dict[str, Any]:
    paths = exact_asset_paths(anchor)
    compare_exists = paths["compare"].exists()
    final_exists = paths["final"].exists()
    final_eligible = compare_exists and final_exists
    source_base = Path(str(db34.get("source_base", ""))).as_posix()
    is_db32_source_base = source_base.endswith(f"SR_bmw_db28_a{anchor}_final_1024x2048.png")

    if anchor == 200 and final_eligible and is_db32_source_base:
        verdict = "confirmed_current_source_sidestep_base"
        role = "keep_a200_db32_lineage"
        reasons = [
            "exact_compare_available",
            "exact_final_available",
            "db34_source_base",
            "db32_noncore_byte_exact_to_source",
            "best_or_tied_db28_line_risk",
            "no_new_candidate_image",
        ]
        note = "Confirm a200 as the current source-sidestep base for the existing DB32 handoff candidate; this does not make DB32 source-faithful because sky core remains generated/harmonized."
    elif anchor == 204 and final_eligible:
        verdict = "hold_alternate_final_eligible"
        role = "alternate_not_selected"
        reasons = [
            "exact_compare_available",
            "exact_final_available",
            "not_db34_source_base",
            "no_downstream_db29_db32_qa",
            "slightly_worse_rank_score",
            "review_only",
        ]
        note = "a204 is a valid exact alternate to keep in accounting, but it lacks the downstream DB29/DB32/DB34 lineage and does not displace a200 under this brief."
    elif anchor == 105 and compare_exists and not final_exists:
        verdict = "hold_compare_only_not_final_eligible"
        role = "hold"
        reasons = [
            "exact_compare_available",
            "exact_final_missing",
            "different_scene_context_risk",
            "not_final_eligible",
            "review_only",
        ]
        note = "a105 has an exact compare image but no existing final image, so it cannot be promoted in DB47e."
    else:
        verdict = "hold_missing_required_assets"
        role = "hold"
        reasons = ["missing_required_exact_asset", "not_final_eligible", "review_only"]
        note = "Missing required exact evidence for final-candidate review."

    return {
        "candidate_id": f"02a00399_a{anchor:04d}",
        "anchor": anchor,
        "db47d_candidate_present": row is not None,
        "db47d_visual_verdict": None if row is None else row.get("db47d_visual_verdict"),
        "rank_score_low_is_better": None if row is None else as_float(row, "rank_score_low_is_better"),
        "roi_lidar_support": None if row is None else as_float(row, "roi_lidar_support"),
        "roi_line_risk": None if row is None else as_float(row, "roi_line_risk"),
        "exact_assets": {
            "compare": rel(paths["compare"]) if compare_exists else None,
            "final": rel(paths["final"]) if final_exists else None,
        },
        "asset_stats": {
            "compare": image_stats(paths["compare"]),
            "final": image_stats(paths["final"]),
        },
        "final_eligible": final_eligible,
        "db34_source_base": is_db32_source_base,
        "db47e_verdict": verdict,
        "role": role,
        "visual_reasons": reasons,
        "note": note,
        "claim_boundary": "source-sidestep candidate review only; not original-G repair, not source_id_map evidence",
    }


def build_manifest() -> dict[str, Any]:
    db47d = read_json(DB47D)
    db34 = read_json(DB34)
    db41 = read_json(DB41)
    brief_text = BRIEF.read_text(encoding="utf-8")
    row_by_anchor = {as_int(r, "anchor"): r for r in db47d.get("reviewed_rows", [])}

    candidates = [classify_candidate(anchor, row_by_anchor.get(anchor), db34) for anchor in ANCHORS]
    verdict_counts = Counter(c["db47e_verdict"] for c in candidates)
    reason_counts: Counter[str] = Counter()
    for candidate in candidates:
        reason_counts.update(candidate["visual_reasons"])

    db32_sha = sha256_file(DB32)
    a200_final = exact_asset_paths(200)["final"]
    a204_final = exact_asset_paths(204)["final"]
    db34_preservation = db34.get("source_preservation", {})
    missing_holds = [
        r
        for r in db47d.get("reviewed_rows", [])
        if r.get("db47d_visual_verdict") in {"hold_strict_missing_exact", "hold_relaxed_missing_exact"}
    ]

    checks = [
        {
            "id": "brief_scope_is_db47e",
            "pass": "Phase4 / DB47e" in brief_text,
            "evidence": "DB47e Phase4 brief exists before execution.",
        },
        {
            "id": "inputs_existing_artifacts_only",
            "pass": DB47D.exists() and DB34.exists() and DB41.exists() and DB32.exists() and G_BMW.exists(),
            "evidence": "Inputs are existing DB47d, DB28, DB32/DB34, DB41, and G diagnostic artifacts.",
        },
        {
            "id": "reviews_only_allowed_anchors",
            "pass": [c["anchor"] for c in candidates] == ANCHORS,
            "evidence": f"reviewed anchors={ANCHORS}; missing-exact DB47d holds remain holds.",
        },
        {
            "id": "final_eligible_requires_compare_and_final",
            "pass": [c["anchor"] for c in candidates if c["final_eligible"]] == [200, 204],
            "evidence": "Only a200 and a204 have both exact compare and final assets; a105 is compare-only hold.",
        },
        {
            "id": "a200_matches_db32_lineage",
            "pass": any(c["anchor"] == 200 and c["db34_source_base"] for c in candidates),
            "evidence": f"DB34 source_base={db34.get('source_base')}.",
        },
        {
            "id": "db32_sha_unchanged",
            "pass": db32_sha == EXPECTED_DB32_SHA256,
            "evidence": f"DB32 sha256={db32_sha}.",
        },
        {
            "id": "db34_noncore_preservation_present",
            "pass": db34_preservation.get("db32_noncore_vs_source", {}).get("max") == 0,
            "evidence": f"db32_noncore_vs_source={db34_preservation.get('db32_noncore_vs_source')}.",
        },
        {
            "id": "db41_abstain_preserved",
            "pass": (
                db41.get("threshold_results", {}).get("right_roi", {}).get("passes_db41_gate") is False
                and db41.get("threshold_results", {}).get("lower_right_roi", {}).get("passes_db41_gate") is False
                and db41.get("summaries", {}).get("lower_right_roi", {}).get("lidar_support_frac") == 0.0
            ),
            "evidence": "DB41 right/lower-right remain failed gates; lower-right LiDAR support is 0.0.",
        },
        {
            "id": "no_source_id_map_or_provenance_claim",
            "pass": True,
            "evidence": "DB47e does not create or infer source_id_map; DB49 exact-lineage rerun remains required.",
        },
        {
            "id": "no_model_remote_or_generation",
            "pass": True,
            "evidence": "CPU/local review only; no HF/VGGT, A100, executor, seamroute, renderer, scan, generation, source replacement, or repair.",
        },
    ]

    manifest: dict[str, Any] = {
        "db": "DB-47e",
        "status": "accepted_existing_artifact_final_candidate_review",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "source-selection-final-candidate-review-existing-artifacts-only",
        "purpose": "Confirm whether current DB47 exact rows support the existing DB32 source-sidestep base without creating or rerunning imagery.",
        "scope": {
            "cpu_local_only": True,
            "inputs": [rel(DB47D), rel(DB34), rel(DB41), rel(DB32), rel(G_BMW)],
            "reviewed_anchors": ANCHORS,
            "missing_exact_holds_preserved": [r.get("candidate_id") for r in missing_holds],
            "new_dataset_scan": False,
            "new_exact_asset_fetch": False,
            "seamroute_or_renderer": False,
            "a100_used": False,
            "executor_used": False,
            "hf_or_vggt_used": False,
            "model_inference": False,
            "panorama_repair": False,
            "generation": False,
            "source_replacement": False,
            "source_id_map_created": False,
            "permission_change": False,
            "red_promotion": False,
            "output_location": rel(OUT_DIR),
        },
        "candidate_review": candidates,
        "aggregate_counts": {
            "reviewed_candidates": len(candidates),
            "final_eligible_candidates": sum(1 for c in candidates if c["final_eligible"]),
            "compare_only_holds": sum(1 for c in candidates if c["exact_assets"]["compare"] and not c["exact_assets"]["final"]),
            "missing_exact_holds_from_db47d": len(missing_holds),
            "visual_verdict_counts": dict(verdict_counts),
            "visual_reason_counts": dict(reason_counts.most_common()),
        },
        "db32_context": {
            "candidate_image": rel(DB32),
            "sha256": db32_sha,
            "sha256_expected": EXPECTED_DB32_SHA256,
            "source_base": rel(ROOT / db34.get("source_base", "")) if db34.get("source_base") else None,
            "previous_best": rel(ROOT / db34.get("previous_best", "")) if db34.get("previous_best") else None,
            "mask": rel(ROOT / db34.get("mask", "")) if db34.get("mask") else None,
            "object_gate": db34.get("object_gate"),
            "source_preservation": db34_preservation,
            "accepted_caveats": db34.get("accepted_caveats"),
            "claim_boundary": "DB32 s40 is the caveated Bosch-facing handoff candidate with source-sidestep plus generated/harmonized sky caveats; it is not fully source-faithful.",
        },
        "diagnostic_context": {
            "g_bmw_pano": rel(G_BMW),
            "g_bmw_claim": "classic BMW failure / diagnostic reference only; not a repair base.",
            "db41_manifest": rel(DB41),
            "db41_board": rel(DB41_BOARD),
            "db41_right_gate": db41.get("threshold_results", {}).get("right_roi"),
            "db41_lower_right_gate": db41.get("threshold_results", {}).get("lower_right_roi"),
            "db41_lower_right_lidar_support": db41.get("summaries", {}).get("lower_right_roi", {}).get("lidar_support_frac"),
        },
        "image_comparison_stats_diagnostic_only": {
            "a200_vs_a204_full_mae": mean_abs_diff(a200_final, a204_final),
            "a200_vs_a204_source_review_roi_mae": mean_abs_diff(a200_final, a204_final, SOURCE_REVIEW_ROI),
            "a200_vs_db32_full_mae": mean_abs_diff(a200_final, DB32),
            "a200_vs_db32_source_review_roi_mae": mean_abs_diff(a200_final, DB32, SOURCE_REVIEW_ROI),
            "note": "MAE values are diagnostic context only and are not source-ownership evidence.",
        },
        "checks": checks,
        "decision": {
            "db47_status": "accepted_phase4_existing_artifact_final_candidate_review",
            "accepted_db47_source_selection_evidence": True,
            "confirmed_current_source_sidestep_base_anchor": 200,
            "confirmed_current_handoff_candidate": rel(DB32),
            "candidate_image_selection_changed": False,
            "new_candidate_created": False,
            "accepted_source_faithful_repair": False,
            "accepted_original_g_repair": False,
            "accepted_source_id_map_evidence": False,
            "ready_for_uncaveated_bosch_training_data": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "claim_boundary": "DB47e confirms the existing a200/DB32 source-sidestep base under local exact evidence; it does not repair original G/A1/BEST, does not fill DB49 source_id_map, and does not make DB32 fully source-faithful.",
            "next_recommended_step": "Open DB49 exact-lineage source/provenance sidecar rerun if Bosch data-contract packaging is next, or keep DB47 paused unless a bounded fixed-universe scan is explicitly needed.",
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def draw_candidate_table(draw: ImageDraw.ImageDraw, x: int, y: int, candidates: list[dict[str, Any]]) -> int:
    draw_text(draw, (x, y), "Candidate verdicts", fill=(185, 215, 240), size=21)
    y += 35
    cols = [x, x + 95, x + 205, x + 330, x + 460, x + 585, x + 735]
    headers = ["anchor", "eligible", "role", "score", "lidar", "line", "verdict"]
    for col, header in zip(cols, headers):
        draw_text(draw, (col, y), header, fill=(165, 190, 215), size=13)
    y += 24
    for row in candidates:
        vals = [
            str(row["anchor"]),
            "yes" if row["final_eligible"] else "no",
            str(row["role"]).replace("keep_", "keep ").replace("_", " "),
            "n/a" if row["rank_score_low_is_better"] is None else f"{row['rank_score_low_is_better']:.5f}",
            "n/a" if row["roi_lidar_support"] is None else f"{row['roi_lidar_support']:.3f}",
            "n/a" if row["roi_line_risk"] is None else f"{row['roi_line_risk']:.5f}",
            str(row["db47e_verdict"]).replace("_", " "),
        ]
        fill = (210, 245, 215) if row["anchor"] == 200 else (230, 230, 230)
        for col, val in zip(cols, vals):
            draw_text(draw, (col, y), val, fill=fill, size=12)
        y += 23
        y = draw_wrapped(draw, x + 20, y, row["note"], 104, fill=(210, 215, 220), size=12)
        y += 7
    return y


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2400, 2050), (15, 17, 21))
    draw = ImageDraw.Draw(board)
    counts = manifest["aggregate_counts"]
    decision = manifest["decision"]

    draw_text(draw, (28, 24), "DB47e Existing-Artifact Final-Candidate Review", size=30)
    draw_text(
        draw,
        (28, 64),
        "CPU/local review of a105/a200/a204 only - no HF/A100/model/scan/generation/repair/source replacement",
        fill=(220, 215, 170),
        size=16,
    )
    x = 28
    x = pill(draw, x, 102, f"reviewed={counts['reviewed_candidates']}", (95, 100, 145), 150)
    x = pill(draw, x, 102, f"eligible={counts['final_eligible_candidates']}", (65, 135, 85), 145)
    x = pill(draw, x, 102, "confirmed base=a200", (65, 125, 110), 220)
    x = pill(draw, x, 102, "DB32 unchanged", (65, 125, 85), 170)
    x = pill(draw, x, 102, "source_id_map=False", (145, 70, 70), 205)
    x = pill(draw, x, 102, "source-faithful=False", (145, 70, 70), 230)
    pill(draw, x, 102, "RED promotions=0", (65, 125, 85), 180)

    left_y = 158
    left_y = draw_candidate_table(draw, 28, left_y, manifest["candidate_review"])
    left_y += 8
    draw_text(draw, (28, left_y), "Decision", size=22)
    left_y += 34
    for line in [
        decision["claim_boundary"],
        "a204 remains an alternate exact final with no downstream DB29/DB32/DB34 QA.",
        "a105 remains compare-only hold because no exact final asset exists.",
        "DB41 lower-right/right-line remains no-evidence/abstain; lower-right LiDAR support is 0.0.",
        "G_bmw_pano is a classic BMW failure / diagnostic reference only.",
    ]:
        left_y = draw_wrapped(draw, 42, left_y, "- " + line, 104, fill=(235, 235, 215), size=14)

    left_y += 8
    draw_text(draw, (28, left_y), "DB32 source-sidestep context", size=22)
    left_y += 34
    db32_ctx = manifest["db32_context"]
    for line in [
        f"candidate: {db32_ctx['candidate_image']}",
        f"source base: {db32_ctx['source_base']}",
        f"db32 sha ok: {db32_ctx['sha256'] == db32_ctx['sha256_expected']}",
        f"db32 noncore vs source: {db32_ctx['source_preservation'].get('db32_noncore_vs_source')}",
        "generated/harmonized sky caveat preserved; not fully source-faithful.",
    ]:
        left_y = draw_wrapped(draw, 42, left_y, "- " + str(line), 106, fill=(215, 230, 240), size=13)

    x2 = 1030
    draw_text(draw, (x2, 158), "Full context", size=22)
    image_box(board, DB32, (x2, 195, x2 + 420, 470), "DB32 s40 caveated handoff")
    image_box(board, G_BMW, (x2 + 450, 195, x2 + 870, 470), "G_bmw diagnostic failure ref")
    image_box(board, DB41_BOARD, (x2 + 900, 195, x2 + 1335, 470), "DB41 abstain evidence board")

    draw_text(draw, (x2, 505), "Exact compare assets", size=22)
    image_box(board, exact_asset_paths(105)["compare"], (x2, 540, x2 + 420, 835), "a105 compare only")
    image_box(board, exact_asset_paths(200)["compare"], (x2 + 455, 540, x2 + 875, 835), "a200 compare")
    image_box(board, exact_asset_paths(204)["compare"], (x2 + 910, 540, x2 + 1330, 835), "a204 compare")

    draw_text(draw, (x2, 870), "Same-ROI / source-boundary crops", size=22)
    image_box(board, exact_asset_paths(200)["final"], (x2, 905, x2 + 420, 1145), "a200 source-review ROI", crop=SOURCE_REVIEW_ROI, outline=(80, 150, 95))
    image_box(board, exact_asset_paths(204)["final"], (x2 + 455, 905, x2 + 875, 1145), "a204 source-review ROI", crop=SOURCE_REVIEW_ROI)
    image_box(board, DB32, (x2 + 910, 905, x2 + 1330, 1145), "DB32 source-review ROI", crop=SOURCE_REVIEW_ROI)

    image_box(board, exact_asset_paths(200)["final"], (x2, 1180, x2 + 420, 1420), "a200 right/DB41 ROI", crop=RIGHT_ROI, outline=(80, 150, 95))
    image_box(board, exact_asset_paths(204)["final"], (x2 + 455, 1180, x2 + 875, 1420), "a204 right/DB41 ROI", crop=RIGHT_ROI)
    image_box(board, DB32, (x2 + 910, 1180, x2 + 1330, 1420), "DB32 right/DB41 ROI", crop=RIGHT_ROI)

    y2 = 1460
    draw_text(draw, (x2, y2), "Hard checks", size=22)
    y2 += 34
    for check in manifest["checks"]:
        fill = (65, 140, 86) if check["pass"] else (170, 60, 60)
        draw.rounded_rectangle((x2, y2, x2 + 78, y2 + 25), radius=4, fill=fill)
        draw_text(draw, (x2 + 16, y2 + 4), "PASS" if check["pass"] else "STOP", size=12)
        y2 = draw_wrapped(draw, x2 + 92, y2 + 3, f"{check['id']}: {check['evidence']}", 92, size=13)
        y2 += 7

    y3 = 1650
    draw_text(draw, (28, y3), "Diagnostic-only image stats", size=22)
    y3 += 34
    for key, value in manifest["image_comparison_stats_diagnostic_only"].items():
        if key == "note":
            continue
        y3 = draw_wrapped(draw, 42, y3, f"- {key}: {value}", 102, fill=(210, 215, 225), size=13)
    y3 = draw_wrapped(draw, 42, y3 + 5, "- " + manifest["image_comparison_stats_diagnostic_only"]["note"], 102, fill=(235, 225, 190), size=13)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> None:
    manifest = build_manifest()
    build_board(manifest)
    print(f"wrote {MANIFEST}")
    print(f"wrote {BOARD}")
    print(json.dumps(manifest["aggregate_counts"], indent=2))
    print(json.dumps(manifest["decision"], indent=2))


if __name__ == "__main__":
    main()
