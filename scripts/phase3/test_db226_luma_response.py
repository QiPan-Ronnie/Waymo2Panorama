import inspect
import json

import numpy as np
import pytest


def test_collect_pair_samples_preserves_versioned_raw_contract():
    from scripts.phase3.db226_luma_response import (
        RAW_PAIR_SCHEMA_VERSION,
        collect_pair_samples,
    )

    rgb_a = np.array([[12.5, 24.0, 48.25], [64.0, 80.5, 96.0]], dtype=np.float32)
    rgb_b = np.array([[15.0, 30.0, 60.0], [72.0, 90.0, 108.0]], dtype=np.float32)
    rgb_a_before = rgb_a.copy()
    rgb_b_before = rgb_b.copy()
    erp_flat_index = np.array([7, 19])
    xy_a = np.array([[0.1, 0.2], [0.3, 0.4]])
    xy_b = np.array([[0.8, 0.2], [0.6, 0.4]])
    depth_m = np.array([8.0, 16.0])
    parallax_deg = np.array([0.5, 1.25])

    samples = collect_pair_samples(
        rgb_a=rgb_a,
        rgb_b=rgb_b,
        erp_flat_index=erp_flat_index,
        xy_a=xy_a,
        xy_b=xy_b,
        depth_m=depth_m,
        parallax_deg=parallax_deg,
    )

    assert samples.schema_version == RAW_PAIR_SCHEMA_VERSION
    np.testing.assert_array_equal(samples.rgb_a, rgb_a_before)
    np.testing.assert_array_equal(samples.rgb_b, rgb_b_before)
    np.testing.assert_array_equal(samples.erp_flat_index, erp_flat_index)
    np.testing.assert_array_equal(samples.xy_a, xy_a)
    np.testing.assert_array_equal(samples.xy_b, xy_b)
    np.testing.assert_array_equal(samples.depth_m, depth_m)
    np.testing.assert_array_equal(samples.parallax_deg, parallax_deg)
    np.testing.assert_array_equal(rgb_a, rgb_a_before)
    np.testing.assert_array_equal(rgb_b, rgb_b_before)
    assert not np.shares_memory(samples.rgb_a, rgb_a)
    assert not np.shares_memory(samples.rgb_b, rgb_b)
    assert all("gain" not in name for name in inspect.signature(collect_pair_samples).parameters)


def _valid_pair_inputs() -> dict[str, np.ndarray]:
    return {
        "rgb_a": np.full((2, 3), 40.0, dtype=np.float32),
        "rgb_b": np.full((2, 3), 50.0, dtype=np.float32),
        "erp_flat_index": np.array([3, 9]),
        "xy_a": np.array([[0.1, 0.2], [0.3, 0.4]]),
        "xy_b": np.array([[0.8, 0.2], [0.6, 0.4]]),
        "depth_m": np.array([5.0, 10.0]),
        "parallax_deg": np.array([0.5, 1.0]),
    }


