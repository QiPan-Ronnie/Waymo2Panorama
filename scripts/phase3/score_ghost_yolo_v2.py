"""
v2 ghost score — YOLO-based object-aware.

For each anchor:
    1. Run YOLOv8 on each of 7 ring cam images.
    2. Count cars/persons whose bbox center is near the LEFT or RIGHT edge
       of that cam image (= seam region in stitched ERP, where doubled
       ghosts occur).
    3. Score = sum of edge-objects across all 7 cams. Higher = more
       ghost-likely. Zero = truly clean (no near-field objects in seam zones).

Edge zone: outer X% of cam width on each side (default 15% = ~230 px on AV2
2048-wide cams). Adjacent cams overlap roughly in these edge zones.

Categories considered (COCO class IDs):
    2  car
    3  motorcycle
    5  bus
    7  truck
    0  person

Usage:
    python scripts/phase3/score_ghost_yolo_v2.py \\
        --log-dir <path> --output-dir <path> --stride 5 --max-anchors 60
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"
GHOST_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 0: "person"}


def _wire_imports(w2p_code: Path) -> None:
    sys.path.insert(0, str(w2p_code))


def edge_score_for_cam(detections, img_w: int, img_h: int, edge_frac: float) -> dict:
    """Score one cam: count ghost-class detections whose bbox center falls
    in the outer `edge_frac` of the image width."""
    edge_px = int(edge_frac * img_w)
    counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "person": 0}
    total = 0
    for det in detections:
        cls = int(det["cls"])
        if cls not in GHOST_CLASSES:
            continue
        x0, y0, x1, y1 = det["xyxy"]
        cx = 0.5 * (x0 + x1)
        if cx < edge_px or cx > (img_w - edge_px):
            counts[GHOST_CLASSES[cls]] += 1
            total += 1
    return {
        "edge_object_total": total,
        "counts_per_class": counts,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--max-anchors", type=int, default=60)
    ap.add_argument("--edge-frac", type=float, default=0.15,
                    help="Outer fraction of cam image width considered 'seam zone'")
    ap.add_argument("--conf-threshold", type=float, default=0.3)
    ap.add_argument("--top-clean", type=int, default=5)
    ap.add_argument("--yolo-model", default="yolov8n.pt")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent / DEFAULT_W2P_CODE_REL)
    args = ap.parse_args()

    _wire_imports(args.w2p_code)
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7

    args.output_dir.mkdir(parents=True, exist_ok=True)
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    scan_indices = list(range(0, min(len(ts_all), args.max_anchors * args.stride), args.stride))
    print(f"scanning {len(scan_indices)} anchors of {len(ts_all)} total "
          f"(stride={args.stride}, edge={args.edge_frac:.0%})", flush=True)

    from ultralytics import YOLO
    t0 = time.time()
    yolo = YOLO(args.yolo_model)
    print(f"loaded {args.yolo_model} in {time.time()-t0:.1f}s", flush=True)

    results = []
    t0 = time.time()
    for k, idx in enumerate(scan_indices):
        ts = ts_all[idx]
        try:
            frame = loader.load_synced_frame(ts)
        except Exception as e:
            print(f"  anchor {idx}: skip ({e})", flush=True)
            continue
        per_cam_scores = []
        total_edge_objects = 0
        for cam in RING_CAMS_7:
            img = frame.images[cam]
            h_img, w_img = img.shape[:2]
            # YOLO inference
            res = yolo.predict(img, conf=args.conf_threshold, verbose=False)[0]
            dets = []
            if res.boxes is not None and len(res.boxes) > 0:
                xyxy = res.boxes.xyxy.cpu().numpy()
                cls = res.boxes.cls.cpu().numpy()
                conf = res.boxes.conf.cpu().numpy()
                for i in range(len(xyxy)):
                    dets.append({
                        "xyxy": xyxy[i].tolist(),
                        "cls": int(cls[i]),
                        "conf": float(conf[i]),
                    })
            cam_score = edge_score_for_cam(dets, w_img, h_img, args.edge_frac)
            cam_score["cam"] = cam
            cam_score["n_total_det"] = len(dets)
            per_cam_scores.append(cam_score)
            total_edge_objects += cam_score["edge_object_total"]
        results.append({
            "anchor_index": idx,
            "ts_ns": int(ts),
            "total_edge_objects": total_edge_objects,
            "per_cam": per_cam_scores,
        })
        if (k + 1) % 10 == 0:
            print(f"  scanned {k+1}/{len(scan_indices)} anchors "
                  f"({time.time()-t0:.0f}s wall)", flush=True)

    results_sorted = sorted(results, key=lambda r: r["total_edge_objects"])

    print(f"\n=== TOP CLEAN ANCHORS (lowest YOLO edge-objects = ghost-free) ===")
    for r in results_sorted[:args.top_clean]:
        per_cam_str = ", ".join(
            f"{c['cam'][5:]}={c['edge_object_total']}" for c in r['per_cam'] if c['edge_object_total'] > 0
        ) or "all clean"
        print(f"  anchor {r['anchor_index']:3d}: total={r['total_edge_objects']:2d}  ({per_cam_str})")

    print(f"\n=== TOP GHOSTY ANCHORS (highest YOLO edge-objects = most ghost-likely) ===")
    for r in results_sorted[-args.top_clean:]:
        per_cam_str = ", ".join(
            f"{c['cam'][5:]}={c['edge_object_total']}" for c in r['per_cam'] if c['edge_object_total'] > 0
        )
        print(f"  anchor {r['anchor_index']:3d}: total={r['total_edge_objects']:2d}  ({per_cam_str})")

    scores = [r["total_edge_objects"] for r in results_sorted]
    n_zero = sum(s == 0 for s in scores)
    print(f"\n=== STATS ({len(results_sorted)} anchors) ===")
    print(f"  min:    {min(scores)}")
    print(f"  median: {np.median(scores)}")
    print(f"  mean:   {np.mean(scores):.2f}")
    print(f"  max:    {max(scores)}")
    print(f"  zero edge-objects: {n_zero}/{len(scores)} anchors = truly ghost-free candidates")

    with open(args.output_dir / "yolo_ghost_scores.json", "w") as f:
        json.dump({
            "log_dir": str(args.log_dir),
            "stride": args.stride,
            "edge_frac": args.edge_frac,
            "conf_threshold": args.conf_threshold,
            "yolo_model": args.yolo_model,
            "stats": {
                "min": int(min(scores)),
                "median": float(np.median(scores)),
                "mean": float(np.mean(scores)),
                "max": int(max(scores)),
                "n_zero_edge_objects": int(n_zero),
            },
            "results_sorted": results_sorted,
        }, f, indent=2)
    print(f"\n=> {args.output_dir / 'yolo_ghost_scores.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
