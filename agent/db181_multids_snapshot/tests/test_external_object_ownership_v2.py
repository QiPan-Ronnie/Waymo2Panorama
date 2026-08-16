from __future__ import annotations

import numpy as np

from agent.db181_multids.external_object_ownership import (
    enforce_dominant_single_source_objects,
)


def _base() -> tuple[np.ndarray, np.ndarray]:
    bestcam = np.zeros((20, 30), np.int8)
    bestcam[:, 15:] = 1
    valid = np.ones((2, 20, 30), bool)
    return bestcam, valid


def _run(bestcam: np.ndarray, valid: np.ndarray, objects: np.ndarray, **changes):
    return enforce_dominant_single_source_objects(
        bestcam,
        valid,
        objects,
        dominance_ratio=2.5,
        dilation_px=2,
        min_object_px=8,
        max_component_fraction=0.5,
        **changes,
    )


def test_dominant_complete_view_owns_whole_boundary_object() -> None:
    bestcam, valid = _base()
    objects = np.zeros_like(valid)
    objects[0, 6:14, 10:19] = True
    objects[1, 8:12, 16:19] = True

    fixed, report = _run(bestcam, valid, objects)

    assert np.all(fixed[6:14, 10:19] == 0)
    assert report["components_reassigned"] == 1
    assert report["changed_px"] > 0
    assert np.array_equal(bestcam[:, :10], fixed[:, :10])


def test_balanced_cross_camera_object_is_not_forced_to_one_view() -> None:
    bestcam, valid = _base()
    objects = np.zeros_like(valid)
    objects[0, 6:14, 10:17] = True
    objects[1, 6:14, 14:21] = True

    fixed, report = _run(bestcam, valid, objects)

    assert np.array_equal(fixed, bestcam)
    assert report["components_reassigned"] == 0


def test_object_away_from_camera_boundary_is_unchanged() -> None:
    bestcam, valid = _base()
    objects = np.zeros_like(valid)
    objects[0, 5:10, 2:8] = True

    fixed, report = _run(bestcam, valid, objects)

    assert np.array_equal(fixed, bestcam)
    assert report["components_reassigned"] == 0


def test_owner_must_cover_nearly_all_of_component() -> None:
    bestcam, valid = _base()
    objects = np.zeros_like(valid)
    objects[0, 6:14, 10:19] = True
    objects[1, 8:12, 16:19] = True
    valid[0, :, 16:] = False

    fixed, report = _run(
        bestcam,
        valid,
        objects,
        min_owner_valid_fraction=0.9,
    )

    assert np.array_equal(fixed, bestcam)
    assert report["components_reassigned"] == 0
