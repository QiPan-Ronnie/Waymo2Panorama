"""Raw same-ray observations for fixed cross-log luminance diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
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

    def __post_init__(self) -> None:
        if self.schema_version != RAW_PAIR_SCHEMA_VERSION:
            raise ValueError(f"unsupported raw pair schema: {self.schema_version!r}")
        arrays = {
            name: np.asarray(getattr(self, name))
            for name in (
                "rgb_a",
                "rgb_b",
                "erp_flat_index",
                "xy_a",
                "xy_b",
                "depth_m",
                "parallax_deg",
            )
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
        for name in ("xy_a", "xy_b", "depth_m", "parallax_deg"):
            if not np.issubdtype(arrays[name].dtype, np.floating):
                raise ValueError(f"{name} must contain floating-point values")
        for name, value in arrays.items():
            if not np.isfinite(value).all():
                raise ValueError(f"{name} must contain only finite values")
        for name in ("rgb_a", "rgb_b"):
            if ((arrays[name] < 0.0) | (arrays[name] > 255.0)).any():
                raise ValueError(f"{name} code-value RGB must be within [0, 255]")
        if (arrays["erp_flat_index"] < 0).any():
            raise ValueError("erp_flat_index must be nonnegative")
        for name in ("xy_a", "xy_b"):
            if ((arrays[name] < 0.0) | (arrays[name] > 1.0)).any():
                raise ValueError(f"{name} coordinates must be within [0, 1]")
        if (arrays["depth_m"] <= 0.0).any():
            raise ValueError("depth_m must be positive")
        if (arrays["parallax_deg"] < 0.0).any():
            raise ValueError("parallax_deg must be nonnegative")
        for name, value in arrays.items():
            owned = value.copy()
            owned.flags.writeable = False
            object.__setattr__(self, name, owned)


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

    return PairSamples(
        schema_version=RAW_PAIR_SCHEMA_VERSION,
        rgb_a=rgb_a,
        rgb_b=rgb_b,
        erp_flat_index=erp_flat_index,
        xy_a=xy_a,
        xy_b=xy_b,
        depth_m=depth_m,
        parallax_deg=parallax_deg,
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
    underflow = shared_corrected < edges[0]
    overflow = shared_corrected >= edges[-1]
    in_range = ~underflow & ~overflow

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
        "max_parallax_deg": None if max_parallax_deg is None else float(max_parallax_deg),
        "input_n": int(len(shared_corrected)),
        "in_range_n": int(in_range.sum()),
        "underflow_n": int(underflow.sum()),
        "overflow_n": int(overflow.sum()),
        "saturated_n": int(saturated.sum()),
        "parallax_rejected_n": int((~parallax_ok).sum()),
        "bins": rows,
    }


def _checked_log_ids(values: Sequence[str], *, field: str) -> list[str]:
    log_ids = list(values)
    if not log_ids or any(not isinstance(value, str) or not value for value in log_ids):
        raise ValueError(f"{field} must contain nonempty strings")
    if len(set(log_ids)) != len(log_ids):
        raise ValueError(f"{field} must not contain duplicates")
    return sorted(log_ids)


def split_assignment_sha256(
    selected_log_ids: Sequence[str],
    train_log_ids: Sequence[str],
    heldout_log_ids: Sequence[str],
) -> str:
    payload = {
        "heldout_log_ids": sorted(heldout_log_ids),
        "selected_log_ids": sorted(selected_log_ids),
        "train_log_ids": sorted(train_log_ids),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def split_log_ids(
    log_ids: Sequence[str],
    *,
    holdout_fraction: float = 1 / 3,
) -> dict[str, object]:
    """Create a deterministic whole-log split without observing measurements."""

    selected = _checked_log_ids(log_ids, field="log_ids")
    if len(selected) < 2:
        raise ValueError("at least two log_ids are required")
    if not math.isfinite(holdout_fraction) or not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be strictly between zero and one")
    ranked = sorted(
        selected,
        key=lambda log_id: (
            hashlib.sha256(f"db226-v1:{log_id}".encode("utf-8")).hexdigest(),
            log_id,
        ),
    )
    heldout_count = min(max(round(len(selected) * holdout_fraction), 1), len(selected) - 1)
    heldout = sorted(ranked[:heldout_count])
    train = sorted(set(selected) - set(heldout))
    return {
        "selected_log_ids": selected,
        "train_log_ids": train,
        "heldout_log_ids": heldout,
        "split_sha256": split_assignment_sha256(selected, train, heldout),
    }


def canonicalize_pair_frame(row: Mapping[str, object]) -> dict[str, object]:
    """Return one pair-frame in lexical camera order, swapping every A/B field."""

    pair_value = row.get("camera_pair")
    if (
        not isinstance(pair_value, (list, tuple))
        or len(pair_value) != 2
        or any(not isinstance(value, str) or not value for value in pair_value)
        or pair_value[0] == pair_value[1]
    ):
        raise ValueError("camera_pair must contain two distinct nonempty camera names")
    samples = row.get("samples")
    if not isinstance(samples, PairSamples):
        raise ValueError("samples must be PairSamples")
    gain_a = float(row.get("gain_log_a", 0.0))
    gain_b = float(row.get("gain_log_b", 0.0))
    if not math.isfinite(gain_a) or not math.isfinite(gain_b):
        raise ValueError("pair-frame gains must be finite")

    pair = (pair_value[0], pair_value[1])
    canonical = dict(row)
    if pair[0] > pair[1]:
        pair = (pair[1], pair[0])
        samples = PairSamples(
            schema_version=samples.schema_version,
            rgb_a=samples.rgb_b,
            rgb_b=samples.rgb_a,
            erp_flat_index=samples.erp_flat_index,
            xy_a=samples.xy_b,
            xy_b=samples.xy_a,
            depth_m=samples.depth_m,
            parallax_deg=samples.parallax_deg,
        )
        gain_a, gain_b = gain_b, gain_a
    canonical["camera_pair"] = pair
    canonical["samples"] = samples
    canonical["gain_log_a"] = gain_a
    canonical["gain_log_b"] = gain_b
    return canonical


def _frame_shape_profile(
    row: Mapping[str, object],
    *,
    edges: np.ndarray,
    rho_min: float | None,
    max_parallax_deg: float | None,
    min_bin_samples: int,
) -> dict[str, object]:
    canonical = canonicalize_pair_frame(row)
    samples = canonical["samples"]
    assert isinstance(samples, PairSamples)
    rho_value = canonical.get("rho_log_luma")
    rho = None if rho_value is None else float(rho_value)
    if rho is not None and not math.isfinite(rho):
        raise ValueError("rho_log_luma must be finite or null")
    frame = {
        "log_id": str(canonical.get("log_id", "")),
        "anchor_index": int(canonical.get("anchor_index", -1)),
        "camera_pair": canonical["camera_pair"],
        "rho_log_luma": rho,
    }
    if not frame["log_id"]:
        raise ValueError("pair-frame log_id must be a nonempty string")
    if rho_min is not None and (rho is None or rho < rho_min):
        return {**frame, "status": "UNKNOWN", "reason": "rho_below_threshold", "bins": {}}

    sat_lo = float(canonical.get("sat_lo", 10.0))
    sat_hi = float(canonical.get("sat_hi", 245.0))
    if not math.isfinite(sat_lo) or not math.isfinite(sat_hi) or sat_lo >= sat_hi:
        raise ValueError("pair-frame saturation bounds must be finite and increasing")
    raw_a = np.asarray(samples.rgb_a, dtype=np.float64)
    raw_b = np.asarray(samples.rgb_b, dtype=np.float64)
    log_a = np.log(np.maximum(raw_a.mean(axis=1), 1e-6)) + float(canonical["gain_log_a"])
    log_b = np.log(np.maximum(raw_b.mean(axis=1), 1e-6)) + float(canonical["gain_log_b"])
    shared = 0.5 * (log_a + log_b)
    residual = log_b - log_a
    usable = (
        (raw_a > sat_lo).all(axis=1)
        & (raw_a < sat_hi).all(axis=1)
        & (raw_b > sat_lo).all(axis=1)
        & (raw_b < sat_hi).all(axis=1)
    )
    if max_parallax_deg is not None:
        usable &= samples.parallax_deg <= max_parallax_deg
    if not usable.any():
        return {**frame, "status": "UNKNOWN", "reason": "no_usable_samples", "bins": {}}

    # This is the only held-out nuisance estimate: the same scalar exposure
    # degree of freedom already permitted in production, estimated once from
    # all usable same-ray samples for this pair-frame. No brightness shape is
    # fitted on held-out data.
    scalar_baseline = float(np.median(residual[usable]))
    centered = residual - scalar_baseline
    bin_index = np.searchsorted(edges, shared, side="right") - 1
    bins: dict[int, dict[str, object]] = {}
    for index in range(len(edges) - 1):
        selected = usable & (bin_index == index)
        count = int(selected.sum())
        if count < min_bin_samples:
            continue
        bins[index] = {
            "bin_index": index,
            "log_luma_center": float(0.5 * (edges[index] + edges[index + 1])),
            "count": count,
            "centered_residual": float(np.median(centered[selected])),
        }
    return {
        **frame,
        "status": "READY",
        "reason": None,
        "scalar_baseline": scalar_baseline,
        "usable_sample_n": int(usable.sum()),
        "bins": bins,
    }


def _zero_small(value: float, *, tolerance: float = 1e-12) -> float:
    return 0.0 if abs(value) <= tolerance else float(value)


def _strict_majority(rows: Sequence[Mapping[str, object]]) -> bool:
    decided = [row for row in rows if row["status"] != "UNKNOWN"]
    return bool(decided) and sum(bool(row["improved"]) for row in decided) > len(decided) / 2


def evaluate_profile_transfer(
    rows: Sequence[Mapping[str, object]],
    *,
    train_log_ids: Sequence[str],
    heldout_log_ids: Sequence[str],
    rho_min: float | None = 0.45,
    max_parallax_deg: float | None = 5.0,
    log_luma_edges: Sequence[float] = DEFAULT_LOG_LUMA_EDGES,
    min_bin_samples: int = 8,
    min_reliable_bins: int = 3,
    min_train_logs_per_bin: int = 2,
) -> dict[str, object]:
    """Fit train-only pair shapes and falsify them on whole held-out logs.

    Every pair-frame first loses exactly one sample-level median scalar. Training
    then aggregates frame/bin medians within each log before logs receive equal
    weight. Held-out profiles are never used to fit nonlinear or affine shape.
    """

    train_ids = _checked_log_ids(train_log_ids, field="train_log_ids")
    heldout_ids = _checked_log_ids(heldout_log_ids, field="heldout_log_ids")
    if not set(train_ids).isdisjoint(heldout_ids):
        raise ValueError("train_log_ids and heldout_log_ids must be disjoint")
    if rho_min is not None and (not math.isfinite(rho_min) or not -1.0 <= rho_min <= 1.0):
        raise ValueError("rho_min must be null or within [-1, 1]")
    if max_parallax_deg is not None and (
        not math.isfinite(max_parallax_deg) or max_parallax_deg < 0.0
    ):
        raise ValueError("max_parallax_deg must be null or nonnegative")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in (min_bin_samples, min_reliable_bins, min_train_logs_per_bin)
    ):
        raise ValueError("support thresholds must be positive integers")
    edges = np.asarray(log_luma_edges, dtype=np.float64)
    if edges.ndim != 1 or len(edges) < 2 or not np.isfinite(edges).all():
        raise ValueError("log_luma_edges must be a finite 1D sequence")
    if not np.all(np.diff(edges) > 0):
        raise ValueError("log_luma_edges must be strictly increasing")

    selected_ids = set(train_ids) | set(heldout_ids)
    canonical_rows = [canonicalize_pair_frame(row) for row in rows]
    unexpected = sorted({str(row.get("log_id", "")) for row in canonical_rows} - selected_ids)
    if unexpected:
        raise ValueError(f"pair-frame rows contain unexpected logs: {unexpected}")
    canonical_rows.sort(
        key=lambda row: (
            str(row["log_id"]),
            int(row["anchor_index"]),
            tuple(row["camera_pair"]),
        )
    )
    identities = [
        (str(row["log_id"]), int(row["anchor_index"]), tuple(row["camera_pair"]))
        for row in canonical_rows
    ]
    if len(set(identities)) != len(identities):
        raise ValueError("pair-frame rows must have unique log/anchor/camera_pair identities")
    expected_pairs = sorted(
        {
            tuple(row["camera_pair"])
            for row in canonical_rows
            if str(row["log_id"]) in train_ids
        }
    )
    profiles = [
        _frame_shape_profile(
            row,
            edges=edges,
            rho_min=rho_min,
            max_parallax_deg=max_parallax_deg,
            min_bin_samples=min_bin_samples,
        )
        for row in canonical_rows
    ]

    frame_values: dict[tuple[str, tuple[str, str], int], list[float]] = defaultdict(list)
    for profile in profiles:
        if profile["log_id"] not in train_ids or profile["status"] != "READY":
            continue
        pair = tuple(profile["camera_pair"])
        for bin_index, bin_row in profile["bins"].items():
            frame_values[(str(profile["log_id"]), pair, int(bin_index))].append(
                float(bin_row["centered_residual"])
            )

    log_values: dict[tuple[tuple[str, str], int], list[tuple[str, float]]] = defaultdict(list)
    for (log_id, pair, bin_index), values in sorted(frame_values.items()):
        log_values[(pair, bin_index)].append((log_id, float(np.median(values))))

    model_by_pair: dict[tuple[str, str], dict[str, object]] = {}
    for pair, bin_index in sorted(log_values):
        log_rows = log_values[(pair, bin_index)]
        if len(log_rows) < min_train_logs_per_bin:
            continue
        model = model_by_pair.setdefault(pair, {"bins": {}, "train_logs": set()})
        model["bins"][bin_index] = float(np.median([value for _, value in log_rows]))
        model["train_logs"].update(log_id for log_id, _ in log_rows)

    training_shapes: list[dict[str, object]] = []
    for pair in sorted(model_by_pair):
        model = model_by_pair[pair]
        bins = model["bins"]
        indices = sorted(bins)
        x = np.asarray([0.5 * (edges[index] + edges[index + 1]) for index in indices])
        y = np.asarray([bins[index] for index in indices])
        if len(indices) >= 2:
            design = np.column_stack([x, np.ones(len(x))])
            slope, intercept = np.linalg.lstsq(design, y, rcond=None)[0]
            affine = (float(slope), float(intercept))
        else:
            affine = None
        model["affine"] = affine
        training_shapes.append(
            {
                "camera_pair": list(pair),
                "train_log_count": len(model["train_logs"]),
                "affine_slope": None if affine is None else affine[0],
                "affine_intercept": None if affine is None else affine[1],
                "bins": [
                    {
                        "bin_index": index,
                        "log_luma_center": float(x[position]),
                        "nonlinear_shape": float(y[position]),
                        "train_log_count": len(log_values[(pair, index)]),
                    }
                    for position, index in enumerate(indices)
                ],
            }
        )

    pair_frames: list[dict[str, object]] = []
    for profile in profiles:
        if profile["log_id"] not in heldout_ids:
            continue
        pair = tuple(profile["camera_pair"])
        base = {
            "log_id": profile["log_id"],
            "anchor_index": profile["anchor_index"],
            "camera_pair": list(pair),
            "rho_log_luma": profile["rho_log_luma"],
        }
        model = model_by_pair.get(pair)
        if profile["status"] != "READY" or model is None:
            reason = profile["reason"] if profile["status"] != "READY" else "no_train_shape"
            pair_frames.append(
                {
                    **base,
                    "status": "UNKNOWN",
                    "reason": reason,
                    "supported_bin_count": 0,
                    "supported_bin_coverage": 0.0,
                }
            )
            continue
        common = sorted(set(profile["bins"]) & set(model["bins"]))
        if len(common) < min_reliable_bins:
            pair_frames.append(
                {
                    **base,
                    "status": "UNKNOWN",
                    "reason": "fewer_than_min_reliable_bins",
                    "supported_bin_count": len(common),
                    "supported_bin_coverage": float(len(common) / max(len(model["bins"]), 1)),
                }
            )
            continue
        observed = np.asarray(
            [profile["bins"][index]["centered_residual"] for index in common], dtype=float
        )
        predicted = np.asarray([model["bins"][index] for index in common], dtype=float)
        centers = np.asarray(
            [0.5 * (edges[index] + edges[index + 1]) for index in common], dtype=float
        )
        affine = model["affine"]
        assert affine is not None
        affine_predicted = affine[0] * centers + affine[1]
        zero_mae = float(np.mean(np.abs(observed)))
        nonlinear_mae = float(np.mean(np.abs(observed - predicted)))
        affine_mae = float(np.mean(np.abs(observed - affine_predicted)))
        delta = _zero_small(zero_mae - nonlinear_mae)
        affine_delta = _zero_small(zero_mae - affine_mae)
        if np.std(observed) > 1e-12 and np.std(predicted) > 1e-12:
            correlation = float(np.corrcoef(observed, predicted)[0, 1])
        else:
            correlation = None
        pair_frames.append(
            {
                **base,
                "status": "PASS" if delta > 0.0 else "NEG",
                "reason": None,
                "supported_bin_count": len(common),
                "supported_bin_coverage": float(len(common) / max(len(model["bins"]), 1)),
                "zero_shape_mae": zero_mae,
                "nonlinear_mae": nonlinear_mae,
                "affine_mae": affine_mae,
                "delta_mae": delta,
                "affine_delta_mae": affine_delta,
                "signed_correlation": correlation,
                "improved": delta > 0.0,
                "affine_improved": affine_delta > 0.0,
                "nonlinear_beats_affine": affine_mae - nonlinear_mae > 1e-12,
            }
        )

    pair_reports: list[dict[str, object]] = []
    for pair in expected_pairs:
        by_log: dict[str, list[dict[str, object]]] = defaultdict(list)
        for frame in pair_frames:
            if tuple(frame["camera_pair"]) == pair and frame["status"] != "UNKNOWN":
                by_log[str(frame["log_id"])].append(frame)
        if not by_log:
            pair_reports.append(
                {"camera_pair": list(pair), "status": "UNKNOWN", "improved": False}
            )
            continue
        log_metrics = []
        for log_id in sorted(by_log):
            frames = by_log[log_id]
            metric = {
                key: float(np.mean([frame[key] for frame in frames]))
                for key in (
                    "zero_shape_mae",
                    "nonlinear_mae",
                    "affine_mae",
                    "supported_bin_count",
                    "supported_bin_coverage",
                )
            }
            correlations = [
                float(frame["signed_correlation"])
                for frame in frames
                if frame["signed_correlation"] is not None
            ]
            metric["signed_correlation"] = (
                float(np.mean(correlations)) if correlations else None
            )
            log_metrics.append(metric)
        zero_mae = float(np.mean([metric["zero_shape_mae"] for metric in log_metrics]))
        nonlinear_mae = float(np.mean([metric["nonlinear_mae"] for metric in log_metrics]))
        affine_mae = float(np.mean([metric["affine_mae"] for metric in log_metrics]))
        delta = _zero_small(zero_mae - nonlinear_mae)
        affine_delta = _zero_small(zero_mae - affine_mae)
        correlations = [
            float(metric["signed_correlation"])
            for metric in log_metrics
            if metric["signed_correlation"] is not None
        ]
        pair_reports.append(
            {
                "camera_pair": list(pair),
                "status": "PASS" if delta > 0.0 else "NEG",
                "heldout_log_count": len(log_metrics),
                "zero_shape_mae": zero_mae,
                "nonlinear_mae": nonlinear_mae,
                "affine_mae": affine_mae,
                "delta_mae": delta,
                "affine_delta_mae": affine_delta,
                "mean_supported_bin_count": float(
                    np.mean([metric["supported_bin_count"] for metric in log_metrics])
                ),
                "mean_supported_bin_coverage": float(
                    np.mean([metric["supported_bin_coverage"] for metric in log_metrics])
                ),
                "mean_signed_correlation": (
                    float(np.mean(correlations)) if correlations else None
                ),
                "improved": delta > 0.0,
                "nonlinear_beats_affine": affine_mae - nonlinear_mae > 1e-12,
            }
        )

    log_reports: list[dict[str, object]] = []
    for log_id in heldout_ids:
        by_pair: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
        for frame in pair_frames:
            if frame["log_id"] == log_id and frame["status"] != "UNKNOWN":
                by_pair[tuple(frame["camera_pair"])].append(frame)
        if not by_pair:
            log_reports.append({"log_id": log_id, "status": "UNKNOWN", "improved": False})
            continue
        pair_metrics = []
        for pair in sorted(by_pair):
            frames = by_pair[pair]
            metric = {
                key: float(np.mean([frame[key] for frame in frames]))
                for key in (
                    "zero_shape_mae",
                    "nonlinear_mae",
                    "affine_mae",
                    "supported_bin_count",
                    "supported_bin_coverage",
                )
            }
            correlations = [
                float(frame["signed_correlation"])
                for frame in frames
                if frame["signed_correlation"] is not None
            ]
            metric["signed_correlation"] = (
                float(np.mean(correlations)) if correlations else None
            )
            pair_metrics.append(metric)
        zero_mae = float(np.mean([metric["zero_shape_mae"] for metric in pair_metrics]))
        nonlinear_mae = float(np.mean([metric["nonlinear_mae"] for metric in pair_metrics]))
        affine_mae = float(np.mean([metric["affine_mae"] for metric in pair_metrics]))
        delta = _zero_small(zero_mae - nonlinear_mae)
        affine_delta = _zero_small(zero_mae - affine_mae)
        correlations = [
            float(metric["signed_correlation"])
            for metric in pair_metrics
            if metric["signed_correlation"] is not None
        ]
        log_reports.append(
            {
                "log_id": log_id,
                "status": "PASS" if delta > 0.0 else "NEG",
                "heldout_pair_count": len(pair_metrics),
                "zero_shape_mae": zero_mae,
                "nonlinear_mae": nonlinear_mae,
                "affine_mae": affine_mae,
                "delta_mae": delta,
                "affine_delta_mae": affine_delta,
                "mean_supported_bin_count": float(
                    np.mean([metric["supported_bin_count"] for metric in pair_metrics])
                ),
                "mean_supported_bin_coverage": float(
                    np.mean([metric["supported_bin_coverage"] for metric in pair_metrics])
                ),
                "mean_signed_correlation": (
                    float(np.mean(correlations)) if correlations else None
                ),
                "improved": delta > 0.0,
                "nonlinear_beats_affine": affine_mae - nonlinear_mae > 1e-12,
            }
        )

    evaluable_frames = [frame for frame in pair_frames if frame["status"] != "UNKNOWN"]
    expected_pair_log_cells = {
        (log_id, pair) for log_id in heldout_ids for pair in expected_pairs
    }
    observed_pair_log_cells = {
        (str(profile["log_id"]), tuple(profile["camera_pair"]))
        for profile in profiles
        if profile["log_id"] in heldout_ids
        and tuple(profile["camera_pair"]) in expected_pairs
    }
    evaluable_pair_log_cells = {
        (str(frame["log_id"]), tuple(frame["camera_pair"]))
        for frame in evaluable_frames
        if tuple(frame["camera_pair"]) in expected_pairs
    }
    unknown_pair_log_cells = expected_pair_log_cells - evaluable_pair_log_cells
    completeness_gate = {
        "unit": "heldout_log_x_expected_canonical_pair",
        "expected_cell_n": len(expected_pair_log_cells),
        "evaluable_cell_n": len(evaluable_pair_log_cells),
        "unknown_cell_n": len(unknown_pair_log_cells),
        "complete": bool(expected_pair_log_cells) and not unknown_pair_log_cells,
        "unknown_cells": [
            {"log_id": log_id, "camera_pair": list(pair)}
            for log_id, pair in sorted(unknown_pair_log_cells)
        ],
    }

    supported_majority_pairs = _strict_majority(pair_reports)
    supported_majority_logs = _strict_majority(log_reports)
    decided_pairs = sum(report["status"] != "UNKNOWN" for report in pair_reports)
    decided_logs = sum(report["status"] != "UNKNOWN" for report in log_reports)
    has_unknown_pair = decided_pairs != len(pair_reports)
    has_unknown_log = decided_logs != len(log_reports)
    if (
        not completeness_gate["complete"]
        or decided_pairs == 0
        or decided_logs == 0
        or has_unknown_pair
        or has_unknown_log
    ):
        status = "UNKNOWN"
        majority_pairs = False
        majority_logs = False
    elif supported_majority_pairs and supported_majority_logs:
        status = "PASS"
        majority_pairs = True
        majority_logs = True
    else:
        status = "NEG"
        majority_pairs = supported_majority_pairs
        majority_logs = supported_majority_logs

    def win_counts(reports: Sequence[Mapping[str, object]]) -> dict[str, int]:
        counts = {"win_n": 0, "loss_n": 0, "tie_n": 0, "unknown_n": 0}
        for report in reports:
            if report["status"] == "UNKNOWN":
                counts["unknown_n"] += 1
            elif float(report["delta_mae"]) > 0.0:
                counts["win_n"] += 1
            elif float(report["delta_mae"]) < 0.0:
                counts["loss_n"] += 1
            else:
                counts["tie_n"] += 1
        return counts

    return {
        "schema_version": "db226.profile_transfer.v1",
        "status": status,
        "registered_pass": status == "PASS",
        "primary_gate": "nonlinear_vs_zero_only",
        "scalar_baseline_method": "sample_median_per_pair_frame",
        "training_aggregation": "frame_then_log_equal_weight",
        "train_log_ids": train_ids,
        "heldout_log_ids": heldout_ids,
        "rho_min": None if rho_min is None else float(rho_min),
        "max_parallax_deg": (
            None if max_parallax_deg is None else float(max_parallax_deg)
        ),
        "min_bin_samples": min_bin_samples,
        "min_reliable_bins": min_reliable_bins,
        "min_train_logs_per_bin": min_train_logs_per_bin,
        "log_luma_edges": edges.tolist(),
        "training_shapes": training_shapes,
        "heldout_pair_frames": pair_frames,
        "heldout_pairs": pair_reports,
        "heldout_logs": log_reports,
        "registered_completeness_gate": completeness_gate,
        "majority_heldout_pairs_improved": majority_pairs,
        "majority_heldout_logs_improved": majority_logs,
        "win_summary": {
            "heldout_pairs": win_counts(pair_reports),
            "heldout_logs": win_counts(log_reports),
        },
        "coverage": {
            "expected_pair_n": len(expected_pairs),
            "expected_pair_log_cell_n": len(expected_pair_log_cells),
            "observed_pair_log_cell_n": len(observed_pair_log_cells),
            "evaluable_pair_log_cell_n": len(evaluable_pair_log_cells),
            "missing_pair_log_cell_n": len(
                expected_pair_log_cells - observed_pair_log_cells
            ),
            "unknown_pair_log_cell_n": len(unknown_pair_log_cells),
            "heldout_pair_frame_n": len(pair_frames),
            "evaluable_pair_frame_n": len(evaluable_frames),
            "unknown_pair_frame_n": len(pair_frames) - len(evaluable_frames),
            "evaluable_pair_n": decided_pairs,
            "unknown_pair_n": len(pair_reports) - decided_pairs,
            "evaluable_log_n": decided_logs,
            "unknown_log_n": len(log_reports) - decided_logs,
            "mean_supported_bin_coverage": (
                float(np.mean([frame["supported_bin_coverage"] for frame in evaluable_frames]))
                if evaluable_frames
                else 0.0
            ),
        },
    }
