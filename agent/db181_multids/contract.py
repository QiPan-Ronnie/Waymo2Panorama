from __future__ import annotations

import json
import math
import re
import tempfile
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_COMMIT_RE = re.compile(r"[0-9a-f]{7,64}\Z")
_AZIMUTH_TOLERANCE_DEG = 1e-9
_RIGID_TRANSFORM_TOLERANCE = 1e-9


@dataclass(frozen=True)
class SourceArtifact:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class CameraRecord:
    name: str
    source_name: str
    frame_count: int
    max_sync_delta_ns: int


@dataclass(frozen=True)
class FrameRecord:
    index: int
    anchor_timestamp_ns: int
    camera_timestamps_ns: Mapping[str, int]
    lidar_timestamp_ns: int | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "camera_timestamps_ns",
            MappingProxyType(dict(self.camera_timestamps_ns)),
        )


@dataclass(frozen=True)
class ConversionManifest:
    schema_version: str
    dataset: str
    source_scene_id: str
    output_log_id: str
    mode: Literal["A", "B"]
    cameras: tuple[str, ...]
    anchor_camera: str
    source_frame_count: int
    output_frame_count: int
    source_frame_rate_hz: float
    output_frame_rate_hz: float
    camera_records: tuple[CameraRecord, ...]
    frames: tuple[FrameRecord, ...]
    calibration_sha256: str
    source_artifacts: tuple[SourceArtifact, ...]
    has_lidar: bool
    has_ego_pose: bool
    has_annotations: bool
    real_mask_pattern: str | None
    faithfill_mask_pattern: str | None
    honest_black_mask_pattern: str | None
    supported_azimuth_deg: tuple[tuple[float, float], ...]
    honest_black_azimuth_deg: tuple[tuple[float, float], ...]
    coordinate_convention_transform: tuple[tuple[float, ...], ...]
    converter_git_commit: str
    created_at: str

    def __post_init__(self) -> None:
        for name in (
            "cameras",
            "camera_records",
            "frames",
            "source_artifacts",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for name in ("supported_azimuth_deg", "honest_black_azimuth_deg"):
            object.__setattr__(
                self,
                name,
                tuple(tuple(interval) for interval in getattr(self, name)),
            )
        object.__setattr__(
            self,
            "coordinate_convention_transform",
            tuple(tuple(row) for row in self.coordinate_convention_transform),
        )

    @property
    def frame_contract(self) -> str:
        if not _is_int(self.output_frame_count) or self.output_frame_count < 1:
            raise ValueError("output_frame_count must be at least 1")
        return f"1+{self.output_frame_count - 1}"

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("schema_version must be exactly '1.0'")

        for name in (
            "dataset",
            "source_scene_id",
            "output_log_id",
            "converter_git_commit",
            "created_at",
        ):
            if not _is_nonempty_string(getattr(self, name)):
                raise ValueError(f"{name} must be a nonempty string")
        if _GIT_COMMIT_RE.fullmatch(self.converter_git_commit) is None:
            raise ValueError(
                "converter_git_commit must be lowercase hexadecimal with 7 to 64 characters"
            )
        _validate_created_at(self.created_at)

        if self.mode not in ("A", "B"):
            raise ValueError("mode must be 'A' or 'B'")

        if not self.cameras:
            raise ValueError("cameras must be nonempty")
        if any(not _is_nonempty_string(camera) for camera in self.cameras):
            raise ValueError("cameras must not contain blank names")
        if len(set(self.cameras)) != len(self.cameras):
            raise ValueError("cameras must not contain duplicates")
        if self.anchor_camera != self.cameras[0]:
            raise ValueError("anchor_camera must equal cameras[0]")

        if not _is_int(self.source_frame_count) or self.source_frame_count < 1:
            raise ValueError("source_frame_count must be a positive integer")
        if not _is_int(self.output_frame_count) or self.output_frame_count < 1:
            raise ValueError("output_frame_count must be a positive integer")
        if self.output_frame_count > self.source_frame_count:
            raise ValueError("silent frame padding is forbidden: output exceeds source")

        _validate_positive_rate("source_frame_rate_hz", self.source_frame_rate_hz)
        _validate_positive_rate("output_frame_rate_hz", self.output_frame_rate_hz)

        for name in ("has_lidar", "has_ego_pose", "has_annotations"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")

        if self.mode == "A" and not self.has_lidar:
            raise ValueError("A mode requires LiDAR")
        if self.mode == "A" and not _is_nonempty_string(self.real_mask_pattern):
            raise ValueError("A mode requires a nonempty real_mask_pattern")

        for name in (
            "real_mask_pattern",
            "faithfill_mask_pattern",
            "honest_black_mask_pattern",
        ):
            value = getattr(self, name)
            if value is not None and not _is_nonempty_string(value):
                raise ValueError(f"{name} must be None or a nonempty string")

        if any(not isinstance(record, CameraRecord) for record in self.camera_records):
            raise ValueError("camera_records entries must be CameraRecord objects")
        if tuple(record.name for record in self.camera_records) != self.cameras:
            raise ValueError("camera_records names/order must exactly match cameras")
        for record in self.camera_records:
            if not _is_nonempty_string(record.source_name):
                raise ValueError("camera_records source_name must be nonempty")
            if (
                not _is_int(record.frame_count)
                or record.frame_count != self.output_frame_count
            ):
                raise ValueError(
                    "camera_records frame_count must be an integer equal to output_frame_count"
                )
            if not _is_int(record.max_sync_delta_ns) or record.max_sync_delta_ns < 0:
                raise ValueError("camera_records max_sync_delta_ns must be a nonnegative integer")

        if len(self.frames) != self.output_frame_count:
            raise ValueError("frames length must equal output_frame_count")
        if any(not isinstance(frame, FrameRecord) for frame in self.frames):
            raise ValueError("frames entries must be FrameRecord objects")
        if any(not _is_int(frame.index) for frame in self.frames):
            raise ValueError("frame indices must be integers")
        expected_indices = tuple(range(self.output_frame_count))
        if tuple(frame.index for frame in self.frames) != expected_indices:
            raise ValueError("frame indices must be exactly 0..output_frame_count-1")

        previous_anchor: int | None = None
        expected_camera_keys = set(self.cameras)
        for frame in self.frames:
            if not _is_positive_int(frame.anchor_timestamp_ns):
                raise ValueError("anchor timestamp must be a positive integer")
            if previous_anchor is not None and frame.anchor_timestamp_ns <= previous_anchor:
                raise ValueError("anchor timestamps must be strictly increasing")
            previous_anchor = frame.anchor_timestamp_ns

            if set(frame.camera_timestamps_ns) != expected_camera_keys:
                raise ValueError("frame camera timestamp keys must exactly match cameras")
            if any(
                not _is_positive_int(timestamp)
                for timestamp in frame.camera_timestamps_ns.values()
            ):
                raise ValueError("camera timestamps must be positive integers")
            if (
                frame.camera_timestamps_ns[self.anchor_camera]
                != frame.anchor_timestamp_ns
            ):
                raise ValueError(
                    "anchor camera timestamp must exactly equal anchor_timestamp_ns"
                )

            if self.has_lidar:
                if not _is_positive_int(frame.lidar_timestamp_ns):
                    raise ValueError(
                        "lidar_timestamp_ns must be a positive integer when has_lidar is true"
                    )
            elif frame.lidar_timestamp_ns is not None:
                raise ValueError("lidar_timestamp_ns must be null when has_lidar is false")

        for record in self.camera_records:
            observed_max_sync_delta_ns = max(
                abs(
                    frame.camera_timestamps_ns[record.name]
                    - frame.anchor_timestamp_ns
                )
                for frame in self.frames
            )
            if record.max_sync_delta_ns != observed_max_sync_delta_ns:
                raise ValueError(
                    f"camera_records max_sync_delta_ns for {record.name!r} must equal "
                    f"observed value {observed_max_sync_delta_ns}"
                )

        if not _is_sha256(self.calibration_sha256):
            raise ValueError("calibration_sha256 must be a lowercase 64-hex SHA-256")
        if not self.source_artifacts:
            raise ValueError("source_artifacts must contain at least one artifact")
        artifact_paths: set[str] = set()
        for artifact in self.source_artifacts:
            if not isinstance(artifact, SourceArtifact):
                raise ValueError("source_artifacts entries must be SourceArtifact objects")
            if not _is_nonempty_string(artifact.path):
                raise ValueError("source_artifacts paths must be nonempty")
            if artifact.path in artifact_paths:
                raise ValueError("source_artifacts paths must be unique")
            artifact_paths.add(artifact.path)
            if not _is_sha256(artifact.sha256):
                raise ValueError("source_artifacts hashes must be lowercase 64-hex SHA-256")
            if not _is_int(artifact.size_bytes) or artifact.size_bytes < 0:
                raise ValueError("source_artifacts size_bytes must be a nonnegative integer")

        _validate_transform(self.coordinate_convention_transform)
        supported = _validate_azimuth_intervals(
            "supported_azimuth_deg", self.supported_azimuth_deg
        )
        honest_black = _validate_azimuth_intervals(
            "honest_black_azimuth_deg", self.honest_black_azimuth_deg
        )
        _validate_complete_azimuth_union((*supported, *honest_black))

        supported_width = sum(end - start for start, end in supported)
        if self.mode == "A" and supported_width <= 0.0:
            raise ValueError("A mode requires strictly positive supported azimuth width")
        if (
            supported_width < 360.0 - _AZIMUTH_TOLERANCE_DEG
            and not _is_nonempty_string(self.honest_black_mask_pattern)
        ):
            raise ValueError(
                "honest_black_mask_pattern is required when supported azimuth is less than 360"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset": self.dataset,
            "source_scene_id": self.source_scene_id,
            "output_log_id": self.output_log_id,
            "mode": self.mode,
            "cameras": list(self.cameras),
            "anchor_camera": self.anchor_camera,
            "source_frame_count": self.source_frame_count,
            "output_frame_count": self.output_frame_count,
            "source_frame_rate_hz": self.source_frame_rate_hz,
            "output_frame_rate_hz": self.output_frame_rate_hz,
            "camera_records": [
                {
                    "name": record.name,
                    "source_name": record.source_name,
                    "frame_count": record.frame_count,
                    "max_sync_delta_ns": record.max_sync_delta_ns,
                }
                for record in self.camera_records
            ],
            "frames": [
                {
                    "index": frame.index,
                    "anchor_timestamp_ns": frame.anchor_timestamp_ns,
                    "camera_timestamps_ns": dict(frame.camera_timestamps_ns),
                    "lidar_timestamp_ns": frame.lidar_timestamp_ns,
                }
                for frame in self.frames
            ],
            "calibration_sha256": self.calibration_sha256,
            "source_artifacts": [
                {
                    "path": artifact.path,
                    "sha256": artifact.sha256,
                    "size_bytes": artifact.size_bytes,
                }
                for artifact in self.source_artifacts
            ],
            "has_lidar": self.has_lidar,
            "has_ego_pose": self.has_ego_pose,
            "has_annotations": self.has_annotations,
            "real_mask_pattern": self.real_mask_pattern,
            "faithfill_mask_pattern": self.faithfill_mask_pattern,
            "honest_black_mask_pattern": self.honest_black_mask_pattern,
            "supported_azimuth_deg": [
                list(interval) for interval in self.supported_azimuth_deg
            ],
            "honest_black_azimuth_deg": [
                list(interval) for interval in self.honest_black_azimuth_deg
            ],
            "coordinate_convention_transform": [
                list(row) for row in self.coordinate_convention_transform
            ],
            "converter_git_commit": self.converter_git_commit,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConversionManifest:
        if not isinstance(data, Mapping):
            raise TypeError("manifest JSON root must be an object")

        expected_fields = {field.name for field in fields(cls)}
        actual_fields = set(data)
        missing = expected_fields - actual_fields
        unknown = actual_fields - expected_fields
        if missing or unknown:
            details = []
            if missing:
                details.append(f"missing fields: {sorted(missing)}")
            if unknown:
                details.append(f"unknown fields: {sorted(unknown)}")
            raise TypeError("invalid manifest fields (" + "; ".join(details) + ")")

        values = dict(data)
        values["cameras"] = tuple(values["cameras"])
        values["camera_records"] = tuple(
            CameraRecord(**record) for record in values["camera_records"]
        )
        values["frames"] = tuple(
            FrameRecord(
                **{
                    **frame,
                    "camera_timestamps_ns": dict(frame["camera_timestamps_ns"]),
                }
            )
            for frame in values["frames"]
        )
        values["source_artifacts"] = tuple(
            SourceArtifact(**artifact) for artifact in values["source_artifacts"]
        )
        values["supported_azimuth_deg"] = tuple(
            tuple(interval) for interval in values["supported_azimuth_deg"]
        )
        values["honest_black_azimuth_deg"] = tuple(
            tuple(interval) for interval in values["honest_black_azimuth_deg"]
        )
        values["coordinate_convention_transform"] = tuple(
            tuple(row) for row in values["coordinate_convention_transform"]
        )

        manifest = cls(**values)
        manifest.validate()
        return manifest

    def write_json(self, path: str | Path) -> None:
        self.validate()
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                dir=output_path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    self.to_dict(),
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                temporary_file.write("\n")
            temporary_path.replace(output_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @classmethod
    def read_json(cls, path: str | Path) -> ConversionManifest:
        with Path(path).open(encoding="utf-8") as manifest_file:
            data = json.load(manifest_file)
        return cls.from_dict(data)


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: object) -> bool:
    return _is_int(value) and value > 0


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _validate_created_at(value: str) -> None:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError("created_at must be a valid ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("created_at must include a timezone")


def _validate_positive_rate(name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and positive")


def _validate_transform(transform: tuple[tuple[float, ...], ...]) -> None:
    if len(transform) != 4 or any(len(row) != 4 for row in transform):
        raise ValueError("coordinate_convention_transform must be exactly 4x4")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for row in transform
        for value in row
    ):
        raise ValueError("coordinate_convention_transform must contain only finite numbers")
    if tuple(transform[3]) != (0, 0, 0, 1):
        raise ValueError("coordinate_convention_transform bottom row must be [0, 0, 0, 1]")

    rotation = tuple(tuple(row[column] for column in range(3)) for row in transform[:3])
    for left in range(3):
        for right in range(3):
            dot_product = sum(
                rotation[left][column] * rotation[right][column]
                for column in range(3)
            )
            expected = 1.0 if left == right else 0.0
            if abs(dot_product - expected) > _RIGID_TRANSFORM_TOLERANCE:
                raise ValueError(
                    "coordinate_convention_transform upper 3x3 must be orthonormal"
                )

    determinant = (
        rotation[0][0]
        * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
        - rotation[0][1]
        * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
        + rotation[0][2]
        * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
    )
    if abs(determinant - 1.0) > _RIGID_TRANSFORM_TOLERANCE:
        raise ValueError(
            "coordinate_convention_transform upper 3x3 determinant must be +1"
        )


def _validate_azimuth_intervals(
    name: str, intervals: tuple[tuple[float, float], ...]
) -> tuple[tuple[float, float], ...]:
    validated: list[tuple[float, float]] = []
    for interval in intervals:
        if len(interval) != 2:
            raise ValueError(f"{name} azimuth intervals must contain start and end")
        start, end = interval
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not 0 <= start < end <= 360
        ):
            raise ValueError(
                f"{name} azimuth intervals must satisfy 0 <= start < end <= 360"
            )
        validated.append((float(start), float(end)))
    return tuple(validated)


def _validate_complete_azimuth_union(
    intervals: tuple[tuple[float, float], ...],
) -> None:
    ordered = sorted(intervals)
    if not ordered or abs(ordered[0][0]) > _AZIMUTH_TOLERANCE_DEG:
        raise ValueError("azimuth union must begin at 0 without a gap")

    cursor = ordered[0][1]
    for start, end in ordered[1:]:
        if start > cursor + _AZIMUTH_TOLERANCE_DEG:
            raise ValueError("azimuth union must not contain gaps")
        if start < cursor - _AZIMUTH_TOLERANCE_DEG:
            raise ValueError("azimuth union must not contain overlaps")
        cursor = end
    if abs(cursor - 360.0) > _AZIMUTH_TOLERANCE_DEG:
        raise ValueError("azimuth union must end at 360 without a gap")
