"""
Count YOLO detections directly on rendered ERP panoramas.

For each anchor's panorama: run YOLO, count car/person/etc. detections.
If multiband produces doubled-feature ghosts, the YOLO count on multiband
should be HIGHER than on hard_hdr_of (which eliminates the double).

This is a direct quantitative test of ghost elimination, complementing the
seam-gap luminance metric.

Usage:
    python scripts/phase3/score_panorama_yolo.py \
        --pano-dirs \
            multiband=/content/.../multiband_baseline_v1 \
            hard_hdr_of_v1=/content/.../full_pipeline_v1 \
            hard_hdr_of_v2=/content/.../full_pipeline_v2 \
        --output-json /content/.../panorama_yolo_scores.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


# COCO classes we count as "vehicles/people" — should be roughly equal between
# methods on the SAME anchor; ghost-doubling should INCREASE multiband counts
COUNT_CLASSES = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}


def score_one_panorama(yolo_model, pano_path: Path, conf: float = 0.3) -> dict:
    img = np.array(Image.open(pano_path).convert("RGB"))
    results = yolo_model(img, conf=conf, verbose=False)
    res = results[0]
    boxes = res.boxes
    counts: dict[str, int] = {n: 0 for n in COUNT_CLASSES.values()}
    counts["all_count"] = 0
    if boxes is not None and len(boxes) > 0:
        cls = boxes.cls.cpu().numpy().astype(int)
        for c in cls:
            if int(c) in COUNT_CLASSES:
                counts[COUNT_CLASSES[int(c)]] += 1
                counts["all_count"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pano-dirs", type=str, nargs="+", required=True,
                    help="space-list of name=path, e.g. multiband=/foo hard_hdr_of=/bar")
    ap.add_argument("--output-json", type=Path, required=True)
    ap.add_argument("--conf", type=float, default=0.3, help="YOLO confidence threshold")
    ap.add_argument("--model", type=str, default="yolov8n.pt",
                    help="YOLO model name or path (downloads to /content)")
    args = ap.parse_args()

    method_dirs: dict[str, Path] = {}
    for spec in args.pano_dirs:
        name, path = spec.split("=", 1)
        method_dirs[name] = Path(path)

    print("loading YOLO...")
    from ultralytics import YOLO
    yolo = YOLO(args.model)

    # Find common anchor names across methods (by log + anchor)
    # Dir structure: pano-dir/log_short/anchor_NNNN.png
    method_anchors: dict[str, set[tuple[str, str]]] = {}
    for method, root in method_dirs.items():
        method_anchors[method] = set()
        for log_dir in sorted(root.iterdir()):
            if not log_dir.is_dir(): continue
            for png in sorted(log_dir.glob("anchor_*.png")):
                method_anchors[method].add((log_dir.name, png.name))

    common = set.intersection(*method_anchors.values())
    print(f"common anchors across all methods: {len(common)}")

    per_anchor: dict[str, dict] = {}
    method_totals: dict[str, dict[str, int]] = {m: {} for m in method_dirs.keys()}
    for log_short, png_name in sorted(common):
        key = f"{log_short}/{png_name}"
        per_anchor[key] = {}
        for method, root in method_dirs.items():
            pano = root / log_short / png_name
            scores = score_one_panorama(yolo, pano, conf=args.conf)
            per_anchor[key][method] = scores
            for cls, n in scores.items():
                method_totals[method][cls] = method_totals[method].get(cls, 0) + n
        # print short progress
        ctr_str = ", ".join(f"{m}={per_anchor[key][m]['all_count']}" for m in method_dirs.keys())
        print(f"  {key}: {ctr_str}")

    # Aggregate: method-level totals + per-method mean detections per panorama
    n_common = len(common)
    aggregate: dict[str, dict] = {}
    for m in method_dirs.keys():
        agg = {f"total_{k}": v for k, v in method_totals[m].items()}
        agg["mean_all_per_panorama"] = round(method_totals[m].get("all_count", 0) / max(n_common, 1), 2)
        aggregate[m] = agg

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "n_common_anchors": n_common,
        "conf_threshold": args.conf,
        "per_anchor": per_anchor,
        "aggregate": aggregate,
    }
    with open(args.output_json, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nAGGREGATE (mean detections per panorama):")
    for m in method_dirs.keys():
        print(f"  {m:<20s}: {aggregate[m]['mean_all_per_panorama']}")
    print(f"saved {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
