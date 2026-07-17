from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from agent.db145_ground_operator.evaluate import HeldoutEvaluation
from agent.db145_ground_operator.report import save_rgb


def _panel(image_rgb: np.ndarray, title: str, *, width: int = 300, height: int = 250) -> np.ndarray:
    image = np.asarray(image_rgb)
    if np.issubdtype(image.dtype, np.floating):
        image = np.clip(image * 255.0, 0, 255).astype(np.uint8)
    if image.ndim == 2:
        image = np.repeat(image[..., None], 3, axis=2)
    scale = min(width / max(image.shape[1], 1), (height - 34) / max(image.shape[0], 1))
    resized = cv2.resize(
        image,
        (
            max(1, int(round(image.shape[1] * scale))),
            max(1, int(round(image.shape[0] * scale))),
        ),
        interpolation=cv2.INTER_NEAREST,
    )
    canvas = np.full((height, width, 3), 22, np.uint8)
    x = (width - resized.shape[1]) // 2
    y = 34 + (height - 34 - resized.shape[0]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    cv2.putText(
        canvas,
        title,
        (8, 23),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    return canvas


def _strip(values: np.ndarray) -> np.ndarray:
    return np.asarray(values, np.float32).reshape(1, -1, 3)


def make_safe_patch_board(
    path: Path,
    *,
    textures: dict[str, np.ndarray],
    safe_inverse_mask: np.ndarray,
    uncertainty: np.ndarray,
    heldout: dict[str, HeldoutEvaluation],
    selected_label: str,
) -> None:
    """Write the compact eye-gate board for one DB-146 patch."""

    first = np.hstack(
        [
            _panel(textures["A"], "A baseline"),
            _panel(textures["B"], "B full inverse"),
            _panel(textures["D"], f"D safe ({selected_label})"),
            _panel(safe_inverse_mask.astype(np.uint8) * 255, "safe inverse mask"),
            _panel(uncertainty, "cross-fold uncertainty"),
        ]
    )
    raw = _strip(heldout["A"].raw_rgb)
    second = np.hstack(
        [
            _panel(raw, "untouched outer raw"),
            _panel(_strip(heldout["A"].predicted_rgb), "A outer render"),
            _panel(_strip(heldout["D"].predicted_rgb), "D outer render"),
            _panel(_strip(np.clip(heldout["A"].absolute_error * 4.0, 0, 1)), "A error x4"),
            _panel(_strip(np.clip(heldout["D"].absolute_error * 4.0, 0, 1)), "D error x4"),
        ]
    )
    save_rgb(Path(path), np.vstack((first, second)))
