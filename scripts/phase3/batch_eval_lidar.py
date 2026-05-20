"""
P3.1b — Run eval_pi3_vs_lidar over each anchor_<idx> subdir from P3.1
multi-anchor Pi3 outputs. Aggregates mean +/- std.
"""
from __future__ import annotations

import argparse
import json
import sys
import subprocess
from pathlib import Path
from typing import Any

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--multi-anchor-dir", required=True,
                    help="Root with anchor_<idx>/ subdirs from P3.1")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--eval-script", required=True,
                    help="Path to scripts/phase2/eval_pi3_vs_lidar.py")
    ap.add_argument("--python", default="python")
    args = ap.parse_args()

    multi_root = Path(args.multi_anchor_dir)
    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    anchor_dirs = sorted([d for d in multi_root.glob("anchor_*") if d.is_dir()])
    if not anchor_dirs:
        print(f"no anchor_*/ subdirs found in {multi_root}", file=sys.stderr)
        return 1
    print(f"[batch] found {len(anchor_dirs)} anchors")

    per_anchor = {}
    for d in anchor_dirs:
        anchor_idx = int(d.name.split("_")[1])
        ev_out = out_root / d.name
        ev_out.mkdir(parents=True, exist_ok=True)
        cmd = [
            args.python, args.eval_script,
            "--log-dir", args.log_dir,
            "--pi3-dir", str(d),
            "--output-dir", str(ev_out),
        ]
        print(f"[batch] anchor {anchor_idx} -> {ev_out}")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  FAIL: {r.stderr[-500:]}")
            per_anchor[anchor_idx] = {"error": r.stderr[-500:]}
            continue
        metrics = json.loads((ev_out / "metrics_overall.json").read_text(encoding="utf-8"))
        per_anchor[anchor_idx] = metrics
        ov = metrics["overall"]
        print(f"  abs_rel={ov['abs_rel']:.3f} rmse={ov['rmse']:.2f}m "
              f"d1.25={ov['delta_1_25']:.3f} n={ov['n']}")

    # aggregate
    keys = ["abs_rel", "rmse", "rmse_log", "delta_1_25", "delta_1_25_2",
            "delta_1_25_3", "lidar_depth_mean", "pi3_depth_mean", "n"]
    agg_overall: dict[str, dict] = {k: {} for k in keys}
    for k in keys:
        vals = [pa["overall"][k] for pa in per_anchor.values() if "overall" in pa]
        if vals:
            agg_overall[k] = {
                "mean": round(float(np.mean(vals)), 4),
                "std": round(float(np.std(vals)), 4),
                "min": round(float(np.min(vals)), 4),
                "max": round(float(np.max(vals)), 4),
                "n_anchors": len(vals),
            }

    # per cam aggregate (mean over anchors)
    cams = list(next(iter(per_anchor.values()))["per_cam"].keys()) if per_anchor else []
    agg_per_cam: dict[str, dict] = {}
    for cam in cams:
        agg_per_cam[cam] = {}
        for k in keys:
            vals = [pa["per_cam"][cam][k] for pa in per_anchor.values()
                    if "per_cam" in pa and pa["per_cam"][cam].get(k) is not None]
            if vals:
                agg_per_cam[cam][k] = {
                    "mean": round(float(np.mean(vals)), 4),
                    "std": round(float(np.std(vals)), 4),
                }

    out = {
        "n_anchors": len(per_anchor),
        "anchor_indices": sorted(per_anchor.keys()),
        "agg_overall": agg_overall,
        "agg_per_cam": agg_per_cam,
        "per_anchor": per_anchor,
    }
    (out_root / "aggregate.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("\n=== aggregate across anchors ===")
    print(f"{'metric':<22}{'mean':>10}{'std':>10}{'min':>10}{'max':>10}")
    for k in ["abs_rel", "rmse", "delta_1_25", "delta_1_25_2", "lidar_depth_mean", "pi3_depth_mean", "n"]:
        a = agg_overall[k]
        if a:
            print(f"{k:<22}{a['mean']:>10.3f}{a['std']:>10.3f}{a['min']:>10.3f}{a['max']:>10.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
