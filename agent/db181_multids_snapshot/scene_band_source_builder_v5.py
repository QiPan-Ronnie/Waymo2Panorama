from __future__ import annotations

import re

from .scene_band_policy import SceneBandPolicy
from .scene_band_source_builder_v2 import (
    build_scene_band_renderer_source as _build_scene_band_renderer_source,
)


def _inject_photometric_seam_ownership(source: str) -> str:
    marker_pattern = re.compile(r"^    # RULE 1: object-body rays.*$", re.MULTILINE)
    markers = tuple(marker_pattern.finditer(source))
    if len(markers) != 1:
        raise ValueError(
            "expected exactly one RULE 1 object-body marker, "
            f"found {len(markers)}"
        )
    block = """    from agent.db181_multids.photometric_seam_ownership import (
        optimize_photometric_ownership_seams as _optimize_photoseam,
    )
    _validcam = np.stack([_p["ok"].reshape(H, W) for _p in proj], axis=0)
    _rgbcam = np.zeros((len(ring_cams), H, W, 3), np.uint8)
    for _pci, _pcam in enumerate(ring_cams):
        _pp = proj[_pci]
        _pflat = np.zeros((len(Xf), 3), np.uint8)
        _psel = np.nonzero(_pp["ok"])[0]
        if _psel.size:
            _pimg = np.clip(
                frame.images[_pcam].astype(np.float32)
                * np.exp(gains[_pci])[None, None, :].astype(np.float32),
                0,
                255,
            ).astype(np.uint8)
            _pflat[_psel] = np.clip(
                bilinear(_pimg, _pp["px"][_psel], _pp["py"][_psel]),
                0,
                255,
            ).astype(np.uint8)
        _rgbcam[_pci] = _pflat.reshape(H, W, 3)
    _bestcam2, _photoseam_report = _optimize_photoseam(
        bestcam.reshape(H, W),
        _validcam,
        _rgbcam,
        max_shift_px=max(64, W // 8),
        max_step_px=8,
        min_boundary_rows=12,
        min_relative_improvement=0.20,
        deviation_cost=0.05,
        smoothness_cost=0.25,
    )
    bestcam = _bestcam2.reshape(-1).astype(np.int8)
    print("PHOTOMETRIC_SEAM_OWNERSHIP", json.dumps(_photoseam_report), flush=True)
"""
    source = marker_pattern.sub(block + markers[0].group(0), source, count=1)

    result_pattern = re.compile(
        r'^(?P<indent>\s*)"color_diag":\s*'
        r'(?P<value>[A-Za-z_][A-Za-z0-9_]*)\}$',
        re.MULTILINE,
    )
    result_matches = tuple(result_pattern.finditer(source))
    if len(result_matches) != 1:
        raise ValueError(
            "expected exactly one generated color_diag result anchor, "
            f"found {len(result_matches)}"
        )
    match = result_matches[0]
    indent = match.group("indent")
    replacement = (
        f'{indent}"photometric_seam_ownership": _photoseam_report,\n'
        f'{indent}"color_diag": {match.group("value")}' + "}"
    )
    return result_pattern.sub(replacement, source, count=1)


def build_scene_band_renderer_source(
    source: str,
    *,
    policy: SceneBandPolicy,
    data_root: str,
    output_root: str,
    result_name: str,
    code_root: str,
    photometric_seam_ownership: bool = False,
) -> str:
    rendered = _build_scene_band_renderer_source(
        source,
        policy=policy,
        data_root=data_root,
        output_root=output_root,
        result_name=result_name,
        code_root=code_root,
    )
    if not photometric_seam_ownership:
        return rendered
    return _inject_photometric_seam_ownership(rendered)


__all__ = ["build_scene_band_renderer_source"]
