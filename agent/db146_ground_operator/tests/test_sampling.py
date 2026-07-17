import numpy as np

from agent.db145_ground_operator.av2_extract import ObservationArrays
from agent.db146_ground_operator.sampling import bound_observations


def _observations(counts: tuple[int, ...]) -> ObservationArrays:
    sources = np.concatenate(
        [np.full(count, source, np.int64) for source, count in enumerate(counts)]
    )
    n = len(sources)
    index = np.arange(n)
    return ObservationArrays(
        centers_cell=np.column_stack((index, index)).astype(np.float32),
        covariance_cell=np.repeat(np.eye(2, dtype=np.float32)[None], n, axis=0),
        source_ids=sources,
        rgb=np.column_stack((index, index + 1, index + 2)).astype(np.float32),
        provenance={
            "u": (index % 11).astype(np.int32),
            "v": (index // 11).astype(np.int32),
            "original_index": index,
        },
    )


def test_budget_is_exact_and_balanced_across_source_views():
    bounded, report = bound_observations(_observations((100, 30, 10)), max_observations=60)
    assert len(bounded.rgb) == 60
    assert report.kept_by_source == {"0": 25, "1": 25, "2": 10}
    assert report.selection == "geometry_only_equal_source_spatial_raster"


def test_sampling_is_deterministic_and_does_not_look_at_rgb():
    observations = _observations((100, 100))
    changed_rgb = ObservationArrays(
        observations.centers_cell,
        observations.covariance_cell,
        observations.source_ids,
        observations.rgb[::-1].copy(),
        observations.provenance,
    )
    first, _ = bound_observations(observations, max_observations=40)
    second, _ = bound_observations(changed_rgb, max_observations=40)
    np.testing.assert_array_equal(
        first.provenance["original_index"], second.provenance["original_index"]
    )
    assert len(np.unique(first.provenance["v"])) > 2


def test_identity_path_preserves_all_aligned_arrays():
    observations = _observations((3, 2))
    bounded, report = bound_observations(observations, max_observations=10)
    np.testing.assert_array_equal(bounded.rgb, observations.rgb)
    np.testing.assert_array_equal(
        bounded.provenance["original_index"],
        observations.provenance["original_index"],
    )
    assert report.selection == "identity"
