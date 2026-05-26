"""
Phase 3 route 13 / 新-D — Option B held-out cycle eval (cam-plane GT-anchored).

The companion `eval_option_b_cycle.py` only gives an INTER-METHOD PSNR (plain L1
ERP vs L1+v3 ERP) — it tells you v3 moved the pixels, not whether it moved them
in the RIGHT direction. This script gives the GT-anchored answer:

For each anchor x each held-out cam_h:
  1. Reconstruct cam_h's view from the OTHER 6 cams via L1 cam-plane feather
     (exactly `reconstruct_l1` from phase2/eval_cycle_consistency.py).
  2. Same, but apply Option B v3 reweight to each contributing cam's feather
     weight, using v3 masks built from stereo pairs that DON'T involve cam_h
     (so the held-out cam never leaks back into the boost signal).
  3. Score both against the actual cam_h GT image.
  4. delta = PSNR_v3 - PSNR_L1. If positive, v3 helps.

How v3 reweight transfers from ERP-space to cam-plane:
  - For each cam_h pixel (u, v), compute its ego direction d_ego.
  - Project d_ego onto the ERP grid (same formula as `ego_points_to_erp_uv` but
    with a unit-length direction instead of a finite 3D point) -> (u_erp, v_erp).
  - For each contributing cam_j, sample v3_mask[cam_j] at (u_erp, v_erp); the
    sampled value is cam_j's confidence boost at that ego direction.
  - Apply the same `w_j *= (1 + alpha * mask_sample)` formula used in
    `apply_option_b_reweight`.

Usage:
    python scripts/phase3/eval_option_b_holdout_cycle.py \\
        --pi3-cache-root  outputs/phase3/p3.1_multi_anchor \\
        --stereo-cache-root outputs/phase3/p3.6_stereo \\
        --anchors 0 60 90 150 \\
        --output-dir outputs/phase3/p3.7_option_b/holdout_cycle_v3 \\
        --alpha 5.0 --sigma-px 24.0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

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
    """PSNR on uint8 RGB, scored only where mask is True. NaN if no pixels."""
    if mask.sum() == 0:
        return float("nan")
    a = a.astype(np.float64); b = b.astype(np.float64)
    mse = ((a - b) ** 2)[mask].mean()
    if mse <= 1e-12:
        return float("inf")
    return 20.0 * np.log10(255.0) - 10.0 * np.log10(mse)


def _ego_dir_to_erp_uv(
    d_ego: np.ndarray, erp_hw: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Convert (H, W, 3) ego unit directions to ERP pixel coords (u_f, v_f).

    Uses the same convention as `ego_points_to_erp_uv` — see
    code/waymo2panorama/pipeline/lift_and_project.py.
    """
    h_erp, w_erp = erp_hw
    x = d_ego[..., 0]; y = d_ego[..., 1]; z = d_ego[..., 2]
    r = np.sqrt(x * x + y * y + z * z).clip(min=1e-9)
    sin_phi = np.clip(z / r, -1.0, 1.0)
    phi = np.arcsin(sin_phi)
    theta = np.arctan2(y, x)
    u_f = (np.pi - theta) * w_erp / (2.0 * np.pi) - 0.5
    v_f = (np.pi / 2.0 - phi) * h_erp / np.pi - 0.5
    u_f = np.mod(u_f, w_erp)
    return u_f.astype(np.float32), v_f.astype(np.float32)


def _sample_erp_at_uv(
    mask_erp: np.ndarray, u_f: np.ndarray, v_f: np.ndarray,
) -> np.ndarray:
    """Bilinear-sample (H, W) mask at (u_f, v_f) coords. Out-of-range v_f -> 0."""
    h_erp, w_erp = mask_erp.shape
    # Use cv2.remap with horizontal wrap manually (no native wrap mode for remap)
    # Simple bilinear with manual wrap on x:
    u0 = np.floor(u_f).astype(np.int64)
    u1 = (u0 + 1) % w_erp
    v0 = np.floor(v_f).astype(np.int64).clip(0, h_erp - 1)
    v1 = (v0 + 1).clip(0, h_erp - 1)
    u0_m = np.mod(u0, w_erp); u1_m = np.mod(u1, w_erp)
    fu = (u_f - np.floor(u_f)).astype(np.float32)
    fv = (v_f - np.floor(v_f)).astype(np.float32)
    a = mask_erp[v0, u0_m] * (1 - fu) + mask_erp[v0, u1_m] * fu
    b = mask_erp[v1, u0_m] * (1 - fu) + mask_erp[v1, u1_m] * fu
    out = a * (1 - fv) + b * fv
    # zero out where v was outside valid range
    out = np.where((v_f >= 0) & (v_f < h_erp), out, 0.0)
    return out.astype(np.float32)


