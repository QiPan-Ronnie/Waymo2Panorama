from __future__ import annotations

import struct
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from agent.db181_multids.waymo_e2e_adapter import (
    WAYMO_E2E_CAMERA_MAP,
    E2ECameraFrame,
    E2EFrame,
    convert_waymo_e2e_records,
    convert_waymo_e2e_tfrecord,
)
from waymo2panorama.data_io.av2_loader import AV2RingLoader


COMMIT = "d03754c"
CREATED_AT = "2026-07-31T12:00:00-07:00"


@pytest.fixture
def writable_test_dir() -> Path:
    scratch_root = Path(gettempdir()) / "w2p_db216_e2e"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="case-", dir=scratch_root) as temp_dir:
        yield Path(temp_dir)


def _jpeg(value: int) -> bytes:
    buffer = BytesIO()
    Image.fromarray(np.full((3, 4, 3), value, dtype=np.uint8)).save(
        buffer,
        format="JPEG",
    )
    return buffer.getvalue()


def _transform(camera_index: int) -> tuple[float, ...]:
    transform = np.eye(4, dtype=np.float64)
    transform[:3, 3] = [1.0, 0.1 * camera_index, 1.5]
    return tuple(transform.reshape(-1))


def _world_pose(camera_index: int, frame_index: int) -> tuple[float, ...]:
    del camera_index, frame_index
    return tuple(np.eye(4, dtype=np.float64).reshape(-1))


def _frame(
    index: int,
    *,
    missing_camera: str | None = None,
    source_scene_id: str = "e2e-scene",
) -> E2EFrame:
    anchor = 1_000_000_000 + index * 100_000_000
    cameras = []
    for camera_index, (source_name, _) in enumerate(WAYMO_E2E_CAMERA_MAP):
        if source_name == missing_camera:
            continue
        cameras.append(
            E2ECameraFrame(
                source_name=source_name,
                timestamp_ns=anchor + camera_index * 1_000_000,
                image_jpeg=_jpeg(20 + camera_index + index),
                intrinsic=(100.0, 110.0, 2.0, 1.5, 0.0, 0.0, 0.0, 0.0, 0.0),
                transform_ego_waymo_camera=_transform(camera_index),
                transform_world_ego=_world_pose(camera_index, index),
                width_px=4,
                height_px=3,
            )
        )
    return E2EFrame(
        source_scene_id=source_scene_id,
        anchor_timestamp_ns=anchor,
        cameras=tuple(cameras),
    )


def _write_tfrecord(path: Path, payloads: tuple[bytes, ...]) -> None:
    with path.open("wb") as stream:
        for payload in payloads:
            stream.write(struct.pack("<Q", len(payload)))
            stream.write(b"\0" * 4)
            stream.write(payload)
            stream.write(b"\0" * 4)


def test_two_frame_e2e_tfrecord_writes_honest_b_only_eight_camera_log(
    writable_test_dir: Path,
) -> None:
    source = writable_test_dir / "source.tfrecord"
    _write_tfrecord(source, (b"frame-0", b"frame-1"))
    decoded = {b"frame-0": _frame(0), b"frame-1": _frame(1)}

    output_dir, manifest = convert_waymo_e2e_tfrecord(
        source,
        writable_test_dir / "output",
        "waymo-e2e",
        record_decoder=lambda payload, _: decoded[payload],
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )

    expected_cameras = tuple(pseudo_name for _, pseudo_name in WAYMO_E2E_CAMERA_MAP)
    assert manifest.dataset == "waymo_e2e"
    assert manifest.mode == "B"
    assert manifest.cameras == expected_cameras
    assert manifest.anchor_camera == "ring_front_center"
    assert manifest.output_frame_count == 2
    assert manifest.has_lidar is False
    assert manifest.has_ego_pose is False
    assert manifest.has_annotations is False
    assert manifest.real_mask_pattern is None
    assert manifest.faithfill_mask_pattern is None
    assert manifest.honest_black_mask_pattern is None
    assert manifest.supported_azimuth_deg == ((0.0, 360.0),)
    assert manifest.honest_black_azimuth_deg == ()
    assert all(frame.lidar_timestamp_ns is None for frame in manifest.frames)
    assert manifest.camera_records[-1].max_sync_delta_ns == 7_000_000
    manifest.validate()

    assert not (output_dir / "sensors" / "lidar").exists()
    assert not (output_dir / "city_SE3_egovehicle.feather").exists()
    assert not (output_dir / "annotations.feather").exists()
    loader = AV2RingLoader(output_dir, cameras=manifest.cameras)
    sample = loader.load_synced_frame(manifest.frames[0].anchor_timestamp_ns)
    assert tuple(sample.images) == expected_cameras
    assert all(image.shape == (3, 4, 3) for image in sample.images.values())

    extrinsics = pd.read_feather(
        output_dir / "calibration" / "egovehicle_SE3_sensor.feather"
    )
    front = extrinsics.loc[extrinsics["sensor_name"] == "ring_front_center"].iloc[0]
    expected_rotation = np.array(
        [[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]]
    )
    np.testing.assert_allclose(
        sample.calibrations["ring_front_center"].T_ego_cam[:3, :3],
        expected_rotation,
    )
    assert front["tx_m"] == pytest.approx(1.0)


