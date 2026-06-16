from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DB64 = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0"
DB65 = ROOT / "deliverables" / "layered_target_raycaster" / "db65_visible_photometric_fallback"
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db66_narrow_inpaint_fallback"

RUN = "02a00399_a000_bmw"
HARD = DB64 / "phase2_lidar_zbuffer_fetch" / RUN / f"{RUN}_hard_select.jpg"
DB64_VISIBLE = DB64 / "phase5a_continuous_surface" / "db64_phase5a_current_best_visible_candidate_rejected.png"
DB65_BEST = DB65 / "db65_best_visible_photometric_candidate.png"
DB65_MASK = DB65 / "db65_best_edit_mask.png"
PHASE5A_REVIEW = DB64 / "phase5a_continuous_surface" / "fetch" / RUN / f"{RUN}_phase5a_crop_review.jpg"

BEST = OUT_DIR / "db66_best_visible_inpaint_candidate.png"
BEST_MASK = OUT_DIR / "db66_best_inpaint_mask.png"
BEST_DIFF = OUT_DIR / "db66_best_diff_x6.png"
BOARD = OUT_DIR / "db66_narrow_inpaint_fallback_board.jpg"
TOP_SHEET = OUT_DIR / "db66_top_variant_roi_sheet.jpg"
METRICS = OUT_DIR / "db66_narrow_inpaint_fallback_metrics.json"
MANIFEST = OUT_DIR / "db66_narrow_inpaint_fallback_manifest.json"

DB25_ROI = (850, 420, 1650, 720)

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "trycloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "json_token": re.compile(r'"token"\s*:\s*"[A-Za-z0-9._\-]{12,}"'),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
}


def rel(path: Path | str) -> str:
    p = Path(path)
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return "<non-repo path omitted>"


def read_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def save_rgb(path: Path, arr: np.ndarray) -> None:
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB").save(path)


def cleanup() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.png", "*.jpg", "*.json"):
        for path in OUT_DIR.glob(pattern):
            path.unlink()


def rgb_to_y(rgb: np.ndarray) -> np.ndarray:
    x = rgb.astype(np.float32)
    return 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]


