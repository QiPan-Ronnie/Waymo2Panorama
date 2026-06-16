from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DB64 = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0"
PHASE2 = DB64 / "phase2_lidar_zbuffer_fetch" / "02a00399_a000_bmw"
PHASE5A = DB64 / "phase5a_continuous_surface"
FETCH5A = PHASE5A / "fetch" / "02a00399_a000_bmw"
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db65_visible_photometric_fallback"

RUN = "02a00399_a000_bmw"
HARD = PHASE2 / f"{RUN}_hard_select.jpg"
LIDAR_BEST = PHASE2 / f"{RUN}_lidar_best.jpg"
DB64_VISIBLE = PHASE5A / "db64_phase5a_current_best_visible_candidate_rejected.png"
TRANSITION = FETCH5A / f"{RUN}_before_after_transition_map.png"
VETO = FETCH5A / f"{RUN}_protected_veto_proxy_map.png"
CURRENT_CAUSE = FETCH5A / f"{RUN}_current_z_cause_primary_map.png"
FUSED_CAUSE = FETCH5A / f"{RUN}_fused_z_cause_primary_map.png"
PHASE5A_REVIEW = FETCH5A / f"{RUN}_phase5a_crop_review.jpg"
PHASE5A_BOARD = PHASE5A / "db64_phase5a_continuous_surface_board.jpg"

BEST = OUT_DIR / "db65_best_visible_photometric_candidate.png"
BEST_MASK = OUT_DIR / "db65_best_edit_mask.png"
BEST_DIFF = OUT_DIR / "db65_best_diff_x6.png"
BOARD = OUT_DIR / "db65_visible_photometric_fallback_board.jpg"
TOP_SHEET = OUT_DIR / "db65_top_variant_roi_sheet.jpg"
METRICS = OUT_DIR / "db65_visible_photometric_fallback_metrics.json"
MANIFEST = OUT_DIR / "db65_visible_photometric_fallback_manifest.json"

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
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)


def read_u8(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path), dtype=np.uint8)


def save_rgb(path: Path, arr: np.ndarray) -> None:
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB").save(path)


def cleanup_old_variant_files() -> None:
    for pattern in ("hard_select_*.png", "lidar_best_*.png", "db64_visible_*.png"):
        for path in OUT_DIR.glob(pattern):
            path.unlink()


def rgb_to_y(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask.astype(bool)
    m = mask.astype(bool)
    out = m.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius:
                continue
            src_y0 = max(0, -dy)
            src_y1 = m.shape[0] - max(0, dy)
            src_x0 = max(0, -dx)
            src_x1 = m.shape[1] - max(0, dx)
            dst_y0 = max(0, dy)
            dst_y1 = m.shape[0] - max(0, -dy)
            dst_x0 = max(0, dx)
            dst_x1 = m.shape[1] - max(0, -dx)
            out[dst_y0:dst_y1, dst_x0:dst_x1] |= m[src_y0:src_y1, src_x0:src_x1]
    return out


def blur_array(arr: np.ndarray, radius: float) -> np.ndarray:
    if arr.ndim == 2:
        im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="L")
        return np.asarray(im.filter(ImageFilter.GaussianBlur(radius=radius)), dtype=np.float32)
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius=radius)), dtype=np.float32)


def soft_alpha(mask: np.ndarray, radius: int, strength: float, boundary: np.ndarray) -> np.ndarray:
    core = mask.astype(np.float32) * 255.0
    alpha = blur_array(core, max(1.0, radius / 2.0)) / 255.0
    alpha = np.clip(alpha, 0.0, 1.0) * strength
    alpha *= np.where(boundary, 0.60, 1.0).astype(np.float32)
    return alpha.astype(np.float32)


