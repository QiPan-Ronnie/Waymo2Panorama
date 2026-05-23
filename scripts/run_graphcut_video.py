"""
新-B Graph-cut seam video driver — L1 sphere + Boykov-Kolmogorov min-cut
seam selection per frame -> mp4.

Per frame:
  1. Render 7 cam to ERP slabs (sphere projection)
  2. Compute per-cam ERP axis columns (where each cam's optical axis hits ERP)
  3. apply_graphcut_seams() -> new weights with min-cut seam paths
  4. multiband_blend(slabs, new_weights) -> ERP frame
  5. Append to mp4

CPU only (PyMaxflow / scipy.csgraph). Per frame ~6-8s.
Expected wall time: ~12-15 min for 5sec video (100 frames).

Usage on Colab:
    python scripts/run_graphcut_video.py \
        --log-dir /content/drive/MyDrive/.../02a00399-... \
        --out-dir /content/drive/MyDrive/.../outputs/graphcut_video \
        --start-sec 3.0 --duration-sec 5.0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import imageio
import numpy as np


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--erp-height", type=int, default=1024)
    ap.add_argument("--erp-width", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--start-sec", type=float, default=3.0)
    ap.add_argument("--duration-sec", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--frame-step", type=int, default=1)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / "../code").resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.blending.graphcut_seam import (
        apply_graphcut_seams,
        _erp_column_for_axis_world,
    )

    log_id = args.log_dir.name
    out_dir = args.out_dir / log_id
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / "graphcut_video.mp4"
    done_marker = out_dir / "done.json"

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    start_idx = max(0, int(args.start_sec * args.fps))
    stop_idx = min(len(ts_all), start_idx + int(args.duration_sec * args.fps))
    indices = list(range(start_idx, stop_idx, args.frame_step))
    n_frames = len(indices)
    print(f"[gc-video] Rendering {n_frames} frames", flush=True)

    writer = imageio.get_writer(mp4_path, fps=args.fps, codec="libx264", quality=8)
    erp_hw = (args.erp_height, args.erp_width)
    cams = list(RING_CAMS_7)

    t_total = time.time()
    per_frame_times: list[float] = []

    for i, idx in enumerate(indices):
        anchor = ts_all[idx]
        frame = loader.load_synced_frame(anchor)

        t0 = time.time()
        slabs: list[np.ndarray] = []
        alphas: list[np.ndarray] = []
        weights: list[np.ndarray] = []
        cam_axes_erp: list[float] = []
        for cam in cams:
            img = frame.images[cam]
            calib = frame.calibrations[cam]
            rgb, alpha, w = render_camera_to_erp(
                image=img, K=calib.K, T_ego_cam=calib.T_ego_cam, erp_hw=erp_hw,
            )
            slabs.append(rgb)
            alphas.append(alpha)
            weights.append(w)
            cam_axes_erp.append(_erp_column_for_axis_world(calib.T_ego_cam, args.erp_width))

        new_weights = apply_graphcut_seams(slabs, alphas, weights, cam_axes_erp)
        erp = multiband_blend(slabs, new_weights, num_bands=args.num_bands, wrap=True)
        per_frame_times.append(time.time() - t0)
        writer.append_data(erp)

        if (i + 1) % 10 == 0 or i == 0 or i == n_frames - 1:
            elapsed = time.time() - t_total
            print(f"  [{i + 1:4d}/{n_frames:4d}] idx={idx} frame={per_frame_times[-1]:.2f}s elapsed={elapsed:6.1f}s", flush=True)

    writer.close()
    total = time.time() - t_total

    summary = {
        "route": "graphcut_seam",
        "log_id": log_id,
        "n_frames": n_frames,
        "mp4_path": str(mp4_path),
        "wall_time_s": round(total, 2),
        "mean_frame_s": round(float(np.mean(per_frame_times)), 3),
    }
    done_marker.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[gc-video] DONE in {total:.1f}s ({n_frames} frames, mean {summary['mean_frame_s']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
