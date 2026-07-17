from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Mapping, Sequence

import cv2
import numpy as np

from agent.db145_ground_operator.baseline import BaselineResult
from agent.db145_ground_operator.solver import SolverResult


# Ordered coarse -> fine.  ``sigma=0`` is the untruncated DB-145 inverse.
BAND_SPECS: tuple[tuple[str, float], ...] = (
    ("lp8", 8.0),
    ("lp4", 4.0),
    ("lp2", 2.0),
    ("lp1", 1.0),
    ("full", 0.0),
)


@dataclass(frozen=True)
class FoldBandMetrics:
    fold: int
    baseline_robust_mae: float
    candidate_robust_mae: float
    baseline_median_l2: float
    candidate_median_l2: float
    checker_ratio: float

    @property
    def robust_gain(self) -> float:
        return (self.baseline_robust_mae - self.candidate_robust_mae) / max(
            self.baseline_robust_mae, 1.0e-8
        )

    @property
    def median_l2_gain(self) -> float:
        return (self.baseline_median_l2 - self.candidate_median_l2) / max(
            self.baseline_median_l2, 1.0e-8
        )


@dataclass(frozen=True)
class BandVerdict:
    label: str
    sigma_cell: float
    accepted: bool
    reasons: tuple[str, ...]
    median_robust_gain: float
    worst_robust_gain: float
    positive_robust_folds: int
    median_l2_gain: float
    worst_l2_gain: float
    median_checker_ratio: float
    worst_checker_ratio: float
    correction_agreement: float


@dataclass(frozen=True)
class GateDecision:
    selected_label: str
    selected_sigma_cell: float | None
    fallback_reason: str | None
    verdicts: tuple[BandVerdict, ...]

    @property
    def uses_inverse(self) -> bool:
        return self.selected_sigma_cell is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "selected_label": self.selected_label,
            "selected_sigma_cell": self.selected_sigma_cell,
            "uses_inverse": self.uses_inverse,
            "fallback_reason": self.fallback_reason,
            "verdicts": [asdict(verdict) for verdict in self.verdicts],
        }


_GROUP_PATTERN = re.compile(r"^f(?P<frame>\d+):(?P<camera>.+)$")


def _group_identity(group: str) -> tuple[int, str]:
    match = _GROUP_PATTERN.match(group)
    if match is None:
        raise ValueError(f"source group {group!r} does not match f###:camera")
    return int(match.group("frame")), match.group("camera")


def _contiguous_time_folds(
    counts: Mapping[str, int],
    groups: Sequence[str],
    n_folds: int,
) -> tuple[tuple[str, ...], ...]:
    by_time: dict[int, list[str]] = {}
    for group in groups:
        frame, _ = _group_identity(group)
        by_time.setdefault(frame, []).append(group)
    times = sorted(by_time)
    if len(times) < n_folds:
        raise ValueError("fewer distinct source times than inner folds")
    block_counts = np.asarray(
        [sum(counts[group] for group in by_time[frame]) for frame in times],
        np.int64,
    )
    prefix = np.r_[0, np.cumsum(block_counts)]
    target = float(prefix[-1]) / n_folds

    # Exact dynamic programming is unnecessary for three folds and <=24 AV2
    # source times.  Enumerating the two cut positions makes the structural
    # rule transparent and deterministic.
    if n_folds != 3:
        raise ValueError("DB-146 freezes exactly three inner folds")
    options: list[tuple[float, int, int]] = []
    for first in range(1, len(times) - 1):
        for second in range(first + 1, len(times)):
            totals = (
                prefix[first],
                prefix[second] - prefix[first],
                prefix[-1] - prefix[second],
            )
            imbalance = max(abs(float(total) - target) for total in totals) / max(
                target, 1.0
            )
            options.append((imbalance, first, second))
    _, first, second = min(options)
    time_partitions = (times[:first], times[first:second], times[second:])
    return tuple(
        tuple(
            sorted(
                group
                for frame in partition
                for group in by_time[frame]
            )
        )
        for partition in time_partitions
    )


