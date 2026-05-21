"""
Compose the route-14 HDR before/after deliverable figure.

Crops the central content band (removes black top/bottom borders) and stacks
two anchors with before/after captions. Outputs to deliverables/images/.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent  # repo root

# Anchor sources: (anchor_id, before_path, after_path)
SOURCES = [
    (60, ROOT / "outputs/phase3/p3.7_hdr/anchor_060/before.png",
         ROOT / "outputs/phase3/p3.7_hdr/anchor_060/after.png"),
    (90, Path("D:/tmp/hdr_out/anchor_090/before.png"),
         Path("D:/tmp/hdr_out/anchor_090/after.png")),
]
OUT = ROOT / "deliverables/images/route_hdr_before_after.png"


def _crop_content(img: np.ndarray, pad: int = 10) -> np.ndarray:
    """Crop to the bounding box of non-black pixels (vertical only)."""
    gray = img.mean(axis=2)
    row_active = (gray > 5).any(axis=1)
    if not row_active.any():
        return img
    rows = np.where(row_active)[0]
    y0 = max(0, rows[0] - pad)
    y1 = min(img.shape[0], rows[-1] + 1 + pad)
    return img[y0:y1]


def _stamp(img: np.ndarray, text: str, h: int = 36) -> np.ndarray:
    bar = np.full((h, img.shape[1], 3), 32, dtype=np.uint8)
    cv2.putText(
        bar, text, (12, h - 12),
        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 1, cv2.LINE_AA,
    )
    return np.concatenate([bar, img], axis=0)


def _hsep(w: int, h: int = 6, val: int = 255) -> np.ndarray:
    return np.full((h, w, 3), val, dtype=np.uint8)


def _vsep(h: int, w: int = 6, val: int = 255) -> np.ndarray:
    return np.full((h, w, 3), val, dtype=np.uint8)


def build_one_anchor(anchor_id: int, before_p: Path, after_p: Path) -> np.ndarray:
    before = np.asarray(Image.open(before_p).convert("RGB"))
    after = np.asarray(Image.open(after_p).convert("RGB"))
    # Match crop based on union of valid rows for fair comparison
    valid_b = (before.mean(axis=2) > 5).any(axis=1)
    valid_a = (after.mean(axis=2) > 5).any(axis=1)
    union = valid_b | valid_a
    rows = np.where(union)[0]
    pad = 8
    y0 = max(0, rows[0] - pad)
    y1 = min(before.shape[0], rows[-1] + 1 + pad)
    before_c = before[y0:y1]
    after_c = after[y0:y1]

    before_lbl = _stamp(
        before_c, f"Anchor {anchor_id} - BEFORE  (L1 sphere ERP, raw per-cam colors)",
    )
    after_lbl = _stamp(
        after_c, f"Anchor {anchor_id} - AFTER  (L1 + cross-cam HDR / WB compensation)",
    )
    sep = _hsep(before_lbl.shape[1], h=6, val=255)
    return np.concatenate([before_lbl, sep, after_lbl], axis=0)


def main() -> int:
    panels: list[np.ndarray] = []
    for aid, b, a in SOURCES:
        if not b.exists() or not a.exists():
            print(f"[warn] skipping anchor {aid}: missing {b} or {a}")
            continue
        panels.append(build_one_anchor(aid, b, a))

    if not panels:
        print("[err] no panels built", file=sys.stderr)
        return 1

    # Vertical gap between anchors
    W = panels[0].shape[1]
    gap = np.full((24, W, 3), 16, dtype=np.uint8)
    stacked_parts: list[np.ndarray] = []
    for k, p in enumerate(panels):
        if k > 0:
            stacked_parts.append(gap)
        stacked_parts.append(p)
    full = np.concatenate(stacked_parts, axis=0)

    # Header
    header_h = 56
    header = np.full((header_h, W, 3), 0, dtype=np.uint8)
    cv2.putText(
        header, "Route 14 (new-E) - Cross-cam HDR / WB compensation",
        (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.80, (255, 255, 255), 1, cv2.LINE_AA,
    )
    cv2.putText(
        header,
        "Per-cam gain+bias (6 params) solved by global LS + Huber, applied before "
        "multiband blending. 7 ring cams, AV2.",
        (12, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (210, 210, 210), 1, cv2.LINE_AA,
    )

    full = np.concatenate([header, full], axis=0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(full).save(OUT)
    print(f"[ok] wrote {OUT}  shape={full.shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
