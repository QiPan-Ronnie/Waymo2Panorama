from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time

import cv2
import numpy as np
import torch

from agent.db145_ground_operator.av2_extract import GroundPatch, extract_patch
from agent.db145_ground_operator.config import DEFAULT_CONFIG
from agent.db145_ground_operator.evaluate import evaluate_heldout
from agent.db145_ground_operator.report import (
    save_heldout_evidence,
    save_texture,
    write_json,
)
from agent.db145_ground_operator.solver import SolverResult, solve_sensor_native

from .gate import (
    BAND_SPECS,
    FoldBandMetrics,
    checker_ratio,
    correction_uncertainty,
    result_from_texture,
    select_safe_band,
    structured_group_folds,
    truncated_texture,
)
from .report import make_safe_patch_board
from .sampling import MAX_OPERATOR_OBSERVATIONS, bound_observations


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _baseline_result(extraction: object) -> SolverResult:
    baseline = extraction.baseline
    return SolverResult(
        texture_rgb=baseline.texture_rgb,
        evidence_valid=baseline.valid,
        source_shift_cell=np.zeros((1, 2), np.float32),
        source_gain=np.ones(1, np.float32),
        loss_curve=(),
        elapsed_s=0.0,
        max_cuda_memory_mb=0.0,
    )


def _improvement(a: float, candidate: float) -> float:
    return 100.0 * (a - candidate) / max(a, 1.0e-8)


def _inner_fold(
    *,
    fold_index: int,
    fit_groups: tuple[str, ...],
    validation_groups: tuple[str, ...],
    log_dir: Path,
    patch: GroundPatch,
    window: tuple[int, int],
    device: str,
) -> tuple[
    dict[str, FoldBandMetrics],
    dict[str, np.ndarray],
    dict[str, object],
]:
    extraction = extract_patch(
        log_dir,
        patch,
        window,
        device=device,
        training_groups=fit_groups,
        heldout_groups=validation_groups,
    )
    train_arrays, train_sampling = bound_observations(
        extraction.train_observations
    )
    validation_arrays, validation_sampling = bound_observations(
        extraction.heldout_observations
    )
    train = train_arrays.build_operator(
        grid_hw=extraction.baseline.valid.shape,
        config=DEFAULT_CONFIG,
        device=device,
    )
    validation = validation_arrays.build_operator(
        grid_hw=extraction.baseline.valid.shape,
        config=DEFAULT_CONFIG,
        device=device,
    )
    baseline_result = _baseline_result(extraction)
    baseline_evaluation = evaluate_heldout(baseline_result, validation)
    inverse = solve_sensor_native(train, extraction.baseline, progress_every=100)
    records: dict[str, FoldBandMetrics] = {}
    corrections: dict[str, np.ndarray] = {}
    candidate_payload: dict[str, object] = {}
    for label, sigma in BAND_SPECS:
        texture = truncated_texture(extraction.baseline, inverse, sigma)
        result = result_from_texture(
            texture, extraction.baseline, inverse, uses_inverse=True
        )
        evaluation = evaluate_heldout(result, validation)
        records[label] = FoldBandMetrics(
            fold=fold_index,
            baseline_robust_mae=baseline_evaluation.metrics.robust_rgb_mae,
            candidate_robust_mae=evaluation.metrics.robust_rgb_mae,
            baseline_median_l2=baseline_evaluation.metrics.median_rgb_l2,
            candidate_median_l2=evaluation.metrics.median_rgb_l2,
            checker_ratio=checker_ratio(texture, extraction.baseline.texture_rgb),
        )
        corrections[label] = texture - extraction.baseline.texture_rgb
        candidate_payload[label] = {
            "sigma_cell": sigma,
            "metrics": asdict(evaluation.metrics),
            "checker_ratio": records[label].checker_ratio,
            "robust_gain_percent": 100.0 * records[label].robust_gain,
            "median_l2_gain_percent": 100.0 * records[label].median_l2_gain,
        }
    payload = {
        "fold": fold_index,
        "fit_groups": list(fit_groups),
        "validation_groups": list(validation_groups),
        "fit_observations": train_sampling.as_dict(),
        "validation_observations": validation_sampling.as_dict(),
        "baseline": asdict(baseline_evaluation.metrics),
        "inverse_solver": {
            "elapsed_s": inverse.elapsed_s,
            "max_cuda_memory_mb": inverse.max_cuda_memory_mb,
            "loss_first": inverse.loss_curve[0],
            "loss_last": inverse.loss_curve[-1],
            "shift_abs_max": float(np.abs(inverse.source_shift_cell).max()),
            "log_gain_abs_max": float(np.abs(np.log(inverse.source_gain)).max()),
        },
        "bands": candidate_payload,
    }
    del train, validation
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return records, corrections, payload


