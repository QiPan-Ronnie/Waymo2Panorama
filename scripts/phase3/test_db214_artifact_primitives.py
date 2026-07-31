import ast
import base64
import hashlib
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pytest

from scripts.phase3.db214_artifact_primitives import (
    angular_overlap_weight,
    annotation_enabled,
    load_ego_pose_interpolators,
    ownership_boundary_indices,
    pair_evidence_weights,
    photometric_pair_residual_stats,
    solve_gain_components,
    validate_renderer_capabilities,
)
from scripts.phase3.db89_ghost_recovery import remote_py


def test_raw_sensor_policy_never_loads_annotation_compositor():
    assert annotation_enabled("raw_sensor", has_annotations=True) is False


def test_composite_policy_requires_annotation_file():
    assert annotation_enabled("composite", has_annotations=True) is True
    assert annotation_enabled("composite", has_annotations=False) is False


def test_unknown_annotation_policy_fails_closed():
    with pytest.raises(ValueError, match="annotation policy"):
        annotation_enabled("guess", has_annotations=True)


def test_flat_pair_is_unidentifiable_and_uses_only_prior():
    measurement, prior, confidence = pair_evidence_weights(
        rho=None,
        sample_count=100,
        prior_fraction=0.05,
    )
    assert measurement == 0.0
    assert prior == pytest.approx(5.0)
    assert confidence == 0.0


def test_pair_weight_changes_continuously_across_legacy_threshold():
    below = pair_evidence_weights(0.299, 1000, 0.05)
    above = pair_evidence_weights(0.301, 1000, 0.05)
    assert above[0] > below[0]
    assert above[0] - below[0] < 2.0
    assert above[1] < below[1]


def test_extreme_low_correlation_is_prior_dominated():
    measurement, prior, confidence = pair_evidence_weights(0.029, 2690, 0.05)
    assert confidence == pytest.approx(0.029**2)
    assert measurement / prior < 0.02


def test_healthy_pair_is_measurement_dominated():
    measurement, prior, confidence = pair_evidence_weights(0.9, 1000, 0.05)
    assert confidence == pytest.approx(0.81)
    assert measurement / prior > 80.0


def _gain_edge(
    matrix: np.ndarray,
    rhs: np.ndarray,
    first: int,
    second: int,
    weight: float,
    log_difference: float,
) -> None:
    matrix[first, first] += weight
    matrix[second, second] += weight
    matrix[first, second] -= weight
    matrix[second, first] -= weight
    rhs[first] += weight * log_difference
    rhs[second] -= weight * log_difference


def test_gain_component_solver_matches_legacy_connected_solution():
    matrix = np.zeros((4, 4), dtype=np.float64)
    rhs = np.zeros(4, dtype=np.float64)
    edges = ((0, 1), (1, 2), (2, 3), (3, 0))
    for (first, second), difference in zip(edges, (0.2, -0.1, 0.3, -0.4)):
        _gain_edge(matrix, rhs, first, second, 100.0, difference)

    expected = np.linalg.solve(matrix + np.ones_like(matrix), rhs)
    actual = solve_gain_components(matrix, rhs, edges)

    np.testing.assert_allclose(actual, expected, atol=1e-12)


def test_gain_component_solver_returns_identity_for_disconnected_cameras():
    matrix = np.zeros((6, 6), dtype=np.float64)
    rhs = np.zeros(6, dtype=np.float64)
    edges = ((0, 5), (3, 4), (4, 5))
    _gain_edge(matrix, rhs, 0, 5, 388.0, -0.14)
    _gain_edge(matrix, rhs, 3, 4, 432.0, 0.01)
    _gain_edge(matrix, rhs, 4, 5, 111.0, -0.02)

    gains = solve_gain_components(matrix, rhs, edges)

    assert np.isfinite(gains).all()
    assert gains[1] == 0.0
    assert gains[2] == 0.0
    assert gains[[0, 3, 4, 5]].mean() == pytest.approx(0.0, abs=1e-12)
    np.testing.assert_allclose(
        matrix[np.ix_([0, 3, 4, 5], [0, 3, 4, 5])]
        @ gains[[0, 3, 4, 5]],
        rhs[[0, 3, 4, 5]],
        atol=1e-10,
    )


