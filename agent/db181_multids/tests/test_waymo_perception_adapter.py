from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
import pytest
from PIL import Image

from agent.db181_multids import waymo_perception_adapter as adapter
from agent.db181_multids.contract import ConversionManifest
from agent.db181_multids.geometry import quaternion_wxyz_to_matrix, rotation_z_deg
from agent.db181_multids.io import sha256_file
from agent.db181_multids.waymo_perception_adapter import (
    WAYMO_CAMERA_MAP,
    WAYMO_CAMERA_TO_OPENCV,
    convert_waymo_perception_segment,
)
from waymo2panorama.data_io.av2_loader import AV2RingLoader


SEGMENT = "1005081002024129653_5313_150_5333_150"
COMMIT = "16f190af257573d5d7ff9b15b040f3d99c54a921"
CREATED_AT = "2026-07-30T14:00:00Z"
COMPONENTS = (
    "camera_image",
    "camera_calibration",
    "vehicle_pose",
    "lidar",
    "lidar_calibration",
    "lidar_box",
)
EXPECTED_CAMERA_MAP = (
    (1, "FRONT", "ring_front_center"),
    (2, "FRONT_LEFT", "ring_front_left"),
    (4, "SIDE_LEFT", "ring_side_left"),
    (5, "SIDE_RIGHT", "ring_side_right"),
    (3, "FRONT_RIGHT", "ring_front_right"),
)


@pytest.fixture
def writable_test_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    scratch_root = repo_root / ".pytest_cache" / "db212_waymo_perception_adapter"
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


def _write_component(root: Path, component: str, rows: list[dict[str, object]]) -> Path:
    path = root / component / f"{SEGMENT}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def _transform(rotation: np.ndarray, translation: list[float]) -> list[float]:
    value = np.eye(4, dtype=np.float64)
    value[:3, :3] = rotation
    value[:3, 3] = translation
    return value.reshape(-1).tolist()


