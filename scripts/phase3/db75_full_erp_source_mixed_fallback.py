from __future__ import annotations

import argparse
import base64
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, rel, safe_status, sanitize


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db75_full_erp_source_mixed_fallback"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db75_full_erp_source_mixed_fallback"
REMOTE_RESULT = REMOTE_OUT + "/DB75_remote_result.json"
LOCAL_REMOTE_RESULT = OUT_DIR / "DB75_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "DB75_batch_summary.json"
MANIFEST = OUT_DIR / "DB75_manifest.json"
BOARD = OUT_DIR / "DB75_full_review_board.jpg"
ROI_SHEET = OUT_DIR / "DB75_same_roi_comparison_sheet.jpg"
FETCH_DIR = OUT_DIR / "fetch"

FETCH_ITEMS = {
    "summary": ("DB75_batch_summary.json", 16),
    "remote_result": ("DB75_remote_result.json", 16),
    "board": ("DB75_full_review_board.jpg", 60),
    "roi_sheet": ("DB75_same_roi_comparison_sheet.jpg", 60),
    "bmw_baseline": ("02a00399_a000_bmw_hard_select_raw.png", 30),
    "bmw_candidate": ("02a00399_a000_bmw_source_mixed_candidate.png", 30),
    "bmw_alpha": ("02a00399_a000_bmw_alpha_map.png", 16),
    "bmw_mix": ("02a00399_a000_bmw_source_mix_mask.png", 16),
    "bmw_changed": ("02a00399_a000_bmw_changed_mask.png", 16),
    "bmw_diff": ("02a00399_a000_bmw_diff_x6.png", 30),
    "bmw_report": ("02a00399_a000_bmw_marked_roi_report.json", 16),
    "bmw_claim": ("02a00399_a000_bmw_claim.json", 8),
    "clean_baseline": ("0bae3b5e_a030_clean_far_hard_select_raw.png", 30),
    "clean_candidate": ("0bae3b5e_a030_clean_far_source_mixed_candidate.png", 30),
    "clean_alpha": ("0bae3b5e_a030_clean_far_alpha_map.png", 16),
    "clean_report": ("0bae3b5e_a030_clean_far_marked_roi_report.json", 16),
    "clean_claim": ("0bae3b5e_a030_clean_far_claim.json", 8),
}

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "trycloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "json_token": re.compile(r'"token"\s*:\s*"[A-Za-z0-9._\-]{12,}"'),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
}


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pattern in TOKEN_PATTERNS.items():
        found = pattern.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


