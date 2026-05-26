"""
Test root-cause parallax fix: per-overlap Farneback optical flow warp +
hard cam selection.

Hypothesis: hard_select eliminated doubled-feature ghost but exposed seam
misalignment (parallax — same 3D point lands at different ERP positions in
adjacent cams). LiDAR/DA-V2 depth failed for view-synthesis reasons, but the
two cams themselves carry the parallax info: dense optical flow between
their ERP slabs in the overlap zone IS the per-pixel parallax displacement.
Apply the flow to warp cam B to match cam A, then hard_select.

Anchor cam = front_center. Chain warp two ways:
  - CCW: front_left → front_center, side_left → front_left (warped),
         rear_left → side_left (warped)
  - CW:  front_right → front_center, side_right → front_right (warped),
         rear_right → side_right (warped)
Back seam (rear_left vs rear_right) gets whatever drift the two chains produce.

Usage:
    python scripts/phase3/test_hard_select_of.py \
        --log-dir /content/drive/MyDrive/.../02a00399-... \
        --anchor 0 --output-dir /content/drive/.../hard_select_of_anchor0
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def warp_pair_with_of(
    slab_a: np.ndarray,
    weight_a: np.ndarray,
    slab_b: np.ndarray,
    weight_b: np.ndarray,
    of_winsize: int = 31,
    of_levels: int = 4,
    of_iter: int = 5,
    smooth_sigma: float = 5.0,
    overlap_dilate_px: int = 20,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute Farneback OF in overlap of (a, b), warp slab_b + weight_b to align with a.

    Returns: (slab_b_warped, weight_b_warped, flow_used)
    """
    H, W = slab_a.shape[:2]
    overlap = (weight_a > 1e-6) & (weight_b > 1e-6)
    if overlap.sum() < 100:
        return slab_b.copy(), weight_b.copy(), np.zeros((H, W, 2), dtype=np.float32)

    ga = cv2.cvtColor(slab_a.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor(slab_b.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    ga_m = np.where(overlap, ga, 0).astype(np.uint8)
    gb_m = np.where(overlap, gb, 0).astype(np.uint8)

    flow = cv2.calcOpticalFlowFarneback(
        gb_m, ga_m,
        flow=None, pyr_scale=0.5, levels=of_levels, winsize=of_winsize,
        iterations=of_iter, poly_n=7, poly_sigma=1.5, flags=0,
    )

    # Smooth flow within overlap, zero outside (so non-overlap regions don't move)
    overlap_f = overlap.astype(np.float32)
    flow_x = flow[..., 0] * overlap_f
    flow_y = flow[..., 1] * overlap_f
    if smooth_sigma > 0:
        flow_x = cv2.GaussianBlur(flow_x, (0, 0), smooth_sigma)
        flow_y = cv2.GaussianBlur(flow_y, (0, 0), smooth_sigma)
        # normalize: divide by smoothed mask to avoid edge fade
        m_smooth = cv2.GaussianBlur(overlap_f, (0, 0), smooth_sigma)
        m_smooth = np.where(m_smooth > 1e-3, m_smooth, 1.0)
        flow_x = flow_x / m_smooth
        flow_y = flow_y / m_smooth

    # Mask to slightly-dilated overlap (so warp tapers smoothly to zero just outside)
    if overlap_dilate_px > 0:
        kernel = np.ones((overlap_dilate_px*2+1, overlap_dilate_px*2+1), np.uint8)
        overlap_dilated = cv2.dilate(overlap.astype(np.uint8), kernel).astype(bool)
    else:
        overlap_dilated = overlap
    flow_x = np.where(overlap_dilated, flow_x, 0)
    flow_y = np.where(overlap_dilated, flow_y, 0)
    flow_final = np.stack([flow_x, flow_y], axis=-1).astype(np.float32)

    u, v = np.meshgrid(np.arange(W), np.arange(H))
    map_u = (u + flow_final[..., 0]).astype(np.float32)
    map_v = (v + flow_final[..., 1]).astype(np.float32)
    slab_b_w = cv2.remap(slab_b, map_u, map_v, cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0))
    weight_b_w = cv2.remap(weight_b, map_u, map_v, cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return slab_b_w, weight_b_w, flow_final


def hard_select(slabs: list[np.ndarray], weights: list[np.ndarray]) -> np.ndarray:
    w_stack = np.stack(weights, axis=0)
    rgb_stack = np.stack(slabs, axis=0).astype(np.float32)
    argmax = w_stack.argmax(axis=0)
    valid = w_stack.max(axis=0) > 0
    idx = argmax[None, ..., None]
    picked = np.take_along_axis(rgb_stack, idx, axis=0)[0]
    return np.where(valid[..., None], picked, 0).astype(np.uint8)


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

    # cam order in RING_CAMS_7 (verify): expected
    # 0=front_center, 1=front_left, 2=side_left, 3=rear_left,
    # 4=rear_right, 5=side_right, 6=front_right
    print("RING_CAMS_7:", RING_CAMS_7)

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts_all[args.anchor])
    erp_hw = (args.erp_h, args.erp_w)

    # Project all 7 cams
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    t0 = time.time()
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(
            image=frame.images[cam], K=c.K, T_ego_cam=c.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb); weights.append(w)
    print(f"projection: {time.time()-t0:.1f}s")

    # Reference: multiband + plain hard_select (no OF)
    erp_mb = multiband_blend(slabs, weights, num_bands=5, wrap=True)
    erp_hs = hard_select(slabs, weights)

    # Chain warp using front_center (idx 0) as anchor
    # CCW chain: 0 -> 1 -> 2 -> 3
    # CW  chain: 0 -> 6 -> 5 -> 4
    ccw_order = [0, 1, 2, 3]
    cw_order  = [0, 6, 5, 4]

    warped_slabs = [None] * 7
    warped_weights = [None] * 7
    warped_slabs[0] = slabs[0]; warped_weights[0] = weights[0]

    t0 = time.time()
    for i in range(1, len(ccw_order)):
        prev = ccw_order[i-1]; cur = ccw_order[i]
        sw, ww, _ = warp_pair_with_of(
            warped_slabs[prev], warped_weights[prev], slabs[cur], weights[cur]
        )
        warped_slabs[cur] = sw; warped_weights[cur] = ww
        print(f"  CCW {RING_CAMS_7[cur]:<18s} -> {RING_CAMS_7[prev]:<18s} aligned")

    for i in range(1, len(cw_order)):
        prev = cw_order[i-1]; cur = cw_order[i]
        sw, ww, _ = warp_pair_with_of(
            warped_slabs[prev], warped_weights[prev], slabs[cur], weights[cur]
        )
        warped_slabs[cur] = sw; warped_weights[cur] = ww
        print(f"  CW  {RING_CAMS_7[cur]:<18s} -> {RING_CAMS_7[prev]:<18s} aligned")
    print(f"chain warp: {time.time()-t0:.1f}s")

    # Hard select on warped slabs
    erp_hs_of = hard_select(warped_slabs, warped_weights)

    # Save 3 ERPs
    Image.fromarray(erp_mb).save(args.output_dir / "erp_multiband.png")
    Image.fromarray(erp_hs).save(args.output_dir / "erp_hard.png")
    Image.fromarray(erp_hs_of).save(args.output_dir / "erp_hard_of.png")

    # Zoom crops
    H, W = args.erp_h, args.erp_w
    def crop(arr, col_c, row_t, row_b, half):
        col_l = max(0, col_c - half); col_r = min(W, col_c + half)
        return arr[row_t:row_b, col_l:col_r]

    bmw_c = int(3500/4096 * W); bmw_t = int(900/2048 * H); bmw_b = int(1300/2048 * H); bmw_hw = int(250/4096 * W)
    por_c = int(1500/4096 * W); por_t = int(1000/2048 * H); por_b = int(1300/2048 * H); por_hw = int(300/4096 * W)

    def label(arr, text):
        pil = Image.fromarray(arr.copy()); draw = ImageDraw.Draw(pil)
        try: f = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        except Exception: f = ImageFont.load_default()
        for dx, dy in [(-2,0),(2,0),(0,-2),(0,2)]:
            draw.text((6+dx, 4+dy), text, font=f, fill="black")
        draw.text((6, 4), text, font=f, fill="white")
        return np.array(pil)

    # Triple-panel for each zoom: multiband | hard | hard+OF
    def triple_panel(arr1, arr2, arr3, labels):
        a1 = label(arr1, labels[0]); a2 = label(arr2, labels[1]); a3 = label(arr3, labels[2])
        return np.concatenate([a1, a2, a3], axis=1)

    bmw_panel = triple_panel(
        crop(erp_mb, bmw_c, bmw_t, bmw_b, bmw_hw),
        crop(erp_hs, bmw_c, bmw_t, bmw_b, bmw_hw),
        crop(erp_hs_of, bmw_c, bmw_t, bmw_b, bmw_hw),
        ["MULTIBAND", "HARD SELECT", "HARD SELECT + OF"]
    )
    Image.fromarray(bmw_panel).save(args.output_dir / "bmw_triple.png")

    porsche_panel = triple_panel(
        crop(erp_mb, por_c, por_t, por_b, por_hw),
        crop(erp_hs, por_c, por_t, por_b, por_hw),
        crop(erp_hs_of, por_c, por_t, por_b, por_hw),
        ["MULTIBAND", "HARD SELECT", "HARD SELECT + OF"]
    )
    Image.fromarray(porsche_panel).save(args.output_dir / "porsche_triple.png")

    # Full ERP 3-way stack
    def downsize(a, h=512):
        w = int(a.shape[1] * h / a.shape[0])
        return np.array(Image.fromarray(a).resize((w, h), Image.LANCZOS))

    full_panel = np.concatenate([
        label(downsize(erp_mb), "MULTIBAND BLEND"),
        label(downsize(erp_hs), "HARD SELECT"),
        label(downsize(erp_hs_of), "HARD SELECT + OF"),
    ], axis=0)
    Image.fromarray(full_panel).save(args.output_dir / "full_triple.png")

    print(f"saved 5 PNGs to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
