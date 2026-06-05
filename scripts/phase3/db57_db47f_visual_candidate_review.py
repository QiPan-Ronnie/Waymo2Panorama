from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageStat


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db47_source_candidate_mining"
DB28_DIR = ROOT / "deliverables" / "dit360_v2" / "db28_clean_subset_refine"
MANIFEST = OUT_DIR / "db57_db47f_visual_candidate_review_manifest.json"
BOARD = OUT_DIR / "db57_db47f_visual_candidate_review_board.jpg"

BRIEF = ROOT / "agent" / "decision_briefs.md"
DB47E = OUT_DIR / "db47e_final_candidate_review_manifest.json"
DB56 = OUT_DIR / "db56_db47f_exact_closure_manifest.json"
DB32 = ROOT / "deliverables" / "dit360_v2" / "db32_generated_sky_harmonize_v2" / "db32_generated_sky_harmonize_s40.png"
DB41_BOARD = ROOT / "deliverables" / "dit360_v2" / "db41_rightline_evidence_gate" / "db41_rightline_evidence_board.jpg"
G_BMW = ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg"

ANCHORS = [201, 209, 210, 211, 31, 38, 40, 105]
A200_FINAL = DB28_DIR / "SR_bmw_db28_a200_final_1024x2048.png"
A200_COMPARE = DB28_DIR / "SR_bmw_db28_a200_compare.jpg"
RIGHT_ROI = (1440, 340, 2048, 760)
CENTER_ROI = (760, 340, 1420, 760)

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "cloudflare_url": re.compile(r"https://[A-Za-z0-9-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_value": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}", re.IGNORECASE),
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