def _outer_and_save(
    *,
    directory: Path,
    selected: dict[str, object],
    scene: dict[str, object],
    log_dir: Path,
    decision: object,
    selected_corrections: list[np.ndarray],
    device: str,
) -> dict[str, object]:
    patch = GroundPatch(**selected["patch"])
    extraction = extract_patch(
        log_dir,
        patch,
        tuple(scene["window"]),
        device=device,
        training_groups=selected["training_groups"],
        heldout_groups=selected["heldout_groups"],
    )
    train_arrays, train_sampling = bound_observations(
        extraction.train_observations
    )
    outer_arrays, outer_sampling = bound_observations(
        extraction.heldout_observations
    )
    train = train_arrays.build_operator(
        grid_hw=extraction.baseline.valid.shape,
        config=DEFAULT_CONFIG,
        device=device,
    )
    outer = outer_arrays.build_operator(
        grid_hw=extraction.baseline.valid.shape,
        config=DEFAULT_CONFIG,
        device=device,
    )
    a_result = _baseline_result(extraction)
    b_result = solve_sensor_native(train, extraction.baseline, progress_every=100)
    if decision.uses_inverse:
        d_texture = truncated_texture(
            extraction.baseline, b_result, float(decision.selected_sigma_cell)
        )
        d_result = result_from_texture(
            d_texture, extraction.baseline, b_result, uses_inverse=True
        )
        safe_inverse_mask = np.asarray(b_result.evidence_valid, bool)
        uncertainty = correction_uncertainty(selected_corrections)
    else:
        d_texture = extraction.baseline.texture_rgb.copy()
        d_result = _baseline_result(extraction)
        safe_inverse_mask = np.zeros_like(extraction.baseline.valid)
        uncertainty = np.ones_like(extraction.baseline.valid, np.float32)
    results = {"A": a_result, "B": b_result, "D": d_result}
    evaluations = {
        name: evaluate_heldout(result, outer) for name, result in results.items()
    }

    directory.mkdir(parents=True, exist_ok=True)
    for name, result in results.items():
        save_texture(
            directory / f"{name}_texture.png",
            result.texture_rgb,
            result.evidence_valid,
        )
        cv2.imwrite(
            str(directory / f"{name}_valid.png"),
            result.evidence_valid.astype(np.uint8) * 255,
        )
    cv2.imwrite(
        str(directory / "safe_inverse_mask.png"),
        safe_inverse_mask.astype(np.uint8) * 255,
    )
    cv2.imwrite(
        str(directory / "uncertainty.png"),
        np.clip(uncertainty * 255.0, 0, 255).astype(np.uint8),
    )

    original = outer_arrays.provenance["original_source_id"]
    sources, counts = np.unique(original, return_counts=True)
    chosen_source = int(sources[np.argmax(counts)])
    chosen = original == chosen_source
    vision_outer = outer.subset(chosen)
    uv = np.column_stack(
        (
            outer_arrays.provenance["u"][chosen],
            outer_arrays.provenance["v"][chosen],
        )
    )
    source_evaluations = {
        name: evaluate_heldout(result, vision_outer) for name, result in results.items()
    }
    evidence = {
        name: save_heldout_evidence(directory, name, evaluation, uv)
        for name, evaluation in source_evaluations.items()
    }
    make_safe_patch_board(
        directory / "patch_board.png",
        textures={name: result.texture_rgb for name, result in results.items()},
        safe_inverse_mask=safe_inverse_mask,
        uncertainty=uncertainty,
        heldout=source_evaluations,
        selected_label=decision.selected_label,
    )

    a_metrics = evaluations["A"].metrics
    payload = {
        "extraction": extraction.diagnostics,
        "operator_sampling": {
            "training": train_sampling.as_dict(),
            "outer_heldout": outer_sampling.as_dict(),
        },
        "gate": decision.as_dict(),
        "outer_metrics": {
            name: asdict(evaluation.metrics) for name, evaluation in evaluations.items()
        },
        "outer_gain_percent": {
            name: {
                "robust_mae": _improvement(
                    a_metrics.robust_rgb_mae, evaluation.metrics.robust_rgb_mae
                ),
                "median_rgb_l2": _improvement(
                    a_metrics.median_rgb_l2, evaluation.metrics.median_rgb_l2
                ),
            }
            for name, evaluation in evaluations.items()
            if name != "A"
        },
        "vision_source_original_id": chosen_source,
        "vision_source_evidence": evidence,
        "safe_inverse_fraction": float(safe_inverse_mask.mean()),
        "uncertainty_mean": float(uncertainty.mean()),
        "solver": {
            "elapsed_s": b_result.elapsed_s,
            "max_cuda_memory_mb": b_result.max_cuda_memory_mb,
            "loss_first": b_result.loss_curve[0],
            "loss_last": b_result.loss_curve[-1],
        },
    }
    write_json(directory / "metrics.json", payload)
    del train, outer, vision_outer
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return payload


