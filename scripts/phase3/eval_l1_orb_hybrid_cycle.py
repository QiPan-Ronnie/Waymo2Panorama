"""
Phase 3 WS2 / 新-B — L1+ORB hybrid cycle-PSNR eval (L1 baseline vs L1+prewarp).

Like `eval_option_b_cycle.py`, this measures *inter-method* PSNR: we render
the SAME 7 ring cams twice into ERP — once with plain L1 (no prewarp),
once with the per-pair overlap-homography prewarp pipeline (DISK+LightGlue
-> cv2.findHomography -> cv2.warpPerspective). We then compare the two
final ERPs on the union of L1 coverage. We report:

  - per-anchor psnr_l1_prewarp_vs_baseline_dB
      (PSNR between the two ERPs on the L1-covered region; LOWER means the
       prewarp moved more pixels — bigger effect.)
  - per-anchor psnr_in_overlap_region_dB
      (same PSNR, restricted to the overlap region between adjacent ring
       cams — i.e. where the prewarp is supposed to do its work. This
       SHOULD be lower than the union value if prewarp is actually helping.)
  - per-anchor mae_change_on_union
      (mean abs RGB delta in [0, 255] units — interpretable magnitude check)
  - per-anchor pair_homography_stats
      (per-adjacent-pair status / inlier counts / residuals — the diagnostic
       signal we use to debug bad fits.)
  - aggregate (mean) across anchors

This intentionally does NOT replicate the per-cam held-out cycle-PSNR
protocol of `scripts/phase2/eval_cycle_consistency.py` (which produces the
"L1 = 12.34 dB" headline). Doing so here would duplicate ~150 LOC. Instead,
the controller pairs this eval with a separate run of `eval_cycle_consistency.py`
on the driver's output (l1_orb_hybrid.png) to obtain the *absolute* PSNR
numbers and the delta vs the baseline 12.34 dB.

Per-anchor checkpointing (optional): wraps the anchor loop in
`colab_direct.checkpointed` if that module is available; otherwise the
checkpointing is a no-op decorator. This lets long runs survive a Colab
kernel restart without re-doing finished anchors.

Usage:
    python scripts/phase3/eval_l1_orb_hybrid_cycle.py \\
        --pi3-cache-root outputs/phase3/pi3_cache \\
        --anchors 0 60 90 150 \\
        --output-dir outputs/phase3/p3.X_l1_orb/eval_cycle \\
        --device cuda

Single-anchor:
    python scripts/phase3/eval_l1_orb_hybrid_cycle.py \\
        --pi3-dir outputs/phase3/pi3_cache/anchor_060 \\
        --output-dir outputs/phase3/p3.X_l1_orb/eval_cycle_one \\
        --device cuda
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
# Optional Colab-direct checkpointing — same pattern as eval_option_b_cycle.py
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
# PSNR / MAE helpers (matches eval_option_b_cycle.py)
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


def _build_overlap_mask(alphas: list[np.ndarray]) -> np.ndarray:
    """Pixels covered by >=2 cams = the overlap region where prewarp matters.

    Returns a 2D bool array same shape as each alpha.
    """
    if not alphas:
        return np.zeros((0, 0), dtype=bool)
    count = np.zeros(alphas[0].shape, dtype=np.int32)
    for a in alphas:
        count += a.astype(bool).astype(np.int32)
    return count >= 2


# ---------------------------------------------------------------------------
# Single-anchor evaluator
# ---------------------------------------------------------------------------


def _eval_one_anchor(
    pi3_dir: Path,
    erp_hw: tuple[int, int],
    num_bands: int,
    no_ego_mask: bool,
    device: str,
    max_num_keypoints: int,
    lightglue_min_confidence: float,
    ransac_threshold_px: float,
    max_residual_px: float,
) -> dict:
    """Run both plain-L1 baseline and L1+prewarp blends, compute cycle PSNR
    (L1+prewarp vs L1) on union of L1 coverage AND on the multi-cam overlap
    region. Returns a per-anchor summary dict.

    Important: this is NOT the held-out per-cam cycle protocol of
    eval_cycle_consistency.py. Here we have two ERP outputs from the same
    cam set and we want to know how much the prewarp moves them. Plain L1
    is the reference. PSNR(L1+prewarp, L1) tells us how much the prewarp
    perturbs the final pixels:
        - very high (>40 dB) on the overlap region == prewarp had ~no effect
          (homographies fell back to identity or were tiny).
        - moderate (~25-35 dB) on the overlap region == meaningful shift.
        - very low (<20 dB) on union == aggressive warp; likely degrading
          non-overlap content too.
    To get the SIGN of the change relative to a real GT, run the per-cam
    hold-out protocol separately on the driver's l1_orb_hybrid.png output.
    """
    from waymo2panorama.alignment.pair_homography import compute_overlap_homography
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.pipeline.stitch_frame import (
        ADJACENT_PAIRS_RING,
        _prewarp_one_cam,
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

    # =========================================================================
    # Baseline blend (plain L1, no prewarp) — using ORIGINAL per-cam images.
    # =========================================================================
    slabs_base: list[np.ndarray] = []
    weights_base: list[np.ndarray] = []
    alphas: list[np.ndarray] = []
    t_proj0 = time.time()
    for cam in cams:
        d = per_cam[cam]
        rgb, alpha_map, w = render_camera_to_erp(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        slabs_base.append(rgb)
        weights_base.append(w)
        alphas.append(alpha_map)
    t_proj_base_s = time.time() - t_proj0

    t_blend0 = time.time()
    erp_baseline = multiband_blend(slabs_base, weights_base, num_bands=num_bands, wrap=True)
    t_blend_base_s = time.time() - t_blend0

    # =========================================================================
    # Prewarp blend (L1+ORB hybrid): per-pair compute_overlap_homography, then
    # warp non-anchor cams, then render+blend. Mirrors stitch_one_frame_with_
    # prewarp's logic but exposes the intermediate stats (per-pair status).
    # =========================================================================
    pair_results: dict[tuple[str, str], dict] = {}
    t_pair0 = time.time()
    for cam_a, cam_b in ADJACENT_PAIRS_RING:
        if cam_a not in per_cam or cam_b not in per_cam:
            continue
        res = compute_overlap_homography(
            img_a=per_cam[cam_a]["image"], img_b=per_cam[cam_b]["image"],
            K_a=per_cam[cam_a].get("K"), K_b=per_cam[cam_b].get("K"),
            T_ego_a=per_cam[cam_a].get("T_ego_cam"),
            T_ego_b=per_cam[cam_b].get("T_ego_cam"),
            device=device,
            max_num_keypoints=max_num_keypoints,
            lightglue_min_confidence=lightglue_min_confidence,
            ransac_threshold_px=ransac_threshold_px,
            max_residual_px=max_residual_px,
        )
        pair_results[(cam_a, cam_b)] = res
    t_pair_s = time.time() - t_pair0

    # Warp each cam_b into cam_a's frame; cam not appearing as 'b' stays as-is.
    warped_per_cam: dict[str, np.ndarray] = {}
    cam_to_pair_as_b: dict[str, tuple[str, str]] = {b: (a, b) for a, b in pair_results.keys()}
    for cam, data in per_cam.items():
        pair_key = cam_to_pair_as_b.get(cam)
        if pair_key is None:
            warped_per_cam[cam] = data["image"].copy()
            continue
        res = pair_results[pair_key]
        cam_a_for_warp = pair_key[0]
        img_a_ref = per_cam[cam_a_for_warp]["image"]
        warped_per_cam[cam] = _prewarp_one_cam(img_a_ref, data["image"], res["H"])

    # Project warped cams through their OWN K/T_ego_cam (v1 simplification —
    # see stitch_frame.py module docstring). Use the SAME ego_masks as baseline.
    slabs_pw: list[np.ndarray] = []
    weights_pw: list[np.ndarray] = []
    alphas_pw: list[np.ndarray] = []
    t_proj_pw0 = time.time()
    for cam in cams:
        d = per_cam[cam]
        rgb, alpha_map, w = render_camera_to_erp(
            image=warped_per_cam[cam], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
        )
        slabs_pw.append(rgb)
        weights_pw.append(w)
        alphas_pw.append(alpha_map)
    t_proj_pw_s = time.time() - t_proj_pw0

    t_blend_pw0 = time.time()
    erp_prewarp = multiband_blend(slabs_pw, weights_pw, num_bands=num_bands, wrap=True)
    t_blend_pw_s = time.time() - t_blend_pw0

    # =========================================================================
    # PSNR metrics. Use the UNION of baseline alphas as the scoring region
    # (so a warped cam that suddenly covers more or fewer canvas pixels does
    # not bias the metric). The overlap region we use the BASELINE overlap so
    # both methods are scored on the same pixels.
    # =========================================================================
    union_alpha = np.zeros(erp_hw, dtype=bool)
    for a in alphas:
        union_alpha |= a
    overlap_mask = _build_overlap_mask(alphas)
    overlap_in_union = overlap_mask & union_alpha

    psnr_pw_vs_baseline = _psnr_masked(erp_prewarp, erp_baseline, union_alpha)
    psnr_in_overlap = _psnr_masked(erp_prewarp, erp_baseline, overlap_in_union)

    if union_alpha.sum() > 0:
        a_d = erp_baseline.astype(np.float64)
        b_d = erp_prewarp.astype(np.float64)
        mae_change = float(np.abs(a_d - b_d)[union_alpha].mean())
    else:
        mae_change = float("nan")

    # Per-pair stats (json-serializable) — same shape as run_l1_orb_hybrid.py
    pair_log: dict[str, dict] = {}
    for (cam_a, cam_b), res in pair_results.items():
        key = f"{cam_a}__to__{cam_b}"
        pair_log[key] = {
            "cam_a": cam_a,
            "cam_b": cam_b,
            "inlier_count": int(res["inlier_count"]),
            "residual_px": float(res["residual_px"]) if np.isfinite(res["residual_px"]) else None,
            "match_count": int(res["match_count"]),
            "status": str(res["status"]),
            "time_s": float(res.get("time_s", 0.0)),
        }
    n_ok = int(sum(1 for r in pair_results.values() if r["status"] == "ok"))
    n_total = len(pair_results)

    return {
        "pi3_dir": str(pi3_dir),
        "erp_hw": list(erp_hw),
        "params": {
            "num_bands": num_bands,
            "no_ego_mask": no_ego_mask,
            "device": device,
            "max_num_keypoints": max_num_keypoints,
            "lightglue_min_confidence": lightglue_min_confidence,
            "ransac_threshold_px": ransac_threshold_px,
            "max_residual_px": max_residual_px,
        },
        "metrics": {
            # Main signal: PSNR between plain-L1 and prewarp-L1 on L1-covered region.
            # LOWER value = prewarp perturbed more pixels (= more effect).
            "psnr_l1_prewarp_vs_baseline_dB": psnr_pw_vs_baseline,
            # Same metric restricted to the multi-cam overlap region:
            # this is where prewarp is supposed to act. A meaningfully LOWER
            # number here (vs the full-union value) is the "prewarp targeted
            # the right pixels" signature.
            "psnr_in_overlap_region_dB": psnr_in_overlap,
            # Mean RGB shift on union (0..255 scale).
            "mae_change_on_union": mae_change,
        },
        "pair_homography_stats": {
            "n_pairs_ok": n_ok,
            "n_pairs_total": n_total,
            "pairs": pair_log,
        },
        "overlap_frac": float(overlap_mask.mean()),
        "union_alpha_frac": float(union_alpha.mean()),
        "runtime_s": {
            "projection_baseline": round(t_proj_base_s, 3),
            "projection_prewarp": round(t_proj_pw_s, 3),
            "pair_homographies": round(t_pair_s, 3),
            "blend_baseline": round(t_blend_base_s, 3),
            "blend_prewarp": round(t_blend_pw_s, 3),
        },
    }


# ---------------------------------------------------------------------------
# Per-anchor checkpoint wrapper — primitive-arg signature so colab_direct
# can serialize / hash the args reliably.
# ---------------------------------------------------------------------------


def _eval_one_anchor_for_checkpoint(
    anchor_idx: int,
    pi3_dir: str,
    erp_h: int,
    erp_w: int,
    num_bands: int,
    no_ego_mask: bool,
    device: str,
    max_num_keypoints: int,
    lightglue_min_confidence: float,
    ransac_threshold_px: float,
    max_residual_px: float,
) -> dict:
    """Primitive-arg wrapper so colab_direct.checkpointed can serialize args."""
    return _eval_one_anchor(
        pi3_dir=Path(pi3_dir),
        erp_hw=(erp_h, erp_w),
        num_bands=num_bands,
        no_ego_mask=no_ego_mask,
        device=device,
        max_num_keypoints=max_num_keypoints,
        lightglue_min_confidence=lightglue_min_confidence,
        ransac_threshold_px=ransac_threshold_px,
        max_residual_px=max_residual_px,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    # Multi-anchor sweep
    ap.add_argument("--pi3-cache-root", type=Path, default=None,
                    help="Root containing anchor_NNN/ subdirs (e.g. outputs/phase3/pi3_cache).")
    ap.add_argument("--anchors", nargs="+", type=int, default=None,
                    help="Anchor indices to eval (e.g. 0 30 60 90 150).")
    # Single-anchor mode
    ap.add_argument("--pi3-dir", type=Path, default=None,
                    help="Pi3 anchor cache dir (image_*.png + av2_*.npy).")
    ap.add_argument("--output-dir", type=Path, required=True)
    # Hyperparams
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--no-ego-mask", action="store_true")
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu",
                    help="Device for DISK+LightGlue. CPU works (slower); Colab should use cuda.")
    ap.add_argument("--max-num-keypoints", type=int, default=2048)
    ap.add_argument("--lightglue-min-confidence", type=float, default=0.2)
    ap.add_argument("--ransac-threshold-px", type=float, default=3.0)
    ap.add_argument("--max-residual-px", type=float, default=2.0)
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
    print(f"[eval_l1_orb] colab_direct checkpointing: "
          f"{'ON (dir=' + str(ckpt_dir) + ')' if _HAS_CD else 'OFF (no-op)'}",
          flush=True)

    eval_one_anchor_checkpointed = _make_checkpointed(ckpt_dir)(_eval_one_anchor_for_checkpoint)

    # Resolve targets
    targets: list[tuple[int, Path]] = []
    if args.pi3_dir is not None:
        # Single-anchor mode
        try:
            anchor_idx = int(args.pi3_dir.name.split("_")[1])
        except (IndexError, ValueError):
            anchor_idx = -1
        targets.append((anchor_idx, args.pi3_dir))
    else:
        if args.pi3_cache_root is None or args.anchors is None:
            ap.error("multi-anchor mode requires --pi3-cache-root + --anchors")
        for a in args.anchors:
            pi3 = args.pi3_cache_root / f"anchor_{int(a):03d}"
            if not pi3.exists():
                raise FileNotFoundError(f"missing pi3 cache: {pi3}")
            targets.append((int(a), pi3))

    per_anchor: list[dict] = []
    for anchor_idx, pi3_dir in targets:
        print(f"\n=== anchor {anchor_idx:03d}: pi3={pi3_dir.name} ===", flush=True)
        result = eval_one_anchor_checkpointed(
            anchor_idx=anchor_idx,
            pi3_dir=str(pi3_dir),
            erp_h=args.erp_h,
            erp_w=args.erp_w,
            num_bands=args.num_bands,
            no_ego_mask=args.no_ego_mask,
            device=args.device,
            max_num_keypoints=args.max_num_keypoints,
            lightglue_min_confidence=args.lightglue_min_confidence,
            ransac_threshold_px=args.ransac_threshold_px,
            max_residual_px=args.max_residual_px,
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
        ph = result["pair_homography_stats"]
        print(f"  PSNR (L1+prewarp vs L1) = {m['psnr_l1_prewarp_vs_baseline_dB']:.2f} dB "
              f"(in overlap region: {m['psnr_in_overlap_region_dB']:.2f} dB)", flush=True)
        print(f"  pair homographies ok: {ph['n_pairs_ok']}/{ph['n_pairs_total']}", flush=True)

    # Aggregate
    finite_union = [
        r["metrics"]["psnr_l1_prewarp_vs_baseline_dB"] for r in per_anchor
        if np.isfinite(r["metrics"]["psnr_l1_prewarp_vs_baseline_dB"])
    ]
    finite_overlap = [
        r["metrics"]["psnr_in_overlap_region_dB"] for r in per_anchor
        if np.isfinite(r["metrics"]["psnr_in_overlap_region_dB"])
    ]
    finite_mae = [
        r["metrics"]["mae_change_on_union"] for r in per_anchor
        if np.isfinite(r["metrics"]["mae_change_on_union"])
    ]
    total_ok = sum(r["pair_homography_stats"]["n_pairs_ok"] for r in per_anchor)
    total_pairs = sum(r["pair_homography_stats"]["n_pairs_total"] for r in per_anchor)
    agg = {
        "n_anchors": len(per_anchor),
        "mean_psnr_l1_prewarp_vs_baseline_dB": (
            float(np.mean(finite_union)) if finite_union else None
        ),
        "mean_psnr_in_overlap_region_dB": (
            float(np.mean(finite_overlap)) if finite_overlap else None
        ),
        "mean_mae_change_on_union": (
            float(np.mean(finite_mae)) if finite_mae else None
        ),
        "fraction_pair_homographies_ok": (
            float(total_ok) / float(total_pairs) if total_pairs > 0 else None
        ),
    }
    summary = {
        "route": "WS2 / 新-B L1+ORB hybrid cycle eval",
        "erp_hw": [args.erp_h, args.erp_w],
        "per_anchor": per_anchor,
        "aggregate": agg,
        "interpretation": (
            "psnr_l1_prewarp_vs_baseline_dB = PSNR between plain-L1 and L1+prewarp ERPs "
            "on the L1-covered region. LOWER value = prewarp perturbed more pixels "
            "(= more effect). psnr_in_overlap_region_dB restricts the metric to pixels "
            "where >=2 cams contribute; a meaningfully lower number there (vs the full-"
            "union value) is the expected signature of working, overlap-targeted prewarp. "
            "fraction_pair_homographies_ok is the per-pair RANSAC success rate; a low "
            "rate means most adjacent overlaps fell back to identity (no warp applied). "
            "For absolute quality (vs real GT), pair this with the per-cam hold-out cycle "
            "protocol from scripts/phase2/eval_cycle_consistency.py on the driver's "
            "l1_orb_hybrid.png output."
        ),
    }
    (out_dir / "eval_l1_orb_hybrid_cycle.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[eval_l1_orb] aggregate -> {out_dir / 'eval_l1_orb_hybrid_cycle.json'}", flush=True)
    print(json.dumps(agg, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
