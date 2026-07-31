from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO

import pytest

import agent.db181_multids.contract as contract_module
from agent.db181_multids import (
    CameraRecord,
    ConversionManifest,
    FrameRecord,
    SourceArtifact,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _frames(*, count: int = 3, has_lidar: bool = True) -> tuple[FrameRecord, ...]:
    return tuple(
        FrameRecord(
            index=index,
            anchor_timestamp_ns=1_000 + index * 100,
            camera_timestamps_ns={
                "ring_front_center": 1_000 + index * 100,
                "ring_front_left": 1_010 + index * 100,
            },
            lidar_timestamp_ns=2_000 + index * 100 if has_lidar else None,
        )
        for index in range(count)
    )


def _manifest(**changes: object) -> ConversionManifest:
    manifest = ConversionManifest(
        schema_version="1.0",
        dataset="waymo_perception",
        source_scene_id="segment-001",
        output_log_id="pseudo-av2-segment-001",
        mode="A",
        cameras=("ring_front_center", "ring_front_left"),
        anchor_camera="ring_front_center",
        source_frame_count=3,
        output_frame_count=3,
        source_frame_rate_hz=10.0,
        output_frame_rate_hz=10.0,
        camera_records=(
            CameraRecord("ring_front_center", "FRONT", 3, 0),
            CameraRecord("ring_front_left", "FRONT_LEFT", 3, 10),
        ),
        frames=_frames(),
        calibration_sha256=SHA_A,
        source_artifacts=(SourceArtifact("source/segment.tfrecord", SHA_B, 123),),
        has_lidar=True,
        has_ego_pose=True,
        has_annotations=True,
        real_mask_pattern="masks/real/{index:06d}.png",
        faithfill_mask_pattern="masks/faithfill/{index:06d}.png",
        honest_black_mask_pattern="masks/honest_black/{index:06d}.png",
        supported_azimuth_deg=((0.0, 252.0),),
        honest_black_azimuth_deg=((252.0, 360.0),),
        coordinate_convention_transform=(
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        converter_git_commit="926136993f482c9fb720caff6f2209bf3001b8e2",
        created_at="2026-07-30T20:00:00-07:00",
    )
    return replace(manifest, **changes)


def _b_manifest() -> ConversionManifest:
    return _manifest(
        dataset="waymo_e2e",
        mode="B",
        frames=_frames(has_lidar=False),
        has_lidar=False,
        has_ego_pose=False,
        has_annotations=False,
        real_mask_pattern=None,
        faithfill_mask_pattern=None,
        honest_black_mask_pattern=None,
        supported_azimuth_deg=((0.0, 360.0),),
        honest_black_azimuth_deg=(),
    )


def _replace_frame(
    manifest: ConversionManifest, frame_position: int, **changes: object
) -> ConversionManifest:
    frames = list(manifest.frames)
    frames[frame_position] = replace(frames[frame_position], **changes)
    return replace(manifest, frames=tuple(frames))


def test_valid_a_roundtrip_is_stable_deterministic_and_atomic() -> None:
    manifest = _manifest()
    manifest.validate()
    assert manifest.frame_contract == "1+2"
    assert ConversionManifest.from_dict(manifest.to_dict()) == manifest

    with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
        temp_path = Path(temporary_directory)
        first_path = temp_path / "nested" / "manifest.json"
        second_path = temp_path / "second.json"
        manifest.write_json(first_path)
        first_text = first_path.read_text(encoding="utf-8")
        manifest.write_json(second_path)

        assert ConversionManifest.read_json(first_path) == manifest
        assert first_text == second_path.read_text(encoding="utf-8")
        assert first_text.endswith("\n")
        assert list(json.loads(first_text)) == sorted(json.loads(first_text))
        assert list(first_path.parent.iterdir()) == [first_path]


def test_frame_camera_timestamp_mapping_is_defensively_copied_and_immutable() -> None:
    timestamps = {"camera": 100}
    frame = FrameRecord(0, 100, timestamps, None)
    timestamps["camera"] = 200

    assert frame.camera_timestamps_ns["camera"] == 100
    with pytest.raises(TypeError):
        frame.camera_timestamps_ns["camera"] = 300  # type: ignore[index]


def test_direct_list_inputs_normalize_to_nested_immutable_tuples() -> None:
    base = _manifest()
    manifest = replace(
        base,
        cameras=list(base.cameras),
        camera_records=list(base.camera_records),
        frames=list(base.frames),
        source_artifacts=list(base.source_artifacts),
        supported_azimuth_deg=[list(interval) for interval in base.supported_azimuth_deg],
        honest_black_azimuth_deg=[
            list(interval) for interval in base.honest_black_azimuth_deg
        ],
        coordinate_convention_transform=[
            list(row) for row in base.coordinate_convention_transform
        ],
    )

    assert isinstance(manifest.cameras, tuple)
    assert isinstance(manifest.camera_records, tuple)
    assert isinstance(manifest.frames, tuple)
    assert isinstance(manifest.source_artifacts, tuple)
    assert isinstance(manifest.supported_azimuth_deg, tuple)
    assert all(isinstance(interval, tuple) for interval in manifest.supported_azimuth_deg)
    assert isinstance(manifest.honest_black_azimuth_deg, tuple)
    assert all(
        isinstance(interval, tuple) for interval in manifest.honest_black_azimuth_deg
    )
    assert isinstance(manifest.coordinate_convention_transform, tuple)
    assert all(
        isinstance(row, tuple) for row in manifest.coordinate_convention_transform
    )
    with pytest.raises(TypeError):
        manifest.coordinate_convention_transform[0][0] = 2.0  # type: ignore[index]


def test_atomic_write_preserves_destination_and_removes_temp_on_serialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_after_partial_write(
        _value: object, stream: TextIO, **_kwargs: object
    ) -> None:
        stream.write("partial")
        raise TypeError("serialization failed")

    monkeypatch.setattr(contract_module.json, "dump", fail_after_partial_write)
    with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
        directory = Path(temporary_directory)
        destination = directory / "manifest.json"
        destination.write_text("original\n", encoding="utf-8")

        with pytest.raises(TypeError, match="serialization failed"):
            _manifest().write_json(destination)

        assert destination.read_text(encoding="utf-8") == "original\n"
        assert list(directory.iterdir()) == [destination]


def test_atomic_write_preserves_destination_and_removes_temp_on_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(_path: Path, _target: str | Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(type(Path.cwd()), "replace", fail_replace)
    with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
        directory = Path(temporary_directory)
        destination = directory / "manifest.json"
        destination.write_text("original\n", encoding="utf-8")

        with pytest.raises(OSError, match="replace failed"):
            _manifest().write_json(destination)

        assert destination.read_text(encoding="utf-8") == "original\n"
        assert list(directory.iterdir()) == [destination]


def test_valid_b_may_omit_lidar_and_mask_patterns() -> None:
    manifest = _b_manifest()
    manifest.validate()
    assert ConversionManifest.from_dict(manifest.to_dict()) == manifest


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        (field_name, invalid_value)
        for field_name in (
            "real_mask_pattern",
            "faithfill_mask_pattern",
            "honest_black_mask_pattern",
        )
        for invalid_value in (object(), 1, "", "   ")
    ],
)
def test_optional_mask_patterns_reject_non_strings_and_blanks_in_b_mode(
    field_name: str, invalid_value: object
) -> None:
    with pytest.raises(ValueError, match=field_name):
        replace(_b_manifest(), **{field_name: invalid_value}).validate()


def test_valid_optional_mask_pattern_strings_are_preserved() -> None:
    patterns = {
        "real_mask_pattern": " masks/real/{index}.png ",
        "faithfill_mask_pattern": " masks/faithfill/{index}.png ",
        "honest_black_mask_pattern": " masks/black/{index}.png ",
    }
    manifest = replace(_b_manifest(), **patterns)
    manifest.validate()
    assert manifest.real_mask_pattern == patterns["real_mask_pattern"]
    assert manifest.faithfill_mask_pattern == patterns["faithfill_mask_pattern"]
    assert manifest.honest_black_mask_pattern == patterns["honest_black_mask_pattern"]


@pytest.mark.parametrize(
    ("supported", "honest_black"),
    [
        (((0.0, 252.0),), ((252.0, 360.0),)),
        (((0.0, 360.0),), ()),
    ],
)
def test_waymo_partial_and_panda_full_azimuth_coverage_pass(
    supported: tuple[tuple[float, float], ...],
    honest_black: tuple[tuple[float, float], ...],
) -> None:
    manifest = _manifest(
        supported_azimuth_deg=supported,
        honest_black_azimuth_deg=honest_black,
        honest_black_mask_pattern=("masks/black/{index}.png" if honest_black else None),
    )
    manifest.validate()


@pytest.mark.parametrize(
    "changes",
    [
        {"has_lidar": False},
        {"real_mask_pattern": None},
        {"real_mask_pattern": ""},
    ],
)
def test_a_mode_requires_lidar_and_real_mask(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="A mode requires"):
        replace(_manifest(), **changes).validate()


def test_output_cannot_silently_pad_panda_80_frames_to_93() -> None:
    with pytest.raises(ValueError, match="silent frame padding"):
        _manifest(source_frame_count=80, output_frame_count=93).validate()


@pytest.mark.parametrize(
    "cameras",
    [(), ("ring_front_center", "ring_front_center"), ("ring_front_center", "")],
)
def test_camera_tuple_must_be_nonempty_unique_and_without_blanks(
    cameras: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="cameras"):
        _manifest(cameras=cameras).validate()


def test_anchor_must_be_first_ordered_camera() -> None:
    with pytest.raises(ValueError, match="anchor_camera"):
        _manifest(anchor_camera="ring_front_left").validate()


def test_camera_record_order_must_exactly_match_cameras() -> None:
    manifest = _manifest()
    with pytest.raises(ValueError, match="camera_records"):
        replace(manifest, camera_records=tuple(reversed(manifest.camera_records))).validate()


def test_malformed_camera_record_entry_has_contextual_error() -> None:
    manifest = _manifest()
    malformed_records = (object(), manifest.camera_records[1])
    with pytest.raises(ValueError, match="camera_records"):
        replace(manifest, camera_records=malformed_records).validate()


@pytest.mark.parametrize(
    "record",
    [
        CameraRecord("ring_front_center", "", 3, 0),
        CameraRecord("ring_front_center", "FRONT", 2, 0),
        CameraRecord("ring_front_center", "FRONT", 3.0, 0),
        CameraRecord("ring_front_center", "FRONT", True, 0),
        CameraRecord("ring_front_center", "FRONT", 3, -1),
    ],
)
def test_camera_record_fields_are_valid(record: CameraRecord) -> None:
    manifest = _manifest()
    records = (record, manifest.camera_records[1])
    with pytest.raises(ValueError, match="camera_records"):
        replace(manifest, camera_records=records).validate()


def test_camera_record_frame_count_rejects_bool_equal_to_output_count() -> None:
    manifest = _manifest(
        source_frame_count=1,
        output_frame_count=1,
        camera_records=(
            CameraRecord("ring_front_center", "FRONT", True, 0),
            CameraRecord("ring_front_left", "FRONT_LEFT", True, 0),
        ),
        frames=_frames(count=1),
    )
    with pytest.raises(ValueError, match="camera_records"):
        manifest.validate()


@pytest.mark.parametrize("declared_delta_ns", [9, 11])
def test_camera_record_max_sync_delta_must_exactly_match_observed_frames(
    declared_delta_ns: int,
) -> None:
    manifest = _manifest()
    records = (
        manifest.camera_records[0],
        replace(manifest.camera_records[1], max_sync_delta_ns=declared_delta_ns),
    )
    with pytest.raises(ValueError, match="max_sync_delta_ns"):
        replace(manifest, camera_records=records).validate()


def test_frame_count_must_match_output_count() -> None:
    with pytest.raises(ValueError, match="frames length"):
        replace(_manifest(), frames=_manifest().frames[:-1]).validate()


def test_frame_indices_must_be_exactly_ordered() -> None:
    manifest = _replace_frame(_manifest(), 1, index=2)
    with pytest.raises(ValueError, match="indices"):
        manifest.validate()


@pytest.mark.parametrize(
    ("frame_position", "index"),
    [(0, False), (1, True)],
)
def test_frame_indices_must_be_real_integers(
    frame_position: int, index: bool
) -> None:
    manifest = _replace_frame(_manifest(), frame_position, index=index)
    with pytest.raises(ValueError, match="indices"):
        manifest.validate()


def test_malformed_frame_entry_has_contextual_error() -> None:
    manifest = _manifest()
    malformed_frames = (object(), *manifest.frames[1:])
    with pytest.raises(ValueError, match="frames"):
        replace(manifest, frames=malformed_frames).validate()


@pytest.mark.parametrize(
    "camera_timestamps",
    [
        {"ring_front_center": 1_000},
        {
            "ring_front_center": 1_000,
            "ring_front_left": 1_010,
            "extra": 1_020,
        },
    ],
)
def test_frame_camera_keys_must_exactly_match_cameras(
    camera_timestamps: dict[str, int],
) -> None:
    manifest = _replace_frame(
        _manifest(), 0, camera_timestamps_ns=camera_timestamps
    )
    with pytest.raises(ValueError, match="camera timestamp keys"):
        manifest.validate()


def test_anchor_camera_timestamp_must_equal_anchor_timestamp() -> None:
    manifest = _replace_frame(
        _manifest(),
        0,
        camera_timestamps_ns={
            "ring_front_center": 1_001,
            "ring_front_left": 1_010,
        },
    )
    with pytest.raises(ValueError, match="anchor camera timestamp"):
        manifest.validate()


def test_anchor_timestamps_must_be_strictly_increasing() -> None:
    manifest = _replace_frame(_manifest(), 1, anchor_timestamp_ns=1_000)
    with pytest.raises(ValueError, match="strictly increasing"):
        manifest.validate()


@pytest.mark.parametrize(
    ("frame_index", "changes"),
    [
        (0, {"anchor_timestamp_ns": 0}),
        (
            0,
            {
                "camera_timestamps_ns": {
                    "ring_front_center": 1_000,
                    "ring_front_left": 0,
                }
            },
        ),
        (0, {"lidar_timestamp_ns": 0}),
        (0, {"anchor_timestamp_ns": 1.5}),
    ],
)
def test_frame_timestamps_must_be_positive_integers(
    frame_index: int, changes: dict[str, object]
) -> None:
    manifest = _replace_frame(_manifest(), frame_index, **changes)
    with pytest.raises(ValueError, match="timestamp"):
        manifest.validate()


def test_lidar_timestamp_is_required_when_manifest_has_lidar() -> None:
    manifest = _replace_frame(_manifest(), 0, lidar_timestamp_ns=None)
    with pytest.raises(ValueError, match="lidar_timestamp_ns"):
        manifest.validate()


def test_lidar_timestamp_is_forbidden_when_manifest_has_no_lidar() -> None:
    manifest = _replace_frame(_b_manifest(), 0, lidar_timestamp_ns=2_000)
    with pytest.raises(ValueError, match="lidar_timestamp_ns"):
        manifest.validate()


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "2.0"},
        {"dataset": ""},
        {"source_scene_id": ""},
        {"output_log_id": ""},
        {"converter_git_commit": ""},
        {"created_at": ""},
        {"mode": "C"},
        {"source_frame_count": 0},
        {"output_frame_count": 0},
        {"source_frame_rate_hz": 0.0},
        {"output_frame_rate_hz": float("inf")},
    ],
)
def test_manifest_core_fields_are_validated(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(_manifest(), **changes).validate()


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("has_lidar", 1),
        ("has_lidar", "true"),
        ("has_ego_pose", 0),
        ("has_ego_pose", "false"),
        ("has_annotations", 1),
        ("has_annotations", "true"),
    ],
)
def test_boolean_flags_from_json_must_be_actual_booleans(
    field_name: str, invalid_value: object
) -> None:
    data = _manifest().to_dict()
    data[field_name] = invalid_value

    with pytest.raises(ValueError, match=field_name):
        ConversionManifest.from_dict(data)


