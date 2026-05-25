"""
Phase 3 route 13 / 新-D — Option B reweight: stereo confidence -> L1 weight boost.

Background
----------
新-D (`stereo/wide_baseline_stereo.py`) extracts sparse 3D points per adjacent
ring-cam pair by DISK+LightGlue features + epipolar inlier filtering + DLT
triangulation. It outputs ~44 inlier 3D pts/pair (median) sitting in the AV2
ego frame. These cached points (saved by `scripts/phase3/run_wide_baseline_stereo.py`
as `stereo_<a>__<b>.npz` files) carry real geometric evidence about which ERP
regions contain physically-coherent overlap content — exactly the regions L1
sphere-projection tends to ghost on.

But until now, those 3D points were never plumbed back into the stitching
pipeline. Option B closes that loop with the minimal possible change:

  1. Splat the stereo 3D points onto the ERP canvas as a confidence mask
     C(u, v) in [0, 1], using the same `ego_points_to_erp_uv` projection
     L3 splats use (so the mask lines up exactly with the stitching frame).
  2. Multiply L1's per-cam blend weights by  (1 + alpha * C)  before
     handing them to the multi-band blender.

The hypothesis: pixels where stereo confirms real 3D structure get weighted
MORE in the blend, while parallax-ambiguous pixels (where stereo found
nothing — the empty-overlap regions L1 has to fall back on cos^2 cosine
feathering) stay at L1 weights. The net effect is to suppress ghosting in
the overlap zones that have actual depth evidence.

Why this is "Option B" (and not Option A)
-----------------------------------------
Option A (rejected): use the stereo 3D points themselves to forward-splat
RGB onto ERP (so they directly REPLACE L1 in their footprint). That's a
high-variance change — stereo is sparse (44 pts/pair) so most of the canvas
still needs L1, and the boundary between "L1 region" and "stereo region"
introduces its own discontinuity.

Option B (this file): just REWEIGHT existing L1 contributions. No new pixels
are created or removed; the only change is *how strongly* each cam votes per
pixel. Drop-in: it inserts between `render_camera_to_erp` and `multiband_blend`
without modifying either module.

Pipeline insertion
------------------
Standard L1::
                            +-> w_cam_0  ---+
    render_camera_to_erp -+-> w_cam_1  ---+--> multiband_blend -> erp.png
                            +-> ...        |
                            +-> w_cam_6  ---+

With Option B::
                            +-> w_cam_0 *= (1 + a*C) -+
    render_camera_to_erp -+-> w_cam_1 *= (1 + a*C) -+--> multiband_blend -> erp.png
                            +-> ...                   |
                            +-> w_cam_6 *= (1 + a*C) -+
                                     ^
    stereo_*.npz --> build_stereo_confidence_mask --> C(u,v)  in [0,1]

The `alpha` hyperparameter (typically 0.5 ~ 2.0) sets the boost magnitude:
alpha=0 means "no reweight" (= plain L1); alpha=1 means "double the weight
at full-confidence ERP pixels".

Expected effect
---------------
+0.05 ~ +0.3 dB on cycle-PSNR vs plain L1 — the gain depends on how much of
the overlap region has stereo coverage. Side-cam overlaps tend to have
strong DISK+LG matches (treelines, roadside structure), so we expect the
biggest wins there; rear cams with motion-blurred night content will have
sparse coverage and a near-zero gain.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np

from .lift_and_project import ego_points_to_erp_uv


__all__ = [
    "STEREO_NPZ_PTS_KEY",
    "build_stereo_confidence_mask",
    "apply_option_b_reweight",
]


# Key used by `scripts/phase3/run_wide_baseline_stereo.py` when calling
# np.savez_compressed(...). The ego-frame 3D points live under this key.
STEREO_NPZ_PTS_KEY: str = "pts_3d_ego"


def _load_stereo_pts_ego(path: Path) -> np.ndarray:
    """Load (N, 3) ego-frame 3D points from one stereo_<a>__<b>.npz file.

    Returns an empty (0, 3) array if the file has no inliers (or the key is
    missing — defensive: we don't want a single malformed cache file to
    blow up the whole mask build).
    """
    with np.load(path) as npz:
        if STEREO_NPZ_PTS_KEY not in npz.files:
            return np.zeros((0, 3), dtype=np.float32)
        pts = np.asarray(npz[STEREO_NPZ_PTS_KEY], dtype=np.float32)
    if pts.ndim != 2 or pts.shape[1] != 3:
        return np.zeros((0, 3), dtype=np.float32)
    return pts


def build_stereo_confidence_mask(
    stereo_npz_paths: Iterable[Path],
    erp_hw: tuple[int, int],
    sigma_px: float = 12.0,
) -> np.ndarray:
    """Splat sparse stereo 3D points to an ERP-shaped confidence mask in [0, 1].

    For each `.npz` in `stereo_npz_paths`:
      1. Load ego-frame 3D points (`pts_3d_ego` key).
      2. Project each point onto the ERP canvas via `ego_points_to_erp_uv`.
      3. Add a unit-amplitude isotropic Gaussian centred at the projected
         (u, v), with stdev `sigma_px`, to a float32 accumulator.

    After all files are processed, the accumulator is normalized to [0, 1]
    by dividing by its global max. (We deliberately choose max-divide over
    saturating accumulation so that `alpha` keeps a clear "boost fraction"
    interpretation regardless of how many points happened to land.)

    Args:
        stereo_npz_paths: iterable of Paths pointing at stereo_<a>__<b>.npz
            files. Files with zero inliers or a missing `pts_3d_ego` key
            are silently skipped (warning printed). Empty input -> all-zero
            mask returned.
        erp_hw: (H_erp, W_erp) of the target panorama (must match the L1
            slabs/weights you intend to multiply this into).
        sigma_px: standard deviation of the Gaussian splat, in ERP pixels.
            Default 12 px is roughly the L1 cos^2 feather characteristic
            length at H_erp=1024. Smaller -> tighter dots (more localized
            boost); larger -> smoother diffuse boost.

    Returns:
        confidence_mask: (H_erp, W_erp) float32 in [0, 1].
    """
    h_erp, w_erp = erp_hw
    if h_erp <= 0 or w_erp <= 0:
        raise ValueError(f"erp_hw must be positive, got {erp_hw}")
    if sigma_px <= 0:
        raise ValueError(f"sigma_px must be > 0, got {sigma_px}")

    canvas = np.zeros((h_erp, w_erp), dtype=np.float32)

    # Splat box half-size: 3 sigma captures ~99.7% of the gaussian mass
    half = max(int(np.ceil(3.0 * sigma_px)), 1)
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1].astype(np.float32)
    rr2 = (xx * xx + yy * yy)
    kernel = np.exp(-rr2 / (2.0 * sigma_px * sigma_px)).astype(np.float32)
    # Don't pre-normalize the kernel (peak = 1.0) so peaks accumulate
    # naturally when multiple points splat to nearby ERP pixels.

    paths = list(stereo_npz_paths)
    n_files_total = len(paths)
    n_files_used = 0
    n_pts_splat = 0

    for p in paths:
        try:
            pts_ego = _load_stereo_pts_ego(p)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"[option_b] warn: cannot load {p}: {exc}")
            continue
        if pts_ego.shape[0] == 0:
            print(f"[option_b] warn: {p.name} has 0 inliers, skipping")
            continue
        n_files_used += 1

        u_f, v_f, valid = ego_points_to_erp_uv(pts_ego, erp_hw=(h_erp, w_erp))
        # Discard out-of-vertical-range points (azimuth already mod-W'd by
        # ego_points_to_erp_uv, so u is always in [0, W)).
        valid &= (v_f >= 0.0) & (v_f < h_erp)
        if not np.any(valid):
            continue

        u_int = np.round(u_f[valid]).astype(np.int64)
        v_int = np.round(v_f[valid]).astype(np.int64)
        u_int = np.mod(u_int, w_erp)            # belt-and-suspenders wrap
        v_int = np.clip(v_int, 0, h_erp - 1)

        ksize = 2 * half + 1
        for ui, vi in zip(u_int, v_int):
            y0 = vi - half
            y1 = vi + half + 1
            # Clamp vertical (no wrap on elevation)
            ky0 = max(0, -y0)
            ky1 = ksize - max(0, y1 - h_erp)
            cy0 = max(0, y0)
            cy1 = min(h_erp, y1)
            if cy1 <= cy0 or ky1 <= ky0:
                continue

            # Horizontal: wrap-aware. Walk each kernel column individually and
            # mod into canvas. Clear, slow, but kernel widths are tiny (~75 px
            # max for sigma=12) and the point count per file is ~44.
            for k_col in range(ksize):
                cx = (ui - half + k_col) % w_erp
                np.maximum(
                    canvas[cy0:cy1, cx],
                    kernel[ky0:ky1, k_col],
                    out=canvas[cy0:cy1, cx],
                )
            n_pts_splat += 1

    max_v = float(canvas.max())
    if max_v > 1e-9:
        canvas /= max_v
    else:
        # all-zero mask is a valid (degenerate) output — apply_option_b_reweight
        # treats it as "no boost anywhere".
        pass

    print(
        f"[option_b] built confidence mask: erp_hw={erp_hw}, "
        f"files_used={n_files_used}/{n_files_total}, pts_splat={n_pts_splat}, "
        f"sigma_px={sigma_px}, coverage_frac={float((canvas > 0.05).mean()):.4f}"
    )
    return canvas


def apply_option_b_reweight(
    erp_weights: dict[str, np.ndarray] | list[np.ndarray],
    confidence_mask: np.ndarray,
    alpha: float = 1.0,
) -> dict[str, np.ndarray] | list[np.ndarray]:
    """Multiply each per-cam ERP weight by (1 + alpha * confidence_mask).

    Args:
        erp_weights: either {cam_name: (H, W) float} or a list of (H, W)
            float arrays. The returned container has the same type and key
            order as the input.
        confidence_mask: (H_erp, W_erp) float in [0, 1] (output of
            `build_stereo_confidence_mask`).
        alpha: boost magnitude.  Must be >= 0. With alpha=0 the output equals
            the input (identity reweight).

    Returns:
        New container of the same type / order as `erp_weights`, with each
        weight array multiplied elementwise by (1 + alpha * confidence_mask).
        The input is NOT mutated.

    Notes:
        * Multiband blending re-normalizes per-pixel weights to sum to 1, so
          you don't need to clip or rescale these. The reweight is purely a
          relative-importance signal between cams at each pixel.
        * alpha < 0 is allowed but unusual (would DOWN-weight high-confidence
          regions) — we just enforce >= 0 to avoid silent foot-shooting.
    """
    if alpha < 0:
        raise ValueError(f"alpha must be >= 0, got {alpha}")
    if confidence_mask.ndim != 2:
        raise ValueError(f"confidence_mask must be 2D, got shape {confidence_mask.shape}")

    boost = (1.0 + alpha * confidence_mask).astype(np.float32)

    if isinstance(erp_weights, dict):
        out_dict: dict[str, np.ndarray] = {}
        for cam, w in erp_weights.items():
            if w.shape != confidence_mask.shape:
                raise ValueError(
                    f"weight shape mismatch for cam={cam}: "
                    f"{w.shape} vs mask {confidence_mask.shape}"
                )
            out_dict[cam] = (w.astype(np.float32) * boost).astype(np.float32)
        return out_dict

    if isinstance(erp_weights, list):
        out_list: list[np.ndarray] = []
        for i, w in enumerate(erp_weights):
            if w.shape != confidence_mask.shape:
                raise ValueError(
                    f"weight shape mismatch at index {i}: "
                    f"{w.shape} vs mask {confidence_mask.shape}"
                )
            out_list.append((w.astype(np.float32) * boost).astype(np.float32))
        return out_list

    raise TypeError(
        f"erp_weights must be dict or list, got {type(erp_weights).__name__}"
    )
