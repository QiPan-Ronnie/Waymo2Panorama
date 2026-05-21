"""
L3.B — Multi-view depth Bayesian fusion at ERP overlap regions (T16, 2026-05-20).

Motivation
----------
The vanilla L3 forward-splat (`lift_and_project.splat_to_erp`) blends each cam's
RGB by its Pi3 confidence (weighted RGB accumulator). It works as long as the
3D points from different cams really land on the same ERP pixel. But Pi3 is a
*per-cam* depth predictor: two cams looking at the same 3D point usually
disagree on its depth by 0.5 - 2 m, which translates to two slightly
displaced ERP locations. The result is a "double-image" ghost at every
overlap seam, visually noisier than L1 in those regions.

Idea
----
At each ERP pixel covered by ≥ 2 cams, fuse the per-cam depth estimates as a
Bayesian inverse-variance-weighted mean (treating Pi3 conf ∈ [0, 1] as
inverse variance):

  d_fused = Σ_i (w_i * d_i) / Σ_i w_i
  c_fused = Σ_i (w_i * c_i) / Σ_i w_i        (per-channel RGB)
  weight_fused = Σ_i w_i                     (combined precision)

This is what the existing `splat_to_erp` already does *for RGB* — but each
cam splats its OWN depth without ever combining depths across cams. The
depth map produced by the naive pipeline is implicitly "first-arrival" / a
weighted-mean of disagreeing depths through the RGB accumulator; we never
get a clean fused depth map. Here we compute the fused depth explicitly
and emit it alongside the ERP RGB.

Public API
----------
`splat_with_bayesian_fusion(per_cam_outputs, image_dict, erp_hw, ...)
    -> dict with keys: erp_rgb, erp_depth, erp_conf, erp_coverage,
                       naive_erp_rgb, naive_erp_depth, diagnostics`

`per_cam_outputs` is the canonical Phase-3 Pi3 dict-of-dicts:
    {
        cam_name: {
            "points_ego":  (H, W, 3)   ego-frame 3D points (after Sim3 alignment)
            "conf":        (H, W)      Pi3 confidence in [0, 1] (already sigmoid'd)
            "rgb":         (H, W, 3)   uint8 image
        },
        ...
    }

The function reuses `ego_points_to_erp_uv` from `lift_and_project` for the
ERP projection convention, then runs a *nearest-pixel* splat with `np.add.at`
accumulating five separate buffers per ERP pixel:
    Σw, Σ(w·R), Σ(w·G), Σ(w·B), Σ(w·d)

Bilinear splat is intentionally NOT used here: bilinear smears one cam's
single depth estimate across 4 neighboring ERP pixels, which dilutes the
"who agrees with whom" signal we are trying to estimate. Nearest splat keeps
each (cam, pixel) → (ERP pixel) vote crisp. If the user wants the smoother
visual of bilinear, run the naive path in parallel; this module ships both.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from .lift_and_project import ego_points_to_erp_uv


def _nearest_splat_with_depth(
    points_ego: np.ndarray,
    colors: np.ndarray,
    confs: np.ndarray,
    erp_hw: tuple[int, int],
    conf_threshold: float,
    min_distance_m: float,
    max_distance_m: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """One cam's contribution. Nearest-pixel splat.

    Returns:
        sum_w           (H, W)       float64
        sum_w_rgb       (H, W, 3)    float64    (sum of w * rgb)
        sum_w_d         (H, W)       float64    (sum of w * depth)
        n_cam_pixels    int          number of source pixels actually splatted
    """
    h_erp, w_erp = erp_hw
    sum_w = np.zeros((h_erp, w_erp), dtype=np.float64)
    sum_w_rgb = np.zeros((h_erp, w_erp, 3), dtype=np.float64)
    sum_w_d = np.zeros((h_erp, w_erp), dtype=np.float64)

    u_f, v_f, valid = ego_points_to_erp_uv(points_ego, erp_hw)
    d = np.linalg.norm(points_ego, axis=-1)

    valid = (
        valid
        & (confs > conf_threshold)
        & (d > min_distance_m) & (d < max_distance_m)
        & (v_f >= 0.0) & (v_f < h_erp - 1.0)
    )
    if not np.any(valid):
        return sum_w, sum_w_rgb, sum_w_d, 0

    u_v = u_f[valid]; v_v = v_f[valid]
    c_v = colors[valid].astype(np.float64)
    w_v = confs[valid].astype(np.float64)
    d_v = d[valid].astype(np.float64)

    ui = np.round(u_v).astype(np.int64) % w_erp
    vi = np.clip(np.round(v_v).astype(np.int64), 0, h_erp - 1)

    np.add.at(sum_w, (vi, ui), w_v)
    np.add.at(sum_w_d, (vi, ui), w_v * d_v)
    for ch in range(3):
        np.add.at(sum_w_rgb, (vi, ui, ch), w_v * c_v[:, ch])

    return sum_w, sum_w_rgb, sum_w_d, int(valid.sum())


def _naive_zbuffer_splat(
    points_ego: np.ndarray,
    colors: np.ndarray,
    confs: np.ndarray,
    erp_hw: tuple[int, int],
    conf_threshold: float,
    min_distance_m: float,
    max_distance_m: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    """One cam, but ALSO retain per-pixel argmin-depth (so a global z-buffer
    can be reconstructed across cams later).

    Returns:
        zbuf_depth   (H, W)    float32, +inf at empty pixels
        zbuf_rgb     (H, W, 3) float32, 0 at empty pixels
        n_pixels     int
    """
    h_erp, w_erp = erp_hw
    zbuf_d = np.full((h_erp, w_erp), np.inf, dtype=np.float32)
    zbuf_rgb = np.zeros((h_erp, w_erp, 3), dtype=np.float32)

    u_f, v_f, valid = ego_points_to_erp_uv(points_ego, erp_hw)
    d = np.linalg.norm(points_ego, axis=-1)

    valid = (
        valid
        & (confs > conf_threshold)
        & (d > min_distance_m) & (d < max_distance_m)
        & (v_f >= 0.0) & (v_f < h_erp - 1.0)
    )
    if not np.any(valid):
        return zbuf_d, zbuf_rgb, 0

    u_v = u_f[valid]; v_v = v_f[valid]
    c_v = colors[valid].astype(np.float32)
    d_v = d[valid].astype(np.float32)

    ui = np.round(u_v).astype(np.int64) % w_erp
    vi = np.clip(np.round(v_v).astype(np.int64), 0, h_erp - 1)

    # Sort by depth ascending, nearest first; first occurrence per pixel wins
    order = np.argsort(d_v)
    ui_s = ui[order]; vi_s = vi[order]
    c_s = c_v[order]; d_s = d_v[order]
    flat = vi_s.astype(np.int64) * w_erp + ui_s
    _, first = np.unique(flat, return_index=True)
    keep_ui = ui_s[first]; keep_vi = vi_s[first]
    keep_c = c_s[first]; keep_d = d_s[first]

    zbuf_d[keep_vi, keep_ui] = keep_d
    zbuf_rgb[keep_vi, keep_ui] = keep_c
    return zbuf_d, zbuf_rgb, int(valid.sum())


def splat_with_bayesian_fusion(
    per_cam_outputs: dict[str, dict[str, np.ndarray]],
    erp_hw: tuple[int, int] = (1024, 2048),
    conf_threshold: float = 0.1,
    min_distance_m: float = 0.5,
    max_distance_m: float = 200.0,
    cams: Iterable[str] | None = None,
) -> dict:
    """Bayesian-fuse multi-view Pi3 depth at ERP overlap regions.

    Args:
        per_cam_outputs: see module docstring. Each cam needs `points_ego`
            (H, W, 3), `conf` (H, W) in [0, 1], and `rgb` (H, W, 3) uint8.
        erp_hw: output (H, W) for ERP canvas. 1024x2048 by default.
        conf_threshold: drop per-pixel votes below this Pi3 confidence.
        min_distance_m, max_distance_m: drop points outside this depth range
            (Pi3 is unreliable at < 0.5 m, > 200 m).
        cams: optional iterable to restrict / order cams. Default = all keys.

    Returns:
        dict with keys:
          erp_rgb         (H, W, 3) float32 in [0, 255]   — fused colour
          erp_depth       (H, W)    float32                — fused depth (m); 0 where empty
          erp_conf        (H, W)    float32                — Σ w_i (combined precision)
          erp_coverage    (H, W)    int32                  — number of cams contributing
          naive_erp_rgb   (H, W, 3) float32                — global z-buffer RGB (one cam per pixel)
          naive_erp_depth (H, W)    float32                — global z-buffer depth (one cam per pixel)
          per_cam         dict                             — per-cam pixel counts and weight sums
    """
    h_erp, w_erp = erp_hw
    cams = list(cams) if cams is not None else list(per_cam_outputs.keys())

    bayes_sum_w = np.zeros((h_erp, w_erp), dtype=np.float64)
    bayes_sum_wrgb = np.zeros((h_erp, w_erp, 3), dtype=np.float64)
    bayes_sum_wd = np.zeros((h_erp, w_erp), dtype=np.float64)
    coverage = np.zeros((h_erp, w_erp), dtype=np.int32)

    naive_d = np.full((h_erp, w_erp), np.inf, dtype=np.float32)
    naive_rgb = np.zeros((h_erp, w_erp, 3), dtype=np.float32)

    per_cam_stats: dict[str, dict] = {}

    for cam in cams:
        rec = per_cam_outputs[cam]
        pts = rec["points_ego"]
        conf = rec["conf"]
        rgb = rec["rgb"]
        H, W = pts.shape[:2]
        pts_flat = pts.reshape(-1, 3)
        rgb_flat = rgb.reshape(-1, 3)
        conf_flat = conf.reshape(-1)

        # Bayesian accumulation (nearest splat for crisp votes)
        sw, swr, swd, n_use = _nearest_splat_with_depth(
            pts_flat, rgb_flat, conf_flat, erp_hw,
            conf_threshold, min_distance_m, max_distance_m,
        )
        # coverage count: pixels this cam wrote to (any weight > 0)
        coverage += (sw > 0).astype(np.int32)
        bayes_sum_w += sw
        bayes_sum_wrgb += swr
        bayes_sum_wd += swd

        # Naive z-buffer comparison
        zd, zrgb, _ = _naive_zbuffer_splat(
            pts_flat, rgb_flat, conf_flat, erp_hw,
            conf_threshold, min_distance_m, max_distance_m,
        )
        # Update global z-buffer: keep depth-min across cams
        mask_closer = zd < naive_d
        naive_d = np.where(mask_closer, zd, naive_d)
        naive_rgb = np.where(mask_closer[..., None], zrgb, naive_rgb)

        per_cam_stats[cam] = {
            "n_total": int(H * W),
            "n_after_threshold": int(n_use),
            "coverage_pixels": int((sw > 0).sum()),
            "weight_sum": float(sw.sum()),
        }

    has_w = bayes_sum_w > 1e-9
    w_safe = np.where(has_w, bayes_sum_w, 1.0)

    erp_rgb_fused = np.where(
        has_w[..., None], bayes_sum_wrgb / w_safe[..., None], 0.0,
    ).astype(np.float32)
    erp_depth_fused = np.where(
        has_w, bayes_sum_wd / w_safe, 0.0,
    ).astype(np.float32)

    # Naive: turn +inf depths into 0 (empty marker), match fused output format
    naive_empty = ~np.isfinite(naive_d)
    naive_depth_out = np.where(naive_empty, 0.0, naive_d).astype(np.float32)

    # Diagnostics on overlap region (≥2 cams)
    overlap_mask = coverage >= 2
    single_mask = coverage == 1
    overlap_frac = float(overlap_mask.mean())
    cov_frac = float((coverage > 0).mean())

    # Depth disagreement: where ≥2 cams contributed, what was the
    # weighted variance of their depth votes? Use the parallel
    # accumulator trick: Var = E[w*d^2]/E[w] - (E[w*d]/E[w])^2.
    # We did not accumulate w*d^2 above; compute a *proxy* dispersion by
    # taking |Bayesian - naive| diff in the overlap region (positive means
    # the fused depth pulls the closer-by argmin away).
    depth_diff = np.zeros_like(erp_depth_fused)
    valid_diff = overlap_mask & has_w & ~naive_empty
    depth_diff[valid_diff] = np.abs(
        erp_depth_fused[valid_diff] - naive_depth_out[valid_diff],
    )

    diagnostics = {
        "per_cam": per_cam_stats,
        "coverage_pixels": int(has_w.sum()),
        "coverage_ratio": cov_frac,
        "overlap_pixels": int(overlap_mask.sum()),
        "overlap_ratio": overlap_frac,
        "single_cam_pixels": int(single_mask.sum()),
        "mean_coverage_in_covered": (
            float(coverage[has_w].mean()) if has_w.any() else 0.0
        ),
        "max_coverage": int(coverage.max()),
        "mean_depth_diff_m_overlap": (
            float(depth_diff[valid_diff].mean()) if valid_diff.any() else 0.0
        ),
        "median_depth_diff_m_overlap": (
            float(np.median(depth_diff[valid_diff])) if valid_diff.any() else 0.0
        ),
        "p95_depth_diff_m_overlap": (
            float(np.percentile(depth_diff[valid_diff], 95.0))
            if valid_diff.any() else 0.0
        ),
        "fused_weight_sum": float(bayes_sum_w.sum()),
        "conf_threshold": float(conf_threshold),
        "min_distance_m": float(min_distance_m),
        "max_distance_m": float(max_distance_m),
    }

    return {
        "erp_rgb": erp_rgb_fused,
        "erp_depth": erp_depth_fused,
        "erp_conf": bayes_sum_w.astype(np.float32),
        "erp_coverage": coverage,
        "naive_erp_rgb": naive_rgb,
        "naive_erp_depth": naive_depth_out,
        "diagnostics": diagnostics,
    }