@pytest.mark.parametrize(
    "commit",
    ["not-a-commit", "ABCDEF0", "abcdeg0", "abcdef", "a" * 65],
)
def test_converter_git_commit_requires_lowercase_hex_7_to_64_chars(
    commit: str,
) -> None:
    with pytest.raises(ValueError, match="converter_git_commit"):
        replace(_manifest(), converter_git_commit=commit).validate()


@pytest.mark.parametrize("created_at", ["yesterday", "2026-07-30T12:00:00"])
def test_created_at_requires_parseable_timezone_aware_iso8601(created_at: str) -> None:
    with pytest.raises(ValueError, match="created_at"):
        replace(_manifest(), created_at=created_at).validate()


@pytest.mark.parametrize(
    ("commit", "created_at"),
    [
        ("abcdef0", "2026-07-30T12:00:00+00:00"),
        ("a" * 64, "2026-07-30T12:00:00Z"),
    ],
)
def test_valid_commit_and_timezone_aware_created_at_are_preserved(
    commit: str, created_at: str
) -> None:
    manifest = replace(
        _manifest(), converter_git_commit=commit, created_at=created_at
    )
    manifest.validate()
    assert manifest.converter_git_commit == commit
    assert manifest.created_at == created_at


@pytest.mark.parametrize(
    "changes",
    [
        {"calibration_sha256": "A" * 64},
        {"calibration_sha256": "a" * 63},
        {"calibration_sha256": "g" * 64},
        {"source_artifacts": ()},
        {"source_artifacts": (SourceArtifact("", SHA_B, 1),)},
        {"source_artifacts": (SourceArtifact("source.bin", "B" * 64, 1),)},
        {"source_artifacts": (SourceArtifact("source.bin", SHA_B, -1),)},
        {
            "source_artifacts": (
                SourceArtifact("same.bin", SHA_A, 1),
                SourceArtifact("same.bin", SHA_B, 2),
            )
        },
    ],
)
def test_hashes_and_source_provenance_are_validated(
    changes: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="SHA-256|source_artifacts"):
        replace(_manifest(), **changes).validate()


