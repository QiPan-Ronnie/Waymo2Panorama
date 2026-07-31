import inspect
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

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


def _profile_frame(
    log_id: str,
    anchor_index: int,
    shape: np.ndarray,
    *,
    offset: float = 0.0,
    camera_pair: tuple[str, str] = ("cam_a", "cam_b"),
    reverse: bool = False,
    repeats: int = 8,
    rho: float = 0.9,
    parallax_deg: float = 1.0,
) -> dict[str, object]:
    from scripts.phase3.db226_luma_response import collect_pair_samples

    shape = np.asarray(shape, dtype=np.float64)
    # Stay away from fixed-bin edges even after the synthetic frame offset
    # shifts the shared corrected-brightness coordinate.
    luma = np.asarray([5.5, 11.0, 22.0, 44.0, 76.0, 105.0], dtype=float)
    luma_a = np.repeat(luma, repeats)
    residual = np.repeat(shape + offset, repeats)
    rgb_a = np.repeat(luma_a[:, None], 3, axis=1)
    rgb_b = rgb_a * np.exp(residual[:, None])
    sample_count = len(luma_a)
    xy_a = np.column_stack(
        [np.linspace(0.1, 0.8, sample_count), np.full(sample_count, 0.25)]
    )
    xy_b = np.column_stack(
        [np.linspace(0.9, 0.2, sample_count), np.full(sample_count, 0.75)]
    )
    pair = camera_pair
    if reverse:
        pair = (camera_pair[1], camera_pair[0])
        rgb_a, rgb_b = rgb_b, rgb_a
        xy_a, xy_b = xy_b, xy_a
    samples = collect_pair_samples(
        rgb_a=rgb_a,
        rgb_b=rgb_b,
        erp_flat_index=np.arange(sample_count),
        xy_a=xy_a,
        xy_b=xy_b,
        depth_m=np.full(sample_count, 20.0),
        parallax_deg=np.full(sample_count, parallax_deg),
    )
    return {
        "log_id": log_id,
        "anchor_index": anchor_index,
        "camera_pair": pair,
        "samples": samples,
        "gain_log_a": 0.0,
        "gain_log_b": 0.0,
        "rho_log_luma": rho,
        "sat_lo": 0.0,
        "sat_hi": 255.0,
    }


def test_log_split_is_deterministic_disjoint_and_hashed():
    from scripts.phase3.db226_luma_response import split_log_ids

    first = split_log_ids(["c", "a", "b", "d", "e", "f"], holdout_fraction=1 / 3)
    second = split_log_ids(["f", "e", "d", "c", "b", "a"], holdout_fraction=1 / 3)

    assert first == second
    assert set(first["train_log_ids"]).isdisjoint(first["heldout_log_ids"])
    assert set(first["selected_log_ids"]) == {"a", "b", "c", "d", "e", "f"}
    assert len(first["heldout_log_ids"]) == 2
    assert len(first["split_sha256"]) == 64


def test_evaluator_rejects_log_leakage():
    from scripts.phase3.db226_luma_response import evaluate_profile_transfer

    with pytest.raises(ValueError, match="disjoint"):
        evaluate_profile_transfer([], train_log_ids=["same"], heldout_log_ids=["same"])


def test_fixed_shape_transfers_but_frame_offsets_do_not_fake_it():
    from scripts.phase3.db226_luma_response import evaluate_profile_transfer

    shape = np.linspace(-0.12, 0.12, 6)
    stable = [
        _profile_frame("train0", 0, shape, offset=-0.20),
        _profile_frame("train1", 0, shape, offset=0.10),
        _profile_frame("heldout", 0, shape, offset=0.20),
    ]
    verdict = evaluate_profile_transfer(
        stable,
        train_log_ids=["train0", "train1"],
        heldout_log_ids=["heldout"],
    )

    assert verdict["scalar_baseline_method"] == "sample_median_per_pair_frame"
    assert verdict["training_aggregation"] == "frame_then_log_equal_weight"
    assert verdict["status"] == "PASS"
    assert verdict["majority_heldout_pairs_improved"] is True
    assert verdict["majority_heldout_logs_improved"] is True
    frame = verdict["heldout_pair_frames"][0]
    assert frame["supported_bin_count"] == 6
    assert frame["nonlinear_mae"] < frame["zero_shape_mae"]
    assert frame["signed_correlation"] > 0.99
    for aggregate in (verdict["heldout_pairs"][0], verdict["heldout_logs"][0]):
        assert aggregate["affine_delta_mae"] == pytest.approx(
            aggregate["zero_shape_mae"] - aggregate["affine_mae"]
        )
        assert aggregate["mean_supported_bin_coverage"] == pytest.approx(1.0)
        assert aggregate["mean_signed_correlation"] > 0.99
    assert verdict["win_summary"]["heldout_pairs"] == {
        "win_n": 1,
        "loss_n": 0,
        "tie_n": 0,
        "unknown_n": 0,
    }

    offsets_only = [
        _profile_frame("train0", 0, np.zeros(6), offset=-0.20),
        _profile_frame("train1", 0, np.zeros(6), offset=0.10),
        _profile_frame("heldout", 0, np.zeros(6), offset=0.20),
    ]
    neutral = evaluate_profile_transfer(
        offsets_only,
        train_log_ids=["train0", "train1"],
        heldout_log_ids=["heldout"],
    )

    assert neutral["status"] == "NEG"
    assert neutral["majority_heldout_pairs_improved"] is False
    assert neutral["majority_heldout_logs_improved"] is False
    assert neutral["heldout_pair_frames"][0]["delta_mae"] == pytest.approx(0.0, abs=1e-12)
    assert neutral["win_summary"]["heldout_pairs"]["tie_n"] == 1
    assert neutral["win_summary"]["heldout_pairs"]["win_n"] == 0


