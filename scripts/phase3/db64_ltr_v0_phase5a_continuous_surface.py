from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from db64_ltr_v0_phase4b_z_visibility_cause import (
    ColabClient,
    poll_job,
    rel,
    safe_status,
    sanitize,
    secret_hits,
)


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "layered_target_raycaster" / "db64_ltr_v0" / "phase5a_continuous_surface"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db64_ltr_v0/phase5a_continuous_surface"
REMOTE_RESULT = REMOTE_OUT + "/db64_phase5a_continuous_surface_remote_result.json"
REMOTE_SUMMARY = REMOTE_OUT + "/batch_summary.json"

LOCAL_REMOTE_RESULT = OUT_DIR / "db64_phase5a_continuous_surface_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "db64_phase5a_batch_summary.json"
MANIFEST = OUT_DIR / "db64_phase5a_continuous_surface_manifest.json"
BOARD = OUT_DIR / "db64_phase5a_continuous_surface_board.jpg"
FETCH_DIR = OUT_DIR / "fetch"

CASES = ["02a00399:0:bmw", "0bae3b5e:30:clean_far"]
RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]
REQUIRED_MAPS = [
    "current_support_map",
    "fused_support_map",
    "temporal_support_count_map",
    "surface_hypothesis_id_map",
    "surface_confidence_map",
    "raw_projection_valid_count_map",
    "current_zbuffer_visible_count_map",
    "current_z_cause_primary_map",
    "fused_z_cause_primary_map",
    "current_z_repairability_map",
    "fused_z_repairability_map",
    "before_after_transition_map",
    "protected_veto_proxy_map",
    "z_residual_min_cm_u16",
]


