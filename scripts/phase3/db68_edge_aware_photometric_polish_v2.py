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
DB67 = ROOT / "deliverables" / "layered_target_raycaster" / "db67_dense_raw_aligned_surface_audit"
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db68_edge_aware_photometric_polish_v2"

RUN = "02a00399_a000_bmw"
PHASE2 = DB64 / "phase2_lidar_zbuffer_fetch" / RUN
PHASE5A = DB64 / "phase5a_continuous_surface"
FETCH5A = PHASE5A / "fetch" / RUN
FETCH67 = DB67 / "phase1_vggt_dense_evidence" / "fetch" / RUN

HARD = PHASE2 / f"{RUN}_hard_select.jpg"
DB64_VISIBLE = PHASE5A / "db64_phase5a_current_best_visible_candidate_rejected.png"
DB65_BEST = DB65 / "db65_best_visible_photometric_candidate.png"
DB65_MASK = DB65 / "db65_best_edit_mask.png"
TRANSITION = FETCH5A / f"{RUN}_before_after_transition_map.png"
VETO = FETCH5A / f"{RUN}_protected_veto_proxy_map.png"
PHASE5A_REVIEW = FETCH5A / f"{RUN}_phase5a_crop_review.jpg"
DB65_BOARD = DB65 / "db65_visible_photometric_fallback_board.jpg"
DB67_BOARD = DB67 / "phase1_vggt_dense_evidence" / "db67_phase1_vggt_dense_board.jpg"
DB67_REVIEW = FETCH67 / f"{RUN}_phase1_vggt_dense_review_768.jpg"
DB67_SUMMARY = DB67 / "phase1_vggt_dense_evidence" / "db67_phase1_vggt_dense_batch_summary.json"

BEST = OUT_DIR / "db68_best_edge_aware_candidate.png"
BEST_MASK = OUT_DIR / "db68_best_incremental_edit_mask.png"
BEST_DIFF_DB65 = OUT_DIR / "db68_best_diff_x6_vs_db65.png"
BEST_DIFF_DB64 = OUT_DIR / "db68_best_diff_x6_vs_db64.png"
BOARD = OUT_DIR / "db68_edge_aware_polish_board.jpg"
TOP_SHEET = OUT_DIR / "db68_top_variant_roi_sheet.jpg"
METRICS = OUT_DIR / "db68_edge_aware_metrics.json"
MANIFEST = OUT_DIR / "db68_edge_aware_manifest.json"

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


def read_u8(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path), dtype=np.uint8)


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


def morph(mask: np.ndarray, radius: int, op: str = "dilate") -> np.ndarray:
    if radius <= 0:
        return mask.astype(bool)
    k = 2 * radius + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    src = mask.astype(np.uint8) * 255
    if op == "erode":
        out = cv2.erode(src, kernel)
    else:
        out = cv2.dilate(src, kernel)
    return out > 0


