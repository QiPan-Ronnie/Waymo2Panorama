import numpy as np
import torch

from agent.db145_ground_operator.operator import EWAObservationSet


def _observations(covariance: np.ndarray, grid_hw=(5, 5)) -> EWAObservationSet:
    return EWAObservationSet.from_numpy(
        centers_cell=np.array([[2.0, 2.0]], np.float32),
        covariance_cell=np.asarray(covariance, np.float32)[None],
        source_ids=np.array([0], np.int64),
        rgb=np.zeros((1, 3), np.float32),
        grid_hw=grid_hw,
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
    )


def test_isotropic_one_texel_observation_is_identity():
    obs = EWAObservationSet.from_numpy(
        centers_cell=np.array([[1.0, 1.0]], np.float32),
        covariance_cell=np.array([[[0.03, 0.0], [0.0, 0.03]]], np.float32),
        source_ids=np.array([0], np.int64),
        rgb=np.array([[0.2, 0.4, 0.6]], np.float32),
        grid_hw=(3, 3),
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
    )
    texture = torch.zeros((3, 3, 3))
    texture[1, 1] = torch.tensor([0.2, 0.4, 0.6])
    pred = obs.predict(texture, torch.zeros((1, 2)))
    torch.testing.assert_close(pred[0], texture[1, 1], atol=1e-4, rtol=0.0)


def test_anisotropic_operator_blurs_only_long_axis():
    obs = _observations(np.array([[4.0, 0.0], [0.0, 0.03]]))
    vertical = torch.zeros((5, 5, 3))
    vertical[:, 2] = 1.0
    horizontal = torch.zeros((5, 5, 3))
    horizontal[2, :] = 1.0
    shift = torch.zeros((1, 2))
    pred_vertical = obs.predict(vertical, shift)[0, 0]
    pred_horizontal = obs.predict(horizontal, shift)[0, 0]
    assert float(pred_horizontal) > float(pred_vertical) + 0.25


def test_operator_is_differentiable_in_texture_and_shift():
    obs = _observations(np.array([[1.0, 0.2], [0.2, 0.5]]))
    texture = torch.rand((5, 5, 3), requires_grad=True)
    raw_shift = torch.zeros((1, 2), requires_grad=True)
    pred = obs.predict(texture, obs.bounded_shift(raw_shift))
    pred.sum().backward()
    assert texture.grad is not None and torch.isfinite(texture.grad).all()
    assert raw_shift.grad is not None and torch.isfinite(raw_shift.grad).all()


def test_nominal_weights_are_positive_and_normalized():
    obs = _observations(np.array([[1.0, 0.0], [0.0, 0.5]]))
    texture = torch.ones((5, 5, 3))
    pred, weight_sum = obs.predict(
        texture, torch.zeros((1, 2)), return_weight_sum=True
    )
    torch.testing.assert_close(pred, torch.ones_like(pred))
    assert bool((weight_sum > 0).all())
