from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db53_db47f_launch_harness"
MANIFEST = OUT_DIR / "db53_db47f_launch_harness_manifest.json"
BOARD = OUT_DIR / "db53_db47f_launch_harness_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
SEAMROUTE = ROOT / "scripts" / "phase3" / "_seamroute.py"
DB47F = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining" / "db47f_fixed_universe_exact_closure_preflight_manifest.json"
DB52 = ROOT / "deliverables" / "dit360_v2" / "db52_secure_runtime_contract" / "db52_secure_runtime_contract_manifest.json"
DB47F_BOARD = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining" / "db47f_fixed_universe_exact_closure_preflight_board.jpg"
DB52_BOARD = ROOT / "deliverables" / "dit360_v2" / "db52_secure_runtime_contract" / "db52_secure_runtime_contract_board.jpg"
DB28_DIR = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine"

TARGET_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
REMOTE_SEAMROUTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/seamroute"
REMOTE_REPO_WORKDIR_CANDIDATES = [
    "/content/waymo2panorama",
    "/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama",
]
EXPECTED_TARGET_IDS = [
    "02a00399_a0201",
    "02a00399_a0209",
    "02a00399_a0210",
    "02a00399_a0211",
    "02a00399_a0031",
    "02a00399_a0038",
    "02a00399_a0040",
    "02a00399_a0105",
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


def status_pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, ok: bool, w: int = 260) -> None:
    fill = (39, 109, 73) if ok else (126, 65, 49)
    draw.rounded_rectangle((x, y, x + w, y + 38), radius=6, fill=fill, outline=(185, 185, 185))
    draw_text(draw, (x + 11, y + 10), label, size=13)


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


def seamroute_capabilities(text: str) -> dict[str, Any]:
    return {
        "has_uuid_arg": "--uuid" in text,
        "has_anchor_arg": "--anchor" in text,
        "has_tag_arg": "--tag" in text,
        "writes_compare": 'f"SR_{a.tag}_compare.jpg"' in text,
        "writes_final": 'f"SR_{a.tag}_final_1024x2048.png"' in text,
        "writes_sidecars_default_off": "--save-source-id-map" in text and "--sidecar-dir" in text,
        "seamroute_executed": False,
    }


def planned_targets(db47f: dict[str, Any]) -> list[dict[str, Any]]:
    source_rows = db47f.get("fixed_target_contract", {}).get("targets", [])
    rows: list[dict[str, Any]] = []
    for row in source_rows:
        anchor = int(row["anchor"])
        tag = f"bmw_db28_a{anchor}"
        required = list(row.get("required", []))
        expected_remote = {
            "compare": f"{REMOTE_SEAMROUTE_OUT}/SR_{tag}_compare.jpg",
            "final": f"{REMOTE_SEAMROUTE_OUT}/SR_{tag}_final_1024x2048.png",
        }
        expected_local = {
            "compare": rel(DB28_DIR / f"SR_bmw_db28_a{anchor}_compare.jpg"),
            "final": rel(DB28_DIR / f"SR_bmw_db28_a{anchor}_final_1024x2048.png"),
        }
        command = [
            "python",
            "scripts/phase3/_seamroute.py",
            "--uuid",
            TARGET_UUID,
            "--anchor",
            str(anchor),
            "--tag",
            tag,
        ]
        rows.append(
            {
                "candidate_id": row["candidate_id"],
                "anchor": anchor,
                "tag": tag,
                "required_assets": required,
                "dry_run_command_argv": command,
                "remote_workdir_candidates": REMOTE_REPO_WORKDIR_CANDIDATES,
                "expected_remote_outputs": {k: v for k, v in expected_remote.items() if k in required},
                "expected_local_destinations": {k: v for k, v in expected_local.items() if k in required},
                "current_local_missing_required": row.get("missing_required", row.get("missing_required_as_of_db47f", required)),
                "claim_boundary": "exact source-selection evidence only; not local seam repair and not original-G repair",
                "executed": False,
            }
        )
    return rows


