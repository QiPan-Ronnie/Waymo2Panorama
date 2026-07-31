from __future__ import annotations

import hashlib
import json
import warnings
import weakref
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest
from PIL import Image

import agent.db181_multids.pandaset_adapter as pandaset_adapter
from agent.db181_multids.contract import ConversionManifest
from agent.db181_multids.geometry import (
    make_transform,
    matrix_to_quaternion_wxyz,
    quaternion_wxyz_to_matrix,
    rotation_z_deg,
)
from agent.db181_multids.io import sha256_file
from agent.db181_multids.pandaset_adapter import PANDASET_CAMERA_MAP, convert_pandaset_scene
from waymo2panorama.data_io.av2_loader import AV2RingLoader


EXPECTED_CAMERA_MAP = (
    ("front_camera", "ring_front_center"),
    ("front_left_camera", "ring_front_left"),
    ("left_camera", "ring_side_left"),
    ("back_camera", "ring_rear"),
    ("right_camera", "ring_side_right"),
    ("front_right_camera", "ring_front_right"),
)


def _pose(transform: np.ndarray) -> dict[str, dict[str, float]]:
    qw, qx, qy, qz = matrix_to_quaternion_wxyz(transform[:3, :3])
    return {
        "heading": {"w": qw, "x": qx, "y": qy, "z": qz},
        "position": {
            "x": float(transform[0, 3]),
            "y": float(transform[1, 3]),
            "z": float(transform[2, 3]),
        },
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_scene(root: Path, *, frame_count: int = 3) -> Path:
    scene = root / "019"
    if frame_count == 3:
        anchor_times = [1.1, 2.1, 3.1]
        lidar_times = [1.0, 2.0, 4.0]
    else:
        anchor_times = [10.0 + index * 0.1 for index in range(frame_count)]
        lidar_times = list(anchor_times)
    lidar_poses = [
        make_transform(np.eye(3), [0.0, 2.0 * (timestamp - lidar_times[0]), 1.0])
        for timestamp in lidar_times
    ]
    lidar_dir = scene / "lidar"
    lidar_dir.mkdir(parents=True)
    _write_json(lidar_dir / "timestamps.json", lidar_times)
    _write_json(lidar_dir / "poses.json", [_pose(value) for value in lidar_poses])
    normalized_rotation = rotation_z_deg(90.0)
    for index, transform in enumerate(lidar_poses):
        local = np.array([1.0, 2.0, -1.0])
        world = transform[:3, 3] + normalized_rotation @ local
        pd.DataFrame(
            {"x": [world[0]], "y": [world[1]], "z": [world[2]], "i": [0.25 + index]}
        ).to_pickle(lidar_dir / f"{index:02d}.pkl.gz", compression="gzip")

    for camera_index, (source_name, _) in enumerate(EXPECTED_CAMERA_MAP):
        camera_dir = scene / "camera" / source_name
        camera_dir.mkdir(parents=True)
        _write_json(
            camera_dir / "intrinsics.json",
            {"fx": 100.0 + camera_index, "fy": 110.0, "cx": 1.0, "cy": 0.5},
        )
        camera_times = list(anchor_times)
        if frame_count == 3 and source_name == "left_camera":
            camera_times = [1.09, 1.11, 3.11]
        elif frame_count == 3 and source_name != "front_camera":
            offset = 0.01 * ((camera_index % 3) - 1)
            camera_times = [timestamp + offset for timestamp in anchor_times]
        _write_json(camera_dir / "timestamps.json", camera_times)
        extrinsic = make_transform(np.eye(3), [camera_index + 1.0, 0.25, 0.5])
        camera_poses = []
        for timestamp in camera_times:
            world_ego = make_transform(
                normalized_rotation,
                [0.0, 2.0 * (timestamp - lidar_times[0]), 1.0],
            )
            camera_poses.append(_pose(world_ego @ extrinsic))
        _write_json(camera_dir / "poses.json", camera_poses)
        for frame_index in range(frame_count):
            shape = (1, 1, 3) if frame_count == 80 else (2, 3, 3)
            pixels = np.full(shape, 20 + camera_index + frame_index, dtype=np.uint8)
            Image.fromarray(pixels).save(camera_dir / f"{frame_index:02d}.jpg")
    return scene


@pytest.fixture
def writable_test_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    scratch_root = repo_root / ".pytest_cache" / "db212_pandaset_adapter"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="case-", dir=scratch_root) as temp_dir:
        yield Path(temp_dir)


