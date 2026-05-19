"""
Single-frame stitching: 7 ring cams (with calibration) -> 1 ERP image.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from waymo2panorama.blending.multiband import multiband_blend
from waymo2panorama.data_io.av2_loader import RING_CAMS_7, FrameSample
from waymo2panorama.projection.sphere_projection import render_camera_to_erp


def stitch_one_frame(
    frame: FrameSample,
    erp_hw: tuple[int, int] = (1024, 2048),
    num_bands: int = 5,
    ego_masks: Optional[dict[str, np.ndarray]] = None,
    wrap: bool = True,
) -> np.ndarray:
    """Stitch one synchronized frame's 7 ring cams into a single ERP image (uint8 HxWx3)."""
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in RING_CAMS_7:
        img = frame.images[cam]
        calib = frame.calibrations[cam]
        mask = ego_masks.get(cam) if ego_masks else None
        rgb, _alpha, w = render_camera_to_erp(
            image=img,
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            ego_mask=mask,
        )
        slabs.append(rgb)
        weights.append(w)
    return multiband_blend(slabs, weights, num_bands=num_bands, wrap=wrap)