def build_manifest() -> dict[str, Any]:
    brief = BRIEF.read_text(encoding="utf-8", errors="replace")
    seamroute_text = SEAMROUTE.read_text(encoding="utf-8", errors="replace")
    db47f = read_json(DB47F)
    db52 = read_json(DB52)
    targets = planned_targets(db47f)
    target_ids = [row["candidate_id"] for row in targets]
    capabilities = seamroute_capabilities(seamroute_text)

    safe_data_path = bool(db52.get("runtime_preconditions", {}).get("safe_data_path_available"))
    dry_run_only = True
    future_launch_allowed_now = safe_data_path and False

    hard_checks = [
        {
            "id": "db53_brief_running",
            "pass": "# DB-53: DB47f token-free launch harness dry-run" in brief and "Status: running" in brief,
            "evidence": "DB53 brief exists before dry-run plan execution.",
        },
        {
            "id": "db47f_fixed_universe_preserved",
            "pass": target_ids == EXPECTED_TARGET_IDS and len(targets) == 8,
            "evidence": f"target_ids={target_ids}",
        },
        {
            "id": "db52_safe_path_still_absent",
            "pass": safe_data_path is False,
            "evidence": "DB52 reports no env runtime pair, no approved non-repo runtime secret file, and no local target data.",
        },
        {
            "id": "seamroute_has_required_args_and_outputs",
            "pass": all(
                capabilities[k]
                for k in ["has_uuid_arg", "has_anchor_arg", "has_tag_arg", "writes_compare", "writes_final"]
            ),
            "evidence": "Static _seamroute.py audit found uuid/anchor/tag args and compare/final output templates.",
        },
        {
            "id": "dry_run_only_no_execution",
            "pass": dry_run_only and not any(row["executed"] for row in targets),
            "evidence": "DB53 generated command argv only; it did not run _seamroute.py or any remote command.",
        },
        {
            "id": "no_source_map_or_repair_claim",
            "pass": True,
            "evidence": "The plan excludes sidecar/source_id_map acceptance and labels closure as source-selection evidence only.",
        },
    ]

    manifest: dict[str, Any] = {
        "db": "DB53",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "dry_run_plan_paused_for_safe_data_path",
        "evidence_type": "db47f-token-free-launch-harness-dry-run-only",
        "purpose": "Freeze a token-free launch harness and command/output mapping for the next DB47f batch without executing it.",
        "scope": {
            "cpu_local_dry_run": True,
            "target_count": len(targets),
            "remote_status": False,
            "remote_exec": False,
            "a100": False,
            "network": False,
            "hf_or_vggt": False,
            "model_inference": False,
            "seamroute_executed": False,
            "exact_asset_fetch_or_copy": False,
            "panorama_repair": False,
            "generation": False,
            "source_replacement": False,
            "source_id_map_created": False,
            "permission_change": False,
            "red_promotion": False,
            "output_location": rel(OUT_DIR),
        },
        "db52_preconditions": {
            "safe_data_path_available": safe_data_path,
            "closure_batch_allowed_now": False,
            "reason": "DB53 is dry-run only even if a future safe path becomes available; actual execution requires a follow-up DB47f closure run.",
        },
        "seamroute_static_capabilities": capabilities,
        "planned_batch": {
            "target_uuid": TARGET_UUID,
            "remote_workdir_candidates": REMOTE_REPO_WORKDIR_CANDIDATES,
            "remote_output_root": REMOTE_SEAMROUTE_OUT,
            "target_ids": target_ids,
            "anchors": [row["anchor"] for row in targets],
            "targets": targets,
            "batch_size_max": 8,
            "remote_jobs_max": 1,
            "execution_policy": "Do not execute until COLAB_URL/COLAB_TOKEN env or approved non-repo runtime secret source, or local target data, is available.",
        },
        "accepted_outputs_if_future_execution_passes": [
            "SR_bmw_db28_a<anchor>_compare.jpg",
            "SR_bmw_db28_a<anchor>_final_1024x2048.png",
        ],
        "explicitly_not_accepted": [
            "source-faithful local repair claim",
            "original-G/A1/BEST repair claim",
            "source_id_map or Bosch provenance sidecar",
            "generated pixels",
            "DB25/DB41 RED promotion",
            "uncaveated Bosch training-data claim",
        ],
        "hard_checks": hard_checks,
        "hard_checks_pass": all(row["pass"] for row in hard_checks),
        "token_scan_hits": [],
        "dependencies": {
            "db47f_manifest": rel(DB47F),
            "db52_manifest": rel(DB52),
            "seamroute_script": rel(SEAMROUTE),
        },
    }
    return manifest