def test_reverse_pair_orientation_canonicalizes_samples_gains_and_coordinates():
    from scripts.phase3.db226_luma_response import canonicalize_pair_frame

    shape = np.linspace(-0.12, 0.12, 6)
    forward_row = _profile_frame("log", 0, shape, offset=0.1)
    reverse_row = _profile_frame("log", 0, shape, offset=0.1, reverse=True)
    forward_row.update({"gain_log_a": 0.1, "gain_log_b": 0.3})
    reverse_row.update({"gain_log_a": 0.3, "gain_log_b": 0.1})
    forward = canonicalize_pair_frame(forward_row)
    reverse = canonicalize_pair_frame(reverse_row)

    assert forward["camera_pair"] == reverse["camera_pair"] == ("cam_a", "cam_b")
    assert reverse["gain_log_a"] == forward["gain_log_a"]
    assert reverse["gain_log_b"] == forward["gain_log_b"]
    for name in ("rgb_a", "rgb_b", "xy_a", "xy_b"):
        np.testing.assert_allclose(
            getattr(reverse["samples"], name),
            getattr(forward["samples"], name),
        )


def test_training_aggregates_each_log_before_fitting_shape():
    from scripts.phase3.db226_luma_response import evaluate_profile_transfer

    shape = np.linspace(-0.12, 0.12, 6)
    rows = [
        *[_profile_frame("many_frames", anchor, -shape) for anchor in range(9)],
        _profile_frame("train1", 0, shape),
        _profile_frame("train2", 0, shape),
        _profile_frame("heldout", 0, shape),
    ]

    report = evaluate_profile_transfer(
        rows,
        train_log_ids=["many_frames", "train1", "train2"],
        heldout_log_ids=["heldout"],
    )

    assert report["status"] == "PASS"
    assert report["heldout_pair_frames"][0]["nonlinear_mae"] < 1e-10
    assert report["training_shapes"][0]["train_log_count"] == 3


def test_affine_diagnostic_is_train_only_and_nonlinear_can_surpass_it():
    from scripts.phase3.db226_luma_response import evaluate_profile_transfer

    nonlinear_shape = np.array([0.12, 0.02, -0.08, -0.08, 0.02, 0.12])
    rows = [
        _profile_frame("train0", 0, nonlinear_shape, offset=-0.1),
        _profile_frame("train1", 0, nonlinear_shape, offset=0.1),
        _profile_frame("heldout", 0, nonlinear_shape, offset=0.2),
    ]

    report = evaluate_profile_transfer(
        rows,
        train_log_ids=["train0", "train1"],
        heldout_log_ids=["heldout"],
    )

    frame = report["heldout_pair_frames"][0]
    assert frame["affine_mae"] >= frame["nonlinear_mae"]
    assert frame["nonlinear_beats_affine"] is True
    assert report["heldout_pairs"][0]["nonlinear_beats_affine"] is True
    assert report["primary_gate"] == "nonlinear_vs_zero_only"


def test_low_bin_support_is_unknown_and_never_vacuously_passes_majority():
    from scripts.phase3.db226_luma_response import evaluate_profile_transfer

    shape = np.linspace(-0.12, 0.12, 6)
    rows = [
        _profile_frame("train0", 0, shape, repeats=1),
        _profile_frame("train1", 0, shape, repeats=1),
        _profile_frame("heldout", 0, shape, repeats=1),
    ]

    report = evaluate_profile_transfer(
        rows,
        train_log_ids=["train0", "train1"],
        heldout_log_ids=["heldout"],
    )

    assert report["status"] == "UNKNOWN"
    assert report["registered_pass"] is False
    assert report["majority_heldout_pairs_improved"] is False
    assert report["majority_heldout_logs_improved"] is False
    assert report["coverage"]["evaluable_pair_frame_n"] == 0
    assert report["heldout_pair_frames"][0]["status"] == "UNKNOWN"


