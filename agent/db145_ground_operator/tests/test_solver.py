import numpy as np
import torch

from agent.db145_ground_operator.baseline import BaselineResult
from agent.db145_ground_operator.config import ExperimentConfig
from agent.db145_ground_operator.evaluate import evaluate_heldout
from agent.db145_ground_operator.operator import EWAObservationSet
from agent.db145_ground_operator.solver import solve_sensor_native


def _synthetic_fixture():
    rng = np.random.default_rng(145)
    h = w = 16
    yy, xx = np.mgrid[:h, :w]
    truth = np.zeros((h, w, 3), np.float32) + 0.15
    truth[np.abs(yy - xx) <= 1] = [0.95, 0.8, 0.15]
    truth[np.abs(yy + xx - (w - 1)) <= 1] = [0.1, 0.7, 0.95]
    centers = rng.uniform(1.0, 14.0, size=(700, 2)).astype(np.float32)
    source_ids = np.repeat(np.arange(7), 100)
    cov = np.empty((700, 2, 2), np.float32)
    cov[:400] = [[1.8, 0.0], [0.0, 0.12]]
    cov[400:] = [[0.12, 0.0], [0.0, 1.8]]
    dummy = np.zeros((700, 3), np.float32)
    all_obs = EWAObservationSet.from_numpy(
        centers_cell=centers,
        covariance_cell=cov,
        source_ids=source_ids,
        rgb=dummy,
        grid_hw=(h, w),
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
    )
    true_shift = np.zeros((7, 2), np.float32)
    true_shift[:6] = rng.uniform(-0.20, 0.20, size=(6, 2))
    true_gain = np.ones(7, np.float32)
    true_gain[:6] = rng.uniform(0.94, 1.06, size=6)
    with torch.no_grad():
        normalized = all_obs.predict(torch.tensor(truth), torch.tensor(true_shift)).numpy()
    raw = np.clip(normalized / true_gain[source_ids, None], 0.0, 1.0)
    raw += rng.normal(0.0, 0.003, raw.shape).astype(np.float32)
    train = EWAObservationSet.from_numpy(
        centers_cell=centers[:600],
        covariance_cell=cov[:600],
        source_ids=source_ids[:600],
        rgb=raw[:600],
        grid_hw=(h, w),
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
    )
    # Renumber held-out to source 0 so strict evaluation uses zero shift/unit gain.
    heldout = EWAObservationSet.from_numpy(
        centers_cell=centers[600:],
        covariance_cell=cov[600:],
        source_ids=np.zeros(100, np.int64),
        rgb=raw[600:],
        grid_hw=(h, w),
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
    )
    coarse = truth.copy()
    coarse = torch.nn.functional.avg_pool2d(
        torch.tensor(coarse).permute(2, 0, 1)[None], 3, 1, 1
    )[0].permute(1, 2, 0).numpy()
    baseline = BaselineResult(coarse, np.ones((h, w), bool), np.ones((h, w), np.uint16))
    return truth, train, heldout, baseline


def test_solver_is_bounded_and_improves_heldout_over_coarse_baseline():
    _, train, heldout, baseline = _synthetic_fixture()
    config = ExperimentConfig(
        patch_size_m=0.4,
        grid_hw=16,
        solver_steps=100,
        learning_rate=0.04,
    )
    baseline_result = type("R", (), {})()
    baseline_result.texture_rgb = baseline.texture_rgb
    baseline_result.evidence_valid = baseline.valid
    baseline_result.source_shift_cell = np.zeros((6, 2), np.float32)
    baseline_result.source_gain = np.ones(6, np.float32)
    baseline_error = evaluate_heldout(baseline_result, heldout).metrics.robust_rgb_mae
    solved = solve_sensor_native(train, baseline, config=config)
    solved_error = evaluate_heldout(solved, heldout).metrics.robust_rgb_mae
    assert solved_error < 0.90 * baseline_error
    assert np.max(np.abs(solved.source_shift_cell)) <= 0.5 + 1e-6
    assert np.max(np.abs(np.log(solved.source_gain))) <= 0.10 + 1e-6
    assert abs(solved.source_shift_cell[1:].mean(axis=0)).max() < 1e-5
    assert solved.loss_curve[-1] < solved.loss_curve[0]
