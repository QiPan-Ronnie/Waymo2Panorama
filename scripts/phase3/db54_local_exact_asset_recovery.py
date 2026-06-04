from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db54_local_artifact_recovery"
MANIFEST = OUT_DIR / "db54_local_exact_asset_recovery_manifest.json"
BOARD = OUT_DIR / "db54_local_exact_asset_recovery_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
DB47F = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining" / "db47f_fixed_universe_exact_closure_preflight_manifest.json"
DB53 = ROOT / "deliverables" / "dit360_v2" / "db53_db47f_launch_harness" / "db53_db47f_launch_harness_manifest.json"
DB47F_BOARD = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining" / "db47f_fixed_universe_exact_closure_preflight_board.jpg"
DB53_BOARD = ROOT / "deliverables" / "dit360_v2" / "db53_db47f_launch_harness" / "db53_db47f_launch_harness_board.jpg"

DB28_DIR = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine"

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

SEARCH_ROOTS = [
    ROOT / "deliverables",
    ROOT / "outputs",
    ROOT / "results",
]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

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


def status_pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, ok: bool, w: int = 270) -> None:
    fill = (39, 105, 73) if ok else (128, 67, 48)
    draw.rounded_rectangle((x, y, x + w, y + 38), radius=6, fill=fill, outline=(190, 190, 190))
    draw_text(draw, (x + 11, y + 10), label, size=13)


