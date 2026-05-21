"""
Phase 3 route 11 / 新-B — Graph-cut optimal seam selection.

Loads 7 ring-cam images (from a Pi3 cache anchor_NNN/ dir or an AV2 log),
renders each onto an ERP via the existing sphere projection (cos^2 weights),
then *replaces* those weights with hard 0/1 (lightly feathered) per-cam masks
chosen by min-cut over a (color + gradient + boundary) energy. Pipes the
new weights into the existing `multiband_blend` and saves:

    sphere_l1_baseline.png            (cos^2 weights -> multiband)
    graphcut_seam.png                 (graph-cut weights -> multiband)
    compare_l1_vs_graphcut.png        (top: L1, bottom: graphcut, with seam overlay)
    seams_overlay_graphcut.png        (graphcut ERP with seam lines highlighted in red)
    seams_overlay_l1.png              (L1 baseline ERP with seam lines, for reference)
    seam_log.json                     per-pair cut statistics + cycle-PSNR if available

CLI (Pi3 cache mode is fast & self-contained):
    python scripts/phase3/run_graphcut_seam.py \
        --pi3-dir outputs/phase3/pi3_cache/anchor_060 \
        --output-dir outputs/phase3/p3.5_graphcut/anchor_060

Or with the full AV2 loader:
    python scripts/phase3/run_graphcut_seam.py \
        --log-dir <AV2_log_root> --anchor 60 \
        --output-dir outputs/phase3/p3.5_graphcut/anchor_060
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_W2P_CODE_REL = "../../code"

RING_CAMS_7 = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _erp_col_from_T_ego_cam(T_ego_cam: np.ndarray, W_erp: int) -> float:
    R = T_ego_cam[:3, :3]
    axis_ego = R @ np.array([0.0, 0.0, 1.0])
    x, y = float(axis_ego[0]), float(axis_ego[1])
    theta = np.arctan2(y, x)
    u = ((np.pi - theta) / (2.0 * np.pi)) * W_erp - 0.5
    return float(u % W_erp)


def _load_from_pi3_cache(pi3_dir: Path, cam: str) -> dict:
    image = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
    K = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
    T_ego_cam = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
    return {"image": image, "K": K, "T_ego_cam": T_ego_cam}


def _load_from_av2(loader, frame, cam: str) -> dict:
    img = frame.images[cam]
    calib = frame.calibrations[cam]
    return {"image": img, "K": calib.K, "T_ego_cam": calib.T_ego_cam}


def _make_label_panel(top_erp: np.ndarray, bot_erp: np.ndarray,
                      top_caption: str, bot_caption: str) -> np.ndarray:
    H, W = top_erp.shape[:2]
    label_h = 36
    sep_h = 4
    sep = np.full((sep_h, W, 3), 32, dtype=np.uint8)
    cap_top = np.full((label_h, W, 3), 16, dtype=np.uint8)
    cap_mid = np.full((label_h, W, 3), 16, dtype=np.uint8)
    panel = np.concatenate([cap_top, top_erp, sep, cap_mid, bot_erp], axis=0)
    try:
        pim = Image.fromarray(panel)
        draw = ImageDraw.Draw(pim)
        try:
            font = ImageFont.truetype("arial.ttf", 24)
        except OSError:
            font = ImageFont.load_default()
        draw.text((12, 6), top_caption, fill=(255, 255, 0), font=font)
        mid_y = label_h + H + sep_h + 6
        draw.text((12, mid_y), bot_caption, fill=(0, 255, 255), font=font)
        return np.asarray(pim)
    except Exception:
        return panel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--input-mode", choices=["av2", "pi3-cache", "auto"], default="auto")
    ap.add_argument("--log-dir", type=Path, default=None)
    ap.add_argument("--pi3-dir", type=Path, default=None)
    ap.add_argument("--anchor", type=int, default=60)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--no-wrap", action="store_true")
    ap.add_argument("--feather-sigma", type=float, default=3.0)
    ap.add_argument("--alpha-w", type=float, default=1.0, help="color term weight")
    ap.add_argument("--beta-w", type=float, default=0.5, help="gradient term weight")
    ap.add_argument("--gamma-w", type=float, default=0.1, help="boundary term weight")
    ap.add_argument("--min-overlap-px", type=int, default=1024)
    ap.add_argument("--also-make-compare-png", type=Path, default=None,
                    help="Write the L1-vs-graphcut compare panel to an extra absolute path.")
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.blending.graphcut_seam import (  # noqa: E402
        apply_graphcut_seams, draw_seam_overlay_on_erp,
    )
    from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402

    mode = args.input_mode
    if mode == "auto":
        if args.pi3_dir is not None:
            mode = "pi3-cache"
        elif args.log_dir is not None:
            mode = "av2"
        else:
            ap.error("provide --pi3-dir or --log-dir")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)
    print(f"[graphcut] mode={mode}, erp_hw={erp_hw}, feather_sigma={args.feather_sigma}",
          flush=True)

    per_cam_data: dict[str, dict] = {}
    anchor_idx = args.anchor
    anchor_ts_ns: int | None = None
    if mode == "pi3-cache":
        if args.pi3_dir is None:
            ap.error("--pi3-dir required")
        pi3_dir = Path(args.pi3_dir)
        if not pi3_dir.exists():
            raise FileNotFoundError(pi3_dir)
        try:
            anchor_idx = int(pi3_dir.name.split("_")[1])
        except (IndexError, ValueError):
            pass
        summary_path = pi3_dir / "summary.json"
        if summary_path.exists():
            anchor_ts_ns = json.loads(summary_path.read_text()).get("anchor_timestamp_ns")
        for cam in RING_CAMS_7:
            per_cam_data[cam] = _load_from_pi3_cache(pi3_dir, cam)
    else:
        if args.log_dir is None:
            ap.error("--log-dir required")
        from waymo2panorama.data_io.av2_loader import AV2RingLoader  # noqa: E402
        loader = AV2RingLoader(args.log_dir)
        ts_all = loader.anchor_timestamps_ns()
        if not (0 <= anchor_idx < len(ts_all)):
            raise IndexError(f"--anchor {anchor_idx} out of range")
        anchor_ts_ns = ts_all[anchor_idx]
        frame = loader.load_synced_frame(anchor_ts_ns)
        for cam in RING_CAMS_7:
            per_cam_data[cam] = _load_from_av2(loader, frame, cam)

    # ---- project each cam onto the sphere ERP (gets cos^2 weights) ----
    slabs: list[np.ndarray] = []
    alphas: list[np.ndarray] = []
    cos2_weights: list[np.ndarray] = []
    cam_axes_erp: list[float] = []

    t_proj0 = time.time()
    per_cam_log = []
    for cam in RING_CAMS_7:
        d = per_cam_data[cam]
        rgb, alpha, w = render_camera_to_erp(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"], erp_hw=erp_hw,
        )
        slabs.append(rgb)
        alphas.append(alpha)
        cos2_weights.append(w)
        cam_axes_erp.append(_erp_col_from_T_ego_cam(d["T_ego_cam"], args.erp_w))
        per_cam_log.append({
            "cam": cam,
            "alpha_px": int(alpha.sum()),
            "cam_axis_erp_u": round(cam_axes_erp[-1], 1),
        })
        print(f"[graphcut]   {cam}: alpha={int(alpha.sum())} axis_u={cam_axes_erp[-1]:.1f}",
              flush=True)
    t_proj_s = time.time() - t_proj0

    # ---- L1 baseline (cos^2 weights -> multiband) ----
    t_l1_0 = time.time()
    erp_l1 = multiband_blend(slabs, cos2_weights, num_bands=args.num_bands,
                             wrap=not args.no_wrap)
    t_l1_s = time.time() - t_l1_0

    # ---- Graph-cut weights ----
    t_gc0 = time.time()
    seam_log: list[dict] = []
    gc_weights = apply_graphcut_seams(
        slabs, alphas, cos2_weights, cam_axes_erp,
        feather_sigma=args.feather_sigma,
        energy_weights=(args.alpha_w, args.beta_w, args.gamma_w),
        min_overlap_px=args.min_overlap_px,
        seam_log=seam_log,
    )
    erp_gc = multiband_blend(slabs, gc_weights, num_bands=args.num_bands,
                             wrap=not args.no_wrap)
    t_gc_s = time.time() - t_gc0

    # ---- Save raw ERPs ----
    Image.fromarray(erp_l1).save(out_dir / "sphere_l1_baseline.png")
    Image.fromarray(erp_gc).save(out_dir / "graphcut_seam.png")

    # ---- Seam overlays: show where the dominant-cam boundaries fall ----
    erp_l1_seams = draw_seam_overlay_on_erp(erp_l1, cos2_weights, color=(255, 64, 64), thickness=2)
    erp_gc_seams = draw_seam_overlay_on_erp(erp_gc, gc_weights, color=(255, 64, 64), thickness=2)
    Image.fromarray(erp_l1_seams).save(out_dir / "seams_overlay_l1.png")
    Image.fromarray(erp_gc_seams).save(out_dir / "seams_overlay_graphcut.png")

    # ---- Vertical compare panel (the paper figure) ----
    panel = _make_label_panel(
        erp_l1_seams, erp_gc_seams,
        f"L1: Sphere + cos^2 weights (fixed-midline seams, red)  anchor={anchor_idx}",
        "Route 11 / new-B: Graph-cut seams over (color + grad + boundary) energy (red)",
    )
    Image.fromarray(panel).save(out_dir / "compare_l1_vs_graphcut.png")
    if args.also_make_compare_png is not None:
        args.also_make_compare_png.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(panel).save(args.also_make_compare_png)
        print(f"[graphcut] wrote compare PNG -> {args.also_make_compare_png}", flush=True)

    # ---- Quick metrics: L1 vs graphcut PSNR and seam-band gradient energy ----
    # Cycle-PSNR Δ via full cycle eval is in run_graphcut_cycle.py; here we report:
    #   1) L1 vs graphcut PSNR (high = mostly identical image; difference is at seams)
    #   2) Seam-band mean gradient: take pixels within seam_band_px of the dominant-cam
    #      argmax boundary, average |Sobel|. Lower = smoother (less visible) seam.
    import cv2 as _cv2  # noqa: PLC0415
    a_l1 = erp_l1.astype(np.float32)
    a_gc = erp_gc.astype(np.float32)
    mse_l1_gc = float(((a_l1 - a_gc) ** 2).mean())
    psnr_l1_gc = 99.0 if mse_l1_gc < 1e-9 else float(20.0 * np.log10(255.0) - 10.0 * np.log10(mse_l1_gc))

    def _seam_band(weights: list[np.ndarray], dilate_px: int = 8) -> np.ndarray:
        stack = np.stack(weights, axis=0)
        argmax_id = stack.argmax(axis=0).astype(np.int32)
        cover = stack.max(axis=0) > 1e-6
        argmax_id = np.where(cover, argmax_id, -1)
        edge = np.zeros_like(cover)
        edge[:, 1:] |= (argmax_id[:, 1:] != argmax_id[:, :-1])
        edge[1:, :] |= (argmax_id[1:, :] != argmax_id[:-1, :])
        bdy_h = (argmax_id[:, 1:] == -1) | (argmax_id[:, :-1] == -1)
        bdy_v = (argmax_id[1:, :] == -1) | (argmax_id[:-1, :] == -1)
        edge[:, 1:] &= ~bdy_h
        edge[1:, :] &= ~bdy_v
        k = max(3, dilate_px | 1)
        return _cv2.dilate(edge.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)

    def _grad_energy(rgb: np.ndarray, mask: np.ndarray) -> float:
        gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)
        gx = _cv2.Sobel(gray, _cv2.CV_32F, 1, 0, ksize=3)
        gy = _cv2.Sobel(gray, _cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx * gx + gy * gy)
        if not mask.any():
            return float("nan")
        return float(mag[mask].mean())

    band_l1 = _seam_band(cos2_weights, dilate_px=8)
    band_gc = _seam_band(gc_weights, dilate_px=8)
    grad_l1_in_band = _grad_energy(erp_l1, band_l1)
    grad_gc_in_band = _grad_energy(erp_gc, band_gc)
    # Common band (intersect both) gives apples-to-apples region
    common_band = band_l1 & band_gc
    grad_l1_common = _grad_energy(erp_l1, common_band)
    grad_gc_common = _grad_energy(erp_gc, common_band)
    metrics = {
        "psnr_l1_vs_graphcut_dB": round(psnr_l1_gc, 4),
        "seam_band_dilate_px": 8,
        "seam_band_px_l1": int(band_l1.sum()),
        "seam_band_px_graphcut": int(band_gc.sum()),
        "common_band_px": int(common_band.sum()),
        "mean_grad_in_l1_seam_band__l1_erp": round(grad_l1_in_band, 3),
        "mean_grad_in_gc_seam_band__gc_erp": round(grad_gc_in_band, 3),
        "mean_grad_common_band__l1_erp": round(grad_l1_common, 3),
        "mean_grad_common_band__gc_erp": round(grad_gc_common, 3),
        "delta_seam_smoothness_dB_proxy": round(
            10.0 * np.log10(max(grad_l1_in_band, 1e-6) / max(grad_gc_in_band, 1e-6)), 3,
        ),
    }
    print(f"[graphcut] metrics: L1<->GC PSNR={psnr_l1_gc:.2f} dB, "
          f"mean |grad| in seam band: L1={grad_l1_in_band:.2f} GC={grad_gc_in_band:.2f}",
          flush=True)

    # ---- Per-pair seam-cost summary ----
    summary = {
        "route": "11 / new-B",
        "mode": mode,
        "anchor_idx": anchor_idx,
        "anchor_timestamp_ns": anchor_ts_ns,
        "erp_hw": list(erp_hw),
        "params": {
            "num_bands": args.num_bands,
            "wrap": not args.no_wrap,
            "feather_sigma": args.feather_sigma,
            "energy_weights": [args.alpha_w, args.beta_w, args.gamma_w],
            "min_overlap_px": args.min_overlap_px,
        },
        "per_cam": per_cam_log,
        "per_pair_seams": [
            {
                "pair_idx": p["pair"],
                "pair_cams": [RING_CAMS_7[p["pair"][0]], RING_CAMS_7[p["pair"][1]]],
                "overlap_px": p["overlap_px"],
                "assigned_i_px": p["assigned_i_px"],
                "assigned_j_px": p["assigned_j_px"],
                "energy_mean_on_overlap": round(p["energy_mean_on_overlap"], 4),
            }
            for p in seam_log
        ],
        "runtime_s": {
            "projection": round(t_proj_s, 2),
            "l1_blend": round(t_l1_s, 2),
            "graphcut_blend": round(t_gc_s, 2),
        },
        "metrics": metrics,
        "outputs": {
            "sphere_l1_baseline": str((out_dir / "sphere_l1_baseline.png").resolve()),
            "graphcut_seam": str((out_dir / "graphcut_seam.png").resolve()),
            "compare_l1_vs_graphcut": str((out_dir / "compare_l1_vs_graphcut.png").resolve()),
            "seams_overlay_l1": str((out_dir / "seams_overlay_l1.png").resolve()),
            "seams_overlay_graphcut": str((out_dir / "seams_overlay_graphcut.png").resolve()),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[graphcut] done -> {out_dir}", flush=True)
    print(f"[graphcut] runtime: proj={t_proj_s:.1f}s, l1={t_l1_s:.1f}s, gc={t_gc_s:.1f}s",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