def test_missing_required_camera_fails_atomically(writable_test_dir: Path) -> None:
    source = writable_test_dir / "source.tfrecord"
    _write_tfrecord(source, (b"bad",))
    output_root = writable_test_dir / "output"

    with pytest.raises(ValueError, match="missing required Waymo E2E cameras"):
        convert_waymo_e2e_tfrecord(
            source,
            output_root,
            "bad-output",
            record_decoder=lambda _payload, _index: _frame(
                0, missing_camera="REAR_RIGHT"
            ),
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )

    assert not (output_root / "bad-output").exists()
    assert not list(output_root.glob(".bad-output.staging-*"))


def test_truncated_tfrecord_is_rejected_before_publish(writable_test_dir: Path) -> None:
    source = writable_test_dir / "truncated.tfrecord"
    source.write_bytes(struct.pack("<Q", 50) + b"\0" * 4 + b"short")

    with pytest.raises(ValueError, match="truncated TFRecord"):
        convert_waymo_e2e_tfrecord(
            source,
            writable_test_dir / "output",
            "truncated-output",
            record_decoder=lambda _payload, _index: _frame(0),
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )


def test_shard_may_contain_distinct_per_record_context_names(
    writable_test_dir: Path,
) -> None:
    source = writable_test_dir / "multi-context.tfrecord"
    _write_tfrecord(source, (b"a", b"b"))
    decoded = {
        b"a": _frame(0, source_scene_id="frame-id-a"),
        b"b": _frame(1, source_scene_id="frame-id-b"),
    }

    _, manifest = convert_waymo_e2e_tfrecord(
        source,
        writable_test_dir / "output",
        "multi-context",
        record_decoder=lambda payload, _: decoded[payload],
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )

    assert manifest.source_scene_id == source.name


def test_selected_e2e_records_become_independent_honest_static_single_frame_logs(
    writable_test_dir: Path,
) -> None:
    source = writable_test_dir / "source.tfrecord"
    _write_tfrecord(source, (b"record-0", b"record-1", b"record-2"))
    decoded = {
        b"record-0": _frame(0, source_scene_id="context-000"),
        b"record-1": _frame(1, source_scene_id="context-001"),
        b"record-2": _frame(2, source_scene_id="context-002"),
    }
    # Real E2E shards expose no physical capture timestamps.  The converter must
    # not join unrelated contexts into a fake temporal sequence.
    decoded = {
        payload: E2EFrame(
            source_scene_id=frame.source_scene_id,
            anchor_timestamp_ns=0,
            cameras=tuple(
                E2ECameraFrame(
                    **{
                        **camera.__dict__,
                        "timestamp_ns": 0,
                    }
                )
                for camera in frame.cameras
            ),
        )
        for payload, frame in decoded.items()
    }

    converted = convert_waymo_e2e_records(
        source,
        writable_test_dir / "output",
        "waymo-e2e",
        record_indices=(0, 2),
        record_decoder=lambda payload, _: decoded[payload],
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )

    assert len(converted) == 2
    for selected_index, (output_dir, manifest) in zip((0, 2), converted):
        assert output_dir.name.startswith(f"waymo-e2e_r{selected_index:06d}_")
        assert manifest.source_scene_id == f"context-{selected_index:03d}"
        assert manifest.source_frame_count == 1
        assert manifest.output_frame_count == 1
        assert manifest.has_ego_pose is False
        assert manifest.frames[0].anchor_timestamp_ns == 1
        assert set(manifest.frames[0].camera_timestamps_ns.values()) == {1}
        assert all(record.max_sync_delta_ns == 0 for record in manifest.camera_records)

        assert not (output_dir / "camera_capture_poses.feather").exists()
        assert not (output_dir / "city_SE3_egovehicle.feather").exists()

        provenance = __import__("json").loads(
            (output_dir / "waymo_e2e_provenance.json").read_text(encoding="utf-8")
        )
        assert provenance["physical_timestamps_available"] is False
        assert provenance["record_is_independent_scene"] is True
        assert provenance["surrogate_timestamp_ns"] == 1
        assert provenance["camera_pose_available"] is False
        assert provenance["camera_pose_field_status"] == "placeholder_identity"


def test_unverified_nonplaceholder_e2e_pose_fails_closed(
    writable_test_dir: Path,
) -> None:
    source = writable_test_dir / "source.tfrecord"
    _write_tfrecord(source, (b"record",))
    frame = _frame(0)
    translated = np.eye(4, dtype=np.float64)
    translated[0, 3] = 1.0
    cameras = list(frame.cameras)
    cameras[0] = replace(
        cameras[0],
        timestamp_ns=0,
        transform_world_ego=tuple(translated.reshape(-1)),
    )
    frame = replace(
        frame,
        anchor_timestamp_ns=0,
        cameras=tuple(replace(camera, timestamp_ns=0) for camera in cameras),
    )
    output_root = writable_test_dir / "output"

    with pytest.raises(ValueError, match="semantics must be verified"):
        convert_waymo_e2e_records(
            source,
            output_root,
            "waymo-e2e",
            record_indices=(0,),
            record_decoder=lambda _payload, _index: frame,
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )

    assert not list(output_root.glob("waymo-e2e*"))
    assert not list(output_root.glob(".waymo-e2e*.staging-*"))
