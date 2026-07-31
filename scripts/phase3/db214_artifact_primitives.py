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


def ownership_boundary_indices(bestcam: np.ndarray) -> dict[tuple[int, int], np.ndarray]:
    """Return flat ERP pixels touching each camera-ownership boundary.

    Horizontal adjacency is periodic because the first and last ERP columns are
    neighbours.  Vertical adjacency is not periodic.  Both sides of every edge
    are returned so later diagnostics can sample both cameras at the same 3D
    rays instead of comparing unrelated neighbouring scene pixels.
    """

    owners = np.asarray(bestcam)
    if owners.ndim != 2:
        raise ValueError("bestcam must be a 2D ERP ownership map")
    height, width = owners.shape
    buckets: dict[tuple[int, int], list[np.ndarray]] = {}

    def add_edges(
        camera_a: np.ndarray,
        camera_b: np.ndarray,
        index_a: np.ndarray,
        index_b: np.ndarray,
    ) -> None:
        valid = (camera_a >= 0) & (camera_b >= 0) & (camera_a != camera_b)
        if not valid.any():
            return
        a = camera_a[valid].astype(np.int64, copy=False)
        b = camera_b[valid].astype(np.int64, copy=False)
        lo = np.minimum(a, b)
        hi = np.maximum(a, b)
        pairs = np.unique(np.column_stack([lo, hi]), axis=0)
        ia = index_a[valid]
        ib = index_b[valid]
        for first, second in pairs:
            take = (lo == first) & (hi == second)
            buckets.setdefault((int(first), int(second)), []).extend(
                [ia[take], ib[take]]
            )

    flat = np.arange(height * width, dtype=np.int64).reshape(height, width)
    add_edges(owners[:, 1:], owners[:, :-1], flat[:, 1:], flat[:, :-1])
    add_edges(owners[:, :1], owners[:, -1:], flat[:, :1], flat[:, -1:])
    add_edges(owners[1:, :], owners[:-1, :], flat[1:, :], flat[:-1, :])
    return {
        pair: np.unique(np.concatenate(chunks))
        for pair, chunks in sorted(buckets.items())
    }


def photometric_pair_residual_stats(
    rgb_a: np.ndarray,
    rgb_b: np.ndarray,
    gain_a: np.ndarray | float,
    gain_b: np.ndarray | float,
    *,
    xy_a: np.ndarray | None = None,
    xy_b: np.ndarray | None = None,
    bins: int = 4,
) -> dict[str, float | int | None]:
    """Summarize same-3D-point photometric residuals for one camera pair.

    A global exposure gain can translate the log-luminance residual but cannot
    remove a residual that changes across camera coordinates.  Reporting both
    the corrected robust spread and its low-frequency spatial range therefore
    distinguishes a bad scalar estimate from a scalar model that is too weak.
    Chroma is measured with log(R/G, B/G), independent of scene brightness.
    """

    a = np.asarray(rgb_a, dtype=np.float64)
    b = np.asarray(rgb_b, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 3:
        raise ValueError("rgb_a and rgb_b must have matching shape (N, 3)")
    ga = np.broadcast_to(np.asarray(gain_a, dtype=np.float64), (3,))
    gb = np.broadcast_to(np.asarray(gain_b, dtype=np.float64), (3,))
    finite = np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    finite &= (a.mean(axis=1) > 0.0) & (b.mean(axis=1) > 0.0)
    if not finite.any():
        raise ValueError("no finite positive photometric samples")
    a = a[finite]
    b = b[finite]
    ac = a * np.exp(ga)[None, :]
    bc = b * np.exp(gb)[None, :]
    raw_luma_a = np.maximum(a.mean(axis=1), 1e-6)
    raw_luma_b = np.maximum(b.mean(axis=1), 1e-6)
    corrected_luma_a = np.maximum(ac.mean(axis=1), 1e-6)
    corrected_luma_b = np.maximum(bc.mean(axis=1), 1e-6)
    raw_residual = np.log(raw_luma_b) - np.log(raw_luma_a)
    corrected_residual = np.log(corrected_luma_b) - np.log(corrected_luma_a)
    corrected_median = float(np.median(corrected_residual))
    corrected_mad = float(np.median(np.abs(corrected_residual - corrected_median)))

    chroma_a = np.column_stack(
        [
            np.log(np.maximum(ac[:, 0], 1e-6) / np.maximum(ac[:, 1], 1e-6)),
            np.log(np.maximum(ac[:, 2], 1e-6) / np.maximum(ac[:, 1], 1e-6)),
        ]
    )
    chroma_b = np.column_stack(
        [
            np.log(np.maximum(bc[:, 0], 1e-6) / np.maximum(bc[:, 1], 1e-6)),
            np.log(np.maximum(bc[:, 2], 1e-6) / np.maximum(bc[:, 1], 1e-6)),
        ]
    )
    chroma_residual = np.linalg.norm(chroma_b - chroma_a, axis=1)

    def spatial_range(xy: np.ndarray | None) -> float | None:
        if xy is None:
            return None
        coords = np.asarray(xy, dtype=np.float64)
        if coords.shape != (finite.size, 2):
            raise ValueError("xy arrays must have shape (N, 2)")
        coords = coords[finite]
        if bins < 2:
            raise ValueError("bins must be at least 2")
        cells = np.floor(np.clip(coords, 0.0, 1.0 - np.finfo(float).eps) * bins).astype(int)
        codes = cells[:, 1] * bins + cells[:, 0]
        min_count = max(8, int(math.ceil(len(codes) * 0.01)))
        medians = [
            float(np.median(corrected_residual[codes == code]))
            for code in np.unique(codes)
            if int((codes == code).sum()) >= min_count
        ]
        if len(medians) < 2:
            return None
        return float(max(medians) - min(medians))

    if raw_luma_a.std() < 1e-9 or raw_luma_b.std() < 1e-9:
        rho = None
    else:
        rho = float(np.corrcoef(np.log(raw_luma_a), np.log(raw_luma_b))[0, 1])
    return {
        "n": int(len(a)),
        "rho_log_luma": rho,
        "raw_log_luma_median": float(np.median(raw_residual)),
        "corrected_log_luma_median": corrected_median,
        "corrected_log_luma_mad": corrected_mad,
        "corrected_abs_log_luma_p90": float(
            np.quantile(np.abs(corrected_residual), 0.90)
        ),
        "corrected_chroma_logratio_median": float(np.median(chroma_residual)),
        "corrected_chroma_logratio_p90": float(np.quantile(chroma_residual, 0.90)),
        "camera_a_spatial_median_range": spatial_range(xy_a),
        "camera_b_spatial_median_range": spatial_range(xy_b),
    }
