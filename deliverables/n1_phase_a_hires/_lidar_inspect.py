import sys
sys.path.insert(0, "/content/waymo2panorama/code")
import numpy as np
from pathlib import Path
from av2.utils.io import read_lidar_sweep

LOG = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/02a00399-3857-444e-8db3-a8f58489c394")
sweeps = sorted((LOG / "sensors/lidar").glob("*.feather"))
print(f"n_sweeps={len(sweeps)}")
print(f"first ts={int(sweeps[0].stem)}, last ts={int(sweeps[-1].stem)}")
if len(sweeps) > 1:
    avg_ms = (int(sweeps[-1].stem) - int(sweeps[0].stem)) / (len(sweeps) - 1) / 1e6
    print(f"avg interval ms={avg_ms:.1f}")

pts = read_lidar_sweep(sweeps[0])
print(f"shape={pts.shape} dtype={pts.dtype}")
print(f"x range: {pts[:,0].min():.1f} to {pts[:,0].max():.1f}")
print(f"y range: {pts[:,1].min():.1f} to {pts[:,1].max():.1f}")
print(f"z range: {pts[:,2].min():.1f} to {pts[:,2].max():.1f}")
ranges = np.linalg.norm(pts, axis=1)
print(f"range stats: min={ranges.min():.2f}, max={ranges.max():.2f}, mean={ranges.mean():.2f}")
print(f"range pct [5,25,50,75,95]: {np.percentile(ranges, [5,25,50,75,95])}")

anchor_ts = 315966070549927210
sweep_tss = np.array([int(p.stem) for p in sweeps])
nearest_idx = int(np.argmin(np.abs(sweep_tss - anchor_ts)))
delta_ms = abs(sweep_tss[nearest_idx] - anchor_ts) / 1e6
print(f"nearest sweep to anchor 0: idx={nearest_idx}, ts={sweep_tss[nearest_idx]}, delta={delta_ms:.2f} ms")

# Check extrinsics for lidar
import pandas as pd
extr = pd.read_feather(LOG / "calibration/egovehicle_SE3_sensor.feather")
print("\\nSensors in extrinsics:")
print(extr["sensor_name"].tolist())
lidar_row = extr.loc[extr["sensor_name"].isin(["up_lidar", "down_lidar"])]
print(f"\\nLiDAR extrinsics:")
print(lidar_row)
