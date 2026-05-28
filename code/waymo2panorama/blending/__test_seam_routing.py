from __future__ import annotations

import numpy as np

from waymo2panorama.blending.hard_hdr_of import hard_select
from waymo2panorama.blending.seam_routing import (
    SeamRouteConfig,
    blend_seam_routing,
    solve_dp_seam,
)


def test_solve_dp_seam_respects_valid_band():
    cost = np.ones((12, 15), dtype=np.float32)
    valid = np.zeros_like(cost, dtype=bool)
    valid[:, 5:10] = True
    cost[:, 7] = 10.0
    path, ok = solve_dp_seam(cost, valid, max_step=2)
    assert ok.sum() == 12
    assert np.all((path[ok] >= 5) & (path[ok] < 10))
    assert not np.any(path[ok] == 7)


def test_blend_seam_routing_preserves_shape_and_dtype():
    h, w = 32, 64
    yy, xx = np.mgrid[:h, :w]
    slab_a = np.dstack([np.full((h, w), 180), xx * 2, yy * 4]).astype(np.float32)
    slab_b = np.dstack([xx * 2, np.full((h, w), 160), yy * 4]).astype(np.float32)
    wa = np.clip(1.0 - (xx - 20) / 24.0, 0, 1).astype(np.float32)
    wb = np.clip((xx - 20) / 24.0, 0, 1).astype(np.float32)
    out, diag = blend_seam_routing(
        [slab_a, slab_b],
        [wa, wb],
        ring_pairs=[(0, 1)],
        return_diagnostics=True,
        band_half_width=8,
        max_step=2,
    )
    assert out.shape == slab_a.shape
    assert out.dtype == np.uint8
    assert diag["seam_mask_pixels"] > 0
    assert diag["pairs"][0]["status"] == "ok"


def test_blend_seam_routing_is_hard_selected():
    h, w = 24, 48
    rng = np.random.default_rng(0)
    slab_a = rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8).astype(np.float32)
    slab_b = rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8).astype(np.float32)
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    wa = np.repeat(1.0 - x, h, axis=0)
    wb = np.repeat(x, h, axis=0)
    out = blend_seam_routing(
        [slab_a, slab_b],
        [wa, wb],
        ring_pairs=[(0, 1)],
        band_half_width=6,
    )
    a_u8 = slab_a.astype(np.uint8)
    b_u8 = slab_b.astype(np.uint8)
    from_a = np.all(out == a_u8, axis=2)
    from_b = np.all(out == b_u8, axis=2)
    assert np.all(from_a | from_b)
    # It should still be a hard selector, not a blended average.
    assert not np.any((~from_a) & (~from_b))


def test_blend_seam_routing_accepts_external_cost():
    h, w = 32, 64
    yy, xx = np.mgrid[:h, :w]
    slab_a = np.dstack([np.full((h, w), 150), xx * 2, yy * 4]).astype(np.float32)
    slab_b = np.dstack([xx * 2, np.full((h, w), 140), yy * 4]).astype(np.float32)
    wa = np.clip(1.0 - (xx - 20) / 24.0, 0, 1).astype(np.float32)
    wb = np.clip((xx - 20) / 24.0, 0, 1).astype(np.float32)
    external = np.zeros((h, w), dtype=np.float32)
    external[:, 30:34] = 1.0

    out, diag = blend_seam_routing(
        [slab_a, slab_b],
        [wa, wb],
        ring_pairs=[(0, 1)],
        return_diagnostics=True,
        band_half_width=12,
        max_step=3,
        external_cost=external,
        external_weight=4.0,
    )
    assert out.shape == slab_a.shape
    assert diag["pairs"][0]["external_weight"] == 4.0
    assert diag["seam_mask_pixels"] > 0
