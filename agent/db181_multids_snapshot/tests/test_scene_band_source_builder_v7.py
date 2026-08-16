from __future__ import annotations

from agent.db181_multids.scene_band_policy import policy_for_dataset
from agent.db181_multids.scene_band_source_builder_v7 import (
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
            "    if EMC_RENDER:   # DB-118 speed #1a: display-only A/B render",
            '            "ground_fill": ground_stats,',
            '            "color_diag": color_diag_report}',
        )
    )


def test_angular_fallback_uses_direction_only_projection() -> None:
    rendered = build_scene_band_renderer_source(
        _source(),
        policy=policy_for_dataset("pandaset"),
        data_root="/panda",
        output_root="/out",
        result_name="manifest.json",
        code_root="/content/w2p_ego",
        photometric_seam_ownership=True,
        angular_gap_fallback=True,
    )
    assert '"projection": "direction_only"' in rendered
    assert "_gcam = _gdirs @ _gR" in rendered
    assert "_gpoints = C[None, :] + _gdirs * 100.0" not in rendered
    assert "(_gpoints - _gt[None, :])" not in rendered


def test_disabled_fallback_needs_no_direction_rewrite() -> None:
    rendered = build_scene_band_renderer_source(
        _source(),
        policy=policy_for_dataset("pandaset"),
        data_root="/panda",
        output_root="/out",
        result_name="manifest.json",
        code_root="/content/w2p_ego",
        photometric_seam_ownership=True,
        angular_gap_fallback=False,
    )
    assert "_gcam = _gdirs @ _gR" not in rendered
    assert '"angular_gap_fallback"' not in rendered
