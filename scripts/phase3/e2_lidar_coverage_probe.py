"""E2 de-risk probe: is LiDAR dense/accurate enough INSIDE the ~7 seam strips?

E2 plans to depth-correct (N1 reproject) the cameras inside the seam strips using per-pixel
LiDAR range, so the two overlapping views ALIGN (no doubling) before blending. That only works
if LiDAR actually covers the strip pixels — especially the NEAR-FIELD objects (the BMW car at
the front-right seam) whose parallax is the whole problem. This probe quantifies that BEFORE we
invest in the full E2 blend.

Outputs, per anchor:
  - coverage stats: % of seam-strip pixels with real LiDAR support (hit or kNN-densified, i.e.
    depth < fill_far), split by ALL strips vs NEAR strips (depth < near_m), printed + JSON.
  - {tag}_e2probe.png: L1 hard_select with the seam strips outlined and the LiDAR ERP depth
    (turbo) shown INSIDE the strips, so we can SEE whether the near car/building is covered.

Reads LiDAR .feather directly with pandas (no av2 SDK dependency / py-version issues).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


def load_lidar_xyz_nearest(log_dir: Path, anchor_ts_ns: int, max_delta_ms: float = 75.0):
    """Nearest LiDAR sweep to anchor_ts; return (N,3) ego-frame xyz via pandas (no av2)."""
    import pandas as pd
    sweep_dir = Path(log_dir) / "sensors" / "lidar"
    sweeps = sorted(sweep_dir.glob("*.feather"))
    if not sweeps:
        raise FileNotFoundError(f"no LiDAR sweeps in {sweep_dir}")
    tss = np.array([int(p.stem) for p in sweeps], dtype=np.int64)
    i = int(np.argmin(np.abs(tss - anchor_ts_ns)))
    delta_ms = abs(int(tss[i]) - int(anchor_ts_ns)) / 1e6
    df = pd.read_feather(sweeps[i])
    xyz = np.stack([df["x"].to_numpy(), df["y"].to_numpy(), df["z"].to_numpy()], axis=1).astype(np.float64)
    return xyz, int(tss[i]), float(delta_ms)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--tag", type=str, default="bmw")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=64)
    ap.add_argument("--near-m", type=float, default=15.0, help="threshold for 'near-field' strip pixels")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent / "code")
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.w2p_code))

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.depth.lidar_to_erp_depth import project_lidar_to_erp_depth, visualize_depth_map
    from waymo2panorama.blending.seam_confined import _label_and_base, _seam_alpha

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    anchor_ts = int(ts_all[args.anchor])
    frame = loader.load_synced_frame(anchor_ts)
    erp_hw = (args.erp_h, args.erp_w)
    H, W = erp_hw

    slabs, weights = [], []
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(image=frame.images[cam], K=c.K, T_ego_cam=c.T_ego_cam,
                                         erp_hw=erp_hw, convergence_distance_m=None)
        slabs.append(rgb); weights.append(w)
    argmax, valid, base = _label_and_base(slabs, weights)
    alpha, boundary = _seam_alpha(argmax, valid, args.band_half_width)
    strip = alpha > 0  # the ~7 seam strips

    xyz, sweep_ts, delta_ms = load_lidar_xyz_nearest(args.log_dir, anchor_ts)
    depth, summ = project_lidar_to_erp_depth(xyz, erp_hw=erp_hw, max_range_m=100.0,
                                             densify_radius_px=6, fill_far_m=1000.0)
    supported = depth < 500.0  # real LiDAR support (hit or densified), not far-fill

    n_strip = int(strip.sum())
    strip_support = float((strip & supported).sum() / max(1, n_strip))
    near = strip & (depth < args.near_m)
    n_near = int(near.sum())
    near_support = float((near & supported).sum() / max(1, n_near)) if n_near else 0.0
    # also: of strip pixels, what fraction are near-field at all (the cases that NEED E2)
    near_frac_of_strip = float(n_near / max(1, n_strip))

    report = dict(
        tag=args.tag, anchor=args.anchor, lidar_delta_ms=round(delta_ms, 2),
        n_strip_px=n_strip, strip_lidar_support_pct=round(100 * strip_support, 1),
        near_m=args.near_m, n_near_strip_px=n_near,
        near_frac_of_strip_pct=round(100 * near_frac_of_strip, 1),
        near_strip_lidar_support_pct=round(100 * near_support, 1),
        lidar_global=summ,
    )
    print(json.dumps(report, indent=2))
    with open(args.output_dir / f"{args.tag}_e2probe.json", "w") as f:
        json.dump(report, f, indent=2)

    # viz: base, with LiDAR depth (turbo) shown inside strips, strip boundary outlined
    import cv2
    vis = base.copy()
    dviz = visualize_depth_map(depth)            # RGB
    show = strip & supported
    vis[show] = (0.45 * vis[show] + 0.55 * dviz[show]).astype(np.uint8)
    # outline the strips (boundary of the strip mask)
    edge = (cv2.dilate(strip.astype(np.uint8), np.ones((3, 3), np.uint8)) - strip.astype(np.uint8)) > 0
    vis[edge] = (255, 255, 0)
    Image.fromarray(vis).save(args.output_dir / f"{args.tag}_e2probe.png")
    print(f"[{args.tag}] strip LiDAR support {report['strip_lidar_support_pct']}% | "
          f"near({args.near_m}m) strip support {report['near_strip_lidar_support_pct']}% "
          f"({report['near_frac_of_strip_pct']}% of strip is near) -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
