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

from waymo2panorama.data_io.av2_loader import AV2RingLoader

from .contract import CameraRecord, ConversionManifest, FrameRecord, SourceArtifact
from .geometry import make_transform, matrix_to_quaternion_wxyz, quaternion_wxyz_to_matrix
from .io import materialize_file, sha256_file, write_empty_annotations, write_feather


NUSCENES_CAMERA_MAP: tuple[tuple[str, str], ...] = (
    ("CAM_FRONT", "ring_front_center"),
    ("CAM_FRONT_LEFT", "ring_front_left"),
    ("CAM_BACK_LEFT", "ring_side_left"),
    ("CAM_BACK", "ring_rear"),
    ("CAM_BACK_RIGHT", "ring_side_right"),
    ("CAM_FRONT_RIGHT", "ring_front_right"),
)

_LIDAR_CHANNEL = "LIDAR_TOP"
_METADATA_FILES = (
    "scene.json",
    "sample.json",
    "sample_data.json",
    "ego_pose.json",
    "calibrated_sensor.json",
    "sensor.json",
)


@dataclass(frozen=True)
class _SourceSnapshot:
    path: Path
    artifact_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class _Calibration:
    token: str
    channel: str
    modality: str
    transform: np.ndarray
    intrinsic: np.ndarray | None


@dataclass(frozen=True)
class _Datum:
    token: str
    channel: str
    timestamp_us: int
    timestamp_ns: int
    sample_token: str
    ego_pose_token: str
    calibration: _Calibration
    filename: str
    path: Path
    width_px: int
    height_px: int


def _read_json_list(path: Path, table_name: str) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as source_file:
            value = json.load(source_file)
    except FileNotFoundError:
        raise ValueError(f"missing required metadata file: {path}") from None
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON metadata file: {path}") from error
    if not isinstance(value, list):
        raise ValueError(f"{table_name} JSON root must be a list")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise ValueError(f"{table_name} row {index} must be an object")
        rows.append(row)
    return rows


def _token_index(rows: list[dict[str, Any]], table_name: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        token = row.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError(f"{table_name} row {index} has missing or invalid token")
        if token in result:
            raise ValueError(f"{table_name} contains duplicate token {token!r}")
        result[token] = row
    return result


def _required_string(row: dict[str, Any], field: str, context: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} has missing or invalid {field}")
    return value


def _required_int(row: dict[str, Any], field: str, context: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context} has missing or invalid {field}")
    return value


def _vector(value: object, length: int, context: str) -> np.ndarray:
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} must contain numeric values") from error
    if result.shape != (length,) or not np.isfinite(result).all():
        raise ValueError(f"{context} must contain {length} finite numeric values")
    return result


def _pose(row: dict[str, Any], context: str) -> np.ndarray:
    rotation = quaternion_wxyz_to_matrix(_vector(row.get("rotation"), 4, f"{context} rotation"))
    translation = _vector(row.get("translation"), 3, f"{context} translation")
    return make_transform(rotation, translation)


def _intrinsic(row: dict[str, Any], context: str, modality: str) -> np.ndarray | None:
    value = row.get("camera_intrinsic")
    if modality != "camera":
        if value not in ([], None):
            raise ValueError(f"{context} non-camera camera_intrinsic must be empty")
        return None
    try:
        matrix = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} camera_intrinsic must be numeric 3x3") from error
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError(f"{context} camera_intrinsic must be finite 3x3")
    if matrix[0, 0] <= 0.0 or matrix[1, 1] <= 0.0:
        raise ValueError(f"{context} camera_intrinsic fx/fy must be positive")
    return matrix


def _snapshot(path: Path, artifact_path: str) -> _SourceSnapshot:
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


def _resolve_data_path(source_root: Path, filename: str, context: str) -> Path:
    relative = Path(filename)
    if relative.is_absolute():
        raise ValueError(f"{context} filename must be relative to source_root")
    root = source_root.resolve()
    path = (source_root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"{context} filename escapes source_root")
    if not path.is_file():
        raise ValueError(f"missing sample_data file: {filename}")
    return path