def _arrow_table(path: Path) -> pa.Table:
    with pa.memory_map(str(path), "r") as source:
        return ipc.open_file(source).read_all()


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _set_camera_timeline(scene: Path, source_name: str, timestamps: list[float]) -> None:
    camera_dir = scene / "camera" / source_name
    _write_json(camera_dir / "timestamps.json", timestamps)
    camera_index = [name for name, _ in EXPECTED_CAMERA_MAP].index(source_name)
    extrinsic = make_transform(np.eye(3), [camera_index + 1.0, 0.25, 0.5])
    poses = []
    for timestamp in timestamps:
        world_ego = make_transform(
            rotation_z_deg(90.0),
            [0.0, 2.0 * (timestamp - 1.0), 1.0],
        )
        poses.append(_pose(world_ego @ extrinsic))
    _write_json(camera_dir / "poses.json", poses)


def _set_lidar_timeline(
    scene: Path,
    timestamps: list[float],
    *,
    first_local_points: list[tuple[float, float, float]] | None = None,
) -> None:
    lidar_dir = scene / "lidar"
    poses = [
        make_transform(np.eye(3), [0.0, 2.0 * (timestamp - 1.0), 1.0])
        for timestamp in timestamps
    ]
    _write_json(lidar_dir / "timestamps.json", timestamps)
    _write_json(lidar_dir / "poses.json", [_pose(value) for value in poses])
    normalized_rotation = rotation_z_deg(90.0)
    for index, transform in enumerate(poses):
        local_points = (
            first_local_points
            if index == 0 and first_local_points is not None
            else [(1.0, 2.0, -1.0)]
        )
        local = np.asarray(local_points, dtype=np.float64)
        world = transform[:3, 3] + (normalized_rotation @ local.T).T
        pd.DataFrame(
            {
                "x": world[:, 0],
                "y": world[:, 1],
                "z": world[:, 2],
                "i": np.arange(len(world), dtype=np.float64) + 0.25,
            }
        ).to_pickle(lidar_dir / f"{index:02d}.pkl.gz", compression="gzip")


