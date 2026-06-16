from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DB64 = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0"
OUT_DIR = DB64 / "phase5a_continuous_surface"
PHASE2 = DB64 / "phase2_lidar_zbuffer_fetch"
PHASE3 = DB64 / "phase3_sidecar_instrumentation" / "fetch"
PHASE4B = DB64 / "phase4b_z_visibility_cause" / "fetch"
PHASE5A = OUT_DIR / "fetch"
SUMMARY4B = DB64 / "phase4b_z_visibility_cause" / "db64_phase4b_batch_summary.json"
SUMMARY5A = OUT_DIR / "db64_phase5a_batch_summary.json"
SUMMARY2 = DB64 / "db64_phase2_lidar_zbuffer_batch_summary.json"

RUN = "02a00399_a000_bmw"
CONTROL_RUN = "0bae3b5e_a030_clean_far"
CONTROL = PHASE2 / RUN / f"{RUN}_hard_select.jpg"
CANDIDATE_SRC = PHASE2 / RUN / f"{RUN}_lidar_best.jpg"
CROP_REVIEW2 = PHASE2 / RUN / f"{RUN}_lidar_zbuffer_crop_review.jpg"
SIDECAR_REVIEW3 = PHASE3 / RUN / f"{RUN}_sidecar_review_768.jpg"
Z_REVIEW4B = PHASE4B / RUN / f"{RUN}_z_visibility_review_768.jpg"
Z_CAUSE = PHASE4B / RUN / f"{RUN}_z_cause_primary_viz.png"
Z_REPAIR = PHASE4B / RUN / f"{RUN}_z_repairability_viz.png"
PHASE5A_BOARD = OUT_DIR / "db64_phase5a_continuous_surface_board.jpg"
PHASE5A_BMW_REVIEW = PHASE5A / RUN / f"{RUN}_phase5a_crop_review.jpg"
PHASE5A_CONTROL_REVIEW = PHASE5A / CONTROL_RUN / f"{CONTROL_RUN}_phase5a_crop_review.jpg"
PHASE5A_BMW_FUSED_CAUSE = PHASE5A / RUN / f"{RUN}_fused_z_cause_primary_viz.png"
PHASE5A_BMW_TRANSITION = PHASE5A / RUN / f"{RUN}_before_after_transition_viz.png"

LOCAL_CANDIDATE = OUT_DIR / "db64_phase5a_current_best_visible_candidate_rejected.png"
BOARD = OUT_DIR / "db64_phase5a_final_visible_result_board.jpg"
MANIFEST = OUT_DIR / "db64_phase5a_final_visible_result_manifest.json"

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "trycloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "json_token": re.compile(r'"token"\s*:\s*"[A-Za-z0-9._\-]{12,}"'),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
}


def rel(path: Path | str) -> str:
    p = Path(path)
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return "<non-repo path omitted>"


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pat in TOKEN_PATTERNS.items():
        found = pat.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=(236, 236, 236), size=15) -> None:
    draw.text(xy, str(text), fill=fill, font=font(size))


