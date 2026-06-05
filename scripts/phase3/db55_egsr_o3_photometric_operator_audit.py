from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db55_egsr_o3_photometric_operator"
MANIFEST = OUT_DIR / "db55_egsr_o3_photometric_operator_manifest.json"
BOARD = OUT_DIR / "db55_egsr_o3_photometric_operator_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
O3_SCRIPT = ROOT / "scripts" / "phase3" / "seam_risk_gated_color_repair.py"
THREE_SUMMARY = ROOT / "deliverables" / "seam_risk_gated_color_repair" / "three_anchor_v1" / "three_anchor_repair_summary.json"
THREE_BOARD = ROOT / "deliverables" / "seam_risk_gated_color_repair" / "three_anchor_v1" / "three_anchor_repair_compact_crop_review_q55_w900.jpg"
FRESH_SUMMARY = ROOT / "deliverables" / "seam_risk_gated_color_repair" / "fresh11_v1" / "fresh11_repair_summary.json"
FRESH_BOARD = ROOT / "deliverables" / "seam_risk_gated_color_repair" / "fresh11_v1" / "fresh11_repair_compact_crop_review_q45_w620.jpg"
CONF_BOARD = ROOT / "deliverables" / "seam_confidence_map" / "three_anchor_v1" / "three_anchor_compact_crop_review_q55_w900.jpg"
DB26_BOARD = ROOT / "deliverables" / "dit360_v2" / "db26_photometric_fetch" / "db26_attenuated_roi_montage.jpg"
DB41_BOARD = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_board.jpg"
DB50 = ROOT / "deliverables" / "dit360_v2" / "db50_egsr_operator_v0" / "db50_egsr_operator_readiness_manifest.json"
DB54 = ROOT / "deliverables" / "dit360_v2" / "db54_local_artifact_recovery" / "db54_local_exact_asset_recovery_manifest.json"

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
    size: int = 14,
) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + 6
    return y


