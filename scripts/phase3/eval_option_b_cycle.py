"""
Phase 3 route 13 / 新-D — Option B cycle-PSNR eval (L1 baseline vs L1+reweight).

Like `eval_cylindrical_cycle.py`, this measures *inter-method* PSNR: we blend
the SAME per-cam L1 slabs twice (plain L1 weights, and L1*(1+alpha*confidence)
weights), then compare the two final ERPs on the union of L1 coverage. We
report:

  - per-anchor psnr_l1_reweighted_vs_baseline_dB
      (PSNR between the two ERPs on the L1-covered region; LOWER means the
       reweight moved pixels more)
  - per-anchor psnr_in_confidence_region_dB
      (same PSNR, restricted to the high-confidence stereo region; we expect
       this to be LOWER than the union value if reweight is targeting the
       right pixels — that's the "Option B is doing what we want" signature)
  - per-anchor mae_change_on_union
      (mean abs RGB delta in [0, 255] units — interpretable magnitude check)
  - aggregate (mean) across anchors

This intentionally does NOT replicate the per-cam held-out cycle-PSNR
protocol of `scripts/phase2/eval_cycle_consistency.py` (which produces the
"L1 = 12.34 dB" headline). Doing so here would duplicate ~150 LOC. Instead,
the controller pairs this eval with a separate run of `eval_cycle_consistency.py`
on the driver's output (option_b_l1.png) to obtain the *absolute* PSNR
numbers and the delta vs the baseline 12.34 dB.

Per-anchor checkpointing (optional): wraps the anchor loop in
`colab_direct.checkpointed` if that module is available; otherwise the
checkpointing is a no-op decorator. This lets long runs survive a Colab
kernel restart without re-doing finished anchors.

Usage:
    python scripts/phase3/eval_option_b_cycle.py \\
        --pi3-cache-root outputs/phase3/pi3_cache \\
        --stereo-cache-root outputs/phase3/p3.6_stereo \\
        --anchors 0 60 90 150 \\
        --output-dir outputs/phase3/p3.7_option_b/eval_cycle \\
        --alpha 1.0
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


# ---------------------------------------------------------------------------
# Optional Colab-direct checkpointing
# ---------------------------------------------------------------------------
#
# colab_direct.checkpointed has signature (unit_id_fn, storage_dir, verbose=True).
# On Colab we wrap the anchor loop so a kernel restart skips already-finished
# anchors via .done markers in storage_dir. Locally (no colab_direct), the
# decorator becomes a pass-through.

try:
    import colab_direct as cd  # type: ignore
    _HAS_CD = True

    def _make_checkpointed(storage_dir):
        return cd.checkpointed(
            unit_id_fn=lambda anchor_idx, **_: f"anchor_{int(anchor_idx):03d}",
            storage_dir=storage_dir,
            verbose=True,
        )
except ImportError:
    _HAS_CD = False

    def _make_checkpointed(storage_dir):
        """No-op fallback when colab_direct is unavailable (local runs)."""
        def wrapper(fn):
            return fn
        return wrapper


# ---------------------------------------------------------------------------
# PSNR helper (matches eval_cycle_consistency.py)
# ---------------------------------------------------------------------------


def _psnr_masked(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    """PSNR on uint8 RGB, scored only where mask is True. NaN if no pixels."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    if mask.sum() == 0:
        return float("nan")
    diff_sq = (a - b) ** 2
    mse = diff_sq[mask].mean()
    if mse <= 1e-12:
        return float("inf")
    return 20.0 * np.log10(255.0) - 10.0 * np.log10(mse)


# ---------------------------------------------------------------------------
# Single-anchor evaluator
# ---------------------------------------------------------------------------