def _match_ordered_distinct_indices(
    values: tuple[int, ...],
    queries: tuple[int, ...],
    channel: str,
    max_delta_ns: int,
) -> tuple[int, ...]:
    if len(values) < len(queries):
        raise ValueError(
            f"{channel} has fewer source frames than the common alignment target"
        )
    if not queries:
        raise ValueError("common alignment target must contain at least one frame")

    source_count = len(values)
    query_count = len(queries)
    parents = [[-1] * source_count for _ in range(query_count)]
    previous = [
        delta if (delta := abs(value - queries[0])) <= max_delta_ns else math.inf
        for value in values
    ]

    for query_index in range(1, query_count):
        current = [math.inf] * source_count
        best_predecessor = query_index - 1
        best_cost = previous[best_predecessor]
        for value_index in range(query_index, source_count):
            predecessor = value_index - 1
            predecessor_cost = previous[predecessor]
            if (predecessor_cost, predecessor) < (best_cost, best_predecessor):
                best_cost = predecessor_cost
                best_predecessor = predecessor
            delta = abs(values[value_index] - queries[query_index])
            if best_cost < math.inf and delta <= max_delta_ns:
                current[value_index] = best_cost + delta
                parents[query_index][value_index] = best_predecessor
        previous = current

    last_index = min(
        range(query_count - 1, source_count),
        key=lambda index: (previous[index], index),
    )
    if previous[last_index] == math.inf:
        raise ValueError(
            f"{channel} cannot satisfy ordered distinct matching within nominal cadence"
        )
    selected = [last_index]
    for query_index in range(query_count - 1, 0, -1):
        last_index = parents[query_index][last_index]
        selected.append(last_index)
    selected.reverse()
    return tuple(selected)


def _nominal_cadence_ns(values: tuple[int, ...], channel: str) -> int:
    if len(values) < 2:
        raise ValueError(
            f"{channel} needs at least two timestamps to derive nominal cadence"
        )
    deltas = sorted(right - left for left, right in zip(values, values[1:]))
    if any(delta <= 0 for delta in deltas):
        raise ValueError(f"{channel} timestamps must be strictly increasing")
    midpoint = len(deltas) // 2
    if len(deltas) % 2:
        return deltas[midpoint]
    return (deltas[midpoint - 1] + deltas[midpoint] + 1) // 2


def _maximum_common_anchor_indices(
    timestamps_by_channel: dict[str, tuple[int, ...]],
    sync_window_ns: dict[str, int],
    anchor_channel: str,
) -> tuple[int, ...]:
    """Select a maximum-cardinality common subsequence without source reuse.

    With sorted timestamps and fixed per-channel windows, choosing the earliest
    feasible source index for the earliest feasible anchor cannot reduce any
    later match. This exchange property makes the forward selection maximal.
    """
    source_anchors = timestamps_by_channel[anchor_channel]
    last_selected = {channel: -1 for channel in timestamps_by_channel}
    selected_anchors: list[int] = []

    for anchor_index, anchor in enumerate(source_anchors):
        candidates: dict[str, int] = {anchor_channel: anchor_index}
        for channel, values in timestamps_by_channel.items():
            if channel == anchor_channel:
                continue
            window = sync_window_ns[channel]
            candidate = bisect.bisect_left(
                values,
                anchor - window,
                lo=last_selected[channel] + 1,
            )
            if candidate == len(values) or values[candidate] > anchor + window:
                break
            candidates[channel] = candidate
        else:
            selected_anchors.append(anchor_index)
            last_selected.update(candidates)

    if not selected_anchors:
        raise ValueError(
            "no common anchors satisfy cadence-derived ordered distinct matching"
        )
    return tuple(selected_anchors)


def _read_lidar_bin(path: Path) -> np.ndarray:
    size_bytes = path.stat().st_size
    if size_bytes % (5 * np.dtype(np.float32).itemsize) != 0:
        raise ValueError(f"lidar sweep must be float32 Nx5: {path}")
    points = np.fromfile(path, dtype=np.float32)
    points = points.reshape((-1, 5))
    if not np.isfinite(points).all():
        raise ValueError(f"lidar sweep contains non-finite values: {path}")
    return points


