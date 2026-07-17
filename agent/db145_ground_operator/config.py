from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class ExperimentConfig:
    """Numerical contract shared by every DB-145 scene and patch."""

    patch_size_m: float = 2.0
    cell_m: float = 0.025
    grid_hw: int = 80
    pixel_support_sigma: float = 3.0
    min_source_range_m: float = 2.5
    max_source_range_m: float = 30.0
    max_footprint_aspect: float = 40.0
    max_footprint_area_m2: float = 0.20
    pose_shift_limit_cell: float = 0.5
    log_gain_limit: float = 0.10
    huber_delta: float = 0.04
    tv_weight: float = 5.0e-4
    coarse_tie_weight: float = 2.0e-3
    solver_steps: int = 300
    learning_rate: float = 2.0e-2
    heldout_time_fraction: float = 0.20
    random_seed: int = 145
    pair_chunk_size: int = 2_000_000

    def __post_init__(self) -> None:
        if self.grid_hw * self.cell_m != self.patch_size_m:
            raise ValueError("grid_hw * cell_m must equal patch_size_m")
        if not 0.0 < self.heldout_time_fraction < 1.0:
            raise ValueError("heldout_time_fraction must lie in (0, 1)")
        if self.min_source_range_m >= self.max_source_range_m:
            raise ValueError("source range bounds are reversed")

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


DEFAULT_CONFIG = ExperimentConfig()
