from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
PHASE3_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0" / "phase3_sidecar_instrumentation"
PHASE3_FETCH = PHASE3_DIR / "fetch"
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0" / "phase4_cause_map"
MANIFEST = OUT_DIR / "db64_phase4_cause_map_manifest.json"
BATCH_SUMMARY = OUT_DIR / "db64_phase4_batch_summary.json"
BOARD = OUT_DIR / "db64_phase4_cause_map_board.jpg"

RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]
CASE_LABELS = {
    "02a00399_a000_bmw": "BMW target",
    "0bae3b5e_a030_clean_far": "Clean control",
}

CAUSE_CODES = {
    0: "explainable_source_visible",
    10: "out_of_fov_or_no_source_projection",
    20: "no_target_surface_support",
    30: "single_source_only_no_consensus",
    40: "z_mismatch_or_occlusion_conflict_proxy",
    50: "disocclusion_candidate",
    60: "source_boundary_or_protected_risk_proxy",
    70: "layer_ambiguous_or_insufficient_layer_evidence",
    255: "unknown_unclassified",
}

FLAG_BITS = {
    0: "near_source_boundary",
    1: "near_car_person_bike_mask_unavailable",
    2: "near_lane_curb_road_marking_mask_unavailable",
    3: "z_buffer_conflict_proxy",
    4: "out_of_fov_or_no_source_projection",
    5: "no_lidar_local_surface",
    6: "disocclusion_proxy",
    7: "layer_ambiguous",
}

REPAIRABILITY_CODES = {
    0: "non_target_or_keep_control",
    1: "repairable_now_source_visible_no_rgb_edit",
    2: "repairable_later_needs_local_layer_fit",
    3: "needs_multiframe_or_dense_surface",
    4: "presentation_only_or_abstain",
    5: "abstain_required_protected_or_occlusion",
}

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "trycloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "json_token": re.compile(r'"token"\s*:\s*"[A-Za-z0-9._\-]{12,}"'),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
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
        return "<non-repo path omitted>"


def load_u8(path: Path) -> np.ndarray:
    if not path.exists():
        raise FileNotFoundError(path)
    return np.array(Image.open(path).convert("L"), dtype=np.uint8)


def load_rgb(path: Path) -> np.ndarray:
    if not path.exists():
        raise FileNotFoundError(path)
    return np.array(Image.open(path).convert("RGB"), dtype=np.uint8)


def save_u8(path: Path, arr: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype(np.uint8)).save(path)


