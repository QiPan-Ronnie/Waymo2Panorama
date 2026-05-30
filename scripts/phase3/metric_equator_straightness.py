"""
Equator-row / horizon straightness metric for ERP panoramas (HSW: Horizon
Straightness Waviness).

GEOMETRIC PRINCIPLE
-------------------
Under ANY correct rigid spherical (equirectangular) projection from a single
optical center:
  - the horizon (eye-level points at infinity) maps to the straight horizontal
    row at the equator (phi = 0, i.e. v = H/2);
  - any far-field horizontal structure that lies near eye-level (distant
    rooflines, the far ground/skyline, building eaves, overpass edges) maps to
    a horizontal ERP curve whose row v(u) is a SMOOTH, SLOWLY-VARYING function
    of column u (a great-circle arc projected to ERP). For purely horizontal
    far structure at small elevation it is a near-flat line.

A single-center re-render (3DGS) that WARPS the scene ("buildings wave, ground
curves") breaks this: a near-eye-level horizontal edge no longer maps to a
flat / smoothly-curving row but acquires high-frequency vertical wobble -- the
extracted edge-row trace v(u) develops curvature/oscillation that a rigid
projection mathematically cannot produce. We measure exactly that wobble.

ERP CONVENTION (matches code/waymo2panorama/projection/sphere_projection.py):
    H,W = 1024,2048 (2:1). origin top-left.
    phi = pi/2 - (v+0.5)/H * pi   -> equator phi=0 at v = H/2 - 0.5  ~  H/2.
    elevation is LINEAR in row: dv/dphi = -H/pi, so a global pitch error of
    delta_phi radians shifts the equator by delta_phi * H/pi rows
    (1 deg ~ H/180 ~ 5.7 px at H=1024).

This module provides:
  (A) hsw_absolute(erp)            -- no-reference absolute score.
  (B) hsw_relative(erp, erp_ref)   -- difference vs the clean L1 anchor.

Both return LOW for the clean rigid L1 and HIGH for the warped 3DGS.

Deps: numpy, opencv-python (cv2). torch not required.
"""
from __future__ import annotations

import numpy as np
import cv2


# ----------------------------------------------------------------------------
# Core: extract near-equator horizontal-edge row traces and measure waviness.
# ----------------------------------------------------------------------------
def _equator_band(h: int, band_frac: float = 0.18) -> tuple[int, int]:
    """Row range [v0, v1) of the analysis band centered on the equator.

    band_frac=0.18 -> +-9% of H above/below equator ~ +-16 deg elevation.
    Distant horizontal structure (skyline, far rooflines, overpass edges,
    far ground horizon) lives here; near-field clutter mostly does not.
    """
    v_eq = h / 2.0
    half = band_frac * h / 2.0
    return int(round(v_eq - half)), int(round(v_eq + half))


def extract_horizontal_edge_rows(
    erp: np.ndarray,
    band_frac: float = 0.18,
    grad_percentile: float = 92.0,
    min_run_cols: int = 24,
) -> list[np.ndarray]:
    """Trace near-horizontal far-field edges inside the equator band.

    Returns a list of per-column row-position traces. Each trace is a float
    array of length W; entries are NaN where no horizontal edge is present in
    that column. Each trace is one connected near-horizontal structure.

    Method (LSD-based, the primary path):
      1. grayscale, light blur.
      2. cv2 Line Segment Detector (cv2.createLineSegmentDetector). LSD is
         parameter-light and sub-pixel; it returns straight segments. A warped
         building edge fragments into MANY short tilted/curved sub-segments
         whose endpoints, when binned per column, trace a wavy v(u); a rigid
         edge yields one long flat segment.
      3. keep segments that are (a) near-horizontal (|slope| < 0.35, i.e.
         < ~19 deg) and (b) overlap the equator band.
      4. rasterize each kept segment into a per-column v(u) trace; merge
         column-overlapping near-collinear traces.
    """
    if erp.ndim == 3:
        gray = cv2.cvtColor(erp, cv2.COLOR_RGB2GRAY)
    else:
        gray = erp
    gray = gray.astype(np.uint8)
    h, w = gray.shape
    v0, v1 = _equator_band(h, band_frac)

    gray_b = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.0)

    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    lines = lsd.detect(gray_b)[0]
    traces: list[np.ndarray] = []
    if lines is None:
        return traces

    for ln in lines:
        x1, y1, x2, y2 = ln[0]
        if x2 == x1:
            continue
        slope = (y2 - y1) / (x2 - x1)
        if abs(slope) > 0.35:                       # not near-horizontal
            continue
        ymid = 0.5 * (y1 + y2)
        if not (v0 <= ymid <= v1):                  # not in equator band
            continue
        ca, cb = sorted((int(round(x1)), int(round(x2))))
        if cb - ca < min_run_cols:
            continue
        trace = np.full(w, np.nan, dtype=np.float64)
        cols = np.arange(max(0, ca), min(w, cb + 1))
        trace[cols] = y1 + slope * (cols - x1)
        traces.append(trace)
    return traces


