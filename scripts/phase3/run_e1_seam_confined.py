"""E1 driver: L1 hard_select + seam-confined multiband, on one AV2 anchor. Runs on Colab.

Produces, per anchor:
  {tag}_L1.png         - L1 hard_select baseline (the clean geometry backbone)
  {tag}_E1.png         - E1 seam-confined output (far field == L1, ~7 seam strips feathered to mb)
  {tag}_multiband.png  - full multiband (reference: smooth seams BUT near-field doubling everywhere)
  {tag}_alpha.png      - the seam-band alpha (where E1 differs from L1)
  {tag}_seamcrops.png  - stacked L1 / E1 / multiband crops at the strongest seams + the BMW seam,
                         so the photometric-vs-parallax isolation is visible to the eye.

E1 expectation (the E1->E2 isolation): at FAR-field seams the photometric step vanishes with NO
doubling (no parallax there); at NEAR-field seams (BMW car) E1 ~ multiband (doubling reappears),
flagging exactly which seams need E2's parallax alignment.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


def _crop(img, c_col, c_row, half_w, half_h, W, H):
    l, r = max(0, c_col - half_w), min(W, c_col + half_w)
    t, b = max(0, c_row - half_h), min(H, c_row + half_h)
    return img[t:b, l:r]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--tag", type=str, default="bmw")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--band-half-width", type=int, default=64)
    ap.add_argument("--lowfreq-cutoff", type=int, default=-1,
                    help="-1=E1 full multiband in band; >=0=E1.5 low-freq-only blend cutoff level")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent / "code")
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.w2p_code))

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.seam_confined import blend_seam_confined

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    if args.anchor >= len(ts_all):
        print(f"anchor {args.anchor} >= {len(ts_all)} available")
        return 1
    frame = loader.load_synced_frame(ts_all[args.anchor])
    erp_hw = (args.erp_h, args.erp_w)
    H, W = erp_hw

    slabs, weights = [], []
    t0 = time.time()
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(
            image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
    print(f"[{args.tag}] projected {len(slabs)} cams in {time.time()-t0:.1f}s")

    t0 = time.time()
    lfc = None if args.lowfreq_cutoff < 0 else args.lowfreq_cutoff
    res = blend_seam_confined(slabs, weights, num_bands=args.num_bands,
                              band_half_width=args.band_half_width, wrap=True,
                              lowfreq_cutoff=lfc)
    print(f"[{args.tag}] seam-confined blend in {time.time()-t0:.1f}s")
    L1, E1, mb, alpha = res["base"], res["out"], res["mb"], res["alpha"]

    # far-field byte-identity sanity: how much of the image did E1 change?
    changed = np.any(E1 != L1, axis=-1)
    print(f"[{args.tag}] E1 changed {100*changed.mean():.2f}% of pixels (far field must be untouched)")

    Image.fromarray(L1).save(args.output_dir / f"{args.tag}_L1.png")
    Image.fromarray(E1).save(args.output_dir / f"{args.tag}_E1.png")
    Image.fromarray(mb).save(args.output_dir / f"{args.tag}_multiband.png")
    Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8)).save(args.output_dir / f"{args.tag}_alpha.png")

    # --- seam crops: pick the strongest seam columns from alpha column-mass, + the BMW seam ---
    col_mass = alpha.sum(axis=0)
    # non-maximum suppression: take peaks >= 40% of max, min 80px apart
    peaks = []
    order = np.argsort(col_mass)[::-1]
    for c in order:
        if col_mass[c] < 0.4 * col_mass.max():
            break
        if all(abs(c - p) > 80 and abs(c - p) < W - 80 for p in peaks):
            peaks.append(int(c))
        if len(peaks) >= 4:
            break
    bmw_col = int(round(3500 / 4096 * W))  # BMW near-field seam (from test_hard_select coords)
    cols = sorted(set(peaks + [bmw_col]))
    hw, hh = 110, 220
    rows = []
    for c in cols:
        trip = [_crop(im, c, H // 2, hw, hh, W, H) for im in (L1, E1, mb)]
        trip = [np.pad(t, ((0, max(0, 2 * hh - t.shape[0])), (0, max(0, 2 * hw - t.shape[1])), (0, 0)))
                for t in trip]
        rows.append(np.hstack(trip))
    if rows:
        maxw = max(r.shape[1] for r in rows)
        rows = [np.pad(r, ((0, 0), (0, maxw - r.shape[1]), (0, 0))) for r in rows]
        strip = np.vstack(rows)
        Image.fromarray(strip).save(args.output_dir / f"{args.tag}_seamcrops.png")
        print(f"[{args.tag}] seam crop columns (L1|E1|multiband per row): {cols}")
    print(f"[{args.tag}] done -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
