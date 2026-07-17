from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from agent.db145_ground_operator.av2_extract import (
    SOURCE_FRAME_STEP,
    freeze_patch_heldout_groups,
    generate_patch_candidates,
)
from agent.db145_ground_operator.config import DEFAULT_CONFIG
from agent.db145_ground_operator.observability import select_patch_pair
from agent.db145_ground_operator.report import write_json


def freeze_unseen_log(
    *,
    data_root: Path,
    split: str,
    log_id: str,
    window: tuple[int, int],
    role: str,
    source_manifest: Path,
    output: Path,
    git_commit: str,
) -> dict[str, object]:
    """Freeze geometry-only high/low patches for a genuinely unseen log."""

    source = json.loads(Path(source_manifest).read_text(encoding="utf-8"))
    development_logs = {
        str(scene["log_id"]) for scene in source.get("scenes", {}).values()
    }
    if log_id in development_logs:
        raise ValueError(f"{log_id} already appears in the development manifest")
    log_dir = Path(data_root) / split / log_id
    patches, scores, diagnostics = generate_patch_candidates(log_dir, window)
    patch_by_id = {patch.patch_id: patch for patch in patches}
    high, low = select_patch_pair(scores)
    selected: list[dict[str, object]] = []
    for label, score in (("high", high), ("low", low)):
        patch = patch_by_id[score.patch_id]
        heldout, counts = freeze_patch_heldout_groups(log_dir, patch, window)
        selected.append(
            {
                "label": label,
                "patch": asdict(patch),
                "observability": {**asdict(score), "score": score.score},
                "training_groups": list(heldout.training_groups),
                "heldout_groups": list(heldout.heldout_groups),
                "heldout_strategy": heldout.strategy,
                "heldout_fraction_geometry": heldout.heldout_fraction,
                "heldout_geometry_pixel_counts": counts,
            }
        )
    payload: dict[str, object] = {
        "schema": "db146_unseen_manifest_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "frozen_before_optimization": True,
        "source_frame_step": SOURCE_FRAME_STEP,
        "source_development_manifest": str(source_manifest),
        "runtime": {
            "git_commit": git_commit,
            "config_sha256": DEFAULT_CONFIG.sha256(),
            "config": json.loads(DEFAULT_CONFIG.canonical_json()),
        },
        "scenes": {
            role: {
                "log_id": log_id,
                "split": split,
                "window": list(window),
                "selection_evidence": "unseen geometry-only automatic high/low selection",
                "patches": selected,
                "candidate_diagnostics": diagnostics,
            }
        },
    }
    write_json(Path(output), payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--split", default="val")
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--window", nargs=2, type=int, required=True)
    parser.add_argument("--role", default="unseen_dry")
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--git-commit", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = freeze_unseen_log(
        data_root=args.data_root,
        split=args.split,
        log_id=args.log_id,
        window=tuple(args.window),
        role=args.role,
        source_manifest=args.source_manifest,
        output=args.output,
        git_commit=args.git_commit,
    )
    scene = payload["scenes"][args.role]
    print(
        "DB146_UNSEEN_FROZEN "
        + " ".join(
            f"{item['label']}={item['patch']['patch_id']}"
            for item in scene["patches"]
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
