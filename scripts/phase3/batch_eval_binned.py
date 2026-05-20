"""
P3.3 batch — depth-binned Pi3 vs LiDAR over all 10 anchors.

Wraps eval_pi3_lidar_binned.py per anchor_<idx>/. Aggregates per-bin mean ± std
across anchors to test whether the Pi3 depth-bias pattern (worsens with range)
is stable across the sequence or anchor-0-specific.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--multi-anchor-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--eval-script", required=True)
    ap.add_argument("--python", default="python")
    args = ap.parse_args()

    multi_root = Path(args.multi_anchor_dir)
    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    anchor_dirs = sorted([d for d in multi_root.glob("anchor_*") if d.is_dir()])
    print(f"[batch-binned] found {len(anchor_dirs)} anchors")

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
        print(f"[batch-binned] anchor {anchor_idx}")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  FAIL: {r.stderr[-400:]}")
            per_anchor[anchor_idx] = {"error": r.stderr[-400:]}
            continue
        m = json.loads((ev_out / "binned_metrics.json").read_text(encoding="utf-8"))
        per_anchor[anchor_idx] = m

    # aggregate per-bin
    if per_anchor:
        first_with_data = next((v for v in per_anchor.values() if "overall_binned" in v), None)
        if first_with_data:
            bins = [b["bin"] for b in first_with_data["overall_binned"]]
            agg_per_bin = {b: {"abs_rel": [], "rmse": [], "delta_1_25": [],
                                "lidar_mean": [], "pi3_mean": [], "bias_pct": [],
                                "n": []} for b in bins}
            for pa in per_anchor.values():
                if "overall_binned" not in pa:
                    continue
                for b in pa["overall_binned"]:
                    if b["abs_rel"] is None:
                        continue
                    agg = agg_per_bin[b["bin"]]
                    for k in agg.keys():
                        agg[k].append(b[k])

            agg_summary = {}
            for b, vals in agg_per_bin.items():
                agg_summary[b] = {
                    k: {
                        "mean": round(float(np.mean(v)), 4) if v else None,
                        "std": round(float(np.std(v)), 4) if v else None,
                        "n_anchors": len(v),
                    } for k, v in vals.items()
                }
        else:
            agg_summary = {}

        out = {
            "n_anchors_total": len(anchor_dirs),
            "n_anchors_with_data": sum(1 for v in per_anchor.values() if "overall_binned" in v),
            "anchor_indices": sorted(per_anchor.keys()),
            "agg_per_bin": agg_summary,
            "per_anchor": per_anchor,
        }
        (out_root / "aggregate_binned.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

        # pretty print
        print("\n=== per-bin aggregate across anchors ===")
        print(f"{'bin':<10}{'abs_rel μ±σ':>16}{'d<1.25 μ±σ':>16}{'bias% μ±σ':>16}{'lidar_μ μ±σ':>18}{'n_anchors':>12}")
        for b in bins:
            a = agg_summary[b]
            ar = a["abs_rel"]
            d1 = a["delta_1_25"]
            bp = a["bias_pct"]
            lm = a["lidar_mean"]
            na = ar["n_anchors"] if ar["mean"] is not None else 0
            if ar["mean"] is None:
                print(f"{b:<10}{'--':>16}{'--':>16}{'--':>16}{'--':>18}{na:>12}")
            else:
                ar_s = "%.3f+/-%.3f" % (ar["mean"], ar["std"])
                d1_s = "%.3f+/-%.3f" % (d1["mean"], d1["std"])
                bp_s = "%+.1f+/-%.1f" % (bp["mean"], bp["std"])
                lm_s = "%.2f+/-%.2f" % (lm["mean"], lm["std"])
                print(f"{b:<10}{ar_s:>16}{d1_s:>16}{bp_s:>16}{lm_s:>18}{na:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