def reconstruct_l1_with_v3(
    holdout_cam: str,
    other_cams: list[str],
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_rgb: dict[str, np.ndarray],
    v3_masks: Optional[dict[str, np.ndarray]] = None,
    alpha: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Same as reconstruct_l1 but with optional v3 per-cam reweight.

    v3_masks: {cam_name: (H_erp, W_erp) float32} confidence mask per cam (ERP-space).
              When provided, multiplies w_j by (1 + alpha * v3_mask[cam_j] sampled
              at the ego direction corresponding to each held-out pixel).
              When None, behaves exactly like the original reconstruct_l1.
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
    d_ego = (d_cam_h @ R_ego_cam_h.T).astype(np.float32)

    erp_hw: Optional[tuple[int, int]] = None
    u_erp_f: Optional[np.ndarray] = None
    v_erp_f: Optional[np.ndarray] = None
    if v3_masks is not None:
        any_mask = next(iter(v3_masks.values()))
        erp_hw = any_mask.shape
        u_erp_f, v_erp_f = _ego_dir_to_erp_uv(d_ego, erp_hw)

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
        sampled = cv2.remap(
            rgb_j, map_x, map_y, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0.0, 0.0, 0.0),
        )
        cos_axis = np.clip(z_j, 0.0, 1.0)
        w_j = (cos_axis ** 2) * valid.astype(np.float32)

        if v3_masks is not None and cam_j in v3_masks:
            mask_sample = _sample_erp_at_uv(v3_masks[cam_j], u_erp_f, v_erp_f)
            w_j = w_j * (1.0 + alpha * mask_sample)

        rgb_sum += sampled * w_j[..., None]
        w_sum += w_j

    w_safe = np.where(w_sum > 1e-6, w_sum, 1.0)
    rgb_out = rgb_sum / w_safe[..., None]
    rgb_out = np.where(w_sum[..., None] > 1e-6, rgb_out, 0.0)
    mask = w_sum > 1e-6
    return rgb_out.astype(np.float32), mask


