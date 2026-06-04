from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db52_secure_runtime_contract"
MANIFEST = OUT_DIR / "db52_secure_runtime_contract_manifest.json"
BOARD = OUT_DIR / "db52_secure_runtime_contract_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
DB47F = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining" / "db47f_fixed_universe_exact_closure_preflight_manifest.json"
DB47F_BOARD = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining" / "db47f_fixed_universe_exact_closure_preflight_board.jpg"
DB51 = ROOT / "deliverables" / "dit360_v2" / "db51_egsr_target_acquisition" / "db51_egsr_target_acquisition_manifest.json"
DB51_BOARD = ROOT / "deliverables" / "dit360_v2" / "db51_egsr_target_acquisition" / "db51_egsr_target_acquisition_board.jpg"
DB32 = ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_generated_sky_harmonize_s40.png"
DB41_BOARD = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_board.jpg"

TARGET_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
LOCAL_TARGET_LOG = ROOT / "data" / "argoverse2" / "val" / TARGET_UUID
REPO_RUNTIME_SECRET_FILE = ROOT / "runtime" / "active_url.json"

FIXED_TARGETS = [
    {"candidate_id": "02a00399_a0201", "anchor": 201, "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0209", "anchor": 209, "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0210", "anchor": 210, "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0211", "anchor": 211, "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0031", "anchor": 31, "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0038", "anchor": 38, "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0040", "anchor": 40, "required": ["compare", "final"]},
    {"candidate_id": "02a00399_a0105", "anchor": 105, "required": ["final"]},
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


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def hf_token_file_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / ".cache" / "huggingface" / "token",
        home / ".huggingface" / "token",
    ]


