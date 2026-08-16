from __future__ import annotations

from agent.db181_multids.scene_band_policy import policy_for_dataset
from agent.db181_multids.scene_band_source_builder_v2 import (
    build_scene_band_renderer_source,
)


def _source() -> str:
    return "\n".join(
        (
            'REMOTE_OUT = pathlib.Path("/old"); REMOTE_RESULT = pathlib.Path("/old/r.json")',
            'DATA_ROOT = pathlib.Path("/data")',
            'sys.path.insert(0, "/content/waymo2panorama/scripts/phase3"); sys.path.insert(0, "/content/waymo2panorama/code")',
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


def test_builder_pins_generated_imports_to_the_validated_code_tree() -> None:
    rendered = build_scene_band_renderer_source(
        _source(),
        policy=policy_for_dataset("pandaset"),
        data_root="/panda",
        output_root="/out",
        result_name="manifest.json",
        code_root="/content/w2p_ego",
    )
    assert (
        'sys.path.insert(0, "/content/w2p_ego/scripts/phase3"); '
        'sys.path.insert(0, "/content/w2p_ego/code")'
    ) in rendered
    assert "/content/waymo2panorama/code" not in rendered


def test_builder_rejects_missing_generated_code_tree_anchor() -> None:
    source = _source().replace(
        'sys.path.insert(0, "/content/waymo2panorama/scripts/phase3"); sys.path.insert(0, "/content/waymo2panorama/code")\n',
        "",
    )
    try:
        build_scene_band_renderer_source(
            source,
            policy=policy_for_dataset("nuscenes"),
            data_root="/data",
            output_root="/out",
            result_name="manifest.json",
            code_root="/content/w2p_ego",
        )
    except ValueError as error:
        assert "generated code-tree insertion" in str(error)
    else:
        raise AssertionError("missing code-tree anchor must fail")

