from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import label


def _validate_inputs(
    bestcam: np.ndarray,
    valid_by_camera: np.ndarray,
    rgb_by_camera: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ownership = np.asarray(bestcam)
    valid = np.asarray(valid_by_camera, dtype=bool)
    rgb = np.asarray(rgb_by_camera)
    if ownership.ndim != 2:
        raise ValueError("bestcam must be HxW")
    if valid.ndim != 3 or valid.shape[1:] != ownership.shape:
        raise ValueError("valid_by_camera must be CxHxW")
    if rgb.shape != valid.shape + (3,):
        raise ValueError("rgb_by_camera must be CxHxWx3")
    if np.any((ownership >= valid.shape[0]) | (ownership < -1)):
        raise ValueError("bestcam contains an out-of-range camera index")
    return ownership, valid, rgb


def _transition(
    previous: np.ndarray,
    max_step: int,
    smoothness: float,
) -> tuple[np.ndarray, np.ndarray]:
    count = len(previous)
    best = np.full(count, np.inf, np.float32)
    parent = np.full(count, -1, np.int16)
    for delta in range(-max_step, max_step + 1):
        if delta >= 0:
            old = np.arange(0, count - delta)
            new = old + delta
        else:
            old = np.arange(-delta, count)
            new = old + delta
        candidate = previous[old] + smoothness * abs(delta)
        improve = candidate < best[new]
        if np.any(improve):
            chosen_new = new[improve]
            best[chosen_new] = candidate[improve]
            parent[chosen_new] = old[improve].astype(np.int16)
    return best, parent


def optimize_photometric_ownership_seams(
    bestcam: np.ndarray,
    valid_by_camera: np.ndarray,
    rgb_by_camera: np.ndarray,
    *,
    max_shift_px: int = 192,
    max_step_px: int = 8,
    min_boundary_rows: int = 12,
    min_relative_improvement: float = 0.20,
    deviation_cost: float = 0.05,
    smoothness_cost: float = 0.25,
) -> tuple[np.ndarray, dict[str, object]]:
    ownership, valid, rgb = _validate_inputs(
        bestcam, valid_by_camera, rgb_by_camera
    )
    for name, value, minimum in (
        ("max_shift_px", max_shift_px, 1),
        ("max_step_px", max_step_px, 1),
        ("min_boundary_rows", min_boundary_rows, 2),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    for name, value in (
        ("min_relative_improvement", min_relative_improvement),
        ("deviation_cost", deviation_cost),
        ("smoothness_cost", smoothness_cost),
    ):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")

    height, width = ownership.shape
    left = ownership[:, :-1]
    right = ownership[:, 1:]
    direct = (left >= 0) & (right >= 0) & (left != right)
    if not np.any(direct):
        return np.array(ownership, copy=True), {
            "seams_optimized": 0,
            "changed_px": 0,
            "seams": [],
        }

    pair_values = np.stack(
        [np.minimum(left[direct], right[direct]), np.maximum(left[direct], right[direct])],
        axis=1,
    )
    pairs = np.unique(pair_values, axis=0)
    output = np.array(ownership, copy=True)
    reports: list[dict[str, object]] = []
    changed_total = 0
    structure = np.ones((3, 3), np.uint8)
    rgb_float = rgb.astype(np.float32)

    for camera_a, camera_b in pairs.tolist():
        pair_mask = direct & (
            ((left == camera_a) & (right == camera_b))
            | ((left == camera_b) & (right == camera_a))
        )
        components, component_count = label(pair_mask, structure=structure)
        difference = np.abs(
            rgb_float[camera_a] - rgb_float[camera_b]
        ).mean(axis=2)
        invalid_prefix = np.pad(
            np.cumsum(~valid[[camera_a, camera_b]], axis=2, dtype=np.int32),
            ((0, 0), (0, 0), (1, 0)),
        )

        for component_id in range(1, component_count + 1):
            component = components == component_id
            rows = np.flatnonzero(component.any(axis=1))
            if len(rows) < min_boundary_rows:
                continue
            # Skip the ERP wrap boundary. It needs circular DP and is deliberately
            # handled separately rather than pretending a flat image is continuous.
            component_cols = np.flatnonzero(component.any(axis=0))
            if component_cols[0] == 0 or component_cols[-1] == width - 2:
                continue
            original = np.asarray(
                [int(np.median(np.flatnonzero(component[row]) + 1)) for row in rows],
                np.int32,
            )
            orientations = np.asarray(
                [(ownership[row, x - 1], ownership[row, x]) for row, x in zip(rows, original)],
                np.int16,
            )
            variants, counts = np.unique(orientations, axis=0, return_counts=True)
            left_camera, right_camera = variants[int(np.argmax(counts))].tolist()
            consistent = np.all(orientations == (left_camera, right_camera), axis=1)
            if float(consistent.mean()) < 0.9:
                continue

            x_min = max(1, int(original.min()) - max_shift_px)
            x_max = min(width - 1, int(original.max()) + max_shift_px)
            candidates = np.arange(x_min, x_max + 1, dtype=np.int32)
            state_count = len(candidates)
            data = np.full((len(rows), state_count), np.inf, np.float32)
            left_slot = 0 if left_camera == camera_a else 1
            right_slot = 0 if right_camera == camera_a else 1
            for row_index, (row, origin) in enumerate(zip(rows, original)):
                both = valid[camera_a, row, candidates] & valid[camera_b, row, candidates]
                feasible = np.array(both, copy=True)
                for state, candidate_x in enumerate(candidates):
                    if not feasible[state]:
                        continue
                    if candidate_x < origin:
                        bad = (
                            invalid_prefix[right_slot, row, origin]
                            - invalid_prefix[right_slot, row, candidate_x]
                        )
                    elif candidate_x > origin:
                        bad = (
                            invalid_prefix[left_slot, row, candidate_x]
                            - invalid_prefix[left_slot, row, origin]
                        )
                    else:
                        bad = 0
                    if bad:
                        feasible[state] = False
                row_cost = difference[row, candidates]
                row_cost = row_cost + deviation_cost * np.abs(candidates - origin)
                data[row_index, feasible] = row_cost[feasible]

            score = data[0]
            parents = np.full((len(rows), state_count), -1, np.int16)
            for row_index in range(1, len(rows)):
                row_gap = max(1, int(rows[row_index] - rows[row_index - 1]))
                transition, parent = _transition(
                    score,
                    max_step=max_step_px * row_gap,
                    smoothness=smoothness_cost / row_gap,
                )
                score = data[row_index] + transition
                parents[row_index] = parent
            if not np.isfinite(score).any():
                continue
            state = int(np.argmin(score))
            path = np.empty(len(rows), np.int32)
            path[-1] = candidates[state]
            valid_path = True
            for row_index in range(len(rows) - 1, 0, -1):
                state = int(parents[row_index, state])
                if state < 0:
                    valid_path = False
                    break
                path[row_index - 1] = candidates[state]
            if not valid_path:
                continue

            old_cost = difference[rows, np.clip(original, 0, width - 1)]
            new_cost = difference[rows, np.clip(path, 0, width - 1)]
            old_mean = float(old_cost.mean())
            new_mean = float(new_cost.mean())
            if old_mean <= 1e-6:
                continue
            relative_improvement = (old_mean - new_mean) / old_mean
            if relative_improvement < min_relative_improvement:
                continue

            changed = 0
            for row, origin, target in zip(rows, original, path):
                if target < origin:
                    before = output[row, target:origin]
                    changed += int(np.count_nonzero(before != right_camera))
                    output[row, target:origin] = right_camera
                elif target > origin:
                    before = output[row, origin:target]
                    changed += int(np.count_nonzero(before != left_camera))
                    output[row, origin:target] = left_camera
            if changed == 0:
                continue
            changed_total += changed
            reports.append(
                {
                    "camera_pair": [int(camera_a), int(camera_b)],
                    "rows": [int(rows[0]), int(rows[-1])],
                    "original_x_median": int(np.median(original)),
                    "new_x_min": int(path.min()),
                    "new_x_max": int(path.max()),
                    "max_abs_shift": int(np.max(np.abs(path - original))),
                    "old_mean_cost": round(old_mean, 6),
                    "new_mean_cost": round(new_mean, 6),
                    "relative_improvement": round(relative_improvement, 6),
                    "changed_px": changed,
                }
            )

    return output, {
        "seams_optimized": len(reports),
        "changed_px": changed_total,
        "seams": reports,
    }


__all__ = ["optimize_photometric_ownership_seams"]
