from __future__ import annotations

import hashlib
import json
import warnings
import weakref
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest
from PIL import Image

import agent.db181_multids.nuscenes_adapter as nuscenes_adapter
from agent.db181_multids.contract import ConversionManifest
from agent.db181_multids.geometry import quaternion_wxyz_to_matrix, rotation_z_deg
from agent.db181_multids.io import sha256_file
from agent.db181_multids.nuscenes_adapter import (
    NUSCENES_CAMERA_MAP,
    convert_nuscenes_scene,
)
from waymo2panorama.data_io.av2_loader import AV2RingLoader


EXPECTED_CAMERA_MAP = (
    ("CAM_FRONT", "ring_front_center"),
    ("CAM_FRONT_LEFT", "ring_front_left"),
    ("CAM_BACK_LEFT", "ring_side_left"),
    ("CAM_BACK", "ring_rear"),
    ("CAM_BACK_RIGHT", "ring_side_right"),
    ("CAM_FRONT_RIGHT", "ring_front_right"),
)
METADATA_FILES = (
    "scene.json",
    "sample.json",
    "sample_data.json",
    "ego_pose.json",
    "calibrated_sensor.json",
    "sensor.json",
)
COMMIT = "991017fbeb51adf69077bce2038573584a7b274d"
CREATED_AT = "2026-07-30T12:00:00Z"


@pytest.fixture
def writable_test_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    scratch_root = repo_root / ".pytest_cache" / "db212_nuscenes_adapter"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="case-", dir=scratch_root) as temp_dir:
        yield Path(temp_dir)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _arrow_table(path: Path) -> pa.Table:
    with pa.memory_map(str(path), "r") as source:
        return ipc.open_file(source).read_all()


def _write_nuscenes(
    root: Path,
    *,
    asynchronous_12hz_boundary: bool = False,
) -> tuple[Path, Path]:
    source_root = root / "nuscenes"
    metadata_root = root / "metadata"
    source_root.mkdir()
    metadata_root.mkdir()

    anchor_us = [1_000_000, 2_000_000, 3_000_000]
    sample_tokens = [f"sample-{index}" for index in range(3)]
    samples = [
        {
            "token": token,
            "timestamp": timestamp,
            "scene_token": "scene-token",
            "prev": sample_tokens[index - 1] if index else "",
            "next": sample_tokens[index + 1] if index + 1 < len(sample_tokens) else "",
        }
        for index, (token, timestamp) in enumerate(zip(sample_tokens, anchor_us))
    ]
    scenes = [
        {
            "token": "scene-token",
            "name": "scene-0001",
            "first_sample_token": sample_tokens[0],
            "last_sample_token": sample_tokens[-1],
        }
    ]

    sensors: list[dict[str, object]] = []
    calibrations: list[dict[str, object]] = []
    sample_data: list[dict[str, object]] = []
    ego_by_timestamp: dict[int, str] = {}
    ego_poses: list[dict[str, object]] = []

    if asynchronous_12hz_boundary:
        camera_anchors = [1_000_000, 1_083_333, 1_166_666, 1_249_999]
        camera_times = {
            channel: list(camera_anchors) for channel, _ in EXPECTED_CAMERA_MAP
        }
        camera_times["CAM_BACK"] = [
            1_050_000,
            1_133_333,
            1_216_666,
        ]
    else:
        camera_times = {
            "CAM_FRONT": anchor_us,
            # Anchor 1 is exactly tied and must select 990000 (the lower timestamp).
            "CAM_FRONT_LEFT": [990_000, 1_010_000, 2_010_000, 3_010_000],
            "CAM_BACK_LEFT": [997_000, 1_997_000, 2_997_000],
            "CAM_BACK": [1_004_000, 2_004_000, 3_004_000],
            "CAM_BACK_RIGHT": [995_000, 1_995_000, 2_995_000],
            "CAM_FRONT_RIGHT": [1_006_000, 2_006_000, 3_006_000],
        }

    def ego_token(timestamp: int) -> str:
        existing = ego_by_timestamp.get(timestamp)
        if existing is not None:
            return existing
        token = f"ego-{timestamp}"
        ego_by_timestamp[timestamp] = token
        ego_poses.append(
            {
                "token": token,
                "timestamp": timestamp,
                "rotation": [1.0, 0.0, 0.0, 0.0],
                "translation": [timestamp / 1_000_000.0, timestamp / 2_000_000.0, 1.0],
            }
        )
        return token

    for camera_index, (channel, _) in enumerate(EXPECTED_CAMERA_MAP):
        sensor_token = f"sensor-{channel}"
        calibration_token = f"cal-{channel}"
        sensors.append({"token": sensor_token, "channel": channel, "modality": "camera"})
        calibrations.append(
            {
                "token": calibration_token,
                "sensor_token": sensor_token,
                "rotation": [1.0, 0.0, 0.0, 0.0],
                "translation": [float(camera_index), 0.25, 0.5],
                "camera_intrinsic": [
                    [100.0 + camera_index, 0.0, 2.0],
                    [0.0, 110.0 + camera_index, 1.5],
                    [0.0, 0.0, 1.0],
                ],
            }
        )
        timestamps = camera_times[channel]
        tokens = [f"sd-{channel}-{timestamp}" for timestamp in timestamps]
        for index, (timestamp, token) in enumerate(zip(timestamps, tokens)):
            filename = f"samples/{channel}/{token}.jpg"
            image_path = source_root / filename
            image_path.parent.mkdir(parents=True, exist_ok=True)
            pixels = np.full((3, 4, 3), 20 + camera_index + index, dtype=np.uint8)
            Image.fromarray(pixels).save(image_path)
            sample_index = min(range(3), key=lambda value: abs(anchor_us[value] - timestamp))
            sample_data.append(
                {
                    "token": token,
                    "sample_token": sample_tokens[sample_index],
                    "ego_pose_token": ego_token(timestamp),
                    "calibrated_sensor_token": calibration_token,
                    "timestamp": timestamp,
                    "fileformat": "jpg",
                    "is_key_frame": index == 0,
                    "height": 3,
                    "width": 4,
                    "filename": filename,
                    "prev": tokens[index - 1] if index else "",
                    "next": tokens[index + 1] if index + 1 < len(tokens) else "",
                }
            )

    lidar_sensor_token = "sensor-LIDAR_TOP"
    lidar_calibration_token = "cal-LIDAR_TOP"
    sensors.append(
        {"token": lidar_sensor_token, "channel": "LIDAR_TOP", "modality": "lidar"}
    )
    half = np.sqrt(0.5)
    calibrations.append(
        {
            "token": lidar_calibration_token,
            "sensor_token": lidar_sensor_token,
            "rotation": [half, 0.0, 0.0, half],
            "translation": [10.0, 1.0, 2.0],
            "camera_intrinsic": [],
        }
    )
    lidar_times = (
        list(camera_times["CAM_FRONT"])
        if asynchronous_12hz_boundary
        else [
            975_000,
            1_025_000,
            1_975_000,
            2_025_000,
            2_975_000,
            3_025_000,
        ]
    )
    lidar_tokens = [f"sd-LIDAR_TOP-{timestamp}" for timestamp in lidar_times]
    for index, (timestamp, token) in enumerate(zip(lidar_times, lidar_tokens)):
        filename = f"sweeps/LIDAR_TOP/{token}.bin"
        sweep_path = source_root / filename
        sweep_path.parent.mkdir(parents=True, exist_ok=True)
        np.asarray([[1.0, 2.0, 3.0, 0.25 + index, 5.0]], dtype=np.float32).tofile(
            sweep_path
        )
        sample_index = min(range(3), key=lambda value: abs(anchor_us[value] - timestamp))
        sample_data.append(
            {
                "token": token,
                "sample_token": sample_tokens[sample_index],
                "ego_pose_token": ego_token(timestamp),
                "calibrated_sensor_token": lidar_calibration_token,
                "timestamp": timestamp,
                "fileformat": "pcd",
                "is_key_frame": index % 2 == 0,
                "height": 0,
                "width": 0,
                "filename": filename,
                "prev": lidar_tokens[index - 1] if index else "",
                "next": lidar_tokens[index + 1] if index + 1 < len(lidar_tokens) else "",
            }
        )

    for filename, rows in (
        ("scene.json", scenes),
        ("sample.json", samples),
        ("sample_data.json", sample_data),
        ("ego_pose.json", ego_poses),
        ("calibrated_sensor.json", calibrations),
        ("sensor.json", sensors),
    ):
        _write_json(metadata_root / filename, rows)
    return source_root, metadata_root


