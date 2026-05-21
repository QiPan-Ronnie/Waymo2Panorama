"""
T14b aggregator — walks <root>/anchor_<idx>/cycle/cycle_ipm.json files,
aggregates ground-only ΔPSNR and full-image ΔPSNR across anchors.

Standalone script (no nested f-string in bash `python -c`).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True,
                    help="Path containing anchor_<idx>/cycle/cycle_ipm.json subdirs")
    ap.add_argument("--output", required=True,
                    help="Path to write aggregate JSON")
    args = ap.parse_args()

    root = Path(args.root)
    anchors = sorted([d for d in root.glob("anchor_*") if d.is_dir()])
    print(f"[agg] root={root}, found {len(anchors)} anchor dirs")

    rows = []
    ground = []
    full = []
    for a in anchors:
        j = a / "cycle" / "cycle_ipm.json"
        if not j.exists():
            print(f"  {a.name}: NO cycle_ipm.json")
            continue
        try:
            m = json.loads(j.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  {a.name}: JSON parse error {e}")
            continue

        # find ground / full delta numbers — schema may vary, try multiple keys
        gd = None
        fd = None
        # try 'overall' dict
        ov = m.get("overall") or m.get("mean") or {}
        for k in ("PSNR_delta_groundOnly", "ground_delta_psnr", "ground_only_delta_psnr",
                  "ground_dPSNR", "GROUND_dPSNR", "ground_only_PSNR_delta"):
            if k in ov:
                gd = float(ov[k])
                break
        for k in ("PSNR_delta_full", "fullimage_delta_psnr", "full_image_delta_psnr",
                  "full_dPSNR", "ALL_dPSNR", "full_PSNR_delta"):
            if k in ov:
                fd = float(ov[k])
                break

        rows.append({"anchor": a.name, "ground_dPSNR": gd, "full_dPSNR": fd, "raw_overall": ov})
        if gd is not None and np.isfinite(gd):
            ground.append(gd)
        if fd is not None and np.isfinite(fd):
            full.append(fd)
        print(f"  {a.name}: ground_d={gd}  full_d={fd}")

    out = {
        "n_anchors": len(anchors),
        "n_with_ground_d": len(ground),
        "n_with_full_d": len(full),
        "ground_mean": float(np.mean(ground)) if ground else None,
        "ground_std": float(np.std(ground)) if ground else None,
        "ground_min": float(np.min(ground)) if ground else None,
        "ground_max": float(np.max(ground)) if ground else None,
        "full_mean": float(np.mean(full)) if full else None,
        "full_std": float(np.std(full)) if full else None,
        "per_anchor": rows,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print()
    print(f"[agg] wrote {out_path}")
    print(json.dumps({k: v for k, v in out.items() if k != "per_anchor"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
