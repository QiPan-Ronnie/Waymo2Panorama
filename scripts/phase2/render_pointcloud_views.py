"""
Render the fused L3 point cloud as 2D PNG views (since Drive can't preview .ply).

Outputs 4 PNGs:
    bev.png            top-down (Bird's Eye View)
    front_perspective  facing +x (forward), wide angle
    side_perspective   facing -y (looking from right toward left)
    oblique            45deg above ground, looking forward-down

Pure numpy + PIL; no matplotlib / Open3D dependency. Each view rasterizes
points into a 2D grid via additive RGB splatting weighted by 1.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def read_ply_xyz_rgb(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a PLY file written by export_l3_pointcloud.py. Returns xyz (N,3), rgb (N,3) uint8."""
    with open(path, "rb") as f:
        header_end = b"end_header\n"
        header_buf = b""
        while True:
            line = f.readline()
            if not line:
                raise ValueError("PLY header end not found")
            header_buf += line
            if line == header_end:
                break
        header_text = header_buf.decode("ascii", errors="replace")
        # extract vertex count
        n = None
        for line in header_text.splitlines():
            if line.startswith("element vertex"):
                n = int(line.split()[-1])
                break
        if n is None:
            raise ValueError("could not parse vertex count")
        dtype = np.dtype([
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
            ("red", "u1"), ("green", "u1"), ("blue", "u1"),
        ])
        data = np.frombuffer(f.read(n * dtype.itemsize), dtype=dtype, count=n)
        xyz = np.stack([data["x"], data["y"], data["z"]], axis=-1)
        rgb = np.stack([data["red"], data["green"], data["blue"]], axis=-1)
    return xyz, rgb


def render_view(
    xyz: np.ndarray,
    rgb: np.ndarray,
    width: int,
    height: int,
    eye: np.ndarray,
    look_at: np.ndarray,
    up: np.ndarray,
    fov_deg: float,
    z_near: float = 0.5,
    z_far: float = 200.0,
    point_radius_px: int = 1,
) -> np.ndarray:
    """Pinhole project xyz to a width x height canvas seen from `eye`.

    Splats each point as a small disc of given radius (in pixels) with weight 1.
    Where multiple points overlap, the front-most (smallest z in cam frame) wins.
    """
    # Build view matrix (right-handed: cam +z FORWARD, +y UP, +x RIGHT — OpenCV-ish but +y up)
    # We use: looking direction = (look_at - eye); right = norm(forward x up); up' = norm(right x forward)
    forward = look_at - eye
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    up_corr = np.cross(right, forward)
    # cam->world: rotation columns are [right, up_corr, forward]; we want world->cam (transpose)
    R_cam_world = np.stack([right, up_corr, forward], axis=0)   # (3, 3)
    t_cam_world = -R_cam_world @ eye

    pts_cam = xyz @ R_cam_world.T + t_cam_world[None, :]
    z = pts_cam[:, 2]
    in_front = (z > z_near) & (z < z_far)

    # Pinhole intrinsics from FOV
    f = 0.5 * width / np.tan(np.deg2rad(fov_deg) * 0.5)
    u = f * pts_cam[:, 0] / np.maximum(z, 1e-6) + width * 0.5
    v = -f * pts_cam[:, 1] / np.maximum(z, 1e-6) + height * 0.5  # negate y so +up
    in_bounds = (u >= 0) & (u < width) & (v >= 0) & (v < height)
    valid = in_front & in_bounds

    ui = u[valid].astype(np.int64)
    vi = v[valid].astype(np.int64)
    zi = z[valid]
    ci = rgb[valid].astype(np.uint8)

    # Sort back-to-front so nearer points overwrite farther ones
    order = np.argsort(-zi)  # descending z (farthest first), so nearer drawn last
    ui = ui[order]; vi = vi[order]; ci = ci[order]

    canvas = np.zeros((height, width, 3), dtype=np.uint8)

    if point_radius_px <= 0:
        canvas[vi, ui] = ci
    else:
        for k in range(len(ui)):
            x0 = max(0, ui[k] - point_radius_px)
            x1 = min(width, ui[k] + point_radius_px + 1)
            y0 = max(0, vi[k] - point_radius_px)
            y1 = min(height, vi[k] + point_radius_px + 1)
            canvas[y0:y1, x0:x1] = ci[k]

    return canvas


