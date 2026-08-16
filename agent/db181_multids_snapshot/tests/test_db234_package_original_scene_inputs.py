from __future__ import annotations

from pathlib import Path

from agent.db234_package_original_scene_inputs import select_nearest_path


def test_select_nearest_path_uses_numeric_timestamps() -> None:
    paths = [Path("100.jpg"), Path("9.jpg"), Path("20.jpg")]

    selected = select_nearest_path(paths, 18)

    assert selected.name == "20.jpg"


def test_select_nearest_path_tie_chooses_smaller_timestamp() -> None:
    paths = [Path("30.jpg"), Path("10.jpg")]

    selected = select_nearest_path(paths, 20)

    assert selected.name == "10.jpg"
