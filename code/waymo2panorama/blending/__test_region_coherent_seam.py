from __future__ import annotations

import cv2
import numpy as np

from waymo2panorama.blending.region_coherent_seam import (
    RegionCoherentConfig,
    blend_region_coherent_seam,
    protect_pair_regions,
)


def _lane_pair(h: int = 64, w: int = 96) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    slab_a = np.zeros((h, w, 3), dtype=np.float32)
    slab_b = np.zeros((h, w, 3), dtype=np.float32)
    slab_a[:] = [42, 42, 42]
    slab_b[:] = [48, 48, 48]
    cv2.line(slab_a, (8, 34), (w - 8, 30), (230, 230, 230), 4, cv2.LINE_AA)
    cv2.line(slab_b, (8, 39), (w - 8, 35), (230, 230, 230), 4, cv2.LINE_AA)
    x = np.linspace(1.0, 0.0, w, dtype=np.float32)[None, :]
    wa = np.repeat(x, h, axis=0)
    wb = 1.0 - wa
    return slab_a, slab_b, wa, wb


def test_region_coherent_blend_remains_hard_selected() -> None:
    slab_a, slab_b, wa, wb = _lane_pair()
    out, diag = blend_region_coherent_seam(
        [slab_a, slab_b],
        [wa, wb],
        ring_pairs=[(0, 1)],
        return_diagnostics=True,
        band_half_width=12,
        seam_dilate=9,
        min_component_area=8,
        max_component_area=5000,
    )
    a_u8 = np.clip(slab_a, 0, 255).astype(np.uint8)
    b_u8 = np.clip(slab_b, 0, 255).astype(np.uint8)
    from_a = np.all(out == a_u8, axis=2)
    from_b = np.all(out == b_u8, axis=2)
    assert np.all(from_a | from_b)
    assert diag["seam_mask_pixels"] > 0


def test_protect_pair_regions_makes_cut_structure_coherent() -> None:
    slab_a, slab_b, wa, wb = _lane_pair()
    h, w = wa.shape
    labels = np.zeros((h, w), dtype=np.int16)
    labels[:, w // 2 :] = 1
    band = np.zeros((h, w), dtype=bool)
    band[:, w // 2 - 12 : w // 2 + 12] = True
    seam = np.zeros((h, w), dtype=bool)
    seam[:, w // 2] = True

    new_label, protected, diag = protect_pair_regions(
        labels,
        [slab_a, slab_b],
        [wa, wb],
        (0, 1),
        band,
        seam,
        cfg=RegionCoherentConfig(
            band_half_width=12,
            seam_dilate=9,
            min_component_area=8,
            max_component_area=5000,
            max_component_width_frac=0.95,
        ),
    )
    assert diag["accepted_components"] >= 1
    assert protected.sum() > 0
    assert len(np.unique(new_label[protected])) == 1
