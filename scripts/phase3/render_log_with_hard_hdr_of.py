"""
Render all anchors of one AV2 log using the hard_hdr_of pipeline.

The full L1+L2+L3 basic-CV pipeline:
  L1 hard_select  → no doubled-feature ghost
  L2 joint HDR    → no cross-cam exposure step
  L3 OF chain     → reduced parallax seam misalignment

Cost: ~50s/anchor at 2048x4096 on a T4. For a 319-anchor log = ~4.5 hr.

Usage:
    python scripts/phase3/render_log_with_hard_hdr_of.py \
        --log-dir /content/drive/.../02a00399-... \
        --output-dir /content/drive/.../full_pipeline_v1/02a00399 \
        --erp-h 2048 --erp-w 4096 --stride 1 \
        [--anchors 0,1,2 to limit] [--no-of for faster]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--stride", type=int, default=1, help="anchor stride")
    ap.add_argument("--anchors", type=str, default="",
                    help="comma-list of specific anchor indices to render (overrides stride)")
    ap.add_argument("--no-of", action="store_true",
                    help="skip L3 OF (use hard_hdr instead of hard_hdr_of)")
    ap.add_argument("--blend-mode", type=str, default=None,
                    choices=["multiband", "hard_hdr", "hard_hdr_of"],
                    help="explicit blend mode (overrides --no-of)")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent / "code")
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(args.w2p_code))
    from waymo2panorama.data_io.av2_loader import AV2RingLoader
    from waymo2panorama.pipeline.stitch_frame import stitch_one_frame

    if args.blend_mode is not None:
        blend_mode = args.blend_mode
    else:
        blend_mode = "hard_hdr" if args.no_of else "hard_hdr_of"
    print(f"blend_mode={blend_mode}")

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    if args.anchors:
        indices = [int(s) for s in args.anchors.split(",") if s.strip()]
    else:
        indices = list(range(0, len(ts_all), args.stride))
    print(f"will render {len(indices)} anchors (total available: {len(ts_all)})")

    erp_hw = (args.erp_h, args.erp_w)
    log_id_short = args.log_dir.name.split("-")[0]
    per_anchor_log: dict[int, dict] = {}

    t_total_0 = time.time()
    for i, idx in enumerate(indices):
        out_png = args.output_dir / f"anchor_{idx:04d}.png"
        if out_png.exists():
            print(f"[{i+1}/{len(indices)}] anchor {idx}: exists, skip")
            continue
        t0 = time.time()
        try:
            frame = loader.load_synced_frame(ts_all[idx])
            erp = stitch_one_frame(
                frame=frame, erp_hw=erp_hw, blend_mode=blend_mode,
            )
            Image.fromarray(erp).save(out_png)
            dt = time.time() - t0
            per_anchor_log[idx] = {"runtime_s": round(dt, 1), "status": "ok"}
            print(f"[{i+1}/{len(indices)}] anchor {idx}: ok ({dt:.1f}s) -> {out_png.name}")
        except Exception as e:
            per_anchor_log[idx] = {"runtime_s": time.time() - t0, "status": "fail", "error": str(e)}
            print(f"[{i+1}/{len(indices)}] anchor {idx}: FAIL {e}")

    t_total = time.time() - t_total_0
    summary_path = args.output_dir / "render_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "log_id_short": log_id_short,
            "blend_mode": blend_mode,
            "erp_hw": list(erp_hw),
            "n_rendered": len(per_anchor_log),
            "n_total_available": len(ts_all),
            "total_runtime_s": round(t_total, 1),
            "mean_per_anchor_s": round(t_total / max(len(per_anchor_log), 1), 1),
            "per_anchor": per_anchor_log,
        }, f, indent=2)
    print(f"\nDONE: {len(per_anchor_log)} anchors in {t_total/60:.1f} min "
          f"({t_total/max(len(per_anchor_log),1):.1f}s avg)")
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
