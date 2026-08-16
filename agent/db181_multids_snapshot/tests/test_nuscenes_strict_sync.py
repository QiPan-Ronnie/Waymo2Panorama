from __future__ import annotations

import pytest

from agent.db181_multids.contract import FrameRecord
from agent.db181_multids.nuscenes_strict_sync import (
    scaled_sync_window_ns,
    strict_sync_report,
)


def _frame(index: int, anchor: int, side: int) -> FrameRecord:
    return FrameRecord(
        index=index,
        anchor_timestamp_ns=anchor,
        camera_timestamps_ns={"front": anchor, "side": side},
        lidar_timestamp_ns=anchor,
    )


def test_half_cadence_gate_rejects_nearly_one_frame_old_camera() -> None:
    base = 1_000_000_000
    frames = (
        _frame(0, base, base + 8_000_000),
        _frame(1, base + 100_000_000, base + 108_000_000),
        _frame(2, base + 200_000_000, base + 208_000_000),
        _frame(3, base + 300_000_000, base + 392_000_000),
    )

    report = strict_sync_report(frames, ("front", "side"), fraction=0.5)

    assert report.windows_ns == {
        "front": 50_000_000,
        "side": 50_000_000,
        "lidar": 50_000_000,
    }
    assert report.accepted_indices == (0, 1, 2)
    assert len(report.rejected) == 1
    assert report.rejected[0].index == 3
    assert report.rejected[0].violations_ns == {"side": 92_000_000}


@pytest.mark.parametrize("invalid", [0.0, -0.1, 1.1, float("inf"), float("nan"), True])
def test_scaled_sync_window_rejects_invalid_fraction(invalid: float) -> None:
    with pytest.raises(ValueError, match="fraction"):
        scaled_sync_window_ns(83_333_000, invalid)
