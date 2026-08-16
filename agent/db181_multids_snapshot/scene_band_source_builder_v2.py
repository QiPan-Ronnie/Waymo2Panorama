from __future__ import annotations

import json
import re

from .scene_band_policy import SceneBandPolicy
from .scene_band_source_builder import (
    build_scene_band_renderer_source as _build_scene_band_renderer_source,
)


def _pin_generated_code_tree(source: str, code_root: str) -> str:
    pattern = re.compile(
        r"^(?P<indent>\s*)sys\.path\.insert\(0,\s*[^\r\n;]+/scripts/phase3[^\r\n;]*\);\s*"
        r"sys\.path\.insert\(0,\s*[^\r\n;]+/code[^\r\n;]*\)$",
        re.MULTILINE,
    )
    matches = tuple(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one generated code-tree insertion, "
            f"found {len(matches)}"
        )
    phase3 = f"{code_root.rstrip('/')}/scripts/phase3"
    code = f"{code_root.rstrip('/')}/code"
    replacement = (
        f'{matches[0].group("indent")}sys.path.insert(0, {json.dumps(phase3)}); '
        f'sys.path.insert(0, {json.dumps(code)})'
    )
    return pattern.sub(replacement, source, count=1)


def build_scene_band_renderer_source(
    source: str,
    *,
    policy: SceneBandPolicy,
    data_root: str,
    output_root: str,
    result_name: str,
    code_root: str,
) -> str:
    rendered = _build_scene_band_renderer_source(
        source,
        policy=policy,
        data_root=data_root,
        output_root=output_root,
        result_name=result_name,
    )
    return _pin_generated_code_tree(rendered, code_root)


__all__ = ["build_scene_band_renderer_source"]

