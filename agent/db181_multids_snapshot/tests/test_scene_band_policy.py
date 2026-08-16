from __future__ import annotations

from dataclasses import replace

import pytest

from agent.db181_multids import CameraRecord, ConversionManifest, FrameRecord, SourceArtifact
from agent.db181_multids.scene_band_policy import (
    apply_policy_to_db89_source,
    policy_for_dataset,
    validate_manifest_for_policy,
)


def _manifest(dataset: str) -> ConversionManifest:
    supported = ((0.0, 360.0),)
    honest_black: tuple[tuple[float, float], ...] = ()
    mode = "B"
    has_lidar = dataset != "waymo_e2e"
    has_ego_pose = dataset != "waymo_e2e"
    has_annotations = False
    if dataset == "waymo_perception":
        supported = ((0.0, 126.0), (234.0, 360.0))
        honest_black = ((126.0, 234.0),)
        mode = "A"
        has_annotations = True
    frame = FrameRecord(
        index=0,
        anchor_timestamp_ns=1,
        camera_timestamps_ns={"ring_front_center": 1},
        lidar_timestamp_ns=1 if has_lidar else None,
    )
    manifest = ConversionManifest(
        schema_version="1.0",
        dataset=dataset,
        source_scene_id="scene",
        output_log_id="log",
        mode=mode,
        cameras=("ring_front_center",),
        anchor_camera="ring_front_center",
        source_frame_count=1,
        output_frame_count=1,
        source_frame_rate_hz=10.0,
        output_frame_rate_hz=10.0,
        camera_records=(CameraRecord("ring_front_center", "FRONT", 1, 0),),
        frames=(frame,),
        calibration_sha256="a" * 64,
        source_artifacts=(SourceArtifact("source.bin", "b" * 64, 1),),
        has_lidar=has_lidar,
        has_ego_pose=has_ego_pose,
        has_annotations=has_annotations,
        real_mask_pattern="masks/real/{index:06d}.png" if mode == "A" else None,
        faithfill_mask_pattern=None,
        honest_black_mask_pattern=(
            "masks/honest_black/{index:06d}.png" if honest_black else None
        ),
        supported_azimuth_deg=supported,
        honest_black_azimuth_deg=honest_black,
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


def _db89_source() -> str:
    return "\n".join(
        (
            'GROUND_MODE = "fill"   # renderer default',
            'ANNOTATION_POLICY = "composite"',
            "GAIN_PER_CHANNEL = True",
            "GAIN_PRIOR_W = 0.0",
            "GAIN_STRENGTH = 0.5",
            "DEPTH_SEAMRAMP_DEG = 0.0",
            'BAND_DEPTH_MODE = "metric"',
            "CAP_ONLY = False",
            "BAND_TORCH = False",
            "EMC_RENDER = True",
            "EGO_BLACK = True",
            'EGO_IMG_MASK = "/content/egomask_cur.npz"',
        )
    )


def test_dataset_policies_freeze_validated_scene_band_safeguards() -> None:
    av2 = policy_for_dataset("av2")
    panda = policy_for_dataset("pandaset")
    nusc = policy_for_dataset("nuscenes")
    waymo = policy_for_dataset("waymo_perception")
    e2e = policy_for_dataset("waymo_e2e")

    assert av2.band_depth_mode == "metric"
    assert panda.band_depth_mode == "plane_far"
    assert nusc.band_depth_mode == "plane_far"
    assert nusc.strict_sync_fraction == 0.5
    assert waymo.supported_azimuth_deg == ((0.0, 126.0), (234.0, 360.0))
    assert waymo.honest_black_azimuth_deg == ((126.0, 234.0),)
    assert e2e.requires_lidar is False
    assert e2e.requires_ego_pose is False

    for policy in (av2, panda, nusc, waymo, e2e):
        assert policy.annotation_policy == "raw_sensor"
        assert policy.gain_per_channel is False
        assert policy.gain_prior_w == 0.05
        assert policy.gain_strength == 1.0
        assert policy.depth_seamramp_deg == 10.546875
        assert policy.ground_mode == "off"


@pytest.mark.parametrize(
    ("alias", "canonical"),
    (
        ("argoverse2", "av2"),
        ("panda", "pandaset"),
        ("nusc", "nuscenes"),
        ("waymo_open", "waymo_perception"),
        ("streetcrafter_waymo", "waymo_perception"),
        ("waymo_end_to_end", "waymo_e2e"),
    ),
)
def test_policy_aliases_are_explicit(alias: str, canonical: str) -> None:
    assert policy_for_dataset(alias).dataset == canonical


def test_policy_lookup_rejects_unknown_dataset() -> None:
    with pytest.raises(ValueError, match="unsupported dataset"):
        policy_for_dataset("pretty_panorama")


def test_apply_policy_rewrites_every_production_assignment_once() -> None:
    policy = policy_for_dataset("pandaset")
    rendered = apply_policy_to_db89_source(_db89_source(), policy)

    assert 'GROUND_MODE = "off"   # renderer default' in rendered
    assert 'ANNOTATION_POLICY = "raw_sensor"' in rendered
    assert "GAIN_PER_CHANNEL = False" in rendered
    assert "GAIN_PRIOR_W = 0.05" in rendered
    assert "GAIN_STRENGTH = 1.0" in rendered
    assert "DEPTH_SEAMRAMP_DEG = 10.546875" in rendered
    assert 'BAND_DEPTH_MODE = "plane_far"' in rendered
    assert "CAP_ONLY = True" in rendered
    assert "BAND_TORCH = True" in rendered
    assert "EMC_RENDER = False" in rendered
    assert "EGO_BLACK = False" in rendered
    assert 'EGO_IMG_MASK = ""' in rendered


def test_apply_policy_rejects_missing_or_duplicate_assignment() -> None:
    policy = policy_for_dataset("nuscenes")
    with pytest.raises(ValueError, match="missing DB89 assignment.*GAIN_PRIOR_W"):
        apply_policy_to_db89_source(
            _db89_source().replace("GAIN_PRIOR_W = 0.0\n", ""), policy
        )
    with pytest.raises(ValueError, match="duplicate DB89 assignment.*GROUND_MODE"):
        apply_policy_to_db89_source(
            _db89_source() + '\nGROUND_MODE = "fill"', policy
        )


@pytest.mark.parametrize(
    "dataset",
    ("pandaset", "nuscenes", "waymo_perception", "waymo_e2e"),
)
def test_manifest_validation_accepts_only_matching_evidence(dataset: str) -> None:
    policy = policy_for_dataset(dataset)
    manifest = _manifest(dataset)
    validate_manifest_for_policy(manifest, policy)

    with pytest.raises(ValueError, match="manifest dataset"):
        validate_manifest_for_policy(replace(manifest, dataset="other"), policy)


def test_manifest_validation_rejects_invented_waymo_rear_rgb() -> None:
    manifest = _manifest("waymo_perception")
    policy = policy_for_dataset("waymo_perception")
    invented = replace(
        manifest,
        supported_azimuth_deg=((0.0, 360.0),),
        honest_black_azimuth_deg=(),
        honest_black_mask_pattern=None,
    )
    invented.validate()
    with pytest.raises(ValueError, match="supported azimuth"):
        validate_manifest_for_policy(invented, policy)


def test_policy_report_is_json_serializable_and_states_production_boundary() -> None:
    report = policy_for_dataset("waymo_e2e").to_dict()
    assert report["dataset"] == "waymo_e2e"
    assert report["full_panorama_class"] == "camera_only_candidate"
    assert report["ground_pipeline"] == "unsupported_honest_black"
    assert report["supported_azimuth_deg"] == [[0.0, 360.0]]
