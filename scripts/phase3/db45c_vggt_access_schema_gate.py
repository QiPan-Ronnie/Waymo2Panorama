#!/usr/bin/env python
"""Build DB45c VGGT access update and evidence-schema gate artifacts.

DB45c records the changed HF access fact after DB45a: the Commercial VGGT
files are now reachable by HEAD. This is still not a model run. The route must
remain evidence-only until the runtime, cache, extractor schema, and DB45b
negative-control guardrails are satisfied.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit"
DB45A = OUT_DIR / "db45a_vggt_feasibility_manifest.json"
DB45B = OUT_DIR / "db45b_evidence_permission_calibration_manifest.json"
RUN_VGGT = ROOT / "scripts" / "phase3" / "run_vggt_multi_anchor.py"
MANIFEST = OUT_DIR / "db45c_vggt_access_schema_gate_manifest.json"
BOARD = OUT_DIR / "db45c_vggt_access_schema_gate_board.jpg"


# Latest bounded checks from 2026-06-04. Tokens are intentionally not stored.
CURRENT_HF_CHECK = {
    "source": "local Hugging Face whoami/model metadata/config HEAD recheck; token used only as runtime secret",
    "whoami_status": 200,
    "model_api_status": 200,
    "model": "facebook/VGGT-1B-Commercial",
    "gated": "manual",
    "has_config": True,
    "large_files": ["model.safetensors"],
    "head_config_json_status": 200,
    "download_attempted": False,
}

CURRENT_REMOTE_PROBE = {
    "source": "Colab Direct one-shot Python readiness probe",
    "job_id": "434b5f311a2349b295eabf8b54c3c8b4",
    "cwd": "/content/waymo2panorama",
    "repo_head": "d544214",
    "repo_dirty": False,
    "imports": {
        "torch": True,
        "vggt": False,
        "numpy": True,
        "cv2": True,
        "PIL": True,
        "pandas": True,
        "pyarrow": True,
        "scipy": True,
    },
    "cache": {
        "/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt/vggt-repo.tar.zst": {
            "exists": True,
            "size": 0,
        },
        "/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt/restore.sh": {
            "exists": True,
            "size": 1100,
        },
        "/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt/tar_cache_log.txt": {
            "exists": True,
            "size": 2219,
        },
    },
    "disk_free_gb": {
        "/content": 64.35,
        "/content/drive/MyDrive/koi_waymo2pano_colab": 61.13,
    },
    "install_attempted": False,
    "model_download_attempted": False,
    "model_inference_attempted": False,
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def previous_hf_status(db45a: dict) -> int | None:
    return (
        db45a.get("remote_checks", {})
        .get("hf_access_check", {})
        .get("head_config_json_status")
    )


def script_uses_uniform_confidence() -> bool:
    if not RUN_VGGT.exists():
        return False
    text = RUN_VGGT.read_text(encoding="utf-8")
    return "np.ones" in text and "VGGT: no conf" in text


def db45b_guardrails(db45b: dict) -> dict:
    decision = db45b.get("decision", {})
    rows = db45b.get("rows", [])
    red_rows = [
        r
        for r in rows
        if r.get("calibrated_evidence_state") == "RED"
        or r.get("evidence_state_db45_v0") == "RED"
    ]
    checks = db45b.get("checks", [])
    return {
        "accepted_evidence_type": decision.get("accepted_evidence_type", "permission-calibration-only"),
        "gate_pass": bool(decision.get("gate_pass", db45b.get("gate_pass"))),
        "rows": len(rows),
        "red_control_count": len(red_rows),
        "all_checks_pass": all(bool(c.get("pass")) for c in checks) if checks else None,
        "permission_state_changes": decision.get("permission_state_changes", "none"),
        "red_promotions": decision.get("red_promotions", []),
        "rules": [
            "target-surface support is required",
            "flow-only cannot promote RED",
            "detector-clean cannot promote RED",
            "case-level depth/parallax cannot promote target ROI",
            "source-sidestep is not original-source repair",
            "best-flow pair cannot launder weak target-pair evidence",
        ],
        "mandatory_negative_controls": ["DB25", "DB41 right", "DB41 lower-right", "DB36", "DB40"],
    }


def build_evidence_schema() -> list[dict[str, object]]:
    return [
        {
            "field": "segment_id",
            "required": True,
            "purpose": "Join the VGGT reducer to the frozen DB45 8-control set.",
        },
        {
            "field": "roi_xyxy",
            "required": True,
            "purpose": "Target ROI only; case-level confidence cannot promote a target surface.",
        },
        {
            "field": "source_pair_or_cam_set",
            "required": True,
            "purpose": "Identify which raw cameras support the target surface.",
        },
        {
            "field": "valid_point_fraction",
            "required": True,
            "purpose": "Finite VGGT point/depth coverage inside the ROI; zero or sparse support cannot promote.",
        },
        {
            "field": "multi_view_consistency_score",
            "required": True,
            "purpose": "Evidence must come from agreement across source views, not a single hallucinated surface.",
        },
        {
            "field": "target_surface_overlap",
            "required": True,
            "purpose": "Support must overlap road/curb/line/object surface being routed.",
        },
        {
            "field": "occlusion_or_no_evidence_flag",
            "required": True,
            "purpose": "Explicit abstain flag for no-evidence or occluded regions.",
        },
        {
            "field": "raw_or_lidar_consistency",
            "required": True,
            "purpose": "VGGT cannot override raw-camera/LiDAR evidence conflicts.",
        },
        {
            "field": "confidence_source",
            "required": True,
            "purpose": "Must name real validity/consistency source; uniform constants are rejected.",
        },
        {
            "field": "db45b_guard_result",
            "required": True,
            "purpose": "Permission delta after DB45b guardrails; RED promotion requires target-surface support.",
        },
    ]


def build_checks(db45a: dict, db45b: dict) -> list[dict[str, object]]:
    prev_status = previous_hf_status(db45a)
    uniform_conf = script_uses_uniform_confidence()
    guards = db45b_guardrails(db45b)
    cache = CURRENT_REMOTE_PROBE["cache"]
    tar = cache["/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt/vggt-repo.tar.zst"]

    def chk(check_id: str, passed: bool, severity: str, evidence: str) -> dict[str, object]:
        return {"id": check_id, "pass": bool(passed), "severity": severity, "evidence": evidence}

    return [
        chk(
            "hf_access_blocker_cleared",
            prev_status == 403 and CURRENT_HF_CHECK["head_config_json_status"] == 200,
            "info",
            f"DB45a config HEAD={prev_status}; DB45c recheck config HEAD=200. Commercial file access is now approved.",
        ),
        chk(
            "token_not_stored",
            True,
            "scope",
            "HF token was used only as a runtime secret for the recheck and is not present in DB45c artifacts.",
        ),
        chk(
            "remote_repo_current",
            CURRENT_REMOTE_PROBE["repo_head"] != "d544214",
            "blocker",
            "Remote repo head is still d544214, older than local DB43-DB45c commits.",
        ),
        chk(
            "vggt_import_available",
            CURRENT_REMOTE_PROBE["imports"].get("vggt") is True,
            "blocker",
            "Current Colab base Python still cannot import vggt.",
        ),
        chk(
            "vggt_repo_cache_nonzero",
            tar["exists"] and int(tar["size"] or 0) > 0,
            "blocker",
            "VGGT repo cache tarball exists but is 0 bytes, so cache restore is not valid.",
        ),
        chk(
            "verified_checkpoint_cache_present",
            False,
            "blocker",
            "No verified local VGGT checkpoint cache was recorded. HF access now permits a download, but DB45c forbids download/inference.",
        ),
        chk(
            "existing_wrapper_confidence_evidential",
            not uniform_conf,
            "blocker",
            "Existing run_vggt_multi_anchor.py writes uniform np.ones confidence, which DB45b cannot accept as geometry evidence.",
        ),
        chk(
            "db45b_guardrails_available",
            guards["gate_pass"] and not guards["red_promotions"],
            "info",
            "DB45b guardrails are available and recorded no RED promotions.",
        ),
        chk(
            "no_model_action",
            not CURRENT_REMOTE_PROBE["install_attempted"]
            and not CURRENT_REMOTE_PROBE["model_download_attempted"]
            and not CURRENT_REMOTE_PROBE["model_inference_attempted"]
            and not CURRENT_HF_CHECK["download_attempted"],
            "scope",
            "DB45c performed no install, model download, inference, renderer, or repaired ERP generation.",
        ),
    ]


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    width: int,
    color: tuple[int, int, int],
    size: int = 14,
    line_gap: int = 6,
) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw.text((x, y), line, fill=color, font=font(size))
        y += size + line_gap
    return y


def draw_pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    label: str,
    fill: tuple[int, int, int],
    text_fill: tuple[int, int, int] = (255, 255, 255),
) -> None:
    draw.rounded_rectangle(xy, radius=6, fill=fill)
    draw.text((xy[0] + 10, xy[1] + 7), label, fill=text_fill, font=font(14))


def build_board(manifest: dict) -> None:
    board = Image.new("RGB", (1700, 1180), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text(
        (24, 18),
        "DB45c VGGT Commercial access cleared; evidence route still blocked",
        fill=(255, 255, 255),
        font=font(28),
    )
    draw.text(
        (24, 54),
        "Evidence-only readiness/schema gate. No install, no download, no inference, no repair.",
        fill=(220, 220, 220),
        font=font(16),
    )

    y = 98
    draw_pill(draw, (24, y, 250, y + 34), "HF file access: 200", (34, 128, 76))
    draw_pill(draw, (270, y, 520, y + 34), "accepted evidence: none", (142, 74, 32))
    draw_pill(draw, (540, y, 755, y + 34), "RED promotions: 0", (78, 78, 78))
    y += 58

    draw.text((24, y), "Access delta", fill=(255, 255, 255), font=font(21))
    y += 30
    access_lines = [
        "DB45a Commercial config HEAD: 403",
        "DB45c Commercial config HEAD: 200",
        "model metadata visible: gated=manual, large file=model.safetensors",
        "token was not written to artifacts",
    ]
    for line in access_lines:
        y = draw_wrapped(draw, 36, y, "- " + line, 96, (230, 230, 230), 14)
    y += 10

    draw.text((24, y), "Current blockers", fill=(255, 255, 255), font=font(21))
    y += 30
    blockers = [c for c in manifest["checks"] if c["severity"] == "blocker" and not c["pass"]]
    for c in blockers:
        draw.rectangle((36, y + 2, 48, y + 14), fill=(190, 72, 72))
        y = draw_wrapped(draw, 60, y, f"{c['id']}: {c['evidence']}", 112, (235, 235, 235), 14)
        y += 4

    x2 = 890
    y2 = 98
    draw.text((x2, y2), "DB45c schema requirements", fill=(255, 255, 255), font=font(21))
    y2 += 32
    schema = manifest["vggt_roi_evidence_schema"]
    for item in schema[:10]:
        draw.rectangle((x2, y2 + 4, x2 + 10, y2 + 14), fill=(82, 142, 198))
        y2 = draw_wrapped(draw, x2 + 18, y2, f"{item['field']}: {item['purpose']}", 74, (232, 232, 232), 13, 5)
        y2 += 3

    y2 += 12
    draw.text((x2, y2), "Guardrail verdict", fill=(255, 255, 255), font=font(21))
    y2 += 32
    verdict_lines = [
        "VGGT access blocker cleared, but no VGGT geometry evidence is accepted.",
        "Old uniform-confidence wrapper remains rejected.",
        "DB25 / DB41 / DB36 / DB40 remain RED controls.",
        "DB32 remains source-sidestep handoff with caveats.",
        "Next allowed work requires a new bounded extractor/setup brief or DB45 sub-scope.",
    ]
    for line in verdict_lines:
        y2 = draw_wrapped(draw, x2, y2, "- " + line, 80, (235, 235, 235), 14)

    y3 = 960
    draw.line((24, y3 - 20, 1660, y3 - 20), fill=(80, 80, 80), width=1)
    draw.text((24, y3), "Checks", fill=(255, 255, 255), font=font(21))
    y3 += 34
    x = 24
    for i, c in enumerate(manifest["checks"]):
        color = (48, 140, 82) if c["pass"] else ((190, 72, 72) if c["severity"] == "blocker" else (150, 112, 52))
        label = "PASS" if c["pass"] else "STOP"
        draw_pill(draw, (x, y3, x + 68, y3 + 30), label, color)
        y_text = draw_wrapped(draw, x + 78, y3 + 2, c["id"], 34, (240, 240, 240), 13, 4)
        x += 330
        if (i + 1) % 5 == 0:
            x = 24
            y3 = max(y3 + 54, y_text + 8)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db45a = read_json(DB45A)
    db45b = read_json(DB45B)
    checks = build_checks(db45a, db45b)
    blockers = [c for c in checks if c["severity"] == "blocker" and not c["pass"]]
    guards = db45b_guardrails(db45b)

    manifest = {
        "db": "DB-45c",
        "status": "vggt_access_schema_gate",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Record that VGGT Commercial file access is approved now, while keeping DB45 evidence admission blocked until runtime/cache/extractor/schema guardrails pass.",
        "scope": {
            "install_packages": False,
            "download_models": False,
            "model_inference": False,
            "panorama_generation": False,
            "panorama_repair": False,
            "source_replacement": False,
            "uses_hf_token_as_runtime_secret_only": True,
            "uses_colab_direct_probe_only": True,
        },
        "refs": {
            "db45a_manifest": rel(DB45A),
            "db45b_manifest": rel(DB45B),
            "run_vggt_script": rel(RUN_VGGT),
        },
        "access_delta": {
            "db45a_head_config_json_status": previous_hf_status(db45a),
            "db45c_head_config_json_status": CURRENT_HF_CHECK["head_config_json_status"],
            "commercial_file_access_cleared": CURRENT_HF_CHECK["head_config_json_status"] == 200,
            "note": "This clears only the HF gated-file blocker. It does not create accepted geometry evidence.",
        },
        "current_hf_check": CURRENT_HF_CHECK,
        "current_remote_probe": CURRENT_REMOTE_PROBE,
        "db45b_guardrails": guards,
        "vggt_roi_evidence_schema": build_evidence_schema(),
        "checks": checks,
        "route_state": "access_cleared_but_not_evidence_ready" if blockers else "ready_for_separate_scoped_vggt_evidence_job",
        "decision": {
            "accepted_evidence_type": "readiness-and-schema-only",
            "accepted_db45_geometry_evidence": False,
            "vggt_model_negative": False,
            "vggt_evidence_job_runnable_now": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "db45_remains_running": True,
            "why": "Commercial file access is approved now, but the current runtime still has stale code, no vggt import, an invalid 0-byte VGGT repo cache, no verified checkpoint cache, and the existing wrapper emits uniform confidence rather than auditable target-ROI evidence.",
            "next_allowed_step": "Open a new bounded DB45 sub-scope before any install/download/inference. That sub-scope must sync the extractor, prepare dependencies explicitly, avoid uniform confidence, run only the frozen controls first, and stop on DB45b kill criteria.",
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    build_board(manifest)
    print(json.dumps({"manifest": rel(MANIFEST), "board": rel(BOARD), "route_state": manifest["route_state"]}, indent=2))


if __name__ == "__main__":
    main()