def test_missing_heldout_log_aggregate_forces_registered_unknown():
    from scripts.phase3.db226_luma_response import evaluate_profile_transfer

    shape = np.linspace(-0.12, 0.12, 6)
    rows = [
        _profile_frame("train0", 0, shape),
        _profile_frame("train1", 0, shape),
        _profile_frame("heldout_pass", 0, shape),
    ]

    report = evaluate_profile_transfer(
        rows,
        train_log_ids=["train0", "train1"],
        heldout_log_ids=["heldout_pass", "heldout_missing"],
    )

    assert report["status"] == "UNKNOWN"
    assert report["registered_pass"] is False
    assert report["majority_heldout_pairs_improved"] is False
    assert report["majority_heldout_logs_improved"] is False
    logs = {row["log_id"]: row for row in report["heldout_logs"]}
    assert logs["heldout_pass"]["status"] == "PASS"
    assert logs["heldout_missing"]["status"] == "UNKNOWN"
    assert report["coverage"]["expected_pair_log_cell_n"] == 2
    assert report["coverage"]["missing_pair_log_cell_n"] == 1
    assert report["coverage"]["unknown_pair_log_cell_n"] == 1


def test_missing_expected_pair_aggregate_forces_registered_unknown():
    from scripts.phase3.db226_luma_response import evaluate_profile_transfer

    shape = np.linspace(-0.12, 0.12, 6)
    rows = [
        _profile_frame("train0", 0, shape, camera_pair=("cam_a", "cam_b")),
        _profile_frame("train1", 0, shape, camera_pair=("cam_a", "cam_b")),
        _profile_frame("train0", 1, shape, camera_pair=("cam_a", "cam_c")),
        _profile_frame("train1", 1, shape, camera_pair=("cam_a", "cam_c")),
        _profile_frame("heldout", 0, shape, camera_pair=("cam_a", "cam_b")),
    ]

    report = evaluate_profile_transfer(
        rows,
        train_log_ids=["train0", "train1"],
        heldout_log_ids=["heldout"],
    )

    assert report["status"] == "UNKNOWN"
    assert report["majority_heldout_pairs_improved"] is False
    assert report["majority_heldout_logs_improved"] is False
    pairs = {tuple(row["camera_pair"]): row for row in report["heldout_pairs"]}
    assert pairs[("cam_a", "cam_b")]["status"] == "PASS"
    assert pairs[("cam_a", "cam_c")]["status"] == "UNKNOWN"
    assert report["heldout_logs"][0]["status"] == "PASS"
    assert report["coverage"]["expected_pair_n"] == 2
    assert report["coverage"]["missing_pair_log_cell_n"] == 1


def test_cross_sparse_pass_marginals_remain_unknown_in_every_sensitivity_cell():
    from agent.db115_drivers.db226_analyze import analyze_rows

    shape = np.linspace(-0.12, 0.12, 6)
    rows = [
        _profile_frame("train0", 0, shape, camera_pair=("cam_a", "cam_b")),
        _profile_frame("train1", 0, shape, camera_pair=("cam_a", "cam_b")),
        _profile_frame("train0", 1, shape, camera_pair=("cam_a", "cam_c")),
        _profile_frame("train1", 1, shape, camera_pair=("cam_a", "cam_c")),
        _profile_frame("heldout0", 0, shape, camera_pair=("cam_a", "cam_b")),
        _profile_frame("heldout1", 0, shape, camera_pair=("cam_a", "cam_c")),
    ]
    report = analyze_rows(
        rows,
        _split_manifest(["train0", "train1"], ["heldout0", "heldout1"]),
    )

    assert report["primary"]["status"] == "UNKNOWN"
    assert report["primary"]["majority_heldout_pairs_improved"] is False
    assert report["primary"]["majority_heldout_logs_improved"] is False
    assert all(pair["status"] == "PASS" for pair in report["primary"]["heldout_pairs"])
    assert all(log["status"] == "PASS" for log in report["primary"]["heldout_logs"])
    gate = report["primary"]["registered_completeness_gate"]
    assert gate == {
        "unit": "heldout_log_x_expected_canonical_pair",
        "expected_cell_n": 4,
        "evaluable_cell_n": 2,
        "unknown_cell_n": 2,
        "complete": False,
        "unknown_cells": [
            {"log_id": "heldout0", "camera_pair": ["cam_a", "cam_c"]},
            {"log_id": "heldout1", "camera_pair": ["cam_a", "cam_b"]},
        ],
    }
    for cell in report["sensitivity"]:
        evaluation = cell["evaluation"]
        assert evaluation["status"] == "UNKNOWN"
        assert evaluation["registered_completeness_gate"]["complete"] is False


def test_complete_heldout_log_pair_matrix_can_register_pass():
    from scripts.phase3.db226_luma_response import evaluate_profile_transfer

    shape = np.linspace(-0.12, 0.12, 6)
    pairs = [("cam_a", "cam_b"), ("cam_a", "cam_c")]
    rows = [
        *[
            _profile_frame(train_log, pair_index, shape, camera_pair=pair)
            for pair_index, pair in enumerate(pairs)
            for train_log in ("train0", "train1")
        ],
        *[
            _profile_frame(heldout_log, pair_index, shape, camera_pair=pair)
            for heldout_log in ("heldout0", "heldout1")
            for pair_index, pair in enumerate(pairs)
        ],
    ]

    report = evaluate_profile_transfer(
        rows,
        train_log_ids=["train0", "train1"],
        heldout_log_ids=["heldout0", "heldout1"],
    )

    assert report["status"] == "PASS"
    assert report["registered_pass"] is True
    assert report["registered_completeness_gate"]["complete"] is True
    assert report["registered_completeness_gate"]["unknown_cells"] == []
    assert report["coverage"]["expected_pair_log_cell_n"] == 4
    assert report["coverage"]["evaluable_pair_log_cell_n"] == 4
    assert report["coverage"]["unknown_pair_log_cell_n"] == 0


