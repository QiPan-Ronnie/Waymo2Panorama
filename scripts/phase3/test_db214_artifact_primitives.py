import math
from pathlib import Path

import numpy as np
import pytest

from scripts.phase3.db214_artifact_primitives import (
    angular_overlap_weight,
    annotation_enabled,
    pair_evidence_weights,
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
