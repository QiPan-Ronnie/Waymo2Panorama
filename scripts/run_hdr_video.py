"""
新-E HDR cross-cam compensation video driver — L1 sphere ERP + per-cam color
correction (6-param gain+bias via global LS + Huber) per frame -> mp4.

Mirrors run_l1_baseline.py with an HDR correction step inserted between the
per-cam ERP slab rendering and the multiband blending:

  for each frame t:
    1. Render 7 cam to ERP slabs (sphere projection)  [same as L1]
    2. Extract overlap correspondences between slabs
    3. Solve 6-param gain+bias per cam (cam_0 = identity)
    4. Apply correction to each slab
    5. Multiband blend corrected slabs -> ERP
    6. Append to mp4

CPU only. Per-frame cost ~2-3s (L1 1-2s + LS solve ~0.5-1s).
Expected wall time: ~5-7 min for 5sec video (100 frames).

Usage:
    python scripts/run_hdr_video.py \
        --log-dir /content/drive/MyDrive/.../02a00399-... \
        --out-dir /content/drive/MyDrive/.../outputs/hdr_video \
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
    ap.add_argument("--valid-threshold", type=float, default=0.01,
                    help="weight threshold for overlap extraction")
    ap.add_argument("--also-baseline", action="store_true",
                    help="also write a parallel L1 baseline mp4 for side-by-side")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / "../code").resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.color.hdr_gain_estimate import (
        extract_overlap_pixels, global_color_correction, apply_correction,
    )

    log_id = args.log_dir.name
    out_dir = args.out_dir / log_id
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / "hdr_video.mp4"
    baseline_mp4 = out_dir / "baseline_video.mp4"
    done_marker = out_dir / "done.json"

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    start_idx = max(0, int(args.start_sec * args.fps))
    stop_idx = min(len(ts_all), start_idx + int(args.duration_sec * args.fps))
    indices = list(range(start_idx, stop_idx, args.frame_step))
    n_frames = len(indices)
    print(f"[hdr-video] Rendering {n_frames} frames ({args.start_sec:.1f}s -> {args.start_sec + args.duration_sec:.1f}s)")
    print(f"[hdr-video] log_id={log_id}, anchor frames in log: {loader.num_anchor_frames()}")

    writer = imageio.get_writer(mp4_path, fps=args.fps, codec="libx264", quality=8)
    writer_base = imageio.get_writer(baseline_mp4, fps=args.fps, codec="libx264", quality=8) if args.also_baseline else None
    erp_hw = (args.erp_height, args.erp_width)
    cams = list(RING_CAMS_7)

    t_total = time.time()
    per_frame_times: list[dict] = []

    for i, idx in enumerate(indices):
        anchor = ts_all[idx]
        frame = loader.load_synced_frame(anchor)

        t0 = time.time()
        # 1. Render slabs
        slabs: list[np.ndarray] = []
        weights: list[np.ndarray] = []
        for cam in cams:
            img = frame.images[cam]
            calib = frame.calibrations[cam]
            rgb, _alpha, w = render_camera_to_erp(
                image=img, K=calib.K, T_ego_cam=calib.T_ego_cam, erp_hw=erp_hw,
            )
            slabs.append(rgb)
            weights.append(w)
        t_render = time.time() - t0

        # 2. Extract overlap correspondences
        t1 = time.time()
        overlaps = extract_overlap_pixels(slabs, weights, valid_threshold=args.valid_threshold)
        t_extract = time.time() - t1

        # 3. Solve correction
        t2 = time.time()
        corrections = global_color_correction(overlaps, num_cams=len(cams))
        t_solve = time.time() - t2

        # 4. Apply correction to each slab
        slabs_corrected = [apply_correction(slab, corrections[c_i]) for c_i, slab in enumerate(slabs)]

        # 5. Multiband blend (corrected)
        t3 = time.time()
        erp_after = multiband_blend(slabs_corrected, weights, num_bands=args.num_bands, wrap=True)
        t_blend = time.time() - t3

        writer.append_data(erp_after)

        if writer_base is not None:
            erp_before = multiband_blend(slabs, weights, num_bands=args.num_bands, wrap=True)
            writer_base.append_data(erp_before)

        frame_total = time.time() - t0
        per_frame_times.append({
            "render": t_render, "extract": t_extract,
            "solve": t_solve, "blend": t_blend, "total": frame_total,
        })

        if (i + 1) % 10 == 0 or i == 0 or i == n_frames - 1:
            elapsed = time.time() - t_total
            rate = (i + 1) / max(elapsed, 1e-9)
            print(f"  [{i + 1:4d}/{n_frames:4d}] idx={idx} ts={anchor} "
                  f"render={t_render:.2f}s extract={t_extract:.2f}s solve={t_solve:.2f}s blend={t_blend:.2f}s "
                  f"total={frame_total:.2f}s elapsed={elapsed:6.1f}s rate={rate:.2f}fps")

    writer.close()
    if writer_base is not None:
        writer_base.close()
    total = time.time() - t_total

    mean_solve = float(np.mean([t["solve"] for t in per_frame_times]))
    mean_frame = float(np.mean([t["total"] for t in per_frame_times]))
    summary = {
        "route": "hdr_compensation",
        "log_id": log_id,
        "n_frames": n_frames,
        "mp4_path": str(mp4_path),
        "baseline_mp4_path": str(baseline_mp4) if writer_base is not None else None,
        "wall_time_s": round(total, 2),
        "mean_frame_s": round(mean_frame, 3),
        "mean_solve_s": round(mean_solve, 3),
        "erp_hw": list(erp_hw),
        "num_bands": args.num_bands,
        "args": {
            "start_sec": args.start_sec,
            "duration_sec": args.duration_sec,
            "fps": args.fps,
        },
    }
    done_marker.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[hdr-video] DONE in {total:.1f}s ({n_frames} frames, mean frame {summary['mean_frame_s']}s, mean solve {summary['mean_solve_s']}s)")
    print(f"[hdr-video] mp4: {mp4_path}")
    if writer_base is not None:
        print(f"[hdr-video] baseline mp4: {baseline_mp4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