def test_transfer_report_is_deterministic_native_json():
    from scripts.phase3.db226_luma_response import evaluate_profile_transfer

    shape = np.linspace(-0.12, 0.12, 6)
    rows = [
        _profile_frame("train0", 0, shape),
        _profile_frame("train1", 0, shape),
        _profile_frame("heldout", 0, shape),
    ]
    kwargs = {"train_log_ids": ["train0", "train1"], "heldout_log_ids": ["heldout"]}

    first = json.dumps(evaluate_profile_transfer(rows, **kwargs), sort_keys=True)
    second = json.dumps(evaluate_profile_transfer(list(reversed(rows)), **kwargs), sort_keys=True)

    assert first == second


def _write_verified_bundle(
    root: Path,
    row: dict[str, object],
    *,
    acquisition_helper_sha256: str | None = None,
) -> tuple[Path, Path]:
    from scripts.phase3 import db226_luma_response as luma_response

    log_id = str(row["log_id"])
    anchor_index = int(row["anchor_index"])
    pair = list(row["camera_pair"])
    samples = row["samples"]
    prefix = "pair_000"
    bundle_dir = root / log_id
    bundle_dir.mkdir(parents=True, exist_ok=True)
    npz_path = bundle_dir / f"{log_id}_a{anchor_index:03d}_color_diag_samples.npz"
    with npz_path.open("w+b") as handle:
        np.savez_compressed(
            handle,
            **{
                prefix + "__rgb_a": samples.rgb_a,
                prefix + "__rgb_b": samples.rgb_b,
                prefix + "__erp_flat_index": samples.erp_flat_index,
                prefix + "__xy_a": samples.xy_a,
                prefix + "__xy_b": samples.xy_b,
                prefix + "__depth_m": samples.depth_m,
                prefix + "__parallax_deg": samples.parallax_deg,
            },
        )
    sample_sha256 = hashlib.sha256(npz_path.read_bytes()).hexdigest()
    helper_sha256 = acquisition_helper_sha256 or hashlib.sha256(
        Path(luma_response.__file__).read_bytes()
    ).hexdigest()
    transaction_binding = json.dumps(
        {
            "log_id": log_id,
            "anchor_index": anchor_index,
            "sample_sha256": sample_sha256,
            "helper_source_sha256": helper_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sidecar = {
        "schema_version": luma_response.RAW_PAIR_SCHEMA_VERSION,
        "measurement": "same_3d_ray_at_curved_ownership_boundary",
        "artifact_state": "complete",
        "artifact_transaction_id": hashlib.sha256(transaction_binding).hexdigest(),
        "helper_source_sha256": helper_sha256,
        "dataset": "av2",
        "log_id": log_id,
        "anchor_index": anchor_index,
        "anchor_timestamp_ns": anchor_index * 1000,
        "sat_lo": float(row["sat_lo"]),
        "sat_hi": float(row["sat_hi"]),
        "sample_npz": npz_path.name,
        "sample_sha256": sample_sha256,
        "pairs": [
            {
                "sample_prefix": prefix,
                "camera_pair": pair,
                "emitted_n": len(samples.rgb_a),
                "rho_log_luma": row["rho_log_luma"],
                "fixed_brightness_profile": {
                    "schema_version": luma_response.PROFILE_SCHEMA_VERSION,
                    "raw_pair_schema_version": luma_response.RAW_PAIR_SCHEMA_VERSION,
                    "gain_log_a": float(row["gain_log_a"]),
                    "gain_log_b": float(row["gain_log_b"]),
                },
            }
        ],
    }
    sidecar_path = bundle_dir / f"{log_id}_a{anchor_index:03d}_color_diag.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return sidecar_path, npz_path


def _split_manifest(
    train_log_ids: list[str],
    heldout_log_ids: list[str],
    *,
    include_selected: bool = True,
    include_anchors: bool = True,
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "train_log_ids": train_log_ids,
        "heldout_log_ids": heldout_log_ids,
        "helper_source_sha256": hashlib.sha256(
            Path(__file__).with_name("db226_luma_response.py").read_bytes()
        ).hexdigest(),
    }
    selected = sorted(train_log_ids + heldout_log_ids)
    if include_selected:
        manifest["selected_log_ids"] = selected
    if include_anchors:
        manifest["anchors"] = {log_id: [0] for log_id in selected}
    return manifest


def test_original_preregistered_manifest_derives_acquisition_helper_from_sidecars(
    db226_tmp_path: Path,
):
    from agent.db115_drivers import db226_analyze

    acquisition_sha256 = "5fae29c5" + "3" * 56
    shape = np.linspace(-0.12, 0.12, 6)
    for log_id in ("train0", "train1", "heldout"):
        _write_verified_bundle(
            db226_tmp_path,
            _profile_frame(log_id, 0, shape),
            acquisition_helper_sha256=acquisition_sha256,
        )
    original_manifest = {
        "selected_log_ids": ["train0", "train1", "heldout"],
        "train_log_ids": ["train0", "train1"],
        "heldout_log_ids": ["heldout"],
    }

    rows = db226_analyze.load_verified_sidecars(db226_tmp_path, original_manifest)
    report = db226_analyze.analyze_rows(rows, original_manifest)

    assert len(rows) == 3
    assert {
        row["acquisition_helper_source_sha256"]
        for row in rows
    } == {acquisition_sha256}
    assert report["acquisition_helper_source_sha256"] == acquisition_sha256
    assert report["analyzer_helper_source_sha256"] == hashlib.sha256(
        Path(db226_analyze.luma_response.__file__).read_bytes()
    ).hexdigest()
    assert report["analyzer_helper_source_sha256"] != acquisition_sha256


def test_original_manifest_rejects_mixed_sidecar_helper_sha256(
    db226_tmp_path: Path,
):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    shape = np.linspace(-0.12, 0.12, 6)
    _write_verified_bundle(
        db226_tmp_path,
        _profile_frame("heldout", 0, shape),
        acquisition_helper_sha256="a" * 64,
    )
    _write_verified_bundle(
        db226_tmp_path,
        _profile_frame("heldout", 1, shape),
        acquisition_helper_sha256="b" * 64,
    )
    original_manifest = {
        "selected_log_ids": ["train", "heldout"],
        "train_log_ids": ["train"],
        "heldout_log_ids": ["heldout"],
    }

    with pytest.raises(ValueError, match="sidecar helper_source_sha256.*unique"):
        load_verified_sidecars(
            db226_tmp_path,
            original_manifest,
            require_all_selected_logs=False,
        )


@pytest.mark.parametrize("helper_value", [None, "not-a-64-hex-sha"])
def test_original_manifest_rejects_missing_or_invalid_sidecar_helper_sha256(
    db226_tmp_path: Path,
    helper_value: str | None,
):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    sidecar_path, _ = _write_verified_bundle(
        db226_tmp_path,
        _profile_frame("heldout", 0, np.linspace(-0.12, 0.12, 6)),
    )
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if helper_value is None:
        sidecar.pop("helper_source_sha256")
    else:
        sidecar["helper_source_sha256"] = helper_value
    transaction_binding = json.dumps(
        {
            "log_id": sidecar["log_id"],
            "anchor_index": sidecar["anchor_index"],
            "sample_sha256": sidecar["sample_sha256"],
            "helper_source_sha256": sidecar.get("helper_source_sha256"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sidecar["artifact_transaction_id"] = hashlib.sha256(transaction_binding).hexdigest()
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    original_manifest = {
        "selected_log_ids": ["train", "heldout"],
        "train_log_ids": ["train"],
        "heldout_log_ids": ["heldout"],
    }

    with pytest.raises(ValueError, match="sidecar helper_source_sha256.*64.*hex"):
        load_verified_sidecars(
            db226_tmp_path,
            original_manifest,
            require_all_selected_logs=False,
        )


def test_loader_separates_recorded_acquisition_helper_from_analyzer_version(
    db226_tmp_path: Path,
):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    acquisition_sha256 = "5fae29c5" + "1" * 56
    _write_verified_bundle(
        db226_tmp_path,
        _profile_frame("heldout", 0, np.linspace(-0.12, 0.12, 6)),
        acquisition_helper_sha256=acquisition_sha256,
    )
    manifest = _split_manifest(["train"], ["heldout"])
    manifest["helper_source_sha256"] = acquisition_sha256
    manifest["anchors"] = {"train": [], "heldout": [0]}

    rows = load_verified_sidecars(
        db226_tmp_path,
        manifest,
        require_all_selected_logs=False,
    )

    assert len(rows) == 1


def test_loader_rejects_sidecar_helper_that_differs_from_acquisition_manifest(
    db226_tmp_path: Path,
):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    _write_verified_bundle(
        db226_tmp_path,
        _profile_frame("heldout", 0, np.linspace(-0.12, 0.12, 6)),
        acquisition_helper_sha256="b" * 64,
    )
    manifest = _split_manifest(["train"], ["heldout"])
    manifest["helper_source_sha256"] = "a" * 64
    manifest["anchors"] = {"train": [], "heldout": [0]}

    with pytest.raises(ValueError, match="sidecar helper_source_sha256"):
        load_verified_sidecars(
            db226_tmp_path,
            manifest,
            require_all_selected_logs=False,
        )


def test_loader_requires_legal_recorded_acquisition_helper_sha256(db226_tmp_path: Path):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    manifest = _split_manifest(["train"], ["heldout"])
    manifest["helper_source_sha256"] = "not-a-64-hex-sha"
    manifest["anchors"] = {"train": [], "heldout": [0]}
    db226_tmp_path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="64.*hex"):
        load_verified_sidecars(
            db226_tmp_path,
            manifest,
            require_all_selected_logs=False,
        )


def test_analysis_report_names_acquisition_and_analyzer_helper_hashes_separately():
    from agent.db115_drivers import db226_analyze

    acquisition_sha256 = "5fae29c5" + "2" * 56
    shape = np.linspace(-0.12, 0.12, 6)
    rows = [
        _profile_frame("train0", 0, shape),
        _profile_frame("train1", 0, shape),
        _profile_frame("heldout", 0, shape),
    ]
    manifest = _split_manifest(["train0", "train1"], ["heldout"])
    manifest["helper_source_sha256"] = acquisition_sha256

    report = db226_analyze.analyze_rows(rows, manifest)

    assert report["acquisition_helper_source_sha256"] == acquisition_sha256
    assert report["analyzer_helper_source_sha256"] == hashlib.sha256(
        Path(db226_analyze.luma_response.__file__).read_bytes()
    ).hexdigest()
    assert report["analyzer_helper_source_sha256"] != acquisition_sha256
    assert "helper_source_sha256" not in report


@pytest.fixture
def db226_tmp_path():
    scratch_root = Path(__file__).resolve().parents[2] / ".pytest_cache" / "db226_analyze"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="case-", dir=scratch_root) as temp_dir:
        yield Path(temp_dir)


def test_manifest_validation_preserves_existing_split_and_checks_hash_and_selected_set():
    from agent.db115_drivers.db226_analyze import validate_split_manifest

    existing = _split_manifest(["train1", "train0"], ["heldout"], include_selected=False)
    normalized = validate_split_manifest(existing)

    assert normalized["train_log_ids"] == ["train0", "train1"]
    assert normalized["heldout_log_ids"] == ["heldout"]
    assert normalized["selected_log_ids"] == ["heldout", "train0", "train1"]
    assert len(normalized["split_sha256"]) == 64
    with pytest.raises(ValueError, match="disjoint"):
        validate_split_manifest(_split_manifest(["same"], ["same"]))
    with pytest.raises(ValueError, match="selected"):
        validate_split_manifest(
            {
                **_split_manifest(["train"], ["heldout"]),
                "selected_log_ids": ["train", "heldout", "unexpected"],
            }
        )
    with pytest.raises(ValueError, match="split_sha256"):
        validate_split_manifest(
            {**_split_manifest(["train"], ["heldout"]), "split_sha256": "0" * 64}
        )


def test_enriched_cases_define_partitioned_nonempty_anchor_identities():
    from agent.db115_drivers.db226_analyze import validate_split_manifest

    manifest = _split_manifest(
        ["train0", "train1"],
        ["heldout"],
        include_anchors=False,
    )
    manifest.update(
        {
            "source_split_sha256": "a" * 64,
            "cases": [
                {"log_id": "train0", "anchors": [0, 10, 20], "partition": "train"},
                {"log_id": "train1", "anchors": [1, 11, 21], "partition": "train"},
                {"log_id": "heldout", "anchors": [2, 12, 22], "partition": "heldout"},
            ],
        }
    )

    normalized = validate_split_manifest(manifest)

    assert normalized["anchors"] == {
        "heldout": [2, 12, 22],
        "train0": [0, 10, 20],
        "train1": [1, 11, 21],
    }
    assert normalized["source_split_sha256"] == "a" * 64


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda cases: cases.append(dict(cases[0])), "duplicate"),
        (lambda cases: cases.pop(), "cover"),
        (lambda cases: cases[0].update(partition="heldout"), "partition"),
        (lambda cases: cases[0].update(anchors=[]), "nonempty"),
        (lambda cases: cases[0].update(anchors=[0, 0, 1]), "duplicate"),
    ],
)
def test_enriched_cases_fail_closed_on_bad_identity_contract(mutate, message: str):
    from agent.db115_drivers.db226_analyze import validate_split_manifest

    manifest = _split_manifest(["train"], ["heldout"], include_anchors=False)
    cases = [
        {"log_id": "train", "anchors": [0, 1, 2], "partition": "train"},
        {"log_id": "heldout", "anchors": [0, 1, 2], "partition": "heldout"},
    ]
    mutate(cases)
    manifest["cases"] = cases
    manifest["source_split_sha256"] = "b" * 64

    with pytest.raises(ValueError, match=message):
        validate_split_manifest(manifest)