def test_angular_overlap_ramp_is_resolution_invariant():
    low = np.zeros((8, 16), dtype=bool)
    high = np.zeros((16, 32), dtype=bool)
    low[:, 4] = True
    high[:, 8] = True
    ramp = math.radians(90.0)

    low_w = angular_overlap_weight(low, ramp)
    high_w = angular_overlap_weight(high, ramp)

    assert low_w[4, 4] == pytest.approx(1.0)
    assert high_w[8, 8] == pytest.approx(1.0)
    assert low_w[4, 6] == pytest.approx(high_w[8, 12], abs=1e-6)
    assert low_w[4, 8] == pytest.approx(0.0, abs=1e-6)
    assert high_w[8, 16] == pytest.approx(0.0, abs=1e-6)


def test_angular_overlap_ramp_wraps_at_erp_meridian():
    overlap = np.zeros((8, 16), dtype=bool)
    overlap[:, 0] = True
    weight = angular_overlap_weight(overlap, math.radians(45.0))

    assert weight[4, 0] == pytest.approx(1.0)
    assert weight[4, -1] == pytest.approx(0.5, abs=1e-6)


def test_renderer_annotation_policy_is_explicit_not_ground_mode_coupled():
    code = remote_py()
    assert 'ANNOTATION_POLICY = "composite"' in code
    assert "annotation_enabled(ANNOTATION_POLICY" in code
    assert 'annotations.feather").exists() and GROUND_MODE != "off"' not in code


def test_current_v15_band_driver_requests_raw_sensor_pixel_ownership():
    driver = (
        Path(__file__).resolve().parents[2]
        / "agent"
        / "db115_drivers"
        / "db144_v15.py"
    ).read_text(encoding="utf-8")
    assert '["ANNOTATION_POLICY = \\"composite\\"", "ANNOTATION_POLICY = \\"raw_sensor\\""]' in driver


def test_renderer_uses_continuous_pair_confidence_not_hard_gate():
    code = remote_py()
    assert "pair_evidence_weights(" in code
    assert "rho >= GAIN_MIN_CORR" not in code


def test_renderer_uses_resolution_invariant_angular_depth_ramp():
    code = remote_py()
    assert "DEPTH_SEAMRAMP_DEG" in code
    assert "angular_overlap_weight(" in code
    assert "_dov / float(DEPTH_SEAMRAMP)" not in code


def test_renderer_uses_loader_camera_contract_for_multidataset_rings():
    code = remote_py()
    assert "ring_cams = list(loader.cameras())" in code
    assert "for cam in RING_CAMS_7" not in code
    assert "list(RING_CAMS_7)" not in code


def test_missing_pose_file_uses_explicit_static_ego_fallback():
    scratch_root = Path(__file__).resolve().parents[2] / ".pytest_cache" / "db213_pose"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="case-", dir=scratch_root) as temp_dir:
        cte, tri = load_ego_pose_interpolators(Path(temp_dir))

        rotation, translation = cte(123)
        np.testing.assert_array_equal(rotation, np.eye(3))
        np.testing.assert_array_equal(translation, np.zeros(3))
        np.testing.assert_array_equal(tri(np.array([100, 200])), np.zeros((2, 3)))