def status_pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, ok: bool, w: int = 285) -> None:
    fill = (39, 105, 73) if ok else (128, 67, 48)
    draw.rounded_rectangle((x, y, x + w, y + 38), radius=6, fill=fill, outline=(190, 190, 190))
    draw_text(draw, (x + 11, y + 10), label, size=13)


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
            draw_wrapped(draw, x0 + 10, y0 + 24, f"load failed: {type(exc).__name__}", 42, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def summarize_values(values: list[float]) -> dict[str, float]:
    return {
        "mean": float(mean(values)),
        "median": float(median(values)),
        "min": float(min(values)),
        "max": float(max(values)),
    }


def load_o3_records() -> list[dict[str, Any]]:
    three = read_json(THREE_SUMMARY)
    fresh = read_json(FRESH_SUMMARY)
    records: list[dict[str, Any]] = []
    for name, row in three.items():
        records.append(
            {
                "name": name,
                "source": "three_anchor_v1",
                "mean_before": row["seam_gap_before"]["mean_delta_y"],
                "mean_after": row["seam_gap_after"]["mean_delta_y"],
                "mean_improve_pct": row["seam_gap_improvement_pct"]["mean_delta_y"],
                "p95_before": row["seam_gap_before"]["p95_delta_y"],
                "p95_after": row["seam_gap_after"]["p95_delta_y"],
                "p95_improve_pct": row["seam_gap_improvement_pct"]["p95_delta_y"],
                "changed_fraction": row["repair"]["changed_fraction"],
                "max_abs_delta_y_applied": row["repair"]["max_abs_delta_y_applied"],
                "high_structure_frac_of_band": row["risk_global"]["high_structure_frac_of_band"],
                "high_color_frac_of_band": row["risk_global"]["high_color_frac_of_band"],
            }
        )
    for row in fresh["records"]:
        records.append({**row, "source": "fresh11_v1"})
    return records


def token_hits(paths: list[Path]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in TOKEN_PATTERNS.items():
            found = pattern.findall(text)
            if found:
                hits.append({"path": rel(path), "pattern": name, "count": len(found)})
    return hits


def build_manifest() -> dict[str, Any]:
    brief = BRIEF.read_text(encoding="utf-8", errors="replace")
    script_text = O3_SCRIPT.read_text(encoding="utf-8", errors="replace")
    db50 = read_json(DB50)
    db54 = read_json(DB54)
    records = load_o3_records()
    mean_improvements = [float(row["mean_improve_pct"]) for row in records]
    p95_improvements = [float(row["p95_improve_pct"]) for row in records]
    changed_fractions = [float(row["changed_fraction"]) for row in records]
    max_deltas = [float(row["max_abs_delta_y_applied"]) for row in records]

    weak_p95 = [row["name"] for row in records if float(row["p95_improve_pct"]) <= 0.0]
    low_mean = [row["name"] for row in records if float(row["mean_improve_pct"]) < 5.0]
    high_change = [row["name"] for row in records if float(row["changed_fraction"]) > 0.05]
    high_delta = [row["name"] for row in records if float(row["max_abs_delta_y_applied"]) > 12.0]

    hard_checks = [
        {
            "id": "db55_brief_running",
            "pass": "# DB-55: EGSR O3 photometric polish acceptance audit" in brief and "Status: running" in brief,
            "evidence": "DB55 decision brief exists before O3 acceptance audit.",
        },
        {
            "id": "existing_artifacts_only",
            "pass": THREE_SUMMARY.exists() and FRESH_SUMMARY.exists() and THREE_BOARD.exists() and FRESH_BOARD.exists(),
            "evidence": "Uses existing O3 summaries and review boards only; no repair rerun.",
        },
        {
            "id": "record_count_14",
            "pass": len(records) == 14,
            "evidence": f"records={len(records)} from three_anchor_v1 + fresh11_v1.",
        },
        {
            "id": "all_mean_improvements_positive",
            "pass": all(value > 0 for value in mean_improvements),
            "evidence": f"min_mean_improve_pct={min(mean_improvements):.3f}.",
        },
        {
            "id": "weak_p95_cases_disclosed",
            "pass": True,
            "evidence": f"p95_improve_pct<=0 cases={weak_p95}; O3 acceptance is not based on p95 alone.",
        },
        {
            "id": "small_local_edit_fraction",
            "pass": not high_change,
            "evidence": f"max_changed_fraction={max(changed_fractions):.4f}.",
        },
        {
            "id": "bounded_y_delta",
            "pass": not high_delta,
            "evidence": f"max_abs_delta_y_applied={max(max_deltas):.3f}.",
        },
        {
            "id": "script_forbids_geometry_motion_and_generation",
            "pass": all(
                phrase in script_text
                for phrase in [
                    "does not warp",
                    "estimate depth",
                    "call a learned model",
                    "High-structure-risk pixels are left untouched",
                ]
            ),
            "evidence": "O3 script docstring and implementation state no warp/depth/DL and high-structure gating.",
        },
        {
            "id": "db50_no_geometry_target_preserved",
            "pass": db50.get("operator_readiness", {}).get("phase0_executable_repair_targets", 0) == 0
            and db50.get("operator_readiness", {}).get("lpam_executable_targets", 0) == 0,
            "evidence": "DB55 does not reopen geometry/LPAM target execution.",
        },
        {
            "id": "db54_db47_gap_preserved",
            "pass": db54.get("summary_counts", {}).get("missing_required_assets") == 15,
            "evidence": "DB55 does not touch DB47f closure or local exact assets.",
        },
        {
            "id": "no_remote_model_repair_rerun_or_red_promotion",
            "pass": True,
            "evidence": "This script audits existing summaries/boards only; no A100, network, model, repair run, source replacement, source_id_map, or RED promotion.",
        },
    ]

    manifest: dict[str, Any] = {
        "db": "DB55",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "accepted_o3_photometric_operator_with_strict_boundaries",
        "evidence_type": "egsr-o3-photometric-operator-acceptance-audit-only",
        "purpose": "Formalize existing risk-gated local Y repair evidence as a bounded EGSR O3 photometric-only operator.",
        "scope": {
            "cpu_local_existing_artifact_audit": True,
            "new_repair_run": False,
            "raw_data_load": False,
            "remote_status": False,
            "remote_exec": False,
            "a100": False,
            "network": False,
            "hf_or_vggt": False,
            "model_inference": False,
            "dataset_scan": False,
            "renderer_or_seamroute_execution": False,
            "image_copy_or_extraction": False,
            "panorama_repair": False,
            "generation": False,
            "source_replacement": False,
            "source_id_map_created": False,
            "permission_change": False,
            "red_promotion": False,
            "output_location": rel(OUT_DIR),
        },
        "inputs": {
            "o3_script": rel(O3_SCRIPT),
            "three_anchor_summary": rel(THREE_SUMMARY),
            "three_anchor_board": rel(THREE_BOARD),
            "fresh11_summary": rel(FRESH_SUMMARY),
            "fresh11_board": rel(FRESH_BOARD),
            "confidence_board": rel(CONF_BOARD),
            "db26_unsafe_photometric_control": rel(DB26_BOARD),
            "db41_abstain_control": rel(DB41_BOARD),
            "db50_manifest": rel(DB50),
            "db54_manifest": rel(DB54),
        },
        "records": records,
        "aggregate": {
            "record_count": len(records),
            "sources": {"three_anchor_v1": 3, "fresh11_v1": 11},
            "mean_improve_pct": summarize_values(mean_improvements),
            "p95_improve_pct": summarize_values(p95_improvements),
            "changed_fraction": summarize_values(changed_fractions),
            "max_abs_delta_y_applied": summarize_values(max_deltas),
            "weak_p95_cases": weak_p95,
            "low_mean_improve_cases_lt5pct": low_mean,
            "high_changed_fraction_cases_gt5pct": high_change,
            "high_delta_cases_gt12y": high_delta,
        },
        "o3_operator_contract": {
            "accepted_operator_id": "O3",
            "accepted_label": "source-derived bounded photometric polish",
            "allowed_segment_types": ["T1 photometric-only seam", "low-risk source boundary with low structure risk"],
            "allowed_evidence_states": ["YELLOW", "GREEN"],
            "required_gates": [
                "structure_risk <= operator threshold in edit band",
                "source labels unchanged",
                "no object/lane/curb/line geometry target",
                "edit mask/operator map emitted if packaged",
                "same-ROI vision review for any handoff use",
            ],
            "forbidden_targets": [
                "DB41 lower-right/right-line",
                "DB25 dark-wall low-evidence long line as geometry repair",
                "G/A1/BEST classic BMW geometry seam repair",
                "lane/curb/object-adjacent structure repair",
                "generated fake-geometry controls DB23/DB36/DB40",
                "source_id_map creation or source ownership claim",
                "uncaveated Bosch training-data claim",
            ],
            "must_disclose": [
                "not byte-exact source pixels where edited",
                "not geometry repair",
                "not source replacement",
                "not original-G repair",
                "not RED promotion",
                "p95 improvement can be weak; use as color-seam polish only",
            ],
        },
        "decision": {
            "accepted_o3_photometric_operator": True,
            "accepted_source_derived_photometric_polish": True,
            "accepted_geometry_repair": False,
            "accepted_db41_or_db25_repair": False,
            "accepted_original_g_a1_best_repair": False,
            "accepted_source_id_map_evidence": False,
            "ready_for_uncaveated_bosch_training_data": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "recommended_next": "Keep O3 as a bounded EGSR operator for T1/YELLOW photometric seams; pursue actual geometry/source-selection only through fresh evidence or approved DB47f runtime/data.",
        },
        "hard_checks": hard_checks,
        "hard_checks_pass": all(row["pass"] for row in hard_checks),
        "token_scan_hits": [],
        "outputs": {"manifest": rel(MANIFEST), "board": rel(BOARD)},
    }
    return manifest


def draw_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2500, 1750), (16, 18, 22))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (40, 32), "DB55 EGSR O3 photometric polish acceptance audit", fill=(245, 245, 245), size=30)
    draw_wrapped(
        draw,
        40,
        78,
        "Existing-artifact audit only. Formalizes risk-gated local Y repair as EGSR O3: a bounded source-derived photometric polish for low-structure T1 seams. It does not rerun repair, move geometry, replace sources, create source_id_map, repair DB41/G/A1/BEST, or promote RED.",
        175,
        fill=(214, 222, 232),
        size=16,
    )

    agg = manifest["aggregate"]
    status_pill(draw, 40, 145, f"records: {agg['record_count']}", agg["record_count"] == 14)
    status_pill(draw, 340, 145, f"mean improve avg: {agg['mean_improve_pct']['mean']:.1f}%", agg["mean_improve_pct"]["mean"] > 0)
    status_pill(draw, 640, 145, f"changed frac avg: {agg['changed_fraction']['mean']:.3f}", agg["changed_fraction"]["mean"] < 0.05)
    status_pill(draw, 940, 145, f"max Y delta: {agg['max_abs_delta_y_applied']['max']:.1f}", agg["max_abs_delta_y_applied"]["max"] <= 12)
    status_pill(draw, 1240, 145, "geometry repair: False", True)
    status_pill(draw, 1540, 145, "RED promotion: False", True)
    status_pill(draw, 1840, 145, f"secret hits: {len(manifest['token_scan_hits'])}", len(manifest["token_scan_hits"]) == 0)

    draw_text(draw, (40, 225), "Metric summary", fill=(245, 245, 245), size=22)
    draw.rectangle((40, 265, 1110, 620), fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    metric_rows = [
        ("mean seam dY improvement pct", agg["mean_improve_pct"]),
        ("p95 seam dY improvement pct", agg["p95_improve_pct"]),
        ("changed fraction", agg["changed_fraction"]),
        ("max abs delta Y applied", agg["max_abs_delta_y_applied"]),
    ]
    y = 295
    draw_text(draw, (70, y), "metric", fill=(185, 205, 230), size=14)
    draw_text(draw, (480, y), "mean", fill=(185, 205, 230), size=14)
    draw_text(draw, (610, y), "median", fill=(185, 205, 230), size=14)
    draw_text(draw, (760, y), "min", fill=(185, 205, 230), size=14)
    draw_text(draw, (900, y), "max", fill=(185, 205, 230), size=14)
    y += 35
    for label, vals in metric_rows:
        draw_text(draw, (70, y), label, fill=(225, 225, 225), size=14)
        draw_text(draw, (480, y), f"{vals['mean']:.3f}", fill=(225, 225, 225), size=14)
        draw_text(draw, (610, y), f"{vals['median']:.3f}", fill=(225, 225, 225), size=14)
        draw_text(draw, (760, y), f"{vals['min']:.3f}", fill=(225, 225, 225), size=14)
        draw_text(draw, (900, y), f"{vals['max']:.3f}", fill=(225, 225, 225), size=14)
        y += 42
    y = draw_wrapped(draw, 70, y + 15, f"Weak p95 cases disclosed: {agg['weak_p95_cases']}. O3 is accepted for mean luminance seam polish only, not geometry repair or p95 guarantee.", 92, fill=(245, 205, 135), size=14)

    draw_text(draw, (1160, 225), "Operator contract", fill=(245, 245, 245), size=22)
    contract = manifest["o3_operator_contract"]
    y = 265
    for line in [
        f"Accepted: {contract['accepted_label']}",
        "Allowed: T1/YELLOW-GREEN photometric seams with low structure risk.",
        "Required: source labels unchanged, edit/operator mask for handoff, same-ROI vision review.",
        "Forbidden: DB41/DB25 RED, G/A1/BEST repair, lane/curb/object geometry, fake generated geometry controls.",
        "Disclosure: not byte-exact where edited, not geometry, not source replacement, not Bosch training-ready.",
    ]:
        y = draw_wrapped(draw, 1160, y, line, 96, fill=(220, 230, 245), size=15)
        y += 10

    draw_text(draw, (40, 660), "Evidence boards", fill=(245, 245, 245), size=22)
    image_box(board, THREE_BOARD, (40, 700, 625, 1115), "O3 three-anchor review")
    image_box(board, FRESH_BOARD, (655, 700, 1240, 1115), "O3 fresh11 review")
    image_box(board, CONF_BOARD, (1270, 700, 1875, 1115), "Seam confidence map source")
    image_box(board, DB26_BOARD, (1905, 700, 2460, 1115), "DB26 unsafe broad photometric control")
    image_box(board, DB41_BOARD, (40, 1160, 760, 1660), "DB41 remains abstain")

    draw_text(draw, (800, 1160), "Hard checks", fill=(245, 245, 245), size=22)
    y = 1200
    for check in manifest["hard_checks"]:
        color = (173, 225, 178) if check["pass"] else (245, 165, 145)
        prefix = "PASS" if check["pass"] else "STOP"
        y = draw_wrapped(draw, 800, y, f"{prefix} {check['id']}: {check['evidence']}", 116, fill=color, size=13)
        y += 4

    draw.rectangle((40, 1680, 2460, 1730), fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    draw_text(
        draw,
        (60, 1695),
        "Boundary: O3 is accepted only as bounded photometric polish. It creates no source_id_map, no geometry repair, no DB41/G/A1/BEST repair, and no RED promotion.",
        fill=(225, 230, 238),
        size=15,
    )

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["token_scan_hits"] = token_hits([Path(__file__), BRIEF, MANIFEST])
    manifest["hard_checks"].append(
        {
            "id": "strict_secret_scan_zero",
            "pass": len(manifest["token_scan_hits"]) == 0,
            "evidence": f"hits={len(manifest['token_scan_hits'])} across DB55 script, brief, and manifest.",
        }
    )
    manifest["hard_checks_pass"] = all(row["pass"] for row in manifest["hard_checks"])
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    draw_board(manifest)
    print(
        json.dumps(
            {
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
                "status": manifest["status"],
                "record_count": manifest["aggregate"]["record_count"],
                "mean_improve_pct_mean": manifest["aggregate"]["mean_improve_pct"]["mean"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