def _convert(
    root: Path,
    *,
    scene_id: str = "scene-0001",
    output_log_id: str = "nuscenes-scene-0001",
    **kwargs: object,
) -> tuple[Path, ConversionManifest]:
    source_root, metadata_root = _write_nuscenes(root)
    return convert_nuscenes_scene(
        source_root,
        metadata_root,
        scene_id,
        root / "output",
        output_log_id,
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
        **kwargs,
    )


def test_three_anchor_scene_writes_reproducible_b_only_pseudo_av2(
    writable_test_dir: Path,
) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        output_dir, manifest = convert_nuscenes_scene(
            source_root,
            metadata_root,
            "scene-0001",
            writable_test_dir / "output",
            "nuscenes-scene-0001",
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )

    assert NUSCENES_CAMERA_MAP == EXPECTED_CAMERA_MAP
    assert manifest.cameras == tuple(pseudo for _, pseudo in EXPECTED_CAMERA_MAP)
    assert manifest.anchor_camera == "ring_front_center"
    assert manifest.dataset == "nuscenes"
    assert manifest.source_scene_id == "scene-token"
    assert manifest.output_log_id == "nuscenes-scene-0001"
    assert manifest.converter_git_commit == COMMIT
    assert manifest.created_at == CREATED_AT
    assert manifest.mode == "B"
    assert manifest.real_mask_pattern is None
    assert manifest.faithfill_mask_pattern is None
    assert manifest.honest_black_mask_pattern is None
    assert manifest.has_lidar and manifest.has_ego_pose
    assert not manifest.has_annotations
    assert manifest.supported_azimuth_deg == ((0.0, 360.0),)
    assert manifest.honest_black_azimuth_deg == ()
    assert manifest.coordinate_convention_transform == (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    assert manifest.source_frame_count == manifest.output_frame_count == 3
    assert manifest.frame_contract == "1+2"
    assert manifest.source_frame_rate_hz == pytest.approx(1.0)
    assert manifest.output_frame_rate_hz == pytest.approx(1.0)
    assert [frame.anchor_timestamp_ns for frame in manifest.frames] == [
        1_000_000_000,
        2_000_000_000,
        3_000_000_000,
    ]
    assert [
        frame.camera_timestamps_ns["ring_front_left"] for frame in manifest.frames
    ] == [990_000_000, 2_010_000_000, 3_010_000_000]
    assert [frame.lidar_timestamp_ns for frame in manifest.frames] == [
        975_000_000,
        1_975_000_000,
        2_975_000_000,
    ]
    records = {record.name: record for record in manifest.camera_records}
    assert records["ring_front_left"].source_name == "CAM_FRONT_LEFT"
    assert records["ring_front_left"].max_sync_delta_ns == 10_000_000
    assert all(record.frame_count == 3 for record in manifest.camera_records)

    assert output_dir == writable_test_dir / "output" / "nuscenes-scene-0001"
    assert ConversionManifest.read_json(output_dir / "conversion_manifest.json") == manifest
    for camera in manifest.cameras:
        assert len(list((output_dir / "sensors" / "cameras" / camera).glob("*.jpg"))) == 3
    assert len(list((output_dir / "sensors" / "lidar").glob("*.feather"))) == 3

    loader = AV2RingLoader(output_dir, cameras=manifest.cameras)
    assert loader.num_anchor_frames() == 3
    assert tuple(loader.load_synced_frame(1_000_000_000).images) == manifest.cameras
    for camera_index, (_, pseudo_name) in enumerate(EXPECTED_CAMERA_MAP):
        calibration = loader.calibration(pseudo_name)
        np.testing.assert_allclose(
            calibration.T_ego_cam[:3, 3],
            [float(camera_index), 0.25, 0.5],
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
        assert (calibration.image_width, calibration.image_height) == (4, 3)

    extrinsics = _arrow_table(
        output_dir / "calibration" / "egovehicle_SE3_sensor.feather"
    ).to_pandas()
    lidar_extrinsic = extrinsics.loc[extrinsics.sensor_name == "up_lidar"].iloc[0]
    np.testing.assert_allclose(
        quaternion_wxyz_to_matrix(
            [lidar_extrinsic.qw, lidar_extrinsic.qx, lidar_extrinsic.qy, lidar_extrinsic.qz]
        ),
        rotation_z_deg(90.0),
        atol=1e-12,
    )
    np.testing.assert_allclose(
        [lidar_extrinsic.tx_m, lidar_extrinsic.ty_m, lidar_extrinsic.tz_m],
        [10.0, 1.0, 2.0],
        atol=0.0,
    )
    lidar = _arrow_table(
        output_dir / "sensors" / "lidar" / "975000000.feather"
    ).to_pandas()
    assert list(lidar.columns) == ["x", "y", "z", "intensity"]
    assert all(dtype == np.dtype("float32") for dtype in lidar.dtypes)
    np.testing.assert_allclose(lidar.iloc[0].to_numpy(), [8.0, 2.0, 5.0, 0.25], atol=1e-6)

    used_timestamps = sorted(
        {
            *(timestamp for frame in manifest.frames for timestamp in frame.camera_timestamps_ns.values()),
            *(frame.lidar_timestamp_ns for frame in manifest.frames),
        }
    )
    city = _arrow_table(output_dir / "city_SE3_egovehicle.feather").to_pandas()
    assert city.timestamp_ns.tolist() == used_timestamps
    for row in city.itertuples(index=False):
        np.testing.assert_allclose(
            [row.tx_m, row.ty_m, row.tz_m],
            [row.timestamp_ns / 1_000_000_000.0, row.timestamp_ns / 2_000_000_000.0, 1.0],
            atol=0.0,
        )
    annotations = _arrow_table(output_dir / "annotations.feather")
    assert annotations.num_rows == 0
    assert annotations.schema.field("track_uuid").type == pa.string()
    assert annotations.schema.field("category").type == pa.string()

    artifacts = {artifact.path: artifact for artifact in manifest.source_artifacts}
    assert len(artifacts) == 28
    assert {f"metadata/{name}" for name in METADATA_FILES}.issubset(artifacts)
    for relative, artifact in artifacts.items():
        if relative.startswith("metadata/"):
            source = metadata_root / relative.removeprefix("metadata/")
        elif relative.startswith("data/"):
            source = source_root / relative.removeprefix("data/")
        else:
            assert relative.startswith("derived:nuscenes_temporal_alignment=")
            payload = relative.encode("utf-8")
            assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
            assert artifact.size_bytes == len(payload)
            continue
        assert artifact.sha256 == sha256_file(source)
        assert artifact.size_bytes == source.stat().st_size

    calibration_digest = hashlib.sha256()
    for path in sorted((output_dir / "calibration").glob("*.feather"), key=lambda value: value.name):
        calibration_digest.update(path.read_bytes())
    assert manifest.calibration_sha256 == calibration_digest.hexdigest()


def test_asynchronous_12hz_boundary_drops_one_anchor_instead_of_reusing_frame(
    writable_test_dir: Path,
) -> None:
    source_root, metadata_root = _write_nuscenes(
        writable_test_dir,
        asynchronous_12hz_boundary=True,
    )
    output_dir, manifest = convert_nuscenes_scene(
        source_root,
        metadata_root,
        "scene-0001",
        writable_test_dir / "output",
        "asynchronous-12hz",
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )

    assert manifest.source_frame_count == 4
    assert manifest.output_frame_count == 3
    assert [frame.anchor_timestamp_ns for frame in manifest.frames] == [
        1_083_333_000,
        1_166_666_000,
        1_249_999_000,
    ]
    rear_timestamps = [
        frame.camera_timestamps_ns["ring_rear"] for frame in manifest.frames
    ]
    assert rear_timestamps == [1_050_000_000, 1_133_333_000, 1_216_666_000]
    assert len(rear_timestamps) == len(set(rear_timestamps))
    for camera in manifest.cameras:
        timestamps = [frame.camera_timestamps_ns[camera] for frame in manifest.frames]
        assert all(right > left for left, right in zip(timestamps, timestamps[1:]))
    lidar_timestamps = [frame.lidar_timestamp_ns for frame in manifest.frames]
    assert all(
        right is not None and left is not None and right > left
        for left, right in zip(lidar_timestamps, lidar_timestamps[1:])
    )
    rear_record = next(
        record for record in manifest.camera_records if record.name == "ring_rear"
    )
    assert rear_record.frame_count == 3
    assert rear_record.max_sync_delta_ns == 33_333_000

    artifacts = {artifact.path: artifact for artifact in manifest.source_artifacts}
    selected_rear_paths = {
        "data/samples/CAM_BACK/sd-CAM_BACK-1050000.jpg",
        "data/samples/CAM_BACK/sd-CAM_BACK-1133333.jpg",
        "data/samples/CAM_BACK/sd-CAM_BACK-1216666.jpg",
    }
    assert selected_rear_paths.issubset(artifacts)
    assert "data/samples/CAM_FRONT/sd-CAM_FRONT-1000000.jpg" not in artifacts
    for relative in selected_rear_paths:
        source = source_root / relative.removeprefix("data/")
        assert artifacts[relative].sha256 == sha256_file(source)
        assert artifacts[relative].size_bytes == source.stat().st_size
    alignment_path = next(
        path
        for path in artifacts
        if path.startswith("derived:nuscenes_temporal_alignment=")
    )
    alignment = json.loads(alignment_path.split("=", maxsplit=1)[1])
    assert alignment == {
        "adapter_algorithm_version": "nuscenes_ordered_distinct_v2",
        "anchor_channel": "CAM_FRONT",
        "dropped_anchor_frame_count": 1,
        "matching_objective": "minimum_total_absolute_sync_delta_ns",
        "master_channel": "CAM_BACK",
        "output_frame_count": 3,
        "source_anchor_frame_count": 4,
        "source_channel_frame_counts": {
            "CAM_BACK": 3,
            "CAM_BACK_LEFT": 4,
            "CAM_BACK_RIGHT": 4,
            "CAM_FRONT": 4,
            "CAM_FRONT_LEFT": 4,
            "CAM_FRONT_RIGHT": 4,
            "LIDAR_TOP": 4,
        },
    }
    assert (
        ConversionManifest.read_json(output_dir / "conversion_manifest.json")
        == manifest
    )


def test_scene_token_is_accepted(writable_test_dir: Path) -> None:
    _, manifest = _convert(writable_test_dir, scene_id="scene-token")
    assert manifest.source_scene_id == "scene-token"


def test_a_mode_is_rejected_without_explicit_experimental_override(
    writable_test_dir: Path,
) -> None:
    with pytest.raises(ValueError, match="experimental"):
        _convert(
            writable_test_dir,
            mode="A",
            observed_real_fill_fraction=0.42,
            real_mask_pattern="render/**/*_real_mask.png",
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "A", "allow_experimental_a": True},
        {
            "mode": "A",
            "allow_experimental_a": True,
            "observed_real_fill_fraction": 0.42,
        },
        {
            "mode": "A",
            "allow_experimental_a": True,
            "real_mask_pattern": "render/**/*_real_mask.png",
        },
        {
            "mode": "A",
            "allow_experimental_a": True,
            "observed_real_fill_fraction": float("nan"),
            "real_mask_pattern": "render/**/*_real_mask.png",
        },
        {
            "mode": "A",
            "allow_experimental_a": True,
            "observed_real_fill_fraction": -0.01,
            "real_mask_pattern": "render/**/*_real_mask.png",
        },
        {
            "mode": "A",
            "allow_experimental_a": True,
            "observed_real_fill_fraction": 1.01,
            "real_mask_pattern": "render/**/*_real_mask.png",
        },
        {
            "mode": "A",
            "allow_experimental_a": True,
            "observed_real_fill_fraction": 0.42,
            "real_mask_pattern": " ",
        },
        {
            "mode": "A",
            "allow_experimental_a": 1,
            "observed_real_fill_fraction": 0.42,
            "real_mask_pattern": "render/**/*_real_mask.png",
        },
    ],
)
def test_experimental_a_requires_complete_bounded_evidence(
    writable_test_dir: Path,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="fraction|real_mask_pattern|experimental"):
        _convert(writable_test_dir, **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"allow_experimental_a": True},
        {"observed_real_fill_fraction": 0.42},
        {"real_mask_pattern": "render/**/*_real_mask.png"},
    ],
)
def test_b_mode_rejects_a_only_evidence_parameters(
    writable_test_dir: Path,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="B mode"):
        _convert(writable_test_dir, **kwargs)


