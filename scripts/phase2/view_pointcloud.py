"""
Interactive 3D viewer for the fused L3 point cloud (Open3D-based).

Default opens:
    outputs/phase2/l3_pointcloud/fused_pointcloud.ply

Controls (Open3D viewer):
    LMB drag      rotate
    Shift+LMB drag pan
    Wheel         zoom
    [ / ]         decrease / increase point size
    R             reset view
    H             help (full key list)
    Q / Esc       quit

The ego origin (0, 0, 0) is at the bottom center of the scene
(AV2 ego: x forward, y left, z up).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    default_ply = (here / "../../outputs/phase2/l3_pointcloud/fused_pointcloud.ply").resolve()
    ap.add_argument("--ply", default=str(default_ply))
    ap.add_argument("--point-size", type=float, default=2.0)
    ap.add_argument("--background", default="dark", choices=["dark", "light"])
    args = ap.parse_args()

    ply_path = Path(args.ply)
    if not ply_path.exists():
        print(f"PLY not found: {ply_path}", file=sys.stderr)
        print("Download it from Drive (see prompt) and re-run.", file=sys.stderr)
        return 1

    import open3d as o3d  # noqa: PLC0415

    print(f"Loading {ply_path} ...")
    pc = o3d.io.read_point_cloud(str(ply_path))
    pts = np.asarray(pc.points)
    print(f"Loaded {len(pts)} points")
    print(f"  ego x in [{pts[:, 0].min():.1f}, {pts[:, 0].max():.1f}] m (forward)")
    print(f"  ego y in [{pts[:, 1].min():.1f}, {pts[:, 1].max():.1f}] m (left)")
    print(f"  ego z in [{pts[:, 2].min():.1f}, {pts[:, 2].max():.1f}] m (up)")

    # Coordinate frame at ego origin (3 colored arrows: red=+x, green=+y, blue=+z)
    axes = o3d.geometry.TriangleMesh.create_coordinate_frame(size=2.0, origin=[0, 0, 0])

    # A small sphere at ego origin so it's easy to find
    ego_sphere = o3d.geometry.TriangleMesh.create_sphere(radius=0.3)
    ego_sphere.translate([0, 0, 0])
    ego_sphere.paint_uniform_color([1.0, 0.0, 0.0])  # red

    vis = o3d.visualization.Visualizer()
    vis.create_window(window_name=f"L3 fused point cloud — {ply_path.name}",
                      width=1400, height=900)
    vis.add_geometry(pc)
    vis.add_geometry(axes)
    vis.add_geometry(ego_sphere)

    opt = vis.get_render_option()
    opt.point_size = args.point_size
    if args.background == "dark":
        opt.background_color = np.asarray([0.05, 0.05, 0.07])
    else:
        opt.background_color = np.asarray([0.95, 0.95, 0.95])

    # Camera: place ~30m above + 15m behind ego, looking forward+down
    ctr = vis.get_view_control()
    # set front (camera-to-target dir), up, lookat, zoom
    ctr.set_lookat([5.0, 0.0, 0.0])
    ctr.set_up([0.0, 0.0, 1.0])
    ctr.set_front([-1.0, 0.0, 0.6])
    ctr.set_zoom(0.5)

    print("Viewer ready. LMB drag = rotate, Shift+LMB drag = pan, wheel = zoom, [/] = point size, R = reset, Q = quit.")
    vis.run()
    vis.destroy_window()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
