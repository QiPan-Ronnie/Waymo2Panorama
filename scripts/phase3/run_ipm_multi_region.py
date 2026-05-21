"""
Phase 3 P3.3 — IPM multi-region prior (新-C, plan v6.1 route 12).

Extends `run_ipm_hybrid.py` (T14, ground-only) to three regions:
  ground (T14 unchanged) + sky (sphere-equivalent tagging) + building (RANSAC
  per-tile vertical-plane fit).

Pipeline (per anchor frame):
  for each of 7 ring cams:
    load image (504x504), K, T_ego_cam, local_points, conf  (Pi3 cache)
    segment_regions_from_pi3 -> ground / sky / building / unknown masks
    ipm_project_multi_region -> sphere base + per-region warps + composed slab
  multi-band blend the 7 per-cam composed slabs -> multi_region_composite.png
  also produce per-region-only ERPs for inspection.

Outputs:
  region_mask_<cam>.png                 — 4-color overlay (G/B/R/gray)
  ground_only.png                       — multi-band blend of ground-only slabs
  sky_only.png                          — multi-band blend of sky-only slabs
  building_only.png                     — multi-band blend of building-only slabs
  per_cam_slab_<cam>.png                — composed per-cam ERP slab
  multi_region_composite.png            — final composed blend
  l1_baseline.png                       — pure sphere blend (for A/B)
  summary.json                          — per-cam region coverage + plane counts
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


def _load_per_cam(pi3_dir: Path, cam: str) -> dict:
    image = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
    K = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
    T_ego_cam = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
    local_points = np.load(pi3_dir / f"local_points_{cam}.npy")
    conf_path = pi3_dir / f"conf_{cam}.npy"
    conf = np.load(conf_path) if conf_path.exists() else None
    return {
        "image": image, "K": K, "T_ego_cam": T_ego_cam,
        "local_points": local_points, "conf": conf,
    }


def _str_to_bool(s: str) -> bool:
    return s.lower() in ("true", "1", "yes", "y", "on")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--enable-building", type=_str_to_bool, default=False,
                    help="Toggle building plane IPM (default False per design hard floor: "
                         "the per-tile RANSAC plane fit produces stable per-cam composites "
                         "but transfers poorly to held-out cams in cycle eval (-0.33 dB on "
                         "building mask). Ground+sky only is strictly ≥ T14.).")
    ap.add_argument("--enable-sky-routing", type=_str_to_bool, default=True,
                    help="Toggle sky tagging (currently sphere-equivalent).")
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--ground-z-thresh-m", type=float, default=0.30)
    ap.add_argument("--building-min-height-m", type=float, default=0.5)
    ap.add_argument("--building-normal-z-max", type=float, default=0.30)
    ap.add_argument("--building-normal-xy-min", type=float, default=0.85)
    ap.add_argument("--ransac-threshold-m", type=float, default=0.20)
    ap.add_argument("--ransac-iters", type=int, default=50)
    ap.add_argument("--min-inlier-frac", type=float, default=0.40)
    ap.add_argument("--tile-size", type=int, default=32)
    ap.add_argument("--min-pts-per-tile", type=int, default=200)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.projection.ipm_multi_region import (
        ipm_project_multi_region,
        make_region_overlay,
    )
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    RING_CAMS_7 = (
        "ring_front_center", "ring_front_left", "ring_side_left",
        "ring_rear_left", "ring_rear_right", "ring_side_right", "ring_front_right",
    )

    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    pi3_summary_path = pi3_dir / "summary.json"
    pi3_summary = json.loads(pi3_summary_path.read_text()) if pi3_summary_path.exists() else {}
    cams = pi3_summary.get("cameras", list(RING_CAMS_7))

    per_cam_stats: list[dict] = []
    sphere_slabs: list[np.ndarray] = []
    sphere_weights: list[np.ndarray] = []
    composed_slabs: list[np.ndarray] = []
    composed_weights: list[np.ndarray] = []
    ground_slabs: list[np.ndarray] = []
    ground_weights: list[np.ndarray] = []
    sky_slabs: list[np.ndarray] = []
    sky_weights: list[np.ndarray] = []
    bld_slabs: list[np.ndarray] = []
    bld_weights: list[np.ndarray] = []

    segment_kwargs = dict(
        ground_z_thresh_m=args.ground_z_thresh_m,
        building_min_height_m=args.building_min_height_m,
        building_normal_z_max=args.building_normal_z_max,
        building_normal_xy_min=args.building_normal_xy_min,
    )
    building_kwargs = dict(
        tile_size=args.tile_size,
        ransac_iters=args.ransac_iters,
        ransac_threshold_m=args.ransac_threshold_m,
        min_inlier_frac=args.min_inlier_frac,
        min_pts_per_tile=args.min_pts_per_tile,
    )

    t_start = time.time()
    for cam in cams:
        print(f"[multi-region] cam={cam}", flush=True)
        data = _load_per_cam(pi3_dir, cam)
        image = data["image"]
        K = data["K"]
        T = data["T_ego_cam"]
        lp = data["local_points"]
        conf = data["conf"]

        slab = ipm_project_multi_region(
            image=image, K=K, T_ego_cam=T,
            local_points_cam=lp, conf=conf,
            erp_hw=erp_hw,
            enable_building=args.enable_building,
            enable_sky_routing=args.enable_sky_routing,
            segment_kwargs=segment_kwargs,
            building_kwargs=building_kwargs,
        )

        cov = slab.masks.coverage()
        info = slab.info
        per_cam_stats.append({
            "cam": cam,
            "region_coverage": cov,
            "plane_count": int(info.get("plane_count", 0)),
            "inlier_frac_mean": float(info.get("inlier_frac_mean", 0.0)),
            "ground_pixel_count_erp": int(slab.ground_alpha.sum()),
            "sky_pixel_count_erp": int(slab.sky_alpha.sum()),
            "building_pixel_count_erp": int(slab.building_alpha.sum()),
            "sphere_pixel_count_erp": int(slab.sphere_alpha.sum()),
        })

        # Save per-cam inspection assets
        Image.fromarray(make_region_overlay(image, slab.masks)).save(
            out_dir / f"region_mask_{cam}.png"
        )
        Image.fromarray(np.clip(slab.merged_rgb, 0, 255).astype(np.uint8)).save(
            out_dir / f"per_cam_slab_{cam}.png"
        )

        sphere_slabs.append(slab.sphere_rgb)
        sphere_weights.append(slab.sphere_weight)
        composed_slabs.append(slab.merged_rgb)
        composed_weights.append(slab.merged_weight)
        ground_slabs.append(slab.ground_rgb)
        ground_weights.append(slab.ground_weight)
        sky_slabs.append(slab.sky_rgb)
        sky_weights.append(slab.sky_weight)
        bld_slabs.append(slab.building_rgb)
        bld_weights.append(slab.building_weight)

    t_per_cam_s = time.time() - t_start

    # ---- Blend ----
    t_blend = time.time()
    composite_erp = multiband_blend(composed_slabs, composed_weights, num_bands=args.num_bands, wrap=True)
    l1_erp = multiband_blend(sphere_slabs, sphere_weights, num_bands=args.num_bands, wrap=True)
    # Per-region inspection: for ground/sky/building blends, use the slab values
    # directly (zeros elsewhere -> blank ERP background outside the slab).
    ground_erp = multiband_blend(ground_slabs, ground_weights, num_bands=args.num_bands, wrap=True)
    sky_erp = multiband_blend(sky_slabs, sky_weights, num_bands=args.num_bands, wrap=True)
    bld_erp = multiband_blend(bld_slabs, bld_weights, num_bands=args.num_bands, wrap=True)
    t_blend_s = time.time() - t_blend

    Image.fromarray(composite_erp).save(out_dir / "multi_region_composite.png")
    Image.fromarray(l1_erp).save(out_dir / "l1_baseline.png")
    Image.fromarray(ground_erp).save(out_dir / "ground_only.png")
    Image.fromarray(sky_erp).save(out_dir / "sky_only.png")
    Image.fromarray(bld_erp).save(out_dir / "building_only.png")

    # 3-way comparison panel
    gap = np.full((args.erp_h, 8, 3), 0, dtype=np.uint8)
    panel = np.concatenate([l1_erp, gap, composite_erp], axis=1)
    Image.fromarray(panel).save(out_dir / "compare_L1_vs_multi_region.png")

    summary = {
        "pi3_dir": str(pi3_dir),
        "erp_hw": list(erp_hw),
        "params": {
            "enable_building": args.enable_building,
            "enable_sky_routing": args.enable_sky_routing,
            "num_bands": args.num_bands,
            **segment_kwargs,
            **building_kwargs,
        },
        "per_cam": per_cam_stats,
        "mean": {
            "ground_cov": float(np.mean([s["region_coverage"]["ground"] for s in per_cam_stats])),
            "sky_cov": float(np.mean([s["region_coverage"]["sky"] for s in per_cam_stats])),
            "building_cov": float(np.mean([s["region_coverage"]["building"] for s in per_cam_stats])),
            "unknown_cov": float(np.mean([s["region_coverage"]["unknown"] for s in per_cam_stats])),
            "plane_count": float(np.mean([s["plane_count"] for s in per_cam_stats])),
            "inlier_frac_mean": float(np.mean([s["inlier_frac_mean"] for s in per_cam_stats])),
        },
        "runtime_s": {
            "per_cam_loop": round(t_per_cam_s, 2),
            "blend": round(t_blend_s, 2),
            "total": round(time.time() - t_start, 2),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["mean"], indent=2))
    print(f"[multi-region] done -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
