"""
T1 Phase B helper — enumerate all AV2 val-split UUIDs from S3 (no download).

The original `find_av2_val_candidates.py` requires `--val-root` to be a local
directory of UUID subdirs, which means it can only score logs we've already
downloaded. To pick 4 diverse logs from the full 150-log val split *without*
downloading 1+ TB blindly, this helper:

  1. Lists all val UUIDs via `s5cmd --no-sign-request ls s3://argoverse/...`
     (CPU-only, network-only, ~30s on Colab).
  2. Optionally, for each UUID, downloads just `annotations.feather` (a few MB)
     to compute ped:vehicle ratio per log.
  3. Optionally, computes total S3 size per UUID (proxy for log length /
     sensor density).
  4. Writes `data/av2_val_uuid_index.json` with one entry per log.

A downstream picker can then read this index, score logs, pick 4, and only
then trigger full s5cmd downloads of the 4 picked logs (~32 GB total instead
of ~1.5 TB blind).

Usage on Colab:
    pip install -q s5cmd  # or `apt-get install -y s5cmd` if available
    python scripts/phase3/list_av2_val_uuids.py \
        --output /content/drive/MyDrive/koi_waymo2pano_colab/data/av2_val_uuid_index.json \
        --fetch-annotations    # optional, adds ~10-15 min wall-clock

Without --fetch-annotations: ~30s for UUID list + S3 sizes.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


S3_VAL_PREFIX = "s3://argoverse/datasets/av2/sensor/val/"


def run_s5cmd(args: list[str], timeout: int = 120) -> str:
    """Run s5cmd and return stdout, or raise on failure."""
    cmd = ["s5cmd", "--no-sign-request", *args]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"s5cmd failed (rc={res.returncode}):\n"
                           f"  cmd: {' '.join(cmd)}\n"
                           f"  stderr: {res.stderr[-500:]}")
    return res.stdout


def list_uuids() -> list[str]:
    """Enumerate val UUIDs from S3 via `s5cmd ls`."""
    out = run_s5cmd(["ls", S3_VAL_PREFIX])
    uuids: list[str] = []
    for line in out.splitlines():
        # s5cmd ls output format on dirs: "         DIR  <uuid>/"
        m = re.search(r"DIR\s+([0-9a-f-]{36})/?$", line)
        if m:
            uuids.append(m.group(1))
    return sorted(uuids)


def get_uuid_size_bytes(uuid: str) -> int | None:
    """Sum total bytes for one UUID's S3 prefix (recursive ls)."""
    prefix = f"{S3_VAL_PREFIX}{uuid}/"
    try:
        out = run_s5cmd(["du", prefix], timeout=120)
        # s5cmd du output: "<bytes> objects ... <total_bytes>"
        # We look for a trailing integer that's the total bytes.
        for line in reversed(out.splitlines()):
            m = re.search(r"\b(\d{8,})\b", line)
            if m:
                return int(m.group(1))
        return None
    except Exception as e:
        print(f"  warn: du failed on {uuid}: {e}", file=sys.stderr)
        return None


def fetch_annotations_and_score(uuid: str, scratch: Path) -> dict | None:
    """Download annotations.feather for one UUID and compute ped:veh ratio."""
    import pandas as pd  # only imported when needed

    local = scratch / f"{uuid}_annotations.feather"
    if local.exists():
        local.unlink()
    src = f"{S3_VAL_PREFIX}{uuid}/annotations.feather"
    try:
        run_s5cmd(["cp", src, str(local)], timeout=120)
    except RuntimeError as e:
        print(f"  warn: annotations.feather missing for {uuid}: {e}",
              file=sys.stderr)
        return None
    if not local.exists():
        return None
    try:
        ann = pd.read_feather(local)
    except Exception as e:
        print(f"  warn: read_feather failed for {uuid}: {e}", file=sys.stderr)
        return None
    finally:
        # keep the file around for now; caller cleans up at end
        pass

    if "category" not in ann.columns:
        return {"uuid": uuid, "n_annotations": int(len(ann)), "ped_veh_ratio": None}

    cats = ann["category"].value_counts().to_dict()
    veh = sum(cats.get(k, 0) for k in (
        "REGULAR_VEHICLE", "LARGE_VEHICLE", "BUS", "TRUCK",
        "TRUCK_CAB", "BOX_TRUCK", "VEHICULAR_TRAILER",
    ))
    peds = sum(cats.get(k, 0) for k in (
        "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST",
    ))
    ratio = float(peds / veh) if veh > 0 else None
    return {
        "uuid": uuid,
        "n_annotations": int(len(ann)),
        "ped_count": int(peds),
        "veh_count": int(veh),
        "ped_veh_ratio": ratio,
        "category_top5": dict(sorted(cats.items(), key=lambda kv: -kv[1])[:5]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", required=True,
                    help="Path to write the index JSON.")
    ap.add_argument("--fetch-annotations", action="store_true",
                    help="Also download annotations.feather per UUID to score ped:veh ratio "
                         "(adds ~10-15 min total, ~few MB per UUID).")
    ap.add_argument("--fetch-sizes", action="store_true",
                    help="Also compute total S3 size per UUID (adds ~1-2 min via `s5cmd du`).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only process the first N UUIDs (for smoke tests).")
    args = ap.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[list_av2_val] listing UUIDs from {S3_VAL_PREFIX} ...")
    t0 = time.time()
    uuids = list_uuids()
    print(f"[list_av2_val] {len(uuids)} UUIDs in {time.time()-t0:.1f}s")
    if not uuids:
        print("[list_av2_val] ERROR: 0 UUIDs found, aborting.", file=sys.stderr)
        return 2

    if args.limit:
        uuids = uuids[: args.limit]
        print(f"[list_av2_val] --limit {args.limit} → processing {len(uuids)} of {len(uuids)} UUIDs")

    entries: list[dict] = []
    with tempfile.TemporaryDirectory() as scratch:
        scratch_path = Path(scratch)
        for i, uuid in enumerate(uuids):
            entry: dict = {"uuid": uuid}
            if args.fetch_sizes:
                sz = get_uuid_size_bytes(uuid)
                entry["size_bytes"] = sz
            if args.fetch_annotations:
                scored = fetch_annotations_and_score(uuid, scratch_path)
                if scored:
                    entry.update({k: v for k, v in scored.items() if k != "uuid"})
            entries.append(entry)
            if (i + 1) % 25 == 0 or (i + 1) == len(uuids):
                print(f"[list_av2_val] {i+1}/{len(uuids)} processed "
                      f"({time.time()-t0:.0f}s elapsed)")

    out_payload = {
        "s3_prefix": S3_VAL_PREFIX,
        "n_uuids": len(uuids),
        "fetch_annotations": args.fetch_annotations,
        "fetch_sizes": args.fetch_sizes,
        "wall_seconds": round(time.time() - t0, 1),
        "uuids": entries,
    }
    out_path.write_text(json.dumps(out_payload, indent=2), encoding="utf-8")
    print(f"[list_av2_val] wrote {out_path} ({out_path.stat().st_size} bytes)")
    print(f"[list_av2_val] DONE: {len(uuids)} UUIDs in {time.time()-t0:.0f}s total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