def horizontal_edge_energy(rgb: np.ndarray, seam_mask: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> float:
    y = rgb_to_y(rgb)
    edge = np.abs(y[:, 1:] - y[:, :-1])
    mask = seam_mask[:, 1:] | seam_mask[:, :-1]
    valid = mask
    if roi is not None:
        x0, y0, x1, y1 = roi
        roi_mask = np.zeros_like(mask, dtype=bool)
        roi_mask[y0:y1, max(0, x0 - 1) : max(0, x1 - 1)] = True
        valid &= roi_mask
    if not np.any(valid):
        return 0.0
    return float(edge[valid].mean())


def apply_variant(
    base: np.ndarray,
    seam_mask: np.ndarray,
    boundary: np.ndarray,
    mode: str,
    radius: int,
    strength: float,
    max_delta: float,
) -> tuple[np.ndarray, np.ndarray]:
    alpha = soft_alpha(seam_mask, radius, strength, boundary)
    if mode == "y_blur":
        y = rgb_to_y(base)
        target = blur_array(y, radius)
        delta_y = np.clip((target - y) * alpha, -max_delta, max_delta)
        denom = np.maximum(y[..., None], 1.0)
        out = base * ((y + delta_y)[..., None] / denom)
        return np.clip(out, 0, 255), np.abs(delta_y) > 0.25

    if mode == "rgb_blur":
        target = blur_array(base, radius)
        delta = np.clip((target - base) * alpha[..., None], -max_delta, max_delta)
        return np.clip(base + delta, 0, 255), np.max(np.abs(delta), axis=2) > 0.25

    if mode == "warm_wall":
        # Very small low-frequency chroma/Y correction, useful only as a presentation fallback.
        target = blur_array(base, radius)
        delta = np.clip((target - base) * alpha[..., None], -max_delta, max_delta)
        delta[..., 0] += alpha * min(3.0, max_delta * 0.25)
        delta[..., 2] -= alpha * min(2.0, max_delta * 0.18)
        delta = np.clip(delta, -max_delta, max_delta)
        return np.clip(base + delta, 0, 255), np.max(np.abs(delta), axis=2) > 0.25

    raise ValueError(mode)


def mask_to_rgb(mask: np.ndarray, color: tuple[int, int, int] = (0, 210, 255)) -> np.ndarray:
    out = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)
    out[mask] = color
    return out


def diff_viz(before: np.ndarray, after: np.ndarray, scale: float = 6.0) -> np.ndarray:
    d = np.abs(after - before).mean(axis=2) * scale
    out = np.zeros((*d.shape, 3), dtype=np.float32)
    out[..., 0] = np.clip(d, 0, 255)
    out[..., 1] = np.clip(d * 0.65, 0, 200)
    return out.astype(np.uint8)


def crop(arr: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return arr[y0:y1, x0:x1]


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
    if not path.exists():
        draw = ImageDraw.Draw(board)
        draw.rectangle(box, fill=(35, 35, 42), outline=(110, 110, 110))
        draw_wrapped(draw, box[0] + 12, box[1] + 12, f"missing: {rel(path)}", 42, fill=(255, 150, 120))
        return
    arr = np.asarray(Image.open(path).convert("RGB"))
    paste_arr(board, arr, box)


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pat in TOKEN_PATTERNS.items():
        found = pat.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


def build_board(best_row: dict[str, Any], hard: np.ndarray, db64_visible: np.ndarray, best: np.ndarray, mask: np.ndarray, diff: np.ndarray, rows: list[dict[str, Any]]) -> None:
    board = Image.new("RGB", (2100, 1780), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 22), "DB65 evidence-gated visible photometric fallback", size=28)
    y = 64
    for line in [
        "CPU/local only. Existing DB64 artifacts only. Photometric seam-band polish; no geometry, no generated pixels, no source replacement.",
        f"best={best_row['name']} mode={best_row['mode']} radius={best_row['radius']} strength={best_row['strength']} max_delta={best_row['max_delta']}",
        f"classification=presentation/diagnostic photometric polish, not source-faithful repair",
        f"score={best_row['score']:.3f} seam_reduction={best_row['seam_reduction_pct']:.2f}% roi_reduction={best_row['roi_reduction_pct']:.2f}% changed={best_row['changed_fraction']:.4f} p95_delta={best_row['p95_abs_delta']:.2f} max_delta={best_row['max_abs_delta']:.2f}",
    ]:
        y = draw_wrapped(draw, 34, y, "- " + line, 158, size=14)

    slots = [
        ("HardSelect control", hard, (28, 245, 650, 555)),
        ("DB64 current visible rejected diagnostic", db64_visible, (690, 245, 1312, 555)),
        ("DB65 best visible candidate", best, (1352, 245, 2070, 555)),
        ("DB25 ROI before", crop(db64_visible, DB25_ROI), (28, 630, 650, 900)),
        ("DB25 ROI after", crop(best, DB25_ROI), (690, 630, 1312, 900)),
        ("DB25 diff x6", crop(diff, DB25_ROI), (1352, 630, 2070, 900)),
        ("Edit mask", mask_to_rgb(mask), (28, 970, 650, 1260)),
    ]
    for label, arr, box in slots:
        draw_text(draw, (box[0], box[1] - 28), label, size=18)
        paste_arr(board, arr, box)

    draw_text(draw, (690, 942), "Phase5a BMW review", size=18)
    paste_file(board, PHASE5A_REVIEW, (690, 970, 1312, 1260))
    draw_text(draw, (1352, 942), "Phase5a aggregate board", size=18)
    paste_file(board, PHASE5A_BOARD, (1352, 970, 2070, 1260))

    top = rows[:6]
    x0, y0 = 28, 1325
    draw_text(draw, (x0, y0 - 30), "Top candidate crops", size=18)
    for i, row in enumerate(top):
        img = np.asarray(Image.open(OUT_DIR / row["file"]).convert("RGB"), dtype=np.uint8)
        c = crop(img, DB25_ROI)
        bx = x0 + i * 335
        paste_arr(board, c, (bx, y0, bx + 310, y0 + 185))
        draw_wrapped(
            draw,
            bx,
            y0 + 192,
            f"{i+1}. {row['name']} score={row['score']:.1f} red={row['seam_reduction_pct']:.1f}% chg={row['changed_fraction']:.3f}",
            36,
            size=12,
        )

    board.save(BOARD, quality=92)


