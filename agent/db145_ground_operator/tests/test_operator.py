import numpy as np
import torch

from agent.db145_ground_operator.operator import (
    EWAObservationSet,
    _build_fixed_support_pairs,
)


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


def test_vectorized_support_matches_scalar_reference_exactly():
    rng = np.random.default_rng(145)
    centers = rng.uniform(-0.5, 5.5, size=(40, 2)).astype(np.float32)
    angle = rng.uniform(0, np.pi, size=40)
    eigenvalues = rng.uniform([0.03, 0.4], [0.3, 3.0], size=(40, 2))
    rotation = np.stack(
        [
            np.stack((np.cos(angle), -np.sin(angle)), axis=1),
            np.stack((np.sin(angle), np.cos(angle)), axis=1),
        ],
        axis=1,
    )
    covariance = np.einsum(
        "nij,nj,nkj->nik", rotation, eigenvalues, rotation
    ).astype(np.float32)
    obs, texel, xy = _build_fixed_support_pairs(
        centers,
        covariance,
        grid_hw=(6, 6),
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
    )
    vectorized = set(
        zip(obs.tolist(), texel.tolist(), map(tuple, xy.astype(int).tolist()), strict=True)
    )

    scalar = set()
    for obs_id, (center, cov) in enumerate(zip(centers, covariance, strict=True)):
        eig = np.linalg.eigvalsh(cov.astype(np.float64))
        radius = 3.0 * np.sqrt(eig[-1]) + 0.5
        x0, x1 = max(0, int(np.floor(center[0] - radius))), min(
            5, int(np.ceil(center[0] + radius))
        )
        y0, y1 = max(0, int(np.floor(center[1] - radius))), min(
            5, int(np.ceil(center[1] + radius))
        )
        yy, xx = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
        points = np.column_stack((xx.ravel(), yy.ravel()))
        delta = points - center
        mahalanobis = np.einsum(
            "ni,ij,nj->n", delta, np.linalg.inv(cov.astype(np.float64)), delta
        )
        keep = np.maximum(mahalanobis - 0.5**2 / eig[0], 0.0) <= 3.0**2
        for point in points[keep]:
            scalar.add((obs_id, int(point[1] * 6 + point[0]), tuple(point)))
    assert vectorized == scalar


def test_subset_compacts_sparse_source_ids():
    observations = EWAObservationSet.from_numpy(
        centers_cell=np.array([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0]], np.float32),
        covariance_cell=np.repeat(
            np.array([[[0.2, 0.0], [0.0, 0.2]]], np.float32), 3, axis=0
        ),
        source_ids=np.array([0, 4, 9], np.int64),
        rgb=np.zeros((3, 3), np.float32),
        grid_hw=(5, 5),
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
        provenance={"original_source_id": np.array([10, 20, 30])},
    )
    subset = observations.subset(np.array([False, True, True]))
    assert subset.n_sources == 2
    np.testing.assert_array_equal(subset.source_ids.cpu().numpy(), [0, 1])
    np.testing.assert_array_equal(
        subset.provenance["original_source_id"], [20, 30]
    )
