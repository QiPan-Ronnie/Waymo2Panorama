"""Convert Waymo End-to-End camera TFRecords to the honest Mode-B contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

from waymo2panorama.data_io.av2_loader import AV2RingLoader

from .contract import CameraRecord, ConversionManifest, FrameRecord, SourceArtifact
from .geometry import matrix_to_quaternion_wxyz, validate_rigid_transform
from .io import sha256_file, write_feather


WAYMO_E2E_CAMERA_MAP: tuple[tuple[str, str], ...] = (
    ("FRONT", "ring_front_center"),
    ("FRONT_LEFT", "ring_front_left"),
    ("SIDE_LEFT", "ring_side_left"),
    ("REAR_LEFT", "ring_rear_left"),
    ("REAR", "ring_rear"),
    ("REAR_RIGHT", "ring_rear_right"),
    ("SIDE_RIGHT", "ring_side_right"),
    ("FRONT_RIGHT", "ring_front_right"),
)

WAYMO_CAMERA_TO_OPENCV = np.array(
    [[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]],
    dtype=np.float64,
)

_PSEUDO_BY_SOURCE = dict(WAYMO_E2E_CAMERA_MAP)


@dataclass(frozen=True)
class E2ECameraFrame:
    """One decoded E2E camera observation in Waymo camera coordinates."""

    source_name: str
    timestamp_ns: int
    image_jpeg: bytes
    intrinsic: tuple[float, ...]
    transform_ego_waymo_camera: tuple[float, ...]
    transform_world_ego: tuple[float, ...]
    width_px: int
    height_px: int


@dataclass(frozen=True)
class E2EFrame:
    """One E2E record, containing all eight asynchronous camera observations."""

    source_scene_id: str
    anchor_timestamp_ns: int
    cameras: tuple[E2ECameraFrame, ...]


RecordDecoder = Callable[[bytes, int], E2EFrame]


@dataclass(frozen=True)
class _Calibration:
    source_name: str
    pseudo_name: str
    intrinsic: tuple[float, ...]
    transform_ego_opencv_camera: np.ndarray
    width_px: int
    height_px: int


def _iter_tfrecord_payloads(path: Path) -> Iterator[bytes]:
    with path.open("rb") as stream:
        record_index = 0
        while True:
            header = stream.read(8)
            if not header:
                return
            if len(header) != 8:
                raise ValueError(f"truncated TFRecord length header at record {record_index}")
            payload_size = struct.unpack("<Q", header)[0]
            if len(stream.read(4)) != 4:
                raise ValueError(f"truncated TFRecord length CRC at record {record_index}")
            payload = stream.read(payload_size)
            if len(payload) != payload_size:
                raise ValueError(f"truncated TFRecord payload at record {record_index}")
            if len(stream.read(4)) != 4:
                raise ValueError(f"truncated TFRecord data CRC at record {record_index}")
            yield payload
            record_index += 1


def _decode_waymo_record(payload: bytes, record_index: int) -> E2EFrame:
    try:
        from waymo_open_dataset import dataset_pb2
        from waymo_open_dataset.protos import end_to_end_driving_data_pb2 as e2ed
    except ImportError as error:
        raise RuntimeError(
            "Waymo E2E decoding requires waymo-open-dataset; install it in the "
            "runtime or pass record_decoder for a pre-decoded source"
        ) from error

    message = e2ed.E2EDFrame()
    try:
        message.ParseFromString(payload)
    except Exception as error:
        raise ValueError(f"invalid E2EDFrame protobuf at record {record_index}") from error
    frame = message.frame
    context_name = str(frame.context.name)
    if not context_name:
        raise ValueError(f"record {record_index} has an empty context name")
    fallback_timestamp_ns = int(frame.timestamp_micros) * 1000
    calibrations = {int(value.name): value for value in frame.context.camera_calibrations}
    cameras: list[E2ECameraFrame] = []
    for image in frame.images:
        camera_id = int(image.name)
        calibration = calibrations.get(camera_id)
        if calibration is None:
            raise ValueError(
                f"record {record_index} camera {camera_id} has no calibration"
            )
        source_name = dataset_pb2.CameraName.Name.Name(camera_id)
        pose_timestamp = float(getattr(image, "pose_timestamp", 0.0))
        timestamp_ns = (
            int(round(pose_timestamp * 1_000_000_000.0))
            if pose_timestamp > 0.0
            else fallback_timestamp_ns
        )
        cameras.append(
            E2ECameraFrame(
                source_name=source_name,
                timestamp_ns=timestamp_ns,
                image_jpeg=bytes(image.image),
                intrinsic=tuple(float(value) for value in calibration.intrinsic),
                transform_ego_waymo_camera=tuple(
                    float(value) for value in calibration.extrinsic.transform
                ),
                transform_world_ego=tuple(
                    float(value) for value in image.pose.transform
                ),
                width_px=int(calibration.width),
                height_px=int(calibration.height),
            )
        )
    by_name = {camera.source_name: camera for camera in cameras}
    front = by_name.get("FRONT")
    anchor_timestamp_ns = (
        front.timestamp_ns if front is not None else fallback_timestamp_ns
    )
    return E2EFrame(context_name, anchor_timestamp_ns, tuple(cameras))


def _positive_integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context} must be a positive integer")
    return value


def _calibration(camera: E2ECameraFrame) -> _Calibration:
    pseudo_name = _PSEUDO_BY_SOURCE[camera.source_name]
    if len(camera.intrinsic) < 9:
        raise ValueError(f"{camera.source_name} intrinsic must contain at least 9 values")
    intrinsic = tuple(float(value) for value in camera.intrinsic[:9])
    if not np.isfinite(intrinsic).all() or intrinsic[0] <= 0.0 or intrinsic[1] <= 0.0:
        raise ValueError(f"{camera.source_name} intrinsic is invalid")
    try:
        transform = np.asarray(
            camera.transform_ego_waymo_camera, dtype=np.float64
        ).reshape(4, 4)
    except ValueError as error:
        raise ValueError(
            f"{camera.source_name} extrinsic must contain a flattened 4x4 transform"
        ) from error
    convention = np.eye(4, dtype=np.float64)
    convention[:3, :3] = WAYMO_CAMERA_TO_OPENCV
    transform = validate_rigid_transform(transform @ convention)
    return _Calibration(
        camera.source_name,
        pseudo_name,
        intrinsic,
        transform,
        _positive_integer(camera.width_px, f"{camera.source_name} width"),
        _positive_integer(camera.height_px, f"{camera.source_name} height"),
    )


def _camera_map(frame: E2EFrame, record_index: int) -> dict[str, E2ECameraFrame]:
    cameras: dict[str, E2ECameraFrame] = {}
    for camera in frame.cameras:
        if camera.source_name not in _PSEUDO_BY_SOURCE:
            raise ValueError(
                f"record {record_index} has unsupported Waymo E2E camera "
                f"{camera.source_name!r}"
            )
        if camera.source_name in cameras:
            raise ValueError(
                f"record {record_index} has duplicate camera {camera.source_name!r}"
            )
        cameras[camera.source_name] = camera
    required = set(_PSEUDO_BY_SOURCE)
    if set(cameras) != required:
        missing = sorted(required - set(cameras))
        raise ValueError(
            f"record {record_index} missing required Waymo E2E cameras: {missing}"
        )
    return cameras


def _validate_jpeg(camera: E2ECameraFrame, calibration: _Calibration) -> None:
    if not isinstance(camera.image_jpeg, bytes) or not camera.image_jpeg:
        raise ValueError(f"{camera.source_name} image must contain JPEG bytes")
    try:
        from io import BytesIO

        with Image.open(BytesIO(camera.image_jpeg)) as image:
            dimensions = image.size
            image.verify()
    except Exception as error:
        raise ValueError(f"invalid JPEG for {camera.source_name}") from error
    if dimensions != (calibration.width_px, calibration.height_px):
        raise ValueError(f"{camera.source_name} image dimensions mismatch calibration")


def _undistorted_jpeg(camera: E2ECameraFrame, calibration: _Calibration) -> bytes:
    _validate_jpeg(camera, calibration)
    distortion = np.array(
        [
            calibration.intrinsic[4],
            calibration.intrinsic[5],
            calibration.intrinsic[7],
            calibration.intrinsic[8],
            calibration.intrinsic[6],
        ],
        dtype=np.float64,
    )
    if np.allclose(distortion, 0.0):
        return camera.image_jpeg
    import cv2

    encoded = np.frombuffer(camera.image_jpeg, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"OpenCV could not decode JPEG for {camera.source_name}")
    fx, fy, cx, cy = calibration.intrinsic[:4]
    intrinsic = np.array(
        [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float64
    )
    corrected = cv2.undistort(image, intrinsic, distortion)
    ok, output = cv2.imencode(".jpg", corrected, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise ValueError(f"OpenCV could not encode JPEG for {camera.source_name}")
    return bytes(output)


def _pose_row(calibration: _Calibration) -> dict[str, object]:
    transform = calibration.transform_ego_opencv_camera
    qw, qx, qy, qz = matrix_to_quaternion_wxyz(transform[:3, :3])
    return {
        "sensor_name": calibration.pseudo_name,
        "qw": qw,
        "qx": qx,
        "qy": qy,
        "qz": qz,
        "tx_m": float(transform[0, 3]),
        "ty_m": float(transform[1, 3]),
        "tz_m": float(transform[2, 3]),
    }


def _validate_placeholder_camera_pose(camera: E2ECameraFrame) -> None:
    try:
        transform = np.asarray(
            camera.transform_world_ego, dtype=np.float64
        ).reshape(4, 4)
    except ValueError as error:
        raise ValueError("CameraImage.pose must contain a flattened 4x4 transform") from error
    transform = validate_rigid_transform(transform)
    if not np.allclose(transform, np.eye(4), rtol=0.0, atol=1e-12):
        raise ValueError(
            f"{camera.source_name} CameraImage.pose is non-placeholder; "
            "its semantics must be verified before use"
        )


def _calibration_hash(calibration_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(calibration_dir.glob("*.feather"), key=lambda value: value.name):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _same_calibration(first: _Calibration, current: _Calibration) -> bool:
    return (
        first.source_name == current.source_name
        and first.pseudo_name == current.pseudo_name
        and first.width_px == current.width_px
        and first.height_px == current.height_px
        and np.allclose(first.intrinsic, current.intrinsic, rtol=0.0, atol=1e-9)
        and np.allclose(
            first.transform_ego_opencv_camera,
            current.transform_ego_opencv_camera,
            rtol=0.0,
            atol=1e-9,
        )
    )


def _record_output_id(prefix: str, record_index: int, context_name: str) -> str:
    if not prefix or prefix in (".", "..") or Path(prefix).name != prefix:
        raise ValueError("output_log_prefix must be one nonempty path component")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", context_name).strip("._-")
    if not slug:
        slug = "context"
    return f"{prefix}_r{record_index:06d}_{slug[:64]}"


def _stage_independent_record(
    *,
    frame: E2EFrame,
    record_index: int,
    output_root: Path,
    output_log_prefix: str,
    source_path: Path,
    source_sha256: str,
    source_size: int,
    converter_git_commit: str,
    created_at: str,
) -> tuple[Path, Path, ConversionManifest]:
    """Stage one source record as one honest pose-aware Mode-B log."""

    if not frame.source_scene_id:
        raise ValueError(f"record {record_index} has an empty source scene id")
    output_log_id = _record_output_id(
        output_log_prefix, record_index, frame.source_scene_id
    )
    final_output = output_root / output_log_id
    if os.path.lexists(final_output):
        raise FileExistsError(f"output log already exists: {final_output}")
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_log_id}.staging-", dir=output_root)
    )
    surrogate_timestamp_ns = 1
    cameras = tuple(pseudo for _, pseudo in WAYMO_E2E_CAMERA_MAP)
    by_source = _camera_map(frame, record_index)
    calibrations: dict[str, _Calibration] = {}
    camera_timestamps = {camera: surrogate_timestamp_ns for camera in cameras}
    expected_image_hashes: dict[str, str] = {}

    try:
        if frame.anchor_timestamp_ns != 0:
            raise ValueError(
                f"record {record_index} has an unexpected physical anchor timestamp"
            )
        for source_name, pseudo_name in WAYMO_E2E_CAMERA_MAP:
            camera = by_source[source_name]
            if camera.timestamp_ns != 0:
                raise ValueError(
                    f"record {record_index} {source_name} has an unexpected "
                    "physical timestamp"
                )
            _validate_placeholder_camera_pose(camera)
            calibration = _calibration(camera)
            calibrations[source_name] = calibration
            output_payload = _undistorted_jpeg(camera, calibration)
            destination = (
                staging
                / "sensors"
                / "cameras"
                / pseudo_name
                / f"{surrogate_timestamp_ns}.jpg"
            )
            _write_bytes(destination, output_payload)
            relative = destination.relative_to(staging).as_posix()
            expected_image_hashes[relative] = hashlib.sha256(output_payload).hexdigest()

        calibration_dir = staging / "calibration"
        intrinsics_rows = []
        extrinsics_rows = []
        for source_name, _ in WAYMO_E2E_CAMERA_MAP:
            calibration = calibrations[source_name]
            fx, fy, cx, cy = calibration.intrinsic[:4]
            intrinsics_rows.append(
                {
                    "sensor_name": calibration.pseudo_name,
                    "fx_px": fx,
                    "fy_px": fy,
                    "cx_px": cx,
                    "cy_px": cy,
                    "width_px": calibration.width_px,
                    "height_px": calibration.height_px,
                    "k1": 0.0,
                    "k2": 0.0,
                    "k3": 0.0,
                    "p1": 0.0,
                    "p2": 0.0,
                }
            )
            extrinsics_rows.append(_pose_row(calibration))
        write_feather(pd.DataFrame(intrinsics_rows), calibration_dir / "intrinsics.feather")
        write_feather(
            pd.DataFrame(extrinsics_rows),
            calibration_dir / "egovehicle_SE3_sensor.feather",
        )
        frame_record = FrameRecord(
            index=0,
            anchor_timestamp_ns=surrogate_timestamp_ns,
            camera_timestamps_ns=camera_timestamps,
            lidar_timestamp_ns=None,
        )
        manifest = ConversionManifest(
            schema_version="1.0",
            dataset="waymo_e2e",
            source_scene_id=frame.source_scene_id,
            output_log_id=output_log_id,
            mode="B",
            cameras=cameras,
            anchor_camera=cameras[0],
            source_frame_count=1,
            output_frame_count=1,
            # The generic v1 manifest requires positive rates.  These values
            # describe only a single-record container; provenance below makes
            # explicit that no physical cadence or timestamp exists.
            source_frame_rate_hz=1.0,
            output_frame_rate_hz=1.0,
            camera_records=tuple(
                CameraRecord(pseudo, source, 1, 0)
                for source, pseudo in WAYMO_E2E_CAMERA_MAP
            ),
            frames=(frame_record,),
            calibration_sha256=_calibration_hash(calibration_dir),
            source_artifacts=(
                SourceArtifact(source_path.name, source_sha256, source_size),
            ),
            has_lidar=False,
            has_ego_pose=False,
            has_annotations=False,
            real_mask_pattern=None,
            faithfill_mask_pattern=None,
            honest_black_mask_pattern=None,
            supported_azimuth_deg=((0.0, 360.0),),
            honest_black_azimuth_deg=(),
            coordinate_convention_transform=(
                (0.0, 0.0, 1.0, 0.0),
                (-1.0, 0.0, 0.0, 0.0),
                (0.0, -1.0, 0.0, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            ),
            converter_git_commit=converter_git_commit,
            created_at=created_at,
        )
        manifest.write_json(staging / "conversion_manifest.json")
        (staging / "waymo_e2e_provenance.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "source_shard": source_path.name,
                    "source_record_index": record_index,
                    "source_context_name": frame.source_scene_id,
                    "physical_timestamps_available": False,
                    "record_is_independent_scene": True,
                    "surrogate_timestamp_ns": surrogate_timestamp_ns,
                    "manifest_rate_hz_is_single_record_placeholder": True,
                    "camera_pose_available": False,
                    "camera_pose_field_status": "placeholder_identity",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        written = {
            path.relative_to(staging).as_posix(): path
            for path in (staging / "sensors" / "cameras").glob("*/*.jpg")
        }
        if set(written) != set(expected_image_hashes):
            raise ValueError("staged camera image set does not match decoded record")
        for relative, path in written.items():
            if sha256_file(path) != expected_image_hashes[relative]:
                raise ValueError(f"staged camera JPEG hash mismatch: {relative}")
        reloaded = ConversionManifest.read_json(staging / "conversion_manifest.json")
        if reloaded != manifest:
            raise ValueError("written conversion manifest does not round-trip")
        AV2RingLoader(staging, cameras=manifest.cameras).load_synced_frame(
            surrogate_timestamp_ns
        )
        return staging, final_output, manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def convert_waymo_e2e_records(
    tfrecord_path: Path,
    output_root: Path,
    output_log_prefix: str,
    *,
    record_indices: tuple[int, ...],
    record_decoder: RecordDecoder | None = None,
    converter_git_commit: str,
    created_at: str,
) -> tuple[tuple[Path, ConversionManifest], ...]:
    """Convert selected independent E2E records without inventing a timeline."""

    tfrecord_path = Path(tfrecord_path)
    output_root = Path(output_root)
    if not tfrecord_path.is_file():
        raise ValueError(f"tfrecord_path must be a file: {tfrecord_path}")
    if (
        not record_indices
        or any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in record_indices)
        or tuple(sorted(set(record_indices))) != record_indices
    ):
        raise ValueError("record_indices must be nonempty, unique, and strictly increasing")
    _record_output_id(output_log_prefix, 0, "validation")
    decoder = record_decoder or _decode_waymo_record
    source_size = tfrecord_path.stat().st_size
    source_sha256 = sha256_file(tfrecord_path)
    if tfrecord_path.stat().st_size != source_size:
        raise ValueError("source TFRecord changed while snapshotting")
    output_root.mkdir(parents=True, exist_ok=True)
    wanted = set(record_indices)
    staged: list[tuple[Path, Path, ConversionManifest]] = []
    found_indices: list[int] = []
    try:
        for record_index, payload in enumerate(_iter_tfrecord_payloads(tfrecord_path)):
            if record_index not in wanted:
                if record_index > record_indices[-1]:
                    break
                continue
            frame = decoder(payload, record_index)
            if not isinstance(frame, E2EFrame):
                raise TypeError("record_decoder must return E2EFrame")
            staged.append(
                _stage_independent_record(
                    frame=frame,
                    record_index=record_index,
                    output_root=output_root,
                    output_log_prefix=output_log_prefix,
                    source_path=tfrecord_path,
                    source_sha256=source_sha256,
                    source_size=source_size,
                    converter_git_commit=converter_git_commit,
                    created_at=created_at,
                )
            )
            found_indices.append(record_index)
            if len(staged) == len(record_indices):
                break
        found = tuple(found_indices)
        if found != record_indices:
            raise ValueError(
                f"TFRecord is missing selected record indices: "
                f"{sorted(wanted - set(found))}"
            )
        if tfrecord_path.stat().st_size != source_size or sha256_file(tfrecord_path) != source_sha256:
            raise ValueError("source TFRecord changed after conversion")
        results: list[tuple[Path, ConversionManifest]] = []
        for staging, final_output, manifest in staged:
            if os.path.lexists(final_output):
                raise FileExistsError(f"output log already exists: {final_output}")
            staging.rename(final_output)
            results.append((final_output, manifest))
        return tuple(results)
    finally:
        for staging, _, _ in staged:
            if staging.exists():
                shutil.rmtree(staging)


def convert_waymo_e2e_tfrecord(
    tfrecord_path: Path,
    output_root: Path,
    output_log_id: str,
    *,
    record_decoder: RecordDecoder | None = None,
    converter_git_commit: str,
    created_at: str,
) -> tuple[Path, ConversionManifest]:
    """Convert every record without inventing LiDAR, pose, boxes, or pixels."""

    tfrecord_path = Path(tfrecord_path)
    output_root = Path(output_root)
    if not tfrecord_path.is_file():
        raise ValueError(f"tfrecord_path must be a file: {tfrecord_path}")
    if (
        not output_log_id
        or output_log_id in (".", "..")
        or Path(output_log_id).name != output_log_id
    ):
        raise ValueError("output_log_id must be one nonempty path component")
    final_output = output_root / output_log_id
    if os.path.lexists(final_output):
        raise FileExistsError(f"output log already exists: {final_output}")
    decoder = record_decoder or _decode_waymo_record

    source_size_before = tfrecord_path.stat().st_size
    source_sha256 = sha256_file(tfrecord_path)
    if tfrecord_path.stat().st_size != source_size_before:
        raise ValueError("source TFRecord changed while snapshotting")

    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_log_id}.staging-", dir=output_root))
    published = False
    try:
        calibrations: dict[str, _Calibration] = {}
        frame_records: list[FrameRecord] = []
        seen_image_paths: set[str] = set()
        expected_image_hashes: dict[str, str] = {}

        for record_index, payload in enumerate(_iter_tfrecord_payloads(tfrecord_path)):
            frame = decoder(payload, record_index)
            if not isinstance(frame, E2EFrame):
                raise TypeError("record_decoder must return E2EFrame")
            if not frame.source_scene_id:
                raise ValueError(f"record {record_index} has an empty source scene id")
            anchor_timestamp = _positive_integer(
                frame.anchor_timestamp_ns, f"record {record_index} anchor timestamp"
            )
            if frame_records and anchor_timestamp <= frame_records[-1].anchor_timestamp_ns:
                raise ValueError("Waymo E2E anchor timestamps must be strictly increasing")
            by_source = _camera_map(frame, record_index)
            camera_timestamps: dict[str, int] = {}
            for source_name, pseudo_name in WAYMO_E2E_CAMERA_MAP:
                camera = by_source[source_name]
                timestamp = _positive_integer(
                    camera.timestamp_ns,
                    f"record {record_index} {source_name} timestamp",
                )
                if source_name == "FRONT" and timestamp != anchor_timestamp:
                    raise ValueError("FRONT timestamp must equal E2E anchor timestamp")
                current_calibration = _calibration(camera)
                previous_calibration = calibrations.get(source_name)
                if previous_calibration is None:
                    calibrations[source_name] = current_calibration
                elif not _same_calibration(previous_calibration, current_calibration):
                    raise ValueError(f"{source_name} calibration changes within TFRecord")
                output_payload = _undistorted_jpeg(camera, current_calibration)
                destination = (
                    staging / "sensors" / "cameras" / pseudo_name / f"{timestamp}.jpg"
                )
                relative = destination.relative_to(staging).as_posix()
                if relative in seen_image_paths:
                    raise ValueError(f"duplicate camera timestamp path: {relative}")
                seen_image_paths.add(relative)
                _write_bytes(destination, output_payload)
                expected_image_hashes[relative] = hashlib.sha256(output_payload).hexdigest()
                camera_timestamps[pseudo_name] = timestamp
            frame_records.append(
                FrameRecord(
                    index=record_index,
                    anchor_timestamp_ns=anchor_timestamp,
                    camera_timestamps_ns=camera_timestamps,
                    lidar_timestamp_ns=None,
                )
            )

        if not frame_records:
            raise ValueError("Waymo E2E TFRecord contains no records")

        calibration_dir = staging / "calibration"
        intrinsics_rows = []
        extrinsics_rows = []
        for source_name, _ in WAYMO_E2E_CAMERA_MAP:
            calibration = calibrations[source_name]
            fx, fy, cx, cy = calibration.intrinsic[:4]
            intrinsics_rows.append(
                {
                    "sensor_name": calibration.pseudo_name,
                    "fx_px": fx,
                    "fy_px": fy,
                    "cx_px": cx,
                    "cy_px": cy,
                    "width_px": calibration.width_px,
                    "height_px": calibration.height_px,
                    "k1": 0.0,
                    "k2": 0.0,
                    "k3": 0.0,
                    "p1": 0.0,
                    "p2": 0.0,
                }
            )
            extrinsics_rows.append(_pose_row(calibration))
        write_feather(pd.DataFrame(intrinsics_rows), calibration_dir / "intrinsics.feather")
        write_feather(
            pd.DataFrame(extrinsics_rows),
            calibration_dir / "egovehicle_SE3_sensor.feather",
        )

        anchors = np.asarray(
            [frame.anchor_timestamp_ns for frame in frame_records], dtype=np.int64
        )
        frame_rate = (
            1.0
            if len(anchors) == 1
            else 1_000_000_000.0 / float(np.median(np.diff(anchors)))
        )
        cameras = tuple(pseudo for _, pseudo in WAYMO_E2E_CAMERA_MAP)
        manifest = ConversionManifest(
            schema_version="1.0",
            dataset="waymo_e2e",
            source_scene_id=tfrecord_path.name,
            output_log_id=output_log_id,
            mode="B",
            cameras=cameras,
            anchor_camera=cameras[0],
            source_frame_count=len(frame_records),
            output_frame_count=len(frame_records),
            source_frame_rate_hz=frame_rate,
            output_frame_rate_hz=frame_rate,
            camera_records=tuple(
                CameraRecord(
                    pseudo_name,
                    source_name,
                    len(frame_records),
                    max(
                        abs(
                            frame.camera_timestamps_ns[pseudo_name]
                            - frame.anchor_timestamp_ns
                        )
                        for frame in frame_records
                    ),
                )
                for source_name, pseudo_name in WAYMO_E2E_CAMERA_MAP
            ),
            frames=tuple(frame_records),
            calibration_sha256=_calibration_hash(calibration_dir),
            source_artifacts=(
                SourceArtifact(tfrecord_path.name, source_sha256, source_size_before),
            ),
            has_lidar=False,
            has_ego_pose=False,
            has_annotations=False,
            real_mask_pattern=None,
            faithfill_mask_pattern=None,
            honest_black_mask_pattern=None,
            supported_azimuth_deg=((0.0, 360.0),),
            honest_black_azimuth_deg=(),
            coordinate_convention_transform=(
                (0.0, 0.0, 1.0, 0.0),
                (-1.0, 0.0, 0.0, 0.0),
                (0.0, -1.0, 0.0, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            ),
            converter_git_commit=converter_git_commit,
            created_at=created_at,
        )
        manifest.write_json(staging / "conversion_manifest.json")

        if tfrecord_path.stat().st_size != source_size_before:
            raise ValueError("source TFRecord changed after conversion")
        if sha256_file(tfrecord_path) != source_sha256:
            raise ValueError("source TFRecord changed after conversion")
        written = {
            path.relative_to(staging).as_posix(): path
            for path in (staging / "sensors" / "cameras").glob("*/*.jpg")
        }
        if set(written) != set(expected_image_hashes):
            raise ValueError("staged camera image set does not match decoded records")
        for relative, path in written.items():
            if sha256_file(path) != expected_image_hashes[relative]:
                raise ValueError(f"staged camera JPEG hash mismatch: {relative}")
        reloaded = ConversionManifest.read_json(staging / "conversion_manifest.json")
        if reloaded != manifest:
            raise ValueError("written conversion manifest does not round-trip")
        AV2RingLoader(staging, cameras=manifest.cameras).load_synced_frame(
            manifest.frames[0].anchor_timestamp_ns
        )
        if os.path.lexists(final_output):
            raise FileExistsError(f"output log already exists: {final_output}")
        staging.rename(final_output)
        published = True
        return final_output, manifest
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)
