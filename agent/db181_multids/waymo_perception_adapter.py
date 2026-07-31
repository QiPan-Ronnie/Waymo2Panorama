from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
from PIL import Image

from waymo2panorama.data_io.av2_loader import AV2RingLoader

from .contract import CameraRecord, ConversionManifest, FrameRecord, SourceArtifact
from .geometry import matrix_to_quaternion_wxyz, validate_rigid_transform
from .io import empty_annotations_frame, sha256_file, write_feather


WAYMO_CAMERA_MAP: tuple[tuple[int, str, str], ...] = (
    (1, "FRONT", "ring_front_center"),
    (2, "FRONT_LEFT", "ring_front_left"),
    (4, "SIDE_LEFT", "ring_side_left"),
    (5, "SIDE_RIGHT", "ring_side_right"),
    (3, "FRONT_RIGHT", "ring_front_right"),
)

WAYMO_CAMERA_TO_OPENCV = np.array(
    [[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]],
    dtype=np.float64,
)

_COMPONENTS = (
    "camera_image",
    "camera_calibration",
    "vehicle_pose",
    "lidar",
    "lidar_calibration",
    "lidar_box",
)
_TOP_LIDAR = 1
_CAMERA_BY_ID = {camera_id: value for camera_id, *value in WAYMO_CAMERA_MAP}
_BOX_CATEGORY = {
    1: "REGULAR_VEHICLE",
    2: "PEDESTRIAN",
    3: "SIGN",
    4: "BICYCLIST",
}
_ANNOTATION_COLUMNS = (
    "timestamp_ns",
    "track_uuid",
    "category",
    "length_m",
    "width_m",
    "height_m",
    "qw",
    "qx",
    "qy",
    "qz",
    "tx_m",
    "ty_m",
    "tz_m",
    "num_interior_pts",
)

_SEGMENT = "key.segment_context_name"
_TIMESTAMP = "key.frame_timestamp_micros"
_CAMERA_NAME = "key.camera_name"
_LASER_NAME = "key.laser_name"
_OBJECT_ID = "key.laser_object_id"

_IMAGE = "[CameraImageComponent].image"
_CAMERA_EXTRINSIC = "[CameraCalibrationComponent].extrinsic.transform"
_INTRINSIC_PREFIX = "[CameraCalibrationComponent].intrinsic."
_CAMERA_WIDTH = "[CameraCalibrationComponent].width"
_CAMERA_HEIGHT = "[CameraCalibrationComponent].height"
_WORLD_FROM_VEHICLE = "[VehiclePoseComponent].world_from_vehicle.transform"
_LIDAR_EXTRINSIC = "[LiDARCalibrationComponent].extrinsic.transform"
_BEAM_MIN = "[LiDARCalibrationComponent].beam_inclination.min"
_BEAM_MAX = "[LiDARCalibrationComponent].beam_inclination.max"
_BEAM_VALUES = "[LiDARCalibrationComponent].beam_inclination.values"
_RANGE_VALUES = "[LiDARComponent].range_image_return1.values"
_RANGE_SHAPE = "[LiDARComponent].range_image_return1.shape"
_BOX_PREFIX = "[LiDARBoxComponent]."


@dataclass(frozen=True)
class _SourceSnapshot:
    path: Path
    artifact_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class _CameraCalibration:
    camera_id: int
    source_name: str
    pseudo_name: str
    transform: np.ndarray
    intrinsic: dict[str, float]
    width_px: int
    height_px: int


@dataclass(frozen=True)
class _LidarCalibration:
    transform: np.ndarray
    beam_min: float
    beam_max: float
    beam_values: tuple[float, ...] | None


def _component_path(root: Path, component: str, segment: str) -> Path:
    return root / component / f"{segment}.parquet"


def _snapshot(root: Path, path: Path) -> _SourceSnapshot:
    artifact_path = path.relative_to(root).as_posix()
    try:
        size_before = path.stat().st_size
        digest = sha256_file(path)
        size_after = path.stat().st_size
    except (FileNotFoundError, OSError) as error:
        raise ValueError(f"source snapshot failed for {artifact_path}") from error
    if size_before != size_after:
        raise ValueError(f"source changed while snapshotting: {artifact_path}")
    return _SourceSnapshot(path, artifact_path, digest, size_after)


