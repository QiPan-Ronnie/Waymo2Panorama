from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np
import torch
import torch.nn.functional as F

from .baseline import BaselineResult
from .config import DEFAULT_CONFIG, ExperimentConfig
from .operator import EWAObservationSet


@dataclass(frozen=True)
class SolverResult:
    texture_rgb: np.ndarray
    evidence_valid: np.ndarray
    source_shift_cell: np.ndarray
    source_gain: np.ndarray
    loss_curve: tuple[float, ...]
    elapsed_s: float
    max_cuda_memory_mb: float
    rejected_sources: tuple[int, ...] = ()
    rejection_reasons: tuple[str, ...] = ()


def _evidence_mask(observations: EWAObservationSet) -> torch.Tensor:
    mask = torch.zeros(
        observations.grid_hw[0] * observations.grid_hw[1],
        dtype=torch.bool,
        device=observations.centers_cell.device,
    )
    mask[torch.unique(observations.pair_texel_ids)] = True
    return mask.reshape(observations.grid_hw)


def _initial_texture(
    baseline: BaselineResult, evidence: torch.Tensor, device: torch.device
) -> torch.Tensor:
    texture = torch.as_tensor(baseline.texture_rgb, dtype=torch.float32, device=device).clone()
    valid = torch.as_tensor(baseline.valid, dtype=torch.bool, device=device) & evidence
    if bool(valid.any()):
        fill = texture[valid].median(dim=0).values
    else:
        fill = torch.full((3,), 0.5, device=device)
    texture[~valid] = fill
    return texture


