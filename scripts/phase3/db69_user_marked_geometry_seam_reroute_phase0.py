from __future__ import annotations

import argparse
import json
import math
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
RUN = "02a00399_a000_bmw"
DB64 = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0"
DB65 = ROOT / "deliverables" / "layered_target_raycaster" / "db65_visible_photometric_fallback"
DB68 = ROOT / "deliverables" / "layered_target_raycaster" / "db68_edge_aware_photometric_polish_v2"
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db69_user_marked_geometry_seam_reroute"

PHASE2 = DB64 / "phase2_lidar_zbuffer_fetch" / RUN
PHASE3 = DB64 / "phase3_sidecar_instrumentation" / "fetch" / RUN
PHASE4B = DB64 / "phase4b_z_visibility_cause" / "fetch" / RUN

HARD = PHASE2 / f"{RUN}_hard_select.jpg"
DB65_BEST = DB65 / "db65_best_visible_photometric_candidate.png"
DB68_BEST = DB68 / "db68_best_edge_aware_candidate.png"
SOURCE_ID = PHASE3 / f"{RUN}_source_id_map.png"
SOURCE_ID_VIZ = PHASE3 / f"{RUN}_source_id_map_viz.png"
HARD_SOURCE_ID = PHASE3 / f"{RUN}_hard_select_source_id_map.png"
BOUNDARY = PHASE3 / f"{RUN}_source_boundary_risk_mask.png"
SEAM_BAND = PHASE3 / f"{RUN}_seam_band_mask.png"
SEAM_CORE = PHASE3 / f"{RUN}_seam_core_mask.png"
RISK_MAP = PHASE3 / f"{RUN}_risk_map.png"
UNKNOWN = PHASE3 / f"{RUN}_unknown_mask.png"
LIDAR_SUPPORT = PHASE3 / f"{RUN}_lidar_support_map.png"
VIS_COUNT = PHASE3 / f"{RUN}_visibility_count_map.png"
Z_CAUSE = PHASE4B / f"{RUN}_z_cause_primary_map.png"
Z_REPAIRABILITY = PHASE4B / f"{RUN}_z_repairability_map.png"

BOARD = OUT_DIR / "db69_phase0_user_marked_geometry_audit_board.jpg"
ROI_SHEET = OUT_DIR / "db69_phase0_marked_roi_sheet.jpg"
COST_BOARD = OUT_DIR / "db69_phase0_route_cost_components_board.jpg"
SEGMENT_OVERLAY = OUT_DIR / "db69_phase0_seam_segment_overlay.png"
SOURCE_BOUNDARY_OVERLAY = OUT_DIR / "db69_phase0_source_boundary_overlay.png"
STRUCTURE_RISK = OUT_DIR / "db69_phase0_structure_risk_proxy.png"
CORRIDOR = OUT_DIR / "db69_phase0_route_corridor_map.png"
FEASIBILITY_MAP = OUT_DIR / "db69_phase0_marked_roi_feasibility_map.png"
FEASIBILITY_JSON = OUT_DIR / "db69_phase0_marked_roi_feasibility.json"
SEGMENT_JSON = OUT_DIR / "db69_phase0_seam_segment_table.json"
METRICS = OUT_DIR / "db69_phase0_metrics.json"
MANIFEST = OUT_DIR / "db69_phase0_manifest.json"

MARKED_ROIS = {
    "left_road_patch": (250, 515, 460, 715),
    "lower_center_road_patch": (740, 595, 1035, 745),
    "center_lane_marking": (1030, 515, 1325, 735),
    "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760),
}

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


