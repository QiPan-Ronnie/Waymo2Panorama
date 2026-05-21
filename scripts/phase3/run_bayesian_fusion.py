"""
Phase 3 T16 — run Bayesian depth-fusion at ERP overlap regions on cached
Pi3 multi-anchor outputs.

This script consumes the Phase 3 W1 (`p3.1_multi_anchor`) anchor folders
already present on disk (downloaded from Drive). For a given anchor it:

  1) loads per-cam Pi3 local points + conf + image + AV2 intrinsics/extrinsics
  2) fits Sim(3) (Pi3 world → AV2 ego) using the 7 cam translations
  3) lifts each cam's local points to ego frame
  4) runs `splat_with_bayesian_fusion`, which emits BOTH the fused ERP
     and a naive global-z-buffer ERP for direct comparison
  5) saves:
       erp_naive.png, erp_bayesian.png, erp_diff.png   (3-panel viz)
       erp_naive.npy, erp_bayesian.npy                  (depth maps)
       depth_diff_overlap.png                           (heat-map of fusion delta)
       coverage.png                                     (cam-count visualisation)
       summary.json                                     (metrics)

Usage:
    python scripts/phase3/run_bayesian_fusion.py \\
        --anchor-dir outputs/phase3/pi3_cache/anchor_060 \\
        --output-dir outputs/phase3/bayesian_fusion/anchor_060 \\
        --erp-height 1024
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"missing path: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _depth_to_uint8(d: np.ndarray, max_m: float = 60.0) -> np.ndarray:
    """Linear viridis-like mapping (PIL doesn't include colormaps; we hand-roll)."""
    d = np.clip(d / max_m, 0.0, 1.0)
    # Approx viridis: 3-stop interpolation purple -> teal -> yellow
    stops = np.array([
        [68, 1, 84],     # 0.0  purple
        [33, 145, 140],  # 0.5  teal
        [253, 231, 37],  # 1.0  yellow
    ], dtype=np.float32)
    idx = d * 2.0
    lo = np.floor(idx).clip(0, 1).astype(np.int32)
    hi = (lo + 1).clip(0, 2)
    f = (idx - lo).clip(0.0, 1.0)[..., None]
    rgb = stops[lo] * (1 - f) + stops[hi] * f
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _coverage_to_uint8(cov: np.ndarray) -> np.ndarray:
    """0 = black, 1 = red, 2 = orange, 3 = yellow, 4 = green, 5+ = white."""
    lut = np.array([
        [0, 0, 0],
        [200, 60, 60],
        [240, 150, 50],
        [240, 220, 70],
        [100, 220, 100],
        [255, 255, 255],
        [255, 255, 255],
        [255, 255, 255],
    ], dtype=np.uint8)
    cov_clip = np.clip(cov, 0, len(lut) - 1)
    return lut[cov_clip]


def _save_3panel(out_path: Path, panels: list[np.ndarray], labels: list[str]) -> None:
    """Stack panels horizontally with a small gap; render labels at top."""
    assert len(panels) == len(labels) and len(panels) > 0
    h, w = panels[0].shape[:2]
    gap = 6
    pad_top = 24
    canvas = np.full(
        (h + pad_top, len(panels) * w + (len(panels) - 1) * gap, 3),
        30, dtype=np.uint8,
    )
    for i, p in enumerate(panels):
        p_u8 = np.clip(p, 0, 255).astype(np.uint8)
        if p_u8.ndim == 2:
            p_u8 = np.repeat(p_u8[..., None], 3, axis=-1)
        x0 = i * (w + gap)
        canvas[pad_top:pad_top + h, x0:x0 + w] = p_u8
    img = Image.fromarray(canvas)
    try:
        from PIL import ImageDraw, ImageFont  # noqa: PLC0415
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 16)
        except Exception:
            font = ImageFont.load_default()
        for i, lbl in enumerate(labels):
            x0 = i * (w + gap)
            draw.text((x0 + 8, 4), lbl, fill=(255, 255, 255), font=font)
    except Exception:
        pass
    img.save(out_path)


