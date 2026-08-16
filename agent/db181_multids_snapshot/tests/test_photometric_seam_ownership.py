from __future__ import annotations

import numpy as np

from agent.db181_multids.photometric_seam_ownership import (
    optimize_photometric_ownership_seams,
)


def _base_case() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    height, width = 80, 100
    bestcam = np.zeros((height, width), np.int8)
    bestcam[:, 50:] = 1
    valid = np.ones((2, height, width), bool)
    rgb = np.full((2, height, width, 3), 100, np.uint8)
    return bestcam, valid, rgb


def test_moves_seam_around_misaligned_object_without_blending() -> None:
    bestcam, valid, rgb = _base_case()
    rgb[0, 20:61, 25:50] = 20
    rgb[1, 20:61, 50:75] = 20

    fixed, report = optimize_photometric_ownership_seams(
        bestcam,
        valid,
        rgb,
        max_shift_px=35,
        max_step_px=5,
        min_boundary_rows=8,
        min_relative_improvement=0.15,
    )

    assert report["seams_optimized"] == 1
    assert report["changed_px"] > 0
    path = report["seams"][0]
    assert path["new_mean_cost"] < 0.2 * path["old_mean_cost"]
    # Through the moving object rows, the ownership boundary leaves the
    # disagreement interval rather than cutting between the two copies.
    for row in range(25, 56):
        transitions = np.flatnonzero(fixed[row, 1:] != fixed[row, :-1]) + 1
        assert len(transitions) == 1
        assert transitions[0] <= 25 or transitions[0] >= 75


def test_identical_views_keep_original_ownership_exactly() -> None:
    bestcam, valid, rgb = _base_case()
    fixed, report = optimize_photometric_ownership_seams(bestcam, valid, rgb)
    np.testing.assert_array_equal(fixed, bestcam)
    assert report["seams_optimized"] == 0
    assert report["changed_px"] == 0


def test_never_reassigns_to_an_invalid_camera() -> None:
    bestcam, valid, rgb = _base_case()
    rgb[0, 20:61, 25:50] = 20
    rgb[1, 20:61, 50:75] = 20
    valid[1, 20:61, :50] = False
    valid[0, 20:61, 50:] = False

    fixed, _ = optimize_photometric_ownership_seams(
        bestcam,
        valid,
        rgb,
        max_shift_px=35,
        min_relative_improvement=0.15,
    )
    for camera in range(2):
        assert np.all(valid[camera][fixed == camera])
