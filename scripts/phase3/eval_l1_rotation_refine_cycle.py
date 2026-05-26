"""
Phase 3 WS2 v3 — Held-out cycle PSNR eval with rotation-refined extrinsics.

For each anchor x each held-out cam_h:
  1. Estimate pair rotations among the OTHER 6 cams (excluding pairs involving
     cam_h) -- clean held-out, no leakage.
  2. Bundle-adjust 5 refinements for the non-anchor non-held cams.
  3. Apply refinements to T_ego_cam for the other 6 cams.
  4. Reconstruct cam_h's view by projecting the other 6 cams' images into
     cam_h's plane via cos^2 feathered blend (using cam_h's ORIGINAL
     calibration -- it's the held-out GT; only the other 6 are refined).
  5. Score PSNR(reconstruction, cam_h_GT_image).
  6. Compare against baseline (no refinement, all 6 use calibrated extrinsics).

The headline number is the same one in scripts/phase2/eval_cycle_consistency.py:
"L1 = 12.34 dB" on the 7 ring cams of AV2 log 02a00399.

Usage:
    python scripts/phase3/eval_l1_rotation_refine_cycle.py \
        --pi3-cache-root outputs/phase3/p3.1_multi_anchor \
        --anchors 0 60 90 150 \
        --output-dir outputs/phase3/p3.X_l1_rot_refine/eval_cycle \
        --device cuda
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"
RING_CAMS_7 = (
    "ring_front_center", "ring_front_left", "ring_side_left", "ring_rear_left",
    "ring_rear_right", "ring_side_right", "ring_front_right",
)


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _psnr(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    if mask.sum() == 0:
        return float("nan")
    a = a.astype(np.float64); b = b.astype(np.float64)
    mse = ((a - b) ** 2)[mask].mean()
    if mse <= 1e-12:
        return float("inf")
    return 20.0 * np.log10(255.0) - 10.0 * np.log10(mse)


def _reconstruct_cam_plane(
    holdout_cam: str,
    other_cams: list[str],
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_rgb: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """Same L1 reconstruction as scripts/phase2/eval_cycle_consistency.reconstruct_l1.

    Per held-out pixel: compute ego ray, transform into each OTHER cam's frame,
    sample with cv2.remap, cos^2 feather blend. Returns (rgb, mask).
    """
    K_h = cam_K[holdout_cam]
    T_ego_cam_h = cam_T_ego_cam[holdout_cam]
    H, W = cam_rgb[holdout_cam].shape[:2]

    uu, vv = np.meshgrid(np.arange(W), np.arange(H))
    pix_h = np.stack([uu, vv, np.ones_like(uu)], axis=-1).astype(np.float64)
    K_h_inv = np.linalg.inv(K_h)
    d_cam_h = pix_h @ K_h_inv.T
    d_cam_h = d_cam_h / np.linalg.norm(d_cam_h, axis=-1, keepdims=True)
    R_ego_cam_h = T_ego_cam_h[:3, :3]
    d_ego = d_cam_h @ R_ego_cam_h.T

    rgb_sum = np.zeros((H, W, 3), dtype=np.float32)
    w_sum = np.zeros((H, W), dtype=np.float32)
    for cam_j in other_cams:
        K_j = cam_K[cam_j]
        T_ego_cam_j = cam_T_ego_cam[cam_j]
        rgb_j = cam_rgb[cam_j].astype(np.float32)
        H_j, W_j = rgb_j.shape[:2]
        R_cam_ego_j = T_ego_cam_j[:3, :3].T
        d_cam_j = d_ego @ R_cam_ego_j.T
        z_j = d_cam_j[..., 2]
        in_front = z_j > 1e-6
        z_safe = np.where(in_front, z_j, 1.0)
        u_j = K_j[0, 0] * d_cam_j[..., 0] / z_safe + K_j[0, 2]
        v_j = K_j[1, 1] * d_cam_j[..., 1] / z_safe + K_j[1, 2]
        margin = 0.5
        in_bounds = (u_j >= margin) & (u_j <= W_j - 1 - margin) & (v_j >= margin) & (v_j <= H_j - 1 - margin)
        valid = in_front & in_bounds
        map_x = np.where(valid, u_j, -1.0).astype(np.float32)
        map_y = np.where(valid, v_j, -1.0).astype(np.float32)
        sampled = cv2.remap(rgb_j, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0.0, 0.0, 0.0))
        cos_axis = np.clip(z_j, 0.0, 1.0)
        w_j = (cos_axis ** 2) * valid.astype(np.float32)
        rgb_sum += sampled * w_j[..., None]
        w_sum += w_j
    w_safe = np.where(w_sum > 1e-6, w_sum, 1.0)
    rgb_out = rgb_sum / w_safe[..., None]
    rgb_out = np.where(w_sum[..., None] > 1e-6, rgb_out, 0.0)
    mask = w_sum > 1e-6
    return rgb_out.astype(np.float32), mask


def _eval_one_anchor(
    pi3_dir: Path,
    device: str,
    max_num_keypoints: int,
    lightglue_min_confidence: float,
    ransac_threshold_px: float,
    min_inliers: int,
    anchor_cam_for_BA: str,
    include_holdout_pairs: bool,
    l2_reg_weight: float,
    max_pair_deviation_deg: float,
) -> dict:
    from waymo2panorama.alignment.pair_homography import ADJACENT_PAIRS
    from waymo2panorama.alignment.rotation_refinement import (
        estimate_pair_rotation_observed,
        bundle_adjust_rotations,
        apply_rotation_refinements,
        R_to_axis_angle,
    )

    cams = list(RING_CAMS_7)
    cam_K: dict[str, np.ndarray] = {}
    cam_T: dict[str, np.ndarray] = {}
    cam_rgb: dict[str, np.ndarray] = {}
    for cam in cams:
        cam_K[cam] = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy").astype(np.float64)
        cam_T[cam] = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy").astype(np.float64)
        cam_rgb[cam] = np.asarray(
            Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")
        ).astype(np.uint8)

    rows: list[dict] = []
    for holdout in cams:
        other_cams = [c for c in cams if c != holdout]

        # Estimate pair rotations among allowed pairs
        pair_R_obs: dict[tuple[str, str], np.ndarray] = {}
        for cam_a, cam_b in ADJACENT_PAIRS:
            if not include_holdout_pairs and (cam_a == holdout or cam_b == holdout):
                continue
            res = estimate_pair_rotation_observed(
                cam_rgb[cam_a], cam_rgb[cam_b],
                cam_K[cam_a], cam_K[cam_b],
                device=device, max_num_keypoints=max_num_keypoints,
                lightglue_min_confidence=lightglue_min_confidence,
                ransac_threshold_px=ransac_threshold_px,
                min_inliers=min_inliers,
            )
            if res is None:
                continue
            # Drop pair fits that deviate too much from calibration -- those
            # are corrupted by near-field parallax (the rotation-only assumption
            # breaks down when matches span varied depths). Trust calibration
            # over noisy feature fits in those cases.
            R = res["R_obs_a_to_b"]
            R_cal = cam_T[cam_b][:3, :3].T @ cam_T[cam_a][:3, :3]
            from waymo2panorama.alignment.rotation_refinement import R_to_axis_angle as _R2aa
            deviation_deg = float(np.linalg.norm(_R2aa(R @ R_cal.T))) * 180.0 / np.pi
            if max_pair_deviation_deg > 0 and deviation_deg > max_pair_deviation_deg:
                continue  # likely a parallax-biased fit, drop
            pair_R_obs[(cam_a, cam_b)] = R

        # Pick BA anchor: use the requested anchor if it's NOT held-out;
        # otherwise pick its 1-hop neighbor.
        ba_anchor = anchor_cam_for_BA
        if ba_anchor == holdout:
            anchor_idx = cams.index(holdout)
            ba_anchor = cams[(anchor_idx + 1) % len(cams)]

        dR = bundle_adjust_rotations(
            pair_R_obs, cam_T, cam_names=cams, anchor_cam=ba_anchor, verbose=False,
            l2_reg_weight=l2_reg_weight,
        )
        cam_T_refined = apply_rotation_refinements(cam_T, dR)

        # Baseline reconstruction (calibrated extrinsics, no refinement)
        rgb_baseline, mask_baseline = _reconstruct_cam_plane(
            holdout, other_cams, cam_K, cam_T, cam_rgb,
        )
        # v3 reconstruction (refined extrinsics for the other 6 cams; held-out
        # cam_h still uses ORIGINAL extrinsics since that's how its GT plane
        # was acquired -- it's the test target, not part of the refined set.)
        cam_T_for_recon = dict(cam_T_refined)
        cam_T_for_recon[holdout] = cam_T[holdout]
        rgb_v3, mask_v3 = _reconstruct_cam_plane(
            holdout, other_cams, cam_K, cam_T_for_recon, cam_rgb,
        )

        gt = cam_rgb[holdout]
        common = mask_baseline & mask_v3
        psnr_b = _psnr(gt, np.clip(rgb_baseline, 0, 255).astype(np.uint8), common)
        psnr_v = _psnr(gt, np.clip(rgb_v3, 0, 255).astype(np.uint8), common)
        delta = psnr_v - psnr_b
        rows.append({
            "cam": holdout,
            "ba_anchor": ba_anchor,
            "n_pairs_used": len(pair_R_obs),
            "PSNR_L1": psnr_b,
            "PSNR_L1_rot_refine": psnr_v,
            "delta_dB": delta,
            "coverage_common_frac": float(common.mean()),
            "max_dR_deg": float(max(
                np.linalg.norm(R_to_axis_angle(dR[c])) for c in cams
            )) * 180.0 / np.pi,
        })
        print(f"  {holdout:24s} n_pairs={len(pair_R_obs):d}  L1={psnr_b:6.3f}  "
              f"L1+rot={psnr_v:6.3f}  delta={delta:+6.3f} dB", flush=True)

    finite_d = [r["delta_dB"] for r in rows if np.isfinite(r["delta_dB"])]
    finite_b = [r["PSNR_L1"] for r in rows if np.isfinite(r["PSNR_L1"])]
    finite_v = [r["PSNR_L1_rot_refine"] for r in rows if np.isfinite(r["PSNR_L1_rot_refine"])]
    agg = {
        "mean_PSNR_L1": float(np.mean(finite_b)) if finite_b else None,
        "mean_PSNR_L1_rot_refine": float(np.mean(finite_v)) if finite_v else None,
        "mean_delta_dB": float(np.mean(finite_d)) if finite_d else None,
        "n_better": int(sum(1 for d in finite_d if d > 0)),
        "n_worse": int(sum(1 for d in finite_d if d < 0)),
        "n_tied": int(sum(1 for d in finite_d if d == 0)),
    }
    return {
        "pi3_dir": str(pi3_dir),
        "include_holdout_pairs": include_holdout_pairs,
        "per_cam": rows,
        "aggregate": agg,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-cache-root", type=Path, required=True)
    ap.add_argument("--anchors", nargs="+", type=int, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    ap.add_argument("--max-num-keypoints", type=int, default=2048)
    ap.add_argument("--lightglue-min-confidence", type=float, default=0.2)
    ap.add_argument("--ransac-threshold-px", type=float, default=3.0)
    ap.add_argument("--min-inliers", type=int, default=30)
    ap.add_argument("--anchor-cam-for-BA", default="ring_front_center")
    ap.add_argument("--l2-reg-weight", type=float, default=0.10,
                    help="L2 reg on dR axis-angle params. Higher = stronger pull to "
                         "identity (smaller refinements). Default 0.05 caps dRs to "
                         "~0.5-1 deg, defending against bad pair fits from parallax bias.")
    ap.add_argument("--max-pair-deviation-deg", type=float, default=1.0,
                    help="Drop pair fits whose observed R deviates from calibration "
                         "by more than this angle. Near-field parallax can give bogus "
                         "fits with 5+ deg deviation; default 1.5 deg keeps only "
                         "well-conditioned pairs.")
    ap.add_argument("--include-holdout-pairs", action="store_true",
                    help="If set, ALSO use stereo pairs involving the held-out cam. "
                         "Methodologically 'leaky' but useful to diagnose whether the "
                         "clean held-out metric is well-conditioned.")
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    per_anchor: list[dict] = []
    for a in args.anchors:
        pi3 = args.pi3_cache_root / f"anchor_{int(a):03d}"
        if not pi3.exists():
            raise FileNotFoundError(f"missing: {pi3}")
        print(f"\n=== anchor {a:03d}: {pi3.name} (include_holdout_pairs={args.include_holdout_pairs}) ===", flush=True)
        t0 = time.time()
        r = _eval_one_anchor(
            pi3_dir=pi3, device=args.device,
            max_num_keypoints=args.max_num_keypoints,
            lightglue_min_confidence=args.lightglue_min_confidence,
            ransac_threshold_px=args.ransac_threshold_px,
            min_inliers=args.min_inliers,
            anchor_cam_for_BA=args.anchor_cam_for_BA,
            include_holdout_pairs=args.include_holdout_pairs,
            l2_reg_weight=args.l2_reg_weight,
            max_pair_deviation_deg=args.max_pair_deviation_deg,
        )
        r["anchor_idx"] = a; r["wall_s"] = round(time.time() - t0, 2)
        per_anchor.append(r)
        agg = r["aggregate"]
        print(f"  ANCHOR {a:03d} AGG: L1={agg['mean_PSNR_L1']:.3f}  "
              f"L1+rot={agg['mean_PSNR_L1_rot_refine']:.3f}  "
              f"delta={agg['mean_delta_dB']:+.3f} dB  "
              f"better/worse/tied = {agg['n_better']}/{agg['n_worse']}/{agg['n_tied']}",
              flush=True)

    # Overall aggregate
    all_b = []; all_v = []; all_d = []
    for r in per_anchor:
        for row in r["per_cam"]:
            if np.isfinite(row["PSNR_L1"]): all_b.append(row["PSNR_L1"])
            if np.isfinite(row["PSNR_L1_rot_refine"]): all_v.append(row["PSNR_L1_rot_refine"])
            if np.isfinite(row["delta_dB"]): all_d.append(row["delta_dB"])
    overall = {
        "n_anchors": len(per_anchor),
        "n_measurements": len(all_d),
        "global_mean_PSNR_L1": float(np.mean(all_b)) if all_b else None,
        "global_mean_PSNR_L1_rot_refine": float(np.mean(all_v)) if all_v else None,
        "global_mean_delta_dB": float(np.mean(all_d)) if all_d else None,
        "n_better": int(sum(1 for d in all_d if d > 0)),
        "n_worse": int(sum(1 for d in all_d if d < 0)),
        "include_holdout_pairs": args.include_holdout_pairs,
        "anchor_cam_for_BA": args.anchor_cam_for_BA,
    }
    summary = {
        "route": "WS2 v3 / L1 + rotation refine, held-out cycle PSNR (cam plane)",
        "per_anchor": per_anchor,
        "overall": overall,
    }
    out_path = out_dir / "eval_l1_rotation_refine_cycle.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n=== OVERALL (n={overall['n_measurements']} measurements) ===")
    print(f"  PSNR_L1               = {overall['global_mean_PSNR_L1']:.3f} dB")
    print(f"  PSNR_L1+rotation     = {overall['global_mean_PSNR_L1_rot_refine']:.3f} dB")
    print(f"  delta                = {overall['global_mean_delta_dB']:+.3f} dB")
    print(f"  better/worse         = {overall['n_better']}/{overall['n_worse']}")
    print(f"  json -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
