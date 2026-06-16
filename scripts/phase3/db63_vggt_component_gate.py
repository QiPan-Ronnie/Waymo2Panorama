from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DB62_DIR = ROOT / "deliverables" / "dit360_v2" / "db62_vggt_raw_source_composite"
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db63_vggt_component_gate"
A1_PANO = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment" / "A1_view_none_bmw_1024x2048.png"
G_PANO = ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg"
ROI = (850, 420, 1650, 720)


def load_rgb(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def load_gray01(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32) / 255.0


def save_rgb(path: Path, arr: np.ndarray) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    img.save(path)
    return png_stats(path)


def save_gray(path: Path, arr: np.ndarray) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8), "L")
    img.save(path)
    return png_stats(path)


def png_stats(path: Path) -> dict[str, Any]:
    img = Image.open(path)
    return {
        "path": str(path.relative_to(ROOT)),
        "exists": path.exists(),
        "bytes": path.stat().st_size,
        "size": list(img.size),
    }


def connected_components(mask: np.ndarray) -> list[dict[str, Any]]:
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    comps: list[dict[str, Any]] = []
    for y in range(h):
        xs = np.flatnonzero(mask[y] & ~seen[y])
        for x0 in xs:
            if seen[y, x0] or not mask[y, x0]:
                continue
            q: deque[tuple[int, int]] = deque([(y, int(x0))])
            seen[y, x0] = True
            pixels: list[tuple[int, int]] = []
            while q:
                cy, cx = q.popleft()
                pixels.append((cy, cx))
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            ys = np.array([p[0] for p in pixels], dtype=np.int32)
            xs2 = np.array([p[1] for p in pixels], dtype=np.int32)
            comps.append(
                {
                    "area": int(len(pixels)),
                    "bbox_xyxy": [int(xs2.min()), int(ys.min()), int(xs2.max() + 1), int(ys.max() + 1)],
                    "pixels": pixels,
                }
            )
    comps.sort(key=lambda c: int(c["area"]), reverse=True)
    return comps


def component_stats(mask: np.ndarray, alpha: np.ndarray, label_rgb: np.ndarray, min_area: int = 20) -> list[dict[str, Any]]:
    comps = connected_components(mask)
    rows: list[dict[str, Any]] = []
    for idx, comp in enumerate(comps):
        if comp["area"] < min_area:
            continue
        pix = comp["pixels"]
        ys = np.array([p[0] for p in pix], dtype=np.int32)
        xs = np.array([p[1] for p in pix], dtype=np.int32)
        labels = label_rgb[ys, xs]
        unique, counts = np.unique(labels.reshape(-1, 3), axis=0, return_counts=True)
        dominant_idx = int(np.argmax(counts))
        bbox = comp["bbox_xyxy"]
        bw = max(1, bbox[2] - bbox[0])
        bh = max(1, bbox[3] - bbox[1])
        rows.append(
            {
                "rank": len(rows) + 1,
                "raw_component_index": idx,
                "area": int(comp["area"]),
                "area_frac_roi": round(float(comp["area"] / mask.size), 6),
                "bbox_xyxy_roi": bbox,
                "bbox_wh": [int(bw), int(bh)],
                "fill_frac_bbox": round(float(comp["area"] / (bw * bh)), 6),
                "alpha_mean": round(float(alpha[ys, xs].mean()), 6),
                "alpha_max": round(float(alpha[ys, xs].max()), 6),
                "dominant_label_rgb": [int(v) for v in unique[dominant_idx].tolist()],
                "dominant_label_ratio": round(float(counts[dominant_idx] / counts.sum()), 6),
            }
        )
    return rows


