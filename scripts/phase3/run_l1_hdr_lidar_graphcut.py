"""
Combined: N1+LiDAR cam-translation-aware projection + 新-E HDR cross-cam color
correction + 新-B graphcut hard-seam selection. The full stack.

This driver puts all three shipped improvements together. Per the 5.22 §1b
color-shift audit (2026-05-26): AV2 has mean 5.5 dB cross-cam lum gap,
which is what makes the geometric seams visible after N1 projection. Adding
HDR pre-step should match cam brightness BEFORE N1 + seam selection, giving
the cleanest geometric+photometric result we can produce in this paradigm.

Outputs:
    l1_inf.png                       — legacy L1 baseline (no fixes)
    l1_hdr.png                       — L1 + HDR only (shipped 新-E)
    l1_lidar.png                     — L1 + N1+LiDAR (Phase C)
    l1_hdr_lidar.png                 — L1 + HDR + N1+LiDAR
    l1_hdr_lidar_graphcut.png        — L1 + HDR + N1+LiDAR + graphcut (full stack)
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
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent / DEFAULT_W2P_CODE_REL)
    args = ap.parse_args()

    _wire_imports(args.w2p_code)
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.blending.graphcut_seam import (
        apply_graphcut_seams, _erp_column_for_axis_world,
    )
    from waymo2panorama.color.hdr_gain_estimate import (
        extract_overlap_pixels, global_color_correction, apply_correction,
    )
    from waymo2panorama.depth.lidar_to_erp_depth import (
        load_lidar_sweep_nearest_to_ts, project_lidar_to_erp_depth,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    anchor_ts = ts_all[args.anchor_index]
    frame = loader.load_synced_frame(anchor_ts)
    pts, sweep_ts, delta_ms = load_lidar_sweep_nearest_to_ts(args.log_dir, anchor_ts)
    depth_map, depth_summary = project_lidar_to_erp_depth(pts, erp_hw=erp_hw)
    print(f"[stack] anchor={args.anchor_index} lidar_delta={delta_ms:.1f}ms "
          f"depth: hit={depth_summary['hit_pct']}%", flush=True)

    # Render both projection modes (legacy and N1+LiDAR)
    def render_set(conv_dist):
        slabs, alphas, weights = [], [], []
        for cam in RING_CAMS_7:
            calib = frame.calibrations[cam]
            rgb, alpha, w = render_camera_to_erp(
                image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
                erp_hw=erp_hw, convergence_distance_m=conv_dist,
            )
            slabs.append(rgb); alphas.append(alpha); weights.append(w)
        return slabs, alphas, weights

    t0 = time.time()
    slabs_inf, alphas_inf, weights_inf = render_set(None)
    slabs_n1, alphas_n1, weights_n1 = render_set(depth_map)
    print(f"[stack] rendered legacy+N1 in {time.time()-t0:.1f}s", flush=True)

    # Solve HDR correction (on the LEGACY slabs — overlap pixels in the original
    # projection space; the per-cam color correction is projection-agnostic).
    t0 = time.time()
    overlaps = extract_overlap_pixels(slabs_inf, alphas_inf)
    corrections = global_color_correction(overlaps, num_cams=len(RING_CAMS_7),
                                          verbose=False)
    print(f"[stack] HDR solve: {len(overlaps)} pairs, {time.time()-t0:.1f}s", flush=True)
    print(f"[stack] HDR gains: {[round(float(c[0]), 3) for c in corrections]}", flush=True)

    def apply_corr_all(slabs, corrections):
        return [apply_correction(s, c) for s, c in zip(slabs, corrections)]

    slabs_inf_hdr = apply_corr_all(slabs_inf, corrections)
    slabs_n1_hdr = apply_corr_all(slabs_n1, corrections)

    cam_axes_erp = [
        _erp_column_for_axis_world(frame.calibrations[c].T_ego_cam, args.erp_w)
        for c in RING_CAMS_7
    ]

    # Outputs
    out_jobs = [
        ("l1_inf.png",                 slabs_inf,     weights_inf,        False),
        ("l1_hdr.png",                 slabs_inf_hdr, weights_inf,        False),
        ("l1_lidar.png",               slabs_n1,      weights_n1,         False),
        ("l1_hdr_lidar.png",           slabs_n1_hdr,  weights_n1,         False),
        ("l1_hdr_lidar_graphcut.png",  slabs_n1_hdr,  weights_n1,         True),
    ]
    timings = {}
    for fname, sl, w, use_gc in out_jobs:
        if use_gc:
            t0 = time.time()
            w = apply_graphcut_seams(
                slabs=sl, alphas=alphas_n1, cos2_weights=w,
                cam_axes_erp=cam_axes_erp, feather_sigma=3.0,
            )
            t_gc = time.time() - t0
            print(f"[stack] graphcut for {fname}: {t_gc:.1f}s", flush=True)
        t0 = time.time()
        erp = multiband_blend(sl, w, num_bands=args.num_bands, wrap=True)
        t_b = time.time() - t0
        Image.fromarray(erp).save(args.output_dir / fname)
        timings[fname] = round(t_b, 2)
        print(f"[stack] {fname}: blend {t_b:.1f}s", flush=True)

    # Also make thumbnails
    for fname in [j[0] for j in out_jobs]:
        im = Image.open(args.output_dir / fname).copy()
        im.thumbnail((1024, 512))
        im.save(args.output_dir / fname.replace(".png", "_thumb.png"))

    summary = {
        "log_dir": str(args.log_dir),
        "anchor_index": args.anchor_index,
        "erp_hw": list(erp_hw),
        "lidar_delta_ms": delta_ms,
        "depth_diagnostics": depth_summary,
        "hdr_gains_per_cam": [c.tolist() for c in corrections],
        "blend_timings_s": timings,
    }
    with open(args.output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"[stack] done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
