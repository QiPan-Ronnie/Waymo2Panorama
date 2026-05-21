"""
Phase 3 P3.3 — cycle-consistency eval, multi-region IPM vs L1 vs T14 ground-only.

For each held-out cam:
  GT = AV2 letterboxed image
  L1 recon  = `reconstruct_l1`  (sphere ray-trace, no depth)
  T14 recon = L1 outside ground + IPM-ground inside ground mask
  newC recon = L1 outside (ground ∪ building) + IPM-ground inside ground +
                IPM-building inside building (RANSAC per-tile vertical plane)

Metrics: PSNR / MAE over intersection masks, broken down by region.

Outputs:
  cycle_multi_region.json
  reconstruction_<cam>.png  (GT | L1 | T14 | newC)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"
DEFAULT_PHASE2_REL = "../phase2"
DEFAULT_PHASE3_REL = "../phase3"


def _wire_imports(*paths: Path) -> None:
    for p in paths:
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


def reconstruct_ipm_building(
    holdout_cam: str,
    other_cams: list[str],
    cam_K: dict[str, np.ndarray],
    cam_T: dict[str, np.ndarray],
    cam_rgb: dict[str, np.ndarray],
    cam_lp: dict[str, np.ndarray],
    cam_building_mask: dict[str, np.ndarray],
    cam_planes: dict[str, list[tuple[float, float, float]]],
    min_distance_m: float = 1.0,
    max_distance_m: float = 80.0,
) -> tuple[np.ndarray, np.ndarray]:
    """For each held-out pixel: cast cam ray, intersect with the LIST of fitted
    vertical planes (one per source cam tile). Pick the plane whose intersection
    is in front of the cam at smallest distance and within the radius window.
    Then project that 3D point back into all other cams and sample.

    This is the symmetric counterpart of `reconstruct_ipm_ground` from
    `eval_ipm_hybrid_cycle.py` but for building facades.
    """
    K_h = cam_K[holdout_cam]
    T_h = cam_T[holdout_cam]
    H, W = cam_rgb[holdout_cam].shape[:2]

    uu, vv = np.meshgrid(np.arange(W), np.arange(H))
    pix = np.stack([uu + 0.5, vv + 0.5, np.ones_like(uu)], axis=-1).astype(np.float64)
    K_h_inv = np.linalg.inv(K_h)
    d_cam_h = pix @ K_h_inv.T
    R_h = T_h[:3, :3]
    t_h = T_h[:3, 3]
    d_ego_h = d_cam_h @ R_h.T  # (H, W, 3)

    # Aggregate all planes from other cams' source-side fits.
    all_planes: list[tuple[float, float, float]] = []
    for c in other_cams:
        all_planes.extend(cam_planes.get(c, []))

    if not all_planes:
        return np.zeros((H, W, 3), dtype=np.float32), np.zeros((H, W), dtype=bool)

    # For each plane, compute t per held-out pixel. Pick smallest valid t.
    best_t = np.full((H, W), np.inf, dtype=np.float64)
    best_px = np.full((H, W), np.nan, dtype=np.float64)
    best_py = np.full((H, W), np.nan, dtype=np.float64)
    best_pz = np.full((H, W), np.nan, dtype=np.float64)
    valid_any = np.zeros((H, W), dtype=bool)

    for (n_x, n_y, d) in all_planes:
        denom = n_x * d_ego_h[..., 0] + n_y * d_ego_h[..., 1]
        denom_ok = np.abs(denom) > 1e-3
        t = (d - n_x * t_h[0] - n_y * t_h[1]) / np.where(denom_ok, denom, 1.0)
        ok = denom_ok & (t > min_distance_m) & (t < max_distance_m) & (t < best_t)
        if not ok.any():
            continue
        best_t = np.where(ok, t, best_t)
        p_x = t_h[0] + t * d_ego_h[..., 0]
        p_y = t_h[1] + t * d_ego_h[..., 1]
        p_z = t_h[2] + t * d_ego_h[..., 2]
        best_px = np.where(ok, p_x, best_px)
        best_py = np.where(ok, p_y, best_py)
        best_pz = np.where(ok, p_z, best_pz)
        valid_any |= ok

    if not valid_any.any():
        return np.zeros((H, W, 3), dtype=np.float32), np.zeros((H, W), dtype=bool)

    # Sample each other cam at the 3D intersection.
    rgb_sum = np.zeros((H, W, 3), dtype=np.float32)
    w_sum = np.zeros((H, W), dtype=np.float32)

    pt_ego = np.stack([best_px, best_py, best_pz], axis=-1)
    for cam_j in other_cams:
        K_j = cam_K[cam_j]
        T_j = cam_T[cam_j]
        rgb_j = cam_rgb[cam_j].astype(np.float32)
        H_j, W_j = rgb_j.shape[:2]
        bm_j = cam_building_mask.get(cam_j)
        R_cam_ego_j = T_j[:3, :3].T
        t_cam_ego_j = -R_cam_ego_j @ T_j[:3, 3]
        pt_j = pt_ego @ R_cam_ego_j.T + t_cam_ego_j[None, None, :]
        z_j = pt_j[..., 2]
        in_front_j = z_j > 1e-6
        z_safe = np.where(in_front_j, z_j, 1.0)
        u_j = K_j[0, 0] * pt_j[..., 0] / z_safe + K_j[0, 2]
        v_j = K_j[1, 1] * pt_j[..., 1] / z_safe + K_j[1, 2]
        margin = 0.5
        in_bounds = (u_j >= margin) & (u_j <= W_j - 1 - margin) & (v_j >= margin) & (v_j <= H_j - 1 - margin)
        valid_j = in_front_j & in_bounds & valid_any

        if bm_j is not None:
            map_x = np.where(valid_j, u_j, -1.0).astype(np.float32)
            map_y = np.where(valid_j, v_j, -1.0).astype(np.float32)
            bm_sampled = cv2.remap(
                bm_j.astype(np.float32), map_x, map_y,
                interpolation=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT, borderValue=0.0,
            )
            valid_j = valid_j & (bm_sampled > 0.5)

        map_x = np.where(valid_j, u_j, -1.0).astype(np.float32)
        map_y = np.where(valid_j, v_j, -1.0).astype(np.float32)
        sampled = cv2.remap(
            rgb_j, map_x, map_y, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0.0, 0.0, 0.0),
        )
        norm_pt = np.linalg.norm(pt_j, axis=-1)
        cos_axis = np.where(in_front_j, z_j / np.maximum(norm_pt, 1e-6), 0.0)
        cos_axis = np.clip(cos_axis, 0.0, 1.0)
        dist_decay = 1.0 / (1.0 + best_t / 20.0)
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
    ap.add_argument("--pi3-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--enable-building", type=lambda s: s.lower() in ("true","1","yes"), default=True)
    ap.add_argument("--min-distance-m", type=float, default=1.0)
    ap.add_argument("--max-distance-m", type=float, default=60.0)
    ap.add_argument("--save-recon-pngs", action="store_true")
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--phase2-dir", default=None)
    ap.add_argument("--phase3-dir", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    phase2_dir = Path(args.phase2_dir) if args.phase2_dir else (here / DEFAULT_PHASE2_REL).resolve()
    phase3_dir = Path(args.phase3_dir) if args.phase3_dir else here.resolve()
    _wire_imports(w2p_code, phase2_dir, phase3_dir)

    RING_CAMS_7 = (
        "ring_front_center", "ring_front_left", "ring_side_left",
        "ring_rear_left", "ring_rear_right", "ring_side_right", "ring_front_right",
    )
    from waymo2panorama.projection.ipm_multi_region import (
        segment_regions_from_pi3,
        _ransac_vertical_plane,
    )
    from eval_cycle_consistency import reconstruct_l1
    from eval_ipm_hybrid_cycle import reconstruct_ipm_ground

    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pi3_summary = json.loads((pi3_dir / "summary.json").read_text())
    cams = pi3_summary.get("cameras", list(RING_CAMS_7))

    cam_K, cam_T, cam_rgb, cam_lp = {}, {}, {}, {}
    cam_gm, cam_sm, cam_bm = {}, {}, {}
    cam_planes: dict[str, list[tuple[float, float, float]]] = {}

    for cam in cams:
        cam_K[cam] = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
        cam_T[cam] = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        cam_rgb[cam] = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
        cam_lp[cam] = np.load(pi3_dir / f"local_points_{cam}.npy")
        conf_path = pi3_dir / f"conf_{cam}.npy"
        conf = np.load(conf_path) if conf_path.exists() else None

        masks = segment_regions_from_pi3(cam_lp[cam], cam_T[cam], conf=conf)
        cam_gm[cam] = masks.ground
        cam_sm[cam] = masks.sky
        cam_bm[cam] = masks.building

        # Re-fit per-tile planes for cycle evaluation (we don't re-use the
        # building IPM cache because cycle eval needs planes, not splats).
        H, W, _ = cam_lp[cam].shape
        pts = cam_lp[cam].reshape(-1, 3).astype(np.float64)
        pts_h = np.concatenate([pts, np.ones((pts.shape[0], 1))], axis=1)
        ego = (cam_T[cam] @ pts_h.T).T[:, :3].reshape(H, W, 3)

        planes: list[tuple[float, float, float]] = []
        rng = np.random.default_rng(42)
        TS = 32
        for v0 in range(0, H, TS):
            for u0 in range(0, W, TS):
                v1 = min(v0 + TS, H)
                u1 = min(u0 + TS, W)
                tm = masks.building[v0:v1, u0:u1]
                if tm.sum() < 200:
                    continue
                tile_pts = ego[v0:v1, u0:u1][tm]
                model, frac = _ransac_vertical_plane(
                    tile_pts, iters=50, threshold_m=0.20, min_inlier_frac=0.40, rng=rng,
                )
                if model is not None:
                    planes.append(model)
        cam_planes[cam] = planes
        print(f"[{cam}] fitted {len(planes)} planes for cycle eval")

    rows: list[dict] = []
    header = (f"{'cam':22s}  {'cov_int':>7s}  {'PSNR_L1':>8s}  {'PSNR_T14':>8s}  "
              f"{'PSNR_newC':>9s}  {'d_T14':>6s}  {'d_newC':>7s}  "
              f"{'PSNR_L1_g':>9s} {'PSNR_T14_g':>10s} {'PSNR_newC_g':>11s}  "
              f"{'PSNR_L1_b':>9s} {'PSNR_newC_b':>11s}")
    print(header)
    print("-" * len(header))

    for holdout in cams:
        others = [c for c in cams if c != holdout]
        gt = cam_rgb[holdout]
        H, W = gt.shape[:2]

        l1_rgb, l1_mask = reconstruct_l1(holdout, others, cam_K, cam_T, cam_rgb)
        ipm_g_rgb, ipm_g_mask = reconstruct_ipm_ground(
            holdout, others, cam_K, cam_T, cam_rgb, cam_gm,
            min_distance_m=1.5,
            max_distance_m=40.0,
        )
        # T14 hybrid: ground IPM where the holdout's GT-ground mask says ground.
        use_t14 = ipm_g_mask & cam_gm[holdout]
        t14_rgb = np.where(use_t14[..., None], ipm_g_rgb, l1_rgb).astype(np.float32)

        # Building IPM
        if args.enable_building:
            ipm_b_rgb, ipm_b_mask = reconstruct_ipm_building(
                holdout, others, cam_K, cam_T, cam_rgb, cam_lp,
                cam_bm, cam_planes,
                min_distance_m=args.min_distance_m,
                max_distance_m=args.max_distance_m,
            )
            use_bld = ipm_b_mask & cam_bm[holdout] & ~use_t14
        else:
            ipm_b_rgb = np.zeros_like(l1_rgb)
            ipm_b_mask = np.zeros((H, W), dtype=bool)
            use_bld = np.zeros((H, W), dtype=bool)

        # newC: ground -> T14, building -> IPM-bld, else L1
        newc_rgb = t14_rgb.copy()
        newc_rgb[use_bld] = ipm_b_rgb[use_bld]

        l1_u8 = np.clip(l1_rgb, 0, 255).astype(np.uint8)
        t14_u8 = np.clip(t14_rgb, 0, 255).astype(np.uint8)
        nc_u8 = np.clip(newc_rgb, 0, 255).astype(np.uint8)

        intersect = l1_mask
        gm_h = cam_gm[holdout]
        bm_h = cam_bm[holdout]
        ground_eval = intersect & gm_h
        bld_eval = intersect & bm_h

        p_l1 = psnr(gt, l1_u8, mask=intersect)
        p_t14 = psnr(gt, t14_u8, mask=intersect)
        p_nc = psnr(gt, nc_u8, mask=intersect)

        p_l1_g = psnr(gt, l1_u8, mask=ground_eval)
        p_t14_g = psnr(gt, t14_u8, mask=ground_eval)
        p_nc_g = psnr(gt, nc_u8, mask=ground_eval)

        p_l1_b = psnr(gt, l1_u8, mask=bld_eval)
        p_nc_b = psnr(gt, nc_u8, mask=bld_eval)

        m_l1 = l1_mae(gt, l1_u8, mask=intersect)
        m_nc = l1_mae(gt, nc_u8, mask=intersect)

        rows.append({
            "cam": holdout,
            "coverage_inter": float(intersect.mean()),
            "ground_eval_frac": float(ground_eval.mean()),
            "building_eval_frac": float(bld_eval.mean()),
            "PSNR_L1": p_l1, "PSNR_T14": p_t14, "PSNR_newC": p_nc,
            "PSNR_L1_ground": p_l1_g, "PSNR_T14_ground": p_t14_g, "PSNR_newC_ground": p_nc_g,
            "PSNR_L1_building": p_l1_b, "PSNR_newC_building": p_nc_b,
            "MAE_L1": m_l1, "MAE_newC": m_nc,
            "PSNR_delta_T14": p_t14 - p_l1,
            "PSNR_delta_newC": p_nc - p_l1,
            "PSNR_delta_ground_newC": p_nc_g - p_l1_g,
            "PSNR_delta_building_newC": p_nc_b - p_l1_b,
        })
        print(f"{holdout:22s}  {intersect.mean():7.1%}  "
              f"{p_l1:8.2f}  {p_t14:8.2f}  {p_nc:9.2f}  "
              f"{p_t14 - p_l1:+6.2f}  {p_nc - p_l1:+7.2f}  "
              f"{p_l1_g:9.2f} {p_t14_g:10.2f} {p_nc_g:11.2f}  "
              f"{p_l1_b:9.2f} {p_nc_b:11.2f}")

        if args.save_recon_pngs:
            gap = 4
            panel = np.full((H, 4 * W + 3 * gap, 3), 32, dtype=np.uint8)
            panel[:, :W] = gt
            panel[:, W + gap:2 * W + gap] = l1_u8
            panel[:, 2 * W + 2 * gap:3 * W + 2 * gap] = t14_u8
            panel[:, 3 * W + 3 * gap:] = nc_u8
            Image.fromarray(panel).save(out_dir / f"reconstruction_{holdout}.png")

    mean = {
        "PSNR_L1": float(np.nanmean([r["PSNR_L1"] for r in rows])),
        "PSNR_T14": float(np.nanmean([r["PSNR_T14"] for r in rows])),
        "PSNR_newC": float(np.nanmean([r["PSNR_newC"] for r in rows])),
        "PSNR_delta_T14": float(np.nanmean([r["PSNR_delta_T14"] for r in rows])),
        "PSNR_delta_newC": float(np.nanmean([r["PSNR_delta_newC"] for r in rows])),
        "PSNR_L1_ground": float(np.nanmean([r["PSNR_L1_ground"] for r in rows])),
        "PSNR_newC_ground": float(np.nanmean([r["PSNR_newC_ground"] for r in rows])),
        "PSNR_delta_ground_newC": float(np.nanmean([r["PSNR_delta_ground_newC"] for r in rows])),
        "PSNR_L1_building": float(np.nanmean([r["PSNR_L1_building"] for r in rows])),
        "PSNR_newC_building": float(np.nanmean([r["PSNR_newC_building"] for r in rows])),
        "PSNR_delta_building_newC": float(np.nanmean([r["PSNR_delta_building_newC"] for r in rows])),
        "MAE_L1": float(np.nanmean([r["MAE_L1"] for r in rows])),
        "MAE_newC": float(np.nanmean([r["MAE_newC"] for r in rows])),
    }
    print("-" * len(header))
    print(f"MEAN  L1={mean['PSNR_L1']:.2f}  T14={mean['PSNR_T14']:.2f}  newC={mean['PSNR_newC']:.2f}  "
          f"d_T14={mean['PSNR_delta_T14']:+.2f}  d_newC={mean['PSNR_delta_newC']:+.2f}  "
          f"GROUND: d_newC={mean['PSNR_delta_ground_newC']:+.2f}  "
          f"BUILDING: d_newC={mean['PSNR_delta_building_newC']:+.2f}")

    summary = {
        "pi3_dir": str(pi3_dir),
        "params": vars(args),
        "per_cam": rows,
        "mean": mean,
    }
    (out_dir / "cycle_multi_region.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(f"[cycle-multi-region] wrote {out_dir / 'cycle_multi_region.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