def mask_from_components(mask: np.ndarray, alpha: np.ndarray, label_rgb: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
    stats = component_stats(mask, alpha, label_rgb, min_area=30)
    keep = np.zeros_like(mask, dtype=bool)
    comps = connected_components(mask)
    for row in stats:
        good_area = row["area"] >= 180
        good_label = row["dominant_label_ratio"] >= 0.86
        not_tiny_speckle = row["fill_frac_bbox"] >= 0.04
        if good_area and good_label and not_tiny_speckle:
            comp = comps[row["raw_component_index"]]
            for y, x in comp["pixels"]:
                keep[y, x] = True
    return keep, stats


def feather(mask: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    return np.asarray(img.filter(ImageFilter.GaussianBlur(radius=radius)), dtype=np.float32) / 255.0


def compose_candidate(base_path: Path, hard_crop: Image.Image, alpha: np.ndarray, out_path: Path) -> dict[str, Any]:
    base = load_rgb(base_path)
    crop = base.crop(ROI).convert("RGB")
    b = np.asarray(crop, dtype=np.float32)
    h = np.asarray(hard_crop, dtype=np.float32)
    a = np.clip(alpha, 0.0, 0.90)[..., None]
    out_crop = b * (1.0 - a) + h * a
    out = base.copy()
    out.paste(Image.fromarray(np.clip(out_crop, 0, 255).astype(np.uint8), "RGB"), (ROI[0], ROI[1]))
    out.save(out_path)
    diff = np.abs(out_crop - b)
    return {
        **png_stats(out_path),
        "metrics": {
            "roi_alpha_mean": round(float(alpha.mean()), 6),
            "roi_alpha_max": round(float(alpha.max()), 6),
            "roi_changed_frac_alpha_gt_0_05": round(float((alpha > 0.05).mean()), 6),
            "roi_changed_frac_alpha_gt_0_20": round(float((alpha > 0.20).mean()), 6),
            "roi_mean_abs_delta": round(float(diff.mean()), 6),
            "roi_p95_abs_delta": round(float(np.percentile(diff, 95)), 6),
            "roi_max_abs_delta": round(float(diff.max()), 6),
        },
    }


def diff_x5(a: Image.Image, b: Image.Image) -> Image.Image:
    aa = np.asarray(a.convert("RGB"), dtype=np.float32)
    bb = np.asarray(b.convert("RGB"), dtype=np.float32)
    return Image.fromarray(np.clip(np.abs(aa - bb) * 5.0, 0, 255).astype(np.uint8), "RGB")


def heat01(arr: np.ndarray) -> Image.Image:
    a = np.clip(arr, 0.0, 1.0)
    r = np.clip((a - 0.45) / 0.55, 0, 1)
    g = np.clip(1.0 - np.abs(a - 0.5) / 0.5, 0, 1)
    b = np.clip((0.55 - a) / 0.55, 0, 1)
    rgb = np.stack([r, g, b], axis=-1) * 255.0
    return Image.fromarray(rgb.astype(np.uint8), "RGB")


def overlay_mask(img: Image.Image, mask: np.ndarray, color: tuple[int, int, int] = (255, 210, 45)) -> Image.Image:
    base = np.asarray(img.convert("RGB"), dtype=np.float32)
    m = np.clip(mask, 0.0, 1.0)[..., None]
    col = np.array(color, dtype=np.float32).reshape(1, 1, 3)
    out = base * (1.0 - 0.55 * m) + col * (0.55 * m)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf"):
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    img = img.convert("RGB").copy()
    img.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (10, 12, 16))
    canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    return canvas


def panel(board: Image.Image, img: Image.Image, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.text((x0, y0 - 24), label, fill=(236, 239, 245), font=font(15))
    board.paste(fit(img, (x1 - x0, y1 - y0)), (x0, y0))
    draw.rectangle(box, outline=(78, 86, 99), width=2)


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2400, 1720), (15, 17, 22))
    draw = ImageDraw.Draw(board)
    draw.text((40, 28), "DB63 VGGT component-gated raw-source probe", fill=(245, 247, 252), font=font(28))
    draw.text(
        (40, 68),
        "Existing DB62 VGGT only. Tests whether high-confidence source-switch islands form a repairable sub-region.",
        fill=(230, 200, 135),
        font=font(16),
    )

    stats = manifest["component_summary"]
    text_lines = [
        f"db62_alpha_gt_005_frac={stats['alpha_gt_005_frac']} selected_component_frac={stats['selected_component_frac']}",
        f"selected_components={stats['selected_component_count']} selected_alpha_mean={stats['selected_alpha_mean']}",
        f"verdict={manifest['verdict']} secret_hits={len(manifest['token_scan_hits'])}",
    ]
    y = 104
    for line in text_lines:
        draw.text((50, y), line, fill=(210, 216, 226), font=font(14))
        y += 22

    a1 = load_rgb(A1_PANO)
    g = load_rgb(G_PANO)
    a1_db62 = load_rgb(DB62_DIR / "db62_a1_vggt_raw_source_composite.png")
    g_db62 = load_rgb(DB62_DIR / "db62_g_vggt_raw_source_composite.png")
    a1_keep = load_rgb(OUT_DIR / "db63_a1_component_keep.png")
    g_keep = load_rgb(OUT_DIR / "db63_g_component_keep.png")
    a1_amp = load_rgb(OUT_DIR / "db63_a1_component_amplified.png")
    g_amp = load_rgb(OUT_DIR / "db63_g_component_amplified.png")

    a1_roi = a1.crop(ROI)
    g_roi = g.crop(ROI)
    p_y = 210
    w, h, gap = 360, 170, 22
    panels = [
        (a1_roi, "A1 original ROI"),
        (a1_db62.crop(ROI), "A1 DB62 soft"),
        (a1_keep.crop(ROI), "A1 DB63 keep"),
        (a1_amp.crop(ROI), "A1 DB63 amplified"),
        (diff_x5(a1_roi, a1_amp.crop(ROI)), "A1 amp diff x5"),
        (g_roi, "G original ROI"),
        (g_db62.crop(ROI), "G DB62 soft"),
        (g_keep.crop(ROI), "G DB63 keep"),
        (g_amp.crop(ROI), "G DB63 amplified"),
        (diff_x5(g_roi, g_amp.crop(ROI)), "G amp diff x5"),
    ]
    for i, (img, label) in enumerate(panels):
        row = i // 5
        col = i % 5
        x0 = 40 + col * (w + gap)
        y0 = p_y + row * (h + 68)
        panel(board, img, (x0, y0, x0 + w, y0 + h), label)

    y2 = 730
    panel(board, load_rgb(DB62_DIR / "db62_vggt_source_select_hard_crop.png"), (40, y2, 600, y2 + 245), "DB62 hard raw source crop")
    panel(board, load_rgb(DB62_DIR / "db62_vggt_source_composite_crop.png"), (620, y2, 1180, y2 + 245), "DB62 soft source crop")
    panel(board, load_rgb(OUT_DIR / "db63_component_mask_overlay.png"), (1200, y2, 1760, y2 + 245), "DB63 selected component overlay")
    panel(board, load_rgb(OUT_DIR / "db63_component_alpha_heat.png"), (1780, y2, 2340, y2 + 245), "DB63 component alpha heat")

    y3 = 1060
    panel(board, a1_keep.resize((512, 256)), (40, y3, 600, y3 + 245), "A1 keep full")
    panel(board, a1_amp.resize((512, 256)), (620, y3, 1180, y3 + 245), "A1 amplified full")
    panel(board, g_keep.resize((512, 256)), (1200, y3, 1760, y3 + 245), "G keep full")
    panel(board, g_amp.resize((512, 256)), (1780, y3, 2340, y3 + 245), "G amplified full")

    x_text, y_text = 40, 1360
    draw.text((x_text, y_text), "Top components after alpha>0.05 component analysis", fill=(245, 247, 252), font=font(20))
    y_text += 34
    for row in manifest["component_stats_alpha_gt_005"][:10]:
        line = (
            f"#{row['rank']} area={row['area']} frac={row['area_frac_roi']} bbox={row['bbox_xyxy_roi']} "
            f"alpha_mean={row['alpha_mean']} max={row['alpha_max']} label_ratio={row['dominant_label_ratio']}"
        )
        draw.text((x_text, y_text), line, fill=(210, 216, 226), font=font(14))
        y_text += 22

    claim = [
        "Claim boundary:",
        "source_faithful=False accepted_repair=False presentation_only=True",
        "raw_camera_backed_diagnostic=True source_id_map=False red_promotion=False",
        "DB41/right-line/lower-right not edited; no A100/model rerun; no generation.",
    ]
    y_text += 16
    for line in claim:
        draw.text((x_text, y_text), line, fill=(230, 200, 135), font=font(15))
        y_text += 24

    out = OUT_DIR / "db63_vggt_component_gate_board.jpg"
    board.save(out, quality=94)