def save_rgb(path: Path, arr: np.ndarray, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(path, quality=quality)


def resize_w(rgb: np.ndarray, width: int) -> np.ndarray:
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    height = max(1, round(img.height * width / img.width))
    return np.array(img.resize((width, height), Image.Resampling.BILINEAR), dtype=np.uint8)


def label_panel(rgb: np.ndarray, label: str, label_h: int = 34) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    band = Image.new("RGB", (rgb.shape[1], label_h), (0, 0, 0))
    draw = ImageDraw.Draw(band)
    draw.text((8, 9), label, fill=(255, 255, 255), font=font(16))
    return np.vstack([np.array(band, dtype=np.uint8), rgb])


def stack_named(rows: list[tuple[str, np.ndarray]]) -> np.ndarray:
    panels = [label_panel(img, label) for label, img in rows]
    width = max(p.shape[1] for p in panels)
    out = []
    for panel in panels:
        if panel.shape[1] < width:
            pad = np.zeros((panel.shape[0], width - panel.shape[1], 3), dtype=np.uint8)
            panel = np.hstack([panel, pad])
        out.append(panel)
    return np.vstack(out)


def colorize(arr: np.ndarray, palette: dict[int, tuple[int, int, int]], default=(40, 42, 48)) -> np.ndarray:
    out = np.zeros((*arr.shape, 3), dtype=np.uint8)
    out[:] = np.array(default, dtype=np.uint8)
    for code, color in palette.items():
        out[arr == int(code)] = np.array(color, dtype=np.uint8)
    return out


def mask_rgb(mask: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    out = np.zeros((*mask.shape, 3), dtype=np.uint8)
    out[mask.astype(bool)] = np.array(color, dtype=np.uint8)
    return out


def blend_overlay(base: np.ndarray, overlay: np.ndarray, alpha_mask: np.ndarray, alpha: float = 0.55) -> np.ndarray:
    out = np.clip(base, 0, 255).astype(np.uint8).copy()
    m = alpha_mask.astype(bool)
    if np.any(m):
        out[m] = ((1.0 - alpha) * out[m].astype(np.float32) + alpha * overlay[m].astype(np.float32)).astype(np.uint8)
    return out


def unique_counts(arr: np.ndarray) -> dict[str, int]:
    vals, counts = np.unique(arr.reshape(-1), return_counts=True)
    return {str(int(v)): int(c) for v, c in zip(vals, counts)}


def frac(mask: np.ndarray, denom: np.ndarray | None = None) -> float | None:
    m = mask.astype(bool)
    if denom is None:
        return float(m.mean())
    d = denom.astype(bool)
    n = int(d.sum())
    if n == 0:
        return None
    return float((m & d).sum() / n)


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pat in TOKEN_PATTERNS.items():
        found = pat.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


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
        draw.rectangle(box, outline=(100, 100, 100), fill=(34, 36, 42))
        draw_wrapped(draw, x0 + 12, y0 + 12, f"missing: {rel(path)}", 42, fill=(255, 170, 145), size=14)
        return
    with Image.open(path) as img:
        thumb = img.convert("RGB")
        thumb.thumbnail((x1 - x0, y1 - y0))
        px = x0 + ((x1 - x0) - thumb.width) // 2
        py = y0 + ((y1 - y0) - thumb.height) // 2
        board.paste(thumb, (px, py))
        draw.rectangle((px, py, px + thumb.width, py + thumb.height), outline=(185, 185, 185))


def compute_case(run_name: str) -> dict[str, Any]:
    case_in = PHASE3_FETCH / run_name
    case_out = OUT_DIR / run_name
    case_out.mkdir(parents=True, exist_ok=True)

    hard = load_rgb(case_in / f"{run_name}_hard_select_reference.jpg")
    hard_source = load_u8(case_in / f"{run_name}_hard_select_source_id_map.png")
    source_id = load_u8(case_in / f"{run_name}_source_id_map.png")
    visibility = load_u8(case_in / f"{run_name}_visibility_count_map.png")
    support = load_u8(case_in / f"{run_name}_lidar_support_map.png") > 0
    unknown_phase3 = load_u8(case_in / f"{run_name}_unknown_mask.png") > 0
    disocclusion = load_u8(case_in / f"{run_name}_disocclusion_mask.png") > 0
    boundary = load_u8(case_in / f"{run_name}_source_boundary_risk_mask.png") > 0
    seam_band = load_u8(case_in / f"{run_name}_seam_band_mask.png") > 0
    seam_core = load_u8(case_in / f"{run_name}_seam_core_mask.png") > 0
    layer_id = load_u8(case_in / f"{run_name}_layer_id_map.png")

    source_valid = hard_source != 255
    visible_any = source_valid & support & (visibility > 0) & (source_id != 255)
    visible_ge2 = visible_any & (visibility >= 2)
    single_source = source_valid & support & (visibility == 1)
    no_surface = source_valid & (~support)
    out_of_fov = ~source_valid
    layer_ambiguous = source_valid & (layer_id == 255)

    cause = np.full(hard_source.shape, 255, dtype=np.uint8)
    cause[out_of_fov] = 10
    cause[source_valid & visible_ge2] = 0
    cause[single_source] = 30
    cause[no_surface] = 20
    cause[disocclusion] = 50
    cause[layer_ambiguous] = 70
    # Boundary is a safety overlay. Make it primary only where the ray was otherwise explainable.
    cause[boundary & visible_ge2] = 60

    flags = np.zeros(hard_source.shape, dtype=np.uint8)
    flags[boundary] |= 1 << 0
    flags[disocclusion] |= 1 << 3
    flags[out_of_fov] |= 1 << 4
    flags[no_surface] |= 1 << 5
    flags[disocclusion] |= 1 << 6
    flags[layer_ambiguous] |= 1 << 7

    repairability = np.zeros(hard_source.shape, dtype=np.uint8)
    target = source_valid & seam_band
    repairability[target & visible_ge2 & (~boundary)] = 1
    repairability[target & single_source & (~boundary)] = 2
    repairability[target & no_surface & (~boundary)] = 3
    repairability[target & out_of_fov] = 4
    repairability[target & disocclusion] = 5
    repairability[target & boundary] = 5

    save_u8(case_out / f"{run_name}_cause_primary_map.png", cause)
    save_u8(case_out / f"{run_name}_cause_flag_map.png", flags)
    save_u8(case_out / f"{run_name}_repairability_map.png", repairability)

    cause_palette = {
        0: (75, 220, 120),
        10: (25, 25, 28),
        20: (245, 178, 62),
        30: (250, 230, 75),
        40: (210, 70, 230),
        50: (70, 210, 240),
        60: (255, 75, 105),
        70: (165, 105, 220),
        255: (90, 94, 102),
    }
    repair_palette = {
        0: (50, 85, 150),
        1: (70, 220, 120),
        2: (230, 215, 70),
        3: (245, 170, 60),
        4: (170, 110, 210),
        5: (255, 75, 95),
    }
    flag_viz = np.zeros((*flags.shape, 3), dtype=np.uint8)
    flag_viz[flags & (1 << 5) > 0] = (245, 170, 60)
    flag_viz[flags & (1 << 6) > 0] = (70, 210, 240)
    flag_viz[flags & (1 << 0) > 0] = (255, 75, 105)
    flag_viz[flags & (1 << 4) > 0] = (25, 25, 28)
    flag_viz[flags & (1 << 7) > 0] = (165, 105, 220)

    cause_viz = colorize(cause, cause_palette)
    repair_viz = colorize(repairability, repair_palette)
    save_rgb(case_out / f"{run_name}_cause_primary_viz.png", cause_viz)
    save_rgb(case_out / f"{run_name}_cause_flag_viz.png", flag_viz)
    save_rgb(case_out / f"{run_name}_repairability_viz.png", repair_viz)

    overlay_mask = seam_band | unknown_phase3 | boundary | disocclusion
    cause_overlay = blend_overlay(hard, cause_viz, overlay_mask, alpha=0.50)
    repair_overlay = blend_overlay(hard, repair_viz, seam_band, alpha=0.58)
    seam_core_rgb = mask_rgb(seam_core, (255, 255, 255))
    cause_overlay = blend_overlay(cause_overlay, seam_core_rgb, seam_core, alpha=0.80)
    save_rgb(case_out / f"{run_name}_cause_overlay.jpg", cause_overlay, quality=90)
    save_rgb(case_out / f"{run_name}_repairability_overlay.jpg", repair_overlay, quality=90)

    phase3_crop = case_in / f"{run_name}_sidecar_crop_review.jpg"
    if phase3_crop.exists():
        with Image.open(phase3_crop) as img:
            phase3_crop_rgb = np.array(img.convert("RGB"), dtype=np.uint8)
    else:
        phase3_crop_rgb = hard

    review = stack_named(
        [
            ("hard_select control", resize_w(hard, 768)),
            ("cause_primary_map evidence codes", resize_w(cause_viz, 768)),
            ("cause_flags overlay colors", resize_w(flag_viz, 768)),
            ("repairability_map policy", resize_w(repair_viz, 768)),
            ("cause_overlay on hard_select", resize_w(cause_overlay, 768)),
            ("repairability_overlay on seam band", resize_w(repair_overlay, 768)),
        ]
    )
    save_rgb(case_out / f"{run_name}_cause_overlay_board.jpg", review, quality=90)

    phase_compare = stack_named(
        [
            ("phase3 sidecar crop review", resize_w(phase3_crop_rgb, 900)),
            ("phase4 cause/repairability review", resize_w(review, 900)),
        ]
    )
    save_rgb(case_out / f"{run_name}_phase3_vs_phase4_review_board.jpg", phase_compare, quality=88)

    denom = source_valid & seam_band
    unknown_denom = unknown_phase3 & denom
    cause_counts = unique_counts(cause)
    flag_counts = {name: int(((flags & (1 << bit)) > 0).sum()) for bit, name in FLAG_BITS.items()}
    flag_fracs_seam = {name: frac((flags & (1 << bit)) > 0, denom) for bit, name in FLAG_BITS.items()}
    repair_counts = unique_counts(repairability)
    primary_fracs_seam = {
        CAUSE_CODES[code]: frac(cause == code, denom)
        for code in CAUSE_CODES
    }
    primary_fracs_unknown = {
        CAUSE_CODES[code]: frac(cause == code, unknown_denom)
        for code in CAUSE_CODES
    }
    repair_fracs_seam = {
        REPAIRABILITY_CODES[code]: frac(repairability == code, denom)
        for code in REPAIRABILITY_CODES
    }
    unexplained_unknown_frac = primary_fracs_unknown.get("unknown_unclassified")
    if unexplained_unknown_frac is None:
        unexplained_unknown_frac = 0.0

    breakdown = {
        "case": run_name,
        "case_label": CASE_LABELS.get(run_name, run_name),
        "inputs": {
            "phase3_case_dir": rel(case_in),
            "uses_phase3_sidecars_only": True,
            "uses_rgb_similarity": False,
            "uses_model_or_remote": False,
        },
        "taxonomy": {
            "cause_codes": CAUSE_CODES,
            "flag_bits": FLAG_BITS,
            "repairability_codes": REPAIRABILITY_CODES,
            "z_mismatch_note": "Phase3 did not persist z residual cause maps; z/occlusion is only represented as a conservative disocclusion proxy flag.",
            "protected_mask_note": "No semantic car/person/lane/curb masks are used in Phase4a; source-boundary risk is a proxy only.",
        },
        "counts": {
            "cause_primary": cause_counts,
            "cause_flags": flag_counts,
            "repairability": repair_counts,
        },
        "fractions": {
            "source_valid_frac": frac(source_valid),
            "seam_band_frac": frac(seam_band),
            "phase3_unknown_frac_seam": frac(unknown_phase3, denom),
            "cause_primary_frac_seam": primary_fracs_seam,
            "cause_primary_frac_of_phase3_unknown": primary_fracs_unknown,
            "cause_flag_frac_seam": flag_fracs_seam,
            "repairability_frac_seam": repair_fracs_seam,
            "unknown_unclassified_frac_of_phase3_unknown": float(unexplained_unknown_frac or 0.0),
        },
        "outputs": {
            "cause_primary_map": f"{run_name}_cause_primary_map.png",
            "cause_flag_map": f"{run_name}_cause_flag_map.png",
            "repairability_map": f"{run_name}_repairability_map.png",
            "cause_overlay_board": f"{run_name}_cause_overlay_board.jpg",
            "phase3_vs_phase4_review_board": f"{run_name}_phase3_vs_phase4_review_board.jpg",
        },
        "claim_boundary": [
            "cause maps are evidence/policy maps, not semantic layer truth",
            "repairability map is action triage, not repair permission",
            "no RGB repair or source replacement was created",
        ],
    }
    (case_out / f"{run_name}_unknown_cause_breakdown.json").write_text(
        json.dumps(breakdown, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return breakdown


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    def mean_nested(path: list[str]) -> float | None:
        vals = []
        for case in cases:
            cur: Any = case
            for key in path:
                if not isinstance(cur, dict):
                    cur = None
                    break
                cur = cur.get(key)
            if isinstance(cur, (int, float)):
                vals.append(float(cur))
        return float(np.mean(vals)) if vals else None

    by_case = {case["case"]: case["fractions"] for case in cases}
    bmw = by_case.get("02a00399_a000_bmw", {})
    clean = by_case.get("0bae3b5e_a030_clean_far", {})
    diff: dict[str, float] = {}
    for key in [
        "phase3_unknown_frac_seam",
        "unknown_unclassified_frac_of_phase3_unknown",
    ]:
        if isinstance(bmw.get(key), (int, float)) and isinstance(clean.get(key), (int, float)):
            diff[key + "_bmw_minus_clean"] = float(bmw[key] - clean[key])
    for cause_name in CAUSE_CODES.values():
        b = (bmw.get("cause_primary_frac_seam") or {}).get(cause_name)
        c = (clean.get("cause_primary_frac_seam") or {}).get(cause_name)
        if isinstance(b, (int, float)) and isinstance(c, (int, float)):
            diff["cause_seam_" + cause_name + "_bmw_minus_clean"] = float(b - c)

    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "n_cases": len(cases),
        "cases": [case["case"] for case in cases],
        "status": "phase4a_cause_maps_complete" if len(cases) == len(RUN_NAMES) else "phase4a_incomplete",
        "mean_phase3_unknown_frac_seam": mean_nested(["fractions", "phase3_unknown_frac_seam"]),
        "mean_unknown_unclassified_frac_of_phase3_unknown": mean_nested(["fractions", "unknown_unclassified_frac_of_phase3_unknown"]),
        "by_case": by_case,
        "bmw_minus_clean": diff,
    }


def write_board(summary: dict[str, Any], manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1900, 1500), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 24), "DB64 Phase4a unknown-cause attribution / repairability map", size=27)
    draw_text(draw, (28, 60), "CPU-local from Phase3 sidecars only. Evidence maps only; no RGB repair, no VGGT, no remote.", fill=(218, 224, 235), size=15)

    y = 100
    lines = [
        f"status={summary.get('status')} secret_hits={manifest['strict_secret_scan']['hit_count']} cases={', '.join(RUN_NAMES)}",
        f"mean phase3_unknown_frac_seam={fmt(summary.get('mean_phase3_unknown_frac_seam'))}",
        f"mean unknown_unclassified_of_phase3_unknown={fmt(summary.get('mean_unknown_unclassified_frac_of_phase3_unknown'))}",
        "primary cause maps are mutually exclusive; cause flags are multi-label overlays.",
        "z-mismatch is proxy only because Phase3 did not persist z residual cause maps.",
    ]
    for line in lines:
        y = draw_wrapped(draw, 36, y, "- " + line, 145, size=14)

    y += 8
    draw_text(draw, (28, y), "Claim Boundary", size=20)
    y += 28
    for line in [
        "cause maps are not road/wall/car semantic truth",
        "repairability map is action triage, not repair permission",
        "no Phase2 RGB copy tuning or source replacement occurred",
    ]:
        y = draw_wrapped(draw, 36, y, "- " + line, 145, fill=(255, 235, 185), size=13)

    diff = summary.get("bmw_minus_clean") or {}
    y += 8
    draw_text(draw, (28, y), "BMW Minus Clean Highlights", size=20)
    y += 28
    highlight_keys = [
        "phase3_unknown_frac_seam_bmw_minus_clean",
        "cause_seam_no_target_surface_support_bmw_minus_clean",
        "cause_seam_single_source_only_no_consensus_bmw_minus_clean",
        "cause_seam_disocclusion_candidate_bmw_minus_clean",
        "cause_seam_source_boundary_or_protected_risk_proxy_bmw_minus_clean",
        "unknown_unclassified_frac_of_phase3_unknown_bmw_minus_clean",
    ]
    for key in highlight_keys:
        y = draw_wrapped(draw, 36, y, f"- {key}={fmt(diff.get(key))}", 145, fill=(224, 232, 255), size=12)

    x0, x1 = 28, 940
    x2, x3 = 970, 1870
    paste_thumb(board, OUT_DIR / RUN_NAMES[0] / f"{RUN_NAMES[0]}_cause_overlay_board.jpg", (x0, 560, x1, 1010))
    draw_text(draw, (x0, 530), "BMW Phase4 cause board", size=18)
    paste_thumb(board, OUT_DIR / RUN_NAMES[1] / f"{RUN_NAMES[1]}_cause_overlay_board.jpg", (x2, 560, x3, 1010))
    draw_text(draw, (x2, 530), "Clean Phase4 cause board", size=18)
    paste_thumb(board, OUT_DIR / RUN_NAMES[0] / f"{RUN_NAMES[0]}_phase3_vs_phase4_review_board.jpg", (x0, 1080, x1, 1450))
    draw_text(draw, (x0, 1050), "BMW Phase3 vs Phase4", size=18)
    paste_thumb(board, OUT_DIR / RUN_NAMES[1] / f"{RUN_NAMES[1]}_phase3_vs_phase4_review_board.jpg", (x2, 1080, x3, 1450))
    draw_text(draw, (x2, 1050), "Clean Phase3 vs Phase4", size=18)
    board.save(BOARD, quality=92)


def fmt(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return "n/a"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    case_results = [compute_case(run_name) for run_name in RUN_NAMES]
    summary = aggregate(case_results)
    BATCH_SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_phase4a_cause_map_cpu_local",
        "accepted_evidence_type": "unknown_cause_attribution_and_repairability_map",
        "scope": {
            "cpu_local_only": True,
            "uses_existing_phase3_sidecars_only": True,
            "fixed_cases_only": RUN_NAMES,
            "remote_status_or_exec_used": False,
            "a100_used_or_needed": False,
            "model_inference_used": False,
            "vggt_used": False,
            "dit_flux_generation_used": False,
            "source_replacement_used": False,
            "rgb_repair_created": False,
            "semantic_layer_truth_created": False,
            "red_promotion": False,
        },
        "taxonomy": {
            "cause_codes": CAUSE_CODES,
            "flag_bits": FLAG_BITS,
            "repairability_codes": REPAIRABILITY_CODES,
        },
        "inputs": {
            "phase3_manifest": rel(PHASE3_DIR / "db64_ltr_v0_phase3_sidecar_manifest.json"),
            "phase3_fetch_dir": rel(PHASE3_FETCH),
        },
        "outputs": {
            "output_dir": rel(OUT_DIR),
            "batch_summary": rel(BATCH_SUMMARY),
            "board": rel(BOARD),
        },
        "summary": summary,
        "decision": {
            "accepted_as_cause_evidence": True,
            "accepted_as_repair": False,
            "accepted_as_source_truth": False,
            "accepted_as_semantic_layer_truth": False,
            "a100_needed_now": False,
            "next_allowed_step": "fresh brief/sub-scope for cause-map refinement with z residual cause maps or protected masks; no RGB repair yet",
        },
        "claim_boundary": [
            "Phase4a decomposes Phase3 sidecar states into evidence/policy causes.",
            "It does not repair pixels or create a renderer output.",
            "It does not claim semantic road/wall/car layers.",
            "z mismatch is only a proxy until z residual/cause maps are instrumented.",
            "protected object/lane/curb masks are not available; source-boundary risk remains a proxy.",
        ],
    }
    scan_text = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(case_results, ensure_ascii=False)
    hits = secret_hits(scan_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    write_board(summary, manifest)
    # Board path is added after rendering so image existence can be checked independently.
    manifest["outputs"]["board_exists"] = BOARD.exists()
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": manifest["status"],
                "secret_hits": manifest["strict_secret_scan"]["hit_count"],
                "summary": rel(BATCH_SUMMARY),
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