def structured_group_folds(
    group_counts: Mapping[str, int],
    groups: Sequence[str],
    *,
    n_folds: int = 3,
) -> tuple[tuple[str, ...], ...]:
    """Hold out whole cameras or contiguous time blocks, never interleaved views.

    Random or largest-first group assignment leaks neighbouring frames into
    both fit and validation.  DB-146 reconstructs temporal outpainting holes,
    so its inner missingness must have the same structure: leave out complete
    cameras when at least three cameras carry useful evidence; otherwise leave
    out three contiguous time ranges.
    """

    selected = sorted(set(groups))
    if len(selected) < n_folds:
        raise ValueError("fewer source groups than inner folds")
    counts: dict[str, int] = {}
    for group in selected:
        count = int(group_counts.get(group, 0))
        if count <= 0:
            raise ValueError(f"group {group!r} has no positive geometry count")
        counts[group] = count

    by_camera: dict[str, list[str]] = {}
    for group in selected:
        _, camera = _group_identity(group)
        by_camera.setdefault(camera, []).append(group)
    if len(by_camera) >= n_folds:
        bins: list[list[str]] = [[] for _ in range(n_folds)]
        totals = [0] * n_folds
        camera_totals = {
            camera: sum(counts[group] for group in camera_groups)
            for camera, camera_groups in by_camera.items()
        }
        for camera in sorted(by_camera, key=lambda item: (-camera_totals[item], item)):
            target = min(range(n_folds), key=lambda index: (totals[index], index))
            bins[target].extend(by_camera[camera])
            totals[target] += camera_totals[camera]
        total = sum(totals)
        # A camera with only a handful of grazing pixels is not an informative
        # validation fold.  Fall back to time blocks rather than pretending it
        # is independent evidence.
        if all(value >= 0.05 * total for value in totals):
            return tuple(tuple(sorted(fold)) for fold in bins)
    return _contiguous_time_folds(counts, selected, n_folds)


def _gaussian(image: np.ndarray, sigma: float) -> np.ndarray:
    values = np.asarray(image, dtype=np.float32)
    if sigma <= 0:
        return values.copy()
    return cv2.GaussianBlur(
        values,
        (0, 0),
        sigmaX=float(sigma),
        sigmaY=float(sigma),
        borderType=cv2.BORDER_REFLECT101,
    )


def truncated_texture(
    baseline: BaselineResult,
    inverse: SolverResult,
    sigma_cell: float,
) -> np.ndarray:
    """Keep only the selected spatial band of the inverse correction."""

    base = np.asarray(baseline.texture_rgb, np.float32)
    recovered = np.asarray(inverse.texture_rgb, np.float32)
    if base.shape != recovered.shape:
        raise ValueError("baseline and inverse texture shapes differ")
    correction = recovered - base
    candidate = base + _gaussian(correction, sigma_cell)
    # A missing baseline cell has no meaningful zero-centred residual.  It may
    # use the inverse only when the sensor operator itself has evidence.
    missing = ~np.asarray(baseline.valid, bool)
    inverse_valid = np.asarray(inverse.evidence_valid, bool)
    candidate[missing & inverse_valid] = recovered[missing & inverse_valid]
    candidate[missing & ~inverse_valid] = 0.0
    return np.clip(candidate, 0.0, 1.0).astype(np.float32)


def result_from_texture(
    texture: np.ndarray,
    baseline: BaselineResult,
    inverse: SolverResult,
    *,
    uses_inverse: bool,
) -> SolverResult:
    valid = np.asarray(baseline.valid, bool).copy()
    if uses_inverse:
        valid |= np.asarray(inverse.evidence_valid, bool)
    return SolverResult(
        texture_rgb=np.asarray(texture, np.float32),
        evidence_valid=valid,
        source_shift_cell=inverse.source_shift_cell,
        source_gain=inverse.source_gain,
        loss_curve=inverse.loss_curve,
        elapsed_s=inverse.elapsed_s,
        max_cuda_memory_mb=inverse.max_cuda_memory_mb,
    )


def checker_energy(texture: np.ndarray) -> float:
    """Measure one-pixel diagonal alternation, the DB-145 wet failure mode."""

    values = np.asarray(texture, np.float32)
    if values.ndim != 3 or values.shape[-1] != 3:
        raise ValueError("texture must be HxWx3")
    diagonal = (
        values[:-1, :-1]
        - values[1:, :-1]
        - values[:-1, 1:]
        + values[1:, 1:]
    )
    return float(np.mean(np.abs(diagonal)))


def checker_ratio(candidate: np.ndarray, baseline: np.ndarray) -> float:
    # The absolute floor prevents a perfectly flat A patch from making one
    # legitimate edge look infinitely risky.
    return checker_energy(candidate) / max(checker_energy(baseline), 2.0e-3)


