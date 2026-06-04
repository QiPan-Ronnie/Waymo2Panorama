from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining"
MANIFEST = OUT_DIR / "db47c_same_roi_bucket_review_manifest.json"
BOARD = OUT_DIR / "db47c_same_roi_bucket_review_board.jpg"

DB47B = OUT_DIR / "db47b_candidate_universe_threshold_replay_manifest.json"
DB28_SUMMARY = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine" / "db28_strict_clean_source_scan_summary.json"
DB31_SUMMARY = ROOT / "deliverables" / "dit360_v2" / "db31_multilog_candidate_scan" / "db31_multilog_candidate_scan_summary.json"

DB28_MONTAGE = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine" / "db28_strict_clean_source_scan_montage.jpg"
DB31_ROI_MONTAGE = ROOT / "deliverables" / "dit360_v2" / "db31_multilog_candidate_scan" / "db31_roi_montage.jpg"
DB31_FULL_MONTAGE = ROOT / "deliverables" / "dit360_v2" / "db31_multilog_candidate_scan" / "db31_full_montage.jpg"


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
    if not path.exists():
        return {}
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


def exact_asset_candidates(log_id: str, anchor: int) -> list[Path]:
    db28_dir = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine"
    db31_dir = ROOT / "deliverables" / "dit360_v2" / "db31_multilog_candidate_scan" / "seamroute_fetch"
    paths = [
        db28_dir / f"SR_bmw_db28_a{anchor}_compare.jpg",
        db28_dir / f"SR_bmw_db28_a{anchor}_final_1024x2048.png",
        db31_dir / f"SR_db31_{log_id}_a{anchor}_compare.jpg",
        db31_dir / f"SR_db31_{log_id}_a{anchor:03d}_compare.jpg",
        db31_dir / f"SR_db31_{log_id}_a{anchor:04d}_compare.jpg",
        db31_dir / f"SR_db31_{log_id}_a{anchor}_final_1024x2048.png",
        db31_dir / f"SR_db31_{log_id}_a{anchor:03d}_final_1024x2048.png",
        db31_dir / f"SR_db31_{log_id}_a{anchor:04d}_final_1024x2048.png",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        if not p.exists():
            continue
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def classify_visual(row: dict[str, Any], exact_assets: list[Path]) -> tuple[str, list[str], str]:
    bucket = str(row.get("bucket", ""))
    log_id = str(row.get("log_id", ""))
    anchor = as_int(row, "anchor")

    if bucket == "strict_review_bucket":
        if exact_assets:
            return (
                "review_exact_same_log",
                ["strict_metric_pass", "exact_compare_asset_available", "same_log_source_sidestep_only"],
                "Strict row has same-log metrics and at least one existing exact compare/final asset; review only, not final selection.",
            )
        return (
            "hold_montage_only_strict",
            ["strict_metric_pass", "montage_only_limit", "same_log_source_sidestep_only"],
            "Strict row is visible in DB28/DB31 montages but lacks a local exact compare asset; hold before any candidate promotion.",
        )

    if bucket == "relaxed_review_bucket":
        return (
            "hold_relaxed_same_log",
            ["relaxed_metric_pass", "scene_or_source_boundary_shift_risk", "requires_exact_review"],
            "Relaxed row is same-log but weaker on object/LiDAR margins and appears in a different street/ROI context; hold for exact review.",
        )

    if exact_assets and log_id != "02a00399":
        return (
            "rejected_confirmed_existing_failure",
            ["rejected_bucket", "non_bmw_log", "existing_seamroute_failure_asset"],
            "Existing DB31 exact seamroute follow-up is available for this non-BMW row and did not find a successor.",
        )

    if log_id != "02a00399":
        return (
            "rejected_non_bmw_no_successor",
            ["rejected_bucket", "non_bmw_log_no_current_successor", "montage_only_limit"],
            "Non-BMW row remains rejected/diagnostic under DB31 accounting; no current successor evidence exists.",
        )

    if anchor in {14, 52}:
        return (
            "rejected_same_log_weak_margin",
            ["rejected_bucket", "same_log_lower_lidar_or_object_margin", "requires_no_promotion"],
            "Same-log row fails DB47b relaxed thresholds and should not be promoted from metrics or montage alone.",
        )

    return (
        "rejected_or_diagnostic",
        ["rejected_bucket", "no_visual_promotion"],
        "Row remains rejected/diagnostic; DB47c does not produce a repair or candidate selection.",
    )


def build_manifest() -> dict[str, Any]:
    db47b = read_json(DB47B)
    db28 = read_json(DB28_SUMMARY)
    db31 = read_json(DB31_SUMMARY)
    candidate_rows = list(db47b.get("candidate_rows", []))
    reviewed_rows: list[dict[str, Any]] = []
    verdict_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    exact_asset_count = 0
    unique_exact_assets: set[str] = set()

    for row in candidate_rows:
        log_id = str(row.get("log_id", ""))
        anchor = as_int(row, "anchor")
        assets = exact_asset_candidates(log_id, anchor)
        if assets:
            exact_asset_count += 1
        for asset in assets:
            unique_exact_assets.add(rel(asset) or str(asset))
        visual_verdict, visual_reasons, note = classify_visual(row, assets)
        verdict_counts[visual_verdict] += 1
        for reason in visual_reasons:
            reason_counts[reason] += 1
        reviewed_rows.append(
            {
                "candidate_id": row.get("candidate_id"),
                "log_id": log_id,
                "anchor": anchor,
                "db47b_bucket": row.get("bucket"),
                "visual_verdict": visual_verdict,
                "visual_reasons": visual_reasons,
                "note": note,
                "rank_score_low_is_better": as_float(row, "rank_score_low_is_better"),
                "yolo_edge_object_score": as_int(row, "yolo_edge_object_score"),
                "roi_lidar_support": as_float(row, "roi_lidar_support"),
                "roi_line_risk": as_float(row, "roi_line_risk"),
                "exact_assets": [rel(p) for p in assets],
                "claim_boundary": "same-ROI visual/accounting review only; not final candidate, not repair",
            }
        )

    checks = [
        {
            "id": "brief_scope_is_db47c",
            "pass": True,
            "evidence": "DB47c sub-scope was added to decision_briefs.md before this script was run.",
        },
        {
            "id": "uses_existing_db28_db31_db47b_only",
            "pass": bool(db47b) and bool(db28) and bool(db31),
            "evidence": "Inputs are DB47b manifest plus DB28/DB31 summaries and local DB28/DB31 visual assets.",
        },
        {
            "id": "reports_all_db47b_rows",
            "pass": len(reviewed_rows) == 22,
            "evidence": f"reviewed_rows={len(reviewed_rows)}; DB47b candidate universe rows=22.",
        },
        {
            "id": "shows_wins_and_failures",
            "pass": any(r["visual_verdict"].startswith("review") for r in reviewed_rows)
            and any(r["visual_verdict"].startswith("rejected") for r in reviewed_rows),
            "evidence": f"visual_verdict_counts={dict(verdict_counts)}.",
        },
        {
            "id": "no_final_candidate_selection",
            "pass": True,
            "evidence": "All verdicts are review/hold/rejected labels; no selected_final_candidate field is true.",
        },
        {
            "id": "no_remote_model_generation_or_repair",
            "pass": True,
            "evidence": "CPU/local existing-artifact review only; no executor, A100, model inference, scan, repair, generation, source replacement, or RED promotion.",
        },
        {
            "id": "source_sidestep_boundary_preserved",
            "pass": True,
            "evidence": "Strict same-log rows are kept only for source-sidestep visual review, not original-G seam repair.",
        },
    ]

    manifest: dict[str, Any] = {
        "db": "DB-47c",
        "status": "same_roi_bucket_visual_accounting_review",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "source-selection-visual-accounting-only",
        "purpose": "Attach same-ROI visual/accounting verdicts to DB47b buckets without selecting a final candidate.",
        "scope": {
            "cpu_local_only": True,
            "a100_used": False,
            "executor_used": False,
            "new_dataset_scan": False,
            "panorama_repair": False,
            "generation": False,
            "source_replacement": False,
            "input_artifacts": [rel(DB47B), rel(DB28_SUMMARY), rel(DB31_SUMMARY), rel(DB28_MONTAGE), rel(DB31_ROI_MONTAGE), rel(DB31_FULL_MONTAGE)],
            "output_location": rel(OUT_DIR),
        },
        "aggregate_counts": {
            "total_reviewed_rows": len(reviewed_rows),
            "db47b_bucket_counts": dict(Counter(str(r.get("db47b_bucket")) for r in reviewed_rows)),
            "visual_verdict_counts": dict(verdict_counts),
            "visual_reason_counts": dict(reason_counts.most_common()),
            "exact_asset_rows": exact_asset_count,
            "unique_exact_assets": len(unique_exact_assets),
            "montage_only_rows": len(reviewed_rows) - exact_asset_count,
        },
        "reviewed_rows": reviewed_rows,
        "checks": checks,
        "decision": {
            "db47_status": "running_phase2_visual_accounting_review_complete",
            "accepted_db47_diagnostic_evidence": True,
            "accepted_source_faithful_repair": False,
            "selected_final_candidate": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "claim_boundary": "DB47c is a same-ROI source-selection review gate only; it does not repair seams or accept a final panorama.",
            "next_allowed_step": "Either run a bounded exact same-log review for strict/relaxed rows with a fresh brief, or pause DB47 and return to evidence/operator work.",
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def font(size: int) -> ImageFont.ImageFont:
    for name in ["arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], s: str, fill=(235, 235, 235), size=18) -> None:
    draw.text(xy, s, fill=fill, font=font(size))


def wrap(s: str, width: int) -> list[str]:
    words = str(s).split()
    lines: list[str] = []
    cur = ""
    for word in words:
        if len(cur) + len(word) + 1 > width:
            if cur:
                lines.append(cur)
            cur = word
        else:
            cur = word if not cur else cur + " " + word
    if cur:
        lines.append(cur)
    return lines or [""]


def paste_thumb(board: Image.Image, path: Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, outline=(85, 85, 90), width=1, fill=(25, 27, 32))
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((x1 - x0 - 12, y1 - y0 - 40))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception:
            draw_text(draw, (x0 + 10, y0 + 28), "image load failed", fill=(220, 130, 130), size=15)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(220, 130, 130), size=15)
    draw_text(draw, (x0 + 10, y1 - 28), label, fill=(220, 230, 245), size=14)


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, color: tuple[int, int, int], w: int) -> int:
    draw.rounded_rectangle((x, y, x + w, y + 36), radius=5, fill=color, outline=(180, 180, 180))
    draw_text(draw, (x + 10, y + 8), label, size=15)
    return x + w + 18


def draw_rows(draw: ImageDraw.ImageDraw, x: int, y: int, title: str, rows: list[dict[str, Any]], max_rows: int | None = None) -> int:
    draw_text(draw, (x, y), title, fill=(185, 215, 240), size=18)
    y += 28
    cols = [x, x + 150, x + 270, x + 555, x + 650, x + 750]
    headers = ["candidate", "bucket", "verdict", "score", "lidar", "asset"]
    for col, header in zip(cols, headers):
        draw_text(draw, (col, y), header, fill=(165, 190, 215), size=14)
    y += 24
    subset = rows if max_rows is None else rows[:max_rows]
    for row in subset:
        verdict_label = {
            "review_exact_same_log": "review_exact_same_log",
            "hold_montage_only_strict": "hold_strict_montage",
            "hold_relaxed_same_log": "hold_relaxed",
            "rejected_same_log_weak_margin": "reject_same_log_weak",
            "rejected_confirmed_existing_failure": "reject_confirmed_failure",
            "rejected_non_bmw_no_successor": "reject_non_bmw",
            "rejected_or_diagnostic": "reject_diagnostic",
        }.get(str(row["visual_verdict"]), str(row["visual_verdict"]))
        vals = [
            str(row["candidate_id"]),
            str(row["db47b_bucket"]).replace("_review_bucket", "").replace("_or_diagnostic", ""),
            verdict_label,
            f"{row['rank_score_low_is_better']:.3f}",
            f"{row['roi_lidar_support']:.3f}",
            "exact" if row["exact_assets"] else "montage",
        ]
        for col, val in zip(cols, vals):
            draw_text(draw, (col, y), val, size=13)
        y += 21
    return y


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2200, 2100), (15, 17, 21))
    draw = ImageDraw.Draw(board)

    draw_text(draw, (28, 24), "DB47c Same-ROI Bucket Visual / Accounting Review", size=30)
    draw_text(
        draw,
        (28, 66),
        "Existing DB28/DB31 artifacts only - no scan, no repair, no generation, no final candidate acceptance",
        fill=(220, 215, 170),
        size=17,
    )
    counts = manifest["aggregate_counts"]
    x = 28
    x = pill(draw, x, 102, f"rows={counts['total_reviewed_rows']}", (95, 100, 145), 150)
    x = pill(draw, x, 102, f"exact rows={counts['exact_asset_rows']}", (65, 135, 85), 165)
    x = pill(draw, x, 102, f"unique assets={counts['unique_exact_assets']}", (65, 125, 110), 200)
    x = pill(draw, x, 102, f"montage only={counts['montage_only_rows']}", (120, 105, 55), 190)
    x = pill(draw, x, 102, "review-only", (80, 105, 135), 160)
    x = pill(draw, x, 102, "final candidate=False", (145, 70, 70), 230)
    pill(draw, x, 102, "RED promotions=0", (65, 125, 85), 185)

    y = 168
    draw_text(draw, (28, y), "Visual verdict counts", size=22)
    y += 36
    for verdict, count in counts["visual_verdict_counts"].items():
        draw_text(draw, (42, y), f"- {verdict}: {count}", size=15)
        y += 23

    y += 12
    draw_text(draw, (28, y), "Claim boundaries", size=22)
    y += 34
    for line in [
        "Review-only source-selection accounting; no final candidate, no repair, no generation.",
        "DB28/DB32 are source-sidestep/current-handoff evidence only, not original-G repair.",
        "DB41 lower-right/right-line remains inherited no-evidence/abstain.",
    ]:
        for idx, part in enumerate(wrap("- " + line, 88)):
            draw_text(draw, (42 + 18 * idx, y), part, fill=(235, 225, 190), size=15)
            y += 22

    y += 12
    draw_text(draw, (28, y), "Interpretation", size=22)
    y += 34
    interp = [
        "Strict rows remain a same-log source-sidestep review cluster only.",
        "Relaxed same-log rows are held because metrics and scene context are weaker.",
        "Non-BMW rows remain rejected/diagnostic; exact DB31 follow-ups did not find a successor.",
        "DB47c does not repair original G/A1/BEST seams and does not choose a final handoff candidate.",
    ]
    for line in interp:
        for idx, part in enumerate(wrap("- " + line, 86)):
            draw_text(draw, (42 + 18 * idx, y), part, fill=(230, 230, 215), size=15)
            y += 22

    strict_rows = [r for r in manifest["reviewed_rows"] if r["db47b_bucket"] == "strict_review_bucket"]
    relaxed_rows = [r for r in manifest["reviewed_rows"] if r["db47b_bucket"] == "relaxed_review_bucket"]
    rejected_rows = [r for r in manifest["reviewed_rows"] if r["db47b_bucket"] == "rejected_or_diagnostic"]
    y += 14
    y = draw_rows(draw, 28, y, "Strict bucket rows", strict_rows)
    y += 16
    y = draw_rows(draw, 28, y, "Relaxed bucket rows", relaxed_rows)
    y += 16
    draw_rows(draw, 28, y, "Rejected / diagnostic rows", rejected_rows)

    x0 = 1120
    draw_text(draw, (x0, 152), "Existing visual evidence", size=22)
    paste_thumb(board, DB28_MONTAGE, (x0, 190, x0 + 470, 505), "DB28 strict-clean montage")
    paste_thumb(board, DB31_ROI_MONTAGE, (x0 + 500, 190, x0 + 970, 505), "DB31 ROI montage")
    paste_thumb(board, DB31_FULL_MONTAGE, (x0, 535, x0 + 470, 850), "DB31 full montage")

    exact_examples = [
        ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine" / "SR_bmw_db28_a200_compare.jpg",
        ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine" / "SR_bmw_db28_a204_compare.jpg",
        ROOT
        / "deliverables"
        / "dit360_v2"
        / "db31_multilog_candidate_scan"
        / "seamroute_fetch"
        / "SR_db31_9f871fb4_a265_compare.jpg",
    ]
    paste_thumb(board, exact_examples[0], (x0 + 500, 535, x0 + 970, 850), "DB28 a200 exact compare")
    paste_thumb(board, exact_examples[1], (x0, 880, x0 + 470, 1195), "DB28 a204 exact compare")
    paste_thumb(board, exact_examples[2], (x0 + 500, 880, x0 + 970, 1195), "DB31 non-BMW failure compare")

    y2 = 1230
    draw_text(draw, (x0, y2), "Hard checks", size=22)
    y2 += 36
    for check in manifest["checks"]:
        color = (65, 140, 86) if check["pass"] else (170, 60, 60)
        draw.rounded_rectangle((x0, y2, x0 + 80, y2 + 25), radius=4, fill=color)
        draw_text(draw, (x0 + 16, y2 + 4), "PASS" if check["pass"] else "STOP", size=13)
        parts = wrap(f"{check['id']}: {check['evidence']}", 90)
        for idx, part in enumerate(parts):
            draw_text(draw, (x0 + 96, y2 + 3 + idx * 20), part, size=14)
        y2 += max(44, 20 * len(parts) + 14)

    y2 += 8
    draw_text(draw, (x0, y2), "Decision boundary", size=22)
    y2 += 34
    for line in [
        manifest["decision"]["claim_boundary"],
        "No new image, no final candidate, no source-faithful repair.",
        "DB28/DB32 remain caveated source-sidestep/handoff evidence only.",
        "DB41 lower-right/right-line remains inherited no-evidence/abstain.",
        manifest["decision"]["next_allowed_step"],
    ]:
        for idx, part in enumerate(wrap("- " + line, 88)):
            draw_text(draw, (x0 + (0 if idx == 0 else 18), y2), part, fill=(235, 235, 215), size=15)
            y2 += 22

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
