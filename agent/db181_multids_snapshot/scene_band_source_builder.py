from __future__ import annotations

import json
import re
from pathlib import PurePath

from .scene_band_policy import SceneBandPolicy, apply_policy_to_db89_source


def _replace_plain_assignment_once(source: str, name: str, expression: str) -> str:
    pattern = re.compile(
        rf"^(?P<prefix>\s*{re.escape(name)}\s*=\s*)"
        rf"(?P<value>[^#;\r\n]*?)(?P<suffix>\s*(?:#.*)?)$",
        re.MULTILINE,
    )
    matches = tuple(pattern.finditer(source))
    if not matches:
        raise ValueError(f"missing DB89 assignment for {name}")
    if len(matches) != 1:
        raise ValueError(f"duplicate DB89 assignment for {name}: {len(matches)}")
    return pattern.sub(
        lambda match: match.group("prefix") + expression + match.group("suffix"),
        source,
        count=1,
    )


def _replace_remote_paths(
    source: str, *, output_root: str, result_name: str
) -> str:
    if not result_name or PurePath(result_name).name != result_name:
        raise ValueError("result_name must be one basename")
    pattern = re.compile(
        r"^(?P<indent>\s*)REMOTE_OUT\s*=\s*[^;\r\n]+;\s*"
        r"REMOTE_RESULT\s*=\s*[^\r\n]+$",
        re.MULTILINE,
    )
    matches = tuple(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one REMOTE_OUT/REMOTE_RESULT compound assignment, "
            f"found {len(matches)}"
        )
    replacement = (
        f'{matches[0].group("indent")}REMOTE_OUT = pathlib.Path({json.dumps(output_root)}); '
        f'REMOTE_RESULT = REMOTE_OUT / {json.dumps(result_name)}'
    )
    return pattern.sub(replacement, source, count=1)


def build_scene_band_renderer_source(
    source: str,
    *,
    policy: SceneBandPolicy,
    data_root: str,
    output_root: str,
    result_name: str,
) -> str:
    rendered = apply_policy_to_db89_source(source, policy)
    rendered = _replace_plain_assignment_once(
        rendered, "DATA_ROOT", f"pathlib.Path({json.dumps(data_root)})"
    )
    return _replace_remote_paths(
        rendered,
        output_root=output_root,
        result_name=result_name,
    )


__all__ = ["build_scene_band_renderer_source"]