def _direct_pair_samples(**changes: object):
    from scripts.phase3.db226_luma_response import PairSamples, RAW_PAIR_SCHEMA_VERSION

    inputs: dict[str, object] = _valid_pair_inputs()
    inputs.update({name: value for name, value in changes.items() if name != "schema_version"})
    return PairSamples(
        schema_version=str(changes.get("schema_version", RAW_PAIR_SCHEMA_VERSION)),
        **inputs,
    )


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("rgb_a", np.ones((2, 2), dtype=np.float32), "rgb_a"),
        ("rgb_b", np.ones((1, 3), dtype=np.float32), "rgb_b"),
        ("rgb_a", np.ones((2, 3), dtype=np.uint8), "floating"),
        ("erp_flat_index", np.array([[3], [9]]), "erp_flat_index"),
        ("erp_flat_index", np.array([3.0, 9.0]), "integer"),
        ("xy_a", np.ones((2, 3)), "xy_a"),
        ("xy_b", np.ones((1, 2)), "xy_b"),
        ("depth_m", np.array([5.0]), "depth_m"),
        ("parallax_deg", np.array([0.5, np.nan]), "finite"),
    ],
)
def test_collect_pair_samples_rejects_invalid_shape_length_or_values(
    field: str,
    bad_value: np.ndarray,
    message: str,
):
    from scripts.phase3.db226_luma_response import collect_pair_samples

    inputs = _valid_pair_inputs()
    inputs[field] = bad_value

    with pytest.raises(ValueError, match=message):
        collect_pair_samples(**inputs)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_version": "db226.raw_same_ray.v0"}, "schema"),
        ({"rgb_a": np.ones((2, 2), dtype=float)}, "rgb_a"),
        ({"rgb_b": np.array([[40.0, 40.0, np.nan], [40.0, 40.0, 40.0]])}, "finite"),
        ({"rgb_a": np.ones((2, 3), dtype=np.uint8)}, "floating"),
        ({"rgb_a": np.full((2, 3), -0.1)}, "code-value"),
        ({"rgb_b": np.full((2, 3), 256.0)}, "code-value"),
        ({"erp_flat_index": np.array([-1, 2])}, "nonnegative"),
        ({"erp_flat_index": np.array([1.0, 2.0])}, "integer"),
        ({"xy_a": np.array([[-0.1, 0.5], [0.5, 0.5]])}, r"\[0, 1\]"),
        ({"xy_b": np.array([[0.5, 1.1], [0.5, 0.5]])}, r"\[0, 1\]"),
        ({"depth_m": np.array([0.0, 1.0])}, "positive"),
        ({"parallax_deg": np.array([-0.1, 1.0])}, "nonnegative"),
    ],
)
def test_direct_pair_samples_rejects_malformed_contract(changes: dict[str, object], message: str):
    with pytest.raises(ValueError, match=message):
        _direct_pair_samples(**changes)


def test_direct_pair_samples_owns_read_only_array_copies():
    inputs = _valid_pair_inputs()

    samples = _direct_pair_samples(**inputs)

    for name, source in inputs.items():
        stored = getattr(samples, name)
        np.testing.assert_array_equal(stored, source)
        assert not np.shares_memory(stored, source)
        assert stored.flags.writeable is False
        with pytest.raises(ValueError, match="read-only"):
            stored.flat[0] = stored.flat[0]


def test_default_log_luma_edges_are_fixed_absolute_code_values():
    from scripts.phase3.db226_luma_response import DEFAULT_LOG_LUMA_EDGES

    expected_code_values = np.array([1, 4, 8, 16, 32, 64, 96, 128, 160, 192, 224, 256])

    np.testing.assert_allclose(DEFAULT_LOG_LUMA_EDGES, np.log(expected_code_values))


def _synthetic_samples(
    luma_a: np.ndarray,
    signed_residual: np.ndarray,
    *,
    parallax_deg: np.ndarray | None = None,
):
    from scripts.phase3.db226_luma_response import collect_pair_samples

    luma_a = np.asarray(luma_a, dtype=np.float64)
    residual = np.asarray(signed_residual, dtype=np.float64)
    rgb_a = np.repeat(luma_a[:, None], 3, axis=1)
    rgb_b = rgb_a * np.exp(residual[:, None])
    sample_count = len(luma_a)
    return collect_pair_samples(
        rgb_a=rgb_a,
        rgb_b=rgb_b,
        erp_flat_index=np.arange(sample_count),
        xy_a=np.column_stack([np.linspace(0.1, 0.9, sample_count), np.full(sample_count, 0.4)]),
        xy_b=np.column_stack([np.linspace(0.9, 0.1, sample_count), np.full(sample_count, 0.6)]),
        depth_m=np.full(sample_count, 20.0),
        parallax_deg=(
            np.full(sample_count, 1.0)
            if parallax_deg is None
            else np.asarray(parallax_deg, dtype=np.float64)
        ),
    )