@pytest.mark.parametrize(
    "transform",
    [
        ((1.0, 0.0), (0.0, 1.0)),
        (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, float("nan"), 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0, 1.0),
        ),
    ],
)
def test_coordinate_transform_is_finite_4x4_with_homogeneous_bottom_row(
    transform: tuple[tuple[float, ...], ...]
) -> None:
    with pytest.raises(ValueError, match="coordinate_convention_transform"):
        _manifest(coordinate_convention_transform=transform).validate()


@pytest.mark.parametrize(
    "transform",
    [
        (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        (
            (2.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        (
            (1.0, 0.25, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        (
            (-1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
    ],
)
def test_coordinate_transform_rejects_non_rigid_rotation(
    transform: tuple[tuple[float, ...], ...]
) -> None:
    with pytest.raises(ValueError, match="coordinate_convention_transform"):
        _manifest(coordinate_convention_transform=transform).validate()


@pytest.mark.parametrize(
    "transform",
    [
        (
            (0.0, -1.0, 0.0, 12.5),
            (1.0, 0.0, 0.0, -3.0),
            (0.0, 0.0, 1.0, 0.25),
            (0.0, 0.0, 0.0, 1.0),
        ),
        (
            (1.0, 0.0, 0.0, -100.0),
            (0.0, 0.0, -1.0, 5.0),
            (0.0, 1.0, 0.0, 2.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
    ],
)
def test_coordinate_transform_accepts_rigid_rotation_and_translation(
    transform: tuple[tuple[float, ...], ...]
) -> None:
    _manifest(coordinate_convention_transform=transform).validate()


@pytest.mark.parametrize(
    ("supported", "honest_black"),
    [
        (((0.0, 250.0),), ((252.0, 360.0),)),
        (((0.0, 253.0),), ((252.0, 360.0),)),
        (((-1.0, 252.0),), ((252.0, 360.0),)),
        (((0.0, 252.0),), ((252.0, 361.0),)),
        (((0.0, 252.0),), ((360.0, 252.0),)),
    ],
)
def test_azimuth_intervals_reject_gaps_overlaps_and_invalid_ranges(
    supported: tuple[tuple[float, float], ...],
    honest_black: tuple[tuple[float, float], ...],
) -> None:
    with pytest.raises(ValueError, match="azimuth"):
        _manifest(
            supported_azimuth_deg=supported,
            honest_black_azimuth_deg=honest_black,
        ).validate()


def test_partial_supported_azimuth_requires_honest_black_mask() -> None:
    with pytest.raises(ValueError, match="honest_black_mask_pattern"):
        _manifest(honest_black_mask_pattern=None).validate()


def test_a_mode_requires_positive_supported_azimuth_width() -> None:
    manifest = _manifest(
        supported_azimuth_deg=(),
        honest_black_azimuth_deg=((0.0, 360.0),),
    )
    with pytest.raises(ValueError, match="A mode requires.*supported azimuth"):
        manifest.validate()


def test_b_mode_may_be_fully_honest_black() -> None:
    manifest = replace(
        _b_manifest(),
        supported_azimuth_deg=(),
        honest_black_azimuth_deg=((0.0, 360.0),),
        honest_black_mask_pattern="masks/black/{index}.png",
    )
    manifest.validate()


def test_from_dict_rejects_missing_and_unknown_fields() -> None:
    data = _manifest().to_dict()
    missing = dict(data)
    missing.pop("dataset")
    unknown = {**data, "unknown": True}

    with pytest.raises((TypeError, ValueError)):
        ConversionManifest.from_dict(missing)
    with pytest.raises((TypeError, ValueError)):
        ConversionManifest.from_dict(unknown)


def test_read_json_reconstructs_tuples_then_validates() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
        path = Path(temporary_directory) / "invalid.json"
        data = _manifest().to_dict()
        data["schema_version"] = "2.0"
        path.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(ValueError, match="schema_version"):
            ConversionManifest.read_json(path)


def test_valid_pandaset_six_camera_manifest_preserves_all_80_frames() -> None:
    cameras = (
        "ring_front_center",
        "ring_front_left",
        "ring_side_left",
        "ring_rear",
        "ring_side_right",
        "ring_front_right",
    )
    source_names = (
        "front_camera",
        "front_left_camera",
        "left_camera",
        "back_camera",
        "right_camera",
        "front_right_camera",
    )
    manifest = ConversionManifest(
        schema_version="1.0",
        dataset="pandaset",
        source_scene_id="pandaset-001",
        output_log_id="pseudo-av2-pandaset-001",
        mode="A",
        cameras=cameras,
        anchor_camera=cameras[0],
        source_frame_count=80,
        output_frame_count=80,
        source_frame_rate_hz=10.0,
        output_frame_rate_hz=10.0,
        camera_records=tuple(
            CameraRecord(camera, source_name, 80, 0)
            for camera, source_name in zip(cameras, source_names, strict=True)
        ),
        frames=tuple(
            FrameRecord(
                index=index,
                anchor_timestamp_ns=1_000_000_000 + index * 100_000_000,
                camera_timestamps_ns={
                    camera: 1_000_000_000 + index * 100_000_000
                    for camera in cameras
                },
                lidar_timestamp_ns=1_000_000_001 + index * 100_000_000,
            )
            for index in range(80)
        ),
        calibration_sha256=SHA_A,
        source_artifacts=(SourceArtifact("pandaset/001.pkl.gz", SHA_B, 456),),
        has_lidar=True,
        has_ego_pose=True,
        has_annotations=True,
        real_mask_pattern="masks/real/{index:06d}.png",
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
        converter_git_commit="926136993f482c9fb720caff6f2209bf3001b8e2",
        created_at="2026-07-30T20:00:00-07:00",
    )

    manifest.validate()
    assert manifest.dataset == "pandaset"
    assert len(manifest.cameras) == 6
    assert manifest.frame_contract == "1+79"


@pytest.mark.parametrize(
    ("instance", "field_name", "new_value"),
    [
        (SourceArtifact("source.bin", SHA_A, 1), "path", "other.bin"),
        (CameraRecord("camera", "source", 1, 0), "name", "other"),
        (
            FrameRecord(0, 1, {"camera": 1}, None),
            "anchor_timestamp_ns",
            2,
        ),
        (_manifest(), "dataset", "other"),
    ],
)
def test_public_dataclasses_are_frozen(
    instance: object, field_name: str, new_value: object
) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(instance, field_name, new_value)
