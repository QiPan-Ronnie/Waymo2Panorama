"""
P3.3 — Depth-binned Pi3 vs LiDAR metrics on existing P2.11 outputs.

P2.11 reported overall metrics across all matched points (n=99,015). That
mean hides depth-dependent behavior — Pi3 underestimated by ~25% overall
but the bias could be range-dependent (cf. p3.3 hypothesis: low-conf far
points are filtered out, biasing the matched-set mean toward closer
distances).

This script re-runs the projection (same code path as eval_pi3_vs_lidar)
but splits the matched points into 5 LiDAR-depth bins and reports per-bin
abs_rel, RMSE, delta-1.25 + sample count.

CPU-only, no Pi3 re-inference. Reuses Pi3 outputs already on Drive.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def find_closest_lidar_sweep(log_dir: Path, anchor_ts_ns: int) -> tuple[Path, int]:
    lidar_dir = log_dir / "sensors" / "lidar"
    paths = sorted(lidar_dir.glob("*.feather"))
    ts = np.array([int(p.stem) for p in paths], dtype=np.int64)
    idx = int(np.argmin(np.abs(ts - anchor_ts_ns)))
    return paths[idx], int(ts[idx])


def load_lidar_points_ego(feather_path: Path) -> np.ndarray:
    df = pd.read_feather(feather_path)
    return df[["x", "y", "z"]].to_numpy(dtype=np.float64)


def project_and_match(
    points_ego, T_ego_cam, K_lb, local_z, conf,
    img_side=504, min_dist=0.5, max_dist=60.0, conf_threshold=0.3,
):
    T_cam_ego = np.linalg.inv(T_ego_cam)
    p_h = np.concatenate([points_ego, np.ones((points_ego.shape[0], 1))], axis=1)
    p_cam = (T_cam_ego @ p_h.T).T[:, :3]
    z = p_cam[:, 2]
    uv1 = (K_lb @ p_cam.T).T
    u = uv1[:, 0] / (uv1[:, 2] + 1e-9)
    v = uv1[:, 1] / (uv1[:, 2] + 1e-9)
    valid = (z > min_dist) & (z < max_dist) & (u >= 0) & (u <= img_side - 1) & (v >= 0) & (v <= img_side - 1)
    u_v = u[valid]
    v_v = v[valid]
    lidar_d = z[valid]
    H, W = local_z.shape
    ui = np.clip(np.round(u_v).astype(np.int64), 0, W - 1)
    vi = np.clip(np.round(v_v).astype(np.int64), 0, H - 1)
    pi3_d = local_z[vi, ui]
    conf_logit = conf[vi, ui]
    conf_prob = 1.0 / (1.0 + np.exp(-conf_logit))
    has = np.isfinite(pi3_d) & (pi3_d > 0) & (conf_prob > conf_threshold)
    return lidar_d[has], pi3_d[has]


def bin_metrics(lidar_d, pi3_d, edges):
    out = []
    eps = 1e-6
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (lidar_d >= lo) & (lidar_d < hi)
        if mask.sum() < 10:
            out.append({"bin": f"[{lo},{hi})", "n": int(mask.sum()),
                        "abs_rel": None, "rmse": None,
                        "delta_1_25": None,
                        "lidar_mean": None, "pi3_mean": None, "bias_pct": None})
            continue
        l = lidar_d[mask]
        p = pi3_d[mask]
        abs_rel = float(np.mean(np.abs(p - l) / (l + eps)))
        rmse = float(np.sqrt(np.mean((p - l) ** 2)))
        ratio = np.maximum(p / (l + eps), l / (p + eps))
        d1 = float(np.mean(ratio < 1.25))
        out.append({
            "bin": f"[{lo},{hi})",
            "n": int(mask.sum()),
            "abs_rel": round(abs_rel, 4),
            "rmse": round(rmse, 3),
            "delta_1_25": round(d1, 4),
            "lidar_mean": round(float(l.mean()), 3),
            "pi3_mean": round(float(p.mean()), 3),
            "bias_pct": round(float((p.mean() - l.mean()) / l.mean() * 100), 2),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--pi3-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--conf-threshold", type=float, default=0.3)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7

    log_dir = Path(args.log_dir)
    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pi3_summary = json.loads((pi3_dir / "summary.json").read_text(encoding="utf-8"))
    anchor_ts = int(pi3_summary["anchor_timestamp_ns"])
    sweep_path, sweep_ts = find_closest_lidar_sweep(log_dir, anchor_ts)
    points_ego = load_lidar_points_ego(sweep_path)
    print(f"[binned] anchor_ts={anchor_ts} sweep={sweep_path.name} pts={points_ego.shape[0]}")

    edges = [0.5, 5, 10, 20, 40, 60]

    all_lidar, all_pi3 = [], []
    per_cam = {}
    for cam in RING_CAMS_7:
        K_lb = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
        T = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        lp = np.load(pi3_dir / f"local_points_{cam}.npy")
        cf = np.load(pi3_dir / f"conf_{cam}.npy")
        if cf.ndim == 3 and cf.shape[-1] == 1:
            cf = cf[..., 0]
        lz = lp[..., 2]
        l, p = project_and_match(points_ego, T, K_lb, lz, cf,
                                  conf_threshold=args.conf_threshold)
        per_cam[cam] = bin_metrics(l, p, edges)
        all_lidar.append(l)
        all_pi3.append(p)
        print(f"[{cam}] n={l.size}")

    all_lidar = np.concatenate(all_lidar)
    all_pi3 = np.concatenate(all_pi3)
    overall = bin_metrics(all_lidar, all_pi3, edges)

    # plot: per-bin abs_rel and bias%
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), dpi=120)
    bins = [b["bin"] for b in overall]
    abs_rels = [b["abs_rel"] if b["abs_rel"] is not None else 0 for b in overall]
    biases = [b["bias_pct"] if b["bias_pct"] is not None else 0 for b in overall]
    ns = [b["n"] for b in overall]
    ax = axes[0]
    bars = ax.bar(bins, abs_rels, color="C0")
    for bar, n in zip(bars, ns):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.005,
                f"n={n}", ha="center", fontsize=8)
    ax.set_ylabel("abs_rel")
    ax.set_title("Pi3 abs_rel error vs LiDAR depth bin")
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    colors = ["C2" if b >= 0 else "C3" for b in biases]
    bars = ax.bar(bins, biases, color=colors)
    ax.axhline(0, color="k", lw=0.5)
    ax.set_ylabel("Pi3 - LiDAR mean (%)")
    ax.set_title("Pi3 systematic depth bias by range")
    for bar, n, b in zip(bars, ns, biases):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + (1 if b >= 0 else -3),
                f"{b:+.1f}%", ha="center", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "binned_metrics.png")
    plt.close(fig)

    out = {
        "config": {
            "anchor_ts": anchor_ts,
            "sweep_ts": sweep_ts,
            "bin_edges_m": edges,
            "conf_threshold": args.conf_threshold,
        },
        "overall_binned": overall,
        "per_cam_binned": per_cam,
    }
    (out_dir / "binned_metrics.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("\nOverall per-bin:")
    print(f"{'bin':<10}{'n':>8}{'abs_rel':>10}{'rmse':>8}{'d<1.25':>10}{'lidar_μ':>10}{'pi3_μ':>10}{'bias%':>9}")
    for b in overall:
        if b["abs_rel"] is None:
            print(f"{b['bin']:<10}{b['n']:>8}{'--':>10}{'--':>8}{'--':>10}{'--':>10}{'--':>10}{'--':>9}")
        else:
            print(f"{b['bin']:<10}{b['n']:>8}{b['abs_rel']:>10.3f}{b['rmse']:>8.2f}{b['delta_1_25']:>10.3f}"
                  f"{b['lidar_mean']:>10.2f}{b['pi3_mean']:>10.2f}{b['bias_pct']:>+9.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
