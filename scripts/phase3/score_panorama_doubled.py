"""
Count "doubled-detection" pairs on rendered ERP panoramas.

This is the second YOLO-based metric, complementing score_panorama_yolo.py.
That script counts TOTAL detections per panorama, which measures DETECTABILITY
(higher when distant objects aren't blurred). It does NOT cleanly isolate
ghost-doubling: hard_hdr_of actually scored HIGHER (1.52 vs 1.06 multiband)
because hard_select preserves distant objects that multiband blends away.

This script targets the actual ghost signature: when multiband doubles a
vehicle near a seam, YOLO should fire TWO overlapping boxes of the SAME class
covering the original + ghost. hard_hdr_of avoids the double-image, so these
overlapping pairs should disappear.

Metric (per panorama):
    n_doubled_pairs = | { (i, j) : i < j,
                          cls_i == cls_j,
                          IOU_LO < IoU(box_i, box_j) < IOU_HI } |

We use:
    IOU_LO = 0.1   — below this, boxes are essentially disjoint, just two
                     real objects of the same class in the scene
    IOU_HI = 0.7   — above this, boxes are near-duplicate (NMS leftover or
                     same physical object), not the ghost pattern
    0.1 < IoU < 0.7 — the sweet spot for "two boxes substantially overlap
                      but neither subsumes the other" — exactly what a ghost
                      doubling produces (object + ~5-30 px shifted copy)

Same CLI shape as score_panorama_yolo.py. Reports BOTH n_detections AND
n_doubled_pairs per panorama, plus aggregates per method.

Usage:
    python scripts/phase3/score_panorama_doubled.py \
        --pano-dirs \
            multiband=/content/.../multiband_baseline_v1 \
            hard_hdr_of=/content/.../full_pipeline_v1 \
        --output-json /content/.../panorama_doubled_scores.json

Expected outcome: multiband should have higher mean_doubled_per_panorama than
hard_hdr_of, IF the metric correctly captures the doubled-feature ghost.
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image


# Same COCO subset as score_panorama_yolo.py — restrict to road actors that
# are the primary ghosting targets and avoid spurious "tie/handbag" style
# noise classes.
COUNT_CLASSES = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

# Ghost-overlap window. See module docstring for rationale.
IOU_LO = 0.1
IOU_HI = 0.7


def iou_xyxy(a: np.ndarray, b: np.ndarray) -> float:
    """IoU of two boxes in xyxy format."""
    inter_x1 = max(a[0], b[0])
    inter_y1 = max(a[1], b[1])
    inter_x2 = min(a[2], b[2])
    inter_y2 = min(a[3], b[3])
    iw = max(0.0, inter_x2 - inter_x1)
    ih = max(0.0, inter_y2 - inter_y1)
    inter = iw * ih
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    if union <= 0.0:
        return 0.0
    return float(inter / union)


def score_one_panorama(
    yolo_model,
    pano_path: Path,
    conf: float = 0.3,
    iou_lo: float = IOU_LO,
    iou_hi: float = IOU_HI,
) -> dict:
    """Return per-class detection counts AND doubled-pair count for one ERP."""
    img = np.array(Image.open(pano_path).convert("RGB"))
    results = yolo_model(img, conf=conf, verbose=False)
    res = results[0]
    boxes = res.boxes

    out: dict = {n: 0 for n in COUNT_CLASSES.values()}
    out["all_count"] = 0
    out["n_doubled_pairs"] = 0
    # Per-class breakdown of doubled pairs is useful for debugging which class
    # carries the ghost signal (cars are by far the dominant case in Waymo).
    out["doubled_pairs_by_class"] = {n: 0 for n in COUNT_CLASSES.values()}

    if boxes is None or len(boxes) == 0:
        return out

    cls = boxes.cls.cpu().numpy().astype(int)
    xyxy = boxes.xyxy.cpu().numpy().astype(float)

    # Keep only the classes we care about
    keep_idx = [i for i, c in enumerate(cls) if int(c) in COUNT_CLASSES]
    for i in keep_idx:
        c = int(cls[i])
        out[COUNT_CLASSES[c]] += 1
        out["all_count"] += 1

    # Doubled-pair count: same class, iou_lo < IoU < iou_hi.
    for i, j in combinations(keep_idx, 2):
        if int(cls[i]) != int(cls[j]):
            continue
        iou = iou_xyxy(xyxy[i], xyxy[j])
        if iou_lo < iou < iou_hi:
            out["n_doubled_pairs"] += 1
            out["doubled_pairs_by_class"][COUNT_CLASSES[int(cls[i])]] += 1

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pano-dirs", type=str, nargs="+", required=True,
                    help="space-list of name=path, e.g. multiband=/foo hard_hdr_of=/bar")
    ap.add_argument("--output-json", type=Path, required=True)
    ap.add_argument("--conf", type=float, default=0.3, help="YOLO confidence threshold")
    ap.add_argument("--model", type=str, default="yolov8n.pt",
                    help="YOLO model name or path (downloads to /content)")
    ap.add_argument("--iou-lo", type=float, default=IOU_LO,
                    help=f"lower IoU bound for 'doubled pair' (default {IOU_LO})")
    ap.add_argument("--iou-hi", type=float, default=IOU_HI,
                    help=f"upper IoU bound for 'doubled pair' (default {IOU_HI})")
    args = ap.parse_args()

    iou_lo = float(args.iou_lo)
    iou_hi = float(args.iou_hi)

    method_dirs: dict[str, Path] = {}
    for spec in args.pano_dirs:
        name, path = spec.split("=", 1)
        method_dirs[name] = Path(path)

    print("loading YOLO...")
    from ultralytics import YOLO
    yolo = YOLO(args.model)

    # Find common anchor names across methods (by log + anchor).
    # Dir structure: pano-dir/log_short/anchor_NNNN.png
    method_anchors: dict[str, set[tuple[str, str]]] = {}
    for method, root in method_dirs.items():
        method_anchors[method] = set()
        for log_dir in sorted(root.iterdir()):
            if not log_dir.is_dir():
                continue
            for png in sorted(log_dir.glob("anchor_*.png")):
                method_anchors[method].add((log_dir.name, png.name))

    common = set.intersection(*method_anchors.values())
    print(f"common anchors across all methods: {len(common)}")
    print(f"doubled-pair window: {iou_lo:.2f} < IoU < {iou_hi:.2f}")

    per_anchor: dict[str, dict] = {}
    method_totals: dict[str, dict[str, int]] = {m: {} for m in method_dirs.keys()}

    for log_short, png_name in sorted(common):
        key = f"{log_short}/{png_name}"
        per_anchor[key] = {}
        for method, root in method_dirs.items():
            pano = root / log_short / png_name
            scores = score_one_panorama(
                yolo, pano, conf=args.conf, iou_lo=iou_lo, iou_hi=iou_hi,
            )
            per_anchor[key][method] = scores

            # Aggregate flat scalars (skip the nested doubled_pairs_by_class
            # dict; we re-derive per-class totals from per-anchor records).
            for k, v in scores.items():
                if isinstance(v, dict):
                    continue
                method_totals[method][k] = method_totals[method].get(k, 0) + v

        ctr_str = ", ".join(
            f"{m}=det{per_anchor[key][m]['all_count']}/dbl{per_anchor[key][m]['n_doubled_pairs']}"
            for m in method_dirs.keys()
        )
        print(f"  {key}: {ctr_str}")

    # Aggregate: method-level totals + per-method means per panorama.
    n_common = len(common)
    aggregate: dict[str, dict] = {}
    for m in method_dirs.keys():
        agg = {f"total_{k}": v for k, v in method_totals[m].items()}
        agg["mean_all_per_panorama"] = round(
            method_totals[m].get("all_count", 0) / max(n_common, 1), 3
        )
        agg["mean_doubled_per_panorama"] = round(
            method_totals[m].get("n_doubled_pairs", 0) / max(n_common, 1), 3
        )
        # Count of panoramas with >=1 doubled pair — a robust binary signal
        # that complements the mean (mean can be inflated by a single
        # heavily-ghosted frame).
        n_with_doubled = sum(
            1 for k in per_anchor
            if per_anchor[k].get(m, {}).get("n_doubled_pairs", 0) > 0
        )
        agg["n_panoramas_with_doubled"] = n_with_doubled
        agg["frac_panoramas_with_doubled"] = round(n_with_doubled / max(n_common, 1), 3)
        aggregate[m] = agg

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "n_common_anchors": n_common,
        "conf_threshold": args.conf,
        "iou_lo": iou_lo,
        "iou_hi": iou_hi,
        "metric": "doubled-pair count: same-class detection pairs with iou_lo < IoU < iou_hi",
        "per_anchor": per_anchor,
        "aggregate": aggregate,
    }
    with open(args.output_json, "w") as f:
        json.dump(summary, f, indent=2)

    print("\nAGGREGATE per method:")
    print(f"  {'method':<22s}  {'mean det':>10s}  {'mean dbl':>10s}  {'frac w/ dbl':>12s}")
    for m in method_dirs.keys():
        a = aggregate[m]
        print(f"  {m:<22s}  {a['mean_all_per_panorama']:>10.3f}  "
              f"{a['mean_doubled_per_panorama']:>10.3f}  "
              f"{a['frac_panoramas_with_doubled']:>12.3f}")
    print(f"saved {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