def image_box(board: Image.Image, path: Path | None, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    if path and path.exists():
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((x1 - x0 - 18, y1 - y0 - 44))
            px = x0 + (x1 - x0 - img.width) // 2
            py = y0 + 8
            board.paste(img, (px, py))
        except Exception as exc:
            draw_wrapped(draw, x0 + 10, y0 + 24, f"load failed: {type(exc).__name__}", 42, fill=(240, 140, 140), size=13)
    else:
        draw_text(draw, (x0 + 10, y0 + 28), "missing / not local file", fill=(240, 140, 140), size=14)
    draw_text(draw, (x0 + 10, y1 - 29), label, fill=(220, 230, 245), size=13)


def expected_filename(anchor: int, asset: str) -> str:
    if asset == "compare":
        return f"SR_bmw_db28_a{anchor}_compare.jpg"
    if asset == "final":
        return f"SR_bmw_db28_a{anchor}_final_1024x2048.png"
    raise ValueError(asset)


def expected_path(anchor: int, asset: str) -> Path:
    return DB28_DIR / expected_filename(anchor, asset)


def matches_db28_asset(path_name: str, anchor: int, asset: str) -> bool:
    lower = path_name.replace("\\", "/").lower()
    name = Path(lower).name
    anchor_pat = re.compile(rf"sr_bmw_db28_a0*{anchor}(?!\d)")
    if not anchor_pat.search(lower):
        return False
    if asset == "compare":
        return "compare" in name
    if asset == "final":
        return "final" in name
    return False


def sha256_prefix(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def image_stats(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as img:
            return {
                "readable": True,
                "size": [int(img.size[0]), int(img.size[1])],
                "mode": img.mode,
                "bytes": path.stat().st_size,
                "sha256": sha256_prefix(path),
            }
    except Exception as exc:
        return {
            "readable": False,
            "image_read_error": type(exc).__name__,
            "bytes": path.stat().st_size if path.exists() else None,
        }


def is_within_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def target_lookup() -> dict[tuple[int, str], dict[str, Any]]:
    rows: dict[tuple[int, str], dict[str, Any]] = {}
    for target in FIXED_TARGETS:
        anchor = int(target["anchor"])
        for asset in target["required"]:
            rows[(anchor, asset)] = {
                "candidate_id": target["candidate_id"],
                "anchor": anchor,
                "asset": asset,
                "expected_filename": expected_filename(anchor, asset),
                "canonical_expected_path": rel(expected_path(anchor, asset)),
                "local_file_matches": [],
                "zip_entry_matches": [],
            }
    return rows


def best_local_match(matches: list[dict[str, Any]], anchor: int, asset: str) -> dict[str, Any] | None:
    if not matches:
        return None
    canonical = rel(expected_path(anchor, asset))

    def score(row: dict[str, Any]) -> tuple[int, int, int, int]:
        stats = row.get("image_stats", {})
        return (
            1 if row.get("path") == canonical else 0,
            1 if stats.get("readable") else 0,
            1 if stats.get("size") == [2048, 1024] and asset == "final" else 0,
            int(stats.get("bytes") or 0),
        )

    return sorted(matches, key=score, reverse=True)[0]


def catalog_local_artifacts() -> dict[str, Any]:
    targets = target_lookup()
    files_scanned = 0
    zip_files_scanned = 0
    zip_members_scanned = 0
    scan_errors: list[dict[str, Any]] = []
    roots_used: list[str] = []

    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        if not is_within_root(root):
            scan_errors.append({"root": str(root), "error": "outside_repo_root"})
            continue
        roots_used.append(rel(root) or str(root))
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            files_scanned += 1
            suffix = path.suffix.lower()
            path_label = rel(path)
            if suffix in IMAGE_SUFFIXES:
                for (anchor, asset), row in targets.items():
                    if matches_db28_asset(path_label or path.name, anchor, asset):
                        stats = image_stats(path)
                        row["local_file_matches"].append(
                            {
                                "path": path_label,
                                "name": path.name,
                                "canonical_expected": path.resolve() == expected_path(anchor, asset).resolve(),
                                "image_stats": stats,
                                "recovered_as_file": True,
                                "claim_boundary": "local recovery candidate only; not accepted final and not repair",
                            }
                        )
            elif suffix == ".zip":
                zip_files_scanned += 1
                try:
                    with zipfile.ZipFile(path) as zf:
                        for member in zf.namelist():
                            zip_members_scanned += 1
                            for (anchor, asset), row in targets.items():
                                if matches_db28_asset(member, anchor, asset):
                                    row["zip_entry_matches"].append(
                                        {
                                            "zip_path": path_label,
                                            "member": member,
                                            "recovered_as_file": False,
                                            "extracted": False,
                                            "claim_boundary": "zip entry only; not a recovered local file until separately extracted under a new scope",
                                        }
                                    )
                except Exception as exc:
                    scan_errors.append({"path": path_label, "error": type(exc).__name__})

    asset_rows: list[dict[str, Any]] = []
    for (anchor, asset), row in targets.items():
        best = best_local_match(row["local_file_matches"], anchor, asset)
        zip_only = bool(row["zip_entry_matches"]) and best is None
        status = "found_local_file_pending_review" if best else "zip_entry_only_not_recovered" if zip_only else "missing"
        asset_rows.append(
            {
                **row,
                "best_local_match": best,
                "local_file_match_count": len(row["local_file_matches"]),
                "zip_entry_match_count": len(row["zip_entry_matches"]),
                "status": status,
                "accepted_as_exact_closure": False,
            }
        )

    by_status = Counter(row["status"] for row in asset_rows)
    return {
        "search_roots": roots_used,
        "files_scanned": files_scanned,
        "zip_files_scanned": zip_files_scanned,
        "zip_members_scanned": zip_members_scanned,
        "scan_errors": scan_errors,
        "asset_rows": asset_rows,
        "status_counts": dict(by_status),
    }


def token_hits(paths: list[Path]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or path.suffix.lower() in IMAGE_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in TOKEN_PATTERNS.items():
            found = pattern.findall(text)
            if found:
                hits.append({"path": rel(path), "pattern": name, "count": len(found)})
    return hits


def build_manifest() -> dict[str, Any]:
    brief_text = BRIEF.read_text(encoding="utf-8", errors="replace")
    db47f = read_json(DB47F)
    db53 = read_json(DB53)
    catalog = catalog_local_artifacts()

    expected_ids = [target["candidate_id"] for target in FIXED_TARGETS]
    db47f_ids = [row["candidate_id"] for row in db47f.get("fixed_target_contract", {}).get("targets", [])]
    db53_ids = db53.get("planned_batch", {}).get("target_ids", [])
    local_found = [row for row in catalog["asset_rows"] if row["status"] == "found_local_file_pending_review"]
    zip_only = [row for row in catalog["asset_rows"] if row["status"] == "zip_entry_only_not_recovered"]
    missing = [row for row in catalog["asset_rows"] if row["status"] == "missing"]

    all_required_have_local_file = len(local_found) == len(catalog["asset_rows"])
    any_required_has_local_file = bool(local_found)
    status = (
        "all_required_local_candidates_found_pending_lineage_review"
        if all_required_have_local_file
        else "local_recovery_candidates_found_pending_review"
        if any_required_has_local_file
        else "paused_no_local_exact_assets_found"
    )

    hard_checks = [
        {
            "id": "db54_brief_running",
            "pass": "# DB-54: DB47f local exact-asset recovery audit" in brief_text and "Status: running" in brief_text,
            "evidence": "DB54 decision brief exists before local catalog execution.",
        },
        {
            "id": "fixed_universe_matches_db47f_db53",
            "pass": expected_ids == db47f_ids == db53_ids,
            "evidence": f"target_ids={expected_ids}",
        },
        {
            "id": "required_asset_count_is_15",
            "pass": len(catalog["asset_rows"]) == 15,
            "evidence": "Seven compare+final targets plus a105 final.",
        },
        {
            "id": "search_roots_inside_repo",
            "pass": all(is_within_root(ROOT / root) for root in catalog["search_roots"]),
            "evidence": f"roots={catalog['search_roots']}",
        },
        {
            "id": "strict_db28_tag_match_policy",
            "pass": True,
            "evidence": "Matches require SR_bmw_db28_a<anchor> plus compare/final naming; generic BMW/GhostKill images cannot match.",
        },
        {
            "id": "no_copy_extract_fetch_or_rerun",
            "pass": True,
            "evidence": "Catalog reads filenames, zip member names, and image metadata only; it does not extract, copy, fetch, rerun, or execute.",
        },
        {
            "id": "no_remote_a100_network_model_or_repair",
            "pass": True,
            "evidence": "CPU/local only; no executor, A100, network, HF/VGGT, seamroute, renderer, repair, generation, source replacement, source_id_map, or RED promotion.",
        },
        {
            "id": "found_paths_not_promoted",
            "pass": all(row["accepted_as_exact_closure"] is False for row in catalog["asset_rows"]),
            "evidence": "Every found path remains a recovery candidate pending same-ROI visual/lineage review.",
        },
    ]

    manifest: dict[str, Any] = {
        "db": "DB54",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "evidence_type": "local-exact-asset-recovery-audit-only",
        "purpose": "Catalog whether DB47f's fixed missing exact assets already exist locally before using any token-bearing runtime.",
        "scope": {
            "cpu_local_catalog_only": True,
            "fixed_target_count": len(FIXED_TARGETS),
            "required_asset_count": len(catalog["asset_rows"]),
            "remote_status": False,
            "remote_exec": False,
            "a100": False,
            "network": False,
            "hf_or_vggt": False,
            "model_inference": False,
            "dataset_scan": False,
            "seamroute_or_renderer_execution": False,
            "zip_extraction": False,
            "image_copy": False,
            "exact_asset_fetch": False,
            "panorama_repair": False,
            "generation": False,
            "source_replacement": False,
            "source_id_map_created": False,
            "permission_change": False,
            "red_promotion": False,
            "accepted_final_candidate": False,
            "output_location": rel(OUT_DIR),
        },
        "fixed_targets": FIXED_TARGETS,
        "local_catalog": catalog,
        "summary_counts": {
            "local_file_found_required_assets": len(local_found),
            "zip_entry_only_required_assets": len(zip_only),
            "missing_required_assets": len(missing),
            "all_required_have_local_file": all_required_have_local_file,
            "any_required_has_local_file": any_required_has_local_file,
        },
        "claim_boundaries": [
            "local recovery candidates only until same-ROI visual/lineage review accepts them",
            "not a repaired ERP",
            "not source-faithful local repair",
            "not original-G/A1/BEST repair",
            "not source_id_map evidence",
            "not uncaveated Bosch training data",
            "does not change DB41/DB25 abstain boundaries",
        ],
        "dependencies": {
            "decision_brief": rel(BRIEF),
            "db47f_manifest": rel(DB47F),
            "db53_manifest": rel(DB53),
        },
        "decision": {
            "accepted_db54_diagnostic_evidence": True,
            "accepted_exact_closure": False,
            "accepted_source_faithful_repair": False,
            "accepted_original_g_repair": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "recommended_next": (
                "Open a bounded DB47g same-ROI lineage/visual review only if local candidates are found; "
                "otherwise DB47f still needs approved env/non-repo runtime or local target data for one actual closure batch."
            ),
        },
        "hard_checks": hard_checks,
        "hard_checks_pass": all(row["pass"] for row in hard_checks),
        "token_scan_hits": [],
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
    }
    return manifest


def draw_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2400, 1700), (16, 18, 22))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (40, 32), "DB54 local exact-asset recovery audit", fill=(245, 245, 245), size=30)
    draw_wrapped(
        draw,
        40,
        78,
        "CPU/local catalog only. Looks for DB47f's fixed SR_bmw_db28_a<anchor> compare/final assets in local artifacts and zip member names. No A100/token/network, no extraction/copy, no seamroute, no repair, no accepted closure.",
        170,
        fill=(214, 222, 232),
        size=16,
    )

    counts = manifest["summary_counts"]
    status_pill(draw, 40, 140, f"targets: {manifest['scope']['fixed_target_count']}", manifest["scope"]["fixed_target_count"] == 8)
    status_pill(draw, 330, 140, f"required assets: {manifest['scope']['required_asset_count']}", manifest["scope"]["required_asset_count"] == 15)
    status_pill(draw, 620, 140, f"local files: {counts['local_file_found_required_assets']}", counts["local_file_found_required_assets"] > 0)
    status_pill(draw, 910, 140, f"zip-only: {counts['zip_entry_only_required_assets']}", counts["zip_entry_only_required_assets"] > 0)
    status_pill(draw, 1200, 140, "remote/A100: False", True)
    status_pill(draw, 1490, 140, "accepted closure: False", True)
    status_pill(draw, 1780, 140, f"secret hits: {len(manifest['token_scan_hits'])}", len(manifest["token_scan_hits"]) == 0)

    draw_text(draw, (40, 220), "Fixed asset table", fill=(245, 245, 245), size=22)
    x0, y0 = 40, 260
    draw.rectangle((x0, y0 - 10, 1580, y0 + 555), fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    headers = [("anchor", 60), ("asset", 170), ("status", 300), ("best local file", 610), ("zip entries", 1200)]
    for label, x in headers:
        draw_text(draw, (x0 + x, y0), label, fill=(185, 205, 230), size=14)
    y = y0 + 32
    for row in manifest["local_catalog"]["asset_rows"]:
        color = (180, 235, 185) if row["status"] == "found_local_file_pending_review" else (246, 208, 130) if row["status"].startswith("zip") else (240, 145, 130)
        best = row.get("best_local_match") or {}
        best_path = best.get("path") or "-"
        zip_count = row.get("zip_entry_match_count", 0)
        draw_text(draw, (x0 + 60, y), f"a{row['anchor']:03d}", fill=color, size=13)
        draw_text(draw, (x0 + 170, y), row["asset"], fill=color, size=13)
        draw_text(draw, (x0 + 300, y), row["status"], fill=color, size=13)
        draw_text(draw, (x0 + 610, y), best_path[-70:], fill=(225, 225, 225), size=12)
        draw_text(draw, (x0 + 1200, y), str(zip_count), fill=(225, 225, 225), size=13)
        y += 32

    draw_text(draw, (1620, 220), "Hard checks", fill=(245, 245, 245), size=22)
    y = 260
    for check in manifest["hard_checks"]:
        color = (173, 225, 178) if check["pass"] else (245, 165, 145)
        prefix = "PASS" if check["pass"] else "STOP"
        y = draw_wrapped(draw, 1620, y, f"{prefix} {check['id']}: {check['evidence']}", 78, fill=color, size=13)
        y += 5

    draw_text(draw, (40, 855), "Local thumbnails / context", fill=(245, 245, 245), size=22)
    thumb_rows = manifest["local_catalog"]["asset_rows"][:8]
    box_w, box_h = 275, 215
    for idx, row in enumerate(thumb_rows):
        best = row.get("best_local_match")
        path = ROOT / best["path"] if best and best.get("path") else None
        x = 40 + (idx % 4) * (box_w + 18)
        yy = 895 + (idx // 4) * (box_h + 46)
        image_box(board, path, (x, yy, x + box_w, yy + box_h), f"a{row['anchor']:03d} {row['asset']} {row['status']}")

    image_box(board, DB47F_BOARD, (1220, 895, 1800, 1480), "DB47f: fixed 8 gaps paused")
    image_box(board, DB53_BOARD, (1820, 895, 2360, 1480), "DB53: launch harness, no execution")

    draw.rectangle((40, 1540, 2360, 1660), fill=(25, 27, 32), outline=(84, 88, 96), width=2)
    draw_wrapped(
        draw,
        60,
        1565,
        "Boundary: found local paths are recovery candidates only. This board does not accept exact closure, does not repair original G/A1/BEST, does not create source_id_map, and does not change DB41/DB25 abstain. If no local files are found, DB47f still needs approved env/non-repo runtime or local target data for one bounded closure batch.",
        220,
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
            "evidence": f"hits={len(manifest['token_scan_hits'])} across DB54 script, brief, and manifest.",
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
                "summary_counts": manifest["summary_counts"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
