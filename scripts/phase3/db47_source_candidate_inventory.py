from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining"
MANIFEST = OUT_DIR / "db47a_source_candidate_inventory_manifest.json"
BOARD = OUT_DIR / "db47a_source_candidate_inventory_board.jpg"

DB27 = ROOT / "deliverables" / "dit360_v2" / "db27_temporal_frame_scan" / "db27_temporal_frame_scan_summary.json"
DB28 = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine" / "db28_strict_clean_source_scan_summary.json"
DB31 = ROOT / "deliverables" / "dit360_v2" / "db31_multilog_candidate_scan" / "db31_multilog_candidate_scan_summary.json"
DB34 = ROOT / "deliverables" / "dit360_v2" / "db34_current_best_qa" / "db34_current_best_manifest.json"
DB38 = ROOT / "deliverables" / "dit360_v2" / "db38_bosch_handoff" / "db38_bosch_handoff_manifest.json"
DB42 = ROOT / "deliverables" / "dit360_v2" / "db42_seam_decision_handoff" / "db42_seam_decision_handoff_manifest.json"
DB43 = ROOT / "deliverables" / "dit360_v2" / "db43_source_faithfulness_gate" / "db43_source_faithfulness_gate_manifest.json"


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


def exists_rel(path: str | None) -> bool:
    if not path:
        return False
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    return p.exists()


