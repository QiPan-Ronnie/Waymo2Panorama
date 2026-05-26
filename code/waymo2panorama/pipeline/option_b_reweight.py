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
    "STEREO_NPZ_CAM_A_KEY",
    "STEREO_NPZ_CAM_B_KEY",
    "build_stereo_confidence_mask",
    "build_stereo_confidence_masks_per_cam",
    "apply_option_b_reweight",
]


# Keys used by `scripts/phase3/run_wide_baseline_stereo.py` when calling
# np.savez_compressed(...). The ego-frame 3D points + originating cam pair.
STEREO_NPZ_PTS_KEY: str = "pts_3d_ego"
STEREO_NPZ_CAM_A_KEY: str = "cam_a"
STEREO_NPZ_CAM_B_KEY: str = "cam_b"


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


def _load_stereo_cam_pair(path: Path) -> tuple[str, str] | None:
    """Load (cam_a, cam_b) full cam names from one stereo_<a>__<b>.npz file.

    Returns None if either key is missing (defensive).
    """
    with np.load(path) as npz:
        if STEREO_NPZ_CAM_A_KEY not in npz.files or STEREO_NPZ_CAM_B_KEY not in npz.files:
            return None
        cam_a = str(npz[STEREO_NPZ_CAM_A_KEY])
        cam_b = str(npz[STEREO_NPZ_CAM_B_KEY])
    return (cam_a, cam_b)


def _build_gaussian_kernel(sigma_px: float) -> tuple[np.ndarray, int]:
    """Construct an isotropic Gaussian kernel with peak=1.0 (not normalized).

    Returns (kernel, half) where kernel.shape = (2*half+1, 2*half+1).
    """
    half = max(int(np.ceil(3.0 * sigma_px)), 1)
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1].astype(np.float32)
    rr2 = (xx * xx + yy * yy)
    kernel = np.exp(-rr2 / (2.0 * sigma_px * sigma_px)).astype(np.float32)
    return kernel, half


def _splat_points_to_canvas(
    canvas: np.ndarray,
    pts_ego: np.ndarray,
    kernel: np.ndarray,
    half: int,
) -> int:
    """Max-merge Gaussian splats of `pts_ego` into `canvas` (in place).

    Returns the number of points actually splatted (post-validity filter).
    """
    h_erp, w_erp = canvas.shape
    if pts_ego.shape[0] == 0:
        return 0

    u_f, v_f, valid = ego_points_to_erp_uv(pts_ego, erp_hw=(h_erp, w_erp))
    valid &= (v_f >= 0.0) & (v_f < h_erp)
    if not np.any(valid):
        return 0

    u_int = np.round(u_f[valid]).astype(np.int64)
    v_int = np.round(v_f[valid]).astype(np.int64)
    u_int = np.mod(u_int, w_erp)
    v_int = np.clip(v_int, 0, h_erp - 1)

    ksize = 2 * half + 1
    n_splatted = 0
    for ui, vi in zip(u_int, v_int):
        y0 = vi - half
        y1 = vi + half + 1
        ky0 = max(0, -y0)
        ky1 = ksize - max(0, y1 - h_erp)
        cy0 = max(0, y0)
        cy1 = min(h_erp, y1)
        if cy1 <= cy0 or ky1 <= ky0:
            continue
        for k_col in range(ksize):
            cx = (ui - half + k_col) % w_erp
            np.maximum(
                canvas[cy0:cy1, cx],
                kernel[ky0:ky1, k_col],
                out=canvas[cy0:cy1, cx],
            )
        n_splatted += 1
    return n_splatted


