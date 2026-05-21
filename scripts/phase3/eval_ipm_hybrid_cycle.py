"""
Phase 3 P3.2 — cycle-consistency eval, IPM-hybrid vs L1 baseline.

For each cam:
  GT = AV2 letterboxed image (already cached by run_pi3_one_frame.py)
  L1 recon = `reconstruct_l1` from `scripts/phase2/eval_cycle_consistency.py`
  IPM-hybrid recon = combine `reconstruct_l1` (sphere) for non-ground pixels of
       the held-out view with an IPM-based ground reconstruction for the rest.

The IPM ground reconstruction:
  For each held-out-cam pixel (u_h, v_h):
    build cam-frame ray d_cam_h = K_h^-1 @ (u, v, 1)
    rotate to ego: d_ego = R_ego_cam_h @ d_cam_h
    intersect with ground plane z=0 at the cam origin offset (using T_ego_cam_h)
    if intersection is valid (in front of cam, in [min_d, max_d] radius):
      query each OTHER cam: project the ground point (x, y, 0) into its image
        plane, sample RGB
      weighted average (cos^2 feather, like sphere)

This isolates the IPM contribution from the sphere contribution: the metric
asks "does adding IPM for ground beat pure sphere for ground?".

Outputs:
  per_cam metrics (cov, PSNR/SSIM/MAE on intersection mask)
  cycle_ipm.json + cycle_ipm_bars.png
  reconstruction_<cam>.png    (GT | L1 | Hybrid)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_W2P_CODE_REL = "../../code"
DEFAULT_PHASE2_REL = "../phase2"


def _wire_imports(w2p_code: Path, phase2_dir: Path) -> None:
    for p in (w2p_code, phase2_dir):
        if not p.exists():
            raise FileNotFoundError(f"required path missing: {p}")
        sys.path.insert(0, str(p))


def psnr(a: np.ndarray, b: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    a = a.astype(np.float64); b = b.astype(np.float64)
    if mask is None:
        mse = np.mean((a - b) ** 2)
    else:
        if mask.sum() == 0:
            return float("nan")
        mse = ((a - b) ** 2)[mask].mean()
    if mse <= 1e-12:
        return float("inf")
    return 20.0 * np.log10(255.0) - 10.0 * np.log10(mse)


def l1_mae(a: np.ndarray, b: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    a = a.astype(np.float64); b = b.astype(np.float64)
    d = np.abs(a - b)
    if mask is None:
        return float(d.mean())
    if mask.sum() == 0:
        return float("nan")
    return float(d[mask].mean())


def reconstruct_ipm_ground(
    holdout_cam: str,
    other_cams: list[str],
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_rgb: dict[str, np.ndarray],
    cam_ground_mask: dict[str, np.ndarray],
    min_distance_m: float = 1.5,
    max_distance_m: float = 40.0,
) -> tuple[np.ndarray, np.ndarray]:
    """For each held-out pixel: ray-trace to ground plane, sample from other cams.

    Returns:
        rgb (H, W, 3) float32 in [0, 255]
        mask (H, W) bool — True where we got a valid sample.
    """
    K_h = cam_K[holdout_cam]
    T_h = cam_T_ego_cam[holdout_cam]
    H, W = cam_rgb[holdout_cam].shape[:2]

    # Build held-out pixel grid -> cam-frame ray
    uu, vv = np.meshgrid(np.arange(W), np.arange(H))
    pix = np.stack([uu + 0.5, vv + 0.5, np.ones_like(uu)], axis=-1).astype(np.float64)
    K_h_inv = np.linalg.inv(K_h)
    d_cam_h = pix @ K_h_inv.T  # (H, W, 3)
    R_h = T_h[:3, :3]
    t_h = T_h[:3, 3]
    d_ego_h = d_cam_h @ R_h.T  # (H, W, 3)

    # Intersect with z=0
    dz = d_ego_h[..., 2]
    plane_ok = np.abs(dz) > 1e-6
    t_hit = np.where(plane_ok, -t_h[2] / np.where(plane_ok, dz, 1.0), -1.0)
    in_front = t_hit > 1e-3
    p_x = t_h[0] + t_hit * d_ego_h[..., 0]
    p_y = t_h[1] + t_hit * d_ego_h[..., 1]
    r = np.sqrt(p_x ** 2 + p_y ** 2)
    radius_ok = (r >= min_distance_m) & (r <= max_distance_m)
    valid_hit = plane_ok & in_front & radius_ok & np.isfinite(p_x) & np.isfinite(p_y)

    rgb_sum = np.zeros((H, W, 3), dtype=np.float32)
    w_sum = np.zeros((H, W), dtype=np.float32)

    for cam_j in other_cams:
        K_j = cam_K[cam_j]
        T_j = cam_T_ego_cam[cam_j]
        rgb_j = cam_rgb[cam_j].astype(np.float32)
        H_j, W_j = rgb_j.shape[:2]
        gm_j = cam_ground_mask.get(cam_j)

        # Bring ground point (p_x, p_y, 0) into cam_j frame
        R_cam_ego_j = T_j[:3, :3].T
        t_cam_ego_j = -R_cam_ego_j @ T_j[:3, 3]
        pt_ego = np.stack([p_x, p_y, np.zeros_like(p_x)], axis=-1)  # (H, W, 3)
        pt_j = pt_ego @ R_cam_ego_j.T + t_cam_ego_j[None, None, :]
        z_j = pt_j[..., 2]
        in_front_j = z_j > 1e-6
        z_safe = np.where(in_front_j, z_j, 1.0)
        u_j = K_j[0, 0] * pt_j[..., 0] / z_safe + K_j[0, 2]
        v_j = K_j[1, 1] * pt_j[..., 1] / z_safe + K_j[1, 2]

        margin = 0.5
        in_bounds = (u_j >= margin) & (u_j <= W_j - 1 - margin) & (v_j >= margin) & (v_j <= H_j - 1 - margin)
        valid_j = in_front_j & in_bounds & valid_hit

        # Optionally only sample from cam_j pixels where cam_j thought it was ground.
        if gm_j is not None:
            map_x = np.where(valid_j, u_j, -1.0).astype(np.float32)
            map_y = np.where(valid_j, v_j, -1.0).astype(np.float32)
            gm_sampled = cv2.remap(
                gm_j.astype(np.float32), map_x, map_y,
                interpolation=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT, borderValue=0.0,
            )
            valid_j = valid_j & (gm_sampled > 0.5)

        map_x = np.where(valid_j, u_j, -1.0).astype(np.float32)
        map_y = np.where(valid_j, v_j, -1.0).astype(np.float32)
        sampled = cv2.remap(
            rgb_j, map_x, map_y, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0.0, 0.0, 0.0),
        )

        # weight: cos^2 of angle from cam_j optical axis (z is forward),
        # times distance-decay so close samples win over far ones.
        norm_pt = np.linalg.norm(pt_j, axis=-1)
        cos_axis = np.where(in_front_j, z_j / np.maximum(norm_pt, 1e-6), 0.0)
        cos_axis = np.clip(cos_axis, 0.0, 1.0)
        dist_decay = 1.0 / (1.0 + r / 10.0)
        w_j = (cos_axis ** 2 * dist_decay).astype(np.float32) * valid_j.astype(np.float32)

        rgb_sum += sampled * w_j[..., None]
        w_sum += w_j

    w_safe = np.where(w_sum > 1e-6, w_sum, 1.0)
    rgb_out = rgb_sum / w_safe[..., None]
    rgb_out = np.where(w_sum[..., None] > 1e-6, rgb_out, 0.0)
    mask = w_sum > 1e-6
    return rgb_out.astype(np.float32), mask


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi3-dir", required=True,
                    help="Existing Pi3 anchor-output dir with image_*.png, K, T_ego_cam, "
                         "local_points_*.npy.")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--ego-z-thresh-m", type=float, default=0.3)
    ap.add_argument("--min-ground-coverage", type=float, default=0.02)
    ap.add_argument("--fallback-lower-fraction", type=float, default=0.3)
    ap.add_argument("--min-distance-m", type=float, default=1.5)
    ap.add_argument("--max-distance-m", type=float, default=40.0)
    ap.add_argument("--save-recon-pngs", action="store_true")
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--phase2-dir", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    phase2_dir = Path(args.phase2_dir) if args.phase2_dir else (here / DEFAULT_PHASE2_REL).resolve()
    _wire_imports(w2p_code, phase2_dir)

    RING_CAMS_7 = (
        "ring_front_center", "ring_front_left", "ring_side_left",
        "ring_rear_left", "ring_rear_right", "ring_side_right", "ring_front_right",
    )
    from waymo2panorama.projection.ipm_ground import detect_ground_from_pi3, detect_ground_lower_n
    from eval_cycle_consistency import reconstruct_l1

    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pi3_summary = json.loads((pi3_dir / "summary.json").read_text())
    cams = pi3_summary.get("cameras", list(RING_CAMS_7))

    cam_K, cam_T, cam_rgb, cam_lp, cam_gm = {}, {}, {}, {}, {}
    for cam in cams:
        cam_K[cam] = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
        cam_T[cam] = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        cam_rgb[cam] = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
        cam_lp[cam] = np.load(pi3_dir / f"local_points_{cam}.npy")
        gm = detect_ground_from_pi3(
            local_points_cam=cam_lp[cam], T_ego_cam=cam_T[cam],
            ego_z_thresh_m=args.ego_z_thresh_m,
            min_forward_m=1.0, max_radius_m=args.max_distance_m,
        )
        if gm.mean() < args.min_ground_coverage:
            gm = detect_ground_lower_n(cam_rgb[cam].shape[:2], args.fallback_lower_fraction)
        cam_gm[cam] = gm

    rows: list[dict] = []
    print(f"{'cam':22s}  {'cov_L1':>7s}  {'cov_Hyb':>7s}  {'cov_int':>7s}  "
          f"{'PSNR_L1':>8s}  {'PSNR_Hy':>8s}  dPSNR  {'MAE_L1':>7s}  {'MAE_Hy':>7s}")
    print("-" * 110)

    for holdout in cams:
        others = [c for c in cams if c != holdout]
        gt = cam_rgb[holdout]
        H, W = gt.shape[:2]

        l1_rgb, l1_mask = reconstruct_l1(holdout, others, cam_K, cam_T, cam_rgb)

        # IPM-ground recon (only valid for the ground part of the held-out cam's view)
        ipm_rgb, ipm_mask = reconstruct_ipm_ground(
            holdout, others, cam_K, cam_T, cam_rgb, cam_gm,
            min_distance_m=args.min_distance_m,
            max_distance_m=args.max_distance_m,
        )

        # Hybrid: IPM where it's valid AND the held-out cam's GT-ground heuristic agrees,
        # otherwise L1 sphere.
        gm_h = cam_gm[holdout]
        use_ipm = ipm_mask & gm_h
        hybrid_rgb = np.where(use_ipm[..., None], ipm_rgb, l1_rgb).astype(np.float32)
        hybrid_mask = l1_mask | use_ipm

        l1_u8 = np.clip(l1_rgb, 0, 255).astype(np.uint8)
        hy_u8 = np.clip(hybrid_rgb, 0, 255).astype(np.uint8)
        intersect = l1_mask & hybrid_mask

        cov_l1 = float(l1_mask.mean())
        cov_hy = float(hybrid_mask.mean())
        cov_int = float(intersect.mean())
        p_l1 = psnr(gt, l1_u8, mask=intersect)
        p_hy = psnr(gt, hy_u8, mask=intersect)
        m_l1 = l1_mae(gt, l1_u8, mask=intersect)
        m_hy = l1_mae(gt, hy_u8, mask=intersect)

        # Ground-region-only PSNR (this is where IPM is supposed to help)
        ground_eval_mask = intersect & gm_h
        p_l1_g = psnr(gt, l1_u8, mask=ground_eval_mask)
        p_hy_g = psnr(gt, hy_u8, mask=ground_eval_mask)

        rows.append({
            "cam": holdout,
            "coverage_L1": cov_l1, "coverage_Hyb": cov_hy, "coverage_inter": cov_int,
            "ground_eval_frac": float(ground_eval_mask.mean()),
            "PSNR_L1": p_l1, "PSNR_Hyb": p_hy, "PSNR_delta": p_hy - p_l1,
            "PSNR_L1_groundOnly": p_l1_g, "PSNR_Hyb_groundOnly": p_hy_g,
            "PSNR_delta_groundOnly": p_hy_g - p_l1_g,
            "MAE_L1": m_l1, "MAE_Hyb": m_hy,
        })
        d_p = p_hy - p_l1
        print(f"{holdout:22s}  {cov_l1:7.1%}  {cov_hy:7.1%}  {cov_int:7.1%}  "
              f"{p_l1:8.2f}  {p_hy:8.2f}  {d_p:+5.2f}  {m_l1:7.2f}  {m_hy:7.2f}  "
              f"| ground-only PSNR L1={p_l1_g:.2f} Hy={p_hy_g:.2f} d={p_hy_g - p_l1_g:+.2f}")

        if args.save_recon_pngs:
            gap = 4
            panel = np.full((H, 3 * W + 2 * gap, 3), 32, dtype=np.uint8)
            panel[:, :W] = gt
            panel[:, W + gap:2 * W + gap] = l1_u8
            panel[:, 2 * W + 2 * gap:] = hy_u8
            Image.fromarray(panel).save(out_dir / f"reconstruction_{holdout}.png")

    mean_psnr_l1 = float(np.nanmean([r["PSNR_L1"] for r in rows]))
    mean_psnr_hy = float(np.nanmean([r["PSNR_Hyb"] for r in rows]))
    mean_psnr_l1_g = float(np.nanmean([r["PSNR_L1_groundOnly"] for r in rows]))
    mean_psnr_hy_g = float(np.nanmean([r["PSNR_Hyb_groundOnly"] for r in rows]))
    mean_mae_l1 = float(np.nanmean([r["MAE_L1"] for r in rows]))
    mean_mae_hy = float(np.nanmean([r["MAE_Hyb"] for r in rows]))
    print("-" * 110)
    print(f"{'MEAN':22s}   ALL: L1={mean_psnr_l1:.2f} Hy={mean_psnr_hy:.2f} d={mean_psnr_hy - mean_psnr_l1:+.2f}  "
          f"GROUND-ONLY: L1={mean_psnr_l1_g:.2f} Hy={mean_psnr_hy_g:.2f} d={mean_psnr_hy_g - mean_psnr_l1_g:+.2f}  "
          f"MAE_L1={mean_mae_l1:.2f} MAE_Hy={mean_mae_hy:.2f}")

    summary = {
        "pi3_dir": str(pi3_dir),
        "params": vars(args),
        "per_cam": rows,
        "mean": {
            "PSNR_L1": mean_psnr_l1, "PSNR_Hyb": mean_psnr_hy,
            "PSNR_delta_full": mean_psnr_hy - mean_psnr_l1,
            "PSNR_L1_groundOnly": mean_psnr_l1_g,
            "PSNR_Hyb_groundOnly": mean_psnr_hy_g,
            "PSNR_delta_groundOnly": mean_psnr_hy_g - mean_psnr_l1_g,
            "MAE_L1": mean_mae_l1, "MAE_Hyb": mean_mae_hy,
        },
    }
    (out_dir / "cycle_ipm.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[cycle-ipm] wrote {out_dir / 'cycle_ipm.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
