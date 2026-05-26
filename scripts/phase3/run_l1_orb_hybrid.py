"""
Phase 3 WS2 / 新-B — L1+ORB hybrid driver: L1 sphere stitch WITH per-pair
DISK+LightGlue-based homography prewarp on the overlap regions.

(Despite the "ORB" tag in the name — kept for continuity with the WS2 spec
in the handoff plan — we use DISK+LightGlue learned features from the same
stack as 新-D wide-baseline stereo. ORB-only is a strict subset of this
implementation: setting `--no-prewarp` falls back to plain L1.)

Pipeline (single anchor):
    1. Load 7 ring cams (image + K + T_ego_cam) from EITHER an AV2 log dir
       OR a Pi3 anchor cache (image_*.png + av2_K_*.npy + av2_T_ego_cam_*.npy).
    2. (If --no-prewarp NOT set): for each adjacent pair, fit a 2D homography
       on the overlap region (DISK+LightGlue+RANSAC) and warp cam_b into
       cam_a's image-plane frame.
    3. L1 sphere project each (warped) cam onto the ERP canvas.
    4. Multi-band blend -> final ERP.
    5. Save:
         l1_orb_hybrid.png             — final ERP (whether prewarped or plain L1)
         pair_homography_summary.json  — per-pair status / inliers / residual
                                          (omitted when --no-prewarp)
         summary.json                  — params, runtime, file paths

A/B mode (--no-prewarp): skip step 2 entirely, equivalent to plain L1.
Use this back-to-back with the prewarp run for an apples-to-apples compare.

Usage (single anchor, Pi3 cache):
    python scripts/phase3/run_l1_orb_hybrid.py \\
        --pi3-dir outputs/phase3/pi3_cache/anchor_060 \\
        --output-dir outputs/phase3/p3.X_l1_orb/anchor_060

A/B (plain L1):
    python scripts/phase3/run_l1_orb_hybrid.py \\
        --pi3-dir outputs/phase3/pi3_cache/anchor_060 \\
        --output-dir outputs/phase3/p3.X_l1_orb/anchor_060_noprewarp \\
        --no-prewarp

AV2 log:
    python scripts/phase3/run_l1_orb_hybrid.py \\
        --input-mode av2 --log-dir <AV2_log_root> --anchor 60 \\
        --output-dir outputs/phase3/p3.X_l1_orb/anchor_060_av2
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


# ------------------------- input loaders -------------------------------------


def _load_from_pi3_cache(pi3_dir: Path, cam: str) -> dict:
    return {
        "image": np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB")),
        "K": np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy").astype(np.float64),
        "T_ego_cam": np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy").astype(np.float64),
    }


def _load_from_av2(frame, cam: str) -> dict:
    img = frame.images[cam]
    calib = frame.calibrations[cam]
    return {"image": img, "K": calib.K.astype(np.float64),
            "T_ego_cam": calib.T_ego_cam.astype(np.float64)}


# ------------------------- main ---------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--input-mode", choices=["av2", "pi3-cache", "auto"], default="auto",
                    help="auto: pick pi3-cache if --pi3-dir given else av2.")
    ap.add_argument("--log-dir", type=Path, default=None,
                    help="(av2 mode) AV2 sensor log root.")
    ap.add_argument("--pi3-dir", type=Path, default=None,
                    help="(pi3-cache mode) Pi3 anchor_NNN/ dir with image_*.png + av2_*_npy.")
    ap.add_argument("--anchor", type=int, default=60,
                    help="(av2 mode) which anchor index to render. Used as the anchor key "
                         "in the output summary; ignored in pi3-cache mode (parsed from dirname).")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--no-prewarp", action="store_true",
                    help="A/B flag: skip overlap-homography prewarp, equivalent to plain L1.")
    ap.add_argument("--no-ego-mask", action="store_true",
                    help="Disable heuristic AV2 ego mask (WS1.2). Use for parity with older runs.")
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu",
                    help="Device for DISK+LightGlue. CPU works (slower); Colab should use cuda.")
    ap.add_argument("--max-num-keypoints", type=int, default=2048,
                    help="DISK max keypoints per image (per pair).")
    ap.add_argument("--lightglue-min-confidence", type=float, default=0.2,
                    help="Drop LightGlue matches below this score before RANSAC.")
    ap.add_argument("--ransac-threshold-px", type=float, default=3.0,
                    help="cv2.findHomography RANSAC inlier threshold.")
    ap.add_argument("--max-residual-px", type=float, default=2.0,
                    help="Fallback to identity if median inlier residual exceeds this.")
    ap.add_argument("--reference-cam", default="ring_front_center",
                    help="Global anchor cam for the chain warp (every other cam is warped "
                         "into this cam's image-plane frame). Default = ring_front_center.")
    ap.add_argument("--warp-model", choices=["homography", "similarity", "rotation_only"],
                    default="rotation_only",
                    help="Warp model used for pair fitting. 'homography' = v1 full perspective "
                         "(8 DOF, drifts after 3-hop chain); 'similarity' = 4 DOF (rotation+scale+"
                         "translation, bounded under chain compose); 'rotation_only' (default) = "
                         "3 DOF rigid 3D rotation via Procrustes on backprojected rays, requires K.")
    ap.add_argument("--min-warp-coverage-frac", type=float, default=0.10,
                    help="Safety valve: if post-warp non-zero pixel coverage falls below this "
                         "fraction (e.g. <50% of canvas still covered), fall back to identity. "
                         "This catches the v1 NEG mode where rear-cam 3-hop perspective chain "
                         "pushed all image content off-canvas, producing all-black slabs.")
    ap.add_argument("--w2p-code", default=None,
                    help="Path to the `code/` dir holding the waymo2panorama package.")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    # Imports happen AFTER sys.path wiring.
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7
    from waymo2panorama.data_io.ego_mask import build_ego_masks
    from waymo2panorama.pipeline.stitch_frame import stitch_one_frame_with_prewarp
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    # Resolve input mode.
    mode = args.input_mode
    if mode == "auto":
        if args.pi3_dir is not None:
            mode = "pi3-cache"
        elif args.log_dir is not None:
            mode = "av2"
        else:
            ap.error("provide either --pi3-dir or --log-dir (or set --input-mode explicitly)")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    cams = list(RING_CAMS_7)

    print(f"[l1-orb] mode={mode}, erp_hw={erp_hw}, prewarp={not args.no_prewarp}, "
          f"device={args.device}", flush=True)

    # ---- gather per-cam data ----
    per_cam: dict[str, dict] = {}
    anchor_idx = args.anchor
    anchor_ts_ns: int | None = None

    if mode == "pi3-cache":
        if args.pi3_dir is None:
            ap.error("--pi3-dir required for pi3-cache mode")
        pi3_dir = Path(args.pi3_dir)
        if not pi3_dir.exists():
            raise FileNotFoundError(pi3_dir)
        try:
            anchor_idx = int(pi3_dir.name.split("_")[1])
        except (IndexError, ValueError):
            pass
        summary_path = pi3_dir / "summary.json"
        if summary_path.exists():
            try:
                s = json.loads(summary_path.read_text(encoding="utf-8"))
                anchor_ts_ns = s.get("anchor_timestamp_ns")
            except Exception:
                anchor_ts_ns = None
        for cam in cams:
            per_cam[cam] = _load_from_pi3_cache(pi3_dir, cam)
    else:
        if args.log_dir is None:
            ap.error("--log-dir required for av2 mode")
        from waymo2panorama.data_io.av2_loader import AV2RingLoader  # noqa: E402
        loader = AV2RingLoader(args.log_dir)
        ts_all = loader.anchor_timestamps_ns()
        if not (0 <= anchor_idx < len(ts_all)):
            raise IndexError(f"--anchor {anchor_idx} out of range [0, {len(ts_all)})")
        anchor_ts_ns = ts_all[anchor_idx]
        frame = loader.load_synced_frame(anchor_ts_ns)
        for cam in cams:
            per_cam[cam] = _load_from_av2(frame, cam)

    # ---- ego masks ----
    cam_image_shapes = {cam: per_cam[cam]["image"].shape[:2] for cam in cams}
    ego_masks = build_ego_masks(cams, cam_image_shapes, enabled=not args.no_ego_mask)

    # ---- run the stitcher ----
    t_stitch0 = time.time()
    if args.no_prewarp:
        # Plain L1 — replicate `stitch_one_frame` inline using the per_cam dict
        # (we don't have a FrameSample because pi3-cache mode doesn't build one).
        slabs: list[np.ndarray] = []
        weights: list[np.ndarray] = []
        for cam in cams:
            d = per_cam[cam]
            rgb, _alpha, w = render_camera_to_erp(
                image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
                erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
            )
            slabs.append(rgb)
            weights.append(w)
        erp = multiband_blend(slabs, weights, num_bands=args.num_bands, wrap=True)
        prewarp_summary = None
    else:
        erp, prewarp_summary = stitch_one_frame_with_prewarp(
            per_cam=per_cam,
            erp_hw=erp_hw,
            num_bands=args.num_bands,
            ego_masks=ego_masks,
            wrap=True,
            device=args.device,
            homography_kwargs={
                "max_num_keypoints": args.max_num_keypoints,
                "lightglue_min_confidence": args.lightglue_min_confidence,
                "ransac_threshold_px": args.ransac_threshold_px,
                "max_residual_px": args.max_residual_px,
            },
            reference_cam=args.reference_cam,
            return_diagnostics=True,
            warp_model=args.warp_model,
            min_warp_coverage_frac=args.min_warp_coverage_frac,
        )
    t_stitch_s = time.time() - t_stitch0

    # ---- save ----
    out_png = out_dir / "l1_orb_hybrid.png"
    Image.fromarray(erp).save(out_png)
    print(f"[l1-orb] wrote {out_png}", flush=True)

    if prewarp_summary is not None:
        (out_dir / "pair_homography_summary.json").write_text(
            json.dumps(prewarp_summary, indent=2), encoding="utf-8"
        )
        ok = prewarp_summary["n_pairs_ok"]
        tot = prewarp_summary["n_pairs_total"]
        print(f"[l1-orb] pair homographies: {ok}/{tot} ok "
              f"(reference_cam={prewarp_summary.get('reference_cam')})", flush=True)
        for key, p in prewarp_summary["pair_homographies"].items():
            res_str = "n/a" if p["residual_px"] is None else f"{p['residual_px']:.2f}px"
            print(f"  {key}: status={p['status']:<14s} "
                  f"matches={p['match_count']:>4d} inliers={p['inlier_count']:>4d} "
                  f"residual={res_str}", flush=True)
        if ok == 0 and tot > 0:
            print("[l1-orb] WARNING: 0/N pairs ok -> chain warp degenerates to identity. "
                  "Output ERP equals plain L1 baseline.", flush=True)
        # Chain-warp diagnostics.
        chain = prewarp_summary.get("chain_warps", {})
        for cam, info in chain.items():
            tag = "identity" if info["is_identity"] else "warped"
            print(f"  chain[{cam:<22s}]: n_hops={info['n_hops']} -> {tag}", flush=True)

    summary = {
        "route": "WS2 / 新-B (L1+ORB hybrid)",
        "mode": mode,
        "anchor_idx": anchor_idx,
        "anchor_timestamp_ns": anchor_ts_ns,
        "erp_hw": list(erp_hw),
        "params": {
            "num_bands": args.num_bands,
            "no_prewarp": bool(args.no_prewarp),
            "no_ego_mask": bool(args.no_ego_mask),
            "device": args.device,
            "max_num_keypoints": args.max_num_keypoints,
            "lightglue_min_confidence": args.lightglue_min_confidence,
            "ransac_threshold_px": args.ransac_threshold_px,
            "max_residual_px": args.max_residual_px,
            "reference_cam": args.reference_cam,
            "ego_mask_cams": sorted(c for c, m in ego_masks.items() if m is not None),
        },
        "outputs": {
            "l1_orb_hybrid": str(out_png.resolve()),
            "pair_homography_summary": (
                str((out_dir / "pair_homography_summary.json").resolve())
                if prewarp_summary is not None else None
            ),
        },
        "runtime_s": {"stitch": round(t_stitch_s, 2)},
        "prewarp_summary": prewarp_summary,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[l1-orb] done in {t_stitch_s:.2f}s -> {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
