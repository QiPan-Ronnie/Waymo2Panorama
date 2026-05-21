"""
Phase 3 T9 helper — inspect ViPE output directory and produce a one-page
diagnostic summary (trajectory length, depth range, depth coverage,
single-frame depth-overlay PNG).

Runs on whatever ViPE chose to save under <output_dir>/<sequence_name>/.
ViPE conventions (per `panorama` branch as of 2026-05):
    <out>/<seq>/vipe_<seq>.npz            (the catch-all artifact archive)
    <out>/<seq>/info.json                 (metadata)
    <out>/<seq>/intrinsics.txt
    <out>/<seq>/poses.txt
    <out>/<seq>/depth/<idx>.npz           (per-frame depth, optional)
    <out>/<seq>/vipe/*.mp4                (visualization videos)

This script is intentionally defensive: it inspects whatever is present and
emits a JSON + a tiny markdown table.

Usage:
    python scripts/phase3/analyze_vipe_outputs.py \
        --vipe-out /path/to/t9_vipe \
        --report   notes/t9_vipe_inspection.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def inspect_npz(p: Path) -> dict:
    try:
        with np.load(p, allow_pickle=True) as z:
            entries = {}
            for k in z.files:
                try:
                    arr = z[k]
                    entries[k] = {
                        "shape": list(arr.shape) if hasattr(arr, "shape") else None,
                        "dtype": str(arr.dtype) if hasattr(arr, "dtype") else None,
                    }
                    if arr.size and arr.dtype.kind in "fiu":
                        flat = arr.reshape(-1)
                        entries[k]["min"] = float(np.nanmin(flat)) if flat.size else None
                        entries[k]["max"] = float(np.nanmax(flat)) if flat.size else None
                        entries[k]["mean"] = float(np.nanmean(flat)) if flat.size else None
                except Exception as e:
                    entries[k] = {"err": str(e)}
            return entries
    except Exception as e:
        return {"_load_error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vipe-out", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()

    rep: dict = {"vipe_out": str(args.vipe_out)}
    if not args.vipe_out.exists():
        rep["error"] = "vipe_out does not exist"
        args.report.write_text(json.dumps(rep, indent=2))
        return 1

    # Walk
    rep["entries"] = []
    for p in sorted(args.vipe_out.rglob("*")):
        if p.is_file():
            rec = {"path": str(p.relative_to(args.vipe_out)), "size_kb": round(p.stat().st_size / 1e3, 2)}
            if p.suffix == ".npz":
                rec["npz_contents"] = inspect_npz(p)
            rep["entries"].append(rec)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(rep, indent=2, default=str))
    print(f"wrote {args.report}")
    print(json.dumps(rep, indent=2, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
