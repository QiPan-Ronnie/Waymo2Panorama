"""
Test (a): hard cam selection (no blend) vs multiband blend.

Hypothesis: BMW doubled-ghost comes from BLENDING two cams that see the same
object from different angles. If we hard-pick the cam with highest cos2 weight
per ERP pixel (no averaging), the BMW appears exactly once — no ghost from
view-mixing. Seams between cams will be visible as colour/content jumps but
the BMW shouldn't be doubled.

Cost: one ERP render pair + side-by-side panel.

Usage:
    python scripts/phase3/test_hard_select.py \
        --log-dir /content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/02a00399-3857-444e-8db3-a8f58489c394 \
        --anchor 0 \
        --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/hard_select \
        --erp-h 2048 --erp-w 4096
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent / "code")
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(args.w2p_code))
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.multiband import multiband_blend

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    if args.anchor >= len(ts_all):
        print(f"anchor {args.anchor} >= {len(ts_all)} available")
        return 1
    frame = loader.load_synced_frame(ts_all[args.anchor])

    erp_hw = (args.erp_h, args.erp_w)
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    cam_names: list[str] = []
    t0 = time.time()
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(
            image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
        cam_names.append(cam)
    print(f"projection of {len(slabs)} cams done in {time.time()-t0:.1f}s")

    # --- Multiband blend (reference) ---
    t0 = time.time()
    erp_multiband = multiband_blend(slabs, weights, num_bands=5, wrap=True)
    print(f"multiband blend done in {time.time()-t0:.1f}s")

    # --- Hard select (argmax of weights) ---
    t0 = time.time()
    w_stack = np.stack(weights, axis=0)  # (K, H, W)
    rgb_stack = np.stack(slabs, axis=0).astype(np.float32)  # (K, H, W, 3)
    argmax = w_stack.argmax(axis=0)  # (H, W) -> cam index per pixel
    valid = w_stack.max(axis=0) > 0  # (H, W)
    idx = argmax[None, ..., None]  # (1, H, W, 1) for take_along_axis
    picked = np.take_along_axis(rgb_stack, idx, axis=0)[0]  # (H, W, 3)
    erp_hard = np.where(valid[..., None], picked, 0).astype(np.uint8)
    print(f"hard select done in {time.time()-t0:.1f}s")

    # --- Per-cam coverage stats ---
    coverage = {cam: int((argmax == i).sum()) for i, cam in enumerate(cam_names)}
    total = args.erp_h * args.erp_w
    valid_total = int(valid.sum())
    print(f"valid pixels: {valid_total}/{total} ({100*valid_total/total:.1f}%)")
    for cam, n in coverage.items():
        print(f"  {cam:<18s}: {n:>8d} px ({100*n/valid_total:.1f}% of valid)")

    # --- Save full ERPs ---
    Image.fromarray(erp_multiband).save(args.output_dir / "erp_multiband.png")
    Image.fromarray(erp_hard).save(args.output_dir / "erp_hard.png")

    # --- BMW zoom crops (from make_n1_phase_a_panel.py coords, scaled) ---
    H, W = args.erp_h, args.erp_w
    bmw_col_c = int(round(3500/4096 * W))
    bmw_row_t = int(round(900/2048 * H))
    bmw_row_b = int(round(1300/2048 * H))
    bmw_half = int(round(250/4096 * W))
    bmw_col_l = max(0, bmw_col_c - bmw_half)
    bmw_col_r = min(W, bmw_col_c + bmw_half)

    bmw_mb = erp_multiband[bmw_row_t:bmw_row_b, bmw_col_l:bmw_col_r]
    bmw_hs = erp_hard[bmw_row_t:bmw_row_b, bmw_col_l:bmw_col_r]

    # --- Side-by-side panel ---
    def label(arr, text):
        pil = Image.fromarray(arr.copy())
        draw = ImageDraw.Draw(pil)
        try:
            f = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        except Exception:
            f = ImageFont.load_default()
        for dx, dy in [(-2,0),(2,0),(0,-2),(0,2)]:
            draw.text((6+dx, 4+dy), text, font=f, fill="black")
        draw.text((6, 4), text, font=f, fill="white")
        return np.array(pil)

    # Big panel: 2 rows of full ERP (smaller) + 2 rows of BMW zoom (bigger)
    erp_mb_small = np.array(Image.fromarray(erp_multiband).resize((W//2, H//2), Image.LANCZOS))
    erp_hs_small = np.array(Image.fromarray(erp_hard).resize((W//2, H//2), Image.LANCZOS))
    erp_mb_lbl = label(erp_mb_small, "MULTIBAND BLEND (current, has BMW ghost)")
    erp_hs_lbl = label(erp_hs_small, "HARD SELECT (proposed, argmax of cos2)")

    full_panel = np.concatenate([erp_mb_lbl, erp_hs_lbl], axis=0)
    Image.fromarray(full_panel).save(args.output_dir / "full_compare.png")

    bmw_mb_lbl = label(bmw_mb, "MULTIBAND BMW")
    bmw_hs_lbl = label(bmw_hs, "HARD SELECT BMW")
    bmw_panel = np.concatenate([bmw_mb_lbl, bmw_hs_lbl], axis=1)
    Image.fromarray(bmw_panel).save(args.output_dir / "bmw_compare.png")

    print(f"saved: {args.output_dir / 'full_compare.png'}")
    print(f"saved: {args.output_dir / 'bmw_compare.png'}")
    print(f"saved: {args.output_dir / 'erp_multiband.png'}")
    print(f"saved: {args.output_dir / 'erp_hard.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
