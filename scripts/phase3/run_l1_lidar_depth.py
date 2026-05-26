"""
N1 Phase C — L1 sphere stitch with per-pixel LiDAR-derived convergence depth.

Loads the LiDAR sweep nearest to the camera anchor, projects to ERP depth map,
then renders the panorama with N1 cam-translation-aware projection using the
per-pixel depth instead of a single global r.

Pipeline:
    1. Load AV2 frame (7 ring cams + calibration) for the chosen anchor.
    2. Load nearest LiDAR sweep (within 75ms by default).
    3. Project LiDAR points to ERP depth map (sparse + kNN-fill densification).
    4. Render L1 sphere with `convergence_distance_m=lidar_depth_map`.
    5. Save outputs:
         l1_lidar.png         — N1 LiDAR-per-pixel ERP
         l1_inf.png           — legacy L1 (None) baseline A/B
         lidar_depth_viz.png  — depth map visualization (turbo-ish colormap)
         summary.json         — params + diagnostics

Visual gate:
    Direct A/B between l1_inf.png and l1_lidar.png at the Porsche/BMW ghost
    locations. With per-pixel r matching actual object distances, the
    cam-FOV-gap problem of single-r Phase A is eliminated, so coverage is
    similar to legacy L1 and any visual difference attributable to ghost-fix.

Usage:
    python scripts/phase3/run_l1_lidar_depth.py \\
        --log-dir /content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/02a00399-3857-444e-8db3-a8f58489c394 \\
        --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_c/02a00399/anchor_0 \\
        --anchor-index 0 \\
        --erp-h 2048 --erp-w 4096

CPU-only; no GPU needed. ~30s wall at 2048x4096 (LiDAR projection 1-2s, render 30s).
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
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def main() -> int:
    ap = argparse.ArgumentParser(description="N1 Phase C — L1 + LiDAR per-pixel r")
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--anchor-index", type=int, default=0)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--min-range-m", type=float, default=0.5)
    ap.add_argument("--max-range-m", type=float, default=100.0)
    ap.add_argument("--densify-radius-px", type=int, default=6)
    ap.add_argument("--fill-far-m", type=float, default=1000.0)
    ap.add_argument("--no-baseline", action="store_true",
                    help="Skip the legacy-L1 baseline render (saves ~30s).")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent / DEFAULT_W2P_CODE_REL)
    args = ap.parse_args()

    _wire_imports(args.w2p_code)
    from waymo2panorama.data_io.av2_loader import AV2RingLoader
    from waymo2panorama.pipeline.stitch_frame import stitch_one_frame
    from waymo2panorama.depth.lidar_to_erp_depth import (
        load_lidar_sweep_nearest_to_ts,
        project_lidar_to_erp_depth,
        visualize_depth_map,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    # 1. Frame load
    print(f"[N1-C] loading log: {args.log_dir}", flush=True)
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    if not 0 <= args.anchor_index < len(ts_all):
        raise IndexError(f"anchor_index {args.anchor_index} out of range (n={len(ts_all)})")
    anchor_ts = ts_all[args.anchor_index]
    print(f"[N1-C] anchor {args.anchor_index} ts_ns={anchor_ts}", flush=True)
    t0 = time.time()
    frame = loader.load_synced_frame(anchor_ts)
    t_load_frame = time.time() - t0
    print(f"[N1-C] frame loaded in {t_load_frame:.1f}s", flush=True)

    # 2. LiDAR load
    t0 = time.time()
    pts, sweep_ts, delta_ms = load_lidar_sweep_nearest_to_ts(args.log_dir, anchor_ts)
    t_load_lidar = time.time() - t0
    print(f"[N1-C] LiDAR sweep ts={sweep_ts}, delta={delta_ms:.2f} ms, "
          f"n_pts={pts.shape[0]}, load_time={t_load_lidar:.2f}s", flush=True)

    # 3. LiDAR → ERP depth map
    t0 = time.time()
    depth_map, depth_summary = project_lidar_to_erp_depth(
        pts,
        erp_hw=erp_hw,
        min_range_m=args.min_range_m,
        max_range_m=args.max_range_m,
        densify_radius_px=args.densify_radius_px,
        fill_far_m=args.fill_far_m,
    )
    t_depth = time.time() - t0
    print(f"[N1-C] depth_map built in {t_depth:.2f}s: "
          f"hit={depth_summary['hit_pct']}%, densified={depth_summary['densified_pct']}%, "
          f"far_fill={depth_summary['far_fill_pct']}%", flush=True)
    # Save depth viz
    depth_viz = visualize_depth_map(depth_map)
    viz_path = args.output_dir / "lidar_depth_viz.png"
    Image.fromarray(depth_viz).save(viz_path)
    # Save raw depth as float32 npz for reuse
    np.savez_compressed(args.output_dir / "lidar_depth_map.npz",
                        depth_map=depth_map,
                        erp_hw=np.array(erp_hw),
                        sweep_ts=sweep_ts,
                        anchor_ts=anchor_ts,
                        delta_ms=delta_ms)

    # 4. Render with N1 LiDAR per-pixel
    t0 = time.time()
    erp_lidar = stitch_one_frame(
        frame=frame,
        erp_hw=erp_hw,
        num_bands=args.num_bands,
        convergence_distance_m=depth_map,
    )
    t_render_lidar = time.time() - t0
    Image.fromarray(erp_lidar).save(args.output_dir / "l1_lidar.png")
    print(f"[N1-C] N1 LiDAR render: {t_render_lidar:.1f}s -> l1_lidar.png", flush=True)

    # 5. (optional) baseline render for direct A/B
    t_render_inf = 0.0
    if not args.no_baseline:
        t0 = time.time()
        erp_inf = stitch_one_frame(
            frame=frame, erp_hw=erp_hw, num_bands=args.num_bands,
            convergence_distance_m=None,
        )
        t_render_inf = time.time() - t0
        Image.fromarray(erp_inf).save(args.output_dir / "l1_inf.png")
        print(f"[N1-C] baseline render: {t_render_inf:.1f}s -> l1_inf.png", flush=True)

    summary = {
        "log_dir": str(args.log_dir),
        "anchor_index": args.anchor_index,
        "anchor_ts_ns": anchor_ts,
        "lidar_sweep_ts_ns": sweep_ts,
        "lidar_delta_ms": delta_ms,
        "n_lidar_pts": int(pts.shape[0]),
        "erp_hw": list(erp_hw),
        "num_bands": args.num_bands,
        "timing": {
            "load_frame_s": round(t_load_frame, 2),
            "load_lidar_s": round(t_load_lidar, 2),
            "depth_build_s": round(t_depth, 2),
            "render_lidar_s": round(t_render_lidar, 2),
            "render_baseline_s": round(t_render_inf, 2),
        },
        "depth_diagnostics": depth_summary,
    }
    summary_path = args.output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"[N1-C] summary -> {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