def _eval_one_anchor(
    pi3_dir: Path,
    stereo_dir: Path,
    erp_hw: tuple[int, int],
    alpha: float,
    sigma_px: float,
    v3_selection: str,
) -> dict:
    from waymo2panorama.pipeline.option_b_reweight import (
        build_stereo_confidence_masks_per_cam_v3,
    )

    cams = list(RING_CAMS_7)

    cam_K: dict[str, np.ndarray] = {}
    cam_T: dict[str, np.ndarray] = {}
    cam_rgb: dict[str, np.ndarray] = {}
    for cam in cams:
        cam_K[cam] = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
        cam_T[cam] = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        cam_rgb[cam] = np.asarray(
            Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")
        ).astype(np.uint8)

    all_stereo_paths = sorted(stereo_dir.glob("stereo_*.npz"))

    rows: list[dict] = []
    for holdout in cams:
        other_cams = [c for c in cams if c != holdout]
        gt = cam_rgb[holdout]

        # Build v3 masks from stereo pairs that DON'T involve holdout
        stereo_paths_filtered = []
        for p in all_stereo_paths:
            with np.load(p) as npz:
                if "cam_a" not in npz.files or "cam_b" not in npz.files:
                    continue
                cam_a = str(npz["cam_a"]); cam_b = str(npz["cam_b"])
            if cam_a == holdout or cam_b == holdout:
                continue
            stereo_paths_filtered.append(p)

        v3_masks: Optional[dict[str, np.ndarray]] = None
        if stereo_paths_filtered:
            v3_masks = build_stereo_confidence_masks_per_cam_v3(
                stereo_paths_filtered, erp_hw=erp_hw, cam_names=cams,
                cam_T_ego_cam=cam_T, sigma_px=sigma_px,
                selection_mode=v3_selection,
            )

        # Reconstruct: plain L1 and L1+v3
        l1_rgb, l1_mask = reconstruct_l1_with_v3(
            holdout, other_cams, cam_K, cam_T, cam_rgb,
            v3_masks=None, alpha=0.0,
        )
        v3_rgb, v3_mask = reconstruct_l1_with_v3(
            holdout, other_cams, cam_K, cam_T, cam_rgb,
            v3_masks=v3_masks, alpha=alpha,
        )

        l1_u8 = np.clip(l1_rgb, 0, 255).astype(np.uint8)
        v3_u8 = np.clip(v3_rgb, 0, 255).astype(np.uint8)
        common = l1_mask & v3_mask

        psnr_l1 = _psnr(gt, l1_u8, common)
        psnr_v3 = _psnr(gt, v3_u8, common)
        delta = psnr_v3 - psnr_l1

        rows.append({
            "cam": holdout,
            "n_stereo_files_used": len(stereo_paths_filtered),
            "coverage_common_frac": float(common.mean()),
            "PSNR_L1": psnr_l1,
            "PSNR_L1_v3": psnr_v3,
            "delta_dB": delta,
        })
        print(
            f"  {holdout:22s}  files={len(stereo_paths_filtered):d}  "
            f"PSNR_L1={psnr_l1:6.3f}  PSNR_L1+v3={psnr_v3:6.3f}  delta={delta:+6.3f} dB",
            flush=True,
        )

    finite_l1 = [r["PSNR_L1"] for r in rows if np.isfinite(r["PSNR_L1"])]
    finite_v3 = [r["PSNR_L1_v3"] for r in rows if np.isfinite(r["PSNR_L1_v3"])]
    finite_d = [
        r["delta_dB"] for r in rows
        if np.isfinite(r["delta_dB"])
    ]
    agg = {
        "mean_PSNR_L1": float(np.mean(finite_l1)) if finite_l1 else None,
        "mean_PSNR_L1_v3": float(np.mean(finite_v3)) if finite_v3 else None,
        "mean_delta_dB": float(np.mean(finite_d)) if finite_d else None,
        "n_cams_v3_better": int(sum(1 for d in finite_d if d > 0)),
        "n_cams_v3_worse": int(sum(1 for d in finite_d if d < 0)),
        "n_cams_tied": int(sum(1 for d in finite_d if d == 0)),
    }

    return {
        "pi3_dir": str(pi3_dir),
        "stereo_dir": str(stereo_dir),
        "params": {
            "alpha": alpha, "sigma_px": sigma_px,
            "v3_selection": v3_selection,
        },
        "per_cam": rows,
        "aggregate": agg,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-cache-root", type=Path, required=True)
    ap.add_argument("--stereo-cache-root", type=Path, required=True)
    ap.add_argument("--anchors", nargs="+", type=int, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--alpha", type=float, default=5.0)
    ap.add_argument("--sigma-px", type=float, default=24.0)
    ap.add_argument("--v3-selection", choices=["winner_take_all", "soft_cos_angle"],
                    default="winner_take_all")
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
        ster = args.stereo_cache_root / f"anchor_{int(a):03d}"
        if not pi3.exists():
            raise FileNotFoundError(f"missing pi3 cache: {pi3}")
        if not ster.exists():
            raise FileNotFoundError(f"missing stereo cache: {ster}")
        print(f"\n=== anchor {a:03d}: {pi3.name} | {ster.name} ===", flush=True)
        t0 = time.time()
        result = _eval_one_anchor(
            pi3_dir=pi3, stereo_dir=ster, erp_hw=(args.erp_h, args.erp_w),
            alpha=args.alpha, sigma_px=args.sigma_px, v3_selection=args.v3_selection,
        )
        result["anchor_idx"] = a
        result["wall_s"] = round(time.time() - t0, 2)
        per_anchor.append(result)
        m = result["aggregate"]
        print(
            f"  ANCHOR {a:03d} AGG: L1={m['mean_PSNR_L1']:.3f} dB, "
            f"L1+v3={m['mean_PSNR_L1_v3']:.3f} dB, "
            f"delta={m['mean_delta_dB']:+.3f} dB, "
            f"v3_better/worse/tied={m['n_cams_v3_better']}/{m['n_cams_v3_worse']}/{m['n_cams_tied']}",
            flush=True,
        )

    # Global aggregate over all anchors x cams
    all_l1 = []
    all_v3 = []
    all_d = []
    for r in per_anchor:
        for row in r["per_cam"]:
            if np.isfinite(row["PSNR_L1"]):
                all_l1.append(row["PSNR_L1"])
            if np.isfinite(row["PSNR_L1_v3"]):
                all_v3.append(row["PSNR_L1_v3"])
            if np.isfinite(row["delta_dB"]):
                all_d.append(row["delta_dB"])

    overall = {
        "n_anchors": len(per_anchor),
        "n_measurements": len(all_d),
        "alpha": args.alpha,
        "sigma_px": args.sigma_px,
        "v3_selection": args.v3_selection,
        "global_mean_PSNR_L1": float(np.mean(all_l1)) if all_l1 else None,
        "global_mean_PSNR_L1_v3": float(np.mean(all_v3)) if all_v3 else None,
        "global_mean_delta_dB": float(np.mean(all_d)) if all_d else None,
        "n_v3_better": int(sum(1 for d in all_d if d > 0)),
        "n_v3_worse": int(sum(1 for d in all_d if d < 0)),
    }
    summary = {
        "route": "13 / 新-D Option B held-out cycle eval (cam plane, GT-anchored)",
        "params": {
            "alpha": args.alpha, "sigma_px": args.sigma_px,
            "v3_selection": args.v3_selection,
        },
        "per_anchor": per_anchor,
        "overall": overall,
    }
    out_path = out_dir / "eval_option_b_holdout_cycle.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n=== OVERALL (n={overall['n_measurements']} measurements) ===")
    print(f"  PSNR_L1       = {overall['global_mean_PSNR_L1']:.3f} dB")
    print(f"  PSNR_L1+v3    = {overall['global_mean_PSNR_L1_v3']:.3f} dB")
    print(f"  delta         = {overall['global_mean_delta_dB']:+.3f} dB")
    print(f"  v3 better/worse = {overall['n_v3_better']}/{overall['n_v3_worse']}")
    print(f"  json -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