def _verify_snapshot(snapshot: _SourceSnapshot) -> None:
    try:
        size_before = snapshot.path.stat().st_size
        digest = sha256_file(snapshot.path)
        size_after = snapshot.path.stat().st_size
    except (FileNotFoundError, OSError) as error:
        raise ValueError(f"source changed after preflight: {snapshot.artifact_path}") from error
    if (
        size_before != size_after
        or size_after != snapshot.size_bytes
        or digest != snapshot.sha256
    ):
        raise ValueError(f"source changed after preflight snapshot: {snapshot.artifact_path}")


def _iter_rows(
    path: Path,
    columns: tuple[str, ...],
    *,
    batch_size: int,
) -> Iterator[dict[str, Any]]:
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, pa.ArrowException) as error:
        raise ValueError(f"invalid parquet component: {path}") from error
    missing = set(columns) - set(parquet.schema_arrow.names)
    if missing:
        raise ValueError(f"parquet component {path} missing columns: {sorted(missing)}")
    for batch in parquet.iter_batches(batch_size=batch_size, columns=list(columns)):
        yield from batch.to_pylist()


def _positive_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context} must be a positive integer")
    return value


def _finite_float(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{context} must be finite")
    return result


def _matrix(value: object, context: str) -> np.ndarray:
    try:
        matrix = np.asarray(value, dtype=np.float64).reshape((4, 4))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} must contain a flattened 4x4 transform") from error
    try:
        return validate_rigid_transform(matrix)
    except ValueError as error:
        raise ValueError(f"invalid rigid transform for {context}: {error}") from error


def _require_segment(row: dict[str, Any], expected: str, context: str) -> None:
    if row.get(_SEGMENT) != expected:
        raise ValueError(f"{context} row has wrong segment context name")


def _camera_calibrations(path: Path, segment: str) -> dict[int, _CameraCalibration]:
    columns = (
        _SEGMENT,
        _CAMERA_NAME,
        _CAMERA_EXTRINSIC,
        *(_INTRINSIC_PREFIX + name for name in ("f_u", "f_v", "c_u", "c_v")),
        *(_INTRINSIC_PREFIX + name for name in ("k1", "k2", "p1", "p2", "k3")),
        _CAMERA_WIDTH,
        _CAMERA_HEIGHT,
    )
    calibrations: dict[int, _CameraCalibration] = {}
    convention = np.eye(4, dtype=np.float64)
    convention[:3, :3] = WAYMO_CAMERA_TO_OPENCV
    for row in _iter_rows(path, columns, batch_size=64):
        _require_segment(row, segment, "camera_calibration")
        camera_id = _positive_int(row[_CAMERA_NAME], "camera name")
        mapping = _CAMERA_BY_ID.get(camera_id)
        if mapping is None:
            raise ValueError(f"unsupported Waymo camera name: {camera_id}")
        if camera_id in calibrations:
            raise ValueError(f"duplicate camera calibration for {camera_id}")
        source_name, pseudo_name = mapping
        intrinsic = {
            name: _finite_float(
                row[_INTRINSIC_PREFIX + name],
                f"{source_name} intrinsic {name}",
            )
            for name in ("f_u", "f_v", "c_u", "c_v", "k1", "k2", "p1", "p2", "k3")
        }
        if intrinsic["f_u"] <= 0.0 or intrinsic["f_v"] <= 0.0:
            raise ValueError(f"{source_name} intrinsic focal lengths must be positive")
        width = _positive_int(row[_CAMERA_WIDTH], f"{source_name} width")
        height = _positive_int(row[_CAMERA_HEIGHT], f"{source_name} height")
        transform = validate_rigid_transform(
            _matrix(row[_CAMERA_EXTRINSIC], f"{source_name} extrinsic") @ convention
        )
        calibrations[camera_id] = _CameraCalibration(
            camera_id,
            source_name,
            pseudo_name,
            transform,
            intrinsic,
            width,
            height,
        )
    if set(calibrations) != set(_CAMERA_BY_ID):
        missing = sorted(set(_CAMERA_BY_ID) - set(calibrations))
        raise ValueError(f"missing required Waymo camera calibrations: {missing}")
    return calibrations


