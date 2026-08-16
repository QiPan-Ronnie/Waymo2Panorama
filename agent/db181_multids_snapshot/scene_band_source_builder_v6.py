from __future__ import annotations

import re

from .scene_band_policy import SceneBandPolicy
from .scene_band_source_builder_v5 import (
    build_scene_band_renderer_source as _build_scene_band_renderer_source,
)


def _inject_angular_gap_fallback(source: str) -> str:
    marker_pattern = re.compile(
        r"^    if EMC_RENDER:\s+# DB-118 speed #1a.*$", re.MULTILINE
    )
    markers = tuple(marker_pattern.finditer(source))
    if len(markers) != 1:
        raise ValueError(
            "expected exactly one DB-118 EMC render marker, "
            f"found {len(markers)}"
        )
    block = """    _angular_gap_report = {
        "sampling_depth_m": 100.0,
        "safe_band_rows": None,
        "target_px": 0,
        "filled_px": 0,
        "unfilled_px": 0,
    }
    _calib_covered = _nv > 0
    _row_full = _calib_covered.all(axis=1)
    _cy = H // 2
    _gap_mask = np.zeros((H, W), bool)
    if _row_full[_cy]:
        _sy0 = _cy
        _sy1 = _cy
        while _sy0 > 0 and _row_full[_sy0 - 1]:
            _sy0 -= 1
        while _sy1 + 1 < H and _row_full[_sy1 + 1]:
            _sy1 += 1
        _angular_gap_report["safe_band_rows"] = [int(_sy0), int(_sy1)]
        _gap_mask = (fbcam.reshape(H, W) < 0) & _calib_covered
        _gap_mask[:_sy0] = False
        _gap_mask[_sy1 + 1:] = False
        _gy, _gx = np.nonzero(_gap_mask)
        _angular_gap_report["target_px"] = int(len(_gy))
        if len(_gy):
            _gdirs = DIRS[_gy, _gx]
            _gpoints = C[None, :] + _gdirs * 100.0
            _gbest = np.full(len(_gy), -1, np.int32)
            _gdot = np.full(len(_gy), -2.0, np.float32)
            _gpx = np.zeros(len(_gy), np.float32)
            _gpy = np.zeros(len(_gy), np.float32)
            for _gci in range(len(ring_cams)):
                _gK, (_ghh, _gww) = cals[_gci]
                _gR, _gt = poses_emc[_gci]
                _gcam = (_gR.T @ (_gpoints - _gt[None, :]).T).T
                _gz = _gcam[:, 2]
                _gu = (_gK[0, 0] * _gcam[:, 0] / np.maximum(_gz, 1e-6)).astype(np.float32) + _gK[0, 2]
                _gv = (_gK[1, 1] * _gcam[:, 1] / np.maximum(_gz, 1e-6)).astype(np.float32) + _gK[1, 2]
                _gok = (
                    (_gz > 0.1)
                    & (_gu >= 1)
                    & (_gu < _gww - 1)
                    & (_gv >= 1)
                    & (_gv < _ghh - 1)
                )
                _gaxis = _gR @ np.array([0.0, 0.0, 1.0])
                _ga = (_gdirs @ _gaxis).astype(np.float32)
                _gpick = _gok & (_ga > _gdot)
                _gbest[_gpick] = _gci
                _gdot[_gpick] = _ga[_gpick]
                _gpx[_gpick] = _gu[_gpick]
                _gpy[_gpick] = _gv[_gpick]
            _gfilled = 0
            for _gci, _gname in enumerate(ring_cams):
                _gsel = np.nonzero(_gbest == _gci)[0]
                if not len(_gsel):
                    continue
                _gimage = np.clip(
                    frame.images[_gname].astype(np.float32)
                    * np.exp(gains[_gci])[None, None, :].astype(np.float32),
                    0,
                    255,
                ).astype(np.uint8)
                comp[_gy[_gsel], _gx[_gsel]] = np.clip(
                    bilinear(_gimage, _gpx[_gsel], _gpy[_gsel]), 0, 255
                ).astype(np.uint8)
                _gfilled += int(len(_gsel))
            _angular_gap_report["filled_px"] = _gfilled
            _angular_gap_report["unfilled_px"] = int(len(_gy) - _gfilled)
    save_rgb(
        REMOTE_OUT / f"{run_name}_angular_gap_mask.png",
        np.dstack([_gap_mask.astype(np.uint8) * 255] * 3),
    )
    ground_stats["angular_gap_fallback"] = _angular_gap_report
    print("ANGULAR_GAP_FALLBACK", json.dumps(_angular_gap_report), flush=True)
"""
    source = marker_pattern.sub(block + markers[0].group(0), source, count=1)

    result_pattern = re.compile(
        r'^(?P<indent>\s*)"color_diag":\s*'
        r'(?P<value>[A-Za-z_][A-Za-z0-9_]*)\}$',
        re.MULTILINE,
    )
    matches = tuple(result_pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one generated color_diag result anchor, "
            f"found {len(matches)}"
        )
    match = matches[0]
    indent = match.group("indent")
    replacement = (
        f'{indent}"angular_gap_fallback": _angular_gap_report,\n'
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
    )
    if not angular_gap_fallback:
        return rendered
    return _inject_angular_gap_fallback(rendered)


__all__ = ["build_scene_band_renderer_source"]
