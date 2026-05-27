"""
Smart-seam graph-cut comparison on the BMW anchor (02a00399 anchor 0).

Renders the SAME 7 ring-cam projections two ways:

    (a) plain hard_hdr_of       (cos² argmax → fixed Voronoi seam through
                                 whatever object sits at the angular boundary)
    (b) hard_hdr_of_graphcut    (per-pair graph-cut seam that routes around
                                 low-disagreement regions instead of cutting
                                 objects)

Then composes a side-by-side panel:
    deliverables/graphcut_seam/bmw_compare.png
        - top row : full ERPs (a) vs (b), downsampled for the side-by-side panel
        - bottom row : zoom of the BMW front-right seam crop, (a) vs (b)

Also writes the two full-resolution ERPs and a seam-overlay visualization
for both, so individual frames can be inspected.

The script is intentionally local-only (no Colab dependency). Run from the
repo root:

    python scripts/phase3/test_graphcut_seam.py \
        --log-dir /path/to/av2/02a00399-... \
        --anchor 0 \
        --output-dir deliverables/graphcut_seam \
        --erp-h 2048 --erp-w 4096

If the log isn't available locally, the script will print a helpful error
and exit — it does NOT call out to Colab or fetch anything from Drive.
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
DEFAULT_OUTPUT_DIR = REPO_ROOT / "deliverables" / "graphcut_seam"


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
    # black outline for legibility
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)]:
        draw.text((10 + dx, 8 + dy), text, font=font, fill="black")
    draw.text((10, 8), text, font=font, fill="white")
    return np.asarray(pil)


def _downsize(arr: np.ndarray, target_h: int) -> np.ndarray:
    """Resize keeping aspect ratio so the result has height target_h."""
    h, w = arr.shape[:2]
    new_w = int(round(w * target_h / h))
    return np.asarray(Image.fromarray(arr).resize((new_w, target_h), Image.LANCZOS))


def _seam_overlay(erp: np.ndarray, weights_used: list[np.ndarray],
                  color: tuple[int, int, int] = (255, 0, 0)) -> np.ndarray:
    """Draw thin red lines where the chosen-cam id changes (visualizes the seam)."""
    H, W = erp.shape[:2]
    stack = np.stack(weights_used, axis=0)
    argmax = stack.argmax(axis=0).astype(np.int32)
    valid = stack.max(axis=0) > 0
    argmax = np.where(valid, argmax, -1)
    edge = np.zeros((H, W), dtype=bool)
    edge[:, 1:] |= (argmax[:, 1:] != argmax[:, :-1])
    edge[1:, :] |= (argmax[1:, :] != argmax[:-1, :])
    # drop transitions across coverage boundary
    bdy_h = (argmax[:, 1:] == -1) | (argmax[:, :-1] == -1)
    bdy_v = (argmax[1:, :] == -1) | (argmax[:-1, :] == -1)
    edge[:, 1:] &= ~bdy_h
    edge[1:, :] &= ~bdy_v
    import cv2  # local import to avoid cv2 cost when only computing seam
    edge = cv2.dilate(edge.astype(np.uint8), np.ones((2, 2), np.uint8)).astype(bool)
    out = erp.copy()
    out[edge] = np.array(color, dtype=out.dtype)
    return out


def _zoom_crop(erp: np.ndarray, center_uv: tuple[int, int], crop_hw: tuple[int, int]) -> np.ndarray:
    """Crop a (crop_h, crop_w) window around (col_center, row_center), clamped."""
    u_c, v_c = center_uv
    H, W = erp.shape[:2]
    crop_h, crop_w = crop_hw
    u0 = max(0, u_c - crop_w // 2); u1 = min(W, u0 + crop_w)
    v0 = max(0, v_c - crop_h // 2); v1 = min(H, v0 + crop_h)
    return erp[v0:v1, u0:u1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--log-dir", type=Path, required=True,
        help="path to AV2 log root containing 'sensors/cameras/...'",
    )
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help=f"default: {DEFAULT_OUTPUT_DIR.relative_to(REPO_ROOT)}",
    )
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument(
        "--no-of", action="store_true",
        help="skip L3 OF chain warp (faster; mostly for debugging seam routing alone)",
    )
    ap.add_argument(
        "--w2p-code", type=Path, default=DEFAULT_W2P_CODE,
        help="path to the waymo2panorama code dir",
    )
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(args.w2p_code))
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.hard_hdr_of import (
        apply_hdr,
        compute_hdr_gains,
        of_chain_warp,
        hard_select,
    )
    from waymo2panorama.blending.hard_hdr_of_graphcut import (
        hard_select_with_graphcut,
    )

    if not args.log_dir.exists():
        print(
            f"ERROR: log dir {args.log_dir} does not exist.\n"
            f"This script is local-only — point --log-dir at a downloaded AV2 log.\n"
            f"Expected anchor 0 of 02a00399 (the BMW anchor).",
            file=sys.stderr,
        )
        return 2

    erp_hw = (args.erp_h, args.erp_w)
    print(f"log_dir = {args.log_dir}")
    print(f"anchor  = {args.anchor}")
    print(f"erp_hw  = {erp_hw}")
    print(f"output  = {args.output_dir}")

    # ---- Load 7 ring cams and project to ERP ------------------------------
    t0 = time.time()
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    if args.anchor >= len(ts_all):
        print(
            f"ERROR: anchor {args.anchor} out of range (log has {len(ts_all)} anchors)",
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
    print(f"L0 projection: {time.time()-t0:.1f}s")

    # ---- Shared L2 HDR + L3 OF, then split into the two L1 variants -------
    # We replicate the body of blend_hard_hdr_of[_graphcut] so both pipelines
    # see the SAME slabs after HDR + OF — only the final pick differs. That
    # makes the side-by-side diff strictly attributable to the seam routing.
    t0 = time.time()
    gains = compute_hdr_gains(slabs, weights)
    print(f"HDR gains: {[round(g, 3) for g in gains]}")
    slabs_hdr = apply_hdr(slabs, gains)
    print(f"L2 HDR: {time.time()-t0:.1f}s")

    if args.no_of:
        slabs_use, weights_use = slabs_hdr, weights
    else:
        t0 = time.time()
        slabs_use, weights_use = of_chain_warp(slabs_hdr, weights)
        print(f"L3 OF chain warp: {time.time()-t0:.1f}s")

    # ---- (a) plain hard_select (the shipped pipeline's L1) ----------------
    t0 = time.time()
    erp_plain = hard_select(slabs_use, weights_use)
    print(f"L1 plain hard_select: {time.time()-t0:.2f}s")

    # ---- (b) hard_select_with_graphcut ------------------------------------
    t0 = time.time()
    erp_gc = hard_select_with_graphcut(slabs_use, weights_use)
    print(f"L1' graph-cut hard_select: {time.time()-t0:.2f}s")

    # ---- Save full-resolution ERPs ----------------------------------------
    Image.fromarray(erp_plain).save(args.output_dir / "erp_plain.png")
    Image.fromarray(erp_gc).save(args.output_dir / "erp_graphcut.png")

    # ---- Seam overlays (red lines where argmax id changes) ----------------
    # For the plain pipeline the seam follows cos² Voronoi.
    Image.fromarray(_seam_overlay(erp_plain, weights_use)).save(
        args.output_dir / "erp_plain_seams.png"
    )

    # For the graph-cut pipeline we don't have a per-cam "weight" anymore (the
    # cut decision is binary per pair). Approximate the visualization by
    # reading back the chosen argmax id we baked into erp_gc — easy: re-run
    # hard_select_with_graphcut and capture the argmax indirectly by drawing
    # the diff vs erp_plain in red. That's a cleaner "what changed" view.
    diff_mask = (np.abs(erp_plain.astype(np.int32) - erp_gc.astype(np.int32)).sum(axis=2) > 0)
    diff_overlay = erp_gc.copy()
    diff_overlay[diff_mask] = (
        0.6 * diff_overlay[diff_mask].astype(np.float32)
        + 0.4 * np.array([255, 0, 0], dtype=np.float32)
    ).astype(np.uint8)
    Image.fromarray(diff_overlay).save(args.output_dir / "erp_graphcut_diff.png")

    # ---- Side-by-side comparison panel ------------------------------------
    H, W = args.erp_h, args.erp_w

    # Top row: full ERPs downsized to width-2000 so they fit side by side at
    # web-viewable resolution.
    top_h = 600  # row height for the full-ERP comparison
    a_top = _label(_downsize(erp_plain, top_h), "(a) hard_hdr_of  [cos² argmax]")
    b_top = _label(_downsize(erp_gc, top_h), "(b) hard_hdr_of_graphcut  [routed seam]")
    top_row = np.concatenate([a_top, b_top], axis=1)

    # Bottom row: zoomed crops at front-right seam (BMW location) and at the
    # back seam (rear_left/rear_right) where the user said the chain meets.
    # Coords are in 2048x4096 (resolution-relative — scale by H/W).
    def scale_xy(x, y):
        return int(x / 4096 * W), int(y / 2048 * H)

    # BMW front-right seam crop in 2048x4096 reference: col ~3500, row ~1100
    bmw_c = scale_xy(3500, 1100)
    bmw_crop_hw = (int(700 / 2048 * H), int(700 / 4096 * W))
    # Back seam crop in 2048x4096 ref: col 0/W (wrap region), row ~1100
    back_c = scale_xy(50, 1100)  # near col 0 = ego rear
    back_crop_hw = (int(700 / 2048 * H), int(900 / 4096 * W))

    def crop_pair(erp_a, erp_b, center, hw, label_a, label_b, zoom_h=500):
        ca = _zoom_crop(erp_a, center, hw)
        cb = _zoom_crop(erp_b, center, hw)
        ca = _label(_downsize(ca, zoom_h), label_a, fontsize=22)
        cb = _label(_downsize(cb, zoom_h), label_b, fontsize=22)
        return np.concatenate([ca, cb], axis=1)

    bmw_panel = crop_pair(
        erp_plain, erp_gc, bmw_c, bmw_crop_hw,
        "BMW seam: plain", "BMW seam: graphcut",
    )
    back_panel = crop_pair(
        erp_plain, erp_gc, back_c, back_crop_hw,
        "back seam: plain", "back seam: graphcut",
    )

    # Stack: top full row, then BMW zoom row, then back-seam zoom row.
    # Equalize widths (smaller of the zoom panels may need padding).
    target_w = top_row.shape[1]

    def pad_to_width(arr, w):
        h, cur_w, c = arr.shape
        if cur_w == w:
            return arr
        if cur_w > w:
            return arr[:, :w]
        pad_w = w - cur_w
        pad = np.zeros((h, pad_w, c), dtype=arr.dtype)
        # center-pad
        left = pad_w // 2
        return np.concatenate([pad[:, :left], arr, pad[:, left:]], axis=1)

    panel = np.concatenate(
        [
            top_row,
            pad_to_width(bmw_panel, target_w),
            pad_to_width(back_panel, target_w),
        ],
        axis=0,
    )
    out_panel = args.output_dir / "bmw_compare.png"
    Image.fromarray(panel).save(out_panel)
    print(f"\nside-by-side panel: {out_panel}")
    print(f"full ERPs       : {args.output_dir / 'erp_plain.png'}")
    print(f"                  {args.output_dir / 'erp_graphcut.png'}")
    print(f"diff overlay    : {args.output_dir / 'erp_graphcut_diff.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
