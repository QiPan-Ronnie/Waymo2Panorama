"""
T1-prep Phase B — Scan AV2 val split via AV2 SDK and pick 4 diverse UUIDs.

Runs on Colab (needs AV2 sensor data S3 access OR pre-downloaded val metadata).
Filters logs into 4 buckets:
  1. Urban daytime baseline    -> Miami, ped:veh ~0.3-0.7, brightness 100-180
  2. Highway / sparse           -> Pittsburgh, mean LiDAR depth > 25 m
  3. Dense intersection / peds  -> Detroit or DC, ped:veh > 1.0
  4. Night / low-light          -> DC, mean image brightness < 80

Writes `data/av2_val_picked.json` with the 4 chosen UUIDs + reasoning.

NOTE: Heavy metadata-only scan (no full Pi3 inference). Per-log cost ~5-10s
on Colab. Total scan: 150 val logs × ~7s = ~18 min wall-clock.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def scan_log_metadata(log_dir: Path, n_sample_frames: int = 3) -> dict | None:
    """Read calibration + sample frames for one val log. Return summary dict or None on error."""
    try:
        # city from city_SE3_egovehicle.feather first row
        city_path = log_dir / "city_SE3_egovehicle.feather"
        annotations_path = log_dir / "annotations.feather"
        cameras_dir = log_dir / "sensors" / "cameras" / "ring_front_center"
        lidar_dir = log_dir / "sensors" / "lidar"

        if not city_path.exists() or not cameras_dir.exists():
            return None

        # city — actually city is encoded in the egovehicle SE3 file? AV2 stores city in `city_name` in `meta` or via map (city-specific). Simplest proxy: sniff first cameras_dir
        # AV2 city metadata: use map_dir if available, else infer from log_dir parent (val/.../<UUID>)

        # frames count from front_center
        front_imgs = sorted(cameras_dir.glob("*.jpg"))
        n_frames = len(front_imgs)
        if n_frames < 100:
            return None

        # Sample brightness from middle of log
        sample_indices = np.linspace(n_frames // 4, 3 * n_frames // 4, n_sample_frames, dtype=int)
        brightness_samples = []
        for i in sample_indices:
            try:
                from PIL import Image
                im = np.asarray(Image.open(front_imgs[i]).convert("L"))  # grayscale
                brightness_samples.append(float(im.mean()))
            except Exception:
                pass
        mean_brightness = float(np.mean(brightness_samples)) if brightness_samples else None

        # ped:veh ratio from annotations (categories)
        ped_veh_ratio = None
        if annotations_path.exists():
            ann = pd.read_feather(annotations_path)
            cats = ann["category"].value_counts() if "category" in ann.columns else None
            if cats is not None:
                veh = sum(cats.get(k, 0) for k in ["REGULAR_VEHICLE", "LARGE_VEHICLE", "BUS", "TRUCK", "TRUCK_CAB", "BOX_TRUCK", "VEHICULAR_TRAILER"])
                peds = sum(cats.get(k, 0) for k in ["PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST"])
                if veh > 0:
                    ped_veh_ratio = float(peds / veh)

        # Mean LiDAR depth from middle sweep
        mean_lidar_depth = None
        if lidar_dir.exists():
            lidar_files = sorted(lidar_dir.glob("*.feather"))
            if lidar_files:
                mid = lidar_files[len(lidar_files) // 2]
                try:
                    sweep = pd.read_feather(mid)
                    if {"x", "y", "z"}.issubset(sweep.columns):
                        pts = sweep[["x", "y", "z"]].to_numpy(dtype=np.float64)
                        # ego-frame radial distance
                        r = np.linalg.norm(pts, axis=1)
                        mean_lidar_depth = float(r.mean())
                except Exception:
                    pass

        return {
            "uuid": log_dir.name,
            "n_frames": n_frames,
            "mean_brightness": mean_brightness,
            "ped_veh_ratio": ped_veh_ratio,
            "mean_lidar_depth_m": mean_lidar_depth,
        }
    except Exception as e:
        return {"uuid": log_dir.name, "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--val-root", required=True,
                    help="Path to AV2 val split root (contains <UUID>/ subdirs)")
    ap.add_argument("--output", required=True,
                    help="Path to write data/av2_val_picked.json")
    ap.add_argument("--exclude-uuid", action="append", default=[],
                    help="UUIDs to exclude (e.g., the one we already have)")
    ap.add_argument("--limit-scan", type=int, default=None,
                    help="Limit number of val logs to scan (for testing)")
    args = ap.parse_args()

    val_root = Path(args.val_root)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log_dirs = sorted([d for d in val_root.iterdir() if d.is_dir() and d.name not in args.exclude_uuid])
    if args.limit_scan:
        log_dirs = log_dirs[: args.limit_scan]
    print(f"[scan] {len(log_dirs)} val logs to scan")

    t0 = time.time()
    summaries: list[dict] = []
    for i, d in enumerate(log_dirs):
        s = scan_log_metadata(d)
        if s is not None:
            summaries.append(s)
        if (i + 1) % 10 == 0:
            print(f"[scan] {i+1}/{len(log_dirs)} done, {time.time()-t0:.0f}s elapsed")
    print(f"[scan] done: {len(summaries)} ok logs in {time.time()-t0:.0f}s")

    # Filter into buckets
    # NOTE: city is not directly in feather; we'd need map_dir or external mapping.
    # Workaround: report all summaries, let user (or this script) pick by combo heuristic.

    def pick_best(filter_fn, sort_key, reverse: bool = False, n: int = 1):
        candidates = [s for s in summaries if filter_fn(s)]
        candidates.sort(key=sort_key, reverse=reverse)
        return candidates[:n]

    # Bucket 1: urban daytime baseline — peds 5+, brightness 100-180, mid frame count
    b1 = pick_best(
        lambda s: s.get("mean_brightness") and 100 <= s["mean_brightness"] <= 180 and s.get("n_frames", 0) > 200 and (s.get("ped_veh_ratio") or 0) > 0.05,
        sort_key=lambda s: abs(s.get("ped_veh_ratio", 0) - 0.4),
    )
    # Bucket 2: highway / sparse — long LiDAR mean, low ped ratio
    b2 = pick_best(
        lambda s: s.get("mean_lidar_depth_m") and s["mean_lidar_depth_m"] > 20 and (s.get("ped_veh_ratio") or 1) < 0.05,
        sort_key=lambda s: s.get("mean_lidar_depth_m", 0),
        reverse=True,
    )
    # Bucket 3: dense intersection — high ped ratio
    b3 = pick_best(
        lambda s: s.get("ped_veh_ratio") and s["ped_veh_ratio"] > 0.5,
        sort_key=lambda s: s.get("ped_veh_ratio", 0),
        reverse=True,
    )
    # Bucket 4: night / low-light — low brightness
    b4 = pick_best(
        lambda s: s.get("mean_brightness") and s["mean_brightness"] < 80,
        sort_key=lambda s: s.get("mean_brightness", 200),
    )

    picks = {
        "bucket_1_urban_daytime": b1[0] if b1 else None,
        "bucket_2_highway_sparse": b2[0] if b2 else None,
        "bucket_3_dense_intersection": b3[0] if b3 else None,
        "bucket_4_night_lowlight": b4[0] if b4 else None,
        "all_summaries": summaries,
        "scan_seconds": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(picks, indent=2), encoding="utf-8")
    print(f"[scan] picks written to {out_path}")
    print(json.dumps({k: (v["uuid"] if isinstance(v, dict) and "uuid" in v else None) for k, v in picks.items() if not k.startswith("all_") and not k.startswith("scan_")}, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