def build_stereo_confidence_mask(
    stereo_npz_paths: Iterable[Path],
    erp_hw: tuple[int, int],
    sigma_px: float = 12.0,
) -> np.ndarray:
    """V1 (legacy): single uniform confidence mask over all stereo points.

    NOTE — v1 is NEG-by-effect in practice: because the same mask is applied
    to all 7 cams in `apply_option_b_reweight`, and multiband_blend normalizes
    weights per pixel, the boost (1 + alpha * C) cancels out of the final blend
    (verified empirically: PSNR(L1 vs L1+reweight) = inf on 4 anchors).

    Use `build_stereo_confidence_masks_per_cam` (v2) for the differential
    per-cam variant that actually changes output.

    Splat sparse stereo 3D points to an ERP-shaped confidence mask in [0, 1].

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
    kernel, half = _build_gaussian_kernel(sigma_px)

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
        n_pts_splat += _splat_points_to_canvas(canvas, pts_ego, kernel, half)

    max_v = float(canvas.max())
    if max_v > 1e-9:
        canvas /= max_v

    print(
        f"[option_b] built confidence mask: erp_hw={erp_hw}, "
        f"files_used={n_files_used}/{n_files_total}, pts_splat={n_pts_splat}, "
        f"sigma_px={sigma_px}, coverage_frac={float((canvas > 0.05).mean()):.4f}"
    )
    return canvas


def build_stereo_confidence_masks_per_cam(
    stereo_npz_paths: Iterable[Path],
    erp_hw: tuple[int, int],
    cam_names: Iterable[str],
    sigma_px: float = 12.0,
) -> dict[str, np.ndarray]:
    """V2: per-cam differential confidence masks.

    For each stereo .npz file (containing 3D pts from cam pair (cam_a, cam_b)),
    splat those points into the masks of BOTH cam_a and cam_b — and only those
    two. The other 5 cams' masks stay zero for that pair.

    Why this works where v1 fails: in `apply_option_b_reweight`, each cam gets
    its OWN mask. multiband_blend's per-pixel weight renormalization no longer
    cancels the boost — because cams that DID see the 3D structure get
    relatively boosted vs cams that did NOT see it. The differential is the key.

    Each per-cam mask is normalized to [0, 1] by dividing by the GLOBAL max
    across all cams' canvases (so a cam with sparse coverage doesn't get
    artificially boosted to 1.0 — its weight stays proportional to its actual
    point density relative to other cams).

    Args:
        stereo_npz_paths: iterable of Paths to stereo_<a>__<b>.npz files.
        erp_hw: (H_erp, W_erp).
        cam_names: list of cam names to allocate masks for (typically the
            7 ring cams). Cams not in any stereo pair get all-zero masks.
        sigma_px: Gaussian splat std-dev. Default 12 px.

    Returns:
        dict {cam_name: (H_erp, W_erp) float32 mask in [0, 1]}.
        All cams in `cam_names` are present in the dict; cams with no stereo
        evidence have all-zero masks (effectively no boost from option_b).
    """
    h_erp, w_erp = erp_hw
    if h_erp <= 0 or w_erp <= 0:
        raise ValueError(f"erp_hw must be positive, got {erp_hw}")
    if sigma_px <= 0:
        raise ValueError(f"sigma_px must be > 0, got {sigma_px}")

    cam_list = list(cam_names)
    if not cam_list:
        raise ValueError("cam_names must be non-empty")
    cam_set = set(cam_list)

    kernel, half = _build_gaussian_kernel(sigma_px)
    canvases: dict[str, np.ndarray] = {
        c: np.zeros((h_erp, w_erp), dtype=np.float32) for c in cam_list
    }

    paths = list(stereo_npz_paths)
    n_files_total = len(paths)
    n_files_used = 0
    n_pts_total = 0
    cams_touched: set[str] = set()
    unknown_cams: set[str] = set()

    for p in paths:
        try:
            pts_ego = _load_stereo_pts_ego(p)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"[option_b/v2] warn: cannot load {p}: {exc}")
            continue
        if pts_ego.shape[0] == 0:
            print(f"[option_b/v2] warn: {p.name} has 0 inliers, skipping")
            continue
        cam_pair = _load_stereo_cam_pair(p)
        if cam_pair is None:
            print(f"[option_b/v2] warn: {p.name} missing cam_a/cam_b keys, skipping")
            continue
        cam_a, cam_b = cam_pair

        # Validate cam names are in the expected set
        if cam_a not in cam_set:
            unknown_cams.add(cam_a)
            continue
        if cam_b not in cam_set:
            unknown_cams.add(cam_b)
            continue

        n_files_used += 1
        # Splat the SAME points into BOTH cams' canvases (both cams "saw" them)
        n_a = _splat_points_to_canvas(canvases[cam_a], pts_ego, kernel, half)
        n_b = _splat_points_to_canvas(canvases[cam_b], pts_ego, kernel, half)
        if n_a > 0:
            cams_touched.add(cam_a)
        if n_b > 0:
            cams_touched.add(cam_b)
        n_pts_total += max(n_a, n_b)  # n_a should equal n_b (same source pts)

    # Global normalization: divide every cam's canvas by the GLOBAL max so
    # relative scales between cams are preserved.
    global_max = max((float(c.max()) for c in canvases.values()), default=0.0)
    if global_max > 1e-9:
        for c in canvases:
            canvases[c] = canvases[c] / global_max

    if unknown_cams:
        print(
            f"[option_b/v2] warn: stereo files referenced unknown cams "
            f"(not in cam_names): {sorted(unknown_cams)}"
        )

    coverage_per_cam = {
        c: float((canvases[c] > 0.05).mean()) for c in cam_list
    }
    print(
        f"[option_b/v2] built per-cam masks: erp_hw={erp_hw}, "
        f"files_used={n_files_used}/{n_files_total}, "
        f"cams_touched={len(cams_touched)}/{len(cam_list)}, "
        f"pts_total={n_pts_total}, sigma_px={sigma_px}"
    )
    for c in cam_list:
        print(f"[option_b/v2]   {c}: coverage_frac={coverage_per_cam[c]:.4f}")
    return canvases


def apply_option_b_reweight(
    erp_weights: dict[str, np.ndarray] | list[np.ndarray],
    confidence_mask: np.ndarray | dict[str, np.ndarray],
    alpha: float = 1.0,
) -> dict[str, np.ndarray] | list[np.ndarray]:
    """Multiply each per-cam ERP weight by (1 + alpha * confidence_mask).

    Supports TWO modes:
      v1 (single mask, NEG-by-effect):
          confidence_mask is a single (H, W) array.
          All cams get the same boost — multiband normalize cancels it.
          Kept for backward compat + the NEG test on record.

      v2 (per-cam differential, fixed):
          confidence_mask is a dict {cam_name: (H, W) mask}.
          Each cam's weight gets multiplied by its OWN mask's boost. This is
          the differential form: cams that "saw" the 3D structure are boosted
          more than cams that didn't, so multiband normalize doesn't cancel
          out.  REQUIRES erp_weights to also be a dict (cam_name keys must
          match).

    Args:
        erp_weights: {cam_name: (H, W) float} dict (preferred), or list of
            (H, W) arrays (legacy; only with v1 single-mask input).
        confidence_mask: either single 2D array (v1) or dict[cam, 2D array]
            (v2 differential).
        alpha: boost magnitude (>= 0). alpha=0 -> identity reweight.

    Returns:
        New container of the same type / order as `erp_weights`. Input NOT
        mutated.

    Raises:
        TypeError: incompatible input shapes for per-cam mode (list input +
            dict mask, or vice versa).
        ValueError: alpha < 0, shape mismatch, or per-cam mode missing a cam
            in the mask dict (every cam in erp_weights MUST have a mask key).
    """
    if alpha < 0:
        raise ValueError(f"alpha must be >= 0, got {alpha}")

    # Dispatch on confidence_mask type
    if isinstance(confidence_mask, dict):
        # v2 per-cam path
        if not isinstance(erp_weights, dict):
            raise TypeError(
                "per-cam reweight requires erp_weights to also be a dict "
                f"(got {type(erp_weights).__name__}). Use single-mask v1 with a "
                "list, or convert your weights to a dict keyed by cam name."
            )
        out_dict: dict[str, np.ndarray] = {}
        for cam, w in erp_weights.items():
            if cam not in confidence_mask:
                raise ValueError(
                    f"per-cam reweight: cam '{cam}' missing from confidence_mask "
                    f"dict (keys present: {sorted(confidence_mask.keys())})"
                )
            mask_i = confidence_mask[cam]
            if mask_i.ndim != 2:
                raise ValueError(
                    f"confidence_mask['{cam}'] must be 2D, got shape {mask_i.shape}"
                )
            if w.shape != mask_i.shape:
                raise ValueError(
                    f"weight/mask shape mismatch for cam={cam}: "
                    f"weight {w.shape} vs mask {mask_i.shape}"
                )
            mask_max = float(mask_i.max()) if mask_i.size > 0 else 0.0
            if mask_max > 1.0 + 1e-3:
                import warnings
                warnings.warn(
                    f"apply_option_b_reweight: confidence_mask['{cam}'] max = "
                    f"{mask_max:.3f} > 1.0; expected normalized [0, 1].",
                    RuntimeWarning, stacklevel=2,
                )
            boost = (1.0 + alpha * mask_i).astype(np.float32)
            out_dict[cam] = (w.astype(np.float32) * boost).astype(np.float32)
        return out_dict

    # v1 single-mask path (legacy + NEG-by-effect)
    if confidence_mask.ndim != 2:
        raise ValueError(
            f"confidence_mask must be 2D (or dict), got shape {confidence_mask.shape}"
        )

    mask_max = float(confidence_mask.max()) if confidence_mask.size > 0 else 0.0
    if mask_max > 1.0 + 1e-3:
        import warnings
        warnings.warn(
            f"apply_option_b_reweight: confidence_mask max = {mask_max:.3f} > 1.0; "
            "expected normalized [0, 1] mask.",
            RuntimeWarning,
            stacklevel=2,
        )

    boost = (1.0 + alpha * confidence_mask).astype(np.float32)

    if isinstance(erp_weights, dict):
        out_dict_v1: dict[str, np.ndarray] = {}
        for cam, w in erp_weights.items():
            if w.shape != confidence_mask.shape:
                raise ValueError(
                    f"weight shape mismatch for cam={cam}: "
                    f"{w.shape} vs mask {confidence_mask.shape}"
                )
            out_dict_v1[cam] = (w.astype(np.float32) * boost).astype(np.float32)
        return out_dict_v1

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