def image_stat(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"exists": path.exists(), "path": rel(path)}
    if path.exists() and path.is_file():
        row["bytes"] = int(path.stat().st_size)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            try:
                with Image.open(path) as img:
                    row["size"] = list(img.size)
            except Exception as exc:
                row["image_error"] = repr(exc)
    return row


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def remote_python() -> str:
    code = r'''
import json
import math
import pathlib
import subprocess
import sys
import time
import traceback

import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__")
REMOTE_RESULT = pathlib.Path("__REMOTE_RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [
    pathlib.Path("/content/waymo2panorama"),
    pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama"),
]
H, W = 1024, 2048
CASES = [
    ("02a00399:0:bmw", "02a00399_a000_bmw"),
    ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far"),
]
MARKED_ROIS = {
    "left_road_patch": (250, 515, 460, 715),
    "lower_center_road_patch": (740, 595, 1035, 745),
    "center_lane_marking": (1030, 515, 1325, 735),
    "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760),
}
VARIANTS = [
    {"id": "soft_r16_a035_g2", "radius": 16, "alpha": 0.35, "gamma": 2.0},
    {"id": "soft_r24_a045_g2", "radius": 24, "alpha": 0.45, "gamma": 2.0},
    {"id": "soft_r32_a055_g2", "radius": 32, "alpha": 0.55, "gamma": 2.0},
    {"id": "soft_r48_a060_g2", "radius": 48, "alpha": 0.60, "gamma": 2.0},
    {"id": "soft_r48_a075_g2", "radius": 48, "alpha": 0.75, "gamma": 2.0},
    {"id": "soft_r64_a065_g1", "radius": 64, "alpha": 0.65, "gamma": 1.0},
    {"id": "soft_r64_a080_g1", "radius": 64, "alpha": 0.80, "gamma": 1.0},
    {"id": "soft_r80_a070_g1", "radius": 80, "alpha": 0.70, "gamma": 1.0},
    {"id": "soft_r32_a050_g4", "radius": 32, "alpha": 0.50, "gamma": 4.0},
    {"id": "soft_r48_a060_g4", "radius": 48, "alpha": 0.60, "gamma": 4.0},
]
OUT = {
    "db": "DB-75",
    "phase": "full_erp_source_mixed_presentation_fallback",
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "scope": {
        "cases": [c[0] for c in CASES],
        "full_erp": True,
        "raw_camera_slabs_only": True,
        "source_mixed_blending": True,
        "generated_mask": 0,
        "model_inference": False,
        "inpaint_generation": False,
        "flow_apap_homography": False,
        "ground_ipm_replacement": False,
        "db32_edit": False,
        "source_faithful_repair_claim_allowed": False,
    },
}


def run(cmd, timeout=420, cwd=None):
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    return {"returncode": int(proc.returncode), "tail": proc.stdout[-1200:]}


def import_ok(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def ensure_deps():
    rows = {}
    if not import_ok("av2"):
        rows["av2_install"] = run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=600)
    rows["av2_import_after"] = import_ok("av2")
    return rows


def find_workdir():
    for cand in WORKDIR_CANDIDATES:
        if (cand / "code" / "waymo2panorama").exists() and (cand / "scripts" / "phase3").exists():
            return cand
    return None


def json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if math.isfinite(v) else None
    return obj


def save_u8(path, arr):
    import cv2
    cv2.imwrite(str(path), arr.astype("uint8"))


def save_rgb(path, arr, quality=92):
    import cv2
    arr8 = np.clip(arr, 0, 255).astype("uint8")
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        cv2.imwrite(str(path), cv2.cvtColor(arr8, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    else:
        cv2.imwrite(str(path), cv2.cvtColor(arr8, cv2.COLOR_RGB2BGR))


def normalize_u8(x, valid=None):
    arr = x.astype(np.float32)
    vals = arr[valid] if valid is not None and bool(np.any(valid)) else arr.reshape(-1)
    lo, hi = np.percentile(vals, [2, 98])
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.uint8)
    return np.clip((arr - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def paste(board, arr, box):
    from PIL import Image, ImageDraw
    im = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    x0, y0, x1, y1 = box
    im.thumbnail((x1 - x0, y1 - y0))
    px = x0 + ((x1 - x0) - im.width) // 2
    py = y0 + ((y1 - y0) - im.height) // 2
    board.paste(im, (px, py))
    ImageDraw.Draw(board).rectangle((px, py, px + im.width, py + im.height), outline=(135, 140, 150))


def draw_rois(img):
    from PIL import Image, ImageDraw
    im = Image.fromarray(np.clip(img, 0, 255).astype("uint8"))
    d = ImageDraw.Draw(im)
    colors = [(255, 82, 82), (255, 170, 40), (80, 220, 255), (170, 110, 255)]
    for (name, roi), color in zip(MARKED_ROIS.items(), colors):
        d.rectangle(roi, outline=color, width=4)
        d.text((roi[0] + 4, max(0, roi[1] - 18)), name, fill=color)
    return np.asarray(im)


def stack_grid(items, width=520):
    from PIL import Image, ImageDraw
    thumbs = []
    for label, arr in items:
        im = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
        im.thumbnail((width, 300))
        panel = Image.new("RGB", (width, im.height + 34), (18, 20, 25))
        panel.paste(im, ((width - im.width) // 2, 30))
        ImageDraw.Draw(panel).text((8, 8), label, fill=(235, 235, 235))
        thumbs.append(panel)
    rows = []
    for i in range(0, len(thumbs), 3):
        row = thumbs[i:i+3]
        h = max(p.height for p in row)
        canvas = Image.new("RGB", (width * len(row), h), (18, 20, 25))
        x = 0
        for p in row:
            canvas.paste(p, (x, 0))
            x += width
        rows.append(canvas)
    out = Image.new("RGB", (max(r.width for r in rows), sum(r.height for r in rows)), (18, 20, 25))
    y = 0
    for r in rows:
        out.paste(r, (0, y))
        y += r.height
    return np.asarray(out)


def compose(slabs, label, valid):
    stack = np.stack([s.astype(np.uint8) for s in slabs], axis=0)
    src = np.clip(label, 0, len(slabs) - 1)
    idx = src[None, ..., None]
    out = np.take_along_axis(stack, idx, axis=0)[0]
    out = np.where(valid[..., None], out, 0).astype(np.uint8)
    return out


def source_boundary(label, valid):
    sid = label.astype(np.int32)
    src_valid = valid & (sid != 255)
    boundary = np.zeros_like(valid, dtype=bool)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        shifted = np.roll(sid, shift=(dy, dx), axis=(0, 1))
        shifted_valid = np.roll(src_valid, shift=(dy, dx), axis=(0, 1))
        boundary |= src_valid & shifted_valid & (shifted != sid)
    return boundary


def soft_source_blend(slabs, weights, gamma):
    stack = np.stack([s.astype(np.float32) for s in slabs], axis=0)
    w = np.stack([np.maximum(x.astype(np.float32), 0.0) for x in weights], axis=0)
    w = np.power(w, float(gamma))
    denom = w.sum(axis=0)
    numer = (stack * w[..., None]).sum(axis=0)
    soft = np.where(denom[..., None] > 1e-8, numer / np.maximum(denom[..., None], 1e-8), 0)
    return np.clip(soft, 0, 255).astype(np.uint8), denom > 1e-8


def alpha_from_boundary(boundary, valid, radius, alpha):
    import cv2
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(radius) * 2 + 1, int(radius) * 2 + 1))
    band = cv2.dilate(boundary.astype(np.uint8), kernel).astype(bool) & valid
    dist = cv2.distanceTransform((~boundary).astype(np.uint8), cv2.DIST_L2, 3)
    a = float(alpha) * np.clip(1.0 - dist / max(float(radius), 1.0), 0.0, 1.0)
    a[~band] = 0.0
    return a.astype(np.float32), band


def luma(rgb):
    arr = rgb.astype(np.float32)
    return 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]


def seam_energy(rgb, boundary, valid):
    import cv2
    gray = luma(rgb).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    m = boundary & valid
    return float(mag[m].mean()) if bool(m.any()) else None


def evaluate(base, cand, label0, valid):
    boundary = source_boundary(label0, valid)
    diff = np.abs(cand.astype(np.int16) - base.astype(np.int16)).max(axis=-1)
    changed = (diff > 1) & valid
    row = {
        "global_seam_energy": seam_energy(cand, boundary, valid),
        "global_changed_fraction": float(changed.mean()),
        "p95_abs_delta": float(np.percentile(diff[valid], 95)) if bool(valid.any()) else 0.0,
        "max_abs_delta": int(diff[valid].max()) if bool(valid.any()) else 0,
        "marked_rois": [],
    }
    roi_scores = []
    for name, (x0, y0, x1, y1) in MARKED_ROIS.items():
        sl = np.s_[y0:y1, x0:x1]
        e = seam_energy(cand[sl], boundary[sl], valid[sl])
        roi_scores.append(e if e is not None else 9999.0)
        row["marked_rois"].append({
            "roi": name,
            "seam_energy": e,
            "changed_fraction": float(changed[sl].mean()),
            "p95_abs_delta": float(np.percentile(diff[sl][valid[sl]], 95)) if bool(valid[sl].any()) else 0.0,
        })
    row["roi_mean_seam_energy"] = float(np.mean(roi_scores)) if roi_scores else None
    return row, boundary, changed, diff


def render_raw_case(case_spec, workdir):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    loader = AV2RingLoader(log_dir)
    anchor_ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)
    slabs, weights = [], []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(frame.images[cam], calib.K, calib.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        slabs.append(rgb.astype(np.uint8))
        weights.append(w.astype(np.float32))
    wstack = np.stack(weights, axis=0)
    valid = wstack.max(axis=0) > 1e-6
    label0 = wstack.argmax(axis=0).astype(np.uint8)
    label0[~valid] = 255
    base = compose(slabs, label0, valid)
    return run_name, slabs, weights, label0, valid, base


def build_candidate(base, slabs, weights, label0, valid, variant):
    soft, soft_valid = soft_source_blend(slabs, weights, variant["gamma"])
    boundary = source_boundary(label0, valid)
    alpha_map, seam_band = alpha_from_boundary(boundary, valid & soft_valid, int(variant["radius"]), float(variant["alpha"]))
    a = alpha_map[..., None]
    cand = base.astype(np.float32) * (1.0 - a) + soft.astype(np.float32) * a
    cand = np.where(valid[..., None], cand, 0).astype(np.uint8)
    return cand, soft, alpha_map, seam_band


def build_roi_sheet(base, cand, alpha_map, changed, diff, label0, valid):
    from PIL import Image, ImageDraw
    boundary = source_boundary(label0, valid)
    sheet = Image.new("RGB", (1840, 1720), (18, 20, 25))
    draw = ImageDraw.Draw(sheet)
    draw.text((30, 24), "DB75 BMW source-mixed seam-band ROI sheet", fill=(240, 240, 240))
    alpha_rgb = np.dstack([normalize_u8(alpha_map, None)] * 3)
    changed_rgb = np.dstack([changed.astype(np.uint8) * 255, np.zeros_like(changed, dtype=np.uint8), np.zeros_like(changed, dtype=np.uint8)])
    diff_rgb = np.dstack([np.clip(diff * 6, 0, 255).astype(np.uint8)] * 3)
    boundary_rgb = np.dstack([boundary.astype(np.uint8) * 255, np.zeros_like(boundary, dtype=np.uint8), np.zeros_like(boundary, dtype=np.uint8)])
    for r, (name, roi) in enumerate(MARKED_ROIS.items()):
        x0, y0, x1, y1 = roi
        yb = 72 + r * 405
        draw.text((28, yb), name, fill=(245, 245, 245))
        crops = [
            ("hard_select", base[y0:y1, x0:x1]),
            ("DB75 source-mixed", cand[y0:y1, x0:x1]),
            ("alpha", alpha_rgb[y0:y1, x0:x1]),
            ("changed", changed_rgb[y0:y1, x0:x1]),
            ("diff x6", diff_rgb[y0:y1, x0:x1]),
            ("source boundary", boundary_rgb[y0:y1, x0:x1]),
        ]
        for c, (label, arr) in enumerate(crops):
            bx = 28 + c * 300
            draw.text((bx, yb + 24), label, fill=(220, 228, 238))
            paste(sheet, arr, (bx, yb + 48, bx + 280, yb + 230))
    sheet.save(REMOTE_OUT / "DB75_same_roi_comparison_sheet.jpg", quality=92)


def run_one_case(case_spec, workdir):
    t0 = time.time()
    run_name, slabs, weights, label0, valid, base = render_raw_case(case_spec, workdir)
    base_metrics, boundary, _base_changed, _base_diff = evaluate(base, base, label0, valid)
    rows = []
    best = None
    best_score = None
    for variant in VARIANTS:
        cand, soft, alpha_map, seam_band = build_candidate(base, slabs, weights, label0, valid, variant)
        metrics, _boundary, changed, diff = evaluate(base, cand, label0, valid)
        roi = metrics["roi_mean_seam_energy"] if metrics["roi_mean_seam_energy"] is not None else 9999.0
        changed_frac = metrics["global_changed_fraction"]
        p95 = metrics["p95_abs_delta"]
        score = float(roi + 12.0 * changed_frac + 0.015 * p95)
        row = {"variant": variant["id"], "params": variant, "metrics": metrics, "score": score}
        rows.append(row)
        save_rgb(REMOTE_OUT / f"{run_name}_candidate_{variant['id']}.png", cand)
        if best is None or score < best_score:
            best = (variant, cand, soft, alpha_map, seam_band, metrics, changed, diff, row)
            best_score = score
    variant, cand, soft, alpha_map, seam_band, metrics, changed, diff, best_row = best
    mix_mask = (alpha_map > 0.01) & valid
    save_rgb(REMOTE_OUT / f"{run_name}_hard_select_raw.png", base)
    save_rgb(REMOTE_OUT / f"{run_name}_source_mixed_candidate.png", cand)
    save_rgb(REMOTE_OUT / f"{run_name}_soft_weighted_blend.png", soft)
    save_u8(REMOTE_OUT / f"{run_name}_source_id_before.png", label0)
    save_u8(REMOTE_OUT / f"{run_name}_alpha_map.png", normalize_u8(alpha_map, None))
    save_u8(REMOTE_OUT / f"{run_name}_seam_band_mask.png", seam_band.astype(np.uint8) * 255)
    save_u8(REMOTE_OUT / f"{run_name}_source_mix_mask.png", mix_mask.astype(np.uint8) * 255)
    save_u8(REMOTE_OUT / f"{run_name}_changed_mask.png", changed.astype(np.uint8) * 255)
    save_u8(REMOTE_OUT / f"{run_name}_diff_x6.png", np.clip(diff * 6, 0, 255).astype(np.uint8))
    save_u8(REMOTE_OUT / f"{run_name}_generated_mask.png", np.zeros((H, W), np.uint8))
    if run_name.startswith("02a00399"):
        build_roi_sheet(base, cand, alpha_map, changed, diff, label0, valid)
    board_items = [
        ("hard_select raw", draw_rois(base) if run_name.startswith("02a00399") else base),
        ("DB75 source-mixed candidate", draw_rois(cand) if run_name.startswith("02a00399") else cand),
        ("soft weighted blend", draw_rois(soft) if run_name.startswith("02a00399") else soft),
        ("alpha map", np.dstack([normalize_u8(alpha_map, None)] * 3)),
        ("source mix mask", np.dstack([mix_mask.astype(np.uint8) * 255] * 3)),
        ("diff x6", np.dstack([np.clip(diff * 6, 0, 255).astype(np.uint8)] * 3)),
    ]
    save_rgb(REMOTE_OUT / f"{run_name}_case_board.jpg", stack_grid(board_items, width=560), quality=90)
    report = {
        "case": run_name,
        "best_variant_id": variant["id"],
        "best_variant_params": variant,
        "baseline_metrics": base_metrics,
        "best_metrics": metrics,
        "best_roi_gain_vs_base": float((base_metrics["roi_mean_seam_energy"] - metrics["roi_mean_seam_energy"]) / max(base_metrics["roi_mean_seam_energy"], 1e-6)),
        "best_changed_fraction": metrics["global_changed_fraction"],
        "source_mix_fraction": float(mix_mask.mean()),
        "variants": rows,
        "runtime_s": round(time.time() - t0, 2),
    }
    (REMOTE_OUT / f"{run_name}_marked_roi_report.json").write_text(json.dumps(json_safe(report), indent=2), encoding="utf-8")
    claim = {
        "case": run_name,
        "classification": "presentation_only_source_mixed_candidate_pending_vision",
        "source_faithful_repair": False,
        "bosch_training_ready": False,
        "raw_camera_sourced_inputs": True,
        "single_source_truth": False,
        "source_mixed_pixels": True,
        "source_mix_fraction": float(mix_mask.mean()),
        "generated_mask": 0,
        "model_inference": False,
        "inpaint_generation": False,
        "flow_apap_homography": False,
        "operator_map": "source_mixed_blend in alpha/mix mask; hard_select elsewhere",
    }
    (REMOTE_OUT / f"{run_name}_claim.json").write_text(json.dumps(claim, indent=2), encoding="utf-8")
    return report


def build_full_board(summary):
    from PIL import Image, ImageDraw
    board = Image.new("RGB", (2320, 1540), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw.text((30, 24), "DB75 - full ERP source-mixed seam-band presentation fallback", fill=(240, 240, 240))
    lines = [
        "Raw source slabs only. Pixels in alpha/mix mask may be multi-source blends. generated_mask=0. No source-faithful repair claim.",
        f"status={summary.get('status')} classification={summary.get('claim_classification')}",
        f"BMW best={summary['by_case']['02a00399_a000_bmw'].get('best_variant_id')} ROI gain={summary['by_case']['02a00399_a000_bmw'].get('best_roi_gain_vs_base')}",
        f"clean degraded={summary['by_case']['0bae3b5e_a030_clean_far'].get('clean_control_degraded')}",
    ]
    y = 48
    for line in lines:
        draw.text((34, y), str(line)[:220], fill=(225, 230, 235))
        y += 22
    panels = [
        ("BMW hard_select", REMOTE_OUT / "02a00399_a000_bmw_hard_select_raw.png"),
        ("BMW DB75 source-mixed", REMOTE_OUT / "02a00399_a000_bmw_source_mixed_candidate.png"),
        ("BMW case sidecars", REMOTE_OUT / "02a00399_a000_bmw_case_board.jpg"),
        ("BMW ROI sheet", REMOTE_OUT / "DB75_same_roi_comparison_sheet.jpg"),
        ("clean hard_select", REMOTE_OUT / "0bae3b5e_a030_clean_far_hard_select_raw.png"),
        ("clean DB75 source-mixed", REMOTE_OUT / "0bae3b5e_a030_clean_far_source_mixed_candidate.png"),
    ]
    for i, (label, path) in enumerate(panels):
        x = 30 + (i % 2) * 1150
        yy = 150 + (i // 2) * 450
        draw.text((x, yy - 24), label, fill=(235, 235, 235))
        if pathlib.Path(path).exists():
            import cv2
            arr = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
            paste(board, arr, (x, yy, x + 1110, yy + 405))
        else:
            draw.rectangle((x, yy, x + 1110, yy + 405), outline=(100, 100, 100))
    board.save(REMOTE_OUT / "DB75_full_review_board.jpg", quality=92)


try:
    t0 = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    OUT["dependency"] = ensure_deps()
    workdir = find_workdir()
    OUT["workdir_found"] = bool(workdir)
    if workdir is None:
        raise RuntimeError("Waymo2Panorama workdir not found on remote")
    sys.path.insert(0, str(workdir / "scripts" / "phase3"))
    sys.path.insert(0, str(workdir / "code"))
    reports = []
    for case_spec, _run_name in CASES:
        reports.append(run_one_case(case_spec, workdir))
    by_case = {r["case"]: r for r in reports}
    bmw = by_case["02a00399_a000_bmw"]
    clean = by_case["0bae3b5e_a030_clean_far"]
    clean_degraded = bool(
        (clean["best_metrics"]["global_seam_energy"] or 0) > 1.08 * (clean["baseline_metrics"]["global_seam_energy"] or 1) or
        clean["best_metrics"]["global_changed_fraction"] > 0.10 or
        clean["best_metrics"]["p95_abs_delta"] > 30
    )
    classification = "presentation_only_source_mixed_candidate_pending_vision"
    if clean_degraded:
        classification = "rejected_source_mixed_candidate_pending_vision_clean_degraded"
    summary = {
        "status": "DB75_complete",
        "claim_classification": classification,
        "source_faithful_repair_claim_allowed": False,
        "bosch_training_ready": False,
        "generated_mask": 0,
        "clean_control_degraded": clean_degraded,
        "by_case": by_case,
        "scope": OUT["scope"],
        "runtime_s": round(time.time() - t0, 2),
    }
    by_case["0bae3b5e_a030_clean_far"]["clean_control_degraded"] = clean_degraded
    build_full_board(summary)
    (REMOTE_OUT / "DB75_batch_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    OUT["status"] = "DB75_completed"
    OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "DB75_failed_or_blocked"
    OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB75_JSON_BEGIN")
    print(json.dumps(json_safe(OUT), sort_keys=True, separators=(",", ":")))
    print("DB75_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__REMOTE_RESULT__", REMOTE_RESULT)


def remote_bash() -> str:
    code_b64 = base64.b64encode(remote_python().encode("utf-8")).decode("ascii")
    return (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{code_b64}').decode('utf-8')\n"
        "exec(compile(code, '<db75_full_erp_source_mixed_fallback_remote>', 'exec'))\n"
        "PY"
    )


def parse_json_from_log(log_tail: str) -> dict[str, Any] | None:
    if "DB75_JSON_BEGIN" not in log_tail or "DB75_JSON_END" not in log_tail:
        return None
    body = log_tail.split("DB75_JSON_BEGIN", 1)[1].split("DB75_JSON_END", 1)[0].strip()
    return json.loads(body)


def poll_job(client: ColabClient, job_id: str, timeout_s: int) -> dict[str, Any]:
    t0 = time.time()
    last: dict[str, Any] = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(7)
        last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running":
            return sanitize(last)
    return sanitize(last or {"state": "poll_timeout", "job_id": job_id})


def fetch_file(client: ColabClient, remote_name: str, local_path: Path, max_size_mb: int) -> dict[str, Any]:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    raw = client.read_file(REMOTE_OUT + "/" + remote_name, max_size_mb=max_size_mb)
    if raw is None:
        return {"path": rel(local_path), "exists": False}
    local_path.write_bytes(raw)
    return image_stat(local_path)


def fetch_outputs(client: ColabClient) -> dict[str, Any]:
    fetched: dict[str, Any] = {}
    for key, (remote_name, max_mb) in FETCH_ITEMS.items():
        local = OUT_DIR / remote_name if key in {"summary", "remote_result", "board", "roi_sheet"} else FETCH_DIR / remote_name
        fetched[key] = fetch_file(client, remote_name, local, max_mb)
    return fetched


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_remote(timeout_s: int) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    status = client.get("/status", timeout=180)
    submit = client.post(
        "/exec",
        {"cmd": ["bash", "-lc", remote_bash()], "cwd": "/content", "timeout_s": timeout_s},
        timeout=180,
    )
    job = poll_job(client, submit["job_id"], timeout_s=timeout_s)
    remote_result: dict[str, Any] | None = None
    raw = client.read_file(REMOTE_RESULT, max_size_mb=24)
    if raw is not None:
        remote_result = json.loads(raw.decode("utf-8"))
    if remote_result is None:
        remote_result = parse_json_from_log(job.get("log_tail", ""))
    if remote_result is None:
        remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    LOCAL_REMOTE_RESULT.write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")
    fetched = fetch_outputs(client)
    summary = load_json_if_exists(LOCAL_SUMMARY) or remote_result.get("summary") or {}
    run_ok = bool(remote_result.get("status") == "DB75_completed")
    complete_outputs = bool((summary or {}).get("status") == "DB75_complete")
    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db75_full_erp_source_mixed_fallback",
        "scope": {
            "remote_status_used": True,
            "remote_exec_used": True,
            "exec_count": 1,
            "fixed_cases_only": ["02a00399:0:bmw", "0bae3b5e:30:clean_far"],
            "full_erp": True,
            "raw_camera_slabs_only": True,
            "source_mixed_blending": True,
            "generated_mask": 0,
            "model_inference_used": False,
            "dit_flux_generation_used": False,
            "freeform_warp_or_apap_or_homography_used": False,
            "db32_edit": False,
            "red_promotion": False,
        },
        "runtime": {
            "secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file",
            "status": safe_status(status),
        },
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail", "cmd"}}),
        "remote_result": rel(LOCAL_REMOTE_RESULT),
        "remote_status": remote_result.get("status"),
        "summary": summary,
        "fetched_outputs": fetched,
        "output_location": rel(OUT_DIR),
        "drive_output_location": "results/layered_target_raycaster/db75_full_erp_source_mixed_fallback/",
        "decision": {
            "run_ok": run_ok,
            "complete_outputs": complete_outputs,
            "accepted_as_source_faithful_repair": False,
            "accepted_as_bosch_training_ready": False,
            "vision_check_required": True,
            "kill_criteria_hit": not bool(run_ok and complete_outputs),
        },
        "claim_boundary": [
            "DB75 is a full-ERP source-mixed presentation fallback.",
            "Inputs are raw camera slabs, but pixels in alpha/mix masks may combine multiple sources.",
            "generated_mask is zero; no model inference, inpaint, generation, flow, APAP, homography, DB32 edit, or RED promotion was used.",
            "The only possible positive claim is presentation-only; never source-faithful single-source repair or Bosch training-ready data.",
        ],
    }
    scan_text = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    manifest["outputs"] = {
        "manifest": rel(MANIFEST),
        "summary": rel(LOCAL_SUMMARY),
        "board": rel(BOARD),
        "roi_sheet": rel(ROI_SHEET),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "status": manifest["status"],
        "run_ok": run_ok,
        "complete_outputs": complete_outputs,
        "secret_hits": manifest["strict_secret_scan"]["hit_count"],
        "manifest": rel(MANIFEST),
        "board": rel(BOARD),
        "roi_sheet": rel(ROI_SHEET),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=1800)
    args = parser.parse_args()
    if not args.run_remote:
        print(json.dumps({"status": "ready", "message": "Use --run-remote after DB75 brief is active."}, indent=2))
        return
    result = run_remote(args.timeout_s)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
