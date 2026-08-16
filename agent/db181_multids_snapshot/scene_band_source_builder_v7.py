from __future__ import annotations

from .scene_band_policy import SceneBandPolicy
from .scene_band_source_builder_v6 import (
    build_scene_band_renderer_source as _build_scene_band_renderer_source,
)


def _replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"expected exactly one direction-fallback anchor, found {count}")
    return source.replace(old, new, 1)


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
) -> str:
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
    if not angular_gap_fallback:
        return rendered
    rendered = _replace_once(
        rendered,
        '        "sampling_depth_m": 100.0,',
        '        "projection": "direction_only",',
    )
    rendered = _replace_once(
        rendered,
        "            _gpoints = C[None, :] + _gdirs * 100.0\n",
        "",
    )
    return _replace_once(
        rendered,
        "                _gcam = (_gR.T @ (_gpoints - _gt[None, :]).T).T",
        "                _gcam = _gdirs @ _gR",
    )


__all__ = ["build_scene_band_renderer_source"]
