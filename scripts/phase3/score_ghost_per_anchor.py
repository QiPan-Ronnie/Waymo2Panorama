"""
Path (c) — Strategic reframe: identify ghost-free anchor subset for Bosch.

Hypothesis: Bosch downstream world model can use ghost-FREE frames; doesn't
need every frame perfect. The N1 architecture work showed visible ghost is
hard to fix on a per-frame basis (view-dependent overlap is fundamental).
But if many anchors are NATURALLY ghost-free (e.g. no near-field cars in
seam regions), we can deliver a clean subset.

Score per anchor:
    ghost_score = pair-wise adjacent-cam overlap MEAN |color_diff|
                  ÷ overlap pixel count
                  (higher = more ghost-likely)

Run on full log (~319 anchors of 02a00399, ~16s wall total at 1024x2048).
Output sorted ranking + thumbnails of top-N "clean" frames.
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


def _wire_imports(w2p_code: Path) -> None:
    sys.path.insert(0, str(w2p_code))


def compute_ghost_score(slabs, alphas) -> dict:
    """For each pair of cams with overlap, compute mean abs color diff.
    Average across pairs. Lower = cleaner."""
    n_cams = len(slabs)
    pair_scores = []
    for i in range(n_cams):
        for j in range(i + 1, n_cams):
            ovl = alphas[i].astype(bool) & alphas[j].astype(bool)
            if ovl.sum() < 100:
                continue
            d = np.abs(slabs[i].astype(np.float32) - slabs[j].astype(np.float32)).mean(axis=-1)
            score = float(d[ovl].mean())
            pair_scores.append({
                "pair": (i, j),
                "overlap_px": int(ovl.sum()),
                "mean_color_diff": score,
            })
    if not pair_scores:
        return {"mean_ghost_score": float("nan"), "pairs": []}
    overall = float(np.mean([p["mean_color_diff"] for p in pair_scores]))
    return {"mean_ghost_score": overall, "pairs": pair_scores}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=512, help="Smaller for fast scan")
    ap.add_argument("--erp-w", type=int, default=1024)
    ap.add_argument("--stride", type=int, default=10, help="Scan every Nth anchor")
    ap.add_argument("--max-anchors", type=int, default=50, help="Max anchors to scan")
    ap.add_argument("--top-clean", type=int, default=5)
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent / DEFAULT_W2P_CODE_REL)
    args = ap.parse_args()

    _wire_imports(args.w2p_code)
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    args.output_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    scan_indices = list(range(0, min(len(ts_all), args.max_anchors * args.stride), args.stride))
    print(f"scanning {len(scan_indices)} anchors of {len(ts_all)} total "
          f"(stride={args.stride}, erp={erp_hw})", flush=True)

    results = []
    for idx in scan_indices:
        ts = ts_all[idx]
        try:
            frame = loader.load_synced_frame(ts)
        except Exception as e:
            print(f"  anchor {idx}: skip ({e})", flush=True)
            continue
        slabs, alphas = [], []
        for cam in RING_CAMS_7:
            calib = frame.calibrations[cam]
            rgb, alpha, _ = render_camera_to_erp(
                image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
                erp_hw=erp_hw, convergence_distance_m=None,
            )
            slabs.append(rgb); alphas.append(alpha)
        score = compute_ghost_score(slabs, alphas)
        results.append({
            "anchor_index": idx,
            "ts_ns": int(ts),
            "mean_ghost_score": score["mean_ghost_score"],
            "n_overlap_pairs": len(score["pairs"]),
        })

    # Sort by ghost score (lower = cleaner)
    results_sorted = sorted([r for r in results if r["mean_ghost_score"] == r["mean_ghost_score"]],
                            key=lambda r: r["mean_ghost_score"])

    print("\n=== TOP CLEAN ANCHORS (low ghost score) ===")
    for r in results_sorted[:args.top_clean]:
        print(f"  anchor {r['anchor_index']:3d}: score = {r['mean_ghost_score']:.2f}")
    print("\n=== TOP GHOSTY ANCHORS (high ghost score) ===")
    for r in results_sorted[-args.top_clean:]:
        print(f"  anchor {r['anchor_index']:3d}: score = {r['mean_ghost_score']:.2f}")

    scores_arr = [r["mean_ghost_score"] for r in results_sorted]
    print(f"\n=== STATS ({len(results_sorted)} anchors scanned) ===")
    print(f"  min:    {min(scores_arr):.2f}")
    print(f"  median: {np.median(scores_arr):.2f}")
    print(f"  mean:   {np.mean(scores_arr):.2f}")
    print(f"  max:    {max(scores_arr):.2f}")
    p25 = np.percentile(scores_arr, 25)
    print(f"  p25:    {p25:.2f}  ← 'clean subset' threshold (lower 25%)")
    n_clean = sum(s <= p25 for s in scores_arr)
    print(f"  → {n_clean}/{len(scores_arr)} anchors below p25 = ghost-free subset candidate")

    # Render the top-clean and top-ghosty for visual A/B
    top_clean = results_sorted[:args.top_clean]
    top_ghosty = results_sorted[-args.top_clean:]
    for label, group in [("clean", top_clean), ("ghosty", top_ghosty)]:
        for k, r in enumerate(group):
            ts = ts_all[r["anchor_index"]]
            frame = loader.load_synced_frame(ts)
            slabs, weights = [], []
            for cam in RING_CAMS_7:
                calib = frame.calibrations[cam]
                rgb, _alpha, w = render_camera_to_erp(
                    image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
                    erp_hw=erp_hw, convergence_distance_m=None,
                )
                slabs.append(rgb); weights.append(w)
            from waymo2panorama.blending.multiband import multiband_blend
            erp = multiband_blend(slabs, weights, num_bands=5, wrap=True)
            out = args.output_dir / f"{label}_rank{k+1}_anchor{r['anchor_index']:03d}_score{r['mean_ghost_score']:.1f}.png"
            Image.fromarray(erp).save(out)
            print(f"  saved {out.name}", flush=True)

    summary = {
        "log_dir": str(args.log_dir),
        "erp_hw": list(erp_hw),
        "n_anchors_scanned": len(results),
        "stride": args.stride,
        "stats": {
            "min": float(min(scores_arr)),
            "median": float(np.median(scores_arr)),
            "mean": float(np.mean(scores_arr)),
            "max": float(max(scores_arr)),
            "p25": float(p25),
        },
        "top_clean": top_clean,
        "top_ghosty": top_ghosty,
        "all_scores": results_sorted,
    }
    with open(args.output_dir / "ghost_scores.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n=> {args.output_dir / 'ghost_scores.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