def _write_waymo_components(root: Path) -> Path:
    component_root = root / "components"
    timestamps_us = [1_000_000, 1_100_000]

    camera_rows = []
    calibration_rows = []
    for camera_index, (camera_name, _, _) in enumerate(EXPECTED_CAMERA_MAP):
        calibration_rows.append(
            {
                "key.segment_context_name": SEGMENT,
                "key.camera_name": camera_name,
                "[CameraCalibrationComponent].extrinsic.transform": _transform(
                    np.eye(3),
                    [float(camera_index), 0.25, 1.5],
                ),
                "[CameraCalibrationComponent].intrinsic.f_u": 100.0 + camera_index,
                "[CameraCalibrationComponent].intrinsic.f_v": 110.0 + camera_index,
                "[CameraCalibrationComponent].intrinsic.c_u": 2.0,
                "[CameraCalibrationComponent].intrinsic.c_v": 1.5,
                "[CameraCalibrationComponent].intrinsic.k1": 0.01,
                "[CameraCalibrationComponent].intrinsic.k2": 0.02,
                "[CameraCalibrationComponent].intrinsic.p1": 0.001,
                "[CameraCalibrationComponent].intrinsic.p2": 0.002,
                "[CameraCalibrationComponent].intrinsic.k3": 0.03,
                "[CameraCalibrationComponent].width": 4,
                "[CameraCalibrationComponent].height": 3,
            }
        )
        for frame_index, timestamp_us in enumerate(timestamps_us):
            camera_rows.append(
                {
                    "key.segment_context_name": SEGMENT,
                    "key.frame_timestamp_micros": timestamp_us,
                    "key.camera_name": camera_name,
                    "[CameraImageComponent].image": _jpeg(
                        20 + 10 * camera_index + frame_index
                    ),
                }
            )

    vehicle_rows = [
        {
            "key.segment_context_name": SEGMENT,
            "key.frame_timestamp_micros": timestamp_us,
            "[VehiclePoseComponent].world_from_vehicle.transform": _transform(
                np.eye(3),
                [float(frame_index), 2.0 * frame_index, 0.5],
            ),
        }
        for frame_index, timestamp_us in enumerate(timestamps_us)
    ]

    lidar_rotation = rotation_z_deg(90.0)
    lidar_calibration_rows = [
        {
            "key.segment_context_name": SEGMENT,
            "key.laser_name": 1,
            "[LiDARCalibrationComponent].extrinsic.transform": _transform(
                lidar_rotation,
                [10.0, 1.0, 2.0],
            ),
            "[LiDARCalibrationComponent].beam_inclination.min": -0.1,
            "[LiDARCalibrationComponent].beam_inclination.max": 0.2,
            "[LiDARCalibrationComponent].beam_inclination.values": [-0.1, 0.2],
        }
    ]
    lidar_rows = []
    for frame_index, timestamp_us in enumerate(timestamps_us):
        range_image = np.zeros((2, 4, 4), dtype=np.float32)
        range_image[0, 0, :3] = [2.0 + frame_index, 0.5, 0.1]
        range_image[1, 2, :3] = [3.0 + frame_index, 1.5, 0.2]
        lidar_rows.append(
            {
                "key.segment_context_name": SEGMENT,
                "key.frame_timestamp_micros": timestamp_us,
                "key.laser_name": 1,
                "[LiDARComponent].range_image_return1.values": range_image.reshape(
                    -1
                ).tolist(),
                "[LiDARComponent].range_image_return1.shape": [2, 4, 4],
            }
        )

    box_rows = []
    for frame_index, timestamp_us in enumerate(timestamps_us):
        box_rows.extend(
            [
                {
                    "key.segment_context_name": SEGMENT,
                    "key.frame_timestamp_micros": timestamp_us,
                    "key.laser_object_id": f"vehicle-{frame_index}",
                    "[LiDARBoxComponent].box.center.x": 1.0 + frame_index,
                    "[LiDARBoxComponent].box.center.y": 2.0,
                    "[LiDARBoxComponent].box.center.z": 3.0,
                    "[LiDARBoxComponent].box.size.x": 4.0,
                    "[LiDARBoxComponent].box.size.y": 2.0,
                    "[LiDARBoxComponent].box.size.z": 1.5,
                    "[LiDARBoxComponent].box.heading": np.pi / 2.0,
                    "[LiDARBoxComponent].type": 1,
                    "[LiDARBoxComponent].num_lidar_points_in_box": 7,
                },
                {
                    "key.segment_context_name": SEGMENT,
                    "key.frame_timestamp_micros": timestamp_us,
                    "key.laser_object_id": f"unknown-{frame_index}",
                    "[LiDARBoxComponent].box.center.x": 0.0,
                    "[LiDARBoxComponent].box.center.y": 0.0,
                    "[LiDARBoxComponent].box.center.z": 0.0,
                    "[LiDARBoxComponent].box.size.x": 1.0,
                    "[LiDARBoxComponent].box.size.y": 1.0,
                    "[LiDARBoxComponent].box.size.z": 1.0,
                    "[LiDARBoxComponent].box.heading": 0.0,
                    "[LiDARBoxComponent].type": 0,
                    "[LiDARBoxComponent].num_lidar_points_in_box": 0,
                },
            ]
        )

    for component, rows in (
        ("camera_image", camera_rows),
        ("camera_calibration", calibration_rows),
        ("vehicle_pose", vehicle_rows),
        ("lidar", lidar_rows),
        ("lidar_calibration", lidar_calibration_rows),
        ("lidar_box", box_rows),
    ):
        _write_component(component_root, component, rows)
    return component_root


def _arrow_table(path: Path) -> pa.Table:
    with pa.memory_map(str(path), "r") as source:
        return ipc.open_file(source).read_all()


def _expected_first_lidar_points() -> np.ndarray:
    rotation = rotation_z_deg(90.0)
    translation = np.array([10.0, 1.0, 2.0])
    values = []
    for range_m, inclination, column in ((2.0, 0.2, 0), (3.0, -0.1, 2)):
        azimuth_correction = np.pi / 2.0
        azimuth = (0.5 - (column + 0.5) / 4.0) * 2.0 * np.pi - azimuth_correction
        sensor = np.array(
            [
                range_m * np.cos(inclination) * np.cos(azimuth),
                range_m * np.cos(inclination) * np.sin(azimuth),
                range_m * np.sin(inclination),
            ]
        )
        values.append(rotation @ sensor + translation)
    return np.asarray(values)


