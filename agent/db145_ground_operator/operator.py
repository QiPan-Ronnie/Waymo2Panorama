from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import torch


@dataclass
class EWAObservationSet:
    """Sparse, fixed-support elliptical weighted-average forward operator."""

    centers_cell: torch.Tensor
    covariance_cell: torch.Tensor
    inverse_covariance_cell: torch.Tensor
    source_ids: torch.Tensor
    rgb: torch.Tensor
    pair_observation_ids: torch.Tensor
    pair_texel_ids: torch.Tensor
    pair_texel_xy: torch.Tensor
    grid_hw: tuple[int, int]
    support_sigma: float
    pose_shift_limit_cell: float
    pair_chunk_size: int
    provenance: dict[str, Any]

    @property
    def n_observations(self) -> int:
        return int(self.centers_cell.shape[0])

    @property
    def n_sources(self) -> int:
        return int(self.source_ids.max().item() + 1) if self.n_observations else 0

    @classmethod
    def from_numpy(
        cls,
        *,
        centers_cell: np.ndarray,
        covariance_cell: np.ndarray,
        source_ids: np.ndarray,
        rgb: np.ndarray,
        grid_hw: tuple[int, int],
        support_sigma: float,
        pose_shift_limit_cell: float,
        provenance: dict[str, Any] | None = None,
        pair_chunk_size: int = 2_000_000,
        device: str | torch.device = "cpu",
    ) -> "EWAObservationSet":
        centers = np.asarray(centers_cell, dtype=np.float32).reshape(-1, 2)
        covariances = np.asarray(covariance_cell, dtype=np.float32).reshape(-1, 2, 2)
        sources = np.asarray(source_ids, dtype=np.int64).reshape(-1)
        colours = np.asarray(rgb, dtype=np.float32).reshape(-1, 3)
        n = len(centers)
        if not (len(covariances) == len(sources) == len(colours) == n):
            raise ValueError("observation arrays have different lengths")
        if n == 0:
            raise ValueError("no observations")
        if sources.min() < 0:
            raise ValueError("source IDs must be non-negative")
        if not np.isfinite(centers).all() or not np.isfinite(colours).all():
            raise ValueError("non-finite observation")

        height, width = grid_hw
        pair_obs: list[np.ndarray] = []
        pair_ids: list[np.ndarray] = []
        pair_xy: list[np.ndarray] = []
        for obs_id, (center, covariance) in enumerate(zip(centers, covariances, strict=True)):
            eigenvalues = np.linalg.eigvalsh(covariance.astype(np.float64))
            if not np.isfinite(eigenvalues).all() or eigenvalues[0] <= 0.0:
                raise ValueError(f"observation {obs_id} has invalid covariance")
            radius = support_sigma * float(np.sqrt(eigenvalues[-1])) + pose_shift_limit_cell
            x0 = max(0, int(np.floor(center[0] - radius)))
            x1 = min(width - 1, int(np.ceil(center[0] + radius)))
            y0 = max(0, int(np.floor(center[1] - radius)))
            y1 = min(height - 1, int(np.ceil(center[1] + radius)))
            if x0 > x1 or y0 > y1:
                raise ValueError(f"observation {obs_id} has empty grid support")
            yy, xx = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
            xy = np.column_stack((xx.ravel(), yy.ravel())).astype(np.float32)
            # Prune by the nominal Mahalanobis ellipse, but retain the maximum
            # permitted shift in Euclidean space so support stays fixed.
            inv = np.linalg.inv(covariance.astype(np.float64))
            delta = xy.astype(np.float64) - center
            conservative = np.maximum(
                np.einsum("ni,ij,nj->n", delta, inv, delta)
                - (pose_shift_limit_cell**2 / eigenvalues[0]),
                0.0,
            )
            keep = conservative <= support_sigma**2
            xy = xy[keep]
            if not len(xy):
                raise ValueError(f"observation {obs_id} has empty EWA support")
            pair_obs.append(np.full(len(xy), obs_id, dtype=np.int64))
            pair_ids.append((xy[:, 1].astype(np.int64) * width + xy[:, 0].astype(np.int64)))
            pair_xy.append(xy)

        target = torch.device(device)
        cov_tensor = torch.as_tensor(covariances, dtype=torch.float32, device=target)
        return cls(
            centers_cell=torch.as_tensor(centers, dtype=torch.float32, device=target),
            covariance_cell=cov_tensor,
            inverse_covariance_cell=torch.linalg.inv(cov_tensor),
            source_ids=torch.as_tensor(sources, dtype=torch.long, device=target),
            rgb=torch.as_tensor(colours, dtype=torch.float32, device=target),
            pair_observation_ids=torch.as_tensor(
                np.concatenate(pair_obs), dtype=torch.long, device=target
            ),
            pair_texel_ids=torch.as_tensor(
                np.concatenate(pair_ids), dtype=torch.long, device=target
            ),
            pair_texel_xy=torch.as_tensor(
                np.concatenate(pair_xy), dtype=torch.float32, device=target
            ),
            grid_hw=(height, width),
            support_sigma=float(support_sigma),
            pose_shift_limit_cell=float(pose_shift_limit_cell),
            pair_chunk_size=int(pair_chunk_size),
            provenance={} if provenance is None else provenance,
        )

    def to(self, device: str | torch.device) -> "EWAObservationSet":
        target = torch.device(device)
        for name in (
            "centers_cell",
            "covariance_cell",
            "inverse_covariance_cell",
            "source_ids",
            "rgb",
            "pair_observation_ids",
            "pair_texel_ids",
            "pair_texel_xy",
        ):
            setattr(self, name, getattr(self, name).to(target))
        return self

    def subset(self, keep: np.ndarray | torch.Tensor) -> "EWAObservationSet":
        """Rebuild an observation subset without leaking removed pixels."""

        keep_np = np.asarray(
            keep.detach().cpu().numpy() if isinstance(keep, torch.Tensor) else keep,
            dtype=bool,
        ).reshape(-1)
        if len(keep_np) != self.n_observations:
            raise ValueError("subset mask length does not match observations")
        if not keep_np.any():
            raise ValueError("subset would contain no observations")
        provenance: dict[str, Any] = {}
        for key, value in self.provenance.items():
            array = np.asarray(value)
            provenance[key] = array[keep_np] if len(array) == len(keep_np) else value
        device = self.centers_cell.device
        return EWAObservationSet.from_numpy(
            centers_cell=self.centers_cell.detach().cpu().numpy()[keep_np],
            covariance_cell=self.covariance_cell.detach().cpu().numpy()[keep_np],
            source_ids=self.source_ids.detach().cpu().numpy()[keep_np],
            rgb=self.rgb.detach().cpu().numpy()[keep_np],
            grid_hw=self.grid_hw,
            support_sigma=self.support_sigma,
            pose_shift_limit_cell=self.pose_shift_limit_cell,
            provenance=provenance,
            pair_chunk_size=self.pair_chunk_size,
            device=device,
        )

    def bounded_shift(self, raw_source_shift: torch.Tensor) -> torch.Tensor:
        return self.pose_shift_limit_cell * torch.tanh(raw_source_shift)

    def predict(
        self,
        texture: torch.Tensor,
        source_shift: torch.Tensor,
        *,
        return_weight_sum: bool = False,
    ) -> torch.Tensor | tuple[torch.Tensor, torch.Tensor]:
        """Predict every raw pixel from the latent texture.

        ``source_shift`` is an already bounded shift in (x, y) cell units.
        Solvers should use :meth:`bounded_shift` for unconstrained parameters.
        """

        height, width = self.grid_hw
        if tuple(texture.shape) != (height, width, 3):
            raise ValueError(f"texture must have shape {(height, width, 3)}")
        if source_shift.shape != (self.n_sources, 2):
            raise ValueError(f"source_shift must have shape {(self.n_sources, 2)}")
        flat = texture.reshape(-1, 3)
        numerator = torch.zeros(
            (self.n_observations, 3), dtype=texture.dtype, device=texture.device
        )
        denominator = torch.zeros(
            self.n_observations, dtype=texture.dtype, device=texture.device
        )

        n_pairs = len(self.pair_observation_ids)
        for start in range(0, n_pairs, self.pair_chunk_size):
            stop = min(start + self.pair_chunk_size, n_pairs)
            obs_id = self.pair_observation_ids[start:stop]
            texel_id = self.pair_texel_ids[start:stop]
            source_id = self.source_ids[obs_id]
            shifted_center = self.centers_cell[obs_id] + source_shift[source_id]
            delta = self.pair_texel_xy[start:stop] - shifted_center
            inv = self.inverse_covariance_cell[obs_id]
            mahalanobis = torch.einsum("ni,nij,nj->n", delta, inv, delta)
            weights = torch.exp(-0.5 * mahalanobis).to(texture.dtype)
            numerator = numerator.index_add(0, obs_id, weights[:, None] * flat[texel_id])
            denominator = denominator.index_add(0, obs_id, weights)

        if bool((denominator <= 0).any()):
            raise RuntimeError("fixed EWA support produced an empty observation")
        prediction = numerator / denominator[:, None]
        return (prediction, denominator) if return_weight_sum else prediction