def test_cases_and_explicit_anchor_views_must_agree():
    from agent.db115_drivers.db226_analyze import validate_split_manifest

    manifest = _split_manifest(["train"], ["heldout"])
    manifest["cases"] = [
        {"log_id": "train", "anchors": [0], "partition": "train"},
        {"log_id": "heldout", "anchors": [9], "partition": "heldout"},
    ]
    manifest["source_split_sha256"] = "c" * 64

    with pytest.raises(ValueError, match="disagree"):
        validate_split_manifest(manifest)


def test_bundle_loader_verifies_and_canonicalizes_reverse_pair(db226_tmp_path: Path):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    shape = np.linspace(-0.12, 0.12, 6)
    source_row = _profile_frame("heldout", 0, shape, reverse=True)
    _write_verified_bundle(db226_tmp_path, source_row)
    manifest = _split_manifest(["train"], ["heldout"])
    manifest["anchors"] = {"heldout": [0], "train": []}

    rows = load_verified_sidecars(db226_tmp_path, manifest, require_all_selected_logs=False)

    assert len(rows) == 1
    loaded = rows[0]
    assert loaded["camera_pair"] == ("cam_a", "cam_b")
    forward = _profile_frame("heldout", 0, shape)
    for name in ("rgb_a", "rgb_b", "xy_a", "xy_b"):
        np.testing.assert_allclose(
            getattr(loaded["samples"], name),
            getattr(forward["samples"], name),
        )


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("artifact_state", "artifact_state"),
        ("schema", "schema"),
        ("log_id", "log_id"),
        ("anchor_index", "anchor"),
        ("npz_path", "sample_npz"),
        ("sample_sha256", "sample_sha256"),
        ("transaction", "transaction"),
        ("helper_sha256", "helper"),
        ("sat_bounds", "sat"),
        ("npz_shape", "shape"),
    ],
)
def test_bundle_loader_fails_closed_on_corrupt_artifact(
    db226_tmp_path: Path,
    corruption: str,
    message: str,
):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    row = _profile_frame("heldout", 0, np.linspace(-0.12, 0.12, 6))
    sidecar_path, npz_path = _write_verified_bundle(db226_tmp_path, row)
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if corruption == "artifact_state":
        sidecar["artifact_state"] = "partial"
    elif corruption == "schema":
        sidecar["schema_version"] = "wrong"
    elif corruption == "log_id":
        sidecar["log_id"] = "unexpected"
    elif corruption == "anchor_index":
        sidecar["anchor_index"] = 99
    elif corruption == "npz_path":
        sidecar["sample_npz"] = "../outside.npz"
    elif corruption == "sample_sha256":
        sidecar["sample_sha256"] = "0" * 64
    elif corruption == "transaction":
        sidecar["artifact_transaction_id"] = "0" * 64
    elif corruption == "helper_sha256":
        sidecar["helper_source_sha256"] = "0" * 64
    elif corruption == "sat_bounds":
        sidecar.pop("sat_lo")
    elif corruption == "npz_shape":
        with npz_path.open("w+b") as handle:
            np.savez_compressed(
                handle,
                pair_000__rgb_a=np.zeros((2, 2)),
                pair_000__rgb_b=np.zeros((2, 3)),
                pair_000__erp_flat_index=np.arange(2),
                pair_000__xy_a=np.zeros((2, 2)),
                pair_000__xy_b=np.zeros((2, 2)),
                pair_000__depth_m=np.ones(2),
                pair_000__parallax_deg=np.ones(2),
            )
        sidecar["sample_sha256"] = hashlib.sha256(npz_path.read_bytes()).hexdigest()
        binding = json.dumps(
            {
                "log_id": sidecar["log_id"],
                "anchor_index": sidecar["anchor_index"],
                "sample_sha256": sidecar["sample_sha256"],
                "helper_source_sha256": sidecar["helper_source_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        sidecar["artifact_transaction_id"] = hashlib.sha256(binding).hexdigest()
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    manifest = _split_manifest(["train"], ["heldout"])
    manifest["anchors"] = {"heldout": [0], "train": []}

    with pytest.raises(ValueError, match=message):
        load_verified_sidecars(db226_tmp_path, manifest, require_all_selected_logs=False)


def test_bundle_loader_requires_every_manifest_anchor(db226_tmp_path: Path):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    _write_verified_bundle(
        db226_tmp_path,
        _profile_frame("heldout", 0, np.linspace(-0.12, 0.12, 6)),
    )
    manifest = _split_manifest(["train"], ["heldout"])
    manifest["anchors"] = {"train": [], "heldout": [0, 1]}

    with pytest.raises(ValueError, match="missing expected sidecars"):
        load_verified_sidecars(db226_tmp_path, manifest, require_all_selected_logs=False)


def test_bundle_loader_requires_every_enriched_case_identity(db226_tmp_path: Path):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    _write_verified_bundle(
        db226_tmp_path,
        _profile_frame("heldout", 0, np.linspace(-0.12, 0.12, 6)),
    )
    manifest = _split_manifest(["train"], ["heldout"], include_anchors=False)
    manifest["cases"] = [
        {"log_id": "train", "anchors": [0], "partition": "train"},
        {"log_id": "heldout", "anchors": [0, 1], "partition": "heldout"},
    ]
    manifest["source_split_sha256"] = "d" * 64

    with pytest.raises(ValueError, match="missing expected sidecars"):
        load_verified_sidecars(db226_tmp_path, manifest, require_all_selected_logs=False)


def test_bundle_loader_can_require_exact_identity_count(db226_tmp_path: Path):
    from agent.db115_drivers.db226_analyze import load_verified_sidecars

    _write_verified_bundle(
        db226_tmp_path,
        _profile_frame("heldout", 0, np.linspace(-0.12, 0.12, 6)),
    )
    manifest = _split_manifest(["train"], ["heldout"])
    manifest["anchors"] = {"train": [], "heldout": [0]}

    with pytest.raises(ValueError, match="2 identities"):
        load_verified_sidecars(
            db226_tmp_path,
            manifest,
            require_all_selected_logs=False,
            expected_identity_count=2,
        )


def test_analysis_reports_registered_primary_and_twelve_labeled_sensitivity_cells():
    from agent.db115_drivers.db226_analyze import analyze_rows

    shape = np.linspace(-0.12, 0.12, 6)
    rows = [
        _profile_frame("train0", 0, shape),
        _profile_frame("train1", 0, shape),
        _profile_frame("heldout", 0, shape),
    ]
    report = analyze_rows(rows, _split_manifest(["train0", "train1"], ["heldout"]))

    assert report["primary_config"] == {"rho_min": 0.45, "max_parallax_deg": 5.0}
    assert report["primary"]["status"] == "PASS"
    assert len(report["sensitivity"]) == 12
    assert {
        (cell["rho_min"], cell["max_parallax_deg"])
        for cell in report["sensitivity"]
    } == {
        (rho, parallax)
        for rho in (None, 0.30, 0.45, 0.60)
        for parallax in (2.0, 5.0, None)
    }
    assert {cell["rho_filter"] for cell in report["sensitivity"]} == {
        "all_samples",
        "rho_gte_0.30",
        "rho_gte_0.45",
        "rho_gte_0.60",
    }
    assert {cell["parallax_filter"] for cell in report["sensitivity"]} == {
        "parallax_lte_2deg",
        "parallax_lte_5deg",
        "all_parallax",
    }
    assert all("evaluation" in cell for cell in report["sensitivity"])
    assert report["primary"]["primary_gate"] == "nonlinear_vs_zero_only"


def test_cli_consumes_exact_frozen_24_log_manifest_and_writes_atomically(
    db226_tmp_path: Path,
):
    from agent.db115_drivers.db226_analyze import main, validate_split_manifest

    train_ids = [f"train{i:02d}" for i in range(16)]
    heldout_ids = [f"heldout{i:02d}" for i in range(8)]
    shape = np.linspace(-0.12, 0.12, 6)
    for log_id in train_ids + heldout_ids:
        for anchor_index in (0, 1, 2):
            _write_verified_bundle(
                db226_tmp_path / "inputs",
                _profile_frame(log_id, anchor_index, shape),
            )
    manifest = _split_manifest(
        train_ids,
        heldout_ids,
        include_selected=False,
        include_anchors=False,
    )
    manifest_path = db226_tmp_path / "split_manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    output_path = db226_tmp_path / "report.json"
    argv = [
        "--split-manifest",
        str(manifest_path),
        "--input-root",
        str(db226_tmp_path / "inputs"),
        "--output",
        str(output_path),
        "--expected-split-manifest-sha256",
        manifest_sha256,
    ]

    assert main(argv) == 0
    first = output_path.read_bytes()
    assert main(argv) == 0
    assert output_path.read_bytes() == first
    report = json.loads(first)
    assert report["split_assignment_sha256"] == validate_split_manifest(manifest)[
        "split_sha256"
    ]
    assert report["split_manifest_sha256"] == manifest_sha256
    assert report["artifact_summary"] == {"log_n": 24, "anchor_n": 72, "pair_frame_n": 72}
    assert not list(db226_tmp_path.glob("report.json.*.tmp"))


def test_cli_rejects_non_24_log_manifest_before_analysis(db226_tmp_path: Path):
    from agent.db115_drivers.db226_analyze import main

    manifest = _split_manifest([f"train{i}" for i in range(15)], [f"heldout{i}" for i in range(8)])
    manifest_path = db226_tmp_path / "split_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="24 selected logs"):
        main(
            [
                "--split-manifest",
                str(manifest_path),
                "--input-root",
                str(db226_tmp_path / "inputs"),
                "--output",
                str(db226_tmp_path / "report.json"),
                "--expected-split-manifest-sha256",
                manifest_sha256,
            ]
        )


def test_cli_requires_expected_frozen_manifest_file_hash(db226_tmp_path: Path):
    from agent.db115_drivers.db226_analyze import main

    manifest = _split_manifest(
        [f"train{i:02d}" for i in range(16)],
        [f"heldout{i:02d}" for i in range(8)],
    )
    manifest_path = db226_tmp_path / "split_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SystemExit):
        main(
            [
                "--split-manifest",
                str(manifest_path),
                "--input-root",
                str(db226_tmp_path / "inputs"),
                "--output",
                str(db226_tmp_path / "report.json"),
            ]
        )
