"""
Multi-radius sphere render with per-pixel R selection (implicit depth).

THE IDEA
========
L1 baseline assumes R = infinity (sphere at infinity). This is wrong for
near-field objects (3-10 m), producing parallax ghost when two cams see
the same object at different ERP positions.

A finite R fixes near-field BUT introduces distortion on far field.
There is no single R that fits all depths.

SOLUTION: render the SAME ERP at multiple R values (e.g. {inf, 30, 10, 5, 3}).
For each ERP pixel in overlap zone, pick the R that maximizes cross-cam
agreement between adjacent cams. This is **implicit depth** — the chosen R
acts as a discrete depth bin, and the criterion (cross-cam consistency)
naturally selects the bin matching that pixel's actual scene depth.

KEY DIFFERENCE FROM N1 SELFSTEREO (which failed):
- N1 estimated CONTINUOUS depth then reprojected → if depth implies a 3D
  point outside cam's FOV cone, black hole.
- Multi-R picks from PRE-RENDERED slabs → each slab already has a `valid`
  mask. We skip R values where either cam is invalid. Fallback to R=inf
  for pixels that have no valid finite R. No black holes.

USAGE
=====
from waymo2panorama.blending.multi_radius_select import render_multi_radius_select
erp = render_multi_radius_select(frame, erp_hw=(2048, 4096),
                                  R_values=[None, 30.0, 10.0, 5.0, 3.0],
                                  blend_mode="weighted")  # or "hard_select"
"""
from __future__ import annotations

import numpy as np

from waymo2panorama.data_io.av2_loader import RING_CAMS_7, FrameSample
from waymo2panorama.projection.sphere_projection import render_camera_to_erp


def render_all_cams_at_R(frame: FrameSample, erp_hw: tuple[int, int], R: float | None):
    """Render all 7 ring cams to ERP at given R. Returns (slabs, weights, valid)."""
    slabs, weights, valids = [], [], []
    for cam in RING_CAMS_7:
        img = frame.images[cam]
        calib = frame.calibrations[cam]
        s, v, w = render_camera_to_erp(
            image=img,
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=R,
        )
        slabs.append(s)
        weights.append(w)
        valids.append(v)
    return np.stack(slabs, axis=0), np.stack(weights, axis=0), np.stack(valids, axis=0)