def _lidar_calibration(path: Path, segment: str) -> _LidarCalibration:
    columns = (
        _SEGMENT,
        _LASER_NAME,
        _LIDAR_EXTRINSIC,
        _BEAM_MIN,
        _BEAM_MAX,
        _BEAM_VALUES,
    )
    result: _LidarCalibration | None = None
    for row in _iter_rows(path, columns, batch_size=16):
        _require_segment(row, segment, "lidar_calibration")
        laser_name = _positive_int(row[_LASER_NAME], "laser name")
        if laser_name != _TOP_LIDAR:
            continue
        if result is not None:
            raise ValueError("duplicate TOP lidar calibration")
        beam_min = _finite_float(row[_BEAM_MIN], "beam inclination min")
        beam_max = _finite_float(row[_BEAM_MAX], "beam inclination max")
        if beam_max <= beam_min:
            raise ValueError("beam inclination max must exceed min")
        raw_values = row[_BEAM_VALUES]
        beam_values: tuple[float, ...] | None = None
        if raw_values is not None:
            if not isinstance(raw_values, list) or not raw_values:
                raise ValueError("beam inclination values must be a nonempty list or null")
            beam_values = tuple(
                _finite_float(value, "beam inclination value") for value in raw_values
            )
        result = _LidarCalibration(
            transform=_matrix(row[_LIDAR_EXTRINSIC], "TOP lidar extrinsic"),
            beam_min=beam_min,
            beam_max=beam_max,
            beam_values=beam_values,
        )
    if result is None:
        raise ValueError("missing TOP lidar calibration")
    return result


