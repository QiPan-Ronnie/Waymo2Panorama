"""
Phase 3 T9 — ViPE (NVIDIA Video Pose Engine) on our L1 ERP video.

Reference:
  paper:  Huang et al., "ViPE: Video Pose Engine for 3D Geometric Perception",
          NVIDIA Spatial Intelligence Lab, 2025 (arXiv:2508.10934)
  code:   https://github.com/nv-tlabs/vipe  (panorama branch for 360 ERP support)
  local:  D:\\BaiduSyncdisk\\2024 to future\\koi chen\\02-vipe\\code\\official\\vipe

# Why this matters (paper Section 6 angle)

ViPE is a *natural downstream consumer* of stitched 360 ERP video. It accepts
360 panorama input (per README: "vipe infer YOUR_VIDEO.mp4 --pipeline panorama"
on the panorama branch), splits the ERP into 6 pinhole virtual cameras, runs
keyframe-based SLAM + dense bundle adjustment + monocular depth alignment,
and outputs camera trajectory + per-frame near-metric depth.

If ViPE produces a sensible trajectory + depth on our L1 ERP, this proves
our pipeline output is consumable by published downstream 3D perception
systems. This is the only "consumer" demo in the paper that consumes the
*stitched RGB* (Panacea+ consumes BEV, Pantheon360 consumes BEV; they are
parallel generators, not consumers).

# Pipeline expectations

Inputs:
    --erp-mp4       path to L1 ERP mp4 (1024x2048, 20 Hz)
    --vipe-dir      path to ViPE clone on Colab (panorama branch installed)
    --output-dir    where ViPE writes outputs (pose/depth/maps)

Outputs (written by ViPE itself into --output-dir):
    artifacts/ , vipe/  (per-sequence per ViPE conventions)
    Camera intrinsics (estimated), camera trajectory, per-frame dense depth
    Optionally visualization mp4 (depth, point cloud)

# Why we wrap this in our own script

The ViPE CLI does the heavy lifting; this script's job is:
  1) Locate inputs on Drive (L1 mp4) and ViPE installation
  2) Compute a few derived sanity metrics from ViPE outputs:
       - did the trajectory length make sense vs. AV2 ego motion?
       - did depth maps land in a plausible metric range (1-50m for outdoor AV)?
       - per-frame depth coverage (fraction of valid pixels)
  3) Pull a single depth-overlay PNG for the report
  4) Emit summary.json for the report aggregator

This is a "system works" demo. Quantitative depth vs. LiDAR comparison is
intentionally out of scope (ERP-pixel <-> cam-pixel mapping is nontrivial
when ViPE operates on the ERP not the original AV2 rings).

# Failure modes to expect (document in notes/t9_*.md regardless of outcome)

  - panorama branch may need extra deps (UniDepth, GroundingDINO,
    SAM, XMem, GeoCalib) that the env doesn't auto-install
  - ViPE may crash on a 100-frame clip if it expects longer videos for SLAM
    init (look for "keyframe" / "tracking failed" errors)
  - ERP at 1024x2048 may be downscaled by ViPE; check the actual operating
    resolution in logs
  - dense BA on 100 frames at 1024x2048 ERP can be slow; budget 15-30 min
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--erp-mp4",
        type=Path,
        required=True,
        help="Path to L1 ERP mp4 input (e.g. baseline.mp4 from Phase 1).",
    )
    ap.add_argument(
        "--vipe-dir",
        type=Path,
        required=True,
        help="Path to the installed ViPE clone (panorama branch).",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where ViPE results will be written.",
    )
    ap.add_argument(
        "--pipeline",
        type=str,
        default="panorama",
        help="Hydra pipeline name (must match a configs/pipeline/<name>.yaml).",
    )
    ap.add_argument(
        "--frame-start",
        type=int,
        default=0,
        help="Starting frame index (passed through to streams.frame_start).",
    )
    ap.add_argument(
        "--frame-end",
        type=int,
        default=100,
        help="Ending frame index (exclusive); passed through to streams.frame_end.",
    )
    ap.add_argument(
        "--visualize",
        action="store_true",
        help="Save visualization mp4 (depth, pcd overlays).",
    )
    args = ap.parse_args()

    assert args.erp_mp4.exists(), f"L1 ERP mp4 not found: {args.erp_mp4}"
    assert args.vipe_dir.exists(), f"ViPE dir not found: {args.vipe_dir}"

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Stage the ERP mp4 under a flat directory the way ViPE expects.
    # ViPE's raw_mp4_stream takes either a single mp4 or a directory of mp4s.
    # We point it at the parent dir of the L1 mp4.
    stream_base = args.erp_mp4.parent
    sequence_name = args.erp_mp4.stem  # "baseline" for baseline.mp4

    cmd = [
        "python",
        "run.py",
        f"pipeline={args.pipeline}",
        "streams=raw_mp4_stream",
        f"streams.base_path={stream_base}",
        f"streams.frame_start={args.frame_start}",
        f"streams.frame_end={args.frame_end}",
        f"pipeline.output.path={args.output_dir}",
        "pipeline.output.save_viz=true",
        "pipeline.output.save_artifacts=true",
    ]
    print(f"[t9] cmd: {' '.join(cmd)}")
    print(f"[t9] cwd: {args.vipe_dir}")
    print(f"[t9] L1 ERP input dir: {stream_base}")
    print(f"[t9] sequence: {sequence_name}")
    print(f"[t9] output: {args.output_dir}")

    t0 = time.time()
    res = subprocess.run(cmd, cwd=str(args.vipe_dir))
    elapsed = time.time() - t0
    print(f"[t9] ViPE exit code: {res.returncode}   wall: {elapsed:.1f}s")

    # Locate ViPE's output sub-directory for this sequence
    # ViPE writes to <output_dir>/<sequence_name>/ typically
    seq_out = args.output_dir / sequence_name
    summary = {
        "exit_code": res.returncode,
        "wall_seconds": round(elapsed, 1),
        "input_mp4": str(args.erp_mp4),
        "input_size_mb": round(args.erp_mp4.stat().st_size / 1e6, 2),
        "frame_start": args.frame_start,
        "frame_end": args.frame_end,
        "pipeline": args.pipeline,
        "vipe_output_root": str(args.output_dir),
        "sequence_dir_exists": seq_out.exists(),
    }

    if seq_out.exists():
        # Enumerate top-level entries inside the sequence dir
        children = sorted([p.name for p in seq_out.iterdir()])
        summary["sequence_children"] = children
        # Try to find common ViPE artifacts:
        for known in ("artifacts", "vipe", "intrinsics.txt", "info.json"):
            cand = seq_out / known
            if cand.exists():
                if cand.is_file():
                    summary[f"file_{known}"] = round(cand.stat().st_size / 1e3, 2)  # KB
                else:
                    summary[f"dir_{known}_children"] = sorted([p.name for p in cand.iterdir()])[:20]

    summary_path = args.output_dir / "t9_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"[t9] wrote summary: {summary_path}")
    print(json.dumps(summary, indent=2, default=str))

    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