def local_path(path: str | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    return p if p.exists() else None


def top_by(items: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    vals = [x for x in items if isinstance(x.get(key), (int, float))]
    if not vals:
        return None
    return min(vals, key=lambda x: float(x[key]))


def summarize_candidate_scan(name: str, data: dict[str, Any]) -> dict[str, Any]:
    summaries = data.get("summaries", [])
    ranked = data.get("ranked_by_line_risk") or data.get("ranked_by_source_risk") or []
    top = None
    if ranked:
        top = ranked[0]
    elif summaries:
        top = top_by(summaries, "line_risk_score_low_is_better")
    unique_logs = sorted({str(x.get("log_id") or str(x.get("uuid", ""))[:8]) for x in ranked if x.get("log_id") or x.get("uuid")})
    return {
        "name": name,
        "present": bool(data),
        "anchors_or_candidates": len(summaries) or int(data.get("selected_count") or len(ranked) or 0),
        "ranked_count": len(ranked),
        "selected_count": data.get("selected_count"),
        "unique_logs": unique_logs,
        "roi": data.get("roi") or data.get("selection", {}).get("roi"),
        "top_candidate": top,
        "ranking_note": data.get("ranking_note") or "Ranking aids only; visual review remains authoritative.",
    }


def classify_from_db43(db43: dict[str, Any]) -> dict[str, Any]:
    cases = db43.get("cases", [])
    by_claim = Counter(str(c.get("claim_label", "missing")) for c in cases)
    reason_counts: Counter[str] = Counter()
    source_selection_cases = []
    no_evidence_cases = []
    reject_cases = []
    for case in cases:
        for reason in case.get("reason_codes", []):
            reason_counts[str(reason)] += 1
        claim = str(case.get("claim_label", ""))
        reasons = set(case.get("reason_codes", []))
        if claim in {"source-sidestep", "caveated-handoff"} or "source_sidestep" in reasons:
            source_selection_cases.append(
                {
                    "case_id": case.get("case_id"),
                    "title": case.get("title"),
                    "claim_label": claim,
                    "evidence_state": case.get("evidence_state"),
                    "reason_codes": case.get("reason_codes", []),
                    "vision_verdict": case.get("vision_verdict"),
                    "artifact": case.get("artifact"),
                }
            )
        if claim in {"abstain"} or "no_source_evidence" in reasons:
            no_evidence_cases.append(case.get("case_id"))
        if claim in {"reject", "diagnostic"}:
            reject_cases.append(case.get("case_id"))
    return {
        "case_count": len(cases),
        "claim_label_counts": dict(by_claim),
        "top_reason_codes": dict(reason_counts.most_common(16)),
        "source_selection_cases": source_selection_cases,
        "no_evidence_case_ids": no_evidence_cases,
        "reject_or_diagnostic_case_ids": reject_cases,
    }


def build_manifest() -> dict[str, Any]:
    db27 = read_json(DB27)
    db28 = read_json(DB28)
    db31 = read_json(DB31)
    db34 = read_json(DB34)
    db38 = read_json(DB38)
    db42 = read_json(DB42)
    db43 = read_json(DB43)

    scan_summaries = [
        summarize_candidate_scan("DB27 temporal nearby-frame scan", db27),
        summarize_candidate_scan("DB28 strict-clean source scan", db28),
        summarize_candidate_scan("DB31 multilog candidate scan", db31),
    ]
    total_candidate_records = sum(int(x.get("anchors_or_candidates") or 0) for x in scan_summaries)
    db31_ranked = db31.get("ranked_by_source_risk", [])
    db31_unique_logs = sorted({str(x.get("log_id")) for x in db31_ranked if x.get("log_id")})

    current_handoff = db42.get("accepted_current_handoff") or db34.get("current_best")
    source_base = db34.get("source_base")
    db38_decisions = db38.get("candidate_decisions", [])
    accepted_rows = [
        d for d in db38_decisions
        if "accepted" in str(d.get("decision", "")).lower() or "current best" in str(d.get("decision", "")).lower()
    ]
    rejected_rows = [
        d for d in db38_decisions
        if "reject" in str(d.get("decision", "")).lower() or "diagnostic" in str(d.get("decision", "")).lower()
    ]
    route_table = db42.get("route_table", [])
    route_status_counts = Counter(str(r.get("status", "missing")) for r in route_table)
    db43_summary = classify_from_db43(db43)

    limitations = [
        "DB47a is an inventory over existing artifacts only; it is not a new dataset scan.",
        "DB31 selected_count is a bounded candidate shortlist, not the full Waymo distribution.",
        "DB32/DB34/DB38/DB42 acceptance is source-sidestep/handoff acceptance, not original-G seam repair.",
        "DB41 lower-right/right-line remains no-evidence/abstain; source selection cannot promote it as repaired.",
    ]

    next_scan_contract = {
        "required_before_full_db47_scan": [
            "fixed candidate universe and logs before execution",
            "report total scanned, strict accepted, relaxed accepted, reject-by-reason, and abstain distribution",
            "show both accepted candidates and failures",
            "same-ROI boards for source-sidestep wins and rejected lookalikes",
            "explicitly label source-sidestep separately from seam repair",
        ],
        "hard_forbidden_claims": [
            "DB32 is a repaired original-G seam",
            "G_bmw_pano is the default repair base",
            "DB41 lower-right/right-line is source-faithfully repairable under current evidence",
            "top pretty candidates imply dataset-level acceptance without rate accounting",
        ],
    }

    checks = [
        {
            "id": "cpu_local_only",
            "pass": True,
            "evidence": "DB47a reads existing artifacts only; no executor/A100/model/generation path exists in this script.",
        },
        {
            "id": "bounded_existing_artifacts",
            "pass": all(x["present"] for x in scan_summaries) and bool(db34) and bool(db38) and bool(db42) and bool(db43),
            "evidence": "Inputs include DB27/DB28/DB31/DB34/DB38/DB42/DB43 existing artifacts.",
        },
        {
            "id": "reports_counts_not_top10_only",
            "pass": total_candidate_records > 0 and len(db31_unique_logs) > 0,
            "evidence": f"candidate_records={total_candidate_records}; db31_unique_logs={len(db31_unique_logs)}",
        },
        {
            "id": "source_sidestep_not_repair",
            "pass": True,
            "evidence": "Manifest labels DB28/DB32 as source-sidestep/current handoff only, not original-G repair.",
        },
        {
            "id": "db41_abstain_preserved",
            "pass": True,
            "evidence": "DB47a cannot promote DB41; it is inventory/source-selection only.",
        },
        {
            "id": "no_repair_or_generation",
            "pass": True,
            "evidence": "No panorama repair, source replacement, generated pixels, diffusion, or refiner output is produced.",
        },
    ]

    manifest: dict[str, Any] = {
        "db": "DB-47a",
        "status": "source_candidate_inventory_phase0",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "source-selection-inventory-only",
        "purpose": "Inventory existing source/frame candidate mining evidence without new repair, generation, or dataset scan.",
        "scope": {
            "cpu_local_only": True,
            "a100_used": False,
            "executor_used": False,
            "new_dataset_scan": False,
            "panorama_repair": False,
            "generation": False,
            "source_replacement": False,
            "input_artifacts": [rel(p) for p in [DB27, DB28, DB31, DB34, DB38, DB42, DB43]],
        },
        "scan_summaries": scan_summaries,
        "aggregate_counts": {
            "existing_candidate_records": total_candidate_records,
            "db31_selected_count": db31.get("selected_count"),
            "db31_unique_logs": len(db31_unique_logs),
            "db31_log_ids": db31_unique_logs,
            "db38_accepted_or_current_best_rows": len(accepted_rows),
            "db38_rejected_or_diagnostic_rows": len(rejected_rows),
            "db42_route_status_counts": dict(route_status_counts),
            "db43_case_count": db43_summary["case_count"],
            "db43_claim_label_counts": db43_summary["claim_label_counts"],
        },
        "accepted_source_selection_evidence": {
            "source_base": source_base,
            "current_handoff": current_handoff,
            "db38_rows": accepted_rows,
            "db42_use": db42.get("decision", {}).get("use"),
            "not_a_claim": "DB32/DB28 are source-sidestep/handoff evidence, not original-G seam repair and not source-faithful ceiling.",
        },
        "rejected_or_caveated_evidence": {
            "db38_rows": rejected_rows,
            "db42_do_not_use_as_final": db42.get("decision", {}).get("do_not_use_as_final", []),
            "db43_reject_or_diagnostic_case_ids": db43_summary["reject_or_diagnostic_case_ids"][:32],
            "db43_no_evidence_case_ids": db43_summary["no_evidence_case_ids"][:32],
            "top_reason_codes": db43_summary["top_reason_codes"],
        },
        "limitations": limitations,
        "next_scan_contract": next_scan_contract,
        "checks": checks,
        "decision": {
            "db45_status": "paused_on_executor_dns_for_vggt_residuals",
            "db47_status": "running_phase0_inventory_complete",
            "accepted_db47_diagnostic_evidence": True,
            "accepted_source_faithful_repair": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "claim_boundary": "Source/frame selection may produce cleaner handoff candidates, but it is not local seam repair.",
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


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], s: str, fill=(235, 235, 235), size=18) -> None:
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


def draw_table(draw: ImageDraw.ImageDraw, x: int, y: int, rows: list[dict[str, Any]]) -> int:
    headers = ["source", "records", "top", "status"]
    col_x = [x, x + 310, x + 430, x + 760]
    for i, h in enumerate(headers):
        text(draw, (col_x[i], y), h, fill=(180, 205, 235), size=16)
    y += 28
    for row in rows:
        top = row.get("top_candidate") or {}
        top_bits = []
        if top.get("log_id"):
            top_bits.append(str(top.get("log_id")))
        if top.get("anchor") is not None:
            top_bits.append("a" + str(top.get("anchor")))
        if top.get("rank_score_low_is_better") is not None:
            top_bits.append("score %.3f" % float(top.get("rank_score_low_is_better")))
        elif top.get("line_risk_score_low_is_better") is not None:
            top_bits.append("risk %.3f" % float(top.get("line_risk_score_low_is_better")))
        if top.get("lidar_support_frac") is not None:
            top_bits.append("LiDAR %.3f" % float(top.get("lidar_support_frac")))
        status = "inventory only"
        if "DB28" in row["name"]:
            status = "accepted source base"
        if "DB31" in row["name"]:
            status = "shortlist; no successor"
        if "DB27" in row["name"]:
            status = "diagnostic; no replacement"
        text(draw, (col_x[0], y), row["name"], size=15)
        text(draw, (col_x[1], y), str(row.get("anchors_or_candidates")), size=15)
        text(draw, (col_x[2], y), "; ".join(top_bits)[:46], size=15)
        text(draw, (col_x[3], y), status, size=15)
        y += 26
    return y


def paste_thumb(board: Image.Image, path: str | None, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    p = local_path(path)
    x0, y0, x1, y1 = box
    draw.rectangle(box, outline=(90, 90, 90), width=1, fill=(26, 29, 33))
    if p:
        try:
            img = Image.open(p).convert("RGB")
            img.thumbnail((x1 - x0 - 10, y1 - y0 - 36))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception:
            text(draw, (x0 + 10, y0 + 24), "image load failed", fill=(220, 130, 130), size=15)
    else:
        text(draw, (x0 + 10, y0 + 24), "missing thumbnail", fill=(220, 130, 130), size=15)
    text(draw, (x0 + 10, y1 - 26), label, fill=(220, 230, 245), size=14)


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1800, 1300), (16, 18, 22))
    draw = ImageDraw.Draw(board)
    text(draw, (28, 24), "DB47a Source/Frame Candidate Inventory", size=30)
    text(draw, (28, 66), "CPU/local existing artifacts only - no repair, no generation, no executor", fill=(210, 210, 170), size=18)
    decision = manifest["decision"]
    pill_specs = [
        ("inventory-only", (120, 86, 42)),
        ("source repair=False", (150, 70, 70)),
        ("RED promotions=0", (65, 130, 82)),
        ("DB45 paused on executor", (110, 90, 150)),
    ]
    x = 28
    for label, color in pill_specs:
        draw.rounded_rectangle((x, 100, x + 250, 136), radius=5, fill=color, outline=(180, 180, 180))
        text(draw, (x + 10, 108), label, size=15)
        x += 270

    y = 170
    text(draw, (28, y), "Existing candidate evidence", size=23)
    y = draw_table(draw, 28, y + 42, manifest["scan_summaries"])

    counts = manifest["aggregate_counts"]
    y += 28
    text(draw, (28, y), "Counts / claim boundary", size=23)
    y += 38
    count_lines = [
        f"existing candidate records reviewed: {counts['existing_candidate_records']}",
        f"DB31 selected shortlist: {counts['db31_selected_count']} candidates across {counts['db31_unique_logs']} logs",
        f"DB38 accepted/current-best rows: {counts['db38_accepted_or_current_best_rows']}; rejected/diagnostic rows: {counts['db38_rejected_or_diagnostic_rows']}",
        f"DB43 known cases: {counts['db43_case_count']} with labels {counts['db43_claim_label_counts']}",
        "DB28/DB32 source-sidestep can improve handoff quality but does not repair original G/A1/BEST seams.",
        "DB41 lower-right/right-line remains no-evidence/abstain under current evidence.",
    ]
    for line in count_lines:
        wrapped = wrap("- " + line, 86)
        for idx, part in enumerate(wrapped):
            text(draw, (40 if idx == 0 else 58, y), part, size=16)
            y += 23

    y += 20
    text(draw, (28, y), "Required next DB47 scan contract", size=23)
    y += 38
    for item in manifest["next_scan_contract"]["required_before_full_db47_scan"]:
        wrapped = wrap("- " + item, 86)
        for idx, part in enumerate(wrapped):
            text(draw, (40 if idx == 0 else 58, y), part, fill=(230, 230, 215), size=16)
            y += 23

    y += 16
    text(draw, (28, y), "Hard checks", size=23)
    y += 38
    for check in manifest["checks"]:
        color = (70, 150, 90) if check["pass"] else (170, 60, 60)
        draw.rounded_rectangle((40, y, 120, y + 26), radius=4, fill=color)
        text(draw, (55, y + 4), "PASS" if check["pass"] else "STOP", size=13)
        text(draw, (135, y + 3), f"{check['id']}: {check['evidence']}", size=15)
        y += 32

    thumb_y = 170
    text(draw, (1030, 132), "Visual inventory references", size=23)
    thumbs = [
        ("deliverables/dit360_v2/db28_clean_subset_refine/db28_strict_clean_source_scan_montage.jpg", "DB28 strict source scan"),
        ("deliverables/dit360_v2/db31_multilog_candidate_scan/db31_roi_montage.jpg", "DB31 bounded shortlist"),
        ("deliverables/dit360_v2/db34_current_best_qa/db34_current_best_review_board.jpg", "DB34 current-best QA"),
        ("deliverables/dit360_v2/db42_seam_decision_handoff/db42_seam_decision_handoff_board.jpg", "DB42 seam decision"),
    ]
    x0 = 1030
    for idx, (path, label) in enumerate(thumbs):
        col = idx % 2
        row = idx // 2
        paste_thumb(board, path, (x0 + col * 375, thumb_y + row * 300, x0 + col * 375 + 350, thumb_y + row * 300 + 270), label)

    dy = 785
    text(draw, (1030, dy), "Decision", size=23)
    dy += 38
    for line in wrap(decision["claim_boundary"], 58):
        text(draw, (1040, dy), line, fill=(235, 235, 215), size=17)
        dy += 25
    dy += 10
    for line in [
        "DB47a accepts inventory/contract evidence only.",
        "It does not produce a new candidate panorama.",
        "It does not change permission states.",
        "Full DB47 scan still needs a bounded candidate universe.",
    ]:
        text(draw, (1040, dy), "- " + line, size=16)
        dy += 25

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> None:
    manifest = build_manifest()
    build_board(manifest)
    print(f"wrote {MANIFEST}")
    print(f"wrote {BOARD}")
    print(json.dumps(manifest["decision"], indent=2))


if __name__ == "__main__":
    main()