def add_axes_overlay(canvas: np.ndarray, title: str) -> np.ndarray:
    img = Image.fromarray(canvas)
    draw = ImageDraw.Draw(img)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except Exception:
        font = None
    draw.text((10, 8), title, fill=(255, 255, 255), font=font)
    return np.asarray(img)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ply-path", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--max-points", type=int, default=400000)
    ap.add_argument("--point-radius-px", type=int, default=1)
    args = ap.parse_args()

    ply_path = Path(args.ply_path)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[render] reading {ply_path} ...")
    xyz, rgb = read_ply_xyz_rgb(ply_path)
    print(f"[render] {xyz.shape[0]} points; ego x range [{xyz[:,0].min():.1f}, {xyz[:,0].max():.1f}] m, "
          f"y [{xyz[:,1].min():.1f}, {xyz[:,1].max():.1f}] m, "
          f"z [{xyz[:,2].min():.1f}, {xyz[:,2].max():.1f}] m")

    if xyz.shape[0] > args.max_points:
        rng = np.random.default_rng(0)
        idx = rng.choice(xyz.shape[0], args.max_points, replace=False)
        xyz = xyz[idx]
        rgb = rgb[idx]
        print(f"[render] subsampled to {xyz.shape[0]} points")

    # Ego frame: x forward, y left, z up.
    # Use world-up = +z; view directions chosen for clarity.
    z_up = np.array([0.0, 0.0, 1.0])

    views = [
        # (filename, eye_pos, look_at, fov_deg, title)
        ("bev.png",
         np.array([0.0, 0.0, 45.0]),       # 45m above ego origin
         np.array([8.0, 0.0, 0.0]),        # looking down at 8m forward
         70.0,
         "Bird's-Eye View (eye at z=45m, looking down; ego = center; +x = forward / up in image)"),

        ("front_perspective.png",
         np.array([-12.0, 0.0, 4.0]),      # 12m behind ego at 4m height
         np.array([10.0, 0.0, 1.0]),       # looking forward+down
         65.0,
         "Front Perspective (eye behind ego, looking forward; +x forward, +z up)"),

        ("side_perspective.png",
         np.array([8.0, 20.0, 6.0]),       # 8m forward, 20m left, 6m up
         np.array([8.0, 0.0, 0.0]),        # looking right at scene
         65.0,
         "Side Perspective (eye left of scene, looking right; shows depth layering)"),

        ("oblique.png",
         np.array([-10.0, 12.0, 12.0]),    # 10m back-left, 12m up
         np.array([10.0, -2.0, 0.0]),
         60.0,
         "Oblique (45deg above scene, forward-right view)"),
    ]

    for fname, eye, look_at, fov, title in views:
        print(f"[render] {fname} eye={eye.tolist()} look_at={look_at.tolist()} fov={fov}")
        canvas = render_view(
            xyz, rgb, args.width, args.height,
            eye, look_at, z_up, fov,
            point_radius_px=args.point_radius_px,
        )
        canvas = add_axes_overlay(canvas, title)
        Image.fromarray(canvas).save(out_dir / fname)

    # Also: an extra-large BEV with point_radius=2 for clarity (top-down is often the most informative)
    big_bev = render_view(
        xyz, rgb, 1600, 1600,
        np.array([0.0, 0.0, 35.0]),
        np.array([5.0, 0.0, 0.0]),
        90.0,
        z_far=200.0,
        point_radius_px=2,
    )
    big_bev = add_axes_overlay(big_bev, "Bird's-Eye View (large, ego=center; +x forward/up; +y left/left)")
    Image.fromarray(big_bev).save(out_dir / "bev_large.png")

    print(f"[render] done -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