def remote_python() -> str:
    return r'''
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REMOTE_OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/layered_target_raycaster/db64_ltr_v0/phase5a_continuous_surface")
REMOTE_RESULT = REMOTE_OUT / "db64_phase5a_continuous_surface_remote_result.json"
AV2_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [
    Path("/content/waymo2panorama"),
    Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama"),
]
CASES = ["02a00399:0:bmw", "0bae3b5e:30:clean_far"]
RUN_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far"]
ACCUM_SWEEPS = 5
REQUIRED_MAPS = [
    "current_support_map",
    "fused_support_map",
    "temporal_support_count_map",
    "surface_hypothesis_id_map",
    "surface_confidence_map",
    "raw_projection_valid_count_map",
    "current_zbuffer_visible_count_map",
    "current_z_cause_primary_map",
    "fused_z_cause_primary_map",
    "current_z_repairability_map",
    "fused_z_repairability_map",
    "before_after_transition_map",
    "protected_veto_proxy_map",
    "z_residual_min_cm_u16",
]


def tail(text, limit=12000):
    if text is None:
        return ""
    return str(text)[-limit:]


def json_safe(obj):
    import numpy as np
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        return val if np.isfinite(val) else None
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def find_workdir():
    for cand in WORKDIR_CANDIDATES:
        if (cand / "scripts" / "phase3" / "test_lidar_zbuffer_seam.py").exists():
            return cand
    return None


def file_row(path):
    return {
        "exists": path.exists(),
        "bytes": int(path.stat().st_size) if path.exists() and path.is_file() else None,
        "path": str(path),
    }


def _se3(qw, qx, qy, qz, tx, ty, tz):
    import numpy as np
    R = np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ], dtype=np.float64)
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = R
    T[:3, 3] = [tx, ty, tz]
    return T


def read_lidar_points(path):
    import numpy as np
    import pandas as pd
    df = pd.read_feather(path)
    if {"x", "y", "z"}.issubset(df.columns):
        return np.stack([df["x"].to_numpy(), df["y"].to_numpy(), df["z"].to_numpy()], axis=1).astype(np.float64)
    arr = df.to_numpy()
    if arr.ndim != 2 or arr.shape[1] < 3:
        raise ValueError(f"unexpected lidar feather columns for {path}: {list(df.columns)}")
    return np.asarray(arr[:, :3], dtype=np.float64)


def read_pose_table(log_dir):
    import numpy as np
    import pandas as pd
    pose_path = Path(log_dir) / "city_SE3_egovehicle.feather"
    if not pose_path.exists():
        raise FileNotFoundError(f"missing city poses: {pose_path}")
    df = pd.read_feather(pose_path).sort_values("timestamp_ns").reset_index(drop=True)
    ts = df["timestamp_ns"].to_numpy(dtype=np.int64)
    mats = []
    for row in df.itertuples(index=False):
        mats.append(_se3(row.qw, row.qx, row.qy, row.qz, row.tx_m, row.ty_m, row.tz_m))
    return ts, np.stack(mats, axis=0)


def nearest_pose(pose_ts, pose_mats, ts_ns):
    import numpy as np
    idx = int(np.argmin(np.abs(pose_ts - int(ts_ns))))
    return pose_mats[idx], int(pose_ts[idx]), idx


def load_current_and_fused_lidar(log_dir, anchor_ts_ns, accum_sweeps=5):
    import numpy as np
    sweep_dir = Path(log_dir) / "sensors" / "lidar"
    sweeps = sorted(sweep_dir.glob("*.feather"))
    if not sweeps:
        raise FileNotFoundError(f"no lidar sweeps in {sweep_dir}")
    sweep_ts = np.array([int(p.stem) for p in sweeps], dtype=np.int64)
    i0 = int(np.argmin(np.abs(sweep_ts - int(anchor_ts_ns))))
    pose_ts, pose_mats = read_pose_table(log_dir)
    anchor_pose_city, anchor_pose_ts, _ = nearest_pose(pose_ts, pose_mats, int(sweep_ts[i0]))
    T_anchor_ego_city = np.linalg.inv(anchor_pose_city)

    half = max(0, int(accum_sweeps) // 2)
    lo = max(0, i0 - half)
    hi = min(len(sweeps), i0 + half + 1)
    entries = []
    fused = []
    for j in range(lo, hi):
        pts = read_lidar_points(sweeps[j])
        pose_city, pose_match_ts, _pose_idx = nearest_pose(pose_ts, pose_mats, int(sweep_ts[j]))
        T_anchor_ego_sweep_ego = T_anchor_ego_city @ pose_city
        pts_anchor = pts @ T_anchor_ego_sweep_ego[:3, :3].T + T_anchor_ego_sweep_ego[:3, 3]
        ego_delta_m = float(np.linalg.norm(T_anchor_ego_sweep_ego[:3, 3]))
        entries.append(
            {
                "sweep_index": int(j),
                "offset_from_nearest": int(j - i0),
                "sweep_ts_ns": int(sweep_ts[j]),
                "pose_match_ts_ns": int(pose_match_ts),
                "pose_dt_ms": float((pose_match_ts - int(sweep_ts[j])) / 1e6),
                "anchor_pose_ts_ns": int(anchor_pose_ts),
                "ego_delta_m": ego_delta_m,
                "n_points": int(pts.shape[0]),
                "points_anchor": pts_anchor,
            }
        )
        fused.append(pts_anchor)
    return {
        "nearest_index": int(i0),
        "nearest_sweep_ts_ns": int(sweep_ts[i0]),
        "nearest_delta_ms": float(abs(int(sweep_ts[i0]) - int(anchor_ts_ns)) / 1e6),
        "entries": entries,
        "current_points": entries[half if lo + half == i0 else int(i0 - lo)]["points_anchor"],
        "fused_points": np.concatenate(fused, axis=0),
    }


def project_sparse_support_count(points_by_sweep, erp_hw, min_range_m=0.5, max_range_m=80.0, dilate_px=3):
    import cv2
    import numpy as np
    h_erp, w_erp = erp_hw
    count = np.zeros((h_erp, w_erp), dtype=np.uint8)
    for pts in points_by_sweep:
        ranges = np.linalg.norm(pts, axis=1)
        keep = (ranges >= min_range_m) & (ranges <= max_range_m)
        p = pts[keep]
        if p.size == 0:
            continue
        x, y, z = p[:, 0], p[:, 1], p[:, 2]
        horiz = np.sqrt(x * x + y * y)
        theta = np.arctan2(y, x)
        phi = np.arctan2(z, horiz)
        u = (np.pi - theta) / (2.0 * np.pi) * w_erp - 0.5
        v = (np.pi / 2.0 - phi) / np.pi * h_erp - 0.5
        uu = np.mod(np.round(u).astype(np.int32), w_erp)
        vv = np.round(v).astype(np.int32)
        good = (vv >= 0) & (vv < h_erp)
        hit = np.zeros((h_erp, w_erp), dtype=np.uint8)
        hit[vv[good], uu[good]] = 1
        if dilate_px > 0:
            k = np.ones((2 * int(dilate_px) + 1, 2 * int(dilate_px) + 1), dtype=np.uint8)
            hit = cv2.dilate(hit, k, iterations=1)
        count = np.clip(count.astype(np.int16) + hit.astype(np.int16), 0, 255).astype(np.uint8)
    return count


def save_u8(path, arr):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype("uint8")).save(path)


def save_u16(path, arr):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype("uint16")).save(path)


def colorize(arr, palette, default=(40, 42, 48)):
    import numpy as np
    out = np.zeros((*arr.shape, 3), dtype=np.uint8)
    out[:] = np.array(default, dtype=np.uint8)
    for key, color in palette.items():
        out[arr == int(key)] = np.array(color, dtype=np.uint8)
    return out


def source_boundary(label, valid, seam_band):
    import cv2
    import numpy as np
    boundary = np.zeros(label.shape, dtype=bool)
    diff_x = (label[:, 1:] != label[:, :-1]) & valid[:, 1:] & valid[:, :-1]
    boundary[:, 1:] |= diff_x
    boundary[:, :-1] |= diff_x
    diff_y = (label[1:, :] != label[:-1, :]) & valid[1:, :] & valid[:-1, :]
    boundary[1:, :] |= diff_y
    boundary[:-1, :] |= diff_y
    boundary &= seam_band.astype(bool)
    return cv2.dilate(boundary.astype("uint8"), np.ones((5, 5), "uint8"), iterations=1).astype(bool)


def frac(mask, denom=None):
    import numpy as np
    m = np.asarray(mask).astype(bool)
    if denom is None:
        return float(m.mean())
    d = np.asarray(denom).astype(bool)
    n = int(d.sum())
    if n <= 0:
        return None
    return float((m & d).sum() / n)


def unique_counts(arr):
    import numpy as np
    vals, counts = np.unique(arr.reshape(-1), return_counts=True)
    return {str(int(v)): int(c) for v, c in zip(vals, counts)}


def longest_supported_component_fraction(support, seam_denom):
    import numpy as np
    columns = np.any(np.asarray(support).astype(bool) & np.asarray(seam_denom).astype(bool), axis=0)
    total = int(np.any(np.asarray(seam_denom).astype(bool), axis=0).sum())
    best = 0
    cur = 0
    for val in columns.tolist():
        if val:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return float(best / max(1, total)), int(best), total


def evaluate_surface_depth(depth_map, zbuffers, images, Ks, Ts, source_valid, seam_band, boundary):
    import cv2
    import numpy as np
    from waymo2panorama.projection.lidar_zbuffer_layer import erp_dirs_ego

    H, W = depth_map.shape
    support = np.isfinite(depth_map) & (depth_map < 120.0)
    dirs = erp_dirs_ego((H, W))
    p_ego = dirs * depth_map.astype(np.float32)[..., None]

    geom_valid_count = np.zeros((H, W), dtype=np.uint8)
    zbuffer_hit_count = np.zeros((H, W), dtype=np.uint8)
    z_mismatch_count = np.zeros((H, W), dtype=np.uint8)
    visible_count = np.zeros((H, W), dtype=np.uint8)
    min_z_resid = np.full((H, W), np.inf, dtype=np.float32)

    for image, K, T, zbuf in zip(images, Ks, Ts, zbuffers):
        h_img, w_img = image.shape[:2]
        r_cam_ego = T[:3, :3].T
        t_ego_cam = T[:3, 3].astype(np.float32)
        p_cam = (p_ego - t_ego_cam[None, None, :]) @ r_cam_ego.T.astype(np.float32)
        z = p_cam[..., 2]
        in_front = z > 1e-6
        z_safe = np.where(in_front, z, 1.0)
        u_img = K[0, 0] * (p_cam[..., 0] / z_safe) + K[0, 2]
        v_img = K[1, 1] * (p_cam[..., 1] / z_safe) + K[1, 2]
        in_bounds = (
            (u_img >= 0.5)
            & (u_img <= w_img - 1.5)
            & (v_img >= 0.5)
            & (v_img <= h_img - 1.5)
        )
        norm = np.linalg.norm(p_cam, axis=-1)
        cam_cos = np.where(norm > 1e-9, z / np.maximum(norm, 1e-9), 0.0)
        geom_valid = support & in_front & in_bounds & (cam_cos >= 0.03)

        map_x = np.where(geom_valid, u_img, -1.0).astype(np.float32)
        map_y = np.where(geom_valid, v_img, -1.0).astype(np.float32)
        sampled_z = cv2.remap(
            zbuf.depth_z_m,
            map_x,
            map_y,
            interpolation=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=1.0e6,
        )
        has_zbuf = geom_valid & (sampled_z < 1.0e6)
        tol = np.maximum(0.9, 0.05 * np.maximum(z, 0.0))
        resid = np.abs(z - sampled_z)
        z_match = has_zbuf & (resid <= tol)
        z_mismatch = has_zbuf & (~z_match)
        visible = geom_valid & z_match

        geom_valid_count += geom_valid.astype(np.uint8)
        zbuffer_hit_count += has_zbuf.astype(np.uint8)
        z_mismatch_count += z_mismatch.astype(np.uint8)
        visible_count += visible.astype(np.uint8)
        min_z_resid = np.minimum(min_z_resid, np.where(has_zbuf, resid.astype(np.float32), np.inf))

    no_surface = source_valid & (~support)
    visible_ge2 = source_valid & support & (visible_count >= 2)
    visible_any = source_valid & support & (visible_count > 0)
    single_visible = source_valid & support & (visible_count == 1)
    no_geom = source_valid & support & (visible_count == 0) & (geom_valid_count == 0)
    no_zbuf = source_valid & support & (visible_count == 0) & (geom_valid_count > 0) & (zbuffer_hit_count == 0)
    z_conflict = source_valid & support & (visible_count == 0) & (z_mismatch_count > 0)
    mixed_no_visible = source_valid & support & (visible_count == 0) & (~no_geom) & (~no_zbuf) & (~z_conflict)

    cause = np.full((H, W), 255, dtype=np.uint8)
    cause[no_surface] = 20
    cause[no_geom] = 41
    cause[no_zbuf] = 42
    cause[z_conflict] = 43
    cause[mixed_no_visible] = 44
    cause[single_visible] = 1
    cause[visible_ge2] = 0
    cause[boundary & visible_ge2] = 60

    repairability = np.zeros((H, W), dtype=np.uint8)
    target = source_valid & seam_band
    repairability[target & visible_ge2 & (~boundary)] = 1
    repairability[target & single_visible & (~boundary)] = 2
    repairability[target & no_surface & (~boundary)] = 3
    repairability[target & (no_geom | no_zbuf | z_conflict | mixed_no_visible)] = 5
    repairability[target & boundary] = 5

    seam_denom = source_valid & seam_band
    residual_vals = min_z_resid[np.isfinite(min_z_resid) & seam_denom]
    lcf, lpx, tpx = longest_supported_component_fraction(support, seam_denom)
    stats = {
        "seam_lidar_support_frac": frac(support, seam_denom),
        "seam_visible_any_frac": frac(visible_any, seam_denom),
        "seam_visible_ge2_frac": frac(visible_ge2, seam_denom),
        "seam_single_visible_frac": frac(single_visible, seam_denom),
        "seam_no_surface_frac": frac(no_surface, seam_denom),
        "seam_no_camera_geom_valid_frac": frac(no_geom, seam_denom),
        "seam_no_raw_zbuffer_support_frac": frac(no_zbuf, seam_denom),
        "seam_z_mismatch_conflict_frac": frac(z_conflict, seam_denom),
        "seam_mixed_no_visible_frac": frac(mixed_no_visible, seam_denom),
        "seam_source_boundary_proxy_frac": frac(boundary, seam_denom),
        "longest_supported_component_frac": lcf,
        "longest_supported_component_px": lpx,
        "seam_length_px": tpx,
        "z_residual_min_m_seam": {
            "n": int(residual_vals.size),
            "mean": float(residual_vals.mean()) if residual_vals.size else None,
            "p50": float(np.percentile(residual_vals, 50)) if residual_vals.size else None,
            "p90": float(np.percentile(residual_vals, 90)) if residual_vals.size else None,
            "p95": float(np.percentile(residual_vals, 95)) if residual_vals.size else None,
        },
    }
    return {
        "support": support,
        "cause": cause,
        "repairability": repairability,
        "geom_valid_count": geom_valid_count,
        "zbuffer_hit_count": zbuffer_hit_count,
        "z_mismatch_count": z_mismatch_count,
        "visible_count": visible_count,
        "min_z_resid": min_z_resid,
        "stats": stats,
    }


def overlay_mask(rgb, mask, color=(40, 230, 120), alpha=0.58):
    import numpy as np
    out = np.clip(rgb, 0, 255).astype(np.uint8).copy()
    m = np.asarray(mask).astype(bool)
    if m.any():
        c = np.array(color, dtype=np.float32)
        out[m] = np.clip(out[m].astype(np.float32) * (1.0 - alpha) + c * alpha, 0, 255).astype(np.uint8)
    return out


def one_case(case_spec, av2_root, out_root):
    import cv2
    import numpy as np
    from depth_visibility_seam_probe import _parse_case
    from seam_confidence_map import _crop_stack, _default_crops, _heatmap_u8, _resize_w, _save_rgb, _stack_named
    from test_lidar_zbuffer_seam import _seam_masks, _winner_label
    from waymo2panorama.blending.hard_hdr_of import hard_select
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.depth.lidar_to_erp_depth import project_lidar_to_erp_depth, visualize_depth_map
    from waymo2panorama.projection.lidar_zbuffer_layer import build_ring_zbuffers
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    t0 = time.time()
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, av2_root)
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    out_dir = out_root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    erp_hw = (1024, 2048)
    loader = AV2RingLoader(log_dir)
    anchor_ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)

    slabs, weights, images, Ks, Ts = [], [], [], [], []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=frame.images[cam],
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
        images.append(frame.images[cam])
        Ks.append(calib.K)
        Ts.append(calib.T_ego_cam)

    hard = hard_select(slabs, weights)
    sphere_label, source_valid = _winner_label(weights)
    seam_band, seam_core, seam_diag = _seam_masks(weights, band_half_width=48, core_half_width=2)
    boundary = source_boundary(sphere_label, source_valid, seam_band)

    fused = load_current_and_fused_lidar(log_dir, anchor_ts, accum_sweeps=ACCUM_SWEEPS)
    current_pts = fused["current_points"]
    fused_pts = fused["fused_points"]
    current_depth, current_depth_summary = project_lidar_to_erp_depth(
        current_pts,
        erp_hw=erp_hw,
        min_range_m=0.5,
        max_range_m=80.0,
        densify_radius_px=8,
        fill_far_m=1000.0,
    )
    fused_depth, fused_depth_summary = project_lidar_to_erp_depth(
        fused_pts,
        erp_hw=erp_hw,
        min_range_m=0.5,
        max_range_m=80.0,
        densify_radius_px=8,
        fill_far_m=1000.0,
    )
    temporal_count = project_sparse_support_count([e["points_anchor"] for e in fused["entries"]], erp_hw, dilate_px=3)
    zbuffers_current = build_ring_zbuffers(
        current_pts,
        images,
        Ks,
        Ts,
        min_range_m=0.5,
        max_range_m=80.0,
        dilation_px=5,
    )
    current_eval = evaluate_surface_depth(current_depth, zbuffers_current, images, Ks, Ts, source_valid, seam_band, boundary)
    fused_eval = evaluate_surface_depth(fused_depth, zbuffers_current, images, Ks, Ts, source_valid, seam_band, boundary)

    current_support = current_eval["support"]
    fused_support = fused_eval["support"]
    surface_id = np.zeros(current_support.shape, dtype=np.uint8)
    surface_id[current_support & fused_support] = 1
    surface_id[fused_support & (~current_support)] = 2
    surface_id[current_support & (~fused_support)] = 3
    confidence = np.clip(temporal_count.astype(np.float32) / float(max(1, ACCUM_SWEEPS)), 0.0, 1.0)
    confidence_u8 = np.clip(confidence * 255.0, 0, 255).astype(np.uint8)

    transition = np.zeros(current_support.shape, dtype=np.uint8)
    cur_rep = current_eval["repairability"]
    fus_rep = fused_eval["repairability"]
    target = source_valid & seam_band
    transition[target & (cur_rep == fus_rep)] = 1
    transition[target & (cur_rep >= 3) & (fus_rep <= 2)] = 2
    transition[target & (cur_rep <= 2) & (fus_rep >= 3)] = 3
    transition[target & boundary] = 5

    fused_z_cm = np.where(
        np.isfinite(fused_eval["min_z_resid"]),
        np.clip(fused_eval["min_z_resid"] * 100.0, 0, 65535),
        65535,
    ).astype(np.uint16)

    maps = {
        "current_support_map": current_support.astype(np.uint8) * 255,
        "fused_support_map": fused_support.astype(np.uint8) * 255,
        "temporal_support_count_map": np.clip(temporal_count, 0, 255).astype(np.uint8),
        "surface_hypothesis_id_map": surface_id,
        "surface_confidence_map": confidence_u8,
        "raw_projection_valid_count_map": fused_eval["geom_valid_count"],
        "current_zbuffer_visible_count_map": fused_eval["visible_count"],
        "current_z_cause_primary_map": current_eval["cause"],
        "fused_z_cause_primary_map": fused_eval["cause"],
        "current_z_repairability_map": cur_rep,
        "fused_z_repairability_map": fus_rep,
        "before_after_transition_map": transition,
        "protected_veto_proxy_map": boundary.astype(np.uint8) * 255,
    }
    for name, arr in maps.items():
        save_u8(out_dir / f"{run_name}_{name}.png", arr)
    save_u16(out_dir / f"{run_name}_z_residual_min_cm_u16.png", fused_z_cm)

    cause_palette = {
        0: (70, 220, 120),
        1: (230, 220, 70),
        20: (245, 170, 60),
        41: (160, 120, 220),
        42: (240, 110, 55),
        43: (210, 70, 230),
        44: (90, 170, 230),
        60: (255, 70, 95),
        255: (60, 64, 72),
    }
    repair_palette = {
        0: (50, 85, 150),
        1: (70, 220, 120),
        2: (230, 215, 70),
        3: (245, 170, 60),
        5: (255, 75, 95),
    }
    transition_palette = {
        0: (35, 38, 44),
        1: (90, 120, 170),
        2: (70, 220, 120),
        3: (255, 110, 75),
        5: (255, 75, 95),
    }
    surface_palette = {
        0: (35, 38, 44),
        1: (70, 220, 120),
        2: (240, 170, 55),
        3: (100, 150, 240),
    }
    current_cause_viz = colorize(current_eval["cause"], cause_palette)
    fused_cause_viz = colorize(fused_eval["cause"], cause_palette)
    current_repair_viz = colorize(cur_rep, repair_palette)
    fused_repair_viz = colorize(fus_rep, repair_palette)
    transition_viz = colorize(transition, transition_palette)
    surface_id_viz = colorize(surface_id, surface_palette)
    temporal_count_viz = _heatmap_u8(np.clip(temporal_count.astype(np.float32) / float(max(1, ACCUM_SWEEPS)), 0, 1))
    visible_viz = _heatmap_u8(np.clip(fused_eval["visible_count"].astype(np.float32) / 3.0, 0, 1))
    geom_viz = _heatmap_u8(np.clip(fused_eval["geom_valid_count"].astype(np.float32) / 7.0, 0, 1))
    fused_depth_viz = visualize_depth_map(fused_depth, log_clip_m=80.0)
    support_overlay = overlay_mask(hard, fused_support & seam_band & source_valid, color=(40, 230, 120), alpha=0.50)
    visible_overlay = overlay_mask(hard, (fused_eval["visible_count"] > 0) & seam_band & source_valid, color=(40, 210, 255), alpha=0.58)
    veto_overlay = overlay_mask(hard, boundary & seam_band & source_valid, color=(255, 70, 95), alpha=0.60)

    save_u8(out_dir / f"{run_name}_current_z_cause_primary_viz.png", current_cause_viz)
    save_u8(out_dir / f"{run_name}_fused_z_cause_primary_viz.png", fused_cause_viz)
    save_u8(out_dir / f"{run_name}_fused_z_repairability_viz.png", fused_repair_viz)
    save_u8(out_dir / f"{run_name}_before_after_transition_viz.png", transition_viz)
    save_u8(out_dir / f"{run_name}_phase5a_support_overlay.png", support_overlay)
    save_u8(out_dir / f"{run_name}_phase5a_visible_overlay.png", visible_overlay)

    review = _stack_named(
        [
            ("hard_select control", _resize_w(hard, 768)),
            ("Phase5a seam-result panel: fused support overlay, no RGB replacement", _resize_w(support_overlay, 768)),
            ("current Phase4b-like z cause", _resize_w(current_cause_viz, 768)),
            ("fused-surface z cause vs current zbuffer", _resize_w(fused_cause_viz, 768)),
            ("surface hypothesis id", _resize_w(surface_id_viz, 768)),
            ("temporal support count", _resize_w(temporal_count_viz, 768)),
            ("raw projection valid count", _resize_w(geom_viz, 768)),
            ("current zbuffer visible count", _resize_w(visible_viz, 768)),
            ("fused repairability policy", _resize_w(fused_repair_viz, 768)),
            ("before/after transition", _resize_w(transition_viz, 768)),
            ("minimal protected/source-boundary veto proxy", _resize_w(veto_overlay, 768)),
            ("fused LiDAR depth", _resize_w(fused_depth_viz, 768)),
        ]
    )
    _save_rgb(out_dir / f"{run_name}_phase5a_evidence_review_768.jpg", review, quality=88)

    crops = _default_crops(*hard.shape[:2])
    crop_board = _crop_stack(
        [
            ("hard_select", hard),
            ("support overlay no RGB edit", support_overlay),
            ("visible overlay no RGB edit", visible_overlay),
            ("current z cause", current_cause_viz),
            ("fused z cause", fused_cause_viz),
            ("transition", transition_viz),
        ],
        crops,
    )
    _save_rgb(out_dir / f"{run_name}_phase5a_crop_review.jpg", crop_board, quality=88)

    cur = current_eval["stats"]
    fus = fused_eval["stats"]
    improvements = {
        "delta_no_surface_frac": (cur.get("seam_no_surface_frac") - fus.get("seam_no_surface_frac")) if isinstance(cur.get("seam_no_surface_frac"), (int, float)) and isinstance(fus.get("seam_no_surface_frac"), (int, float)) else None,
        "gain_visible_any_frac": (fus.get("seam_visible_any_frac") - cur.get("seam_visible_any_frac")) if isinstance(cur.get("seam_visible_any_frac"), (int, float)) and isinstance(fus.get("seam_visible_any_frac"), (int, float)) else None,
        "gain_visible_ge2_frac": (fus.get("seam_visible_ge2_frac") - cur.get("seam_visible_ge2_frac")) if isinstance(cur.get("seam_visible_ge2_frac"), (int, float)) and isinstance(fus.get("seam_visible_ge2_frac"), (int, float)) else None,
        "delta_no_raw_zbuffer_support_frac": (cur.get("seam_no_raw_zbuffer_support_frac") - fus.get("seam_no_raw_zbuffer_support_frac")) if isinstance(cur.get("seam_no_raw_zbuffer_support_frac"), (int, float)) and isinstance(fus.get("seam_no_raw_zbuffer_support_frac"), (int, float)) else None,
        "delta_z_mismatch_conflict_frac": (fus.get("seam_z_mismatch_conflict_frac") - cur.get("seam_z_mismatch_conflict_frac")) if isinstance(cur.get("seam_z_mismatch_conflict_frac"), (int, float)) and isinstance(fus.get("seam_z_mismatch_conflict_frac"), (int, float)) else None,
        "fused_longest_supported_component_frac": fus.get("longest_supported_component_frac"),
    }
    success_checks = {
        "bmw_no_surface_drop": bool(run_name.startswith("02a00399") and ((improvements["delta_no_surface_frac"] is not None and improvements["delta_no_surface_frac"] >= 0.15) or (fus.get("seam_no_surface_frac") is not None and fus.get("seam_no_surface_frac") <= 0.40))),
        "bmw_visible_gain": bool(run_name.startswith("02a00399") and ((improvements["gain_visible_any_frac"] is not None and improvements["gain_visible_any_frac"] >= 0.10) or (fus.get("seam_visible_ge2_frac") is not None and fus.get("seam_visible_ge2_frac") >= 0.15))),
        "bmw_no_zbuf_drop": bool(run_name.startswith("02a00399") and ((improvements["delta_no_raw_zbuffer_support_frac"] is not None and improvements["delta_no_raw_zbuffer_support_frac"] >= 0.07) or (fus.get("seam_no_raw_zbuffer_support_frac") is not None and fus.get("seam_no_raw_zbuffer_support_frac") <= 0.12))),
        "bmw_component_continuity": bool(run_name.startswith("02a00399") and fus.get("longest_supported_component_frac") is not None and fus.get("longest_supported_component_frac") >= 0.25),
        "z_conflict_not_up_gt_0p03": bool(improvements["delta_z_mismatch_conflict_frac"] is None or improvements["delta_z_mismatch_conflict_frac"] <= 0.03),
    }
    if run_name.startswith("02a00399"):
        phase5a_class = "diagnostic_evidence_only_preflight_pass" if all(success_checks.values()) else "diagnostic_evidence_only_preflight_fail"
    else:
        phase5a_class = "clean_control_evidence_only"

    stats = {
        "case": run_name,
        "status": "phase5a_maps_complete",
        "classification": phase5a_class,
        "anchor_idx": int(anchor_idx),
        "anchor_ts_ns": int(anchor_ts),
        "accum_sweeps": int(ACCUM_SWEEPS),
        "current_lidar": {
            "sweep_ts_ns": int(fused["nearest_sweep_ts_ns"]),
            "delta_ms": float(fused["nearest_delta_ms"]),
            "points": int(current_pts.shape[0]),
        },
        "fused_lidar": {
            "points": int(fused_pts.shape[0]),
            "entries": [
                {k: v for k, v in e.items() if k != "points_anchor"}
                for e in fused["entries"]
            ],
        },
        "counts": {
            "surface_hypothesis_id": unique_counts(surface_id),
            "current_z_cause_primary": unique_counts(current_eval["cause"]),
            "fused_z_cause_primary": unique_counts(fused_eval["cause"]),
            "transition": unique_counts(transition),
        },
        "current": cur,
        "fused": fus,
        "improvements": improvements,
        "success_checks": success_checks,
        "depth_summary": {
            "current": current_depth_summary,
            "fused": fused_depth_summary,
        },
        "seam": seam_diag,
        "map_policy": {
            "z_cause": {
                "0": "multi_source_visible",
                "1": "single_source_visible",
                "20": "no_target_surface_support",
                "41": "no_camera_geom_valid",
                "42": "no_raw_camera_zbuffer_support_current_sweep",
                "43": "z_mismatch_or_occlusion_conflict_current_sweep",
                "44": "mixed_no_visible_source",
                "60": "source_boundary_risk_proxy",
                "255": "invalid_or_unclassified",
            },
            "surface_hypothesis_id": {
                "0": "no_surface_candidate",
                "1": "current_and_fused_support",
                "2": "fused_only_temporal_surface_candidate",
                "3": "current_only_surface_candidate",
            },
            "transition": {
                "0": "outside_target",
                "1": "same_repairability_state",
                "2": "improved_from_abstain_to_visible_or_single_source",
                "3": "degraded_from_visible_or_single_source_to_abstain",
                "5": "protected_or_source_boundary_veto_proxy",
            },
        },
        "claim_boundary": [
            "Phase5a uses fused LiDAR only as target-surface evidence",
            "current-sweep zbuffers are used for raw visibility checks to avoid circular self-support",
            "visible seam panels are overlays/diagnostics, not RGB repair or source replacement",
            "source-boundary is a proxy veto, not semantic protected mask",
        ],
        "outputs": {
            "review": f"{run_name}_phase5a_evidence_review_768.jpg",
            "crop_review": f"{run_name}_phase5a_crop_review.jpg",
            **{name: f"{run_name}_{name}.png" for name in REQUIRED_MAPS},
        },
        "runtime_s": round(time.time() - t0, 3),
    }
    (out_dir / f"{run_name}_phase5a_breakdown.json").write_text(json.dumps(json_safe(stats), indent=2), encoding="utf-8")
    return stats


def aggregate(diags):
    import numpy as np

    def mean_path(top, key):
        vals = []
        for d in diags:
            val = (d.get(top) or {}).get(key)
            if isinstance(val, (int, float)):
                vals.append(float(val))
        return float(np.mean(vals)) if vals else None

    def mean_imp(key):
        vals = []
        for d in diags:
            val = (d.get("improvements") or {}).get(key)
            if isinstance(val, (int, float)):
                vals.append(float(val))
        return float(np.mean(vals)) if vals else None

    by_case = {d["case"]: d for d in diags}
    bmw = by_case.get("02a00399_a000_bmw", {})
    clean = by_case.get("0bae3b5e_a030_clean_far", {})
    bmw_checks = bmw.get("success_checks") or {}
    clean_degrade = False
    if clean:
        imp = clean.get("improvements") or {}
        clean_degrade = bool(
            (imp.get("delta_z_mismatch_conflict_frac") is not None and imp.get("delta_z_mismatch_conflict_frac") > 0.03)
            or (imp.get("gain_visible_any_frac") is not None and imp.get("gain_visible_any_frac") < -0.03)
        )
    aggregate_success = bool(bmw and all(bool(v) for v in bmw_checks.values()) and not clean_degrade)
    return {
        "status": "phase5a_maps_complete" if len(diags) == len(CASES) and all(d.get("status") == "phase5a_maps_complete" for d in diags) else "phase5a_maps_incomplete",
        "n_cases": len(diags),
        "aggregate_success": aggregate_success,
        "clean_control_degraded": clean_degrade,
        "by_case_classification": {k: v.get("classification") for k, v in by_case.items()},
        "by_case": {
            k: {
                "current": v.get("current"),
                "fused": v.get("fused"),
                "improvements": v.get("improvements"),
                "success_checks": v.get("success_checks"),
            }
            for k, v in by_case.items()
        },
        "mean_current": {key: mean_path("current", key) for key in [
            "seam_no_surface_frac",
            "seam_visible_any_frac",
            "seam_visible_ge2_frac",
            "seam_no_raw_zbuffer_support_frac",
            "seam_z_mismatch_conflict_frac",
        ]},
        "mean_fused": {key: mean_path("fused", key) for key in [
            "seam_no_surface_frac",
            "seam_visible_any_frac",
            "seam_visible_ge2_frac",
            "seam_no_raw_zbuffer_support_frac",
            "seam_z_mismatch_conflict_frac",
        ]},
        "mean_improvements": {key: mean_imp(key) for key in [
            "delta_no_surface_frac",
            "gain_visible_any_frac",
            "gain_visible_ge2_frac",
            "delta_no_raw_zbuffer_support_frac",
            "delta_z_mismatch_conflict_frac",
            "fused_longest_supported_component_frac",
        ]},
    }


def main():
    t0 = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_phase5a_start",
        "scope": {
            "fixed_cases_only": CASES,
            "continuous_surface_evidence_only": True,
            "accum_sweeps": ACCUM_SWEEPS,
            "uses_current_sweep_zbuffer_for_visibility": True,
            "rgb_repair_created": False,
            "a100_required": False,
            "model_inference": False,
            "vggt_hf": False,
            "dit_flux_generation": False,
            "source_replacement": False,
            "bounded_dependency_bootstrap": "av2>=0.3 only if missing",
        },
        "paths": {"remote_out": str(REMOTE_OUT), "av2_root_exists": AV2_ROOT.exists()},
    }
    workdir = find_workdir()
    result["workdir"] = str(workdir) if workdir else None
    if workdir is None:
        result["status"] = "blocked_remote_script_missing"
        REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("DB64_PHASE5A_JSON_BEGIN")
        print(json.dumps(result, ensure_ascii=False))
        print("DB64_PHASE5A_JSON_END")
        return

    sys.path.insert(0, str(workdir / "scripts" / "phase3"))
    sys.path.insert(0, str(workdir / "code"))

    dep = {"name": "av2", "import_before": False, "install_attempted": False, "import_after": False}
    try:
        import av2  # noqa: F401
        dep["import_before"] = True
        dep["import_after"] = True
    except Exception as exc:
        dep["import_before_error"] = repr(exc)
        dep["install_attempted"] = True
        dep_t0 = time.time()
        proc_dep = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=900,
        )
        dep["install_returncode"] = int(proc_dep.returncode)
        dep["install_duration_s"] = round(time.time() - dep_t0, 2)
        dep["install_stdout_tail"] = tail(proc_dep.stdout, 3000)
        dep["install_stderr_tail"] = tail(proc_dep.stderr, 3000)
        try:
            import av2  # noqa: F401
            dep["import_after"] = True
        except Exception as exc_after:
            dep["import_after_error"] = repr(exc_after)
    result["dependency"] = dep
    if not dep.get("import_after"):
        result["status"] = "blocked_missing_av2_after_bootstrap"
        result["runtime_s"] = round(time.time() - t0, 2)
        REMOTE_RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("DB64_PHASE5A_JSON_BEGIN")
        print(json.dumps(result, ensure_ascii=False))
        print("DB64_PHASE5A_JSON_END")
        return

    os.chdir(str(workdir))
    diags = []
    errors = []
    for case in CASES:
        try:
            diags.append(one_case(case, AV2_ROOT, REMOTE_OUT))
        except Exception as exc:
            errors.append({"case": case, "error": repr(exc)})
    summary = aggregate(diags)
    summary["errors"] = errors
    summary["status"] = "phase5a_maps_complete" if summary["status"] == "phase5a_maps_complete" and not errors else "phase5a_maps_incomplete_or_failed"
    (REMOTE_OUT / "batch_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")

    outputs = {"batch_summary": file_row(REMOTE_OUT / "batch_summary.json")}
    for run_name in RUN_NAMES:
        case_dir = REMOTE_OUT / run_name
        outputs[run_name] = {
            "diagnostics": file_row(case_dir / f"{run_name}_phase5a_breakdown.json"),
            "review": file_row(case_dir / f"{run_name}_phase5a_evidence_review_768.jpg"),
            "crop_review": file_row(case_dir / f"{run_name}_phase5a_crop_review.jpg"),
        }
        for name in REQUIRED_MAPS:
            outputs[run_name][name] = file_row(case_dir / f"{run_name}_{name}.png")

    result["batch_summary"] = summary
    result["outputs"] = outputs
    result["status"] = "db64_phase5a_continuous_surface_completed" if summary["status"] == "phase5a_maps_complete" else "db64_phase5a_continuous_surface_failed_or_blocked"
    result["runtime_s"] = round(time.time() - t0, 2)
    REMOTE_RESULT.write_text(json.dumps(json_safe(result), indent=2), encoding="utf-8")
    print("DB64_PHASE5A_JSON_BEGIN")
    print(json.dumps(json_safe(result), ensure_ascii=False))
    print("DB64_PHASE5A_JSON_END")


if __name__ == "__main__":
    main()
'''