def token_hits(obj: Any) -> list[str]:
    text = json.dumps(obj, sort_keys=True)
    patterns = ("hf_", "trycloudflare.com", "Bearer ", '"token":')
    return [p for p in patterns if p in text]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    alpha = load_gray01(DB62_DIR / "db62_vggt_source_alpha.png")
    label_rgb = np.asarray(load_rgb(DB62_DIR / "db62_vggt_source_label.png"), dtype=np.uint8)
    hard_crop = load_rgb(DB62_DIR / "db62_vggt_source_select_hard_crop.png")
    owner_crop = load_rgb(DB62_DIR / "db62_raw_owner_crop.png")

    mask005 = alpha > 0.05
    mask015 = alpha > 0.15
    mask030 = alpha > 0.30
    selected, stats005 = mask_from_components(mask005, alpha, label_rgb)
    stats015 = component_stats(mask015, alpha, label_rgb, min_area=20)
    stats030 = component_stats(mask030, alpha, label_rgb, min_area=10)

    selected_soft = feather(selected, 3.0)
    keep_alpha = np.clip(alpha * selected_soft, 0.0, 0.85)
    amp_alpha = np.clip(alpha * selected_soft * 2.2, 0.0, 0.85)

    save_gray(OUT_DIR / "db63_component_keep_alpha.png", keep_alpha)
    save_gray(OUT_DIR / "db63_component_amplified_alpha.png", amp_alpha)
    save_rgb(OUT_DIR / "db63_component_alpha_heat.png", np.asarray(heat01(amp_alpha), dtype=np.uint8))
    save_rgb(OUT_DIR / "db63_component_mask_overlay.png", np.asarray(overlay_mask(owner_crop, selected_soft), dtype=np.uint8))

    local_outputs = {
        "a1_component_keep": compose_candidate(A1_PANO, hard_crop, keep_alpha, OUT_DIR / "db63_a1_component_keep.png"),
        "g_component_keep": compose_candidate(G_PANO, hard_crop, keep_alpha, OUT_DIR / "db63_g_component_keep.png"),
        "a1_component_amplified": compose_candidate(A1_PANO, hard_crop, amp_alpha, OUT_DIR / "db63_a1_component_amplified.png"),
        "g_component_amplified": compose_candidate(G_PANO, hard_crop, amp_alpha, OUT_DIR / "db63_g_component_amplified.png"),
    }

    selected_count = int(len([s for s in stats005 if s["area"] >= 180 and s["dominant_label_ratio"] >= 0.86 and s["fill_frac_bbox"] >= 0.04]))
    selected_frac = float(selected.mean())
    preliminary_no_repair = selected_frac < 0.08 or float(amp_alpha.mean()) < 0.02
    verdict = "fragmented_sparse_no_repair" if preliminary_no_repair else "component_candidate_requires_vision_check"
    manifest: dict[str, Any] = {
        "db": "DB-63",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "target": {
            "uuid": "02a00399-3857-444e-8db3-a8f58489c394",
            "anchor": 0,
            "roi_key": "db25_longline",
            "roi_xyxy": list(ROI),
        },
        "inputs": {
            "db62_dir": str(DB62_DIR.relative_to(ROOT)),
            "alpha": str((DB62_DIR / "db62_vggt_source_alpha.png").relative_to(ROOT)),
            "label": str((DB62_DIR / "db62_vggt_source_label.png").relative_to(ROOT)),
            "hard_source_crop": str((DB62_DIR / "db62_vggt_source_select_hard_crop.png").relative_to(ROOT)),
        },
        "scope": {
            "cpu_local_only": True,
            "uses_existing_db62_vggt_outputs": True,
            "a100_job_submitted": False,
            "new_vggt_inference": False,
            "db25_only": True,
            "db41_edited": False,
            "dit_flux_prompt_generation": False,
            "inpainting": False,
            "source_id_map_created": False,
            "red_promotion": False,
        },
        "operator": {
            "name": "vggt_alpha_source_label_connected_component_gate",
            "alpha_thresholds": [0.05, 0.15, 0.30],
            "keep_rules": {
                "area_min_px": 180,
                "dominant_label_ratio_min": 0.86,
                "fill_frac_bbox_min": 0.04,
                "alpha_amplified_scale": 2.2,
                "alpha_cap": 0.85,
            },
            "source_backing": "candidate pixels are blended from DB62 raw-camera hard source crop; VGGT evidence only gates components",
        },
        "component_summary": {
            "alpha_gt_005_frac": round(float(mask005.mean()), 6),
            "alpha_gt_015_frac": round(float(mask015.mean()), 6),
            "alpha_gt_030_frac": round(float(mask030.mean()), 6),
            "selected_component_count": selected_count,
            "selected_component_frac": round(selected_frac, 6),
            "keep_alpha_mean": round(float(keep_alpha.mean()), 6),
            "amplified_alpha_mean": round(float(amp_alpha.mean()), 6),
            "selected_alpha_mean": round(float(alpha[selected].mean()), 6) if selected.any() else 0.0,
        },
        "component_stats_alpha_gt_005": stats005[:30],
        "component_stats_alpha_gt_015": stats015[:30],
        "component_stats_alpha_gt_030": stats030[:30],
        "outputs": {
            "output_dir": str(OUT_DIR.relative_to(ROOT)),
            "manifest": str((OUT_DIR / "db63_vggt_component_gate_manifest.json").relative_to(ROOT)),
            "board": str((OUT_DIR / "db63_vggt_component_gate_board.jpg").relative_to(ROOT)),
            "component_mask_overlay": str((OUT_DIR / "db63_component_mask_overlay.png").relative_to(ROOT)),
            "component_alpha_heat": str((OUT_DIR / "db63_component_alpha_heat.png").relative_to(ROOT)),
        },
        "local_outputs": local_outputs,
        "claim_boundaries": {
            "raw_camera_backed_diagnostic": True,
            "presentation_only": True,
            "source_faithful": False,
            "accepted_repair": False,
            "a1_g_repaired": False,
            "source_id_map": False,
            "red_promotion": False,
            "bosch_training_ready": False,
        },
        "verdict": verdict,
    }
    manifest["token_scan_hits"] = token_hits(manifest)
    manifest["hard_checks_passed"] = (
        len(manifest["token_scan_hits"]) == 0
        and manifest["scope"]["cpu_local_only"]
        and manifest["scope"]["uses_existing_db62_vggt_outputs"]
        and not manifest["scope"]["a100_job_submitted"]
        and not manifest["scope"]["db41_edited"]
        and not manifest["claim_boundaries"]["accepted_repair"]
    )
    (OUT_DIR / "db63_vggt_component_gate_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    build_board(manifest)
    print(
        json.dumps(
            {
                "status": "db63_component_gate_created",
                "manifest": manifest["outputs"]["manifest"],
                "board": manifest["outputs"]["board"],
                "verdict": manifest["verdict"],
                "component_summary": manifest["component_summary"],
                "hard_checks_passed": manifest["hard_checks_passed"],
                "token_scan_hits": len(manifest["token_scan_hits"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
