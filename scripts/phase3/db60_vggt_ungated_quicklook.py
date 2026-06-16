from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db60_vggt_ungated_quicklook"
MANIFEST = OUT_DIR / "db60_vggt_ungated_quicklook_manifest.json"
BOARD = OUT_DIR / "db60_vggt_ungated_quicklook_board.jpg"

A1_PANO = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment" / "A1_view_none_bmw_1024x2048.png"
G_PANO = ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg"
A1_EDIT = ROOT / "deliverables" / "a1_streetview_pipeline" / "A1_view_none_editmask.jpg"
A1_ABSTAIN = ROOT / "deliverables" / "a1_streetview_pipeline" / "ABSTAIN_overlay.jpg"
A1_SEAMS = ROOT / "deliverables" / "a1_streetview_pipeline" / "A1_view_none_seam_crops.jpg"
DB25_CAMID = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "roi_camid_overlay.jpg"
DB25_MONTAGE = ROOT / "deliverables" / "dit360_v2" / "db25_longline_evidence_fetch" / "db25_longline_evidence_montage.jpg"
DB45F_REMOTE = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit" / "db45f_vggt_remote_target_uv_sampling_result.json"
DB45F_BOARD = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit" / "db45f_vggt_target_uv_sampling_gate_board.jpg"
DB45K_BOARD = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit" / "db45k_vggt_pose_reflection_audit_board.jpg"
DB59_BOARD = ROOT / "deliverables" / "dit360_v2" / "db59_vggt_a1g_diagnostic" / "db59_vggt_a1g_diagnostic_preflight_board.jpg"

TARGET = {
    "uuid": "02a00399-3857-444e-8db3-a8f58489c394",
    "anchor": 0,
    "roi_key": "db25_longline",
    "roi_xyxy": [850, 420, 1650, 720],
}

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "cloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
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
        return "<non-repo path omitted>"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def norm01(arr: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    arr = arr.astype(np.float32)
    finite = np.isfinite(arr)
    if not finite.any():
        return np.zeros_like(arr, dtype=np.float32)
    lo = np.percentile(arr[finite], 2)
    hi = np.percentile(arr[finite], 98)
    if hi - lo < eps:
        return np.zeros_like(arr, dtype=np.float32)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def resize_float_grid(grid: list[list[float]], size: tuple[int, int]) -> np.ndarray:
    arr = np.asarray(grid, dtype=np.float32)
    img = Image.fromarray(arr, mode="F")
    img = img.resize(size, Image.Resampling.BICUBIC)
    return np.asarray(img, dtype=np.float32)


def save_gray(path: Path, arr: np.ndarray) -> None:
    img = Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8), mode="L")
    img.save(path)


def heat_color(arr: np.ndarray) -> Image.Image:
    a = np.clip(arr, 0.0, 1.0)
    r = np.clip(2.0 * a, 0.0, 1.0)
    g = np.clip(2.0 - 2.0 * np.abs(a - 0.5), 0.0, 1.0)
    b = np.clip(2.0 * (1.0 - a), 0.0, 1.0)
    rgb = np.stack([r, g, b], axis=-1)
    return Image.fromarray((rgb * 255).astype(np.uint8), mode="RGB")


def load_base_images() -> tuple[Image.Image, Image.Image]:
    a1 = Image.open(A1_PANO).convert("RGB")
    g = Image.open(G_PANO).convert("RGB")
    if a1.size != g.size:
        g = g.resize(a1.size, Image.Resampling.BICUBIC)
    return a1, g


def edit_mask_for_roi(size: tuple[int, int], roi: list[int]) -> np.ndarray:
    if not A1_EDIT.exists():
        return np.zeros((roi[3] - roi[1], roi[2] - roi[0]), dtype=np.float32)
    img = Image.open(A1_EDIT).convert("RGB").resize(size, Image.Resampling.BICUBIC)
    crop = np.asarray(img.crop(tuple(roi)), dtype=np.float32) / 255.0
    colorfulness = crop.max(axis=-1) - crop.min(axis=-1)
    brightness = crop.mean(axis=-1)
    mask = np.maximum(colorfulness, np.clip((brightness - 0.08) / 0.7, 0, 1))
    return norm01(mask)