def load_anchor(anchor_dir: Path) -> tuple[dict, dict]:
    """Load per-cam Pi3 outputs + AV2 metadata. Returns (per_cam, summary)."""
    summary = json.loads((anchor_dir / "summary.json").read_text())
    cams = summary["cameras"]

    per_cam: dict[str, dict] = {}
    for cam in cams:
        d = {
            "local_points": np.load(anchor_dir / f"local_points_{cam}.npy"),
            "points_world": np.load(anchor_dir / f"points_{cam}.npy"),
            "conf_logit":   np.load(anchor_dir / f"conf_{cam}.npy"),
            "pose_pi3":     np.load(anchor_dir / f"pose_{cam}.npy"),
            "K_av2":        np.load(anchor_dir / f"av2_K_letterboxed_{cam}.npy"),
            "T_ego_cam":    np.load(anchor_dir / f"av2_T_ego_cam_{cam}.npy"),
            "rgb":          np.asarray(
                Image.open(anchor_dir / f"image_{cam}.png").convert("RGB")
            ),
        }
        per_cam[cam] = d
    return per_cam, summary


def build_ego_inputs(
    per_cam: dict,
    cams: list[str],
) -> tuple[dict, dict]:
    """Convert per-cam dict into the input format expected by the fusion
    module. Returns (fusion_inputs, sim3_diag).
    """
    from waymo2panorama.alignment.sim3_align import fit_sim3_from_camera_translations
    from waymo2panorama.pipeline.lift_and_project import apply_sim3_to_points

    pi3_pos = {cam: per_cam[cam]["pose_pi3"][:3, 3].astype(np.float64) for cam in cams}
    av2_pos = {cam: per_cam[cam]["T_ego_cam"][:3, 3].astype(np.float64) for cam in cams}

    sim3, sim3_diag = fit_sim3_from_camera_translations(pi3_pos, av2_pos)

    fusion_inputs: dict[str, dict] = {}
    for cam in cams:
        pts_world = per_cam[cam]["points_world"]  # (H, W, 3) Pi3 world frame
        pts_ego = apply_sim3_to_points(pts_world, sim3)
        conf = _sigmoid(per_cam[cam]["conf_logit"]).astype(np.float32)
        fusion_inputs[cam] = {
            "points_ego": pts_ego.astype(np.float64),
            "conf": conf,
            "rgb": per_cam[cam]["rgb"],
        }
    sim3_diag["scale"] = float(sim3.scale)
    return fusion_inputs, sim3_diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--erp-height", type=int, default=1024)
    ap.add_argument("--conf-threshold", type=float, default=0.1)
    ap.add_argument("--min-distance-m", type=float, default=0.5)
    ap.add_argument("--max-distance-m", type=float, default=200.0)
    ap.add_argument("--depth-vis-max-m", type=float, default=60.0)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire(w2p_code)

    anchor_dir = Path(args.anchor_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    from waymo2panorama.pipeline.depth_bayesian_fusion import splat_with_bayesian_fusion

    per_cam, summary = load_anchor(anchor_dir)
    cams = summary["cameras"]
    print(f"[T16] anchor={summary.get('anchor_idx', '?')} cams={len(cams)}")
    fusion_inputs, sim3_diag = build_ego_inputs(per_cam, cams)
    print(f"[T16] Sim3 scale={sim3_diag['fitted_scale']:.4f} "
          f"mean_residual={sim3_diag['mean_residual_m']:.3f} m")

    erp_h = args.erp_height
    erp_w = 2 * erp_h

    out = splat_with_bayesian_fusion(
        fusion_inputs,
        erp_hw=(erp_h, erp_w),
        conf_threshold=args.conf_threshold,
        min_distance_m=args.min_distance_m,
        max_distance_m=args.max_distance_m,
        cams=cams,
    )

    fused_rgb = out["erp_rgb"]
    fused_d = out["erp_depth"]
    naive_rgb = out["naive_erp_rgb"]
    naive_d = out["naive_erp_depth"]
    cov = out["erp_coverage"]
    diag = out["diagnostics"]

    # RMSE between fused depth and naive depth where both > 0
    common = (fused_d > 0) & (naive_d > 0)
    if common.any():
        diff = fused_d[common] - naive_d[common]
        diag["rmse_fused_vs_naive_m_all"] = float(np.sqrt(np.mean(diff ** 2)))
        diag["mae_fused_vs_naive_m_all"] = float(np.mean(np.abs(diff)))
    else:
        diag["rmse_fused_vs_naive_m_all"] = 0.0
        diag["mae_fused_vs_naive_m_all"] = 0.0

    overlap_common = common & (cov >= 2)
    single_common = common & (cov == 1)
    if overlap_common.any():
        diff_o = fused_d[overlap_common] - naive_d[overlap_common]
        diag["rmse_fused_vs_naive_m_overlap"] = float(np.sqrt(np.mean(diff_o ** 2)))
        diag["mae_fused_vs_naive_m_overlap"] = float(np.mean(np.abs(diff_o)))
    else:
        diag["rmse_fused_vs_naive_m_overlap"] = 0.0
        diag["mae_fused_vs_naive_m_overlap"] = 0.0
    if single_common.any():
        diff_s = fused_d[single_common] - naive_d[single_common]
        diag["rmse_fused_vs_naive_m_single_cam"] = float(np.sqrt(np.mean(diff_s ** 2)))
    else:
        diag["rmse_fused_vs_naive_m_single_cam"] = 0.0

    # ---- Save numpy ----
    np.save(out_dir / "erp_naive_depth.npy", naive_d)
    np.save(out_dir / "erp_bayesian_depth.npy", fused_d)
    np.save(out_dir / "erp_coverage.npy", cov)
    Image.fromarray(np.clip(naive_rgb, 0, 255).astype(np.uint8)).save(
        out_dir / "erp_naive_rgb.png"
    )
    Image.fromarray(np.clip(fused_rgb, 0, 255).astype(np.uint8)).save(
        out_dir / "erp_bayesian_rgb.png"
    )

    # ---- Visualisations ----
    naive_d_vis = _depth_to_uint8(naive_d, max_m=args.depth_vis_max_m)
    fused_d_vis = _depth_to_uint8(fused_d, max_m=args.depth_vis_max_m)
    diff_map = np.where(common, np.abs(fused_d - naive_d), 0.0)
    diff_vis = _depth_to_uint8(diff_map, max_m=2.0)  # diff colormap saturates at 2 m
    cov_vis = _coverage_to_uint8(cov)

    _save_3panel(
        out_dir / "rgb_naive_vs_bayesian.png",
        [naive_rgb, fused_rgb, np.abs(fused_rgb - naive_rgb)],
        ["naive z-buffer RGB", "Bayesian-fused RGB", "|diff| (clip 255)"],
    )
    _save_3panel(
        out_dir / "depth_naive_vs_bayesian.png",
        [naive_d_vis, fused_d_vis, diff_vis],
        [
            f"naive depth (0-{args.depth_vis_max_m:.0f} m viridis)",
            f"Bayesian depth (0-{args.depth_vis_max_m:.0f} m viridis)",
            "|Bayes - naive| (0-2 m viridis)",
        ],
    )
    Image.fromarray(cov_vis).save(out_dir / "coverage.png")

    # ---- Summary JSON ----
    summary_out = {
        "anchor_dir": str(anchor_dir),
        "anchor_idx": summary.get("anchor_idx"),
        "cams": cams,
        "erp_hw": [erp_h, erp_w],
        "sim3": sim3_diag,
        "diagnostics": diag,
        "conf_threshold": args.conf_threshold,
        "min_distance_m": args.min_distance_m,
        "max_distance_m": args.max_distance_m,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary_out, indent=2))

    # ---- Console pretty-print ----
    print(f"[T16] coverage    : {diag['coverage_ratio']:.1%} of ERP "
          f"({diag['coverage_pixels']} px)")
    print(f"[T16] overlap >=2 : {diag['overlap_ratio']:.1%} of ERP "
          f"({diag['overlap_pixels']} px, max={diag['max_coverage']} cams)")
    print(f"[T16] depth delta on overlap: mean={diag['mean_depth_diff_m_overlap']:.3f} m, "
          f"median={diag['median_depth_diff_m_overlap']:.3f} m, "
          f"p95={diag['p95_depth_diff_m_overlap']:.3f} m")
    print(f"[T16] RMSE(fused, naive) overlap-only = "
          f"{diag['rmse_fused_vs_naive_m_overlap']:.3f} m, "
          f"single-cam = {diag['rmse_fused_vs_naive_m_single_cam']:.3f} m")
    print(f"[T16] wrote {out_dir}/{{erp_*.npy, *_rgb.png, *_naive_vs_bayesian.png, summary.json}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
