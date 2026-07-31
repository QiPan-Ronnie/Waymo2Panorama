from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as ipc
from PIL import Image
from scipy.spatial.transform import Rotation, Slerp

from waymo2panorama.data_io.av2_loader import AV2RingLoader

from .contract import CameraRecord, ConversionManifest, FrameRecord, SourceArtifact
from .geometry import (
    make_transform,
    matrix_to_quaternion_wxyz,
    quaternion_wxyz_to_matrix,
    relative_transform,
    rotation_z_deg,
)
from .io import materialize_file, sha256_file, write_empty_annotations, write_feather


PANDASET_CAMERA_MAP: tuple[tuple[str, str], ...] = (
    ("front_camera", "ring_front_center"),
    ("front_left_camera", "ring_front_left"),
    ("left_camera", "ring_side_left"),
    ("back_camera", "ring_rear"),
    ("right_camera", "ring_side_right"),
    ("front_right_camera", "ring_front_right"),
)

_ROTATION_RESIDUAL_LIMIT_DEG = 0.5
_TRANSLATION_RESIDUAL_LIMIT_M = 0.05


@dataclass(frozen=True)
class _CameraInput:
    source_name: str
    pseudo_name: str
    directory: Path
    intrinsics: dict[str, float]
    timestamps_ns: tuple[int, ...]
    poses: tuple[np.ndarray, ...]
    images: tuple[Path, ...]
    width_px: int
    height_px: int


@dataclass(frozen=True)
class _LidarInput:
    directory: Path
    timestamps_ns: tuple[int, ...]
    poses: tuple[np.ndarray, ...]
    sweeps: tuple[Path, ...]


@dataclass(frozen=True)
class _SourceSnapshot:
    path: Path
    relative_path: str
    sha256: str
    size_bytes: int


def _snapshot_file(source_scene: Path, path: Path) -> _SourceSnapshot:
    try:
        size_before = path.stat().st_size
        digest = sha256_file(path)
        size_after = path.stat().st_size
    except (FileNotFoundError, OSError) as error:
        raise ValueError(f"source snapshot failed for {path}") from error
    if size_before != size_after:
        raise ValueError(f"source changed while snapshotting: {path}")
    return _SourceSnapshot(
        path=path,
        relative_path=path.relative_to(source_scene).as_posix(),
        sha256=digest,
        size_bytes=size_after,
    )


def _verify_snapshot(snapshot: _SourceSnapshot) -> None:
    try:
        size_before = snapshot.path.stat().st_size
        digest = sha256_file(snapshot.path)
        size_after = snapshot.path.stat().st_size
    except (FileNotFoundError, OSError) as error:
        raise ValueError(f"source changed after preflight: {snapshot.relative_path}") from error
    if (
        size_before != size_after
        or size_after != snapshot.size_bytes
        or digest != snapshot.sha256
    ):
        raise ValueError(f"source changed after preflight snapshot: {snapshot.relative_path}")


def _read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as source_file:
            return json.load(source_file)
    except FileNotFoundError:
        raise ValueError(f"missing required metadata file: {path}") from None


def _parse_timestamps(path: Path, stream_name: str) -> tuple[int, ...]:
    values = _read_json(path)
    if not isinstance(values, list) or not values:
        raise ValueError(f"{stream_name} timestamps must be a nonempty list")
    seconds: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{stream_name} timestamps must be numeric")
        checked = float(value)
        if not math.isfinite(checked) or checked <= 0.0:
            raise ValueError(f"{stream_name} timestamps must be finite and positive")
        seconds.append(checked)
    if any(right <= left for left, right in zip(seconds, seconds[1:])):
        raise ValueError(f"{stream_name} timestamps must be strictly increasing")
    timestamps_ns = tuple(int(round(value * 1_000_000_000.0)) for value in seconds)
    if any(right <= left for left, right in zip(timestamps_ns, timestamps_ns[1:])):
        raise ValueError(
            f"{stream_name} timestamps must remain strictly increasing at nanosecond precision"
        )
    return timestamps_ns