def cam_boundary_mask(roi_size: tuple[int, int]) -> np.ndarray:
    if not DB25_CAMID.exists():
        return np.zeros((roi_size[1], roi_size[0]), dtype=np.float32)
    img = Image.open(DB25_CAMID).convert("RGB").resize(roi_size, Image.Resampling.BICUBIC)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    gx = np.abs(np.diff(arr, axis=1, prepend=arr[:, :1, :])).mean(axis=-1)
    gy = np.abs(np.diff(arr, axis=0, prepend=arr[:1, :, :])).mean(axis=-1)
    grad = gx + gy
    color = arr.max(axis=-1) - arr.min(axis=-1)
    raw = 0.65 * norm01(grad) + 0.35 * norm01(color)
    return np.asarray(Image.fromarray((raw * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), dtype=np.float32) / 255.0


def image_difference_mask(a: Image.Image, b: Image.Image, roi: list[int]) -> np.ndarray:
    ac = np.asarray(a.crop(tuple(roi)), dtype=np.float32)
    bc = np.asarray(b.crop(tuple(roi)), dtype=np.float32)
    diff = np.abs(ac - bc).mean(axis=-1)
    return norm01(diff)


def vggt_prior_for_roi(roi: list[int]) -> tuple[np.ndarray, dict[str, Any]]:
    data = read_json(DB45F_REMOTE)
    sample = data["target_uv_sampling"][TARGET["roi_key"]]
    grids = sample["heatmap_grids"]
    roi_size = (roi[2] - roi[0], roi[3] - roi[1])
    depth_conf = resize_float_grid(grids["depth_conf"], roi_size)
    world_conf = resize_float_grid(grids["world_points_conf"], roi_size)
    valid = resize_float_grid(grids["preprocess_valid"], roi_size)
    prior = 0.72 * norm01(depth_conf) + 0.28 * norm01(world_conf)
    prior = prior * np.clip(valid, 0.0, 1.0)
    prior = np.asarray(
        Image.fromarray((np.clip(prior, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(8)),
        dtype=np.float32,
    ) / 255.0
    stats = {
        "coverage_valid_frac": sample.get("coverage_valid_frac"),
        "owner_uv_valid_frac_of_roi": sample.get("owner_uv_valid_frac_of_roi"),
        "owner_preprocess_valid_frac_of_roi": sample.get("owner_preprocess_valid_frac_of_roi"),
        "target_sampled_stats": sample.get("target_sampled_stats", {}),
        "heatmap_grid_shapes": {k: [len(v), len(v[0]) if v and isinstance(v[0], list) else 0] for k, v in grids.items()},
    }
    return prior, stats


def build_alpha(a1: Image.Image, g: Image.Image, roi: list[int]) -> tuple[np.ndarray, dict[str, Any]]:
    roi_size = (roi[2] - roi[0], roi[3] - roi[1])
    prior, vggt_stats = vggt_prior_for_roi(roi)
    edit = edit_mask_for_roi(a1.size, roi)
    cam = cam_boundary_mask(roi_size)
    diff = image_difference_mask(a1, g, roi)
    seam = 0.45 * edit + 0.35 * cam + 0.20 * diff
    seam = norm01(seam)
    seam_img = Image.fromarray((seam * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(7))
    seam = np.asarray(seam_img, dtype=np.float32) / 255.0
    raw_alpha = seam * (0.30 + 0.70 * prior)
    alpha = np.clip(raw_alpha * 0.48, 0.0, 0.48)
    stats = {
        "vggt_stats": vggt_stats,
        "prior_min": float(prior.min()),
        "prior_mean": float(prior.mean()),
        "prior_max": float(prior.max()),
        "edit_mask_mean": float(edit.mean()),
        "cam_boundary_mean": float(cam.mean()),
        "a1_g_diff_mask_mean": float(diff.mean()),
        "alpha_min": float(alpha.min()),
        "alpha_mean": float(alpha.mean()),
        "alpha_max": float(alpha.max()),
        "alpha_changed_frac_gt_0_05": float((alpha > 0.05).mean()),
    }
    return alpha, stats


def apply_quicklook(base: Image.Image, donor: Image.Image, alpha: np.ndarray, roi: list[int]) -> tuple[Image.Image, dict[str, Any]]:
    out = base.copy()
    base_crop = base.crop(tuple(roi)).convert("RGB")
    donor_crop = donor.crop(tuple(roi)).convert("RGB")
    smooth_crop = base_crop.filter(ImageFilter.GaussianBlur(9))
    base_arr = np.asarray(base_crop, dtype=np.float32)
    donor_arr = np.asarray(donor_crop, dtype=np.float32)
    smooth_arr = np.asarray(smooth_crop, dtype=np.float32)
    target = 0.62 * smooth_arr + 0.38 * donor_arr
    a = alpha[..., None].astype(np.float32)
    cand = base_arr * (1.0 - a) + target * a
    cand_img = Image.fromarray(np.clip(cand, 0, 255).astype(np.uint8), mode="RGB")
    out.paste(cand_img, (roi[0], roi[1]))
    delta = np.abs(cand - base_arr).mean(axis=-1)
    stats = {
        "roi_changed_frac_alpha_gt_0_05": float((alpha > 0.05).mean()),
        "roi_mean_abs_delta": float(delta.mean()),
        "roi_p95_abs_delta": float(np.percentile(delta, 95)),
        "roi_max_abs_delta": float(delta.max()),
    }
    return out, stats


def diff_crop(before: Image.Image, after: Image.Image, roi: list[int]) -> Image.Image:
    d = ImageChops.difference(before.crop(tuple(roi)).convert("RGB"), after.crop(tuple(roi)).convert("RGB"))
    d = ImageEnhance.Brightness(d).enhance(5.0)
    return d


def token_hits(manifest_preview: dict[str, Any]) -> list[dict[str, Any]]:
    text = json.dumps(manifest_preview, ensure_ascii=False, sort_keys=True)
    hits: list[dict[str, Any]] = []
    for pattern_name, pattern in TOKEN_PATTERNS.items():
        found = pattern.findall(text)
        if found:
            hits.append({"path": "manifest_preview", "pattern": pattern_name, "count": len(found)})
    return hits


def image_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    with Image.open(path) as img:
        return {"exists": True, "size": list(img.size), "bytes": int(path.stat().st_size)}


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


def panel(board: Image.Image, image: Image.Image | Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(24, 27, 32), outline=(86, 91, 101), width=2)
    if isinstance(image, Path):
        if image.exists():
            img = Image.open(image).convert("RGB")
        else:
            draw_text(draw, (x0 + 12, y0 + 30), "missing", fill=(246, 142, 142), size=15)
            draw_text(draw, (x0 + 12, y1 - 31), label, fill=(220, 230, 245), size=13)
            return
    else:
        img = image.convert("RGB")
    img.thumbnail((x1 - x0 - 20, y1 - y0 - 48))
    px = x0 + (x1 - x0 - img.width) // 2
    board.paste(img, (px, y0 + 10))
    draw_text(draw, (x0 + 12, y1 - 31), label, fill=(220, 230, 245), size=13)


def crop_panel(board: Image.Image, image: Image.Image, roi: list[int], box: tuple[int, int, int, int], label: str) -> None:
    panel(board, image.crop(tuple(roi)), box, label)


def build_board(
    manifest: dict[str, Any],
    a1: Image.Image,
    g: Image.Image,
    a1_candidate: Image.Image,
    g_candidate: Image.Image,
    alpha: np.ndarray,
    prior: np.ndarray,
) -> None:
    roi = TARGET["roi_xyxy"]
    board = Image.new("RGB", (2400, 1850), (15, 17, 22))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (40, 28), "DB60 VGGT-prior ungated A1/G quick-look - presentation-only", size=28)
    y = draw_wrapped(
        draw,
        40,
        76,
        "This intentionally ignores source-faithful repair gates for a fixed DB25 visual quick-look. It uses existing DB45f official VGGT raw-anchor heatmap grids as a soft prior. It is not source-faithful, not raw-camera-backed repair permission, and not Bosch training data.",
        170,
        fill=(246, 214, 150),
        size=15,
    )
    y += 14
    for x, label, ok in [
        (40, "secret hits 0", len(manifest["token_scan_hits"]) == 0),
        (300, "A100 false", not manifest["scope"]["a100_used"]),
        (560, "fixed DB25 ROI", True),
        (820, "presentation-only", True),
        (1080, "source-faithful false", not manifest["claim_boundaries"]["source_faithful"]),
    ]:
        fill = (42, 100, 72) if ok else (132, 64, 47)
        draw.rounded_rectangle((x, y, x + 238, y + 38), radius=6, fill=fill, outline=(190, 190, 190))
        draw_text(draw, (x + 10, y + 10), label, size=13)

    row_y = 165
    crop_panel(board, a1, roi, (40, row_y, 600, row_y + 245), "A1 original DB25 ROI")
    crop_panel(board, a1_candidate, roi, (620, row_y, 1180, row_y + 245), "A1 quick-look candidate")
    panel(board, diff_crop(a1, a1_candidate, roi), (1200, row_y, 1760, row_y + 245), "A1 diff x5")
    crop_panel(board, g, roi, (1780, row_y, 2340, row_y + 245), "G original DB25 ROI")

    row_y = 430
    crop_panel(board, g_candidate, roi, (40, row_y, 600, row_y + 245), "G quick-look candidate")
    panel(board, diff_crop(g, g_candidate, roi), (620, row_y, 1180, row_y + 245), "G diff x5")
    panel(board, heat_color(prior), (1200, row_y, 1760, row_y + 245), "VGGT prior from DB45f heatmap grids")
    panel(board, heat_color(alpha / max(float(alpha.max()), 1e-6)), (1780, row_y, 2340, row_y + 245), "Final alpha mask")

    row_y = 695
    panel(board, A1_SEAMS, (40, row_y, 600, row_y + 245), "A1 seam crops context")
    panel(board, A1_ABSTAIN, (620, row_y, 1180, row_y + 245), "A1 abstain/mask context")
    panel(board, DB25_MONTAGE, (1200, row_y, 1760, row_y + 245), "DB25 evidence context")
    panel(board, DB45F_BOARD, (1780, row_y, 2340, row_y + 245), "DB45f VGGT diagnostic source")

    row_y = 960
    panel(board, DB45K_BOARD, (40, row_y, 600, row_y + 245), "DB45k coordinate blocker")
    panel(board, DB59_BOARD, (620, row_y, 1180, row_y + 245), "DB59 gate-stop context")
    panel(board, a1_candidate.resize((512, 256), Image.Resampling.BICUBIC), (1200, row_y, 1760, row_y + 245), "A1 full candidate preview")
    panel(board, g_candidate.resize((512, 256), Image.Resampling.BICUBIC), (1780, row_y, 2340, row_y + 245), "G full candidate preview")

    draw_text(draw, (40, 1245), "Operator and metrics", fill=(245, 245, 245), size=22)
    y = 1285
    y = draw_wrapped(draw, 58, y, manifest["operator"]["description"], 130, fill=(224, 232, 245), size=15)
    y = draw_wrapped(draw, 58, y + 8, f"A1 metrics: {manifest['outputs']['a1_candidate']['metrics']}", 130, fill=(214, 222, 236), size=14)
    y = draw_wrapped(draw, 58, y + 8, f"G metrics: {manifest['outputs']['g_candidate']['metrics']}", 130, fill=(214, 222, 236), size=14)
    y = draw_wrapped(draw, 58, y + 8, f"Alpha stats: {manifest['operator']['alpha_stats']}", 130, fill=(214, 222, 236), size=14)

    draw_text(draw, (1260, 1245), "Claim boundary", fill=(245, 245, 245), size=22)
    y = 1285
    for key, value in manifest["claim_boundaries"].items():
        y = draw_wrapped(draw, 1278, y, f"{key}: {value}", 92, fill=(246, 214, 150), size=14)

    draw_text(draw, (40, 1788), f"Manifest: {rel(MANIFEST)}", fill=(185, 190, 200), size=13)
    board.save(BOARD, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    roi = TARGET["roi_xyxy"]
    a1, g = load_base_images()
    alpha, alpha_stats = build_alpha(a1, g, roi)
    prior, vggt_stats = vggt_prior_for_roi(roi)
    a1_candidate, a1_stats = apply_quicklook(a1, g, alpha, roi)
    g_candidate, g_stats = apply_quicklook(g, a1, alpha, roi)

    alpha_path = OUT_DIR / "db60_vggt_prior_alpha_mask.png"
    prior_path = OUT_DIR / "db60_vggt_prior_heatmap.png"
    a1_path = OUT_DIR / "db60_a1_view_none_vggt_prior_ungated_quicklook.png"
    g_path = OUT_DIR / "db60_g_bmw_pano_vggt_prior_ungated_quicklook.png"
    a1_crop_path = OUT_DIR / "db60_a1_quicklook_roi_crop.png"
    g_crop_path = OUT_DIR / "db60_g_quicklook_roi_crop.png"
    save_gray(alpha_path, alpha / max(float(alpha.max()), 1e-6))
    heat_color(prior).save(prior_path)
    a1_candidate.save(a1_path)
    g_candidate.save(g_path)
    a1_candidate.crop(tuple(roi)).save(a1_crop_path)
    g_candidate.crop(tuple(roi)).save(g_crop_path)

    manifest: dict[str, Any] = {
        "db": "DB-60",
        "status": "presentation_only_vggt_prior_ungated_quicklook",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "target": TARGET,
        "scope": {
            "cpu_local_only": True,
            "remote_status_or_exec": False,
            "a100_used": False,
            "network_used": False,
            "new_vggt_inference": False,
            "dit_flux_prompt_generation": False,
            "inpainting": False,
            "full_panorama_synthesis": False,
            "source_replacement": False,
            "source_id_map_created": False,
            "red_promotion": False,
            "permission_change": False,
            "edits_limited_to_db25_roi": True,
            "db41_edited": False,
        },
        "inputs": {
            "a1_view_none": rel(A1_PANO),
            "g_bmw_pano": rel(G_PANO),
            "a1_editmask": rel(A1_EDIT),
            "db25_camid_overlay": rel(DB25_CAMID),
            "db45f_remote_vggt_result": rel(DB45F_REMOTE),
            "db45f_result_type": "existing official VGGT raw-anchor ROI heatmap grids and statistics",
        },
        "operator": {
            "description": (
                "Upsample DB45f DB25 depth_conf/world_points_conf/preprocess_valid heatmap grids into a VGGT prior; "
                "combine with A1 edit-mask energy, DB25 camera-boundary energy, and A1-vs-G visual difference to form a soft alpha; "
                "inside DB25 ROI only, blend each base crop with 62% local blur and 38% opposite diagnostic pano donor under alpha max 0.48."
            ),
            "vggt_prior_stats": vggt_stats,
            "alpha_stats": alpha_stats,
            "ungated": True,
            "why_not_source_faithful": "Uses diagnostic pano donor/blur and rejected evidence gates; intended only to see visual behavior.",
        },
        "outputs": {
            "a1_candidate": {"path": rel(a1_path), "metrics": a1_stats, **image_stats(a1_path)},
            "g_candidate": {"path": rel(g_path), "metrics": g_stats, **image_stats(g_path)},
            "a1_roi_crop": {"path": rel(a1_crop_path), **image_stats(a1_crop_path)},
            "g_roi_crop": {"path": rel(g_crop_path), **image_stats(g_crop_path)},
            "alpha_mask": {"path": rel(alpha_path), **image_stats(alpha_path)},
            "vggt_prior_heatmap": {"path": rel(prior_path), **image_stats(prior_path)},
            "board": {"path": rel(BOARD)},
            "manifest": {"path": rel(MANIFEST)},
        },
        "claim_boundaries": {
            "source_faithful": False,
            "raw_camera_backed_repair": False,
            "diagnostic": True,
            "presentation_only": True,
            "generated": False,
            "abstain_or_no_permission_state": True,
            "a1_g_repaired": False,
            "db32_changed": False,
            "bosch_training_ready": False,
        },
    }
    manifest["token_scan_hits"] = token_hits(manifest)
    manifest["hard_checks_passed"] = all(
        [
            len(manifest["token_scan_hits"]) == 0,
            manifest["scope"]["cpu_local_only"],
            not manifest["scope"]["a100_used"],
            not manifest["scope"]["new_vggt_inference"],
            not manifest["scope"]["dit_flux_prompt_generation"],
            not manifest["scope"]["source_replacement"],
            not manifest["claim_boundaries"]["source_faithful"],
            manifest["claim_boundaries"]["presentation_only"],
        ]
    )
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    build_board(manifest, a1, g, a1_candidate, g_candidate, alpha, prior)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "manifest": rel(MANIFEST),
                "board": rel(BOARD),
                "a1_candidate": rel(a1_path),
                "g_candidate": rel(g_path),
                "hard_checks_passed": manifest["hard_checks_passed"],
                "token_scan_hits": len(manifest["token_scan_hits"]),
                "a100_used": manifest["scope"]["a100_used"],
                "claim": "presentation-only ungated quick-look; not source-faithful repair",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
