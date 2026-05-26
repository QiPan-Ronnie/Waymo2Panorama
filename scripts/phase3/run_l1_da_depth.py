"""
N1 Phase D — Depth Anything V2 (metric outdoor) replaces LiDAR as the dense
per-pixel depth source.

DVGT (project's 5-15 brainstorm L3 first choice) is blocked tonight by auto-
mode classifier (external repo clone). DA-V2-Metric-Outdoor is the closest
substitute: same end goal (dense per-pixel depth) but installable via
HuggingFace transformers (no git clone of untrusted org needed).

Hypothesis vs Phase C LiDAR result:
- LiDAR is SPARSE on smooth car bodies → kNN-fill propagates wrong depth
- DA-V2 is DENSE per pixel → no kNN-fill needed → body depth correct
- Should fix the "body fragmentation" we saw in Phase C+N2 BMW result

Pipeline:
    1. Load AV2 frame.
    2. For each ring cam: run DA-V2-Metric-Outdoor on the raw image →
       cam-frame metric depth map (H_img, W_img).
    3. For each cam: build ERP-sized depth map by inverse-projecting ERP
       rays (legacy unit ray) to cam image and sampling DA depth.
       Convert cam-frame depth to range-from-ego using cam translation.
    4. Render each cam with N1, using ITS OWN ERP depth map (per-cam r).
    5. Multiband blend.

A/B outputs:
    l1_inf.png                 — legacy baseline
    l1_da_depth.png            — N1 + DA-V2 per-cam dense depth
    l1_da_depth_graphcut.png   — same + graphcut hard seam
    depth_viz_<cam>.png        — DA depth visualization per cam (7 files)

Usage:
    python scripts/phase3/run_l1_da_depth.py \\
        --log-dir <path> --output-dir <path> --anchor-index 0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"
DA_MODEL_ID = "depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def build_erp_depth_from_cam_depth(
    cam_depth_m: np.ndarray,    # (H_img, W_img) float32, METRIC depth from cam in meters
    K: np.ndarray,
    T_ego_cam: np.ndarray,
    erp_hw: tuple[int, int],
    fill_far_m: float = 1000.0,
) -> np.ndarray:
    """For each ERP pixel, look up the corresponding cam image pixel (via legacy
    unit-ray projection), sample DA depth, then convert to range-from-ego.

    Returns:
        erp_depth: (H_erp, W_erp) float32, depth from ego origin in meters.
                   fill_far_m where the cam doesn't cover the ERP pixel.
    """
    h_erp, w_erp = erp_hw
    h_img, w_img = cam_depth_m.shape[:2]

    # Build ERP grid (matches sphere_projection.py conventions)
    u_idx = np.arange(w_erp, dtype=np.float64)
    v_idx = np.arange(h_erp, dtype=np.float64)
    uu, vv = np.meshgrid(u_idx, v_idx)
    theta = np.pi - (uu + 0.5) / w_erp * (2.0 * np.pi)
    phi = (np.pi / 2.0) - (vv + 0.5) / h_erp * np.pi
    cos_phi = np.cos(phi)
    d_ego = np.stack([cos_phi * np.cos(theta), cos_phi * np.sin(theta), np.sin(phi)], axis=-1)

    R_ego_cam = T_ego_cam[:3, :3]
    R_cam_ego = R_ego_cam.T
    t_ego_cam = T_ego_cam[:3, 3]
    d_cam = d_ego @ R_cam_ego.T  # unit ray in cam frame (legacy projection)
    z_cam = d_cam[..., 2]
    in_front = z_cam > 1e-6
    z_safe = np.where(in_front, z_cam, 1.0)
    u_img = K[0, 0] * (d_cam[..., 0] / z_safe) + K[0, 2]
    v_img = K[1, 1] * (d_cam[..., 1] / z_safe) + K[1, 2]
    in_bounds = (u_img >= 0.5) & (u_img <= w_img - 1.5) & (v_img >= 0.5) & (v_img <= h_img - 1.5)
    valid = in_front & in_bounds

    # Sample DA depth at (u_img, v_img) — bilinear interpolation via cv2
    import cv2
    map_x = np.where(valid, u_img, -1.0).astype(np.float32)
    map_y = np.where(valid, v_img, -1.0).astype(np.float32)
    cam_depth_at_erp = cv2.remap(
        cam_depth_m.astype(np.float32), map_x, map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0.0,
    )
    # cam_depth_at_erp is in METRIC meters but in the cam's z-direction.
    # The depth we want for N1 projection is "distance from ego origin" along d_ego.
    # For a 3D point at depth Z_cam in cam frame (= cam_depth_at_erp):
    #   P_cam = (X, Y, Z) such that Z = cam_depth_at_erp, and X/Z, Y/Z = sampled (u, v)
    #   P_ego = R_ego_cam @ P_cam + t_ego_cam
    #   r_ego = |P_ego|
    # We have d_cam (unit ray in cam frame). The 3D point in cam frame is
    #   P_cam = d_cam * (Z / d_cam[..., 2])  ← scale unit ray to hit depth Z
    # i.e. P_cam = d_cam * (cam_depth / z_cam_unit_ray_z_component)
    # Then P_ego = R_ego_cam @ P_cam + t_ego_cam, r_ego = |P_ego|.
    scale = np.where(in_front, cam_depth_at_erp / np.maximum(z_cam, 1e-6), 0.0)
    P_cam = d_cam * scale[..., None]  # (H, W, 3) — 3D point in cam frame
    # Rotate to ego frame: P_ego = R_ego_cam @ P_cam
    # For each pixel: P_ego = R_ego_cam @ P_cam. Vectorized: P_ego = P_cam @ R_ego_cam.T = P_cam @ R_cam_ego
    P_ego = P_cam @ R_cam_ego  # = (R_ego_cam @ P_cam) per pixel  — note: R_cam_ego = R_ego_cam.T, so V @ R_cam_ego = R_ego_cam @ V
    # Wait — be careful: V @ M.T = M @ V for 1D V. Here we want R_ego_cam @ P_cam = P_cam @ R_ego_cam.T = P_cam @ R_cam_ego.
    # So P_ego = P_cam @ R_cam_ego. ✓ (R_cam_ego is the .T of R_ego_cam)
    P_ego = P_ego + t_ego_cam  # add cam translation
    r_ego = np.linalg.norm(P_ego, axis=-1)  # range from ego origin

    erp_depth = np.where(valid & (cam_depth_at_erp > 0.1), r_ego, fill_far_m).astype(np.float32)
    return erp_depth


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--anchor-index", type=int, default=0)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--da-model", default=DA_MODEL_ID)
    ap.add_argument("--max-depth-m", type=float, default=80.0,
                    help="Clamp DA outputs above this (outdoor scenes).")
    ap.add_argument("--fill-far-m", type=float, default=1000.0)
    ap.add_argument("--also-graphcut", action="store_true",
                    help="Also render the +graphcut variant (slow on scipy fallback).")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent / DEFAULT_W2P_CODE_REL)
    args = ap.parse_args()

    _wire_imports(args.w2p_code)
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.multiband import multiband_blend

    args.output_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    # Load frame
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    anchor_ts = ts_all[args.anchor_index]
    frame = loader.load_synced_frame(anchor_ts)
    print(f"[DA] loaded anchor {args.anchor_index}", flush=True)

    # Load DA pipeline (HF transformers)
    from transformers import pipeline
    t0 = time.time()
    da = pipeline(task="depth-estimation", model=args.da_model, device=0)
    print(f"[DA] loaded {args.da_model} in {time.time()-t0:.1f}s", flush=True)

    # Run DA on each cam, build per-cam ERP depth, render
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    alphas: list[np.ndarray] = []
    cam_summaries = []

    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        img_arr = frame.images[cam]

        # DA inference
        t0 = time.time()
        pil_img = Image.fromarray(img_arr)
        result = da(pil_img)
        # result["predicted_depth"] is a tensor (H, W) — for metric variant this is METERS
        depth_pil = result["depth"]  # PIL image (visualization, normalized)
        # Use predicted_depth tensor for actual values
        import torch
        depth_tensor = result.get("predicted_depth")
        if depth_tensor is None:
            depth_arr = np.asarray(depth_pil, dtype=np.float32)
        else:
            depth_arr = depth_tensor.cpu().numpy().astype(np.float32)
            if depth_arr.ndim == 3:
                depth_arr = depth_arr[0]
        # Resize to cam image shape if different
        h_img, w_img = img_arr.shape[:2]
        if depth_arr.shape[:2] != (h_img, w_img):
            import cv2
            depth_arr = cv2.resize(depth_arr, (w_img, h_img), interpolation=cv2.INTER_LINEAR)
        # Clamp + fill
        depth_arr = np.clip(depth_arr, 0.5, args.max_depth_m)
        t_da = time.time() - t0
        print(f"[DA]   {cam}: DA={t_da:.2f}s, depth range [{depth_arr.min():.1f}, {depth_arr.max():.1f}] m",
              flush=True)

        # Build ERP-sized depth map for THIS cam
        t0 = time.time()
        erp_depth_this_cam = build_erp_depth_from_cam_depth(
            depth_arr, calib.K, calib.T_ego_cam, erp_hw=erp_hw,
            fill_far_m=args.fill_far_m,
        )
        t_erp_depth = time.time() - t0

        # Render with this cam's ERP depth
        t0 = time.time()
        rgb, alpha, w = render_camera_to_erp(
            image=img_arr, K=calib.K, T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=erp_depth_this_cam,
        )
        t_render = time.time() - t0

        slabs.append(rgb)
        alphas.append(alpha)
        weights.append(w)
        cam_summaries.append({
            "cam": cam,
            "da_time_s": round(t_da, 2),
            "erp_depth_time_s": round(t_erp_depth, 2),
            "render_time_s": round(t_render, 2),
            "depth_min_m": float(depth_arr.min()),
            "depth_max_m": float(depth_arr.max()),
            "depth_median_m": float(np.median(depth_arr)),
        })

    # Multiband blend (cos² weights, N1+DA depth)
    t0 = time.time()
    erp_da = multiband_blend(slabs, weights, num_bands=args.num_bands, wrap=True)
    Image.fromarray(erp_da).save(args.output_dir / "l1_da_depth.png")
    t_blend = time.time() - t0
    print(f"[DA] blend (cos²): {t_blend:.1f}s -> l1_da_depth.png", flush=True)

    # Baseline legacy for A/B
    t0 = time.time()
    base_slabs, base_weights = [], []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        base_slabs.append(rgb); base_weights.append(w)
    erp_inf = multiband_blend(base_slabs, base_weights, num_bands=args.num_bands, wrap=True)
    Image.fromarray(erp_inf).save(args.output_dir / "l1_inf.png")
    print(f"[DA] baseline: {time.time()-t0:.1f}s -> l1_inf.png", flush=True)

    # Optional graphcut variant
    t_gc_total = 0.0
    if args.also_graphcut:
        from waymo2panorama.blending.graphcut_seam import (
            apply_graphcut_seams, _erp_column_for_axis_world,
        )
        cam_axes_erp = [
            _erp_column_for_axis_world(frame.calibrations[c].T_ego_cam, args.erp_w)
            for c in RING_CAMS_7
        ]
        t0 = time.time()
        hard_weights = apply_graphcut_seams(
            slabs=slabs, alphas=alphas, cos2_weights=weights,
            cam_axes_erp=cam_axes_erp, feather_sigma=3.0,
        )
        t_gc_total = time.time() - t0
        erp_gc = multiband_blend(slabs, hard_weights, num_bands=args.num_bands, wrap=True)
        Image.fromarray(erp_gc).save(args.output_dir / "l1_da_depth_graphcut.png")
        print(f"[DA] +graphcut: {t_gc_total:.1f}s -> l1_da_depth_graphcut.png", flush=True)

    # Thumbs for download
    for fn in ["l1_inf.png", "l1_da_depth.png"] + (
        ["l1_da_depth_graphcut.png"] if args.also_graphcut else []
    ):
        im = Image.open(args.output_dir / fn).copy()
        im.thumbnail((1024, 512))
        im.save(args.output_dir / fn.replace(".png", "_thumb.png"))

    summary = {
        "log_dir": str(args.log_dir),
        "anchor_index": args.anchor_index,
        "erp_hw": list(erp_hw),
        "da_model": args.da_model,
        "max_depth_m": args.max_depth_m,
        "cam_summaries": cam_summaries,
        "also_graphcut": args.also_graphcut,
        "graphcut_time_s": round(t_gc_total, 2),
    }
    with open(args.output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"[DA] done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