def test_experimental_a_evidence_is_canonical_and_never_claims_validation(
    writable_test_dir: Path,
) -> None:
    output_dir, manifest = _convert(
        writable_test_dir,
        mode="A",
        allow_experimental_a=True,
        observed_real_fill_fraction=0.42,
        real_mask_pattern="render/**/*_real_mask.png",
    )
    payload = {
        "adapter_algorithm_version": "nuscenes_pseudo_av2_v1",
        "observed_real_fill_fraction": 0.42,
        "real_mask_pattern": "render/**/*_real_mask.png",
        "status": "experimental_not_a_ready",
    }
    descriptor = "derived:nuscenes_experimental_a_evidence=" + json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert manifest.mode == "A"
    assert manifest.real_mask_pattern == "render/**/*_real_mask.png"
    artifact = next(
        value
        for value in manifest.source_artifacts
        if value.path.startswith("derived:nuscenes_experimental_a_evidence=")
    )
    assert artifact.path == descriptor
    assert artifact.sha256 == hashlib.sha256(descriptor.encode("utf-8")).hexdigest()
    assert artifact.size_bytes == len(descriptor.encode("utf-8"))
    assert "validated" not in descriptor.lower()
    assert ConversionManifest.read_json(output_dir / "conversion_manifest.json") == manifest