def draw_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2300, 1500), (18, 20, 24))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (40, 32), "DB53 DB47f token-free launch harness dry-run", fill=(245, 245, 245), size=30)
    draw_wrapped(
        draw,
        40,
        78,
        "CPU/local dry run only. Generates a no-secret argv/output mapping for the future fixed 8-anchor DB47f closure batch. No remote/status/exec, no _seamroute execution, no exact fetch/copy, no repair/generation/source_id_map/RED promotion.",
        165,
        fill=(214, 222, 232),
        size=16,
    )

    pre = manifest["db52_preconditions"]
    status_pill(draw, 40, 140, f"safe data path: {pre['safe_data_path_available']}", bool(pre["safe_data_path_available"]))
    status_pill(draw, 320, 140, "dry-run only: True", True)
    status_pill(draw, 600, 140, f"targets: {manifest['scope']['target_count']}", manifest["scope"]["target_count"] == 8)
    status_pill(draw, 880, 140, "remote exec: False", True)
    status_pill(draw, 1160, 140, f"secret scan: {len(manifest['token_scan_hits'])}", len(manifest["token_scan_hits"]) == 0)

    draw_text(draw, (40, 220), "Planned fixed batch", fill=(245, 245, 245), size=22)
    table = manifest["planned_batch"]["targets"]
    x0, y0 = 40, 260
    draw.rectangle((x0, y0 - 10, 1435, y0 + 460), fill=(25, 27, 32), outline=(84, 88, 96))
    headers = ["anchor", "tag", "required", "dry-run argv", "local destinations"]
    xs = [58, 140, 300, 430, 930]
    for x, h in zip(xs, headers):
        draw_text(draw, (x, y0), h, fill=(198, 212, 232), size=13)
    y = y0 + 30
    for row in table:
        draw.line((50, y - 5, 1425, y - 5), fill=(54, 58, 66))
        draw_text(draw, (xs[0], y), str(row["anchor"]), size=12)
        draw_text(draw, (xs[1], y), row["tag"], size=12)
        draw_text(draw, (xs[2], y), ",".join(row["required_assets"]), size=12)
        argv = " ".join(row["dry_run_command_argv"])
        draw_text(draw, (xs[3], y), argv[:80] + ("..." if len(argv) > 80 else ""), size=11)
        dest = "; ".join(row["expected_local_destinations"].values())
        draw_text(draw, (xs[4], y), dest[:78] + ("..." if len(dest) > 78 else ""), size=11)
        y += 52

    draw_text(draw, (1490, 220), "Hard checks", fill=(245, 245, 245), size=22)
    y = 260
    for check in manifest["hard_checks"]:
        color = (173, 225, 178) if check["pass"] else (245, 165, 145)
        prefix = "PASS" if check["pass"] else "STOP"
        y = draw_wrapped(draw, 1500, y, f"{prefix} {check['id']}: {check['evidence']}", 85, fill=color, size=13)
        y += 5

    draw_text(draw, (1490, 620), "Claim boundary", fill=(245, 245, 245), size=22)
    draw_wrapped(
        draw,
        1500,
        660,
        "DB53 is not a closure result. It does not prove a better source candidate, does not repair G/A1/BEST, does not make DB32 source-faithful, does not create source_id_map, and does not promote DB25/DB41. It only freezes a token-free future batch plan.",
        82,
        fill=(252, 218, 172),
        size=14,
    )

    draw_text(draw, (40, 800), "Visual context", fill=(245, 245, 245), size=22)
    image_box(board, DB47F_BOARD, (40, 840, 760, 1420), "DB47f: fixed 8 gaps still unresolved")
    image_box(board, DB52_BOARD, (800, 840, 1520, 1420), "DB52: safe runtime/data absent")
    draw.rectangle((1560, 840, 2240, 1420), fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    draw_text(draw, (1580, 865), "Accepted future output names", fill=(220, 230, 245), size=18)
    yy = 910
    for row in table:
        yy = draw_wrapped(
            draw,
            1580,
            yy,
            f"a{row['anchor']}: " + "; ".join(row["expected_local_destinations"].values()),
            82,
            fill=(225, 225, 225),
            size=12,
        )
        yy += 4
    draw_text(draw, (1580, 1388), "No exact assets were written or copied in DB53.", fill=(245, 191, 176), size=13)

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
            "evidence": f"hits={len(manifest['token_scan_hits'])} across DB53 script, brief, and manifest.",
        }
    )
    manifest["hard_checks_pass"] = all(row["pass"] for row in manifest["hard_checks"])
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    draw_board(manifest)
    print(json.dumps({"manifest": rel(MANIFEST), "board": rel(BOARD), "status": manifest["status"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
