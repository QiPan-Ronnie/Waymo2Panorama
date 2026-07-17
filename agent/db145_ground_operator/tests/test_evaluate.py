import numpy as np

from agent.db145_ground_operator.evaluate import dense_uv_evidence, evaluate_heldout
from agent.db145_ground_operator.operator import EWAObservationSet
from agent.db145_ground_operator.solver import SolverResult


def test_heldout_metrics_and_dense_evidence_use_identical_pixels():
    texture = np.zeros((3, 3, 3), np.float32)
    texture[1, 1] = [0.2, 0.4, 0.6]
    obs = EWAObservationSet.from_numpy(
        centers_cell=np.array([[1.0, 1.0]], np.float32),
        covariance_cell=np.array([np.eye(2) * 0.01], np.float32),
        source_ids=np.array([0]),
        rgb=np.array([[0.3, 0.4, 0.5]], np.float32),
        grid_hw=(3, 3),
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
    )
    result = SolverResult(
        texture,
        np.ones((3, 3), bool),
        np.zeros((1, 2), np.float32),
        np.ones(1, np.float32),
        (),
        0.0,
        0.0,
    )
    evaluation = evaluate_heldout(result, obs)
    np.testing.assert_allclose(evaluation.predicted_rgb[0], [0.2, 0.4, 0.6], atol=1e-4)
    assert evaluation.metrics.n_pixels == 1
    board = dense_uv_evidence(evaluation, np.array([[10, 20]]), padding=0)
    np.testing.assert_allclose(board["raw"][0, 0], [0.3, 0.4, 0.5])
    assert board["mask"][0, 0] == 255
