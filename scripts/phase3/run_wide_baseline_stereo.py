"""
Phase 3 route 13 / 新-D — Wide-baseline stereo on adjacent AV2 ring cams.

Driver script:
    - Loads one anchor's 7 ring-cam images (+ K, T_ego_cam) from a pi3_cache dir.
    - Runs the kornia DISK + LightGlue + epipolar-filter + DLT triangulation
      pipeline on each of the 7 adjacent cam pairs (see ADJACENT_PAIRS).
    - Saves per-pair `.npz` (3D pts in ego + cam_a frames, mkpts, inlier mask),
      a depth-coloured back-projection PNG, plus an anchor-level `summary.json`
      with match / inlier counts and per-pair timings.

Usage:
    python scripts/phase3/run_wide_baseline_stereo.py \
        --pi3-dir outputs/phase3/pi3_cache/anchor_060 \
        --output-dir outputs/phase3/p3.6_stereo/anchor_060

Multiple anchors:
    python scripts/phase3/run_wide_baseline_stereo.py \
        --pi3-cache-root outputs/phase3/pi3_cache \
        --anchors 0 60 90 150 \
        --output-root outputs/phase3/p3.6_stereo

Optionally also produces a mosaic figure of all per-pair depth-vis PNGs at
`--mosaic-out <path>` (typically used for the paper figure
`deliverables/images/route_wide_baseline_depth.png`).

Mode is **pure stereo extraction** — this script does NOT modify L1 / change
the ERP rendering. Integration with L1 (Option B reweighting) is a downstream
hook; the .npz outputs here are the required input for that.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# Path wiring
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_CODE_DIR = _REPO / "code"
if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

from waymo2panorama.stereo.wide_baseline_stereo import (  # noqa: E402
    ADJACENT_PAIRS,
    RING_CAMS_7,
    StereoMatchResult,
    _load_pi3_anchor,
    process_anchor_all_pairs_from_data,
)
from waymo2panorama.data_io.av2_loader import AV2RingLoader  # noqa: E402


# ---------------------------------------------------------------------------
# Per-pair depth visualization
# ---------------------------------------------------------------------------


def _draw_depth_viz(
    img_a: np.ndarray,
    img_b: np.ndarray,
    mkpts_a: np.ndarray,
    mkpts_b: np.ndarray,
    inlier_mask: np.ndarray,
    depth_cam_a: np.ndarray,
    title: str,
) -> np.ndarray:
    """Build a side-by-side viz: img_a with depth-colored inliers + img_b with corresponding inliers.

    Color codes depth from cam_a (turbo colormap). Outliers shown in gray.
    """
    # AV2 raw front_center is portrait (2048x1550) while other cams are landscape
    # (1550x2048). Use max dims so both fit; center-align each image in its half.
    h_a, w_a = img_a.shape[:2]
    h_b, w_b = img_b.shape[:2]
    h = max(h_a, h_b)
    w = max(w_a, w_b)
    out = np.zeros((h + 60, 2 * w + 8, 3), dtype=np.uint8)
    out[:, :] = 32

    # Header strip
    cv2.putText(
        out, title, (12, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
        (255, 255, 255), 1, cv2.LINE_AA,
    )

    img_a_vis = img_a.copy()
    img_b_vis = img_b.copy()

    # Outliers in light gray
    out_idx = np.where(~inlier_mask)[0]
    for i in out_idx:
        xa, ya = mkpts_a[i]
        xb, yb = mkpts_b[i]
        cv2.circle(img_a_vis, (int(xa), int(ya)), 2, (140, 140, 140), -1, cv2.LINE_AA)
        cv2.circle(img_b_vis, (int(xb), int(yb)), 2, (140, 140, 140), -1, cv2.LINE_AA)

    # Inliers colored by depth (turbo)
    in_idx = np.where(inlier_mask)[0]
    if len(in_idx) > 0:
        d = depth_cam_a
        # clip to a robust range so outlier far-points don't crush color scale
        d_lo, d_hi = np.percentile(d, [5, 95]) if len(d) >= 4 else (d.min(), d.max())
        d_lo = max(d_lo, 0.5)
        d_hi = max(d_hi, d_lo + 1.0)
        d_norm = np.clip((d - d_lo) / (d_hi - d_lo), 0, 1)
        cmap = cv2.applyColorMap(
            (d_norm * 255).astype(np.uint8).reshape(-1, 1), cv2.COLORMAP_TURBO
        ).reshape(-1, 3)
        # cv2 returns BGR; flip to RGB to match img_a/b
        cmap = cmap[:, ::-1]
        for k, i in enumerate(in_idx):
            xa, ya = mkpts_a[i]
            xb, yb = mkpts_b[i]
            col = tuple(int(c) for c in cmap[k])
            cv2.circle(img_a_vis, (int(xa), int(ya)), 3, col, -1, cv2.LINE_AA)
            cv2.circle(img_b_vis, (int(xb), int(yb)), 3, col, -1, cv2.LINE_AA)
        # Add depth legend text
        legend = f"depth range: {d_lo:.1f} - {d_hi:.1f} m  ({len(in_idx)} inliers)"
    else:
        legend = "no inliers"
    cv2.putText(
        out, legend, (12, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
        (220, 220, 220), 1, cv2.LINE_AA,
    )

    # Center each image in its half-cell so portrait/landscape mixes still fit
    y_a = 60 + (h - h_a) // 2
    x_a = (w - w_a) // 2
    y_b = 60 + (h - h_b) // 2
    x_b = w + 8 + (w - w_b) // 2
    out[y_a:y_a + h_a, x_a:x_a + w_a] = img_a_vis
    out[y_b:y_b + h_b, x_b:x_b + w_b] = img_b_vis
    return out


def _load_pi3_image(anchor_dir: Path, cam: str) -> np.ndarray:
    return np.asarray(Image.open(anchor_dir / f"image_{cam}.png").convert("RGB"))


def _load_av2_raw_anchor(loader: AV2RingLoader, anchor_idx: int) -> dict[str, dict]:
    """Build cams_data dict from AV2 raw at the given anchor index.

    Same return shape as _load_pi3_anchor (one entry per cam), but loads full-res
    images and factory K / T_ego_cam directly from the sensor log — no letterbox
    degradation.
    """
    anchor_ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)
    return {
        cam: {
            "image": frame.images[cam],
            "K": frame.calibrations[cam].K.astype(np.float64),
            "T_ego_cam": frame.calibrations[cam].T_ego_cam.astype(np.float64),
        }
        for cam in RING_CAMS_7
    }


# ---------------------------------------------------------------------------
# Mosaic builder (paper figure)
# ---------------------------------------------------------------------------


def _build_mosaic(viz_pngs: list[Path], cols: int = 2, gap: int = 6) -> np.ndarray:
    """Stack a list of viz PNGs into a (rows, cols) mosaic with `gap` px black borders."""
    if not viz_pngs:
        raise ValueError("no viz PNGs to mosaic")
    imgs = [np.asarray(Image.open(p).convert("RGB")) for p in viz_pngs]
    # Normalize to the same H,W (resize to max H, pad columns to max W of column)
    max_h = max(im.shape[0] for im in imgs)
    max_w = max(im.shape[1] for im in imgs)
    pad_imgs = []
    for im in imgs:
        h, w = im.shape[:2]
        pad = np.full((max_h, max_w, 3), 16, dtype=np.uint8)
        pad[:h, :w] = im
        pad_imgs.append(pad)
    rows = (len(pad_imgs) + cols - 1) // cols
    mosaic_h = rows * max_h + (rows - 1) * gap
    mosaic_w = cols * max_w + (cols - 1) * gap
    mosaic = np.full((mosaic_h, mosaic_w, 3), 16, dtype=np.uint8)
    for k, im in enumerate(pad_imgs):
        r = k // cols
        c = k % cols
        y = r * (max_h + gap)
        x = c * (max_w + gap)
        mosaic[y:y + max_h, x:x + max_w] = im
    return mosaic


# ---------------------------------------------------------------------------
# Per-anchor driver
# ---------------------------------------------------------------------------


def run_one_anchor(
    cams_data: dict[str, dict],
    output_dir: Path,
    device: str,
    stereo_kwargs: dict,
    source_label: str = "",
) -> dict:
    """Stereo extract one anchor given pre-loaded cam data.

    source_label: human-readable string (e.g. "pi3:anchor_060" or "av2raw:log_xxx:anchor_60")
    saved into summary.json for provenance tracking.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    t_all = time.time()
    results: dict[tuple[str, str], StereoMatchResult] = process_anchor_all_pairs_from_data(
        cams_data, device=device, **stereo_kwargs,
    )

    per_pair_summary: list[dict] = []
    viz_pngs: list[Path] = []
    for (cam_a, cam_b), r in results.items():
        tag = f"{cam_a.replace('ring_', '')}__{cam_b.replace('ring_', '')}"
        npz_path = output_dir / f"stereo_{tag}.npz"
        np.savez_compressed(
            npz_path,
            pts_3d_ego=r.pts_3d_ego,
            pts_3d_cam_a=r.pts_3d_cam_a,
            depth_cam_a=r.depth_cam_a,
            parallax_deg=r.parallax_deg,
            mkpts_a=r.mkpts_a,
            mkpts_b=r.mkpts_b,
            inlier_mask=r.inlier_mask,
            F=r.F,
            cam_a=np.array(cam_a),
            cam_b=np.array(cam_b),
            baseline_m=np.array(r.baseline_m),
        )

        img_a = cams_data[cam_a]["image"]
        img_b = cams_data[cam_b]["image"]
        title = (
            f"{cam_a} -> {cam_b}    baseline={r.baseline_m:.2f} m    "
            f"matches={r.notes['n_matches_lightglue']}  "
            f"epi_in={r.notes['epipolar_inliers_before_geom_filter']}  "
            f"final={len(r.pts_3d_ego)}"
        )
        viz = _draw_depth_viz(img_a, img_b, r.mkpts_a, r.mkpts_b, r.inlier_mask, r.depth_cam_a, title)
        viz_path = output_dir / f"depth_viz_{tag}.png"
        Image.fromarray(viz).save(viz_path)
        viz_pngs.append(viz_path)

        d = r.depth_cam_a
        per_pair_summary.append(
            {
                "cam_a": cam_a, "cam_b": cam_b,
                "baseline_m": r.baseline_m,
                "n_kpts_a": r.n_kpts_a, "n_kpts_b": r.n_kpts_b,
                "n_matches_lightglue": int(r.notes["n_matches_lightglue"]),
                "n_epi_inliers": int(r.notes["epipolar_inliers_before_geom_filter"]),
                "n_dropped_cheirality": int(r.notes["n_dropped_cheirality"]),
                "n_dropped_depth_band": int(r.notes["n_dropped_depth_band"]),
                "n_dropped_parallax": int(r.notes["n_dropped_parallax"]),
                "n_final_pts": int(len(r.pts_3d_ego)),
                "depth_min_m": float(d.min()) if len(d) else None,
                "depth_max_m": float(d.max()) if len(d) else None,
                "depth_median_m": float(np.median(d)) if len(d) else None,
                "depth_p10_m": float(np.percentile(d, 10)) if len(d) >= 4 else None,
                "depth_p90_m": float(np.percentile(d, 90)) if len(d) >= 4 else None,
                "median_parallax_deg": r.notes["median_parallax_deg_kept"],
                "time_s": r.time_s,
                "npz_path": str(npz_path.relative_to(output_dir)),
                "viz_path": str(viz_path.relative_to(output_dir)),
            }
        )

    elapsed_total = time.time() - t_all
    # Aggregate stats
    n_finals = [p["n_final_pts"] for p in per_pair_summary]
    summary = {
        "source": source_label,
        "output_dir": str(output_dir),
        "n_pairs": len(per_pair_summary),
        "stereo_kwargs": stereo_kwargs,
        "device": device,
        "elapsed_total_s": elapsed_total,
        "per_pair": per_pair_summary,
        "agg": {
            "n_final_pts_total": int(sum(n_finals)),
            "n_final_pts_mean": float(np.mean(n_finals)) if n_finals else 0.0,
            "n_final_pts_min": int(min(n_finals)) if n_finals else 0,
            "n_final_pts_max": int(max(n_finals)) if n_finals else 0,
            "depth_min_m_overall": float(
                min((p["depth_min_m"] for p in per_pair_summary if p["depth_min_m"] is not None), default=0.0)
            ),
            "depth_max_m_overall": float(
                max((p["depth_max_m"] for p in per_pair_summary if p["depth_max_m"] is not None), default=0.0)
            ),
        },
    }
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    # Anchor-level mosaic alongside per-pair PNGs (lightweight, for quick QA)
    anchor_mosaic = _build_mosaic(viz_pngs, cols=2)
    Image.fromarray(anchor_mosaic).save(output_dir / "depth_viz_mosaic.png")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_anchor_tasks(args: argparse.Namespace) -> list[tuple[dict[str, dict], Path, str]]:
    """Return list of (cams_data, out_dir, source_label) tasks.

    Supports two input modes:
      - pi3-cache: --pi3-dir / --pi3-cache-root + --anchors
      - AV2 raw:   --av2-log-dir + --anchor-idx (single) or --anchors (multi)
    """
    # --- pi3-cache: single anchor ---
    if args.pi3_dir is not None:
        if args.output_dir is None:
            raise ValueError("--output-dir is required with --pi3-dir")
        pi3_dir = Path(args.pi3_dir)
        cams = {cam: _load_pi3_anchor(pi3_dir, cam) for cam in RING_CAMS_7}
        return [(cams, Path(args.output_dir), f"pi3:{pi3_dir}")]

    # --- pi3-cache: multi anchor ---
    if args.pi3_cache_root is not None:
        if args.anchors is None or args.output_root is None:
            raise ValueError("--pi3-cache-root requires --anchors + --output-root")
        tasks = []
        root = Path(args.pi3_cache_root)
        out_root = Path(args.output_root)
        for a in args.anchors:
            pi3_dir = root / f"anchor_{int(a):03d}"
            if not pi3_dir.exists():
                raise FileNotFoundError(f"missing pi3 cache: {pi3_dir}")
            cams = {cam: _load_pi3_anchor(pi3_dir, cam) for cam in RING_CAMS_7}
            tasks.append((cams, out_root / f"anchor_{int(a):03d}", f"pi3:{pi3_dir}"))
        return tasks

    # --- AV2 raw ---
    if args.av2_log_dir is not None:
        log_dir = Path(args.av2_log_dir)
        if not log_dir.exists():
            raise FileNotFoundError(f"missing AV2 log dir: {log_dir}")
        loader = AV2RingLoader(log_dir)

        # Single-anchor mode via --anchor-idx + --output-dir, OR multi via --anchors + --output-root
        if args.anchor_idx is not None:
            if args.output_dir is None:
                raise ValueError("--av2-log-dir + --anchor-idx requires --output-dir")
            cams = _load_av2_raw_anchor(loader, args.anchor_idx)
            label = f"av2raw:{log_dir.name}:anchor_{args.anchor_idx}"
            return [(cams, Path(args.output_dir), label)]

        if args.anchors is None or args.output_root is None:
            raise ValueError(
                "--av2-log-dir requires either --anchor-idx + --output-dir, "
                "or --anchors + --output-root"
            )
        tasks = []
        out_root = Path(args.output_root)
        for a in args.anchors:
            cams = _load_av2_raw_anchor(loader, int(a))
            label = f"av2raw:{log_dir.name}:anchor_{a}"
            tasks.append((cams, out_root / f"anchor_{int(a):03d}", label))
        return tasks

    raise ValueError(
        "must provide one of: --pi3-dir, --pi3-cache-root + --anchors, "
        "--av2-log-dir + --anchor-idx, or --av2-log-dir + --anchors"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Wide-baseline stereo on AV2 ring-cam pairs.")
    # Single-anchor mode (pi3-cache OR AV2 raw)
    p.add_argument("--pi3-dir", type=str, default=None, help="Path to one pi3_cache anchor dir.")
    p.add_argument("--av2-log-dir", type=str, default=None,
                   help="Path to AV2 sensor log dir for full-res input. Use with "
                        "--anchor-idx + --output-dir (single) or --anchors + --output-root (multi).")
    p.add_argument("--anchor-idx", type=int, default=None,
                   help="Single anchor index for --av2-log-dir mode.")
    p.add_argument("--output-dir", type=str, default=None, help="Where to write outputs for single anchor.")
    # Multi-anchor mode
    p.add_argument("--pi3-cache-root", type=str, default=None, help="Root containing anchor_NNN subdirs.")
    p.add_argument("--output-root", type=str, default=None, help="Root for per-anchor outputs.")
    p.add_argument("--anchors", nargs="+", type=int, default=None, help="Anchor indices (e.g. 0 60 90 150).")
    # Stereo hyperparams
    p.add_argument("--device", type=str, default="cpu", choices=["cpu", "cuda"], help="kornia device")
    p.add_argument("--max-num-keypoints", type=int, default=2048)
    p.add_argument("--lightglue-min-confidence", type=float, default=0.2)
    p.add_argument("--epipolar-threshold-px", type=float, default=3.0)
    p.add_argument("--min-depth-m", type=float, default=0.5)
    p.add_argument("--max-depth-m", type=float, default=120.0)
    p.add_argument(
        "--min-parallax-deg", type=float, default=0.5,
        help="Reject triangulated points whose ray parallax is below this angle. "
             "Sparse stereo on ~0.25 m baselines becomes unreliable below ~0.3-0.5 deg "
             "(corresponds to >>50 m depth with 1 px keypoint noise).",
    )
    # Optional mosaic figure across anchors
    p.add_argument(
        "--mosaic-out", type=str, default=None,
        help="If set, after all anchors complete, write a combined depth-viz mosaic to this path.",
    )
    p.add_argument(
        "--mosaic-anchor-id", type=int, default=None,
        help=(
            "If set together with --mosaic-out, build the mosaic from this anchor's depth_viz "
            "PNGs only (skips cross-anchor mosaicing). Default: use the first anchor processed."
        ),
    )
    args = p.parse_args()

    stereo_kwargs = dict(
        max_num_keypoints=args.max_num_keypoints,
        lightglue_min_confidence=args.lightglue_min_confidence,
        epipolar_threshold_px=args.epipolar_threshold_px,
        min_depth_m=args.min_depth_m,
        max_depth_m=args.max_depth_m,
        min_parallax_deg=args.min_parallax_deg,
    )
    print(f"stereo_kwargs: {stereo_kwargs}")
    print(f"device: {args.device}")

    tasks = _resolve_anchor_tasks(args)
    summaries: list[dict] = []
    for cams_data, out_dir, source_label in tasks:
        print(f"\n=== {source_label} -> {out_dir} ===")
        # Print sample image shape so we know whether we're on raw or letterbox
        sample_cam = next(iter(cams_data))
        print(f"  sample image shape ({sample_cam}): {cams_data[sample_cam]['image'].shape}")
        summary = run_one_anchor(cams_data, out_dir, args.device, stereo_kwargs, source_label=source_label)
        print(f"  total pts across 7 pairs: {summary['agg']['n_final_pts_total']}")
        print(f"  pts/pair mean/min/max: "
              f"{summary['agg']['n_final_pts_mean']:.1f} / "
              f"{summary['agg']['n_final_pts_min']} / "
              f"{summary['agg']['n_final_pts_max']}")
        print(f"  depth range overall: "
              f"[{summary['agg']['depth_min_m_overall']:.2f}, "
              f"{summary['agg']['depth_max_m_overall']:.2f}] m")
        print(f"  elapsed: {summary['elapsed_total_s']:.1f}s")
        summaries.append(summary)

    if args.mosaic_out is not None:
        if args.mosaic_anchor_id is not None:
            ax = next(
                (s for s in summaries if Path(s["output_dir"]).name == f"anchor_{args.mosaic_anchor_id:03d}"),
                None,
            )
            if ax is None:
                raise ValueError(f"--mosaic-anchor-id {args.mosaic_anchor_id} not in processed anchors")
            anchor_out = Path(ax["output_dir"])
        else:
            anchor_out = Path(summaries[0]["output_dir"])
        viz_pngs = sorted(anchor_out.glob("depth_viz_*__*.png"))
        mosaic = _build_mosaic(viz_pngs, cols=2)
        out_path = Path(args.mosaic_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(mosaic).save(out_path)
        print(f"\nmosaic written: {out_path} ({mosaic.shape[1]}x{mosaic.shape[0]})")


if __name__ == "__main__":
    main()
