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
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db70_protected_ground_plane_alignment"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db70_protected_ground_plane_alignment"
REMOTE_RESULT = REMOTE_OUT + "/db70_protected_ground_plane_remote_result.json"

LOCAL_REMOTE_RESULT = OUT_DIR / "db70_protected_ground_plane_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "db70_protected_ground_plane_summary.json"
MANIFEST = OUT_DIR / "db70_protected_ground_plane_manifest.json"
BOARD = OUT_DIR / "db70_protected_ground_plane_board.jpg"
ROI_SHEET = OUT_DIR / "db70_protected_ground_plane_roi_sheet.jpg"
FETCH_DIR = OUT_DIR / "fetch"

FETCH_ITEMS = {
    "summary": ("db70_protected_ground_plane_summary.json", 12),
    "remote_result": ("db70_protected_ground_plane_remote_result.json", 12),
    "board": ("db70_protected_ground_plane_board.jpg", 50),
    "roi_sheet": ("db70_protected_ground_plane_roi_sheet.jpg", 50),
    "hard_select_raw": ("db70_hard_select_raw.png", 25),
    "db69_reference": ("db70_db69_source_label_reference.png", 25),
    "best_candidate": ("db70_best_protected_ground_plane_candidate.png", 25),
    "ground_effect_mask": ("db70_best_ground_effect_mask.png", 12),
    "protected_veto_mask": ("db70_protected_veto_mask.png", 12),
    "changed_overlay": ("db70_best_changed_overlay.png", 25),
    "source_id_used": ("db70_best_source_id_used.png", 12),
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


def build_local_board(summary: dict[str, Any], status: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board = Image.new("RGB", (1800, 960), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw.text((30, 24), "DB70 protected ground-plane local alignment", fill=(240, 240, 240), font=font(27))
    lines = [
        f"remote_status={summary.get('status')} classification={summary.get('claim_classification')}",
        f"best_variant={summary.get('best_variant_id')} accepted_by_metrics={summary.get('accepted_by_metrics')}",
        f"runtime={safe_status(status)}",
        "Scope: raw slabs + LiDAR ground plane; narrow ROI/seam mask; car/wall/tall/out-of-FOV vetoes.",
    ]
    y = 66
    for line in lines:
        draw.text((34, y), str(line)[:220], fill=(220, 228, 238), font=font(15))
        y += 24
    panels = [
        ("remote review board", BOARD),
        ("ROI sheet", ROI_SHEET),
        ("best candidate", FETCH_DIR / "db70_best_protected_ground_plane_candidate.png"),
        ("changed overlay", FETCH_DIR / "db70_best_changed_overlay.png"),
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
    board.save(OUT_DIR / "db70_protected_ground_plane_local_board.jpg", quality=92)


def remote_python() -> str:
    return rf'''
import json
import math
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
MARKED_ROIS = {{
    "left_road_patch": (250, 515, 460, 715),
    "lower_center_road_patch": (740, 595, 1035, 745),
    "center_lane_marking": (1030, 515, 1325, 735),
    "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760),
}}
VARIANTS = [
    {{"id": "hard_r10_a070", "base": "hard", "radius": 10, "alpha": 0.70, "sigma": 1.5}},
    {{"id": "hard_r18_a060", "base": "hard", "radius": 18, "alpha": 0.60, "sigma": 2.0}},
    {{"id": "route_r10_a070", "base": "route", "radius": 10, "alpha": 0.70, "sigma": 1.5}},
    {{"id": "route_r18_a060", "base": "route", "radius": 18, "alpha": 0.60, "sigma": 2.0}},
]
OUT = {{
    "db": "DB-70",
    "phase": "protected_ground_plane_local_alignment",
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "scope": {{
        "case": "02a00399:0:bmw",
        "raw_erp_slabs": True,
        "lidar_ground_plane": True,
        "protected_local_mask": True,
        "full_frame_ground_replacement": False,
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


def ensure_deps():
    rows = {{}}
    if not import_ok("av2"):
        rows["av2_install"] = run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=420)
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
        left = np.empty(w); left[0] = inf; left[1:] = prev[:-1]
        right = np.empty(w); right[-1] = inf; right[:-1] = prev[1:]
        choices = np.stack([left, prev, right], 0)
        k = choices.argmin(0)
        m[r] += choices[k, np.arange(w)]
        back[r] = np.arange(w) + (k - 1)
    seam = np.zeros(h, np.int32)
    seam[h - 1] = int(np.argmin(m[h - 1]))
    for r in range(h - 1, 0, -1):
        seam[r - 1] = back[r, seam[r]]
    return seam


def compose(slabs, label, valid):
    import numpy as np
    stack = np.stack([s.astype(np.uint8) for s in slabs], 0)
    idx = np.clip(label, 0, 6)[None, ..., None]
    out = np.take_along_axis(stack, idx, axis=0)[0]
    return np.where(valid[..., None], out, 0).astype(np.uint8)


def normalize(x, valid):
    import numpy as np
    vals = x[valid] if bool(np.any(valid)) else x.reshape(-1)
    lo, hi = np.percentile(vals, [2, 98])
    if hi <= lo:
        return np.zeros_like(x, dtype=np.float32)
    return np.clip((x - lo) / (hi - lo), 0, 1).astype(np.float32)


def structure(rgb, valid):
    import cv2
    import numpy as np
    gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edge = normalize(np.sqrt(gx * gx + gy * gy), valid)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = hsv[..., 1].astype(np.float32)
    val = hsv[..., 2].astype(np.float32)
    hue = hsv[..., 0].astype(np.float32)
    yy = np.arange(H)[:, None]
    road_band = (yy > 390) & (yy < 790)
    lane = (((val > 168) & (sat < 95) & road_band) | ((hue > 15) & (hue < 45) & (sat > 60) & (val > 90) & road_band))
    lane = cv2.dilate(lane.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))).astype(bool)
    horiz = normalize(np.abs(gy), valid)
    wall = (horiz > 0.62) & (yy > 430) & (yy < 760) & (~lane)
    dark_wall = (val < 65) & (yy > 390) & (yy < 760) & (~lane)
    return {{"edge": edge, "lane": lane, "wall": wall | dark_wall}}


def route_label_lw06(a1, slabs, weights, label0, valid, comps):
    import cv2
    import numpy as np
    label = label0.copy()
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
        lane = np.roll(comps["lane"], roll, 1)[r0:r1, c0:c1].astype(np.float32)
        wall = np.roll(comps["wall"], roll, 1)[r0:r1, c0:c1].astype(np.float32)
        edge = np.roll(comps["edge"], roll, 1)[r0:r1, c0:c1]
        cost = rgb_res + 6.0 * lane + 2.0 * wall + 0.7 * edge + 1000.0 * (~ovb).astype(np.float32)
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
    label[~valid] = 255
    return label.astype(np.uint8)


def lidar_tall_mask(pts, ground):
    import cv2
    import numpy as np
    if not ground:
        return np.zeros((H, W), bool)
    n = np.asarray(ground["n"], dtype=np.float64)
    d = float(ground["d"])
    h = pts @ n - d
    rr = np.linalg.norm(pts, axis=1)
    tp = pts[(h > 0.45) & (rr < 45.0)]
    mask = np.zeros((H, W), np.uint8)
    if len(tp):
        x, y, z = tp[:, 0], tp[:, 1], tp[:, 2]
        th = np.arctan2(y, x)
        ph = np.arctan2(z, np.hypot(x, y))
        u = ((np.pi - th) / (2 * np.pi) * W).astype(int) % W
        v = ((np.pi / 2 - ph) / np.pi * H).astype(int).clip(0, H - 1)
        mask[v, u] = 1
    return cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (27, 27))).astype(bool)


def roi_mask():
    import numpy as np
    m = np.zeros((H, W), bool)
    for x0, y0, x1, y1 in MARKED_ROIS.values():
        m[y0:y1, x0:x1] = True
    return m


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


def evaluate(rgb, label, base, mask, valid):
    import numpy as np
    boundary = source_boundary(label, valid)
    changed = (np.abs(rgb.astype(np.int16) - base.astype(np.int16)).max(2) > 1) & valid
    rows, scores = [], []
    for name, roi in MARKED_ROIS.items():
        x0, y0, x1, y1 = roi
        sl = np.s_[y0:y1, x0:x1]
        e = seam_energy(rgb, boundary, roi)
        rows.append({{
            "roi": name,
            "seam_energy": e,
            "boundary_fraction": float(boundary[sl].mean()),
            "effect_fraction": float(mask[sl].mean()),
            "changed_fraction": float(changed[sl].mean()),
        }})
        if e is not None:
            scores.append(e)
    return {{
        "roi_mean_seam_energy": float(np.mean(scores)) if scores else None,
        "global_changed_fraction": float(changed.mean()),
        "global_effect_fraction": float(mask.mean()),
        "marked_rois": rows,
    }}


def save_u8(path, arr):
    import cv2
    cv2.imwrite(str(path), arr.astype("uint8"))


def save_rgb(path, arr):
    import cv2
    cv2.imwrite(str(path), cv2.cvtColor(arr.astype("uint8"), cv2.COLOR_RGB2BGR))


def overlay(rgb, mask, color, alpha=0.55):
    import numpy as np
    out = rgb.astype(np.float32).copy()
    c = np.array(color, dtype=np.float32)
    out[mask] = out[mask] * (1.0 - alpha) + c * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def label_viz(label):
    import numpy as np
    palette = np.array([[80,220,120],[255,210,65],[255,120,80],[80,210,255],[180,110,255],[255,90,200],[120,170,255]], dtype=np.uint8)
    out = np.zeros((H, W, 3), np.uint8)
    m = label != 255
    out[m] = palette[np.clip(label[m], 0, 6)]
    return out


def draw_rois(img):
    import numpy as np
    from PIL import Image, ImageDraw
    colors = {{
        "left_road_patch": (255, 82, 82),
        "lower_center_road_patch": (255, 170, 40),
        "center_lane_marking": (80, 220, 255),
        "right_curb_sidewalk_wall_base": (170, 110, 255),
    }}
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
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


def build_boards(hard, route_ref, best, best_mask, veto, label, summary):
    import numpy as np
    from PIL import Image, ImageDraw
    board = Image.new("RGB", (2220, 1540), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw.text((30, 24), "DB70 - protected ground-plane local alignment", fill=(240, 240, 240))
    draw.text((34, 50), f"best={{summary.get('best_variant_id')}} accepted_by_metrics={{summary.get('accepted_by_metrics')}} classification={{summary.get('claim_classification')}}", fill=(225, 230, 235))
    changed_overlay = overlay(best, best_mask, (255, 50, 50), 0.65)
    veto_viz = np.zeros((H, W, 3), np.uint8)
    veto_viz[veto] = (255, 70, 70)
    source_viz = (0.55 * best.astype(np.float32) + 0.45 * label_viz(label).astype(np.float32)).astype(np.uint8)
    panels = [
        ("hard-select raw", draw_rois(hard)),
        ("DB69 source-label reference", draw_rois(route_ref)),
        ("DB70 best candidate", draw_rois(best)),
        ("ground effect mask overlay", draw_rois(changed_overlay)),
        ("protected veto mask", draw_rois(veto_viz)),
        ("source id used", draw_rois(source_viz)),
    ]
    for i, (label_text, arr) in enumerate(panels):
        x = 30 + (i % 3) * 730
        y = 115 + (i // 3) * 455
        draw.text((x, y - 24), label_text, fill=(235, 235, 235))
        paste(board, arr, (x, y, x + 690, y + 405))
    y = 1050
    draw.text((34, y), "Marked ROI metrics", fill=(245, 245, 245))
    y += 24
    for row in summary["best_metrics"]["marked_rois"]:
        draw.text((42, y), f"{{row['roi']}}: seam_energy={{row['seam_energy']}} effect={{row['effect_fraction']:.4f}} changed={{row['changed_fraction']:.4f}}", fill=(218, 224, 232))
        y += 21
    board.save(REMOTE_OUT / "db70_protected_ground_plane_board.jpg", quality=92)

    sheet = Image.new("RGB", (1840, 1720), (18, 20, 25))
    draw = ImageDraw.Draw(sheet)
    draw.text((30, 24), "DB70 ROI sheet", fill=(240, 240, 240))
    for r, (name, roi) in enumerate(MARKED_ROIS.items()):
        x0, y0, x1, y1 = roi
        yb = 72 + r * 405
        draw.text((28, yb), name, fill=(245, 245, 245))
        crops = [
            ("hard", hard[y0:y1, x0:x1]),
            ("DB69", route_ref[y0:y1, x0:x1]),
            ("DB70", best[y0:y1, x0:x1]),
            ("effect", changed_overlay[y0:y1, x0:x1]),
            ("veto", veto_viz[y0:y1, x0:x1]),
            ("diff x6", np.clip(np.abs(best[y0:y1, x0:x1].astype(np.int16)-hard[y0:y1, x0:x1].astype(np.int16))*6,0,255).astype(np.uint8)),
        ]
        for c, (label_text, arr) in enumerate(crops):
            bx = 28 + c * 300
            draw.text((bx, yb + 24), label_text, fill=(220, 228, 238))
            paste(sheet, arr, (bx, yb + 48, bx + 280, yb + 230))
    sheet.save(REMOTE_OUT / "db70_protected_ground_plane_roi_sheet.jpg", quality=92)


try:
    import cv2
    import numpy as np
    t0 = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    OUT["dependency"] = ensure_deps()
    workdir = find_workdir()
    OUT["workdir_found"] = bool(workdir)
    if workdir is None:
        raise RuntimeError("Waymo2Panorama workdir not found on remote")
    sys.path.insert(0, str(workdir / "scripts" / "phase3"))
    import run_a1_streetview_pipeline as a1

    loader = a1.AV2RingLoader(DATA_ROOT / a1.BMW_UUID)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[0])
    pts, _labels, _dms = a1.load_lidar_feather(DATA_ROOT / a1.BMW_UUID, ts[0], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, _facades = a1.fit_planes_p3(pts)
    if ground is None:
        raise RuntimeError("ground plane fit failed")
    cams = {{cam: frame.calibrations[cam] for cam in a1.RING_CAMS_7}}
    slabs, weights, g_slabs = [], [], []
    conv_g = a1.build_plane_convergence(ground, [], (H, W))
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        rgb, _alpha, w = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        grgb, _ga, _gw = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=conv_g)
        slabs.append(rgb.astype(np.uint8))
        weights.append(w.astype(np.float32))
        g_slabs.append(grgb.astype(np.uint8))
    weight_stack = np.stack(weights, 0)
    valid = weight_stack.max(0) > 1e-6
    label_hard = weight_stack.argmax(0).astype(np.uint8)
    label_hard[~valid] = 255
    hard = compose(slabs, label_hard, valid)
    comps = structure(hard, valid)
    label_route = route_label_lw06(a1, slabs, weights, label_hard, valid, comps)
    route_ref = compose(slabs, label_route, valid)
    roi = roi_mask()
    tall = lidar_tall_mask(pts, ground)
    ground_valid = (conv_g > 3.0) & (conv_g < 35.0) & valid
    base_veto = tall | comps["wall"] | (~ground_valid)
    rows = []
    hard_metrics = evaluate(hard, label_hard, hard, np.zeros((H, W), bool), valid)
    route_metrics = evaluate(route_ref, label_route, hard, np.zeros((H, W), bool), valid)
    best = None
    best_score = None
    for variant in VARIANTS:
        label = label_hard if variant["base"] == "hard" else label_route
        base = hard if variant["base"] == "hard" else route_ref
        boundary = source_boundary(label, valid)
        band = cv2.dilate(boundary.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (variant["radius"]*2+1, variant["radius"]*2+1))).astype(bool)
        mask = band & roi & (~base_veto)
        g_pano = compose(g_slabs, label, valid)
        mask &= g_pano.sum(2) > 0
        alpha = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), variant["sigma"])
        alpha = np.clip(alpha * float(variant["alpha"]), 0, float(variant["alpha"]))
        cand = np.where(valid[..., None], base.astype(np.float32) * (1 - alpha[..., None]) + g_pano.astype(np.float32) * alpha[..., None], 0).clip(0, 255).astype(np.uint8)
        metrics = evaluate(cand, label, hard, mask, valid)
        base_energy = hard_metrics["roi_mean_seam_energy"] if variant["base"] == "hard" else route_metrics["roi_mean_seam_energy"]
        cand_energy = metrics["roi_mean_seam_energy"] if metrics["roi_mean_seam_energy"] is not None else 9999.0
        gain = (base_energy - cand_energy) / max(base_energy, 1e-6)
        veto_touch = float((mask & base_veto).mean())
        score = cand_energy + 400.0 * metrics["global_changed_fraction"] + 10000.0 * veto_touch
        row = {{"variant": variant, "metrics": metrics, "gain_vs_variant_base": float(gain), "score": float(score), "veto_touch_fraction": veto_touch}}
        rows.append(row)
        save_rgb(REMOTE_OUT / f"db70_candidate_{{variant['id']}}.png", cand)
        save_u8(REMOTE_OUT / f"db70_effect_mask_{{variant['id']}}.png", (mask.astype(np.uint8) * 255))
        if best is None or score < best_score:
            best = (cand, label, mask, row)
            best_score = score
    best_cand, best_label, best_mask, best_row = best
    hard_energy = hard_metrics["roi_mean_seam_energy"] or 9999.0
    best_energy = best_row["metrics"]["roi_mean_seam_energy"] or 9999.0
    gain_hard = (hard_energy - best_energy) / max(hard_energy, 1e-6)
    accepted = bool(gain_hard > 0.04 and best_row["metrics"]["global_changed_fraction"] < 0.04 and best_mask.mean() < 0.02)
    summary = {{
        "status": "db70_protected_ground_plane_complete",
        "claim_classification": "protected source-backed geometry candidate pending vision" if accepted else "diagnostic/evidence-only or rejected by metrics",
        "accepted_by_metrics": accepted,
        "best_variant_id": best_row["variant"]["id"],
        "hard_metrics": hard_metrics,
        "db69_reference_metrics": route_metrics,
        "best_metrics": best_row["metrics"],
        "best_gain_vs_hard": float(gain_hard),
        "variants": rows,
        "scope": OUT["scope"],
        "runtime_s": round(time.time() - t0, 2),
    }}
    save_rgb(REMOTE_OUT / "db70_hard_select_raw.png", hard)
    save_rgb(REMOTE_OUT / "db70_db69_source_label_reference.png", route_ref)
    save_rgb(REMOTE_OUT / "db70_best_protected_ground_plane_candidate.png", best_cand)
    save_u8(REMOTE_OUT / "db70_best_ground_effect_mask.png", (best_mask.astype(np.uint8) * 255))
    save_u8(REMOTE_OUT / "db70_protected_veto_mask.png", (base_veto.astype(np.uint8) * 255))
    save_rgb(REMOTE_OUT / "db70_best_changed_overlay.png", overlay(best_cand, best_mask, (255, 50, 50), 0.65))
    save_u8(REMOTE_OUT / "db70_best_source_id_used.png", best_label)
    build_boards(hard, route_ref, best_cand, best_mask, base_veto, best_label, summary)
    (REMOTE_OUT / "db70_protected_ground_plane_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    OUT["status"] = "db70_protected_ground_plane_completed"
    OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "db70_protected_ground_plane_failed_or_blocked"
    OUT["error"] = {{"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB70_JSON_BEGIN")
    print(json.dumps(json_safe(OUT), sort_keys=True, separators=(",", ":")))
    print("DB70_JSON_END")
'''


def remote_bash() -> str:
    b64 = base64.b64encode(remote_python().encode("utf-8")).decode("ascii")
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
        "db": "DB-70",
        "phase": "protected_ground_plane_local_alignment",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "remote_status": remote.get("status"),
        "summary_status": summary.get("status"),
        "claim_classification": summary.get("claim_classification", "unknown"),
        "accepted_by_metrics": summary.get("accepted_by_metrics", False),
        "best_variant_id": summary.get("best_variant_id"),
        "scope": {
            "one_status_one_exec": True,
            "bmw_only": True,
            "raw_erp_slabs": True,
            "lidar_ground_plane": True,
            "protected_local_mask": True,
            "full_frame_ground_replacement": False,
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
            "local_board": rel(OUT_DIR / "db70_protected_ground_plane_local_board.jpg"),
        },
        "decision": {
            "accepted_as_source_faithful_repair": False,
            "needs_vision_review": bool(summary),
            "next_route_if_rejected": "presentation-only protected local visual candidate or abstain/handoff package.",
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
    build_local_board(summary, status)
    manifest = build_manifest(remote, fetched, status)
    print(
        json.dumps(
            {
                "status": manifest["remote_status"],
                "summary_status": manifest["summary_status"],
                "claim_classification": manifest["claim_classification"],
                "accepted_by_metrics": manifest["accepted_by_metrics"],
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
