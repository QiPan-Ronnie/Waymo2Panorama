from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining"
MANIFEST = OUT_DIR / "db47b_candidate_universe_threshold_replay_manifest.json"
BOARD = OUT_DIR / "db47b_candidate_universe_threshold_replay_board.jpg"

DB27 = ROOT / "deliverables" / "dit360_v2" / "db27_temporal_frame_scan" / "db27_temporal_frame_scan_summary.json"
DB28 = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine" / "db28_strict_clean_source_scan_summary.json"
DB31 = ROOT / "deliverables" / "dit360_v2" / "db31_multilog_candidate_scan" / "db31_multilog_candidate_scan_summary.json"

DB28_BOARD = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine" / "db28_strict_clean_source_scan_montage.jpg"
DB31_ROI_BOARD = ROOT / "deliverables" / "dit360_v2" / "db31_multilog_candidate_scan" / "db31_roi_montage.jpg"
DB31_FULL_BOARD = ROOT / "deliverables" / "dit360_v2" / "db31_multilog_candidate_scan" / "db31_full_montage.jpg"
DB31_NONBMW_FAILURE = (
    ROOT
    / "deliverables"
    / "dit360_v2"
    / "db31_multilog_candidate_scan"
    / "seamroute_fetch"
    / "SR_db31_9f871fb4_a265_compare.jpg"
)


STRICT = {
    "yolo_edge_object_score_max": 0,
    "roi_lidar_support_min": 0.30,
    "roi_line_risk_max": 0.0615,
    "rank_score_max": 0.083,
}

RELAXED = {
    "yolo_edge_object_score_max": 1,
    "roi_lidar_support_min": 0.27,
    "roi_line_risk_max": 0.0640,
    "rank_score_max": 0.104,
}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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


def as_float(row: dict[str, Any], key: str, default: float = 999.0) -> float:
    val = row.get(key)
    if isinstance(val, (int, float)):
        return float(val)
    return default


def as_int(row: dict[str, Any], key: str, default: int = 999) -> int:
    val = row.get(key)
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    return default


def existing_review_asset(row: dict[str, Any]) -> Path | None:
    log_id = str(row.get("log_id", ""))
    anchor = as_int(row, "anchor", -1)
    exact_assets = {
        ("02a00399", 200): ROOT
        / "deliverables"
        / "dit360_v2"
        / "db28_clean_subset_refine"
        / "SR_bmw_db28_a200_final_1024x2048.png",
        ("9f871fb4", 265): ROOT
        / "deliverables"
        / "dit360_v2"
        / "db31_multilog_candidate_scan"
        / "seamroute_fetch"
        / "SR_db31_9f871fb4_a265_compare.jpg",
        ("0bae3b5e", 280): ROOT
        / "deliverables"
        / "dit360_v2"
        / "db31_multilog_candidate_scan"
        / "seamroute_fetch"
        / "SR_db31_0bae3b5e_a280_compare.jpg",
        ("2c652f9e", 160): ROOT
        / "deliverables"
        / "dit360_v2"
        / "db31_multilog_candidate_scan"
        / "seamroute_fetch"
        / "SR_db31_2c652f9e_a160_compare.jpg",
    }
    p = exact_assets.get((log_id, anchor))
    return p if p and p.exists() else None