def trace_waviness(trace: np.ndarray, detrend_arc: bool = True) -> float | None:
    """RMS high-frequency vertical deviation of one edge-row trace, in pixels.

    A rigid great-circle arc, after removing a smooth low-order (quadratic)
    fit, leaves ~0 residual. A warped edge leaves a non-zero residual: the
    "waviness".

    Returns RMS residual in pixels, or None if the trace is too short.
    """
    cols = np.where(~np.isnan(trace))[0]
    if cols.size < 32:
        return None
    v = trace[cols]
    if detrend_arc:
        # Degree-2 fit absorbs the legitimate smooth great-circle curvature and
        # any residual roll/pitch; whatever remains is non-rigid wobble.
        coef = np.polyfit(cols.astype(np.float64), v, deg=2)
        resid = v - np.polyval(coef, cols)
    else:
        resid = v - v.mean()
    return float(np.sqrt(np.mean(resid ** 2)))


def hsw_absolute(
    erp: np.ndarray,
    band_frac: float = 0.18,
    win_cols: int = 129,
) -> dict:
    """No-reference absolute Horizon Straightness Waviness.

    The per-column dominant-edge trace v(u) is the right primitive (NOT
    per-LSD-segment fits, which hide the wobble inside short locally-straight
    chunks). A rigid great-circle arc is GLOBALLY smooth: a sliding degree-2
    fit predicts it to sub-pixel everywhere. A warped edge has high-frequency
    column-to-column wobble the smooth model cannot follow.

    Score = RMS of (v(u) - locally-smooth-fit(v)(u)) over valid columns, in px.
    The local smooth fit is a sliding-window (win_cols) degree-2 detrend so the
    legitimate great-circle curvature and any global roll/pitch are removed but
    short-wavelength warp wobble survives. LOW = clean (rigid), HIGH = warped.
    """
    v = _dominant_edge_row_per_col(erp, band_frac)          # length-W, NaN gaps
    valid = ~np.isnan(v)
    n_valid = int(valid.sum())
    if n_valid < 64:
        return {"hsw": float("nan"), "n_cols": n_valid,
                "hsw_p90": float("nan"), "equator_offset_px": float("nan")}

    cols = np.arange(v.size, dtype=np.float64)
    vc = np.where(valid, v, np.nan)
    # Sliding-window degree-2 detrend implemented as: smooth = Savitzky-Golay
    # (poly-2) on the gap-interpolated trace; residual outside gaps is the warp.
    vi = vc.copy()
    vi[~valid] = np.interp(cols[~valid], cols[valid], vc[valid])
    half = win_cols // 2
    # local quadratic smoother via cv2 boxfilter of poly basis is overkill;
    # use scipy if present, else a simple moving-average baseline (poly-0).
    try:
        from scipy.signal import savgol_filter
        wl = win_cols if win_cols % 2 == 1 else win_cols + 1
        wl = min(wl, (vi.size // 2) * 2 - 1)
        smooth = savgol_filter(vi, window_length=max(wl, 5), polyorder=2,
                               mode="interp")
    except Exception:
        k = np.ones(win_cols) / win_cols
        smooth = np.convolve(vi, k, mode="same")
    resid = (vi - smooth)[valid]
    hsw = float(np.sqrt(np.mean(resid ** 2)))
    hsw_p90 = float(np.percentile(np.abs(resid), 90))
    equator_offset = float(np.nanmedian(vc) - erp.shape[0] / 2.0)
    return {
        "hsw": hsw,                       # primary absolute score (px)
        "hsw_p90": hsw_p90,
        "n_cols": n_valid,
        "equator_offset_px": equator_offset,
    }


def hsw_relative(erp: np.ndarray, erp_ref: np.ndarray,
                 band_frac: float = 0.18) -> dict:
    """Relative HSW vs the clean L1 anchor.

    Per matched column u in the equator band, compare the row of the strongest
    horizontal edge in `erp` vs in `erp_ref`, after independently detrending
    each by a degree-2 fit. The RMS of (delta-detrended) row difference is the
    excess waviness introduced by `erp`. Because the far field of a good
    seam-confined method is byte-identical to L1, this score is ~0 for it and
    only fires where the candidate actually bent a far edge. LOW = clean.
    """
    a = _dominant_edge_row_per_col(erp, band_frac)
    b = _dominant_edge_row_per_col(erp_ref, band_frac)
    valid = ~np.isnan(a) & ~np.isnan(b)
    if valid.sum() < 64:
        return {"hsw_rel": float("nan"), "n_cols": int(valid.sum())}
    cols = np.where(valid)[0].astype(np.float64)
    da = a[valid] - np.polyval(np.polyfit(cols, a[valid], 2), cols)
    db = b[valid] - np.polyval(np.polyfit(cols, b[valid], 2), cols)
    diff = da - db
    return {
        "hsw_rel": float(np.sqrt(np.mean(diff ** 2))),     # excess waviness (px)
        "hsw_rel_p90": float(np.percentile(np.abs(diff), 90)),
        "n_cols": int(valid.sum()),
    }


def _dominant_edge_row_per_col(erp: np.ndarray, band_frac: float) -> np.ndarray:
    """Per-column row of the strongest horizontal gradient inside the band.

    Sub-pixel: parabolic interpolation around the argmax of the vertical
    gradient magnitude column profile. NaN where the band has no salient
    horizontal edge (gradient below the per-image 80th percentile).
    """
    if erp.ndim == 3:
        gray = cv2.cvtColor(erp, cv2.COLOR_RGB2GRAY)
    else:
        gray = erp
    gray = gray.astype(np.float32)
    h, w = gray.shape
    v0, v1 = _equator_band(h, band_frac)
    band = gray[v0:v1, :]
    gy = cv2.Sobel(band, cv2.CV_32F, 0, 1, ksize=3)          # vertical gradient
    mag = np.abs(gy)
    thr = np.percentile(mag, 80.0)
    out = np.full(w, np.nan, dtype=np.float64)
    for u in range(w):
        col = mag[:, u]
        k = int(np.argmax(col))
        if col[k] < thr:
            continue
        # parabolic sub-pixel refine
        if 0 < k < col.size - 1:
            d = col[k - 1] - 2 * col[k] + col[k + 1]
            off = 0.5 * (col[k - 1] - col[k + 1]) / d if abs(d) > 1e-6 else 0.0
        else:
            off = 0.0
        out[u] = v0 + k + off
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--erp", required=True)
    ap.add_argument("--ref", default=None, help="clean L1 anchor (optional)")
    args = ap.parse_args()
    erp = cv2.cvtColor(cv2.imread(args.erp), cv2.COLOR_BGR2RGB)
    print("ABSOLUTE:", hsw_absolute(erp))
    if args.ref:
        ref = cv2.cvtColor(cv2.imread(args.ref), cv2.COLOR_BGR2RGB)
        print("RELATIVE:", hsw_relative(erp, ref))
