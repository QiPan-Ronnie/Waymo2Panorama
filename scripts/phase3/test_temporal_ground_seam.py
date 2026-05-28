"""Probe temporal ego-motion evidence for AV2 seam repair.

This is deliberately not another single-frame seam/path tweak.  The probe asks:

    Can nearby frames, compensated with AV2 ego poses, provide source-faithful
    evidence for the road/lane part of a hard_select seam?

For every target ERP pixel in the lower image half, we intersect the virtual ERP
ray with the target-time ground plane, transform that 3D point through
city_SE3_egovehicle to a nearby timestamp, and sample the neighboring frame's
ring cameras.  A conservative diagnostic repair only replaces hard_select pixels
inside seam bands where multiple temporal samples agree.

Expected limitation: this can only help static ground-plane content.  It should
not fix vehicles, poles, facades, or non-ground parallax.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
import pandas as pd
from PIL import Image
from scipy.spatial.transform import Rotation

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))

from measure_overlap_ncc import score_one_anchor  # noqa: E402
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.ground_plane_layer import estimate_panorama_center_z  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


DEFAULT_LOGS = {
    "02a00399": "02a00399-3857-444e-8db3-a8f58489c394",
    "fbee355f": "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
    "0bae3b5e": "0bae3b5e-417d-3b03-abaa-806b433233b8",
}
DEFAULT_CASES = ["02a00399:0:bmw", "fbee355f:95:ped_obj", "0bae3b5e:30:clean_far"]


@dataclass
class TemporalRender:
    rgb: np.ndarray
    valid: np.ndarray
    weight: np.ndarray
    cam_index: np.ndarray
    offset: int
    target_ts_ns: int
    source_ts_ns: int
    ego_delta_m: float


def _save_rgb(path: Path, rgb: np.ndarray, quality: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.clip(rgb, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        img.save(path, quality=quality)
    else:
        img.save(path)


def _resize_w(rgb: np.ndarray, width: int) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    h, w = rgb.shape[:2]
    return cv2.resize(rgb, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)


def _label_panel(rgb: np.ndarray, label: str, label_h: int = 38) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    band = np.zeros((label_h, rgb.shape[1], 3), dtype=np.uint8)
    cv2.putText(band, label, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([band, rgb])


def _stack_rows(rows: list[tuple[str, np.ndarray]]) -> np.ndarray:
    panels = [_label_panel(img, label) for label, img in rows]
    width = max(p.shape[1] for p in panels)
    out = []
    for p in panels:
        if p.shape[1] < width:
            pad = np.zeros((p.shape[0], width - p.shape[1], 3), dtype=np.uint8)
            p = np.hstack([p, pad])
        out.append(p)
    return np.vstack(out)


def _stack_crop_grid(rows: list[tuple[str, np.ndarray]], crops: dict[str, tuple[int, int, int, int]]) -> np.ndarray:
    row_imgs = []
    for label, rgb in rows:
        cells = []
        for crop_name, (y0, y1, x0, x1) in crops.items():
            view = rgb[y0:y1, x0:x1]
            cells.append(_label_panel(view, f"{label} | {crop_name}", label_h=34))
        height = max(c.shape[0] for c in cells)
        padded = []
        for c in cells:
            if c.shape[0] < height:
                pad = np.zeros((height - c.shape[0], c.shape[1], 3), dtype=np.uint8)
                c = np.vstack([c, pad])
            padded.append(c)
        row_imgs.append(np.hstack(padded))
    width = max(r.shape[1] for r in row_imgs)
    out = []
    for r in row_imgs:
        if r.shape[1] < width:
            pad = np.zeros((r.shape[0], width - r.shape[1], 3), dtype=np.uint8)
            r = np.hstack([r, pad])
        out.append(r)
    return np.vstack(out)


def _default_crops(h: int, w: int) -> dict[str, tuple[int, int, int, int]]:
    return {
        "bmw_left": (int(0.38 * h), int(0.62 * h), int(0.05 * w), int(0.33 * w)),
        "road_mid": (int(0.32 * h), int(0.63 * h), int(0.29 * w), int(0.58 * w)),
        "suv_right": (int(0.20 * h), int(0.65 * h), int(0.66 * w), int(0.95 * w)),
    }


def _to_y(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0]


def _erp_dirs_ego(erp_hw: tuple[int, int]) -> np.ndarray:
    h, w = erp_hw
    u_idx = np.arange(w, dtype=np.float64)
    v_idx = np.arange(h, dtype=np.float64)
    uu, vv = np.meshgrid(u_idx, v_idx)
    theta = np.pi - (uu + 0.5) / w * (2.0 * np.pi)
    phi = (np.pi / 2.0) - (vv + 0.5) / h * np.pi
    cos_phi = np.cos(phi)
    return np.stack([cos_phi * np.cos(theta), cos_phi * np.sin(theta), np.sin(phi)], axis=-1)


def _read_city_poses(log_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    pose_path = log_dir / "city_SE3_egovehicle.feather"
    if not pose_path.exists():
        raise FileNotFoundError(f"missing city poses: {pose_path}")
    df = pd.read_feather(pose_path).sort_values("timestamp_ns").reset_index(drop=True)
    ts = df["timestamp_ns"].to_numpy(dtype=np.int64)
    mats = np.repeat(np.eye(4, dtype=np.float64)[None, :, :], len(df), axis=0)
    q = df[["qx", "qy", "qz", "qw"]].to_numpy(dtype=np.float64)
    mats[:, :3, :3] = Rotation.from_quat(q).as_matrix()
    mats[:, :3, 3] = df[["tx_m", "ty_m", "tz_m"]].to_numpy(dtype=np.float64)
    return ts, mats


def _nearest_pose(ts_all: np.ndarray, mats: np.ndarray, ts_ns: int) -> tuple[np.ndarray, int, int]:
    idx = int(np.argmin(np.abs(ts_all - int(ts_ns))))
    return mats[idx], int(ts_all[idx]), idx


def _render_target_slabs(loader: AV2RingLoader, anchor_idx: int, erp_hw: tuple[int, int]):
    ts_all = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts_all[anchor_idx])
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, weight = render_camera_to_erp(
            frame.images[cam],
            calib.K,
            calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(weight)
    return frame, slabs, weights


def _project_temporal_ground(
    *,
    neighbor_frame,
    target_T_city_ego: np.ndarray,
    neighbor_T_city_ego: np.ndarray,
    erp_hw: tuple[int, int],
    panorama_center_z: float,
    ground_z_m: float,
    min_distance_m: float,
    max_distance_m: float,
    min_cam_cos: float,
    offset: int,
    target_ts_ns: int,
    source_ts_ns: int,
) -> TemporalRender:
    h, w = erp_hw
    dirs = _erp_dirs_ego(erp_hw)
    center = np.array([0.0, 0.0, float(panorama_center_z)], dtype=np.float64)
    dz = dirs[..., 2]
    denom_ok = np.abs(dz) > 1e-8
    s = np.where(denom_ok, (float(ground_z_m) - center[2]) / np.where(denom_ok, dz, 1.0), -1.0)
    p_target = center[None, None, :] + s[..., None] * dirs
    dist = np.linalg.norm(p_target - center[None, None, :], axis=-1)
    ground_ok = denom_ok & (s > 0) & np.isfinite(dist) & (dist >= min_distance_m) & (dist <= max_distance_m)

    p_flat = p_target.reshape(-1, 3)
    p_city_flat = p_flat @ target_T_city_ego[:3, :3].T + target_T_city_ego[:3, 3][None, :]
    p_city = p_city_flat.reshape(h, w, 3)

    temporal_slabs: list[np.ndarray] = []
    temporal_weights: list[np.ndarray] = []
    for cam in RING_CAMS_7:
        calib = neighbor_frame.calibrations[cam]
        image = neighbor_frame.images[cam]
        h_img, w_img = image.shape[:2]
        T_city_cam = neighbor_T_city_ego @ calib.T_ego_cam
        R_cam_city = T_city_cam[:3, :3].T
        t_city_cam = T_city_cam[:3, 3]
        p_cam = (p_city - t_city_cam[None, None, :]) @ R_cam_city.T
        z_cam = p_cam[..., 2]
        in_front = z_cam > 1e-6
        z_safe = np.where(in_front, z_cam, 1.0)
        u_img = calib.K[0, 0] * (p_cam[..., 0] / z_safe) + calib.K[0, 2]
        v_img = calib.K[1, 1] * (p_cam[..., 1] / z_safe) + calib.K[1, 2]
        in_bounds = (
            (u_img >= 0.5)
            & (u_img <= w_img - 1.5)
            & (v_img >= 0.5)
            & (v_img <= h_img - 1.5)
        )
        cam_norm = np.linalg.norm(p_cam, axis=-1)
        cam_cos = np.where(cam_norm > 1e-9, z_cam / np.maximum(cam_norm, 1e-9), 0.0)
        valid = ground_ok & in_front & in_bounds & (cam_cos >= float(min_cam_cos))
        map_x = np.where(valid, u_img, -1).astype(np.float32)
        map_y = np.where(valid, v_img, -1).astype(np.float32)
        sampled = cv2.remap(
            image.astype(np.float32),
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0.0, 0.0, 0.0),
        )
        sampled = np.where(valid[..., None], sampled, 0.0).astype(np.float32)
        weight = (np.clip(cam_cos, 0.0, 1.0) ** 2).astype(np.float32)
        weight *= np.exp(-dist / max(float(max_distance_m), 1.0)).astype(np.float32)
        weight = np.where(valid, weight, 0.0).astype(np.float32)
        temporal_slabs.append(sampled)
        temporal_weights.append(weight)

    stack_w = np.stack(temporal_weights, axis=0)
    stack_rgb = np.stack(temporal_slabs, axis=0)
    cam_idx = stack_w.argmax(axis=0)
    valid = stack_w.max(axis=0) > 1e-8
    picked = np.take_along_axis(stack_rgb, cam_idx[None, ..., None], axis=0)[0]
    rgb = np.where(valid[..., None], picked, 0).astype(np.float32)
    ego_delta = float(np.linalg.norm(neighbor_T_city_ego[:3, 3] - target_T_city_ego[:3, 3]))
    return TemporalRender(
        rgb=rgb,
        valid=valid,
        weight=stack_w.max(axis=0).astype(np.float32),
        cam_index=cam_idx.astype(np.int16),
        offset=int(offset),
        target_ts_ns=int(target_ts_ns),
        source_ts_ns=int(source_ts_ns),
        ego_delta_m=ego_delta,
    )


def _build_seam_band(weights: Sequence[np.ndarray], band_half_width: int, core_half_width: int, y_min_frac: float):
    h, w = weights[0].shape
    band_all = np.zeros((h, w), dtype=bool)
    core_all = np.zeros((h, w), dtype=bool)
    pair_diags: list[dict] = []
    lower = np.zeros((h, w), dtype=bool)
    lower[int(round(y_min_frac * h)) :, :] = True
    for i, j in RING_PAIRS:
        wi = weights[i].astype(np.float32)
        wj = weights[j].astype(np.float32)
        overlap = (wi > 1e-6) & (wj > 1e-6)
        band, signed = build_voronoi_seam_band(wi, wj, band_half_width=band_half_width)
        band &= overlap & lower
        core = band & (np.abs(signed) <= float(core_half_width))
        band_all |= band
        core_all |= core
        pair_diags.append({"pair": [int(i), int(j)], "band_px": int(band.sum()), "core_px": int(core.sum())})
    return band_all, core_all, pair_diags


def _temporal_consensus(renders: Sequence[TemporalRender]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if not renders:
        raise ValueError("no temporal renders")
    h, w = renders[0].valid.shape
    sum_rgb = np.zeros((h, w, 3), dtype=np.float32)
    count = np.zeros((h, w), dtype=np.float32)
    y_values = []
    for r in renders:
        valid = r.valid.astype(np.float32)
        sum_rgb += r.rgb.astype(np.float32) * valid[..., None]
        count += valid
        y = _to_y(r.rgb)
        y_values.append(np.where(r.valid, y.astype(np.float32), np.nan))
    rgb = np.where(count[..., None] > 0, sum_rgb / np.maximum(count[..., None], 1.0), 0.0)
    y_stack = np.stack(y_values, axis=0)
    y_std = np.nanstd(y_stack, axis=0)
    y_std = np.where(np.isfinite(y_std), y_std, 255.0).astype(np.float32)
    return rgb.astype(np.float32), count.astype(np.int16), y_std, np.stack([r.valid for r in renders], axis=0)


def _compose_temporal_repair(
    base: np.ndarray,
    temporal_rgb: np.ndarray,
    temporal_count: np.ndarray,
    temporal_y_std: np.ndarray,
    seam_band: np.ndarray,
    *,
    min_count: int,
    max_temporal_y_std: float,
    max_base_temporal_y_diff: float,
) -> tuple[np.ndarray, np.ndarray, dict]:
    base_y = _to_y(base).astype(np.float32)
    temp_y = _to_y(temporal_rgb).astype(np.float32)
    y_diff = np.abs(base_y - temp_y)
    replace = (
        seam_band
        & (temporal_count >= int(min_count))
        & (temporal_y_std <= float(max_temporal_y_std))
        & (y_diff <= float(max_base_temporal_y_diff))
    )
    out = base.copy()
    out[replace] = np.clip(temporal_rgb[replace], 0, 255).astype(np.uint8)
    diag = {
        "min_count": int(min_count),
        "max_temporal_y_std": float(max_temporal_y_std),
        "max_base_temporal_y_diff": float(max_base_temporal_y_diff),
        "replace_px": int(replace.sum()),
        "replace_frac_erp": float(replace.mean()),
        "replace_frac_seam_band": float(replace.sum() / max(1, int(seam_band.sum()))),
        "temporal_count_mean_in_band": float(temporal_count[seam_band].mean()) if seam_band.any() else 0.0,
        "temporal_y_std_p50_in_band": float(np.percentile(temporal_y_std[seam_band], 50)) if seam_band.any() else 0.0,
        "temporal_y_std_p90_in_band": float(np.percentile(temporal_y_std[seam_band], 90)) if seam_band.any() else 0.0,
        "base_temporal_y_diff_p50_in_band": float(np.percentile(y_diff[seam_band], 50)) if seam_band.any() else 0.0,
        "base_temporal_y_diff_p90_in_band": float(np.percentile(y_diff[seam_band], 90)) if seam_band.any() else 0.0,
    }
    return out, replace, diag


def _overlay_mask(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.clip(rgb, 0, 255).astype(np.uint8).copy()
    if mask.any():
        dil = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool)
        color = np.array([30, 230, 255], dtype=np.uint8)
        out[dil] = (0.45 * out[dil].astype(np.float32) + 0.55 * color.astype(np.float32)).astype(np.uint8)
    return out


def _parse_case(case: str, av2_root: Path) -> tuple[Path, int, str]:
    parts = case.split(":")
    if len(parts) < 2:
        raise ValueError(f"case must be LOGSHORT:ANCHOR[:NAME], got {case!r}")
    log_key = parts[0]
    anchor = int(parts[1])
    name = parts[2] if len(parts) > 2 else f"a{anchor:03d}"
    if Path(log_key).exists():
        log_dir = Path(log_key)
        short = log_dir.name.split("-")[0]
    else:
        log_name = DEFAULT_LOGS.get(log_key, log_key)
        log_dir = av2_root / log_name
        short = log_key.split("-")[0]
    return log_dir, anchor, f"{short}_a{anchor:03d}_{name}"


def run_case(log_dir: Path, anchor_idx: int, run_name: str, args) -> dict:
    t0 = time.time()
    erp_hw = (args.erp_h, args.erp_w)
    out_dir = Path(args.out_dir) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[case] {run_name} log={log_dir} anchor={anchor_idx}", flush=True)

    loader = AV2RingLoader(log_dir)
    anchor_ts = loader.anchor_timestamps_ns()
    if anchor_idx < 0 or anchor_idx >= len(anchor_ts):
        raise IndexError(f"anchor {anchor_idx} out of range 0..{len(anchor_ts) - 1}")
    pose_ts, pose_mats = _read_city_poses(log_dir)
    target_frame, slabs, weights = _render_target_slabs(loader, anchor_idx, erp_hw)
    base = hard_select(slabs, weights)
    center_z = estimate_panorama_center_z([target_frame.calibrations[c].T_ego_cam for c in RING_CAMS_7])
    target_pose, target_pose_ts, _target_pose_idx = _nearest_pose(pose_ts, pose_mats, target_frame.anchor_timestamp_ns)

    offsets_req = [int(x) for x in args.offsets.split(",") if x.strip()]
    renders: list[TemporalRender] = []
    offset_diags = []
    for off in offsets_req:
        src_idx = anchor_idx + off
        if src_idx < 0 or src_idx >= len(anchor_ts):
            offset_diags.append({"offset": off, "status": "out_of_range"})
            continue
        src_frame = loader.load_synced_frame(anchor_ts[src_idx])
        src_pose, src_pose_ts, _src_pose_idx = _nearest_pose(pose_ts, pose_mats, src_frame.anchor_timestamp_ns)
        print(f"[temporal] offset={off} src_anchor={src_idx}", flush=True)
        render = _project_temporal_ground(
            neighbor_frame=src_frame,
            target_T_city_ego=target_pose,
            neighbor_T_city_ego=src_pose,
            erp_hw=erp_hw,
            panorama_center_z=center_z,
            ground_z_m=args.ground_z_m,
            min_distance_m=args.min_ground_dist_m,
            max_distance_m=args.max_ground_dist_m,
            min_cam_cos=args.min_cam_cos,
            offset=off,
            target_ts_ns=int(target_frame.anchor_timestamp_ns),
            source_ts_ns=int(src_frame.anchor_timestamp_ns),
        )
        renders.append(render)
        offset_diags.append(
            {
                "offset": int(off),
                "status": "ok",
                "src_anchor": int(src_idx),
                "valid_frac": float(render.valid.mean()),
                "valid_px": int(render.valid.sum()),
                "ego_delta_m": float(render.ego_delta_m),
                "pose_dt_ms": float((src_pose_ts - target_pose_ts) / 1e6),
            }
        )
        _save_rgb(out_dir / f"temporal_offset_{off:+d}.jpg", render.rgb)

    if not renders:
        raise RuntimeError(f"no valid temporal offsets for {run_name}")

    temporal_rgb, temporal_count, temporal_y_std, _valid_stack = _temporal_consensus(renders)
    seam_band, seam_core, seam_pair_diags = _build_seam_band(
        weights,
        band_half_width=args.band_half_width,
        core_half_width=args.core_half_width,
        y_min_frac=args.y_min_frac,
    )
    # Anchor 0 only has future offsets; do not require more temporal views than exist.
    min_count = min(int(args.min_temporal_count), len(renders))
    repair, replace_mask, repair_diag = _compose_temporal_repair(
        base,
        temporal_rgb,
        temporal_count,
        temporal_y_std,
        seam_band,
        min_count=min_count,
        max_temporal_y_std=args.max_temporal_y_std,
        max_base_temporal_y_diff=args.max_base_temporal_y_diff,
    )

    overlay = _overlay_mask(base, replace_mask)
    seam_overlay = _overlay_mask(base, seam_band)
    temporal_valid_overlay = _overlay_mask(base, temporal_count >= min_count)

    scores = {
        "hard_select": score_one_anchor(base, slabs, weights, RING_PAIRS, win=args.ncc_win, max_sample_per_pair=args.max_sample_per_pair).get("aggregate", {}),
        "temporal_repair": score_one_anchor(repair, slabs, weights, RING_PAIRS, win=args.ncc_win, max_sample_per_pair=args.max_sample_per_pair).get("aggregate", {}),
    }
    base_f = base.astype(np.float32)
    repair_f = repair.astype(np.float32)
    changed = np.any(base != repair, axis=-1)
    fidelity = {
        "changed_px": int(changed.sum()),
        "changed_frac": float(changed.mean()),
        "mae_vs_hard_select_all": float(np.abs(repair_f - base_f).mean()),
        "mae_vs_hard_select_changed": float(np.abs(repair_f[changed] - base_f[changed]).mean()) if changed.any() else 0.0,
    }

    crops = _default_crops(args.erp_h, args.erp_w)
    rows = [
        ("hard_select", base),
        ("temporal_consensus", temporal_rgb),
        ("temporal_repair", repair),
        ("replace_overlay", overlay),
    ]
    crop_panel = _stack_crop_grid(rows, crops)
    overall_panel = _stack_rows(
        [
            ("hard_select", _resize_w(base, args.review_width)),
            ("seam_band_overlay", _resize_w(seam_overlay, args.review_width)),
            ("temporal_valid_overlay", _resize_w(temporal_valid_overlay, args.review_width)),
            ("temporal_consensus", _resize_w(temporal_rgb, args.review_width)),
            ("temporal_repair", _resize_w(repair, args.review_width)),
        ]
    )

    _save_rgb(out_dir / f"{run_name}_hard_select.jpg", base)
    _save_rgb(out_dir / f"{run_name}_temporal_consensus.jpg", temporal_rgb)
    _save_rgb(out_dir / f"{run_name}_temporal_repair.jpg", repair)
    _save_rgb(out_dir / f"{run_name}_replace_overlay.jpg", overlay)
    _save_rgb(out_dir / f"{run_name}_crop_review.jpg", crop_panel, quality=88)
    _save_rgb(out_dir / f"{run_name}_overall_review.jpg", overall_panel, quality=88)

    diag = {
        "run_name": run_name,
        "log_dir": str(log_dir),
        "anchor_idx": int(anchor_idx),
        "anchor_timestamp_ns": int(target_frame.anchor_timestamp_ns),
        "target_pose_timestamp_ns": int(target_pose_ts),
        "erp_hw": [int(args.erp_h), int(args.erp_w)],
        "panorama_center_z": float(center_z),
        "offsets": offset_diags,
        "seam": {
            "band_half_width": int(args.band_half_width),
            "core_half_width": int(args.core_half_width),
            "y_min_frac": float(args.y_min_frac),
            "band_px": int(seam_band.sum()),
            "core_px": int(seam_core.sum()),
            "pair_diags": seam_pair_diags,
        },
        "repair": repair_diag,
        "fidelity": fidelity,
        "scores": scores,
        "runtime_s": round(time.time() - t0, 3),
        "outputs": {
            "hard_select": str(out_dir / f"{run_name}_hard_select.jpg"),
            "temporal_consensus": str(out_dir / f"{run_name}_temporal_consensus.jpg"),
            "temporal_repair": str(out_dir / f"{run_name}_temporal_repair.jpg"),
            "crop_review": str(out_dir / f"{run_name}_crop_review.jpg"),
            "overall_review": str(out_dir / f"{run_name}_overall_review.jpg"),
        },
    }
    with (out_dir / f"{run_name}_diagnostics.json").open("w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2)
    print(
        f"[done] {run_name} replace={repair_diag['replace_px']} "
        f"changed={fidelity['changed_px']} runtime={diag['runtime_s']:.1f}s",
        flush=True,
    )
    return diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--cases", nargs="*", default=DEFAULT_CASES)
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/temporal_ground_seam_v1"))
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--offsets", type=str, default="-2,-1,1,2")
    ap.add_argument("--ground-z-m", type=float, default=0.0)
    ap.add_argument("--min-ground-dist-m", type=float, default=2.0)
    ap.add_argument("--max-ground-dist-m", type=float, default=55.0)
    ap.add_argument("--min-cam-cos", type=float, default=0.05)
    ap.add_argument("--band-half-width", type=int, default=48)
    ap.add_argument("--core-half-width", type=int, default=3)
    ap.add_argument("--y-min-frac", type=float, default=0.40)
    ap.add_argument("--min-temporal-count", type=int, default=2)
    ap.add_argument("--max-temporal-y-std", type=float, default=18.0)
    ap.add_argument("--max-base-temporal-y-diff", type=float, default=45.0)
    ap.add_argument("--ncc-win", type=int, default=17)
    ap.add_argument("--max-sample-per-pair", type=int, default=20000)
    ap.add_argument("--review-width", type=int, default=1200)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_diags = []
    for case in args.cases:
        log_dir, anchor, run_name = _parse_case(case, args.av2_root)
        all_diags.append(run_case(log_dir, anchor, run_name, args))
    summary_path = args.out_dir / "temporal_ground_seam_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump({"cases": all_diags}, f, indent=2)
    print(f"[summary] {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
