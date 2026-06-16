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
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db69_user_marked_geometry_seam_reroute" / "phase1_source_label_reroute"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db69_user_marked_geometry_seam_reroute/phase1_source_label_reroute"
REMOTE_RESULT = REMOTE_OUT + "/db69_phase1_source_label_reroute_remote_result.json"
REMOTE_SUMMARY = REMOTE_OUT + "/db69_phase1_source_label_reroute_summary.json"

LOCAL_REMOTE_RESULT = OUT_DIR / "db69_phase1_source_label_reroute_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "db69_phase1_source_label_reroute_summary.json"
MANIFEST = OUT_DIR / "db69_phase1_source_label_reroute_manifest.json"
BOARD = OUT_DIR / "db69_phase1_source_label_reroute_board.jpg"
ROI_SHEET = OUT_DIR / "db69_phase1_source_label_reroute_roi_sheet.jpg"
FETCH_DIR = OUT_DIR / "fetch"

FETCH_ITEMS = {
    "summary": ("db69_phase1_source_label_reroute_summary.json", 12),
    "remote_result": ("db69_phase1_source_label_reroute_remote_result.json", 12),
    "board": ("db69_phase1_source_label_reroute_board.jpg", 40),
    "roi_sheet": ("db69_phase1_source_label_reroute_roi_sheet.jpg", 40),
    "hard_select_raw": ("db69_phase1_hard_select_raw.png", 25),
    "best_candidate": ("db69_phase1_best_source_label_candidate.png", 25),
    "source_id_before": ("db69_phase1_source_id_before.png", 12),
    "source_id_after": ("db69_phase1_source_id_after.png", 12),
    "route_changed_mask": ("db69_phase1_route_changed_mask.png", 12),
    "boundary_before": ("db69_phase1_boundary_before.png", 12),
    "boundary_after": ("db69_phase1_boundary_after.png", 12),
    "changed_overlay": ("db69_phase1_route_changed_overlay.png", 25),
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


def build_local_board(summary: dict[str, Any], fetched: dict[str, Any], status: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board = Image.new("RGB", (1800, 960), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw.text((30, 24), "DB69 Phase1 source-label-only reroute", fill=(240, 240, 240), font=font(28))
    lines = [
        f"remote_status={summary.get('status')} classification={summary.get('claim_classification')}",
        f"best_variant={summary.get('best_variant_id')} phase1_candidate_promoted={summary.get('phase1_candidate_promoted')}",
        f"runtime={safe_status(status)}",
        "Scope: raw 7-camera ERP slabs only; no warp, no blend, no IPM, no model inference, no generation.",
    ]
    y = 66
    for line in lines:
        draw.text((34, y), str(line)[:220], fill=(220, 228, 238), font=font(15))
        y += 24

    panels = [
        ("remote review board", BOARD),
        ("ROI sheet", ROI_SHEET),
        ("best candidate", FETCH_DIR / "db69_phase1_best_source_label_candidate.png"),
        ("route changed overlay", FETCH_DIR / "db69_phase1_route_changed_overlay.png"),
    ]
    for i, (label, path) in enumerate(panels):
        x = 32 + (i % 2) * 880
        yy = 170 + (i // 2) * 365
        draw.text((x, yy - 24), label, fill=(230, 230, 230), font=font(16))
        if path.exists():
            im = Image.open(path).convert("RGB")
            im.thumbnail((830, 320))
            board.paste(im, (x, yy))
            draw.rectangle((x, yy, x + im.width, yy + im.height), outline=(130, 135, 145))
        else:
            draw.rectangle((x, yy, x + 830, yy + 320), outline=(90, 95, 105))
            draw.text((x + 18, yy + 18), "missing", fill=(255, 120, 120), font=font(15))
    board.save(OUT_DIR / "db69_phase1_source_label_reroute_local_board.jpg", quality=92)


def remote_python() -> str:
    return rf'''
import json
import math
import os
import pathlib
import subprocess
import sys
import time
import traceback

REMOTE_OUT = pathlib.Path("{REMOTE_OUT}")
REMOTE_RESULT = pathlib.Path("{REMOTE_RESULT}")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [
    pathlib.Path("/content/waymo2panorama"),
    pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama"),
]
H, W = 1024, 2048
RUN_NAME = "02a00399_a000_bmw"
VARIANTS = [
    {{"id": "lw06", "line_w": 6.0, "edge_w": 0.7, "wall_w": 2.0}},
    {{"id": "lw14", "line_w": 14.0, "edge_w": 0.9, "wall_w": 3.0}},
    {{"id": "lw26", "line_w": 26.0, "edge_w": 1.2, "wall_w": 4.0}},
    {{"id": "lw40", "line_w": 40.0, "edge_w": 1.5, "wall_w": 5.0}},
]
MARKED_ROIS = {{
    "left_road_patch": (250, 515, 460, 715),
    "lower_center_road_patch": (740, 595, 1035, 745),
    "center_lane_marking": (1030, 515, 1325, 735),
    "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760),
}}
OUT = {{
    "db": "DB-69",
    "phase": "phase1_source_label_only_reroute",
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "scope": {{
        "case": "02a00399:0:bmw",
        "raw_erp_slabs": True,
        "source_label_only": True,
        "flow_warp": False,
        "virtual_center_composite": False,
        "ground_ipm": False,
        "blend": False,
        "model_inference": False,
        "inpaint_generation": False,
        "db32_edit": False,
        "red_promotion": False,
    }},
    "secret_policy": "runtime secret is used only by local executor client; remote script receives no token",
}}


def run(cmd, timeout=360, cwd=None):
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    return {{"returncode": int(proc.returncode), "tail": proc.stdout[-1200:]}}


def import_ok(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def json_safe(obj):
    import numpy as np
    if isinstance(obj, dict):
        return {{str(k): json_safe(v) for k, v in obj.items()}}
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


def find_workdir():
    for cand in WORKDIR_CANDIDATES:
        if (cand / "scripts" / "phase3" / "run_a1_streetview_pipeline.py").exists():
            return cand
    return None


def ensure_deps():
    rows = {{}}
    if not import_ok("av2"):
        rows["av2_install"] = run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=420)
    rows["av2_import_after"] = import_ok("av2")
    return rows


def source_boundary(source, valid):
    import numpy as np
    sid = source.astype(np.int32)
    boundary = np.zeros_like(valid, dtype=bool)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        shifted = np.roll(sid, shift=(dy, dx), axis=(0, 1))
        shifted_valid = np.roll(valid, shift=(dy, dx), axis=(0, 1))
        boundary |= valid & shifted_valid & (sid != shifted)
    return boundary


def dp_seam(cost):
    import numpy as np
    h, w = cost.shape
    m = cost.astype(np.float64).copy()
    back = np.zeros((h, w), np.int32)
    inf = 1e18
    for r in range(1, h):
        prev = m[r - 1]
        left = np.empty(w)
        left[0] = inf
        left[1:] = prev[:-1]
        right = np.empty(w)
        right[-1] = inf
        right[:-1] = prev[1:]
        choices = np.stack([left, prev, right], 0)
        k = choices.argmin(0)
        m[r] += choices[k, np.arange(w)]
        back[r] = np.arange(w) + (k - 1)
    seam = np.zeros(h, np.int32)
    seam[h - 1] = int(np.argmin(m[h - 1]))
    for r in range(h - 1, 0, -1):
        seam[r - 1] = back[r, seam[r]]
    return seam


def normalize_u8(x, valid=None):
    import numpy as np
    arr = x.astype(np.float32)
    vals = arr[valid] if valid is not None and bool(np.any(valid)) else arr.reshape(-1)
    lo, hi = np.percentile(vals, [2, 98])
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.uint8)
    return np.clip((arr - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def structure_components(rgb, valid):
    import cv2
    import numpy as np
    y = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)
    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    edge = normalize_u8(np.sqrt(gx * gx + gy * gy), valid).astype(np.float32) / 255.0
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = hsv[..., 1].astype(np.float32)
    val = hsv[..., 2].astype(np.float32)
    hue = hsv[..., 0].astype(np.float32)
    yy = np.arange(rgb.shape[0])[:, None]
    road_band = (yy > 390) & (yy < 780)
    lane = (((val > 168) & (sat < 95) & road_band) | ((hue > 15) & (hue < 45) & (sat > 60) & (val > 90) & road_band)).astype(np.uint8)
    lane = cv2.dilate(lane, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))).astype(bool)
    wall = ((np.abs(gy) > np.percentile(np.abs(gy[valid]), 90) if bool(np.any(valid)) else np.abs(gy) > 50) & (yy > 430) & (yy < 760))
    return {{"edge": edge, "lane": lane.astype(np.float32), "wall": wall.astype(np.float32)}}


def compose(slabs, label, valid):
    import numpy as np
    stack = np.stack([s.astype(np.uint8) for s in slabs], 0)
    idx = np.clip(label, 0, 6)[None, ..., None]
    out = np.take_along_axis(stack, idx, axis=0)[0]
    out = np.where(valid[..., None], out, 0).astype(np.uint8)
    return out


def route_labels(a1, slabs, weights, label0, valid, comps, variant):
    import cv2
    import numpy as np
    label = label0.copy()
    routed = 0
    for i, j in a1.RING_PAIRS:
        ov = (weights[i] > 1e-6) & (weights[j] > 1e-6)
        if int(ov.sum()) < 300:
            continue
        cc = a1._circular_center_col(ov, W)
        roll = (W // 2 - cc) if cc is not None else 0
        ovr = np.roll(ov, roll, 1)
        ys, xs = np.where(ovr)
        if ys.size == 0:
            continue
        r0, r1, c0, c1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
        si = np.roll(slabs[i], roll, 1)[r0:r1, c0:c1]
        sj = np.roll(slabs[j], roll, 1)[r0:r1, c0:c1]
        ovb = ovr[r0:r1, c0:c1]
        rgb_res = np.abs(si.astype(np.float32) - sj.astype(np.float32)).mean(2) / 255.0
        lane = np.roll(comps["lane"], roll, 1)[r0:r1, c0:c1]
        wall = np.roll(comps["wall"], roll, 1)[r0:r1, c0:c1]
        edge = np.roll(comps["edge"], roll, 1)[r0:r1, c0:c1]
        cost = rgb_res + variant["line_w"] * lane + variant["wall_w"] * wall + variant["edge_w"] * edge + 1000.0 * (~ovb).astype(np.float32)
        cost = cv2.GaussianBlur(cost.astype(np.float32), (0, 0), 1.35)
        seam = dp_seam(cost)
        wir = np.roll(weights[i], roll, 1)[r0:r1, c0:c1]
        wjr = np.roll(weights[j], roll, 1)[r0:r1, c0:c1]
        iex = (wir > 1e-6) & (wjr <= 1e-6)
        jex = (wjr > 1e-6) & (wir <= 1e-6)
        i_mean = np.where(iex)[1].mean() if bool(iex.any()) else 0.0
        j_mean = np.where(jex)[1].mean() if bool(jex.any()) else float(c1 - c0)
        left_cam, right_cam = (i, j) if i_mean <= j_mean else (j, i)
        lab_box = np.full((r1 - r0, c1 - c0), -1, np.int32)
        cols = np.arange(c1 - c0)[None, :]
        leftmask = cols < seam[:, None]
        lab_box[leftmask & ovb] = left_cam
        lab_box[(~leftmask) & ovb] = right_cam
        lab_full = np.full((H, W), -1, np.int32)
        lab_full[r0:r1, c0:c1] = lab_box
        lab_full = np.roll(lab_full, -roll, 1)
        m = lab_full >= 0
        label[m] = lab_full[m]
        routed += 1
    label[~valid] = 255
    return label, routed


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


def evaluate(rgb, label, label0, valid):
    import numpy as np
    boundary = source_boundary(label, valid)
    changed = (label != label0) & valid
    roi_rows = []
    roi_scores = []
    for name, roi in MARKED_ROIS.items():
        e = seam_energy(rgb, boundary, roi)
        x0, y0, x1, y1 = roi
        sl = np.s_[y0:y1, x0:x1]
        row = {{
            "roi": name,
            "seam_energy": e,
            "boundary_fraction": float(boundary[sl].mean()),
            "changed_fraction": float(changed[sl].mean()),
            "source_ids": sorted(int(v) for v in np.unique(label[sl]) if int(v) != 255),
        }}
        roi_rows.append(row)
        if e is not None:
            roi_scores.append(e)
    return {{
        "global_boundary_fraction": float(boundary.mean()),
        "global_changed_fraction": float(changed.mean()),
        "global_seam_energy": seam_energy(rgb, boundary),
        "roi_mean_seam_energy": float(np.mean(roi_scores)) if roi_scores else None,
        "marked_rois": roi_rows,
    }}, boundary, changed


def save_u8(path, arr):
    import cv2
    cv2.imwrite(str(path), arr.astype("uint8"))


def save_rgb(path, arr):
    import cv2
    cv2.imwrite(str(path), cv2.cvtColor(arr.astype("uint8"), cv2.COLOR_RGB2BGR))


def label_viz(label):
    import numpy as np
    palette = np.array([[80,220,120],[255,210,65],[255,120,80],[80,210,255],[180,110,255],[255,90,200],[120,170,255]], dtype=np.uint8)
    out = np.zeros((H, W, 3), np.uint8)
    m = label != 255
    out[m] = palette[np.clip(label[m], 0, 6)]
    return out


def overlay(rgb, mask, color, alpha=0.55):
    import numpy as np
    out = rgb.astype(np.float32).copy()
    c = np.array(color, dtype=np.float32)
    out[mask] = out[mask] * (1.0 - alpha) + c * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def draw_rois(img):
    from PIL import Image, ImageDraw
    colors = {{
        "left_road_patch": (255, 82, 82),
        "lower_center_road_patch": (255, 170, 40),
        "center_lane_marking": (80, 220, 255),
        "right_curb_sidewalk_wall_base": (170, 110, 255),
    }}
    im = Image.fromarray(img.copy())
    draw = ImageDraw.Draw(im)
    for name, roi in MARKED_ROIS.items():
        draw.rectangle(roi, outline=colors[name], width=4)
        draw.text((roi[0] + 4, max(0, roi[1] - 18)), name, fill=colors[name])
    return np.asarray(im)


def paste(board, arr, box):
    from PIL import Image, ImageDraw
    im = Image.fromarray(arr.astype("uint8"))
    x0, y0, x1, y1 = box
    im.thumbnail((x1 - x0, y1 - y0))
    px = x0 + ((x1 - x0) - im.width) // 2
    py = y0 + ((y1 - y0) - im.height) // 2
    board.paste(im, (px, py))
    ImageDraw.Draw(board).rectangle((px, py, px + im.width, py + im.height), outline=(130, 135, 145))


def build_boards(base, best, label0, label1, changed, boundary0, boundary1, summary):
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    board = Image.new("RGB", (2220, 1540), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw.text((30, 24), "DB69 Phase1 - source-label-only reroute", fill=(240, 240, 240))
    lines = [
        "Raw 7-camera ERP slabs; hard source selection only. No warp, blend, IPM, model inference, inpaint, generation.",
        f"best={{summary.get('best_variant_id')}} promoted={{summary.get('phase1_candidate_promoted')}} classification={{summary.get('claim_classification')}}",
    ]
    y = 48
    for line in lines:
        draw.text((34, y), line, fill=(225, 230, 235))
        y += 20
    change_overlay = overlay(best, changed, (255, 50, 50), 0.70)
    before_overlay = overlay(0.55 * base.astype(np.float32) + 0.45 * label_viz(label0).astype(np.float32), boundary0, (255, 0, 0), 0.85).astype(np.uint8)
    after_overlay = overlay(0.55 * best.astype(np.float32) + 0.45 * label_viz(label1).astype(np.float32), boundary1, (255, 0, 0), 0.85).astype(np.uint8)
    panels = [
        ("hard_select raw baseline", draw_rois(base)),
        ("best source-label candidate", draw_rois(best)),
        ("route changed mask overlay", draw_rois(change_overlay)),
        ("source/boundary before", draw_rois(before_overlay)),
        ("source/boundary after", draw_rois(after_overlay)),
        ("abs diff x6", draw_rois(np.clip(np.abs(best.astype(np.int16)-base.astype(np.int16))*6,0,255).astype(np.uint8))),
    ]
    for i, (label, arr) in enumerate(panels):
        x = 30 + (i % 3) * 730
        yy = 115 + (i // 3) * 455
        draw.text((x, yy - 24), label, fill=(235, 235, 235))
        paste(board, arr, (x, yy, x + 690, yy + 405))
    y = 1050
    draw.text((34, y), "Marked ROI metrics", fill=(245, 245, 245))
    y += 24
    for row in summary["best_metrics"]["marked_rois"]:
        draw.text((42, y), f"{{row['roi']}}: seam_energy={{row['seam_energy']}} boundary={{row['boundary_fraction']:.4f}} changed={{row['changed_fraction']:.4f}} sources={{row['source_ids']}}", fill=(218, 224, 232))
        y += 21
    board.save(REMOTE_OUT / "db69_phase1_source_label_reroute_board.jpg", quality=92)

    roi_sheet = Image.new("RGB", (1840, 1720), (18, 20, 25))
    draw = ImageDraw.Draw(roi_sheet)
    draw.text((30, 24), "DB69 Phase1 ROI sheet", fill=(240, 240, 240))
    for r, (name, roi) in enumerate(MARKED_ROIS.items()):
        x0, y0, x1, y1 = roi
        yb = 72 + r * 405
        draw.text((28, yb), name, fill=(245, 245, 245))
        crops = [
            ("baseline", base[y0:y1, x0:x1]),
            ("candidate", best[y0:y1, x0:x1]),
            ("changed", change_overlay[y0:y1, x0:x1]),
            ("source before", before_overlay[y0:y1, x0:x1]),
            ("source after", after_overlay[y0:y1, x0:x1]),
            ("diff x6", np.clip(np.abs(best[y0:y1, x0:x1].astype(np.int16)-base[y0:y1, x0:x1].astype(np.int16))*6,0,255).astype(np.uint8)),
        ]
        for c, (label, arr) in enumerate(crops):
            bx = 28 + c * 300
            draw.text((bx, yb + 24), label, fill=(220, 228, 238))
            paste(roi_sheet, arr, (bx, yb + 48, bx + 280, yb + 230))
    roi_sheet.save(REMOTE_OUT / "db69_phase1_source_label_reroute_roi_sheet.jpg", quality=92)


try:
    t0 = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    OUT["dependency"] = ensure_deps()
    workdir = find_workdir()
    OUT["workdir_found"] = bool(workdir)
    if workdir is None:
        raise RuntimeError("Waymo2Panorama workdir not found on remote")
    sys.path.insert(0, str(workdir / "scripts" / "phase3"))
    import cv2
    import numpy as np
    import run_a1_streetview_pipeline as a1

    loader = a1.AV2RingLoader(DATA_ROOT / a1.BMW_UUID)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[0])
    cams = {{cam: frame.calibrations[cam] for cam in a1.RING_CAMS_7}}
    slabs, weights = [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        rgb, _alpha, w = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        slabs.append(rgb.astype(np.uint8))
        weights.append(w.astype(np.float32))
    weight_stack = np.stack(weights, 0)
    valid = weight_stack.max(0) > 1e-6
    label0 = weight_stack.argmax(0).astype(np.uint8)
    label0[~valid] = 255
    base = compose(slabs, label0, valid)
    comps = structure_components(base, valid)
    base_metrics, boundary0, _base_changed = evaluate(base, label0, label0, valid)
    rows = []
    best = None
    best_key = None
    for variant in VARIANTS:
        routed_label, routed_pairs = route_labels(a1, slabs, weights, label0, valid, comps, variant)
        cand = compose(slabs, routed_label, valid)
        metrics, boundary, changed = evaluate(cand, routed_label, label0, valid)
        roi_energy = metrics["roi_mean_seam_energy"] if metrics["roi_mean_seam_energy"] is not None else 9999.0
        base_roi_energy = base_metrics["roi_mean_seam_energy"] if base_metrics["roi_mean_seam_energy"] is not None else 9999.0
        score = roi_energy + 250.0 * metrics["global_changed_fraction"]
        row = {{"variant": variant, "routed_pairs": routed_pairs, "metrics": metrics, "score": float(score), "roi_gain_vs_base": float((base_roi_energy - roi_energy) / max(base_roi_energy, 1e-6))}}
        rows.append(row)
        save_rgb(REMOTE_OUT / f"db69_phase1_candidate_{{variant['id']}}.png", cand)
        save_u8(REMOTE_OUT / f"db69_phase1_source_id_after_{{variant['id']}}.png", routed_label)
        if best is None or score < best_key[0]:
            best = (cand, routed_label, boundary, changed)
            best_key = (score, row)
    best_cand, best_label, best_boundary, best_changed = best
    best_row = best_key[1]
    base_roi_energy = base_metrics["roi_mean_seam_energy"] if base_metrics["roi_mean_seam_energy"] is not None else 9999.0
    best_roi_energy = best_row["metrics"]["roi_mean_seam_energy"] if best_row["metrics"]["roi_mean_seam_energy"] is not None else 9999.0
    gain = (base_roi_energy - best_roi_energy) / max(base_roi_energy, 1e-6)
    promoted = bool(gain > 0.03 and best_row["metrics"]["global_changed_fraction"] < 0.20)
    summary = {{
        "status": "db69_phase1_source_label_reroute_complete",
        "claim_classification": "source-selection candidate pending vision" if promoted else "diagnostic/evidence-only or rejected by metrics",
        "phase1_candidate_promoted": promoted,
        "best_variant_id": best_row["variant"]["id"],
        "baseline_metrics": base_metrics,
        "best_metrics": best_row["metrics"],
        "best_roi_gain_vs_base": float(gain),
        "variants": rows,
        "scope": OUT["scope"],
        "runtime_s": round(time.time() - t0, 2),
    }}
    save_rgb(REMOTE_OUT / "db69_phase1_hard_select_raw.png", base)
    save_rgb(REMOTE_OUT / "db69_phase1_best_source_label_candidate.png", best_cand)
    save_u8(REMOTE_OUT / "db69_phase1_source_id_before.png", label0)
    save_u8(REMOTE_OUT / "db69_phase1_source_id_after.png", best_label)
    save_u8(REMOTE_OUT / "db69_phase1_route_changed_mask.png", (best_changed.astype(np.uint8) * 255))
    save_u8(REMOTE_OUT / "db69_phase1_boundary_before.png", (boundary0.astype(np.uint8) * 255))
    save_u8(REMOTE_OUT / "db69_phase1_boundary_after.png", (best_boundary.astype(np.uint8) * 255))
    save_rgb(REMOTE_OUT / "db69_phase1_route_changed_overlay.png", overlay(best_cand, best_changed, (255, 50, 50), 0.7))
    build_boards(base, best_cand, label0, best_label, best_changed, boundary0, best_boundary, summary)
    (REMOTE_OUT / "db69_phase1_source_label_reroute_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    OUT["status"] = "db69_phase1_source_label_reroute_completed"
    OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "db69_phase1_source_label_reroute_failed_or_blocked"
    OUT["error"] = {{"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB69_PHASE1_JSON_BEGIN")
    print(json.dumps(json_safe(OUT), sort_keys=True, separators=(",", ":")))
    print("DB69_PHASE1_JSON_END")
'''


def remote_bash() -> str:
    code = remote_python().encode("utf-8")
    b64 = base64.b64encode(code).decode("ascii")
    return "python - <<'PY'\nimport base64\ncode = base64.b64decode('" + b64 + "').decode('utf-8')\nexec(code)\nPY"


def poll_job(client: ColabClient, job_id: str, timeout_s: int) -> dict[str, Any]:
    t0 = time.time()
    last: dict[str, Any] = {}
    while time.time() - t0 < timeout_s + 90:
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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_remote(timeout_s: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    status = sanitize(client.get("/status", timeout=180))
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash()], "cwd": "/content", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, str(submit["job_id"]), timeout_s=timeout_s)
    raw = client.read_file(REMOTE_RESULT, max_size_mb=12)
    remote: dict[str, Any]
    if raw is None:
        remote = {"status": "missing_remote_result", "job": job}
    else:
        remote = json.loads(raw.decode("utf-8"))
        remote["job"] = job
    LOCAL_REMOTE_RESULT.write_text(json.dumps(sanitize(remote), indent=2, ensure_ascii=False), encoding="utf-8")
    fetched = fetch_outputs(client)
    return sanitize(remote), fetched, status


def build_manifest(remote: dict[str, Any], fetched: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    summary = read_json(LOCAL_SUMMARY)
    manifest: dict[str, Any] = {
        "db": "DB-69",
        "phase": "phase1_source_label_only_reroute",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "remote_status": remote.get("status"),
        "summary_status": summary.get("status"),
        "claim_classification": summary.get("claim_classification", "unknown"),
        "phase1_candidate_promoted": summary.get("phase1_candidate_promoted", False),
        "best_variant_id": summary.get("best_variant_id"),
        "scope": {
            "one_status_one_exec": True,
            "bmw_only": True,
            "source_label_only": True,
            "raw_erp_slabs": True,
            "flow_warp": False,
            "virtual_center_composite": False,
            "ground_ipm": False,
            "blend": False,
            "model_inference": False,
            "inpaint_generation": False,
            "db32_edit": False,
            "red_promotion": False,
            "source_faithful_permission_changed": False,
        },
        "runtime_status_pre_exec": safe_status(status),
        "summary": summary,
        "fetched": fetched,
        "local_outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
            "roi_sheet": rel(ROI_SHEET),
            "fetch_dir": rel(FETCH_DIR),
            "local_board": rel(OUT_DIR / "db69_phase1_source_label_reroute_local_board.jpg"),
        },
        "decision": {
            "accepted_as_source_faithful_repair": False,
            "needs_vision_review": bool(summary),
            "next_route_if_rejected": "local geometry/ground-plane alignment may require a separate brief after source-label-only reroute evidence.",
        },
    }
    text = json.dumps(sanitize(manifest), ensure_ascii=False)
    hits = secret_hits(text)
    manifest["strict_secret_scan"] = {"hit_count": sum(int(h["count"]) for h in hits), "hits": hits}
    MANIFEST.write_text(json.dumps(sanitize(manifest), indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=1800)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.run_remote:
        remote, fetched, status = run_remote(args.timeout_s)
    else:
        remote = read_json(LOCAL_REMOTE_RESULT)
        fetched = {}
        status = remote.get("runtime_status_pre_exec", {})
    summary = read_json(LOCAL_SUMMARY)
    build_local_board(summary, fetched, status)
    manifest = build_manifest(remote, fetched, status)
    print(
        json.dumps(
            {
                "status": manifest["remote_status"],
                "summary_status": manifest["summary_status"],
                "claim_classification": manifest["claim_classification"],
                "phase1_candidate_promoted": manifest["phase1_candidate_promoted"],
                "best_variant_id": manifest["best_variant_id"],
                "board": rel(BOARD),
                "roi_sheet": rel(ROI_SHEET),
                "manifest": rel(MANIFEST),
                "secret_hits": manifest["strict_secret_scan"]["hit_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
