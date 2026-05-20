"""
Phase 2 — Export the L3 fused 3D point cloud (the actual product of L3) +
per-view depth visualizations.

L3's value is NOT a pretty ERP image — it's a dense metric 3D scene in the
ego frame, suitable for downstream consumers (Pantheon360, 360° diffusion).

This script writes:
    fused_pointcloud.ply           # 7-cam-fused, Sim3-aligned, conf-filtered,
                                   # in AV2 ego frame (x forward, y left, z up).
    depth_{cam}.png                # per-view depth, viridis colormap, 504x504
    depth_overlay_{cam}.png        # depth overlaid on input RGB (50% blend)
    pointcloud_summary.json        # diagnostics: N points, per-cam contribution,
                                   # depth histograms, distance stats
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _depth_to_colormap(depth: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    """Cheap viridis-like colormap without matplotlib dependency."""
    d = np.clip((depth - vmin) / max(vmax - vmin, 1e-9), 0.0, 1.0)
    # Approximate viridis with 5 anchor RGB stops
    stops = np.array([
        [0.267, 0.005, 0.329],   # dark purple
        [0.282, 0.140, 0.457],
        [0.254, 0.265, 0.530],
        [0.207, 0.372, 0.553],
        [0.165, 0.471, 0.558],
        [0.128, 0.567, 0.551],
        [0.135, 0.659, 0.518],
        [0.267, 0.749, 0.441],
        [0.478, 0.821, 0.318],
        [0.741, 0.873, 0.150],
        [0.993, 0.906, 0.144],   # yellow
    ])
    # Map to stops
    i_f = d * (len(stops) - 1)
    i0 = np.floor(i_f).astype(np.int32)
    i0 = np.clip(i0, 0, len(stops) - 2)
    t = (i_f - i0).astype(np.float32)
    c0 = stops[i0]
    c1 = stops[i0 + 1]
    rgb = (1 - t[..., None]) * c0 + t[..., None] * c1
    return (rgb * 255.0).astype(np.uint8)


def _write_ply_binary(path: Path, xyz: np.ndarray, rgb_u8: np.ndarray) -> None:
    """Write a binary little-endian PLY file with vertex x/y/z + red/green/blue."""
    assert xyz.shape[0] == rgb_u8.shape[0]
    n = xyz.shape[0]
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property uchar red\n"
        "property uchar green\n"
        "property uchar blue\n"
        "end_header\n"
    ).encode("ascii")
    # interleave: x, y, z, r, g, b (4+4+4+1+1+1 = 15 bytes each)
    dtype = np.dtype([
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
        ("red", "u1"), ("green", "u1"), ("blue", "u1"),
    ])
    buf = np.empty(n, dtype=dtype)
    buf["x"] = xyz[:, 0]
    buf["y"] = xyz[:, 1]
    buf["z"] = xyz[:, 2]
    buf["red"] = rgb_u8[:, 0]
    buf["green"] = rgb_u8[:, 1]
    buf["blue"] = rgb_u8[:, 2]
    with open(path, "wb") as f:
        f.write(header)
        f.write(buf.tobytes())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--pi3-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--conf-threshold", type=float, default=0.5)
    ap.add_argument("--min-distance-m", type=float, default=0.5)
    ap.add_argument("--max-distance-m", type=float, default=60.0)
    ap.add_argument("--depth-vmin", type=float, default=0.0,
                    help="depth colormap min (metric, after Sim3 scale)")
    ap.add_argument("--depth-vmax", type=float, default=40.0)
    ap.add_argument("--max-points-per-cam", type=int, default=0,
                    help="if > 0, randomly subsample to this many points per cam after filtering")
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.alignment.sim3_align import fit_sim3_from_camera_translations
    from waymo2panorama.pipeline.lift_and_project import apply_sim3_to_points

    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pi3_summary = json.loads((pi3_dir / "summary.json").read_text())
    pi3_cams = pi3_summary["cameras"]

    # ---- Load + fit Sim(3) ----
    pi3_world_points: dict[str, np.ndarray] = {}
    pi3_local_z: dict[str, np.ndarray] = {}
    pi3_confs: dict[str, np.ndarray] = {}
    cam_colors: dict[str, np.ndarray] = {}
    pi3_cam_pos: dict[str, np.ndarray] = {}
    av2_cam_pos: dict[str, np.ndarray] = {}
    for cam in pi3_cams:
        pi3_world_points[cam] = np.load(pi3_dir / f"points_{cam}.npy")
        local = np.load(pi3_dir / f"local_points_{cam}.npy")
        pi3_local_z[cam] = local[..., 2].copy()
        conf_raw = np.load(pi3_dir / f"conf_{cam}.npy")
        pi3_confs[cam] = _sigmoid(conf_raw).astype(np.float32)
        pose_pi3 = np.load(pi3_dir / f"pose_{cam}.npy")
        T_ego_cam = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        pi3_cam_pos[cam] = pose_pi3[:3, 3].astype(np.float64)
        av2_cam_pos[cam] = T_ego_cam[:3, 3].astype(np.float64)
        img = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
        cam_colors[cam] = img  # uint8 (H, W, 3)

    sim3, sim3_diag = fit_sim3_from_camera_translations(pi3_cam_pos, av2_cam_pos)
    print(f"[ply] Sim(3): scale={sim3.scale:.4f}, mean_residual={sim3_diag['mean_residual_m']:.3f} m")

    # ---- Per-cam filter + fuse ----
    all_xyz: list[np.ndarray] = []
    all_rgb: list[np.ndarray] = []
    per_cam_stats: dict[str, dict] = {}

    rng = np.random.default_rng(0)

    for cam in pi3_cams:
        pts_world = pi3_world_points[cam]      # (H, W, 3) Pi3 world
        pts_ego = apply_sim3_to_points(pts_world, sim3)  # (H, W, 3) ego frame metric
        conf = pi3_confs[cam]                  # (H, W) prob in [0,1]
        rgb = cam_colors[cam]                  # (H, W, 3) uint8

        N = pts_ego.shape[0] * pts_ego.shape[1]
        pts_flat = pts_ego.reshape(-1, 3)
        rgb_flat = rgb.reshape(-1, 3)
        conf_flat = conf.reshape(-1)
        local_z_flat = pi3_local_z[cam].reshape(-1)

        r = np.linalg.norm(pts_flat, axis=-1)
        valid = (
            np.isfinite(r) & (r > args.min_distance_m) & (r < args.max_distance_m)
            & (conf_flat > args.conf_threshold)
            & (local_z_flat > 0)
        )
        n_valid = int(valid.sum())

        if args.max_points_per_cam > 0 and n_valid > args.max_points_per_cam:
            keep_idx = np.flatnonzero(valid)
            chosen = rng.choice(keep_idx, args.max_points_per_cam, replace=False)
            valid = np.zeros_like(valid)
            valid[chosen] = True
            n_valid = int(valid.sum())

        per_cam_stats[cam] = {
            "n_total": N,
            "n_after_filter": n_valid,
            "keep_ratio": n_valid / N if N > 0 else 0.0,
            "depth_median_m": float(np.median(r[valid])) if n_valid > 0 else None,
            "depth_p10_m": float(np.percentile(r[valid], 10)) if n_valid > 0 else None,
            "depth_p90_m": float(np.percentile(r[valid], 90)) if n_valid > 0 else None,
        }
        print(f"[ply]   {cam:22s} kept {n_valid:7d}/{N} = {n_valid/N:.1%}  "
              f"depth median={per_cam_stats[cam]['depth_median_m']:.1f} m")

        all_xyz.append(pts_flat[valid].astype(np.float32))
        all_rgb.append(rgb_flat[valid].astype(np.uint8))

        # ---- depth visualizations (per-view) ----
        depth_2d = np.linalg.norm(pts_ego, axis=-1)  # (H, W) ego-origin distance
        # Mask invalid
        mask_2d = (
            np.isfinite(depth_2d)
            & (depth_2d > args.min_distance_m)
            & (depth_2d < args.max_distance_m)
            & (conf > args.conf_threshold)
            & (pi3_local_z[cam] > 0)
        )
        depth_vis = _depth_to_colormap(depth_2d, args.depth_vmin, args.depth_vmax)
        depth_vis[~mask_2d] = 0
        Image.fromarray(depth_vis).save(out_dir / f"depth_{cam}.png")

        # Overlay (50% blend on input)
        overlay = (0.5 * rgb.astype(np.float32) + 0.5 * depth_vis.astype(np.float32))
        overlay[~mask_2d] = rgb[~mask_2d].astype(np.float32) * 0.4  # darken excluded
        overlay = np.clip(overlay, 0, 255).astype(np.uint8)
        Image.fromarray(overlay).save(out_dir / f"depth_overlay_{cam}.png")

    # ---- Concatenate + write PLY ----
    xyz = np.concatenate(all_xyz, axis=0)
    rgb_u8 = np.concatenate(all_rgb, axis=0)
    n_total = xyz.shape[0]
    print(f"[ply] total fused points: {n_total}")

    ply_path = out_dir / "fused_pointcloud.ply"
    _write_ply_binary(ply_path, xyz, rgb_u8)
    print(f"[ply] wrote {ply_path}  size MB: {ply_path.stat().st_size / 1024 / 1024:.1f}")

    # ---- Summary JSON ----
    summary = {
        "pi3_dir": str(pi3_dir),
        "n_total_points": int(n_total),
        "conf_threshold": args.conf_threshold,
        "min_distance_m": args.min_distance_m,
        "max_distance_m": args.max_distance_m,
        "sim3": {
            "scale": sim3.scale,
            "translation_m": sim3.t.tolist(),
            "rotation_matrix": sim3.R.tolist(),
            "diagnostics": sim3_diag,
        },
        "per_cam": per_cam_stats,
        "ego_frame_extent": {
            "x_min": float(xyz[:, 0].min()), "x_max": float(xyz[:, 0].max()),
            "y_min": float(xyz[:, 1].min()), "y_max": float(xyz[:, 1].max()),
            "z_min": float(xyz[:, 2].min()), "z_max": float(xyz[:, 2].max()),
        },
        "outputs": {
            "pointcloud_ply": str(ply_path.name),
            "depth_pngs": [f"depth_{c}.png" for c in pi3_cams],
            "depth_overlays": [f"depth_overlay_{c}.png" for c in pi3_cams],
        },
    }
    (out_dir / "pointcloud_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[ply] done -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