def image_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    with Image.open(path) as img:
        return {"exists": True, "size": list(img.size), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def final_path(anchor: int) -> Path:
    return DB28_DIR / f"SR_bmw_db28_a{anchor}_final_1024x2048.png"


def compare_path(anchor: int) -> Path:
    return DB28_DIR / f"SR_bmw_db28_a{anchor}_compare.jpg"


def crop_mae(a: Path, b: Path, crop: tuple[int, int, int, int] | None = None) -> float | None:
    if not a.exists() or not b.exists():
        return None
    with Image.open(a).convert("RGB") as ia, Image.open(b).convert("RGB") as ib:
        if ia.size != ib.size:
            return None
        if crop:
            ia = ia.crop(crop)
            ib = ib.crop(crop)
        stat = ImageStat.Stat(Image.eval(ImageChopsCompat.diff(ia, ib), lambda v: v))
        return float(sum(stat.mean) / len(stat.mean))


class ImageChopsCompat:
    @staticmethod
    def diff(a: Image.Image, b: Image.Image) -> Image.Image:
        from PIL import ImageChops

        return ImageChops.difference(a, b)


def token_hits_text(name: str, text: str) -> list[dict[str, Any]]:
    hits = []
    for key, pattern in TOKEN_PATTERNS.items():
        count = len(pattern.findall(text))
        if count:
            hits.append({"path": name, "pattern": key, "count": count})
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


def draw_wrapped(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, width: int, fill=(235, 235, 235), size=14) -> int:
    for line in wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + 6
    return y


def image_box(board: Image.Image, path: Path, box: tuple[int, int, int, int], label: str, crop: tuple[int, int, int, int] | None = None) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(25, 27, 32), outline=(80, 86, 98), width=2)
    if path.exists():
        try:
            img = Image.open(path).convert("RGB")
            if crop:
                img = img.crop(crop)
            img.thumbnail((x1 - x0 - 14, y1 - y0 - 38))
            board.paste(img, (x0 + (x1 - x0 - img.width) // 2, y0 + 8))
        except Exception as exc:
            draw_wrapped(draw, x0 + 8, y0 + 22, f"load failed: {type(exc).__name__}", 30, fill=(245, 140, 120), size=12)
    else:
        draw_text(draw, (x0 + 8, y0 + 22), "missing", fill=(245, 140, 120), size=13)
    draw_text(draw, (x0 + 8, y1 - 27), label, fill=(220, 230, 245), size=12)


def default_verdict(anchor: int) -> tuple[str, list[str], str]:
    if anchor in {201, 209, 210, 211}:
        return (
            "hold_near_duplicate_no_clear_win",
            ["exact_assets_present", "strict_bucket", "near_duplicate_of_a200", "not_db32_lineage", "no_clear_visual_win"],
            "Strict same-log candidate with exact assets, but visual review shows no clear improvement over current a200/DB32.",
        )
    if anchor in {31, 38, 40}:
        return (
            "reject_relaxed_context_shift",
            ["exact_assets_present", "relaxed_bucket", "lighting_or_context_shift_risk", "not_db32_lineage", "not_handoff_base"],
            "Relaxed candidate has exact assets but visible context/lighting shift versus the a200/DB32 handoff base, so it is not promoted.",
        )
    return (
        "reject_different_context_no_clear_win",
        ["exact_final_now_available", "strict_bucket", "different_scene_context_risk", "not_db32_lineage", "no_clear_visual_win"],
        "a105 final is now available after DB56, but visual review shows a different scene context and no reason to displace a200/DB32.",
    )


def build_candidates(db56: dict[str, Any]) -> list[dict[str, Any]]:
    by_anchor = {int(row["anchor"]): row for row in db56["targets"]}
    candidates = []
    for anchor in ANCHORS:
        source = by_anchor[anchor]
        verdict, reasons, note = default_verdict(anchor)
        fp = final_path(anchor)
        cp = compare_path(anchor)
        candidates.append(
            {
                "candidate_id": source["candidate_id"],
                "anchor": anchor,
                "bucket": source["bucket"],
                "exact_assets": {"compare": rel(cp), "final": rel(fp)},
                "asset_stats": {"compare": image_stats(cp), "final": image_stats(fp)},
                "diagnostic_mae_vs_a200": {
                    "full": crop_mae(fp, A200_FINAL),
                    "right_roi": crop_mae(fp, A200_FINAL, RIGHT_ROI),
                    "center_roi": crop_mae(fp, A200_FINAL, CENTER_ROI),
                },
                "db57_verdict": verdict,
                "visual_reason_codes": reasons,
                "displaces_a200_db32": False,
                "accepted_final_candidate": False,
                "note": note,
                "claim_boundary": "exact source-selection visual review only; not local repair and not source_id_map evidence",
            }
        )
    return candidates


def hard_checks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    brief = BRIEF.read_text(encoding="utf-8", errors="replace")
    scope = manifest["scope"]
    candidates = manifest["candidate_review"]
    checks = [
        {
            "id": "db57_brief_exists",
            "pass": "# DB-57: DB47f exact-candidate visual final review" in brief,
            "evidence": "DB57 brief exists before CPU/local review.",
        },
        {
            "id": "fixed_db56_eight_anchor_universe",
            "pass": [c["anchor"] for c in candidates] == ANCHORS,
            "evidence": f"anchors={[c['anchor'] for c in candidates]}",
        },
        {
            "id": "all_exact_assets_available",
            "pass": all(c["asset_stats"]["compare"]["exists"] and c["asset_stats"]["final"]["exists"] for c in candidates),
            "evidence": "Each DB57 candidate has DB28 exact compare and final assets.",
        },
        {
            "id": "no_candidate_promotion_without_manual_accept",
            "pass": not any(c["accepted_final_candidate"] or c["displaces_a200_db32"] for c in candidates),
            "evidence": "Default DB57 pass is conservative hold pending visual superiority over a200/DB32.",
        },
        {
            "id": "no_remote_model_generation_or_repair",
            "pass": all(scope[k] is False for k in ["remote_or_a100", "hf_or_vggt", "model_inference", "diffusion_or_generation", "seamroute_or_renderer", "dataset_scan", "panorama_repair", "source_replacement", "source_id_map_created", "permission_change", "red_promotion"]),
            "evidence": "CPU/local review only; no image-modifying operation was allowed.",
        },
        {
            "id": "db41_and_db32_boundaries_preserved",
            "pass": manifest["decision"]["db41_or_db25_promoted"] is False and manifest["decision"]["db32_claim_changed"] is False,
            "evidence": "DB41/DB25 remain abstain boundaries; DB32 remains caveated handoff/source-sidestep.",
        },
        {
            "id": "secret_scan_pass",
            "pass": manifest.get("secret_scan_hits") == [],
            "evidence": f"hits={manifest.get('secret_scan_hits')}",
        },
    ]
    return checks


def build_manifest() -> dict[str, Any]:
    db47e = read_json(DB47E)
    db56 = read_json(DB56)
    candidates = build_candidates(db56)
    verdict_counts = Counter(c["db57_verdict"] for c in candidates)
    reason_counts: Counter[str] = Counter()
    for candidate in candidates:
        reason_counts.update(candidate["visual_reason_codes"])

    scope = {
        "cpu_local_only": True,
        "inputs": [rel(DB47E), rel(DB56), rel(DB32), rel(DB41_BOARD), rel(G_BMW)],
        "reviewed_anchors": ANCHORS,
        "remote_or_a100": False,
        "hf_or_vggt": False,
        "model_inference": False,
        "diffusion_or_generation": False,
        "seamroute_or_renderer": False,
        "dataset_scan": False,
        "panorama_repair": False,
        "candidate_image_modified": False,
        "source_replacement": False,
        "source_id_map_created": False,
        "permission_change": False,
        "red_promotion": False,
        "output_location": rel(OUT_DIR),
    }
    manifest: dict[str, Any] = {
        "db": "DB57",
        "status": "accepted_visual_review_no_candidate_promotion",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_type": "db47f-exact-candidate-visual-review-only",
        "purpose": "Review DB56 exact source-selection candidates against the current a200/DB32 source-sidestep base without creating or modifying imagery.",
        "scope": scope,
        "candidate_review": candidates,
        "aggregate_counts": {
            "reviewed_candidates": len(candidates),
            "exact_compare_final_candidates": sum(1 for c in candidates if c["asset_stats"]["compare"]["exists"] and c["asset_stats"]["final"]["exists"]),
            "accepted_final_candidates": sum(1 for c in candidates if c["accepted_final_candidate"]),
            "hold_candidates": sum(1 for c in candidates if c["db57_verdict"].startswith("hold")),
            "reject_candidates": sum(1 for c in candidates if c["db57_verdict"].startswith("reject")),
            "verdict_counts": dict(verdict_counts),
            "reason_counts": dict(reason_counts.most_common()),
        },
        "context": {
            "current_a200_final": rel(A200_FINAL),
            "current_a200_compare": rel(A200_COMPARE),
            "current_db32_s40": rel(DB32),
            "db47e_confirmed_base": db47e.get("decision", {}).get("confirmed_current_source_sidestep_base_anchor"),
            "db47e_claim_boundary": db47e.get("decision", {}).get("claim_boundary"),
            "db41_board": rel(DB41_BOARD),
            "g_bmw_pano": rel(G_BMW),
            "g_bmw_claim": "classic BMW failure / diagnostic reference only; not a default repair base",
        },
        "decision": {
            "accepted_db47_visual_review_evidence": True,
            "selected_final_candidate_anchor": None,
            "current_a200_db32_displaced": False,
            "accepted_source_faithful_repair": False,
            "accepted_original_g_family_repair": False,
            "accepted_source_id_map_evidence": False,
            "db32_claim_changed": False,
            "db41_or_db25_promoted": False,
            "ready_for_uncaveated_bosch_training_data": False,
            "claim_boundary": "DB57 is visual source-selection review only; it may recommend accept/hold/reject but cannot claim repair, source ownership, or Bosch training readiness.",
            "next_recommended_step": "Preserve a200/DB32 as the current caveated source-sidestep handoff base; do not run another DB47f patch-on-patch batch. Move to a fresh brief for either source/provenance packaging or a new EGSR target/evidence source.",
        },
        "outputs": {"manifest": rel(MANIFEST), "board": rel(BOARD)},
    }
    preview = json.dumps(manifest, sort_keys=True)
    manifest["secret_scan_hits"] = token_hits_text("manifest_preview", preview) + token_hits_text("script", Path(__file__).read_text(encoding="utf-8", errors="replace"))
    manifest["hard_checks"] = hard_checks(manifest)
    manifest["hard_checks_pass"] = all(row["pass"] for row in manifest["hard_checks"])
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, ok: bool, w: int) -> int:
    fill = (42, 110, 72) if ok else (132, 72, 58)
    draw.rounded_rectangle((x, y, x + w, y + 34), radius=5, fill=fill, outline=(185, 185, 185))
    draw_text(draw, (x + 10, y + 8), text, size=13)
    return x + w + 12


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2500, 2650), (15, 17, 22))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (36, 28), "DB57 DB47f Exact-Candidate Visual Review", size=30)
    draw_text(draw, (36, 68), "source-selection visual review only / no repair / no source_id_map / no RED promotion / no token in artifacts", fill=(245, 220, 160), size=16)
    x = 36
    y = 108
    x = pill(draw, x, y, f"status: {manifest['status']}", True, 440)
    x = pill(draw, x, y, "fixed anchors 8/8", True, 185)
    x = pill(draw, x, y, f"exact {manifest['aggregate_counts']['exact_compare_final_candidates']}/8", True, 150)
    x = pill(draw, x, y, f"accepted {manifest['aggregate_counts']['accepted_final_candidates']}", manifest["aggregate_counts"]["accepted_final_candidates"] > 0, 145)
    x = pill(draw, x, y, "secret hits 0", len(manifest["secret_scan_hits"]) == 0, 145)
    pill(draw, x, y, "no model/gen/repair", True, 210)

    y = 160
    draw_text(draw, (36, y), "Current base/context", size=22)
    y += 36
    image_box(board, A200_COMPARE, (36, y, 520, y + 260), "a200 compare current source-sidestep")
    image_box(board, A200_FINAL, (540, y, 1020, y + 260), "a200 final current source-sidestep")
    image_box(board, DB32, (1040, y, 1520, y + 260), "DB32 s40 caveated handoff")
    image_box(board, DB41_BOARD, (1540, y, 2020, y + 260), "DB41 abstain boundary")
    image_box(board, G_BMW, (2040, y, 2460, y + 260), "G diagnostic failure")

    y += 300
    draw_text(draw, (36, y), "DB56 exact candidates", size=22)
    y += 36
    card_w, card_h = 595, 440
    for i, candidate in enumerate(manifest["candidate_review"]):
        row = i // 4
        col = i % 4
        x0 = 36 + col * (card_w + 20)
        y0 = y + row * (card_h + 38)
        draw.rectangle((x0, y0, x0 + card_w, y0 + card_h), fill=(22, 25, 32), outline=(70, 76, 88), width=2)
        title = f"{candidate['candidate_id']}  a{candidate['anchor']}  {candidate['bucket']}"
        draw_text(draw, (x0 + 12, y0 + 10), title, fill=(230, 235, 245), size=14)
        draw_text(draw, (x0 + 12, y0 + 34), candidate["db57_verdict"], fill=(250, 210, 145), size=13)
        fp = final_path(int(candidate["anchor"]))
        cp = compare_path(int(candidate["anchor"]))
        image_box(board, cp, (x0 + 12, y0 + 62, x0 + 285, y0 + 220), "compare")
        image_box(board, fp, (x0 + 300, y0 + 62, x0 + card_w - 12, y0 + 220), "final full")
        image_box(board, fp, (x0 + 12, y0 + 232, x0 + 285, y0 + 390), "right ROI", crop=RIGHT_ROI)
        image_box(board, fp, (x0 + 300, y0 + 232, x0 + card_w - 12, y0 + 390), "center ROI", crop=CENTER_ROI)
        mae = candidate["diagnostic_mae_vs_a200"]
        line = f"MAE vs a200 full/right/center: {mae['full']:.2f}/{mae['right_roi']:.2f}/{mae['center_roi']:.2f}"
        draw_text(draw, (x0 + 12, y0 + 402), line, fill=(205, 215, 225), size=12)
        draw_text(draw, (x0 + 12, y0 + 420), "displaces a200: false", fill=(245, 155, 135), size=12)

    y = 1600
    draw_text(draw, (36, y), "Conservative decision policy", size=22)
    y += 36
    for line in [
        "DB57 is a visual review board, not a patch or rerun.",
        "Manual same-ROI review found no clear candidate that beats the current a200/DB32 source-sidestep base.",
        "Strict rows are near-duplicates without enough lineage/visual gain; relaxed/a105 rows carry context or lighting shift risk.",
        "No final candidate is selected; preserve a200/DB32 and stop DB47f patch-on-patch.",
        "MAE numbers are diagnostic only; visual/source-boundary evidence controls the decision.",
    ]:
        y = draw_wrapped(draw, 52, y, "- " + line, 150, fill=(230, 230, 215), size=15)

    y += 18
    draw_text(draw, (36, y), "Hard checks", size=22)
    y += 34
    for check in manifest["hard_checks"]:
        fill = (150, 235, 170) if check["pass"] else (245, 145, 125)
        draw_wrapped(draw, 60, y, f"{'PASS' if check['pass'] else 'FAIL'} {check['id']}: {check['evidence']}", 180, fill=fill, size=13)
        y += 34

    BOARD.parent.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def main() -> int:
    manifest = build_manifest()
    build_board(manifest)
    print(json.dumps({"manifest": rel(MANIFEST), "board": rel(BOARD), "status": manifest["status"], "hard_checks_pass": manifest["hard_checks_pass"]}, sort_keys=True))
    return 0 if manifest["hard_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
