"""
P3.1b — Run eval_cycle_consistency over each anchor_<idx> subdir from P3.1.
Aggregates mean +/- std of L1 vs L3 PSNR / SSIM / MAE / coverage.
"""
from __future__ import annotations

import argparse
import json
import sys
import subprocess
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--multi-anchor-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--eval-script", required=True,
                    help="Path to scripts/phase2/eval_cycle_consistency.py")
    ap.add_argument("--python", default="python")
    args = ap.parse_args()

    multi_root = Path(args.multi_anchor_dir)
    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    anchor_dirs = sorted([d for d in multi_root.glob("anchor_*") if d.is_dir()])
    print(f"[batch-cycle] found {len(anchor_dirs)} anchors")

    per_anchor = {}
    for d in anchor_dirs:
        anchor_idx = int(d.name.split("_")[1])
        ev_out = out_root / d.name
        ev_out.mkdir(parents=True, exist_ok=True)
        cmd = [
            args.python, args.eval_script,
            "--pi3-dir", str(d),
            "--output-dir", str(ev_out),
        ]
        print(f"[batch-cycle] anchor {anchor_idx}")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  FAIL: {r.stderr[-500:]}")
            per_anchor[anchor_idx] = {"error": r.stderr[-500:]}
            continue
        metrics_file = ev_out / "cycle_consistency.json"
        if not metrics_file.exists():
            print(f"  WARN: no cycle_consistency.json")
            per_anchor[anchor_idx] = {"error": "no output json"}
            continue
        metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
        per_anchor[anchor_idx] = metrics
        if "mean" in metrics:
            m = metrics["mean"]
            print(f"  L1 PSNR={m.get('PSNR_L1', 'NA')} L3 PSNR={m.get('PSNR_L3', 'NA')} "
                  f"d={m.get('PSNR_delta_L3_minus_L1', 'NA')}")

    out = {
        "n_anchors": len(per_anchor),
        "anchor_indices": sorted(per_anchor.keys()),
        "per_anchor": per_anchor,
    }
    (out_root / "aggregate.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[batch-cycle] wrote aggregate to {out_root / 'aggregate.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