def _parse_pose(value: object, stream_name: str, index: int) -> np.ndarray:
    try:
        assert isinstance(value, dict)
        heading = value["heading"]
        position = value["position"]
        assert isinstance(heading, dict) and isinstance(position, dict)
        quaternion = [heading[key] for key in ("w", "x", "y", "z")]
        translation = [position[key] for key in ("x", "y", "z")]
        if any(isinstance(item, bool) for item in (*quaternion, *translation)):
            raise ValueError
        quaternion_values = np.asarray(quaternion, dtype=np.float64)
        translation_values = np.asarray(translation, dtype=np.float64)
    except (AssertionError, KeyError, TypeError, ValueError):
        raise ValueError(
            f"{stream_name} pose {index} must contain numeric heading w/x/y/z "
            "and position x/y/z"
        ) from None
    if not np.isfinite(quaternion_values).all() or not np.isfinite(translation_values).all():
        raise ValueError(f"{stream_name} pose {index} must contain only finite values")
    return make_transform(
        quaternion_wxyz_to_matrix(quaternion_values),
        translation_values,
    )


def _parse_poses(path: Path, stream_name: str) -> tuple[np.ndarray, ...]:
    values = _read_json(path)
    if not isinstance(values, list):
        raise ValueError(f"{stream_name} poses must be a list")
    return tuple(_parse_pose(value, stream_name, index) for index, value in enumerate(values))


def _numbered_files(directory: Path, pattern: str, stream_name: str) -> tuple[Path, ...]:
    paths = tuple(sorted(directory.glob(pattern), key=lambda path: path.name))
    if not paths:
        raise ValueError(f"{stream_name} must contain at least one {pattern} file")
    return paths


def _camera_input(source_scene: Path, source_name: str, pseudo_name: str) -> _CameraInput:
    directory = source_scene / "camera" / source_name
    intrinsics_value = _read_json(directory / "intrinsics.json")
    if not isinstance(intrinsics_value, dict):
        raise ValueError(f"{source_name} intrinsics must be an object")
    intrinsics: dict[str, float] = {}
    for key in ("fx", "fy", "cx", "cy"):
        value = intrinsics_value.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{source_name} intrinsics {key} must be finite")
        checked = float(value)
        if not math.isfinite(checked):
            raise ValueError(f"{source_name} intrinsics {key} must be finite")
        intrinsics[key] = checked
    if intrinsics["fx"] <= 0.0 or intrinsics["fy"] <= 0.0:
        raise ValueError(f"{source_name} intrinsics fx/fy must be positive")

    timestamps_ns = _parse_timestamps(directory / "timestamps.json", source_name)
    poses = _parse_poses(directory / "poses.json", source_name)
    images = _numbered_files(directory, "*.jpg", source_name)
    if len(timestamps_ns) != len(poses) or len(timestamps_ns) != len(images):
        raise ValueError(
            f"{source_name} timestamps/images/poses count mismatch: "
            f"{len(timestamps_ns)}/{len(images)}/{len(poses)}"
        )
    dimensions: list[tuple[int, int]] = []
    for image_path in images:
        try:
            with Image.open(image_path) as image:
                dimensions.append(image.size)
                image.verify()
        except Exception as error:
            raise ValueError(f"invalid JPEG for {source_name}: {image_path}") from error
    if any(value != dimensions[0] for value in dimensions[1:]):
        raise ValueError(f"{source_name} image dimensions must be consistent across stream")
    return _CameraInput(
        source_name=source_name,
        pseudo_name=pseudo_name,
        directory=directory,
        intrinsics=intrinsics,
        timestamps_ns=timestamps_ns,
        poses=poses,
        images=images,
        width_px=dimensions[0][0],
        height_px=dimensions[0][1],
    )


def _lidar_input(source_scene: Path) -> _LidarInput:
    directory = source_scene / "lidar"
    timestamps_ns = _parse_timestamps(directory / "timestamps.json", "lidar")
    poses = _parse_poses(directory / "poses.json", "lidar")
    sweeps = _numbered_files(directory, "*.pkl.gz", "lidar")
    if len(timestamps_ns) != len(poses) or len(timestamps_ns) != len(sweeps):
        raise ValueError(
            "lidar timestamps/poses/pkl count mismatch: "
            f"{len(timestamps_ns)}/{len(poses)}/{len(sweeps)}"
        )
    return _LidarInput(directory, timestamps_ns, poses, sweeps)


