"""
4-way panorama comparison for the frequency-band hybrid blend.

Panels (left → right):
  1. MULTIBAND               — Burt-Adelson cos²-weighted blend (current SOTA classical)
  2. HARD SELECT             — argmax of cos² weight (kills doubled BMW, sharp seam)
  3. HS+HDR+OF (FULL)        — shipped 3-layer pipeline (hard_select + L2 HDR + L3 OF)
  4. HS+HDR+OF (FREQHYBRID)  — NEW: replaces final hard_select with per-band hybrid
                              (high-freq hard, low-freq blended). Goal: keep the
                              ghost-free edges of hard_select AND hide residual
                              tone steps in low-freq smooth areas.

Designed for the BMW anchor (log 02a00399, anchor 0) at 2048×4096 ERP.

Two data-source modes
---------------------
  --log-dir   /path/to/argoverse2/.../02a00399-...   (raw AV2: feather + .jpg)
              Production mode. Requires pyarrow + raw AV2 sensor log.

  --cache-dir D:/.../outputs/phase3/pi3_cache/anchor_000                (cached)
              Local-only fallback. Loads per-cam K, T_ego_cam, and a
              letterboxed image from a cached anchor directory. Cached
              images are 504×504 (Pi3X letterbox); ERP resolution is
              still controlled by --erp-h/--erp-w but the source data
              is what dictates effective quality.

Output → deliverables/freqhybrid/bmw_4way.png

This script does NOT run on Colab — the T4 is busy with the v1 5-log render.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


# Cam ordering must match RING_CAMS_7 in waymo2panorama.data_io.av2_loader.
RING_CAMS_7 = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)


def _load_from_cache(cache_dir: Path) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray]]:
    """Load images, K, T_ego_cam for 7 ring cams from a pi3_cache anchor dir."""
    images, Ks, Ts = [], [], []
    for cam in RING_CAMS_7:
        img_path = cache_dir / f"image_{cam}.png"
        k_path = cache_dir / f"av2_K_letterboxed_{cam}.npy"
        t_path = cache_dir / f"av2_T_ego_cam_{cam}.npy"
        for p in (img_path, k_path, t_path):
            if not p.exists():
                raise FileNotFoundError(f"missing cache file: {p}")
        images.append(np.asarray(Image.open(img_path).convert("RGB")))
        Ks.append(np.load(k_path))
        Ts.append(np.load(t_path))
    return images, Ks, Ts


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
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--log-dir", type=Path,
                     help="raw AV2 sensor log dir (Colab-style path)")
    src.add_argument("--cache-dir", type=Path,
                     help="cached anchor dir with image_*.png + "
                          "av2_K_letterboxed_*.npy + av2_T_ego_cam_*.npy")
    ap.add_argument("--anchor", type=int, default=0,
                    help="anchor index when using --log-dir (ignored with --cache-dir)")
    ap.add_argument("--out-png", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent
                    / "deliverables" / "freqhybrid" / "bmw_4way.png",
                    help="output path for 4-way comparison PNG")
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--num-bands", type=int, default=5,
                    help="pyramid bands for multiband / freqhybrid")
    ap.add_argument("--hard-cutoff", type=int, default=None,
                    help="freqhybrid: levels < cutoff use hard select; "
                         "levels >= cutoff use weighted blend. "
                         "Default num_bands // 2.")
    ap.add_argument("--full-h", type=int, default=1024,
                    help="height of each downsized panel in the 4-way PNG")
    ap.add_argument("--no-of", action="store_true",
                    help="skip L3 OF in BOTH full + freqhybrid pipelines "
                         "(much faster, ~20s vs ~50s per blend)")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent / "code",
                    help="path to the `code/` directory (added to sys.path)")
    args = ap.parse_args()

    args.out_png.parent.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(args.w2p_code))
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.blending.hard_hdr_of import hard_select, blend_hard_hdr_of
    from waymo2panorama.blending.hard_hdr_of_freqhybrid import (
        blend_hard_hdr_of_freqhybrid,
    )

    # ---- Load cameras (raw AV2 OR cache) ---------------------------------
    if args.log_dir is not None:
        from waymo2panorama.data_io.av2_loader import AV2RingLoader
        loader = AV2RingLoader(args.log_dir)
        ts_all = loader.anchor_timestamps_ns()
        if args.anchor < 0 or args.anchor >= len(ts_all):
            raise IndexError(
                f"--anchor {args.anchor} out of range; log has {len(ts_all)} anchors"
            )
        frame = loader.load_synced_frame(ts_all[args.anchor])
        images = [frame.images[c] for c in RING_CAMS_7]
        Ks = [frame.calibrations[c].K for c in RING_CAMS_7]
        Ts = [frame.calibrations[c].T_ego_cam for c in RING_CAMS_7]
        src_desc = f"log_dir={args.log_dir.name}, anchor={args.anchor}"
    else:
        images, Ks, Ts = _load_from_cache(args.cache_dir)
        src_desc = f"cache_dir={args.cache_dir.name} (letterboxed source)"

    print(f"source: {src_desc}")
    print(f"source image sizes: {[im.shape for im in images]}")

    # ---- Project all 7 cams to ERP ---------------------------------------
    erp_hw = (args.erp_h, args.erp_w)
    print(f"projecting 7 cams to {erp_hw} ERP ...")
    t0 = time.time()
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam, im, K, T in zip(RING_CAMS_7, images, Ks, Ts):
        rgb, _alpha, w = render_camera_to_erp(
            image=im, K=K, T_ego_cam=T,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
        cov = float((w > 1e-6).mean())
        print(f"  {cam:<18s}: coverage {cov*100:5.1f}%")
    print(f"  projection: {time.time() - t0:.1f}s")

    apply_of = not args.no_of
    hard_cutoff = (
        args.hard_cutoff
        if args.hard_cutoff is not None
        else args.num_bands // 2
    )
    print(f"\nfreqhybrid: num_bands={args.num_bands}, hard_cutoff_level={hard_cutoff}")
    print(
        f"  -> levels < {hard_cutoff} (i.e. 0..{hard_cutoff-1}) = HARD SELECT (high-freq)\n"
        f"  -> levels >= {hard_cutoff} (i.e. {hard_cutoff}..{args.num_bands}) = "
        f"WEIGHTED BLEND (low-freq)"
    )

    # ---- 1. Multiband baseline -------------------------------------------
    print("\n[1/4] multiband ...")
    t0 = time.time()
    erp_mb = multiband_blend(slabs, weights, num_bands=args.num_bands, wrap=True)
    print(f"  multiband: {time.time() - t0:.1f}s")

    # ---- 2. Plain hard select (no HDR, no OF) ----------------------------
    print("[2/4] hard_select ...")
    t0 = time.time()
    erp_hs = hard_select(slabs, weights)
    print(f"  hard_select: {time.time() - t0:.1f}s")

    # ---- 3. Full shipped pipeline (HS + HDR + OF) ------------------------
    print(f"[3/4] hard_hdr_of (apply_of={apply_of}) ...")
    t0 = time.time()
    erp_full = blend_hard_hdr_of(slabs, weights, apply_of=apply_of)
    print(f"  hard_hdr_of: {time.time() - t0:.1f}s")

    # ---- 4. NEW: freqhybrid (HS-replaced) + HDR + OF ---------------------
    print(f"[4/4] hard_hdr_of_freqhybrid (apply_of={apply_of}) ...")
    t0 = time.time()
    erp_fh = blend_hard_hdr_of_freqhybrid(
        slabs, weights,
        apply_of=apply_of,
        num_bands=args.num_bands,
        hard_cutoff_level=hard_cutoff,
    )
    print(f"  freqhybrid: {time.time() - t0:.1f}s")

    # ---- 4-way side-by-side ----------------------------------------------
    labels = [
        "MULTIBAND",
        "HARD SELECT",
        "HS+HDR+OF (FULL)",
        f"HS+HDR+OF (FREQHYBRID c={hard_cutoff})",
    ]
    erps = [erp_mb, erp_hs, erp_full, erp_fh]
    panels = [_label(_downsize(e, h=args.full_h), l) for e, l in zip(erps, labels)]
    panel = np.concatenate(panels, axis=1)
    Image.fromarray(panel).save(args.out_png)
    print(f"\nsaved 4-way -> {args.out_png}")

    # Also save full-res ERPs alongside for closer inspection.
    out_dir = args.out_png.parent
    Image.fromarray(erp_mb).save(out_dir / "erp_multiband.png")
    Image.fromarray(erp_hs).save(out_dir / "erp_hard_select.png")
    Image.fromarray(erp_full).save(out_dir / "erp_hard_hdr_of_full.png")
    Image.fromarray(erp_fh).save(out_dir / "erp_hard_hdr_of_freqhybrid.png")
    print(f"saved 4 full-res ERPs -> {out_dir}")

    # ---- BMW zoom panel (4-way) ------------------------------------------
    # BMW is at roughly the same ERP coordinates as in test_hard_select_hdr_of.py.
    # Reference: 4096x2048 ERP, BMW centered at col ~3500, rows 900..1300.
    # Scale to current ERP size.
    H, W = args.erp_h, args.erp_w
    col_c = int(3500 / 4096 * W)
    row_t = int( 900 / 2048 * H)
    row_b = int(1300 / 2048 * H)
    half  = int( 250 / 4096 * W)
    col_l = max(0, col_c - half)
    col_r = min(W, col_c + half)
    crops = [e[row_t:row_b, col_l:col_r] for e in erps]
    if all(c.size > 0 for c in crops):
        bmw_panel = np.concatenate(
            [_label(c, l, fontsize=22) for c, l in zip(crops, labels)],
            axis=1,
        )
        bmw_path = out_dir / "bmw_4way_zoom.png"
        Image.fromarray(bmw_panel).save(bmw_path)
        print(f"saved BMW zoom -> {bmw_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
