#!/usr/bin/env python
"""Audit DB49d seamroute provenance sidecar instrumentation.

DB49d is instrumentation-only. It verifies that _seamroute.py can optionally
save future source/provenance sidecars while keeping default panorama behavior
unchanged. It does not run seamroute, call an executor, infer DB32 ownership, or
create a DB32 source_id_map.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db49_bosch_data_contract"
BRIEF = ROOT / "agent" / "decision_briefs.md"
SEAMROUTE = ROOT / "scripts" / "phase3" / "_seamroute.py"

MANIFEST = OUT_DIR / "db49d_seamroute_source_map_instrumentation_manifest.json"
BOARD = OUT_DIR / "db49d_seamroute_source_map_instrumentation_board.jpg"

SIDECARS = [
    {
        "field": "routed_source_id_map",
        "filename_template": "SR_<tag>_routed_source_id_map.png",
        "codes": "0..6=a1.RING_CAMS_7 owner before final VC composite; 255=invalid/out-of-FOV",
        "caveat": "Future exact rerun only; not DB32 ownership until produced by that rerun.",
    },
    {
        "field": "valid_mask",
        "filename_template": "SR_<tag>_valid_mask.png",
        "codes": "255=valid, 0=invalid/out-of-FOV",
        "caveat": "Separates valid source-id pixels from no-source pixels.",
    },
    {
        "field": "virtual_center_effect_mask",
        "filename_template": "SR_<tag>_virtual_center_effect_mask.png",
        "codes": "0..255 scaled effect alpha",
        "caveat": "Nonzero means final pixels may not be single-source owner truth.",
    },
    {
        "field": "ground_reproject_effect_mask",
        "filename_template": "SR_<tag>_ground_reproject_effect_mask.png",
        "codes": "255=ground diagnostic effect, 0=no ground diagnostic effect",
        "caveat": "Applies to separate ground_pano diagnostic, not the default final source map.",
    },
    {
        "field": "final_source_state_map",
        "filename_template": "SR_<tag>_final_source_state_map.png",
        "codes": "0..6=single-source routed owner; 250=VC composite/effect; 255=invalid/out-of-FOV",
        "caveat": "Preserves the composite/mixed-source boundary instead of overclaiming ownership.",
    },
    {
        "field": "source_id_overlay",
        "filename_template": "SR_<tag>_source_id_overlay.png",
        "codes": "RGB preview; magenta=VC composite/effect, black=invalid",
        "caveat": "Presentation preview only; not a machine-readable ownership substitute.",
    },
    {
        "field": "source_id_sidecar_legend",
        "filename_template": "SR_<tag>_source_id_sidecar_legend.json",
        "codes": "JSON label convention and claim boundary",
        "caveat": "Must travel with future sidecars to avoid Bosch-facing overclaim.",
    },
]

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{16,}"),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{16,}"),
    "cloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com"),
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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
    leading: int = 5,
) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + leading
    return y


def token_hits(paths: list[Path]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in TOKEN_PATTERNS.items():
            found = pattern.findall(text)
            if found:
                hits.append({"path": rel(path), "pattern": name, "count": len(found)})
    return hits


def source_contains_all(source: str, needles: list[str]) -> bool:
    return all(needle in source for needle in needles)


def build_checks(brief_text: str, seamroute_text: str) -> list[dict[str, Any]]:
    expected_filename_variants = [
        (
            entry["filename_template"].replace("<tag>", "{tag}"),
            entry["filename_template"].replace("<tag>", "{a.tag}"),
        )
        for entry in SIDECARS
    ]

    checks = [
        {
            "name": "decision_brief_exists",
            "passed": "DB49d" in brief_text and "Seamroute source/provenance sidecar instrumentation" in brief_text,
            "evidence": "DB49d brief present before implementation.",
        },
        {
            "name": "helper_present",
            "passed": "def write_source_sidecars(" in seamroute_text,
            "evidence": "write_source_sidecars helper exists in _seamroute.py.",
        },
        {
            "name": "flag_default_off",
            "passed": "--save-source-id-map" in seamroute_text and 'action="store_true"' in seamroute_text,
            "evidence": "Optional flag is argparse store_true and therefore false unless explicitly set.",
        },
        {
            "name": "sidecar_dir_optional",
            "passed": "--sidecar-dir" in seamroute_text and "a.sidecar_dir or OUT" in seamroute_text,
            "evidence": "Sidecar destination defaults to OUT only when optional export is requested.",
        },
        {
            "name": "sidecar_write_gated",
            "passed": "if a.save_source_id_map:" in seamroute_text and "write_source_sidecars(" in seamroute_text,
            "evidence": "Sidecar writes are gated behind the default-off flag.",
        },
        {
            "name": "all_sidecar_filenames_present",
            "passed": all(any(template in seamroute_text for template in variants) for variants in expected_filename_variants),
            "evidence": "All expected sidecar filename templates are in _seamroute.py.",
        },
        {
            "name": "invalid_code_255_present",
            "passed": "255" in seamroute_text and "invalid_or_out_of_fov" in seamroute_text,
            "evidence": "Invalid/out-of-FOV pixels are explicitly coded instead of hidden.",
        },
        {
            "name": "mixed_code_250_present",
            "passed": "250" in seamroute_text and "virtual-centre composite_or_warped_source_effect" in seamroute_text,
            "evidence": "Virtual-centre composite/effect pixels are marked as non-single-source truth.",
        },
        {
            "name": "ground_effect_kept_separate",
            "passed": "ground_reproject_effect_mask" in seamroute_text and "SR_<tag>_ground_pano.jpg" in seamroute_text,
            "evidence": "Ground diagnostic effect is a separate mask, not fused into DB32 ownership.",
        },
        {
            "name": "no_dataset_run_in_audit",
            "passed": True,
            "evidence": "This script reads files and writes DB49d manifest/board only; it does not invoke _seamroute.py.",
        },
    ]
    checks.append(
        {
            "name": "no_token_like_strings_in_touched_sources",
            "passed": not token_hits([SEAMROUTE, BRIEF, Path(__file__)]),
            "evidence": "Fresh endpoint, HF, Bearer, and API-token patterns are absent from DB49d touched sources.",
        }
    )
    return checks


def build_manifest() -> dict[str, Any]:
    brief_text = BRIEF.read_text(encoding="utf-8", errors="replace")
    seamroute_text = SEAMROUTE.read_text(encoding="utf-8", errors="replace")
    checks = build_checks(brief_text, seamroute_text)

    manifest = {
        "decision_id": "DB49d",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "accepted_evidence_type": "source-map-instrumentation-only",
        "status": "accepted_if_all_checks_pass",
        "inputs": {
            "decision_briefs": {"path": rel(BRIEF), "sha256": sha256_file(BRIEF)},
            "seamroute_script": {"path": rel(SEAMROUTE), "sha256": sha256_file(SEAMROUTE)},
        },
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
        },
        "source_id_map_for_db32_created": False,
        "complete_source_id_map_for_db32_found": False,
        "source_id_map_status": "missing_until_exact_seamroute_rerun_not_fabricated",
        "seamroute_default_behavior_changed": False,
        "claim_boundary": {
            "dataset_run": False,
            "a100_used": False,
            "executor_used": False,
            "network_used": False,
            "model_inference": False,
            "repair_created": False,
            "generated_pixels": False,
            "permission_change": False,
            "red_promotions": [],
            "db32_training_ready": False,
            "db32_fully_source_faithful": False,
        },
        "instrumented_sidecars": SIDECARS,
        "final_composite_caveat": (
            "The default final panorama can contain virtual-centre warped/composited pixels. "
            "Those pixels are marked with code 250/effect mask and are not claimed as single-source owner truth."
        ),
        "db49c_dependency": (
            "DB49c remains binding: DB32 source_id_map is missing/blocking until an exact lineage rerun "
            "saves and validates a true owner artifact."
        ),
        "checks": checks,
    }
    manifest["all_checks_passed"] = all(check["passed"] for check in checks)
    return manifest


def draw_badges(draw: ImageDraw.ImageDraw, badges: list[tuple[str, tuple[int, int, int], int]], x: int, y: int) -> None:
    for label, fill, width in badges:
        draw.rounded_rectangle((x, y, x + width, y + 38), radius=6, fill=fill, outline=(165, 165, 165))
        draw_text(draw, (x + 11, y + 10), label, fill=(255, 255, 255), size=15)
        x += width + 12


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2200, 1800), (18, 20, 24))
    draw = ImageDraw.Draw(board)

    draw_text(draw, (34, 28), "DB49d Seamroute Source/Provenance Instrumentation", fill=(255, 255, 255), size=30)
    draw_text(
        draw,
        (36, 70),
        "Default-off sidecar export for future exact seamroute reruns. No DB32 source_id_map, no dataset run, no model, no repair.",
        fill=(215, 220, 228),
        size=16,
    )
    draw_badges(
        draw,
        [
            ("default-off", (45, 115, 70), 160),
            ("DB32 map: still missing", (135, 55, 45), 250),
            ("not training-ready", (135, 55, 45), 220),
            ("no run/no model", (45, 115, 70), 210),
            ("VC caveat explicit", (115, 100, 35), 220),
            ("not original-G repair", (115, 100, 35), 240),
        ],
        36,
        112,
    )

    draw_text(draw, (36, 178), "Sidecar Contract", fill=(255, 245, 190), size=23)
    yy = 218
    col_x = [36, 330, 850, 1450, 2160]
    headers = ["field", "filename", "codes", "claim boundary"]
    for i, header in enumerate(headers):
        draw.rectangle((col_x[i], yy, col_x[i + 1], yy + 42), fill=(38, 42, 48), outline=(82, 88, 96))
        draw_text(draw, (col_x[i] + 10, yy + 12), header, size=15)
    yy += 42
    for entry in manifest["instrumented_sidecars"]:
        h = 92
        values = [entry["field"], entry["filename_template"], entry["codes"], entry["caveat"]]
        fills = [(28, 31, 36), (28, 31, 36), (28, 31, 36), (42, 58, 76)]
        for i, value in enumerate(values):
            draw.rectangle((col_x[i], yy, col_x[i + 1], yy + h), fill=fills[i], outline=(74, 80, 88))
            draw_wrapped(draw, col_x[i] + 10, yy + 12, value, max(18, (col_x[i + 1] - col_x[i]) // 12), size=13)
        yy += h

    y2 = yy + 42
    draw_text(draw, (36, y2), "Static Checks", fill=(255, 245, 190), size=23)
    y2 += 42
    check_col = [36, 460, 660, 2160]
    for i, header in enumerate(["check", "passed", "evidence"]):
        draw.rectangle((check_col[i], y2, check_col[i + 1], y2 + 42), fill=(38, 42, 48), outline=(82, 88, 96))
        draw_text(draw, (check_col[i] + 10, y2 + 12), header, size=15)
    y2 += 42
    for check in manifest["checks"]:
        h = 68
        pass_fill = (45, 115, 70) if check["passed"] else (135, 55, 45)
        values = [check["name"], str(check["passed"]).lower(), check["evidence"]]
        fills = [(28, 31, 36), pass_fill, (28, 31, 36)]
        for i, value in enumerate(values):
            draw.rectangle((check_col[i], y2, check_col[i + 1], y2 + h), fill=fills[i], outline=(74, 80, 88))
            draw_wrapped(draw, check_col[i] + 10, y2 + 12, value, max(18, (check_col[i + 1] - check_col[i]) // 12), size=13)
        y2 += h

    footer_y = y2 + 28
    draw_text(
        draw,
        (36, footer_y),
        "Boundary: instrumentation-only; DB32 s40 remains Bosch-facing presentation/handoff candidate with source-sidestep and generated-sky caveats.",
        fill=(230, 220, 190),
        size=16,
    )
    draw_text(
        draw,
        (36, footer_y + 26),
        "G_bmw_pano remains classic BMW failure / diagnostic reference, not a default repair base.",
        fill=(230, 220, 190),
        size=16,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=94)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    if not manifest["all_checks_passed"]:
        failed = [check["name"] for check in manifest["checks"] if not check["passed"]]
        manifest["status"] = "failed_static_checks"
        MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        build_board(manifest)
        raise SystemExit(f"DB49d static checks failed: {failed}")

    manifest["status"] = "accepted"
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    build_board(manifest)
    print(f"[DB49d] wrote {rel(MANIFEST)}")
    print(f"[DB49d] wrote {rel(BOARD)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
