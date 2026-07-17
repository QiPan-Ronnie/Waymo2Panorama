from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np


SCENE_CANDIDATES: dict[str, dict[str, object]] = {
    "02a00399-3857-444e-8db3-a8f58489c394": {
        "split": "val",
        "wet": False,
        "window": [0, 92],
    },
    "02678d04-cc9f-3148-9f95-1ba66347dff9": {
        "split": "val",
        "wet": False,
        "window": [75, 167],
    },
    "2c652f9e-8db8-3572-aa49-fae1344a875b": {
        "split": "val",
        "wet": False,
        "window": [219, 311],
    },
    "8749f79f-a30b-3c3f-8a44-dbfa682bbef1": {
        "split": "val",
        "wet": False,
        "window": [209, 301],
    },
    "05fa5048-f355-3274-b565-c0ddc547b315": {
        "split": "val",
        "wet": True,
        "window": [32, 124],
        "evidence": "DB-128 wet-road Lambertian failure",
    },
}


@dataclass(frozen=True)
class SceneMotion:
    log_id: str
    dmax_m: float
    path_length_m: float
    endpoint_ratio: float
    yaw_change_rad: float
    lateral_deviation_m: float


@dataclass(frozen=True)
class PatchObservability:
    patch_id: str
    center_xy: tuple[float, float]
    coverage_fraction: float
    source_view_count: int
    angular_diversity: float
    subpixel_phase_entropy: float
    camera_diversity: float
    median_footprint_aspect: float
    plane_rmse_m: float
    min_evidence: bool

    @property
    def score(self) -> float:
        return float(
            0.35 * np.clip(self.coverage_fraction, 0.0, 1.0)
            + 0.20 * np.clip(np.log1p(self.source_view_count) / np.log(65.0), 0.0, 1.0)
            + 0.20 * np.clip(self.angular_diversity, 0.0, 1.0)
            + 0.15 * np.clip(self.subpixel_phase_entropy, 0.0, 1.0)
            + 0.10 * np.clip(self.camera_diversity, 0.0, 1.0)
            - 0.15
            * np.clip(np.log1p(self.median_footprint_aspect) / np.log(41.0), 0.0, 1.0)
        )


@dataclass(frozen=True)
class HeldoutSplit:
    training_groups: tuple[str, ...]
    heldout_groups: tuple[str, ...]
    strategy: str
    heldout_fraction: float


def scene_motion_from_poses(log_id: str, xy: np.ndarray, yaw_rad: np.ndarray) -> SceneMotion:
    points = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    yaw = np.unwrap(np.asarray(yaw_rad, dtype=np.float64).reshape(-1))
    if len(points) < 2 or len(yaw) != len(points):
        raise ValueError("scene motion needs aligned pose samples")
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    path = float(steps.sum())
    endpoint = float(np.linalg.norm(points[-1] - points[0]))
    centre = points[len(points) // 2]
    dmax = float(np.linalg.norm(points - centre, axis=1).max())
    chord = points[-1] - points[0]
    chord_norm = float(np.linalg.norm(chord))
    if chord_norm > 1.0e-8:
        relative = points - points[0]
        lateral = float(np.abs(relative[:, 0] * chord[1] - relative[:, 1] * chord[0]).max())
        lateral /= chord_norm
    else:
        lateral = float(np.linalg.norm(points - points[0], axis=1).max())
    return SceneMotion(
        log_id=log_id,
        dmax_m=dmax,
        path_length_m=path,
        endpoint_ratio=endpoint / max(path, 1.0e-8),
        yaw_change_rad=float(np.abs(np.diff(yaw)).sum()),
        lateral_deviation_m=lateral,
    )


def _rank01(values: np.ndarray) -> np.ndarray:
    if len(values) <= 1:
        return np.zeros(len(values), np.float64)
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), np.float64)
    ranks[order] = np.arange(len(values), dtype=np.float64) / (len(values) - 1)
    return ranks


