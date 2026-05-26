"""Stage 3 A.5 — direct measurement of parallax ghost reduction.

For each adjacent ring-cam pair (cam_a, cam_b):
  1. Render per-cam L1 sphere ERP slab + weight (NO blending).
  2. Optionally apply WS4 A2 displacement warp to each slab.
  3. In the overlap mask (both weights > 0), compute:
       - L1 distance between slab_a and slab_b (lower = better alignment)
       - Pearson correlation per channel, averaged (higher = better alignment)
  4. Aggregate across 7 pairs.

The 2-wheel ghost is *caused* by slab_a and slab_b painting the same physical
object at different ERP locations. Pixel-wise mismatch in the overlap region
is the direct signal we want to reduce. Improvement in these metrics from
plain L1 → A2 means A2 is actually moving content into alignment.

Usage:
    python scripts/phase3/eval_parallax_ghost_alignment.py \\
        --av2-log-dir <log> --anchor-idx <N> \\
        --stereo-cache-dir <cache> \\
        --apply-a2 \\
        --output-json eval_ghost.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_CODE = _REPO / "code"
if str(_CODE) not in sys.path:
    sys.path.insert(0, str(_CODE))

from waymo2panorama.alignment.pair_homography import ADJACENT_PAIRS  # noqa: E402
from waymo2panorama.alignment.sparse_displacement import build_warped_slabs_a2  # noqa: E402
from waymo2panorama.data_io.av2_loader import RING_CAMS_7  # noqa: E402
from waymo2panorama.data_io.ego_mask import build_ego_masks  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


def _load_pi3_cam(pi3_dir: Path, cam: str) -> dict:
    return {
        "image": np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")),
        "K": np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy").astype(np.float64),
        "T_ego_cam": np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy").astype(np.float64),
    }


def _load_av2_raw_per_cam(av2_log_dir: Path, anchor_idx: int, cams: list[str]):
    from waymo2panorama.data_io.av2_loader import AV2RingLoader
    loader = AV2RingLoader(av2_log_dir)
    anchor_ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)
    return {
        cam: {
            "image": frame.images[cam],
            "K": frame.calibrations[cam].K.astype(np.float64),
            "T_ego_cam": frame.calibrations[cam].T_ego_cam.astype(np.float64),
        }
        for cam in cams
    }


def _overlap_alignment_stats(
    slab_a: np.ndarray, slab_b: np.ndarray,
    weight_a: np.ndarray, weight_b: np.ndarray,
    weight_threshold: float = 0.01,
) -> dict:
    """L1 + Pearson correlation between two slabs in their overlap mask."""
    overlap = (weight_a > weight_threshold) & (weight_b > weight_threshold)
    n_overlap = int(overlap.sum())
    if n_overlap < 100:
        return {"n_overlap_px": n_overlap, "l1": None, "pearson": None}
    a = slab_a[overlap].astype(np.float64)
    b = slab_b[overlap].astype(np.float64)
    l1 = float(np.mean(np.abs(a - b)))
    # Pearson per channel, mean
    rs = []
    for c in range(3):
        ac = a[:, c] - a[:, c].mean()
        bc = b[:, c] - b[:, c].mean()
        denom = (np.linalg.norm(ac) * np.linalg.norm(bc))
        rs.append(float(ac @ bc / denom) if denom > 1e-9 else 0.0)
    return {
        "n_overlap_px": n_overlap,
        "l1": l1,
        "pearson": float(np.mean(rs)),
        "pearson_per_channel": rs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", type=Path, default=None)
    ap.add_argument("--av2-log-dir", type=Path, default=None)
    ap.add_argument("--anchor-idx", type=int, default=None)
    ap.add_argument("--stereo-cache-dir", type=Path, default=None,
                    help="Required when --apply-a2; ignored otherwise.")
    ap.add_argument("--apply-a2", action="store_true",
                    help="Apply WS4 A2 displacement warp before computing alignment.")
    ap.add_argument("--target-mode", choices=["ideal", "midpoint"], default="ideal",
                    help="When --apply-a2: 'ideal' (orig A2) or 'midpoint' (Stage 3 Phase C joint).")
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--no-ego-mask", action="store_true")
    ap.add_argument("--output-json", type=Path, required=True)
    args = ap.parse_args()

    if (args.pi3_dir is None) == (args.av2_log_dir is None):
        ap.error("must provide exactly one of --pi3-dir / --av2-log-dir")
    if args.av2_log_dir is not None and args.anchor_idx is None:
        ap.error("--av2-log-dir requires --anchor-idx")
    if args.apply_a2 and args.stereo_cache_dir is None:
        ap.error("--apply-a2 requires --stereo-cache-dir")

    cams = list(RING_CAMS_7)
    erp_hw = (args.erp_h, args.erp_w)

    if args.av2_log_dir is not None:
        source_label = f"av2raw:{args.av2_log_dir.name}:anchor_{args.anchor_idx}"
        per_cam = _load_av2_raw_per_cam(args.av2_log_dir, args.anchor_idx, cams)
    else:
        source_label = f"pi3:{args.pi3_dir}"
        per_cam = {cam: _load_pi3_cam(args.pi3_dir, cam) for cam in cams}

    print(f"[ghost-align] source={source_label} apply_a2={args.apply_a2}", flush=True)

    ego_masks = build_ego_masks(
        cams, {cam: per_cam[cam]["image"].shape[:2] for cam in cams},
        enabled=not args.no_ego_mask,
    )

    slabs = {}
    weights = {}
    for cam in cams:
        d = per_cam[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        slabs[cam] = rgb
        weights[cam] = w

    if args.apply_a2:
        stereo_paths = sorted(args.stereo_cache_dir.glob("stereo_*.npz"))
        cam_K = {c: per_cam[c]["K"] for c in cams}
        cam_T = {c: per_cam[c]["T_ego_cam"] for c in cams}
        warped, a2_summary = build_warped_slabs_a2(
            l1_slabs=slabs, stereo_npz_paths=stereo_paths,
            cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=cams,
            erp_hw=erp_hw,
            target_mode=args.target_mode,
        )
        slabs = warped
    else:
        a2_summary = None

    per_pair = []
    for cam_a, cam_b in ADJACENT_PAIRS:
        s = _overlap_alignment_stats(slabs[cam_a], slabs[cam_b], weights[cam_a], weights[cam_b])
        s["cam_a"] = cam_a
        s["cam_b"] = cam_b
        per_pair.append(s)
        l1_s = f"{s['l1']:.2f}" if s['l1'] is not None else "n/a"
        p_s = f"{s['pearson']:.3f}" if s['pearson'] is not None else "n/a"
        print(f"  {cam_a:25s} -> {cam_b:25s}  n_overlap={s['n_overlap_px']:>7}  "
              f"L1={l1_s:>6}  Pearson={p_s:>6}", flush=True)

    valid = [p for p in per_pair if p["l1"] is not None]
    agg = {
        "n_pairs_valid": len(valid),
        "mean_l1": float(np.mean([p["l1"] for p in valid])) if valid else None,
        "mean_pearson": float(np.mean([p["pearson"] for p in valid])) if valid else None,
    }
    print(f"\n  AGGREGATE  mean_L1={agg['mean_l1']:.2f}  mean_Pearson={agg['mean_pearson']:.3f}",
          flush=True)

    output = {
        "source": source_label,
        "apply_a2": args.apply_a2,
        "erp_hw": list(erp_hw),
        "per_pair": per_pair,
        "agg": agg,
        "a2_summary": a2_summary,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, indent=2))
    print(f"\nwrote {args.output_json}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
