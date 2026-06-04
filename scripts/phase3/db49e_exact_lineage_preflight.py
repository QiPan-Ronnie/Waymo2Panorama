from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract"
MANIFEST = OUT_DIR / "db49e_exact_lineage_preflight_manifest.json"
BOARD = OUT_DIR / "db49e_exact_lineage_preflight_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
SEAMROUTE = ROOT / "scripts" / "phase3" / "_seamroute.py"
DB32 = ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_generated_sky_harmonize_s40.png"
DB34 = ROOT / "deliverables" / "dit360_v2" / "db34_current_best_qa" / "db34_current_best_manifest.json"
DB49B = OUT_DIR / "db49b_sidecar_starter_pack_manifest.json"
DB49C = OUT_DIR / "db49c_source_id_map_feasibility_manifest.json"
DB49D = OUT_DIR / "db49d_seamroute_source_map_instrumentation_manifest.json"
DB49B_OVERLAY = OUT_DIR / "db49b_sidecar_overlay_on_db32.jpg"
DB49D_BOARD = OUT_DIR / "db49d_seamroute_source_map_instrumentation_board.jpg"
DB47E = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining" / "db47e_final_candidate_review_manifest.json"
RUNTIME_SECRET_FILE = ROOT / "runtime" / "active_url.json"
LOCAL_TARGET_LOG = ROOT / "data" / "argoverse2" / "val" / "02a00399-3857-444e-8db3-a8f58489c394"

