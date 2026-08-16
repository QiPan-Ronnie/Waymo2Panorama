from __future__ import annotations

import json
from pathlib import Path

from agent.db181_multids import CameraRecord, ConversionManifest, FrameRecord, SourceArtifact
from agent.db181_multids.scene_band_worker_config import (
    build_scene_band_worker_config,
    write_scene_band_worker_config,
)


def _manifest(dataset: str, cameras: tuple[str, ...]) -> ConversionManifest:
    is_waymo = dataset == "waymo_perception"
    no_lidar = dataset == "waymo_e2e"
    supported = ((0.0, 126.0), (234.0, 360.0)) if is_waymo else ((0.0, 360.0),)
    honest = ((126.0, 234.0),) if is_waymo else ()
    frame = FrameRecord(
        index=0,
        anchor_timestamp_ns=100,
        camera_timestamps_ns={camera: 100 for camera in cameras},
        lidar_timestamp_ns=None if no_lidar else 100,
    )
    manifest = ConversionManifest(
        schema_version="1.0",
        dataset=dataset,
        source_scene_id="source-scene",
        output_log_id="pseudo-log",
        mode="A" if is_waymo else "B",
        cameras=cameras,
        anchor_camera=cameras[0],
        source_frame_count=1,
        output_frame_count=1,
        source_frame_rate_hz=10.0,
        output_frame_rate_hz=10.0,
        camera_records=tuple(CameraRecord(camera, camera.upper(), 1, 0) for camera in cameras),
        frames=(frame,),
        calibration_sha256="a" * 64,
        source_artifacts=(SourceArtifact("source.bin", "b" * 64, 1),),
        has_lidar=not no_lidar,
        has_ego_pose=not no_lidar,
        has_annotations=is_waymo,
        real_mask_pattern="masks/real/{index:06d}.png" if is_waymo else None,
        faithfill_mask_pattern=None,
        honest_black_mask_pattern="masks/black/{index:06d}.png" if honest else None,
        supported_azimuth_deg=supported,
        honest_black_azimuth_deg=honest,
        coordinate_convention_transform=(
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        converter_git_commit="1234567",
        created_at="2026-07-31T12:00:00-07:00",
    )
    manifest.validate()
    return manifest


def test_worker_config_carries_camera_order_and_dataset_policy() -> None:
    cameras = ("ring_front_center", "ring_front_left", "ring_side_left")
    config = build_scene_band_worker_config(_manifest("pandaset", cameras))

    assert config.dataset == "pandaset"
    assert config.camera_order == cameras
    assert config.policy.band_depth_mode == "plane_far"
    assert config.strict_sync_fraction is None
    assert config.output_frame_count == 1


def test_nuscenes_worker_config_requires_strict_half_cadence_gate() -> None:
    config = build_scene_band_worker_config(
        _manifest("nuscenes", ("ring_front_center",))
    )
    assert config.strict_sync_fraction == 0.5
    assert config.full_panorama_class == "scene_band_sky_only"


def test_waymo_worker_config_preserves_rear_honest_black_boundary() -> None:
    config = build_scene_band_worker_config(
        _manifest("waymo_perception", ("ring_front_center",))
    )
    payload = config.to_dict()
    assert payload["supported_azimuth_deg"] == [[0.0, 126.0], [234.0, 360.0]]
    assert payload["honest_black_azimuth_deg"] == [[126.0, 234.0]]
    assert payload["ground_pipeline"] == "observed_azimuth_metric_only"


def test_worker_config_roundtrip_contains_no_runtime_secret(tmp_path: Path) -> None:
    manifest = _manifest("waymo_e2e", ("ring_front_center", "ring_rear"))
    destination = tmp_path / "worker_config.json"
    write_scene_band_worker_config(manifest, destination)

    payload = json.loads(destination.read_text(encoding="utf-8"))
    text = destination.read_text(encoding="utf-8").lower()
    assert payload["dataset"] == "waymo_e2e"
    assert payload["policy"]["annotation_policy"] == "raw_sensor"
    assert "bearer" not in text
    assert "token" not in text
    assert "trycloudflare" not in text
