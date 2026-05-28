import numpy as np

from waymo2panorama.blending.dit_oracle_source import (
    DiTOracleConfig,
    blend_dit_oracle_source,
)


def test_dit_oracle_source_remains_source_faithful():
    h, w = 32, 48
    slab_a = np.zeros((h, w, 3), dtype=np.uint8)
    slab_b = np.full((h, w, 3), 100, dtype=np.uint8)
    weights = [
        np.full((h, w), 0.8, dtype=np.float32),
        np.full((h, w), 0.6, dtype=np.float32),
    ]
    target = slab_b.copy()
    mask = np.zeros((h, w), dtype=np.uint8)

    out, diag = blend_dit_oracle_source(
        [slab_a, slab_b],
        weights,
        target,
        preserve_mask=mask,
        config=DiTOracleConfig(
            weight_penalty=0.0,
            stay_penalty=0.0,
            switch_margin=1.0,
            min_component_area=4,
            min_component_gain=1.0,
            max_changed_frac=1.0,
        ),
        return_diagnostics=True,
    )

    assert diag["selected_change_pixels"] == h * w
    assert np.all(out == slab_b)


def test_dit_oracle_source_identity_when_no_overlap():
    h, w = 20, 24
    slab_a = np.full((h, w, 3), 30, dtype=np.uint8)
    slab_b = np.full((h, w, 3), 200, dtype=np.uint8)
    weights = [
        np.ones((h, w), dtype=np.float32),
        np.zeros((h, w), dtype=np.float32),
    ]
    target = slab_b.copy()
    mask = np.zeros((h, w), dtype=np.uint8)

    out, diag = blend_dit_oracle_source(
        [slab_a, slab_b],
        weights,
        target,
        preserve_mask=mask,
        config=DiTOracleConfig(max_changed_frac=1.0),
        return_diagnostics=True,
    )

    assert diag["selected_change_pixels"] == 0
    assert np.all(out == slab_a)
