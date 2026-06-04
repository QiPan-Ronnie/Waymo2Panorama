"""DB-35: seam-first target board.

Build same-ROI visual evidence for the user-priority seam problem. This script
does not edit panoramas; it only normalizes existing candidates into comparable
full/ROI panels.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def read_bgr(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return img


def extract_a1_result(path: Path) -> np.ndarray:
    """Extract the result half from A1_view_none_L1_vs_result.jpg."""
    fig = read_bgr(path)
    h, w = fig.shape[:2]
    panel_h = round(w * 1024 / 2048)
    res_y0 = 30 + panel_h + 30
    res = fig[res_y0 : res_y0 + panel_h, :]
    return cv2.resize(res, (2048, 1024), interpolation=cv2.INTER_AREA)


def ensure_erp(img: np.ndarray) -> np.ndarray:
    if img.shape[:2] == (1024, 2048):
        return img
    return cv2.resize(img, (2048, 1024), interpolation=cv2.INTER_AREA)


def label(img: np.ndarray, text: str, h: int = 32) -> np.ndarray:
    bar = np.zeros((h, img.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, img])


def fit_panel(img: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = img.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    rs = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((h, w, 3), np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = rs
    return out


def crop(img: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return img[y0:y1, x0:x1]


def draw_roi(img: np.ndarray, roi: tuple[int, int, int, int], color: tuple[int, int, int]) -> np.ndarray:
    out = img.copy()
    x0, y0, x1, y1 = roi
    cv2.rectangle(out, (x0, y0), (x1, y1), color, 4)
    return out


def edge_panel(img: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    c = crop(img, roi)
    gray = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 150)
    vis = c.copy()
    vis[edges > 0] = (0, 0, 255)
    return vis


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--long-roi", default="850,420,1650,720")
    ap.add_argument("--right-roi", default="1440,360,2048,720")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    long_roi = tuple(int(x) for x in args.long_roi.split(","))
    right_roi = tuple(int(x) for x in args.right_roi.split(","))

    candidates: list[tuple[str, np.ndarray, str]] = [
        ("G_bmw_pano", read_bgr(Path("deliverables/ghostkill/G_bmw_pano.jpg")), "user nearest original; wavy seam"),
        ("BEST_bmw_pano", read_bgr(Path("deliverables/ghostkill/BEST_bmw_pano.jpg")), "ghost/building risk"),
        ("A1_view_none", extract_a1_result(Path("deliverables/a1_streetview_pipeline/A1_view_none_L1_vs_result.jpg")), "parallax residual"),
        (
            "DB14_G_v14_tau5",
            read_bgr(Path("deliverables/dit360_v2/db14_g_bmw_pano_fetch/G_bmw_pano/g_r008_h016_w025_tau5/g_r008_h016_w025_tau5_corecompose.png")),
            "old v14 DiT seam recipe on G",
        ),
        (
            "DB14_BEST_v14_tau5",
            read_bgr(Path("deliverables/dit360_v2/db14_best_bmw_pano_fetch/BEST_bmw_pano/best_r008_h016_w025_tau5/best_r008_h016_w025_tau5_corecompose.png")),
            "old v14 DiT seam recipe on BEST",
        ),
        (
            "DB14_A1_v14_tau5",
            read_bgr(Path("deliverables/dit360_v2/db14_a1_view_none_fetch/A1_view_none_bmw/a1view_r008_h016_w025_tau5/a1view_r008_h016_w025_tau5_corecompose.png")),
            "old v14 DiT seam recipe on A1_view_none",
        ),
        (
            "DB19_G_sky_only",
            read_bgr(Path("deliverables/dit360_v2/db19_G_bmw_pano_sky_t50_s0_postcompose_thr45.png")),
            "G plus accepted sky-only; seam unchanged",
        ),
        (
            "DB28_a200_source",
            read_bgr(Path("deliverables/dit360_v2/db28_clean_subset_refine/SR_bmw_db28_a200_final_1024x2048.png")),
            "strict-clean source candidate; different anchor",
        ),
        (
            "DB32_s40_current",
            read_bgr(Path("deliverables/dit360_v2/db32_generated_sky_harmonize_v2/db32_generated_sky_harmonize_s40.png")),
            "current best QA reference; sky only changed",
        ),
    ]
    rows = []
    for name, img, note in candidates:
        img = ensure_erp(img)
        full = draw_roi(draw_roi(img, long_roi, (0, 0, 255)), right_roi, (0, 255, 255))
        panels = [
            label(fit_panel(full, 380, 190), f"{name} full"),
            label(fit_panel(crop(img, long_roi), 380, 190), "long/source-boundary ROI"),
            label(fit_panel(crop(img, right_roi), 380, 190), "right white-line ROI"),
            label(fit_panel(edge_panel(img, right_roi), 380, 190), "right ROI edges"),
        ]
        rows.append(np.hstack(panels))
        cv2.imwrite(str(args.out_dir / f"{name}_long_roi.jpg"), crop(img, long_roi), [cv2.IMWRITE_JPEG_QUALITY, 95])
        cv2.imwrite(str(args.out_dir / f"{name}_right_roi.jpg"), crop(img, right_roi), [cv2.IMWRITE_JPEG_QUALITY, 95])

    board = np.vstack(rows)
    cv2.imwrite(str(args.out_dir / "db35_seam_target_board.jpg"), board, [cv2.IMWRITE_JPEG_QUALITY, 94])
    manifest = {
        "long_roi": list(long_roi),
        "right_roi": list(right_roi),
        "candidates": [{"name": name, "note": note} for name, _img, note in candidates],
        "board": str(args.out_dir / "db35_seam_target_board.jpg"),
        "purpose": "same-ROI visual evidence only; not a repair",
    }
    (args.out_dir / "db35_seam_target_board_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(args.out_dir / "db35_seam_target_board.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