def test_camera_only_manifest_fails_closed_if_ground_fill_is_requested():
    scratch_root = Path(__file__).resolve().parents[2] / ".pytest_cache" / "db213_caps"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="case-", dir=scratch_root) as temp_dir:
        log_dir = Path(temp_dir)
        (log_dir / "conversion_manifest.json").write_text(
            '{"mode":"B","has_lidar":false,"has_ego_pose":false}',
            encoding="utf-8",
        )

        validate_renderer_capabilities(log_dir, "off")
        with pytest.raises(ValueError, match="GROUND_MODE='off'"):
            validate_renderer_capabilities(log_dir, "fill")


def test_renderer_handles_empty_lidar_directory_before_argmin():
    code = remote_py()
    assert "if not sweeps:" in code
    assert "load_ego_pose_interpolators(log_dir)" in code
    assert "validate_renderer_capabilities(log_dir, GROUND_MODE)" in code


def test_ownership_boundaries_include_both_sides_and_wrap_erp():
    owners = np.array([[0, 0, 1, 1], [0, 0, 1, 1]], dtype=np.int8)

    pairs = ownership_boundary_indices(owners)

    assert set(pairs) == {(0, 1)}
    # The internal boundary contributes columns 1/2 and the periodic ERP
    # boundary contributes columns 0/3, so all pixels touch this pair.
    assert np.array_equal(pairs[(0, 1)], np.arange(8))


def test_photometric_report_separates_scalar_offset_from_spatial_underfit():
    n = 400
    x = np.linspace(0.0, 1.0, n)
    rgb_a = np.repeat(np.array([[80.0, 100.0, 120.0]]), n, axis=0)
    scalar = math.log(1.25)
    spatial = 0.30 * (x - 0.5)
    rgb_b = rgb_a * np.exp(scalar + spatial)[:, None]
    xy = np.column_stack([x, np.full(n, 0.5)])

    report = photometric_pair_residual_stats(
        rgb_a,
        rgb_b,
        gain_a=np.zeros(3),
        gain_b=np.full(3, -scalar),
        xy_a=xy,
        xy_b=xy,
        bins=4,
    )

    assert report["corrected_log_luma_median"] == pytest.approx(0.0, abs=1e-3)
    assert report["corrected_log_luma_mad"] > 0.07
    assert report["camera_a_spatial_median_range"] > 0.20
    assert report["corrected_chroma_logratio_p90"] == pytest.approx(0.0, abs=1e-6)
    grid = report["camera_a_spatial_grid"]
    assert grid["bins"] == 4
    assert len(grid["cells"]) == 16
    occupied = [cell for cell in grid["cells"] if cell["n"] > 0]
    assert sum(cell["n"] for cell in occupied) == n
    signed = [
        cell["corrected_log_luma_median"]
        for cell in occupied
        if cell["corrected_log_luma_median"] is not None
    ]
    assert min(signed) < -0.10
    assert max(signed) > 0.10
    assert all(cell["corrected_chroma_rg_median"] == pytest.approx(0.0) for cell in occupied)
    assert all(cell["corrected_chroma_bg_median"] == pytest.approx(0.0) for cell in occupied)


def test_photometric_grid_preserves_camera_specific_cell_coordinates():
    side = 20
    x, y = np.meshgrid(
        (np.arange(side) + 0.5) / side,
        (np.arange(side) + 0.5) / side,
    )
    xy_a = np.column_stack([x.ravel(), y.ravel()])
    xy_b = np.column_stack([1.0 - x.ravel(), y.ravel()])
    base = np.repeat(np.array([[60.0, 90.0, 120.0]]), side * side, axis=0)
    residual = 0.4 * (xy_a[:, 0] - 0.5)
    shifted = base * np.exp(residual)[:, None]

    report = photometric_pair_residual_stats(
        base,
        shifted,
        gain_a=np.zeros(3),
        gain_b=np.zeros(3),
        xy_a=xy_a,
        xy_b=xy_b,
        bins=4,
    )

    grid_a = report["camera_a_spatial_grid"]["cells"]
    grid_b = report["camera_b_spatial_grid"]["cells"]
    row_a = [grid_a[x_index]["corrected_log_luma_median"] for x_index in range(4)]
    row_b = [grid_b[x_index]["corrected_log_luma_median"] for x_index in range(4)]
    assert row_a == sorted(row_a)
    assert row_b == sorted(row_b, reverse=True)


