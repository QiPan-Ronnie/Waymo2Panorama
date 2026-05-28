from __future__ import annotations

import numpy as np

from waymo2panorama.projection.ground_plane_layer import (
    compose_ground_plane_hybrid,
    render_ground_plane_to_erp,
)


def _downward_camera() -> tuple[np.ndarray, np.ndarray]:
    K = np.array(
        [
            [32.0, 0.0, 32.0],
            [0.0, 32.0, 32.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    # cam +z -> ego -z, cam +x -> ego +y, cam +y -> ego +x
    R = np.array(
        [
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0],
        ],
        dtype=np.float64,
    )
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = R
    T[:3, 3] = [0.0, 0.0, 1.5]
    return K, T


def test_render_ground_plane_hits_below_equator() -> None:
    image = np.zeros((64, 64, 3), dtype=np.uint8)
    image[..., 0] = 220
    K, T = _downward_camera()
    render = render_ground_plane_to_erp(
        image=image,
        K=K,
        T_ego_cam=T,
        erp_hw=(128, 256),
        panorama_center_ego=np.array([0.0, 0.0, 1.5], dtype=np.float64),
        min_distance_m=0.2,
        max_distance_m=15.0,
    )
    assert render.alpha.any()
    ys, _xs = np.where(render.alpha)
    assert (ys > 64).mean() > 0.75
    assert float(render.weight[render.alpha].max()) > 0.0
    assert int(render.rgb[..., 0].max()) == 220


def test_compose_ground_plane_no_valid_ground_returns_base() -> None:
    base_a = np.zeros((16, 32, 3), dtype=np.float32)
    base_b = np.full((16, 32, 3), 100, dtype=np.float32)
    wa = np.zeros((16, 32), dtype=np.float32)
    wb = np.zeros((16, 32), dtype=np.float32)
    wa[:, :16] = 1.0
    wb[:, 16:] = 1.0
    ground_slabs = [np.full_like(base_a, 200), np.full_like(base_b, 220)]
    ground_weights = [np.zeros_like(wa), np.zeros_like(wb)]
    out, diag = compose_ground_plane_hybrid(
        [base_a, base_b],
        [wa, wb],
        ground_slabs,
        ground_weights,
        return_diagnostics=True,
    )
    expected = np.zeros((16, 32, 3), dtype=np.uint8)
    expected[:, 16:] = 100
    assert np.array_equal(out, expected)
    assert diag["replace_pixels"] == 0
