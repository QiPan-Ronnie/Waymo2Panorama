from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import time

import cv2
import numpy as np
import torch

from .av2_extract import (
    GroundPatch,
    SOURCE_FRAME_STEP,
    extract_patch,
    freeze_patch_heldout_groups,
    generate_patch_candidates,
)
from .config import DEFAULT_CONFIG
from .evaluate import evaluate_heldout
from .observability import SCENE_CANDIDATES, select_patch_pair
from .report import make_patch_board, save_heldout_evidence, save_texture, write_json
from .solver import SolverResult, solve_sensor_native, solve_with_view_rejection


FROZEN_ROLES_R1 = {
    "dry_straight": "8749f79f-a30b-3c3f-8a44-dbfa682bbef1",
    "dry_turn": "02a00399-3857-444e-8db3-a8f58489c394",
    "wet_or_specular": "05fa5048-f355-3274-b565-c0ddc547b315",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_facts(git_commit: str) -> dict[str, object]:
    return {
        "git_commit": git_commit,
        "config_sha256": DEFAULT_CONFIG.sha256(),
        "config": json.loads(DEFAULT_CONFIG.canonical_json()),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    }


def _patch_dict(patch: GroundPatch, score: object, label: str) -> dict[str, object]:
    return {
        "label": label,
        "patch": asdict(patch),
        "observability": {
            **asdict(score),
            "score": score.score,
        },
    }


def run_p0(args: argparse.Namespace) -> None:
    output = Path(args.output_root) / args.run_id
    output.mkdir(parents=True, exist_ok=True)
    scenes: dict[str, object] = {}
    started = time.perf_counter()
    for role, log_id in FROZEN_ROLES_R1.items():
        window = tuple(int(x) for x in SCENE_CANDIDATES[log_id]["window"])
        log_dir = Path(args.data_root) / "val" / log_id
        print(f"DB145_P0_SCENE role={role} log={log_id} window={window}", flush=True)
        patches, scores, diagnostics = generate_patch_candidates(log_dir, window)
        patch_by_id = {patch.patch_id: patch for patch in patches}
        high, low = select_patch_pair(scores)
        selected = [
            _patch_dict(patch_by_id[high.patch_id], high, "high"),
            _patch_dict(patch_by_id[low.patch_id], low, "low"),
        ]
        for item in selected:
            patch = GroundPatch(**item["patch"])
            split, group_counts = freeze_patch_heldout_groups(log_dir, patch, window)
            item["training_groups"] = list(split.training_groups)
            item["heldout_groups"] = list(split.heldout_groups)
            item["heldout_strategy"] = split.strategy
            item["heldout_fraction_geometry"] = split.heldout_fraction
            item["heldout_geometry_pixel_counts"] = group_counts
        scenes[role] = {
            "log_id": log_id,
            "split": "val",
            "window": list(window),
            "selection_evidence": SCENE_CANDIDATES[log_id].get("evidence", "pose-ranked dry role"),
            "patches": selected,
            "candidate_diagnostics": diagnostics,
        }
        print(
            f"DB145_P0_SELECTED role={role} high={high.patch_id}:{high.score:.4f} "
            f"low={low.patch_id}:{low.score:.4f}",
            flush=True,
        )
    manifest = {
        "schema": "db145_manifest_v2",
        "run_id": args.run_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "experiment": "sensor-native anisotropic ground operator kill-test",
        "frozen_before_optimization": True,
        "runtime": _runtime_facts(args.git_commit),
        "role_selection": {
            "roles": FROZEN_ROLES_R1,
            "evidence": "pose-only robust curvature ranks; wet role fixed by DB-128",
        },
        "source_frame_step": SOURCE_FRAME_STEP,
        "scenes": scenes,
        "p0_elapsed_s": time.perf_counter() - started,
    }
    write_json(output / "manifest.json", manifest)
    (output / "P0_COMPLETE").write_text(DEFAULT_CONFIG.sha256() + "\n", encoding="ascii")
    print(f"DB145_P0_FROZEN {DEFAULT_CONFIG.sha256()}", flush=True)


def _baseline_solver_result(extraction: object) -> SolverResult:
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


def _save_patch_results(
    directory: Path,
    extraction: object,
    a_result: SolverResult,
    b_result: SolverResult,
    c_result: SolverResult,
    heldout_operator: object,
) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    results = {"A": a_result, "B": b_result, "C": c_result}
    evaluations = {
        name: evaluate_heldout(result, heldout_operator) for name, result in results.items()
    }
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

    original = extraction.heldout_observations.provenance["original_source_id"]
    source, counts = np.unique(original, return_counts=True)
    chosen_source = int(source[np.argmax(counts)])
    chosen = original == chosen_source
    heldout_source = heldout_operator.subset(chosen)
    uv = np.column_stack(
        (
            extraction.heldout_observations.provenance["u"][chosen],
            extraction.heldout_observations.provenance["v"][chosen],
        )
    )
    source_evaluations = {
        name: evaluate_heldout(result, heldout_source) for name, result in results.items()
    }
    evidence = {
        name: save_heldout_evidence(directory, name, evaluation, uv)
        for name, evaluation in source_evaluations.items()
    }
    make_patch_board(
        directory / "patch_board.png",
        {name: result.texture_rgb for name, result in results.items()},
        {name: result.evidence_valid for name, result in results.items()},
        source_evaluations,
    )
    payload = {
        "extraction": extraction.diagnostics,
        "heldout_metrics_all": {
            name: asdict(evaluation.metrics) for name, evaluation in evaluations.items()
        },
        "vision_source_original_id": chosen_source,
        "vision_source_evidence": evidence,
        "solver": {
            "B": {
                "elapsed_s": b_result.elapsed_s,
                "max_cuda_memory_mb": b_result.max_cuda_memory_mb,
                "loss_first": b_result.loss_curve[0],
                "loss_last": b_result.loss_curve[-1],
                "shift_abs_max": float(np.abs(b_result.source_shift_cell).max()),
                "log_gain_abs_max": float(np.abs(np.log(b_result.source_gain)).max()),
            },
            "C": {
                "elapsed_s": c_result.elapsed_s,
                "max_cuda_memory_mb": c_result.max_cuda_memory_mb,
                "rejected_sources": list(c_result.rejected_sources),
                "rejection_reasons": list(c_result.rejection_reasons),
            },
        },
    }
    write_json(directory / "metrics.json", payload)
    return payload


def run_p1(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest["runtime"]["config_sha256"]
    if expected != DEFAULT_CONFIG.sha256():
        raise RuntimeError(f"config mismatch: manifest={expected} current={DEFAULT_CONFIG.sha256()}")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    output = manifest_path.parent
    started = time.perf_counter()
    completed: list[str] = []
    for role, scene in manifest["scenes"].items():
        for selected in scene["patches"]:
            key = f"{role}:{selected['label']}"
            if args.only and key != args.only:
                continue
            directory = output / role / selected["label"]
            if (directory / "COMPLETE").exists() and not args.force:
                print(f"DB145_P1_SKIP {key}", flush=True)
                continue
            if time.perf_counter() - started > args.max_gpu_hours * 3600:
                raise TimeoutError("DB-145 GPU-hour ceiling reached")
            patch = GroundPatch(**selected["patch"])
            log_dir = Path(args.data_root) / scene["split"] / scene["log_id"]
            print(f"DB145_P1_EXTRACT {key}", flush=True)
            extraction = extract_patch(
                log_dir,
                patch,
                tuple(scene["window"]),
                device=device,
                training_groups=selected["training_groups"],
                heldout_groups=selected["heldout_groups"],
            )
            print(f"DB145_P1_OPERATOR {key}", flush=True)
            train = extraction.train_observations.build_operator(
                grid_hw=(DEFAULT_CONFIG.grid_hw, DEFAULT_CONFIG.grid_hw),
                config=DEFAULT_CONFIG,
                device=device,
            )
            heldout = extraction.heldout_observations.build_operator(
                grid_hw=(DEFAULT_CONFIG.grid_hw, DEFAULT_CONFIG.grid_hw),
                config=DEFAULT_CONFIG,
                device=device,
            )
            print(f"DB145_P1_SOLVE_B {key}", flush=True)
            b_result = solve_sensor_native(
                train,
                extraction.baseline,
                progress_every=50,
            )
            print(f"DB145_P1_SOLVE_C {key}", flush=True)
            c_result = solve_with_view_rejection(
                train,
                extraction.baseline,
                b_result,
            )
            payload = _save_patch_results(
                directory,
                extraction,
                _baseline_solver_result(extraction),
                b_result,
                c_result,
                heldout,
            )
            complete = {
                "key": key,
                "metrics_sha256": None,
                "completed_utc": datetime.now(timezone.utc).isoformat(),
                "metrics": payload["heldout_metrics_all"],
            }
            complete["metrics_sha256"] = _sha256_file(directory / "metrics.json")
            write_json(directory / "COMPLETE", complete)
            completed.append(key)
            print(f"DB145_P1_COMPLETE {key}", flush=True)
            del train, heldout
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    write_json(
        output / "p1_last_run.json",
        {
            "completed": completed,
            "elapsed_s": time.perf_counter() - started,
            "git_status": subprocess.run(
                ["git", "status", "--short"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout,
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    p0 = subparsers.add_parser("p0")
    p0.add_argument("--run-id", required=True)
    p0.add_argument("--data-root", required=True)
    p0.add_argument("--output-root", required=True)
    p0.add_argument("--git-commit", required=True)
    p1 = subparsers.add_parser("p1")
    p1.add_argument("--manifest", required=True)
    p1.add_argument("--data-root", required=True)
    p1.add_argument("--only")
    p1.add_argument("--force", action="store_true")
    p1.add_argument("--max-gpu-hours", type=float, default=4.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "p0":
        run_p0(args)
    else:
        run_p1(args)


if __name__ == "__main__":
    main()