def _assert_preflight_failure(
    root: Path,
    error: type[BaseException] | tuple[type[BaseException], ...],
    match: str,
) -> None:
    source_root = root / "nuscenes"
    metadata_root = root / "metadata"
    output_root = root / "output"
    with pytest.raises(error, match=match):
        convert_nuscenes_scene(
            source_root,
            metadata_root,
            "scene-0001",
            output_root,
            "bad-scene",
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )
    assert not output_root.exists()


@pytest.mark.parametrize("scene_id", ["missing-scene", "ambiguous"])
def test_scene_selection_rejects_missing_or_ambiguous_identifier(
    writable_test_dir: Path,
    scene_id: str,
) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    if scene_id == "ambiguous":
        scenes = _load_json(metadata_root / "scene.json")
        assert isinstance(scenes, list)
        scenes.append(
            {
                "token": "ambiguous",
                "name": "other-name",
                "first_sample_token": "sample-0",
                "last_sample_token": "sample-2",
            }
        )
        scenes[0]["name"] = "ambiguous"
        _write_json(metadata_root / "scene.json", scenes)
    with pytest.raises(ValueError, match="exactly one"):
        convert_nuscenes_scene(
            source_root,
            metadata_root,
            scene_id,
            writable_test_dir / "output",
            "bad-scene-id",
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )


def test_missing_required_channel_fails_before_output_root(writable_test_dir: Path) -> None:
    _, metadata_root = _write_nuscenes(writable_test_dir)
    rows = _load_json(metadata_root / "sample_data.json")
    assert isinstance(rows, list)
    rows = [row for row in rows if "LIDAR_TOP" not in row["token"]]
    _write_json(metadata_root / "sample_data.json", rows)
    _assert_preflight_failure(writable_test_dir, ValueError, "LIDAR_TOP")


@pytest.mark.parametrize("mutation", ["missing_token", "dangling_ego", "dangling_sample", "dangling_next"])
def test_missing_or_dangling_rows_are_rejected_before_output(
    writable_test_dir: Path,
    mutation: str,
) -> None:
    _, metadata_root = _write_nuscenes(writable_test_dir)
    rows = _load_json(metadata_root / "sample_data.json")
    assert isinstance(rows, list)
    target = next(row for row in rows if row["token"] == "sd-CAM_FRONT_LEFT-1010000")
    if mutation == "missing_token":
        del target["token"]
    elif mutation == "dangling_ego":
        target["ego_pose_token"] = "missing-ego"
    elif mutation == "dangling_sample":
        target["sample_token"] = "missing-sample"
    else:
        target["next"] = "missing-sample-data"
    _write_json(metadata_root / "sample_data.json", rows)
    _assert_preflight_failure(writable_test_dir, ValueError, "token|dangling")


def test_duplicate_channel_timestamp_is_rejected_before_output(writable_test_dir: Path) -> None:
    _, metadata_root = _write_nuscenes(writable_test_dir)
    rows = _load_json(metadata_root / "sample_data.json")
    assert isinstance(rows, list)
    camera_rows = [row for row in rows if "CAM_BACK-" in row["token"]]
    camera_rows[1]["timestamp"] = camera_rows[0]["timestamp"]
    _write_json(metadata_root / "sample_data.json", rows)
    _assert_preflight_failure(writable_test_dir, ValueError, "strictly increasing")