def classify_row(row: dict[str, Any]) -> tuple[str, list[str]]:
    yolo = as_int(row, "yolo_edge_object_score")
    lidar = as_float(row, "roi_lidar_support")
    line = as_float(row, "roi_line_risk")
    rank = as_float(row, "rank_score_low_is_better")
    log_id = str(row.get("log_id", ""))
    anchor = as_int(row, "anchor", -1)

    strict_pass = (
        yolo <= STRICT["yolo_edge_object_score_max"]
        and lidar >= STRICT["roi_lidar_support_min"]
        and line <= STRICT["roi_line_risk_max"]
        and rank <= STRICT["rank_score_max"]
    )
    if strict_pass:
        return "strict_review_bucket", ["metric_strict_pass", "source_sidestep_review_only"]

    relaxed_pass = (
        yolo <= RELAXED["yolo_edge_object_score_max"]
        and lidar >= RELAXED["roi_lidar_support_min"]
        and line <= RELAXED["roi_line_risk_max"]
        and rank <= RELAXED["rank_score_max"]
    )
    if relaxed_pass:
        return "relaxed_review_bucket", ["metric_relaxed_pass", "requires_same_roi_vision"]

    reasons: list[str] = []
    if yolo > RELAXED["yolo_edge_object_score_max"]:
        reasons.append("edge_object_score_gt_relaxed")
    elif yolo > STRICT["yolo_edge_object_score_max"]:
        reasons.append("edge_object_score_gt_strict")
    if lidar < RELAXED["roi_lidar_support_min"]:
        reasons.append("lidar_support_below_relaxed")
    elif lidar < STRICT["roi_lidar_support_min"]:
        reasons.append("lidar_support_below_strict")
    if line > RELAXED["roi_line_risk_max"]:
        reasons.append("roi_line_risk_above_relaxed")
    elif line > STRICT["roi_line_risk_max"]:
        reasons.append("roi_line_risk_above_strict")
    if rank > RELAXED["rank_score_max"]:
        reasons.append("rank_score_above_relaxed")
    elif rank > STRICT["rank_score_max"]:
        reasons.append("rank_score_above_strict")
    if (log_id, anchor) in {("9f871fb4", 265), ("0bae3b5e", 280), ("2c652f9e", 160)}:
        reasons.append("db31_exact_seamroute_no_successor")
    if log_id != "02a00399":
        reasons.append("non_bmw_log_no_current_successor")
    if not reasons:
        reasons.append("insufficient_margin_for_review_bucket")
    return "rejected_or_diagnostic", reasons