def _edge_aware_tv(texture: torch.Tensor, guide: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    dx = texture[:, 1:] - texture[:, :-1]
    dy = texture[1:, :] - texture[:-1, :]
    gx = guide[:, 1:] - guide[:, :-1]
    gy = guide[1:, :] - guide[:-1, :]
    valid_x = valid[:, 1:] & valid[:, :-1]
    valid_y = valid[1:, :] & valid[:-1, :]
    weight_x = torch.exp(-10.0 * gx.abs().mean(dim=-1))
    weight_y = torch.exp(-10.0 * gy.abs().mean(dim=-1))
    terms: list[torch.Tensor] = []
    if bool(valid_x.any()):
        terms.append((weight_x[valid_x, None] * dx[valid_x].abs()).mean())
    if bool(valid_y.any()):
        terms.append((weight_y[valid_y, None] * dy[valid_y].abs()).mean())
    return torch.stack(terms).mean() if terms else texture.sum() * 0.0


def _coarse_tie(
    texture: torch.Tensor,
    baseline_texture: torch.Tensor,
    baseline_valid: torch.Tensor,
    factor: int = 4,
) -> torch.Tensor:
    tex = texture.permute(2, 0, 1)[None]
    base = baseline_texture.permute(2, 0, 1)[None]
    valid = baseline_valid.float()[None, None]
    numerator = F.avg_pool2d(base * valid, factor, factor)
    denominator = F.avg_pool2d(valid, factor, factor)
    target = numerator / denominator.clamp_min(1.0e-6)
    pred = F.avg_pool2d(tex, factor, factor)
    keep = denominator > 0.25
    return (pred - target).abs()[keep.expand_as(pred)].mean() if bool(keep.any()) else tex.sum() * 0.0


def _bounded_gauge(raw: torch.Tensor, anchor: int, limit: float) -> torch.Tensor:
    """Bound physical nuisances, pin the anchor, and remove the remaining gauge."""

    if raw.shape[0] <= 1:
        return torch.zeros_like(raw)
    mask = torch.ones(raw.shape[0], dtype=torch.bool, device=raw.device)
    mask[anchor] = False
    bounded = limit * torch.tanh(raw)
    non_anchor = bounded[mask] - bounded[mask].mean(dim=0, keepdim=True)
    # Re-centring two extreme values can exceed the original box.  A common
    # scale preserves zero mean and direction while enforcing the hard limit.
    maximum = non_anchor.abs().amax(dim=0, keepdim=True).clamp_min(limit)
    non_anchor = non_anchor * (limit / maximum)
    gauged = torch.zeros_like(bounded)
    gauged[mask] = non_anchor
    return gauged


def solve_sensor_native(
    observations: EWAObservationSet,
    baseline: BaselineResult,
    *,
    config: ExperimentConfig = DEFAULT_CONFIG,
    steps: int | None = None,
    anchor_source: int | None = None,
    warm_start: SolverResult | None = None,
    progress_every: int = 0,
) -> SolverResult:
    """Solve DB-145 B on raw pixels with only bounded source nuisances."""

    device = observations.centers_cell.device
    n_sources = observations.n_sources
    if baseline.texture_rgb.shape[:2] != observations.grid_hw:
        raise ValueError("baseline and operator grids differ")
    if n_sources == 0:
        raise ValueError("no sources")
    anchor = (
        int(torch.bincount(observations.source_ids).argmax().item())
        if anchor_source is None
        else int(anchor_source)
    )
    if not 0 <= anchor < n_sources:
        raise ValueError("anchor source out of range")

    evidence = _evidence_mask(observations)
    if warm_start is None:
        initial = _initial_texture(baseline, evidence, device)
        raw_shift = torch.zeros((n_sources, 2), device=device)
        raw_gain = torch.zeros(n_sources, device=device)
    else:
        initial = torch.as_tensor(warm_start.texture_rgb, dtype=torch.float32, device=device)
        shifts = np.zeros((n_sources, 2), dtype=np.float32)
        gains = np.ones(n_sources, dtype=np.float32)
        old_n = min(n_sources, len(warm_start.source_gain))
        shifts[:old_n] = warm_start.source_shift_cell[:old_n]
        gains[:old_n] = warm_start.source_gain[:old_n]
        raw_shift = torch.atanh(
            torch.as_tensor(shifts / config.pose_shift_limit_cell, device=device).clamp(-0.999, 0.999)
        )
        raw_gain = torch.atanh(
            (torch.log(torch.as_tensor(gains, device=device)) / config.log_gain_limit).clamp(
                -0.999, 0.999
            )
        )
    texture = initial.clone().requires_grad_(True)
    raw_shift = raw_shift.requires_grad_(True)
    raw_gain = raw_gain.requires_grad_(True)
    optimizer = torch.optim.Adam(
        [texture, raw_shift, raw_gain],
        lr=config.learning_rate,
    )
    baseline_texture = torch.as_tensor(
        baseline.texture_rgb, dtype=torch.float32, device=device
    )
    baseline_valid = torch.as_tensor(baseline.valid, dtype=torch.bool, device=device)
    total_steps = config.solver_steps if steps is None else int(steps)
    curve: list[float] = []
    started = time.perf_counter()
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)

    for step in range(total_steps):
        optimizer.zero_grad(set_to_none=True)
        shift = _bounded_gauge(raw_shift, anchor, config.pose_shift_limit_cell)
        log_gain = _bounded_gauge(
            raw_gain[:, None], anchor, config.log_gain_limit
        )[:, 0]
        gain = torch.exp(log_gain)
        prediction = observations.predict(texture, shift)
        normalized_raw = gain[observations.source_ids, None] * observations.rgb
        data = F.huber_loss(
            prediction,
            normalized_raw,
            delta=config.huber_delta,
            reduction="mean",
        )
        tv = _edge_aware_tv(texture, baseline_texture, evidence)
        coarse = _coarse_tie(texture, baseline_texture, baseline_valid)
        nuisance = 1.0e-3 * (shift.square().mean() + torch.log(gain).square().mean())
        loss = data + config.tv_weight * tv + config.coarse_tie_weight * coarse + nuisance
        loss.backward()
        optimizer.step()
        with torch.no_grad():
            texture.clamp_(0.0, 1.0)
            raw_shift[anchor].zero_()
            raw_gain[anchor].zero_()
        curve.append(float(loss.detach().cpu()))
        if progress_every and (step + 1) % progress_every == 0:
            print(f"DB145_SOLVE step={step + 1}/{total_steps} loss={curve[-1]:.7f}", flush=True)

    with torch.no_grad():
        shift = _bounded_gauge(raw_shift, anchor, config.pose_shift_limit_cell)
        log_gain = _bounded_gauge(
            raw_gain[:, None], anchor, config.log_gain_limit
        )[:, 0]
        gain = torch.exp(log_gain)
    memory_mb = (
        float(torch.cuda.max_memory_allocated(device) / 1024**2) if device.type == "cuda" else 0.0
    )
    return SolverResult(
        texture_rgb=texture.detach().cpu().numpy().astype(np.float32),
        evidence_valid=evidence.detach().cpu().numpy(),
        source_shift_cell=shift.detach().cpu().numpy().astype(np.float32),
        source_gain=gain.detach().cpu().numpy().astype(np.float32),
        loss_curve=tuple(curve),
        elapsed_s=time.perf_counter() - started,
        max_cuda_memory_mb=memory_mb,
    )