def _range_image(row: dict[str, Any]) -> np.ndarray:
    shape = row[_RANGE_SHAPE]
    if (
        not isinstance(shape, list)
        or len(shape) != 3
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in shape)
        or shape[2] != 4
    ):
        raise ValueError("TOP range image shape must be [H, W, 4]")
    try:
        values = np.asarray(row[_RANGE_VALUES], dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise ValueError("TOP range image values must be float32-compatible") from error
    if values.size != math.prod(shape):
        raise ValueError("TOP range image values/shape mismatch")
    image = values.reshape(tuple(shape))
    if not np.isfinite(image).all():
        raise ValueError("TOP range image contains non-finite values")
    if np.any(image[..., 0] < 0.0):
        raise ValueError("TOP range image ranges must be nonnegative")
    return image


def _reconstruct_top_lidar(
    row: dict[str, Any], calibration: _LidarCalibration
) -> np.ndarray:
    range_image = _range_image(row)
    height, width, _ = range_image.shape
    if calibration.beam_values is not None:
        if len(calibration.beam_values) != height:
            raise ValueError("beam inclination count must equal TOP range image height")
        inclinations = np.asarray(calibration.beam_values[::-1], dtype=np.float64)
    else:
        centers = (0.5 + np.arange(height, dtype=np.float64)) / height
        inclinations = (
            calibration.beam_min
            + centers * (calibration.beam_max - calibration.beam_min)
        )[::-1]
    columns = np.arange(width, dtype=np.float64)
    azimuth_correction = math.atan2(
        calibration.transform[1, 0], calibration.transform[0, 0]
    )
    azimuths = (
        (0.5 - (columns + 0.5) / width) * 2.0 * np.pi - azimuth_correction
    )
    ranges = range_image[..., 0].astype(np.float64)
    cos_inclination = np.cos(inclinations)[:, None]
    sensor_xyz = np.stack(
        [
            ranges * cos_inclination * np.cos(azimuths)[None, :],
            ranges * cos_inclination * np.sin(azimuths)[None, :],
            ranges * np.sin(inclinations)[:, None] * np.ones((1, width)),
        ],
        axis=-1,
    )
    ego_xyz = (
        sensor_xyz @ calibration.transform[:3, :3].T
        + calibration.transform[:3, 3]
    )
    mask = ranges > 0.0
    return np.column_stack(
        [ego_xyz[mask], range_image[..., 1][mask].astype(np.float64)]
    ).astype(np.float32)


def _validate_jpeg(payload: object, calibration: _CameraCalibration) -> bytes:
    if not isinstance(payload, bytes) or not payload:
        raise ValueError(f"{calibration.source_name} image must contain JPEG bytes")
    try:
        with Image.open(BytesIO(payload)) as image:
            dimensions = image.size
            image.verify()
    except Exception as error:
        raise ValueError(f"invalid JPEG for {calibration.source_name}") from error
    if dimensions != (calibration.width_px, calibration.height_px):
        raise ValueError(f"{calibration.source_name} image dimensions mismatch calibration")
    return payload


def _camera_timestamps(
    path: Path,
    segment: str,
    calibrations: dict[int, _CameraCalibration],
) -> dict[int, tuple[int, ...]]:
    timestamps: dict[int, list[int]] = {camera_id: [] for camera_id in calibrations}
    columns = (_SEGMENT, _TIMESTAMP, _CAMERA_NAME, _IMAGE)
    for row in _iter_rows(path, columns, batch_size=1):
        _require_segment(row, segment, "camera_image")
        camera_id = _positive_int(row[_CAMERA_NAME], "camera name")
        calibration = calibrations.get(camera_id)
        if calibration is None:
            raise ValueError(f"unsupported Waymo camera image name: {camera_id}")
        timestamp = _positive_int(row[_TIMESTAMP], "camera frame timestamp") * 1000
        _validate_jpeg(row[_IMAGE], calibration)
        timestamps[camera_id].append(timestamp)
    checked: dict[int, tuple[int, ...]] = {}
    for camera_id, values in timestamps.items():
        ordered = tuple(sorted(values))
        if not ordered:
            raise ValueError(f"missing camera images for {calibrations[camera_id].source_name}")
        if len(set(ordered)) != len(ordered):
            raise ValueError(
                f"duplicate camera timestamp for {calibrations[camera_id].source_name}"
            )
        checked[camera_id] = ordered
    anchors = checked[WAYMO_CAMERA_MAP[0][0]]
    for camera_id, values in checked.items():
        if values != anchors:
            raise ValueError(
                f"camera timestamps for {calibrations[camera_id].source_name} "
                "must exactly match FRONT anchors"
            )
    return checked


def _vehicle_poses(path: Path, segment: str) -> dict[int, np.ndarray]:
    columns = (_SEGMENT, _TIMESTAMP, _WORLD_FROM_VEHICLE)
    poses: dict[int, np.ndarray] = {}
    for row in _iter_rows(path, columns, batch_size=64):
        _require_segment(row, segment, "vehicle_pose")
        timestamp = _positive_int(row[_TIMESTAMP], "vehicle pose timestamp") * 1000
        if timestamp in poses:
            raise ValueError(f"duplicate vehicle pose timestamp: {timestamp}")
        poses[timestamp] = _matrix(row[_WORLD_FROM_VEHICLE], "world_from_vehicle")
    return poses


def _lidar_timestamps(
    path: Path,
    segment: str,
    calibration: _LidarCalibration,
) -> tuple[int, ...]:
    columns = (_SEGMENT, _TIMESTAMP, _LASER_NAME, _RANGE_VALUES, _RANGE_SHAPE)
    timestamps: list[int] = []
    for row in _iter_rows(path, columns, batch_size=1):
        _require_segment(row, segment, "lidar")
        laser_name = _positive_int(row[_LASER_NAME], "laser name")
        if laser_name != _TOP_LIDAR:
            continue
        timestamp = _positive_int(row[_TIMESTAMP], "lidar timestamp") * 1000
        _reconstruct_top_lidar(row, calibration)
        timestamps.append(timestamp)
    ordered = tuple(sorted(timestamps))
    if not ordered:
        raise ValueError("missing TOP lidar range images")
    if len(set(ordered)) != len(ordered):
        raise ValueError("duplicate TOP lidar timestamp")
    return ordered


def _annotation_rows(path: Path, segment: str) -> list[dict[str, object]]:
    columns = (
        _SEGMENT,
        _TIMESTAMP,
        _OBJECT_ID,
        *(_BOX_PREFIX + f"box.center.{axis}" for axis in "xyz"),
        *(_BOX_PREFIX + f"box.size.{axis}" for axis in "xyz"),
        _BOX_PREFIX + "box.heading",
        _BOX_PREFIX + "type",
        _BOX_PREFIX + "num_lidar_points_in_box",
    )
    annotations = []
    for row in _iter_rows(path, columns, batch_size=256):
        _require_segment(row, segment, "lidar_box")
        box_type = row[_BOX_PREFIX + "type"]
        if isinstance(box_type, bool) or not isinstance(box_type, int):
            raise ValueError("lidar box type must be an integer")
        category = _BOX_CATEGORY.get(box_type)
        if category is None:
            continue
        object_id = row[_OBJECT_ID]
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("lidar box object id must be nonempty")
        timestamp = _positive_int(row[_TIMESTAMP], "lidar box timestamp") * 1000
        center = [
            _finite_float(row[_BOX_PREFIX + f"box.center.{axis}"], f"box center {axis}")
            for axis in "xyz"
        ]
        size = [
            _finite_float(row[_BOX_PREFIX + f"box.size.{axis}"], f"box size {axis}")
            for axis in "xyz"
        ]
        if any(value <= 0.0 for value in size):
            raise ValueError("lidar box dimensions must be positive")
        heading = _finite_float(row[_BOX_PREFIX + "box.heading"], "box heading")
        point_count = row[_BOX_PREFIX + "num_lidar_points_in_box"]
        if isinstance(point_count, bool) or not isinstance(point_count, int) or point_count < 0:
            raise ValueError("num_lidar_points_in_box must be a nonnegative integer")
        annotations.append(
            {
                "timestamp_ns": timestamp,
                "track_uuid": object_id,
                "category": category,
                "length_m": size[0],
                "width_m": size[1],
                "height_m": size[2],
                "qw": math.cos(heading / 2.0),
                "qx": 0.0,
                "qy": 0.0,
                "qz": math.sin(heading / 2.0),
                "tx_m": center[0],
                "ty_m": center[1],
                "tz_m": center[2],
                "num_interior_pts": point_count,
            }
        )
    return sorted(
        annotations,
        key=lambda value: (int(value["timestamp_ns"]), str(value["track_uuid"])),
    )


def _pose_row(sensor_name: str, transform: np.ndarray) -> dict[str, object]:
    qw, qx, qy, qz = matrix_to_quaternion_wxyz(transform[:3, :3])
    return {
        "sensor_name": sensor_name,
        "qw": qw,
        "qx": qx,
        "qy": qy,
        "qz": qz,
        "tx_m": float(transform[0, 3]),
        "ty_m": float(transform[1, 3]),
        "tz_m": float(transform[2, 3]),
    }


def _city_pose_row(timestamp_ns: int, transform: np.ndarray) -> dict[str, object]:
    row = _pose_row("vehicle", transform)
    row.pop("sensor_name")
    return {"timestamp_ns": timestamp_ns, **row}


def _calibration_hash(calibration_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(calibration_dir.glob("*.feather"), key=lambda value: value.name):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def _validate_written_log(
    staging: Path,
    manifest: ConversionManifest,
    expected_camera_hashes: dict[str, str],
) -> None:
    manifest.validate()
    ConversionManifest.read_json(staging / "conversion_manifest.json")
    arrow_paths = [
        staging / "calibration" / "intrinsics.feather",
        staging / "calibration" / "egovehicle_SE3_sensor.feather",
        staging / "city_SE3_egovehicle.feather",
        staging / "annotations.feather",
    ]
    arrow_paths.extend(
        staging / "sensors" / "lidar" / f"{frame.lidar_timestamp_ns}.feather"
        for frame in manifest.frames
    )
    for path in arrow_paths:
        with pa.memory_map(str(path), "r") as source:
            ipc.open_file(source).read_all()
    images = tuple((staging / "sensors" / "cameras").glob("**/*.jpg"))
    images_by_relative = {path.relative_to(staging).as_posix(): path for path in images}
    if set(images_by_relative) != set(expected_camera_hashes):
        raise ValueError("staged camera image set does not match source component")
    for relative, path in images_by_relative.items():
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            raise ValueError(f"invalid staged camera JPEG: {relative}") from error
        if sha256_file(path) != expected_camera_hashes[relative]:
            raise ValueError(f"staged camera JPEG hash mismatch: {relative}")
    loader = AV2RingLoader(staging, cameras=manifest.cameras)
    loader.load_synced_frame(manifest.frames[0].anchor_timestamp_ns)


def convert_waymo_perception_segment(
    component_root: Path,
    segment_context_name: str,
    output_root: Path,
    output_log_id: str,
    *,
    real_mask_pattern: str = "render/**/*_real_mask.png",
    honest_black_mask_pattern: str = "render/**/*_honest_black_mask.png",
    converter_git_commit: str,
    created_at: str,
) -> tuple[Path, ConversionManifest]:
    component_root = Path(component_root)
    output_root = Path(output_root)
    if not component_root.is_dir():
        raise ValueError(f"component_root must be a directory: {component_root}")
    if (
        not segment_context_name
        or segment_context_name in (".", "..")
        or Path(segment_context_name).name != segment_context_name
    ):
        raise ValueError("segment_context_name must be one nonempty path component")
    if (
        not output_log_id
        or output_log_id in (".", "..")
        or Path(output_log_id).name != output_log_id
    ):
        raise ValueError("output_log_id must be one nonempty path component")
    if not isinstance(real_mask_pattern, str) or not real_mask_pattern.strip():
        raise ValueError("Waymo Perception A mode requires a nonempty real_mask_pattern")
    if (
        not isinstance(honest_black_mask_pattern, str)
        or not honest_black_mask_pattern.strip()
    ):
        raise ValueError("252-degree output requires a nonempty honest_black_mask_pattern")
    final_output = output_root / output_log_id
    if os.path.lexists(final_output):
        raise FileExistsError(f"output log already exists: {final_output}")

    component_paths = {
        component: _component_path(component_root, component, segment_context_name)
        for component in _COMPONENTS
    }
    snapshots = {
        component: _snapshot(component_root, path)
        for component, path in component_paths.items()
    }
    camera_calibrations = _camera_calibrations(
        component_paths["camera_calibration"], segment_context_name
    )
    lidar_calibration = _lidar_calibration(
        component_paths["lidar_calibration"], segment_context_name
    )
    camera_timestamps = _camera_timestamps(
        component_paths["camera_image"],
        segment_context_name,
        camera_calibrations,
    )
    anchors = camera_timestamps[WAYMO_CAMERA_MAP[0][0]]
    poses = _vehicle_poses(component_paths["vehicle_pose"], segment_context_name)
    if tuple(sorted(poses)) != anchors:
        raise ValueError("vehicle pose timestamps must exactly match FRONT anchors")
    lidar_timestamps = _lidar_timestamps(
        component_paths["lidar"], segment_context_name, lidar_calibration
    )
    if lidar_timestamps != anchors:
        raise ValueError("TOP lidar timestamps must exactly match FRONT anchors")
    annotations = _annotation_rows(component_paths["lidar_box"], segment_context_name)
    if any(int(row["timestamp_ns"]) not in poses for row in annotations):
        raise ValueError("lidar box timestamp is outside converted frame timestamps")
    for snapshot in snapshots.values():
        _verify_snapshot(snapshot)

    frames = tuple(
        FrameRecord(
            index=index,
            anchor_timestamp_ns=timestamp,
            camera_timestamps_ns={
                pseudo_name: camera_timestamps[camera_id][index]
                for camera_id, _, pseudo_name in WAYMO_CAMERA_MAP
            },
            lidar_timestamp_ns=lidar_timestamps[index],
        )
        for index, timestamp in enumerate(anchors)
    )
    source_artifacts = tuple(
        SourceArtifact(snapshot.artifact_path, snapshot.sha256, snapshot.size_bytes)
        for snapshot in sorted(snapshots.values(), key=lambda value: value.artifact_path)
    )

    output_root.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(final_output):
        raise FileExistsError(f"output log already exists: {final_output}")
    staging = Path(tempfile.mkdtemp(prefix=f".{output_log_id}.staging-", dir=output_root))
    published = False
    try:
        calibration_dir = staging / "calibration"
        intrinsics_rows = []
        extrinsics_rows = []
        for camera_id, _, pseudo_name in WAYMO_CAMERA_MAP:
            calibration = camera_calibrations[camera_id]
            intrinsics_rows.append(
                {
                    "sensor_name": pseudo_name,
                    "fx_px": calibration.intrinsic["f_u"],
                    "fy_px": calibration.intrinsic["f_v"],
                    "cx_px": calibration.intrinsic["c_u"],
                    "cy_px": calibration.intrinsic["c_v"],
                    "width_px": calibration.width_px,
                    "height_px": calibration.height_px,
                    "k1": calibration.intrinsic["k1"],
                    "k2": calibration.intrinsic["k2"],
                    "k3": calibration.intrinsic["k3"],
                    "p1": calibration.intrinsic["p1"],
                    "p2": calibration.intrinsic["p2"],
                }
            )
            extrinsics_rows.append(_pose_row(pseudo_name, calibration.transform))
        extrinsics_rows.append(_pose_row("up_lidar", lidar_calibration.transform))
        write_feather(pd.DataFrame(intrinsics_rows), calibration_dir / "intrinsics.feather")
        write_feather(
            pd.DataFrame(extrinsics_rows),
            calibration_dir / "egovehicle_SE3_sensor.feather",
        )

        expected_camera_hashes: dict[str, str] = {}
        image_columns = (_SEGMENT, _TIMESTAMP, _CAMERA_NAME, _IMAGE)
        for row in _iter_rows(
            component_paths["camera_image"], image_columns, batch_size=1
        ):
            _require_segment(row, segment_context_name, "camera_image")
            camera_id = _positive_int(row[_CAMERA_NAME], "camera name")
            calibration = camera_calibrations[camera_id]
            timestamp = _positive_int(row[_TIMESTAMP], "camera timestamp") * 1000
            payload = _validate_jpeg(row[_IMAGE], calibration)
            destination = (
                staging
                / "sensors"
                / "cameras"
                / calibration.pseudo_name
                / f"{timestamp}.jpg"
            )
            _write_bytes(destination, payload)
            expected_camera_hashes[destination.relative_to(staging).as_posix()] = (
                hashlib.sha256(payload).hexdigest()
            )
        _verify_snapshot(snapshots["camera_image"])

        lidar_columns = (_SEGMENT, _TIMESTAMP, _LASER_NAME, _RANGE_VALUES, _RANGE_SHAPE)
        for row in _iter_rows(component_paths["lidar"], lidar_columns, batch_size=1):
            _require_segment(row, segment_context_name, "lidar")
            if _positive_int(row[_LASER_NAME], "laser name") != _TOP_LIDAR:
                continue
            timestamp = _positive_int(row[_TIMESTAMP], "lidar timestamp") * 1000
            points = _reconstruct_top_lidar(row, lidar_calibration)
            lidar_frame = pd.DataFrame(
                {
                    "x": points[:, 0],
                    "y": points[:, 1],
                    "z": points[:, 2],
                    "intensity": points[:, 3],
                }
            )
            write_feather(
                lidar_frame,
                staging / "sensors" / "lidar" / f"{timestamp}.feather",
            )
            del points, lidar_frame
        _verify_snapshot(snapshots["lidar"])

        annotation_frame = (
            pd.DataFrame(annotations, columns=_ANNOTATION_COLUMNS)
            if annotations
            else empty_annotations_frame()
        )
        write_feather(annotation_frame, staging / "annotations.feather")
        write_feather(
            pd.DataFrame(
                [_city_pose_row(timestamp, poses[timestamp]) for timestamp in anchors]
            ),
            staging / "city_SE3_egovehicle.feather",
        )

        frame_rate = (
            1.0
            if len(anchors) == 1
            else 1_000_000_000.0
            / float(np.median(np.diff(np.asarray(anchors, dtype=np.int64))))
        )
        manifest = ConversionManifest(
            schema_version="1.0",
            dataset="waymo_perception",
            source_scene_id=segment_context_name,
            output_log_id=output_log_id,
            mode="A",
            cameras=tuple(value[2] for value in WAYMO_CAMERA_MAP),
            anchor_camera=WAYMO_CAMERA_MAP[0][2],
            source_frame_count=len(anchors),
            output_frame_count=len(frames),
            source_frame_rate_hz=frame_rate,
            output_frame_rate_hz=frame_rate,
            camera_records=tuple(
                CameraRecord(
                    name=pseudo_name,
                    source_name=source_name,
                    frame_count=len(frames),
                    max_sync_delta_ns=0,
                )
                for _, source_name, pseudo_name in WAYMO_CAMERA_MAP
            ),
            frames=frames,
            calibration_sha256=_calibration_hash(calibration_dir),
            source_artifacts=source_artifacts,
            has_lidar=True,
            has_ego_pose=True,
            has_annotations=True,
            real_mask_pattern=real_mask_pattern,
            faithfill_mask_pattern=None,
            honest_black_mask_pattern=honest_black_mask_pattern,
            supported_azimuth_deg=((0.0, 126.0), (234.0, 360.0)),
            honest_black_azimuth_deg=((126.0, 234.0),),
            coordinate_convention_transform=(
                (1.0, 0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            ),
            converter_git_commit=converter_git_commit,
            created_at=created_at,
        )
        manifest.write_json(staging / "conversion_manifest.json")
        _validate_written_log(staging, manifest, expected_camera_hashes)
        for snapshot in snapshots.values():
            _verify_snapshot(snapshot)
        if os.path.lexists(final_output):
            raise FileExistsError(f"output log already exists: {final_output}")
        staging.rename(final_output)
        published = True
        return final_output, manifest
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)
