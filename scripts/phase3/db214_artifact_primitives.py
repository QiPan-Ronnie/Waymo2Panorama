"""Small, testable DB-214 policies used by the production panorama renderer."""

from __future__ import annotations

import math
import json
from pathlib import Path
from typing import Callable

import numpy as np


def validate_renderer_capabilities(log_dir: Path, ground_mode: str) -> None:
    """Fail closed when a requested renderer mode needs absent source evidence."""

    manifest_path = Path(log_dir) / "conversion_manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid conversion manifest: {manifest_path}") from error
    has_lidar = manifest.get("has_lidar") is True
    has_ego_pose = manifest.get("has_ego_pose") is True
    if ground_mode != "off" and (not has_lidar or not has_ego_pose):
        raise ValueError(
            "camera-only conversion requires GROUND_MODE='off'; ground/temporal "
            "fill needs real LiDAR and ego-pose evidence"
        )


def load_ego_pose_interpolators(
    log_dir: Path,
) -> tuple[
    Callable[[int], tuple[np.ndarray, np.ndarray]],
    Callable[[np.ndarray], np.ndarray],
]:
    """Load ego-pose interpolation, or expose an explicit static-rig fallback.

    Mode-B camera-only datasets do not contain an ego trajectory.  Treating the
    rig as static preserves same-record camera geometry without inventing motion.
    The caller can still report ``has_ego_pose=false`` in its conversion manifest.
    """

    pose_path = Path(log_dir) / "city_SE3_egovehicle.feather"
    if not pose_path.exists():
        identity = np.eye(3, dtype=np.float64)
        origin = np.zeros(3, dtype=np.float64)

        def static_cte(_timestamp_ns: int) -> tuple[np.ndarray, np.ndarray]:
            return identity.copy(), origin.copy()

        def static_tri(timestamps_ns: np.ndarray) -> np.ndarray:
            timestamps = np.asarray(timestamps_ns)
            return np.zeros(timestamps.shape + (3,), dtype=np.float64)

        return static_cte, static_tri

    import pandas as pd
    from scipy.spatial.transform import Rotation, Slerp

    poses = (
        pd.read_feather(pose_path)
        .sort_values("timestamp_ns")
        .drop_duplicates("timestamp_ns")
        .reset_index(drop=True)
    )
    timestamps = poses["timestamp_ns"].to_numpy(np.int64)
    if len(timestamps) == 0:
        raise ValueError(f"ego pose table is empty: {pose_path}")
    origin_ns = int(timestamps[0])
    relative = (timestamps - origin_ns).astype(np.float64)
    quaternions = poses[["qx", "qy", "qz", "qw"]].to_numpy(np.float64)
    translations = poses[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
    keep = np.concatenate([[True], np.diff(relative) > 0])
    relative = relative[keep]
    quaternions = quaternions[keep]
    translations = translations[keep]
    lower = float(relative.min())
    upper = float(relative.max())
    slerp = Slerp(relative, Rotation.from_quat(quaternions)) if len(relative) > 1 else None

    def cte(timestamp_ns: int) -> tuple[np.ndarray, np.ndarray]:
        query = float(np.clip(float(int(timestamp_ns) - origin_ns), lower, upper))
        rotation = (
            slerp(query).as_matrix()
            if slerp is not None
            else Rotation.from_quat(quaternions[0]).as_matrix()
        )
        translation = np.array(
            [np.interp(query, relative, translations[:, axis]) for axis in range(3)]
        )
        return rotation, translation

    def tri(timestamps_ns: np.ndarray) -> np.ndarray:
        query = np.clip(
            (np.asarray(timestamps_ns, dtype=np.int64) - origin_ns).astype(np.float64),
            lower,
            upper,
        )
        return np.stack(
            [np.interp(query, relative, translations[:, axis]) for axis in range(3)],
            axis=-1,
        )

    return cte, tri


def annotation_enabled(policy: str, has_annotations: bool) -> bool:
    """Return whether annotation-derived pixels may enter the rendered band."""

    if policy == "raw_sensor":
        return False
    if policy == "composite":
        return bool(has_annotations)
    raise ValueError(f"unknown annotation policy: {policy!r}")


def pair_evidence_weights(
    rho: float | None,
    sample_count: int,
    prior_fraction: float,
) -> tuple[float, float, float]:
    """Continuously split a camera-pair edge between evidence and prior.

    Positive correlation squared is the fraction of shared luminance variation
    explained by a same-surface exposure-offset model.  Uncorrelated, negative,
    flat, or non-finite pairs therefore contribute no measured offset, while the
    complementary fraction falls back to the zero-difference prior.
    """

    n = max(int(sample_count), 0)
    if rho is None or not math.isfinite(float(rho)):
        confidence = 0.0
    else:
        confidence = min(max(float(rho), 0.0), 1.0) ** 2
    measurement_weight = n * confidence
    prior_weight = n * (1.0 - confidence) * float(prior_fraction)
    return measurement_weight, prior_weight, confidence


def solve_gain_components(
    laplacian: np.ndarray,
    rhs: np.ndarray,
    edges: tuple[tuple[int, int], ...] | set[tuple[int, int]],
) -> np.ndarray:
    """Solve relative log gains without inventing offsets between components.

    Pairwise exposure evidence defines a weighted graph Laplacian.  A connected
    graph has one additive gauge, but sparse cross-dataset overlap can leave
    several connected components and therefore several independent gauges.
    Each component is solved with its own zero-mean gauge; an isolated camera
    has no relative evidence and honestly falls back to identity gain.
    """

    matrix = np.asarray(laplacian, dtype=np.float64)
    vector = np.asarray(rhs, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("gain laplacian must be square")
    camera_count = matrix.shape[0]
    if vector.shape != (camera_count,):
        raise ValueError("gain rhs must match the laplacian size")
    if not np.isfinite(matrix).all() or not np.isfinite(vector).all():
        raise ValueError("gain system must be finite")

    neighbours: list[set[int]] = [set() for _ in range(camera_count)]
    for raw_first, raw_second in edges:
        first, second = int(raw_first), int(raw_second)
        if not (0 <= first < camera_count and 0 <= second < camera_count):
            raise ValueError("gain edge camera index is out of range")
        if first == second:
            raise ValueError("gain edge cannot be a self-loop")
        neighbours[first].add(second)
        neighbours[second].add(first)

    gains = np.zeros(camera_count, dtype=np.float64)
    unseen = set(range(camera_count))
    while unseen:
        seed = min(unseen)
        component: list[int] = []
        frontier = [seed]
        unseen.remove(seed)
        while frontier:
            camera = frontier.pop()
            component.append(camera)
            for neighbour in sorted(neighbours[camera]):
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    frontier.append(neighbour)
        indices = np.asarray(sorted(component), dtype=np.int64)
        if len(indices) == 1 and not neighbours[int(indices[0])]:
            continue
        submatrix = matrix[np.ix_(indices, indices)]
        subrhs = vector[indices]
        gauged = submatrix + np.ones_like(submatrix)
        try:
            solution = np.linalg.solve(gauged, subrhs)
        except np.linalg.LinAlgError as error:
            raise ValueError(
                f"gain component remains singular: cameras={indices.tolist()}"
            ) from error
        solution -= solution.mean()
        gains[indices] = solution
    return gains


def angular_overlap_weight(overlap: np.ndarray, ramp_angle_rad: float) -> np.ndarray:
    """Fine-depth weight from angular distance to overlap on a periodic ERP."""

    from scipy.ndimage import distance_transform_edt

    overlap = np.asarray(overlap, dtype=bool)
    if overlap.ndim != 2:
        raise ValueError("overlap must be a 2D ERP mask")
    if ramp_angle_rad <= 0:
        raise ValueError("ramp_angle_rad must be positive")
    height, width = overlap.shape
    tiled = np.concatenate([overlap, overlap, overlap], axis=1)
    angular_distance = distance_transform_edt(
        ~tiled,
        sampling=(math.pi / height, 2.0 * math.pi / width),
    )[:, width : 2 * width]
    return np.clip(1.0 - angular_distance / ramp_angle_rad, 0.0, 1.0).astype(np.float32)


def ownership_boundary_indices(bestcam: np.ndarray) -> dict[tuple[int, int], np.ndarray]:
    """Return flat ERP pixels touching each camera-ownership boundary.

    Horizontal adjacency is periodic because the first and last ERP columns are
    neighbours.  Vertical adjacency is not periodic.  Both sides of every edge
    are returned so later diagnostics can sample both cameras at the same 3D
    rays instead of comparing unrelated neighbouring scene pixels.
    """

    owners = np.asarray(bestcam)
    if owners.ndim != 2:
        raise ValueError("bestcam must be a 2D ERP ownership map")
    height, width = owners.shape
    buckets: dict[tuple[int, int], list[np.ndarray]] = {}

    def add_edges(
        camera_a: np.ndarray,
        camera_b: np.ndarray,
        index_a: np.ndarray,
        index_b: np.ndarray,
    ) -> None:
        valid = (camera_a >= 0) & (camera_b >= 0) & (camera_a != camera_b)
        if not valid.any():
            return
        a = camera_a[valid].astype(np.int64, copy=False)
        b = camera_b[valid].astype(np.int64, copy=False)
        lo = np.minimum(a, b)
        hi = np.maximum(a, b)
        pairs = np.unique(np.column_stack([lo, hi]), axis=0)
        ia = index_a[valid]
        ib = index_b[valid]
        for first, second in pairs:
            take = (lo == first) & (hi == second)
            buckets.setdefault((int(first), int(second)), []).extend(
                [ia[take], ib[take]]
            )

    flat = np.arange(height * width, dtype=np.int64).reshape(height, width)
    add_edges(owners[:, 1:], owners[:, :-1], flat[:, 1:], flat[:, :-1])
    add_edges(owners[:, :1], owners[:, -1:], flat[:, :1], flat[:, -1:])
    add_edges(owners[1:, :], owners[:-1, :], flat[1:, :], flat[:-1, :])
    return {
        pair: np.unique(np.concatenate(chunks))
        for pair, chunks in sorted(buckets.items())
    }


def photometric_pair_residual_stats(
    rgb_a: np.ndarray,
    rgb_b: np.ndarray,
    gain_a: np.ndarray | float,
    gain_b: np.ndarray | float,
    *,
    xy_a: np.ndarray | None = None,
    xy_b: np.ndarray | None = None,
    bins: int = 4,
) -> dict[str, object]:
    """Summarize same-3D-point photometric residuals for one camera pair.

    A global exposure gain can translate the log-luminance residual but cannot
    remove a residual that changes across camera coordinates.  Reporting both
    the corrected robust spread and its low-frequency spatial range therefore
    distinguishes a bad scalar estimate from a scalar model that is too weak.
    Chroma is measured with log(R/G, B/G), independent of scene brightness.
    """

    a = np.asarray(rgb_a, dtype=np.float64)
    b = np.asarray(rgb_b, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 3:
        raise ValueError("rgb_a and rgb_b must have matching shape (N, 3)")
    ga = np.broadcast_to(np.asarray(gain_a, dtype=np.float64), (3,))
    gb = np.broadcast_to(np.asarray(gain_b, dtype=np.float64), (3,))
    finite = np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    finite &= (a.mean(axis=1) > 0.0) & (b.mean(axis=1) > 0.0)
    if not finite.any():
        raise ValueError("no finite positive photometric samples")
    a = a[finite]
    b = b[finite]
    ac = a * np.exp(ga)[None, :]
    bc = b * np.exp(gb)[None, :]
    raw_luma_a = np.maximum(a.mean(axis=1), 1e-6)
    raw_luma_b = np.maximum(b.mean(axis=1), 1e-6)
    corrected_luma_a = np.maximum(ac.mean(axis=1), 1e-6)
    corrected_luma_b = np.maximum(bc.mean(axis=1), 1e-6)
    raw_residual = np.log(raw_luma_b) - np.log(raw_luma_a)
    corrected_residual = np.log(corrected_luma_b) - np.log(corrected_luma_a)
    corrected_median = float(np.median(corrected_residual))
    corrected_mad = float(np.median(np.abs(corrected_residual - corrected_median)))

    chroma_a = np.column_stack(
        [
            np.log(np.maximum(ac[:, 0], 1e-6) / np.maximum(ac[:, 1], 1e-6)),
            np.log(np.maximum(ac[:, 2], 1e-6) / np.maximum(ac[:, 1], 1e-6)),
        ]
    )
    chroma_b = np.column_stack(
        [
            np.log(np.maximum(bc[:, 0], 1e-6) / np.maximum(bc[:, 1], 1e-6)),
            np.log(np.maximum(bc[:, 2], 1e-6) / np.maximum(bc[:, 1], 1e-6)),
        ]
    )
    chroma_residual = np.linalg.norm(chroma_b - chroma_a, axis=1)
    chroma_components = chroma_b - chroma_a
    saturated = (
        (a <= 1.0).any(axis=1)
        | (a >= 254.0).any(axis=1)
        | (b <= 1.0).any(axis=1)
        | (b >= 254.0).any(axis=1)
    )

    def spatial_grid(xy: np.ndarray | None) -> dict[str, object] | None:
        if xy is None:
            return None
        coords = np.asarray(xy, dtype=np.float64)
        if coords.shape != (finite.size, 2):
            raise ValueError("xy arrays must have shape (N, 2)")
        coords = coords[finite]
        if bins < 2:
            raise ValueError("bins must be at least 2")
        cells = np.floor(np.clip(coords, 0.0, 1.0 - np.finfo(float).eps) * bins).astype(int)
        codes = cells[:, 1] * bins + cells[:, 0]
        min_count = max(8, int(math.ceil(len(codes) * 0.01)))
        rows: list[dict[str, object]] = []
        for code in range(bins * bins):
            selected = codes == code
            count = int(selected.sum())
            reliable = count >= min_count
            values = corrected_residual[selected]
            chroma = chroma_components[selected]
            rows.append(
                {
                    "x_index": code % bins,
                    "y_index": code // bins,
                    "n": count,
                    "min_reliable_n": min_count,
                    "reliable": reliable,
                    "saturated_n": int(saturated[selected].sum()),
                    "saturated_fraction": (
                        float(saturated[selected].mean()) if count else None
                    ),
                    "corrected_log_luma_median": (
                        float(np.median(values)) if reliable else None
                    ),
                    "corrected_log_luma_mad": (
                        float(np.median(np.abs(values - np.median(values))))
                        if reliable
                        else None
                    ),
                    "corrected_abs_log_luma_p90": (
                        float(np.quantile(np.abs(values), 0.90))
                        if reliable
                        else None
                    ),
                    "corrected_chroma_rg_median": (
                        float(np.median(chroma[:, 0])) if reliable else None
                    ),
                    "corrected_chroma_bg_median": (
                        float(np.median(chroma[:, 1])) if reliable else None
                    ),
                    "corrected_chroma_norm_p90": (
                        float(np.quantile(chroma_residual[selected], 0.90))
                        if reliable
                        else None
                    ),
                }
            )
        return {"bins": bins, "cells": rows}

    def grid_range(grid: dict[str, object] | None) -> float | None:
        if grid is None:
            return None
        medians = [
            float(cell["corrected_log_luma_median"])
            for cell in grid["cells"]
            if cell["corrected_log_luma_median"] is not None
        ]
        if len(medians) < 2:
            return None
        return float(max(medians) - min(medians))

    grid_a = spatial_grid(xy_a)
    grid_b = spatial_grid(xy_b)

    if raw_luma_a.std() < 1e-9 or raw_luma_b.std() < 1e-9:
        rho = None
    else:
        rho = float(np.corrcoef(np.log(raw_luma_a), np.log(raw_luma_b))[0, 1])
    return {
        "n": int(len(a)),
        "rho_log_luma": rho,
        "raw_log_luma_median": float(np.median(raw_residual)),
        "corrected_log_luma_median": corrected_median,
        "corrected_log_luma_mad": corrected_mad,
        "corrected_abs_log_luma_p90": float(
            np.quantile(np.abs(corrected_residual), 0.90)
        ),
        "corrected_chroma_logratio_median": float(np.median(chroma_residual)),
        "corrected_chroma_logratio_p90": float(np.quantile(chroma_residual, 0.90)),
        "camera_a_spatial_median_range": grid_range(grid_a),
        "camera_b_spatial_median_range": grid_range(grid_b),
        "camera_a_spatial_grid": grid_a,
        "camera_b_spatial_grid": grid_b,
    }
