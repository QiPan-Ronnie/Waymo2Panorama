"""Small, testable DB-214 policies used by the production panorama renderer."""

from __future__ import annotations

import math

import numpy as np


def annotation_enabled(policy: str, has_annotations: bool) -> bool:
    """Return whether annotation-derived pixels may enter the rendered band."""

    if policy == "raw_sensor":
        return False
    if policy == "composite":
        return bool(has_annotations)
    raise ValueError(f"unknown annotation policy: {policy!r}")


def pair_evidence_weights(
    rho: float | None,
    sample_count: int,
    prior_fraction: float,
) -> tuple[float, float, float]:
    """Continuously split a camera-pair edge between evidence and prior.

    Positive correlation squared is the fraction of shared luminance variation
    explained by a same-surface exposure-offset model.  Uncorrelated, negative,
    flat, or non-finite pairs therefore contribute no measured offset, while the
    complementary fraction falls back to the zero-difference prior.
    """

    n = max(int(sample_count), 0)
    if rho is None or not math.isfinite(float(rho)):
        confidence = 0.0
    else:
        confidence = min(max(float(rho), 0.0), 1.0) ** 2
    measurement_weight = n * confidence
    prior_weight = n * (1.0 - confidence) * float(prior_fraction)
    return measurement_weight, prior_weight, confidence


def angular_overlap_weight(overlap: np.ndarray, ramp_angle_rad: float) -> np.ndarray:
    """Fine-depth weight from angular distance to overlap on a periodic ERP."""

    from scipy.ndimage import distance_transform_edt

    overlap = np.asarray(overlap, dtype=bool)
    if overlap.ndim != 2:
        raise ValueError("overlap must be a 2D ERP mask")
    if ramp_angle_rad <= 0:
        raise ValueError("ramp_angle_rad must be positive")
    height, width = overlap.shape
    tiled = np.concatenate([overlap, overlap, overlap], axis=1)
    angular_distance = distance_transform_edt(
        ~tiled,
        sampling=(math.pi / height, 2.0 * math.pi / width),
    )[:, width : 2 * width]
    return np.clip(1.0 - angular_distance / ramp_angle_rad, 0.0, 1.0).astype(np.float32)
