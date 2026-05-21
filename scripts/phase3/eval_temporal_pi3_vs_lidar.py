"""
T12 — Evaluate temporal-Pi3 center frame against AV2 LiDAR.

Reuses the EXACT projection + metric code path from
`scripts/phase2/eval_pi3_vs_lidar.py` and the depth-binning code from
`scripts/phase3/eval_pi3_lidar_binned.py` so results are directly comparable
to Phase 3 W1 single-frame numbers (anchor 90: abs_rel=0.186, δ<1.25=0.725).

The temporal run writes per-frame dirs under <output_dir>; we just pass the
CENTER frame dir (e.g. `anchor090_K3/frame_001`) as `--pi3-dir` and produce:

    temporal_lidar_metrics.json    overall + per-cam + depth-binned metrics
    summary.txt                     human-readable

We import from sibling scripts via sys.path insert so we don't duplicate code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

DEFAULT_W2P_CODE_REL = "../../code"


def _wire(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))
    # add scripts dirs for sibling-script imports
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent / "phase2"))
    sys.path.insert(0, str(here))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--pi3-dir", required=True,
                    help="Path to <output_dir>/frame_<center_local_idx>/ from run_pi3_temporal_multi_frame")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--conf-threshold", type=float, default=0.3)
    ap.add_argument("--min-dist", type=float, default=0.5)
    ap.add_argument("--max-dist", type=float, default=60.0)
    ap.add_argument("--img-side", type=int, default=504)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire(w2p_code)

    from waymo2panorama.data_io.av2_loader import RING_CAMS_7  # noqa: E402
    from eval_pi3_vs_lidar import (                              # noqa: E402
        find_closest_lidar_sweep, load_lidar_points_ego,
        project_lidar_to_cam, sample_pi3_depth, compute_depth_metrics,
    )
    from eval_pi3_lidar_binned import bin_metrics                # noqa: E402
    from PIL import Image                                         # noqa: E402

    log_dir = Path(args.log_dir)
    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pi3_summary = json.loads((pi3_dir / "summary.json").read_text(encoding="utf-8"))
    anchor_ts = int(pi3_summary["anchor_timestamp_ns"])
    K = pi3_summary.get("window_size_K", None)
    anchor_idx = pi3_summary.get("anchor_idx", None)

    sweep_path, sweep_ts = find_closest_lidar_sweep(log_dir, anchor_ts)
    sweep_dt_ms = abs(sweep_ts - anchor_ts) / 1e6
    print(f"[temporal-eval] anchor_idx={anchor_idx} K={K} anchor_ts={anchor_ts} "
          f"sweep_ts={sweep_ts} Δ={sweep_dt_ms:.1f}ms")

    points_ego = load_lidar_points_ego(sweep_path)

    per_cam: dict[str, dict] = {}
    overall_lidar: list[np.ndarray] = []
    overall_pi3: list[np.ndarray] = []

    for cam in RING_CAMS_7:
        K_lb = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
        T_ego_cam = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        local_pts = np.load(pi3_dir / f"local_points_{cam}.npy")
        conf = np.load(pi3_dir / f"conf_{cam}.npy")
        if conf.ndim == 3 and conf.shape[-1] == 1:
            conf = conf[..., 0]
        local_z = local_pts[..., 2]

        proj = project_lidar_to_cam(
            points_ego, T_ego_cam, K_lb,
            img_side=args.img_side, min_dist=args.min_dist, max_dist=args.max_dist,
        )
        valid = proj["valid_mask"]
        u_v = proj["u"][valid]; v_v = proj["v"][valid]
        lidar_d = proj["z_cam"][valid]

        pi3_d, has_pi3 = sample_pi3_depth(
            local_z, conf, u_v, v_v, conf_threshold=args.conf_threshold,
        )
        lidar_m = lidar_d[has_pi3]; pi3_m = pi3_d[has_pi3]

        m = compute_depth_metrics(lidar_m, pi3_m)
        m["n_lidar_in_cam_fov"] = int(u_v.size)
        m["n_matched_pi3_conf"] = int(lidar_m.size)
        per_cam[cam] = m
        overall_lidar.append(lidar_m)
        overall_pi3.append(pi3_m)
        print(f"[{cam}] in_fov={u_v.size:>6d} matched={lidar_m.size:>6d} "
              f"abs_rel={m['abs_rel']:.3f} rmse={m['rmse']:.2f}m d<1.25={m['delta_1_25']:.3f}")

    all_lidar = np.concatenate(overall_lidar) if overall_lidar else np.empty(0)
    all_pi3 = np.concatenate(overall_pi3) if overall_pi3 else np.empty(0)
    overall = compute_depth_metrics(all_lidar, all_pi3)
    binned = bin_metrics(all_lidar, all_pi3, [0.5, 5, 10, 20, 40, 60])

    out = {
        "config": {
            "log_dir": str(log_dir),
            "pi3_dir": str(pi3_dir),
            "center_anchor_idx": anchor_idx,
            "window_size_K": K,
            "anchor_timestamp_ns": anchor_ts,
            "lidar_sweep_timestamp_ns": sweep_ts,
            "lidar_sweep_delta_ms": sweep_dt_ms,
            "lidar_sweep_file": sweep_path.name,
            "img_side": args.img_side,
            "conf_threshold": args.conf_threshold,
            "min_dist_m": args.min_dist,
            "max_dist_m": args.max_dist,
            "cameras": list(RING_CAMS_7),
        },
        "overall": overall,
        "per_cam": per_cam,
        "binned_overall": binned,
    }
    (out_dir / "temporal_lidar_metrics.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8",
    )

    # human-readable summary
    lines = []
    lines.append(f"Temporal Pi3 (K={K}, center_anchor={anchor_idx}) vs AV2 LiDAR")
    lines.append("-" * 96)
    lines.append(f"{'cam':<22} {'n':>7} {'abs_rel':>9} {'rmse':>7} {'d<1.25':>8} {'d<1.25^2':>10}")
    for cam in RING_CAMS_7:
        m = per_cam[cam]
        lines.append(f"{cam:<22} {m['n']:>7d} {m['abs_rel']:>9.3f} {m['rmse']:>7.2f} "
                     f"{m['delta_1_25']:>8.3f} {m['delta_1_25_2']:>10.3f}")
    lines.append("-" * 96)
    m = overall
    lines.append(f"{'OVERALL':<22} {m['n']:>7d} {m['abs_rel']:>9.3f} {m['rmse']:>7.2f} "
                 f"{m['delta_1_25']:>8.3f} {m['delta_1_25_2']:>10.3f}")
    lines.append("")
    lines.append("Depth-binned (overall):")
    lines.append(f"{'bin':<10}{'n':>8}{'abs_rel':>10}{'rmse':>8}{'d<1.25':>10}{'lidar_μ':>10}{'pi3_μ':>10}{'bias%':>9}")
    for b in binned:
        if b["abs_rel"] is None:
            lines.append(f"{b['bin']:<10}{b['n']:>8}{'--':>10}{'--':>8}{'--':>10}{'--':>10}{'--':>10}{'--':>9}")
        else:
            lines.append(f"{b['bin']:<10}{b['n']:>8}{b['abs_rel']:>10.3f}{b['rmse']:>8.2f}"
                         f"{b['delta_1_25']:>10.3f}{b['lidar_mean']:>10.2f}{b['pi3_mean']:>10.2f}"
                         f"{b['bias_pct']:>+9.2f}")
    summary_text = "\n".join(lines)
    (out_dir / "summary.txt").write_text(summary_text + "\n", encoding="utf-8")
    print()
    print(summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
