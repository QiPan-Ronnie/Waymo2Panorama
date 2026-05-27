"""Unit tests for LPAM-inspired seam-local alignment."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.blending.hard_hdr_of import hard_select  # noqa: E402
from waymo2panorama.blending.seam_local_align import (  # noqa: E402
    build_voronoi_seam_band,
    estimate_translation_ecc,
    seam_local_align_slabs,
)


def _stripe_image(h: int = 96, w: int = 128, shift_x: int = 0) -> np.ndarray:
    """Synthetic textured RGB slab with line structure for translation tests."""
    gray = np.zeros((h, w), dtype=np.uint8)
    for x in range(12, w, 24):
        cv2.line(gray, (x + shift_x, 0), (x + shift_x, h - 1), 180, 2)
    for y in range(10, h, 22):
        cv2.line(gray, (0, y), (w - 1, y), 90, 1)
    cv2.rectangle(gray, (42 + shift_x, 30), (70 + shift_x, 58), 230, -1)
    return np.repeat(gray[..., None], 3, axis=2).astype(np.float32)


def test_voronoi_seam_band_tracks_weight_equality_boundary() -> None:
    H, W = 32, 64
    x = np.linspace(1.0, 0.0, W, dtype=np.float32)
    weight_a = np.tile(x, (H, 1))
    weight_b = 1.0 - weight_a

    band, signed_distance = build_voronoi_seam_band(
        weight_a, weight_b, band_half_width=4, threshold=1e-6
    )

    assert band.shape == (H, W)
    assert signed_distance.shape == (H, W)
    assert band[:, 31:33].all(), "band should include the A/B equality boundary"
    assert not band[:, :20].any(), "band should not include far A-side pixels"
    assert not band[:, 44:].any(), "band should not include far B-side pixels"


def test_estimate_translation_ecc_recovers_small_local_shift() -> None:
    ref = _stripe_image()
    moved = _stripe_image(shift_x=5)

    result = estimate_translation_ecc(
        cv2.cvtColor(ref.astype(np.uint8), cv2.COLOR_RGB2GRAY),
        cv2.cvtColor(moved.astype(np.uint8), cv2.COLOR_RGB2GRAY),
        max_dx=12,
        max_dy=4,
        min_ncc_gain=0.02,
    )

    assert result.accepted, result.reason
    assert abs(result.dx + 5.0) <= 1.0
    assert abs(result.dy) <= 1.0
    assert result.ncc_after > result.ncc_before + 0.02


def test_seam_local_align_improves_shifted_pair_without_changing_far_pixels() -> None:
    H, W = 96, 128
    slab_a = _stripe_image(H, W, shift_x=0)
    slab_b = _stripe_image(H, W, shift_x=5)
    weight_a = np.zeros((H, W), dtype=np.float32)
    weight_b = np.zeros((H, W), dtype=np.float32)
    weight_a[:, :72] = 1.0
    weight_b[:, 56:] = 1.0
    # Soft crossing near the seam so build_voronoi_seam_band has a real boundary.
    ramp = np.linspace(1.0, 0.0, 16, dtype=np.float32)
    weight_a[:, 56:72] = ramp
    weight_b[:, 56:72] = 1.0 - ramp

    base = hard_select([slab_a, slab_b], [weight_a, weight_b])
    aligned_slabs, aligned_weights, diagnostics = seam_local_align_slabs(
        [slab_a, slab_b],
        [weight_a, weight_b],
        ring_pairs=[(0, 1)],
        band_half_width=10,
        tile_hw=(64, 48),
        stride_hw=(32, 24),
        max_dx=12,
        max_dy=4,
        min_ncc_gain=0.02,
        return_diagnostics=True,
    )
    aligned = hard_select(aligned_slabs, aligned_weights)

    seam_cols = slice(60, 78)
    before = np.mean(np.abs(base[:, seam_cols].astype(np.float32) - slab_a[:, seam_cols]))
    after = np.mean(np.abs(aligned[:, seam_cols].astype(np.float32) - slab_a[:, seam_cols]))
    assert after < before * 0.75
    assert diagnostics["pairs"][0]["accepted_tiles"] > 0
    # Outside the seam band on the A side, content should remain unchanged.
    assert np.allclose(aligned[:, :24], base[:, :24], atol=1.0)

