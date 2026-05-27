"""
Full 4-way panorama comparison:
  1. multiband (baseline — blended ghosts)
  2. hard_select (kills doubled-feature ghost)
  3. hard_select + HDR per-cam exposure compensation (kills brightness step)
  4. hard_select + HDR + OF chain warp (kills brightness + position step)

HDR algorithm: per-channel gain, anchor = front_center (gain=1.0), chain
through adjacency pairs by overlap-mean ratio:
  gain[cur] = gain[prev] * mean(prev in overlap) / mean(cur in overlap)

OF algorithm: Farneback dense optical flow in overlap zone, warp cam B
to align with cam A, chain from front_center as anchor.

Usage:
    python scripts/phase3/test_hard_select_hdr_of.py \
        --log-dir /content/drive/.../02a00399-... --anchor 0 \
        --output-dir /content/drive/.../hard_select_hdr_of_anchor0
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


# Cam adjacency in RING_CAMS_7 ordering
# 0=front_center, 1=front_left, 2=side_left, 3=rear_left,
# 4=rear_right, 5=side_right, 6=front_right
CCW = [0, 1, 2, 3]   # left half: front_center → front_left → side_left → rear_left
CW  = [0, 6, 5, 4]   # right half: front_center → front_right → side_right → rear_right


def overlap_mean_rgb(
    slab_a: np.ndarray, weight_a: np.ndarray,
    slab_b: np.ndarray, weight_b: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (mean_RGB_A_in_overlap, mean_RGB_B_in_overlap), 3-vectors each."""
    overlap = (weight_a > 1e-6) & (weight_b > 1e-6)
    n = int(overlap.sum())
    if n < 100:
        return np.array([1.0, 1.0, 1.0]), np.array([1.0, 1.0, 1.0])
    mean_a = slab_a[overlap].astype(np.float64).mean(axis=0)
    mean_b = slab_b[overlap].astype(np.float64).mean(axis=0)
    return mean_a, mean_b


# All adjacent pairs in the 7-cam ring (including back seam between cam 3 & 4)
RING_PAIRS = [
    (0, 1), (1, 2), (2, 3),   # CCW: front_center → left half
    (0, 6), (6, 5), (5, 4),   # CW: front_center → right half
    (3, 4),                   # back seam — closes the loop, prevents drift
]


def compute_hdr_gains(
    slabs: list[np.ndarray], weights: list[np.ndarray],
) -> list[float]:
    """Joint global least-squares solve for per-cam luminance gain.

    For each adjacent pair (i, j), enforce in log space:
      log(g_i) + log(mean_Y_i) = log(g_j) + log(mean_Y_j)
    i.e. matched brightness in overlap. Anchor: log(g_0) = 0.
    Solve over all 7 ring pairs (including back seam) via lstsq — no chain
    drift because the back-seam constraint closes the loop.

    Returns 7 scalar gains. gains[0] = 1.0 (anchor=front_center).
    """
    n = len(slabs)
    A_rows: list[np.ndarray] = []
    b_rows: list[float] = []
    for (i, j) in RING_PAIRS:
        overlap = (weights[i] > 1e-6) & (weights[j] > 1e-6)
        if int(overlap.sum()) < 100:
            continue
        y_i = cv2.cvtColor(slabs[i].astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]
        y_j = cv2.cvtColor(slabs[j].astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]
        m_i = max(float(y_i[overlap].mean()), 1.0)
        m_j = max(float(y_j[overlap].mean()), 1.0)
        # log(g_i) - log(g_j) = log(m_j) - log(m_i)
        row = np.zeros(n)
        row[i] = 1.0; row[j] = -1.0
        A_rows.append(row)
        b_rows.append(np.log(m_j) - np.log(m_i))
    # Anchor: log(g_0) = 0
    anchor = np.zeros(n); anchor[0] = 1.0
    A_rows.append(anchor); b_rows.append(0.0)

    A = np.array(A_rows); b = np.array(b_rows)
    log_g, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    gains = np.exp(log_g)
    gains = np.clip(gains, 0.5, 2.0)
    return list(gains)