def _expected_ground_descriptor(
    source_scene: Path,
    *,
    quantile: float,
    radius_m: float,
    shift_m: float,
) -> str:
    sweep_path = source_scene / "lidar" / "00.pkl.gz"
    payload = {
        "algorithm_version": "pandaset_ground_origin_v1",
        "ground_quantile": quantile,
        "ground_radius_m": radius_m,
        "near_field_rule": "hypot(x,y)<=ground_radius_m",
        "shift_m": shift_m,
        "source_sweep_path": "lidar/00.pkl.gz",
        "source_sweep_sha256": sha256_file(sweep_path),
    }
    return "derived:ego_ground_shift=" + json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_three_frame_scene_writes_complete_pseudo_av2_log(writable_test_dir: Path) -> None:
    source_scene = _write_scene(writable_test_dir)

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        output_dir, manifest = convert_pandaset_scene(
            source_scene,
            writable_test_dir / "output",
            "panda-019",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )

    assert PANDASET_CAMERA_MAP == EXPECTED_CAMERA_MAP
    assert manifest.cameras == tuple(pseudo for _, pseudo in EXPECTED_CAMERA_MAP)
    assert manifest.dataset == "pandaset"
    assert manifest.source_scene_id == "019"
    assert manifest.output_frame_count == manifest.source_frame_count == 3
    assert manifest.frame_contract == "1+2"
    assert manifest.mode == "A"
    assert manifest.real_mask_pattern == "render/**/*_real_mask.png"
    assert manifest.faithfill_mask_pattern is None
    assert manifest.honest_black_mask_pattern is None
    assert manifest.has_lidar and manifest.has_ego_pose
    assert not manifest.has_annotations
    assert manifest.supported_azimuth_deg == ((0.0, 360.0),)
    assert manifest.honest_black_azimuth_deg == ()
    assert manifest.source_frame_rate_hz == pytest.approx(1.0)
    assert manifest.output_frame_rate_hz == pytest.approx(1.0)
    assert output_dir.name == "panda-019"
    assert (output_dir / "conversion_manifest.json").is_file()
    assert ConversionManifest.read_json(output_dir / "conversion_manifest.json") == manifest
    for camera in manifest.cameras:
        assert len(list((output_dir / "sensors" / "cameras" / camera).glob("*.jpg"))) == 3

    transform = np.asarray(manifest.coordinate_convention_transform)
    np.testing.assert_allclose(transform[:3, :3], rotation_z_deg(90.0), atol=1e-12)
    np.testing.assert_allclose(transform[:3, 3], 0.0, atol=0.0)
    assert [frame.anchor_timestamp_ns for frame in manifest.frames] == [
        1_100_000_000,
        2_100_000_000,
        3_100_000_000,
    ]
    assert [frame.camera_timestamps_ns["ring_side_left"] for frame in manifest.frames] == [
        1_090_000_000,
        1_110_000_000,
        3_110_000_000,
    ]
    assert [frame.lidar_timestamp_ns for frame in manifest.frames] == [
        1_000_000_000,
        2_000_000_000,
        4_000_000_000,
    ]
    records = {record.name: record for record in manifest.camera_records}
    assert records["ring_side_left"].max_sync_delta_ns == 990_000_000

    city = _arrow_table(output_dir / "city_SE3_egovehicle.feather").to_pandas()
    assert city["timestamp_ns"].tolist() == sorted(city["timestamp_ns"].tolist())
    row = city.loc[city["timestamp_ns"] == 2_100_000_000].iloc[0]
    np.testing.assert_allclose([row.tx_m, row.ty_m, row.tz_m], [0.0, 2.2, 1.0], atol=1e-9)
    np.testing.assert_allclose(
        quaternion_wxyz_to_matrix([row.qw, row.qx, row.qy, row.qz]),
        rotation_z_deg(90.0),
        atol=1e-9,
    )

    loader = AV2RingLoader(output_dir, cameras=manifest.cameras)
    assert loader.num_anchor_frames() == 3
    sample = loader.load_synced_frame(manifest.frames[0].anchor_timestamp_ns)
    assert tuple(sample.images) == manifest.cameras
    for camera_index, (_, pseudo_name) in enumerate(EXPECTED_CAMERA_MAP):
        np.testing.assert_allclose(
            loader.calibration(pseudo_name).T_ego_cam,
            make_transform(np.eye(3), [camera_index + 1.0, 0.25, 0.5]),
            atol=1e-9,
        )

    extrinsics = _arrow_table(
        output_dir / "calibration" / "egovehicle_SE3_sensor.feather"
    ).to_pandas()
    lidar_calibration = extrinsics.loc[extrinsics["sensor_name"] == "up_lidar"].iloc[0]
    np.testing.assert_allclose(
        [lidar_calibration.qw, lidar_calibration.qx, lidar_calibration.qy, lidar_calibration.qz],
        [1.0, 0.0, 0.0, 0.0],
    )
    np.testing.assert_allclose([lidar_calibration.tx_m, lidar_calibration.ty_m, lidar_calibration.tz_m], 0.0)

    lidar = _arrow_table(
        output_dir / "sensors" / "lidar" / "1000000000.feather"
    ).to_pandas()
    assert list(lidar.columns) == ["x", "y", "z", "intensity"]
    assert all(dtype == np.dtype("float32") for dtype in lidar.dtypes)
    np.testing.assert_allclose(lidar.iloc[0].to_numpy(), [1.0, 2.0, -1.0, 0.25], atol=1e-6)

    annotations = _arrow_table(output_dir / "annotations.feather")
    assert annotations.num_rows == 0
    assert annotations.schema.field("track_uuid").type == pa.string()
    assert annotations.schema.field("category").type == pa.string()

    expected_files = sorted(path for path in source_scene.rglob("*") if path.is_file())
    artifact_by_path = {
        artifact.path: artifact
        for artifact in manifest.source_artifacts
        if not artifact.path.startswith("derived:")
    }
    assert set(artifact_by_path) == {
        path.relative_to(source_scene).as_posix() for path in expected_files
    }
    for path in expected_files:
        artifact = artifact_by_path[path.relative_to(source_scene).as_posix()]
        assert artifact.sha256 == sha256_file(path)
        assert artifact.size_bytes == path.stat().st_size

    alignment = [
        artifact
        for artifact in manifest.source_artifacts
        if artifact.path.startswith("derived:pandaset_temporal_alignment=")
    ]
    assert len(alignment) == 1
    assert '"adapter_algorithm_version":"pandaset_cadence_window_v3"' in alignment[0].path
    assert alignment[0].sha256 == hashlib.sha256(
        alignment[0].path.encode("utf-8")
    ).hexdigest()
    assert alignment[0].size_bytes == len(alignment[0].path.encode("utf-8"))

    digest = hashlib.sha256()
    calibration_files = sorted((output_dir / "calibration").glob("*.feather"), key=lambda path: path.name)
    for path in calibration_files:
        digest.update(path.read_bytes())
    assert manifest.calibration_sha256 == digest.hexdigest()


