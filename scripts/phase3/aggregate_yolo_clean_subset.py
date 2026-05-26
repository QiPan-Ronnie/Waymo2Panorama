"""
Aggregate YOLO v2 ghost scores across all 5 val logs → final clean-subset
report for Bosch dataset delivery.

Reads per-log yolo_ghost_scores.json files, produces:
    - clean_subset_summary.json — aggregated stats per log + total
    - clean_anchors_per_log.json — list of (log_id, anchor_index) for the
      strict ghost-free subset (score=0) and the relaxed subset (score<=2)

Usage:
    python scripts/phase3/aggregate_yolo_clean_subset.py \\
        --root-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/ghost_scoring_yolo_v2 \\
        --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/bosch_clean_subset
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root-dir", type=Path, required=True,
                    help="Dir containing <log_id>/yolo_ghost_scores.json")
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    per_log = {}
    strict_subset = []   # score == 0
    relaxed_subset = []  # score <= 2

    for log_dir in sorted(args.root_dir.glob("*")):
        if not log_dir.is_dir():
            continue
        scores_path = log_dir / "yolo_ghost_scores.json"
        if not scores_path.exists():
            print(f"  skip {log_dir.name}: no scores file")
            continue
        with open(scores_path) as f:
            data = json.load(f)
        results = data["results_sorted"]
        log_id = log_dir.name
        n_total = len(results)
        n_strict = sum(1 for r in results if r["total_edge_objects"] == 0)
        n_relaxed = sum(1 for r in results if r["total_edge_objects"] <= 2)

        per_log[log_id] = {
            "n_anchors_scanned": n_total,
            "n_strict_zero_edge": n_strict,
            "n_relaxed_le2": n_relaxed,
            "frac_strict": n_strict / n_total if n_total else 0.0,
            "frac_relaxed": n_relaxed / n_total if n_total else 0.0,
            "stride": data["stride"],
            "score_min": data["stats"]["min"],
            "score_median": data["stats"]["median"],
            "score_mean": data["stats"]["mean"],
            "score_max": data["stats"]["max"],
        }
        # Collect anchor lists
        for r in results:
            entry = {"log_id": log_id, "anchor_index": r["anchor_index"],
                     "ts_ns": r["ts_ns"], "score": r["total_edge_objects"]}
            if r["total_edge_objects"] == 0:
                strict_subset.append(entry)
            if r["total_edge_objects"] <= 2:
                relaxed_subset.append(entry)
        print(f"  {log_id}: n={n_total}, strict={n_strict} ({n_strict/n_total*100:.1f}%), "
              f"relaxed={n_relaxed} ({n_relaxed/n_total*100:.1f}%)")

    # Aggregate
    n_total_all = sum(p["n_anchors_scanned"] for p in per_log.values())
    n_strict_all = sum(p["n_strict_zero_edge"] for p in per_log.values())
    n_relaxed_all = sum(p["n_relaxed_le2"] for p in per_log.values())

    print(f"\n=== TOTAL ===")
    print(f"  anchors scanned: {n_total_all}")
    print(f"  STRICT ghost-free (score=0): {n_strict_all} = {n_strict_all/max(n_total_all,1)*100:.1f}%")
    print(f"  RELAXED (score<=2):           {n_relaxed_all} = {n_relaxed_all/max(n_total_all,1)*100:.1f}%")
    print(f"\nTo scale up to full ~319 anchors/log × 5 logs ≈ 1600 anchors at stride=1:")
    if per_log:
        first = next(iter(per_log.values()))
        stride = first["stride"]
        scale = stride
        print(f"  (current scan at stride={stride}, projecting to stride=1 with {scale}x scan density)")
        print(f"  projected STRICT: ~{n_strict_all * scale}")
        print(f"  projected RELAXED: ~{n_relaxed_all * scale}")

    summary = {
        "per_log": per_log,
        "total_anchors_scanned": n_total_all,
        "total_strict_ghost_free": n_strict_all,
        "total_relaxed_le2": n_relaxed_all,
    }
    with open(args.output_dir / "clean_subset_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    with open(args.output_dir / "strict_clean_anchors.json", "w") as f:
        json.dump(strict_subset, f, indent=2)
    with open(args.output_dir / "relaxed_clean_anchors.json", "w") as f:
        json.dump(relaxed_subset, f, indent=2)
    print(f"\n=> wrote {args.output_dir / 'clean_subset_summary.json'}")
    print(f"   wrote {args.output_dir / 'strict_clean_anchors.json'} ({len(strict_subset)} entries)")
    print(f"   wrote {args.output_dir / 'relaxed_clean_anchors.json'} ({len(relaxed_subset)} entries)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
