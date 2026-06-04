from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining"
MANIFEST = OUT_DIR / "db47f_fixed_universe_exact_closure_preflight_manifest.json"
BOARD = OUT_DIR / "db47f_fixed_universe_exact_closure_preflight_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
DB47D = OUT_DIR / "db47d_exact_same_log_review_manifest.json"
DB47E = OUT_DIR / "db47e_final_candidate_review_manifest.json"
DB47E_BOARD = OUT_DIR / "db47e_final_candidate_review_board.jpg"
DB51 = ROOT / "deliverables" / "dit360_v2" / "db51_egsr_target_acquisition" / "db51_egsr_target_acquisition_manifest.json"
DB51_BOARD = ROOT / "deliverables" / "dit360_v2" / "db51_egsr_target_acquisition" / "db51_egsr_target_acquisition_board.jpg"
DB28_DIR = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine"
DB32 = ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_generated_sky_harmonize_s40.png"
DB41 = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_manifest.json"
DB41_BOARD = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_board.jpg"
DB25 = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "db25_longline_summary.json"
DB25_BOARD = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "db25_longline_evidence_montage.jpg"

RUNTIME_SECRET_FILE = ROOT / "runtime" / "active_url.json"
LOCAL_TARGET_LOG = ROOT / "data" / "argoverse2" / "val" / "02a00399-3857-444e-8db3-a8f58489c394"
TARGET_UUID = "02a00399-3857-444e-8db3-a8f58489c394"

FIXED_TARGETS = [
    {"candidate_id": "02a00399_a0201", "anchor": 201, "bucket": "strict_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0209", "anchor": 209, "bucket": "strict_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0210", "anchor": 210, "bucket": "strict_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0211", "anchor": 211, "bucket": "strict_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0031", "anchor": 31, "bucket": "relaxed_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0038", "anchor": 38, "bucket": "relaxed_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0040", "anchor": 40, "bucket": "relaxed_review_bucket", "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0105", "anchor": 105, "bucket": "strict_review_bucket", "required": ["final"]},
]

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


def exact_paths(anchor: int) -> dict[str, Path]:
    return {
        "compare": DB28_DIR / f"SR_bmw_db28_a{anchor}_compare.jpg",
        "final": DB28_DIR / f"SR_bmw_db28_a{anchor}_final_1024x2048.png",
    }


def image_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        with Image.open(path) as img:
            return {"exists": True, "size": list(img.size), "bytes": path.stat().st_size}
    except Exception as exc:
        return {"exists": True, "image_read_error": type(exc).__name__, "bytes": path.stat().st_size}


def target_status() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in FIXED_TARGETS:
        paths = exact_paths(int(target["anchor"]))
        available = {name: path.exists() for name, path in paths.items()}
        missing_required = [name for name in target["required"] if not available[name]]
        rows.append(
            {
                **target,
                "exact_assets": {
                    "compare": rel(paths["compare"]) if available["compare"] else None,
                    "final": rel(paths["final"]) if available["final"] else None,
                },
                "asset_stats": {name: image_stats(path) for name, path in paths.items()},
                "available_required": [name for name in target["required"] if available[name]],
                "missing_required": missing_required,
                "closure_status": "closed_local_assets_present" if not missing_required else "open_missing_exact_assets",
                "claim_boundary": "source-selection exact evidence only; not local seam repair and not original-G repair",
            }
        )
    return rows


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=(235, 235, 235), size=16) -> None:
    draw.text(xy, str(text), fill=fill, font=font(size))


def draw_wrapped(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, width: int, fill=(235, 235, 235), size: int = 14) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + 6
    return y


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, fill: tuple[int, int, int], w: int) -> int:
    draw.rounded_rectangle((x, y, x + w, y + 34), radius=5, fill=fill, outline=(185, 185, 185))
    draw_text(draw, (x + 10, y + 8), label, size=13)
    return x + w + 12