def test_b_mode_allows_no_real_mask_pattern(writable_test_dir: Path) -> None:
    source_scene = _write_scene(writable_test_dir)
    _, manifest = convert_pandaset_scene(
        source_scene,
        writable_test_dir / "output",
        "panda-b",
        mode="B",
        real_mask_pattern=None,
        converter_git_commit="0dcf6795",
        created_at="2026-07-30T12:00:00Z",
    )
    assert manifest.mode == "B"
    assert manifest.real_mask_pattern is None


def test_full_80_frame_scene_is_exactly_1_plus_79(writable_test_dir: Path) -> None:
    source_scene = _write_scene(writable_test_dir, frame_count=80)
    _, manifest = convert_pandaset_scene(
        source_scene,
        writable_test_dir / "output",
        "panda-80",
        converter_git_commit="0dcf6795",
        created_at="2026-07-30T12:00:00Z",
    )
    assert manifest.output_frame_count == 80
    assert manifest.frame_contract == "1+79"


def test_ground_origin_is_shifted_and_recorded_as_derived_provenance(
    writable_test_dir: Path,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    output_dir, manifest = convert_pandaset_scene(
        source_scene,
        writable_test_dir / "output",
        "panda-ground",
        ego_origin="ground",
        ground_quantile=0.05,
        ground_radius_m=10.0,
        converter_git_commit="0dcf6795",
        created_at="2026-07-30T12:00:00Z",
    )

    descriptor = _expected_ground_descriptor(
        source_scene,
        quantile=0.05,
        radius_m=10.0,
        shift_m=-1.0,
    )
    artifact = next(
        value
        for value in manifest.source_artifacts
        if value.path.startswith("derived:ego_ground_shift=")
    )
    assert artifact.path == descriptor
    assert artifact.sha256 == hashlib.sha256(descriptor.encode("utf-8")).hexdigest()
    assert artifact.size_bytes == len(descriptor.encode("utf-8"))
    city = _arrow_table(output_dir / "city_SE3_egovehicle.feather").to_pandas()
    np.testing.assert_allclose(city["tz_m"], 0.0, atol=1e-9)
    lidar = _arrow_table(
        output_dir / "sensors" / "lidar" / "1000000000.feather"
    ).to_pandas()
    assert float(lidar.iloc[0].z) == pytest.approx(0.0)


def test_ground_consumes_unselected_first_sweep_and_uses_euclidean_radius(
    writable_test_dir: Path,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    _set_lidar_timeline(
        source_scene,
        [0.5, 1.0, 2.0, 4.0],
        first_local_points=[(0.0, 0.0, -1.0), (9.0, 9.0, -5.0)],
    )
    first_sweep = source_scene / "lidar" / "00.pkl.gz"

    output_dir, manifest = convert_pandaset_scene(
        source_scene,
        writable_test_dir / "output",
        "ground-u",
        ego_origin="ground",
        ground_quantile=0.0,
        ground_radius_m=10.0,
        converter_git_commit="0dcf6795",
        created_at="2026-07-30T12:00:00Z",
    )

    assert [frame.lidar_timestamp_ns for frame in manifest.frames] == [
        1_000_000_000,
        2_000_000_000,
        4_000_000_000,
    ]
    assert not (output_dir / "sensors" / "lidar" / "500000000.feather").exists()
    artifact_by_path = {artifact.path: artifact for artifact in manifest.source_artifacts}
    assert artifact_by_path["lidar/00.pkl.gz"].sha256 == sha256_file(first_sweep)
    descriptor = _expected_ground_descriptor(
        source_scene,
        quantile=0.0,
        radius_m=10.0,
        shift_m=-1.0,
    )
    derived = artifact_by_path[descriptor]
    assert derived.sha256 == hashlib.sha256(descriptor.encode("utf-8")).hexdigest()
    assert derived.size_bytes == len(descriptor.encode("utf-8"))
    city = _arrow_table(output_dir / "city_SE3_egovehicle.feather").to_pandas()
    np.testing.assert_allclose(city["tz_m"], 0.0, atol=1e-9)


def test_preflight_rejects_camera_count_mismatch_before_staging(writable_test_dir: Path) -> None:
    source_scene = _write_scene(writable_test_dir)
    (source_scene / "camera" / "right_camera" / "02.jpg").unlink()
    output_root = writable_test_dir / "output"
    with pytest.raises(ValueError, match="count mismatch"):
        convert_pandaset_scene(
            source_scene,
            output_root,
            "bad-count",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )
    assert not output_root.exists()


@pytest.mark.parametrize("bad_sweep", ["unreadable", "missing_columns", "nonfinite"])
def test_lidar_sweep_validation_happens_before_any_output_is_created(
    writable_test_dir: Path,
    bad_sweep: str,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    sweep = source_scene / "lidar" / "01.pkl.gz"
    if bad_sweep == "unreadable":
        sweep.write_bytes(b"not a gzip pickle")
    elif bad_sweep == "missing_columns":
        pd.DataFrame({"x": [0.0], "y": [0.0], "z": [0.0]}).to_pickle(
            sweep,
            compression="gzip",
        )
    else:
        pd.DataFrame(
            {"x": [float("nan")], "y": [0.0], "z": [0.0], "i": [1.0]}
        ).to_pickle(sweep, compression="gzip")
    output_root = writable_test_dir / "output"

    with pytest.raises((OSError, ValueError, EOFError)):
        convert_pandaset_scene(
            source_scene,
            output_root,
            f"bad-sweep-{bad_sweep}",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )

    assert not output_root.exists()


@pytest.mark.parametrize(
    ("stream", "timestamps"),
    [
        ("front_camera", [1.1, 1.1, 3.1]),
        ("front_camera", [1.1, 1.0, 3.1]),
        ("lidar", [1.0, 1.0, 4.0]),
        ("lidar", [1.0, 0.9, 4.0]),
    ],
)
def test_duplicate_or_nonincreasing_source_timestamps_fail_before_output(
    writable_test_dir: Path,
    stream: str,
    timestamps: list[float],
) -> None:
    source_scene = _write_scene(writable_test_dir)
    timestamp_path = (
        source_scene / "lidar" / "timestamps.json"
        if stream == "lidar"
        else source_scene / "camera" / stream / "timestamps.json"
    )
    _write_json(timestamp_path, timestamps)
    output_root = writable_test_dir / "output"

    with pytest.raises(ValueError, match="strictly increasing"):
        convert_pandaset_scene(
            source_scene,
            output_root,
            f"bad-timestamps-{stream}",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )

    assert not output_root.exists()


def test_preflight_rejects_inconsistent_image_dimensions(writable_test_dir: Path) -> None:
    source_scene = _write_scene(writable_test_dir)
    Image.fromarray(np.zeros((3, 4, 3), dtype=np.uint8)).save(
        source_scene / "camera" / "back_camera" / "01.jpg"
    )
    with pytest.raises(ValueError, match="dimensions"):
        convert_pandaset_scene(
            source_scene,
            writable_test_dir / "output",
            "bad-dimensions",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )


def test_pose_interpolation_outside_lidar_range_is_rejected() -> None:
    timestamps = (1_000_000_000, 2_000_000_000, 4_000_000_000)
    poses = tuple(np.eye(4) for _ in timestamps)

    with pytest.raises(ValueError, match="outside lidar pose time range"):
        pandaset_adapter._interpolate_poses(
            timestamps,
            poses,
            (900_000_000,),
        )


def test_ordered_distinct_selection_avoids_nearest_duplicate_reuse() -> None:
    values = (1_000_000_000, 1_599_000_000, 2_599_000_000)
    anchors = (1_000_000_000, 2_100_000_000, 3_100_000_000)

    assert pandaset_adapter._select_indices(
        values,
        anchors,
        "right_camera",
    ) == (0, 1, 2)


def test_camera_anchor_before_lidar_pose_support_is_dropped(
    writable_test_dir: Path,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    for source_name, _ in EXPECTED_CAMERA_MAP:
        _set_camera_timeline(source_scene, source_name, [1.1, 2.1, 3.1])
    _set_lidar_timeline(source_scene, [1.15, 2.0, 4.0])

    _, manifest = convert_pandaset_scene(
        source_scene,
        writable_test_dir / "output",
        "pose-supported",
        converter_git_commit="0dcf6795",
        created_at="2026-07-30T12:00:00Z",
    )

    assert manifest.source_frame_count == 3
    assert manifest.output_frame_count == 2
    assert [frame.anchor_timestamp_ns for frame in manifest.frames] == [
        2_100_000_000,
        3_100_000_000,
    ]
    alignment = next(
        json.loads(artifact.path.split("=", 1)[1])
        for artifact in manifest.source_artifacts
        if artifact.path.startswith("derived:pandaset_temporal_alignment=")
    )
    assert alignment["adapter_algorithm_version"] == "pandaset_cadence_window_v3"
    assert alignment["dropped_anchor_frame_count"] == 1
    assert alignment["lidar_pose_support_ns"] == [1_150_000_000, 4_000_000_000]
    assert alignment["pose_supported_source_frame_counts"] == {
        source_name: 2 for source_name, _ in EXPECTED_CAMERA_MAP
    }


def test_ordered_distinct_selection_rejects_impossible_matching() -> None:
    values = (1_000_000_000, 1_010_000_000, 3_100_000_000)
    anchors = (1_100_000_000, 2_100_000_000, 3_100_000_000)

    with pytest.raises(ValueError, match="ordered distinct matching"):
        pandaset_adapter._select_indices(values, anchors, "right_camera")


def test_static_calibration_drift_reports_camera_and_residuals(writable_test_dir: Path) -> None:
    source_scene = _write_scene(writable_test_dir)
    pose_path = source_scene / "camera" / "left_camera" / "poses.json"
    poses = _load_json(pose_path)
    assert isinstance(poses, list)
    poses[1]["position"]["x"] += 0.1
    _write_json(pose_path, poses)
    with pytest.raises(
        ValueError,
        match=r"left_camera.*rotation residual.*translation residual",
    ):
        convert_pandaset_scene(
            source_scene,
            writable_test_dir / "output",
            "drift",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"ego_origin": "roof"}, "ego_origin"),
        ({"ground_radius_m": 0.0}, "ground_radius_m"),
        ({"ground_radius_m": float("nan")}, "ground_radius_m"),
        ({"ground_quantile": -0.01}, "ground_quantile"),
        ({"ground_quantile": 1.01}, "ground_quantile"),
    ],
)
def test_bad_origin_or_ground_parameters_are_rejected(
    writable_test_dir: Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    with pytest.raises(ValueError, match=message):
        convert_pandaset_scene(
            source_scene,
            writable_test_dir / "output",
            "bad-ground",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
            **kwargs,
        )


def test_existing_final_is_preserved_untouched(writable_test_dir: Path) -> None:
    source_scene = _write_scene(writable_test_dir)
    final = writable_test_dir / "output" / "exists"
    final.mkdir(parents=True)
    marker = final / "keep.txt"
    marker.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        convert_pandaset_scene(
            source_scene,
            final.parent,
            final.name,
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )
    assert marker.read_text(encoding="utf-8") == "original"
    assert [path.name for path in final.iterdir()] == ["keep.txt"]


def test_mid_conversion_write_failure_removes_only_private_staging(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    output_root = writable_test_dir / "output"
    real_write_feather = pandaset_adapter.write_feather
    calls = 0

    def fail_second_write(frame: pd.DataFrame, path: str | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected write fault")
        real_write_feather(frame, path)

    monkeypatch.setattr(pandaset_adapter, "write_feather", fail_second_write)
    with pytest.raises(OSError, match="injected write fault"):
        convert_pandaset_scene(
            source_scene,
            output_root,
            "fault",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )
    assert not (output_root / "fault").exists()
    assert not list(output_root.glob(".fault.staging-*"))


def test_published_camera_images_are_private_snapshot_copies(
    writable_test_dir: Path,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    output_dir, manifest = convert_pandaset_scene(
        source_scene,
        writable_test_dir / "output",
        "private-copy",
        converter_git_commit="0dcf6795",
        created_at="2026-07-30T12:00:00Z",
    )
    artifact_by_path = {artifact.path: artifact for artifact in manifest.source_artifacts}
    output_hashes: dict[Path, str] = {}
    for source_name, pseudo_name in EXPECTED_CAMERA_MAP:
        camera_dir = source_scene / "camera" / source_name
        timestamps = _load_json(camera_dir / "timestamps.json")
        assert isinstance(timestamps, list)
        for index, timestamp in enumerate(timestamps):
            source_image = camera_dir / f"{index:02d}.jpg"
            output_image = (
                output_dir
                / "sensors"
                / "cameras"
                / pseudo_name
                / f"{round(timestamp * 1_000_000_000)}.jpg"
            )
            source_relative = source_image.relative_to(source_scene).as_posix()
            output_hashes[output_image] = sha256_file(output_image)
            assert artifact_by_path[source_relative].sha256 == output_hashes[output_image]

    changed_source = source_scene / "camera" / "front_camera" / "00.jpg"
    Image.fromarray(np.full((2, 3, 3), 255, dtype=np.uint8)).save(changed_source)
    assert all(sha256_file(path) == digest for path, digest in output_hashes.items())


def test_source_replacement_after_preflight_fails_without_publication(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    output_root = writable_test_dir / "output"
    real_materialize = pandaset_adapter.materialize_file
    replaced = False

    def replace_before_copy(
        src: str | Path,
        dst: str | Path,
        prefer_hardlink: bool = True,
    ) -> str:
        nonlocal replaced
        source = Path(src)
        if not replaced and source.name == "01.jpg":
            replaced = True
            Image.fromarray(np.full((2, 3, 3), 254, dtype=np.uint8)).save(source)
        return real_materialize(src, dst, prefer_hardlink=prefer_hardlink)

    monkeypatch.setattr(pandaset_adapter, "materialize_file", replace_before_copy)
    with pytest.raises(ValueError, match="snapshot|changed"):
        convert_pandaset_scene(
            source_scene,
            output_root,
            "source-race",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )
    assert replaced
    assert not (output_root / "source-race").exists()
    assert not list(output_root.glob(".source-race.staging-*"))


def test_corrupted_nonfirst_staged_jpeg_cannot_publish(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    output_root = writable_test_dir / "output"
    real_write_annotations = pandaset_adapter.write_empty_annotations

    def corrupt_after_materialization(path: str | Path) -> None:
        real_write_annotations(path)
        staging = Path(path).parent
        images = sorted(
            (staging / "sensors" / "cameras" / "ring_front_center").glob("*.jpg")
        )
        images[1].write_bytes(b"corrupted staged jpeg")

    monkeypatch.setattr(
        pandaset_adapter,
        "write_empty_annotations",
        corrupt_after_materialization,
    )
    with pytest.raises((OSError, ValueError)):
        convert_pandaset_scene(
            source_scene,
            output_root,
            "corrupt-staged",
            converter_git_commit="0dcf6795",
            created_at="2026-07-30T12:00:00Z",
        )
    assert not (output_root / "corrupt-staged").exists()
    assert not list(output_root.glob(".corrupt-staged.staging-*"))


def test_lidar_live_payload_peak_is_constant_for_80_frames(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_scene = _write_scene(writable_test_dir, frame_count=80)
    real_read_lidar = pandaset_adapter._read_lidar_world
    live = 0
    peak = 0

    def tracked_read(path: Path) -> tuple[np.ndarray, np.ndarray]:
        nonlocal live, peak
        xyz, intensity = real_read_lidar(path)
        live += 1
        peak = max(peak, live)

        def release() -> None:
            nonlocal live
            live -= 1

        weakref.finalize(xyz, release)
        return xyz, intensity

    monkeypatch.setattr(pandaset_adapter, "_read_lidar_world", tracked_read)
    convert_pandaset_scene(
        source_scene,
        writable_test_dir / "output",
        "constant-memory",
        converter_git_commit="0dcf6795",
        created_at="2026-07-30T12:00:00Z",
    )
    assert peak <= 2


def test_ground_only_sweep_is_read_once_and_selected_sweeps_are_streamed_twice(
    writable_test_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_scene = _write_scene(writable_test_dir)
    _set_lidar_timeline(
        source_scene,
        [0.5, 1.0, 2.0, 4.0],
        first_local_points=[(0.0, 0.0, -1.0)],
    )
    real_read_lidar = pandaset_adapter._read_lidar_world
    read_counts: dict[str, int] = {}

    def counted_read(path: Path) -> tuple[np.ndarray, np.ndarray]:
        read_counts[path.name] = read_counts.get(path.name, 0) + 1
        return real_read_lidar(path)

    monkeypatch.setattr(pandaset_adapter, "_read_lidar_world", counted_read)
    convert_pandaset_scene(
        source_scene,
        writable_test_dir / "output",
        "ground-streaming",
        ego_origin="ground",
        converter_git_commit="0dcf6795",
        created_at="2026-07-30T12:00:00Z",
    )
    assert read_counts == {"00.pkl.gz": 1, "01.pkl.gz": 2, "02.pkl.gz": 2, "03.pkl.gz": 2}