def draw_wrapped(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, chars: int, fill=(236, 236, 236), size: int = 14) -> int:
    for line in wrap(str(text), width=chars, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + 6
    return y


def paste_thumb(board: Image.Image, path: Path, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    if not path.exists():
        draw.rectangle(box, fill=(35, 37, 43), outline=(100, 100, 100))
        draw_wrapped(draw, x0 + 12, y0 + 12, f"missing: {rel(path)}", 42, fill=(255, 160, 130), size=14)
        return
    with Image.open(path) as img:
        thumb = img.convert("RGB")
        thumb.thumbnail((x1 - x0, y1 - y0))
        px = x0 + ((x1 - x0) - thumb.width) // 2
        py = y0 + ((y1 - y0) - thumb.height) // 2
        board.paste(thumb, (px, py))
        draw.rectangle((px, py, px + thumb.width, py + thumb.height), outline=(188, 188, 188))


def fmt(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return "n/a"


def make_candidate_copy() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not CANDIDATE_SRC.exists():
        raise FileNotFoundError(CANDIDATE_SRC)
    shutil.copyfile(CANDIDATE_SRC, LOCAL_CANDIDATE)


def get_case(summary: dict[str, Any], case_id: str) -> dict[str, Any]:
    return (summary.get("by_case") or {}).get(case_id) or {}


def write_board(summary2: dict[str, Any], summary4b: dict[str, Any], summary5a: dict[str, Any], manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1900, 1700), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 22), "DB64 Phase5a final visible seam result package", size=27)
    draw_text(
        draw,
        (28, 60),
        "Phase5a CPU Colab evidence completed. Visible RGB candidate remains rejected diagnostic; Phase5a overlays are evidence only.",
        fill=(218, 224, 235),
        size=15,
    )

    phase2_mean = (summary2.get("aggregate") or summary2.get("mean") or {})
    phase4b_mean = summary4b.get("mean") or summary4b.get("mean_current") or {}
    phase4b_bmw = get_case(summary4b, RUN)
    phase5a_bmw = get_case(summary5a, RUN)
    phase5a_improvements = phase5a_bmw.get("improvements") or {}
    y = 102
    lines = [
        f"claim_classification={manifest['claim_classification']}",
        f"phase5a_remote_executed={manifest['phase5a_remote_executed']} aggregate_success={summary5a.get('aggregate_success')} phase5b_allowed=false secret_hits={manifest['strict_secret_scan']['hit_count']}",
        "visible candidate source=Phase2 lidar_best RGB diagnostic; included only so the user has a seam version to inspect",
        f"Phase5a BMW delta_no_surface={fmt(phase5a_improvements.get('delta_no_surface_frac'))} gain_visible_any={fmt(phase5a_improvements.get('gain_visible_any_frac'))} gain_visible_ge2={fmt(phase5a_improvements.get('gain_visible_ge2_frac'))}",
        f"Phase5a BMW delta_no_zbuf={fmt(phase5a_improvements.get('delta_no_raw_zbuffer_support_frac'))} fused_component={fmt(phase5a_improvements.get('fused_longest_supported_component_frac'))}",
        f"Phase4b BMW no_surface={fmt(phase4b_bmw.get('seam_no_surface_frac'))} no_zbuf={fmt(phase4b_bmw.get('seam_no_raw_zbuffer_support_frac'))} visible_ge2={fmt(phase4b_bmw.get('seam_visible_ge2_frac'))}",
        f"Phase4b mean no_surface={fmt(phase4b_mean.get('seam_no_surface_frac'))} no_zbuf={fmt(phase4b_mean.get('seam_no_raw_zbuffer_support_frac'))} z_conflict={fmt(phase4b_mean.get('seam_z_mismatch_conflict_frac'))}",
        f"Phase2 aggregate note={phase2_mean if isinstance(phase2_mean, str) else 'see db64_phase2_lidar_zbuffer_batch_summary.json'}",
    ]
    for line in lines:
        y = draw_wrapped(draw, 36, y, "- " + line, 148, size=13)

    y += 8
    draw_text(draw, (28, y), "Vision Classification", size=20)
    y += 30
    for line in [
        "hard_select remains the conservative control",
        "lidar_best is the best available visible RGB candidate under current local artifacts, but it was already rejected as repair for sparse/blocky support",
        "Phase5a adds some target-surface support, but raw-visible support gets worse and no continuous supported component appears",
        "do not enter Phase5b layer fitting or Phase5c renderer from this evidence; this is the evidence endpoint for DB64 Phase5a",
    ]:
        y = draw_wrapped(draw, 36, y, "- " + line, 148, fill=(255, 235, 185), size=13)

    paste_thumb(board, CONTROL, (28, 430, 610, 720))
    draw_text(draw, (28, 400), "HardSelect control", size=18)
    paste_thumb(board, LOCAL_CANDIDATE, (650, 430, 1232, 720))
    draw_text(draw, (650, 400), "Current best visible RGB candidate, rejected diagnostic", size=18)
    paste_thumb(board, PHASE5A_BOARD, (1272, 430, 1870, 720))
    draw_text(draw, (1272, 400), "Phase5a aggregate evidence board", size=18)

    paste_thumb(board, PHASE5A_BMW_REVIEW, (28, 790, 610, 1180))
    draw_text(draw, (28, 760), "BMW Phase5a same-ROI review", size=18)
    paste_thumb(board, PHASE5A_CONTROL_REVIEW, (650, 790, 1232, 1180))
    draw_text(draw, (650, 760), "Clean-control Phase5a review", size=18)
    paste_thumb(board, PHASE5A_BMW_FUSED_CAUSE, (1272, 790, 1870, 1180))
    draw_text(draw, (1272, 760), "BMW fused z-cause map", size=18)

    paste_thumb(board, PHASE5A_BMW_TRANSITION, (28, 1250, 610, 1660))
    draw_text(draw, (28, 1220), "BMW before/after evidence transition", size=18)
    paste_thumb(board, Z_REVIEW4B, (650, 1250, 1232, 1660))
    draw_text(draw, (650, 1220), "Phase4b z-visibility evidence", size=18)
    paste_thumb(board, SIDECAR_REVIEW3, (1272, 1250, 1870, 1660))
    draw_text(draw, (1272, 1220), "Phase3 sidecar evidence", size=18)
    board.save(BOARD, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_candidate_copy()
    summary2 = load_json(SUMMARY2)
    summary4b = load_json(SUMMARY4B)
    summary5a = load_json(SUMMARY5A)
    phase5a_bmw = get_case(summary5a, RUN)
    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_phase5a_final_visible_result_package",
        "packaging_local_only": True,
        "phase5a_remote_executed": True,
        "uses_existing_artifacts_only": True,
        "source_artifacts": {
            "candidate": rel(CANDIDATE_SRC),
            "control": rel(CONTROL),
            "phase2_crop_review": rel(CROP_REVIEW2),
            "phase3_sidecar_review": rel(SIDECAR_REVIEW3),
            "phase4b_z_review": rel(Z_REVIEW4B),
            "phase4b_z_cause": rel(Z_CAUSE),
            "phase4b_z_repairability": rel(Z_REPAIR),
            "phase5a_summary": rel(SUMMARY5A),
            "phase5a_board": rel(PHASE5A_BOARD),
            "phase5a_bmw_review": rel(PHASE5A_BMW_REVIEW),
            "phase5a_control_review": rel(PHASE5A_CONTROL_REVIEW),
        },
        "outputs": {
            "visible_candidate": rel(LOCAL_CANDIDATE),
            "board": rel(BOARD),
            "manifest": rel(MANIFEST),
        },
        "claim_classification": "rejected diagnostic/evidence-only visible seam result; not source-faithful repair",
        "phase5a_decision": {
            "aggregate_success": bool(summary5a.get("aggregate_success")),
            "phase5b_allowed": False,
            "phase5c_renderer_allowed": False,
            "reason": "BMW thresholds failed: target-surface support gain is too small, raw-visible support decreases, no-zbuffer gap increases, and supported components remain short.",
            "bmw_improvements": phase5a_bmw.get("improvements") or {},
            "bmw_success_checks": phase5a_bmw.get("success_checks") or {},
        },
        "vision_verdict": [
            "The RGB candidate is the best current visible seam image to inspect, but it remains a rejected Phase2 lidar_best diagnostic.",
            "Phase5a BMW review shows added support blocks without continuous raw-visible support; visible overlay is sparse and transition maps do not form a safe GREEN repair band.",
            "Clean control shows the same pattern: fused support increases slightly but raw-visible support degrades and no-zbuffer bands widen.",
        ],
        "safety": {
            "remote_status_exec": "completed earlier through approved non-repo secret path; no secret stored in this package",
            "runtime_secret_access": False,
            "a100": False,
            "vggt_hf_model": False,
            "generation": False,
            "source_replacement": False,
            "red_promotion": False,
        },
        "phase4b_bmw_metrics": ((summary4b.get("by_case") or {}).get(RUN) or {}),
        "phase4b_mean_metrics": summary4b.get("mean") or {},
        "phase5a_bmw_metrics": phase5a_bmw,
        "phase5a_mean_improvements": summary5a.get("mean_improvements") or {},
    }
    scan_text = json.dumps(manifest, ensure_ascii=False)
    hits = secret_hits(scan_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(int(h["count"]) for h in hits), "hits": hits}
    write_board(summary2, summary4b, summary5a, manifest)
    manifest["outputs"]["board_bytes"] = int(BOARD.stat().st_size)
    manifest["outputs"]["visible_candidate_bytes"] = int(LOCAL_CANDIDATE.stat().st_size)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"manifest": rel(MANIFEST), "board": rel(BOARD), "candidate": rel(LOCAL_CANDIDATE)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
