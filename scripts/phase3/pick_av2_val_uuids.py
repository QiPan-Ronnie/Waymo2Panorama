"""
T1 Phase B picker — from av2_val_uuid_index.json, pick 4 diverse UUIDs.

Strategy: stratify by ped:vehicle ratio into 4 buckets, take mid-bucket
representative (most extreme for the highest-density bucket). Excludes
UUIDs we already have downloaded.

Usage:
    python scripts/phase3/pick_av2_val_uuids.py \\
        --index data/av2_val_uuid_index.json \\
        --output data/av2_val_picked.json \\
        --exclude 02a00399-3857-444e-8db3-a8f58489c394
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True, help="av2_val_uuid_index.json from list_av2_val_uuids.py")
    ap.add_argument("--output", required=True, help="Where to write the picked-4 JSON")
    ap.add_argument("--exclude", action="append", default=[],
                    help="UUIDs to exclude (already downloaded)")
    args = ap.parse_args()

    d = json.loads(Path(args.index).read_text(encoding="utf-8"))
    uuids = d["uuids"]
    exclude = set(args.exclude)

    candidates = [u for u in uuids if u["uuid"] not in exclude
                  and u.get("ped_veh_ratio") is not None]

    b1 = sorted([u for u in candidates if u["ped_veh_ratio"] < 0.05],
                key=lambda u: u["ped_veh_ratio"])
    b2 = sorted([u for u in candidates if 0.10 <= u["ped_veh_ratio"] < 0.25],
                key=lambda u: u["ped_veh_ratio"])
    b3 = sorted([u for u in candidates if 0.50 <= u["ped_veh_ratio"] < 1.00],
                key=lambda u: u["ped_veh_ratio"])
    b4 = sorted([u for u in candidates if u["ped_veh_ratio"] >= 1.5],
                key=lambda u: -u["ped_veh_ratio"])

    def pick_mid(b):
        return b[len(b) // 2] if b else None

    picks = {
        "bucket_1_highway_like":  pick_mid(b1),
        "bucket_2_low_med_ped":   pick_mid(b2),
        "bucket_3_med_high_ped":  pick_mid(b3),
        "bucket_4_very_high_ped": b4[0] if b4 else None,
    }

    result = {
        "picked_at": "2026-05-21",
        "criteria": ("Stratified by ped:vehicle ratio: "
                     "bucket 1 highway-like (<0.05), "
                     "2 low-med ([0.10,0.25)), "
                     "3 med-high ([0.50,1.00)), "
                     "4 very-high (>=1.5)"),
        "excluded": sorted(exclude),
        "picks": picks,
        "bucket_sizes": {"b1": len(b1), "b2": len(b2), "b3": len(b3), "b4": len(b4)},
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("=== Picked 4 UUIDs ===")
    for k, v in picks.items():
        if v is None:
            print(f"  {k}: (no candidate)")
        else:
            print(f"  {k}: {v['uuid']} (ped:veh={v['ped_veh_ratio']:.3f}, "
                  f"peds={v.get('ped_count')}, vehs={v.get('veh_count')}, "
                  f"n_ann={v.get('n_annotations')})")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