def image_box(board: Image.Image, path: Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((x1 - x0 - 16, y1 - y0 - 44))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception as exc:
            draw_wrapped(draw, x0 + 10, y0 + 24, f"load failed: {type(exc).__name__}", 42, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def build_manifest() -> dict[str, Any]:
    brief_text = BRIEF.read_text(encoding="utf-8", errors="replace")
    db47d = read_json(DB47D)
    db47e = read_json(DB47E)
    db51 = read_json(DB51)
    db41 = read_json(DB41)
    db25 = read_json(DB25)
    targets = target_status()

    target_ids = [row["candidate_id"] for row in targets]
    expected_ids = [target["candidate_id"] for target in FIXED_TARGETS]
    unresolved = [row for row in targets if row["missing_required"]]
    by_missing = Counter(part for row in targets for part in row["missing_required"])

    env_runtime_present = bool(os.environ.get("COLAB_URL")) and bool(os.environ.get("COLAB_TOKEN"))
    runtime_file_present = RUNTIME_SECRET_FILE.exists()
    secure_runtime_secret_source_present = env_runtime_present or runtime_file_present
    local_target_data_present = LOCAL_TARGET_LOG.exists()
    closure_inputs_available = not unresolved
    closure_can_run = (local_target_data_present or secure_runtime_secret_source_present) and len(targets) == 8

    hard_checks = [
        {
            "id": "db47f_brief_exists",
            "pass": "Phase5 / DB47f" in brief_text and "Fixed-universe exact source-selection closure preflight" in brief_text,
            "evidence": "DB47f brief exists before execution.",
        },
        {
            "id": "db51_recommends_db47f",
            "pass": db51.get("decision", {}).get("recommended_next_single_brief", "").startswith("DB47f fixed-universe"),
            "evidence": "DB51 ranked DB47f as the next single brief if secure runtime/data preconditions pass.",
        },
        {
            "id": "fixed_universe_is_eight_targets",
            "pass": len(targets) == 8 and target_ids == expected_ids,
            "evidence": f"targets={target_ids}.",
        },
        {
            "id": "inherits_db47_missing_holds",
            "pass": set(db47e.get("scope", {}).get("missing_exact_holds_preserved", [])) == set(expected_ids[:7]),
            "evidence": "Seven DB47e missing-exact holds plus a105 final gap are the only closure targets.",
        },
        {
            "id": "stops_without_secure_runtime_or_local_data",
            "pass": not closure_can_run,
            "evidence": (
                f"local_target_data={local_target_data_present}; "
                f"secure_runtime_secret_source={secure_runtime_secret_source_present}; no remote job submitted."
            ),
        },
        {
            "id": "db41_db25_not_promoted",
            "pass": (
                db41.get("threshold_results", {}).get("lower_right_roi", {}).get("passes_db41_gate") is False
                and db25.get("recommendation", "").startswith("abstain")
            ),
            "evidence": "DB41 lower-right gate remains false and DB25 remains abstain.",
        },
        {
            "id": "no_repair_generation_or_source_replacement",
            "pass": True,
            "evidence": "DB47f preflight creates only a manifest/board; no exact fetch/rerun/repair/generation/source replacement.",
        },
    ]

    scan_paths = [Path(__file__), BRIEF, DB47D, DB47E, DB51, DB41, DB25]
    hits = token_hits(scan_paths)
    manifest_preview = {
        "targets": targets,
        "runtime_file_present": runtime_file_present,
        "env_runtime_present": env_runtime_present,
    }
    manifest_has_secret_pattern = any(pattern.search(json.dumps(manifest_preview)) for pattern in TOKEN_PATTERNS.values())
    hard_checks.append(
        {
            "id": "strict_secret_scan_pass",
            "pass": not hits and not manifest_has_secret_pattern,
            "evidence": "Strict token/endpoint scan found no secret-like strings in DB47f inputs or manifest preview.",
        }
    )

    status = "preflight_ready_for_exact_closure" if closure_can_run and closure_inputs_available else "preflight_paused"
    if not closure_can_run:
        pause_reason = "secure_runtime_or_local_data_precondition_missing"
    elif not closure_inputs_available:
        pause_reason = "exact_assets_still_missing"
    else:
        pause_reason = None

    manifest: dict[str, Any] = {
        "db": "DB-47f",
        "status": status,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "fixed-universe-exact-closure-preflight-only",
        "purpose": "Gate exact closure for the fixed DB47/DB51 source-selection gaps without expanding the universe or leaking secrets.",
        "scope": {
            "cpu_local_preflight": True,
            "fixed_target_count": len(targets),
            "new_dataset_scan": False,
            "exact_asset_fetch_or_rerun_executed": False,
            "remote_or_executor_used": False,
            "a100_used": False,
            "hf_or_vggt_used": False,
            "model_inference": False,
            "seamroute_or_renderer_executed": False,
            "panorama_repair": False,
            "generation": False,
            "source_replacement": False,
            "source_id_map_created": False,
            "permission_change": False,
            "red_promotion": False,
            "output_location": rel(OUT_DIR),
        },
        "inputs": {
            "decision_brief": rel(BRIEF),
            "db47d_manifest": rel(DB47D),
            "db47e_manifest": rel(DB47E),
            "db51_manifest": rel(DB51),
            "db41_manifest": rel(DB41),
            "db25_summary": rel(DB25),
        },
        "fixed_target_contract": {
            "target_uuid": TARGET_UUID,
            "targets": targets,
            "unresolved_target_count": len(unresolved),
            "missing_required_counts": dict(by_missing),
            "closure_inputs_available_locally": closure_inputs_available,
            "max_anchors": 8,
            "universe_locked_to_db51_gaps": True,
        },
        "preconditions": {
            "local_target_data_present": local_target_data_present,
            "env_colab_url_and_token_present": env_runtime_present,
            "runtime_secret_file_present": runtime_file_present,
            "secure_runtime_secret_source_present": secure_runtime_secret_source_present,
            "closure_can_run_without_chat_pasted_token": closure_can_run,
            "pause_reason": pause_reason,
        },
        "redacted_exact_closure_contract": {
            "allowed_only_if_preconditions_pass": True,
            "allowed_anchor_count_max": 8,
            "allowed_anchors": [row["anchor"] for row in targets],
            "expected_outputs_per_anchor": ["compare", "final"],
            "remote_secret_policy": "Use only COLAB_URL/COLAB_TOKEN env vars or approved non-repo runtime secret source; never chat-pasted token command/artifact use.",
            "claim_policy": "Source-selection sidestep evidence only; not original-G repair, not source-faithful local repair, not source_id_map evidence.",
        },
        "hard_checks": hard_checks,
        "strict_secret_scan": {
            "checked_paths": [rel(p) for p in scan_paths],
            "hits": hits,
            "manifest_preview_has_secret_pattern": manifest_has_secret_pattern,
        },
        "decision": {
            "db47f_status": status,
            "accepted_source_selection_preflight": True,
            "accepted_exact_closure": False,
            "selected_new_candidate": False,
            "candidate_image_selection_changed": False,
            "accepted_source_faithful_repair": False,
            "accepted_original_g_repair": False,
            "accepted_source_id_map_evidence": False,
            "ready_for_uncaveated_bosch_training_data": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "recommended_next": (
                "Provide secure runtime/data through env or approved non-repo secret source, then run one bounded DB47f exact closure batch; "
                "otherwise keep DB47f paused and do not run operators."
            ),
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2200, 1650), (15, 17, 21))
    draw = ImageDraw.Draw(board)
    decision = manifest["decision"]
    pre = manifest["preconditions"]
    contract = manifest["fixed_target_contract"]

    draw_text(draw, (28, 24), "DB47f Fixed-Universe Exact Source-Selection Closure Preflight", size=28)
    draw_text(draw, (28, 63), "CPU/local only - no exact fetch, no remote job, no repair, no token values", fill=(220, 215, 170), size=16)

    x = 28
    x = pill(draw, x, 100, decision["db47f_status"], (135, 105, 55), 235)
    x = pill(draw, x, 100, f"targets={contract['target_uuid'][-8:]} x{contract['target_count'] if 'target_count' in contract else manifest['scope']['fixed_target_count']}", (70, 95, 145), 170)
    x = pill(draw, x, 100, f"unresolved={contract['unresolved_target_count']}", (145, 70, 70), 165)
    x = pill(draw, x, 100, f"local data={pre['local_target_data_present']}", (145, 70, 70), 165)
    x = pill(draw, x, 100, f"runtime secret={pre['secure_runtime_secret_source_present']}", (145, 70, 70), 215)
    x = pill(draw, x, 100, "RED promotions=0", (65, 125, 85), 180)
    pill(draw, x, 100, "no token use", (65, 125, 85), 155)

    y = 155
    draw_text(draw, (28, y), "Fixed target closure table", size=22)
    y += 34
    header = f"{'candidate':<18} {'anchor':<6} {'bucket':<22} {'compare':<8} {'final':<8} missing"
    draw_text(draw, (42, y), header, fill=(210, 220, 240), size=14)
    y += 25
    for row in contract["targets"]:
        compare = "yes" if row["asset_stats"]["compare"].get("exists") else "no"
        final = "yes" if row["asset_stats"]["final"].get("exists") else "no"
        missing = ",".join(row["missing_required"]) if row["missing_required"] else "none"
        fill = (255, 210, 170) if row["missing_required"] else (210, 245, 210)
        line = f"{row['candidate_id']:<18} {row['anchor']:<6} {row['bucket']:<22} {compare:<8} {final:<8} {missing}"
        draw_text(draw, (42, y), line, fill=fill, size=14)
        y += 23

    y += 20
    draw_text(draw, (28, y), "Hard checks", size=22)
    y += 34
    for check in manifest["hard_checks"]:
        fill = (65, 140, 86) if check["pass"] else (170, 60, 60)
        draw.rounded_rectangle((42, y, 120, y + 25), radius=4, fill=fill)
        draw_text(draw, (56, y + 4), "PASS" if check["pass"] else "STOP", size=12)
        y = draw_wrapped(draw, 136, y + 3, f"{check['id']}: {check['evidence']}", 116, size=13)
        y += 7

    y += 10
    draw_text(draw, (28, y), "Decision", size=22)
    y += 34
    for line in [
        "accepted preflight only; exact closure not executed",
        f"pause reason: {pre['pause_reason']}",
        "next: secure runtime/data through env or approved non-repo secret source, then one bounded 8-anchor closure batch",
        "not source-faithful repair, not original-G repair, not source_id_map evidence, not Bosch training-ready",
    ]:
        y = draw_wrapped(draw, 42, y, "- " + line, 116, fill=(235, 235, 215), size=14)

    x2 = 1180
    draw_text(draw, (x2, 155), "Evidence context", size=22)
    image_box(board, DB47E_BOARD, (x2, 190, x2 + 430, 465), "DB47e a200/a204/a105 review")
    image_box(board, DB51_BOARD, (x2 + 465, 190, x2 + 910, 465), "DB51 acquisition queue")
    image_box(board, DB41_BOARD, (x2, 505, x2 + 430, 790), "DB41 right/lower-right abstain")
    image_box(board, DB25_BOARD, (x2 + 465, 505, x2 + 910, 790), "DB25 longline abstain")
    image_box(board, DB32, (x2, 830, x2 + 910, 1130), "DB32 s40 caveated handoff, not ceiling")

    y2 = 1170
    draw_text(draw, (x2, y2), "Allowed closure contract if preconditions pass", size=22)
    y2 += 36
    for line in [
        "Exactly 8 fixed targets, max one bounded closure batch.",
        "Expected outputs per anchor: exact compare and final source-selection candidate assets.",
        "Use only env/runtime-secret source; no chat-pasted token command or artifact use.",
        "Every candidate remains source-selection sidestep evidence until same-ROI visual closure accepts it.",
        "DB41/DB25 remain negative boundaries; no RED/no-evidence promotion.",
    ]:
        y2 = draw_wrapped(draw, x2 + 18, y2, "- " + line, 78, fill=(215, 230, 240), size=14)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> int:
    manifest = build_manifest()
    build_board(manifest)
    summary = {
        "db": manifest["db"],
        "status": manifest["status"],
        "unresolved_target_count": manifest["fixed_target_contract"]["unresolved_target_count"],
        "local_target_data_present": manifest["preconditions"]["local_target_data_present"],
        "secure_runtime_secret_source_present": manifest["preconditions"]["secure_runtime_secret_source_present"],
        "strict_secret_hits": len(manifest["strict_secret_scan"]["hits"]),
        "remote_or_executor_used": manifest["scope"]["remote_or_executor_used"],
        "recommended_next": manifest["decision"]["recommended_next"],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