def remote_bash() -> str:
    code_b64 = base64.b64encode(remote_python().encode("utf-8")).decode("ascii")
    return (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{code_b64}').decode('utf-8')\n"
        "exec(compile(code, '<db64_phase5a_continuous_surface_remote>', 'exec'))\n"
        "PY"
    )


def parse_json_from_log(log_tail: str) -> dict[str, Any] | None:
    if "DB64_PHASE5A_JSON_BEGIN" not in log_tail or "DB64_PHASE5A_JSON_END" not in log_tail:
        return None
    body = log_tail.split("DB64_PHASE5A_JSON_BEGIN", 1)[1].split("DB64_PHASE5A_JSON_END", 1)[0].strip()
    return json.loads(body)


def fetch_outputs(client: ColabClient) -> dict[str, Any]:
    FETCH_DIR.mkdir(parents=True, exist_ok=True)
    items: list[tuple[str, str, Path, int]] = [
        ("batch_summary", REMOTE_SUMMARY, LOCAL_SUMMARY, 24),
    ]
    for run_name in RUN_NAMES:
        base = REMOTE_OUT + "/" + run_name + "/" + run_name
        local_case = FETCH_DIR / run_name
        items.extend(
            [
                (f"{run_name}_diagnostics", base + "_phase5a_breakdown.json", local_case / f"{run_name}_phase5a_breakdown.json", 24),
                (f"{run_name}_review", base + "_phase5a_evidence_review_768.jpg", local_case / f"{run_name}_phase5a_evidence_review_768.jpg", 64),
                (f"{run_name}_crop_review", base + "_phase5a_crop_review.jpg", local_case / f"{run_name}_phase5a_crop_review.jpg", 64),
                (f"{run_name}_support_overlay", base + "_phase5a_support_overlay.png", local_case / f"{run_name}_phase5a_support_overlay.png", 24),
                (f"{run_name}_visible_overlay", base + "_phase5a_visible_overlay.png", local_case / f"{run_name}_phase5a_visible_overlay.png", 24),
                (f"{run_name}_current_z_cause_primary_viz", base + "_current_z_cause_primary_viz.png", local_case / f"{run_name}_current_z_cause_primary_viz.png", 24),
                (f"{run_name}_fused_z_cause_primary_viz", base + "_fused_z_cause_primary_viz.png", local_case / f"{run_name}_fused_z_cause_primary_viz.png", 24),
                (f"{run_name}_fused_z_repairability_viz", base + "_fused_z_repairability_viz.png", local_case / f"{run_name}_fused_z_repairability_viz.png", 24),
                (f"{run_name}_before_after_transition_viz", base + "_before_after_transition_viz.png", local_case / f"{run_name}_before_after_transition_viz.png", 24),
            ]
        )
        for name in REQUIRED_MAPS:
            items.append((f"{run_name}_{name}", base + f"_{name}.png", local_case / f"{run_name}_{name}.png", 24))

    fetched: dict[str, Any] = {}
    for key, remote_path, local_path, max_mb in items:
        raw = client.read_file(remote_path, max_size_mb=max_mb)
        if raw is None:
            fetched[key] = {"fetched": False, "path": rel(local_path)}
            continue
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(raw)
        row: dict[str, Any] = {"fetched": True, "path": rel(local_path), "bytes": int(local_path.stat().st_size)}
        if local_path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            try:
                with Image.open(local_path) as img:
                    row["size"] = list(img.size)
            except Exception as exc:
                row["image_error"] = repr(exc)
        fetched[key] = row
    return fetched


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=(236, 236, 236), size=15) -> None:
    draw.text(xy, str(text), fill=fill, font=font(size))