def horizontal_edge_energy(rgb: np.ndarray, mask: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> float:
    y = rgb_to_y(rgb)
    edge = np.abs(y[:, 1:] - y[:, :-1])
    m = mask[:, 1:] | mask[:, :-1]
    if roi is not None:
        x0, y0, x1, y1 = roi
        r = np.zeros_like(m, dtype=bool)
        r[y0:y1, max(0, x0 - 1) : max(0, x1 - 1)] = True
        m &= r
    if not np.any(m):
        return 0.0
    return float(edge[m].mean())


def crop(arr: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return arr[y0:y1, x0:x1]


def diff_viz(before: np.ndarray, after: np.ndarray, scale: float = 6.0) -> np.ndarray:
    d = np.abs(after.astype(np.float32) - before.astype(np.float32)).mean(axis=2) * scale
    out = np.zeros((*d.shape, 3), dtype=np.float32)
    out[..., 0] = np.clip(d, 0, 255)
    out[..., 1] = np.clip(d * 0.65, 0, 210)
    return out.astype(np.uint8)


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=(236, 236, 236), size: int = 15) -> None:
    draw.text(xy, str(text), fill=fill, font=font(size))


def draw_wrapped(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, chars: int, fill=(236, 236, 236), size: int = 14) -> int:
    for line in wrap(str(text), width=chars, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + 6
    return y


def paste_arr(board: Image.Image, arr: np.ndarray, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")
    im.thumbnail((x1 - x0, y1 - y0))
    px = x0 + ((x1 - x0) - im.width) // 2
    py = y0 + ((y1 - y0) - im.height) // 2
    board.paste(im, (px, py))
    ImageDraw.Draw(board).rectangle((px, py, px + im.width, py + im.height), outline=(180, 180, 180))


def paste_file(board: Image.Image, path: Path, box: tuple[int, int, int, int]) -> None:
    if path.exists():
        paste_arr(board, read_rgb(path), box)
        return
    draw = ImageDraw.Draw(board)
    draw.rectangle(box, fill=(35, 35, 42), outline=(110, 110, 110))
    draw_text(draw, (box[0] + 12, box[1] + 12), f"missing: {rel(path)}", fill=(255, 150, 120))


def mask_rgb(mask: np.ndarray) -> np.ndarray:
    out = np.zeros((*mask.shape, 3), dtype=np.uint8)
    out[mask > 0] = (0, 210, 255)
    return out


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pat in TOKEN_PATTERNS.items():
        found = pat.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


def build_board(best_row: dict[str, Any], db64_visible: np.ndarray, db65_best: np.ndarray, best: np.ndarray, mask: np.ndarray, rows: list[dict[str, Any]]) -> None:
    board = Image.new("RGB", (2100, 1700), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 22), "DB66 narrow-mask classic inpaint presentation fallback", size=27)
    y = 64
    for line in [
        "CPU/local only. OpenCV classic inpaint on DB65 narrow mask. Presentation-only; not source-faithful repair.",
        f"best={best_row['name']} method={best_row['method']} radius={best_row['radius']} mask={best_row['mask_name']}",
        f"score={best_row['score']:.2f} seam_reduction={best_row['seam_reduction_pct']:.2f}% roi_reduction={best_row['roi_reduction_pct']:.2f}% changed={best_row['changed_fraction']:.4f} max_delta={best_row['max_abs_delta']:.2f}",
    ]:
        y = draw_wrapped(draw, 34, y, "- " + line, 158, size=14)

    panels = [
        ("DB64 visible", db64_visible, (28, 235, 650, 545)),
        ("DB65 photometric best", db65_best, (690, 235, 1312, 545)),
        ("DB66 inpaint best", best, (1352, 235, 2070, 545)),
        ("DB25 before", crop(db65_best, DB25_ROI), (28, 620, 650, 900)),
        ("DB25 after", crop(best, DB25_ROI), (690, 620, 1312, 900)),
        ("DB25 diff x6", crop(diff_viz(db65_best, best), DB25_ROI), (1352, 620, 2070, 900)),
        ("Inpaint mask", mask_rgb(mask), (28, 970, 650, 1240)),
    ]
    for label, arr, box in panels:
        draw_text(draw, (box[0], box[1] - 28), label, size=18)
        paste_arr(board, arr, box)
    draw_text(draw, (690, 942), "Phase5a BMW evidence review", size=18)
    paste_file(board, PHASE5A_REVIEW, (690, 970, 1312, 1240))
    draw_text(draw, (1352, 942), "DB65 top-variant sheet", size=18)
    paste_file(board, DB65 / "db65_top_variant_roi_sheet.jpg", (1352, 970, 2070, 1240))

    draw_text(draw, (28, 1305), "Top DB66 ROI crops", size=18)
    for i, row in enumerate(rows[:6]):
        img = read_rgb(OUT_DIR / row["file"])
        bx = 28 + i * 335
        paste_arr(board, crop(img, DB25_ROI), (bx, 1335, bx + 310, 1518))
        draw_wrapped(draw, bx, 1526, f"{i+1}. {row['name']} score={row['score']:.1f} chg={row['changed_fraction']:.3f}", 36, size=12)
    board.save(BOARD, quality=92)


def build_top_sheet(db65_best: np.ndarray, rows: list[dict[str, Any]]) -> None:
    items: list[tuple[str, np.ndarray]] = [("DB65 before", crop(db65_best, DB25_ROI))]
    for row in rows[:8]:
        img = read_rgb(OUT_DIR / row["file"])
        items.append((f"{row['name']} score={row['score']:.1f} chg={row['changed_fraction']:.3f}", crop(img, DB25_ROI)))
    board = Image.new("RGB", (1360, 1500), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    for i, (label, arr) in enumerate(items):
        x = (i % 2) * 680 + 20
        y = (i // 2) * 300 + 20
        draw_text(draw, (x, y), label, size=15)
        im = Image.fromarray(arr).resize((640, 240))
        board.paste(im, (x, y + 30))
        draw.rectangle((x, y + 30, x + 640, y + 270), outline=(190, 190, 190))
    board.save(TOP_SHEET, quality=92)


def main() -> None:
    cleanup()
    hard = read_rgb(HARD)
    db64_visible = read_rgb(DB64_VISIBLE)
    db65_best = read_rgb(DB65_BEST)
    mask0 = np.asarray(Image.open(DB65_MASK).convert("L"), dtype=np.uint8)
    valid = (db65_best.sum(axis=2) > 18).astype(np.uint8)

    kernel3 = np.ones((3, 3), dtype=np.uint8)
    masks = {
        "db65_mask": ((mask0 > 0) & (valid > 0)).astype(np.uint8) * 255,
        "core_erode": cv2.erode(((mask0 > 0) & (valid > 0)).astype(np.uint8) * 255, kernel3, iterations=1),
        "soft_dilate": cv2.dilate(((mask0 > 0) & (valid > 0)).astype(np.uint8) * 255, kernel3, iterations=1),
    }
    bases = {"db65_best": db65_best, "hard_select": hard}
    methods = {"telea": cv2.INPAINT_TELEA, "ns": cv2.INPAINT_NS}
    radii = [1.5, 2.5, 3.5, 5.0]

    seed = masks["db65_mask"] > 0
    before_energy = horizontal_edge_energy(db65_best, seed)
    before_roi_energy = horizontal_edge_energy(db65_best, seed, DB25_ROI)
    rows: list[dict[str, Any]] = []
    kept: list[tuple[dict[str, Any], np.ndarray]] = []

    for base_name, base in bases.items():
        bgr = cv2.cvtColor(base, cv2.COLOR_RGB2BGR)
        for mask_name, mask in masks.items():
            if int(mask.sum()) == 0:
                continue
            for method_name, method in methods.items():
                for radius in radii:
                    out_bgr = cv2.inpaint(bgr, mask, float(radius), method)
                    out = cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB)
                    delta = np.abs(out.astype(np.float32) - base.astype(np.float32)).mean(axis=2)
                    changed_fraction = float(np.mean(delta > 0.25))
                    max_abs_delta = float(delta.max())
                    p95_abs_delta = float(np.percentile(delta, 95))
                    energy = horizontal_edge_energy(out, seed)
                    roi_energy = horizontal_edge_energy(out, seed, DB25_ROI)
                    seam_reduction_pct = 100.0 * (before_energy - energy) / max(before_energy, 1e-6)
                    roi_reduction_pct = 100.0 * (before_roi_energy - roi_energy) / max(before_roi_energy, 1e-6)
                    penalty = 220.0 * max(0.0, changed_fraction - 0.04) + 0.15 * max(0.0, max_abs_delta - 40.0)
                    if changed_fraction > 0.08:
                        penalty += 120.0
                    score = 0.65 * seam_reduction_pct + 0.35 * roi_reduction_pct - penalty
                    name = f"{base_name}_{mask_name}_{method_name}_r{str(radius).replace('.', 'p')}"
                    row = {
                        "name": name,
                        "file": f"{name}.png",
                        "base": base_name,
                        "mask_name": mask_name,
                        "method": method_name,
                        "radius": radius,
                        "score": float(score),
                        "seam_energy_before": before_energy,
                        "seam_energy_after": float(energy),
                        "roi_energy_before": before_roi_energy,
                        "roi_energy_after": float(roi_energy),
                        "seam_reduction_pct": float(seam_reduction_pct),
                        "roi_reduction_pct": float(roi_reduction_pct),
                        "changed_fraction": changed_fraction,
                        "p95_abs_delta": p95_abs_delta,
                        "max_abs_delta": max_abs_delta,
                    }
                    rows.append(row)
                    kept.append((row, out))
                    kept.sort(key=lambda item: item[0]["score"], reverse=True)
                    if len(kept) > 18:
                        kept.pop()

    rows.sort(key=lambda r: (r["score"], -r["changed_fraction"]), reverse=True)
    kept_by_name = {row["name"]: img for row, img in kept}
    for row in rows[:12]:
        img = kept_by_name.get(row["name"])
        if img is not None:
            save_rgb(OUT_DIR / row["file"], img)
    best_row = rows[0]
    best = read_rgb(OUT_DIR / best_row["file"])
    best_mask = masks[best_row["mask_name"]]
    save_rgb(BEST, best)
    Image.fromarray(best_mask, mode="L").save(BEST_MASK)
    Image.fromarray(diff_viz(db65_best, best), mode="RGB").save(BEST_DIFF)
    build_board(best_row, db64_visible, db65_best, best, best_mask > 0, rows)
    build_top_sheet(db65_best, rows)

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db66_narrow_inpaint_fallback_complete",
        "claim_classification": "presentation-only local interpolation candidate; rejected as source-faithful repair",
        "scope": {
            "cpu_local_only": True,
            "existing_db65_mask_and_db64_db65_images_only": True,
            "remote_status_exec": False,
            "a100": False,
            "vggt_hf_model": False,
            "dit_flux_3dgs_prompt_generation": False,
            "source_replacement": False,
            "geometry_warp": False,
            "red_promotion": False,
        },
        "inputs": {
            "db64_visible": rel(DB64_VISIBLE),
            "db65_best": rel(DB65_BEST),
            "db65_mask": rel(DB65_MASK),
        },
        "outputs": {
            "best_candidate": rel(BEST),
            "best_mask": rel(BEST_MASK),
            "best_diff_x6": rel(BEST_DIFF),
            "board": rel(BOARD),
            "top_variant_roi_sheet": rel(TOP_SHEET),
            "metrics": rel(METRICS),
            "manifest": rel(MANIFEST),
        },
        "best": best_row,
        "decision": {
            "accepted_if_vision_ok": False,
            "source_faithful_repair_allowed": False,
            "reason": "Classic inpaint interpolates local image content inside unsupported seam columns, so it can only be presentation-only even if it looks better.",
        },
        "mask_stats": {name: float((mask > 0).mean()) for name, mask in masks.items()},
    }
    hits = secret_hits(json.dumps(manifest, ensure_ascii=False))
    manifest["strict_secret_scan"] = {"hit_count": sum(int(h["count"]) for h in hits), "hits": hits}
    METRICS.write_text(json.dumps({"top": rows[:12], "all_count": len(rows)}, indent=2), encoding="utf-8")
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"best": rel(BEST), "board": rel(BOARD), "manifest": rel(MANIFEST)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
