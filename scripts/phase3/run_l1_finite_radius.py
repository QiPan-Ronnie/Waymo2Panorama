"""
N1 Phase A — Cam-translation-aware finite-radius L1 r-sweep.

Modifies the L1 sphere baseline by enabling translation-aware projection via
the `convergence_distance_m` parameter on render_camera_to_erp. Sweeps a list
of r values (default {inf, 3, 5, 7, 10, 15, 30}) and writes one ERP per value.

The visual test target is log 02a00399 anchor 0 (Porsche + BMW frame) where
the 2-wheel ghost is documented (Stage 3 v5 ghost-truth audit). Goal: identify
the convergence distance r that visibly collapses the doubled wheels.

Pipeline:
    1. Load 7 ring cams (raw AV2, full resolution) for the chosen anchor.
    2. For each r in r-values:
         - Run stitch_one_frame(frame, convergence_distance_m=r).
         - Save l1_<label>.png.
    3. Write summary.json (params, per-r runtime, paths).

Usage (Colab):
    python scripts/phase3/run_l1_finite_radius.py \\
        --log-dir /content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/02a00399-3857-444e-8db3-a8f58489c394 \\
        --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/n1_phase_a/02a00399/anchor_0 \\
        --anchor-index 0

Notes:
    - Pure CPU operation; no GPU needed (~5 min wall on Colab free CPU for 7 r values).
    - convergence_distance_m=None (CLI token 'inf') degenerates to the legacy L1
      baseline; use this as the A/B reference column.
    - Per the 2026-05-26 plan (agent/plans/2026-05-26-N1-cam-translation-aware-L1-plan.md),
      the visual gate criterion is: r=5m visibly halves Porsche-wheel ghost width
      vs the 'inf' baseline. PASS → Day 2 LiDAR per-pixel. FAIL → STOP, audit
      time-sync / motion-blur / intrinsic errors.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def parse_r_values(spec: str) -> list[tuple[str, float | None]]:
    """Parse comma-separated r spec into list of (label, value) tuples.

    'inf' / 'infinity' / 'none' / 'plain' -> label='inf', value=None (legacy L1).
    Otherwise positive float -> label like 'r5m' or 'r5p5m', value=float.
    """
    out: list[tuple[str, float | None]] = []
    for token in spec.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token in ("none", "inf", "infinity", "plain"):
            out.append(("inf", None))
        else:
            r = float(token)
            if r <= 0:
                raise ValueError(f"r must be positive, got {r}")
            if r == int(r):
                label = f"r{int(r)}m"
            else:
                label = f"r{r:g}m".replace(".", "p")
            out.append((label, r))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="N1 Phase A — finite-radius L1 r-sweep")
    ap.add_argument("--log-dir", type=Path, required=True,
                    help="AV2 sensor log root (contains calibration/ + sensors/cameras/)")
    ap.add_argument("--output-dir", type=Path, required=True,
                    help="Output dir for ERP PNGs and summary.json")
    ap.add_argument("--anchor-index", type=int, default=0,
                    help="Anchor index in the log (default 0 = first front_center frame)")
    ap.add_argument("--r-values", default="inf,3,5,7,10,15,30",
                    help="Comma-separated r values. 'inf' = legacy L1 (None). "
                         "Default: inf,3,5,7,10,15,30")
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5,
                    help="multiband Laplacian band count (default 5)")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent / DEFAULT_W2P_CODE_REL,
                    help="Path to waymo2panorama code dir "
                         "(auto-detected from script location)")
    args = ap.parse_args()

    _wire_imports(args.w2p_code)
    from waymo2panorama.data_io.av2_loader import AV2RingLoader
    from waymo2panorama.pipeline.stitch_frame import stitch_one_frame

    args.output_dir.mkdir(parents=True, exist_ok=True)

    r_specs = parse_r_values(args.r_values)
    print(f"[N1] r specs: {r_specs}", flush=True)

    print(f"[N1] loading log: {args.log_dir}", flush=True)
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    if not 0 <= args.anchor_index < len(ts_all):
        raise IndexError(
            f"anchor_index {args.anchor_index} out of range (n_anchors={len(ts_all)})"
        )

    anchor_ts = ts_all[args.anchor_index]
    print(f"[N1] anchor {args.anchor_index} ts_ns={anchor_ts}", flush=True)
    t_load0 = time.time()
    frame = loader.load_synced_frame(anchor_ts)
    t_load_s = time.time() - t_load0
    print(f"[N1] frame loaded in {t_load_s:.1f}s", flush=True)

    erp_hw = (args.erp_h, args.erp_w)
    summary: dict = {
        "log_dir": str(args.log_dir),
        "anchor_index": args.anchor_index,
        "anchor_ts_ns": anchor_ts,
        "erp_hw": list(erp_hw),
        "num_bands": args.num_bands,
        "r_spec": args.r_values,
        "load_time_s": round(t_load_s, 2),
        "results": [],
    }

    for label, r in r_specs:
        out_path = args.output_dir / f"l1_{label}.png"
        t0 = time.time()
        erp = stitch_one_frame(
            frame=frame,
            erp_hw=erp_hw,
            num_bands=args.num_bands,
            convergence_distance_m=r,
        )
        wall_s = time.time() - t0
        Image.fromarray(erp).save(out_path)
        print(f"[N1]   {label} (r={r}): {wall_s:.1f}s -> {out_path.name}", flush=True)
        summary["results"].append({
            "label": label,
            "r": r,
            "wall_s": round(wall_s, 2),
            "out_file": str(out_path.name),
        })

    summary_path = args.output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"[N1] summary -> {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