def test_single_source_frame_reduces_output_instead_of_padding_or_reuse(
    writable_test_dir: Path,
) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    rows = _load_json(metadata_root / "sample_data.json")
    assert isinstance(rows, list)
    kept_one = False
    filtered = []
    for row in rows:
        if "CAM_BACK_RIGHT" not in row["token"]:
            filtered.append(row)
        elif not kept_one:
            row["prev"] = ""
            row["next"] = ""
            filtered.append(row)
            kept_one = True
    _write_json(metadata_root / "sample_data.json", filtered)
    output_dir, manifest = convert_nuscenes_scene(
        source_root,
        metadata_root,
        "scene-0001",
        writable_test_dir / "output",
        "one-shared-frame",
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )

    assert manifest.source_frame_count == 3
    assert manifest.output_frame_count == 1
    assert len(manifest.frames) == 1
    selected_timestamp = manifest.frames[0].camera_timestamps_ns["ring_side_right"]
    assert selected_timestamp == 995_000_000
    selected_images = output_dir / "sensors" / "cameras" / "ring_side_right"
    assert len(list(selected_images.glob("*.jpg"))) == 1


def test_bad_selected_jpeg_is_rejected_before_output(writable_test_dir: Path) -> None:
    source_root, _ = _write_nuscenes(writable_test_dir)
    (source_root / "samples" / "CAM_BACK" / "sd-CAM_BACK-2004000.jpg").write_bytes(
        b"not a jpeg"
    )
    _assert_preflight_failure(writable_test_dir, ValueError, "JPEG")


def test_camera_dimensions_drift_is_rejected_before_output(writable_test_dir: Path) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    path = source_root / "samples" / "CAM_BACK" / "sd-CAM_BACK-2004000.jpg"
    Image.fromarray(np.zeros((3, 5, 3), dtype=np.uint8)).save(path)
    rows = _load_json(metadata_root / "sample_data.json")
    assert isinstance(rows, list)
    target = next(row for row in rows if row["token"] == "sd-CAM_BACK-2004000")
    target["width"] = 5
    _write_json(metadata_root / "sample_data.json", rows)
    _assert_preflight_failure(writable_test_dir, ValueError, "dimensions")


@pytest.mark.parametrize("bad_lidar", ["shape", "nonfinite"])
def test_bad_lidar_is_rejected_before_output(
    writable_test_dir: Path,
    bad_lidar: str,
) -> None:
    source_root, _ = _write_nuscenes(writable_test_dir)
    path = source_root / "sweeps" / "LIDAR_TOP" / "sd-LIDAR_TOP-1975000.bin"
    if bad_lidar == "shape":
        np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float32).tofile(path)
    else:
        np.asarray([[1.0, 2.0, np.nan, 0.25, 5.0]], dtype=np.float32).tofile(path)
    _assert_preflight_failure(writable_test_dir, ValueError, "Nx5|non-finite")


def test_unselected_sample_data_calibration_drift_is_rejected(
    writable_test_dir: Path,
) -> None:
    _, metadata_root = _write_nuscenes(writable_test_dir)
    calibrations = _load_json(metadata_root / "calibrated_sensor.json")
    sample_data = _load_json(metadata_root / "sample_data.json")
    assert isinstance(calibrations, list) and isinstance(sample_data, list)
    original = next(row for row in calibrations if row["token"] == "cal-CAM_FRONT_LEFT")
    drifted = dict(original)
    drifted["token"] = "cal-CAM_FRONT_LEFT-drift"
    drifted["translation"] = [99.0, 0.25, 0.5]
    calibrations.append(drifted)
    target = next(row for row in sample_data if row["token"] == "sd-CAM_FRONT_LEFT-1010000")
    target["calibrated_sensor_token"] = drifted["token"]
    _write_json(metadata_root / "calibrated_sensor.json", calibrations)
    _write_json(metadata_root / "sample_data.json", sample_data)
    _assert_preflight_failure(writable_test_dir, ValueError, "calibration drift")


def test_conflicting_ego_pose_at_same_timestamp_is_rejected(
    writable_test_dir: Path,
) -> None:
    _, metadata_root = _write_nuscenes(writable_test_dir)
    sample_data = _load_json(metadata_root / "sample_data.json")
    ego_poses = _load_json(metadata_root / "ego_pose.json")
    assert isinstance(sample_data, list) and isinstance(ego_poses, list)
    target = next(row for row in sample_data if row["token"] == "sd-CAM_BACK-1004000")
    target["timestamp"] = 1_000_000
    target["ego_pose_token"] = "ego-conflict"
    ego_poses.append(
        {
            "token": "ego-conflict",
            "timestamp": 1_000_000,
            "rotation": [1.0, 0.0, 0.0, 0.0],
            "translation": [999.0, 0.0, 0.0],
        }
    )
    _write_json(metadata_root / "sample_data.json", sample_data)
    _write_json(metadata_root / "ego_pose.json", ego_poses)
    _assert_preflight_failure(writable_test_dir, ValueError, "conflicting ego poses")


def test_existing_final_is_preserved_untouched(writable_test_dir: Path) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    final = writable_test_dir / "output" / "existing"
    final.mkdir(parents=True)
    marker = final / "keep.txt"
    marker.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        convert_nuscenes_scene(
            source_root,
            metadata_root,
            "scene-0001",
            final.parent,
            final.name,
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )
    assert marker.read_text(encoding="utf-8") == "original"
    assert [path.name for path in final.iterdir()] == ["keep.txt"]


