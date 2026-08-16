from __future__ import annotations

from agent.db181_multids.scene_band_policy import policy_for_dataset
from agent.db181_multids.scene_band_source_builder import build_scene_band_renderer_source


def _db89_compound_source() -> str:
    return "\n".join(
        (
            'REMOTE_OUT = pathlib.Path("/old/output"); REMOTE_RESULT = pathlib.Path("/old/output/old.json")',
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


def test_builder_preserves_compound_result_assignment_under_new_output_root() -> None:
    rendered = build_scene_band_renderer_source(
        _db89_compound_source(),
        policy=policy_for_dataset("pandaset"),
        data_root="/content/db228_panda_019/pseudo",
        output_root="/content/db232_scene_band/pandaset",
        result_name="manifest_video_db232_panda.json",
    )

    assert (
        'REMOTE_OUT = pathlib.Path("/content/db232_scene_band/pandaset"); '
        'REMOTE_RESULT = REMOTE_OUT / "manifest_video_db232_panda.json"'
    ) in rendered
    assert 'DATA_ROOT = pathlib.Path("/content/db228_panda_019/pseudo")' in rendered
    assert 'BAND_DEPTH_MODE = "plane_far"' in rendered


def test_builder_rejects_missing_compound_result_assignment() -> None:
    source = _db89_compound_source().replace(
        '; REMOTE_RESULT = pathlib.Path("/old/output/old.json")', ""
    )
    try:
        build_scene_band_renderer_source(
            source,
            policy=policy_for_dataset("nuscenes"),
            data_root="/data",
            output_root="/output",
            result_name="manifest.json",
        )
    except ValueError as error:
        assert "REMOTE_OUT/REMOTE_RESULT compound assignment" in str(error)
    else:
        raise AssertionError("missing REMOTE_RESULT must fail before rendering")