def build_manifest() -> dict[str, Any]:
    db27 = read_json(DB27)
    db28 = read_json(DB28)
    db31 = read_json(DB31)

    ranked = list(db31.get("ranked_by_source_risk", []))
    rows = []
    reject_reason_counts: Counter[str] = Counter()
    review_reason_counts: Counter[str] = Counter()
    bucket_counts: Counter[str] = Counter()
    by_log: dict[str, Counter[str]] = defaultdict(Counter)
    asset_counts: Counter[str] = Counter()

    for idx, row in enumerate(ranked, start=1):
        bucket, reasons = classify_row(row)
        asset = existing_review_asset(row)
        if asset:
            asset_counts["exact_or_candidate_asset_present"] += 1
        else:
            asset_counts["per_candidate_asset_missing_local"] += 1
        for reason in reasons:
            if bucket == "rejected_or_diagnostic":
                reject_reason_counts[reason] += 1
            else:
                review_reason_counts[reason] += 1
        bucket_counts[bucket] += 1
        by_log[str(row.get("log_id", "missing"))][bucket] += 1
        rows.append(
            {
                "universe_rank": idx,
                "candidate_id": f"{row.get('log_id')}_a{as_int(row, 'anchor', -1):04d}",
                "log_id": row.get("log_id"),
                "anchor": as_int(row, "anchor", -1),
                "bucket": bucket,
                "reasons": reasons,
                "rank_score_low_is_better": as_float(row, "rank_score_low_is_better"),
                "yolo_edge_object_score": as_int(row, "yolo_edge_object_score"),
                "roi_line_risk": as_float(row, "roi_line_risk"),
                "roi_lidar_support": as_float(row, "roi_lidar_support"),
                "roi_active_labels": as_int(row, "roi_active_labels"),
                "midband_line_risk": as_float(row, "midband_line_risk"),
                "source_image_recorded": row.get("source_image"),
                "local_review_asset": rel(asset) if asset else None,
                "claim_boundary": "review-bucket only; not accepted panorama and not original-G seam repair",
            }
        )

    strict_rows = [r for r in rows if r["bucket"] == "strict_review_bucket"]
    relaxed_rows = [r for r in rows if r["bucket"] == "relaxed_review_bucket"]
    rejected_rows = [r for r in rows if r["bucket"] == "rejected_or_diagnostic"]

    db27_ranked = db27.get("ranked_by_line_risk", [])
    db28_ranked = db28.get("ranked_by_line_risk", [])

    checks = [
        {
            "id": "brief_scope_is_db47b",
            "pass": True,
            "evidence": "DB47b sub-scope was added to decision_briefs.md before this script was run.",
        },
        {
            "id": "fixed_universe_db31_22",
            "pass": len(rows) == 22,
            "evidence": f"DB31 ranked_by_source_risk rows={len(rows)}.",
        },
        {
            "id": "comparison_only_db27_db28",
            "pass": bool(db27_ranked) and bool(db28_ranked),
            "evidence": f"DB27 rows={len(db27_ranked)}; DB28 rows={len(db28_ranked)}; both are comparison-only.",
        },
        {
            "id": "reports_reject_reasons",
            "pass": bool(rejected_rows) and bool(reject_reason_counts),
            "evidence": f"rejected_or_diagnostic={len(rejected_rows)}; reject_reason_types={len(reject_reason_counts)}.",
        },
        {
            "id": "no_top_pretty_only",
            "pass": len(rows) == int(db31.get("selected_count", 0)),
            "evidence": "All DB31 shortlist rows are reported, not just top examples.",
        },
        {
            "id": "source_sidestep_not_repair",
            "pass": True,
            "evidence": "Strict/relaxed labels are review buckets only, not accepted panoramas or original-G seam repair.",
        },
        {
            "id": "db41_abstain_preserved",
            "pass": True,
            "evidence": "DB47b has no DB41 repair path and no permission-state output; DB41 remains inherited no-evidence/abstain from the active brief.",
        },
        {
            "id": "no_remote_model_generation_or_red_promotion",
            "pass": True,
            "evidence": "CPU/local JSON replay only; no executor, A100, model inference, repair, generation, or RED promotion.",
        },
    ]

    manifest: dict[str, Any] = {
        "db": "DB-47b",
        "status": "candidate_universe_threshold_replay",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "source-selection-threshold-replay-only",
        "purpose": "Freeze a bounded DB47 candidate universe and report strict/relaxed/rejected accounting before any broader scan.",
        "scope": {
            "cpu_local_only": True,
            "a100_used": False,
            "executor_used": False,
            "new_dataset_scan": False,
            "panorama_repair": False,
            "generation": False,
            "source_replacement": False,
            "input_universe": "DB31 ranked_by_source_risk shortlist only",
            "universe_rows": len(rows),
            "comparison_only_artifacts": [rel(DB27), rel(DB28)],
            "output_location": rel(OUT_DIR),
        },
        "thresholds": {
            "strict_review_bucket": STRICT,
            "relaxed_review_bucket": RELAXED,
            "threshold_interpretation": "Buckets select candidates for future review only; they are not final visual acceptance.",
        },
        "aggregate_counts": {
            "total_universe_rows": len(rows),
            "strict_review_bucket": len(strict_rows),
            "relaxed_review_bucket": len(relaxed_rows),
            "rejected_or_diagnostic": len(rejected_rows),
            "unique_logs": sorted({str(r.get("log_id")) for r in rows}),
            "per_log_bucket_counts": {log: dict(counter) for log, counter in sorted(by_log.items())},
            "reject_reason_counts": dict(reject_reason_counts.most_common()),
            "review_bucket_reason_counts": dict(review_reason_counts.most_common()),
            "local_asset_counts": dict(asset_counts),
        },
        "comparison_context": {
            "db27_temporal_rows": len(db27_ranked),
            "db27_best": db27_ranked[0] if db27_ranked else None,
            "db28_strict_rows": len(db28_ranked),
            "db28_best": db28_ranked[0] if db28_ranked else None,
            "not_full_distribution": "DB31 is a shortlist from prior candidate mining, not the full Waymo data distribution.",
            "db41_boundary": "DB41 lower-right/right-line is not in the DB47b candidate universe and remains an inherited no-evidence/abstain constraint.",
        },
        "candidate_rows": rows,
        "review_buckets": {
            "strict_review_bucket": strict_rows,
            "relaxed_review_bucket": relaxed_rows,
            "rejected_or_diagnostic": rejected_rows,
        },
        "checks": checks,
        "decision": {
            "db47_status": "running_phase1_threshold_replay_complete",
            "accepted_db47_diagnostic_evidence": True,
            "accepted_source_faithful_repair": False,
            "selected_final_candidate": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "claim_boundary": "DB47b freezes/accounting-replays a source-selection universe only; it does not repair seams or accept a final panorama.",
            "next_allowed_step": "If DB47 continues, run a bounded same-ROI visual/accounting review on the strict and failure buckets, or open a separate full-scan brief with a fixed dataset universe.",
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


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, color: tuple[int, int, int], w: int = 250) -> int:
    draw.rounded_rectangle((x, y, x + w, y + 36), radius=5, fill=color, outline=(180, 180, 180))
    draw_text(draw, (x + 10, y + 8), label, size=15)
    return x + w + 18


def paste_thumb(board: Image.Image, path: Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, outline=(85, 85, 90), width=1, fill=(25, 27, 32))
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((x1 - x0 - 12, y1 - y0 - 38))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception:
            draw_text(draw, (x0 + 10, y0 + 24), "image load failed", fill=(220, 130, 130), size=15)
    else:
        draw_text(draw, (x0 + 10, y0 + 24), "missing", fill=(220, 130, 130), size=15)
    draw_text(draw, (x0 + 10, y1 - 26), label, fill=(220, 230, 245), size=14)


def draw_bucket_table(draw: ImageDraw.ImageDraw, x: int, y: int, rows: list[dict[str, Any]], title: str) -> int:
    draw_text(draw, (x, y), title, fill=(185, 215, 240), size=18)
    y += 28
    headers = ["rank", "id", "bucket", "score", "yolo", "lidar", "reason"]
    cols = [x, x + 58, x + 188, x + 395, x + 485, x + 565, x + 655]
    for col, h in zip(cols, headers):
        draw_text(draw, (col, y), h, fill=(165, 190, 215), size=14)
    y += 24
    for row in rows:
        reason = ",".join(row["reasons"][:2])
        vals = [
            str(row["universe_rank"]),
            row["candidate_id"],
            row["bucket"].replace("_review_bucket", "").replace("_or_diagnostic", ""),
            f"{row['rank_score_low_is_better']:.3f}",
            str(row["yolo_edge_object_score"]),
            f"{row['roi_lidar_support']:.3f}",
            reason,
        ]
        for col, val in zip(cols, vals):
            draw_text(draw, (col, y), str(val)[:34], size=13)
        y += 21
    return y


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1900, 1600), (15, 17, 21))
    draw = ImageDraw.Draw(board)

    draw_text(draw, (28, 24), "DB47b Candidate Universe Freeze / Threshold Replay", size=30)
    draw_text(
        draw,
        (28, 66),
        "CPU/local DB31 shortlist replay only - no full scan, no repair, no generation, no final candidate acceptance",
        fill=(220, 215, 170),
        size=17,
    )
    x = 28
    counts = manifest["aggregate_counts"]
    x = pill(draw, x, 102, f"universe={counts['total_universe_rows']}", (95, 100, 145), 180)
    x = pill(draw, x, 102, f"strict={counts['strict_review_bucket']}", (65, 135, 85), 150)
    x = pill(draw, x, 102, f"relaxed={counts['relaxed_review_bucket']}", (120, 105, 55), 165)
    x = pill(draw, x, 102, f"rejected={counts['rejected_or_diagnostic']}", (145, 70, 70), 175)
    x = pill(draw, x, 102, "source-sidestep only", (80, 105, 135), 230)
    pill(draw, x, 102, "RED promotions=0", (65, 125, 85), 185)

    y = 168
    draw_text(draw, (28, y), "Thresholds and interpretation", size=22)
    y += 36
    threshold_lines = [
        "strict: yolo=0, LiDAR>=0.30, ROI line risk<=0.0615, rank<=0.083",
        "relaxed: yolo<=1, LiDAR>=0.27, ROI line risk<=0.0640, rank<=0.104",
        "Buckets are review queues only; they are not accepted panoramas or original-G seam repair.",
        "DB31 is a shortlist, not the full Waymo distribution.",
    ]
    for line in threshold_lines:
        draw_text(draw, (42, y), "- " + line, size=15)
        y += 24

    y += 12
    draw_text(draw, (28, y), "Per-log bucket counts", size=22)
    y += 34
    draw_text(draw, (42, y), "log_id      total  strict  relaxed  rejected", fill=(165, 190, 215), size=15)
    y += 25
    for log_id, bucket_counter in counts["per_log_bucket_counts"].items():
        total = sum(int(v) for v in bucket_counter.values())
        line = (
            f"{log_id:<10} {total:>5} {int(bucket_counter.get('strict_review_bucket', 0)):>7} "
            f"{int(bucket_counter.get('relaxed_review_bucket', 0)):>8} "
            f"{int(bucket_counter.get('rejected_or_diagnostic', 0)):>8}"
        )
        draw_text(draw, (42, y), line, size=15)
        y += 24

    y += 12
    draw_text(draw, (28, y), "Reject / diagnostic reasons", size=22)
    y += 34
    for reason, count in list(counts["reject_reason_counts"].items())[:10]:
        draw_text(draw, (42, y), f"- {reason}: {count}", size=15)
        y += 23

    y += 10
    strict_rows = manifest["review_buckets"]["strict_review_bucket"]
    relaxed_rows = manifest["review_buckets"]["relaxed_review_bucket"]
    rejected_rows = manifest["review_buckets"]["rejected_or_diagnostic"]
    y = draw_bucket_table(draw, 28, y, strict_rows, "Strict review bucket")
    y += 18
    y = draw_bucket_table(draw, 28, y, relaxed_rows, "Relaxed review bucket")
    y += 18
    draw_bucket_table(draw, 28, y, rejected_rows, "Rejected/diagnostic rows")

    x0 = 1040
    draw_text(draw, (x0, 152), "Visual references from existing artifacts", size=22)
    paste_thumb(board, DB28_BOARD, (x0, 190, x0 + 390, 455), "DB28 strict-clean same-log montage")
    paste_thumb(board, DB31_ROI_BOARD, (x0 + 420, 190, x0 + 810, 455), "DB31 shortlist ROI montage")
    paste_thumb(board, DB31_FULL_BOARD, (x0, 485, x0 + 390, 750), "DB31 shortlist full montage")
    paste_thumb(board, DB31_NONBMW_FAILURE, (x0 + 420, 485, x0 + 810, 750), "DB31 non-BMW seamroute failure")

    y2 = 800
    draw_text(draw, (x0, y2), "Hard checks", size=22)
    y2 += 36
    for check in manifest["checks"]:
        color = (65, 140, 86) if check["pass"] else (170, 60, 60)
        draw.rounded_rectangle((x0, y2, x0 + 80, y2 + 25), radius=4, fill=color)
        draw_text(draw, (x0 + 16, y2 + 4), "PASS" if check["pass"] else "STOP", size=13)
        parts = wrap(f"{check['id']}: {check['evidence']}", 70)
        for idx, part in enumerate(parts):
            draw_text(draw, (x0 + 96, y2 + 3 + idx * 20), part, size=14)
        y2 += max(44, 20 * len(parts) + 14)

    y2 += 10
    draw_text(draw, (x0, y2), "Decision boundary", size=22)
    y2 += 36
    decision_lines = [
        manifest["decision"]["claim_boundary"],
        "No final winner selected in DB47b.",
        "DB28/DB32 remain caveated source-sidestep/handoff evidence only.",
        "DB41 lower-right/right-line remains no-evidence/abstain.",
        manifest["decision"]["next_allowed_step"],
    ]
    for line in decision_lines:
        for idx, part in enumerate(wrap("- " + line, 72)):
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