def test_fixed_brightness_profile_preserves_signed_brightness_trend():
    from scripts.phase3.db226_luma_response import fixed_brightness_profile

    samples = _synthetic_samples(
        np.array([20, 24, 28, 40, 48, 56, 80, 96, 112], dtype=float),
        np.array([-0.2] * 3 + [0.0] * 3 + [0.2] * 3),
    )

    report = fixed_brightness_profile(
        samples,
        log_luma_edges=np.log([16, 32, 64, 128]),
        min_usable_n=3,
        sat_lo=0.0,
        sat_hi=255.0,
    )

    assert [row["n"] for row in report["bins"]] == [3, 3, 3]
    assert [row["usable_n"] for row in report["bins"]] == [3, 3, 3]
    assert [row["reliable"] for row in report["bins"]] == [True, True, True]
    assert [row["signed_log_luma_median"] for row in report["bins"]] == pytest.approx(
        [-0.2, 0.0, 0.2]
    )
    assert [row["signed_log_luma_mad"] for row in report["bins"]] == pytest.approx(
        [0.0, 0.0, 0.0]
    )
    assert [row["abs_log_luma_p90"] for row in report["bins"]] == pytest.approx(
        [0.2, 0.0, 0.2]
    )


def test_fixed_brightness_profile_marks_low_support_bins_unsupported():
    from scripts.phase3.db226_luma_response import fixed_brightness_profile

    samples = _synthetic_samples(np.array([20.0, 24.0, 48.0]), np.zeros(3))

    report = fixed_brightness_profile(
        samples,
        log_luma_edges=np.log([16, 32, 64, 128]),
        min_usable_n=2,
        sat_lo=0.0,
        sat_hi=255.0,
    )

    assert [row["n"] for row in report["bins"]] == [2, 1, 0]
    assert report["bins"][0]["reliable"] is True
    for row in report["bins"][1:]:
        assert row["reliable"] is False
        assert row["signed_log_luma_median"] is None
        assert row["signed_log_luma_mad"] is None
        assert row["abs_log_luma_p90"] is None


def test_fixed_brightness_profile_accounts_for_every_input_sample():
    from scripts.phase3.db226_luma_response import fixed_brightness_profile

    samples = _synthetic_samples(
        np.array([5.0, 10.0, 20.0, 99.0, 100.0, 150.0]),
        np.zeros(6),
    )

    report = fixed_brightness_profile(
        samples,
        log_luma_edges=np.log([10, 100]),
        min_usable_n=1,
        sat_lo=0.0,
        sat_hi=255.0,
    )

    for field in ("input_n", "in_range_n", "underflow_n", "overflow_n"):
        assert type(report[field]) is int
    assert report["input_n"] == 6
    assert report["in_range_n"] == 3
    assert report["underflow_n"] == 1
    assert report["overflow_n"] == 2
    assert sum(row["n"] for row in report["bins"]) == report["in_range_n"]
    assert (
        sum(row["n"] for row in report["bins"])
        + report["underflow_n"]
        + report["overflow_n"]
        == report["input_n"]
    )


def test_fixed_brightness_profile_excludes_saturation_and_large_parallax():
    from scripts.phase3.db226_luma_response import collect_pair_samples, fixed_brightness_profile

    rgb_a = np.array(
        [[100.0, 100.0, 100.0], [100.0, 100.0, 100.0], [230.0, 35.0, 35.0], [100.0, 100.0, 100.0]]
    )
    rgb_b = rgb_a * np.exp(0.1)
    samples = collect_pair_samples(
        rgb_a=rgb_a,
        rgb_b=rgb_b,
        erp_flat_index=np.arange(4),
        xy_a=np.full((4, 2), 0.25),
        xy_b=np.full((4, 2), 0.75),
        depth_m=np.full(4, 20.0),
        parallax_deg=np.array([1.0, 2.0, 1.0, 8.0]),
    )

    report = fixed_brightness_profile(
        samples,
        log_luma_edges=np.log([64, 128]),
        min_usable_n=2,
        sat_lo=10.0,
        sat_hi=245.0,
        max_parallax_deg=5.0,
    )

    row = report["bins"][0]
    assert row["n"] == 4
    assert row["saturated_n"] == 1
    assert row["usable_n"] == 2
    assert row["reliable"] is True
    assert row["signed_log_luma_median"] == pytest.approx(0.1)
    assert report["saturated_n"] == 1
    assert report["parallax_rejected_n"] == 1