def draw_wrapped(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, chars: int, fill=(236, 236, 236), size: int = 14) -> int:
    for line in wrap(str(text), width=chars, break_long_words=False, break_on_hyphens=False):
        draw_text(draw, (x, y), line, fill=fill, size=size)
        y += size + 6
    return y


def paste_thumb(board: Image.Image, path: Path, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    if not path.exists():
        draw.rectangle(box, outline=(100, 100, 100), fill=(34, 36, 42))
        draw_wrapped(draw, x0 + 12, y0 + 12, f"missing: {rel(path)}", 44, fill=(255, 170, 145), size=14)
        return
    with Image.open(path) as img:
        thumb = img.convert("RGB")
        thumb.thumbnail((x1 - x0, y1 - y0))
        px = x0 + ((x1 - x0) - thumb.width) // 2
        py = y0 + ((y1 - y0) - thumb.height) // 2
        board.paste(thumb, (px, py))
        draw.rectangle((px, py, px + thumb.width, py + thumb.height), outline=(185, 185, 185))


def fmt(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return "n/a"


def write_board(manifest: dict[str, Any]) -> None:
    summary = manifest.get("aggregate") or {}
    board = Image.new("RGB", (1900, 1500), (18, 20, 25))
    draw = ImageDraw.Draw(board)
    draw_text(draw, (28, 24), "DB64 Phase5a continuous target-surface evidence preflight", size=26)
    draw_text(
        draw,
        (28, 60),
        "CPU Colab, fixed two cases. Motion-compensated fused LiDAR target surface; current-sweep zbuffer visibility. No RGB replacement.",
        fill=(218, 224, 235),
        size=15,
    )

    y = 100
    lines = [
        f"status={manifest['status']} run_ok={manifest['decision']['run_ok']} complete_maps={manifest['decision']['complete_required_maps']} secret_hits={manifest['strict_secret_scan']['hit_count']}",
        f"runtime={manifest['runtime']['status'].get('runtime_type')} job_state={manifest['job'].get('state')} exit={manifest['job'].get('exit_code')}",
        f"aggregate_success={summary.get('aggregate_success')} clean_degraded={summary.get('clean_control_degraded')}",
    ]
    for line in lines:
        y = draw_wrapped(draw, 36, y, "- " + line, 145, size=14)

    bmw = ((summary.get("by_case") or {}).get(RUN_NAMES[0]) or {})
    bmw_imp = bmw.get("improvements") or {}
    bmw_fused = bmw.get("fused") or {}
    y += 8
    draw_text(draw, (28, y), "BMW Evidence Delta", size=20)
    y += 28
    for key in [
        "delta_no_surface_frac",
        "gain_visible_any_frac",
        "gain_visible_ge2_frac",
        "delta_no_raw_zbuffer_support_frac",
        "delta_z_mismatch_conflict_frac",
        "fused_longest_supported_component_frac",
    ]:
        y = draw_wrapped(draw, 36, y, f"- {key}={fmt(bmw_imp.get(key))}", 145, fill=(224, 232, 255), size=12)
    y = draw_wrapped(
        draw,
        36,
        y + 4,
        "- fused BMW state: no_surface={} visible_any={} visible_ge2={} no_zbuf={}".format(
            fmt(bmw_fused.get("seam_no_surface_frac")),
            fmt(bmw_fused.get("seam_visible_any_frac")),
            fmt(bmw_fused.get("seam_visible_ge2_frac")),
            fmt(bmw_fused.get("seam_no_raw_zbuffer_support_frac")),
        ),
        145,
        fill=(224, 232, 255),
        size=12,
    )

    y += 8
    draw_text(draw, (28, y), "Boundary", size=20)
    y += 28
    for line in [
        "visible seam panels are overlays/diagnostics, not RGB repair",
        "fused LiDAR is target-surface evidence; current zbuffer checks raw visibility",
        "source-boundary veto is a proxy, not semantic protected mask",
    ]:
        y = draw_wrapped(draw, 36, y, "- " + line, 145, fill=(255, 235, 185), size=13)

    x0, x1 = 28, 940
    x2, x3 = 970, 1870
    paste_thumb(board, FETCH_DIR / RUN_NAMES[0] / f"{RUN_NAMES[0]}_phase5a_evidence_review_768.jpg", (x0, 560, x1, 1370))
    draw_text(draw, (x0, 530), "BMW Phase5a evidence review", size=18)
    paste_thumb(board, FETCH_DIR / RUN_NAMES[1] / f"{RUN_NAMES[1]}_phase5a_evidence_review_768.jpg", (x2, 560, x3, 1370))
    draw_text(draw, (x2, 530), "Clean control Phase5a evidence review", size=18)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    board.save(BOARD, quality=92)


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def source_kind(source: str) -> str:
    return "process_env" if str(source).startswith("process_env") else "non_repo_file" if str(source).startswith("non_repo_file:") else "unknown"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    status = client.get("/status", timeout=180)
    submit = client.post(
        "/exec",
        {"cmd": ["bash", "-lc", remote_bash()], "cwd": "/content", "timeout_s": 3600},
        timeout=180,
    )
    job_id = submit["job_id"]
    job = poll_job(client, job_id, timeout_s=3600)

    remote_result: dict[str, Any] | None = None
    raw = client.read_file(REMOTE_RESULT, max_size_mb=32)
    if raw is not None:
        remote_result = json.loads(raw.decode("utf-8"))
    if remote_result is None:
        remote_result = parse_json_from_log(job.get("log_tail", ""))
    if remote_result is None:
        remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    LOCAL_REMOTE_RESULT.write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")

    fetched = fetch_outputs(client)
    batch_summary = load_json_if_exists(LOCAL_SUMMARY)
    aggregate = batch_summary or remote_result.get("batch_summary") or {}
    run_ok = bool(remote_result.get("status") == "db64_phase5a_continuous_surface_completed")
    complete_required_maps = bool((aggregate or {}).get("status") == "phase5a_maps_complete")

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db64_phase5a_continuous_surface_preflight",
        "accepted_evidence_type": "continuous_target_surface_preflight_evidence",
        "scope": {
            "remote_status_used": True,
            "remote_exec_used": True,
            "fixed_cases_only": CASES,
            "accum_sweeps": 5,
            "uses_current_sweep_zbuffer_for_visibility": True,
            "rgb_repair_created": False,
            "visible_seam_panel_created": True,
            "visible_panel_type": "diagnostic_overlay_no_rgb_replacement",
            "a100_used": False,
            "vggt_hf_model_used": False,
            "dit_flux_generation": False,
            "source_replacement": False,
            "db47_db49_db32_rerun": False,
            "red_promotion": False,
        },
        "runtime": {"status": safe_status(status), "secret_source_kind": source_kind(getattr(client, "source", ""))},
        "job": sanitize(job),
        "remote_result": remote_result,
        "aggregate": aggregate,
        "fetched": fetched,
        "decision": {
            "run_ok": run_ok,
            "complete_required_maps": complete_required_maps,
            "aggregate_success": bool((aggregate or {}).get("aggregate_success")),
            "claim_classification": "diagnostic/evidence-only unless aggregate_success is true and later layer/render brief validates RGB",
            "phase5b_allowed_by_this_result": bool((aggregate or {}).get("aggregate_success")),
        },
        "claim_boundary": [
            "Phase5a is evidence preflight only",
            "visible seam panels are overlays, not source-faithful RGB repair",
            "fused LiDAR target surface is checked against current-sweep raw-camera zbuffers",
            "no A100, VGGT/HF, model inference, generation, source replacement, or RED promotion occurred",
        ],
    }
    scan_text = json.dumps(manifest, ensure_ascii=False) + "\n" + LOCAL_REMOTE_RESULT.read_text(encoding="utf-8")
    hits = secret_hits(scan_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(int(h["count"]) for h in hits), "hits": hits}
    write_board(manifest)
    manifest["board"] = {"path": rel(BOARD), "bytes": int(BOARD.stat().st_size)}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"wrote {rel(MANIFEST)} and {rel(BOARD)} in {time.time() - t0:.1f}s")
