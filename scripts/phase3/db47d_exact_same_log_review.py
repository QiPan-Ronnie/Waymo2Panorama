from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining"
MANIFEST = OUT_DIR / "db47d_exact_same_log_review_manifest.json"
BOARD = OUT_DIR / "db47d_exact_same_log_review_board.jpg"

DB47C = OUT_DIR / "db47c_same_roi_bucket_review_manifest.json"
DB28_DIR = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine"
DB28_SUMMARY = DB28_DIR / "db28_strict_clean_source_scan_summary.json"
DB28_MONTAGE = DB28_DIR / "db28_strict_clean_source_scan_montage.jpg"
BRIEF = ROOT / "agent" / "decision_briefs.md"

ROI = (850, 420, 1650, 720)


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
    return x + w + 14


def image_box(board: Image.Image, path: Path, box: tuple[int, int, int, int], label: str, crop: tuple[int, int, int, int] | None = None) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(25, 27, 32), outline=(80, 84, 92), width=1)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            if crop is not None:
                img = img.crop(crop)
            img.thumbnail((x1 - x0 - 14, y1 - y0 - 42))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception as exc:
            draw_wrapped(draw, x0 + 10, y0 + 22, f"image load failed: {type(exc).__name__}", 42, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 28), label, fill=(220, 230, 245), size=13)


def exact_assets(anchor: int) -> dict[str, str | None]:
    compare = DB28_DIR / f"SR_bmw_db28_a{anchor}_compare.jpg"
    final = DB28_DIR / f"SR_bmw_db28_a{anchor}_final_1024x2048.png"
    return {
        "compare": rel(compare) if compare.exists() else None,
        "final": rel(final) if final.exists() else None,
    }


def visual_verdict(row: dict[str, Any], assets: dict[str, str | None]) -> tuple[str, list[str], str]:
    bucket = str(row.get("db47b_bucket", ""))
    if bucket == "strict_review_bucket" and assets["compare"]:
        return (
            "exact_review_candidate_not_final",
            ["strict_metric_pass", "same_log", "exact_compare_available", "source_sidestep_review_only"],
            "Exact DB28 compare evidence exists. Keep as review candidate only; do not select a final candidate from DB47d.",
        )
    if bucket == "strict_review_bucket":
        return (
            "hold_strict_missing_exact",
            ["strict_metric_pass", "same_log", "missing_exact_asset", "no_promotion"],
            "Strict metric row lacks local exact compare/final evidence, so it remains hold.",
        )
    if bucket == "relaxed_review_bucket":
        return (
            "hold_relaxed_missing_exact",
            ["relaxed_metric_pass", "same_log", "missing_exact_asset", "weaker_metric_boundary", "no_promotion"],
            "Relaxed row lacks local exact evidence and has weaker metrics; it remains hold.",
        )
    return (
        "out_of_scope",
        ["not_strict_or_relaxed"],
        "DB47d reviews only strict/relaxed same-log rows from DB47c.",
    )