def build_top_sheet(db64_visible: np.ndarray, rows: list[dict[str, Any]]) -> None:
    items: list[tuple[str, np.ndarray]] = [("before", crop(db64_visible, DB25_ROI))]
    for row in rows[:8]:
        img = np.asarray(Image.open(OUT_DIR / row["file"]).convert("RGB"), dtype=np.uint8)
        label = f"{row['name']} score={row['score']:.1f} chg={row['changed_fraction']:.3f}"
        items.append((label, crop(img, DB25_ROI)))
    board = Image.new("RGB", (1360, 1500), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    for i, (label, arr) in enumerate(items):
        x = (i % 2) * 680 + 20
        y = (i // 2) * 300 + 20
        draw_text(draw, (x, y), label, size=15)
        im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB").resize((640, 240))
        board.paste(im, (x, y + 30))
        draw.rectangle((x, y + 30, x + 640, y + 270), outline=(190, 190, 190))
    board.save(TOP_SHEET, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_variant_files()
    hard = read_rgb(HARD)
    lidar = read_rgb(LIDAR_BEST)
    db64_visible = read_rgb(DB64_VISIBLE if DB64_VISIBLE.exists() else LIDAR_BEST)
    transition = read_u8(TRANSITION)
    veto = read_u8(VETO) > 0
    cur_cause = read_u8(CURRENT_CAUSE)
    fused_cause = read_u8(FUSED_CAUSE)

    valid = (hard.sum(axis=2) > 18) & (db64_visible.sum(axis=2) > 18)
    # Use only explicit Phase5a seam-transition / source-boundary columns as the edit seed.
    # Broad cause-map differences are diagnostic evidence, not an edit permission.
    seed = (np.isin(transition, [2, 3, 5]) | veto) & valid
    seed &= np.arange(seed.shape[0])[:, None] > 260
    seed &= np.arange(seed.shape[0])[:, None] < 760
    seed = dilate(seed, 2)

    bases = {
        "hard_select": hard,
        "db64_visible": db64_visible,
        "lidar_best": lidar,
    }
    modes = ["y_blur", "rgb_blur", "warm_wall"]
    radii = [3, 5, 8, 12]
    strengths = [0.18, 0.28, 0.38]
    max_deltas = [5.0, 8.0, 10.0]

    rows: list[dict[str, Any]] = []
    kept: list[tuple[dict[str, Any], np.ndarray]] = []
    before_energy = horizontal_edge_energy(db64_visible, seed)
    before_roi_energy = horizontal_edge_energy(db64_visible, seed, DB25_ROI)
    for base_name, base in bases.items():
        for mode in modes:
            for radius in radii:
                seam_mask = dilate(seed, radius)
                seam_mask &= valid
                for strength in strengths:
                    for max_delta in max_deltas:
                        out, edit_mask = apply_variant(base, seam_mask, veto, mode, radius, strength, max_delta)
                        delta = np.abs(out - base).mean(axis=2)
                        changed_fraction = float(np.mean(delta > 0.25))
                        p95_abs_delta = float(np.percentile(delta, 95))
                        max_abs_delta = float(delta.max())
                        energy = horizontal_edge_energy(out, seed)
                        roi_energy = horizontal_edge_energy(out, seed, DB25_ROI)
                        seam_reduction_pct = 100.0 * (before_energy - energy) / max(before_energy, 1e-6)
                        roi_reduction_pct = 100.0 * (before_roi_energy - roi_energy) / max(before_roi_energy, 1e-6)
                        penalty = 260.0 * max(0.0, changed_fraction - 0.055) + 2.0 * max(0.0, p95_abs_delta - 5.5)
                        if changed_fraction > 0.065 or p95_abs_delta > 8.0:
                            penalty += 100.0
                        score = seam_reduction_pct * 0.70 + roi_reduction_pct * 0.30 - penalty
                        name = f"{base_name}_{mode}_r{radius}_s{strength:.2f}_d{int(max_delta)}"
                        file_name = f"{name}.png"
                        row = {
                            "name": name,
                            "file": file_name,
                            "base": base_name,
                            "mode": mode,
                            "radius": radius,
                            "strength": strength,
                            "max_delta": max_delta,
                            "score": float(score),
                            "seam_energy_before": before_energy,
                            "seam_energy_after": energy,
                            "roi_energy_before": before_roi_energy,
                            "roi_energy_after": roi_energy,
                            "seam_reduction_pct": float(seam_reduction_pct),
                            "roi_reduction_pct": float(roi_reduction_pct),
                            "changed_fraction": changed_fraction,
                            "p95_abs_delta": p95_abs_delta,
                            "max_abs_delta": max_abs_delta,
                        }
                        rows.append(row)
                        kept.append((row, np.clip(out, 0, 255).astype(np.uint8)))
                        kept.sort(key=lambda item: item[0]["score"], reverse=True)
                        if len(kept) > 24:
                            kept.pop()

    rows.sort(key=lambda r: (r["score"], -r["changed_fraction"]), reverse=True)
    kept_by_name = {row["name"]: img for row, img in kept}
    for row in rows[:20]:
        img = kept_by_name.get(row["name"])
        if img is not None:
            save_rgb(OUT_DIR / row["file"], img)
    best_row = rows[0]
    best = read_rgb(OUT_DIR / best_row["file"])
    _, best_edit = apply_variant(
        bases[best_row["base"]],
        dilate(seed, int(best_row["radius"])) & valid,
        veto,
        str(best_row["mode"]),
        int(best_row["radius"]),
        float(best_row["strength"]),
        float(best_row["max_delta"]),
    )
    diff = diff_viz(db64_visible, best)
    save_rgb(BEST, best)
    Image.fromarray((best_edit.astype(np.uint8) * 255), mode="L").save(BEST_MASK)
    Image.fromarray(diff, mode="RGB").save(BEST_DIFF)
    build_board(best_row, hard, db64_visible, best, best_edit, diff, rows)
    build_top_sheet(db64_visible, rows)

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db65_visible_photometric_fallback_complete",
        "claim_classification": "presentation/diagnostic photometric polish; not source-faithful repair",
        "scope": {
            "cpu_local_only": True,
            "existing_db64_artifacts_only": True,
            "remote_status_exec": False,
            "a100": False,
            "vggt_hf_model": False,
            "generation": False,
            "source_replacement": False,
            "geometry_warp": False,
            "db32_edit": False,
            "red_promotion": False,
        },
        "inputs": {
            "hard_select": rel(HARD),
            "db64_visible": rel(DB64_VISIBLE if DB64_VISIBLE.exists() else LIDAR_BEST),
            "transition_map": rel(TRANSITION),
            "veto_proxy": rel(VETO),
            "current_z_cause": rel(CURRENT_CAUSE),
            "fused_z_cause": rel(FUSED_CAUSE),
        },
        "outputs": {
            "best_candidate": rel(BEST),
            "best_edit_mask": rel(BEST_MASK),
            "best_diff_x6": rel(BEST_DIFF),
            "board": rel(BOARD),
            "top_variant_roi_sheet": rel(TOP_SHEET),
            "metrics": rel(METRICS),
            "manifest": rel(MANIFEST),
        },
        "best": best_row,
        "decision": {
            "phase5b_allowed": False,
            "source_faithful_repair_allowed": False,
            "accepted_as_visible_result_if_vision_ok": True,
            "reason": "This is a bounded photometric fallback after DB64 Phase5a evidence failure. It does not fix target-surface ownership.",
        },
        "mask_stats": {
            "seed_fraction": float(seed.mean()),
            "veto_fraction": float(veto.mean()),
            "best_edit_fraction": float(best_edit.mean()),
        },
    }
    text = json.dumps(manifest, ensure_ascii=False)
    hits = secret_hits(text)
    manifest["strict_secret_scan"] = {"hit_count": sum(int(h["count"]) for h in hits), "hits": hits}
    METRICS.write_text(json.dumps({"top": rows[:20], "all_count": len(rows)}, indent=2), encoding="utf-8")
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"best": rel(BEST), "board": rel(BOARD), "manifest": rel(MANIFEST)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
