"""DB-34: current-best QA pack for DB-32 s40.

Builds a compact review board and manifest from already-produced artifacts.
No generation and no model inference happens here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def read_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def write_rgb(path: Path, rgb: np.ndarray, quality: int = 94) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])


def label(im: np.ndarray, text: str, h: int = 34) -> np.ndarray:
    bar = np.zeros((h, im.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, im])


def fit_panel(im: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = im.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    rs = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((h, w, 3), np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = np.clip(rs, 0, 255).astype(np.uint8)
    return out


def overlay_core(rgb: np.ndarray, core: np.ndarray) -> np.ndarray:
    out = rgb.copy().astype(np.float32)
    red = np.zeros_like(out)
    red[..., 0] = 255
    out[core] = 0.45 * out[core] + 0.55 * red[core]
    return np.clip(out, 0, 255).astype(np.uint8)


def diff_stats(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> dict:
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    if int(mask.sum()) == 0:
        return {"max": 0, "mae": 0.0, "pixels": 0}
    return {
        "max": int(diff[mask].max()),
        "mae": float(diff[mask].mean()),
        "pixels": int(mask.sum()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--db29", required=True, type=Path)
    ap.add_argument("--db32", required=True, type=Path)
    ap.add_argument("--mask", required=True, type=Path)
    ap.add_argument("--gate-json", required=True, type=Path)
    ap.add_argument("--gate-jpg", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    source = read_rgb(args.source)
    db29 = read_rgb(args.db29)
    db32 = read_rgb(args.db32)
    gate = read_rgb(args.gate_jpg)
    mask = cv2.imread(str(args.mask), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(args.mask)
    core = mask < 128
    noncore = ~core
    gate_json = json.loads(args.gate_json.read_text(encoding="utf-8"))

    overlay = overlay_core(db32, core)
    write_rgb(args.out_dir / "db34_db32_core_overlay.jpg", overlay)

    top = slice(0, 520)
    full_panels = [
        label(fit_panel(source, 430, 230), "DB28 source a200"),
        label(fit_panel(db29, 430, 230), "DB29 DiT sky"),
        label(fit_panel(db32, 430, 230), "DB32 s40 current best"),
        label(fit_panel(overlay, 430, 230), "Generated core overlay"),
        label(fit_panel(gate, 430, 230), f"Object gate PASS={gate_json.get('PASS')}"),
    ]
    top_panels = [
        label(fit_panel(source[top], 430, 170), "source top"),
        label(fit_panel(db29[top], 430, 170), "DB29 top"),
        label(fit_panel(db32[top], 430, 170), "DB32 top"),
        label(fit_panel(overlay[top], 430, 170), "core top"),
        label(fit_panel(gate[top], 430, 170), "gate top"),
    ]
    board = np.vstack([np.hstack(full_panels), np.hstack(top_panels)])
    write_rgb(args.out_dir / "db34_current_best_review_board.jpg", board)

    manifest = {
        "current_best": str(args.db32),
        "source_base": str(args.source),
        "previous_best": str(args.db29),
        "mask": str(args.mask),
        "object_gate": gate_json,
        "source_preservation": {
            "db29_noncore_vs_source": diff_stats(db29, source, noncore),
            "db32_noncore_vs_source": diff_stats(db32, source, noncore),
            "db32_noncore_vs_db29": diff_stats(db32, db29, noncore),
            "db32_core_vs_db29": diff_stats(db32, db29, core),
        },
        "accepted_caveats": [
            "DB32 changes only the generated sky core; source-preserved buildings, vehicles, road, trees, poles, and captured sky panel remain byte-exact versus DB29.",
            "The foreground black car remains in the source content.",
            "The lower out-of-FOV black region remains unfilled because ground/full outpaint was rejected as Bosch-unsafe.",
            "The preserved center sky panel discontinuity is reduced versus DB29 but not eliminated.",
        ],
        "review_board": str(args.out_dir / "db34_current_best_review_board.jpg"),
        "core_overlay": str(args.out_dir / "db34_db32_core_overlay.jpg"),
    }
    (args.out_dir / "db34_current_best_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest["source_preservation"], indent=2), flush=True)
    print(args.out_dir / "db34_current_best_review_board.jpg", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