def test_mid_write_failure_cleans_only_private_staging(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    output_root = writable_test_dir / "output"
    output_root.mkdir()
    sibling = output_root / "unrelated"
    sibling.mkdir()
    marker = sibling / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    real_write_feather = nuscenes_adapter.write_feather
    calls = 0

    def fail_second_write(frame: object, path: str | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected write fault")
        real_write_feather(frame, path)  # type: ignore[arg-type]

    monkeypatch.setattr(nuscenes_adapter, "write_feather", fail_second_write)
    with pytest.raises(OSError, match="injected write fault"):
        convert_nuscenes_scene(
            source_root,
            metadata_root,
            "scene-0001",
            output_root,
            "fault",
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (output_root / "fault").exists()
    assert not list(output_root.glob(".fault.staging-*"))


def test_source_replacement_after_preflight_fails_without_publication(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    output_root = writable_test_dir / "output"
    real_materialize = nuscenes_adapter.materialize_file
    replaced = False

    def replace_before_copy(
        src: str | Path,
        dst: str | Path,
        prefer_hardlink: bool = True,
    ) -> str:
        nonlocal replaced
        source = Path(src)
        if not replaced and source.name == "sd-CAM_FRONT-2000000.jpg":
            replaced = True
            Image.fromarray(np.full((3, 4, 3), 254, dtype=np.uint8)).save(source)
        return real_materialize(src, dst, prefer_hardlink=prefer_hardlink)

    monkeypatch.setattr(nuscenes_adapter, "materialize_file", replace_before_copy)
    with pytest.raises(ValueError, match="snapshot|changed"):
        convert_nuscenes_scene(
            source_root,
            metadata_root,
            "scene-0001",
            output_root,
            "source-race",
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )
    assert replaced
    assert not (output_root / "source-race").exists()
    assert not list(output_root.glob(".source-race.staging-*"))


def test_metadata_replacement_during_parse_fails_before_output(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, metadata_root = _write_nuscenes(writable_test_dir)
    real_read = nuscenes_adapter._read_json_list
    changed = False

    def change_after_read(path: Path, table_name: str) -> list[dict[str, object]]:
        nonlocal changed
        result = real_read(path, table_name)
        if not changed:
            changed = True
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return result

    monkeypatch.setattr(nuscenes_adapter, "_read_json_list", change_after_read)
    _assert_preflight_failure(writable_test_dir, ValueError, "changed")


def test_corrupted_nonfirst_staged_jpeg_cannot_publish(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    output_root = writable_test_dir / "output"
    real_write_annotations = nuscenes_adapter.write_empty_annotations

    def corrupt_after_materialization(path: str | Path) -> None:
        real_write_annotations(path)
        staging = Path(path).parent
        images = sorted(
            (staging / "sensors" / "cameras" / "ring_front_center").glob("*.jpg")
        )
        images[1].write_bytes(b"corrupted staged jpeg")

    monkeypatch.setattr(
        nuscenes_adapter,
        "write_empty_annotations",
        corrupt_after_materialization,
    )
    with pytest.raises((OSError, ValueError), match="JPEG|changed|image"):
        convert_nuscenes_scene(
            source_root,
            metadata_root,
            "scene-0001",
            output_root,
            "corrupt-staged",
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )
    assert not (output_root / "corrupt-staged").exists()
    assert not list(output_root.glob(".corrupt-staged.staging-*"))


def test_published_camera_files_are_private_source_snapshots(
    writable_test_dir: Path,
) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    output_dir, manifest = convert_nuscenes_scene(
        source_root,
        metadata_root,
        "scene-0001",
        writable_test_dir / "output",
        "private-copy",
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )
    artifact_by_path = {artifact.path: artifact for artifact in manifest.source_artifacts}
    output_hashes: dict[Path, str] = {}
    selected_by_channel = {
        source_name: [
            frame.camera_timestamps_ns[pseudo_name] // 1000 for frame in manifest.frames
        ]
        for source_name, pseudo_name in EXPECTED_CAMERA_MAP
    }
    for source_name, pseudo_name in EXPECTED_CAMERA_MAP:
        for timestamp_us in selected_by_channel[source_name]:
            source_relative = f"samples/{source_name}/sd-{source_name}-{timestamp_us}.jpg"
            output_image = (
                output_dir
                / "sensors"
                / "cameras"
                / pseudo_name
                / f"{timestamp_us * 1000}.jpg"
            )
            output_hashes[output_image] = sha256_file(output_image)
            assert artifact_by_path[f"data/{source_relative}"].sha256 == output_hashes[
                output_image
            ]
    changed_source = source_root / "samples" / "CAM_FRONT" / "sd-CAM_FRONT-1000000.jpg"
    Image.fromarray(np.full((3, 4, 3), 255, dtype=np.uint8)).save(changed_source)
    assert all(sha256_file(path) == digest for path, digest in output_hashes.items())


def test_lidar_preflight_and_conversion_are_streamed_with_constant_live_payloads(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    real_read_lidar = nuscenes_adapter._read_lidar_bin
    live = 0
    peak = 0
    read_counts: dict[str, int] = {}

    def tracked_read(path: Path) -> np.ndarray:
        nonlocal live, peak
        points = real_read_lidar(path)
        read_counts[path.name] = read_counts.get(path.name, 0) + 1
        live += 1
        peak = max(peak, live)

        def release() -> None:
            nonlocal live
            live -= 1

        weakref.finalize(points, release)
        return points

    monkeypatch.setattr(nuscenes_adapter, "_read_lidar_bin", tracked_read)
    convert_nuscenes_scene(
        source_root,
        metadata_root,
        "scene-0001",
        writable_test_dir / "output",
        "constant-memory",
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )
    assert peak <= 2
    assert read_counts == {
        "sd-LIDAR_TOP-975000.bin": 2,
        "sd-LIDAR_TOP-1975000.bin": 2,
        "sd-LIDAR_TOP-2975000.bin": 2,
    }


@pytest.mark.parametrize(
    "mutation",
    ["camera_fileformat", "lidar_fileformat", "is_key_frame", "lidar_width"],
)
def test_official_sample_data_shape_and_fileformat_are_required(
    writable_test_dir: Path,
    mutation: str,
) -> None:
    _, metadata_root = _write_nuscenes(writable_test_dir)
    rows = _load_json(metadata_root / "sample_data.json")
    assert isinstance(rows, list)
    if mutation == "camera_fileformat":
        target = next(row for row in rows if row["token"] == "sd-CAM_FRONT-1000000")
        target["fileformat"] = "png"
    elif mutation == "lidar_fileformat":
        target = next(row for row in rows if row["token"] == "sd-LIDAR_TOP-975000")
        target["fileformat"] = "jpg"
    else:
        if mutation == "is_key_frame":
            target = next(row for row in rows if row["token"] == "sd-CAM_FRONT-1000000")
            del target["is_key_frame"]
        else:
            target = next(row for row in rows if row["token"] == "sd-LIDAR_TOP-975000")
            del target["width"]
    _write_json(metadata_root / "sample_data.json", rows)
    _assert_preflight_failure(
        writable_test_dir,
        ValueError,
        "fileformat|is_key_frame|width|dimensions",
    )


def test_output_log_id_cannot_escape_output_root(writable_test_dir: Path) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    output_root = writable_test_dir / "output"
    with pytest.raises(ValueError, match="output_log_id"):
        convert_nuscenes_scene(
            source_root,
            metadata_root,
            "scene-0001",
            output_root,
            "..",
            converter_git_commit=COMMIT,
            created_at=CREATED_AT,
        )
    assert not output_root.exists()


def test_calibration_feather_hash_is_reproducible_for_same_source(
    writable_test_dir: Path,
) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    first_dir, first = convert_nuscenes_scene(
        source_root,
        metadata_root,
        "scene-0001",
        writable_test_dir / "output",
        "first",
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )
    second_dir, second = convert_nuscenes_scene(
        source_root,
        metadata_root,
        "scene-0001",
        writable_test_dir / "output",
        "second",
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )
    assert first.calibration_sha256 == second.calibration_sha256
    for filename in ("intrinsics.feather", "egovehicle_SE3_sensor.feather"):
        assert (first_dir / "calibration" / filename).read_bytes() == (
            second_dir / "calibration" / filename
        ).read_bytes()


def test_official_radar_rows_are_indexed_but_not_consumed(writable_test_dir: Path) -> None:
    source_root, metadata_root = _write_nuscenes(writable_test_dir)
    sensors = _load_json(metadata_root / "sensor.json")
    calibrations = _load_json(metadata_root / "calibrated_sensor.json")
    sample_data = _load_json(metadata_root / "sample_data.json")
    assert isinstance(sensors, list)
    assert isinstance(calibrations, list)
    assert isinstance(sample_data, list)
    sensors.append(
        {"token": "sensor-RADAR_FRONT", "channel": "RADAR_FRONT", "modality": "radar"}
    )
    calibrations.append(
        {
            "token": "cal-RADAR_FRONT",
            "sensor_token": "sensor-RADAR_FRONT",
            "rotation": [1.0, 0.0, 0.0, 0.0],
            "translation": [0.0, 0.0, 0.0],
            "camera_intrinsic": [],
        }
    )
    radar_filename = "sweeps/RADAR_FRONT/radar.pcd"
    radar_path = source_root / radar_filename
    radar_path.parent.mkdir(parents=True)
    radar_path.write_bytes(b"unused radar payload")
    sample_data.append(
        {
            "token": "sd-RADAR_FRONT-1000000",
            "sample_token": "sample-0",
            "ego_pose_token": "ego-1000000",
            "calibrated_sensor_token": "cal-RADAR_FRONT",
            "timestamp": 1_000_000,
            "fileformat": "pcd",
            "is_key_frame": True,
            "height": 0,
            "width": 0,
            "filename": radar_filename,
            "prev": "",
            "next": "",
        }
    )
    _write_json(metadata_root / "sensor.json", sensors)
    _write_json(metadata_root / "calibrated_sensor.json", calibrations)
    _write_json(metadata_root / "sample_data.json", sample_data)

    output_dir, manifest = convert_nuscenes_scene(
        source_root,
        metadata_root,
        "scene-0001",
        writable_test_dir / "output",
        "with-radar",
        converter_git_commit=COMMIT,
        created_at=CREATED_AT,
    )
    assert output_dir.is_dir()
    assert "data/sweeps/RADAR_FRONT/radar.pcd" not in {
        artifact.path for artifact in manifest.source_artifacts
    }
