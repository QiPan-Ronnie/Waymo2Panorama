"""E2 driver: seam-confined DEPTH-ALIGNED fusion (LiDAR z + N1 reprojection), on one AV2 anchor.

Pipeline:
  1. Render 7 cams rotation-only (convergence=None) -> L1 base + seam strips + alpha.
  2. Project nearest LiDAR sweep -> ERP depth map; build a confined r_map = LiDAR depth ONLY
     inside the strips where supported, 'far' elsewhere.
  3. Re-render 7 cams with convergence_distance_m = r_map (N1) -> near/mid strip content is
     reprojected to the ego centre so overlapping cameras AGREE (no parallax doubling).
  4. blend_seam_e2_depth: L1 base outside band; inside, multiband blend of the ALIGNED N1 slabs.
Far field stays BYTE-IDENTICAL to L1.

Outputs: {tag}_L1.png, {tag}_E2.png, {tag}_rmap.png (depth viz), {tag}_E2_seamcrops.png
(L1 | E2 at the strongest seams + BMW seam), and a changed-% sanity print.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


def _se3(qw, qx, qy, qz, tx, ty, tz):
    R = np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]])
    T = np.eye(4); T[:3, :3] = R; T[:3, 3] = [tx, ty, tz]; return T


def load_lidar_xyz_nearest(log_dir: Path, anchor_ts_ns: int, accum_sweeps: int = 1):
    """Single sweep, OR accumulate `accum_sweeps` consecutive sweeps motion-compensated into the
    anchor ego frame via the AV2 city_SE3_egovehicle poses (densifies STATIC scene -> denser depth)."""
    import pandas as pd
    sweeps = sorted((Path(log_dir) / "sensors" / "lidar").glob("*.feather"))
    tss = np.array([int(p.stem) for p in sweeps], dtype=np.int64)
    i0 = int(np.argmin(np.abs(tss - anchor_ts_ns)))
    if accum_sweeps <= 1:
        df = pd.read_feather(sweeps[i0])
        xyz = np.stack([df["x"].to_numpy(), df["y"].to_numpy(), df["z"].to_numpy()], axis=1).astype(np.float64)
        return xyz, float(abs(int(tss[i0]) - anchor_ts_ns) / 1e6)
    poses = pd.read_feather(Path(log_dir) / "city_SE3_egovehicle.feather")
    pts = poses["timestamp_ns"].to_numpy()

    def pose_at(ts):
        r = poses.iloc[int(np.argmin(np.abs(pts - ts)))]
        return _se3(r["qw"], r["qx"], r["qy"], r["qz"], r["tx_m"], r["ty_m"], r["tz_m"])
    T_anchor_city = np.linalg.inv(pose_at(int(tss[i0])))
    half = accum_sweeps // 2
    allp = []
    for j in range(max(0, i0 - half), min(len(sweeps), i0 + half + 1)):
        df = pd.read_feather(sweeps[j])
        P = np.stack([df["x"].to_numpy(), df["y"].to_numpy(), df["z"].to_numpy()], axis=1).astype(np.float64)
        T = T_anchor_city @ pose_at(int(tss[j]))
        allp.append(P @ T[:3, :3].T + T[:3, 3])
    return np.concatenate(allp, 0), 0.0


def _crop(img, c, half_w, half_h, W, H, rc=None):
    rc = H // 2 if rc is None else rc
    l, r = max(0, c - half_w), min(W, c + half_w)
    t, b = max(0, rc - half_h), min(H, rc + half_h)
    out = img[t:b, l:r]
    return np.pad(out, ((0, max(0, 2 * half_h - out.shape[0])), (0, max(0, 2 * half_w - out.shape[1])), (0, 0)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--tag", type=str, default="bmw")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--band-half-width", type=int, default=64)
    ap.add_argument("--near-max-m", type=float, default=-1.0,
                    help=">0: only depth-correct pixels nearer than this (m); else all supported")
    ap.add_argument("--lowfreq-cutoff", type=int, default=-1,
                    help=">=0: low-freq-only blend of the aligned N1 slabs (no doubling on uncorrected)")
    ap.add_argument("--dense-depth", action="store_true",
                    help="use LiDAR-anchored DA-V2 dense metric depth instead of sparse LiDAR+kNN")
    ap.add_argument("--device", type=int, default=-1, help="DA-V2 device (-1 CPU, >=0 GPU id)")
    ap.add_argument("--accum-sweeps", type=int, default=1, help="accumulate N LiDAR sweeps (motion-compensated) for denser depth")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent / "code")
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.w2p_code))

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.depth.lidar_to_erp_depth import project_lidar_to_erp_depth, visualize_depth_map
    from waymo2panorama.blending.seam_confined import confined_depth_rmap, blend_seam_e2_depth

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    anchor_ts = int(ts_all[args.anchor])
    frame = loader.load_synced_frame(anchor_ts)
    erp_hw = (args.erp_h, args.erp_w)
    H, W = erp_hw

    # 1) rotation-only (L1) renders
    t0 = time.time()
    slabs_l1, weights_l1 = [], []
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(image=frame.images[cam], K=c.K, T_ego_cam=c.T_ego_cam,
                                         erp_hw=erp_hw, convergence_distance_m=None)
        slabs_l1.append(rgb); weights_l1.append(w)

    # 2) depth source: sparse LiDAR+kNN (default) OR LiDAR-anchored DA-V2 dense metric depth
    xyz, delta_ms = load_lidar_xyz_nearest(args.log_dir, anchor_ts, accum_sweeps=args.accum_sweeps)
    print(f"[{args.tag}] lidar points={len(xyz)} (accum_sweeps={args.accum_sweeps})", flush=True)
    if args.dense_depth:
        from waymo2panorama.depth.dense_lidar_depth import dense_metric_depth_erp
        depth, ddiag = dense_metric_depth_erp(frame, xyz, erp_hw, device=args.device)
        print(f"[{args.tag}] dense depth support {ddiag['dense_support_pct']}% ; "
              f"fits={ {k: v.get('a') for k, v in ddiag['per_cam_fit'].items()} }")
    else:
        depth, _summ = project_lidar_to_erp_depth(xyz, erp_hw=erp_hw, max_range_m=100.0,
                                                  densify_radius_px=6, fill_far_m=1000.0)
    near_max = None if args.near_max_m <= 0 else args.near_max_m
    r_map, strip, alpha, boundary = confined_depth_rmap(
        slabs_l1, weights_l1, depth, band_half_width=args.band_half_width, near_max_m=near_max)
    print(f"[{args.tag}] LiDAR dt={delta_ms:.1f}ms; strip={int(strip.sum())}px; "
          f"depth-corrected px={int(((r_map<5e5)).sum())}")

    # 3) N1 (depth-aligned) renders with the confined r_map
    slabs_n1, weights_n1 = [], []
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(image=frame.images[cam], K=c.K, T_ego_cam=c.T_ego_cam,
                                         erp_hw=erp_hw, convergence_distance_m=r_map)
        slabs_n1.append(rgb); weights_n1.append(w)

    # 4) E2 blend
    lfc = None if args.lowfreq_cutoff < 0 else args.lowfreq_cutoff
    res = blend_seam_e2_depth(slabs_l1, weights_l1, slabs_n1, weights_n1, alpha,
                              num_bands=args.num_bands, wrap=True, lowfreq_cutoff=lfc)
    L1, E2 = res["base"], res["out"]
    print(f"[{args.tag}] E2 done in {time.time()-t0:.1f}s; "
          f"E2 changed {100*np.any(E2!=L1,axis=-1).mean():.2f}% of pixels")

    Image.fromarray(L1).save(args.output_dir / f"{args.tag}_L1.png")
    Image.fromarray(E2).save(args.output_dir / f"{args.tag}_E2.png")
    Image.fromarray(visualize_depth_map(np.where(strip, depth, 1000.0))).save(args.output_dir / f"{args.tag}_rmap.png")

    # seam crops L1 | E2 at strongest seams + BMW seam
    col_mass = alpha.sum(axis=0)
    peaks = []
    for c in np.argsort(col_mass)[::-1]:
        if col_mass[c] < 0.4 * col_mass.max():
            break
        if all(abs(c - p) > 80 and abs(c - p) < W - 80 for p in peaks):
            peaks.append(int(c))
        if len(peaks) >= 4:
            break
    cols = sorted(set(peaks + [int(round(3500 / 4096 * W))]))
    hw, hh = 110, 220
    rows = []
    for c in cols:
        rows.append(np.hstack([_crop(L1, c, hw, hh, W, H), np.full((2 * hh, 6, 3), 60, np.uint8),
                               _crop(E2, c, hw, hh, W, H)]))
    mw = max(r.shape[1] for r in rows)
    strip_img = np.vstack([np.pad(r, ((0, 0), (0, mw - r.shape[1]), (0, 0))) for r in rows])
    Image.fromarray(strip_img).save(args.output_dir / f"{args.tag}_E2_seamcrops.png")
    print(f"[{args.tag}] seam cols (L1|E2): {cols} -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
