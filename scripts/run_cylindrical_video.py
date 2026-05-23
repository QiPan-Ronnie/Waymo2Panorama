"""
新-A Cylindrical (L2) video driver — 7-cam -> cylindrical projection -> ERP-like
canvas + multiband blending, per frame, assembled into mp4.

Mirrors run_l1_baseline.py but uses `render_camera_to_cylinder` from
`code/waymo2panorama/projection/cylinder.py` instead of the sphere projection.
Reuses the same multiband Laplacian blending pipeline downstream.

CPU only (no neural network). Expected wall time: ~3-5 min for 5sec video
(100 frames) on Colab CPU runtime.

Usage:
    python scripts/run_cylindrical_video.py \
        --log-dir /content/drive/MyDrive/.../02a00399-... \
        --out-dir /content/drive/MyDrive/.../outputs/cylindrical_video \
        --start-sec 3.0 --duration-sec 5.0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
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
    from waymo2panorama.projection.cylinder import render_camera_to_cylinder
    from waymo2panorama.blending.multiband import multiband_blend

    # ---- output ----
    log_id = args.log_dir.name
    out_dir = args.out_dir / log_id
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / "cylindrical_video.mp4"
    done_marker = out_dir / "done.json"

    # ---- frame range ----
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    start_idx = max(0, int(args.start_sec * args.fps))
    stop_idx = min(len(ts_all), start_idx + int(args.duration_sec * args.fps))
    indices = list(range(start_idx, stop_idx, args.frame_step))
    n_frames = len(indices)
    print(f"[cyl-video] Rendering {n_frames} frames "
          f"({args.start_sec:.1f}s -> {args.start_sec + args.duration_sec:.1f}s)")
    print(f"[cyl-video] log_id={log_id}, anchor frames in log: {loader.num_anchor_frames()}")

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
        weights: list[np.ndarray] = []
        for cam in cams:
            img = frame.images[cam]
            calib = frame.calibrations[cam]
            rgb, _alpha, w = render_camera_to_cylinder(
                image=img,
                K=calib.K,
                T_ego_cam=calib.T_ego_cam,
                erp_hw=erp_hw,
            )
            slabs.append(rgb)
            weights.append(w)

        erp = multiband_blend(slabs, weights, num_bands=args.num_bands, wrap=True)
        per_frame_times.append(time.time() - t0)
        writer.append_data(erp)

        if (i + 1) % 10 == 0 or i == 0 or i == n_frames - 1:
            elapsed = time.time() - t_total
            rate = (i + 1) / max(elapsed, 1e-9)
            print(f"  [{i + 1:4d}/{n_frames:4d}] idx={idx} ts={anchor} "
                  f"frame={per_frame_times[-1]:.2f}s elapsed={elapsed:6.1f}s rate={rate:.2f}fps")

    writer.close()
    total = time.time() - t_total

    summary = {
        "route": "cylindrical_l2",
        "log_id": log_id,
        "n_frames": n_frames,
        "mp4_path": str(mp4_path),
        "wall_time_s": round(total, 2),
        "mean_frame_s": round(float(np.mean(per_frame_times)), 3),
        "erp_hw": list(erp_hw),
        "num_bands": args.num_bands,
        "args": {
            "start_sec": args.start_sec,
            "duration_sec": args.duration_sec,
            "fps": args.fps,
        },
    }
    done_marker.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[cyl-video] DONE in {total:.1f}s ({n_frames} frames, mean {summary['mean_frame_s']}s)")
    print(f"[cyl-video] mp4: {mp4_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
