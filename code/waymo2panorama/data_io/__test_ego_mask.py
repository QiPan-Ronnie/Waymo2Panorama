"""
Unit tests for ego_mask.py — heuristic AV2 ring-cam ego masks (WS1.2).

These tests verify the *contract* of make_av2_ego_mask (shape, dtype, value
layout for each cam orientation). They do NOT verify that the masked ROIs
actually cover the correct hardware in real imagery — that is the job of the
Colab anchor-60 verify run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path wiring — allow running from repo root without installation
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()  # code/waymo2panorama/data_io -> code/
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.data_io.av2_loader import RING_CAMS_7  # noqa: E402
from waymo2panorama.data_io.ego_mask import (  # noqa: E402
    make_av2_ego_mask,
    make_waymo_ego_mask,
)


# ---------------------------------------------------------------------------
# Per-cam expected source-image shapes (H, W)
# front_center is the only portrait cam (2048 H x 1550 W); others are
# landscape (1550 H x 2048 W). See av2_loader.py module docstring.
# ---------------------------------------------------------------------------

_EXPECTED_HW: dict[str, tuple[int, int]] = {
    "ring_front_center": (2048, 1550),
    "ring_front_left":   (1550, 2048),
    "ring_side_left":    (1550, 2048),
    "ring_rear_left":    (1550, 2048),
    "ring_rear_right":   (1550, 2048),
    "ring_side_right":   (1550, 2048),
    "ring_front_right":  (1550, 2048),
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cam", RING_CAMS_7)
def test_av2_mask_correct_shape_per_cam(cam: str) -> None:
    """Mask shape must equal the (H, W) passed in (no transposition)."""
    hw = _EXPECTED_HW[cam]
    mask = make_av2_ego_mask(cam, hw)
    assert mask is not None
    assert mask.shape == hw, f"{cam}: expected {hw}, got {mask.shape}"


@pytest.mark.parametrize("cam", RING_CAMS_7)
def test_av2_mask_dtype_uint8(cam: str) -> None:
    """Mask dtype must be uint8 so cv2.remap inside cylinder.py treats it correctly."""
    hw = _EXPECTED_HW[cam]
    mask = make_av2_ego_mask(cam, hw)
    assert mask is not None
    assert mask.dtype == np.uint8


@pytest.mark.parametrize("cam", RING_CAMS_7)
def test_av2_mask_top_rows_zero(cam: str) -> None:
    """Top 3% of rows should be fully zeroed for every ring cam."""
    h, w = _EXPECTED_HW[cam]
    mask = make_av2_ego_mask(cam, (h, w))
    assert mask is not None
    k_top = int(round(h * 0.03))
    assert k_top >= 1, f"sanity: h={h} should give at least 1 top row at 3%"
    assert mask[:k_top, :].sum() == 0, (
        f"{cam}: top {k_top} rows should be all zero, got "
        f"sum={int(mask[:k_top, :].sum())}"
    )


@pytest.mark.parametrize("cam", RING_CAMS_7)
def test_av2_mask_body_rows_one(cam: str) -> None:
    """Middle rows (row H//2) should be fully kept (== 1)."""
    h, w = _EXPECTED_HW[cam]
    mask = make_av2_ego_mask(cam, (h, w))
    assert mask is not None
    mid_row = mask[h // 2, :]
    assert mid_row.min() == 1, (
        f"{cam}: middle row should be all 1s, got min={int(mid_row.min())}"
    )


def test_av2_mask_front_center_bottom_zero() -> None:
    """Portrait ring_front_center should have its bottom 5% (hood) zeroed."""
    h, w = _EXPECTED_HW["ring_front_center"]
    mask = make_av2_ego_mask("ring_front_center", (h, w))
    assert mask is not None
    k_bot = int(round(h * 0.05))
    assert k_bot >= 1
    assert mask[h - k_bot :, :].sum() == 0, (
        f"front_center: bottom {k_bot} rows should be 0 (hood), got "
        f"sum={int(mask[h - k_bot :, :].sum())}"
    )


@pytest.mark.parametrize("cam", ["ring_rear_left", "ring_rear_right"])
def test_av2_mask_rear_bottom_zero(cam: str) -> None:
    """Rear cams should have their bottom 8% (vehicle body) zeroed."""
    h, w = _EXPECTED_HW[cam]
    mask = make_av2_ego_mask(cam, (h, w))
    assert mask is not None
    k_bot = int(round(h * 0.08))
    assert mask[h - k_bot :, :].sum() == 0


def test_av2_mask_side_cams_bottom_kept() -> None:
    """Side / front-left/right cams should NOT have bottom rows masked
    (they don't see the hood or rear body)."""
    for cam in ("ring_front_left", "ring_front_right",
                "ring_side_left", "ring_side_right"):
        h, w = _EXPECTED_HW[cam]
        mask = make_av2_ego_mask(cam, (h, w))
        assert mask is not None
        # Bottom 5% should still be 1 for these cams.
        k = int(round(h * 0.05))
        assert mask[h - k :, :].min() == 1, (
            f"{cam}: bottom {k} rows should be kept (== 1), got "
            f"min={int(mask[h - k :, :].min())}"
        )


def test_av2_mask_returns_none_for_unknown_cam() -> None:
    """Unknown cam name (e.g. stereo cams, typos) should return None."""
    assert make_av2_ego_mask("camera_xyz", (1550, 2048)) is None
    assert make_av2_ego_mask("stereo_front_left", (1550, 2048)) is None
    assert make_av2_ego_mask("", (1550, 2048)) is None


def test_waymo_mask_raises_not_implemented() -> None:
    """Waymo stub must raise NotImplementedError (placeholder for teammate)."""
    with pytest.raises(NotImplementedError):
        make_waymo_ego_mask("front", (1280, 1920))
