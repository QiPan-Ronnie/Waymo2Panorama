from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy.spatial.transform import Rotation


_ROTATION_ATOL = 1e-9
_QUATERNION_ZERO_ATOL = 1e-12


def _float64_copy(value: object, name: str) -> np.ndarray:
    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain numeric values") from error
    if np.iscomplexobj(array) or (
        array.dtype == object
        and any(isinstance(item, (complex, np.complexfloating)) for item in array.flat)
    ):
        raise ValueError(f"{name} must not contain complex values")
    try:
        return np.array(array, dtype=np.float64, copy=True)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain numeric values") from error


def _checked_atol(atol: float) -> float:
    try:
        checked = float(atol)
    except (TypeError, ValueError) as error:
        raise ValueError("atol must be a finite nonnegative number") from error
    if not math.isfinite(checked) or checked < 0.0:
        raise ValueError("atol must be a finite nonnegative number")
    return checked


def _validate_rotation_matrix(
    rotation: object, *, atol: float = _ROTATION_ATOL
) -> np.ndarray:
    checked_atol = _checked_atol(atol)
    matrix = _float64_copy(rotation, "rotation")
    if matrix.shape != (3, 3):
        raise ValueError("rotation must have shape (3, 3)")
    if not np.isfinite(matrix).all():
        raise ValueError("rotation must contain only finite values")
    if not np.allclose(
        matrix.T @ matrix,
        np.eye(3, dtype=np.float64),
        atol=checked_atol,
        rtol=0.0,
    ):
        raise ValueError("rotation must be orthonormal")
    if abs(float(np.linalg.det(matrix)) - 1.0) > checked_atol:
        raise ValueError("rotation determinant must be +1")
    return matrix


def validate_rigid_transform(T: object, *, atol: float = 1e-9) -> np.ndarray:
    """Validate and return a defensive float64 copy of a rigid transform."""
    checked_atol = _checked_atol(atol)
    transform = _float64_copy(T, "transform")
    if transform.shape != (4, 4):
        raise ValueError("transform must have shape (4, 4)")
    if not np.isfinite(transform).all():
        raise ValueError("transform must contain only finite values")
    if not np.allclose(
        transform[3],
        np.array([0.0, 0.0, 0.0, 1.0]),
        atol=checked_atol,
        rtol=0.0,
    ):
        raise ValueError("transform bottom row must be [0, 0, 0, 1]")
    _validate_rotation_matrix(transform[:3, :3], atol=checked_atol)
    return transform


def quaternion_wxyz_to_matrix(q: Sequence[float] | np.ndarray) -> np.ndarray:
    """Convert a w,x,y,z quaternion to a proper float64 rotation matrix."""
    quaternion = _float64_copy(q, "quaternion")
    if quaternion.shape != (4,):
        raise ValueError("quaternion must have shape (4,)")
    if not np.isfinite(quaternion).all():
        raise ValueError("quaternion must contain only finite values")

    scale = float(np.max(np.abs(quaternion)))
    if scale == 0.0:
        raise ValueError("quaternion norm must be nonzero")
    normalized = quaternion / scale
    normalized /= np.linalg.norm(normalized)
    xyzw = normalized[[1, 2, 3, 0]]
    return np.array(Rotation.from_quat(xyzw).as_matrix(), dtype=np.float64, copy=True)


def matrix_to_quaternion_wxyz(R: object) -> tuple[float, float, float, float]:
    """Convert a proper rotation matrix to a canonical w,x,y,z quaternion."""
    rotation = _validate_rotation_matrix(R)
    xyzw = Rotation.from_matrix(rotation).as_quat()
    quaternion = np.array([xyzw[3], xyzw[0], xyzw[1], xyzw[2]], dtype=np.float64)

    if quaternion[0] < -_QUATERNION_ZERO_ATOL:
        quaternion *= -1.0
    elif abs(quaternion[0]) <= _QUATERNION_ZERO_ATOL:
        for component in quaternion[1:]:
            if abs(component) > _QUATERNION_ZERO_ATOL:
                if component < 0.0:
                    quaternion *= -1.0
                break

    return tuple(float(component) for component in quaternion)


def make_transform(R: object, t: Sequence[float] | np.ndarray) -> np.ndarray:
    """Build and validate a rigid transform from rotation and translation."""
    rotation = _validate_rotation_matrix(R)
    translation = _float64_copy(t, "translation")
    if translation.shape != (3,):
        raise ValueError("translation must have shape (3,)")
    if not np.isfinite(translation).all():
        raise ValueError("translation must contain only finite values")

    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rotation
    transform[:3, 3] = translation
    return validate_rigid_transform(transform)


def relative_transform(T_world_parent: object, T_world_child: object) -> np.ndarray:
    """Return the child pose in the parent coordinate frame."""
    world_parent = validate_rigid_transform(T_world_parent)
    world_child = validate_rigid_transform(T_world_child)
    return validate_rigid_transform(np.linalg.inv(world_parent) @ world_child)


def rotation_z_deg(degrees: float) -> np.ndarray:
    """Return a right-handed rotation about +Z by the given degrees."""
    degree_array = np.asarray(degrees)
    if degree_array.shape != ():
        raise ValueError("degrees must be a finite scalar")
    try:
        angle = float(degree_array)
    except (TypeError, ValueError) as error:
        raise ValueError("degrees must be a finite scalar") from error
    if not math.isfinite(angle):
        raise ValueError("degrees must be a finite scalar")

    radians = math.radians(angle)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return _validate_rotation_matrix(
        np.array(
            [
                [cosine, -sine, 0.0],
                [sine, cosine, 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
    )