def run(args: argparse.Namespace) -> None:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    output = Path(args.output_root) / args.run_id
    output.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    started = time.perf_counter()
    summary: dict[str, object] = {
        "schema": "db146_run_v1",
        "run_id": args.run_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": args.git_commit,
        "source_manifest": str(args.manifest),
        "source_manifest_sha256": _sha256_file(Path(args.manifest)),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "bands": [{"label": label, "sigma_cell": sigma} for label, sigma in BAND_SPECS],
        "max_operator_observations": MAX_OPERATOR_OBSERVATIONS,
        "patches": {},
    }
    for role, scene in manifest["scenes"].items():
        for selected in scene["patches"]:
            key = f"{role}:{selected['label']}"
            if args.only and key != args.only:
                continue
            directory = output / role / selected["label"]
            if (directory / "COMPLETE").exists() and not args.force:
                print(f"DB146_SKIP {key}", flush=True)
                continue
            if time.perf_counter() - started > args.max_gpu_hours * 3600:
                raise TimeoutError("DB-146 GPU-hour ceiling reached")
            print(f"DB146_INNER_START {key}", flush=True)
            training_groups = tuple(selected["training_groups"])
            folds = structured_group_folds(
                selected["heldout_geometry_pixel_counts"],
                training_groups,
                n_folds=3,
            )
            by_band: dict[str, list[FoldBandMetrics]] = {
                label: [] for label, _ in BAND_SPECS
            }
            corrections: dict[str, list[np.ndarray]] = {
                label: [] for label, _ in BAND_SPECS
            }
            fold_payloads: list[dict[str, object]] = []
            log_dir = Path(args.data_root) / scene["split"] / scene["log_id"]
            for fold_index, validation_groups in enumerate(folds):
                fit_groups = tuple(
                    group for group in training_groups if group not in set(validation_groups)
                )
                print(
                    f"DB146_INNER_FOLD {key} fold={fold_index} "
                    f"fit={len(fit_groups)} val={len(validation_groups)}",
                    flush=True,
                )
                records, fold_corrections, payload = _inner_fold(
                    fold_index=fold_index,
                    fit_groups=fit_groups,
                    validation_groups=validation_groups,
                    log_dir=log_dir,
                    patch=GroundPatch(**selected["patch"]),
                    window=tuple(scene["window"]),
                    device=device,
                )
                for label, _ in BAND_SPECS:
                    by_band[label].append(records[label])
                    corrections[label].append(fold_corrections[label])
                fold_payloads.append(payload)
            decision = select_safe_band(by_band, corrections)
            inner_payload = {
                "key": key,
                "folds": fold_payloads,
                "decision": decision.as_dict(),
                "outer_heldout_was_not_loaded": True,
                "frozen_utc": datetime.now(timezone.utc).isoformat(),
            }
            write_json(directory / "inner_decision.json", inner_payload)
            inner_sha = _sha256_file(directory / "inner_decision.json")
            (directory / "INNER_FROZEN").write_text(inner_sha + "\n", encoding="ascii")
            print(
                f"DB146_INNER_FROZEN {key} selected={decision.selected_label} sha={inner_sha}",
                flush=True,
            )
            selected_corrections = (
                corrections[decision.selected_label]
                if decision.uses_inverse
                else corrections["lp8"]
            )
            outer_payload = _outer_and_save(
                directory=directory,
                selected=selected,
                scene=scene,
                log_dir=log_dir,
                decision=decision,
                selected_corrections=selected_corrections,
                device=device,
            )
            complete = {
                "key": key,
                "inner_decision_sha256": inner_sha,
                "selected_label": decision.selected_label,
                "outer_metrics": outer_payload["outer_metrics"],
                "completed_utc": datetime.now(timezone.utc).isoformat(),
            }
            write_json(directory / "COMPLETE", complete)
            summary["patches"][key] = complete
            write_json(output / "summary.json", summary)
            print(f"DB146_COMPLETE {key}", flush=True)
    summary["elapsed_s"] = time.perf_counter() - started
    write_json(output / "summary.json", summary)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--only")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-gpu-hours", type=float, default=12.0)
    return parser.parse_args()


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
