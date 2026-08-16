from __future__ import annotations

from .scene_band_policy import SceneBandPolicy
from .scene_band_source_builder_v7 import (
    build_scene_band_renderer_source as _build_scene_band_renderer_source,
)


def _inject_scene_band_export(source: str) -> str:
    anchor = '    ground_stats["angular_gap_fallback"] = _angular_gap_report'
    count = source.count(anchor)
    if count != 1:
        raise ValueError(f"expected exactly one angular-gap report anchor, found {count}")
    block = """    if _angular_gap_report["safe_band_rows"] is not None:
        _band0, _band1 = _angular_gap_report["safe_band_rows"]
        save_rgb(
            REMOTE_OUT / f"{run_name}_scene_band.png",
            comp[_band0:_band1 + 1],
        )
        save_rgb(
            REMOTE_OUT / f"{run_name}_scene_band_angular_mask.png",
            np.dstack([_gap_mask[_band0:_band1 + 1].astype(np.uint8) * 255] * 3),
        )
        _angular_gap_report.update({
            "scene_band_rows": [int(_band0), int(_band1)],
            "scene_band_shape": [int(_band1 - _band0 + 1), int(W)],
            "scene_band_file": f"{run_name}_scene_band.png",
            "scene_band_angular_mask_file": f"{run_name}_scene_band_angular_mask.png",
        })
"""
    return source.replace(anchor, block + anchor, 1)


def build_scene_band_renderer_source(
    source: str,
    *,
    policy: SceneBandPolicy,
    data_root: str,
    output_root: str,
    result_name: str,
    code_root: str,
    photometric_seam_ownership: bool = False,
    angular_gap_fallback: bool = False,
    export_scene_band: bool = False,
) -> str:
    if export_scene_band and not angular_gap_fallback:
        raise ValueError("scene-band export requires calibration-safe angular coverage")
    rendered = _build_scene_band_renderer_source(
        source,
        policy=policy,
        data_root=data_root,
        output_root=output_root,
        result_name=result_name,
        code_root=code_root,
        photometric_seam_ownership=photometric_seam_ownership,
        angular_gap_fallback=angular_gap_fallback,
    )
    if not export_scene_band:
        return rendered
    return _inject_scene_band_export(rendered)


__all__ = ["build_scene_band_renderer_source"]
