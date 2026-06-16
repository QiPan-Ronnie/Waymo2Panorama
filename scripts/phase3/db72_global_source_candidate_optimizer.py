from __future__ import annotations

import argparse
import base64
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, rel, safe_status, sanitize


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db72_global_source_candidate_optimizer"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db72_global_source_candidate_optimizer"
REMOTE_RESULT = REMOTE_OUT + "/db72_remote_result.json"
REMOTE_SUMMARY = REMOTE_OUT + "/db72_batch_summary.json"

LOCAL_REMOTE_RESULT = OUT_DIR / "db72_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "db72_batch_summary.json"
MANIFEST = OUT_DIR / "db72_manifest.json"
BOARD = OUT_DIR / "db72_full_review_board.jpg"
ROI_SHEET = OUT_DIR / "db72_same_roi_comparison_sheet.jpg"
FETCH_DIR = OUT_DIR / "fetch"

FETCH_ITEMS = {
    "summary": ("db72_batch_summary.json", 16),
    "remote_result": ("db72_remote_result.json", 16),
    "board": ("db72_full_review_board.jpg", 60),
    "roi_sheet": ("db72_same_roi_comparison_sheet.jpg", 60),
    "bmw_baseline": ("02a00399_a000_bmw_hard_select_raw.png", 30),
    "bmw_candidate": ("02a00399_a000_bmw_optimized_source_owned_rgb.png", 30),
    "bmw_source_before": ("02a00399_a000_bmw_source_id_before.png", 16),
    "bmw_source_after": ("02a00399_a000_bmw_optimized_source_id_map.png", 16),
    "bmw_source_change": ("02a00399_a000_bmw_source_changed_mask.png", 16),
    "bmw_abstain": ("02a00399_a000_bmw_abstain_mask.png", 16),
    "bmw_boundary": ("02a00399_a000_bmw_seam_boundary_map.png", 16),
    "bmw_risk_components": ("02a00399_a000_bmw_risk_components_board.jpg", 45),
    "bmw_candidate_stack": ("02a00399_a000_bmw_source_candidate_preview_grid.jpg", 45),
    "bmw_valid_count": ("02a00399_a000_bmw_source_valid_count_map.png", 16),
    "bmw_route_cost": ("02a00399_a000_bmw_route_cost_components_board.jpg", 45),
    "bmw_inventory": ("02a00399_a000_bmw_candidate_stack_inventory.json", 16),
    "bmw_marked_report": ("02a00399_a000_bmw_marked_roi_report.json", 16),
    "bmw_claim": ("02a00399_a000_bmw_claim.json", 8),
    "clean_baseline": ("0bae3b5e_a030_clean_far_hard_select_raw.png", 30),
    "clean_candidate": ("0bae3b5e_a030_clean_far_optimized_source_owned_rgb.png", 30),
    "clean_source_change": ("0bae3b5e_a030_clean_far_source_changed_mask.png", 16),
    "clean_abstain": ("0bae3b5e_a030_clean_far_abstain_mask.png", 16),
    "clean_boundary": ("0bae3b5e_a030_clean_far_seam_boundary_map.png", 16),
    "clean_inventory": ("0bae3b5e_a030_clean_far_candidate_stack_inventory.json", 16),
    "clean_marked_report": ("0bae3b5e_a030_clean_far_marked_roi_report.json", 16),
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


def paste_thumb(board: Image.Image, path: Path, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    if not path.exists():
        draw.rectangle(box, outline=(100, 100, 100), fill=(34, 36, 42))
        draw.text((x0 + 12, y0 + 12), "missing", fill=(255, 150, 120), font=font(14))
        return
    with Image.open(path) as img:
        im = img.convert("RGB")
        im.thumbnail((x1 - x0, y1 - y0))
        px = x0 + ((x1 - x0) - im.width) // 2
        py = y0 + ((y1 - y0) - im.height) // 2
        board.paste(im, (px, py))
        draw.rectangle((px, py, px + im.width, py + im.height), outline=(150, 155, 165))


def build_local_board(summary: dict[str, Any], status: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board = Image.new("RGB", (1900, 1320), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw.text((30, 24), "DB72 global source-candidate optimizer", fill=(242, 242, 242), font=font(27))
    bmw = (summary.get("by_case") or {}).get("02a00399_a000_bmw", {})
    clean = (summary.get("by_case") or {}).get("0bae3b5e_a030_clean_far", {})
    lines = [
        f"status={summary.get('status')} classification={summary.get('claim_classification')}",
        f"runtime={safe_status(status)}",
        f"BMW best={bmw.get('best_variant_id')} gain={bmw.get('best_roi_gain_vs_base')} changed={bmw.get('best_changed_fraction')} abstain={bmw.get('best_abstain_fraction')}",
        f"Clean best={clean.get('best_variant_id')} degraded={clean.get('clean_control_degraded')} changed={clean.get('best_changed_fraction')}",
        "Scope: raw 7-camera ERP slabs + source-label optimizer only; no warp/IPM/inpaint/generation/model.",
    ]
    y = 66
    for line in lines:
        draw.text((34, y), str(line)[:220], fill=(220, 228, 238), font=font(14))
        y += 22

    panels = [
        ("remote review board", BOARD),
        ("BMW ROI sheet", ROI_SHEET),
        ("BMW DB72 source-owned RGB", FETCH_DIR / "02a00399_a000_bmw_optimized_source_owned_rgb.png"),
        ("BMW source/risk route board", FETCH_DIR / "02a00399_a000_bmw_route_cost_components_board.jpg"),
        ("BMW candidate stack preview", FETCH_DIR / "02a00399_a000_bmw_source_candidate_preview_grid.jpg"),
        ("Clean DB72 source-owned RGB", FETCH_DIR / "0bae3b5e_a030_clean_far_optimized_source_owned_rgb.png"),
    ]
    for i, (label, path) in enumerate(panels):
        x = 30 + (i % 2) * 940
        yy = 185 + (i // 2) * 390
        draw.text((x, yy - 24), label, fill=(232, 232, 232), font=font(16))
        paste_thumb(board, path, (x, yy, x + 900, yy + 340))
    board.save(OUT_DIR / "db72_local_review_board.jpg", quality=92)


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
ABSTAIN = 7
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
    {
        "id": "source_only_t16",
        "tile": 16,
        "weight_w": 0.38,
        "photo_w": 0.82,
        "lidar_w": 0.36,
        "change_w": 0.16,
        "pair_w": 1.55,
        "struct_w": 4.00,
        "color_w": 1.25,
        "risk_w": 0.58,
        "abstain_base": 12.00,
        "abstain_risk_drop": 0.00,
        "iters": 8,
    },
    {
        "id": "source_only_t32",
        "tile": 32,
        "weight_w": 0.40,
        "photo_w": 0.78,
        "lidar_w": 0.36,
        "change_w": 0.18,
        "pair_w": 2.05,
        "struct_w": 4.40,
        "color_w": 1.20,
        "risk_w": 0.62,
        "abstain_base": 12.00,
        "abstain_risk_drop": 0.00,
        "iters": 8,
    },
    {
        "id": "balanced_t16",
        "tile": 16,
        "weight_w": 0.34,
        "photo_w": 0.80,
        "lidar_w": 0.36,
        "change_w": 0.10,
        "pair_w": 1.15,
        "struct_w": 3.20,
        "color_w": 1.20,
        "risk_w": 0.55,
        "abstain_base": 2.25,
        "abstain_risk_drop": 0.92,
        "iters": 7,
    },
    {
        "id": "smooth_t16",
        "tile": 16,
        "weight_w": 0.32,
        "photo_w": 0.75,
        "lidar_w": 0.32,
        "change_w": 0.12,
        "pair_w": 1.85,
        "struct_w": 4.20,
        "color_w": 1.35,
        "risk_w": 0.65,
        "abstain_base": 2.45,
        "abstain_risk_drop": 1.00,
        "iters": 8,
    },
    {
        "id": "conservative_t16",
        "tile": 16,
        "weight_w": 0.42,
        "photo_w": 0.70,
        "lidar_w": 0.42,
        "change_w": 0.26,
        "pair_w": 1.45,
        "struct_w": 4.60,
        "color_w": 1.15,
        "risk_w": 0.72,
        "abstain_base": 2.75,
        "abstain_risk_drop": 0.82,
        "iters": 7,
    },
]
OUT = {
    "db": "DB-72",
    "phase": "phase0_phase1_global_source_candidate_optimizer",
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "scope": {
        "cases": [c[0] for c in CASES],
        "full_erp": True,
        "raw_erp_slabs": True,
        "source_label_optimizer": True,
        "abstain_label": True,
        "source_owned_rgb_only": True,
        "flow_warp": False,
        "apap_or_homography": False,
        "ground_ipm_rgb_replacement": False,
        "model_inference": False,
        "inpaint_generation": False,
        "dense_renderer": False,
        "db32_edit": False,
        "red_promotion": False,
    },
    "secret_policy": "runtime secret is used only by local executor client; remote script receives no token",
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
        if (cand / "scripts" / "phase3" / "run_a1_streetview_pipeline.py").exists():
            return cand
    return None


def json_safe(obj):
    import numpy as np
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


def normalize_u8(x, valid=None):
    import numpy as np
    arr = x.astype(np.float32)
    vals = arr[valid] if valid is not None and bool(np.any(valid)) else arr.reshape(-1)
    lo, hi = np.percentile(vals, [2, 98])
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.uint8)
    return np.clip((arr - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


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


def label_viz(label):
    import numpy as np
    palette = np.array([
        [80, 220, 120],
        [255, 210, 65],
        [255, 120, 80],
        [80, 210, 255],
        [180, 110, 255],
        [255, 90, 200],
        [120, 170, 255],
        [35, 35, 35],
    ], dtype=np.uint8)
    out = np.zeros((label.shape[0], label.shape[1], 3), np.uint8)
    m = label != 255
    out[m] = palette[np.clip(label[m], 0, ABSTAIN)]
    return out


def overlay(rgb, mask, color, alpha=0.58):
    import numpy as np
    out = rgb.astype(np.float32).copy()
    c = np.array(color, dtype=np.float32)
    out[mask] = out[mask] * (1.0 - alpha) + c * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def draw_rois(img):
    from PIL import Image, ImageDraw
    colors = {
        "left_road_patch": (255, 82, 82),
        "lower_center_road_patch": (255, 170, 40),
        "center_lane_marking": (80, 220, 255),
        "right_curb_sidewalk_wall_base": (170, 110, 255),
    }
    im = Image.fromarray(img.copy())
    draw = ImageDraw.Draw(im)
    for name, roi in MARKED_ROIS.items():
        draw.rectangle(roi, outline=colors[name], width=4)
        draw.text((roi[0] + 4, max(0, roi[1] - 18)), name, fill=colors[name])
    return np.asarray(im)


def paste(board, arr, box):
    from PIL import Image, ImageDraw
    im = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    x0, y0, x1, y1 = box
    im.thumbnail((x1 - x0, y1 - y0))
    px = x0 + ((x1 - x0) - im.width) // 2
    py = y0 + ((y1 - y0) - im.height) // 2
    board.paste(im, (px, py))
    ImageDraw.Draw(board).rectangle((px, py, px + im.width, py + im.height), outline=(135, 140, 150))


def stack_grid(items, width=420):
    from PIL import Image, ImageDraw
    thumbs = []
    for label, arr in items:
        im = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
        im.thumbnail((width, 260))
        panel = Image.new("RGB", (width, im.height + 34), (18, 20, 25))
        panel.paste(im, ((width - im.width) // 2, 30))
        d = ImageDraw.Draw(panel)
        d.text((8, 8), label, fill=(235, 235, 235))
        thumbs.append(panel)
    rows = []
    for i in range(0, len(thumbs), 4):
        row = thumbs[i:i+4]
        h = max(p.height for p in row)
        canvas = Image.new("RGB", (width * len(row), h), (18, 20, 25))
        x = 0
        for p in row:
            canvas.paste(p, (x, 0))
            x += width
        rows.append(canvas)
    total_h = sum(r.height for r in rows)
    out = Image.new("RGB", (max(r.width for r in rows), total_h), (18, 20, 25))
    y = 0
    for r in rows:
        out.paste(r, (0, y))
        y += r.height
    return np.asarray(out)


def source_boundary(source, valid):
    import numpy as np
    sid = source.astype(np.int32)
    src_valid = valid & (sid != ABSTAIN) & (sid != 255)
    boundary = np.zeros_like(valid, dtype=bool)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        shifted = np.roll(sid, shift=(dy, dx), axis=(0, 1))
        shifted_valid = np.roll(src_valid, shift=(dy, dx), axis=(0, 1))
        boundary |= src_valid & shifted_valid & (sid != shifted)
    return boundary


def rgb_to_y(rgb):
    import numpy as np
    x = rgb.astype(np.float32)
    return 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]


def structure_components(rgb, valid):
    import cv2
    import numpy as np
    y = rgb_to_y(rgb)
    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    edge = normalize_u8(np.sqrt(gx * gx + gy * gy), valid).astype(np.float32) / 255.0
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = hsv[..., 1].astype(np.float32)
    val = hsv[..., 2].astype(np.float32)
    hue = hsv[..., 0].astype(np.float32)
    yy = np.arange(rgb.shape[0])[:, None]
    road_band = (yy > 390) & (yy < 780)
    lane = (((val > 168) & (sat < 98) & road_band) | ((hue > 15) & (hue < 45) & (sat > 58) & (val > 88) & road_band))
    lane = cv2.dilate(lane.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0
    wall_base = ((np.abs(gy) > (np.percentile(np.abs(gy[valid]), 88) if bool(np.any(valid)) else 45)) & (yy > 430) & (yy < 780))
    curb = ((edge > 0.34) & road_band & (yy > 500))
    protected = lane | wall_base | curb
    low_texture = (edge < 0.06) & valid & road_band
    risk = np.clip(0.36 * edge + 0.78 * lane.astype(np.float32) + 0.68 * wall_base.astype(np.float32) + 0.52 * curb.astype(np.float32) + 0.22 * low_texture.astype(np.float32), 0, 1)
    return {
        "edge": edge.astype(np.float32),
        "lane": lane.astype(np.float32),
        "wall_base": wall_base.astype(np.float32),
        "curb": curb.astype(np.float32),
        "low_texture": low_texture.astype(np.float32),
        "protected": protected.astype(bool),
        "structure_risk": risk.astype(np.float32),
    }


def compose(slabs, label, valid, label0=None):
    import numpy as np
    stack = np.stack([s.astype(np.uint8) for s in slabs], 0)
    if label0 is None:
        fallback = np.zeros(label.shape, dtype=np.uint8)
    else:
        fallback = np.clip(label0, 0, 6).astype(np.uint8)
    src = label.copy()
    abstain = src == ABSTAIN
    src[abstain] = fallback[abstain]
    idx = np.clip(src, 0, 6)[None, ..., None]
    out = np.take_along_axis(stack, idx, axis=0)[0]
    out = np.where(valid[..., None], out, 0).astype(np.uint8)
    return out


def seam_energy(rgb, boundary, roi=None):
    import numpy as np
    diff = np.abs(rgb.astype(np.float32) - np.roll(rgb.astype(np.float32), 1, axis=1)).mean(2)
    mask = boundary.copy()
    if roi is not None:
        x0, y0, x1, y1 = roi
        local = np.zeros(mask.shape, bool)
        local[y0:y1, x0:x1] = True
        mask &= local
    if not bool(mask.any()):
        return None
    return float(diff[mask].mean())


def component_stats(label, valid):
    import cv2
    import numpy as np
    total_valid = max(int(valid.sum()), 1)
    rows = []
    small = 0
    ncomp = 0
    for sid in range(7):
        m = valid & (label == sid)
        if not bool(m.any()):
            continue
        n, labs, stats, _cent = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
        for cid in range(1, n):
            area = int(stats[cid, cv2.CC_STAT_AREA])
            ncomp += 1
            if area < 256:
                small += area
            if len(rows) < 80:
                rows.append({"source": sid, "area_px": area, "bbox": [int(stats[cid, 0]), int(stats[cid, 1]), int(stats[cid, 2]), int(stats[cid, 3])]})
    return {
        "component_count": int(ncomp),
        "small_component_fraction": float(small / total_valid),
        "largest_components_sample": sorted(rows, key=lambda r: r["area_px"], reverse=True)[:20],
    }


def evaluate(rgb, label, label0, valid, comps):
    import numpy as np
    changed = (label != label0) & valid & (label != ABSTAIN)
    abstain = (label == ABSTAIN) & valid
    eval_label = label.copy()
    eval_label[abstain] = label0[abstain]
    boundary = source_boundary(eval_label, valid)
    protected = comps["protected"]
    roi_rows = []
    roi_scores = []
    for name, roi in MARKED_ROIS.items():
        e = seam_energy(rgb, boundary, roi)
        x0, y0, x1, y1 = roi
        sl = np.s_[y0:y1, x0:x1]
        row = {
            "roi": name,
            "seam_energy": e,
            "boundary_fraction": float(boundary[sl].mean()),
            "protected_boundary_fraction": float((boundary & protected)[sl].mean()),
            "changed_fraction": float(changed[sl].mean()),
            "abstain_fraction": float(abstain[sl].mean()),
            "source_ids": sorted(int(v) for v in np.unique(eval_label[sl]) if int(v) not in (ABSTAIN, 255)),
        }
        roi_rows.append(row)
        if e is not None:
            roi_scores.append(e)
    comp = component_stats(eval_label, valid)
    return {
        "global_boundary_fraction": float(boundary.mean()),
        "global_changed_fraction": float(changed.mean()),
        "global_abstain_fraction": float(abstain.mean()),
        "global_protected_boundary_fraction": float((boundary & protected).mean()),
        "global_seam_energy": seam_energy(rgb, boundary),
        "roi_mean_seam_energy": float(np.mean(roi_scores)) if roi_scores else None,
        "component_count": comp["component_count"],
        "small_component_fraction": comp["small_component_fraction"],
        "marked_rois": roi_rows,
    }, boundary, changed, abstain, comp


def cost_features(slabs, weights, label0, valid, comps, lidar_label, lidar_valid, visible_count):
    import numpy as np
    k = len(slabs)
    stack = np.stack([s.astype(np.float32) for s in slabs], 0)
    vstack = np.stack([w > 1e-6 for w in weights], 0)
    maxw = np.maximum(np.max(np.stack(weights, 0), axis=0), 1e-6)
    weight_cost = np.zeros((k, H, W), np.float32)
    photo_cost = np.zeros((k, H, W), np.float32)
    lidar_cost = np.zeros((k, H, W), np.float32)
    valid_count = vstack.sum(axis=0).astype(np.uint8)

    gains = []
    global_med = np.array([128.0, 128.0, 128.0], np.float32)
    all_vals = stack[vstack].reshape(-1, 3) if bool(vstack.any()) else stack.reshape(-1, 3)
    if all_vals.size:
        global_med = np.percentile(all_vals, 50, axis=0).astype(np.float32)
    norm = stack.copy()
    for i in range(k):
        m = vstack[i]
        if bool(m.any()):
            med = np.percentile(stack[i][m], 50, axis=0).astype(np.float32)
            gain = np.clip(global_med / np.maximum(med, 20.0), 0.72, 1.32)
        else:
            gain = np.ones(3, np.float32)
        gains.append(gain.tolist())
        norm[i] = np.clip(stack[i] * gain[None, None, :], 0, 255)
    count = np.maximum(valid_count.astype(np.float32), 1.0)
    mean_rgb = (norm * vstack[..., None]).sum(axis=0) / count[..., None]

    for i in range(k):
        wnorm = np.clip(weights[i] / maxw, 0, 1)
        weight_cost[i] = 1.0 - wnorm
        photo_cost[i] = np.abs(norm[i] - mean_rgb).mean(axis=2) / 255.0
        photo_cost[i][valid_count <= 1] = 0.16
        if lidar_label is not None:
            match = lidar_valid & (lidar_label == i)
            conflict = lidar_valid & (lidar_label != i)
            lidar_cost[i][match] = -0.16
            lidar_cost[i][conflict] = 0.28
            lidar_cost[i][visible_count == 0] += 0.10
        invalid = ~vstack[i]
        weight_cost[i][invalid] = 1e5
        photo_cost[i][invalid] = 1e5
        lidar_cost[i][invalid] = 1e5

    return {
        "valid_stack": vstack,
        "valid_count": valid_count,
        "weight_cost": weight_cost,
        "photo_cost": photo_cost,
        "lidar_cost": lidar_cost,
        "photometric_gains": gains,
        "mean_rgb": mean_rgb,
    }


def tile_slices(tile):
    rows = []
    for y0 in range(0, H, tile):
        for x0 in range(0, W, tile):
            rows.append((x0, y0, min(W, x0 + tile), min(H, y0 + tile)))
    return rows


def tile_majority(arr, sl, default=ABSTAIN):
    import numpy as np
    vals = arr[sl].reshape(-1)
    vals = vals[(vals >= 0) & (vals <= ABSTAIN)]
    if vals.size == 0:
        return default
    binc = np.bincount(vals.astype(np.int32), minlength=ABSTAIN + 1)
    return int(np.argmax(binc))


def optimize_labels(slabs, weights, label0, valid, comps, cfeat, lidar_label, lidar_valid, variant):
    import numpy as np
    tile = int(variant["tile"])
    boxes = tile_slices(tile)
    ny = math.ceil(H / tile)
    nx = math.ceil(W / tile)
    ntiles = len(boxes)
    k = 7
    unary = np.full((ntiles, ABSTAIN + 1), 1e5, np.float32)
    allowed = np.zeros((ntiles, ABSTAIN + 1), bool)
    struct_tile = np.zeros(ntiles, np.float32)
    risk_tile = np.zeros(ntiles, np.float32)
    for idx, (x0, y0, x1, y1) in enumerate(boxes):
        sl = np.s_[y0:y1, x0:x1]
        vm = valid[sl]
        if not bool(vm.any()):
            allowed[idx, ABSTAIN] = True
            unary[idx, ABSTAIN] = 0.05
            continue
        struct = comps["structure_risk"][sl]
        struct_tile[idx] = float(np.mean(struct[vm])) if bool(vm.any()) else 0.0
        risk = struct_tile[idx]
        if lidar_label is not None:
            lv = lidar_valid[sl]
            risk += 0.35 * (1.0 - float(lv[vm].mean()) if bool(vm.any()) else 1.0)
        risk_tile[idx] = float(np.clip(risk, 0, 1.4))
        for sid in range(k):
            sv = cfeat["valid_stack"][sid][sl]
            frac = float(sv[vm].mean()) if bool(vm.any()) else 0.0
            if frac < 0.18:
                continue
            allowed[idx, sid] = True
            base = (
                variant["weight_w"] * float(np.mean(cfeat["weight_cost"][sid][sl][vm])) +
                variant["photo_w"] * float(np.mean(cfeat["photo_cost"][sid][sl][vm])) +
                variant["lidar_w"] * float(np.mean(cfeat["lidar_cost"][sid][sl][vm]))
            )
            current = tile_majority(label0, sl, default=sid)
            if sid != current:
                base += variant["change_w"]
            unary[idx, sid] = float(base)
        allowed[idx, ABSTAIN] = True
        abstain = variant["abstain_base"] - variant["abstain_risk_drop"] * min(risk_tile[idx], 1.0)
        if np.sum(allowed[idx, :7]) == 0:
            abstain = 0.08
        unary[idx, ABSTAIN] = float(max(0.18, abstain))

    labels = np.full(ntiles, ABSTAIN, np.uint8)
    for idx, (x0, y0, x1, y1) in enumerate(boxes):
        sl = np.s_[y0:y1, x0:x1]
        cur = tile_majority(label0, sl)
        if cur < 7 and allowed[idx, cur]:
            labels[idx] = cur
        else:
            labels[idx] = int(np.argmin(unary[idx]))

    stack = np.stack([s.astype(np.float32) for s in slabs], 0)

    def boundary_pair_cost(aidx, bidx, la, lb, direction):
        if la == lb:
            return 0.0
        if la == ABSTAIN or lb == ABSTAIN:
            return 0.28 + 0.85 * max(struct_tile[aidx], struct_tile[bidx])
        ax0, ay0, ax1, ay1 = boxes[aidx]
        bx0, by0, bx1, by1 = boxes[bidx]
        if direction == "h":
            y0, y1 = max(ay0, by0), min(ay1, by1)
            if y1 <= y0:
                return 0.0
            ca = ax1 - 1
            cb = bx0
            va = cfeat["valid_stack"][la, y0:y1, ca]
            vb = cfeat["valid_stack"][lb, y0:y1, cb]
            m = va & vb
            if not bool(m.any()):
                color = 1.0
            else:
                color = float(np.abs(stack[la, y0:y1, ca][m] - stack[lb, y0:y1, cb][m]).mean() / 255.0)
            struct = float(max(np.mean(comps["structure_risk"][y0:y1, ca]), np.mean(comps["structure_risk"][y0:y1, cb])))
        else:
            x0, x1 = max(ax0, bx0), min(ax1, bx1)
            if x1 <= x0:
                return 0.0
            ra = ay1 - 1
            rb = by0
            va = cfeat["valid_stack"][la, ra, x0:x1]
            vb = cfeat["valid_stack"][lb, rb, x0:x1]
            m = va & vb
            if not bool(m.any()):
                color = 1.0
            else:
                color = float(np.abs(stack[la, ra, x0:x1][m] - stack[lb, rb, x0:x1][m]).mean() / 255.0)
            struct = float(max(np.mean(comps["structure_risk"][ra, x0:x1]), np.mean(comps["structure_risk"][rb, x0:x1])))
        nonadj = 0.16 if abs(int(la) - int(lb)) not in (1, 6) else 0.0
        return variant["pair_w"] * (0.10 + variant["color_w"] * color + variant["struct_w"] * struct + variant["risk_w"] * max(risk_tile[aidx], risk_tile[bidx]) + nonadj)

    neighbors = [[] for _ in range(ntiles)]
    for ty in range(ny):
        for tx in range(nx):
            idx = ty * nx + tx
            if tx + 1 < nx:
                j = idx + 1
                neighbors[idx].append((j, "h"))
                neighbors[j].append((idx, "h"))
            if ty + 1 < ny:
                j = idx + nx
                neighbors[idx].append((j, "v"))
                neighbors[j].append((idx, "v"))

    history = []
    for it in range(int(variant["iters"])):
        changes = 0
        energy = 0.0
        order = range(ntiles) if it % 2 == 0 else range(ntiles - 1, -1, -1)
        for idx in order:
            cand_labels = np.flatnonzero(allowed[idx])
            best_l = int(labels[idx])
            best_e = float("inf")
            for lab in cand_labels:
                e = float(unary[idx, lab])
                for j, direction in neighbors[idx]:
                    e += boundary_pair_cost(idx, j, int(lab), int(labels[j]), direction)
                if e < best_e:
                    best_e = e
                    best_l = int(lab)
            energy += best_e
            if best_l != int(labels[idx]):
                labels[idx] = best_l
                changes += 1
        history.append({"iter": it, "changes": int(changes), "energy_proxy": float(energy)})
        if changes == 0:
            break

    label = np.full((H, W), ABSTAIN, np.uint8)
    for idx, (x0, y0, x1, y1) in enumerate(boxes):
        lab = int(labels[idx])
        sl = np.s_[y0:y1, x0:x1]
        if lab < 7:
            m = cfeat["valid_stack"][lab][sl] & valid[sl]
            label[sl][m] = lab
            label[sl][(~m) & valid[sl]] = ABSTAIN
        else:
            label[sl][valid[sl]] = ABSTAIN
    label[~valid] = 255

    return label, {
        "tile": tile,
        "tile_grid": [int(ny), int(nx)],
        "tile_count": int(ntiles),
        "iterations": history,
        "tile_label_counts": {str(i): int((labels == i).sum()) for i in range(ABSTAIN + 1)},
        "allowed_source_tile_counts": {str(i): int(allowed[:, i].sum()) for i in range(ABSTAIN + 1)},
    }


def optional_lidar_evidence(case_spec, log_dir, anchor_ts, frame, images, Ks, Ts, workdir):
    try:
        import numpy as np
        from depth_visibility_seam_probe import _parse_case
        from test_lidar_zbuffer_seam import _winner_label
        from waymo2panorama.depth.lidar_to_erp_depth import load_lidar_sweep_nearest_to_ts, project_lidar_to_erp_depth
        from waymo2panorama.projection.lidar_zbuffer_layer import build_ring_zbuffers, render_lidar_surface_to_erp
        pts, sweep_ts, lidar_delta_ms = load_lidar_sweep_nearest_to_ts(log_dir, anchor_ts, max_delta_ms=75.0)
        depth_map, _depth_summary = project_lidar_to_erp_depth(pts, erp_hw=(H, W), min_range_m=0.5, max_range_m=80.0, densify_radius_px=8, fill_far_m=1000.0)
        zbuffers = build_ring_zbuffers(pts, images, Ks, Ts, min_range_m=0.5, max_range_m=80.0, dilation_px=5)
        lidar_render = render_lidar_surface_to_erp(images, Ks, Ts, depth_map, zbuffers, depth_support_max_m=120.0, min_cam_cos=0.03, z_tolerance_abs_m=0.9, z_tolerance_rel=0.05)
        lidar_label, lidar_valid = _winner_label(lidar_render.weights)
        return {
            "status": "lidar_zbuffer_evidence_complete",
            "lidar_label": lidar_label.astype(np.uint8),
            "lidar_valid": lidar_valid.astype(bool),
            "support": lidar_render.support_mask.astype(bool),
            "visible_count": lidar_render.visible_count.astype(np.uint8),
            "lidar_delta_ms": float(lidar_delta_ms),
        }
    except Exception as exc:
        import numpy as np
        return {
            "status": "lidar_zbuffer_evidence_blocked",
            "error": {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-2000:]},
            "lidar_label": np.full((H, W), 255, np.uint8),
            "lidar_valid": np.zeros((H, W), bool),
            "support": np.zeros((H, W), bool),
            "visible_count": np.zeros((H, W), np.uint8),
            "lidar_delta_ms": None,
        }


def load_case(case_spec, workdir):
    import numpy as np
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    loader = AV2RingLoader(log_dir)
    anchor_ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)
    slabs, weights, images, Ks, Ts = [], [], [], [], []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        slabs.append(rgb.astype(np.uint8))
        weights.append(w.astype(np.float32))
        images.append(frame.images[cam])
        Ks.append(calib.K)
        Ts.append(calib.T_ego_cam)
    wstack = np.stack(weights, 0)
    valid = wstack.max(0) > 1e-6
    label0 = wstack.argmax(0).astype(np.uint8)
    label0[~valid] = 255
    base = compose(slabs, label0, valid)
    lidar = optional_lidar_evidence(case_spec, log_dir, anchor_ts, frame, images, Ks, Ts, workdir)
    return run_name, slabs, weights, valid, label0, base, lidar


def build_case_boards(run_name, base, best_rgb, label0, best_label, boundary0, best_boundary, changed, abstain, comps, cfeat, summary, comp_stats):
    import numpy as np
    from PIL import Image, ImageDraw
    before_overlay = overlay(0.55 * base.astype(np.float32) + 0.45 * label_viz(label0).astype(np.float32), boundary0, (255, 0, 0), 0.85).astype(np.uint8)
    after_overlay = overlay(0.55 * best_rgb.astype(np.float32) + 0.45 * label_viz(best_label).astype(np.float32), best_boundary, (255, 0, 0), 0.85).astype(np.uint8)
    change_overlay = overlay(best_rgb, changed, (255, 50, 50), 0.70)
    abstain_overlay = overlay(best_rgb, abstain, (45, 45, 45), 0.70)
    risk_rgb = np.dstack([
        normalize_u8(comps["structure_risk"], None),
        cfeat["valid_count"].astype(np.uint8) * 36,
        normalize_u8(cfeat["photo_cost"].min(axis=0), valid=None),
    ]).astype(np.uint8)
    valid_count_viz = normalize_u8(cfeat["valid_count"].astype(np.float32), None)
    save_rgb(REMOTE_OUT / f"{run_name}_hard_select_raw.png", base)
    save_rgb(REMOTE_OUT / f"{run_name}_optimized_source_owned_rgb.png", best_rgb)
    save_u8(REMOTE_OUT / f"{run_name}_source_id_before.png", label0)
    save_u8(REMOTE_OUT / f"{run_name}_optimized_source_id_map.png", best_label)
    save_u8(REMOTE_OUT / f"{run_name}_source_changed_mask.png", changed.astype(np.uint8) * 255)
    save_u8(REMOTE_OUT / f"{run_name}_abstain_mask.png", abstain.astype(np.uint8) * 255)
    save_u8(REMOTE_OUT / f"{run_name}_seam_boundary_map.png", best_boundary.astype(np.uint8) * 255)
    save_u8(REMOTE_OUT / f"{run_name}_source_valid_count_map.png", valid_count_viz)
    save_rgb(REMOTE_OUT / f"{run_name}_source_id_before_after.png", stack_grid([
        ("source before", label_viz(label0)),
        ("source after", label_viz(best_label)),
        ("boundary before", before_overlay),
        ("boundary after", after_overlay),
    ], width=500), quality=90)
    save_rgb(REMOTE_OUT / f"{run_name}_source_candidate_preview_grid.jpg", stack_grid([(f"cam{i} valid={float((cfeat['valid_stack'][i]).mean()):.3f}", slabs_i) for i, slabs_i in enumerate(current_slabs_global)], width=420), quality=88)
    save_rgb(REMOTE_OUT / f"{run_name}_risk_components_board.jpg", stack_grid([
        ("structure risk R / valid-count G / photo B", risk_rgb),
        ("lane proxy", np.dstack([comps["lane"] * 255, comps["lane"] * 255, comps["lane"] * 255])),
        ("wall-base proxy", np.dstack([comps["wall_base"] * 255, comps["wall_base"] * 255, comps["wall_base"] * 255])),
        ("curb proxy", np.dstack([comps["curb"] * 255, comps["curb"] * 255, comps["curb"] * 255])),
        ("valid count", np.dstack([valid_count_viz] * 3)),
        ("min photo cost", np.dstack([normalize_u8(cfeat["photo_cost"].min(axis=0), None)] * 3)),
    ], width=500), quality=88)
    save_rgb(REMOTE_OUT / f"{run_name}_route_cost_components_board.jpg", stack_grid([
        ("hard_select raw", draw_rois(base) if run_name.startswith("02a00399") else base),
        ("DB72 optimized raw-source", draw_rois(best_rgb) if run_name.startswith("02a00399") else best_rgb),
        ("changed overlay", draw_rois(change_overlay) if run_name.startswith("02a00399") else change_overlay),
        ("abstain overlay", draw_rois(abstain_overlay) if run_name.startswith("02a00399") else abstain_overlay),
        ("source before+boundary", draw_rois(before_overlay) if run_name.startswith("02a00399") else before_overlay),
        ("source after+boundary", draw_rois(after_overlay) if run_name.startswith("02a00399") else after_overlay),
    ], width=560), quality=90)


def roi_sheet_for_bmw(base, best_rgb, label0, best_label, changed, abstain, boundary0, best_boundary):
    import numpy as np
    from PIL import Image, ImageDraw
    before_overlay = overlay(0.55 * base.astype(np.float32) + 0.45 * label_viz(label0).astype(np.float32), boundary0, (255, 0, 0), 0.85).astype(np.uint8)
    after_overlay = overlay(0.55 * best_rgb.astype(np.float32) + 0.45 * label_viz(best_label).astype(np.float32), best_boundary, (255, 0, 0), 0.85).astype(np.uint8)
    change_overlay = overlay(best_rgb, changed, (255, 50, 50), 0.70)
    abstain_overlay = overlay(best_rgb, abstain, (45, 45, 45), 0.70)
    sheet = Image.new("RGB", (1840, 1720), (18, 20, 25))
    draw = ImageDraw.Draw(sheet)
    draw.text((30, 24), "DB72 BMW marked ROI sheet", fill=(240, 240, 240))
    for r, (name, roi) in enumerate(MARKED_ROIS.items()):
        x0, y0, x1, y1 = roi
        yb = 72 + r * 405
        draw.text((28, yb), name, fill=(245, 245, 245))
        crops = [
            ("hard_select", base[y0:y1, x0:x1]),
            ("DB72 source-owned", best_rgb[y0:y1, x0:x1]),
            ("changed", change_overlay[y0:y1, x0:x1]),
            ("abstain", abstain_overlay[y0:y1, x0:x1]),
            ("source before", before_overlay[y0:y1, x0:x1]),
            ("source after", after_overlay[y0:y1, x0:x1]),
        ]
        for c, (label, arr) in enumerate(crops):
            bx = 28 + c * 300
            draw.text((bx, yb + 24), label, fill=(220, 228, 238))
            paste(sheet, arr, (bx, yb + 48, bx + 280, yb + 230))
    sheet.save(REMOTE_OUT / "db72_same_roi_comparison_sheet.jpg", quality=92)


def run_one_case(case_spec, workdir):
    import numpy as np
    t0 = time.time()
    run_name, slabs, weights, valid, label0, base, lidar = load_case(case_spec, workdir)
    global current_slabs_global
    current_slabs_global = slabs
    comps = structure_components(base, valid)
    cfeat = cost_features(slabs, weights, label0, valid, comps, lidar["lidar_label"], lidar["lidar_valid"], lidar["visible_count"])
    base_metrics, boundary0, _base_changed, _base_abstain, _base_comp = evaluate(base, label0, label0, valid, comps)
    rows = []
    best_pack = None
    best_score = None
    for variant in VARIANTS:
        opt_label, opt_debug = optimize_labels(slabs, weights, label0, valid, comps, cfeat, lidar["lidar_label"], lidar["lidar_valid"], variant)
        cand = compose(slabs, opt_label, valid, label0=label0)
        metrics, boundary, changed, abstain, comp = evaluate(cand, opt_label, label0, valid, comps)
        base_roi = base_metrics["roi_mean_seam_energy"] if base_metrics["roi_mean_seam_energy"] is not None else 9999.0
        roi = metrics["roi_mean_seam_energy"] if metrics["roi_mean_seam_energy"] is not None else 9999.0
        gain = float((base_roi - roi) / max(base_roi, 1e-6))
        penalty = (
            85.0 * max(0.0, metrics["small_component_fraction"] - 0.015) +
            55.0 * metrics["global_protected_boundary_fraction"] +
            18.0 * metrics["global_abstain_fraction"] +
            8.0 * metrics["global_changed_fraction"]
        )
        score = float(roi + penalty)
        row = {
            "variant": variant["id"],
            "metrics": metrics,
            "optimizer_debug": opt_debug,
            "component_stats": comp,
            "roi_gain_vs_base": gain,
            "score": score,
        }
        rows.append(row)
        save_rgb(REMOTE_OUT / f"{run_name}_candidate_{variant['id']}.png", cand)
        save_u8(REMOTE_OUT / f"{run_name}_source_id_after_{variant['id']}.png", opt_label)
        if best_pack is None or score < best_score:
            best_pack = (variant, opt_label, cand, metrics, boundary, changed, abstain, comp, row)
            best_score = score
    variant, best_label, best_rgb, best_metrics, best_boundary, best_changed, best_abstain, best_comp, best_row = best_pack
    build_case_boards(run_name, base, best_rgb, label0, best_label, boundary0, best_boundary, best_changed, best_abstain, comps, cfeat, best_metrics, best_comp)
    if run_name.startswith("02a00399"):
        roi_sheet_for_bmw(base, best_rgb, label0, best_label, best_changed, best_abstain, boundary0, best_boundary)
    inventory = {
        "case": run_name,
        "valid_pixel_fraction": float(valid.mean()),
        "valid_count_distribution": {str(i): int((cfeat["valid_count"] == i).sum()) for i in range(8)},
        "camera_valid_fraction": {str(i): float(cfeat["valid_stack"][i].mean()) for i in range(7)},
        "photometric_gains_for_cost_only": cfeat["photometric_gains"],
        "lidar_evidence_status": lidar["status"],
        "lidar_visible_fraction": float((lidar["visible_count"] > 0).mean()),
        "lidar_support_fraction": float(lidar["support"].mean()),
        "raw_uv_validity_proxy": "render_camera_to_erp valid/FOV weights; exact raw_uv sidecar not emitted by current renderer API",
    }
    (REMOTE_OUT / f"{run_name}_candidate_stack_inventory.json").write_text(json.dumps(json_safe(inventory), indent=2), encoding="utf-8")
    report = {
        "case": run_name,
        "best_variant_id": variant["id"],
        "baseline_metrics": base_metrics,
        "best_metrics": best_metrics,
        "best_roi_gain_vs_base": best_row["roi_gain_vs_base"],
        "best_changed_fraction": best_metrics["global_changed_fraction"],
        "best_abstain_fraction": best_metrics["global_abstain_fraction"],
        "best_small_component_fraction": best_metrics["small_component_fraction"],
        "best_protected_boundary_fraction": best_metrics["global_protected_boundary_fraction"],
        "variants": rows,
        "inventory": inventory,
        "runtime_s": round(time.time() - t0, 2),
    }
    (REMOTE_OUT / f"{run_name}_marked_roi_report.json").write_text(json.dumps(json_safe(report), indent=2), encoding="utf-8")
    claim = {
        "case": run_name,
        "source_owned_rgb": True,
        "selected_pixels_copied_from_raw_slabs": True,
        "abstain_pixels_kept_as_hard_select_for_visualization": True,
        "generated_mask": 0,
        "warp_vector_map": "absent",
        "operator_map": "source_selection_or_abstain_only",
        "sensor_truth_claim": "source-owned candidate pending vision; not Bosch/source-faithful repair unless sidecars and vision pass",
    }
    (REMOTE_OUT / f"{run_name}_claim.json").write_text(json.dumps(claim, indent=2), encoding="utf-8")
    return report


def build_full_board(summary):
    from PIL import Image, ImageDraw
    board = Image.new("RGB", (2320, 1700), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw.text((30, 24), "DB72 - full ERP source-candidate stack + structure-aware source-label optimizer", fill=(240, 240, 240))
    lines = [
        "Raw slabs only for RGB. No warp, IPM, homography, flow, inpaint, generation, dense renderer, model inference, or DB32 edit.",
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
        ("BMW DB72 source-owned", REMOTE_OUT / "02a00399_a000_bmw_optimized_source_owned_rgb.png"),
        ("BMW route/cost sidecars", REMOTE_OUT / "02a00399_a000_bmw_route_cost_components_board.jpg"),
        ("BMW risk components", REMOTE_OUT / "02a00399_a000_bmw_risk_components_board.jpg"),
        ("clean hard_select", REMOTE_OUT / "0bae3b5e_a030_clean_far_hard_select_raw.png"),
        ("clean DB72 source-owned", REMOTE_OUT / "0bae3b5e_a030_clean_far_optimized_source_owned_rgb.png"),
        ("clean route/cost sidecars", REMOTE_OUT / "0bae3b5e_a030_clean_far_route_cost_components_board.jpg"),
        ("BMW candidate stack", REMOTE_OUT / "02a00399_a000_bmw_source_candidate_preview_grid.jpg"),
    ]
    for i, (label, path) in enumerate(panels):
        x = 30 + (i % 2) * 1150
        yy = 160 + (i // 2) * 395
        draw.text((x, yy - 24), label, fill=(235, 235, 235))
        if pathlib.Path(path).exists():
            import cv2
            arr = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
            paste(board, arr, (x, yy, x + 1110, yy + 350))
        else:
            draw.rectangle((x, yy, x + 1110, yy + 350), outline=(100, 100, 100))
    board.save(REMOTE_OUT / "db72_full_review_board.jpg", quality=92)


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
        clean["best_metrics"]["small_component_fraction"] > clean["baseline_metrics"]["small_component_fraction"] + 0.015 or
        clean["best_metrics"]["global_abstain_fraction"] > 0.025
    )
    bmw_gain = bmw["best_roi_gain_vs_base"]
    bmw_max_roi_abstain = max(float(r.get("abstain_fraction") or 0.0) for r in bmw["best_metrics"]["marked_rois"])
    bmw_ok_metric = bool(
        bmw_gain >= 0.03 and
        bmw["best_metrics"]["small_component_fraction"] <= 0.035 and
        bmw["best_metrics"]["global_protected_boundary_fraction"] <= max(0.0008, bmw["baseline_metrics"]["global_protected_boundary_fraction"] + 0.0004) and
        bmw["best_metrics"]["global_abstain_fraction"] <= 0.012 and
        bmw_max_roi_abstain <= 0.025
    )
    classification = "source-owned candidate pending vision" if bmw_ok_metric and not clean_degraded else "diagnostic/source-owned candidate or rejected pending vision"
    summary = {
        "status": "db72_phase0_phase1_complete",
        "claim_classification": classification,
        "source_faithful_repair_claim_allowed": False,
        "phase2_operator_eligibility_allowed": bool(bmw_ok_metric and not clean_degraded),
        "clean_control_degraded": clean_degraded,
        "by_case": by_case,
        "scope": OUT["scope"],
        "runtime_s": round(time.time() - t0, 2),
    }
    by_case["0bae3b5e_a030_clean_far"]["clean_control_degraded"] = clean_degraded
    build_full_board(summary)
    (REMOTE_OUT / "db72_batch_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    OUT["status"] = "db72_phase0_phase1_completed"
    OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "db72_phase0_phase1_failed_or_blocked"
    OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB72_JSON_BEGIN")
    print(json.dumps(json_safe(OUT), sort_keys=True, separators=(",", ":")))
    print("DB72_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__REMOTE_RESULT__", REMOTE_RESULT)


def remote_bash() -> str:
    code_b64 = base64.b64encode(remote_python().encode("utf-8")).decode("ascii")
    return (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{code_b64}').decode('utf-8')\n"
        "exec(compile(code, '<db72_global_source_candidate_optimizer_remote>', 'exec'))\n"
        "PY"
    )


def parse_json_from_log(log_tail: str) -> dict[str, Any] | None:
    if "DB72_JSON_BEGIN" not in log_tail or "DB72_JSON_END" not in log_tail:
        return None
    body = log_tail.split("DB72_JSON_BEGIN", 1)[1].split("DB72_JSON_END", 1)[0].strip()
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
    run_ok = bool(remote_result.get("status") == "db72_phase0_phase1_completed")
    complete_outputs = bool((summary or {}).get("status") == "db72_phase0_phase1_complete")
    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db72_global_source_candidate_optimizer",
        "scope": {
            "remote_status_used": True,
            "remote_exec_used": True,
            "exec_count": 1,
            "fixed_cases_only": ["02a00399:0:bmw", "0bae3b5e:30:clean_far"],
            "full_erp_source_candidate_stack": True,
            "abstain_label": True,
            "source_owned_rgb_only": True,
            "a100_used_or_needed": False,
            "model_inference_used": False,
            "vggt_used": False,
            "dit_flux_generation_used": False,
            "warp_or_ipm_or_homography_used": False,
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
        "drive_output_location": "results/layered_target_raycaster/db72_global_source_candidate_optimizer/",
        "decision": {
            "run_ok": run_ok,
            "complete_outputs": complete_outputs,
            "accepted_as_source_faithful_repair": False,
            "vision_check_required": True,
            "phase2_operator_eligibility_allowed_by_metrics": bool((summary or {}).get("phase2_operator_eligibility_allowed")),
            "kill_criteria_hit": not bool(run_ok and complete_outputs),
        },
        "claim_boundary": [
            "DB72 Phase0/Phase1 is source-owned candidate stack plus label optimization.",
            "RGB candidate pixels are copied from raw camera ERP slabs; abstain pixels are left as hard_select for visualization and marked by abstain_mask.",
            "No warp, IPM, APAP, homography, flow, inpaint, generation, dense renderer, VGGT/Pi3/model inference, DB32 edit, or RED promotion was used.",
            "Vision review decides whether this is a source-owned candidate, diagnostic, presentation-only, or rejected.",
        ],
    }
    scan_text = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if summary:
        build_local_board(summary, status)
    manifest["outputs"] = {
        "manifest": rel(MANIFEST),
        "summary": rel(LOCAL_SUMMARY),
        "board": rel(BOARD),
        "roi_sheet": rel(ROI_SHEET),
        "local_board": rel(OUT_DIR / "db72_local_review_board.jpg"),
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
    parser.add_argument("--timeout-s", type=int, default=3600)
    args = parser.parse_args()
    if not args.run_remote:
        print(json.dumps({"status": "ready", "message": "Use --run-remote after DB72 brief is active."}, indent=2))
        return
    result = run_remote(args.timeout_s)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