def test_renderer_color_diagnostic_is_gated_and_same_point_based():
    code = remote_py()
    assert "COLOR_DIAG = False" in code
    assert "ownership_boundary_indices(bestcam.reshape(H, W))" in code
    assert "photometric_pair_residual_stats(" in code
    assert "_color_diag.json" in code
    assert "_territory.png" in code


def test_renderer_color_diagnostic_emits_versioned_raw_sample_bundle():
    code = remote_py()
    tree = ast.parse(code)
    color_diag = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "COLOR_DIAG"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "collect_pair_samples"
            for child in ast.walk(node)
        )
    )
    diag_block = ast.get_source_segment(code, color_diag)
    assert diag_block is not None

    assert "hashlib" in code
    assert "from db226_luma_response import" not in code
    for imported_name in (
        "RAW_PAIR_SCHEMA_VERSION",
        "collect_pair_samples",
        "fixed_brightness_profile",
    ):
        assert imported_name in code
    for gated_marker in (
        "_color_diag_samples.npz",
        "collect_pair_samples(",
        "fixed_brightness_profile(",
        "np.savez_compressed(",
        "hashlib.sha256(",
        '"gain_applied_to_npz": False',
    ):
        assert gated_marker in diag_block

    for metadata_key in (
        "schema_version",
        "dataset",
        "log_id",
        "anchor_index",
        "anchor_timestamp_ns",
        "camera_order",
        "luma_definition",
        "input_encoding",
        "gain_applied_to_npz",
        "sat_lo",
        "sat_hi",
        "max_samples_per_pair",
        "sampling",
        "sample_npz",
        "sample_sha256",
        "render_gain_log_rgb",
    ):
        assert f'"{metadata_key}"' in diag_block
    for pair_key in (
        "sample_prefix",
        "camera_pair",
        "boundary_n",
        "sampled_boundary_n",
        "geometry_valid_n",
        "unpoisoned_n",
        "unsaturated_n",
        "emitted_n",
        "fixed_brightness_profile",
    ):
        assert f'"{pair_key}"' in diag_block


def test_renderer_raw_bundle_collects_ungained_frame_observations():
    code = remote_py()
    tree = ast.parse(code)
    color_diag = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "COLOR_DIAG"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "collect_pair_samples"
            for child in ast.walk(node)
        )
    )
    collect_calls = [
        node
        for node in ast.walk(color_diag)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "collect_pair_samples"
    ]
    assert len(collect_calls) == 1
    keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in collect_calls[0].keywords}
    assert keywords["rgb_a"] == "_raw_i_all"
    assert keywords["rgb_b"] == "_raw_j_all"
    assert all("gain" not in value and "gimgs" not in value for value in keywords.values())

    diag_source = ast.get_source_segment(code, color_diag)
    assert diag_source is not None
    assert "_raw_i_all = bilinear(frame.images[ring_cams[_ci]]" in diag_source
    assert "_raw_j_all = bilinear(frame.images[ring_cams[_cj]]" in diag_source
    assert "np.savez_compressed(" in diag_source
    assert "prefix + \"__rgb_a\"" in diag_source
    assert "prefix + \"__rgb_b\"" in diag_source

    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "savez_compressed"
        for node in ast.walk(color_diag)
    )