def _interpolate_poses(
    timestamps_ns: tuple[int, ...],
    poses: tuple[np.ndarray, ...],
    query_timestamps_ns: tuple[int, ...],
) -> dict[int, np.ndarray]:
    if len(timestamps_ns) < 2:
        if any(query != timestamps_ns[0] for query in query_timestamps_ns):
            raise ValueError("camera timestamp is outside lidar pose time range")
        return {query: np.array(poses[0], copy=True) for query in query_timestamps_ns}
    if any(query < timestamps_ns[0] or query > timestamps_ns[-1] for query in query_timestamps_ns):
        raise ValueError("camera timestamp is outside lidar pose time range; extrapolation is forbidden")
    origin = timestamps_ns[0]
    key_times = (np.asarray(timestamps_ns, dtype=np.float64) - origin) / 1_000_000_000.0
    query_times = (np.asarray(query_timestamps_ns, dtype=np.float64) - origin) / 1_000_000_000.0
    rotations = Rotation.from_matrix(np.stack([pose[:3, :3] for pose in poses]))
    interpolated_rotations = Slerp(key_times, rotations)(query_times).as_matrix()
    translations = np.stack([pose[:3, 3] for pose in poses])
    interpolated_translations = np.column_stack(
        [np.interp(query_times, key_times, translations[:, axis]) for axis in range(3)]
    )
    return {
        timestamp: make_transform(rotation, translation)
        for timestamp, rotation, translation in zip(
            query_timestamps_ns,
            interpolated_rotations,
            interpolated_translations,
        )
    }


def _nearest_index(values: tuple[int, ...], query: int) -> int:
    right = bisect.bisect_left(values, query)
    candidates = []
    if right < len(values):
        candidates.append(right)
    if right > 0:
        candidates.append(right - 1)
    return min(candidates, key=lambda index: (abs(values[index] - query), values[index]))


def _select_indices(values: tuple[int, ...], anchors: tuple[int, ...], stream_name: str) -> tuple[int, ...]:
    indices = tuple(_nearest_index(values, anchor) for anchor in anchors)
    selected = tuple(values[index] for index in indices)
    if any(right <= left for left, right in zip(selected, selected[1:])):
        raise ValueError(
            f"{stream_name} nearest selections must strictly increase without duplicate reuse"
        )
    return indices


def _read_lidar_world(path: Path) -> tuple[np.ndarray, np.ndarray]:
    frame = pd.read_pickle(path, compression="gzip")
    missing = {"x", "y", "z", "i"} - set(frame.columns)
    if missing:
        raise ValueError(f"lidar sweep {path} missing columns: {sorted(missing)}")
    xyz = frame.loc[:, ["x", "y", "z"]].to_numpy(dtype=np.float64)
    intensity = frame.loc[:, "i"].to_numpy(dtype=np.float64)
    if not np.isfinite(xyz).all() or not np.isfinite(intensity).all():
        raise ValueError(f"lidar sweep {path} contains non-finite values")
    return xyz, intensity


def _static_calibration(
    camera: _CameraInput,
    interpolated_ego: dict[int, np.ndarray],
) -> np.ndarray:
    candidates = tuple(
        relative_transform(interpolated_ego[timestamp], world_camera)
        for timestamp, world_camera in zip(camera.timestamps_ns, camera.poses)
    )
    rotations = Rotation.from_matrix(np.stack([value[:3, :3] for value in candidates]))
    mean_rotation = rotations.mean()
    translation = np.median(
        np.stack([value[:3, 3] for value in candidates]),
        axis=0,
    )
    rotation_residual_deg = np.degrees((mean_rotation.inv() * rotations).magnitude())
    translation_residual_m = np.linalg.norm(
        np.stack([value[:3, 3] for value in candidates]) - translation,
        axis=1,
    )
    max_rotation = float(np.max(rotation_residual_deg))
    max_translation = float(np.max(translation_residual_m))
    if (
        max_rotation > _ROTATION_RESIDUAL_LIMIT_DEG
        or max_translation > _TRANSLATION_RESIDUAL_LIMIT_M
    ):
        raise ValueError(
            f"{camera.source_name} calibration drift: max rotation residual "
            f"{max_rotation:.6f} deg, max translation residual {max_translation:.6f} m"
        )
    return make_transform(mean_rotation.as_matrix(), translation)


