from __future__ import annotations

import warnings

import numpy as np
import pytest

from agent.db181_multids.geometry import (
    make_transform,
    matrix_to_quaternion_wxyz,
    quaternion_wxyz_to_matrix,
    relative_transform,
    rotation_z_deg,
    validate_rigid_transform,
)


def test_validate_identity_returns_defensive_float64_copy() -> None:
    source = np.eye(4, dtype=np.float32)

    checked = validate_rigid_transform(source)

    assert checked.dtype == np.float64
    assert np.array_equal(checked, np.eye(4))
    assert not np.shares_memory(checked, source)

    source[0, 3] = 12.0
    assert checked[0, 3] == 0.0

    checked[1, 3] = 7.0
    assert source[1, 3] == 0.0


@pytest.mark.parametrize(
    "transform",
    [
        np.eye(3),
        np.full((4, 4), np.nan),
        np.array(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 1e-4, 1.0],
            ]
        ),
        np.diag([2.0, 1.0, 1.0, 1.0]),
        np.diag([-1.0, 1.0, 1.0, 1.0]),
    ],
    ids=["wrong-shape", "nonfinite", "bad-bottom-row", "not-orthonormal", "reflection"],
)
def test_validate_rigid_transform_rejects_bad_matrices(transform: np.ndarray) -> None:
    with pytest.raises(ValueError):
        validate_rigid_transform(transform)


def test_quaternion_identity_and_pandaset_plus_90_degree_normalization() -> None:
    assert np.allclose(quaternion_wxyz_to_matrix((1.0, 0.0, 0.0, 0.0)), np.eye(3))

    half_sqrt_two = np.sqrt(0.5)
    from_quaternion = quaternion_wxyz_to_matrix(
        (half_sqrt_two, 0.0, 0.0, half_sqrt_two)
    )
    expected = np.array(
        [
            [0.0, -1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
    )

    assert np.allclose(rotation_z_deg(90.0), expected, atol=1e-15)
    assert np.allclose(from_quaternion, rotation_z_deg(90.0), atol=1e-15)
    assert np.allclose(rotation_z_deg(90.0) @ np.array([1.0, 0.0, 0.0]), [0.0, 1.0, 0.0])


def test_near_180_degree_quaternion_matrix_roundtrip_is_canonical() -> None:
    axis = np.array([-1.0, 2.0, -3.0])
    axis /= np.linalg.norm(axis)
    half_angle = np.deg2rad(179.999999) / 2.0
    quaternion = -np.concatenate(([np.cos(half_angle)], axis * np.sin(half_angle)))

    rotation = quaternion_wxyz_to_matrix(quaternion)
    recovered = matrix_to_quaternion_wxyz(rotation)

    assert recovered[0] > 0.0
    assert np.allclose(quaternion_wxyz_to_matrix(recovered), rotation, atol=1e-12)


def test_exact_180_degree_quaternion_uses_first_nonzero_vector_component_sign() -> None:
    axis = np.array([-1.0, 2.0, -3.0])
    axis /= np.linalg.norm(axis)
    rotation = quaternion_wxyz_to_matrix(np.concatenate(([0.0], axis)))

    recovered = matrix_to_quaternion_wxyz(rotation)

    assert abs(recovered[0]) <= 1e-12
    assert recovered[1] > 0.0
    assert np.allclose(quaternion_wxyz_to_matrix(recovered), rotation, atol=1e-12)


@pytest.mark.parametrize("w", [1e-12, -1e-12], ids=["below-pi", "above-pi"])
def test_near_180_degree_canonicalization_preserves_nonzero_w(w: float) -> None:
    x = np.sqrt(1.0 - w * w)
    quaternion = np.array([w, x, 0.0, 0.0])
    rotation = quaternion_wxyz_to_matrix(quaternion)

    recovered = matrix_to_quaternion_wxyz(rotation)
    recovered_from_negated_input = matrix_to_quaternion_wxyz(
        quaternion_wxyz_to_matrix(-quaternion)
    )

    assert recovered[0] == pytest.approx(w, abs=1e-15)
    assert recovered[1] > 0.0
    assert recovered_from_negated_input == pytest.approx(recovered, abs=1e-15)
    rebuilt = quaternion_wxyz_to_matrix(recovered)
    assert np.max(np.abs(rebuilt - rotation)) <= 1e-14


@pytest.mark.parametrize(
    "quaternion",
    [
        (0.0, 0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 0.0, np.nan),
    ],
    ids=["zero", "wrong-shape", "nonfinite"],
)
def test_quaternion_wxyz_to_matrix_rejects_invalid_input(
    quaternion: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError):
        quaternion_wxyz_to_matrix(quaternion)


@pytest.mark.parametrize(
    "rotation",
    [
        np.eye(4),
        np.full((3, 3), np.inf),
        np.diag([1.0, 1.0, 2.0]),
        np.diag([-1.0, 1.0, 1.0]),
    ],
    ids=["wrong-shape", "nonfinite", "not-orthonormal", "reflection"],
)
def test_matrix_to_quaternion_rejects_invalid_rotation(rotation: np.ndarray) -> None:
    with pytest.raises(ValueError):
        matrix_to_quaternion_wxyz(rotation)


@pytest.mark.parametrize(
    ("operation", "value"),
    [
        (quaternion_wxyz_to_matrix, np.array([1.0 + 1.0j, 0.0, 0.0, 0.0])),
        (matrix_to_quaternion_wxyz, np.eye(3, dtype=np.complex128)),
        (lambda value: make_transform(np.eye(3), value), np.array([1.0 + 1.0j, 2.0, 3.0])),
    ],
    ids=["quaternion", "matrix", "translation"],
)
def test_geometry_rejects_complex_inputs_without_warning(operation: object, value: object) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValueError, match="complex"):
            operation(value)  # type: ignore[operator]


def test_make_transform_copies_inputs_and_requires_exact_translation_shape() -> None:
    rotation = rotation_z_deg(30.0)
    translation = np.array([1.0, 2.0, 3.0], dtype=np.float32)

    transform = make_transform(rotation, translation)

    assert transform.dtype == np.float64
    assert np.allclose(transform[:3, :3], rotation)
    assert np.array_equal(transform[:3, 3], translation)
    assert not np.shares_memory(transform, rotation)
    assert not np.shares_memory(transform, translation)

    rotation[0, 0] = 99.0
    translation[0] = 99.0
    assert transform[0, 0] != 99.0
    assert transform[0, 3] == 1.0

    with pytest.raises(ValueError):
        make_transform(np.eye(3), np.array([[1.0, 2.0, 3.0]]))
    with pytest.raises(ValueError):
        make_transform(np.eye(3), [1.0, 2.0, np.inf])


def test_relative_transform_reconstructs_child_from_parent() -> None:
    parent = make_transform(rotation_z_deg(23.0), [1.25, -4.0, 0.5])
    child = make_transform(rotation_z_deg(-71.0), [-8.0, 3.5, 2.0])

    relative = relative_transform(parent, child)

    assert np.allclose(parent @ relative, child, atol=1e-9, rtol=0.0)
    assert not np.shares_memory(relative, parent)
    assert not np.shares_memory(relative, child)


@pytest.mark.parametrize("degrees", [np.nan, np.inf, [90.0]])
def test_rotation_z_deg_rejects_nonfinite_or_nonscalar_input(degrees: object) -> None:
    with pytest.raises(ValueError):
        rotation_z_deg(degrees)  # type: ignore[arg-type]
