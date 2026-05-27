"""
A/B compare: baseline hard_hdr_of vs hard_hdr_of_chroma on one anchor.

The chroma variant adds an L2b stage between luminance HDR (L2) and OF chain
warp (L3): solve per-cam additive Cr / Cb offsets via Tikhonov-regularized
joint LS with similarity-threshold outlier rejection (drops pairs where
overlap chroma diff > 35 in 0-255, suggesting REAL scene chroma difference
rather than cross-cam white-balance drift). Final offsets clipped to
[-20, +20] for safety. See `code/waymo2panorama/blending/hard_hdr_of_chroma.py`.

Outputs (saved to `deliverables/chroma_correction/<log_short>_a<anchor>_compare.png`):
    side-by-side:
        - left:  baseline hard_hdr_of full ERP
        - right: hard_hdr_of_chroma full ERP
    plus printed diagnostics (per-cam Y gains + per-cam Cr/Cb offsets) for
    inspection.

Usage:
    python scripts/phase3/test_chroma_correction.py \
        --log-dir /content/drive/.../02a00399-... \
        --anchor 0 \
        [--out-root deliverables/chroma_correction]

NOTE: This script is for LATER manual run on Colab. The 5-log v1 render is
holding the T4 right now — do not run this on Colab until that finishes.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _label(arr: np.ndarray, text: str, fontsize: int = 28) -> np.ndarray:
    """Draw a black-outlined white text label in the top-left corner."""
    pil = Image.fromarray(arr.copy())
    draw = ImageDraw.Draw(pil)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((8 + dx, 6 + dy), text, font=font, fill="black")
    draw.text((8, 6), text, font=font, fill="white")
    return np.array(pil)


def _downsize(arr: np.ndarray, h: int = 1024) -> np.ndarray:
    """Downsize array to height `h` preserving aspect ratio (Lanczos)."""
    w = int(arr.shape[1] * h / arr.shape[0])
    return np.array(Image.fromarray(arr).resize((w, h), Image.LANCZOS))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True,
                    help="AV2 sensor log directory (containing sensors/, calibration/)")
    ap.add_argument("--anchor", type=int, default=0,
                    help="anchor index (front_center frame index)")
    ap.add_argument("--out-root", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent
                    / "deliverables" / "chroma_correction",
                    help="output directory root; per-anchor compare PNG goes here")
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--full-h", type=int, default=1024,
                    help="height of each panel in the compare PNG (downsized from erp-h)")
    ap.add_argument("--no-of", action="store_true",
                    help="skip L3 OF in BOTH variants (much faster, for quick chroma-only check)")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent / "code",
                    help="path to the `code/` directory (added to sys.path)")
    args = ap.parse_args()

    args.out_root.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(args.w2p_code))
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.hard_hdr_of import blend_hard_hdr_of
    from waymo2panorama.blending.hard_hdr_of_chroma import blend_hard_hdr_of_chroma

    # ---- Load + project once (shared between both variants) --------------
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    if args.anchor < 0 or args.anchor >= len(ts_all):
        raise IndexError(
            f"--anchor {args.anchor} out of range; log has {len(ts_all)} anchors"
        )
    frame = loader.load_synced_frame(ts_all[args.anchor])
    erp_hw = (args.erp_h, args.erp_w)

    print(f"projecting 7 cams to {erp_hw} ERP ...")
    t0 = time.time()
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=frame.images[cam],
            K=c.K, T_ego_cam=c.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
    print(f"  projection: {time.time() - t0:.1f}s")

    apply_of = not args.no_of

    # ---- (a) Baseline: hard_hdr_of (no chroma) ---------------------------
    print(f"running baseline hard_hdr_of (apply_of={apply_of}) ...")
    t0 = time.time()
    erp_base = blend_hard_hdr_of(slabs, weights, apply_of=apply_of)
    print(f"  baseline: {time.time() - t0:.1f}s")

    # ---- (b) New: hard_hdr_of_chroma -------------------------------------
    print(f"running hard_hdr_of_chroma (apply_of={apply_of}) ...")
    t0 = time.time()
    erp_chr, diag = blend_hard_hdr_of_chroma(
        slabs, weights, apply_of=apply_of, return_offsets=True,
    )
    print(f"  chroma  : {time.time() - t0:.1f}s")

    # ---- Diagnostics print -----------------------------------------------
    print("\nDiagnostics:")
    print(f"  Y gains             (target {1.0:.3f}, clipped [0.5, 2.0]):")
    for cam, g in zip(RING_CAMS_7, diag["gains"]):
        print(f"    {cam:<18s}: {g:+.4f}x")
    print(f"  Cr offsets [0-255]  (target 0, clipped [-20, +20]):")
    for cam, dcr in zip(RING_CAMS_7, diag["cr_offsets"]):
        print(f"    {cam:<18s}: {dcr:+.3f}")
    print(f"  Cb offsets [0-255]  (target 0, clipped [-20, +20]):")
    for cam, dcb in zip(RING_CAMS_7, diag["cb_offsets"]):
        print(f"    {cam:<18s}: {dcb:+.3f}")

    # ---- Side-by-side compare PNG ----------------------------------------
    base_small = _downsize(erp_base, h=args.full_h)
    chr_small = _downsize(erp_chr, h=args.full_h)
    panel = np.concatenate([
        _label(base_small, "BASELINE hard_hdr_of"),
        _label(chr_small,  "NEW hard_hdr_of_chroma"),
    ], axis=1)
    log_short = args.log_dir.name.split("-")[0]
    out_png = args.out_root / f"{log_short}_a{args.anchor}_compare.png"
    Image.fromarray(panel).save(out_png)
    print(f"\nsaved compare -> {out_png}")

    # Also save the two full-res ERPs for closer inspection.
    Image.fromarray(erp_base).save(
        args.out_root / f"{log_short}_a{args.anchor}_baseline.png"
    )
    Image.fromarray(erp_chr).save(
        args.out_root / f"{log_short}_a{args.anchor}_chroma.png"
    )
    print(f"saved full-res baseline + chroma to {args.out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
