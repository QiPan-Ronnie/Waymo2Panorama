"""DB-35: right-ground seam donor diagnostic.

This is one bounded CPU-only test: one hand-auditable mask, one feathered
donor-blend method, two donor sources. It is meant to decide whether BEST/A1
contain a safer right-ground white-line patch than G, not to produce a broad
repair sweep.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


LONG_ROI = (850, 420, 1650, 720)
RIGHT_ROI = (1440, 360, 2048, 720)


def read_bgr(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    if img.shape[:2] != (1024, 2048):
        img = cv2.resize(img, (2048, 1024), interpolation=cv2.INTER_AREA)
    return img


def extract_a1_result(path: Path) -> np.ndarray:
    fig = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if fig is None:
        raise FileNotFoundError(path)
    h, w = fig.shape[:2]
    panel_h = round(w * 1024 / 2048)
    res_y0 = 30 + panel_h + 30
    res = fig[res_y0 : res_y0 + panel_h, :]
    return cv2.resize(res, (2048, 1024), interpolation=cv2.INTER_AREA)


def make_rightline_mask() -> np.ndarray:
    mask = np.zeros((1024, 2048), np.uint8)
    # Narrow lower-right strip around the visible wavy sidewalk/road white-line
    # discontinuity. It deliberately avoids the BMW body and the building faces.
    pts = np.array(
        [
            (1660, 662),
            (1765, 708),
            (1905, 700),
            (2047, 625),
            (2047, 512),
            (1915, 550),
            (1780, 610),
            (1660, 635),
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(mask, [pts], 255)
    return mask


def feather(mask: np.ndarray, radius: int) -> np.ndarray:
    m = (mask > 0).astype(np.float32)
    blurred = cv2.GaussianBlur(m, (0, 0), radius)
    return np.clip(blurred, 0.0, 1.0)


def lab_match_donor(base: np.ndarray, donor: np.ndarray, mask: np.ndarray) -> np.ndarray:
    sel = mask > 0
    out_lab = cv2.cvtColor(donor, cv2.COLOR_BGR2LAB).astype(np.float32)
    base_lab = cv2.cvtColor(base, cv2.COLOR_BGR2LAB).astype(np.float32)
    donor_lab = out_lab.copy()
    for ch in range(3):
        b = base_lab[:, :, ch][sel]
        d = donor_lab[:, :, ch][sel]
        b_mean, b_std = float(b.mean()), float(b.std() + 1e-6)
        d_mean, d_std = float(d.mean()), float(d.std() + 1e-6)
        out_lab[:, :, ch] = (out_lab[:, :, ch] - d_mean) * (b_std / d_std) + b_mean
    out_lab = np.clip(out_lab, 0, 255).astype(np.uint8)
    return cv2.cvtColor(out_lab, cv2.COLOR_LAB2BGR)


def donor_blend(base: np.ndarray, donor: np.ndarray, mask: np.ndarray, feather_radius: int) -> np.ndarray:
    matched = lab_match_donor(base, donor, mask)
    alpha = feather(mask, feather_radius)[:, :, None]
    out = base.astype(np.float32) * (1.0 - alpha) + matched.astype(np.float32) * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def crop(img: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return img[y0:y1, x0:x1]


def label(img: np.ndarray, text: str, h: int = 30) -> np.ndarray:
    bar = np.zeros((h, img.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, img])


def fit_panel(img: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = img.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((h, w, 3), np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = resized
    return out


def mask_overlay(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = img.copy()
    red = np.zeros_like(out)
    red[:, :, 2] = 255
    alpha = (mask.astype(np.float32) / 255.0 * 0.55)[:, :, None]
    out = out.astype(np.float32) * (1.0 - alpha) + red.astype(np.float32) * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def diff_heat(base: np.ndarray, variant: np.ndarray) -> np.ndarray:
    diff = cv2.absdiff(base, variant)
    gray = diff.max(axis=2)
    heat = cv2.applyColorMap(np.clip(gray * 4, 0, 255).astype(np.uint8), cv2.COLORMAP_JET)
    heat[gray == 0] = (0, 0, 0)
    return heat


def row_for(name: str, base: np.ndarray, donor: np.ndarray, variant: np.ndarray, mask: np.ndarray) -> np.ndarray:
    panels = [
        label(fit_panel(crop(base, RIGHT_ROI), 330, 190), "G right ROI"),
        label(fit_panel(crop(donor, RIGHT_ROI), 330, 190), f"{name} donor ROI"),
        label(fit_panel(crop(variant, RIGHT_ROI), 330, 190), f"{name} patched ROI"),
        label(fit_panel(crop(mask_overlay(base, mask), RIGHT_ROI), 330, 190), "mask on G"),
        label(fit_panel(crop(diff_heat(base, variant), RIGHT_ROI), 330, 190), "changed pixels"),
        label(fit_panel(crop(variant, LONG_ROI), 330, 190), "long ROI after patch"),
    ]
    return np.hstack(panels)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--feather", type=int, default=10)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    base = read_bgr(Path("deliverables/ghostkill/G_bmw_pano.jpg"))
    donors = {
        "BEST": read_bgr(Path("deliverables/ghostkill/BEST_bmw_pano.jpg")),
        "A1": extract_a1_result(Path("deliverables/a1_streetview_pipeline/A1_view_none_L1_vs_result.jpg")),
    }
    mask = make_rightline_mask()
    cv2.imwrite(str(args.out_dir / "db35_rightline_mask.png"), mask)
    cv2.imwrite(str(args.out_dir / "db35_rightline_mask_overlay.jpg"), mask_overlay(base, mask), [cv2.IMWRITE_JPEG_QUALITY, 95])

    rows = []
    metrics = {
        "mask_pixels": int((mask > 0).sum()),
        "mask_fraction": float((mask > 0).mean()),
        "feather_radius": args.feather,
        "method": "local LAB donor match + feathered blend inside one right-ground seam mask",
    }
    for name, donor in donors.items():
        variant = donor_blend(base, donor, mask, args.feather)
        out_path = args.out_dir / f"db35_rightline_{name.lower()}_donor_patch.png"
        cv2.imwrite(str(out_path), variant)
        cv2.imwrite(str(args.out_dir / f"db35_rightline_{name.lower()}_right_roi.jpg"), crop(variant, RIGHT_ROI), [cv2.IMWRITE_JPEG_QUALITY, 95])
        cv2.imwrite(str(args.out_dir / f"db35_rightline_{name.lower()}_long_roi.jpg"), crop(variant, LONG_ROI), [cv2.IMWRITE_JPEG_QUALITY, 95])
        changed = cv2.absdiff(base, variant).max(axis=2) > 0
        metrics[name] = {
            "output": str(out_path),
            "changed_pixels": int(changed.sum()),
            "outside_mask_changed_pixels": int((changed & (mask == 0)).sum()),
            "right_roi_output": str(args.out_dir / f"db35_rightline_{name.lower()}_right_roi.jpg"),
            "long_roi_output": str(args.out_dir / f"db35_rightline_{name.lower()}_long_roi.jpg"),
        }
        rows.append(row_for(name, base, donor, variant, mask))

    board = np.vstack(rows)
    board_path = args.out_dir / "db35_rightline_donor_diag_board.jpg"
    cv2.imwrite(str(board_path), board, [cv2.IMWRITE_JPEG_QUALITY, 94])
    metrics["board"] = str(board_path)
    (args.out_dir / "db35_rightline_donor_diag_manifest.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(board_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