def crop(arr: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return arr[y0:y1, x0:x1]


def paste_arr(board: Image.Image, arr: np.ndarray, box: tuple[int, int, int, int], outline=(180, 180, 180)) -> None:
    x0, y0, x1, y1 = box
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")
    im.thumbnail((x1 - x0, y1 - y0))
    px = x0 + ((x1 - x0) - im.width) // 2
    py = y0 + ((y1 - y0) - im.height) // 2
    board.paste(im, (px, py))
    ImageDraw.Draw(board).rectangle((px, py, px + im.width, py + im.height), outline=outline)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask.astype(bool)
    k = 2 * radius + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.dilate(mask.astype(np.uint8), kernel) > 0


def normalize_u8(x: np.ndarray, valid: np.ndarray | None = None) -> np.ndarray:
    arr = x.astype(np.float32)
    if valid is None or not np.any(valid):
        lo, hi = np.percentile(arr, [2, 98])
    else:
        lo, hi = np.percentile(arr[valid], [2, 98])
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.uint8)
    return np.clip((arr - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def rgb_to_y(rgb: np.ndarray) -> np.ndarray:
    x = rgb.astype(np.float32)
    return 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]


def overlay_mask(rgb: np.ndarray, mask: np.ndarray, color: tuple[int, int, int], alpha: float = 0.55) -> np.ndarray:
    out = rgb.astype(np.float32).copy()
    c = np.array(color, dtype=np.float32)
    out[mask] = out[mask] * (1.0 - alpha) + c * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def source_id_viz(source: np.ndarray) -> np.ndarray:
    palette = np.array(
        [
            [80, 220, 120],
            [255, 210, 65],
            [255, 120, 80],
            [80, 210, 255],
            [180, 110, 255],
            [255, 90, 200],
            [120, 170, 255],
            [220, 220, 220],
        ],
        dtype=np.uint8,
    )
    out = np.zeros((*source.shape, 3), dtype=np.uint8)
    valid = source != 255
    out[valid] = palette[source[valid] % len(palette)]
    return out


def draw_roi_boxes(rgb: np.ndarray) -> np.ndarray:
    im = Image.fromarray(rgb.copy())
    draw = ImageDraw.Draw(im)
    colors = {
        "left_road_patch": (255, 82, 82),
        "lower_center_road_patch": (255, 170, 40),
        "center_lane_marking": (80, 220, 255),
        "right_curb_sidewalk_wall_base": (170, 110, 255),
    }
    for name, roi in MARKED_ROIS.items():
        draw.rectangle(roi, outline=colors[name], width=4)
        draw_text(draw, (roi[0] + 4, max(0, roi[1] - 22)), name, fill=colors[name], size=16)
    return np.asarray(im)


def source_boundary(source: np.ndarray, valid: np.ndarray) -> np.ndarray:
    sid = source.astype(np.int32)
    boundary = np.zeros_like(valid, dtype=bool)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        shifted = np.roll(sid, shift=(dy, dx), axis=(0, 1))
        shifted_valid = np.roll(valid, shift=(dy, dx), axis=(0, 1))
        boundary |= valid & shifted_valid & (sid != shifted)
    return boundary


def connected_components(mask: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    comps: list[dict[str, Any]] = []
    cid = 0
    for y in range(h):
        xs = np.flatnonzero(mask[y] & (labels[y] == 0))
        for x0 in xs:
            if labels[y, x0] != 0:
                continue
            cid += 1
            q: deque[tuple[int, int]] = deque([(y, int(x0))])
            labels[y, x0] = cid
            pts: list[tuple[int, int]] = []
            while q:
                yy, xx = q.popleft()
                pts.append((yy, xx))
                for ny, nx in ((yy - 1, xx), (yy + 1, xx), (yy, xx - 1), (yy, xx + 1)):
                    if ny < 0 or ny >= h or nx < 0 or nx >= w:
                        continue
                    if not mask[ny, nx] or labels[ny, nx] != 0:
                        continue
                    labels[ny, nx] = cid
                    q.append((ny, nx))
            ys = np.array([p[0] for p in pts])
            xs2 = np.array([p[1] for p in pts])
            comps.append(
                {
                    "segment_id": cid,
                    "area_px": int(len(pts)),
                    "bbox": [int(xs2.min()), int(ys.min()), int(xs2.max()) + 1, int(ys.max()) + 1],
                    "height_px": int(ys.max() - ys.min() + 1),
                    "width_px": int(xs2.max() - xs2.min() + 1),
                }
            )
    return labels, comps


def structure_risk_components(rgb: np.ndarray, seam_valid: np.ndarray) -> dict[str, np.ndarray]:
    y = rgb_to_y(rgb)
    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    edge = normalize_u8(np.sqrt(gx * gx + gy * gy), seam_valid)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = hsv[..., 1].astype(np.float32)
    val = hsv[..., 2].astype(np.float32)
    hue = hsv[..., 0].astype(np.float32)
    yy = np.arange(rgb.shape[0])[:, None]
    road_band = (yy > 390) & (yy < 760)
    white_lane = (val > 168) & (sat < 90) & road_band
    yellow_lane = (hue > 15) & (hue < 45) & (sat > 60) & (val > 90) & road_band
    lane = dilate(white_lane | yellow_lane, 2).astype(np.uint8) * 255
    horizontal = normalize_u8(np.abs(gy), seam_valid)
    wall_base = ((horizontal > 130) & (yy > 430) & (yy < 745)).astype(np.uint8) * 255
    low_texture = (edge < 35).astype(np.uint8) * 255
    return {
        "edge": edge,
        "lane_marking_proxy": lane,
        "wall_curb_base_proxy": wall_base,
        "low_texture_proxy": low_texture,
    }


def weighted_cost(components: dict[str, np.ndarray], source_boundary_mask: np.ndarray, unknown: np.ndarray, risk: np.ndarray) -> np.ndarray:
    edge = components["edge"].astype(np.float32) / 255.0
    lane = components["lane_marking_proxy"].astype(np.float32) / 255.0
    wall = components["wall_curb_base_proxy"].astype(np.float32) / 255.0
    lowtex = components["low_texture_proxy"].astype(np.float32) / 255.0
    unknown_f = (unknown > 0).astype(np.float32)
    risk_f = risk.astype(np.float32) / 255.0
    boundary_f = source_boundary_mask.astype(np.float32)
    cost = 0.18 * edge + 0.28 * lane + 0.23 * wall + 0.09 * lowtex + 0.15 * risk_f + 0.07 * unknown_f
    cost += 0.15 * boundary_f
    return np.clip(cost, 0.0, 1.0)


def colorize_cost(cost: np.ndarray) -> np.ndarray:
    u8 = np.clip(cost * 255.0, 0, 255).astype(np.uint8)
    return cv2.applyColorMap(u8, cv2.COLORMAP_INFERNO)[..., ::-1]


def feasibility_for_roi(
    name: str,
    roi: tuple[int, int, int, int],
    boundary: np.ndarray,
    corridor: np.ndarray,
    low_risk: np.ndarray,
    cost: np.ndarray,
    components: dict[str, np.ndarray],
    source: np.ndarray,
) -> dict[str, Any]:
    x0, y0, x1, y1 = roi
    sl = np.s_[y0:y1, x0:x1]
    b = boundary[sl]
    c = corridor[sl]
    lr = low_risk[sl]
    labels = source[sl]
    uniq = sorted(int(v) for v in np.unique(labels) if int(v) != 255)
    lane_frac = float(np.mean(components["lane_marking_proxy"][sl] > 0))
    wall_frac = float(np.mean(components["wall_curb_base_proxy"][sl] > 0))
    edge_mean = float(np.mean(components["edge"][sl]) / 255.0)
    boundary_frac = float(np.mean(b))
    corridor_frac = float(np.mean(c))
    low_risk_corridor_frac = float(np.mean(lr & c))
    mean_cost_on_boundary = float(np.mean(cost[sl][b])) if np.any(b) else None
    if low_risk_corridor_frac >= 0.06 and boundary_frac > 0:
        verdict = "GREEN"
        reason = "low-risk alternate corridor exists near current boundary"
    elif low_risk_corridor_frac >= 0.02 and boundary_frac > 0:
        verdict = "YELLOW"
        reason = "some alternate corridor exists but tradeoff/risk remains"
    elif boundary_frac == 0:
        verdict = "YELLOW"
        reason = "marked ROI does not intersect current detected source boundary; audit alignment/ROI or neighboring seam"
    else:
        verdict = "RED"
        reason = "current boundary crosses structure and no clear low-risk corridor appears locally"
    return {
        "roi": name,
        "bbox": [x0, y0, x1, y1],
        "verdict": verdict,
        "reason": reason,
        "source_ids_present": uniq,
        "boundary_fraction": boundary_frac,
        "corridor_fraction": corridor_frac,
        "low_risk_corridor_fraction": low_risk_corridor_frac,
        "lane_marking_proxy_fraction": lane_frac,
        "wall_curb_base_proxy_fraction": wall_frac,
        "edge_mean": edge_mean,
        "mean_cost_on_boundary": mean_cost_on_boundary,
    }


def build_segment_report(
    comps: list[dict[str, Any]],
    labels: np.ndarray,
    source: np.ndarray,
    cost: np.ndarray,
    components: dict[str, np.ndarray],
    lidar: np.ndarray,
    vis: np.ndarray,
    unknown: np.ndarray,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for comp in comps:
        mask = labels == int(comp["segment_id"])
        if int(mask.sum()) < 12:
            continue
        ys, xs = np.where(mask)
        dil = dilate(mask, 2)
        src_vals = sorted(int(v) for v in np.unique(source[dil]) if int(v) != 255)
        row = dict(comp)
        row.update(
            {
                "source_ids_near_segment": src_vals,
                "mean_route_cost": float(np.mean(cost[mask])),
                "max_route_cost": float(np.max(cost[mask])),
                "lane_cross_fraction": float(np.mean(components["lane_marking_proxy"][mask] > 0)),
                "wall_curb_base_cross_fraction": float(np.mean(components["wall_curb_base_proxy"][mask] > 0)),
                "mean_edge_risk": float(np.mean(components["edge"][mask]) / 255.0),
                "lidar_support_fraction": float(np.mean(lidar[mask] > 0)),
                "visible_any_fraction": float(np.mean(vis[mask] > 0)),
                "unknown_fraction": float(np.mean(unknown[mask] > 0)),
                "touches_marked_roi": [
                    name
                    for name, (x0, y0, x1, y1) in MARKED_ROIS.items()
                    if bool(np.any((xs >= x0) & (xs < x1) & (ys >= y0) & (ys < y1)))
                ],
            }
        )
        rows.append(row)
    rows.sort(key=lambda r: (len(r["touches_marked_roi"]) > 0, r["area_px"]), reverse=True)
    return rows


def segment_overlay(rgb: np.ndarray, labels: np.ndarray, rows: list[dict[str, Any]]) -> np.ndarray:
    out = rgb.copy()
    palette = np.array(
        [
            [255, 70, 70],
            [255, 170, 40],
            [80, 220, 255],
            [170, 110, 255],
            [80, 240, 120],
            [255, 80, 200],
        ],
        dtype=np.uint8,
    )
    for i, row in enumerate(rows[:32]):
        mask = labels == int(row["segment_id"])
        out = overlay_mask(out, mask, tuple(int(v) for v in palette[i % len(palette)]), alpha=0.80)
    return draw_roi_boxes(out)


def make_source_boundary_overlay(rgb: np.ndarray, source_viz: np.ndarray, boundary: np.ndarray, corridor: np.ndarray) -> np.ndarray:
    base = (0.60 * rgb.astype(np.float32) + 0.40 * source_viz.astype(np.float32)).astype(np.uint8)
    base = overlay_mask(base, corridor, (255, 200, 45), 0.25)
    base = overlay_mask(base, boundary, (255, 0, 0), 0.85)
    return draw_roi_boxes(base)


def make_structure_risk_viz(rgb: np.ndarray, components: dict[str, np.ndarray], cost: np.ndarray) -> np.ndarray:
    out = rgb.copy()
    out = overlay_mask(out, components["lane_marking_proxy"] > 0, (20, 230, 255), 0.65)
    out = overlay_mask(out, components["wall_curb_base_proxy"] > 0, (255, 90, 210), 0.45)
    edge_hot = components["edge"] > 160
    out = overlay_mask(out, edge_hot, (255, 180, 30), 0.30)
    cost_hot = cost > 0.55
    out = overlay_mask(out, cost_hot, (255, 40, 40), 0.18)
    return draw_roi_boxes(out)


def make_corridor_maps(source: np.ndarray, boundary: np.ndarray, cost: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    corridor = dilate(boundary, 30) & valid
    forbidden = corridor & (cost >= 0.50)
    low_risk = corridor & (cost <= 0.28)
    corridor_rgb = np.zeros((*source.shape, 3), dtype=np.uint8)
    corridor_rgb[corridor] = (95, 95, 95)
    corridor_rgb[forbidden] = (255, 70, 70)
    corridor_rgb[low_risk] = (80, 230, 110)
    corridor_rgb[boundary] = (255, 255, 255)
    return corridor, low_risk, corridor_rgb


def build_roi_sheet(hard: np.ndarray, db65: np.ndarray, db68: np.ndarray, source_overlay: np.ndarray, risk_viz: np.ndarray, cost_viz: np.ndarray, feasibility: list[dict[str, Any]]) -> None:
    board = Image.new("RGB", (1840, 1760), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 20), "DB69 Phase0 marked ROI audit", size=28)
    y = 58
    y = draw_wrapped(draw, 32, y, "Rows show the four user-marked geometry seam failures. DB69 Phase0 is audit only: no reroute, no warp, no RGB repair.", 150, size=14)
    panels = [
        ("hard_select", hard),
        ("DB65", db65),
        ("DB68 rejected polish", db68),
        ("source boundary", source_overlay),
        ("structure risk proxy", risk_viz),
        ("route cost", cost_viz),
    ]
    for row_idx, f in enumerate(feasibility):
        x0, y0, x1, y1 = f["bbox"]
        ybase = 120 + row_idx * 405
        draw_text(draw, (28, ybase), f"{f['roi']} verdict={f['verdict']} {f['reason']}", size=17, fill=(245, 245, 245))
        for col, (label, arr) in enumerate(panels):
            bx = 28 + col * 300
            draw_text(draw, (bx, ybase + 26), label, size=12)
            c = crop(arr, (x0, y0, x1, y1))
            paste_arr(board, c, (bx, ybase + 48, bx + 280, ybase + 230))
        metrics = (
            f"boundary={f['boundary_fraction']:.3f} low_risk_corr={f['low_risk_corridor_fraction']:.3f} "
            f"lane={f['lane_marking_proxy_fraction']:.3f} wall/curb={f['wall_curb_base_proxy_fraction']:.3f} "
            f"sources={f['source_ids_present']}"
        )
        draw_wrapped(draw, 28, ybase + 244, metrics, 170, size=13, fill=(210, 220, 230))
    board.save(ROI_SHEET, quality=92)


def build_cost_board(rgb: np.ndarray, components: dict[str, np.ndarray], cost: np.ndarray, corridor_rgb: np.ndarray, source_overlay: np.ndarray) -> None:
    board = Image.new("RGB", (2040, 1280), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 22), "DB69 Phase0 route-cost components", size=27)
    y = 62
    y = draw_wrapped(draw, 32, y, "Risk components are CPU-local proxies plus DB64 sidecars. They are for seam-placement audit, not semantic truth or repair permission.", 160, size=14)
    panels = [
        ("source boundary + corridor", source_overlay),
        ("edge proxy", cv2.cvtColor(components["edge"], cv2.COLOR_GRAY2RGB)),
        ("lane marking proxy", cv2.cvtColor(components["lane_marking_proxy"], cv2.COLOR_GRAY2RGB)),
        ("wall/curb-base proxy", cv2.cvtColor(components["wall_curb_base_proxy"], cv2.COLOR_GRAY2RGB)),
        ("low texture proxy", cv2.cvtColor(components["low_texture_proxy"], cv2.COLOR_GRAY2RGB)),
        ("weighted route cost", colorize_cost(cost)),
        ("corridor feasibility", corridor_rgb),
        ("marked ROIs on RGB", draw_roi_boxes(rgb)),
    ]
    for i, (label, arr) in enumerate(panels):
        x = 28 + (i % 4) * 500
        yy = 120 + (i // 4) * 520
        draw_text(draw, (x, yy - 24), label, size=16)
        paste_arr(board, arr, (x, yy, x + 470, yy + 465))
    board.save(COST_BOARD, quality=92)


def build_main_board(
    hard: np.ndarray,
    db65: np.ndarray,
    db68: np.ndarray,
    source_overlay: np.ndarray,
    risk_viz: np.ndarray,
    cost_viz: np.ndarray,
    segment_viz: np.ndarray,
    corridor_rgb: np.ndarray,
    feasibility_map: np.ndarray,
    feasibility: list[dict[str, Any]],
    phase1_allowed: bool,
    missing_inputs: list[str],
) -> None:
    board = Image.new("RGB", (2260, 1900), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 22), "DB69 Phase0 - user-marked geometry seam audit", size=29)
    y = 64
    for line in [
        "CPU/local audit only. No reroute candidate, no warp, no inpaint/generation, no A100.",
        "DB69 is source-boundary selection/seam-placement evidence, not geometry repair.",
        f"phase1_reroute_allowed={phase1_allowed}; missing_inputs={missing_inputs if missing_inputs else 'none'}",
    ]:
        y = draw_wrapped(draw, 34, y, "- " + line, 175, size=14)
    panels = [
        ("hard_select + marked ROIs", draw_roi_boxes(hard)),
        ("DB65 rejected polish ref", draw_roi_boxes(db65)),
        ("DB68 rejected polish ref", draw_roi_boxes(db68)),
        ("source boundary overlay", source_overlay),
        ("structure risk proxy", risk_viz),
        ("weighted route cost", cost_viz),
        ("seam segment overlay", segment_viz),
        ("corridor map", corridor_rgb),
        ("marked ROI feasibility", feasibility_map),
    ]
    for i, (label, arr) in enumerate(panels):
        x = 28 + (i % 3) * 740
        yy = 170 + (i // 3) * 455
        draw_text(draw, (x, yy - 28), label, size=17)
        paste_arr(board, arr, (x, yy, x + 700, yy + 410))
    y2 = 1540
    draw_text(draw, (28, y2), "Marked ROI feasibility", size=19)
    y2 += 32
    for item in feasibility:
        y2 = draw_wrapped(
            draw,
            34,
            y2,
            f"{item['roi']}: {item['verdict']} - {item['reason']} | boundary={item['boundary_fraction']:.3f} low_risk_corridor={item['low_risk_corridor_fraction']:.3f} lane={item['lane_marking_proxy_fraction']:.3f} wall/curb={item['wall_curb_base_proxy_fraction']:.3f} sources={item['source_ids_present']}",
            190,
            size=13,
            fill=(225, 230, 235),
        )
    board.save(BOARD, quality=92)


def secret_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, pat in TOKEN_PATTERNS.items():
        found = pat.findall(text)
        if found:
            hits.append({"pattern": name, "count": len(found)})
    return hits


def run_self_test() -> None:
    dummy = np.zeros((32, 48), dtype=np.uint8)
    dummy[:, 20:24] = 1
    valid = np.ones_like(dummy, dtype=bool)
    b = source_boundary(dummy, valid)
    assert b.sum() > 0, "source_boundary should find label edge"
    labels, comps = connected_components(b)
    assert labels.max() >= 1 and comps, "connected_components should label boundary"
    rgb = np.zeros((32, 48, 3), dtype=np.uint8)
    rgb[:, :, :] = 100
    rgb[16:18, 5:40] = 240
    comps_risk = structure_risk_components(rgb, valid)
    assert "lane_marking_proxy" in comps_risk and comps_risk["lane_marking_proxy"].shape == dummy.shape
    cost = weighted_cost(comps_risk, b, np.zeros_like(dummy), np.zeros_like(dummy))
    assert cost.shape == dummy.shape and np.isfinite(cost).all()
    roi = ("test", (0, 0, 24, 32))
    f = feasibility_for_roi(roi[0], roi[1], b, dilate(b, 4), cost < 0.2, cost, comps_risk, dummy)
    assert f["verdict"] in {"GREEN", "YELLOW", "RED"}
    print(json.dumps({"self_test": "pass", "segments": len(comps), "boundary_px": int(b.sum())}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return

    cleanup()
    required = [HARD, DB65_BEST, DB68_BEST, SOURCE_ID, HARD_SOURCE_ID, BOUNDARY, SEAM_BAND, RISK_MAP, UNKNOWN, LIDAR_SUPPORT, VIS_COUNT]
    missing_inputs = [rel(p) for p in required if not p.exists()]
    if missing_inputs:
        raise FileNotFoundError("Missing DB69 Phase0 inputs: " + ", ".join(missing_inputs))

    hard = read_rgb(HARD)
    db65 = read_rgb(DB65_BEST)
    db68 = read_rgb(DB68_BEST)
    sparse_source = read_u8(SOURCE_ID)
    source = read_u8(HARD_SOURCE_ID)
    source_viz = source_id_viz(source)
    boundary_sidecar = read_u8(BOUNDARY) > 0
    seam_band = read_u8(SEAM_BAND) > 0
    risk = read_u8(RISK_MAP)
    unknown = read_u8(UNKNOWN)
    lidar = read_u8(LIDAR_SUPPORT)
    vis = read_u8(VIS_COUNT)
    rgb_valid = hard.sum(axis=2) > 18
    valid = (source != 255) & rgb_valid

    boundary_detected = source_boundary(source, valid)
    boundary = (boundary_detected | boundary_sidecar) & rgb_valid
    seam_context = dilate(boundary | seam_band, 5) & rgb_valid
    components = structure_risk_components(hard, seam_context)
    cost = weighted_cost(components, boundary, unknown, risk)
    corridor, low_risk_corridor, corridor_rgb = make_corridor_maps(source, boundary, cost, valid)

    source_overlay = make_source_boundary_overlay(hard, source_viz, boundary, corridor)
    risk_viz = make_structure_risk_viz(hard, components, cost)
    cost_viz = draw_roi_boxes(colorize_cost(cost))

    seg_labels, seg_comps = connected_components(boundary)
    segments = build_segment_report(seg_comps, seg_labels, source, cost, components, lidar, vis, unknown)
    seg_viz = segment_overlay(hard, seg_labels, segments)

    feasibility = [
        feasibility_for_roi(name, roi, boundary, corridor, low_risk_corridor, cost, components, source)
        for name, roi in MARKED_ROIS.items()
    ]
    verdict_colors = {"GREEN": (70, 220, 100), "YELLOW": (255, 210, 65), "RED": (255, 70, 70)}
    feasibility_map = draw_roi_boxes(hard)
    for f in feasibility:
        x0, y0, x1, y1 = f["bbox"]
        m = np.zeros(source.shape, dtype=bool)
        m[y0:y1, x0:x1] = True
        feasibility_map = overlay_mask(feasibility_map, m, verdict_colors[f["verdict"]], 0.28)

    source_pair_slabs_available = False
    phase1_allowed = bool(
        not missing_inputs
        and source_pair_slabs_available
        and any(f["verdict"] in {"GREEN", "YELLOW"} and f["boundary_fraction"] > 0 for f in feasibility)
    )
    phase1_blockers = []
    if not source_pair_slabs_available:
        phase1_blockers.append("no per-camera ERP slab/source-candidate stack found locally for source-label-only reroute")
    if not any(f["boundary_fraction"] > 0 for f in feasibility):
        phase1_blockers.append("marked ROIs do not intersect detected source boundary")
    if not any(f["verdict"] in {"GREEN", "YELLOW"} and f["boundary_fraction"] > 0 for f in feasibility):
        phase1_blockers.append("no marked ROI has clear low-risk alternate corridor around a detected boundary")

    save_rgb(SOURCE_BOUNDARY_OVERLAY, source_overlay)
    save_rgb(STRUCTURE_RISK, risk_viz)
    save_rgb(CORRIDOR, corridor_rgb)
    save_rgb(FEASIBILITY_MAP, feasibility_map)
    save_rgb(SEGMENT_OVERLAY, seg_viz)
    build_roi_sheet(hard, db65, db68, source_overlay, risk_viz, cost_viz, feasibility)
    build_cost_board(hard, components, cost, corridor_rgb, source_overlay)
    build_main_board(hard, db65, db68, source_overlay, risk_viz, cost_viz, seg_viz, corridor_rgb, feasibility_map, feasibility, phase1_allowed, missing_inputs)

    FEASIBILITY_JSON.write_text(json.dumps({"marked_rois": feasibility}, indent=2), encoding="utf-8")
    SEGMENT_JSON.write_text(json.dumps({"segments": segments[:120], "all_segment_count": len(segments)}, indent=2), encoding="utf-8")

    metrics = {
        "status": "db69_phase0_user_marked_geometry_audit_complete",
        "claim_classification": "source-boundary audit / diagnostic evidence only; no RGB repair",
        "phase1_reroute_allowed": phase1_allowed,
        "phase1_blockers": phase1_blockers,
        "source_pair_slabs_available": source_pair_slabs_available,
        "source_ownership_source": rel(HARD_SOURCE_ID),
        "source_candidate_stack_inventory": [],
        "input_sidecar_status": {
            "source_id_map": SOURCE_ID.exists(),
            "source_id_viz": SOURCE_ID_VIZ.exists(),
            "hard_select_source_id_map": HARD_SOURCE_ID.exists(),
            "boundary_sidecar": BOUNDARY.exists(),
            "seam_band": SEAM_BAND.exists(),
            "risk_map": RISK_MAP.exists(),
            "unknown_mask": UNKNOWN.exists(),
            "lidar_support": LIDAR_SUPPORT.exists(),
            "visibility_count": VIS_COUNT.exists(),
            "z_cause": Z_CAUSE.exists(),
            "z_repairability": Z_REPAIRABILITY.exists(),
        },
        "boundary_stats": {
            "rgb_valid_fraction": float(rgb_valid.mean()),
            "sparse_source_valid_fraction": float((sparse_source != 255).mean()),
            "hard_select_source_valid_fraction": float((source != 255).mean()),
            "source_boundary_fraction": float(boundary.mean()),
            "seam_band_fraction": float(seam_band.mean()),
            "corridor_fraction": float(corridor.mean()),
            "low_risk_corridor_fraction": float(low_risk_corridor.mean()),
        },
        "marked_roi_feasibility": feasibility,
        "top_segments": segments[:24],
    }
    METRICS.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": metrics["status"],
        "claim_classification": metrics["claim_classification"],
        "scope": {
            "cpu_local_only": True,
            "phase0_audit_only": True,
            "reroute_candidate_created": False,
            "rgb_repair": False,
            "remote_status_exec": False,
            "a100": False,
            "vggt_pi3_hf_model_inference": False,
            "dit_flux_3dgs_inpaint_generation": False,
            "source_replacement": False,
            "geometry_warp": False,
            "db32_edit": False,
            "red_promotion": False,
        },
        "inputs": {name: rel(path) for name, path in {
            "hard_select": HARD,
            "db65": DB65_BEST,
            "db68": DB68_BEST,
            "source_id": SOURCE_ID,
            "source_id_viz": SOURCE_ID_VIZ,
            "hard_select_source_id": HARD_SOURCE_ID,
            "source_boundary": BOUNDARY,
            "seam_band": SEAM_BAND,
            "risk_map": RISK_MAP,
            "unknown": UNKNOWN,
            "lidar_support": LIDAR_SUPPORT,
            "visibility_count": VIS_COUNT,
            "z_cause": Z_CAUSE,
            "z_repairability": Z_REPAIRABILITY,
        }.items()},
        "outputs": {
            "main_board": rel(BOARD),
            "roi_sheet": rel(ROI_SHEET),
            "cost_components_board": rel(COST_BOARD),
            "source_boundary_overlay": rel(SOURCE_BOUNDARY_OVERLAY),
            "structure_risk_proxy": rel(STRUCTURE_RISK),
            "corridor_map": rel(CORRIDOR),
            "feasibility_map": rel(FEASIBILITY_MAP),
            "feasibility_json": rel(FEASIBILITY_JSON),
            "segment_overlay": rel(SEGMENT_OVERLAY),
            "segment_table": rel(SEGMENT_JSON),
            "metrics": rel(METRICS),
            "manifest": rel(MANIFEST),
        },
        "decision": {
            "phase1_reroute_allowed": phase1_allowed,
            "phase1_blockers": phase1_blockers,
            "local_geometry_alignment_allowed": False,
            "reason": "Phase0 audits source-boundary placement and route feasibility only. Reroute needs per-camera source candidates; local warp needs a later brief.",
        },
    }
    secret_text = json.dumps({"manifest": manifest, "metrics": metrics}, ensure_ascii=False)
    hits = secret_hits(secret_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(int(h["count"]) for h in hits), "hits": hits}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": metrics["status"],
                "phase1_reroute_allowed": phase1_allowed,
                "phase1_blockers": phase1_blockers,
                "board": rel(BOARD),
                "manifest": rel(MANIFEST),
                "secret_hits": manifest["strict_secret_scan"]["hit_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
