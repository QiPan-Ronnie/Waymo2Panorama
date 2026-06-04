"""DB-36: build an ultra-narrow DiT360 mask for the user-marked seam.

Mask convention follows run_dit360_trimap_clamp.py:
  white/255 = preserve source
  black/0   = core region to generate
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


H, W = 1024, 2048
LONG_ROI = (850, 420, 1650, 720)
RIGHT_ROI = (1440, 360, 2048, 720)


def read_bgr(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    if img.shape[:2] != (H, W):
        img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    return img


def draw_core() -> np.ndarray:
    core = np.zeros((H, W), np.uint8)

    # Long dark-wall / curb source-boundary wobble in the user-marked red-line
    # area. Kept low on the ground/curb, away from storefronts and signage.
    cv2.polylines(
        core,
        [
            np.array(
                [
                    (1065, 640),
                    (1115, 602),
                    (1185, 606),
                    (1240, 632),
                    (1320, 654),
                    (1410, 664),
                    (1515, 648),
                ],
                dtype=np.int32,
            )
        ],
        isClosed=False,
        color=255,
        thickness=14,
        lineType=cv2.LINE_AA,
    )

    # Right-ground white-line discontinuity. The line stays below the BMW body
    # and targets only the lower road/sidewalk seam.
    cv2.polylines(
        core,
        [
            np.array(
                [
                    (1655, 652),
                    (1760, 684),
                    (1885, 678),
                    (1980, 625),
                    (2047, 572),
                ],
                dtype=np.int32,
            )
        ],
        isClosed=False,
        color=255,
        thickness=14,
        lineType=cv2.LINE_AA,
    )

    # Small cyan/purple fringe at the lower edge of the right-line seam.
    cv2.ellipse(core, (1840, 695), (52, 11), -8, 0, 360, 255, -1, cv2.LINE_AA)

    # Hard safety exclusions: do not let the core enter the BMW body or main
    # building/sign surfaces even if anti-aliased line pixels drift there.
    exclude = np.zeros((H, W), np.uint8)
    cv2.rectangle(exclude, (1390, 390), (1785, 642), 255, -1)  # BMW body / windshield region
    cv2.rectangle(exclude, (1560, 300), (2047, 555), 255, -1)  # right building/sign upper facade
    cv2.rectangle(exclude, (930, 365), (1370, 565), 255, -1)   # dark storefront/sign upper facade
    core[exclude > 0] = 0
    return core


def crop(img: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return img[y0:y1, x0:x1]


def dilate(mask: np.ndarray, radius_px: int) -> np.ndarray:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius_px * 2 + 1, radius_px * 2 + 1))
    return cv2.dilate(mask, k)


def preview(init: np.ndarray, core: np.ndarray, halo_px: int) -> np.ndarray:
    halo = dilate(core, halo_px)
    halo_only = (halo > 0) & (core == 0)
    core_b = core > 0
    out = init.copy().astype(np.float32)
    yellow = np.zeros_like(out)
    yellow[..., 1] = 210
    yellow[..., 2] = 255
    red = np.zeros_like(out)
    red[..., 2] = 255
    out[halo_only] = out[halo_only] * 0.58 + yellow[halo_only] * 0.42
    out[core_b] = out[core_b] * 0.35 + red[core_b] * 0.65
    return np.clip(out, 0, 255).astype(np.uint8)


def label(img: np.ndarray, text: str, h: int = 30) -> np.ndarray:
    bar = np.zeros((h, img.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, img])


def fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = img.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    rs = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((h, w, 3), np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = rs
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", type=Path, default=Path("deliverables/ghostkill/G_bmw_pano.jpg"))
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--halo-px", type=int, default=16)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    init = read_bgr(args.init)
    core = draw_core()
    preserve = np.where(core > 0, 0, 255).astype(np.uint8)
    prev = preview(init, core, args.halo_px)

    mask_path = args.out_dir / "db36_g_user_redline_mask_preserve_nonseam.png"
    cv2.imwrite(str(mask_path), preserve)
    cv2.imwrite(str(args.out_dir / "db36_g_user_redline_core.png"), core)
    cv2.imwrite(str(args.out_dir / "db36_g_user_redline_trimap_preview.jpg"), prev, [cv2.IMWRITE_JPEG_QUALITY, 95])

    panels = [
        label(fit(init, 500, 250), "G input"),
        label(fit(prev, 500, 250), "full mask preview"),
        label(fit(crop(prev, LONG_ROI), 500, 250), "long/source-boundary ROI"),
        label(fit(crop(prev, RIGHT_ROI), 500, 250), "right white-line ROI"),
    ]
    board = np.vstack(panels)
    board_path = args.out_dir / "db36_g_user_redline_mask_board.jpg"
    cv2.imwrite(str(board_path), board, [cv2.IMWRITE_JPEG_QUALITY, 95])

    ys, xs = np.where(core > 0)
    manifest = {
        "init": str(args.init),
        "mask_convention": "white/255 preserves source; black/0 generates",
        "mask": str(mask_path),
        "core_pixels": int((core > 0).sum()),
        "core_fraction": float((core > 0).mean()),
        "halo_px": args.halo_px,
        "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
        "long_roi": list(LONG_ROI),
        "right_roi": list(RIGHT_ROI),
        "board": str(board_path),
    }
    (args.out_dir / "db36_g_user_redline_mask_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(board_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