def test_fixed_brightness_profile_is_json_serializable_with_numpy_parallax_limit():
    from scripts.phase3.db226_luma_response import fixed_brightness_profile

    samples = _synthetic_samples(np.array([40.0]), np.array([0.1]))
    report = fixed_brightness_profile(
        samples,
        log_luma_edges=np.log([32, 64]),
        min_usable_n=1,
        sat_lo=0.0,
        sat_hi=255.0,
        max_parallax_deg=np.float32(5.0),
    )

    decoded = json.loads(json.dumps(report))

    assert type(decoded["max_parallax_deg"]) is float
    assert decoded["max_parallax_deg"] == 5.0


def test_equal_report_gains_do_not_change_signed_residual():
    from scripts.phase3.db226_luma_response import fixed_brightness_profile

    samples = _synthetic_samples(np.array([40.0, 50.0, 60.0]), np.array([-0.1, 0.0, 0.1]))
    kwargs = {
        "log_luma_edges": np.log([32, 160]),
        "min_usable_n": 3,
        "sat_lo": 0.0,
        "sat_hi": 255.0,
    }

    raw = fixed_brightness_profile(samples, **kwargs)
    equal_gain = fixed_brightness_profile(samples, gain_log_a=0.4, gain_log_b=0.4, **kwargs)

    assert equal_gain["bins"][0]["signed_log_luma_median"] == pytest.approx(
        raw["bins"][0]["signed_log_luma_median"]
    )
    assert equal_gain["bins"][0]["signed_log_luma_mad"] == pytest.approx(
        raw["bins"][0]["signed_log_luma_mad"]
    )


def test_equal_report_gains_shift_shared_brightness_bin_membership():
    from scripts.phase3.db226_luma_response import fixed_brightness_profile

    samples = _synthetic_samples(np.full(3, 20.0), np.zeros(3))
    kwargs = {
        "log_luma_edges": np.log([10, 30, 60]),
        "min_usable_n": 3,
        "sat_lo": 0.0,
        "sat_hi": 255.0,
    }

    raw = fixed_brightness_profile(samples, **kwargs)
    shifted = fixed_brightness_profile(
        samples,
        gain_log_a=np.log(2.0),
        gain_log_b=np.log(2.0),
        **kwargs,
    )

    assert raw["brightness_coordinate"] == "shared_corrected_log_luma"
    assert [row["n"] for row in raw["bins"]] == [3, 0]
    assert [row["n"] for row in shifted["bins"]] == [0, 3]
    assert raw["bins"][0]["signed_log_luma_median"] == pytest.approx(0.0)
    assert shifted["bins"][1]["signed_log_luma_median"] == pytest.approx(0.0)


def test_different_raw_lumas_use_shared_corrected_brightness_coordinate():
    from scripts.phase3.db226_luma_response import fixed_brightness_profile

    samples = _synthetic_samples(np.full(3, 20.0), np.full(3, np.log(4.0)))

    report = fixed_brightness_profile(
        samples,
        log_luma_edges=np.log([10, 30, 60, 100]),
        min_usable_n=3,
        sat_lo=0.0,
        sat_hi=255.0,
    )

    assert [row["n"] for row in report["bins"]] == [0, 3, 0]
    assert report["bins"][1]["signed_log_luma_median"] == pytest.approx(np.log(4.0))


def test_differential_report_gain_translates_signed_residual():
    from scripts.phase3.db226_luma_response import fixed_brightness_profile

    samples = _synthetic_samples(np.array([40.0, 50.0, 60.0]), np.zeros(3))

    report = fixed_brightness_profile(
        samples,
        gain_log_a=0.1,
        gain_log_b=0.35,
        log_luma_edges=np.log([32, 80]),
        min_usable_n=3,
        sat_lo=0.0,
        sat_hi=255.0,
    )

    assert report["bins"][0]["signed_log_luma_median"] == pytest.approx(0.25)
