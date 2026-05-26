"""WS4 A2 — L1 sphere + sparse-stereo-driven ERP displacement warp.

Pipeline:
  1. Render L1 sphere ERP slabs + weights for all 7 cams (UNCHANGED L1).
  2. Build per-cam dense displacement fields from cached stereo .npz.
  3. Warp each cam's ERP slab by its displacement (gated by confidence).
  4. Multi-band blend the warped slabs (UNCHANGED multiband).

A/B baseline: --no-warp skips step 2-3, equivalent to plain L1.

Usage (single anchor, Pi3 cache):
    python scripts/phase3/run_l1_sparse_disp.py \\
        --pi3-dir outputs/phase3/p3.1_multi_anchor/anchor_060 \\
        --stereo-cache-dir outputs/phase3/p3.6_stereo/anchor_060 \\
        --output-dir outputs/phase3/p3.X_parallax/anchor_060_a2
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
    ap.add_argument("--no-warp", action="store_true",
                    help="A/B baseline: skip A2 displacement warp, plain L1 output.")
    ap.add_argument("--no-ego-mask", action="store_true")
    ap.add_argument("--rbf-regularization", type=float, default=1.0)
    ap.add_argument("--confidence-sigma-px", type=float, default=20.0)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.alignment.sparse_displacement import build_warped_slabs_a2
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    cams = list(RING_CAMS_7)
    print(f"[a2-sparse-disp] mode={'NO-WARP plain L1' if args.no_warp else 'A2 displacement warp'}, "
          f"erp_hw={erp_hw}", flush=True)

    per_cam = {cam: _load_pi3_cam(args.pi3_dir, cam) for cam in cams}
    cam_image_shapes = {cam: per_cam[cam]["image"].shape[:2] for cam in cams}
    ego_masks = build_ego_masks(cams, cam_image_shapes, enabled=not args.no_ego_mask)

    # L1 sphere projection (UNCHANGED)
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

    # A2 warp
    t_warp0 = time.time()
    a2_summary = None
    if not args.no_warp:
        stereo_paths = sorted(args.stereo_cache_dir.glob("stereo_*.npz"))
        cam_K = {cam: per_cam[cam]["K"] for cam in cams}
        cam_T = {cam: per_cam[cam]["T_ego_cam"] for cam in cams}
        warped, a2_summary = build_warped_slabs_a2(
            l1_slabs=slabs_dict, stereo_npz_paths=stereo_paths,
            cam_K=cam_K, cam_T_ego_cam=cam_T, cam_names=cams,
            erp_hw=erp_hw,
            rbf_regularization=args.rbf_regularization,
            confidence_sigma_px=args.confidence_sigma_px,
        )
        slabs_dict = warped
    t_warp_s = time.time() - t_warp0

    # Multi-band blend (UNCHANGED)
    t_blend0 = time.time()
    slabs_list = [slabs_dict[c] for c in cams]
    weights_list = [weights_dict[c] for c in cams]
    erp = multiband_blend(slabs_list, weights_list, num_bands=args.num_bands, wrap=True)
    t_blend_s = time.time() - t_blend0

    out_png = out_dir / "l1_sparse_disp.png"
    Image.fromarray(erp).save(out_png)
    print(f"[a2-sparse-disp] wrote {out_png}", flush=True)

    summary = {
        "route": "WS4 A2 — L1 + sparse stereo ERP displacement",
        "mode": "no-warp (plain L1)" if args.no_warp else "warped",
        "pi3_dir": str(args.pi3_dir),
        "stereo_cache_dir": str(args.stereo_cache_dir),
        "erp_hw": list(erp_hw),
        "params": {
            "rbf_regularization": args.rbf_regularization,
            "confidence_sigma_px": args.confidence_sigma_px,
            "no_ego_mask": bool(args.no_ego_mask),
        },
        "a2_warp_summary": a2_summary,
        "runtime_s": {
            "projection": round(t_proj_s, 3),
            "warp": round(t_warp_s, 3),
            "blend": round(t_blend_s, 3),
        },
        "outputs": {"l1_sparse_disp": str(out_png.resolve())},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