def runtime_secret_file_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    env_path = os.environ.get("W2P_RUNTIME_SECRET_FILE")
    candidates: list[tuple[str, Path]] = []
    if env_path:
        candidates.append(("W2P_RUNTIME_SECRET_FILE", Path(env_path)))
    candidates.extend(
        [
            ("default_user_home", Path.home() / ".waymo2panorama" / "runtime" / "active_url.json"),
            ("default_localappdata", Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Waymo2Panorama" / "runtime" / "active_url.json"),
            ("repo_runtime_rejected", REPO_RUNTIME_SECRET_FILE),
        ]
    )
    for source, path in candidates:
        in_repo = inside_repo(path)
        exists = path.exists()
        rows.append(
            {
                "source": source,
                "path": rel(path),
                "exists": exists,
                "inside_repo": in_repo,
                "approved_as_secret_source": exists and not in_repo,
                "value_read": False,
                "notes": "repo-local runtime secrets are rejected" if in_repo else "file existence only; content not read",
            }
        )
    return rows


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


def status_box(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, ok: bool, w: int = 250) -> None:
    color = (39, 109, 73) if ok else (126, 65, 49)
    draw.rounded_rectangle((x, y, x + w, y + 40), radius=6, fill=color, outline=(185, 185, 185))
    draw_text(draw, (x + 12, y + 11), label, size=14)


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
            draw_wrapped(draw, x0 + 10, y0 + 26, f"load failed: {type(exc).__name__}", 42, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def target_rows(db47f: dict[str, Any]) -> list[dict[str, Any]]:
    prior = {row["candidate_id"]: row for row in db47f.get("fixed_targets", [])}
    rows: list[dict[str, Any]] = []
    for target in FIXED_TARGETS:
        old = prior.get(target["candidate_id"], {})
        missing = old.get("missing_required", target["required"])
        rows.append(
            {
                **target,
                "missing_required_as_of_db47f": missing,
                "db47f_closure_status": old.get("closure_status", "unknown"),
                "allowed_next_action": "exact compare/final closure evidence only",
                "claim_boundary": "source-selection/sidestep evidence only; not local seam repair",
            }
        )
    return rows


def build_manifest() -> dict[str, Any]:
    brief = BRIEF.read_text(encoding="utf-8", errors="replace")
    db47f = read_json(DB47F)
    db51 = read_json(DB51)

    runtime_files = runtime_secret_file_candidates()
    env_runtime_pair_present = bool(os.environ.get("COLAB_URL")) and bool(os.environ.get("COLAB_TOKEN"))
    approved_runtime_file_present = any(row["approved_as_secret_source"] for row in runtime_files)
    approved_runtime_secret_source_present = env_runtime_pair_present or approved_runtime_file_present

    hf_auth = {
        "HF_TOKEN_env_present": bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")),
        "configured_hf_token_file_present": any(path.exists() for path in hf_token_file_candidates()),
        "token_value_read": False,
        "network_rechecked": False,
        "notes": "HF access was not rechecked in DB52; this contract is CPU/local and network-free.",
    }

    local_target_data_present = LOCAL_TARGET_LOG.exists()
    safe_data_path_available = local_target_data_present or approved_runtime_secret_source_present
    closure_batch_allowed_now = safe_data_path_available

    targets = target_rows(db47f)
    target_ids = [row["candidate_id"] for row in targets]
    expected_ids = [row["candidate_id"] for row in FIXED_TARGETS]

    hard_checks = [
        {
            "id": "db52_brief_exists",
            "pass": (
                "# DB-52: DB47f secure-runtime/data intake contract" in brief
                and (
                    "Status: running" in brief
                    or "Status: accepted / paused pending safe runtime/data path" in brief
                )
            ),
            "evidence": "DB52 brief exists with running or accepted/paused status.",
        },
        {
            "id": "db51_ranked_db47f",
            "pass": db51.get("decision", {}).get("recommended_next_single_brief", "").startswith("DB47f fixed-universe"),
            "evidence": "DB51 recommends DB47f as the next seam-quality route if secure runtime/data is satisfied.",
        },
        {
            "id": "fixed_universe_eight_only",
            "pass": len(targets) == 8 and target_ids == expected_ids,
            "evidence": f"fixed_targets={target_ids}",
        },
        {
            "id": "does_not_accept_chat_pasted_json",
            "pass": True,
            "evidence": "DB52 accepts only env pair or non-repo runtime secret file existence; pasted JSON is not read or stored.",
        },
        {
            "id": "no_secret_values_read",
            "pass": not any(row.get("value_read") for row in runtime_files) and hf_auth["token_value_read"] is False,
            "evidence": "Only boolean presence/path metadata was inspected.",
        },
        {
            "id": "stops_without_safe_data_path",
            "pass": not closure_batch_allowed_now,
            "evidence": f"local_target_data_present={local_target_data_present}; approved_runtime_secret_source_present={approved_runtime_secret_source_present}",
        },
        {
            "id": "no_remote_or_model_action",
            "pass": True,
            "evidence": "No /status, /exec, A100, HF network, VGGT, renderer, exact fetch, repair, generation, or source replacement was run.",
        },
    ]

    manifest: dict[str, Any] = {
        "db": "DB52",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "accepted_contract_paused_for_safe_data_path",
        "evidence_type": "secure-runtime-contract-only",
        "route": "infra/source-selection-precondition",
        "claim_boundaries": {
            "db32_s40": "Bosch-facing handoff candidate with source-sidestep + generated-sky caveats; not fully source-faithful.",
            "g_bmw_pano": "classic BMW failure / diagnostic reference only; not default repair base.",
            "db41_lower_right": "no-evidence/abstain remains; known zero-LiDAR boundary is not promoted.",
            "db47f": "fixed-universe exact source-selection closure evidence only; not original-G repair.",
        },
        "approved_secret_source_policy": {
            "accepted_sources": [
                "COLAB_URL and COLAB_TOKEN process environment variables",
                "W2P_RUNTIME_SECRET_FILE pointing to a non-repo runtime secret file",
                "default non-repo user runtime secret file locations, existence only",
            ],
            "rejected_sources": [
                "chat-pasted tunnel JSON",
                "chat-pasted HF token",
                "repo-local runtime/active_url.json",
                "any command/artifact containing endpoint URLs or token values",
            ],
            "secret_values_read": False,
        },
        "runtime_preconditions": {
            "env_runtime_pair_present": env_runtime_pair_present,
            "runtime_secret_file_candidates": runtime_files,
            "approved_runtime_secret_source_present": approved_runtime_secret_source_present,
            "local_target_data_path": rel(LOCAL_TARGET_LOG),
            "local_target_data_present": local_target_data_present,
            "safe_data_path_available": safe_data_path_available,
        },
        "hf_preconditions": hf_auth,
        "fixed_db47f_targets": targets,
        "launch_contract": {
            "closure_batch_allowed_now": closure_batch_allowed_now,
            "if_allowed": (
                "Run exactly one DB47f fixed-universe closure batch over the 8 listed targets, "
                "producing exact compare/final source-selection evidence only."
            ),
            "if_not_allowed": "Keep DB47f paused; request env or non-repo runtime secret source, or replicate target data locally.",
            "max_scope": {
                "target_count": 8,
                "remote_jobs": 1,
                "allowed_outputs": ["exact compare/final assets", "closure manifest", "closure review board"],
                "forbidden_outputs": [
                    "repaired ERP",
                    "generated pixels",
                    "source replacement",
                    "source_id_map",
                    "permission change",
                    "RED promotion",
                ],
            },
        },
        "actions_executed": {
            "remote_status": False,
            "remote_exec": False,
            "a100": False,
            "network": False,
            "hf_or_vggt": False,
            "model_inference": False,
            "renderer_or_dataset_scan": False,
            "exact_asset_fetch": False,
            "repair_or_generation": False,
            "source_replacement": False,
            "source_id_map": False,
            "permission_change": False,
            "red_promotion": False,
        },
        "hard_checks": hard_checks,
        "hard_checks_pass": all(row["pass"] for row in hard_checks),
        "token_scan_hits": [],
        "dependencies": {
            "db47f_manifest": rel(DB47F),
            "db51_manifest": rel(DB51),
            "db47f_board": rel(DB47F_BOARD),
            "db51_board": rel(DB51_BOARD),
        },
    }
    return manifest


def draw_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2200, 1500), (18, 20, 24))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (40, 34), "DB52 secure-runtime/data contract for DB47f", fill=(245, 245, 245), size=30)
    draw_wrapped(
        draw,
        40,
        80,
        "CPU/local contract only. No token values read. No remote status/exec, A100, HF network, exact fetch, model inference, repair, generation, source replacement, source_id_map, or RED promotion.",
        160,
        fill=(214, 222, 232),
        size=16,
    )

    pre = manifest["runtime_preconditions"]
    x = 40
    y = 150
    status_box(draw, x, y, f"env runtime pair: {pre['env_runtime_pair_present']}", pre["env_runtime_pair_present"])
    status_box(draw, x + 270, y, f"non-repo secret: {pre['approved_runtime_secret_source_present']}", pre["approved_runtime_secret_source_present"])
    status_box(draw, x + 540, y, f"local target data: {pre['local_target_data_present']}", pre["local_target_data_present"])
    status_box(draw, x + 810, y, f"closure allowed: {manifest['launch_contract']['closure_batch_allowed_now']}", manifest["launch_contract"]["closure_batch_allowed_now"])
    status_box(draw, x + 1080, y, f"secret scan: {len(manifest['token_scan_hits'])}", len(manifest["token_scan_hits"]) == 0)

    y = 230
    draw_text(draw, (40, y), "Approved secret-source policy", fill=(245, 245, 245), size=22)
    y += 36
    policy = manifest["approved_secret_source_policy"]
    y = draw_wrapped(draw, 54, y, "Accepted: " + "; ".join(policy["accepted_sources"]), 98, fill=(202, 235, 211), size=14)
    y = draw_wrapped(draw, 54, y + 8, "Rejected: " + "; ".join(policy["rejected_sources"]), 98, fill=(245, 191, 176), size=14)

    draw_text(draw, (40, 445), "Fixed DB47f closure universe", fill=(245, 245, 245), size=22)
    table_y = 485
    draw.rectangle((40, table_y - 8, 1060, table_y + 360), fill=(25, 27, 32), outline=(84, 88, 96))
    headers = ["anchor", "candidate", "required", "missing as of DB47f", "claim"]
    xs = [58, 145, 360, 505, 770]
    for xi, header in zip(xs, headers):
        draw_text(draw, (xi, table_y), header, fill=(198, 212, 232), size=14)
    y = table_y + 30
    for row in manifest["fixed_db47f_targets"]:
        draw.line((50, y - 5, 1050, y - 5), fill=(54, 58, 66))
        draw_text(draw, (xs[0], y), row["anchor"], size=13)
        draw_text(draw, (xs[1], y), row["candidate_id"], size=13)
        draw_text(draw, (xs[2], y), ",".join(row["required"]), size=13)
        draw_text(draw, (xs[3], y), ",".join(row["missing_required_as_of_db47f"]), fill=(245, 191, 176), size=13)
        draw_text(draw, (xs[4], y), "source-selection only", size=13)
        y += 37

    draw_text(draw, (1120, 230), "Launch decision", fill=(245, 245, 245), size=22)
    decision = manifest["launch_contract"]["if_allowed"] if manifest["launch_contract"]["closure_batch_allowed_now"] else manifest["launch_contract"]["if_not_allowed"]
    draw_wrapped(draw, 1120, 270, decision, 82, fill=(235, 235, 235), size=16)

    draw_text(draw, (1120, 410), "Hard checks", fill=(245, 245, 245), size=22)
    y = 450
    for check in manifest["hard_checks"]:
        prefix = "PASS" if check["pass"] else "STOP"
        color = (173, 225, 178) if check["pass"] else (245, 165, 145)
        y = draw_wrapped(draw, 1130, y, f"{prefix} {check['id']}: {check['evidence']}", 88, fill=color, size=13)
        y += 4

    draw_text(draw, (40, 880), "Visual context", fill=(245, 245, 245), size=22)
    image_box(board, DB47F_BOARD, (40, 920, 570, 1400), "DB47f preflight: 8 gaps, paused")
    image_box(board, DB51_BOARD, (610, 920, 1160, 1400), "DB51 queue: DB47f ranked next")
    image_box(board, DB32, (1200, 920, 1640, 1400), "DB32 s40 caveated handoff")
    image_box(board, DB41_BOARD, (1680, 920, 2160, 1400), "DB41 right/lower-right abstain")

    draw_wrapped(
        draw,
        1120,
        760,
        "Claim boundary: DB52 is not exact closure, not seam repair, not source_id_map, not original-G/A1/BEST repair, and not uncaveated Bosch training data. It is only a token-safe contract for a future bounded DB47f closure batch.",
        92,
        fill=(252, 218, 172),
        size=15,
    )
    board.save(BOARD, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    scan_paths = [Path(__file__), BRIEF, MANIFEST]
    manifest["token_scan_hits"] = token_hits(scan_paths)
    manifest["hard_checks"].append(
        {
            "id": "strict_secret_scan_zero",
            "pass": len(manifest["token_scan_hits"]) == 0,
            "evidence": f"hits={len(manifest['token_scan_hits'])} across DB52 script, brief, and manifest.",
        }
    )
    manifest["hard_checks_pass"] = all(row["pass"] for row in manifest["hard_checks"])
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    draw_board(manifest)
    print(json.dumps({"manifest": rel(MANIFEST), "board": rel(BOARD), "status": manifest["status"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