def source_residuals(
    observations: EWAObservationSet, result: SolverResult
) -> tuple[np.ndarray, np.ndarray]:
    """Return per-observation and per-source robust residual medians."""

    device = observations.centers_cell.device
    shift = np.zeros((observations.n_sources, 2), np.float32)
    gain = np.ones(observations.n_sources, np.float32)
    n = min(observations.n_sources, len(result.source_gain))
    shift[:n] = result.source_shift_cell[:n]
    gain[:n] = result.source_gain[:n]
    with torch.no_grad():
        texture = torch.as_tensor(result.texture_rgb, device=device)
        predicted = observations.predict(texture, torch.as_tensor(shift, device=device))
        normalized_raw = (
            torch.as_tensor(gain, device=device)[observations.source_ids, None] * observations.rgb
        )
        residual = (predicted - normalized_raw).abs().mean(dim=1).cpu().numpy()
    sources = observations.source_ids.cpu().numpy()
    per_source = np.full(observations.n_sources, np.nan, np.float32)
    for source in np.unique(sources):
        per_source[source] = np.median(residual[sources == source])
    return residual, per_source


def identify_outlier_sources(
    observations: EWAObservationSet,
    result: SolverResult,
    *,
    min_observations: int = 100,
) -> tuple[np.ndarray, list[str]]:
    """Frozen C gate: residual MAD plus relative bright/low-saturation evidence."""

    _, per_source = source_residuals(observations, result)
    finite = np.isfinite(per_source)
    median = float(np.median(per_source[finite]))
    mad = float(np.median(np.abs(per_source[finite] - median))) + 1.0e-8
    sources = observations.source_ids.cpu().numpy()
    rgb = observations.rgb.cpu().numpy()
    rejected: list[int] = []
    reasons: list[str] = []
    all_luma = rgb.mean(axis=1)
    global_luma = float(np.median(all_luma))
    for source in np.unique(sources):
        mask = sources == source
        if int(mask.sum()) < min_observations:
            continue
        reason: list[str] = []
        if per_source[source] > median + 3.0 * mad:
            reason.append("residual_mad")
        values = rgb[mask]
        luma = float(np.median(values.mean(axis=1)))
        saturation = float(np.median(values.max(axis=1) - values.min(axis=1)))
        # A deliberately conservative specular indicator: brightness must be
        # source-relative and nearly achromatic.  It never edits individual
        # pixels; it can only make the solver abstain from a whole bad view.
        if luma > max(0.65, 1.25 * global_luma) and saturation < 0.08:
            reason.append("bright_low_saturation")
        if reason:
            rejected.append(int(source))
            reasons.append("+".join(reason))
    return np.asarray(rejected, dtype=np.int64), reasons


def solve_with_view_rejection(
    observations: EWAObservationSet,
    baseline: BaselineResult,
    b_result: SolverResult,
    *,
    config: ExperimentConfig = DEFAULT_CONFIG,
    steps: int | None = None,
    min_observations: int = 100,
) -> SolverResult:
    rejected, reasons = identify_outlier_sources(
        observations, b_result, min_observations=min_observations
    )
    if not len(rejected):
        return SolverResult(
            **{
                **b_result.__dict__,
                "rejected_sources": (),
                "rejection_reasons": (),
            }
        )
    keep = ~np.isin(observations.source_ids.cpu().numpy(), rejected)
    filtered = observations.subset(keep)
    solved = solve_sensor_native(
        filtered, baseline, config=config, steps=steps, warm_start=b_result
    )
    return SolverResult(
        **{
            **solved.__dict__,
            "rejected_sources": tuple(int(x) for x in rejected),
            "rejection_reasons": tuple(reasons),
        }
    )
