"""Strict delivery-time synchronization gate for converted nuScenes logs.

The base converter preserves a maximum-cardinality sequence and permits a full
sensor cadence of offset.  That is useful for inventory, but a camera that is
almost one frame old is not suitable for a visual panorama.  This module keeps
the converter backward-compatible and defines the stricter delivery subset.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

from .contract import FrameRecord


@dataclass(frozen=True)
class RejectedFrame:
    index: int
    violations_ns: dict[str, int]


@dataclass(frozen=True)
class StrictSyncReport:
    fraction: float
    windows_ns: dict[str, int]
    accepted_indices: tuple[int, ...]
    rejected: tuple[RejectedFrame, ...]


def scaled_sync_window_ns(cadence_ns: int, fraction: float) -> int:
    """Return a bounded fraction of one cadence, rounded upward."""

    if isinstance(cadence_ns, bool) or not isinstance(cadence_ns, int) or cadence_ns <= 0:
        raise ValueError("cadence_ns must be a positive integer")
    if (
        isinstance(fraction, bool)
        or not isinstance(fraction, (int, float))
        or not math.isfinite(float(fraction))
        or not 0.0 < float(fraction) <= 1.0
    ):
        raise ValueError("sync fraction must be finite in (0, 1]")
    return max(1, int(math.ceil(cadence_ns * float(fraction))))


def _nominal_cadence_ns(values: tuple[int, ...], channel: str) -> int:
    if len(values) < 2:
        raise ValueError(f"{channel} needs at least two timestamps")
    deltas = tuple(right - left for left, right in zip(values, values[1:]))
    if any(delta <= 0 for delta in deltas):
        raise ValueError(f"{channel} timestamps must be strictly increasing")
    return int(median(deltas))


def strict_sync_report(
    frames: tuple[FrameRecord, ...],
    cameras: tuple[str, ...],
    *,
    fraction: float = 0.5,
    include_lidar: bool = True,
) -> StrictSyncReport:
    """Classify frames whose every sensor is within a fraction of its cadence."""

    if not frames:
        raise ValueError("strict sync needs at least one frame")
    if not cameras:
        raise ValueError("strict sync needs at least one camera")
    if len(set(cameras)) != len(cameras):
        raise ValueError("strict sync camera names must be unique")
    for frame in frames:
        missing = set(cameras) - set(frame.camera_timestamps_ns)
        if missing:
            raise ValueError(f"frame {frame.index} is missing cameras: {sorted(missing)}")

    series = {
        camera: tuple(frame.camera_timestamps_ns[camera] for frame in frames)
        for camera in cameras
    }
    use_lidar = include_lidar and all(frame.lidar_timestamp_ns is not None for frame in frames)
    if use_lidar:
        series["lidar"] = tuple(int(frame.lidar_timestamp_ns) for frame in frames)
    windows = {
        channel: scaled_sync_window_ns(
            _nominal_cadence_ns(timestamps, channel), fraction
        )
        for channel, timestamps in series.items()
    }

    accepted: list[int] = []
    rejected: list[RejectedFrame] = []
    for frame in frames:
        violations: dict[str, int] = {}
        for camera in cameras:
            delta = abs(frame.camera_timestamps_ns[camera] - frame.anchor_timestamp_ns)
            if delta > windows[camera]:
                violations[camera] = delta
        if use_lidar:
            assert frame.lidar_timestamp_ns is not None
            delta = abs(frame.lidar_timestamp_ns - frame.anchor_timestamp_ns)
            if delta > windows["lidar"]:
                violations["lidar"] = delta
        if violations:
            rejected.append(RejectedFrame(frame.index, violations))
        else:
            accepted.append(frame.index)
    if not accepted:
        raise ValueError("strict sync rejected every frame")
    return StrictSyncReport(
        fraction=float(fraction),
        windows_ns=windows,
        accepted_indices=tuple(accepted),
        rejected=tuple(rejected),
    )
