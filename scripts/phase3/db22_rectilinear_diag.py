from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def _erp_to_rect(
    img: np.ndarray,
    yaw_deg: float,
    pitch_deg: float,
    fov_deg: float,
    out_w: int,
    out_h: int,
    interp: int,
) -> np.ndarray:
    h, w = img.shape[:2]
    yaw = np.deg2rad(yaw_deg)
    pitch = np.deg2rad(pitch_deg)
    fov = np.deg2rad(fov_deg)

    xs = (np.arange(out_w, dtype=np.float32) + 0.5 - out_w / 2.0) / (out_w / 2.0)
    ys = (np.arange(out_h, dtype=np.float32) + 0.5 - out_h / 2.0) / (out_h / 2.0)
    xx, yy = np.meshgrid(xs, ys)
    z = np.ones_like(xx) / np.tan(fov / 2.0)
    dirs = np.stack([xx, -yy, z], axis=-1)
    dirs /= np.linalg.norm(dirs, axis=-1, keepdims=True)

    cy, sy = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    # Local camera looks along +Z. Apply pitch about X, then yaw about Y.
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], dtype=np.float32)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
    dirs = dirs @ (ry @ rx).T

    lon = np.arctan2(dirs[..., 0], dirs[..., 2])
    lat = np.arcsin(np.clip(dirs[..., 1], -1.0, 1.0))
    map_x = ((lon / (2 * np.pi) + 0.5) * w).astype(np.float32)
    map_y = ((0.5 - lat / np.pi) * h).astype(np.float32)
    return cv2.remap(img, map_x, map_y, interp, borderMode=cv2.BORDER_WRAP)


def _load(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return img


def _label(img: np.ndarray, text: str) -> np.ndarray:
    out = img.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(out, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def _overlay_mask(img: np.ndarray, preserve_mask: np.ndarray) -> np.ndarray:
    core = preserve_mask < 128
    out = img.copy().astype(np.float32)
    red = np.zeros_like(out)
    red[..., 2] = 255
    out[core] = out[core] * 0.45 + red[core] * 0.55
    return np.clip(out, 0, 255).astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", required=True)
    ap.add_argument("--mask", required=True)
    ap.add_argument("--db21", required=True)
    ap.add_argument("--db19", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--yaw", type=float, default=155.0)
    ap.add_argument("--pitch", type=float, default=-18.0)
    ap.add_argument("--fov", type=float, default=72.0)
    ap.add_argument("--width", type=int, default=900)
    ap.add_argument("--height", type=int, default=520)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    init = _load(Path(args.init))
    mask = cv2.imread(str(args.mask), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(args.mask)
    db21 = _load(Path(args.db21))
    db19 = _load(Path(args.db19))

    init_rect = _erp_to_rect(init, args.yaw, args.pitch, args.fov, args.width, args.height, cv2.INTER_LINEAR)
    mask_rect = _erp_to_rect(mask, args.yaw, args.pitch, args.fov, args.width, args.height, cv2.INTER_NEAREST)
    mask_overlay = _overlay_mask(init_rect, mask_rect)
    db21_rect = _erp_to_rect(db21, args.yaw, args.pitch, args.fov, args.width, args.height, cv2.INTER_LINEAR)
    db19_rect = _erp_to_rect(db19, args.yaw, args.pitch, args.fov, args.width, args.height, cv2.INTER_LINEAR)

    panels = [
        _label(init_rect, "G_bmw input rectilinear"),
        _label(mask_overlay, "DB21 ultra mask overlay"),
        _label(db21_rect, "DB21 ultra lineprompt result (rejected)"),
        _label(db19_rect, "DB19 sky-only final (accepted sky, seam residual kept)"),
    ]
    top = np.hstack(panels[:2])
    bot = np.hstack(panels[2:])
    montage = np.vstack([top, bot])
    cv2.imwrite(str(out_dir / "db22_rect_bmw_rightline_montage.jpg"), montage, [cv2.IMWRITE_JPEG_QUALITY, 94])
    cv2.imwrite(str(out_dir / "db22_rect_input.jpg"), init_rect, [cv2.IMWRITE_JPEG_QUALITY, 94])
    cv2.imwrite(str(out_dir / "db22_rect_mask_overlay.jpg"), mask_overlay, [cv2.IMWRITE_JPEG_QUALITY, 94])
    print(out_dir / "db22_rect_bmw_rightline_montage.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
