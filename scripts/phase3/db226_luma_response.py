"""Raw same-ray observations for fixed cross-log luminance diagnostics."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


RAW_PAIR_SCHEMA_VERSION = "db226.raw_same_ray.v1"
PROFILE_SCHEMA_VERSION = "db226.fixed_brightness_profile.v1"
DEFAULT_LOG_LUMA_EDGES = tuple(
    float(value)
    for value in np.log([1, 4, 8, 16, 32, 64, 96, 128, 160, 192, 224, 256])
)


@dataclass(frozen=True)
class PairSamples:
    """Versioned raw observations for one ordered camera pair."""

    schema_version: str
    rgb_a: np.ndarray
    rgb_b: np.ndarray
    erp_flat_index: np.ndarray
    xy_a: np.ndarray
    xy_b: np.ndarray
    depth_m: np.ndarray
    parallax_deg: np.ndarray


def collect_pair_samples(
    *,
    rgb_a: np.ndarray,
    rgb_b: np.ndarray,
    erp_flat_index: np.ndarray,
    xy_a: np.ndarray,
    xy_b: np.ndarray,
    depth_m: np.ndarray,
    parallax_deg: np.ndarray,
) -> PairSamples:
    """Copy raw same-ray values into the versioned diagnostic contract."""

    arrays = {
        "rgb_a": np.asarray(rgb_a),
        "rgb_b": np.asarray(rgb_b),
        "erp_flat_index": np.asarray(erp_flat_index),
        "xy_a": np.asarray(xy_a),
        "xy_b": np.asarray(xy_b),
        "depth_m": np.asarray(depth_m),
        "parallax_deg": np.asarray(parallax_deg),
    }
    for name in ("rgb_a", "rgb_b"):
        value = arrays[name]
        if value.ndim != 2 or value.shape[1] != 3:
            raise ValueError(f"{name} must have shape (N, 3)")
        if not np.issubdtype(value.dtype, np.floating):
            raise ValueError(f"{name} must contain floating-point raw RGB")
    sample_count = len(arrays["rgb_a"])
    if arrays["rgb_b"].shape[0] != sample_count:
        raise ValueError("rgb_b length must match rgb_a")
    expected_shapes = {
        "erp_flat_index": (sample_count,),
        "xy_a": (sample_count, 2),
        "xy_b": (sample_count, 2),
        "depth_m": (sample_count,),
        "parallax_deg": (sample_count,),
    }
    for name, shape in expected_shapes.items():
        if arrays[name].shape != shape:
            raise ValueError(f"{name} must have shape {shape}")
    if not np.issubdtype(arrays["erp_flat_index"].dtype, np.integer):
        raise ValueError("erp_flat_index must contain integer indices")
    for name, value in arrays.items():
        if not np.isfinite(value).all():
            raise ValueError(f"{name} must contain only finite values")

    return PairSamples(
        schema_version=RAW_PAIR_SCHEMA_VERSION,
        rgb_a=arrays["rgb_a"].copy(),
        rgb_b=arrays["rgb_b"].copy(),
        erp_flat_index=arrays["erp_flat_index"].copy(),
        xy_a=arrays["xy_a"].copy(),
        xy_b=arrays["xy_b"].copy(),
        depth_m=arrays["depth_m"].copy(),
        parallax_deg=arrays["parallax_deg"].copy(),
    )


def fixed_brightness_profile(
    samples: PairSamples,
    *,
    gain_log_a: float = 0.0,
    gain_log_b: float = 0.0,
    log_luma_edges: Sequence[float] = DEFAULT_LOG_LUMA_EDGES,
    min_usable_n: int = 8,
    sat_lo: float = 10.0,
    sat_hi: float = 245.0,
    max_parallax_deg: float | None = None,
) -> dict[str, object]:
    """Report signed residuals in fixed shared-corrected bins without fitting a curve."""

    if samples.schema_version != RAW_PAIR_SCHEMA_VERSION:
        raise ValueError(f"unsupported raw pair schema: {samples.schema_version!r}")
    if not math.isfinite(gain_log_a) or not math.isfinite(gain_log_b):
        raise ValueError("report gains must be finite")
    edges = np.asarray(log_luma_edges, dtype=np.float64)
    if edges.ndim != 1 or len(edges) < 2 or not np.isfinite(edges).all():
        raise ValueError("log_luma_edges must be a finite 1D sequence")
    if not np.all(np.diff(edges) > 0):
        raise ValueError("log_luma_edges must be strictly increasing")
    if not isinstance(min_usable_n, int) or isinstance(min_usable_n, bool) or min_usable_n < 1:
        raise ValueError("min_usable_n must be a positive integer")
    if not math.isfinite(sat_lo) or not math.isfinite(sat_hi) or sat_lo >= sat_hi:
        raise ValueError("saturation bounds must be finite and increasing")
    if max_parallax_deg is not None and (
        not math.isfinite(max_parallax_deg) or max_parallax_deg < 0
    ):
        raise ValueError("max_parallax_deg must be finite and nonnegative")

    luma_a = np.maximum(samples.rgb_a.mean(axis=1), 1e-6)
    luma_b = np.maximum(samples.rgb_b.mean(axis=1), 1e-6)
    corrected_log_luma_a = np.log(luma_a) + gain_log_a
    corrected_log_luma_b = np.log(luma_b) + gain_log_b
    shared_corrected = 0.5 * (corrected_log_luma_a + corrected_log_luma_b)
    signed_residual = corrected_log_luma_b - corrected_log_luma_a
    saturated = (
        (samples.rgb_a <= sat_lo).any(axis=1)
        | (samples.rgb_a >= sat_hi).any(axis=1)
        | (samples.rgb_b <= sat_lo).any(axis=1)
        | (samples.rgb_b >= sat_hi).any(axis=1)
    )
    parallax_ok = np.ones(len(samples.rgb_a), dtype=bool)
    if max_parallax_deg is not None:
        parallax_ok &= samples.parallax_deg <= max_parallax_deg
    usable = ~saturated & parallax_ok
    bin_index = np.searchsorted(edges, shared_corrected, side="right") - 1

    rows: list[dict[str, object]] = []
    for index in range(len(edges) - 1):
        selected = bin_index == index
        selected_usable = selected & usable
        values = signed_residual[selected_usable]
        usable_n = int(selected_usable.sum())
        reliable = usable_n >= min_usable_n
        if reliable:
            median = float(np.median(values))
            mad = float(np.median(np.abs(values - median)))
            abs_p90 = float(np.quantile(np.abs(values), 0.90))
        else:
            median = mad = abs_p90 = None
        rows.append(
            {
                "log_luma_lo": float(edges[index]),
                "log_luma_hi": float(edges[index + 1]),
                "n": int(selected.sum()),
                "usable_n": usable_n,
                "saturated_n": int((selected & saturated).sum()),
                "reliable": reliable,
                "signed_log_luma_median": median,
                "signed_log_luma_mad": mad,
                "abs_log_luma_p90": abs_p90,
            }
        )
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "raw_pair_schema_version": samples.schema_version,
        "brightness_coordinate": "shared_corrected_log_luma",
        "log_luma_edges": edges.tolist(),
        "gain_log_a": float(gain_log_a),
        "gain_log_b": float(gain_log_b),
        "min_usable_n": min_usable_n,
        "sat_lo": float(sat_lo),
        "sat_hi": float(sat_hi),
        "max_parallax_deg": max_parallax_deg,
        "bins": rows,
    }
