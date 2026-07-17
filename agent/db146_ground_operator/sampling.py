from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from agent.db145_ground_operator.av2_extract import ObservationArrays


# A 2 m x 2 m / 80 x 80 latent patch has only 6,400 unknown RGB texels.
# Keeping at most 60k sensor pixels per fit/evaluation operator is still
# strongly overdetermined, while bounding the EWA support graph on close views.
MAX_OPERATOR_OBSERVATIONS = 60_000


@dataclass(frozen=True)
class SamplingReport:
    original_observations: int
    kept_observations: int
    original_by_source: dict[str, int]
    kept_by_source: dict[str, int]
    selection: str

    def as_dict(self) -> dict[str, object]:
        return {
            "original_observations": self.original_observations,
            "kept_observations": self.kept_observations,
            "original_by_source": self.original_by_source,
            "kept_by_source": self.kept_by_source,
            "selection": self.selection,
        }


def _waterfill_quotas(counts: np.ndarray, total: int) -> np.ndarray:
    """Allocate an equal-view budget, redistributing unused small-view quota."""

    counts = np.asarray(counts, dtype=np.int64)
    if total >= int(counts.sum()):
        return counts.copy()
    quotas = np.zeros_like(counts)
    remaining = int(total)
    active = counts > 0
    while remaining > 0 and active.any():
        active_ids = np.flatnonzero(active)
        if remaining < len(active_ids):
            quotas[active_ids[:remaining]] += 1
            remaining = 0
            break
        share = remaining // len(active_ids)
        capacity = counts[active_ids] - quotas[active_ids]
        increment = np.minimum(capacity, share)
        used = int(increment.sum())
        quotas[active_ids] += increment
        remaining -= used
        active = quotas < counts
        if used == 0:
            break
    if remaining:
        active_ids = np.flatnonzero(quotas < counts)
        take = active_ids[:remaining]
        quotas[take] += 1
        remaining -= len(take)
    if remaining != 0 or int(quotas.sum()) != total:
        raise RuntimeError("failed to allocate deterministic observation budget")
    return quotas


def _spatially_spread_indices(
    candidates: np.ndarray,
    quota: int,
    provenance: dict[str, np.ndarray],
) -> np.ndarray:
    if quota >= len(candidates):
        return candidates
    if "u" in provenance and "v" in provenance:
        u = np.asarray(provenance["u"])[candidates]
        v = np.asarray(provenance["v"])[candidates]
        order = candidates[np.lexsort((u, v))]
    else:
        order = candidates
    # Bin centers avoid systematically preferring either edge of the crop.
    positions = np.floor(
        (np.arange(quota, dtype=np.float64) + 0.5) * len(order) / quota
    ).astype(np.int64)
    return order[positions]


def bound_observations(
    observations: ObservationArrays,
    *,
    max_observations: int = MAX_OPERATOR_OBSERVATIONS,
) -> tuple[ObservationArrays, SamplingReport]:
    """Bound operator size without looking at RGB values or reconstruction output.

    The budget is shared as evenly as possible across source views. Within a
    source, pixels are selected uniformly over image raster order. This both
    controls memory and prevents a single close/high-resolution view from
    dominating the inverse objective.
    """

    if max_observations <= 0:
        raise ValueError("max_observations must be positive")
    source_ids = np.asarray(observations.source_ids, dtype=np.int64)
    n = len(source_ids)
    if n == 0:
        raise ValueError("cannot bound an empty observation set")
    sources, counts = np.unique(source_ids, return_counts=True)
    keep_total = min(n, int(max_observations))
    quotas = _waterfill_quotas(counts, keep_total)
    selected = np.concatenate(
        [
            _spatially_spread_indices(
                np.flatnonzero(source_ids == source),
                int(quota),
                observations.provenance,
            )
            for source, quota in zip(sources, quotas, strict=True)
        ]
    )
    selected.sort()
    provenance = {
        key: np.asarray(value)[selected]
        for key, value in observations.provenance.items()
    }
    bounded = ObservationArrays(
        centers_cell=np.asarray(observations.centers_cell)[selected],
        covariance_cell=np.asarray(observations.covariance_cell)[selected],
        source_ids=source_ids[selected],
        rgb=np.asarray(observations.rgb)[selected],
        provenance=provenance,
    )
    report = SamplingReport(
        original_observations=n,
        kept_observations=len(selected),
        original_by_source={
            str(int(source)): int(count)
            for source, count in zip(sources, counts, strict=True)
        },
        kept_by_source={
            str(int(source)): int(quota)
            for source, quota in zip(sources, quotas, strict=True)
        },
        selection=(
            "identity"
            if keep_total == n
            else "geometry_only_equal_source_spatial_raster"
        ),
    )
    return bounded, report