def blur_mask(mask: np.ndarray, sigma: float) -> np.ndarray:
    src = mask.astype(np.float32)
    if sigma <= 0:
        return src
    out = cv2.GaussianBlur(src, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return np.clip(out, 0.0, 1.0)


def edge_stop(rgb: np.ndarray, valid: np.ndarray, canny_low: int = 55, canny_high: int = 115) -> np.ndarray:
    y = rgb_to_y(rgb)
    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    scale = float(np.percentile(mag[valid], 90)) if np.any(valid) else float(np.percentile(mag, 90))
    scale = max(scale, 1.0)
    stop = 1.0 / (1.0 + mag / (scale * 0.55))
    edges = cv2.Canny(np.clip(y, 0, 255).astype(np.uint8), canny_low, canny_high) > 0
    edges = morph(edges, 1)
    stop[edges] *= 0.28
    return np.clip(stop, 0.10, 1.0).astype(np.float32)


def horizontal_edge_energy(rgb: np.ndarray, seam_mask: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> float:
    y = rgb_to_y(rgb)
    edge = np.abs(y[:, 1:] - y[:, :-1])
    mask = seam_mask[:, 1:] | seam_mask[:, :-1]
    if roi is not None:
        x0, y0, x1, y1 = roi
        r = np.zeros_like(mask, dtype=bool)
        r[y0:y1, max(0, x0 - 1) : max(0, x1 - 1)] = True
        mask &= r
    if not np.any(mask):
        return 0.0
    return float(edge[mask].mean())


def crop(arr: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return arr[y0:y1, x0:x1]


def diff_viz(before: np.ndarray, after: np.ndarray, scale: float = 6.0) -> np.ndarray:
    d = np.abs(after.astype(np.float32) - before.astype(np.float32)).mean(axis=2) * scale
    out = np.zeros((*d.shape, 3), dtype=np.float32)
    out[..., 0] = np.clip(d, 0, 255)
    out[..., 1] = np.clip(d * 0.65, 0, 210)
    return out.astype(np.uint8)


def mask_rgb(mask: np.ndarray, color: tuple[int, int, int] = (0, 210, 255)) -> np.ndarray:
    out = np.zeros((*mask.shape, 3), dtype=np.uint8)
    out[mask] = color
    return out


def target_image(base: np.ndarray, mode: str, radius: int) -> np.ndarray:
    src = np.clip(base, 0, 255).astype(np.uint8)
    k = max(3, 2 * radius + 1)
    if mode == "x_smooth":
        return cv2.GaussianBlur(src, (k, 1), sigmaX=max(0.7, radius * 0.45), sigmaY=0).astype(np.float32)
    if mode == "bilateral":
        return cv2.bilateralFilter(src, k, sigmaColor=16 + radius * 2, sigmaSpace=max(2, radius)).astype(np.float32)
    if mode == "hybrid":
        xs = cv2.GaussianBlur(src, (k, 1), sigmaX=max(0.7, radius * 0.45), sigmaY=0).astype(np.float32)
        bi = cv2.bilateralFilter(src, k, sigmaColor=14 + radius * 2, sigmaSpace=max(2, radius)).astype(np.float32)
        return 0.55 * xs + 0.45 * bi
    if mode == "micro_median":
        kk = 3 if radius <= 2 else 5
        return cv2.medianBlur(src, kk).astype(np.float32)
    raise ValueError(mode)


def apply_variant(
    base: np.ndarray,
    seed: np.ndarray,
    edge_gate: np.ndarray,
    mode: str,
    radius: int,
    mask_radius: int,
    strength: float,
    max_delta: float,
    edge_floor: float,
    valid: np.ndarray,
    target: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    work_mask = morph(seed, mask_radius) & valid
    alpha = blur_mask(work_mask, sigma=max(0.8, mask_radius * 0.75 + 0.3))
    gate = np.maximum(edge_gate, edge_floor)
    alpha = np.clip(alpha * gate * strength, 0.0, 1.0)
    if target is None:
        target = target_image(base, mode, radius)
    src = base.astype(np.float32)
    delta = np.clip((target - src) * alpha[..., None], -max_delta, max_delta)
    out = np.clip(src + delta, 0, 255)
    edit = (np.max(np.abs(delta), axis=2) > 0.25) & work_mask
    return out.astype(np.uint8), edit, alpha


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
    draw = ImageDraw.Draw(board)
    if not path.exists():
        draw.rectangle(box, fill=(35, 35, 42), outline=(110, 110, 110))
        draw_wrapped(draw, box[0] + 12, box[1] + 12, f"missing: {rel(path)}", 44, fill=(255, 150, 120))
        return
    paste_arr(board, read_rgb(path), box)


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pat in TOKEN_PATTERNS.items():
        found = pat.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


def read_db67_decision() -> dict[str, Any]:
    if not DB67_SUMMARY.exists():
        return {}
    try:
        data = json.loads(DB67_SUMMARY.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        "status": data.get("status"),
        "aggregate_success": data.get("aggregate_success"),
        "phase2_renderer_allowed": data.get("phase2_renderer_allowed"),
        "clean_control_degraded": data.get("clean_control_degraded"),
        "route_verdict": data.get("route_verdict"),
    }


def build_top_sheet(db65: np.ndarray, rows: list[dict[str, Any]]) -> None:
    items: list[tuple[str, np.ndarray]] = [("DB65 current best", crop(db65, DB25_ROI))]
    for row in rows[:10]:
        img = read_rgb(OUT_DIR / row["file"])
        label = (
            f"{row['name']} score={row['score']:.2f} "
            f"gain={row['seam_gain_vs_db65_pct']:.2f}% chg={row['changed_fraction_vs_db65']:.4f}"
        )
        items.append((label, crop(img, DB25_ROI)))
    board = Image.new("RGB", (1480, 1840), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    for i, (label, arr) in enumerate(items):
        x = (i % 2) * 740 + 20
        y = (i // 2) * 305 + 20
        draw_wrapped(draw, x, y, label, 72, size=13)
        im = Image.fromarray(arr).resize((700, 238))
        board.paste(im, (x, y + 38))
        draw.rectangle((x, y + 38, x + 700, y + 276), outline=(190, 190, 190))
    board.save(TOP_SHEET, quality=92)


def build_board(
    best_row: dict[str, Any],
    hard: np.ndarray,
    db64_visible: np.ndarray,
    db65: np.ndarray,
    best: np.ndarray,
    mask: np.ndarray,
    diff_db65: np.ndarray,
    diff_db64: np.ndarray,
    rows: list[dict[str, Any]],
) -> None:
    board = Image.new("RGB", (2260, 1980), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 22), "DB68 edge-aware bounded photometric polish v2", size=28)
    y = 66
    for line in [
        "CPU/local only. DB65 narrow-mask edge-aware photometric refinement; no geometry, no inpaint, no generated pixels, no source replacement.",
        f"best={best_row['name']} mode={best_row['mode']} base={best_row['base']} radius={best_row['radius']} mask_radius={best_row['mask_radius']} strength={best_row['strength']} max_delta={best_row['max_delta']}",
        "classification candidate=presentation/diagnostic photometric polish only; DB67 Phase2 renderer remains disallowed.",
        f"score={best_row['score']:.3f} seam_gain_vs_db65={best_row['seam_gain_vs_db65_pct']:.2f}% roi_gain_vs_db65={best_row['roi_gain_vs_db65_pct']:.2f}% changed_vs_db65={best_row['changed_fraction_vs_db65']:.4f} max_delta_vs_db65={best_row['max_abs_delta_vs_db65']:.2f}",
    ]:
        y = draw_wrapped(draw, 34, y, "- " + line, 170, size=14)

    panels = [
        ("HardSelect control", hard, (28, 245, 540, 520)),
        ("DB64 rejected visible diagnostic", db64_visible, (575, 245, 1087, 520)),
        ("DB65 current best", db65, (1122, 245, 1634, 520)),
        ("DB68 best candidate", best, (1669, 245, 2232, 520)),
        ("DB25 ROI DB65 before", crop(db65, DB25_ROI), (28, 610, 710, 880)),
        ("DB25 ROI DB68 after", crop(best, DB25_ROI), (750, 610, 1432, 880)),
        ("DB25 diff x6 vs DB65", crop(diff_db65, DB25_ROI), (1472, 610, 2232, 880)),
        ("Incremental edit mask", mask_rgb(mask), (28, 970, 710, 1240)),
        ("Full diff x6 vs DB64", diff_db64, (750, 970, 1432, 1240)),
    ]
    for label, arr, box in panels:
        draw_text(draw, (box[0], box[1] - 28), label, size=17)
        paste_arr(board, arr, box)

    draw_text(draw, (1472, 942), "DB67 dense evidence context", size=17)
    paste_file(board, DB67_REVIEW if DB67_REVIEW.exists() else DB67_BOARD, (1472, 970, 2232, 1240))
    draw_text(draw, (28, 1300), "DB65 board reference", size=17)
    paste_file(board, DB65_BOARD, (28, 1330, 710, 1618))
    draw_text(draw, (750, 1300), "DB67 board reference", size=17)
    paste_file(board, DB67_BOARD, (750, 1330, 1432, 1618))
    draw_text(draw, (1472, 1300), "Phase5a crop evidence", size=17)
    paste_file(board, PHASE5A_REVIEW, (1472, 1330, 2232, 1618))

    draw_text(draw, (28, 1684), "Top DB68 ROI crops", size=18)
    for i, row in enumerate(rows[:6]):
        img = read_rgb(OUT_DIR / row["file"])
        bx = 28 + i * 365
        paste_arr(board, crop(img, DB25_ROI), (bx, 1720, bx + 340, 1895))
        draw_wrapped(
            draw,
            bx,
            1902,
            f"{i+1}. {row['name']} gain={row['seam_gain_vs_db65_pct']:.1f}% chg={row['changed_fraction_vs_db65']:.3f}",
            38,
            size=11,
        )
    board.save(BOARD, quality=92)


def main() -> None:
    cleanup()

    hard = read_rgb(HARD)
    db64_visible = read_rgb(DB64_VISIBLE)
    db65 = read_rgb(DB65_BEST)
    db65_mask = read_u8(DB65_MASK) > 0
    transition = read_u8(TRANSITION)
    veto = read_u8(VETO) > 0

    valid = (hard.sum(axis=2) > 18) & (db65.sum(axis=2) > 18)
    yy = np.arange(db65.shape[0])[:, None]
    seam_y_band = (yy > 260) & (yy < 760)
    transition_seed = np.isin(transition, [2, 3, 5]) & seam_y_band & valid
    transition_seed &= morph(db65_mask, 7)
    seed = (db65_mask | transition_seed) & valid
    eval_mask = morph(db65_mask, 1) & valid
    allowed_envelope = morph(seed, 5) & valid

    edge_gate = edge_stop(db65, valid)
    edge_gate[veto] *= 0.55
    edge_gate = np.clip(edge_gate, 0.08, 1.0)

    bases = {"db65_best": db65}
    modes = ["x_smooth", "bilateral", "hybrid", "micro_median"]
    radii = [1, 2, 3]
    mask_radii = [0, 1, 2]
    strengths = [0.16, 0.28, 0.40]
    max_deltas = [3.0, 5.0]
    edge_floors = [0.12, 0.22]
    target_cache = {
        (base_name, mode, radius): target_image(base, mode, radius)
        for base_name, base in bases.items()
        for mode in modes
        for radius in radii
    }

    db65_energy = horizontal_edge_energy(db65, eval_mask)
    db65_roi_energy = horizontal_edge_energy(db65, eval_mask, DB25_ROI)
    db64_energy = horizontal_edge_energy(db64_visible, eval_mask)
    db64_roi_energy = horizontal_edge_energy(db64_visible, eval_mask, DB25_ROI)

    rows: list[dict[str, Any]] = []
    kept: list[tuple[dict[str, Any], np.ndarray, np.ndarray]] = []
    for base_name, base in bases.items():
        for mode in modes:
            for radius in radii:
                for mask_radius in mask_radii:
                    for strength in strengths:
                        for max_delta in max_deltas:
                            for edge_floor in edge_floors:
                                out, edit, _alpha = apply_variant(
                                    base=base,
                                    seed=seed,
                                    edge_gate=edge_gate,
                                    mode=mode,
                                    radius=radius,
                                    mask_radius=mask_radius,
                                    strength=strength,
                                    max_delta=max_delta,
                                    edge_floor=edge_floor,
                                    valid=valid,
                                    target=target_cache[(base_name, mode, radius)],
                                )
                                delta_vs_db65 = np.abs(out.astype(np.float32) - db65.astype(np.float32)).mean(axis=2)
                                changed = delta_vs_db65 > 0.25
                                outside_changed_fraction = float(np.mean(changed & ~allowed_envelope))
                                changed_fraction = float(np.mean(changed))
                                p95_abs_delta = float(np.percentile(delta_vs_db65, 95))
                                max_abs_delta = float(delta_vs_db65.max())
                                energy = horizontal_edge_energy(out, eval_mask)
                                roi_energy = horizontal_edge_energy(out, eval_mask, DB25_ROI)
                                seam_gain = 100.0 * (db65_energy - energy) / max(db65_energy, 1e-6)
                                roi_gain = 100.0 * (db65_roi_energy - roi_energy) / max(db65_roi_energy, 1e-6)
                                seam_gain_vs_db64 = 100.0 * (db64_energy - energy) / max(db64_energy, 1e-6)
                                roi_gain_vs_db64 = 100.0 * (db64_roi_energy - roi_energy) / max(db64_roi_energy, 1e-6)
                                penalty = 0.0
                                penalty += 420.0 * max(0.0, changed_fraction - 0.030)
                                penalty += 5000.0 * outside_changed_fraction
                                penalty += 2.5 * max(0.0, max_abs_delta - 8.0)
                                penalty += 1.2 * max(0.0, p95_abs_delta - 2.5)
                                if seam_gain < 0:
                                    penalty += 30.0 * abs(seam_gain)
                                if roi_gain < 0:
                                    penalty += 18.0 * abs(roi_gain)
                                score = 0.58 * seam_gain + 0.42 * roi_gain - penalty
                                name = (
                                    f"{base_name}_{mode}_r{radius}_m{mask_radius}_"
                                    f"s{strength:.2f}_d{int(max_delta)}_ef{edge_floor:.2f}"
                                )
                                row = {
                                    "name": name,
                                    "file": f"{name}.png",
                                    "base": base_name,
                                    "mode": mode,
                                    "radius": radius,
                                    "mask_radius": mask_radius,
                                    "strength": strength,
                                    "max_delta": max_delta,
                                    "edge_floor": edge_floor,
                                    "score": float(score),
                                    "db65_seam_energy": db65_energy,
                                    "candidate_seam_energy": energy,
                                    "db65_roi_energy": db65_roi_energy,
                                    "candidate_roi_energy": roi_energy,
                                    "seam_gain_vs_db65_pct": float(seam_gain),
                                    "roi_gain_vs_db65_pct": float(roi_gain),
                                    "seam_gain_vs_db64_pct": float(seam_gain_vs_db64),
                                    "roi_gain_vs_db64_pct": float(roi_gain_vs_db64),
                                    "changed_fraction_vs_db65": changed_fraction,
                                    "outside_allowed_changed_fraction": outside_changed_fraction,
                                    "p95_abs_delta_vs_db65": p95_abs_delta,
                                    "max_abs_delta_vs_db65": max_abs_delta,
                                    "edit_fraction": float(edit.mean()),
                                }
                                rows.append(row)
                                kept.append((row, out, edit))
                                kept.sort(key=lambda item: item[0]["score"], reverse=True)
                                if len(kept) > 36:
                                    kept.pop()

    rows.sort(key=lambda r: (r["score"], r["seam_gain_vs_db65_pct"], -r["changed_fraction_vs_db65"]), reverse=True)
    kept_by_name = {row["name"]: (img, edit) for row, img, edit in kept}
    for row in rows[:28]:
        img_edit = kept_by_name.get(row["name"])
        if img_edit is not None:
            save_rgb(OUT_DIR / row["file"], img_edit[0])

    best_row = rows[0]
    best, best_edit = kept_by_name[best_row["name"]]
    diff_db65 = diff_viz(db65, best)
    diff_db64 = diff_viz(db64_visible, best)
    save_rgb(BEST, best)
    Image.fromarray((best_edit.astype(np.uint8) * 255), mode="L").save(BEST_MASK)
    Image.fromarray(diff_db65, mode="RGB").save(BEST_DIFF_DB65)
    Image.fromarray(diff_db64, mode="RGB").save(BEST_DIFF_DB64)
    build_top_sheet(db65, rows)
    build_board(best_row, hard, db64_visible, db65, best, best_edit, diff_db65, diff_db64, rows)

    hard_checks = {
        "beats_db65_seam_metric": bool(best_row["seam_gain_vs_db65_pct"] > 0.0),
        "beats_db65_roi_metric": bool(best_row["roi_gain_vs_db65_pct"] > 0.0),
        "small_incremental_change": bool(best_row["changed_fraction_vs_db65"] <= 0.030),
        "outside_allowed_low": bool(best_row["outside_allowed_changed_fraction"] <= 0.0005),
        "max_delta_bounded": bool(best_row["max_abs_delta_vs_db65"] <= 8.0),
        "phase2_renderer_allowed": False,
        "source_faithful_repair_allowed": False,
    }
    auto_candidate_pass = all(hard_checks[k] for k in (
        "beats_db65_seam_metric",
        "beats_db65_roi_metric",
        "small_incremental_change",
        "outside_allowed_low",
        "max_delta_bounded",
    ))

    metrics = {
        "status": "db68_edge_aware_polish_complete",
        "claim_classification": "presentation/diagnostic photometric polish candidate; not source-faithful repair",
        "auto_candidate_pass_requires_vision": auto_candidate_pass,
        "db65_baseline": {
            "seam_energy": db65_energy,
            "roi_energy": db65_roi_energy,
        },
        "db64_reference": {
            "seam_energy": db64_energy,
            "roi_energy": db64_roi_energy,
        },
        "best": best_row,
        "hard_checks": hard_checks,
        "top": rows[:28],
        "all_count": len(rows),
    }
    METRICS.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db68_edge_aware_polish_complete",
        "claim_classification": "presentation/diagnostic photometric polish candidate; not source-faithful repair",
        "auto_candidate_pass_requires_vision": auto_candidate_pass,
        "scope": {
            "cpu_local_only": True,
            "existing_db64_db65_db67_artifacts_only": True,
            "remote_status_exec": False,
            "a100": False,
            "vggt_pi3_hf_model_inference": False,
            "dit_flux_3dgs": False,
            "inpaint_generation": False,
            "source_replacement": False,
            "geometry_warp": False,
            "db32_edit": False,
            "red_promotion": False,
        },
        "inputs": {
            "hard_select": rel(HARD),
            "db64_visible_rejected": rel(DB64_VISIBLE),
            "db65_current_best": rel(DB65_BEST),
            "db65_edit_mask": rel(DB65_MASK),
            "phase5a_transition_map": rel(TRANSITION),
            "phase5a_veto_proxy": rel(VETO),
            "db67_summary": rel(DB67_SUMMARY),
        },
        "outputs": {
            "best_candidate": rel(BEST),
            "incremental_edit_mask": rel(BEST_MASK),
            "diff_x6_vs_db65": rel(BEST_DIFF_DB65),
            "diff_x6_vs_db64": rel(BEST_DIFF_DB64),
            "board": rel(BOARD),
            "top_variant_roi_sheet": rel(TOP_SHEET),
            "metrics": rel(METRICS),
            "manifest": rel(MANIFEST),
        },
        "db67_context": read_db67_decision(),
        "best": best_row,
        "hard_checks": hard_checks,
        "decision": {
            "accepted_as_visible_result_if_vision_ok": auto_candidate_pass,
            "phase2_renderer_allowed": False,
            "source_faithful_repair_allowed": False,
            "reason": "DB68 is bounded local photometric polish after DB67 evidence failure; it does not create target-surface or raw-visibility evidence.",
        },
        "mask_stats": {
            "db65_mask_fraction": float(db65_mask.mean()),
            "seed_fraction": float(seed.mean()),
            "eval_mask_fraction": float(eval_mask.mean()),
            "allowed_envelope_fraction": float(allowed_envelope.mean()),
            "best_incremental_edit_fraction": float(best_edit.mean()),
            "veto_fraction": float(veto.mean()),
        },
    }
    text = json.dumps({"manifest": manifest, "metrics": metrics}, ensure_ascii=False)
    hits = secret_hits(text)
    manifest["strict_secret_scan"] = {"hit_count": sum(int(h["count"]) for h in hits), "hits": hits}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": metrics["status"],
                "auto_candidate_pass_requires_vision": auto_candidate_pass,
                "best": best_row["name"],
                "score": best_row["score"],
                "seam_gain_vs_db65_pct": best_row["seam_gain_vs_db65_pct"],
                "roi_gain_vs_db65_pct": best_row["roi_gain_vs_db65_pct"],
                "changed_fraction_vs_db65": best_row["changed_fraction_vs_db65"],
                "max_abs_delta_vs_db65": best_row["max_abs_delta_vs_db65"],
                "board": rel(BOARD),
                "manifest": rel(MANIFEST),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
