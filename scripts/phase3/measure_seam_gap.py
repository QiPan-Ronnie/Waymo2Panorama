"""
Quantify cross-cam luminance gap at seam pixels (before vs after L2 HDR).

For each anchor: project 7 cams to ERP, identify the 7 ring seams (cam pair
boundaries via argmax of cos² weights), measure mean |delta_Y| at seam pixels
where the argmax flips from cam_i to cam_j. Compare:
  - raw (no HDR)
  - with joint global HDR (L2 only, no OF, no hard-select per se — just
    luminance equalization applied to slabs)

A lower seam gap means cams' brightness matches better → less visible seam.

Usage:
    python scripts/phase3/measure_seam_gap.py \
        --log-dir /content/drive/.../02a00399-... \
        --anchors 0,50,100,150,200,250 \
        --output-json /content/.../seam_gap.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np


def measure_one_anchor(loader, ts_ns: int, erp_hw: tuple[int, int],
                       render_camera_to_erp, RING_CAMS_7,
                       compute_hdr_gains, apply_hdr) -> dict:
    frame = loader.load_synced_frame(ts_ns)
    slabs, weights = [], []
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(
            image=frame.images[cam], K=c.K, T_ego_cam=c.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb); weights.append(w)

    # Identify seam pixels: argmax of weights changes at adjacent ERP pixels
    w_stack = np.stack(weights, axis=0)
    argmax = w_stack.argmax(axis=0)
    valid = w_stack.max(axis=0) > 0

    # Horizontal seam mask: argmax[i, j] != argmax[i, j+1]
    seam_mask_h = np.zeros_like(argmax, dtype=bool)
    seam_mask_h[:, :-1] = (argmax[:, :-1] != argmax[:, 1:]) & valid[:, :-1] & valid[:, 1:]

    def seam_gap_score(slab_list) -> dict:
        # Convert each slab to Y channel for luminance
        ys = [cv2.cvtColor(s.astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)
              for s in slab_list]
        # For each horizontal seam pixel, compute |Y(left) - Y(right)|
        # where left and right come from different cams
        gaps = []
        rows, cols = np.where(seam_mask_h)
        if len(rows) == 0:
            return {"n_seam_px": 0, "mean_delta_Y": 0.0, "p95_delta_Y": 0.0}
        for r, c in zip(rows[:5000], cols[:5000]):  # sample for speed
            cam_left = int(argmax[r, c])
            cam_right = int(argmax[r, c+1])
            y_left = ys[cam_left][r, c]
            y_right = ys[cam_right][r, c+1]
            gaps.append(abs(y_left - y_right))
        gaps = np.array(gaps)
        return {
            "n_seam_px": int(len(gaps)),
            "mean_delta_Y": float(gaps.mean()),
            "p95_delta_Y": float(np.percentile(gaps, 95)),
            "max_delta_Y": float(gaps.max()),
        }

    raw_stats = seam_gap_score(slabs)
    gains = compute_hdr_gains(slabs, weights)
    slabs_hdr = apply_hdr(slabs, gains)
    hdr_stats = seam_gap_score(slabs_hdr)
    return {
        "raw": raw_stats,
        "hdr": hdr_stats,
        "gains": [float(g) for g in gains],
        "improvement_mean_pct": float(
            100 * (raw_stats["mean_delta_Y"] - hdr_stats["mean_delta_Y"])
            / max(raw_stats["mean_delta_Y"], 1.0)
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--anchors", type=str, required=True,
                    help="comma list of anchor indices to measure")
    ap.add_argument("--output-json", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent / "code")
    args = ap.parse_args()

    sys.path.insert(0, str(args.w2p_code))
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.hard_hdr_of import compute_hdr_gains, apply_hdr

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    indices = [int(s) for s in args.anchors.split(",")]
    indices = [i for i in indices if i < len(ts_all)]

    results = {}
    for idx in indices:
        print(f"anchor {idx}...")
        r = measure_one_anchor(
            loader, ts_all[idx], (args.erp_h, args.erp_w),
            render_camera_to_erp, RING_CAMS_7,
            compute_hdr_gains, apply_hdr,
        )
        results[str(idx)] = r
        print(f"  raw mean_delta_Y: {r['raw']['mean_delta_Y']:.2f}, "
              f"hdr mean_delta_Y: {r['hdr']['mean_delta_Y']:.2f}, "
              f"improvement: {r['improvement_mean_pct']:.1f}%")

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "per_anchor": results,
        "aggregate": {
            "n_anchors": len(results),
            "mean_raw_delta_Y": float(np.mean([r["raw"]["mean_delta_Y"] for r in results.values()])),
            "mean_hdr_delta_Y": float(np.mean([r["hdr"]["mean_delta_Y"] for r in results.values()])),
            "mean_improvement_pct": float(np.mean([r["improvement_mean_pct"] for r in results.values()])),
        }
    }
    with open(args.output_json, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nAGGREGATE: raw {summary['aggregate']['mean_raw_delta_Y']:.2f} → "
          f"hdr {summary['aggregate']['mean_hdr_delta_Y']:.2f} "
          f"(-{summary['aggregate']['mean_improvement_pct']:.1f}%)")
    print(f"saved {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
