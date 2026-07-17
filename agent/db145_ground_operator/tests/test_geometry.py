import numpy as np

from agent.db145_ground_operator.config import ExperimentConfig
from agent.db145_ground_operator.geometry import (
    intersect_rays_with_plane,
    pixel_footprint_on_plane,
    project_city_to_image,
)


def test_intersect_rays_with_horizontal_plane():
    origins = np.array([[0.0, 0.0, 2.0]])
    rays = np.array([[0.0, 1.0, -1.0]])
    xyz, valid = intersect_rays_with_plane(origins, rays, [0.0, 0.0, 1.0], 0.0)
    assert valid.tolist() == [True]
    np.testing.assert_allclose(xyz[0], [0.0, 2.0, 0.0], atol=1e-8)


def test_behind_camera_intersection_is_invalid():
    xyz, valid = intersect_rays_with_plane(
        [0.0, 0.0, 2.0], [0.0, 1.0, 1.0], [0.0, 0.0, 1.0], 0.0
    )
    assert not valid[0]
    assert np.isnan(xyz[0]).all()


def _synthetic_camera() -> tuple[np.ndarray, np.ndarray]:
    K = np.array(
        [[1000.0, 0.0, 1000.0], [0.0, 1000.0, 775.0], [0.0, 0.0, 1.0]]
    )
    # OpenCV camera x-right/y-down/z-forward to city x-right/y-forward/z-up.
    T_city_cam = np.eye(4)
    T_city_cam[:3, :3] = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
    T_city_cam[:3, 3] = [0.0, 0.0, 2.0]
    return K, T_city_cam


def test_grazing_pixel_footprint_is_anisotropic():
    K, T = _synthetic_camera()
    permissive = ExperimentConfig(
        min_source_range_m=0.1,
        max_source_range_m=100.0,
        max_footprint_aspect=1000.0,
        max_footprint_area_m2=100.0,
    )
    fp = pixel_footprint_on_plane(
        np.array([1000.0, 875.0]), K, T, [0.0, 0.0, 1.0], 0.0, config=permissive
    )
    assert fp.valid, fp.reason
    assert fp.aspect_ratio > 5.0
    assert fp.area_m2 > 0.0
    np.testing.assert_allclose(fp.center_xy, [0.0, 20.0], atol=1e-8)


def test_projection_is_inverse_of_centre_ray_intersection():
    K, T = _synthetic_camera()
    permissive = ExperimentConfig(min_source_range_m=0.1, max_source_range_m=100.0)
    fp = pixel_footprint_on_plane(
        [1040.0, 975.0], K, T, [0.0, 0.0, 1.0], 0.0, config=permissive
    )
    assert fp.valid, fp.reason
    uv, valid = project_city_to_image([*fp.center_xy, 0.0], K, T)
    assert valid
    np.testing.assert_allclose(uv, [1040.0, 975.0], atol=1e-7)
