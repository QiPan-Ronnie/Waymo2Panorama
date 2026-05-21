"""
Phase 3 P3.2 — IPM ground prior + sphere projection hybrid (CPU).

Pipeline (per anchor frame):
  for each of 7 ring cams:
    load image (504x504 letterboxed PNG) + K + T_ego_cam + local_points  (all from
        Pi3 outputs cached by `run_pi3_multi_anchor.py` / `run_pi3_one_frame.py`)
    detect ground mask via Pi3-projected ego-z (Method A)  with lower-N fallback
        if Pi3 ground coverage is too small to be useful
    IPM-warp ground pixels onto an ERP slab (parallax-free)
    sphere-warp non-ground pixels onto an ERP slab (current L1)
    merge: IPM where IPM has coverage, sphere elsewhere -> single per-cam slab
  multi-band blend the 7 per-cam slabs -> hybrid_erp.png

Also writes (purely for inspection):
  ground_mask_<cam>.png       — bottom-up overlay of detected ground in source img
  ipm_only_<cam>.png          — that cam's IPM slab alone
  sphere_only_<cam>.png       — that cam's sphere slab alone
  per_cam_slab_<cam>.png      — that cam's merged IPM+sphere slab
  ipm_hybrid.png              — final blended ERP
  l1_baseline.png             — pure sphere blend (no IPM), for visual A/B
  summary.json                — coverage stats, params, runtime

The script does NOT need an AV2 log dir: it reads everything from a pre-computed
Pi3 directory (Phase 3 W1 outputs).
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
    """Return everything we need for cam from the cached Pi3 output dir."""
    image = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
    K = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
    T_ego_cam = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
    local_points = np.load(pi3_dir / f"local_points_{cam}.npy")
    conf_path = pi3_dir / f"conf_{cam}.npy"
    conf = np.load(conf_path) if conf_path.exists() else None
    return {
        "image": image,
        "K": K,
        "T_ego_cam": T_ego_cam,
        "local_points": local_points,
        "conf": conf,
    }


def _overlay_mask_png(image_rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Tint the masked region green so we can eyeball the ground detector."""
    out = image_rgb.astype(np.float32)
    green = np.zeros_like(out)
    green[..., 1] = 200.0
    a = 0.4
    sel = mask[..., None].astype(np.float32) * a
    out = out * (1.0 - sel) + green * sel
    return np.clip(out, 0, 255).astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", required=True,
                    help="Existing Pi3 anchor-output dir (image_*.png, "
                         "av2_*_letterboxed_*.npy, av2_T_ego_cam_*.npy, "
                         "local_points_*.npy must be present).")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--ego-z-thresh-m", type=float, default=0.3,
                    help="Ground-detection threshold |ego_z| in meters.")
    ap.add_argument("--min-ground-coverage", type=float, default=0.02,
                    help="If Pi3-derived ground mask covers less than this fraction "
                         "of cam pixels, fall back to lower-N heuristic.")
    ap.add_argument("--fallback-lower-fraction", type=float, default=0.3,
                    help="Lower-N heuristic fallback fraction (rows from bottom).")
    ap.add_argument("--max-distance-m", type=float, default=40.0)
    ap.add_argument("--min-distance-m", type=float, default=1.5)
    ap.add_argument("--panorama-center-z-m", type=float, default=1.5,
                    help="Ego-frame z of the virtual ERP viewer (camera height).")
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--w2p-code", default=None,
                    help="Path to the `code/` dir that holds the waymo2panorama package.")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.blending.multiband import multiband_blend
    # Avoid importing av2_loader (pulls pandas) — RING_CAMS_7 is a simple tuple.
    RING_CAMS_7 = (
        "ring_front_center",
        "ring_front_left",
        "ring_side_left",
        "ring_rear_left",
        "ring_rear_right",
        "ring_side_right",
        "ring_front_right",
    )
    from waymo2panorama.projection.ipm_ground import (
        detect_ground_from_pi3,
        detect_ground_lower_n,
        ipm_project_ground,
    )
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

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
    hybrid_slabs: list[np.ndarray] = []
    hybrid_weights: list[np.ndarray] = []

    t_start = time.time()
    for cam in cams:
        print(f"[ipm-hybrid] cam={cam}", flush=True)
        data = _load_per_cam(pi3_dir, cam)
        image = data["image"]
        K = data["K"]
        T = data["T_ego_cam"]
        lp = data["local_points"]
        H_src, W_src = image.shape[:2]

        # ---- ground detection (Method A, with lower-N fallback) ----
        method = "pi3_ego_z"
        ground_mask = detect_ground_from_pi3(
            local_points_cam=lp,
            T_ego_cam=T,
            ego_z_thresh_m=args.ego_z_thresh_m,
            min_forward_m=1.0,
            max_radius_m=args.max_distance_m,
        )
        cov = float(ground_mask.mean())
        if cov < args.min_ground_coverage:
            method = "lower_n_fallback"
            ground_mask = detect_ground_lower_n((H_src, W_src), args.fallback_lower_fraction)
            cov = float(ground_mask.mean())

        # Inspection overlay
        Image.fromarray(_overlay_mask_png(image, ground_mask)).save(
            out_dir / f"ground_mask_{cam}.png"
        )

        # ---- IPM ground slab ----
        ipm_rgb, ipm_alpha, ipm_weight = ipm_project_ground(
            image=image,
            K=K,
            T_ego_cam=T,
            ground_mask=ground_mask,
            erp_hw=erp_hw,
            max_distance_m=args.max_distance_m,
            min_distance_m=args.min_distance_m,
            panorama_center_z_m=args.panorama_center_z_m,
        )

        # ---- Sphere slab (full image; downstream merge will prefer IPM) ----
        sph_rgb, sph_alpha, sph_weight = render_camera_to_erp(
            image=image,
            K=K,
            T_ego_cam=T,
            erp_hw=erp_hw,
        )

        # ---- Merge: where IPM has coverage prefer IPM (boost its weight), else sphere ----
        merged_rgb = sph_rgb.copy()
        merged_weight = sph_weight.copy()
        if ipm_alpha.any():
            merged_rgb[ipm_alpha] = ipm_rgb[ipm_alpha]
            # IPM weight dominates (so multi-band blending across cams trusts it)
            merged_weight[ipm_alpha] = np.maximum(
                merged_weight[ipm_alpha], ipm_weight[ipm_alpha] * 2.0 + 0.5,
            )

        # Save per-cam inspection slabs
        Image.fromarray(np.clip(sph_rgb, 0, 255).astype(np.uint8)).save(
            out_dir / f"sphere_only_{cam}.png"
        )
        Image.fromarray(np.clip(ipm_rgb, 0, 255).astype(np.uint8)).save(
            out_dir / f"ipm_only_{cam}.png"
        )
        Image.fromarray(np.clip(merged_rgb, 0, 255).astype(np.uint8)).save(
            out_dir / f"per_cam_slab_{cam}.png"
        )

        sphere_slabs.append(sph_rgb)
        sphere_weights.append(sph_weight)
        hybrid_slabs.append(merged_rgb)
        hybrid_weights.append(merged_weight)

        per_cam_stats.append({
            "cam": cam,
            "ground_detector": method,
            "ground_coverage_src_frac": cov,
            "ipm_pixel_count_erp": int(ipm_alpha.sum()),
            "sphere_pixel_count_erp": int(sph_alpha.sum()),
        })

    t_per_cam_s = time.time() - t_start

    # ---- Blend ----
    t_blend = time.time()
    hybrid_erp = multiband_blend(hybrid_slabs, hybrid_weights, num_bands=args.num_bands, wrap=True)
    l1_erp = multiband_blend(sphere_slabs, sphere_weights, num_bands=args.num_bands, wrap=True)
    t_blend_s = time.time() - t_blend

    Image.fromarray(hybrid_erp).save(out_dir / "ipm_hybrid.png")
    Image.fromarray(l1_erp).save(out_dir / "l1_baseline.png")

    # Side-by-side teaser for fast eyeballing
    panel = np.concatenate(
        [l1_erp, np.full((args.erp_h, 8, 3), 0, dtype=np.uint8), hybrid_erp], axis=1,
    )
    Image.fromarray(panel).save(out_dir / "compare_L1_vs_hybrid.png")

    summary = {
        "pi3_dir": str(pi3_dir),
        "erp_hw": list(erp_hw),
        "params": {
            "ego_z_thresh_m": args.ego_z_thresh_m,
            "min_ground_coverage": args.min_ground_coverage,
            "fallback_lower_fraction": args.fallback_lower_fraction,
            "max_distance_m": args.max_distance_m,
            "min_distance_m": args.min_distance_m,
            "panorama_center_z_m": args.panorama_center_z_m,
            "num_bands": args.num_bands,
        },
        "per_cam": per_cam_stats,
        "runtime_s": {
            "per_cam_loop": round(t_per_cam_s, 2),
            "blend": round(t_blend_s, 2),
            "total": round(time.time() - t_start, 2),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"[ipm-hybrid] done -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
