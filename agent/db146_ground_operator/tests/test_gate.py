import numpy as np

from agent.db145_ground_operator.baseline import BaselineResult
from agent.db145_ground_operator.solver import SolverResult
from agent.db146_ground_operator.gate import (
    BAND_SPECS,
    FoldBandMetrics,
    checker_ratio,
    select_safe_band,
    structured_group_folds,
    truncated_texture,
)


def _inverse(texture):
    h, w = texture.shape[:2]
    return SolverResult(
        texture_rgb=texture,
        evidence_valid=np.ones((h, w), bool),
        source_shift_cell=np.zeros((1, 2), np.float32),
        source_gain=np.ones(1, np.float32),
        loss_curve=(),
        elapsed_s=0.0,
        max_cuda_memory_mb=0.0,
    )


def test_structured_group_folds_are_deterministic_disjoint_and_complete():
    groups = [f"f{frame:03d}:ring_front_center" for frame in range(6)]
    counts = dict(zip(groups, [100, 80, 60, 40, 20, 10], strict=True))
    first = structured_group_folds(counts, list(reversed(counts)))
    second = structured_group_folds(counts, list(counts))
    assert first == second
    flattened = [group for fold in first for group in fold]
    assert sorted(flattened) == sorted(counts)
    assert len(flattened) == len(set(flattened))
    fold_frames = [
        [int(group.split(":", 1)[0][1:]) for group in fold] for fold in first
    ]
    assert max(fold_frames[0]) < min(fold_frames[1])
    assert max(fold_frames[1]) < min(fold_frames[2])


def test_structured_folds_hold_out_complete_cameras_when_informative():
    groups = [
        f"f{frame:03d}:cam_{camera}"
        for camera in "abcd"
        for frame in range(3)
    ]
    counts = {group: 100 for group in groups}
    folds = structured_group_folds(counts, groups)
    camera_fold: dict[str, int] = {}
    for fold_index, fold in enumerate(folds):
        for group in fold:
            camera = group.split(":", 1)[1]
            camera_fold.setdefault(camera, fold_index)
            assert camera_fold[camera] == fold_index


def test_truncated_texture_suppresses_checker_correction():
    h = w = 32
    baseline_texture = np.full((h, w, 3), 0.4, np.float32)
    yy, xx = np.mgrid[:h, :w]
    checker = ((xx + yy) % 2 * 2 - 1).astype(np.float32)
    recovered = np.clip(baseline_texture + 0.15 * checker[..., None], 0, 1)
    baseline = BaselineResult(
        baseline_texture, np.ones((h, w), bool), np.ones((h, w), np.uint16)
    )
    lowpassed = truncated_texture(baseline, _inverse(recovered), 2.0)
    assert checker_ratio(lowpassed, baseline_texture) < 0.10 * checker_ratio(
        recovered, baseline_texture
    )


def test_gate_picks_finest_safe_band_and_rejects_bad_full_inverse():
    fold_metrics = {}
    corrections = {}
    base = np.zeros((8, 8, 3), np.float32)
    for label, sigma in BAND_SPECS:
        good = sigma >= 1.0
        fold_metrics[label] = [
            FoldBandMetrics(
                fold=fold,
                baseline_robust_mae=0.10,
                candidate_robust_mae=0.09 if good else 0.11,
                baseline_median_l2=0.12,
                candidate_median_l2=0.11 if good else 0.13,
                checker_ratio=1.0 if good else 2.0,
            )
            for fold in range(3)
        ]
        corrections[label] = [base + 0.01 * (fold + 1) for fold in range(3)]
    decision = select_safe_band(fold_metrics, corrections)
    assert decision.selected_label == "lp1"
    assert decision.uses_inverse


def test_gate_falls_back_when_inner_validation_does_not_prove_gain():
    fold_metrics = {}
    corrections = {}
    for label, _ in BAND_SPECS:
        fold_metrics[label] = [
            FoldBandMetrics(
                fold=fold,
                baseline_robust_mae=0.10,
                candidate_robust_mae=0.101,
                baseline_median_l2=0.12,
                candidate_median_l2=0.121,
                checker_ratio=1.0,
            )
            for fold in range(3)
        ]
        corrections[label] = [np.zeros((4, 4, 3), np.float32) for _ in range(3)]
    decision = select_safe_band(fold_metrics, corrections)
    assert decision.selected_label == "A"
    assert not decision.uses_inverse
    assert decision.fallback_reason
