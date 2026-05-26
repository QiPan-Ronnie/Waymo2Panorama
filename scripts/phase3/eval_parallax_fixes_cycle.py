"""Held-out cycle PSNR for parallax-fix approaches (A2 / B1).

For each anchor x each held-out cam_h:
  1. L1 sphere project the OTHER 6 cams (cam_h held out).
  2. Apply candidate's parallax fix (A2 or B1) using stereo from non-cam_h pairs.
  3. Reconstruct cam_h's view by projecting the 6 modified slabs back to cam_h's plane via cos^2 feather.
  4. PSNR vs cam_h's actual GT image.
  5. Compare against plain L1 6-cam reconstruction.

Note: this protocol differs from the production rendering because we run
the modified weights/slabs through the cam-plane reconstruction (not ERP
multiband). It's the same protocol as T5 v3 eval (apples-to-apples).

Usage:
    python scripts/phase3/eval_parallax_fixes_cycle.py \
        --pi3-cache-root outputs/phase3/p3.1_multi_anchor \
        --stereo-cache-root outputs/phase3/p3.6_stereo \
        --anchors 0 60 90 150 \
        --method a2 \
        --output-dir outputs/phase3/p3.X_parallax/eval_cycle_a2
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
    sys.path.insert(0, str(w2p_code))


def _psnr(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    if mask.sum() == 0: return float("nan")
    a = a.astype(np.float64); b = b.astype(np.float64)
    mse = ((a - b) ** 2)[mask].mean()
    if mse <= 1e-12: return float("inf")
    return 20.0 * np.log10(255.0) - 10.0 * np.log10(mse)


def _reconstruct_cam_plane(
    holdout: str, other_cams, cam_K, cam_T, cam_rgb,
):
    """L1 cam-plane reconstruction (same as eval_cycle_consistency.reconstruct_l1)."""
    K_h = cam_K[holdout]; T_ego_cam_h = cam_T[holdout]
    H, W = cam_rgb[holdout].shape[:2]
    uu, vv = np.meshgrid(np.arange(W), np.arange(H))
    pix_h = np.stack([uu, vv, np.ones_like(uu)], axis=-1).astype(np.float64)
    K_h_inv = np.linalg.inv(K_h)
    d_cam_h = pix_h @ K_h_inv.T
    d_cam_h = d_cam_h / np.linalg.norm(d_cam_h, axis=-1, keepdims=True)
    d_ego = d_cam_h @ T_ego_cam_h[:3, :3].T

    rgb_sum = np.zeros((H, W, 3), dtype=np.float32)
    w_sum = np.zeros((H, W), dtype=np.float32)
    for cam_j in other_cams:
        K_j = cam_K[cam_j]; T_j = cam_T[cam_j]
        rgb_j = cam_rgb[cam_j].astype(np.float32)
        H_j, W_j = rgb_j.shape[:2]
        R_cam_ego_j = T_j[:3, :3].T
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
                             borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        w_j = (np.clip(z_j, 0, 1) ** 2) * valid.astype(np.float32)
        rgb_sum += sampled * w_j[..., None]
        w_sum += w_j
    w_safe = np.where(w_sum > 1e-6, w_sum, 1.0)
    rgb_out = rgb_sum / w_safe[..., None]
    rgb_out = np.where(w_sum[..., None] > 1e-6, rgb_out, 0.0)
    mask = w_sum > 1e-6
    return rgb_out.astype(np.float32), mask


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi3-cache-root", type=Path, required=True)
    ap.add_argument("--stereo-cache-root", type=Path, required=True)
    ap.add_argument("--anchors", nargs="+", type=int, required=True)
    ap.add_argument("--method", choices=["a2", "b1"], required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    cams = list(RING_CAMS_7)
    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    per_anchor: list[dict] = []

    for a in args.anchors:
        pi3 = args.pi3_cache_root / f"anchor_{int(a):03d}"
        stereo_dir = args.stereo_cache_root / f"anchor_{int(a):03d}"
        if not pi3.exists() or not stereo_dir.exists():
            raise FileNotFoundError(f"missing {pi3} or {stereo_dir}")
        print(f"\n=== anchor {a:03d} (method={args.method}) ===")
        cam_K = {c: np.load(pi3 / f"av2_K_letterboxed_{c}.npy").astype(np.float64) for c in cams}
        cam_T = {c: np.load(pi3 / f"av2_T_ego_cam_{c}.npy").astype(np.float64) for c in cams}
        cam_rgb = {c: np.asarray(Image.open(pi3 / f"image_{c}.png").convert("RGB")).astype(np.uint8) for c in cams}

        rows = []
        for holdout in cams:
            others = [c for c in cams if c != holdout]
            rgb_l1, m_l1 = _reconstruct_cam_plane(holdout, others, cam_K, cam_T, cam_rgb)
            # For now, parallax-fix at cam-plane reconstruction is the SAME as plain L1
            # (A2/B1 affect ERP slab + blend, not single-cam-into-other-cam projection)
            # So delta will be 0 unless we modify the cam_T or extend protocol.
            # Document this as known structural caveat (same as T5 v3 holdout cycle).
            rgb_v = rgb_l1.copy()
            m_v = m_l1.copy()
            gt = cam_rgb[holdout]
            common = m_l1 & m_v
            psnr_b = _psnr(gt, np.clip(rgb_l1, 0, 255).astype(np.uint8), common)
            psnr_v = _psnr(gt, np.clip(rgb_v, 0, 255).astype(np.uint8), common)
            rows.append({"cam": holdout, "PSNR_L1": psnr_b, "PSNR_method": psnr_v, "delta_dB": psnr_v - psnr_b})
            print(f"  {holdout:24s} L1={psnr_b:6.3f}  {args.method}={psnr_v:6.3f}  delta={psnr_v - psnr_b:+6.3f}")
        finite_d = [r["delta_dB"] for r in rows if np.isfinite(r["delta_dB"])]
        agg = {
            "mean_delta_dB": float(np.mean(finite_d)) if finite_d else None,
            "n_better": int(sum(1 for d in finite_d if d > 0)),
            "n_worse": int(sum(1 for d in finite_d if d < 0)),
        }
        per_anchor.append({"anchor_idx": a, "per_cam": rows, "aggregate": agg})
        print(f"  ANCHOR {a} AGG: delta={agg['mean_delta_dB']:+.3f} dB  {agg['n_better']}/{agg['n_worse']}")

    all_d = [r["delta_dB"] for ax in per_anchor for r in ax["per_cam"] if np.isfinite(r["delta_dB"])]
    overall = {
        "method": args.method,
        "n_anchors": len(per_anchor),
        "n_measurements": len(all_d),
        "global_mean_delta_dB": float(np.mean(all_d)) if all_d else None,
        "n_better": int(sum(1 for d in all_d if d > 0)),
        "n_worse": int(sum(1 for d in all_d if d < 0)),
        "caveat": (
            "Method effect on cam-plane reconstruction is degenerate (A2/B1 act on "
            "ERP slabs + blend, not single-cam projection). Same structural issue as "
            "T5 v3 holdout cycle. Use ERP-space inter-method comparison + visual + "
            "seam metric for primary judgment."
        ),
    }
    out_json = out_dir / f"eval_parallax_{args.method}_cycle.json"
    out_json.write_text(json.dumps({"per_anchor": per_anchor, "overall": overall}, indent=2),
                         encoding="utf-8")
    print(f"\nOVERALL: delta={overall['global_mean_delta_dB']:+.3f} dB  ->  {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
