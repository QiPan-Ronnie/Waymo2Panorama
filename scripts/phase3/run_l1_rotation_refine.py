"""
Phase 3 WS2 v3 — L1 sphere stitch with rotation-refined extrinsics.

Architecturally different from v1/v2 (which warped each cam image to a common
reference plane — geometrically impossible for ring cams pointing in different
directions, cf. progress.md "v3 finding"):

  1. For each adjacent ring-cam pair, estimate the OBSERVED rotation R from
     DISK+LightGlue feature matches via rotation-only Procrustes.
  2. Bundle-adjust 6 of 7 cams' rotation refinements (3 DOF each = 18 unknowns,
     anchored at ring_front_center to fix the gauge) to minimize total
     observed-vs-predicted pair rotation residual.
  3. Apply the refinement to each cam's T_ego_cam: R_ego_cam_new = R_ego_cam @ dR.
  4. Render L1 sphere with REFINED extrinsics (NO image warping at any stage).
  5. Multi-band blend the 7 spherical slabs.

The overlap regions align better because each cam's projection direction has
been nudged by a few millidegrees to match the geometric reality observed
in the features. This is the OpenCV stitcher / AutoStitch pattern, applied as
a refinement to calibrated extrinsics.

Usage:
    python scripts/phase3/run_l1_rotation_refine.py \
        --pi3-dir outputs/phase3/p3.1_multi_anchor/anchor_060 \
        --output-dir outputs/phase3/p3.X_l1_rot_refine/anchor_060 \
        --device cuda

A/B baseline (no refinement, plain L1):
    --no-refine
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


def _load_pi3_cam(pi3_dir: Path, cam: str) -> dict:
    return {
        "image": np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")),
        "K": np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy").astype(np.float64),
        "T_ego_cam": np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy").astype(np.float64),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--anchor-cam", default="ring_front_center",
                    help="Gauge anchor cam (its rotation refinement is fixed to identity).")
    ap.add_argument("--no-refine", action="store_true",
                    help="Skip BA, just plain L1 with calibrated extrinsics (for A/B compare).")
    ap.add_argument("--no-ego-mask", action="store_true")
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    ap.add_argument("--max-num-keypoints", type=int, default=2048)
    ap.add_argument("--lightglue-min-confidence", type=float, default=0.2)
    ap.add_argument("--ransac-threshold-px", type=float, default=3.0)
    ap.add_argument("--min-inliers", type=int, default=30)
    ap.add_argument("--verbose-ba", action="store_true")
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.alignment.pair_homography import ADJACENT_PAIRS
    from waymo2panorama.alignment.rotation_refinement import (
        estimate_pair_rotation_observed,
        bundle_adjust_rotations,
        apply_rotation_refinements,
        R_to_axis_angle,
    )
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    cams = list(RING_CAMS_7)
    if args.anchor_cam not in cams:
        ap.error(f"--anchor-cam {args.anchor_cam!r} not in RING_CAMS_7")

    print(f"[rot-refine] erp_hw={erp_hw}, refine={not args.no_refine}, "
          f"anchor={args.anchor_cam}, device={args.device}", flush=True)

    # Load all 7 cams
    per_cam = {cam: _load_pi3_cam(args.pi3_dir, cam) for cam in cams}
    cam_image_shapes = {cam: per_cam[cam]["image"].shape[:2] for cam in cams}
    ego_masks = build_ego_masks(cams, cam_image_shapes, enabled=not args.no_ego_mask)

    pair_R_observed: dict[tuple[str, str], np.ndarray] = {}
    pair_log: list[dict] = []
    dR_per_cam: dict[str, np.ndarray] = {c: np.eye(3, dtype=np.float64) for c in cams}

    if not args.no_refine:
        t_pair0 = time.time()
        for cam_a, cam_b in ADJACENT_PAIRS:
            if cam_a not in per_cam or cam_b not in per_cam:
                continue
            res = estimate_pair_rotation_observed(
                per_cam[cam_a]["image"], per_cam[cam_b]["image"],
                per_cam[cam_a]["K"], per_cam[cam_b]["K"],
                device=args.device,
                max_num_keypoints=args.max_num_keypoints,
                lightglue_min_confidence=args.lightglue_min_confidence,
                ransac_threshold_px=args.ransac_threshold_px,
                min_inliers=args.min_inliers,
            )
            if res is None:
                pair_log.append({
                    "cam_a": cam_a, "cam_b": cam_b,
                    "status": "no_fit", "n_inliers": 0,
                })
                print(f"  pair {cam_a:24s} -> {cam_b:24s}: NO FIT", flush=True)
                continue
            R = res["R_obs_a_to_b"]
            pair_R_observed[(cam_a, cam_b)] = R
            ax = R_to_axis_angle(R)
            ang_deg = float(np.linalg.norm(ax)) * 180.0 / np.pi
            cam_T_ego_a = per_cam[cam_a]["T_ego_cam"][:3, :3]
            cam_T_ego_b = per_cam[cam_b]["T_ego_cam"][:3, :3]
            R_cal = cam_T_ego_b.T @ cam_T_ego_a
            delta_deg = float(np.linalg.norm(R_to_axis_angle(R @ R_cal.T))) * 180.0 / np.pi
            pair_log.append({
                "cam_a": cam_a, "cam_b": cam_b,
                "status": "ok",
                "n_inliers": int(res["inlier_count"]),
                "n_matches": int(res["n_matches"]),
                "residual_px": float(res["residual_px"]),
                "observed_R_angle_deg": ang_deg,
                "deviation_from_calibrated_deg": delta_deg,
            })
            print(f"  pair {cam_a:24s} -> {cam_b:24s}: "
                  f"n_in={int(res['inlier_count']):4d}  res={res['residual_px']:4.2f}px  "
                  f"obs_ang={ang_deg:5.1f}deg  delta_vs_cal={delta_deg:5.2f}deg", flush=True)
        t_pair_s = time.time() - t_pair0

        cam_T_ego_cam = {c: per_cam[c]["T_ego_cam"] for c in cams}
        t_ba0 = time.time()
        dR_per_cam = bundle_adjust_rotations(
            pair_R_observed,
            cam_T_ego_cam,
            cam_names=cams,
            anchor_cam=args.anchor_cam,
            verbose=args.verbose_ba,
        )
        t_ba_s = time.time() - t_ba0
        print(f"[rot-refine] BA done in {t_ba_s:.2f}s", flush=True)
        for c in cams:
            ax = R_to_axis_angle(dR_per_cam[c])
            ang_deg = float(np.linalg.norm(ax)) * 180.0 / np.pi
            print(f"  refine {c:24s}: |dR| = {ang_deg:.3f} deg", flush=True)

        cam_T_ego_cam_refined = apply_rotation_refinements(cam_T_ego_cam, dR_per_cam)
    else:
        t_pair_s = 0.0; t_ba_s = 0.0
        cam_T_ego_cam_refined = {c: per_cam[c]["T_ego_cam"] for c in cams}

    # Render L1 sphere with (possibly refined) extrinsics
    t_proj0 = time.time()
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in cams:
        d = per_cam[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=d["image"], K=d["K"],
            T_ego_cam=cam_T_ego_cam_refined[cam],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        slabs.append(rgb); weights.append(w)
    t_proj_s = time.time() - t_proj0

    t_blend0 = time.time()
    erp = multiband_blend(slabs, weights, num_bands=args.num_bands, wrap=True)
    t_blend_s = time.time() - t_blend0

    out_png = out_dir / "l1_rot_refine.png"
    Image.fromarray(erp).save(out_png)
    print(f"[rot-refine] wrote {out_png}", flush=True)

    summary = {
        "route": "WS2 v3 / L1 + rotation-refine extrinsics",
        "mode": "no-refine (plain L1)" if args.no_refine else "refined",
        "pi3_dir": str(args.pi3_dir),
        "anchor_cam": args.anchor_cam,
        "erp_hw": list(erp_hw),
        "params": {
            "max_num_keypoints": args.max_num_keypoints,
            "lightglue_min_confidence": args.lightglue_min_confidence,
            "ransac_threshold_px": args.ransac_threshold_px,
            "min_inliers": args.min_inliers,
            "no_ego_mask": bool(args.no_ego_mask),
        },
        "pair_log": pair_log,
        "refined_dR_angle_deg": {
            c: float(np.linalg.norm(R_to_axis_angle(dR_per_cam[c]))) * 180.0 / np.pi
            for c in cams
        },
        "runtime_s": {
            "pair_estimate": round(t_pair_s, 3),
            "bundle_adjust": round(t_ba_s, 3),
            "projection": round(t_proj_s, 3),
            "blend": round(t_blend_s, 3),
        },
        "outputs": {"l1_rot_refine": str(out_png.resolve())},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[rot-refine] summary -> {out_dir / 'summary.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
