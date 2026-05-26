"""WS4 B1 — L1 sphere + disparity-aware graphcut seam (per-pair hard mask).

Pipeline:
  1. Render L1 sphere ERP slabs + weights (UNCHANGED L1).
  2. For each adjacent ring pair, build disparity from stereo cache.
  3. Find min-disparity seam through overlap; replace soft cos^2 blend with
     hard 0/1 mask (with optional soft edge).
  4. Multi-band blend with modified weights (UNCHANGED multiband).

A/B baseline: --no-seam skips step 2-3, plain L1.

Usage:
    python scripts/phase3/run_l1_graphcut_disp.py \\
        --pi3-dir outputs/phase3/p3.1_multi_anchor/anchor_060 \\
        --stereo-cache-dir outputs/phase3/p3.6_stereo/anchor_060 \\
        --output-dir outputs/phase3/p3.X_parallax/anchor_060_b1
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
    ap.add_argument("--stereo-cache-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--no-seam", action="store_true",
                    help="A/B baseline: skip seam, plain L1 output.")
    ap.add_argument("--no-ego-mask", action="store_true")
    ap.add_argument("--disparity-sigma-px", type=float, default=20.0)
    ap.add_argument("--seam-smoothness", type=float, default=1.0)
    ap.add_argument("--seam-soft-px", type=int, default=2)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.alignment.pair_homography import ADJACENT_PAIRS
    from waymo2panorama.blending.graphcut_disparity import build_seam_weights_b1
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    cams = list(RING_CAMS_7)
    print(f"[b1-graphcut] mode={'NO-SEAM plain L1' if args.no_seam else 'B1 seam'}, "
          f"erp_hw={erp_hw}", flush=True)

    per_cam = {cam: _load_pi3_cam(args.pi3_dir, cam) for cam in cams}
    cam_image_shapes = {cam: per_cam[cam]["image"].shape[:2] for cam in cams}
    ego_masks = build_ego_masks(cams, cam_image_shapes, enabled=not args.no_ego_mask)

    t_proj0 = time.time()
    slabs_dict: dict[str, np.ndarray] = {}
    weights_dict: dict[str, np.ndarray] = {}
    for cam in cams:
        d = per_cam[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        slabs_dict[cam] = rgb; weights_dict[cam] = w
    t_proj_s = time.time() - t_proj0

    t_seam0 = time.time()
    b1_summary = None
    if not args.no_seam:
        stereo_paths = sorted(args.stereo_cache_dir.glob("stereo_*.npz"))
        cam_K = {cam: per_cam[cam]["K"] for cam in cams}
        cam_T = {cam: per_cam[cam]["T_ego_cam"] for cam in cams}
        modified_weights, b1_summary = build_seam_weights_b1(
            l1_weights=weights_dict, stereo_npz_paths=stereo_paths,
            cam_K=cam_K, cam_T_ego_cam=cam_T,
            adjacent_pairs=ADJACENT_PAIRS, erp_hw=erp_hw,
            disparity_sigma_px=args.disparity_sigma_px,
            seam_smoothness=args.seam_smoothness,
            seam_soft_px=args.seam_soft_px,
        )
        weights_dict = modified_weights
    t_seam_s = time.time() - t_seam0

    t_blend0 = time.time()
    slabs_list = [slabs_dict[c] for c in cams]
    weights_list = [weights_dict[c] for c in cams]
    erp = multiband_blend(slabs_list, weights_list, num_bands=args.num_bands, wrap=True)
    t_blend_s = time.time() - t_blend0

    out_png = out_dir / "l1_graphcut_disp.png"
    Image.fromarray(erp).save(out_png)
    print(f"[b1-graphcut] wrote {out_png}", flush=True)

    summary = {
        "route": "WS4 B1 — L1 + disparity-aware graphcut seam",
        "mode": "no-seam (plain L1)" if args.no_seam else "seam",
        "pi3_dir": str(args.pi3_dir),
        "stereo_cache_dir": str(args.stereo_cache_dir),
        "erp_hw": list(erp_hw),
        "params": {
            "disparity_sigma_px": args.disparity_sigma_px,
            "seam_smoothness": args.seam_smoothness,
            "seam_soft_px": args.seam_soft_px,
            "no_ego_mask": bool(args.no_ego_mask),
        },
        "b1_seam_summary": b1_summary,
        "runtime_s": {
            "projection": round(t_proj_s, 3),
            "seam": round(t_seam_s, 3),
            "blend": round(t_blend_s, 3),
        },
        "outputs": {"l1_graphcut_disp": str(out_png.resolve())},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