def test_remote_payload_embeds_and_gates_db226_helper_source():
    code = remote_py()
    compile(code, "<db89_remote>", "exec")
    tree = ast.parse(code)
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "db226_luma_response"
        for node in ast.walk(tree)
    )

    color_diag = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "COLOR_DIAG"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "compile"
            for child in ast.walk(node)
        )
    )
    gated_nodes = set(ast.walk(color_diag))
    for function_name in ("compile", "exec"):
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == function_name
        ]
        assert calls
        assert all(call in gated_nodes for call in calls)

    diag_source = ast.get_source_segment(code, color_diag)
    assert diag_source is not None
    assert "types.ModuleType(" in diag_source
    assert "sys.modules[" in diag_source
    for helper_name in (
        "RAW_PAIR_SCHEMA_VERSION",
        "collect_pair_samples",
        "fixed_brightness_profile",
    ):
        assert f"_helper_module.{helper_name}" in diag_source

    constants = {
        target.id: node.value.value
        for node in ast.walk(color_diag)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
    }
    assert {"_helper_source_b64", "_helper_source_sha256"} <= constants.keys()
    helper_bytes = Path(__file__).with_name("db226_luma_response.py").read_bytes()
    assert base64.b64decode(constants["_helper_source_b64"]) == helper_bytes
    assert constants["_helper_source_sha256"] == hashlib.sha256(helper_bytes).hexdigest()


def test_boundary_report_preserves_pre_cap_and_sampled_counts():
    code = remote_py()
    tree = ast.parse(code)
    color_diag = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "COLOR_DIAG"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "collect_pair_samples"
            for child in ast.walk(node)
        )
    )
    diag_source = ast.get_source_segment(code, color_diag)
    assert diag_source is not None

    pre_cap = diag_source.index("_boundary_n = int(len(_idx0))")
    cap = diag_source.index("if len(_idx0) > 50000:")
    sampled = diag_source.index("_sampled_boundary_n = int(len(_idx0))")
    report = diag_source.index('"sampled_boundary_n": _sampled_boundary_n')
    assert pre_cap < cap < sampled < report


def test_color_diag_publication_is_fail_closed_and_atomic():
    code = remote_py()
    tree = ast.parse(code)
    run_case = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_case"
    )
    load_all_call = next(
        node
        for node in ast.walk(run_case)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_all"
    )
    start_gate = next(
        node
        for node in run_case.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "COLOR_DIAG"
        and node.lineno < load_all_call.lineno
    )
    unlink_calls = [
        node
        for node in ast.walk(start_gate)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "unlink"
    ]
    assert unlink_calls
    assert all(call.lineno < load_all_call.lineno for call in unlink_calls)

    color_diag = next(
        node
        for node in run_case.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "COLOR_DIAG"
        and node.lineno > load_all_call.lineno
    )
    diag_source = ast.get_source_segment(code, color_diag)
    assert diag_source is not None
    assert diag_source.count("tempfile.NamedTemporaryFile(") == 2
    assert diag_source.count("os.fsync(") == 2
    assert "np.load(_sample_temp_path, allow_pickle=False)" in diag_source
    assert '"artifact_state": "complete"' in diag_source
    assert '"artifact_transaction_id": _artifact_transaction_id' in diag_source
    assert '"helper_source_sha256": _helper_source_sha256' in diag_source
    assert ".write_text(" not in diag_source

    savez_call = next(
        node
        for node in ast.walk(color_diag)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "savez_compressed"
    )
    assert ast.unparse(savez_call.args[0]) == "_sample_handle"

    sample_replace = diag_source.index("_sample_temp_path.replace(_sample_path)")
    final_hash = diag_source.index("_sample_sha256 = hashlib.sha256(_sample_path.read_bytes())")
    json_replace = diag_source.index("_json_temp_path.replace(_color_diag_json_path)")
    assert sample_replace < final_hash < json_replace
    for transaction_field in (
        '"log_id": log_dir.name',
        '"anchor_index": int(anchor_idx)',
        '"sample_sha256": _sample_sha256',
        '"helper_source_sha256": _helper_source_sha256',
    ):
        assert transaction_field in diag_source
    assert "finally:" in diag_source
    assert "_temp_path.unlink(missing_ok=True)" in diag_source