def _same_transform(left: np.ndarray, right: np.ndarray) -> bool:
    return bool(np.allclose(left, right, rtol=0.0, atol=1e-12))


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
    row = _pose_row("ego", transform)
    row.pop("sensor_name")
    return {"timestamp_ns": timestamp_ns, **row}


def _calibration_hash(calibration_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(calibration_dir.glob("*.feather"), key=lambda value: value.name):
        digest.update(path.read_bytes())
    return digest.hexdigest()


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

    staged_images = tuple((staging / "sensors" / "cameras").glob("**/*.jpg"))
    staged_by_relative = {
        path.relative_to(staging).as_posix(): path for path in staged_images
    }
    if set(staged_by_relative) != set(expected_camera_hashes):
        raise ValueError("staged camera image set does not match selected source images")
    for relative_path, path in staged_by_relative.items():
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            raise ValueError(f"invalid staged camera JPEG: {relative_path}") from error
        if sha256_file(path) != expected_camera_hashes[relative_path]:
            raise ValueError(f"staged camera image changed from source: {relative_path}")

    loader = AV2RingLoader(staging, cameras=manifest.cameras)
    loader.load_synced_frame(manifest.frames[0].anchor_timestamp_ns)


def _frame_rate_hz(anchors_ns: tuple[int, ...]) -> float:
    if len(anchors_ns) == 1:
        return 1.0
    median_delta = float(np.median(np.diff(np.asarray(anchors_ns, dtype=np.int64))))
    return 1_000_000_000.0 / median_delta


def convert_nuscenes_scene(
    source_root: Path,
    metadata_root: Path,
    scene_id: str,
    output_root: Path,
    output_log_id: str,
    *,
    mode: str = "B",
    allow_experimental_a: bool = False,
    observed_real_fill_fraction: float | None = None,
    real_mask_pattern: str | None = None,
    converter_git_commit: str,
    created_at: str,
) -> tuple[Path, ConversionManifest]:
    source_root = Path(source_root)
    metadata_root = Path(metadata_root)
    output_root = Path(output_root)
    if not source_root.is_dir():
        raise ValueError(f"source_root must be a directory: {source_root}")
    if not metadata_root.is_dir():
        raise ValueError(f"metadata_root must be a directory: {metadata_root}")
    if not isinstance(scene_id, str) or not scene_id:
        raise ValueError("scene_id must be a nonempty string")
    if (
        not output_log_id
        or output_log_id in (".", "..")
        or Path(output_log_id).name != output_log_id
    ):
        raise ValueError("output_log_id must be one nonempty path component")
    final_output = output_root / output_log_id
    if os.path.lexists(final_output):
        raise FileExistsError(f"output log already exists: {final_output}")
    if mode not in ("A", "B"):
        raise ValueError("mode must be 'A' or 'B'")
    derived_artifact: SourceArtifact | None = None
    if mode == "B":
        if (
            allow_experimental_a
            or observed_real_fill_fraction is not None
            or real_mask_pattern is not None
        ):
            raise ValueError("B mode rejects experimental A-only evidence parameters")
    else:
        if allow_experimental_a is not True:
            raise ValueError(
                "nuScenes mode A is disabled unless explicitly enabled as experimental"
            )
        if (
            isinstance(observed_real_fill_fraction, bool)
            or not isinstance(observed_real_fill_fraction, (int, float))
            or not math.isfinite(float(observed_real_fill_fraction))
            or not 0.0 <= float(observed_real_fill_fraction) <= 1.0
        ):
            raise ValueError(
                "experimental A requires observed_real_fill_fraction in [0, 1]"
            )
        if not isinstance(real_mask_pattern, str) or not real_mask_pattern.strip():
            raise ValueError("experimental A requires a nonempty real_mask_pattern")
        descriptor_payload = {
            "adapter_algorithm_version": "nuscenes_pseudo_av2_v1",
            "observed_real_fill_fraction": float(observed_real_fill_fraction),
            "real_mask_pattern": real_mask_pattern,
            "status": "experimental_not_a_ready",
        }
        descriptor = "derived:nuscenes_experimental_a_evidence=" + json.dumps(
            descriptor_payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        descriptor_bytes = descriptor.encode("utf-8")
        derived_artifact = SourceArtifact(
            path=descriptor,
            sha256=hashlib.sha256(descriptor_bytes).hexdigest(),
            size_bytes=len(descriptor_bytes),
        )

    metadata_snapshots = tuple(
        _snapshot(metadata_root / filename, f"metadata/{filename}")
        for filename in _METADATA_FILES
    )
    tables = {
        filename.removesuffix(".json"): _read_json_list(
            metadata_root / filename, filename.removesuffix(".json")
        )
        for filename in _METADATA_FILES
    }
    for snapshot in metadata_snapshots:
        _verify_snapshot(snapshot)

    scene_index = _token_index(tables["scene"], "scene")
    sample_index = _token_index(tables["sample"], "sample")
    sample_data_index = _token_index(tables["sample_data"], "sample_data")
    ego_pose_index = _token_index(tables["ego_pose"], "ego_pose")
    calibration_index = _token_index(tables["calibrated_sensor"], "calibrated_sensor")
    sensor_index = _token_index(tables["sensor"], "sensor")

    for row in sample_index.values():
        context = f"sample {row['token']!r}"
        referenced_scene = _required_string(row, "scene_token", context)
        if referenced_scene not in scene_index:
            raise ValueError(f"{context} has dangling scene_token {referenced_scene!r}")
        for field in ("prev", "next"):
            reference = row.get(field)
            if not isinstance(reference, str):
                raise ValueError(f"{context} has missing or invalid {field}")
            if reference and reference not in sample_index:
                raise ValueError(f"{context} has dangling {field} {reference!r}")

    scene_matches = [
        row
        for row in scene_index.values()
        if row["token"] == scene_id or row.get("name") == scene_id
    ]
    if len(scene_matches) != 1:
        raise ValueError(
            f"scene_id must match exactly one scene token or name; found {len(scene_matches)}"
        )
    scene = scene_matches[0]
    scene_token = scene["token"]
    scene_samples = [
        row for row in sample_index.values() if row.get("scene_token") == scene_token
    ]
    if not scene_samples:
        raise ValueError(f"scene {scene_token!r} has no samples")
    scene_sample_tokens = {row["token"] for row in scene_samples}
    for field in ("first_sample_token", "last_sample_token"):
        token = _required_string(scene, field, f"scene {scene_token!r}")
        if token not in scene_sample_tokens:
            raise ValueError(f"scene {scene_token!r} has dangling {field} {token!r}")
    for row in scene_samples:
        context = f"sample {row['token']!r}"
        _required_int(row, "timestamp", context)
        for field in ("prev", "next"):
            reference = row.get(field)
            if not isinstance(reference, str):
                raise ValueError(f"{context} has missing or invalid {field}")
            if reference and reference not in scene_sample_tokens:
                raise ValueError(f"{context} has dangling {field} {reference!r}")

    calibrations: dict[str, _Calibration] = {}
    for token, row in calibration_index.items():
        context = f"calibrated_sensor {token!r}"
        sensor_token = _required_string(row, "sensor_token", context)
        sensor = sensor_index.get(sensor_token)
        if sensor is None:
            raise ValueError(f"{context} has dangling sensor_token {sensor_token!r}")
        channel = _required_string(sensor, "channel", f"sensor {sensor_token!r}")
        modality = _required_string(sensor, "modality", f"sensor {sensor_token!r}")
        calibrations[token] = _Calibration(
            token=token,
            channel=channel,
            modality=modality,
            transform=_pose(row, context),
            intrinsic=_intrinsic(row, context, modality),
        )

    for token, row in sample_data_index.items():
        context = f"sample_data {token!r}"
        sample_token = _required_string(row, "sample_token", context)
        if sample_token not in sample_index:
            raise ValueError(f"{context} has dangling sample_token {sample_token!r}")
        ego_token = _required_string(row, "ego_pose_token", context)
        if ego_token not in ego_pose_index:
            raise ValueError(f"{context} has dangling ego_pose_token {ego_token!r}")
        calibration_token = _required_string(row, "calibrated_sensor_token", context)
        calibration = calibrations.get(calibration_token)
        if calibration is None:
            raise ValueError(
                f"{context} has dangling calibrated_sensor_token {calibration_token!r}"
            )
        fileformat = _required_string(row, "fileformat", context).lower()
        filename = _required_string(row, "filename", context)
        is_key_frame = row.get("is_key_frame")
        if not isinstance(is_key_frame, bool):
            raise ValueError(f"{context} has missing or invalid is_key_frame")
        width = row.get("width")
        height = row.get("height")
        if (
            isinstance(width, bool)
            or not isinstance(width, int)
            or width < 0
            or isinstance(height, bool)
            or not isinstance(height, int)
            or height < 0
        ):
            raise ValueError(f"{context} width/height dimensions must be nonnegative integers")
        if calibration.modality == "camera" and (width == 0 or height == 0):
            raise ValueError(f"{context} camera dimensions must be positive integers")
        suffix = Path(filename).suffix.lower()
        if calibration.modality == "camera" and (
            fileformat not in ("jpg", "jpeg") or suffix not in (".jpg", ".jpeg")
        ):
            raise ValueError(f"{context} camera fileformat must describe a JPEG")
        if calibration.modality == "lidar" and (
            fileformat != "pcd" or suffix != ".bin"
        ):
            raise ValueError(f"{context} lidar fileformat must be pcd with a .bin payload")
        for field in ("prev", "next"):
            reference = row.get(field)
            if not isinstance(reference, str):
                raise ValueError(f"{context} has missing or invalid {field}")
            if reference and reference not in sample_data_index:
                raise ValueError(f"{context} has dangling {field} {reference!r}")

    wanted_channels = {source for source, _ in NUSCENES_CAMERA_MAP} | {_LIDAR_CHANNEL}
    by_channel: dict[str, list[_Datum]] = {channel: [] for channel in wanted_channels}
    for token, row in sample_data_index.items():
        sample_token = _required_string(row, "sample_token", f"sample_data {token!r}")
        if sample_token not in scene_sample_tokens:
            continue
        context = f"sample_data {token!r}"
        ego_token = _required_string(row, "ego_pose_token", context)
        if ego_token not in ego_pose_index:
            raise ValueError(f"{context} has dangling ego_pose_token {ego_token!r}")
        calibration_token = _required_string(row, "calibrated_sensor_token", context)
        calibration = calibrations.get(calibration_token)
        if calibration is None:
            raise ValueError(
                f"{context} has dangling calibrated_sensor_token {calibration_token!r}"
            )
        if calibration.channel not in wanted_channels:
            continue
        timestamp_us = _required_int(row, "timestamp", context)
        filename = _required_string(row, "filename", context)
        path = _resolve_data_path(source_root, filename, context)
        width = row.get("width")
        height = row.get("height")
        if calibration.modality == "camera":
            if (
                isinstance(width, bool)
                or not isinstance(width, int)
                or width <= 0
                or isinstance(height, bool)
                or not isinstance(height, int)
                or height <= 0
            ):
                raise ValueError(f"{context} camera dimensions must be positive integers")
        else:
            width = 0
            height = 0
        by_channel[calibration.channel].append(
            _Datum(
                token=token,
                channel=calibration.channel,
                timestamp_us=timestamp_us,
                timestamp_ns=timestamp_us * 1000,
                sample_token=sample_token,
                ego_pose_token=ego_token,
                calibration=calibration,
                filename=Path(filename).as_posix(),
                path=path,
                width_px=int(width),
                height_px=int(height),
            )
        )

    for channel, values in by_channel.items():
        if not values:
            raise ValueError(f"scene must contain data for required channel {channel}")
        values.sort(key=lambda datum: (datum.timestamp_us, datum.token))
        timestamps = [datum.timestamp_us for datum in values]
        if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
            raise ValueError(f"{channel} timestamps must be strictly increasing")
        expected_modality = "lidar" if channel == _LIDAR_CHANNEL else "camera"
        if any(datum.calibration.modality != expected_modality for datum in values):
            raise ValueError(f"{channel} has inconsistent sensor modality")
        first = values[0]
        for datum in values[1:]:
            if not _same_transform(
                datum.calibration.transform, first.calibration.transform
            ):
                raise ValueError(f"{channel} calibration drift across scene")
            if channel != _LIDAR_CHANNEL:
                if (datum.width_px, datum.height_px) != (
                    first.width_px,
                    first.height_px,
                ):
                    raise ValueError(f"{channel} image dimensions drift across scene")
                if not bool(
                    np.allclose(
                        datum.calibration.intrinsic,
                        first.calibration.intrinsic,
                        rtol=0.0,
                        atol=1e-12,
                    )
                ):
                    raise ValueError(f"{channel} calibration drift across scene")

    source_anchors = tuple(datum.timestamp_ns for datum in by_channel["CAM_FRONT"])
    channel_order = tuple(source for source, _ in NUSCENES_CAMERA_MAP) + (
        _LIDAR_CHANNEL,
    )
    timestamps_by_channel = {
        channel: tuple(datum.timestamp_ns for datum in by_channel[channel])
        for channel in channel_order
    }
    sync_window_ns = {
        channel: _nominal_cadence_ns(timestamps_by_channel[channel], channel)
        for channel in channel_order
    }
    anchor_indices = _maximum_common_anchor_indices(
        timestamps_by_channel,
        sync_window_ns,
        "CAM_FRONT",
    )
    anchors = tuple(source_anchors[index] for index in anchor_indices)
    selected_indices = {
        channel: _match_ordered_distinct_indices(
            timestamps_by_channel[channel],
            anchors,
            channel,
            sync_window_ns[channel],
        )
        for channel in channel_order
        if channel != "CAM_FRONT"
    }
    selected_indices["CAM_FRONT"] = anchor_indices
    selected = {
        channel: tuple(by_channel[channel][index] for index in selected_indices[channel])
        for channel in channel_order
    }
    alignment_payload = {
        "adapter_algorithm_version": "nuscenes_cadence_window_v3",
        "anchor_channel": "CAM_FRONT",
        "common_subsequence_selection": (
            "earliest_feasible_anchor_and_source_indices"
        ),
        "dropped_anchor_frame_count": len(source_anchors) - len(anchors),
        "matching_objective": (
            "maximum_cardinality_then_minimum_per_channel_total_absolute_sync_delta_ns"
        ),
        "output_frame_count": len(anchors),
        "source_anchor_frame_count": len(source_anchors),
        "source_channel_frame_counts": {
            channel: len(timestamps_by_channel[channel]) for channel in channel_order
        },
        "sync_window_derivation": (
            "ceil_median_positive_consecutive_delta_ns_per_channel"
        ),
        "sync_window_ns": sync_window_ns,
    }
    alignment_descriptor = "derived:nuscenes_temporal_alignment=" + json.dumps(
        alignment_payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    alignment_bytes = alignment_descriptor.encode("utf-8")
    alignment_artifact = SourceArtifact(
        path=alignment_descriptor,
        sha256=hashlib.sha256(alignment_bytes).hexdigest(),
        size_bytes=len(alignment_bytes),
    )

    data_snapshots: dict[Path, _SourceSnapshot] = {}
    artifact_paths = {snapshot.artifact_path for snapshot in metadata_snapshots}
    for values in selected.values():
        for datum in values:
            artifact_path = f"data/{datum.filename}"
            if artifact_path in artifact_paths:
                raise ValueError(f"source artifact paths must be globally unique: {artifact_path}")
            artifact_paths.add(artifact_path)
            data_snapshots[datum.path] = _snapshot(datum.path, artifact_path)

    camera_calibrations: dict[str, _Calibration] = {}
    for source_name, _ in NUSCENES_CAMERA_MAP:
        values = selected[source_name]
        first = values[0]
        dimensions = (first.width_px, first.height_px)
        calibration = first.calibration
        for datum in values:
            snapshot = data_snapshots[datum.path]
            _verify_snapshot(snapshot)
            try:
                with Image.open(datum.path) as image:
                    actual_dimensions = image.size
                    image.verify()
            except Exception as error:
                raise ValueError(f"invalid JPEG for {source_name}: {datum.filename}") from error
            _verify_snapshot(snapshot)
            if actual_dimensions != (datum.width_px, datum.height_px):
                raise ValueError(
                    f"{source_name} JPEG dimensions disagree with sample_data metadata"
                )
            if (datum.width_px, datum.height_px) != dimensions:
                raise ValueError(f"{source_name} image dimensions drift across scene")
            same_intrinsic = bool(
                np.allclose(
                    datum.calibration.intrinsic,
                    calibration.intrinsic,
                    rtol=0.0,
                    atol=1e-12,
                )
            )
            if not _same_transform(datum.calibration.transform, calibration.transform) or not same_intrinsic:
                raise ValueError(f"{source_name} calibration drift across scene")
        camera_calibrations[source_name] = calibration

    lidar_calibration = selected[_LIDAR_CHANNEL][0].calibration
    for datum in selected[_LIDAR_CHANNEL]:
        if not _same_transform(datum.calibration.transform, lidar_calibration.transform):
            raise ValueError(f"{_LIDAR_CHANNEL} calibration drift across scene")
        snapshot = data_snapshots[datum.path]
        _verify_snapshot(snapshot)
        points = _read_lidar_bin(datum.path)
        _verify_snapshot(snapshot)
        del points

    pose_by_timestamp: dict[int, np.ndarray] = {}
    for datum in (datum for values in selected.values() for datum in values):
        pose_row = ego_pose_index[datum.ego_pose_token]
        context = f"ego_pose {datum.ego_pose_token!r}"
        pose_timestamp = _required_int(pose_row, "timestamp", context)
        if pose_timestamp != datum.timestamp_us:
            raise ValueError(f"{context} timestamp conflicts with referenced sample_data")
        transform = _pose(pose_row, context)
        existing = pose_by_timestamp.get(datum.timestamp_ns)
        if existing is not None and not _same_transform(existing, transform):
            raise ValueError(f"conflicting ego poses at timestamp {datum.timestamp_ns}")
        pose_by_timestamp[datum.timestamp_ns] = transform

    for snapshot in (*metadata_snapshots, *data_snapshots.values()):
        _verify_snapshot(snapshot)

    frames = tuple(
        FrameRecord(
            index=index,
            anchor_timestamp_ns=anchor,
            camera_timestamps_ns={
                pseudo_name: selected[source_name][index].timestamp_ns
                for source_name, pseudo_name in NUSCENES_CAMERA_MAP
            },
            lidar_timestamp_ns=selected[_LIDAR_CHANNEL][index].timestamp_ns,
        )
        for index, anchor in enumerate(anchors)
    )
    source_artifacts = tuple(
        SourceArtifact(snapshot.artifact_path, snapshot.sha256, snapshot.size_bytes)
        for snapshot in sorted(
            (*metadata_snapshots, *data_snapshots.values()),
            key=lambda value: value.artifact_path,
        )
    )
    if derived_artifact is not None:
        source_artifacts += (derived_artifact,)
    source_artifacts += (alignment_artifact,)

    output_root.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(final_output):
        raise FileExistsError(f"output log already exists: {final_output}")
    staging = Path(tempfile.mkdtemp(prefix=f".{output_log_id}.staging-", dir=output_root))
    published = False
    try:
        calibration_dir = staging / "calibration"
        intrinsics_rows = []
        extrinsics_rows = []
        for source_name, pseudo_name in NUSCENES_CAMERA_MAP:
            datum = selected[source_name][0]
            intrinsic = camera_calibrations[source_name].intrinsic
            assert intrinsic is not None
            intrinsics_rows.append(
                {
                    "sensor_name": pseudo_name,
                    "fx_px": float(intrinsic[0, 0]),
                    "fy_px": float(intrinsic[1, 1]),
                    "cx_px": float(intrinsic[0, 2]),
                    "cy_px": float(intrinsic[1, 2]),
                    "width_px": datum.width_px,
                    "height_px": datum.height_px,
                    "k1": 0.0,
                    "k2": 0.0,
                    "k3": 0.0,
                }
            )
            extrinsics_rows.append(
                _pose_row(pseudo_name, camera_calibrations[source_name].transform)
            )
        extrinsics_rows.append(_pose_row("up_lidar", lidar_calibration.transform))
        write_feather(pd.DataFrame(intrinsics_rows), calibration_dir / "intrinsics.feather")
        write_feather(
            pd.DataFrame(extrinsics_rows),
            calibration_dir / "egovehicle_SE3_sensor.feather",
        )

        expected_camera_hashes: dict[str, str] = {}
        for source_name, pseudo_name in NUSCENES_CAMERA_MAP:
            destination_dir = staging / "sensors" / "cameras" / pseudo_name
            for datum in selected[source_name]:
                destination = destination_dir / f"{datum.timestamp_ns}.jpg"
                materialize_file(datum.path, destination, prefer_hardlink=False)
                snapshot = data_snapshots[datum.path]
                _verify_snapshot(snapshot)
                expected_camera_hashes[destination.relative_to(staging).as_posix()] = (
                    snapshot.sha256
                )

        for datum in selected[_LIDAR_CHANNEL]:
            snapshot = data_snapshots[datum.path]
            _verify_snapshot(snapshot)
            points = _read_lidar_bin(datum.path)
            _verify_snapshot(snapshot)
            xyz_ego = (
                points[:, :3].astype(np.float64) @ lidar_calibration.transform[:3, :3].T
                + lidar_calibration.transform[:3, 3]
            )
            lidar_frame = pd.DataFrame(
                {
                    "x": xyz_ego[:, 0].astype(np.float32),
                    "y": xyz_ego[:, 1].astype(np.float32),
                    "z": xyz_ego[:, 2].astype(np.float32),
                    "intensity": points[:, 3].astype(np.float32),
                }
            )
            write_feather(
                lidar_frame,
                staging / "sensors" / "lidar" / f"{datum.timestamp_ns}.feather",
            )
            del points, xyz_ego, lidar_frame

        write_empty_annotations(staging / "annotations.feather")
        write_feather(
            pd.DataFrame(
                [
                    _city_pose_row(timestamp, pose_by_timestamp[timestamp])
                    for timestamp in sorted(pose_by_timestamp)
                ]
            ),
            staging / "city_SE3_egovehicle.feather",
        )

        source_frame_rate = _frame_rate_hz(source_anchors)
        output_frame_rate = _frame_rate_hz(anchors)
        manifest = ConversionManifest(
            schema_version="1.0",
            dataset="nuscenes",
            source_scene_id=scene_token,
            output_log_id=output_log_id,
            mode=mode,
            cameras=tuple(pseudo for _, pseudo in NUSCENES_CAMERA_MAP),
            anchor_camera=NUSCENES_CAMERA_MAP[0][1],
            source_frame_count=len(source_anchors),
            output_frame_count=len(frames),
            source_frame_rate_hz=source_frame_rate,
            output_frame_rate_hz=output_frame_rate,
            camera_records=tuple(
                CameraRecord(
                    name=pseudo_name,
                    source_name=source_name,
                    frame_count=len(frames),
                    max_sync_delta_ns=max(
                        abs(frame.camera_timestamps_ns[pseudo_name] - frame.anchor_timestamp_ns)
                        for frame in frames
                    ),
                )
                for source_name, pseudo_name in NUSCENES_CAMERA_MAP
            ),
            frames=frames,
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
        for snapshot in (*metadata_snapshots, *data_snapshots.values()):
            _verify_snapshot(snapshot)
        if os.path.lexists(final_output):
            raise FileExistsError(f"output log already exists: {final_output}")
        staging.rename(final_output)
        published = True
        return final_output, manifest
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)