def test_two_frame_waymo_v2_segment_writes_honest_252_degree_pseudo_av2(
    writable_test_dir: Path,
) -> None:
    component_root = _write_waymo_components(writable_test_dir)
    output_dir, manifest = convert_waymo_perception_segment(
        component_root,
        SEGMENT,
        writable_test_dir / "output",
        "waymo-perception",
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )

    assert WAYMO_CAMERA_MAP == EXPECTED_CAMERA_MAP
    np.testing.assert_allclose(
        WAYMO_CAMERA_TO_OPENCV,
        [[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]],
        atol=0.0,
    )
    assert manifest.dataset == "waymo_perception"
    assert manifest.source_scene_id == SEGMENT
    assert manifest.output_log_id == "waymo-perception"
    assert manifest.mode == "A"
    assert manifest.cameras == tuple(value[2] for value in EXPECTED_CAMERA_MAP)
    assert manifest.anchor_camera == "ring_front_center"
    assert manifest.source_frame_count == manifest.output_frame_count == 2
    assert manifest.frame_contract == "1+1"
    assert manifest.source_frame_rate_hz == pytest.approx(10.0)
    assert manifest.output_frame_rate_hz == pytest.approx(10.0)
    assert manifest.has_lidar and manifest.has_ego_pose and manifest.has_annotations
    assert manifest.real_mask_pattern == "render/**/*_real_mask.png"
    assert manifest.faithfill_mask_pattern is None
    assert manifest.honest_black_mask_pattern == "render/**/*_honest_black_mask.png"
    assert manifest.supported_azimuth_deg == ((0.0, 126.0), (234.0, 360.0))
    assert manifest.honest_black_azimuth_deg == ((126.0, 234.0),)
    assert sum(end - start for start, end in manifest.supported_azimuth_deg) == 252.0
    assert sum(end - start for start, end in manifest.honest_black_azimuth_deg) == 108.0
    assert manifest.coordinate_convention_transform == (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    assert manifest.converter_git_commit == COMMIT
    assert manifest.created_at == CREATED_AT
    assert [frame.anchor_timestamp_ns for frame in manifest.frames] == [
        1_000_000_000,
        1_100_000_000,
    ]
    assert all(
        set(frame.camera_timestamps_ns.values()) == {frame.anchor_timestamp_ns}
        for frame in manifest.frames
    )
    assert [frame.lidar_timestamp_ns for frame in manifest.frames] == [
        1_000_000_000,
        1_100_000_000,
    ]
    assert all(record.frame_count == 2 for record in manifest.camera_records)
    assert all(record.max_sync_delta_ns == 0 for record in manifest.camera_records)

    assert ConversionManifest.read_json(output_dir / "conversion_manifest.json") == manifest
    loader = AV2RingLoader(output_dir, cameras=manifest.cameras)
    assert loader.num_anchor_frames() == 2
    assert tuple(loader.load_synced_frame(1_000_000_000).images) == manifest.cameras
    expected_camera_rotation = np.asarray(WAYMO_CAMERA_TO_OPENCV)
    for camera_index, (_, _, pseudo_name) in enumerate(EXPECTED_CAMERA_MAP):
        calibration = loader.calibration(pseudo_name)
        np.testing.assert_allclose(
            calibration.T_ego_cam[:3, :3],
            expected_camera_rotation,
            atol=1e-12,
        )
        np.testing.assert_allclose(
            calibration.T_ego_cam[:3, 3],
            [float(camera_index), 0.25, 1.5],
            atol=0.0,
        )
        np.testing.assert_allclose(
            calibration.K,
            [
                [100.0 + camera_index, 0.0, 2.0],
                [0.0, 110.0 + camera_index, 1.5],
                [0.0, 0.0, 1.0],
            ],
            atol=0.0,
        )

    lidar = _arrow_table(
        output_dir / "sensors" / "lidar" / "1000000000.feather"
    ).to_pandas()
    assert list(lidar.columns) == ["x", "y", "z", "intensity"]
    assert all(dtype == np.dtype("float32") for dtype in lidar.dtypes)
    np.testing.assert_allclose(
        lidar.loc[:, ["x", "y", "z"]].to_numpy(),
        _expected_first_lidar_points(),
        atol=1e-6,
    )
    np.testing.assert_allclose(lidar.intensity, [0.5, 1.5], atol=0.0)

    annotations = _arrow_table(output_dir / "annotations.feather").to_pandas()
    assert len(annotations) == 2
    assert annotations.track_uuid.tolist() == ["vehicle-0", "vehicle-1"]
    assert annotations.category.tolist() == ["REGULAR_VEHICLE", "REGULAR_VEHICLE"]
    assert annotations.timestamp_ns.tolist() == [1_000_000_000, 1_100_000_000]
    assert annotations.num_interior_pts.tolist() == [7, 7]
    np.testing.assert_allclose(annotations.qw, np.sqrt(0.5), atol=1e-12)
    np.testing.assert_allclose(annotations.qz, np.sqrt(0.5), atol=1e-12)
    np.testing.assert_allclose(annotations.loc[:, ["qx", "qy"]], 0.0, atol=0.0)
    np.testing.assert_allclose(
        annotations.loc[:, ["length_m", "width_m", "height_m"]],
        [[4.0, 2.0, 1.5], [4.0, 2.0, 1.5]],
        atol=0.0,
    )

    city = _arrow_table(output_dir / "city_SE3_egovehicle.feather").to_pandas()
    assert city.timestamp_ns.tolist() == [1_000_000_000, 1_100_000_000]
    np.testing.assert_allclose(
        city.loc[:, ["tx_m", "ty_m", "tz_m"]],
        [[0.0, 0.0, 0.5], [1.0, 2.0, 0.5]],
        atol=0.0,
    )
    for row in city.itertuples(index=False):
        np.testing.assert_allclose(
            quaternion_wxyz_to_matrix([row.qw, row.qx, row.qy, row.qz]),
            np.eye(3),
            atol=0.0,
        )

    artifacts = {artifact.path: artifact for artifact in manifest.source_artifacts}
    assert set(artifacts) == {
        f"{component}/{SEGMENT}.parquet" for component in COMPONENTS
    }
    for component in COMPONENTS:
        path = component_root / component / f"{SEGMENT}.parquet"
        artifact = artifacts[path.relative_to(component_root).as_posix()]
        assert artifact.sha256 == sha256_file(path)
        assert artifact.size_bytes == path.stat().st_size

    digest = hashlib.sha256()
    for path in sorted((output_dir / "calibration").glob("*.feather"), key=lambda value: value.name):
        digest.update(path.read_bytes())
    assert manifest.calibration_sha256 == digest.hexdigest()


def test_blob_components_are_streamed_one_row_per_parquet_batch(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component_root = _write_waymo_components(writable_test_dir)
    real_parquet_file = pq.ParquetFile
    observed: list[tuple[str, int, int]] = []

    class RecordingParquetFile:
        def __init__(self, path: Path) -> None:
            self.path = Path(path)
            self.parquet = real_parquet_file(path)
            self.schema_arrow = self.parquet.schema_arrow

        def iter_batches(
            self,
            *,
            batch_size: int,
            columns: list[str],
        ) -> object:
            for batch in self.parquet.iter_batches(
                batch_size=batch_size,
                columns=columns,
            ):
                observed.append((self.path.parent.name, batch_size, batch.num_rows))
                yield batch

    monkeypatch.setattr(adapter.pq, "ParquetFile", RecordingParquetFile)
    adapter.convert_waymo_perception_segment(
        component_root,
        SEGMENT,
        writable_test_dir / "output",
        "streamed",
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )

    blob_batches = [
        (batch_size, row_count)
        for component, batch_size, row_count in observed
        if component in {"camera_image", "lidar"}
    ]
    assert blob_batches
    assert all(batch_size == 1 and row_count == 1 for batch_size, row_count in blob_batches)