def render_multi_radius_select(
    frame: FrameSample,
    erp_hw: tuple[int, int] = (1024, 2048),
    R_values: list[float | None] | None = None,
    blend_mode: str = "hard_select",
    use_y_only: bool = True,
):
    """
    Render ERP using per-pixel R selection across multiple sphere radii.

    Args:
        frame: FrameSample with 7 cam images + calibrations
        erp_hw: ERP dimensions
        R_values: list of sphere radii in meters. None == infinity. Default {inf, 30, 10, 5, 3}.
                  The list MUST contain None (infinity) as fallback for non-overlap pixels.
        blend_mode:
          - "hard_select": at each pixel, pick the winning cam (highest cos² weight at chosen R),
                           use only that cam's pixel. Like L1 hard_select but with per-pixel R.
          - "weighted":    blend the top-2 cams at the chosen R using cos² weights.
        use_y_only: if True, compute cross-cam disagreement on Y channel only
                    (avoids being dominated by exposure mismatch). Default True.

    Returns:
        erp (H, W, 3) uint8
        best_R_idx (H, W) int — which R index was selected per pixel (for debug viz)
    """
    if R_values is None:
        R_values = [None, 30.0, 10.0, 5.0, 3.0]
    if None not in R_values:
        raise ValueError("R_values must contain None (infinity) as the safe fallback")
    inf_idx = R_values.index(None)

    H, W = erp_hw
    n_R = len(R_values)
    n_cam = len(RING_CAMS_7)

    # Step 1: render all 7 cams at each R value -> (n_R, n_cam, H, W, 3) and weights
    all_slabs = np.empty((n_R, n_cam, H, W, 3), dtype=np.float32)
    all_weights = np.empty((n_R, n_cam, H, W), dtype=np.float32)
    all_valid = np.empty((n_R, n_cam, H, W), dtype=bool)
    for k, R in enumerate(R_values):
        all_slabs[k], all_weights[k], all_valid[k] = render_all_cams_at_R(frame, erp_hw, R)

    # Step 2: at R=inf reference, decide which 2 cams compete for each ERP pixel.
    # The 2-cam pair stays fixed across R values (only the R chosen varies).
    ref_w = all_weights[inf_idx]  # n_cam x H x W
    top1 = ref_w.argmax(axis=0)   # H x W — winning cam index
    masked = ref_w.copy()
    rr, cc = np.indices((H, W))
    masked[top1, rr, cc] = -1.0   # mask out winner so argmax picks runner-up
    top2 = masked.argmax(axis=0)  # H x W — runner-up cam index

    # Step 3: at each R, compute cross-cam disagreement |slab_top1 - slab_top2|.
    # Only meaningful where BOTH top1 and top2 are visible at that R.
    slab_A = all_slabs[:, top1, rr, cc]    # n_R x H x W x 3
    slab_B = all_slabs[:, top2, rr, cc]    # n_R x H x W x 3
    valid_A = all_valid[:, top1, rr, cc]   # n_R x H x W
    valid_B = all_valid[:, top2, rr, cc]   # n_R x H x W

    if use_y_only:
        # Y in YCrCb ≈ 0.299 R + 0.587 G + 0.114 B
        Y_A = 0.299 * slab_A[..., 0] + 0.587 * slab_A[..., 1] + 0.114 * slab_A[..., 2]
        Y_B = 0.299 * slab_B[..., 0] + 0.587 * slab_B[..., 1] + 0.114 * slab_B[..., 2]
        D = np.abs(Y_A - Y_B)              # n_R x H x W
    else:
        D = np.linalg.norm(slab_A - slab_B, axis=-1)

    both_valid = valid_A & valid_B
    D = np.where(both_valid, D, np.inf)    # invalid R values get rejected by argmin

    # Step 4: pick best R per pixel (argmin disagreement)
    best_R_idx = D.argmin(axis=0).astype(np.int32)   # H x W

    # For pixels where NO R value has both cams valid (rare, edge of FOV),
    # fall back to inf (always valid where the cam can see).
    has_any_valid = both_valid.any(axis=0)
    best_R_idx = np.where(has_any_valid, best_R_idx, inf_idx)

    # For pixels where the runner-up cam (top2) has zero weight at R=inf
    # (= non-overlap), use inf (best for cams without parallax constraint).
    in_overlap = (ref_w[top2, rr, cc] > 0)
    best_R_idx = np.where(in_overlap, best_R_idx, inf_idx)

    # Step 5: composite final ERP
    final_A = all_slabs[best_R_idx, top1, rr, cc]   # H x W x 3
    final_B = all_slabs[best_R_idx, top2, rr, cc]   # H x W x 3
    w_A = all_weights[best_R_idx, top1, rr, cc]
    w_B = all_weights[best_R_idx, top2, rr, cc]

    if blend_mode == "hard_select":
        # pick winning cam at the chosen R (= per-pixel argmax of cos² at that R)
        # Note: with R != inf, weights shift slightly so top1 may not be argmax at chosen R.
        # We re-check here for correctness.
        use_A = w_A >= w_B
        out = np.where(use_A[..., None], final_A, final_B)
    elif blend_mode == "weighted":
        total = w_A + w_B + 1e-6
        out = (final_A * w_A[..., None] + final_B * w_B[..., None]) / total[..., None]
    else:
        raise ValueError(f"blend_mode must be 'hard_select' or 'weighted', got {blend_mode!r}")

    return np.clip(out, 0, 255).astype(np.uint8), best_R_idx