EXPECTED_DB32_SHA256 = "ade90f2bb629abac88e6516d6a2abd0d6785619024c0be4d5a01ea23dc4a8930"
EXPECTED_SOURCE_BASE_SUFFIX = "deliverables\\dit360_v2\\db28_clean_subset_refine\\SR_bmw_db28_a200_final_1024x2048.png"
EXPECTED_REMOTE_DATA_ROOT = "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"
EXPECTED_TAG = "db49e_a200_exact"

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


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def token_hits(paths: list[Path]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or path.suffix.lower() in {".jpg", ".png"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in TOKEN_PATTERNS.items():
            found = pattern.findall(text)
            if found:
                hits.append({"path": rel(path), "pattern": name, "count": len(found)})
    return hits


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
) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(25, 27, 32), outline=(80, 84, 92), width=2)
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
            draw_wrapped(draw, x0 + 10, y0 + 24, f"image load failed: {type(exc).__name__}", 44, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def build_manifest() -> dict[str, Any]:
    brief_text = BRIEF.read_text(encoding="utf-8", errors="replace")
    seamroute_text = SEAMROUTE.read_text(encoding="utf-8", errors="replace")
    db34 = read_json(DB34)
    db49b = read_json(DB49B)
    db49c = read_json(DB49C)
    db49d = read_json(DB49D)
    db47e = read_json(DB47E)

    db32_sha = sha256_file(DB32)
    source_base = str(db34.get("source_base", ""))
    source_lineage_ok = source_base == EXPECTED_SOURCE_BASE_SUFFIX or source_base.replace("/", "\\") == EXPECTED_SOURCE_BASE_SUFFIX
    env_runtime_present = bool(os.environ.get("COLAB_URL")) and bool(os.environ.get("COLAB_TOKEN"))
    runtime_file_present = RUNTIME_SECRET_FILE.exists()
    local_target_data_present = LOCAL_TARGET_LOG.exists()
    secure_runtime_secret_source_present = env_runtime_present or runtime_file_present
    can_run_local = local_target_data_present
    can_run_remote = secure_runtime_secret_source_present
    sidecar_dir = OUT_DIR / "db49e_exact_lineage_sidecars"

    preflight_checks = [
        {
            "id": "db49e_brief_exists",
            "pass": "Phase4 / DB49e" in brief_text and "Exact-lineage source/provenance rerun" in brief_text,
            "evidence": "DB49e brief exists before execution.",
        },
        {
            "id": "db47e_confirmed_a200_source_base",
            "pass": db47e.get("decision", {}).get("confirmed_current_source_sidestep_base_anchor") == 200,
            "evidence": "DB47e confirmed a200 as current source-sidestep base.",
        },
        {
            "id": "db32_sha_unchanged",
            "pass": db32_sha == EXPECTED_DB32_SHA256,
            "evidence": f"DB32 sha256={db32_sha}.",
        },
        {
            "id": "db34_source_base_is_a200",
            "pass": source_lineage_ok,
            "evidence": f"DB34 source_base={source_base}.",
        },
        {
            "id": "db49b_sidecars_available_partial",
            "pass": DB49B.exists() and db49b.get("source_id_map_created") is False,
            "evidence": "DB49b partial generated/unknown/risk sidecars exist and did not fabricate source_id_map.",
        },
        {
            "id": "db49c_source_map_missing_boundary",
            "pass": db49c.get("source_id_map", {}).get("status") == "missing_blocking_not_fabricated",
            "evidence": "DB49c says source_id_map is missing/blocking and not fabricated.",
        },
        {
            "id": "db49d_sidecar_support_present",
            "pass": (
                "--save-source-id-map" in seamroute_text
                and "--sidecar-dir" in seamroute_text
                and "final_source_state_map" in seamroute_text
                and db49d.get("all_checks_passed") is True
            ),
            "evidence": "DB49d default-off source/provenance sidecar support is present.",
        },
        {
            "id": "local_exact_data_available",
            "pass": local_target_data_present,
            "evidence": f"Local target log path exists={local_target_data_present}: {rel(LOCAL_TARGET_LOG)}.",
        },
        {
            "id": "secure_runtime_secret_source_available",
            "pass": secure_runtime_secret_source_present,
            "evidence": (
                "COLAB_URL/COLAB_TOKEN env present="
                f"{env_runtime_present}; non-repo runtime secret file present={runtime_file_present}."
            ),
        },
        {
            "id": "no_secret_values_scanned_into_outputs",
            "pass": True,
            "evidence": "Preflight records only boolean secret-source presence, never secret values.",
        },
    ]

    rerun_allowed = all(c["pass"] for c in preflight_checks[:7]) and (can_run_local or can_run_remote)
    pause_reasons: list[str] = []
    if not local_target_data_present:
        pause_reasons.append("local_target_log_missing")
    if not secure_runtime_secret_source_present:
        pause_reasons.append("secure_runtime_secret_source_missing")

    manifest: dict[str, Any] = {
        "db": "DB-49e",
        "status": "preflight_paused" if not rerun_allowed else "preflight_ready_for_one_exact_rerun",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "exact-lineage-source-map-rerun-preflight-only",
        "purpose": "Gate the exact DB32/a200 lineage source/provenance sidecar rerun without fabricating ownership or leaking runtime secrets.",
        "scope": {
            "cpu_local_preflight": True,
            "local_target_data_present": local_target_data_present,
            "secure_runtime_secret_source_present": secure_runtime_secret_source_present,
            "remote_rerun_executed": False,
            "local_rerun_executed": False,
            "a100_used": False,
            "executor_used": False,
            "hf_or_vggt_used": False,
            "model_inference": False,
            "generation": False,
            "repair": False,
            "source_replacement": False,
            "candidate_pixels_modified": False,
            "permission_change": False,
            "red_promotion": False,
            "output_location": rel(OUT_DIR),
        },
        "lineage": {
            "target_uuid": "02a00399-3857-444e-8db3-a8f58489c394",
            "target_anchor": 200,
            "tag": EXPECTED_TAG,
            "db32_candidate": rel(DB32),
            "db32_sha256": db32_sha,
            "db34_source_base": source_base,
            "db34_previous_best": db34.get("previous_best"),
            "db34_mask": db34.get("mask"),
            "expected_remote_data_root": EXPECTED_REMOTE_DATA_ROOT,
            "local_target_log": rel(LOCAL_TARGET_LOG),
        },
        "dependencies": {
            "db47e_manifest": rel(DB47E),
            "db49b_manifest": rel(DB49B),
            "db49c_manifest": rel(DB49C),
            "db49d_manifest": rel(DB49D),
            "seamroute_script": rel(SEAMROUTE),
        },
        "preflight_checks": preflight_checks,
        "pause_reasons": pause_reasons,
        "planned_exact_rerun": {
            "allowed_now": rerun_allowed,
            "not_executed_reason": None if rerun_allowed else "preconditions_missing",
            "sidecar_dir": rel(sidecar_dir),
            "command_contract_redacted": [
                "python",
                "scripts/phase3/_seamroute.py",
                "--uuid",
                "02a00399-3857-444e-8db3-a8f58489c394",
                "--anchor",
                "200",
                "--tag",
                EXPECTED_TAG,
                "--save-source-id-map",
                "--sidecar-dir",
                "<db49e_sidecar_dir>",
            ],
            "expected_sidecars": [
                f"SR_{EXPECTED_TAG}_routed_source_id_map.png",
                f"SR_{EXPECTED_TAG}_valid_mask.png",
                f"SR_{EXPECTED_TAG}_virtual_center_effect_mask.png",
                f"SR_{EXPECTED_TAG}_ground_reproject_effect_mask.png",
                f"SR_{EXPECTED_TAG}_final_source_state_map.png",
                f"SR_{EXPECTED_TAG}_source_id_overlay.png",
                f"SR_{EXPECTED_TAG}_source_id_sidecar_legend.json",
            ],
        },
        "decision": {
            "db49e_status": "paused_on_preflight_preconditions" if not rerun_allowed else "ready_for_one_exact_rerun",
            "accepted_source_id_map_evidence": False,
            "source_id_map_created": False,
            "candidate_image_selection_changed": False,
            "db32_candidate_modified": False,
            "accepted_source_faithful_repair": False,
            "accepted_original_g_repair": False,
            "ready_for_uncaveated_bosch_training_data": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "claim_boundary": (
                "DB49e preflight does not create DB32 source ownership. "
                "It gates the exact a200 rerun and pauses when local data or secure runtime secret sources are absent."
            ),
            "next_allowed_step": (
                "Run one exact lineage rerun only after COLAB_URL/COLAB_TOKEN env vars or a non-repo runtime secret file are available; "
                "otherwise keep DB49e paused and do not use chat-pasted tokens in commands or artifacts."
            ),
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
    }
    manifest_text = json.dumps(manifest, indent=2)
    strict_hits = token_hits([BRIEF, SEAMROUTE, DB49B, DB49C, DB49D, DB47E])
    manifest["strict_secret_scan"] = {
        "checked_paths": [rel(p) for p in [BRIEF, SEAMROUTE, DB49B, DB49C, DB49D, DB47E]],
        "hits": strict_hits,
        "manifest_has_secret_pattern": any(pattern.search(manifest_text) for pattern in TOKEN_PATTERNS.values()),
    }
    manifest["preflight_checks"].append(
        {
            "id": "strict_secret_scan_pass",
            "pass": not strict_hits and not manifest["strict_secret_scan"]["manifest_has_secret_pattern"],
            "evidence": "Strict token/endpoint scan found no secret-like strings in DB49e inputs or manifest text.",
        }
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2200, 1700), (15, 17, 21))
    draw = ImageDraw.Draw(board)
    decision = manifest["decision"]
    scope = manifest["scope"]

    draw_text(draw, (28, 24), "DB49e Exact-Lineage Source/Provenance Rerun Preflight", size=29)
    draw_text(
        draw,
        (28, 64),
        "CPU/local preflight only - no remote job, no token values, no source_id_map yet",
        fill=(220, 215, 170),
        size=16,
    )

    x = 28
    x = pill(draw, x, 102, decision["db49e_status"], (135, 105, 55), 285)
    x = pill(draw, x, 102, f"local data={scope['local_target_data_present']}", (145, 70, 70), 185)
    x = pill(draw, x, 102, f"runtime secret={scope['secure_runtime_secret_source_present']}", (145, 70, 70), 225)
    x = pill(draw, x, 102, "source_id_map=False", (145, 70, 70), 205)
    x = pill(draw, x, 102, "DB32 unchanged", (65, 125, 85), 170)
    pill(draw, x, 102, "RED promotions=0", (65, 125, 85), 180)

    y = 155
    draw_text(draw, (28, y), "Lineage and decision", size=22)
    y += 34
    for line in [
        f"target: {manifest['lineage']['target_uuid']} anchor {manifest['lineage']['target_anchor']}",
        f"DB32 sha: {manifest['lineage']['db32_sha256']}",
        f"DB34 source base: {manifest['lineage']['db34_source_base']}",
        decision["claim_boundary"],
        decision["next_allowed_step"],
    ]:
        y = draw_wrapped(draw, 42, y, "- " + str(line), 112, fill=(235, 235, 215), size=14)

    y += 10
    draw_text(draw, (28, y), "Preflight checks", size=22)
    y += 34
    for check in manifest["preflight_checks"]:
        fill = (65, 140, 86) if check["pass"] else (170, 60, 60)
        draw.rounded_rectangle((42, y, 120, y + 25), radius=4, fill=fill)
        draw_text(draw, (56, y + 4), "PASS" if check["pass"] else "STOP", size=12)
        y = draw_wrapped(draw, 136, y + 3, f"{check['id']}: {check['evidence']}", 116, size=13)
        y += 7

    x2 = 1190
    draw_text(draw, (x2, 155), "Visual context", size=22)
    image_box(board, DB32, (x2, 190, x2 + 430, 465), "DB32 s40 unchanged")
    image_box(board, DB49B_OVERLAY, (x2 + 460, 190, x2 + 910, 465), "DB49b partial sidecar overlay")
    image_box(board, DB49D_BOARD, (x2, 505, x2 + 910, 815), "DB49d sidecar contract")

    y2 = 855
    draw_text(draw, (x2, y2), "Planned sidecars if one rerun is allowed", size=22)
    y2 += 36
    for sidecar in manifest["planned_exact_rerun"]["expected_sidecars"]:
        y2 = draw_wrapped(draw, x2 + 18, y2, "- " + sidecar, 78, fill=(215, 230, 240), size=14)

    y2 += 10
    draw_text(draw, (x2, y2), "Pause reasons", size=22)
    y2 += 34
    if manifest["pause_reasons"]:
        for reason in manifest["pause_reasons"]:
            y2 = draw_wrapped(draw, x2 + 18, y2, "- " + reason, 78, fill=(255, 210, 170), size=14)
    else:
        y2 = draw_wrapped(draw, x2 + 18, y2, "- none", 78, fill=(210, 245, 210), size=14)

    y3 = 1390
    draw_text(draw, (28, y3), "Claim boundary", size=22)
    y3 += 34
    for line in [
        "Preflight-only output is not a source_id_map.",
        "Generated sky, out-of-FOV, DB41 abstain, invalid, and VC composite/effect pixels must not be labeled as camera-owned.",
        "DB32 remains caveated handoff; no original-G repair and no uncaveated Bosch training-data claim.",
    ]:
        y3 = draw_wrapped(draw, 42, y3, "- " + line, 118, fill=(235, 225, 190), size=14)

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> None:
    manifest = build_manifest()
    build_board(manifest)
    print(f"wrote {MANIFEST}")
    print(f"wrote {BOARD}")
    print(json.dumps({
        "status": manifest["status"],
        "pause_reasons": manifest["pause_reasons"],
        "decision": manifest["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