def apply_hdr(slabs: list[np.ndarray], gains: list[float]) -> list[np.ndarray]:
    """Multiply Y channel by gain (preserves chroma), convert back to RGB."""
    out = []
    for s, g in zip(slabs, gains):
        ycrcb = cv2.cvtColor(s.astype(np.uint8), cv2.COLOR_RGB2YCrCb).astype(np.float32)
        ycrcb[..., 0] = np.clip(ycrcb[..., 0] * g, 0, 255)
        rgb = cv2.cvtColor(ycrcb.astype(np.uint8), cv2.COLOR_YCrCb2RGB)
        out.append(rgb.astype(np.float32))
    return out


def warp_pair_with_of(
    slab_a: np.ndarray, weight_a: np.ndarray,
    slab_b: np.ndarray, weight_b: np.ndarray,
    of_winsize: int = 31, of_levels: int = 4, of_iter: int = 5,
    smooth_sigma: float = 5.0, overlap_dilate_px: int = 20,
) -> tuple[np.ndarray, np.ndarray]:
    H, W = slab_a.shape[:2]
    overlap = (weight_a > 1e-6) & (weight_b > 1e-6)
    if overlap.sum() < 100:
        return slab_b.copy(), weight_b.copy()

    ga = cv2.cvtColor(slab_a.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor(slab_b.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    ga_m = np.where(overlap, ga, 0).astype(np.uint8)
    gb_m = np.where(overlap, gb, 0).astype(np.uint8)

    flow = cv2.calcOpticalFlowFarneback(
        gb_m, ga_m, flow=None,
        pyr_scale=0.5, levels=of_levels, winsize=of_winsize,
        iterations=of_iter, poly_n=7, poly_sigma=1.5, flags=0,
    )

    overlap_f = overlap.astype(np.float32)
    flow_x = flow[..., 0] * overlap_f
    flow_y = flow[..., 1] * overlap_f
    if smooth_sigma > 0:
        flow_x = cv2.GaussianBlur(flow_x, (0, 0), smooth_sigma)
        flow_y = cv2.GaussianBlur(flow_y, (0, 0), smooth_sigma)
        m_smooth = cv2.GaussianBlur(overlap_f, (0, 0), smooth_sigma)
        m_smooth = np.where(m_smooth > 1e-3, m_smooth, 1.0)
        flow_x = flow_x / m_smooth
        flow_y = flow_y / m_smooth

    if overlap_dilate_px > 0:
        k = np.ones((overlap_dilate_px*2+1, overlap_dilate_px*2+1), np.uint8)
        overlap_d = cv2.dilate(overlap.astype(np.uint8), k).astype(bool)
    else:
        overlap_d = overlap
    flow_x = np.where(overlap_d, flow_x, 0)
    flow_y = np.where(overlap_d, flow_y, 0)

    u, v = np.meshgrid(np.arange(W), np.arange(H))
    map_u = (u + flow_x).astype(np.float32)
    map_v = (v + flow_y).astype(np.float32)
    sw = cv2.remap(slab_b, map_u, map_v, cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0))
    ww = cv2.remap(weight_b, map_u, map_v, cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return sw, ww


def of_chain_warp(slabs: list[np.ndarray], weights: list[np.ndarray]):
    n = len(slabs)
    warped_s = [None] * n; warped_w = [None] * n
    warped_s[0] = slabs[0]; warped_w[0] = weights[0]
    for chain in [CCW, CW]:
        for i in range(1, len(chain)):
            prev = chain[i-1]; cur = chain[i]
            sw, ww = warp_pair_with_of(
                warped_s[prev], warped_w[prev], slabs[cur], weights[cur]
            )
            warped_s[cur] = sw; warped_w[cur] = ww
    return warped_s, warped_w


def hard_select(slabs, weights):
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

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts_all[args.anchor])
    erp_hw = (args.erp_h, args.erp_w)

    slabs, weights = [], []
    t0 = time.time()
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(
            image=frame.images[cam], K=c.K, T_ego_cam=c.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb); weights.append(w)
    print(f"projection: {time.time()-t0:.1f}s")

    # Stage 1: multiband baseline
    erp_mb = multiband_blend(slabs, weights, num_bands=5, wrap=True)

    # Stage 2: plain hard_select
    erp_hs = hard_select(slabs, weights)

    # Stage 3: HDR (no OF) + hard_select
    t0 = time.time()
    gains = compute_hdr_gains(slabs, weights)
    print("HDR luminance gains:")
    for cam, g in zip(RING_CAMS_7, gains):
        print(f"  {cam:<18s}: {g:.3f}x")
    slabs_hdr = apply_hdr(slabs, gains)
    erp_hs_hdr = hard_select(slabs_hdr, weights)
    print(f"HDR: {time.time()-t0:.1f}s")

    # Stage 4: HDR + OF + hard_select
    t0 = time.time()
    slabs_hdr_warp, weights_warp = of_chain_warp(slabs_hdr, weights)
    erp_hs_hdr_of = hard_select(slabs_hdr_warp, weights_warp)
    print(f"OF chain warp: {time.time()-t0:.1f}s")

    # Save full ERPs
    Image.fromarray(erp_mb).save(args.output_dir / "erp_multiband.png")
    Image.fromarray(erp_hs).save(args.output_dir / "erp_hs.png")
    Image.fromarray(erp_hs_hdr).save(args.output_dir / "erp_hs_hdr.png")
    Image.fromarray(erp_hs_hdr_of).save(args.output_dir / "erp_hs_hdr_of.png")

    # Zoom panels
    H, W = args.erp_h, args.erp_w
    def crop(arr, col_c, row_t, row_b, half):
        col_l = max(0, col_c - half); col_r = min(W, col_c + half)
        return arr[row_t:row_b, col_l:col_r]

    bmw = (int(3500/4096 * W), int(900/2048 * H), int(1300/2048 * H), int(250/4096 * W))
    por = (int(1500/4096 * W), int(1000/2048 * H), int(1300/2048 * H), int(300/4096 * W))

    def label(arr, text, fontsize=22):
        pil = Image.fromarray(arr.copy()); draw = ImageDraw.Draw(pil)
        try: f = ImageFont.truetype("DejaVuSans-Bold.ttf", fontsize)
        except Exception: f = ImageFont.load_default()
        for dx, dy in [(-2,0),(2,0),(0,-2),(0,2)]:
            draw.text((6+dx, 4+dy), text, font=f, fill="black")
        draw.text((6, 4), text, font=f, fill="white")
        return np.array(pil)

    def fourway_panel(erps, crop_args, labels):
        cols = [label(crop(e, *crop_args), l) for e, l in zip(erps, labels)]
        return np.concatenate(cols, axis=1)

    labels = ["MULTIBAND", "HARD SELECT", "HS+HDR", "HS+HDR+OF"]
    erps = [erp_mb, erp_hs, erp_hs_hdr, erp_hs_hdr_of]
    Image.fromarray(fourway_panel(erps, bmw, labels)).save(args.output_dir / "bmw_4way.png")
    Image.fromarray(fourway_panel(erps, por, labels)).save(args.output_dir / "porsche_4way.png")

    # Full ERP 4-way stacked
    def downsize(a, h=512):
        w = int(a.shape[1] * h / a.shape[0])
        return np.array(Image.fromarray(a).resize((w, h), Image.LANCZOS))
    full_panel = np.concatenate([
        label(downsize(e), l, fontsize=18) for e, l in zip(erps, labels)
    ], axis=0)
    Image.fromarray(full_panel).save(args.output_dir / "full_4way.png")

    print(f"saved 4 ERPs + 3 panels to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
