"""Behavior tests for the dataset-agnostic ring-camera loader contract."""
from __future__ import annotations

import warnings
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from PIL import Image

import waymo2panorama.data_io.av2_loader as av2_loader


EXPECTED_AV2_RING_CAMS = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)


@pytest.fixture
def writable_test_dir() -> Path:
    """Create test data inside the worktree, not the sandboxed system temp dir."""
    repo_root = Path(__file__).resolve().parents[3]
    scratch_root = repo_root / ".pytest_cache" / "db212_loader_camera_contract"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="case-", dir=scratch_root) as temp_dir:
        yield Path(temp_dir)


def test_camera_contract_defaults_to_unchanged_av2_ring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("W2P_RING_CAMS", raising=False)

    assert av2_loader.RING_CAMS_7 == EXPECTED_AV2_RING_CAMS
    assert av2_loader.resolve_ring_cameras() == EXPECTED_AV2_RING_CAMS


def test_camera_contract_uses_ordered_trimmed_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("W2P_RING_CAMS", " front , left,rear , right ")

    assert av2_loader.resolve_ring_cameras() == ("front", "left", "rear", "right")


def test_explicit_camera_contract_takes_precedence_over_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("W2P_RING_CAMS", "ignored,ignored")

    assert av2_loader.resolve_ring_cameras(("explicit_rear", "explicit_front")) == (
        "explicit_rear",
        "explicit_front",
    )


@pytest.mark.parametrize(
    ("explicit", "environment", "message"),
    [
        ((), None, "at least one"),
        (("front", "", "rear"), None, "empty"),
        (("front", "front"), None, "duplicate"),
        (None, "front,,rear", "empty"),
        (None, "front, front", "duplicate"),
    ],
)
def test_camera_contract_rejects_invalid_membership(
    monkeypatch: pytest.MonkeyPatch,
    explicit: tuple[str, ...] | None,
    environment: str | None,
    message: str,
) -> None:
    monkeypatch.delenv("W2P_RING_CAMS", raising=False)
    if environment is not None:
        monkeypatch.setenv("W2P_RING_CAMS", environment)

    with pytest.raises(ValueError, match=message):
        av2_loader.resolve_ring_cameras(explicit)


def _write_feather(frame: pd.DataFrame, path: Path) -> None:
    """Write Feather V2 through its non-deprecated Arrow IPC interface."""
    table = pa.Table.from_pandas(frame, preserve_index=False)
    with pa.OSFile(str(path), "wb") as sink, pa.ipc.new_file(sink, table.schema) as writer:
        writer.write_table(table)


def _write_synthetic_pseudo_log(log_dir: Path, cameras: tuple[str, str]) -> None:
    calibration_dir = log_dir / "calibration"
    calibration_dir.mkdir(parents=True)

    calibration_values = {
        cameras[0]: {"fx": 101.0, "fy": 111.0, "tx": 1.0},
        cameras[1]: {"fx": 202.0, "fy": 212.0, "tx": 2.0},
    }
    # Deliberately reverse table rows: loader output order must follow ``cameras``.
    intrinsics_rows = []
    extrinsics_rows = []
    for cam in reversed(cameras):
        values = calibration_values[cam]
        intrinsics_rows.append(
            {
                "sensor_name": cam,
                "fx_px": values["fx"],
                "fy_px": values["fy"],
                "cx_px": 1.5,
                "cy_px": 1.0,
                "width_px": 3,
                "height_px": 2,
                "k1": 0.01,
                "k2": 0.02,
                "k3": 0.03,
            }
        )
        extrinsics_rows.append(
            {
                "sensor_name": cam,
                "qw": 1.0,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
                "tx_m": values["tx"],
                "ty_m": 3.0,
                "tz_m": 4.0,
            }
        )

    _write_feather(
        pd.DataFrame(intrinsics_rows), calibration_dir / "intrinsics.feather"
    )
    _write_feather(
        pd.DataFrame(extrinsics_rows),
        calibration_dir / "egovehicle_SE3_sensor.feather",
    )

    streams = {
        cameras[0]: ((300, 30), (100, 10)),
        cameras[1]: ((250, 25), (90, 9), (320, 32)),
    }
    for cam, frames in streams.items():
        camera_dir = log_dir / "sensors" / "cameras" / cam
        camera_dir.mkdir(parents=True)
        for timestamp_ns, pixel_value in frames:
            pixels = np.full((2, 3, 3), pixel_value, dtype=np.uint8)
            Image.fromarray(pixels).save(camera_dir / f"{timestamp_ns}.jpg")


def test_two_camera_pseudo_log_uses_instance_contract_exactly(writable_test_dir: Path) -> None:
    cameras = ("pseudo_front", "pseudo_rear")
    _write_synthetic_pseudo_log(writable_test_dir, cameras)

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        loader = av2_loader.AV2RingLoader(writable_test_dir, cameras=cameras)

    assert loader.cameras() == cameras
    assert loader.num_anchor_frames() == 2
    assert loader.anchor_timestamps_ns() == [100, 300]

    front_calibration = loader.calibration(cameras[0])
    assert front_calibration.name == cameras[0]
    assert front_calibration.image_width == 3
    assert front_calibration.image_height == 2
    np.testing.assert_allclose(
        front_calibration.K,
        np.array([[101.0, 0.0, 1.5], [0.0, 111.0, 1.0], [0.0, 0.0, 1.0]]),
    )
    np.testing.assert_allclose(front_calibration.T_ego_cam[:3, 3], [1.0, 3.0, 4.0])

    samples = list(loader.iter_synced_frames())
    assert [sample.anchor_timestamp_ns for sample in samples] == [100, 300]
    assert [sample.timestamps_ns[cameras[1]] for sample in samples] == [90, 320]

    sample = samples[1]
    assert list(sample.images) == list(cameras)
    assert list(sample.timestamps_ns) == list(cameras)
    assert list(sample.calibrations) == list(cameras)
    assert sample.timestamps_ns == {cameras[0]: 300, cameras[1]: 320}
    assert [calibration.name for calibration in sample.calibrations.values()] == list(cameras)
    assert all(image.shape == (2, 3, 3) for image in sample.images.values())
