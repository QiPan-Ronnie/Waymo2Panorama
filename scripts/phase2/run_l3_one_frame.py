"""
Phase 2 — Lift Pi3 per-cam outputs to ego frame, splat to ERP, compare vs L1.

Reads from a previous Pi3-on-one-frame run (output of run_pi3_one_frame.py) on Drive,
plus the same AV2 sensor log, and produces a side-by-side L1 vs L3 ERP image.

Inputs:
    --pi3-dir       : path to the per-cam Pi3 output dir (has summary.json + per-cam .npy + image_*.png)
    --log-dir       : AV2 sensor log dir (for L1 baseline re-render on the same anchor)
    --anchor-idx    : which AV2 anchor frame to render (must match what Pi3 used)
    --output-dir    : where to write L3 ERP + L1 ERP + comparison PNG

Outputs:
    <output-dir>/l3_erp.png            : L3 (Pi3 + Sim3 + lift_and_project) ERP
    <output-dir>/l1_erp.png            : L1 (sphere projection, parallax-naive) ERP
    <output-dir>/l1_vs_l3.png          : side-by-side comparison (L1 top, L3 bottom)
    <output-dir>/l3_summary.json       : Sim(3) diagnostics + per-cam splat stats
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", required=True,
                    help="Directory containing Pi3 outputs (summary.json + per-cam .npy + image_*.png)")
    ap.add_argument("--log-dir", required=True,
                    help="AV2 log dir for L1 baseline re-render")
    ap.add_argument("--anchor-idx", type=int, default=0)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--conf-threshold", type=float, default=0.1)
    ap.add_argument("--min-distance-m", type=float, default=0.5)
    ap.add_argument("--max-distance-m", type=float, default=200.0)
    ap.add_argument("--no-bilinear", action="store_true", help="use nearest splat instead of bilinear")
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--skip-l1", action="store_true",
                    help="skip L1 baseline re-render (for faster L3-only iteration)")
    ap.add_argument("--hybrid-weight-saturate", type=float, default=0.5,
                    help="L3 weight value at which hybrid uses 100%% L3; below this, blends with L1")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.alignment.sim3_align import fit_sim3_from_camera_translations
    from waymo2panorama.pipeline.lift_and_project import (
        apply_sim3_to_points, lift_and_project_multiview,
    )
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    # ---- 1. Load Pi3 outputs ----
    pi3_summary_path = pi3_dir / "summary.json"
    if not pi3_summary_path.exists():
        raise FileNotFoundError(f"Pi3 summary missing: {pi3_summary_path}")
    pi3_summary = json.loads(pi3_summary_path.read_text())
    pi3_cams = pi3_summary["cameras"]
    print(f"[l3] Pi3 cams: {pi3_cams}")

    pi3_world_points: dict[str, np.ndarray] = {}
    pi3_local_points: dict[str, np.ndarray] = {}
    pi3_confs: dict[str, np.ndarray] = {}
    pi3_cam_positions: dict[str, np.ndarray] = {}
    av2_cam_positions: dict[str, np.ndarray] = {}
    cam_colors: dict[str, np.ndarray] = {}

    for cam in pi3_cams:
        pi3_world_points[cam] = np.load(pi3_dir / f"points_{cam}.npy")            # (H, W, 3)
        pi3_local_points[cam] = np.load(pi3_dir / f"local_points_{cam}.npy")
        conf_raw = np.load(pi3_dir / f"conf_{cam}.npy")                            # logit
        pi3_confs[cam] = _sigmoid(conf_raw).astype(np.float32)
        pose_pi3 = np.load(pi3_dir / f"pose_{cam}.npy")                            # (4, 4)
        T_ego_cam = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")                  # (4, 4)
        pi3_cam_positions[cam] = pose_pi3[:3, 3].astype(np.float64)
        av2_cam_positions[cam] = T_ego_cam[:3, 3].astype(np.float64)

        # Load the letterboxed input image (same that Pi3 saw)
        img = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
        cam_colors[cam] = (img.astype(np.float32) / 255.0)                          # (H, W, 3) in [0, 1]

    # ---- 2. Fit Sim(3) ----
    sim3, sim3_diag = fit_sim3_from_camera_translations(
        pi3_cam_positions, av2_cam_positions,
    )
    print(f"[l3] Sim(3) fitted: scale={sim3.scale:.4f}, "
          f"mean_residual={sim3_diag['mean_residual_m']:.3f} m, "
          f"max_residual={sim3_diag['max_residual_m']:.3f} m")
    for cam in pi3_cams:
        r = sim3_diag["per_cam_residual_m"][cam]
        print(f"[l3]   {cam:22s} residual = {r:.3f} m")

    # ---- 3. Apply Sim(3) to Pi3 world points -> ego frame ----
    cam_points_ego: dict[str, np.ndarray] = {}
    for cam in pi3_cams:
        cam_points_ego[cam] = apply_sim3_to_points(pi3_world_points[cam], sim3)

    # ---- 4. Multi-view lift+project to ERP ----
    print(f"[l3] splatting to ERP {erp_hw} ...")
    erp_rgb, erp_w, l3_diag = lift_and_project_multiview(
        cam_points_ego, cam_colors, pi3_confs,
        erp_hw=erp_hw,
        conf_threshold=args.conf_threshold,
        min_distance_m=args.min_distance_m,
        max_distance_m=args.max_distance_m,
        bilinear=not args.no_bilinear,
    )
    print(f"[l3] ERP coverage: {l3_diag['erp_coverage_ratio']:.3%}, "
          f"total weight: {l3_diag['erp_total_weight']:.1f}")

    erp_rgb_u8 = (np.clip(erp_rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    Image.fromarray(erp_rgb_u8).save(out_dir / "l3_erp.png")

    # ---- 5. L1 baseline re-render on the same anchor ----
    l1_erp_u8 = None
    if not args.skip_l1:
        print(f"[l3] running L1 baseline on same anchor for side-by-side ...")
        loader = AV2RingLoader(Path(args.log_dir))
        anchor_ts = loader.anchor_timestamps_ns()[args.anchor_idx]
        frame = loader.load_synced_frame(anchor_ts)

        rgb_sum = np.zeros((erp_hw[0], erp_hw[1], 3), dtype=np.float32)
        w_sum = np.zeros((erp_hw[0], erp_hw[1]), dtype=np.float32)
        for cam in RING_CAMS_7:
            calib = frame.calibrations[cam]
            rgb_i, alpha_i, w_i = render_camera_to_erp(
                frame.images[cam], calib.K, calib.T_ego_cam, erp_hw=erp_hw,
            )
            rgb_sum += rgb_i * w_i[..., None]
            w_sum += w_i
        w_safe = np.where(w_sum > 1e-6, w_sum, 1.0)
        l1_rgb = rgb_sum / w_safe[..., None]
        l1_rgb = np.where(w_sum[..., None] > 1e-6, l1_rgb, 0.0)
        l1_erp_u8 = np.clip(l1_rgb, 0, 255).astype(np.uint8)
        Image.fromarray(l1_erp_u8).save(out_dir / "l1_erp.png")

    # ---- 6. Hybrid: L1 as base + L3 override where confident ----
    hybrid_erp_u8 = None
    if l1_erp_u8 is not None:
        # alpha = soft-blend weight in [0, 1]. Use L3 weight saturated at
        # `hybrid_weight_saturate` so even modest L3 coverage shows through.
        sat = max(1e-6, args.hybrid_weight_saturate)
        alpha = np.clip(erp_w / sat, 0.0, 1.0).astype(np.float32)
        # L1 is in 0..255 float scale (came from L1's pipeline); L3 was 0..1 then *255
        l1_f = l1_erp_u8.astype(np.float32)
        l3_f = erp_rgb_u8.astype(np.float32)
        hybrid = alpha[..., None] * l3_f + (1.0 - alpha[..., None]) * l1_f
        hybrid_erp_u8 = np.clip(hybrid, 0, 255).astype(np.uint8)
        Image.fromarray(hybrid_erp_u8).save(out_dir / "hybrid_erp.png")
        print(f"[l3] wrote hybrid -> {out_dir / 'hybrid_erp.png'}")

    # ---- 7. Three-panel comparison (L1 / L3 / Hybrid) ----
    if l1_erp_u8 is not None:
        gap = 8
        label_h = 40
        n_panels = 3 if hybrid_erp_u8 is not None else 2
        combined_h = n_panels * (label_h + erp_hw[0]) + (n_panels - 1) * gap
        combined = np.full((combined_h, erp_hw[1], 3), 32, dtype=np.uint8)

        labels = [
            "L1 (sphere projection, parallax-naive)",
            "L3 (Pi3 + Sim3 + lift-and-project, 3D-aware)",
        ]
        panels = [l1_erp_u8, erp_rgb_u8]
        if hybrid_erp_u8 is not None:
            labels.append(f"Hybrid (L1 base + L3 override, alpha=clip(L3w/{args.hybrid_weight_saturate}))")
            panels.append(hybrid_erp_u8)

        try:
            from PIL import ImageDraw, ImageFont
            img = Image.fromarray(combined)
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 28)
            except (OSError, IOError):
                font = ImageFont.load_default()
            colors = [(255, 255, 255), (255, 255, 0), (0, 255, 200)]
            for i, label in enumerate(labels):
                y = i * (label_h + erp_hw[0] + gap) + 5
                draw.text((10, y), label, fill=colors[i % len(colors)], font=font)
            combined = np.array(img)
        except Exception:
            pass

        for i, panel in enumerate(panels):
            y0 = i * (label_h + erp_hw[0] + gap) + label_h
            combined[y0:y0 + erp_hw[0]] = panel
        Image.fromarray(combined).save(out_dir / "l1_vs_l3.png")
        print(f"[l3] wrote 3-panel comparison -> {out_dir / 'l1_vs_l3.png'}")

    # ---- 7. Summary JSON ----
    summary = {
        "pi3_dir": str(pi3_dir),
        "log_dir": str(args.log_dir),
        "anchor_idx": args.anchor_idx,
        "erp_hw": list(erp_hw),
        "conf_threshold": args.conf_threshold,
        "min_distance_m": args.min_distance_m,
        "max_distance_m": args.max_distance_m,
        "splat_mode": "nearest" if args.no_bilinear else "bilinear",
        "sim3": {
            "scale": sim3.scale,
            "translation_m": sim3.t.tolist(),
            "rotation_matrix": sim3.R.tolist(),
        },
        "sim3_diagnostics": sim3_diag,
        "l3_diagnostics": l3_diag,
    }
    (out_dir / "l3_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[l3] done -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