def correction_agreement(corrections: Sequence[np.ndarray]) -> float:
    """Median pairwise cosine agreement of independently fitted corrections."""

    vectors: list[np.ndarray] = []
    for correction in corrections:
        values = np.asarray(correction, np.float64)
        # Remove a global exposure offset; agreement must come from structure.
        values = values - np.median(values, axis=(0, 1), keepdims=True)
        vector = values.ravel()
        norm = float(np.linalg.norm(vector))
        if norm > 1.0e-8:
            vectors.append(vector / norm)
    if len(vectors) < 2:
        return 1.0
    pairwise = [
        float(np.dot(vectors[left], vectors[right]))
        for left in range(len(vectors))
        for right in range(left + 1, len(vectors))
    ]
    return float(np.median(pairwise))


def correction_uncertainty(corrections: Sequence[np.ndarray]) -> np.ndarray:
    """Per-texel cross-fold disagreement, normalized to [0,1] for reporting."""

    if not corrections:
        raise ValueError("no corrections")
    stack = np.stack([np.asarray(item, np.float32) for item in corrections])
    disagreement = np.sqrt(np.mean(np.var(stack, axis=0), axis=-1))
    scale = float(np.quantile(disagreement, 0.95))
    return np.clip(disagreement / max(scale, 1.0e-6), 0.0, 1.0).astype(np.float32)


def select_safe_band(
    fold_metrics: Mapping[str, Sequence[FoldBandMetrics]],
    corrections: Mapping[str, Sequence[np.ndarray]],
) -> GateDecision:
    """Choose the finest band that passes the same conservative inner gates."""

    verdicts: list[BandVerdict] = []
    accepted_labels: list[str] = []
    expected_labels = [label for label, _ in BAND_SPECS]
    if set(fold_metrics) != set(expected_labels):
        raise ValueError("fold metrics do not cover the frozen bands")
    if set(corrections) != set(expected_labels):
        raise ValueError("corrections do not cover the frozen bands")

    for label, sigma in BAND_SPECS:
        records = tuple(fold_metrics[label])
        if len(records) < 2 or len(corrections[label]) != len(records):
            raise ValueError(f"band {label} lacks aligned fold evidence")
        robust = np.asarray([record.robust_gain for record in records])
        median_l2 = np.asarray([record.median_l2_gain for record in records])
        checker = np.asarray([record.checker_ratio for record in records])
        agreement = correction_agreement(corrections[label])
        reasons: list[str] = []
        if not np.isfinite(np.r_[robust, median_l2, checker, agreement]).all():
            reasons.append("non_finite")
        if float(np.median(robust)) < 0.005:
            reasons.append("median_robust_gain<0.5%")
        if int((robust > 0.0).sum()) < 2:
            reasons.append("fewer_than_2_positive_robust_folds")
        # A white-mask inverse has no licence to sacrifice one physical
        # missingness structure for gains on the other two.  Even a small
        # negative fold means the corresponding mode is not general evidence.
        if float(robust.min()) < -1.0e-6:
            reasons.append("some_robust_fold_regresses")
        if float(np.median(median_l2)) < 0.0:
            reasons.append("median_l2_not_improved")
        if float(median_l2.min()) < -1.0e-6:
            reasons.append("some_l2_fold_regresses")
        if float(np.median(checker)) > 1.25:
            reasons.append("median_checker_excess")
        if float(checker.max()) > 1.75:
            reasons.append("worst_checker_excess")
        if agreement < -0.10:
            reasons.append("crossfold_correction_disagrees")
        accepted = not reasons
        if accepted:
            accepted_labels.append(label)
        verdicts.append(
            BandVerdict(
                label=label,
                sigma_cell=sigma,
                accepted=accepted,
                reasons=tuple(reasons),
                median_robust_gain=float(np.median(robust)),
                worst_robust_gain=float(robust.min()),
                positive_robust_folds=int((robust > 0.0).sum()),
                median_l2_gain=float(np.median(median_l2)),
                worst_l2_gain=float(median_l2.min()),
                median_checker_ratio=float(np.median(checker)),
                worst_checker_ratio=float(checker.max()),
                correction_agreement=agreement,
            )
        )

    if not accepted_labels:
        summary = ";".join(
            f"{verdict.label}:{','.join(verdict.reasons)}" for verdict in verdicts
        )
        return GateDecision("A", None, summary, tuple(verdicts))
    # BAND_SPECS is coarse -> fine, so the last accepted item is the highest
    # independently proven spatial bandwidth.
    selected = accepted_labels[-1]
    sigma = dict(BAND_SPECS)[selected]
    return GateDecision(selected, sigma, None, tuple(verdicts))
