from __future__ import annotations

import re

from .scene_band_policy import SceneBandPolicy
from .scene_band_source_builder_v2 import (
    build_scene_band_renderer_source as _build_scene_band_renderer_source,
)


def _replace_assignment_once(source: str, name: str, value: str) -> str:
    pattern = re.compile(rf"^(?P<indent>\s*){re.escape(name)}\s*=\s*[^\r\n]+$", re.MULTILINE)
    matches = tuple(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {name} assignment, found {len(matches)}")
    replacement = f'{matches[0].group("indent")}{name} = {value}'
    return pattern.sub(replacement, source, count=1)


def _inject_external_object_ownership(source: str) -> str:
    marker_pattern = re.compile(r"^    # RULE 1: object-body rays.*$", re.MULTILINE)
    markers = tuple(marker_pattern.finditer(source))
    if len(markers) != 1:
        raise ValueError(
            "expected exactly one RULE 1 object-body marker, "
            f"found {len(markers)}"
        )

    block = """    from agent.db181_multids.external_object_ownership import (
        enforce_dominant_single_source_objects as _enforce_extobj,
    )
    _objcam = np.zeros((len(ring_cams), H, W), bool)
    _validcam = np.stack([_p["ok"].reshape(H, W) for _p in proj], axis=0)
    for _oci in range(len(ring_cams)):
        _p = proj[_oci]
        _flat = np.zeros(len(Xf), bool)
        _sel = np.nonzero(_p["ok"])[0]
        if _sel.size:
            _img = seg_masks[_oci]
            _xi = np.clip(_p["px"][_sel].astype(np.int64), 0, _img.shape[1] - 1)
            _yi = np.clip(_p["py"][_sel].astype(np.int64), 0, _img.shape[0] - 1)
            _flat[_sel] = _img[_yi, _xi]
        _objcam[_oci] = _flat.reshape(H, W)
    _bestcam2, _extobj_report = _enforce_extobj(
        bestcam.reshape(H, W),
        _validcam,
        _objcam,
        dominance_ratio=2.5,
        dilation_px=8,
        min_object_px=64,
        min_owner_valid_fraction=0.9,
        max_component_fraction=0.08,
    )
    bestcam = _bestcam2.reshape(-1).astype(np.int8)
    print("EXTERNAL_OBJECT_OWNERSHIP", json.dumps(_extobj_report), flush=True)
"""
    source = marker_pattern.sub(block + markers[0].group(0), source, count=1)

    result_anchors = (
        '            "color_diag": _color_diag_report}',
        '            "color_diag": color_diag_report}',
    )
    matching_anchors = tuple(
        anchor for anchor in result_anchors if source.count(anchor) == 1
    )
    if len(matching_anchors) != 1:
        raise ValueError(
            "expected exactly one generated color_diag result anchor, "
            f"found {len(matching_anchors)}"
        )
    result_anchor = matching_anchors[0]
    return source.replace(
        result_anchor,
        '            "external_object_ownership": _extobj_report,\n'
        + result_anchor,
        1,
    )


def build_scene_band_renderer_source(
    source: str,
    *,
    policy: SceneBandPolicy,
    data_root: str,
    output_root: str,
    result_name: str,
    code_root: str,
    external_object_ownership: bool = False,
) -> str:
    rendered = _build_scene_band_renderer_source(
        source,
        policy=policy,
        data_root=data_root,
        output_root=output_root,
        result_name=result_name,
        code_root=code_root,
    )
    if not external_object_ownership:
        return rendered
    rendered = _replace_assignment_once(rendered, "CAP_ONLY", "False")
    return _inject_external_object_ownership(rendered)


__all__ = ["build_scene_band_renderer_source"]