def select_scene_roles(motions: Iterable[SceneMotion]) -> dict[str, str]:
    valid = sorted((m for m in motions if m.dmax_m >= 8.0), key=lambda m: m.log_id)
    wet = [log_id for log_id, data in SCENE_CANDIDATES.items() if bool(data["wet"])]
    dry = [m for m in valid if m.log_id not in wet]
    if len(dry) < 2:
        raise ValueError("fewer than two moving dry candidates")
    endpoint_curvature = np.array([1.0 - m.endpoint_ratio for m in dry])
    yaw = np.array([m.yaw_change_rad for m in dry])
    lateral = np.array([m.lateral_deviation_m for m in dry])
    curvature = 0.35 * _rank01(endpoint_curvature) + 0.35 * _rank01(yaw) + 0.30 * _rank01(
        lateral
    )
    straight_idx = int(np.argmin(curvature))
    # Remove the already selected scene before choosing the opposite extreme.
    turn_candidates = [i for i in range(len(dry)) if i != straight_idx]
    turn_idx = max(turn_candidates, key=lambda i: (curvature[i], dry[i].log_id))
    return {
        "dry_straight": dry[straight_idx].log_id,
        "dry_turn": dry[turn_idx].log_id,
        "wet_or_specular": wet[0],
    }


def select_patch_pair(
    candidates: Iterable[PatchObservability], *, minimum_separation_m: float = 4.0
) -> tuple[PatchObservability, PatchObservability]:
    eligible = sorted(
        (
            p
            for p in candidates
            if p.min_evidence and p.plane_rmse_m <= 0.05 and p.coverage_fraction >= 0.20
        ),
        key=lambda p: p.patch_id,
    )
    if len(eligible) < 2:
        raise ValueError("fewer than two eligible patch candidates")
    high = max(eligible, key=lambda p: (p.score, p.patch_id))
    separated = [
        p
        for p in eligible
        if p.patch_id != high.patch_id
        and np.linalg.norm(np.asarray(p.center_xy) - np.asarray(high.center_xy))
        >= minimum_separation_m
    ]
    if not separated:
        raise ValueError("no low-observability patch satisfies separation")
    low = min(separated, key=lambda p: (p.score, p.patch_id))
    return high, low


def select_heldout_groups(
    group_counts: Mapping[str, int],
    *,
    group_camera: Mapping[str, str],
    group_time: Mapping[str, int],
    target_fraction: float = 0.20,
) -> HeldoutSplit:
    """Freeze a whole-camera split when feasible, otherwise a contiguous time block."""

    groups = sorted(group_counts)
    total = sum(max(0, int(group_counts[g])) for g in groups)
    if total <= 0:
        raise ValueError("held-out selection received no observations")
    cameras = sorted(set(group_camera[g] for g in groups))
    options: list[tuple[float, str, list[str], float]] = []
    for camera in cameras:
        selected = [g for g in groups if group_camera[g] == camera]
        fraction = sum(group_counts[g] for g in selected) / total
        if 0.10 <= fraction <= 0.35:
            options.append((abs(fraction - target_fraction), camera, selected, fraction))
    if options:
        _, _, heldout, fraction = min(options)
        strategy = "complete_camera"
    else:
        ordered_times = sorted(set(group_time[g] for g in groups))
        time_options: list[tuple[float, float, int, int, list[str], float]] = []
        centre_index = (len(ordered_times) - 1) / 2.0
        for start in range(len(ordered_times)):
            for stop in range(start + 1, len(ordered_times) + 1):
                time_block = set(ordered_times[start:stop])
                selected = [g for g in groups if group_time[g] in time_block]
                selected_fraction = sum(group_counts[g] for g in selected) / total
                if 0.10 <= selected_fraction <= 0.35:
                    time_options.append(
                        (
                            abs(selected_fraction - target_fraction),
                            abs((start + stop - 1) / 2.0 - centre_index),
                            stop - start,
                            start,
                            selected,
                            selected_fraction,
                        )
                    )
        if not time_options:
            raise ValueError("no contiguous held-out time block has 10-35% evidence")
        _, _, _, _, heldout, fraction = min(time_options)
        strategy = "central_time_block"
    training = [g for g in groups if g not in set(heldout)]
    if not training or not heldout:
        raise ValueError("held-out split is empty on one side")
    return HeldoutSplit(
        tuple(training),
        tuple(heldout),
        strategy,
        float(fraction),
    )
