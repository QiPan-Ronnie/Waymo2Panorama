from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import binary_dilation, label


def enforce_dominant_single_source_objects(
    bestcam: np.ndarray,
    valid_by_camera: np.ndarray,
    object_by_camera: np.ndarray,
    *,
    dominance_ratio: float = 2.5,
    dilation_px: int = 4,
    min_object_px: int = 32,
    min_owner_valid_fraction: float = 0.9,
    max_component_fraction: float = 0.08,
) -> tuple[np.ndarray, dict[str, object]]:
    bestcam = np.asarray(bestcam)
    valid = np.asarray(valid_by_camera, dtype=bool)
    objects = np.asarray(object_by_camera, dtype=bool)
    if bestcam.ndim != 2:
        raise ValueError("bestcam must be HxW")
    if valid.ndim != 3 or valid.shape[1:] != bestcam.shape:
        raise ValueError("valid_by_camera must be CxHxW")
    if objects.shape != valid.shape:
        raise ValueError("object_by_camera must match valid_by_camera")
    if not math.isfinite(dominance_ratio) or dominance_ratio <= 1.0:
        raise ValueError("dominance_ratio must be finite and greater than 1")
    if isinstance(dilation_px, bool) or not isinstance(dilation_px, int) or dilation_px < 0:
        raise ValueError("dilation_px must be a nonnegative integer")
    if isinstance(min_object_px, bool) or not isinstance(min_object_px, int) or min_object_px < 1:
        raise ValueError("min_object_px must be a positive integer")
    if not 0.0 < min_owner_valid_fraction <= 1.0:
        raise ValueError("min_owner_valid_fraction must be in (0, 1]")
    if not 0.0 < max_component_fraction <= 1.0:
        raise ValueError("max_component_fraction must be in (0, 1]")

    boundary = np.zeros(bestcam.shape, bool)
    horizontal = (
        (bestcam[:, 1:] >= 0)
        & (bestcam[:, :-1] >= 0)
        & (bestcam[:, 1:] != bestcam[:, :-1])
    )
    vertical = (
        (bestcam[1:, :] >= 0)
        & (bestcam[:-1, :] >= 0)
        & (bestcam[1:, :] != bestcam[:-1, :])
    )
    boundary[:, 1:] |= horizontal
    boundary[:, :-1] |= horizontal
    boundary[1:, :] |= vertical
    boundary[:-1, :] |= vertical

    union = objects.any(axis=0)
    if dilation_px:
        union = binary_dilation(union, iterations=dilation_px)
    components, count = label(union)
    output = np.array(bestcam, copy=True)
    reports: list[dict[str, object]] = []
    changed_total = 0
    max_component_px = int(math.floor(bestcam.size * max_component_fraction))
    for component_id in range(1, count + 1):
        component = components == component_id
        component_px = int(component.sum())
        if component_px < min_object_px or component_px > max_component_px:
            continue
        if not np.any(component & boundary):
            continue
        counts = objects[:, component].sum(axis=1).astype(np.int64)
        owner = int(np.argmax(counts))
        owner_count = int(counts[owner])
        if owner_count < min_object_px:
            continue
        other = np.delete(counts, owner)
        runner_up = int(other.max()) if other.size else 0
        if owner_count < dominance_ratio * max(runner_up, 1):
            continue
        owner_valid_fraction = float(valid[owner, component].mean())
        if owner_valid_fraction < min_owner_valid_fraction:
            continue
        write = component & valid[owner]
        changed = int(np.count_nonzero(output[write] != owner))
        if changed == 0:
            continue
        output[write] = owner
        changed_total += changed
        reports.append(
            {
                "component_id": component_id,
                "component_px": component_px,
                "camera_counts": counts.tolist(),
                "owner": owner,
                "owner_valid_fraction": round(owner_valid_fraction, 6),
                "changed_px": changed,
            }
        )
    return output, {
        "components_reassigned": len(reports),
        "changed_px": changed_total,
        "components": reports,
    }


__all__ = ["enforce_dominant_single_source_objects"]