def _eval_one_anchor(
    pi3_dir: Path,
    stereo_dir: Path,
    erp_hw: tuple[int, int],
    alpha: float,
    sigma_px: float,
    num_bands: int,
    no_ego_mask: bool,
    mask_mode: str = "v3",
    v3_selection: str = "winner_take_all",
) -> dict:
    """Run both L1-baseline and L1-reweighted blends, compute cycle PSNR (L1 vs
    L1_reweighted) on the union of L1 coverage. Returns a per-anchor summary dict.

    Important: this is NOT the held-out per-cam cycle protocol of
    eval_cycle_consistency.py (which needs a held-out GT and reconstructs into
    the cam plane). Here we have two ERP outputs from the same cam set and we
    want to know how much Option B moves them. The L1 baseline (no reweight)
    is the de-facto "GT" — cycle-PSNR(L1_reweighted, L1_baseline) tells us how
    much Option B perturbs the final pixels. To get the SIGN of the change
    relative to a real GT, callers should run the per-cam hold-out protocol
    separately; in practice the cycle-PSNR-of-ERPs metric tracks the per-cam
    metric well because both measure overlap-region pixel agreement.
    """
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

    cams = list(RING_CAMS_7)

    # Load per-cam data
    per_cam: dict[str, dict] = {}
    for cam in cams:
        per_cam[cam] = {
            "image": np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")),
            "K": np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy"),
            "T_ego_cam": np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy"),
        }

    cam_image_shapes = {cam: per_cam[cam]["image"].shape[:2] for cam in cams}
    ego_masks = build_ego_masks(cams, cam_image_shapes, enabled=not no_ego_mask)

    # Render L1 sphere slabs + weights once
    slabs: list[np.ndarray] = []
    weights_baseline: list[np.ndarray] = []
    alphas: list[np.ndarray] = []
    t_proj0 = time.time()
    for cam in cams:
        d = per_cam[cam]
        rgb, alpha_map, w = render_camera_to_erp(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        slabs.append(rgb)
        weights_baseline.append(w)
        alphas.append(alpha_map)
    t_proj_s = time.time() - t_proj0

    # Build stereo confidence mask(s)
    stereo_paths = sorted(stereo_dir.glob("stereo_*.npz"))
    t_mask0 = time.time()
    confidence_masks_per_cam: dict[str, np.ndarray] | None = None

    if mask_mode == "v3":
        cam_T_ego_cam = {cam: per_cam[cam]["T_ego_cam"] for cam in cams}
        confidence_masks_per_cam = build_stereo_confidence_masks_per_cam_v3(
            stereo_paths, erp_hw=erp_hw, cam_names=cams,
            cam_T_ego_cam=cam_T_ego_cam, sigma_px=sigma_px,
            selection_mode=v3_selection,
        )
        confidence_mask = np.max(
            np.stack([confidence_masks_per_cam[c] for c in cams], axis=0), axis=0,
        )
        weights_baseline_dict = {c: w for c, w in zip(cams, weights_baseline)}
        weights_reweighted_dict = apply_option_b_reweight(
            weights_baseline_dict, confidence_masks_per_cam, alpha=alpha,
        )
        weights_reweighted = [weights_reweighted_dict[c] for c in cams]
    elif mask_mode == "v2":
        confidence_masks_per_cam = build_stereo_confidence_masks_per_cam(
            stereo_paths, erp_hw=erp_hw, cam_names=cams, sigma_px=sigma_px,
        )
        # Max-over-cams view for downstream conf_region logic
        confidence_mask = np.max(
            np.stack([confidence_masks_per_cam[c] for c in cams], axis=0), axis=0,
        )
        # Reweight using per-cam dict
        weights_baseline_dict = {c: w for c, w in zip(cams, weights_baseline)}
        weights_reweighted_dict = apply_option_b_reweight(
            weights_baseline_dict, confidence_masks_per_cam, alpha=alpha,
        )
        weights_reweighted = [weights_reweighted_dict[c] for c in cams]
    else:  # v1 — legacy single mask
        confidence_mask = build_stereo_confidence_mask(
            stereo_paths, erp_hw=erp_hw, sigma_px=sigma_px,
        )
        weights_reweighted = apply_option_b_reweight(
            weights_baseline, confidence_mask, alpha=alpha,
        )
    t_mask_s = time.time() - t_mask0

    # Blend twice
    t_blend0 = time.time()
    erp_baseline = multiband_blend(slabs, weights_baseline, num_bands=num_bands, wrap=True)
    erp_reweight = multiband_blend(slabs, weights_reweighted, num_bands=num_bands, wrap=True)
    t_blend_s = time.time() - t_blend0

    # Score on union of L1 alpha (any cam contributed to the ERP)
    union_alpha = np.zeros(erp_hw, dtype=bool)
    for a in alphas:
        union_alpha |= a

    # ============================================================
    # Cycle-PSNR metric:
    # Following the pattern in `eval_cylindrical_cycle.py` (psnr_l1_vs_l2):
    # we compare the two final ERPs on the union of L1 coverage.
    #
    # For Option B specifically we ALSO need a baseline-quality number so the
    # controller can compare against the "L1 = 12.34 dB" headline. We get that
    # by re-using the held-out cycle protocol from `scripts/phase2/eval_cycle_
    # consistency.py:reconstruct_l1`: for each of the 7 cams, hold it out and
    # reconstruct its view from the other 6 (once with baseline weights, once
    # with reweighted). That gives a per-cam GT-anchored PSNR for both modes,
    # whose difference IS the meaningful delta_dB.
    #
    # We don't replicate that held-out protocol inline (it's 100+ LOC and is
    # cleanly done by the controller's `eval_cycle_consistency.py` invocation
    # against the option_b_l1.png output of the driver). What this eval does
    # add is the *inter-method* signal: psnr_reweight_vs_baseline tells us
    # "how much did Option B even move the pixels?" — close to inf means no
    # effect (likely too-small alpha or all-zero mask); ~25-40 dB means
    # noticeable shift; <20 dB means a big perturbation worth investigating.
    # ============================================================
    psnr_reweight_vs_baseline = _psnr_masked(erp_reweight, erp_baseline, union_alpha)

    # Also: confidence-region-restricted PSNR (only where stereo found structure).
    # This should be LOWER than the union number if reweight is doing meaningful
    # work concentrated where the stereo evidence is.
    conf_region = (confidence_mask > 0.25) & union_alpha
    psnr_inconf = _psnr_masked(erp_reweight, erp_baseline, conf_region)

    # MAE over union (more interpretable for "how much do RGB values move")
    a = erp_baseline.astype(np.float64)
    b = erp_reweight.astype(np.float64)
    if union_alpha.sum() > 0:
        mae_change = float(np.abs(a - b)[union_alpha].mean())
    else:
        mae_change = float("nan")

    return {
        "pi3_dir": str(pi3_dir),
        "stereo_dir": str(stereo_dir),
        "n_stereo_files": len(stereo_paths),
        "erp_hw": list(erp_hw),
        "params": {
            "alpha": alpha,
            "sigma_px": sigma_px,
            "num_bands": num_bands,
            "no_ego_mask": no_ego_mask,
        },
        "metrics": {
            # Main signal: PSNR between the two ERP stitches on L1-covered region.
            # LOWER value means reweight perturbed the image more.
            "psnr_l1_reweighted_vs_baseline_dB": psnr_reweight_vs_baseline,
            # Same metric restricted to high-confidence stereo regions:
            # a LOWER number here (vs the union number) is the expected
            # signature of working, region-targeted reweight.
            "psnr_in_confidence_region_dB": psnr_inconf,
            # Pixel-level MAE on union (0..255 scale) — interpretable as
            # "average RGB change per pixel". Higher = more reweight effect.
            "mae_change_on_union": mae_change,
        },
        "mask_stats": {
            "mean": float(confidence_mask.mean()),
            "max": float(confidence_mask.max()),
            "frac_above_0.05": float((confidence_mask > 0.05).mean()),
            "frac_above_0.25": float((confidence_mask > 0.25).mean()),
            "frac_above_0.5":  float((confidence_mask > 0.5).mean()),
        },
        "union_alpha_frac": float(union_alpha.mean()),
        "runtime_s": {
            "projection": round(t_proj_s, 3),
            "confidence_mask": round(t_mask_s, 3),
            "blend": round(t_blend_s, 3),
        },
    }


# ---------------------------------------------------------------------------
# Per-anchor checkpoint wrapper. We decorate at runtime (in main()) so the
# storage_dir is a CLI parameter; colab_direct.checkpointed keys each call by
# anchor_idx and writes a .done marker so resume skips finished anchors.
# ---------------------------------------------------------------------------


def _eval_one_anchor_for_checkpoint(
    anchor_idx: int,
    pi3_dir: str,
    stereo_dir: str,
    erp_h: int,
    erp_w: int,
    alpha: float,
    sigma_px: float,
    num_bands: int,
    no_ego_mask: bool,
    mask_mode: str = "v3",
    v3_selection: str = "winner_take_all",
) -> dict:
    """Primitive-arg wrapper so colab_direct.checkpointed can serialize args."""
    return _eval_one_anchor(
        pi3_dir=Path(pi3_dir),
        stereo_dir=Path(stereo_dir),
        erp_hw=(erp_h, erp_w),
        alpha=alpha,
        sigma_px=sigma_px,
        num_bands=num_bands,
        no_ego_mask=no_ego_mask,
        mask_mode=mask_mode,
        v3_selection=v3_selection,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    # Multi-anchor sweep
    ap.add_argument("--pi3-cache-root", type=Path, default=None,
                    help="Root containing anchor_NNN/ subdirs (e.g. outputs/phase3/pi3_cache).")
    ap.add_argument("--stereo-cache-root", type=Path, default=None,
                    help="Root containing anchor_NNN/ stereo_*.npz subdirs "
                         "(e.g. outputs/phase3/p3.6_stereo).")
    ap.add_argument("--anchors", nargs="+", type=int, default=None,
                    help="Anchor indices to eval (e.g. 0 60 90 150).")
    # Single-anchor mode
    ap.add_argument("--pi3-dir", type=Path, default=None)
    ap.add_argument("--stereo-cache-dir", type=Path, default=None)
    ap.add_argument("--output-dir", type=Path, required=True)
    # Hyperparams
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--alpha", type=float, default=1.0)
    ap.add_argument("--sigma-px", type=float, default=12.0)
    ap.add_argument("--no-ego-mask", action="store_true")
    ap.add_argument("--mask-mode", choices=["v1", "v2", "v3"], default="v3",
                    help="v3 (default): per-cam ASYMMETRIC ray-angle winner-take-all. "
                         "v2: per-cam symmetric (NEG, same mask both cams in a pair). "
                         "v1: single uniform mask (NEG, multiband cancels).")
    ap.add_argument("--v3-selection", choices=["winner_take_all", "soft_cos_angle"],
                    default="winner_take_all",
                    help="v3 only: which asymmetric splat rule.")
    ap.add_argument("--checkpoint-dir", type=Path, default=None,
                    help="Directory for colab_direct.checkpointed .done markers. "
                         "If unset, defaults to <output-dir>/_checkpoints. Ignored "
                         "if colab_direct is not importable.")
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir = args.checkpoint_dir if args.checkpoint_dir is not None else out_dir / "_checkpoints"
    print(f"[eval_option_b] colab_direct checkpointing: "
          f"{'ON (dir=' + str(ckpt_dir) + ')' if _HAS_CD else 'OFF (no-op)'}",
          flush=True)

    eval_one_anchor_checkpointed = _make_checkpointed(ckpt_dir)(_eval_one_anchor_for_checkpoint)

    # Resolve targets
    targets: list[tuple[int, Path, Path]] = []
    if args.pi3_dir is not None and args.stereo_cache_dir is not None:
        # Single-anchor mode
        try:
            anchor_idx = int(args.pi3_dir.name.split("_")[1])
        except (IndexError, ValueError):
            anchor_idx = -1
        targets.append((anchor_idx, args.pi3_dir, args.stereo_cache_dir))
    else:
        if args.pi3_cache_root is None or args.stereo_cache_root is None or args.anchors is None:
            ap.error("multi-anchor mode requires --pi3-cache-root + --stereo-cache-root + --anchors")
        for a in args.anchors:
            pi3 = args.pi3_cache_root / f"anchor_{int(a):03d}"
            ster = args.stereo_cache_root / f"anchor_{int(a):03d}"
            if not pi3.exists():
                raise FileNotFoundError(f"missing pi3 cache: {pi3}")
            if not ster.exists():
                raise FileNotFoundError(f"missing stereo cache: {ster}")
            targets.append((int(a), pi3, ster))

    per_anchor: list[dict] = []
    for anchor_idx, pi3_dir, stereo_dir in targets:
        print(f"\n=== anchor {anchor_idx:03d}: pi3={pi3_dir.name}, stereo={stereo_dir.name} ===",
              flush=True)
        result = eval_one_anchor_checkpointed(
            anchor_idx=anchor_idx,
            pi3_dir=str(pi3_dir),
            stereo_dir=str(stereo_dir),
            erp_h=args.erp_h,
            erp_w=args.erp_w,
            alpha=args.alpha,
            sigma_px=args.sigma_px,
            num_bands=args.num_bands,
            no_ego_mask=args.no_ego_mask,
            mask_mode=args.mask_mode,
            v3_selection=args.v3_selection,
        )
        # colab_direct.checkpointed returns None for cached anchors -> reload from
        # the per-anchor JSON we wrote during the original run.
        per_anchor_json = out_dir / f"anchor_{anchor_idx:03d}.json"
        if result is None:
            if per_anchor_json.exists():
                result = json.loads(per_anchor_json.read_text(encoding="utf-8"))
                print(f"  [checkpoint hit] reloaded {per_anchor_json.name}", flush=True)
            else:
                raise RuntimeError(
                    f"checkpoint marker present but {per_anchor_json} missing; "
                    f"delete {ckpt_dir / f'anchor_{anchor_idx:03d}.done'} and rerun"
                )
        else:
            # Persist fresh result for future checkpoint hits
            per_anchor_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

        result["anchor_idx"] = anchor_idx
        per_anchor.append(result)
        m = result["metrics"]
        print(f"  PSNR (L1_reweight vs L1_baseline) = {m['psnr_l1_reweighted_vs_baseline_dB']:.2f} dB "
              f"(in confidence region: {m['psnr_in_confidence_region_dB']:.2f} dB)", flush=True)

    # Aggregate
    finite_pairwise = [
        r["metrics"]["psnr_l1_reweighted_vs_baseline_dB"] for r in per_anchor
        if np.isfinite(r["metrics"]["psnr_l1_reweighted_vs_baseline_dB"])
    ]
    finite_inconf = [
        r["metrics"]["psnr_in_confidence_region_dB"] for r in per_anchor
        if np.isfinite(r["metrics"]["psnr_in_confidence_region_dB"])
    ]
    agg = {
        "n_anchors": len(per_anchor),
        "mean_psnr_l1_reweighted_vs_baseline_dB": (
            float(np.mean(finite_pairwise)) if finite_pairwise else None
        ),
        "mean_psnr_in_confidence_region_dB": (
            float(np.mean(finite_inconf)) if finite_inconf else None
        ),
    }
    summary = {
        "route": "13 / 新-D Option B cycle eval",
        "alpha": args.alpha,
        "sigma_px": args.sigma_px,
        "mask_mode": args.mask_mode,
        "v3_selection": args.v3_selection if args.mask_mode == "v3" else None,
        "erp_hw": [args.erp_h, args.erp_w],
        "per_anchor": per_anchor,
        "aggregate": agg,
        "interpretation": (
            "psnr_l1_reweighted_vs_baseline_dB = PSNR between plain-L1 and reweighted-L1 "
            "ERPs on the L1-covered region. LOWER value = reweight perturbed more pixels "
            "(= more effect). psnr_in_confidence_region_dB restricts the metric to high-"
            "confidence stereo regions; a meaningfully lower number there (vs the full-"
            "union value) is the expected signature of working, region-targeted reweight. "
            "For absolute quality (vs real GT), pair this with the per-cam hold-out cycle "
            "protocol from scripts/phase2/eval_cycle_consistency.py on the driver's "
            "option_b_l1.png output."
        ),
    }
    (out_dir / "eval_option_b_cycle.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[eval_option_b] aggregate -> {out_dir / 'eval_option_b_cycle.json'}", flush=True)
    print(json.dumps(agg, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
