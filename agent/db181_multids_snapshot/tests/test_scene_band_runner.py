from __future__ import annotations

from agent.db181_multids.scene_band_policy import policy_for_dataset
from agent.db181_multids.scene_band_runner import build_scene_band_renderer_source


def _source() -> str:
    return "\n".join(
        (
            'REMOTE_OUT = pathlib.Path("/old/output")',
            'DATA_ROOT = pathlib.Path("/old/data")',
            'GROUND_MODE = "fill"',
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


def test_runner_source_applies_policy_and_explicit_remote_roots() -> None:
    rendered = build_scene_band_renderer_source(
        _source(),
        policy=policy_for_dataset("pandaset"),
        data_root="/content/db228_panda_019/pseudo",
        output_root="/content/db232_scene_band/pandaset",
    )

    assert 'REMOTE_OUT = pathlib.Path("/content/db232_scene_band/pandaset")' in rendered
    assert 'DATA_ROOT = pathlib.Path("/content/db228_panda_019/pseudo")' in rendered
    assert 'GROUND_MODE = "off"' in rendered
    assert 'BAND_DEPTH_MODE = "plane_far"' in rendered
    assert 'ANNOTATION_POLICY = "raw_sensor"' in rendered


def test_runner_source_rejects_ambiguous_output_assignment() -> None:
    source = _source() + '\nREMOTE_OUT = pathlib.Path("/second")'
    try:
        build_scene_band_renderer_source(
            source,
            policy=policy_for_dataset("nuscenes"),
            data_root="/data",
            output_root="/output",
        )
    except ValueError as error:
        assert "duplicate DB89 assignment for REMOTE_OUT" in str(error)
    else:
        raise AssertionError("duplicate REMOTE_OUT must be rejected")
