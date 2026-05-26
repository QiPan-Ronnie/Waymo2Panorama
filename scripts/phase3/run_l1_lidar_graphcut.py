"""
N1 Phase C + N2 (新-B graphcut) — combined: per-pixel LiDAR depth projection
+ graphcut hard-seam selection instead of multiband over cos² feather.

Hypothesis: Phase C alone left ghost because two cams contribute via blending
to the same overlap pixels even with correct angular registration (each cam
shows a different view of the object). Graphcut min-cut routes the seam
through low-energy regions (sky / road), so for the near-field car pixels
only ONE cam contributes — no overlap blending, no doubled object.

Pipeline (anchor 0 of log 02a00399):
    1. Load frame + LiDAR sweep.
    2. Build per-pixel LiDAR depth map (as in run_l1_lidar_depth.py).
    3. For each of 7 ring cams: render_camera_to_erp with convergence_distance_m
       = LiDAR depth map -> 7 slabs + 7 alphas + 7 cos² weights.
    4. Compute cam_axes_erp (ERP column for each cam's optical axis).
    5. Run apply_graphcut_seams -> 7 hard-cut weights.
    6. multiband_blend with hard-cut weights -> final ERP.
    7. Also render the baseline (legacy + cos² + multiband) for A/B.

Outputs (--output-dir):
    l1_inf.png                — legacy L1 baseline
    l1_lidar.png              — Phase C: N1+LiDAR, cos² + multiband
    l1_lidar_graphcut.png     — Phase C + N2: N1+LiDAR + hard graphcut seam
    seam_overlay.png          — seam locations drawn on l1_lidar_graphcut
    summary.json
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
    ap = argparse.ArgumentParser()
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
    ap.add_argument("--feather-sigma", type=float, default=3.0)
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent / DEFAULT_W2P_CODE_REL)
    args = ap.parse_args()

    _wire_imports(args.w2p_code)
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.blending.graphcut_seam import (
        apply_graphcut_seams,
        _erp_column_for_axis_world,
        draw_seam_overlay_on_erp,
    )
    from waymo2panorama.depth.lidar_to_erp_depth import (
        load_lidar_sweep_nearest_to_ts,
        project_lidar_to_erp_depth,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    # 1. Load
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    anchor_ts = ts_all[args.anchor_index]
    frame = loader.load_synced_frame(anchor_ts)
    print(f"[N1C+N2] loaded anchor {args.anchor_index}", flush=True)

    pts, sweep_ts, delta_ms = load_lidar_sweep_nearest_to_ts(args.log_dir, anchor_ts)
    print(f"[N1C+N2] LiDAR: delta={delta_ms:.2f}ms, {pts.shape[0]} pts", flush=True)

    # 2. Depth map
    t0 = time.time()
    depth_map, depth_summary = project_lidar_to_erp_depth(
        pts, erp_hw=erp_hw,
        min_range_m=args.min_range_m,
        max_range_m=args.max_range_m,
        densify_radius_px=args.densify_radius_px,
        fill_far_m=args.fill_far_m,
    )
    t_depth = time.time() - t0
    print(f"[N1C+N2] depth: hit={depth_summary['hit_pct']}% "
          f"densified={depth_summary['densified_pct']}% "
          f"far_fill={depth_summary['far_fill_pct']}% ({t_depth:.1f}s)", flush=True)

    # 3. Render 7 cams with N1+LiDAR
    t0 = time.time()
    slabs: list[np.ndarray] = []
    alphas: list[np.ndarray] = []
    cos2_weights: list[np.ndarray] = []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, alpha, weight = render_camera_to_erp(
            image=frame.images[cam],
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=depth_map,
        )
        slabs.append(rgb)
        alphas.append(alpha)
        cos2_weights.append(weight)
    t_render = time.time() - t0
    print(f"[N1C+N2] rendered 7 cams in {t_render:.1f}s", flush=True)

    # 4. cam_axes_erp
    cam_axes_erp = [
        _erp_column_for_axis_world(frame.calibrations[c].T_ego_cam, args.erp_w)
        for c in RING_CAMS_7
    ]

    # 5. multiband (Phase C — N1+LiDAR + cos² + multiband)
    t0 = time.time()
    erp_c_only = multiband_blend(slabs, cos2_weights, num_bands=args.num_bands, wrap=True)
    t_blend_c = time.time() - t0
    Image.fromarray(erp_c_only).save(args.output_dir / "l1_lidar.png")
    print(f"[N1C+N2] Phase C only blend: {t_blend_c:.1f}s", flush=True)

    # 6. Apply graphcut to get hard weights, then multiband (combination = Phase C + N2)
    t0 = time.time()
    seam_log: list[dict] = []
    hard_weights = apply_graphcut_seams(
        slabs=slabs,
        alphas=alphas,
        cos2_weights=cos2_weights,
        cam_axes_erp=cam_axes_erp,
        feather_sigma=args.feather_sigma,
        seam_log=seam_log,
    )
    t_seam = time.time() - t0
    print(f"[N1C+N2] graphcut seam: {t_seam:.1f}s, {len(seam_log)} pairs", flush=True)

    t0 = time.time()
    erp_cn2 = multiband_blend(slabs, hard_weights, num_bands=args.num_bands, wrap=True)
    t_blend_cn2 = time.time() - t0
    Image.fromarray(erp_cn2).save(args.output_dir / "l1_lidar_graphcut.png")
    print(f"[N1C+N2] Phase C + N2 blend: {t_blend_cn2:.1f}s", flush=True)

    # Seam overlay (for debugging)
    try:
        overlay = draw_seam_overlay_on_erp(erp_cn2, hard_weights)
        Image.fromarray(overlay).save(args.output_dir / "seam_overlay.png")
    except Exception as e:
        print(f"[N1C+N2] seam overlay failed: {e}", flush=True)

    # 7. Baseline legacy L1 for A/B
    t0 = time.time()
    base_slabs, base_alphas, base_weights = [], [], []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, alpha, weight = render_camera_to_erp(
            image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        base_slabs.append(rgb); base_alphas.append(alpha); base_weights.append(weight)
    erp_inf = multiband_blend(base_slabs, base_weights, num_bands=args.num_bands, wrap=True)
    t_baseline = time.time() - t0
    Image.fromarray(erp_inf).save(args.output_dir / "l1_inf.png")
    print(f"[N1C+N2] baseline render+blend: {t_baseline:.1f}s", flush=True)

    summary = {
        "log_dir": str(args.log_dir),
        "anchor_index": args.anchor_index,
        "anchor_ts_ns": anchor_ts,
        "lidar_sweep_ts_ns": sweep_ts,
        "lidar_delta_ms": delta_ms,
        "n_lidar_pts": int(pts.shape[0]),
        "erp_hw": list(erp_hw),
        "num_bands": args.num_bands,
        "feather_sigma": args.feather_sigma,
        "depth_diagnostics": depth_summary,
        "seam_log": seam_log,
        "timing": {
            "depth_build_s": round(t_depth, 2),
            "render_lidar_s": round(t_render, 2),
            "blend_c_only_s": round(t_blend_c, 2),
            "graphcut_seam_s": round(t_seam, 2),
            "blend_c_n2_s": round(t_blend_cn2, 2),
            "baseline_total_s": round(t_baseline, 2),
        },
    }
    with open(args.output_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"[N1C+N2] summary written.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