def _artifact(snapshot: _SourceSnapshot) -> SourceArtifact:
    return SourceArtifact(
        path=snapshot.relative_path,
        sha256=snapshot.sha256,
        size_bytes=snapshot.size_bytes,
    )


def _calibration_hash(calibration_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(calibration_dir.glob("*.feather"), key=lambda value: value.name):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _table_pose_row(sensor_name: str, transform: np.ndarray) -> dict[str, object]:
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


def _validate_written_log(
    staging: Path,
    manifest: ConversionManifest,
    expected_camera_hashes: dict[str, str],
) -> None:
    manifest.validate()
    ConversionManifest.read_json(staging / "conversion_manifest.json")
    paths = [
        staging / "calibration" / "intrinsics.feather",
        staging / "calibration" / "egovehicle_SE3_sensor.feather",
        staging / "city_SE3_egovehicle.feather",
        staging / "annotations.feather",
    ]
    for frame in manifest.frames:
        assert frame.lidar_timestamp_ns is not None
        paths.append(staging / "sensors" / "lidar" / f"{frame.lidar_timestamp_ns}.feather")
    for path in paths:
        with pa.memory_map(str(path), "r") as source:
            ipc.open_file(source).read_all()
    staged_images = tuple((staging / "sensors" / "cameras").glob("**/*.jpg"))
    staged_by_relative = {
        path.relative_to(staging).as_posix(): path for path in staged_images
    }
    if set(staged_by_relative) != set(expected_camera_hashes):
        raise ValueError("staged camera image set does not match source snapshots")
    for relative_path, path in staged_by_relative.items():
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            raise ValueError(f"invalid staged camera JPEG: {relative_path}") from error
        if sha256_file(path) != expected_camera_hashes[relative_path]:
            raise ValueError(f"staged camera image changed from source snapshot: {relative_path}")
    loader = AV2RingLoader(staging, cameras=manifest.cameras)
    loader.load_synced_frame(manifest.frames[0].anchor_timestamp_ns)


def convert_pandaset_scene(
    source_scene: Path,
    output_root: Path,
    output_log_id: str,
    *,
    mode: str = "A",
    real_mask_pattern: str | None = "render/**/*_real_mask.png",
    ego_origin: str = "sensor_rig",
    ground_quantile: float = 0.05,
    ground_radius_m: float = 10.0,
    converter_git_commit: str,
    created_at: str,
) -> tuple[Path, ConversionManifest]:
    source_scene = Path(source_scene)
    output_root = Path(output_root)
    if not source_scene.is_dir():
        raise ValueError(f"source_scene must be a directory: {source_scene}")
    if not output_log_id or Path(output_log_id).name != output_log_id:
        raise ValueError("output_log_id must be one nonempty path component")
    final_output = output_root / output_log_id
    if os.path.lexists(final_output):
        raise FileExistsError(f"output log already exists: {final_output}")
    if mode not in ("A", "B"):
        raise ValueError("mode must be 'A' or 'B'")
    if mode == "A" and (not isinstance(real_mask_pattern, str) or not real_mask_pattern.strip()):
        raise ValueError("A mode requires a nonempty real_mask_pattern")
    if ego_origin not in ("sensor_rig", "ground"):
        raise ValueError("ego_origin must be 'sensor_rig' or 'ground'")
    if (
        isinstance(ground_radius_m, bool)
        or not isinstance(ground_radius_m, (int, float))
        or not math.isfinite(float(ground_radius_m))
        or float(ground_radius_m) <= 0.0
    ):
        raise ValueError("ground_radius_m must be finite and positive")
    if (
        isinstance(ground_quantile, bool)
        or not isinstance(ground_quantile, (int, float))
        or not math.isfinite(float(ground_quantile))
        or not 0.0 <= float(ground_quantile) <= 1.0
    ):
        raise ValueError("ground_quantile must be finite and between 0 and 1")

    candidate_paths: list[Path] = []
    for source_name, _ in PANDASET_CAMERA_MAP:
        camera_dir = source_scene / "camera" / source_name
        candidate_paths.extend(
            camera_dir / filename
            for filename in ("intrinsics.json", "poses.json", "timestamps.json")
        )
        candidate_paths.extend(camera_dir.glob("*.jpg"))
    lidar_dir = source_scene / "lidar"
    candidate_paths.extend((lidar_dir / "poses.json", lidar_dir / "timestamps.json"))
    candidate_paths.extend(lidar_dir.glob("*.pkl.gz"))
    snapshots = {
        path: _snapshot_file(source_scene, path)
        for path in candidate_paths
        if path.is_file()
    }

    cameras = tuple(
        _camera_input(source_scene, source_name, pseudo_name)
        for source_name, pseudo_name in PANDASET_CAMERA_MAP
    )
    frame_count = len(cameras[0].timestamps_ns)
    if any(len(camera.timestamps_ns) != frame_count for camera in cameras[1:]):
        raise ValueError("all six cameras must have the same positive frame count")
    lidar = _lidar_input(source_scene)

    anchors = cameras[0].timestamps_ns
    camera_indices = {
        camera.pseudo_name: _select_indices(camera.timestamps_ns, anchors, camera.source_name)
        for camera in cameras
    }
    lidar_indices = _select_indices(lidar.timestamps_ns, anchors, "lidar")
    consumed_lidar_indices = set(lidar_indices)
    if ego_origin == "ground":
        consumed_lidar_indices.add(0)

    convention = make_transform(rotation_z_deg(90.0), [0.0, 0.0, 0.0])
    ego_poses = tuple(pose @ convention for pose in lidar.poses)
    metadata_paths = [
        camera.directory / filename
        for camera in cameras
        for filename in ("intrinsics.json", "poses.json", "timestamps.json")
    ] + [lidar.directory / "poses.json", lidar.directory / "timestamps.json"]
    selected_camera_paths = [
        camera.images[index]
        for camera in cameras
        for index in camera_indices[camera.pseudo_name]
    ]
    consumed_lidar_paths = [
        lidar.sweeps[index] for index in sorted(consumed_lidar_indices)
    ]
    provenance_paths = sorted(
        set((*metadata_paths, *selected_camera_paths, *consumed_lidar_paths)),
        key=lambda path: path.relative_to(source_scene).as_posix(),
    )
    try:
        consumed_snapshots = tuple(snapshots[path] for path in provenance_paths)
    except KeyError as error:
        raise ValueError(f"source file appeared after snapshot preflight: {error.args[0]}") from None
    for snapshot in consumed_snapshots:
        if snapshot.path not in consumed_lidar_paths:
            _verify_snapshot(snapshot)

    derived_artifact: SourceArtifact | None = None
    ground_shift: float | None = None
    for index in sorted(consumed_lidar_indices):
        snapshot = snapshots[lidar.sweeps[index]]
        _verify_snapshot(snapshot)
        xyz_world, intensity = _read_lidar_world(lidar.sweeps[index])
        _verify_snapshot(snapshot)
        if ego_origin == "ground" and index == 0:
            first_local = (
                np.linalg.inv(ego_poses[0])
                @ np.column_stack([xyz_world, np.ones(len(xyz_world))]).T
            ).T[:, :3]
            near = np.hypot(
                first_local[:, 0],
                first_local[:, 1],
            ) <= float(ground_radius_m)
            if not np.any(near):
                raise ValueError("ground origin requires at least one near-field lidar point")
            ground_shift = float(
                np.quantile(first_local[near, 2], float(ground_quantile))
            )
            del first_local
        del xyz_world, intensity

    if ego_origin == "ground":
        assert ground_shift is not None
        ego_poses = tuple(
            pose @ make_transform(np.eye(3), [0.0, 0.0, ground_shift])
            for pose in ego_poses
        )
        source_sweep_path = lidar.sweeps[0].relative_to(source_scene).as_posix()
        descriptor_payload = {
            "algorithm_version": "pandaset_ground_origin_v1",
            "ground_quantile": float(ground_quantile),
            "ground_radius_m": float(ground_radius_m),
            "near_field_rule": "hypot(x,y)<=ground_radius_m",
            "shift_m": ground_shift,
            "source_sweep_path": source_sweep_path,
            "source_sweep_sha256": snapshots[lidar.sweeps[0]].sha256,
        }
        descriptor = "derived:ego_ground_shift=" + json.dumps(
            descriptor_payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload = descriptor.encode("utf-8")
        derived_artifact = SourceArtifact(
            path=descriptor,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )

    all_camera_timestamps = tuple(
        sorted({timestamp for camera in cameras for timestamp in camera.timestamps_ns})
    )
    interpolated_ego = _interpolate_poses(
        lidar.timestamps_ns,
        ego_poses,
        all_camera_timestamps,
    )
    calibrations = {
        camera.pseudo_name: _static_calibration(camera, interpolated_ego)
        for camera in cameras
    }

    frames: list[FrameRecord] = []
    for frame_index, anchor in enumerate(anchors):
        frames.append(
            FrameRecord(
                index=frame_index,
                anchor_timestamp_ns=anchor,
                camera_timestamps_ns={
                    camera.pseudo_name: camera.timestamps_ns[
                        camera_indices[camera.pseudo_name][frame_index]
                    ]
                    for camera in cameras
                },
                lidar_timestamp_ns=lidar.timestamps_ns[lidar_indices[frame_index]],
            )
        )

    source_artifacts = tuple(_artifact(snapshots[path]) for path in provenance_paths)
    if derived_artifact is not None:
        source_artifacts += (derived_artifact,)

    output_root.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(final_output):
        raise FileExistsError(f"output log already exists: {final_output}")
    staging = Path(tempfile.mkdtemp(prefix=f".{output_log_id}.staging-", dir=output_root))
    published = False
    try:
        calibration_dir = staging / "calibration"
        intrinsics_rows = [
            {
                "sensor_name": camera.pseudo_name,
                "fx_px": camera.intrinsics["fx"],
                "fy_px": camera.intrinsics["fy"],
                "cx_px": camera.intrinsics["cx"],
                "cy_px": camera.intrinsics["cy"],
                "width_px": camera.width_px,
                "height_px": camera.height_px,
                "k1": 0.0,
                "k2": 0.0,
                "k3": 0.0,
            }
            for camera in cameras
        ]
        write_feather(pd.DataFrame(intrinsics_rows), calibration_dir / "intrinsics.feather")
        extrinsics_rows = [
            _table_pose_row(camera.pseudo_name, calibrations[camera.pseudo_name])
            for camera in cameras
        ]
        extrinsics_rows.append(_table_pose_row("up_lidar", np.eye(4)))
        write_feather(
            pd.DataFrame(extrinsics_rows),
            calibration_dir / "egovehicle_SE3_sensor.feather",
        )

        expected_camera_hashes: dict[str, str] = {}
        for camera in cameras:
            destination_dir = staging / "sensors" / "cameras" / camera.pseudo_name
            for index in camera_indices[camera.pseudo_name]:
                timestamp = camera.timestamps_ns[index]
                source_image = camera.images[index]
                destination = destination_dir / f"{timestamp}.jpg"
                materialize_file(source_image, destination, prefer_hardlink=False)
                expected_camera_hashes[destination.relative_to(staging).as_posix()] = (
                    snapshots[source_image].sha256
                )
        for index in lidar_indices:
            timestamp = lidar.timestamps_ns[index]
            snapshot = snapshots[lidar.sweeps[index]]
            _verify_snapshot(snapshot)
            xyz_world, intensity = _read_lidar_world(lidar.sweeps[index])
            _verify_snapshot(snapshot)
            xyz_ego = (
                np.linalg.inv(ego_poses[index])
                @ np.column_stack([xyz_world, np.ones(len(xyz_world))]).T
            ).T[:, :3]
            lidar_frame = pd.DataFrame(
                {
                    "x": xyz_ego[:, 0].astype(np.float32),
                    "y": xyz_ego[:, 1].astype(np.float32),
                    "z": xyz_ego[:, 2].astype(np.float32),
                    "intensity": intensity.astype(np.float32),
                }
            )
            write_feather(lidar_frame, staging / "sensors" / "lidar" / f"{timestamp}.feather")
            del xyz_world, intensity, xyz_ego, lidar_frame

        write_empty_annotations(staging / "annotations.feather")
        used_timestamps = tuple(
            sorted(
                {
                    *(timestamp for frame in frames for timestamp in frame.camera_timestamps_ns.values()),
                    *(frame.lidar_timestamp_ns for frame in frames if frame.lidar_timestamp_ns is not None),
                }
            )
        )
        union_interpolated = _interpolate_poses(
            lidar.timestamps_ns,
            ego_poses,
            tuple(timestamp for timestamp in used_timestamps if timestamp not in lidar.timestamps_ns),
        )
        city_rows = []
        lidar_pose_by_timestamp = dict(zip(lidar.timestamps_ns, ego_poses))
        for timestamp in used_timestamps:
            pose = lidar_pose_by_timestamp.get(timestamp, union_interpolated.get(timestamp))
            assert pose is not None
            qw, qx, qy, qz = matrix_to_quaternion_wxyz(pose[:3, :3])
            city_rows.append(
                {
                    "timestamp_ns": timestamp,
                    "qw": qw,
                    "qx": qx,
                    "qy": qy,
                    "qz": qz,
                    "tx_m": float(pose[0, 3]),
                    "ty_m": float(pose[1, 3]),
                    "tz_m": float(pose[2, 3]),
                }
            )
        write_feather(pd.DataFrame(city_rows), staging / "city_SE3_egovehicle.feather")

        median_delta_ns = float(np.median(np.diff(np.asarray(anchors, dtype=np.int64))))
        frame_rate_hz = 1_000_000_000.0 / median_delta_ns
        manifest = ConversionManifest(
            schema_version="1.0",
            dataset="pandaset",
            source_scene_id=source_scene.name,
            output_log_id=output_log_id,
            mode=mode,
            cameras=tuple(camera.pseudo_name for camera in cameras),
            anchor_camera=cameras[0].pseudo_name,
            source_frame_count=frame_count,
            output_frame_count=len(frames),
            source_frame_rate_hz=frame_rate_hz,
            output_frame_rate_hz=frame_rate_hz,
            camera_records=tuple(
                CameraRecord(
                    name=camera.pseudo_name,
                    source_name=camera.source_name,
                    frame_count=len(frames),
                    max_sync_delta_ns=max(
                        abs(
                            frame.camera_timestamps_ns[camera.pseudo_name]
                            - frame.anchor_timestamp_ns
                        )
                        for frame in frames
                    ),
                )
                for camera in cameras
            ),
            frames=tuple(frames),
            calibration_sha256=_calibration_hash(calibration_dir),
            source_artifacts=source_artifacts,
            has_lidar=True,
            has_ego_pose=True,
            has_annotations=False,
            real_mask_pattern=real_mask_pattern,
            faithfill_mask_pattern=None,
            honest_black_mask_pattern=None,
            supported_azimuth_deg=((0.0, 360.0),),
            honest_black_azimuth_deg=(),
            coordinate_convention_transform=tuple(
                tuple(float(value) for value in row) for row in convention
            ),
            converter_git_commit=converter_git_commit,
            created_at=created_at,
        )
        manifest.write_json(staging / "conversion_manifest.json")
        _validate_written_log(staging, manifest, expected_camera_hashes)
        for snapshot in consumed_snapshots:
            _verify_snapshot(snapshot)
        staging.rename(final_output)
        published = True
        return final_output, manifest
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)
