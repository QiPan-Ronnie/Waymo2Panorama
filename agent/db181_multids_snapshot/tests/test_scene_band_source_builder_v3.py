from __future__ import annotations

from agent.db181_multids.scene_band_policy import policy_for_dataset
from agent.db181_multids.scene_band_source_builder_v3 import (
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
            "    # RULE 1: object-body rays — injected before this line",
            '            "ground_fill": ground_stats,',
            '            "color_diag": _color_diag_report}',
        )
    )


def test_builder_enables_segmentation_and_injects_single_source_ownership() -> None:
    rendered = build_scene_band_renderer_source(
        _source(),
        policy=policy_for_dataset("pandaset"),
        data_root="/panda",
        output_root="/out",
        result_name="manifest.json",
        code_root="/content/w2p_ego",
        external_object_ownership=True,
    )

    assert "CAP_ONLY = False" in rendered
    assert "enforce_dominant_single_source_objects" in rendered
    assert "_extobj_report" in rendered
    assert '"external_object_ownership": _extobj_report' in rendered
    assert rendered.index("enforce_dominant_single_source_objects") < rendered.index(
        "# RULE 1: object-body rays"
    )


def test_builder_leaves_object_injection_out_when_disabled() -> None:
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
    assert '"external_object_ownership": _extobj_report' not in rendered
