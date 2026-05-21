"""T6: Rank Pi3 anchors by parallax score.

Computes parallax score for each of the 10 anchors using the per-cam Pi3 summary
statistics (median local depth + high-confidence pixel fraction). The score
favors anchors where:
  - Pi3 reports many close objects (low median depth where confidence > 0.1)
  - Multiple cameras (esp. side cams) see those near objects with good coverage

Rationale: L1 sphere-projection assumes infinite depth, so it ghosts whenever
near objects exist. L3 forward-splat uses actual 3D points and SHOULD do better
on parallax-rich frames. This script identifies that subset.

Inputs : ``data/_summaries_cache/anchor_<idx>_summary.json`` for idx in
         {0,30,60,90,120,150,180,210,240,270}
Outputs:
  - ``data/parallax_subset.json`` (sorted ranking + score breakdown)
  - ``outputs/phase3/parallax/anchor_<idx>_summary.png`` (per-anchor per-cam viz)
  - ``outputs/phase3/parallax/parallax_ranking.png`` (overall comparison bar)
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SUMMARIES_DIR = REPO / "data" / "_summaries_cache"
OUT_DIR = REPO / "outputs" / "phase3" / "parallax"
DATA_OUT = REPO / "data" / "parallax_subset.json"

ANCHORS = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270]
CAMS = [
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
]

# "Close" threshold (m). Pi3 median depth below this == strong parallax signal.
CLOSE_DEPTH_M = 10.0
# Saturate the score so a single very-close cam doesn't dominate.
CLOSE_DEPTH_FLOOR_M = 1.5


def load_summary(idx: int) -> dict:
    fp = SUMMARIES_DIR / f"anchor_{idx:03d}_summary.json"
    return json.loads(fp.read_text())


def closeness(z_m: float) -> float:
    """Convert per-cam median depth to a [0,1] closeness signal.

    Linear ramp: 0 m below floor, 1 at floor, 0 at CLOSE_DEPTH_M, 0 beyond.
    """
    if not math.isfinite(z_m):
        return 0.0
    if z_m <= CLOSE_DEPTH_FLOOR_M:
        return 1.0
    if z_m >= CLOSE_DEPTH_M:
        return 0.0
    return (CLOSE_DEPTH_M - z_m) / (CLOSE_DEPTH_M - CLOSE_DEPTH_FLOOR_M)


def compute_anchor_score(summary: dict) -> dict:
    """Compute parallax score components for one anchor."""
    per_cam = summary["per_cam"]
    cam_records = []
    weighted_closeness = 0.0
    total_weight = 1e-9
    near_cam_count = 0
    for cam in CAMS:
        rec = per_cam[cam]
        z = float(rec["local_z_median_when_valid"])
        # weight by high-conf coverage so "noise pixels" don't shift the score
        w = float(rec["conf_pct_gt_0.1"])
        c = closeness(z)
        weighted_closeness += w * c
        total_weight += w
        if z < CLOSE_DEPTH_M:
            near_cam_count += 1
        cam_records.append(
            {
                "cam": cam,
                "median_z_m": z,
                "conf_pct_gt_0.1": w,
                "conf_pct_gt_0.5": float(rec["conf_pct_gt_0.5"]),
                "closeness": c,
            }
        )

    mean_closeness = weighted_closeness / total_weight  # weighted by valid coverage
    mean_z = float(np.mean([r["median_z_m"] for r in cam_records]))
    median_z = float(np.median([r["median_z_m"] for r in cam_records]))
    mean_conf = float(np.mean([r["conf_pct_gt_0.1"] for r in cam_records]))

    # multi-cam parallax bonus: at least 3 cams seeing close stuff is good
    multi_cam_bonus = min(near_cam_count / 7.0, 1.0)

    # Final score: closeness * coverage * multi-cam consensus.
    # All three factors in [0,1], product still in [0,1].
    score = mean_closeness * mean_conf * (0.5 + 0.5 * multi_cam_bonus)

    return {
        "score": score,
        "mean_closeness": mean_closeness,
        "mean_median_z_m": mean_z,
        "median_median_z_m": median_z,
        "mean_conf_pct_gt_0.1": mean_conf,
        "near_cam_count": near_cam_count,
        "multi_cam_bonus": multi_cam_bonus,
        "per_cam": cam_records,
    }


def render_anchor_viz(idx: int, anchor_score: dict, out_path: Path) -> None:
    per_cam = anchor_score["per_cam"]
    names = [c["cam"].replace("ring_", "") for c in per_cam]
    zs = [c["median_z_m"] for c in per_cam]
    confs = [c["conf_pct_gt_0.1"] for c in per_cam]
    closenesses = [c["closeness"] for c in per_cam]

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))

    ax = axes[0]
    bars = ax.bar(names, zs, color=["#3a7" if z < CLOSE_DEPTH_M else "#888" for z in zs])
    ax.axhline(CLOSE_DEPTH_M, color="r", linestyle="--", linewidth=1, label=f"close < {CLOSE_DEPTH_M:g} m")
    ax.set_ylabel("Pi3 local-Z median (m)")
    ax.set_title(f"Anchor {idx}: per-cam median depth")
    ax.tick_params(axis="x", rotation=40)
    ax.legend(fontsize=8)
    for b, z in zip(bars, zs):
        ax.text(b.get_x() + b.get_width() / 2, z + 0.1, f"{z:.1f}", ha="center", fontsize=7)

    ax = axes[1]
    ax.bar(names, confs, color="#369")
    ax.set_ylabel("conf_pct > 0.1 (valid surface fraction)")
    ax.set_title("Per-cam valid coverage")
    ax.set_ylim(0, 1.0)
    ax.tick_params(axis="x", rotation=40)

    ax = axes[2]
    ax.bar(names, closenesses, color="#c63")
    ax.set_ylabel("Closeness [0,1]")
    ax.set_title(
        f"Closeness signal (final score = {anchor_score['score']:.3f}, near-cams = {anchor_score['near_cam_count']}/7)"
    )
    ax.set_ylim(0, 1.05)
    ax.tick_params(axis="x", rotation=40)

    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close(fig)


def render_overall_ranking(rankings: List[dict], out_path: Path) -> None:
    sorted_r = sorted(rankings, key=lambda r: r["score"], reverse=True)
    names = [f"a{r['anchor_idx']:03d}" for r in sorted_r]
    scores = [r["score"] for r in sorted_r]
    closenesses = [r["mean_closeness"] for r in sorted_r]
    confs = [r["mean_conf_pct_gt_0.1"] for r in sorted_r]
    near_counts = [r["near_cam_count"] for r in sorted_r]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))

    ax = axes[0]
    colors = ["#3a7" if i < 3 else ("#c63" if i >= len(scores) - 2 else "#888") for i in range(len(scores))]
    ax.bar(names, scores, color=colors)
    ax.set_ylabel("Parallax score")
    ax.set_title("Anchor ranking (green = top-3, red = bottom-2)")
    for i, s in enumerate(scores):
        ax.text(i, s + 0.005, f"{s:.3f}", ha="center", fontsize=7)

    ax = axes[1]
    ax.bar(names, closenesses, color="#369")
    ax.set_ylabel("Mean closeness (weighted)")
    ax.set_title("Closeness component")
    for i, c in enumerate(closenesses):
        ax.text(i, c + 0.005, f"{c:.2f}", ha="center", fontsize=7)

    ax = axes[2]
    ax2 = ax.twinx()
    ax.bar(names, confs, color="#a52", alpha=0.55, label="mean conf>0.1")
    ax2.plot(names, near_counts, "ko-", label="near-cam count (/7)")
    ax.set_ylabel("Mean conf>0.1")
    ax2.set_ylabel("Near-cam count")
    ax.set_title("Coverage & multi-cam consensus")
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8)

    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close(fig)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rankings: List[dict] = []
    for idx in ANCHORS:
        summary = load_summary(idx)
        score = compute_anchor_score(summary)
        score["anchor_idx"] = idx
        rankings.append(score)
        render_anchor_viz(idx, score, OUT_DIR / f"anchor_{idx:03d}_summary.png")

    rankings_sorted = sorted(rankings, key=lambda r: r["score"], reverse=True)
    render_overall_ranking(rankings, OUT_DIR / "parallax_ranking.png")

    # Compose JSON output
    out_json = {
        "method": "summary-stat proxy (per-cam median depth + conf coverage)",
        "score_definition": (
            "score = weighted_mean_closeness(z; w=conf>0.1) * mean(conf>0.1) "
            "* (0.5 + 0.5 * near_cam_count/7)"
        ),
        "closeness_function": (
            f"linear ramp: 1.0 at z<={CLOSE_DEPTH_FLOOR_M} m, 0.0 at z>={CLOSE_DEPTH_M} m"
        ),
        "anchors_sorted_by_parallax_desc": [
            {
                "anchor_idx": r["anchor_idx"],
                "score": round(r["score"], 4),
                "mean_closeness": round(r["mean_closeness"], 4),
                "mean_conf_pct_gt_0.1": round(r["mean_conf_pct_gt_0.1"], 4),
                "near_cam_count": r["near_cam_count"],
                "mean_median_z_m": round(r["mean_median_z_m"], 3),
                "median_median_z_m": round(r["median_median_z_m"], 3),
            }
            for r in rankings_sorted
        ],
        "top_3": [r["anchor_idx"] for r in rankings_sorted[:3]],
        "bottom_2": [r["anchor_idx"] for r in rankings_sorted[-2:]],
        "per_anchor_per_cam": {
            f"anchor_{r['anchor_idx']}": {
                "score": round(r["score"], 4),
                "per_cam": [
                    {
                        "cam": c["cam"],
                        "median_z_m": round(c["median_z_m"], 3),
                        "conf_pct_gt_0.1": round(c["conf_pct_gt_0.1"], 4),
                        "conf_pct_gt_0.5": round(c["conf_pct_gt_0.5"], 4),
                        "closeness": round(c["closeness"], 4),
                    }
                    for c in r["per_cam"]
                ],
            }
            for r in rankings
        },
    }
    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(json.dumps(out_json, indent=2))

    print("=== Parallax ranking (desc) ===")
    for r in rankings_sorted:
        print(
            f"  anchor {r['anchor_idx']:>3}: score={r['score']:.4f}  "
            f"close={r['mean_closeness']:.3f}  conf>0.1={r['mean_conf_pct_gt_0.1']:.3f}  "
            f"near_cams={r['near_cam_count']}/7  mean_z={r['mean_median_z_m']:.2f}m"
        )
    print(f"\nWrote {DATA_OUT}")
    print(f"Wrote {len(ANCHORS)} per-anchor PNGs + parallax_ranking.png to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
