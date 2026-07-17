from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import cv2
import numpy as np

from .evaluate import HeldoutEvaluation, dense_uv_evidence


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def save_rgb(path: Path, image_rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.asarray(image_rgb)
    if np.issubdtype(image.dtype, np.floating):
        image = np.clip(image * 255.0, 0, 255).astype(np.uint8)
    cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))


def save_texture(path: Path, texture_rgb: np.ndarray, valid: np.ndarray) -> None:
    texture = np.asarray(texture_rgb).copy()
    texture[~np.asarray(valid, bool)] = 0.0
    save_rgb(path, texture)


def save_heldout_evidence(
    directory: Path,
    name: str,
    evaluation: HeldoutEvaluation,
    uv: np.ndarray,
) -> dict[str, object]:
    evidence = dense_uv_evidence(evaluation, uv, padding=2)
    save_rgb(directory / f"{name}_heldout_raw.png", evidence["raw"])
    save_rgb(directory / f"{name}_heldout_pred.png", evidence["predicted"])
    # Shared scaling makes error panels comparable across A/B/C.
    save_rgb(directory / f"{name}_heldout_error_x4.png", np.clip(evidence["error"] * 4.0, 0, 1))
    cv2.imwrite(str(directory / f"{name}_heldout_mask.png"), evidence["mask"])
    return {
        "metrics": asdict(evaluation.metrics),
        "crop_origin_uv": [int(x) for x in evidence["origin_uv"]],
        "crop_hw": list(evidence["mask"].shape),
    }


def _panel(image_rgb: np.ndarray, title: str, size: int = 320) -> np.ndarray:
    image = np.asarray(image_rgb)
    if np.issubdtype(image.dtype, np.floating):
        image = np.clip(image * 255.0, 0, 255).astype(np.uint8)
    if image.ndim == 2:
        image = np.repeat(image[..., None], 3, axis=2)
    h, w = image.shape[:2]
    scale = min(size / max(w, 1), (size - 32) / max(h, 1))
    resized = cv2.resize(
        image,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=cv2.INTER_NEAREST,
    )
    canvas = np.full((size, size, 3), 24, np.uint8)
    y = 32 + (size - 32 - resized.shape[0]) // 2
    x = (size - resized.shape[1]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    cv2.putText(
        canvas,
        title,
        (8, 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    return canvas


def make_patch_board(
    path: Path,
    textures: dict[str, np.ndarray],
    valid_masks: dict[str, np.ndarray],
    heldout: dict[str, HeldoutEvaluation],
) -> None:
    texture_panels = [
        _panel(np.where(valid_masks[name][..., None], textures[name], 0.0), f"{name} latent")
        for name in ("A", "B", "C")
    ]
    mask = valid_masks["B"].astype(np.uint8) * 255
    texture_panels.append(_panel(mask, "evidence-valid (white=real)"))
    # A wide sample strip alone is hard to read; the full-resolution per-source
    # crops written beside this board remain the truth artifacts.
    rows = [np.hstack(texture_panels)]
    raw_strip = heldout["A"].raw_rgb.reshape(1, -1, 3)
    prediction_panels = [_panel(raw_strip, "held-out raw")]
    prediction_panels.extend(
        _panel(heldout[name].predicted_rgb.reshape(1, -1, 3), f"{name} held-out pred")
        for name in ("A", "B", "C")
    )
    rows.append(np.hstack(prediction_panels))
    board = np.vstack(rows)
    save_rgb(path, board)


def _read_rgb(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _letterbox(image_rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    image = np.asarray(image_rgb)
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(
        image,
        (
            max(1, int(round(image.shape[1] * scale))),
            max(1, int(round(image.shape[0] * scale))),
        ),
        interpolation=cv2.INTER_NEAREST,
    )
    canvas = np.full((height, width, 3), 18, np.uint8)
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


def make_verdict_board_from_artifacts(root: Path, output: Path) -> None:
    """Create the six-patch full evidence board used for the DB-145 verdict."""

    root = Path(root)
    roles = (
        ("dry_straight", "dry straight"),
        ("dry_turn", "dry turn"),
        ("wet_or_specular", "wet/specular"),
    )
    columns = (
        ("A_texture.png", "A latent"),
        ("B_texture.png", "B latent"),
        ("C_texture.png", "C latent"),
        ("A_heldout_raw.png", "held-out raw"),
        ("A_heldout_pred.png", "A render"),
        ("B_heldout_pred.png", "B render"),
        ("C_heldout_pred.png", "C render"),
        ("B_heldout_error_x4.png", "B error x4"),
    )
    tile_w, tile_h = 190, 150
    label_w = 260
    header_h = 40
    header = np.full((header_h, label_w + tile_w * len(columns), 3), 20, np.uint8)
    for index, (_, title) in enumerate(columns):
        cv2.putText(
            header,
            title,
            (label_w + index * tile_w + 8, 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (240, 240, 240),
            1,
            cv2.LINE_AA,
        )
    rows = [header]
    for role, role_title in roles:
        for level in ("high", "low"):
            directory = root / role / level
            metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
            heldout = metrics["heldout_metrics_all"]
            a = heldout["A"]["robust_rgb_mae"]
            b = heldout["B"]["robust_rgb_mae"]
            c = heldout["C"]["robust_rgb_mae"]
            b_delta = 100.0 * (a - b) / a
            c_delta = 100.0 * (a - c) / a
            label = np.full((tile_h, label_w, 3), 28, np.uint8)
            lines = (
                f"{role_title} / {level}",
                f"A {a:.5f}",
                f"B {b:.5f} ({b_delta:+.1f}%)",
                f"C {c:.5f} ({c_delta:+.1f}%)",
                f"train {metrics['extraction']['n_training_observations']:,}",
                f"held {metrics['extraction']['n_heldout_observations']:,}",
            )
            for line_index, text in enumerate(lines):
                cv2.putText(
                    label,
                    text,
                    (8, 23 + line_index * 22),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.48,
                    (245, 245, 245),
                    1,
                    cv2.LINE_AA,
                )
            panels = [
                _letterbox(_read_rgb(directory / filename), tile_w, tile_h)
                for filename, _ in columns
            ]
            rows.append(np.hstack([label, *panels]))
    save_rgb(output, np.vstack(rows))
