"""Post-hoc brighten on Xihan's pre-stitched Waymo panorama.

Goal:  Xihan said "用 blending 的色階把有陰影的 camera picture brighten".
       Apply Y-channel (luminance-only) joint global gain across the 8 detected
       cam regions so neighbor seams stop jumping.

Approach mirrors L2 of `code/waymo2panorama/blending/hard_hdr_of.py` but works
on an ALREADY-STITCHED panorama (no per-cam slabs available):

  1) Detect 7 vertical seams via |dY/dx| spikes (same as diagnose script).
  2) For each seam, measure median Y in 24-px strips on each side
     (and skip the top/bottom black pano regions).
  3) Joint lstsq for per-region log-gain G_i:
        G_{i+1} - G_i = log(Y_left_at_seam_i / Y_right_at_seam_i)
     with cyclic wrap (region 7 -> region 0) constraint and
     mean(G_i) = 0  (centered, don't change global brightness).
  4) Apply per-region gain on Y channel of YCrCb, keep Cr/Cb untouched
     (preserves hue).
  5) Cross-region taper: blend gain over ~32-px on each side of each seam
     to avoid a new sharp step at the seam line.

Also provide a CLAHE-only baseline (`--mode clahe`) for direct comparison.

Output:
  deliverables/xihan/brighten_waymo_jointhdr.png   (proposed)
  deliverables/xihan/brighten_waymo_clahe.png     (baseline reference)
  deliverables/xihan/brighten_waymo_4way.png      (raw / clahe / jointhdr / jointhdr+clahe)
  deliverables/xihan/brighten_waymo.json
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
PANO = (REPO / "meeting" / "5.22_meeting with xihan" / "xihan" / "assets"
        / "xihan task" / "c4b1d01f8f6616e59a2d203b879db1d3.jpg")
OUT_DIR = REPO / "deliverables" / "xihan"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------------
# Reused from diagnose script (kept local to keep this a single-file deliverable).
# ----------------------------------------------------------------------------
def find_vertical_seams(rgb, band_top=0.30, band_bot=0.55,
                       smooth=21, n_seams=7, min_sep_frac=0.04):
    H, W = rgb.shape[:2]
    Y = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)
    r0, r1 = int(H * band_top), int(H * band_bot)
    band = Y[r0:r1, :]
    abs_sob = np.abs(cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3))
    col = np.median(abs_sob, axis=0)
    if smooth > 1:
        col = np.convolve(col, np.ones(smooth) / smooth, mode="same")
    edge_pad = int(W * 0.04)
    col[:edge_pad] = 0
    col[-edge_pad:] = 0
    min_sep = max(1, int(W * min_sep_frac))
    picked = []
    for c in np.argsort(col)[::-1]:
        if col[c] <= 0 or len(picked) >= n_seams:
            break
        if all(abs(int(c) - p) >= min_sep for p in picked):
            picked.append(int(c))
    return sorted(picked)


# ----------------------------------------------------------------------------
# Joint-HDR fitting
# ----------------------------------------------------------------------------
def seam_y_pair(Y: np.ndarray, x_seam: int, strip: int = 24,
                r_top_frac: float = 0.30, r_bot_frac: float = 0.55) -> tuple[float, float]:
    """Median Y in a `strip`-px window on each side of x_seam, in the central
    horizontal band (avoids top/bottom alpha holes)."""
    H, W = Y.shape
    r0, r1 = int(H * r_top_frac), int(H * r_bot_frac)
    xL0, xL1 = max(0, x_seam - strip), x_seam
    xR0, xR1 = x_seam, min(W, x_seam + strip)
    left = Y[r0:r1, xL0:xL1]
    right = Y[r0:r1, xR0:xR1]
    return float(np.median(left)), float(np.median(right))


def fit_region_gains(Y: np.ndarray, seams: list[int],
                     reg_lambda: float = 0.15,
                     gain_clip: tuple[float, float] = (0.75, 1.35)) -> np.ndarray:
    """Return log-gains G of shape (N_regions,) with mean(G)=0 such that
    G_{i+1} - G_i ≈ log(Y_left_at_seam_i / Y_right_at_seam_i)
    and the back-seam (region N-1 to region 0) is included for cyclic closure.
    Adds a small Tikhonov term reg_lambda * G_i ≈ 0 for stability.
    """
    H, W = Y.shape
    n = len(seams) + 1  # number of regions
    eq_A: list[list[float]] = []
    eq_b: list[float] = []

    # Interior seams: constrain neighbor pair
    for i, x in enumerate(seams):
        yL, yR = seam_y_pair(Y, x)
        if yL <= 0 or yR <= 0:
            continue
        # gain on each side: g_i * yL  ≈  g_{i+1} * yR
        # log:  G_i + log(yL)  =  G_{i+1} + log(yR)
        # =>    G_{i+1} - G_i  =  log(yL) - log(yR)
        row = [0.0] * n
        row[i] = -1.0
        row[i + 1] = 1.0
        eq_A.append(row)
        eq_b.append(float(np.log(yL) - np.log(yR)))

    # Back-seam (wrap): the panorama theta=±π crosses at x=0 / x=W.
    # If the leftmost and rightmost columns are both content (not alpha holes),
    # constrain region N-1 -> region 0 similarly. Otherwise skip — Xihan's pano
    # has alpha holes at the edges (distance-to-boundary blending), so this is
    # usually skipped automatically.
    Y_strip = Y[int(H * 0.30):int(H * 0.55), :]
    leftmost = Y_strip[:, :24]
    rightmost = Y_strip[:, -24:]
    if (leftmost >= 8).mean() > 0.5 and (rightmost >= 8).mean() > 0.5:
        yL = float(np.median(rightmost[rightmost >= 8]))
        yR = float(np.median(leftmost[leftmost >= 8]))
        row = [0.0] * n
        row[n - 1] = -1.0
        row[0] = 1.0
        eq_A.append(row)
        eq_b.append(float(np.log(yL) - np.log(yR)))

    # Tikhonov regularization toward 0
    for i in range(n):
        row = [0.0] * n
        row[i] = reg_lambda
        eq_A.append(row)
        eq_b.append(0.0)

    A = np.array(eq_A, dtype=np.float64)
    b = np.array(eq_b, dtype=np.float64)

    # Solve, then center (subtract mean) so we don't change global brightness
    G_raw, *_ = np.linalg.lstsq(A, b, rcond=None)
    G = G_raw - G_raw.mean()
    # Clip per-region gain to a sane range. Edge regions of Xihan's panorama
    # have alpha-hole borders where the few-pixels-wide content strip can
    # produce extreme gains; clipping prevents over-brightening those slivers.
    g_lo, g_hi = np.log(gain_clip[0]), np.log(gain_clip[1])
    G = np.clip(G, g_lo, g_hi)
    # Re-center after clipping
    G = G - G.mean()
    return G


def build_gain_map(G_per_region: np.ndarray, seams: list[int],
                   W: int, taper_halfwidth: int = 48) -> np.ndarray:
    """Convert per-region log-gains into a per-column gain map of shape (W,),
    with smooth taper (~taper_halfwidth pixels) at each seam to avoid creating
    a NEW step at the seam itself.
    """
    n = len(G_per_region)
    bounds = [0] + list(seams) + [W]
    # piecewise-constant base
    base = np.zeros(W, dtype=np.float64)
    for i in range(n):
        base[bounds[i]:bounds[i + 1]] = np.exp(G_per_region[i])
    # taper across each interior seam: linear blend over ±taper_halfwidth
    out = base.copy()
    for i, x in enumerate(seams):
        gL = float(np.exp(G_per_region[i]))
        gR = float(np.exp(G_per_region[i + 1]))
        for off in range(-taper_halfwidth, taper_halfwidth + 1):
            col = x + off
            if col < 0 or col >= W:
                continue
            t = (off + taper_halfwidth) / (2.0 * taper_halfwidth)  # 0..1
            out[col] = (1.0 - t) * gL + t * gR
    return out


def apply_y_gain_map(rgb: np.ndarray, gain_map_w: np.ndarray) -> np.ndarray:
    """Apply per-column Y-channel gain. Preserves Cr/Cb (no chroma shift)."""
    ycc = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb).astype(np.float32)
    Y = ycc[..., 0]
    # only scale non-alpha pixels (Y >= 8 = real content)
    gmap = gain_map_w[None, :].astype(np.float32)
    Y_new = np.where(Y >= 8, np.clip(Y * gmap, 0, 255), Y)
    ycc[..., 0] = Y_new
    return cv2.cvtColor(ycc.astype(np.uint8), cv2.COLOR_YCrCb2RGB)


def apply_clahe(rgb: np.ndarray, clip: float = 2.0, grid: int = 16) -> np.ndarray:
    """CLAHE-only baseline on Y channel."""
    ycc = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    Y = ycc[..., 0]
    # mask out alpha holes — don't equalize them
    mask = Y >= 8
    Y_valid = Y * mask + Y.mean() * (~mask)  # placeholder for invalid pixels
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid))
    Y_eq = clahe.apply(Y_valid.astype(np.uint8))
    Y_out = np.where(mask, Y_eq, Y)
    ycc[..., 0] = Y_out
    return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB)


def measure_seam_jumps(rgb: np.ndarray, seams: list[int]) -> dict:
    """Mean and max |delta Y| at the 7 seams (after brighten, ideally near 0)."""
    Y = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)
    diffs = []
    for x in seams:
        yL, yR = seam_y_pair(Y, x)
        diffs.append(abs(yR - yL))
    return {"per_seam": diffs, "mean": float(np.mean(diffs)),
            "max": float(np.max(diffs))}


def main() -> None:
    bgr = cv2.imread(str(PANO))
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    H, W = rgb.shape[:2]
    print(f"loaded panorama {W}x{H}")

    seams = find_vertical_seams(rgb)
    print(f"detected {len(seams)} seams at x = {seams}")

    Y = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)
    G = fit_region_gains(Y, seams)
    gains = np.exp(G)
    print(f"per-region gains: {gains.round(3).tolist()}  (mean log={float(G.mean()):+.3f})")

    gain_map = build_gain_map(G, seams, W, taper_halfwidth=48)
    out_jointhdr = apply_y_gain_map(rgb, gain_map)
    out_clahe = apply_clahe(rgb)
    out_both = apply_clahe(out_jointhdr)

    # seam-jump metrics
    jumps_raw = measure_seam_jumps(rgb, seams)
    jumps_hdr = measure_seam_jumps(out_jointhdr, seams)
    jumps_clahe = measure_seam_jumps(out_clahe, seams)
    jumps_both = measure_seam_jumps(out_both, seams)

    print(f"  raw     mean|dY|={jumps_raw['mean']:.2f}  max={jumps_raw['max']:.2f}")
    print(f"  clahe   mean|dY|={jumps_clahe['mean']:.2f}  max={jumps_clahe['max']:.2f}")
    print(f"  jointhdr mean|dY|={jumps_hdr['mean']:.2f}  max={jumps_hdr['max']:.2f}")
    print(f"  both    mean|dY|={jumps_both['mean']:.2f}  max={jumps_both['max']:.2f}")

    # Save individual images
    cv2.imwrite(str(OUT_DIR / "brighten_waymo_jointhdr.png"),
                cv2.cvtColor(out_jointhdr, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(OUT_DIR / "brighten_waymo_clahe.png"),
                cv2.cvtColor(out_clahe, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(OUT_DIR / "brighten_waymo_jointhdr_plus_clahe.png"),
                cv2.cvtColor(out_both, cv2.COLOR_RGB2BGR))

    # 4-way stacked comparison (raw / clahe / jointhdr / both)
    def annotate(img, txt):
        bar = np.zeros((42, img.shape[1], 3), dtype=np.uint8)
        cv2.putText(bar, txt, (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
                    (255, 255, 255), 2, cv2.LINE_AA)
        return np.concatenate([bar, img], axis=0)

    stack = np.concatenate([
        annotate(rgb, "raw  (Xihan distance-to-boundary, Y jumps mean={:.1f})".format(jumps_raw['mean'])),
        annotate(out_clahe, "CLAHE baseline                 (Y jumps mean={:.1f})".format(jumps_clahe['mean'])),
        annotate(out_jointhdr, "joint-HDR per-region gain     (Y jumps mean={:.1f})".format(jumps_hdr['mean'])),
        annotate(out_both, "joint-HDR + CLAHE              (Y jumps mean={:.1f})".format(jumps_both['mean'])),
    ], axis=0)
    cv2.imwrite(str(OUT_DIR / "brighten_waymo_4way.png"),
                cv2.cvtColor(stack, cv2.COLOR_RGB2BGR))

    summary = {
        "pano": str(PANO.relative_to(REPO)),
        "size_wh": [W, H],
        "seams_x": seams,
        "per_region_gain": gains.tolist(),
        "per_region_log_gain": G.tolist(),
        "seam_jumps": {
            "raw": jumps_raw,
            "clahe": jumps_clahe,
            "jointhdr": jumps_hdr,
            "jointhdr_plus_clahe": jumps_both,
        },
    }
    (OUT_DIR / "brighten_waymo.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(f"wrote 4 PNGs and 1 JSON to {OUT_DIR.relative_to(REPO)}")


if __name__ == "__main__":
    main()
