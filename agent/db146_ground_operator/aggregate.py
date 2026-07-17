from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from agent.db145_ground_operator.report import save_rgb, write_json


EXPECTED_DEVELOPMENT_PATCHES: tuple[tuple[str, str], ...] = tuple(
    (role, level)
    for role in ("dry_straight", "dry_turn", "wet_or_specular")
    for level in ("high", "low")
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gain_percent(baseline: float, candidate: float) -> float:
    return 100.0 * (baseline - candidate) / max(baseline, 1.0e-8)


def _collect_patch(directory: Path, key: str) -> dict[str, object]:
    required = (
        "COMPLETE",
        "INNER_FROZEN",
        "inner_decision.json",
        "metrics.json",
        "patch_board.png",
    )
    missing = [name for name in required if not (directory / name).exists()]
    if missing:
        raise FileNotFoundError(f"{key} missing {missing}")
    frozen_sha = (directory / "INNER_FROZEN").read_text(encoding="ascii").strip()
    actual_sha = _sha256_file(directory / "inner_decision.json")
    if frozen_sha != actual_sha:
        raise RuntimeError(f"{key} inner decision changed after freeze")
    complete = json.loads((directory / "COMPLETE").read_text(encoding="utf-8"))
    if complete["inner_decision_sha256"] != frozen_sha:
        raise RuntimeError(f"{key} COMPLETE points to a different inner decision")
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    outer = metrics["outer_metrics"]
    robust_gain = _gain_percent(
        outer["A"]["robust_rgb_mae"], outer["D"]["robust_rgb_mae"]
    )
    l2_gain = _gain_percent(
        outer["A"]["median_rgb_l2"], outer["D"]["median_rgb_l2"]
    )
    return {
        "key": key,
        "selected_label": metrics["gate"]["selected_label"],
        "uses_inverse": metrics["gate"]["uses_inverse"],
        "inner_decision_sha256": frozen_sha,
        "outer_metrics": outer,
        "D_vs_A_percent": {
            "robust_rgb_mae": robust_gain,
            "median_rgb_l2": l2_gain,
        },
        "B_vs_A_percent": metrics["outer_gain_percent"]["B"],
        "safe_inverse_fraction": metrics["safe_inverse_fraction"],
        "uncertainty_mean": metrics["uncertainty_mean"],
        "automatic_no_regression": robust_gain >= -1.0 and l2_gain >= -1.0,
        "board": str(directory / "patch_board.png"),
    }


def _letterbox(image: np.ndarray, width: int, height: int) -> np.ndarray:
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(
        image,
        (
            max(1, int(round(image.shape[1] * scale))),
            max(1, int(round(image.shape[0] * scale))),
        ),
        interpolation=cv2.INTER_AREA,
    )
    canvas = np.full((height, width, 3), 18, np.uint8)
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


def _make_board(rows: list[dict[str, object]], path: Path) -> None:
    label_width = 300
    board_width = 1500
    row_height = 500
    output: list[np.ndarray] = []
    for item in rows:
        image = cv2.imread(str(item["board"]), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(item["board"])
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        panel = _letterbox(image, board_width, row_height)
        label = np.full((row_height, label_width, 3), 26, np.uint8)
        gains = item["D_vs_A_percent"]
        lines = (
            str(item["key"]),
            f"selected={item['selected_label']}",
            f"D robust {gains['robust_rgb_mae']:+.2f}%",
            f"D median {gains['median_rgb_l2']:+.2f}%",
            f"safe fraction {item['safe_inverse_fraction']:.3f}",
            f"auto pass {item['automatic_no_regression']}",
        )
        for index, text in enumerate(lines):
            cv2.putText(
                label,
                text,
                (10, 36 + index * 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (245, 245, 245),
                1,
                cv2.LINE_AA,
            )
        output.append(np.hstack((label, panel)))
    save_rgb(path, np.vstack(output))


def aggregate(
    *,
    development_root: Path,
    output: Path,
    expected_commit: str,
) -> dict[str, object]:
    patches = [
        _collect_patch(
            Path(development_root) / role / level,
            f"{role}:{level}",
        )
        for role, level in EXPECTED_DEVELOPMENT_PATCHES
    ]
    dry_high = [
        item
        for item in patches
        if item["key"] in ("dry_straight:high", "dry_turn:high")
    ]
    checks = {
        "six_patches_complete": len(patches) == 6,
        "all_inner_decisions_hash_frozen": all(
            bool(item["inner_decision_sha256"]) for item in patches
        ),
        "all_D_within_1pct_of_A_on_both_outer_metrics": all(
            bool(item["automatic_no_regression"]) for item in patches
        ),
        "at_least_one_dry_high_retains_real_gain": any(
            item["uses_inverse"]
            and item["D_vs_A_percent"]["robust_rgb_mae"] > 0.0
            and item["D_vs_A_percent"]["median_rgb_l2"] > 0.0
            for item in dry_high
        ),
        "no_low_or_wet_patch_selects_untruncated_full": all(
            item["selected_label"] != "full"
            for item in patches
            if ":low" in item["key"] or item["key"].startswith("wet_or_specular:")
        ),
    }
    payload: dict[str, object] = {
        "schema": "db146_aggregate_v1",
        "expected_git_commit": expected_commit,
        "development_root": str(development_root),
        "checks": checks,
        "automatic_pass": all(checks.values()),
        "vision_gate": "PENDING_HUMAN_EYE",
        "patches": patches,
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "verdict_metrics.json", payload)
    _make_board(patches, output / "verdict_board.png")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = aggregate(
        development_root=args.development_root,
        output=args.output,
        expected_commit=args.expected_commit,
    )
    print(
        f"DB146_AGGREGATE automatic_pass={payload['automatic_pass']} "
        f"vision={payload['vision_gate']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
