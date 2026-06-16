from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
KOI_ROOT = ROOT.parent.parent
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db67_dense_raw_aligned_surface_audit"
MANIFEST = OUT_DIR / "db67_phase0_inventory_manifest.json"
BOARD = OUT_DIR / "db67_phase0_backend_selection_board.jpg"

DB64_PHASE4B = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0" / "phase4b_z_visibility_cause"
DB64_PHASE5A = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0" / "phase5a_continuous_surface"
DB65 = ROOT / "deliverables" / "layered_target_raycaster" / "db65_visible_photometric_fallback"
DB61 = ROOT / "deliverables" / "dit360_v2" / "db61_fresh_vggt_a1g_quicklook"
DB62 = ROOT / "deliverables" / "dit360_v2" / "db62_vggt_raw_source_composite"
PI3_ROOT = KOI_ROOT / "01-pi3"

TARGET_CASES = {
    "bmw": "02a00399:0:bmw",
    "clean_control": "0bae3b5e:30:clean_far",
}

TOKEN_PATTERNS = {
    "hf_secret_value": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "cloudflare_tunnel": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_auth": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "json_auth_value": re.compile(r'"(?:token|url)"\s*:\s*"[A-Za-z0-9:/.?\-_=]{12,}"', re.IGNORECASE),
}


