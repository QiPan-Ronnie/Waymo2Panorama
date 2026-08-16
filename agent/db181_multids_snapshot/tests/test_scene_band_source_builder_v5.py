from __future__ import annotations

from agent.db181_multids.scene_band_policy import policy_for_dataset
from agent.db181_multids.scene_band_source_builder_v5 import (
    build_scene_band_renderer_source,
)


def _source() -> str:
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
            '            "color_diag": color_diag_report}',
        )
    )


def test_builder_injects_photometric_seam_without_enabling_yolo() -> None:
    rendered = build_scene_band_renderer_source(
        _source(),
        policy=policy_for_dataset("pandaset"),
        data_root="/panda",
        output_root="/out",
        result_name="manifest.json",
        code_root="/content/w2p_ego",
        photometric_seam_ownership=True,
    )
    assert "CAP_ONLY = True" in rendered
    assert "optimize_photometric_ownership_seams" in rendered
    assert '"photometric_seam_ownership": _photoseam_report,' in rendered
    assert rendered.index("optimize_photometric_ownership_seams") < rendered.index(
        "# RULE 1: object-body rays"
    )


def test_builder_is_exact_v2_policy_when_feature_is_disabled() -> None:
    rendered = build_scene_band_renderer_source(
        _source(),
        policy=policy_for_dataset("pandaset"),
        data_root="/panda",
        output_root="/out",
        result_name="manifest.json",
        code_root="/content/w2p_ego",
        photometric_seam_ownership=False,
    )
    assert "CAP_ONLY = True" in rendered
    assert "optimize_photometric_ownership_seams" not in rendered
    assert '"photometric_seam_ownership"' not in rendered
