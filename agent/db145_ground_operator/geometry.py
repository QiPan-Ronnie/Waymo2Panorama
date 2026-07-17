from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import DEFAULT_CONFIG, ExperimentConfig


@dataclass(frozen=True)
class PixelFootprint:
    """A raw camera pixel's first-order footprint on a local ground plane.

    The two covariance axes are expressed in city XY metres.  ``area_m2`` is
    the area of the one-standard-deviation ellipse; it is used only as an
    evidence gate, not as a claim that a projected pixel is Gaussian.
    """

    center_xy: np.ndarray
    covariance_xy: np.ndarray
    jacobian_xy_uv: np.ndarray
    area_m2: float
    aspect_ratio: float
    range_m: float
    valid: bool
    reason: str = ""


def _invalid_footprint(reason: str) -> PixelFootprint:
    return PixelFootprint(
        center_xy=np.full(2, np.nan, dtype=np.float64),
        covariance_xy=np.full((2, 2), np.nan, dtype=np.float64),
        jacobian_xy_uv=np.full((2, 2), np.nan, dtype=np.float64),
        area_m2=float("nan"),
        aspect_ratio=float("inf"),
        range_m=float("nan"),
        valid=False,
        reason=reason,
    )


def normalize_plane(plane_n: np.ndarray, plane_d: float) -> tuple[np.ndarray, float]:
    n = np.asarray(plane_n, dtype=np.float64).reshape(3)
    norm = float(np.linalg.norm(n))
    if not np.isfinite(norm) or norm < 1.0e-12:
        raise ValueError("plane normal is degenerate")
    return n / norm, float(plane_d) / norm


def intersect_rays_with_plane(
    origins: np.ndarray,
    rays: np.ndarray,
    plane_n: np.ndarray,
    plane_d: float,
    *,
    horizon_epsilon: float = 1.0e-8,
) -> tuple[np.ndarray, np.ndarray]:
    """Intersect forward rays with ``n dot X + d = 0``.

    Invalid/behind-camera intersections are returned as NaN and accompanied by
    a false validity bit.  Rays need not be normalized.
    """

    o = np.asarray(origins, dtype=np.float64)
    r = np.asarray(rays, dtype=np.float64)
    if o.ndim == 1:
        o = o[None, :]
    if r.ndim == 1:
        r = r[None, :]
    if o.shape[-1] != 3 or r.shape[-1] != 3:
        raise ValueError("origins and rays must end in dimension 3")
    if o.shape[0] == 1 and r.shape[0] != 1:
        o = np.broadcast_to(o, r.shape)
    if o.shape != r.shape:
        raise ValueError("origins and rays are not broadcast-compatible")

    n, d = normalize_plane(plane_n, plane_d)
    denom = r @ n
    numer = -(o @ n + d)
    finite = np.isfinite(o).all(axis=1) & np.isfinite(r).all(axis=1)
    non_grazing = np.abs(denom) > horizon_epsilon
    t = np.full(r.shape[0], np.nan, dtype=np.float64)
    np.divide(numer, denom, out=t, where=non_grazing)
    valid = finite & non_grazing & np.isfinite(t) & (t > 0.0)
    xyz = o + t[:, None] * r
    xyz[~valid] = np.nan
    return xyz, valid


def camera_rays(uv: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Return camera-frame rays using the AV2/OpenCV (+Z forward) convention."""

    points = np.asarray(uv, dtype=np.float64)
    one = points.ndim == 1
    points = np.atleast_2d(points)
    if points.shape[1] != 2:
        raise ValueError("uv must have shape (..., 2)")
    intrinsic = np.asarray(K, dtype=np.float64).reshape(3, 3)
    rays = np.linalg.solve(intrinsic, np.c_[points, np.ones(len(points))].T).T
    return rays[0] if one else rays


def project_city_to_image(
    xyz_city: np.ndarray, K: np.ndarray, T_city_cam: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Project city-frame points into an OpenCV camera."""

    xyz = np.asarray(xyz_city, dtype=np.float64)
    one = xyz.ndim == 1
    xyz = np.atleast_2d(xyz)
    T = np.asarray(T_city_cam, dtype=np.float64).reshape(4, 4)
    T_cam_city = np.linalg.inv(T)
    xyz_cam = (T_cam_city[:3, :3] @ xyz.T).T + T_cam_city[:3, 3]
    valid = np.isfinite(xyz_cam).all(axis=1) & (xyz_cam[:, 2] > 1.0e-8)
    homogeneous = (np.asarray(K, dtype=np.float64) @ xyz_cam.T).T
    uv = np.full((len(xyz), 2), np.nan, dtype=np.float64)
    uv[valid] = homogeneous[valid, :2] / homogeneous[valid, 2:3]
    return (uv[0], valid[0]) if one else (uv, valid)


def pixel_footprint_on_plane(
    uv: np.ndarray,
    K: np.ndarray,
    T_city_cam: np.ndarray,
    plane_n: np.ndarray,
    plane_d: float,
    *,
    config: ExperimentConfig = DEFAULT_CONFIG,
) -> PixelFootprint:
    """Linearize one pixel's projected support on a local ground plane."""

    uv = np.asarray(uv, dtype=np.float64).reshape(2)
    # Order: centre, u-, u+, v-, v+.  Opposing half-pixel samples are one
    # full pixel apart, so their difference directly estimates each J column.
    probes = np.array(
        [
            uv,
            uv + [-0.5, 0.0],
            uv + [0.5, 0.0],
            uv + [0.0, -0.5],
            uv + [0.0, 0.5],
        ],
        dtype=np.float64,
    )
    T = np.asarray(T_city_cam, dtype=np.float64).reshape(4, 4)
    origin = T[:3, 3]
    rays_city = (T[:3, :3] @ camera_rays(probes, K).T).T
    hits, valid = intersect_rays_with_plane(
        np.broadcast_to(origin, rays_city.shape), rays_city, plane_n, plane_d
    )
    if not bool(valid.all()):
        return _invalid_footprint("horizon_or_behind")

    center = hits[0]
    range_m = float(np.linalg.norm(center - origin))
    if not config.min_source_range_m <= range_m <= config.max_source_range_m:
        return _invalid_footprint("range")

    J = np.column_stack((hits[2, :2] - hits[1, :2], hits[4, :2] - hits[3, :2]))
    if not np.isfinite(J).all():
        return _invalid_footprint("nonfinite_jacobian")

    raster_floor = (config.cell_m**2 / 12.0) * np.eye(2)
    covariance = (J @ J.T) / 12.0 + raster_floor
    eigenvalues = np.linalg.eigvalsh(covariance)
    if not np.isfinite(eigenvalues).all() or eigenvalues[0] <= 0.0:
        return _invalid_footprint("degenerate_covariance")
    aspect = float(np.sqrt(eigenvalues[-1] / eigenvalues[0]))
    area = float(np.pi * np.sqrt(np.prod(eigenvalues)))
    if aspect > config.max_footprint_aspect:
        return _invalid_footprint("aspect")
    if area > config.max_footprint_area_m2:
        return _invalid_footprint("area")

    return PixelFootprint(
        center_xy=center[:2].copy(),
        covariance_xy=covariance,
        jacobian_xy_uv=J,
        area_m2=area,
        aspect_ratio=aspect,
        range_m=range_m,
        valid=True,
    )
