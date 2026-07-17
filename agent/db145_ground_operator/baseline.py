from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BaselineResult:
    texture_rgb: np.ndarray
    valid: np.ndarray
    source_count: np.ndarray


def six_slot_median(
    texel_ids: np.ndarray,
    source_ids: np.ndarray,
    ground_ranges: np.ndarray,
    rgb: np.ndarray,
    *,
    grid_hw: tuple[int, int],
    slots: int = 6,
) -> BaselineResult:
    """Cell-centric v10/v15 baseline: nearest distinct sources then RGB median."""

    cells = np.asarray(texel_ids, dtype=np.int64).reshape(-1)
    sources = np.asarray(source_ids, dtype=np.int64).reshape(-1)
    ranges = np.asarray(ground_ranges, dtype=np.float64).reshape(-1)
    colours = np.asarray(rgb, dtype=np.float32).reshape(-1, 3)
    n = len(cells)
    if not (len(sources) == len(ranges) == len(colours) == n):
        raise ValueError("sample arrays have different lengths")
    if slots <= 0:
        raise ValueError("slots must be positive")

    height, width = grid_hw
    n_cells = height * width
    if n and (cells.min() < 0 or cells.max() >= n_cells):
        raise ValueError("texel id out of range")

    out = np.zeros((n_cells, 3), dtype=np.float32)
    counts = np.zeros(n_cells, dtype=np.uint16)
    if n:
        finite = np.isfinite(ranges) & np.isfinite(colours).all(axis=1)
        cells, sources, ranges, colours = (
            values[finite] for values in (cells, sources, ranges, colours)
        )
        # Deterministic, input-order-independent ordering.  Source ID before
        # range makes duplicate-source elimination stable; the survivors are
        # subsequently ranked by range.
        order = np.lexsort(
            (
                colours[:, 2],
                colours[:, 1],
                colours[:, 0],
                ranges,
                sources,
                cells,
            )
        )
        cells, sources, ranges, colours = (
            values[order] for values in (cells, sources, ranges, colours)
        )
        start = 0
        while start < len(cells):
            stop = int(np.searchsorted(cells, cells[start], side="right"))
            cell_sources = sources[start:stop]
            _, first = np.unique(cell_sources, return_index=True)
            candidate = start + first
            keep = candidate[np.argsort(ranges[candidate], kind="stable")[:slots]]
            cell = int(cells[start])
            out[cell] = np.median(colours[keep], axis=0)
            counts[cell] = len(keep)
            start = stop

    valid = counts > 0
    return BaselineResult(
        texture_rgb=out.reshape(height, width, 3),
        valid=valid.reshape(height, width),
        source_count=counts.reshape(height, width),
    )
