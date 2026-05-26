"""
Phase 3 route 13 / 新-D — Option B reweight driver: L1 sphere stitch boosted by
sparse stereo confidence (`build_stereo_confidence_mask -> apply_option_b_reweight`).

Loads:
    --pi3-dir          : Pi3 anchor cache (image_*.png + av2_K_*.npy + av2_T_ego_cam_*.npy)
    --stereo-cache-dir : 新-D output dir (stereo_<a>__<b>.npz files from `run_wide_baseline_stereo.py`)

Pipeline (single anchor):
    1. Build L1 sphere ERP slabs + weights (`render_camera_to_erp` for each of 7 ring cams)
    2. Build stereo confidence mask from all stereo_*.npz files in --stereo-cache-dir
    3. Apply Option B reweight (weights *= 1 + alpha * confidence_mask)
    4. Multi-band blend -> final ERP
    5. Save: option_b_l1.png, confidence_mask.png (viz), summary.json

A/B mode (--no-reweight): skip steps 2-3 (also skip confidence_mask.png) and
write plain L1 ERP as option_b_l1.png. Useful for back-to-back compare against
the reweighted version.

Usage (single anchor, default alpha=1.0):
    python scripts/phase3/run_option_b_reweight.py \\
        --pi3-dir outputs/phase3/pi3_cache/anchor_060 \\
        --stereo-cache-dir outputs/phase3/p3.6_stereo/anchor_060 \\
        --output-dir outputs/phase3/p3.7_option_b/anchor_060

A/B baseline (plain L1, same script):
    python scripts/phase3/run_option_b_reweight.py \\
        --pi3-dir outputs/phase3/pi3_cache/anchor_060 \\
        --stereo-cache-dir outputs/phase3/p3.6_stereo/anchor_060 \\
        --output-dir outputs/phase3/p3.7_option_b/anchor_060_noreweight \\
        --no-reweight
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
        "K": np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy"),
        "T_ego_cam": np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy"),
    }


def _stereo_npz_paths(stereo_cache_dir: Path) -> list[Path]:
    """All `stereo_<a>__<b>.npz` files in stereo cache dir, sorted for determinism."""
    return sorted(stereo_cache_dir.glob("stereo_*.npz"))


def _confidence_viz(mask: np.ndarray) -> np.ndarray:
    """Float [0,1] mask -> uint8 RGB heatmap (turbo) for QA."""
    import cv2  # local import
    m8 = np.clip(mask * 255.0, 0, 255).astype(np.uint8)
    rgb_bgr = cv2.applyColorMap(m8, cv2.COLORMAP_TURBO)
    return rgb_bgr[..., ::-1]  # BGR -> RGB


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", type=Path, required=True,
                    help="Pi3 anchor cache (image_*.png + av2_K_*.npy + av2_T_ego_cam_*.npy).")
    ap.add_argument("--stereo-cache-dir", type=Path, required=True,
                    help="新-D output dir with stereo_<a>__<b>.npz files. "
                         "Ignored if --no-reweight is set.")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--alpha", type=float, default=1.0,
                    help="Option B boost magnitude: weights *= (1 + alpha * confidence). "
                         "0 = no boost (= plain L1); 1.0 = double weight at full-confidence "
                         "pixels; typical sweep range [0.5, 2.0]. Ignored if --no-reweight.")
    ap.add_argument("--sigma-px", type=float, default=12.0,
                    help="Stddev (in ERP pixels) of the Gaussian splat per stereo point.")
    ap.add_argument("--no-reweight", action="store_true",
                    help="A/B flag: skip stereo + reweight, just write plain L1 ERP.")
    ap.add_argument("--mask-mode", choices=["v1", "v2", "v3"], default="v3",
                    help="v1: single uniform mask (NEG-by-effect: multiband normalize "
                         "cancels uniform boost). v2: per-cam symmetric (same points "
                         "splatted into both cams in a pair -> still cancels in 2-cam "
                         "overlap regions). v3 (default): per-cam ASYMMETRIC using ray-"
                         "angle winner-take-all (each stereo point splatted into the cam "
                         "with smaller ray angle, NOT both). Breaks the symmetry that "
                         "killed v2.")
    ap.add_argument("--v3-selection", choices=["winner_take_all", "soft_cos_angle"],
                    default="winner_take_all",
                    help="v3 only: which asymmetric splat rule. winner_take_all (default) "
                         "picks one cam per point; soft_cos_angle splats both with "
                         "amp = cos(angle)^2.")
    ap.add_argument("--no-ego-mask", action="store_true",
                    help="Disable heuristic AV2 ego mask (WS1.2) — for A/B parity with "
                         "older runs that did not have the mask.")
    ap.add_argument("--w2p-code", default=None,
                    help="Path to the `code/` dir holding the waymo2panorama package.")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.pipeline.option_b_reweight import (
        apply_option_b_reweight,
        build_stereo_confidence_mask,
        build_stereo_confidence_masks_per_cam,
        build_stereo_confidence_masks_per_cam_v3,
    )
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not pi3_dir.exists():
        raise FileNotFoundError(pi3_dir)

    erp_hw = (args.erp_h, args.erp_w)
    cams = list(RING_CAMS_7)
    print(f"[option_b] mode={'NO-REWEIGHT (plain L1)' if args.no_reweight else 'REWEIGHT'}, "
          f"erp_hw={erp_hw}, alpha={args.alpha}, sigma_px={args.sigma_px}", flush=True)

    # ---- load per-cam data ----
    per_cam_data: dict[str, dict] = {cam: _load_pi3_cam(pi3_dir, cam) for cam in cams}

    cam_image_shapes = {cam: per_cam_data[cam]["image"].shape[:2] for cam in cams}
    ego_masks = build_ego_masks(cams, cam_image_shapes, enabled=not args.no_ego_mask)

    # ---- render L1 sphere slabs ----
    t_proj0 = time.time()
    sph_slabs: list[np.ndarray] = []
    sph_weights: list[np.ndarray] = []
    per_cam_log: list[dict] = []
    for cam in cams:
        d = per_cam_data[cam]
        rgb, alpha_map, w = render_camera_to_erp(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        sph_slabs.append(rgb)
        sph_weights.append(w)
        per_cam_log.append({
            "cam": cam,
            "src_hw": list(d["image"].shape[:2]),
            "alpha_px": int(alpha_map.sum()),
            "weight_sum": float(w.sum()),
        })
        print(f"[option_b]   {cam}: alpha_px={int(alpha_map.sum())} weight_sum={float(w.sum()):.1f}",
              flush=True)
    t_proj_s = time.time() - t_proj0

    # ---- build stereo confidence mask + reweight (unless --no-reweight) ----
    confidence_mask: np.ndarray | None = None
    confidence_masks_per_cam: dict[str, np.ndarray] | None = None
    n_stereo_files = 0
    if not args.no_reweight:
        stereo_dir = Path(args.stereo_cache_dir)
        if not stereo_dir.exists():
            raise FileNotFoundError(stereo_dir)
        stereo_paths = _stereo_npz_paths(stereo_dir)
        n_stereo_files = len(stereo_paths)
        if n_stereo_files == 0:
            print(f"[option_b] WARN: no stereo_*.npz files in {stereo_dir} — falling back to plain L1")
        t_mask0 = time.time()

        # Convert list weights → dict (cam-keyed) so per-cam mask path works
        sph_weights_dict = {cam: w for cam, w in zip(cams, sph_weights)}

        if args.mask_mode == "v3":
            cam_T_ego_cam = {cam: per_cam_data[cam]["T_ego_cam"] for cam in cams}
            confidence_masks_per_cam = build_stereo_confidence_masks_per_cam_v3(
                stereo_paths, erp_hw=erp_hw, cam_names=cams,
                cam_T_ego_cam=cam_T_ego_cam, sigma_px=args.sigma_px,
                selection_mode=args.v3_selection,
            )
            sph_weights_dict = apply_option_b_reweight(
                sph_weights_dict, confidence_masks_per_cam, alpha=args.alpha,
            )
            confidence_mask = np.max(
                np.stack([confidence_masks_per_cam[c] for c in cams], axis=0), axis=0,
            )
        elif args.mask_mode == "v2":
            confidence_masks_per_cam = build_stereo_confidence_masks_per_cam(
                stereo_paths, erp_hw=erp_hw, cam_names=cams, sigma_px=args.sigma_px,
            )
            sph_weights_dict = apply_option_b_reweight(
                sph_weights_dict, confidence_masks_per_cam, alpha=args.alpha,
            )
            # Save a "max-over-cams" viz mask for QA (same shape as v1 mask)
            confidence_mask = np.max(
                np.stack([confidence_masks_per_cam[c] for c in cams], axis=0), axis=0,
            )
        else:  # v1 — legacy / NEG reproducer
            confidence_mask = build_stereo_confidence_mask(
                stereo_paths, erp_hw=erp_hw, sigma_px=args.sigma_px,
            )
            sph_weights_dict = apply_option_b_reweight(
                sph_weights_dict, confidence_mask, alpha=args.alpha,
            )

        # Back to list-of-cams (cams order) for multiband_blend
        sph_weights = [sph_weights_dict[c] for c in cams]
        t_mask_s = time.time() - t_mask0
    else:
        t_mask_s = 0.0

    # ---- multi-band blend ----
    t_blend0 = time.time()
    erp = multiband_blend(sph_slabs, sph_weights, num_bands=args.num_bands, wrap=True)
    t_blend_s = time.time() - t_blend0

    # ---- save outputs ----
    out_png = out_dir / "option_b_l1.png"
    Image.fromarray(erp).save(out_png)
    print(f"[option_b] wrote {out_png}", flush=True)

    if confidence_mask is not None:
        # PNG viz (turbo) + raw .npy for reuse
        Image.fromarray(_confidence_viz(confidence_mask)).save(out_dir / "confidence_mask.png")
        np.save(out_dir / "confidence_mask.npy", confidence_mask.astype(np.float32))

    # Coverage / mask stats
    mask_stats: dict | None = None
    if confidence_mask is not None:
        mask_stats = {
            "mean": float(confidence_mask.mean()),
            "max": float(confidence_mask.max()),
            "frac_above_0.05": float((confidence_mask > 0.05).mean()),
            "frac_above_0.25": float((confidence_mask > 0.25).mean()),
            "frac_above_0.5":  float((confidence_mask > 0.5).mean()),
        }

    # Per-cam mask stats (v2 only)
    per_cam_mask_stats: dict[str, dict] | None = None
    if confidence_masks_per_cam is not None:
        per_cam_mask_stats = {
            c: {
                "max": float(confidence_masks_per_cam[c].max()),
                "frac_above_0.05": float((confidence_masks_per_cam[c] > 0.05).mean()),
            }
            for c in cams
        }

    summary = {
        "route": "13 / 新-D Option B",
        "mode": (
            "no-reweight (plain L1)" if args.no_reweight
            else f"reweight ({args.mask_mode})"
        ),
        "pi3_dir": str(pi3_dir),
        "stereo_cache_dir": (None if args.no_reweight else str(args.stereo_cache_dir)),
        "n_stereo_files": n_stereo_files,
        "erp_hw": list(erp_hw),
        "params": {
            "alpha": args.alpha if not args.no_reweight else None,
            "sigma_px": args.sigma_px if not args.no_reweight else None,
            "num_bands": args.num_bands,
            "no_ego_mask": bool(args.no_ego_mask),
            "mask_mode": (None if args.no_reweight else args.mask_mode),
            "v3_selection": (
                args.v3_selection if (not args.no_reweight and args.mask_mode == "v3") else None
            ),
        },
        "per_cam": per_cam_log,
        "mask_stats": mask_stats,
        "per_cam_mask_stats": per_cam_mask_stats,
        "runtime_s": {
            "projection": round(t_proj_s, 3),
            "confidence_mask": round(t_mask_s, 3),
            "blend": round(t_blend_s, 3),
        },
        "outputs": {
            "option_b_l1": str(out_png.resolve()),
            "confidence_mask_png": (
                str((out_dir / "confidence_mask.png").resolve())
                if confidence_mask is not None else None
            ),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[option_b] summary -> {out_dir / 'summary.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
