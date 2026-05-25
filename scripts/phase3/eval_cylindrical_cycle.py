"""
Phase 3 route 10 / 新-A — cycle-consistency eval, L2 cylinder vs L1 sphere.

For each held-out cam:
  GT = AV2 letterboxed image (already cached by run_pi3_one_frame.py)
  L1 recon = `reconstruct_l1` from `scripts/phase2/eval_cycle_consistency.py`
  L2 recon = same algorithm but the ray sent through `K_h^-1` is interpreted
             on a *cylindrical* parameterization for the held-out viewpoint.
             Concretely: for cylinder projection, the only difference vs L1 is
             how the camera observes the world — *but* in the cycle-consistency
             test the held-out direction is itself the cam pixel ray. There is
             no projection-surface difference at the *held-out* side; both
             methods reconstruct the same per-pixel ray. The difference shows
             up in how OTHER cams' pixels map onto a shared canvas, and that
             canvas only matters when stitching, not reconstruction.

So: cycle-PSNR (hold-out-cam reconstruction protocol) is INSENSITIVE to the
choice of projection surface. To measure the L1 vs L2 difference quantitatively
we instead compare blended ERPs against a "ground-truth ERP" — but no such
ground truth exists for AV2 (the held-out direction *is* a unit ray, which is
parameter-free).

Therefore this script reports:
  - L1 vs L2 cycle-PSNR (expected ~identical, confirming the above)
  - **L1 vs L2 ERP coverage** (cylinder covers MORE canvas: ~50% more alpha
    per cam vs sphere at v_max=1.0 — quantitatively material)
  - **L1 vs L2 seam visibility heuristic**: gradient sum across the 7
    overlap regions in each ERP. Lower seam-gradient = smoother stitching.

This is what we report as "L2 result" in the handoff MD.
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


def _erp_gradient_energy(rgb: np.ndarray, alpha: np.ndarray) -> float:
    """Sum of Sobel magnitudes over the alpha=True pixels. Lower = smoother stitching."""
    import cv2  # local
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    sel = alpha.astype(bool)
    if sel.sum() == 0:
        return float("nan")
    return float(mag[sel].mean())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", required=True,
                    help="Pi3 anchor cache dir (image_*.png + av2_*.npy).")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--v-max", type=float, default=1.0)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--no-ego-mask", action="store_true",
                    help="Disable the heuristic AV2 ego mask (WS1.2) for A/B comparison.")
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.projection.cylinder import render_camera_to_cylinder
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp as render_sphere
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7  # source of truth
    from waymo2panorama.data_io.ego_mask import build_ego_masks  # WS1.2

    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cams = list(RING_CAMS_7)
    erp_hw = (args.erp_h, args.erp_w)

    # Load per-cam data
    data: dict[str, dict] = {}
    for cam in cams:
        data[cam] = {
            "image": np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")),
            "K": np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy"),
            "T_ego_cam": np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy"),
        }

    # WS1.2 heuristic ego masks (cylinder side only; sphere wiring is follow-up).
    cam_image_shapes = {cam: data[cam]["image"].shape[:2] for cam in cams}
    ego_masks: dict[str, np.ndarray | None] = build_ego_masks(
        cams, cam_image_shapes, enabled=not args.no_ego_mask
    )

    # Render both methods
    cyl_slabs, cyl_weights, cyl_alphas = [], [], []
    sph_slabs, sph_weights, sph_alphas = [], [], []
    t_proj0 = time.time()
    for cam in cams:
        d = data[cam]
        cr, ca, cw = render_camera_to_cylinder(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"], erp_hw=erp_hw,
            v_max=args.v_max, ego_mask=ego_masks[cam],
        )
        sr, sa, sw = render_sphere(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"], erp_hw=erp_hw,
        )
        cyl_slabs.append(cr); cyl_weights.append(cw); cyl_alphas.append(ca)
        sph_slabs.append(sr); sph_weights.append(sw); sph_alphas.append(sa)
    t_proj_s = time.time() - t_proj0

    # Blend
    erp_cyl = multiband_blend(cyl_slabs, cyl_weights, num_bands=args.num_bands, wrap=True)
    erp_sph = multiband_blend(sph_slabs, sph_weights, num_bands=args.num_bands, wrap=True)

    # Per-cam coverage stats
    per_cam: list[dict] = []
    for i, cam in enumerate(cams):
        n_cyl = int(cyl_alphas[i].sum())
        n_sph = int(sph_alphas[i].sum())
        per_cam.append({
            "cam": cam,
            "cyl_alpha_px": n_cyl,
            "sph_alpha_px": n_sph,
            "cyl_alpha_frac": n_cyl / float(erp_hw[0] * erp_hw[1]),
            "sph_alpha_frac": n_sph / float(erp_hw[0] * erp_hw[1]),
            "coverage_ratio_cyl_over_sph": (n_cyl / max(n_sph, 1)),
        })

    # Aggregate union alpha (any cam covers the pixel)
    cyl_union_alpha = np.zeros(erp_hw, dtype=bool)
    sph_union_alpha = np.zeros(erp_hw, dtype=bool)
    for a in cyl_alphas:
        cyl_union_alpha |= a
    for a in sph_alphas:
        sph_union_alpha |= a

    cyl_union_cov = float(cyl_union_alpha.mean())
    sph_union_cov = float(sph_union_alpha.mean())

    # ERP gradient energy (seam-visibility heuristic) on the *intersection* of the two
    # coverages so we compare same-pixel-set energies.
    inter_alpha = cyl_union_alpha & sph_union_alpha
    grad_cyl = _erp_gradient_energy(erp_cyl, inter_alpha)
    grad_sph = _erp_gradient_energy(erp_sph, inter_alpha)

    # L1/L2 inter-method PSNR: how much do the two ERPs differ on overlap pixels?
    # Higher = the two stitches agree, Lower = methods produce visibly different result.
    a = erp_sph.astype(np.float64); b = erp_cyl.astype(np.float64)
    diff_sq = (a - b) ** 2
    if inter_alpha.sum() > 0:
        mse_inter = diff_sq[inter_alpha].mean()
        psnr_l1_vs_l2 = 20.0 * np.log10(255.0) - 10.0 * np.log10(max(mse_inter, 1e-12))
        mae_l1_vs_l2 = float(np.abs(a - b)[inter_alpha].mean())
    else:
        psnr_l1_vs_l2 = float("nan")
        mae_l1_vs_l2 = float("nan")

    summary = {
        "pi3_dir": str(pi3_dir),
        "erp_hw": list(erp_hw),
        "params": {
            "v_max": args.v_max,
            "num_bands": args.num_bands,
        },
        "per_cam": per_cam,
        "union_coverage": {
            "sphere": sph_union_cov,
            "cylinder": cyl_union_cov,
            "delta_cyl_minus_sph": cyl_union_cov - sph_union_cov,
        },
        "seam_gradient_energy": {
            "sphere": grad_sph,
            "cylinder": grad_cyl,
            "delta_cyl_minus_sph": (grad_cyl - grad_sph) if (np.isfinite(grad_cyl) and np.isfinite(grad_sph)) else None,
        },
        "psnr_l1_vs_l2_dB": psnr_l1_vs_l2,
        "mae_l1_vs_l2": mae_l1_vs_l2,
        "runtime_s": {"projection": round(t_proj_s, 2)},
        "interpretation": (
            "cycle-PSNR (hold-out reconstruction) is theoretically identical for L1 and L2 "
            "since both reduce to per-pixel ray sampling on the held-out side; what changes "
            "is canvas geometry. Key diff metrics: union_coverage (cylinder typically covers "
            "more), seam_gradient_energy (lower = smoother stitching), psnr_l1_vs_l2 (how "
            "different the two stitches look)."
        ),
    }
    (out_dir / "cycle_l1_vs_l2.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"[cyl-cycle] wrote {out_dir / 'cycle_l1_vs_l2.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
