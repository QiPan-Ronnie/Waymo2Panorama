import numpy as np

from agent.db145_ground_operator.baseline import six_slot_median


def test_baseline_uses_nearest_six_distinct_sources_and_true_median():
    result = six_slot_median(
        texel_ids=np.zeros(8, dtype=np.int64),
        source_ids=np.arange(8, dtype=np.int64),
        ground_ranges=np.arange(8, dtype=np.float64),
        rgb=np.repeat(np.arange(8, dtype=np.float32)[:, None], 3, axis=1),
        grid_hw=(1, 1),
    )
    np.testing.assert_allclose(result.texture_rgb[0, 0], [2.5, 2.5, 2.5])
    assert result.source_count[0, 0] == 6
    assert result.valid[0, 0]


def test_baseline_is_order_invariant_and_deduplicates_source():
    cells = np.array([0, 0, 0, 1])
    sources = np.array([4, 4, 7, 8])
    ranges = np.array([9.0, 2.0, 3.0, 1.0])
    rgb = np.array([[0.9] * 3, [0.2] * 3, [0.6] * 3, [1.0] * 3])
    a = six_slot_median(cells, sources, ranges, rgb, grid_hw=(1, 3))
    order = np.array([3, 0, 2, 1])
    b = six_slot_median(
        cells[order], sources[order], ranges[order], rgb[order], grid_hw=(1, 3)
    )
    np.testing.assert_array_equal(a.texture_rgb, b.texture_rgb)
    np.testing.assert_array_equal(a.source_count, b.source_count)
    np.testing.assert_allclose(a.texture_rgb[0, 0], [0.4] * 3)
    assert not a.valid[0, 2]
