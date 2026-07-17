from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import torch

from .operator import EWAObservationSet
from .solver import SolverResult


@dataclass(frozen=True)
class HeldoutMetrics:
    robust_rgb_mae: float
    median_rgb_l2: float
    p95_rgb_l1: float
    n_pixels: int
    coverage: float


@dataclass(frozen=True)
class HeldoutEvaluation:
    metrics: HeldoutMetrics
    predicted_rgb: np.ndarray
    raw_rgb: np.ndarray
    absolute_error: np.ndarray


def render_heldout(
    result: SolverResult,
    observations: EWAObservationSet,
    *,
    use_known_nuisance: bool = False,
) -> np.ndarray:
    """Render held-out raw pixels.

    Strict evaluation defaults to zero shift/unit gain for a wholly unseen
    source.  ``use_known_nuisance`` is only for synthetic operator tests where
    nuisance truth is explicitly part of the fixture.
    """

    n_sources = observations.n_sources
    shift = np.zeros((n_sources, 2), np.float32)
    gain = np.ones(n_sources, np.float32)
    if use_known_nuisance:
        n = min(n_sources, len(result.source_gain))
        shift[:n] = result.source_shift_cell[:n]
        gain[:n] = result.source_gain[:n]
    device = observations.centers_cell.device
    with torch.no_grad():
        texture = torch.as_tensor(result.texture_rgb, dtype=torch.float32, device=device)
        normalized = observations.predict(texture, torch.as_tensor(shift, device=device))
        raw_prediction = normalized / torch.as_tensor(gain, device=device)[
            observations.source_ids, None
        ]
    return raw_prediction.clamp(0.0, 1.0).cpu().numpy()


def evaluate_heldout(
    result: SolverResult,
    observations: EWAObservationSet,
    *,
    use_known_nuisance: bool = False,
) -> HeldoutEvaluation:
    predicted = render_heldout(
        result, observations, use_known_nuisance=use_known_nuisance
    )
    raw = observations.rgb.cpu().numpy()
    absolute = np.abs(predicted - raw)
    per_pixel_l1 = absolute.mean(axis=1)
    cutoff = np.quantile(per_pixel_l1, 0.95) if len(per_pixel_l1) else 0.0
    trimmed = per_pixel_l1[per_pixel_l1 <= cutoff]
    metrics = HeldoutMetrics(
        robust_rgb_mae=float(trimmed.mean()) if len(trimmed) else float("nan"),
        median_rgb_l2=float(np.median(np.linalg.norm(predicted - raw, axis=1))),
        p95_rgb_l1=float(np.quantile(per_pixel_l1, 0.95)),
        n_pixels=len(raw),
        coverage=float(result.evidence_valid.mean()),
    )
    return HeldoutEvaluation(metrics, predicted, raw.copy(), absolute)


def dense_uv_evidence(
    evaluation: HeldoutEvaluation,
    uv: np.ndarray,
    *,
    padding: int = 2,
) -> dict[str, np.ndarray]:
    """Rasterize sparse held-out pixels into raw/pred/error/mask evidence crops."""

    points = np.asarray(uv, dtype=np.int64).reshape(-1, 2)
    if len(points) != evaluation.metrics.n_pixels:
        raise ValueError("uv count differs from held-out observations")
    minimum = points.min(axis=0) - padding
    maximum = points.max(axis=0) + padding
    width, height = (maximum - minimum + 1).tolist()
    shape = (height, width, 3)
    raw = np.zeros(shape, np.float32)
    predicted = np.zeros(shape, np.float32)
    mask = np.zeros((height, width), np.uint8)
    local = points - minimum
    x, y = local[:, 0], local[:, 1]
    raw[y, x] = evaluation.raw_rgb
    predicted[y, x] = evaluation.predicted_rgb
    mask[y, x] = 255
    error = np.abs(raw - predicted)
    return {
        "raw": raw,
        "predicted": predicted,
        "error": error,
        "mask": mask,
        "origin_uv": minimum,
    }


def masked_sobel_edge_mae(evidence: dict[str, np.ndarray]) -> float:
    mask = evidence["mask"] > 0
    if mask.sum() < 1000:
        return float("nan")
    eroded = cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    raw_gray = cv2.cvtColor(evidence["raw"], cv2.COLOR_RGB2GRAY)
    pred_gray = cv2.cvtColor(evidence["predicted"], cv2.COLOR_RGB2GRAY)
    raw_edge = cv2.magnitude(
        cv2.Sobel(raw_gray, cv2.CV_32F, 1, 0),
        cv2.Sobel(raw_gray, cv2.CV_32F, 0, 1),
    )
    pred_edge = cv2.magnitude(
        cv2.Sobel(pred_gray, cv2.CV_32F, 1, 0),
        cv2.Sobel(pred_gray, cv2.CV_32F, 0, 1),
    )
    return float(np.abs(raw_edge - pred_edge)[eroded].mean()) if eroded.any() else float("nan")
