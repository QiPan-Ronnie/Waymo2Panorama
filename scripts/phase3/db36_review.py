"""DB-36 local review board and preservation stats."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


LONG_ROI = (850, 420, 1650, 720)
RIGHT_ROI = (1440, 360, 2048, 720)
LOWER_RIGHT_ROI = (1600, 560, 2048, 760)


def read_bgr(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    if img.shape[:2] != (1024, 2048):
        img = cv2.resize(img, (2048, 1024), interpolation=cv2.INTER_AREA)
    return img


def read_mask(path: Path) -> np.ndarray:
    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(path)
    if mask.shape[:2] != (1024, 2048):
        mask = cv2.resize(mask, (2048, 1024), interpolation=cv2.INTER_NEAREST)
    return mask


def crop(img: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return img[y0:y1, x0:x1]


def label(img: np.ndarray, text: str, h: int = 30) -> np.ndarray:
    bar = np.zeros((h, img.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, img])


def fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = img.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((h, w, 3), np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = resized
    return out


def diff_heat(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = cv2.absdiff(a, b).max(axis=2)
    heat = cv2.applyColorMap(np.clip(d * 3, 0, 255).astype(np.uint8), cv2.COLORMAP_JET)
    heat[d == 0] = (0, 0, 0)
    return heat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", type=Path, default=Path("deliverables/ghostkill/G_bmw_pano.jpg"))
    ap.add_argument(
        "--mask",
        type=Path,
        default=Path("deliverables/dit360_v2/db36_user_redline_mask/db36_g_user_redline_mask_preserve_nonseam.png"),
    )
    ap.add_argument(
        "--result",
        type=Path,
        default=Path(
            "deliverables/dit360_v2/db36_user_redline_mask/G_bmw_pano_user_redline_tau5_fetch/"
            "G_bmw_pano_user_redline_tau5/db36_user_redline_tau5/db36_user_redline_tau5_corecompose.png"
        ),
    )
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    init = read_bgr(args.init)
    result = read_bgr(args.result)
    preserve = read_mask(args.mask) >= 128
    core = ~preserve
    diff = cv2.absdiff(init, result)

    rows = []
    for name, roi in [("long_source", LONG_ROI), ("right_white", RIGHT_ROI), ("lower_right", LOWER_RIGHT_ROI)]:
        row = np.hstack(
            [
                label(fit(crop(init, roi), 360, 200), f"input | {name}"),
                label(fit(crop(result, roi), 360, 200), f"corecompose | {name}"),
                label(fit(crop(diff_heat(init, result), roi), 360, 200), f"diff | {name}"),
            ]
        )
        rows.append(row)
    board = np.vstack(rows)
    board_path = args.out_dir / "db36_reject_review_board.jpg"
    cv2.imwrite(str(board_path), board, [cv2.IMWRITE_JPEG_QUALITY, 95])

    metrics = {
        "init": str(args.init),
        "result": str(args.result),
        "mask": str(args.mask),
        "core_pixels": int(core.sum()),
        "core_fraction": float(core.mean()),
        "outside_mask_max_abs_diff": int(diff[preserve].max()) if np.any(preserve) else None,
        "outside_mask_mean_abs_diff": float(diff[preserve].mean()) if np.any(preserve) else None,
        "core_mean_abs_diff": float(diff[core].mean()) if np.any(core) else None,
        "board": str(board_path),
        "vision_verdict": "reject: fake ground slabs/holes in corecompose; seam not cleanly solved",
    }
    (args.out_dir / "db36_reject_review_manifest.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(board_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
