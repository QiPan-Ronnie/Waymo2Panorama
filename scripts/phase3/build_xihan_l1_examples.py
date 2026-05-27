"""Build a side-by-side panel of 2 representative L1 sphere baseline ERPs for Xihan.

Picks one far-field-clean case + one near-field-ghost case from existing
deliverables/l1_baseline_diverse/.

Output: deliverables/xihan/l1_examples_panel.png (2 rows, annotated).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "deliverables" / "l1_baseline_diverse"
OUT_DIR = REPO / "deliverables" / "xihan"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def label_band(img: np.ndarray, text: str, height: int = 48) -> np.ndarray:
    """Add a black caption band above img with white text."""
    H, W = img.shape[:2]
    band = np.zeros((height, W, 3), dtype=np.uint8)
    cv2.putText(
        band, text,
        org=(16, height - 14),
        fontFace=cv2.FONT_HERSHEY_SIMPLEX,
        fontScale=0.7,
        color=(255, 255, 255),
        thickness=1,
        lineType=cv2.LINE_AA,
    )
    return np.concatenate([band, img], axis=0)


def main() -> None:
    case_a = SRC / "0bae3b5e_a030_L1_multiband_1024x2048.png"   # far-field clean
    case_b = SRC / "fbee355f_a030_L1_multiband_1024x2048.png"   # near-field ghost

    img_a = cv2.imread(str(case_a))
    img_b = cv2.imread(str(case_b))
    assert img_a is not None and img_b is not None, "input PNG missing"

    # Both are 1024x2048; annotate
    img_a = label_band(img_a,
        "Example A: 0bae3b5e anchor 30 -- urban intersection, far-field. L1 succeeds.")
    img_b = label_band(img_b,
        "Example B: fbee355f anchor 30 -- parking lot, near-field white truck. L1 ghosts.")

    panel = np.concatenate([img_a, img_b], axis=0)

    out = OUT_DIR / "l1_examples_panel.png"
    cv2.imwrite(str(out), panel)
    print(f"wrote {out}  ({panel.shape[1]}x{panel.shape[0]})")

    # also save individual annotated for easier embedding
    cv2.imwrite(str(OUT_DIR / "l1_example_A_farfield_clean.png"), img_a)
    cv2.imwrite(str(OUT_DIR / "l1_example_B_nearfield_ghost.png"), img_b)


if __name__ == "__main__":
    main()
