from __future__ import annotations

import pytest

from agent.db181_multids.scene_band_policy import policy_for_dataset
from agent.db181_multids.scene_band_source_builder_v4 import (
    build_scene_band_renderer_source,
)


def _source(color_name: str = "color_diag_report") -> str:
    return "\n".join(
        (
            'REMOTE_OUT = pathlib.Path("/old"); REMOTE_RESULT = pathlib.Path("/old/r.json")',
            'DATA_ROOT = pathlib.Path("/data")',
            'sys.path.insert(0, "/old/scripts/phase3"); sys.path.insert(0, "/old/code")',
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
            "    # RULE 1: object-body rays",
            '            "ground_fill": ground_stats,',
            f'            "color_diag": {color_name}' + "}",
        )
    )


@pytest.mark.parametrize("color_name", ("color_diag_report", "_color_diag_report"))
def test_builder_handles_real_and_legacy_color_diag_names(color_name: str) -> None:
    rendered = build_scene_band_renderer_source(
        _source(color_name),
        policy=policy_for_dataset("pandaset"),
        data_root="/panda",
        output_root="/out",
        result_name="manifest.json",
        code_root="/content/w2p_ego",
        external_object_ownership=True,
    )
    assert "CAP_ONLY = False" in rendered
    assert "enforce_dominant_single_source_objects" in rendered
    assert '"external_object_ownership": _extobj_report,' in rendered
    assert f'"color_diag": {color_name}' + "}" in rendered


def test_builder_keeps_cap_only_when_external_ownership_is_disabled() -> None:
    rendered = build_scene_band_renderer_source(
        _source(),
        policy=policy_for_dataset("pandaset"),
        data_root="/panda",
        output_root="/out",
        result_name="manifest.json",
        code_root="/content/w2p_ego",
        external_object_ownership=False,
    )
    assert "CAP_ONLY = True" in rendered
    assert "enforce_dominant_single_source_objects" not in rendered