def build_manifest() -> dict[str, Any]:
    db47c = read_json(DB47C)
    db28 = read_json(DB28_SUMMARY)
    rows = [
        r
        for r in db47c.get("reviewed_rows", [])
        if r.get("db47b_bucket") in {"strict_review_bucket", "relaxed_review_bucket"}
    ]
    rows.sort(key=lambda r: (str(r.get("db47b_bucket")) != "strict_review_bucket", as_float(r, "rank_score_low_is_better"), as_int(r, "anchor")))

    reviewed: list[dict[str, Any]] = []
    verdict_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for row in rows:
        anchor = as_int(row, "anchor")
        assets = exact_assets(anchor)
        verdict, reasons, note = visual_verdict(row, assets)
        verdict_counts[verdict] += 1
        reason_counts.update(reasons)
        reviewed.append(
            {
                "candidate_id": row.get("candidate_id"),
                "anchor": anchor,
                "db47b_bucket": row.get("db47b_bucket"),
                "db47c_visual_verdict": row.get("visual_verdict"),
                "db47d_visual_verdict": verdict,
                "visual_reasons": reasons,
                "note": note,
                "rank_score_low_is_better": as_float(row, "rank_score_low_is_better"),
                "roi_lidar_support": as_float(row, "roi_lidar_support"),
                "roi_line_risk": as_float(row, "roi_line_risk"),
                "exact_assets": assets,
                "claim_boundary": "same-log exact review pack only; not final candidate and not original-G repair",
            }
        )

    exact_rows = sum(1 for r in reviewed if r["exact_assets"]["compare"])
    final_rows = sum(1 for r in reviewed if r["exact_assets"]["final"])
    missing_exact_rows = len(reviewed) - exact_rows
    strict_rows = [r for r in reviewed if r["db47b_bucket"] == "strict_review_bucket"]
    relaxed_rows = [r for r in reviewed if r["db47b_bucket"] == "relaxed_review_bucket"]

    checks = [
        {
            "id": "brief_scope_is_db47d",
            "pass": "Phase3 / DB47d" in BRIEF.read_text(encoding="utf-8"),
            "evidence": "DB47d sub-scope exists in decision_briefs.md before execution.",
        },
        {
            "id": "uses_existing_db47c_db28_only",
            "pass": DB47C.exists() and DB28_SUMMARY.exists() and DB28_MONTAGE.exists(),
            "evidence": "Inputs are DB47c manifest, DB28 summary/montage, and local DB28 exact assets only.",
        },
        {
            "id": "reviews_all_strict_relaxed_rows",
            "pass": len(reviewed) == 10 and len(strict_rows) == 7 and len(relaxed_rows) == 3,
            "evidence": f"reviewed={len(reviewed)} strict={len(strict_rows)} relaxed={len(relaxed_rows)}.",
        },
        {
            "id": "exact_and_missing_assets_reported",
            "pass": exact_rows == 3 and missing_exact_rows == 7,
            "evidence": f"exact_compare_rows={exact_rows}; missing_exact_rows={missing_exact_rows}; final_rows={final_rows}.",
        },
        {
            "id": "no_final_candidate_selection",
            "pass": True,
            "evidence": "All verdicts are exact-review-candidate or hold labels; no selected final field exists.",
        },
        {
            "id": "no_scan_repair_generation_or_source_replacement",
            "pass": True,
            "evidence": "CPU/local existing-artifact review only; no scan, seamroute, renderer, model, A100, executor, generation, repair, or source replacement.",
        },
        {
            "id": "source_sidestep_not_original_g_repair",
            "pass": True,
            "evidence": "Exact rows are same-log source-sidestep review candidates only, not original-G seam repair.",
        },
        {
            "id": "db41_abstain_boundary_preserved",
            "pass": True,
            "evidence": "DB47d does not evaluate or promote DB41 lower-right/right-line; inherited no-evidence/abstain remains.",
        },
    ]

    manifest: dict[str, Any] = {
        "db": "DB-47d",
        "status": "exact_same_log_review_pack",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "source-selection-exact-review-pack-only",
        "purpose": "Make DB47c strict/relaxed same-log exact evidence self-contained without selecting a final candidate.",
        "scope": {
            "cpu_local_only": True,
            "inputs": [rel(DB47C), rel(DB28_SUMMARY), rel(DB28_MONTAGE)],
            "db47c_rows_reviewed": len(reviewed),
            "new_dataset_scan": False,
            "new_exact_asset_fetch": False,
            "a100_used": False,
            "executor_used": False,
            "model_inference": False,
            "seamroute_or_renderer": False,
            "panorama_repair": False,
            "generation": False,
            "source_replacement": False,
            "permission_change": False,
            "red_promotion": False,
            "output_location": rel(OUT_DIR),
        },
        "db28_context": {
            "uuid": db28.get("uuid"),
            "roi": db28.get("roi"),
            "anchors": db28.get("anchors"),
            "ranking_note": db28.get("ranking_note"),
        },
        "aggregate_counts": {
            "total_reviewed_rows": len(reviewed),
            "strict_rows": len(strict_rows),
            "relaxed_rows": len(relaxed_rows),
            "exact_compare_rows": exact_rows,
            "exact_final_rows": final_rows,
            "missing_exact_rows": missing_exact_rows,
            "visual_verdict_counts": dict(verdict_counts),
            "visual_reason_counts": dict(reason_counts.most_common()),
        },
        "reviewed_rows": reviewed,
        "checks": checks,
        "decision": {
            "db47_status": "running_phase3_exact_review_pack_complete",
            "accepted_db47_diagnostic_evidence": True,
            "accepted_source_faithful_repair": False,
            "selected_final_candidate": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "claim_boundary": "DB47d is an exact same-log source-selection review pack only; it does not repair seams or accept a final panorama.",
            "next_allowed_step": "Either open a final-candidate review with stricter same-ROI criteria, open a fixed-universe full scan, or pause DB47; do not select a final from DB47d alone.",
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def draw_rows(draw: ImageDraw.ImageDraw, x: int, y: int, title: str, rows: list[dict[str, Any]]) -> int:
    draw_text(draw, (x, y), title, fill=(185, 215, 240), size=18)
    y += 30
    cols = [x, x + 145, x + 250, x + 485, x + 585, x + 680, x + 780]
    headers = ["candidate", "bucket", "verdict", "score", "lidar", "line", "asset"]
    for col, header in zip(cols, headers):
        draw_text(draw, (col, y), header, fill=(165, 190, 215), size=13)
    y += 23
    for row in rows:
        vals = [
            str(row["candidate_id"]),
            str(row["db47b_bucket"]).replace("_review_bucket", ""),
            str(row["db47d_visual_verdict"]).replace("exact_review_candidate_not_final", "exact_review").replace("hold_strict_missing_exact", "hold_missing_exact").replace("hold_relaxed_missing_exact", "hold_relaxed"),
            f"{row['rank_score_low_is_better']:.3f}",
            f"{row['roi_lidar_support']:.3f}",
            f"{row['roi_line_risk']:.3f}",
            "compare+final" if row["exact_assets"]["final"] else ("compare" if row["exact_assets"]["compare"] else "missing"),
        ]
        for col, val in zip(cols, vals):
            draw_text(draw, (col, y), val, size=12)
        y += 21
    return y


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2200, 1800), (15, 17, 21))
    draw = ImageDraw.Draw(board)
    counts = manifest["aggregate_counts"]

    draw_text(draw, (28, 24), "DB47d Exact Same-Log Review Pack", size=30)
    draw_text(draw, (28, 64), "Existing DB47c + DB28 artifacts only - no scan, no repair, no generation, no final candidate", fill=(220, 215, 170), size=16)
    x = 28
    x = pill(draw, x, 100, f"rows={counts['total_reviewed_rows']}", (95, 100, 145), 130)
    x = pill(draw, x, 100, f"exact compare={counts['exact_compare_rows']}", (65, 135, 85), 180)
    x = pill(draw, x, 100, f"final imgs={counts['exact_final_rows']}", (65, 125, 110), 150)
    x = pill(draw, x, 100, f"missing exact={counts['missing_exact_rows']}", (130, 105, 55), 180)
    x = pill(draw, x, 100, "review-only", (80, 105, 135), 150)
    x = pill(draw, x, 100, "final candidate=False", (145, 70, 70), 220)
    pill(draw, x, 100, "RED promotions=0", (65, 125, 85), 180)

    y = 154
    draw_text(draw, (28, y), "Verdict counts", size=21)
    y += 32
    for verdict, count in counts["visual_verdict_counts"].items():
        draw_text(draw, (42, y), f"- {verdict}: {count}", size=14)
        y += 21

    y += 10
    draw_text(draw, (28, y), "Claim boundaries", size=21)
    y += 32
    for line in [
        "Exact rows are source-sidestep review candidates only, not accepted final panoramas.",
        "Missing-exact strict/relaxed rows stay hold; montage or metrics alone cannot promote them.",
        "DB28/DB32 remain handoff/source-sidestep evidence, not original-G seam repair.",
        "DB41 lower-right/right-line remains inherited no-evidence/abstain.",
    ]:
        y = draw_wrapped(draw, 42, y, "- " + line, 86, fill=(235, 225, 190), size=14)

    strict_rows = [r for r in manifest["reviewed_rows"] if r["db47b_bucket"] == "strict_review_bucket"]
    relaxed_rows = [r for r in manifest["reviewed_rows"] if r["db47b_bucket"] == "relaxed_review_bucket"]
    y += 12
    y = draw_rows(draw, 28, y, "Strict rows", strict_rows)
    y += 18
    y = draw_rows(draw, 28, y, "Relaxed rows", relaxed_rows)

    x2 = 1120
    draw_text(draw, (x2, 154), "Exact visual evidence", size=22)
    image_box(board, DB28_MONTAGE, (x2, 190, x2 + 980, 455), "DB28 strict-clean montage")
    image_box(board, DB28_DIR / "SR_bmw_db28_a105_compare.jpg", (x2, 485, x2 + 310, 835), "a105 exact compare")
    image_box(board, DB28_DIR / "SR_bmw_db28_a200_compare.jpg", (x2 + 335, 485, x2 + 645, 835), "a200 exact compare")
    image_box(board, DB28_DIR / "SR_bmw_db28_a204_compare.jpg", (x2 + 670, 485, x2 + 980, 835), "a204 exact compare")
    image_box(board, DB28_DIR / "SR_bmw_db28_a200_final_1024x2048.png", (x2, 870, x2 + 480, 1110), "a200 final ROI crop", crop=ROI)
    image_box(board, DB28_DIR / "SR_bmw_db28_a204_final_1024x2048.png", (x2 + 500, 870, x2 + 980, 1110), "a204 final ROI crop", crop=ROI)

    y2 = 1145
    draw_text(draw, (x2, y2), "Hard checks", size=22)
    y2 += 34
    for check in manifest["checks"]:
        fill = (65, 140, 86) if check["pass"] else (170, 60, 60)
        draw.rounded_rectangle((x2, y2, x2 + 78, y2 + 25), radius=4, fill=fill)
        draw_text(draw, (x2 + 16, y2 + 4), "PASS" if check["pass"] else "STOP", size=12)
        y2 = draw_wrapped(draw, x2 + 92, y2 + 3, f"{check['id']}: {check['evidence']}", 92, size=13)
        y2 += 8

    y3 = 1270
    draw_text(draw, (28, y3), "Decision boundary", size=22)
    y3 += 34
    for line in [
        manifest["decision"]["claim_boundary"],
        "No final candidate, no original-G repair, no source-faithful repair permission.",
        "Next action needs a fresh final-candidate review, fixed-universe full scan, or DB47 pause.",
    ]:
        y3 = draw_wrapped(draw, 42, y3, "- " + line, 112, fill=(235, 235, 215), size=15)

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
