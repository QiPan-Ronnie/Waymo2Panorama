"""
3-way A/B compare: shipped L1+L2+L3 vs +chroma vs +chroma+graphcut.

Renders the SAME 7 ring-cam projections through three pipelines that share
identical L0 input and L2 luminance HDR, then diverge at the chroma /
seam-pick stages:

    (a) hard_hdr_of              -> L2 Y-HDR + L3 OF + L1 cos² argmax
    (b) hard_hdr_of_chroma       -> (a) + L2b chroma offsets
    (c) hard_hdr_of_combined     -> (b) + L1' graph-cut seam routing

Hypothesis: (c) is the production-grade variant. Chroma handles the
per-cam white-balance drift Y-HDR cannot touch; graph-cut routes seams
around objects (BMW bumper, lane lines, columns) rather than through them.

Outputs (saved under `deliverables/combined/`):
    bmw_3way_real.png       — labelled side-by-side panel (a) | (b) | (c)
    bmw_a_baseline.png      — full-res (a)
    bmw_b_chroma.png        — full-res (b)
    bmw_c_combined.png      — full-res (c)
plus printed diagnostics (Y gains, Cr/Cb offsets, per-stage wall time).

Usage:
    python scripts/phase3/test_combined.py \
        --log-dir /path/to/av2/02a00399-... \
        --anchor 0 \
        --erp-h 2048 --erp-w 4096

This script is local-only (no Colab). It does NOT trigger a re-render
across logs — that is the user's decision after reviewing the 3-way panel.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_W2P_CODE = REPO_ROOT / "code"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "deliverables" / "combined"


def _label(arr: np.ndarray, text: str, fontsize: int = 28) -> np.ndarray:
    """Burn a labelled caption into the top-left corner of an RGB image."""
    pil = Image.fromarray(arr.copy())
    draw = ImageDraw.Draw(pil)
    font = None
    for name in ("DejaVuSans-Bold.ttf", "arial.ttf", "Arial.ttf"):
        try:
            font = ImageFont.truetype(name, fontsize)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)]:
        draw.text((10 + dx, 8 + dy), text, font=font, fill="black")
    draw.text((10, 8), text, font=font, fill="white")
    return np.asarray(pil)


def _downsize(arr: np.ndarray, target_h: int) -> np.ndarray:
    """Resize keeping aspect ratio so the result has height target_h."""
    h, w = arr.shape[:2]
    new_w = int(round(w * target_h / h))
    return np.asarray(Image.fromarray(arr).resize((new_w, target_h), Image.LANCZOS))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--log-dir", type=Path, required=True,
        help="path to AV2 log root containing 'sensors/cameras/...'",
    )
    ap.add_argument("--anchor", type=int, default=0,
                    help="anchor index (front_center frame index); default 0")
    ap.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help=f"default: {DEFAULT_OUTPUT_DIR.relative_to(REPO_ROOT)}",
    )
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--panel-h", type=int, default=900,
                    help="height of each panel in the 3-way side-by-side PNG")
    ap.add_argument(
        "--no-of", action="store_true",
        help="skip L3 OF chain warp in ALL three variants (faster, for "
             "quickly sanity-checking the chroma/seam stages alone)",
    )
    ap.add_argument(
        "--w2p-code", type=Path, default=DEFAULT_W2P_CODE,
        help="path to the waymo2panorama code dir",
    )
    args = ap.parse_args()

    if not args.log_dir.exists():
        print(
            f"ERROR: log dir {args.log_dir} does not exist.\n"
            f"This script is local-only — point --log-dir at a downloaded AV2 log.\n"
            f"Expected anchor 0 of 02a00399 (the BMW anchor).",
            file=sys.stderr,
        )
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.w2p_code))

    # All imports happen after sys.path mutation so the waymo2panorama package
    # is resolvable from the repo `code/` dir without a global install.
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.hard_hdr_of import (
        apply_hdr,
        compute_hdr_gains,
        hard_select,
        of_chain_warp,
    )
    from waymo2panorama.blending.hard_hdr_of_chroma import (
        apply_chroma,
        compute_chroma_offsets,
    )
    from waymo2panorama.blending.hard_hdr_of_graphcut import (
        hard_select_with_graphcut,
    )

    erp_hw = (args.erp_h, args.erp_w)
    apply_of = not args.no_of
    print(f"log_dir   = {args.log_dir}")
    print(f"anchor    = {args.anchor}")
    print(f"erp_hw    = {erp_hw}")
    print(f"apply_of  = {apply_of}")
    print(f"output    = {args.output_dir}")

    # ---- Load + project once (shared between all three variants) ----------
    t0 = time.time()
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    if args.anchor < 0 or args.anchor >= len(ts_all):
        print(
            f"ERROR: --anchor {args.anchor} out of range; log has "
            f"{len(ts_all)} anchors",
            file=sys.stderr,
        )
        return 2
    frame = loader.load_synced_frame(ts_all[args.anchor])

    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(
            image=frame.images[cam], K=c.K, T_ego_cam=c.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
    print(f"L0 projection ........ {time.time() - t0:.1f}s")

    # ---- Shared L2 luminance HDR (all three variants see the same Y) ------
    t0 = time.time()
    gains = compute_hdr_gains(slabs, weights)
    slabs_y = apply_hdr(slabs, gains)
    print(f"L2 luminance HDR ..... {time.time() - t0:.1f}s")

    # ---- Variant (a) baseline: L2 + L3 + L1 cos² argmax ------------------
    t0 = time.time()
    if apply_of:
        slabs_a, weights_a = of_chain_warp(slabs_y, weights)
    else:
        slabs_a, weights_a = slabs_y, weights
    erp_a = hard_select(slabs_a, weights_a)
    print(f"(a) baseline        .. {time.time() - t0:.1f}s")

    # ---- Variant (b) +chroma: L2 + L2b chroma + L3 + L1 argmax -----------
    t0 = time.time()
    cr_off, cb_off = compute_chroma_offsets(slabs_y, weights)
    slabs_chroma_only = apply_chroma(slabs_y, cr_off, cb_off)
    if apply_of:
        slabs_b, weights_b = of_chain_warp(slabs_chroma_only, weights)
    else:
        slabs_b, weights_b = slabs_chroma_only, weights
    erp_b = hard_select(slabs_b, weights_b)
    print(f"(b) +chroma         .. {time.time() - t0:.1f}s")

    # ---- Variant (c) combined: L2 + L2b chroma + L3 + L1' graphcut -------
    # Reuse the OF-warped chroma slabs from (b) — the OF chain only depends on
    # the slabs/weights, so the warped output for (b) and (c) is bit-identical.
    t0 = time.time()
    erp_c = hard_select_with_graphcut(slabs_b, weights_b)
    print(f"(c) +chroma+graphcut . {time.time() - t0:.1f}s "
          f"(reuses (b)'s OF-warped slabs; only the L1 pick differs)")

    # ---- Diagnostics print ------------------------------------------------
    print("\nDiagnostics:")
    print("  Y gains    (target 1.000, clipped [0.5, 2.0]):")
    for cam, g in zip(RING_CAMS_7, gains):
        print(f"    {cam:<18s}: {g:+.4f}x")
    print("  Cr offsets (target 0, clipped [-20, +20]):")
    for cam, dcr in zip(RING_CAMS_7, cr_off):
        print(f"    {cam:<18s}: {dcr:+.3f}")
    print("  Cb offsets (target 0, clipped [-20, +20]):")
    for cam, dcb in zip(RING_CAMS_7, cb_off):
        print(f"    {cam:<18s}: {dcb:+.3f}")

    # ---- 3-way side-by-side panel -----------------------------------------
    a_small = _label(_downsize(erp_a, args.panel_h),
                     "(a) L1+L2+L3 baseline  [cos2 argmax]")
    b_small = _label(_downsize(erp_b, args.panel_h),
                     "(b) +chroma  [Y-HDR + Cr/Cb offsets]")
    c_small = _label(_downsize(erp_c, args.panel_h),
                     "(c) +chroma +graphcut  [PRODUCTION]")
    panel = np.concatenate([a_small, b_small, c_small], axis=1)
    out_panel = args.output_dir / "bmw_3way_real.png"
    Image.fromarray(panel).save(out_panel)
    print(f"\n3-way panel       -> {out_panel}")

    # ---- Full-res ERPs for closer inspection ------------------------------
    Image.fromarray(erp_a).save(args.output_dir / "bmw_a_baseline.png")
    Image.fromarray(erp_b).save(args.output_dir / "bmw_b_chroma.png")
    Image.fromarray(erp_c).save(args.output_dir / "bmw_c_combined.png")
    print(f"full-res (a)      -> {args.output_dir / 'bmw_a_baseline.png'}")
    print(f"full-res (b)      -> {args.output_dir / 'bmw_b_chroma.png'}")
    print(f"full-res (c)      -> {args.output_dir / 'bmw_c_combined.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
