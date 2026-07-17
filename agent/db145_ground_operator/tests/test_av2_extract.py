from pathlib import Path

import numpy as np
import pandas as pd

from agent.db145_ground_operator.av2_extract import (
    Box3D,
    GroundPatch,
    PoseTable,
    SourceView,
    _occluded_by_boxes,
    _raw_pixels_for_view,
)
from agent.db145_ground_operator.config import ExperimentConfig


def _pose_table():
    return PoseTable(
        pd.DataFrame(
            {
                "timestamp_ns": [0, 1_000_000_000],
                "tx_m": [0.0, 1.0],
                "ty_m": [0.0, 0.0],
                "tz_m": [2.0, 2.0],
                "qx": [0.0, 0.0],
                "qy": [0.0, 0.0],
                "qz": [0.0, 0.0],
                "qw": [1.0, 1.0],
            }
        )
    )


def _view():
    K = np.array([[50.0, 0, 50.0], [0, 50.0, 50.0], [0, 0, 1.0]])
    T = np.eye(4)
    T[:3, :3] = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
    T[:3, 3] = [0, 0, 2]
    return SourceView(0, 0, 0, "fake", 0, Path("fake.jpg"), K, T, 100, 100)


def test_pose_interpolation_keeps_translation_and_rotation_contract():
    rotation, translation = _pose_table().at(500_000_000)
    np.testing.assert_allclose(translation, [0.5, 0.0, 2.0])
    np.testing.assert_allclose(rotation, np.eye(3))


def test_segment_box_occlusion_rejects_object_before_ground():
    box = Box3D(np.array([0.0, 3.0, 1.0]), np.array([2.0, 2.0, 2.0]), np.eye(3), "car")
    blocked = _occluded_by_boxes(
        np.array([0.0, 0.0, 2.0]),
        np.array([[0.0, 6.0, 0.0], [5.0, 6.0, 0.0]]),
        [box],
    )
    assert blocked.tolist() == [True, False]


def test_raw_records_retain_pixel_coordinates_and_anisotropic_covariance():
    config = ExperimentConfig(
        patch_size_m=2.0,
        grid_hw=80,
        min_source_range_m=0.1,
        max_source_range_m=100,
        max_footprint_area_m2=100,
        max_footprint_aspect=1000,
    )
    patch = GroundPatch("p", (-0.5, 8.0), (0, 0, 1), 0.0, 0.0, 0)
    image = np.full((100, 100, 3), 127, np.uint8)
    records = _raw_pixels_for_view(image, patch, _view(), (), config)
    assert records is not None
    assert len(records["u"]) == len(records["rgb"]) > 0
    assert np.median(records["aspect"]) > 1.0
    assert records["centers_cell"].shape[1] == 2