def rel(path: Path | str | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        try:
            return str(p.relative_to(KOI_ROOT)).replace("\\", "/")
        except ValueError:
            return "<non-repo path omitted>"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def nested(data: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def file_stat(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"path": rel(path), "exists": path.exists()}
    if path.exists() and path.is_file():
        row["bytes"] = int(path.stat().st_size)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            try:
                with Image.open(path) as img:
                    row["size"] = list(img.size)
            except Exception as exc:  # pragma: no cover - diagnostic only
                row["image_error"] = str(exc)
    return row


def count_files(root: Path, patterns: tuple[str, ...]) -> dict[str, Any]:
    if not root.exists():
        return {"exists": False, "counts": {}}
    counts: dict[str, int] = {}
    for pat in patterns:
        counts[pat] = len(list(root.rglob(pat)))
    return {"exists": True, "path": rel(root), "counts": counts}


def secret_hits_text(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pattern in TOKEN_PATTERNS.items():
        found = pattern.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


def collect_baselines() -> dict[str, Any]:
    phase4b_summary = read_json(DB64_PHASE4B / "db64_phase4b_batch_summary.json")
    phase5a_summary = read_json(DB64_PHASE5A / "db64_phase5a_batch_summary.json")
    db62_manifest = read_json(DB62 / "db62_vggt_raw_source_composite_manifest.json")
    db61_manifest = read_json(DB61 / "db61_fresh_vggt_a1g_quicklook_manifest.json")

    bmw4 = nested(phase4b_summary, ["by_case", "02a00399_a000_bmw"], {})
    clean4 = nested(phase4b_summary, ["by_case", "0bae3b5e_a030_clean_far"], {})
    bmw5 = nested(phase5a_summary, ["by_case", "02a00399_a000_bmw"], {})
    db62_stats = nested(db62_manifest, ["remote_result", "db62_raw_source_operator", "stats"], {})
    db61_stats = nested(
        db61_manifest,
        ["quicklook", "operator", "alpha_stats", "vggt_stats"],
        {},
    )

    return {
        "db64_phase4b": {
            "summary": file_stat(DB64_PHASE4B / "db64_phase4b_batch_summary.json"),
            "board": file_stat(DB64_PHASE4B / "db64_phase4b_z_visibility_board.jpg"),
            "bmw": {
                "no_surface": bmw4.get("seam_no_surface_frac"),
                "no_raw_zbuffer": bmw4.get("seam_no_raw_zbuffer_support_frac"),
                "visible_any": (bmw4.get("seam_visible_ge2_frac") or 0.0)
                + (bmw4.get("seam_single_visible_frac") or 0.0),
                "visible_ge2": bmw4.get("seam_visible_ge2_frac"),
                "z_conflict": bmw4.get("seam_z_mismatch_conflict_frac"),
                "source_boundary_proxy": bmw4.get("seam_source_boundary_proxy_frac"),
            },
            "clean_control": {
                "no_surface": clean4.get("seam_no_surface_frac"),
                "no_raw_zbuffer": clean4.get("seam_no_raw_zbuffer_support_frac"),
                "visible_any": (clean4.get("seam_visible_ge2_frac") or 0.0)
                + (clean4.get("seam_single_visible_frac") or 0.0),
                "visible_ge2": clean4.get("seam_visible_ge2_frac"),
                "z_conflict": clean4.get("seam_z_mismatch_conflict_frac"),
            },
        },
        "db64_phase5a": {
            "summary": file_stat(DB64_PHASE5A / "db64_phase5a_batch_summary.json"),
            "board": file_stat(DB64_PHASE5A / "db64_phase5a_continuous_surface_board.jpg"),
            "aggregate_success": phase5a_summary.get("aggregate_success"),
            "phase5b_allowed": phase5a_summary.get("phase5b_allowed", False),
            "bmw_improvements": bmw5.get("improvements", {}),
            "bmw_fused": bmw5.get("fused", {}),
        },
        "db61_vggt_prior": {
            "manifest": file_stat(DB61 / "db61_fresh_vggt_a1g_quicklook_manifest.json"),
            "board": file_stat(DB61 / "db61_fresh_vggt_a1g_quicklook_board.jpg"),
            "coverage_valid_frac": db61_stats.get("coverage_valid_frac"),
            "owner_preprocess_valid_frac_of_roi": db61_stats.get("owner_preprocess_valid_frac_of_roi"),
            "prediction_fields_evidence": ["depth", "depth_conf", "world_points", "world_points_conf"],
        },
        "db62_vggt_raw_source_probe": {
            "manifest": file_stat(DB62 / "db62_vggt_raw_source_composite_manifest.json"),
            "board": file_stat(DB62 / "db62_vggt_raw_source_composite_board.jpg"),
            "operator_stats": db62_stats,
        },
        "db65_current_visible_reference": {
            "manifest": file_stat(DB65 / "db65_visible_photometric_fallback_manifest.json"),
            "board": file_stat(DB65 / "db65_visible_photometric_fallback_board.jpg"),
            "claim": "current best visible presentation-diagnostic reference only",
        },
    }


def backend_inventory() -> dict[str, Any]:
    vggt_ready = (
        (DB61 / "db61_fresh_vggt_remote_result.json").exists()
        and (DB62 / "db62_vggt_raw_source_remote_result.json").exists()
    )
    pi3_files = count_files(PI3_ROOT, ("*.py", "*.safetensors", "*.pth", "*.pt", "*.json"))
    pi3_ckpt_files = list((PI3_ROOT / "checkpoints").rglob("*")) if (PI3_ROOT / "checkpoints").exists() else []
    pi3_ckpts = [
        p
        for p in pi3_ckpt_files
        if p.is_file() and p.suffix.lower() in {".safetensors", ".pth", ".pt", ".bin", ".ckpt"}
    ]
    pi3_outputs = count_files(PI3_ROOT / "outputs", ("*.json", "*.ply", "*.png", "*.jpg", "*.md"))

    return {
        "vggt": {
            "rank": 1,
            "selected_for_phase1": True,
            "local_prior_outputs_exist": vggt_ready,
            "why": [
                "existing DB61/DB62 wrappers already run official VGGT on the raw seven-camera BMW anchor",
                "available evidence fields include dense depth, dense world points, depth confidence, and world-point confidence",
                "DB67 can reuse the DB64 raw projection and z-buffer checks as post-model evidence gates",
            ],
            "known_risks": [
                "DB61/DB62 direct A1/G operators failed visually, so VGGT confidence cannot be treated as repair permission",
                "VGGT coordinate/reflection issues from DB45 mean dense points require LiDAR and raw-zbuffer agreement before promotion",
                "Phase1 needs A100 and approved non-repo runtime/auth sources",
            ],
        },
        "pi3_or_pi3x": {
            "rank": 2,
            "selected_for_phase1": False,
            "repo": file_stat(PI3_ROOT / "code" / "official" / "Pi3" / "README.md"),
            "workspace": file_stat(PI3_ROOT / "README.md"),
            "checkpoint_files_found_locally": len(pi3_ckpts),
            "sample_outputs": pi3_outputs,
            "file_counts": pi3_files,
            "why_deferred": [
                "local Pi3 repo exists, but there is no Waymo2Panorama raw-camera/ERP/zbuffer integration artifact yet",
                "no local model checkpoint file was found in the Pi3 checkpoints folder during Phase0",
                "Pi3X is promising for smoother points and conditioning, but would be a separate integration route and should not be mixed with the first DB67 A100 run",
            ],
        },
    }


def phase1_contract(baselines: dict[str, Any]) -> dict[str, Any]:
    bmw = baselines["db64_phase4b"]["bmw"]
    return {
        "a100_needed_after_phase0": True,
        "phase1_runtime_policy": [
            "use only process env or a non-repo runtime secret source",
            "do not echo or write endpoint, bearer, HF, or runtime JSON values",
            "run at most one external status plus one external exec unless a fresh brief extension is written",
        ],
        "selected_backend": "VGGT",
        "fixed_cases": TARGET_CASES,
        "required_outputs": [
            "dense_depth_or_point_maps",
            "dense_confidence_maps",
            "raw_camera_alignment_residual_maps",
            "lidar_agreement_maps",
            "raw_projection_valid_count_maps",
            "zbuffer_visible_count_maps",
            "before_after_z_cause_maps",
            "continuous_support_component_stats",
            "protected_source_boundary_overlap_maps",
            "per_case_breakdowns",
            "batch_summary",
            "review_board",
        ],
        "evidence_thresholds": {
            "bmw_no_surface_baseline": bmw.get("no_surface"),
            "bmw_no_surface_target": "drop >= 0.15 or reach <= 0.40",
            "bmw_no_raw_zbuffer_baseline": bmw.get("no_raw_zbuffer"),
            "bmw_no_raw_zbuffer_target": "do not worsen; ideally drop >= 0.07",
            "bmw_visible_any_target": "gain >= 0.10",
            "bmw_visible_ge2_target": "gain >= 0.05 or justify coherent single-source support",
            "longest_component_target": ">= 0.25 of seam-band length",
            "protected_overlap_target": "<= 0.02 strict, <= 0.05 warning",
            "clean_control_rule": "no material degradation",
        },
        "forbidden_in_phase1": [
            "RGB renderer",
            "source replacement",
            "prompt generation",
            "DiT or FLUX",
            "3DGS",
            "DB32 edit",
            "dataset scan beyond fixed cases",
            "RED promotion",
        ],
    }


def panel_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], title: str, lines: list[str], width: int) -> None:
    x, y = xy
    font_title = ImageFont.load_default()
    font = ImageFont.load_default()
    draw.text((x, y), title, fill=(20, 20, 20), font=font_title)
    yy = y + 18
    for line in lines:
        chunks: list[str] = []
        text = str(line)
        while len(text) > width:
            chunks.append(text[:width])
            text = text[width:]
        chunks.append(text)
        for chunk in chunks:
            draw.text((x, yy), chunk, fill=(45, 45, 45), font=font)
            yy += 14


def thumb(path: Path, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGB", size, (245, 245, 245))
    if not path.exists():
        d = ImageDraw.Draw(canvas)
        d.text((12, 12), "missing", fill=(120, 40, 40), font=ImageFont.load_default())
        d.text((12, 30), rel(path) or "", fill=(80, 80, 80), font=ImageFont.load_default())
        return canvas
    with Image.open(path) as img:
        im = img.convert("RGB")
        im.thumbnail(size, Image.Resampling.LANCZOS)
    x = (size[0] - im.width) // 2
    y = (size[1] - im.height) // 2
    canvas.paste(im, (x, y))
    return canvas


def write_board(manifest: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    W, H = 1800, 1500
    board = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(board)
    draw.rectangle([0, 0, W, 80], fill=(235, 238, 242))
    draw.text((24, 18), "DB67 Phase0 - Dense Raw-Aligned Target-Surface Evidence Audit", fill=(15, 20, 28), font=ImageFont.load_default())
    draw.text((24, 42), "No A100, no remote, no model inference, no RGB repair. Phase1 needs A100 only after this inventory.", fill=(60, 64, 70), font=ImageFont.load_default())

    images = [
        ("DB64 Phase4b z-cause", DB64_PHASE4B / "db64_phase4b_z_visibility_board.jpg"),
        ("DB64 Phase5a failed surface", DB64_PHASE5A / "db64_phase5a_continuous_surface_board.jpg"),
        ("DB62 VGGT raw-source probe", DB62 / "db62_vggt_raw_source_composite_board.jpg"),
        ("DB65 visible reference", DB65 / "db65_visible_photometric_fallback_board.jpg"),
    ]
    x0, y0 = 24, 100
    cell_w, cell_h = 430, 330
    for i, (label, path) in enumerate(images):
        x = x0 + i * 440
        draw.rectangle([x, y0, x + cell_w, y0 + cell_h], outline=(190, 190, 190), width=1)
        board.paste(thumb(path, (cell_w - 16, cell_h - 42)), (x + 8, y0 + 30))
        draw.text((x + 10, y0 + 10), label, fill=(30, 30, 30), font=ImageFont.load_default())

    bmw = manifest["baselines"]["db64_phase4b"]["bmw"]
    p5 = manifest["baselines"]["db64_phase5a"]
    db62_stats = manifest["baselines"]["db62_vggt_raw_source_probe"]["operator_stats"]
    contract = manifest["phase1_contract"]
    panel_text(
        draw,
        (40, 470),
        "Why DB67 exists",
        [
            f"BMW Phase4b no_surface={bmw.get('no_surface'):.4f}, no_raw_zbuffer={bmw.get('no_raw_zbuffer'):.4f}, visible_any={bmw.get('visible_any'):.4f}",
            f"Phase5a aggregate_success={p5.get('aggregate_success')}; Phase5b remains blocked.",
            "DB65 is the current visible result, but it is presentation/diagnostic only.",
            "DB67 tests only whether dense surfaces can become raw-visible target-surface evidence.",
        ],
        118,
    )
    panel_text(
        draw,
        (40, 640),
        "Backend decision",
        [
            "Selected Phase1 backend: VGGT.",
            "Reason: existing raw seven-camera template and dense depth/world-point/confidence outputs already exist.",
            "Pi3/Pi3X is deferred: repo exists, but no W2P raw-zbuffer integration or local checkpoint artifact was found in Phase0.",
            "Dense confidence is never repair permission; LiDAR/raw-zbuffer/continuity/protected veto must pass.",
        ],
        118,
    )
    panel_text(
        draw,
        (40, 820),
        "Known prior VGGT failure",
        [
            f"DB62 alpha>0.05 fraction={db62_stats.get('alpha_changed_frac_gt_0_05')}; best source differs from owner={db62_stats.get('best_differs_from_owner_frac')}",
            "Direct raw-source composite was sparse/blocky and visually rejected.",
            "DB67 therefore does not re-run DB62; it audits dense target-surface support against DB64 gates.",
        ],
        118,
    )
    panel_text(
        draw,
        (40, 1000),
        "Phase1 allowed scope",
        [
            "At most one status plus one exec; fixed BMW and clean-control cases only.",
            "Required maps: dense surface, raw alignment residual, LiDAR agreement, raw projection, zbuffer visible count, z-cause transition, components, protected overlap.",
            "Forbidden: RGB renderer, source replacement, prompt generation, DiT/FLUX, DB32 edit, RED promotion.",
            f"A100 needed after Phase0: {contract['a100_needed_after_phase0']}",
        ],
        118,
    )
    draw.rectangle([30, 1330, W - 30, 1470], outline=(170, 60, 60), width=2)
    panel_text(
        draw,
        (50, 1350),
        "Claim boundary",
        [
            "Phase0 result: inventory and backend selection only.",
            "No model inference ran. No source-faithful repair claim changed.",
            "If Phase1 fails thresholds, pause BMW source-faithful seam repair and pivot practical line to HardSelect++ sidecars plus presentation-only branches.",
        ],
        140,
    )
    board.save(BOARD, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    baselines = collect_baselines()
    backends = backend_inventory()
    contract = phase1_contract(baselines)
    manifest: dict[str, Any] = {
        "db": "DB-67",
        "phase": "phase0_inventory_and_backend_selection",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "phase0_complete_a100_needed_for_phase1",
        "what_ran": {
            "cpu_local_only": True,
            "remote_status_or_exec": False,
            "runtime_secret_accessed": False,
            "a100_used": False,
            "dense_model_inference": False,
            "rgb_repair": False,
            "source_replacement": False,
            "generation": False,
            "permission_change": False,
        },
        "fixed_cases": TARGET_CASES,
        "baselines": baselines,
        "backend_inventory": backends,
        "phase1_contract": contract,
        "phase0_verdict": {
            "selected_next_step": "request A100 for VGGT dense raw-aligned evidence audit Phase1",
            "a100_needed_now_for_next_step": True,
            "why_not_continue_cpu_only": [
                "DB64 Phase5a already exhausted the LiDAR-only/local-surface CPU route",
                "DB67's question requires dense geometry inference not present in current local artifacts",
                "Phase0 can choose and constrain the route, but cannot answer whether dense surfaces improve raw-visible target support",
            ],
            "claim_classification": "paused_before_model_evidence; diagnostic inventory only",
        },
        "outputs": {
            "output_dir": rel(OUT_DIR),
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
    }
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
    hits = secret_hits_text(text)
    manifest["secret_scan"] = {
        "strict_secret_like_hit_count": len(hits),
        "hits": hits,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    write_board(manifest)

    # Re-scan after adding output paths and board metadata.
    final_text = MANIFEST.read_text(encoding="utf-8")
    final_hits = secret_hits_text(final_text)
    if final_hits:
        raise RuntimeError(f"secret-like values detected in DB67 Phase0 manifest: {final_hits}")
    print(json.dumps({"status": manifest["status"], "manifest": str(MANIFEST), "board": str(BOARD)}, indent=2))


if __name__ == "__main__":
    main()
