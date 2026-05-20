"""
Phase 2 P2.11 — Pi3 vs AV2 LiDAR ground-truth depth comparison.

Anchored by the LiDAR sweep closest to a Pi3 inference frame, this script:

    1. Loads the AV2 LiDAR sweep (.feather, already in egovehicle frame after
       motion compensation) at the timestamp closest to the Pi3 anchor.
    2. For each of the 7 ring cameras:
         a. Transforms LiDAR points from ego frame -> camera frame via
            T_cam_ego = inv(T_ego_cam).
         b. Projects to the letterboxed (504x504) Pi3 input plane using the
            AV2-truth letterboxed K (saved by run_pi3_one_frame as
            av2_K_letterboxed_<cam>.npy).
         c. Filters: z_cam > 0, (u, v) in bounds, dist in [min, max], Pi3 conf
            above threshold.
         d. Samples Pi3 depth = local_points_<cam>.npy[..., 2] at the nearest
            integer projection of each LiDAR point.
         e. Computes per-cam abs_rel_err, RMSE, log_RMSE, delta thresholds
            (1.25, 1.25^2, 1.25^3).
    3. Aggregates an overall (all-cam) metric.
    4. Writes scatter plots + per-cam error visualizations.

We compare DEPTH = z in camera frame (not Euclidean distance), since Pi3's
local_points stores the per-pixel 3D coordinate in cam frame and z = depth.

Why letterboxed-cam-plane and not full-res:
  Pi3 outputs are at 504x504 (the letterboxed input resolution). The Pi3
  per-pixel depth at (u_lb, v_lb) corresponds to the cam ray through that
  letterboxed pixel. To compare apples-to-apples, we project LiDAR via the
  letterboxed K and read Pi3 depth at the same (u_lb, v_lb). No back-projection
  to full-res is needed -- the letterbox transform is just a pad+scale on K.

Outputs (under --output-dir):
    metrics_<cam>.json        per-cam metrics + matched-point count
    metrics_overall.json      all-cam aggregated metrics + run config
    scatter_<cam>.png         Pi3 depth vs LiDAR depth scatter
    overlay_<cam>.png         LiDAR points colored by abs rel err on Pi3 image
    summary.txt               human-readable table for quick eyeballing

The LiDAR is the ground truth (mm-level accuracy). Pi3 is the unit under test.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---- repo wiring ----

DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


# ---- lidar loading ----

def find_closest_lidar_sweep(log_dir: Path, anchor_ts_ns: int) -> tuple[Path, int]:
    """Find the lidar .feather closest in time to anchor_ts_ns. Returns (path, ts)."""
    lidar_dir = log_dir / "sensors" / "lidar"
    if not lidar_dir.exists():
        raise FileNotFoundError(f"missing AV2 lidar dir: {lidar_dir}")
    paths = sorted(lidar_dir.glob("*.feather"))
    if not paths:
        raise FileNotFoundError(f"no .feather lidar sweeps in {lidar_dir}")
    ts = np.array([int(p.stem) for p in paths], dtype=np.int64)
    idx = int(np.argmin(np.abs(ts - anchor_ts_ns)))
    return paths[idx], int(ts[idx])


def load_lidar_points_ego(feather_path: Path) -> np.ndarray:
    """Load AV2 lidar sweep -> (N, 3) float64 points in egovehicle frame.

    AV2 lidar sweeps are stored already motion-compensated to the egovehicle
    frame at the sweep reference timestamp (see AV2 user guide). Columns:
    x, y, z, intensity, laser_number, offset_ns.
    """
    df = pd.read_feather(feather_path)
    cols = ("x", "y", "z")
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"lidar .feather missing columns {missing}, has: {list(df.columns)}")
    p = df[list(cols)].to_numpy(dtype=np.float64)
    return p


# ---- projection ----

def project_lidar_to_cam(
    points_ego: np.ndarray,        # (N, 3)
    T_ego_cam: np.ndarray,         # (4, 4) cam-to-ego
    K_letterboxed: np.ndarray,     # (3, 3) at 504x504 scale
    img_side: int = 504,
    min_dist: float = 0.5,
    max_dist: float = 60.0,
) -> dict[str, np.ndarray]:
    """Project ego-frame lidar to letterboxed cam plane. Returns dict of arrays."""
    # ego -> cam
    T_cam_ego = np.linalg.inv(T_ego_cam)
    p_ego_h = np.concatenate([points_ego, np.ones((points_ego.shape[0], 1))], axis=1)  # (N, 4)
    p_cam = (T_cam_ego @ p_ego_h.T).T[:, :3]  # (N, 3)

    z = p_cam[:, 2]
    in_front = z > min_dist
    not_far = z < max_dist

    # pinhole project
    uv1 = (K_letterboxed @ p_cam.T).T  # (N, 3)
    eps = 1e-9
    u = uv1[:, 0] / (uv1[:, 2] + eps)
    v = uv1[:, 1] / (uv1[:, 2] + eps)

    in_bounds = (u >= 0) & (u <= img_side - 1) & (v >= 0) & (v <= img_side - 1)
    valid = in_front & not_far & in_bounds

    return {
        "valid_mask": valid,
        "u": u,
        "v": v,
        "z_cam": z,                  # lidar GT depth (cam frame z)
        "p_cam": p_cam,
    }


# ---- sampling Pi3 depth ----

def sample_pi3_depth(
    pi3_local_z: np.ndarray,       # (H, W)
    pi3_conf: np.ndarray,           # (H, W) raw logit
    u: np.ndarray,
    v: np.ndarray,
    conf_threshold: float = 0.3,
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-neighbor sample. Returns (pi3_depths, has_pi3_mask)."""
    H, W = pi3_local_z.shape
    ui = np.clip(np.round(u).astype(np.int64), 0, W - 1)
    vi = np.clip(np.round(v).astype(np.int64), 0, H - 1)

    pi3_depth = pi3_local_z[vi, ui]
    conf_logit = pi3_conf[vi, ui]
    conf_prob = 1.0 / (1.0 + np.exp(-conf_logit))

    has_pi3 = (
        np.isfinite(pi3_depth)
        & (pi3_depth > 0.0)
        & (conf_prob > conf_threshold)
    )
    return pi3_depth, has_pi3


# ---- metrics (standard monocular depth eval per Eigen et al.) ----

def compute_depth_metrics(lidar_depth: np.ndarray, pi3_depth: np.ndarray) -> dict[str, float]:
    if lidar_depth.size == 0:
        return {
            "n": 0,
            "abs_rel": float("nan"),
            "sq_rel": float("nan"),
            "rmse": float("nan"),
            "rmse_log": float("nan"),
            "delta_1_25": float("nan"),
            "delta_1_25_2": float("nan"),
            "delta_1_25_3": float("nan"),
            "lidar_depth_mean": float("nan"),
            "pi3_depth_mean": float("nan"),
        }
    eps = 1e-6
    abs_rel = float(np.mean(np.abs(pi3_depth - lidar_depth) / (lidar_depth + eps)))
    sq_rel = float(np.mean((pi3_depth - lidar_depth) ** 2 / (lidar_depth + eps)))
    rmse = float(np.sqrt(np.mean((pi3_depth - lidar_depth) ** 2)))
    rmse_log = float(np.sqrt(np.mean((np.log(np.maximum(pi3_depth, eps)) - np.log(np.maximum(lidar_depth, eps))) ** 2)))
    ratio = np.maximum(pi3_depth / (lidar_depth + eps), lidar_depth / (pi3_depth + eps))
    d1 = float(np.mean(ratio < 1.25))
    d2 = float(np.mean(ratio < 1.25 ** 2))
    d3 = float(np.mean(ratio < 1.25 ** 3))
    return {
        "n": int(lidar_depth.size),
        "abs_rel": abs_rel,
        "sq_rel": sq_rel,
        "rmse": rmse,
        "rmse_log": rmse_log,
        "delta_1_25": d1,
        "delta_1_25_2": d2,
        "delta_1_25_3": d3,
        "lidar_depth_mean": float(np.mean(lidar_depth)),
        "pi3_depth_mean": float(np.mean(pi3_depth)),
    }


# ---- viz ----

def save_scatter(lidar_d: np.ndarray, pi3_d: np.ndarray, cam: str, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(5, 5), dpi=110)
    ax.scatter(lidar_d, pi3_d, s=2, alpha=0.25, c="C0", edgecolors="none")
    lim = max(lidar_d.max() if lidar_d.size else 1.0, pi3_d.max() if pi3_d.size else 1.0)
    lim = float(min(lim, 60.0))
    ax.plot([0, lim], [0, lim], "k--", lw=1, alpha=0.6, label="y=x")
    ax.plot([0, lim], [0, 1.25 * lim], "r:", lw=1, alpha=0.6, label="x1.25")
    ax.plot([0, lim], [0, lim / 1.25], "r:", lw=1, alpha=0.6)
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.set_xlabel("LiDAR depth (m)")
    ax.set_ylabel("Pi3 depth (m)")
    ax.set_title(f"{cam} | n={lidar_d.size}")
    ax.legend(loc="upper left", fontsize=8)
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def save_overlay(
    image_lb: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    lidar_d: np.ndarray,
    pi3_d: np.ndarray,
    cam: str,
    out: Path,
) -> None:
    eps = 1e-6
    abs_rel = np.abs(pi3_d - lidar_d) / (lidar_d + eps)
    fig, ax = plt.subplots(figsize=(6, 6), dpi=110)
    ax.imshow(image_lb)
    sc = ax.scatter(u, v, c=abs_rel, s=4, cmap="turbo", vmin=0, vmax=0.5,
                    alpha=0.85, edgecolors="none")
    cb = fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label("Pi3 abs rel err (cap 0.5)")
    ax.set_title(f"{cam} | matched n={u.size}")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# ---- main ----

@dataclass
class PerCamResult:
    cam: str
    metrics: dict[str, float]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", required=True,
                    help="AV2 sensor log dir (the same one used by run_pi3_one_frame)")
    ap.add_argument("--pi3-dir", required=True,
                    help="Directory containing run_pi3_one_frame outputs (npy + summary.json)")
    ap.add_argument("--output-dir", required=True,
                    help="Where to write metrics + viz")
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--conf-threshold", type=float, default=0.3)
    ap.add_argument("--min-dist", type=float, default=0.5)
    ap.add_argument("--max-dist", type=float, default=60.0)
    ap.add_argument("--img-side", type=int, default=504)
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
    sweep_dt_ms = abs(sweep_ts - anchor_ts) / 1e6
    print(f"[lidar] anchor_ts={anchor_ts} sweep_ts={sweep_ts} delta={sweep_dt_ms:.1f}ms")
    print(f"[lidar] sweep_file={sweep_path.name}")

    points_ego = load_lidar_points_ego(sweep_path)
    print(f"[lidar] loaded {points_ego.shape[0]} ego-frame points")

    per_cam: dict[str, PerCamResult] = {}
    overall_lidar: list[np.ndarray] = []
    overall_pi3: list[np.ndarray] = []

    for cam in RING_CAMS_7:
        K_lb = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
        T_ego_cam = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        local_pts = np.load(pi3_dir / f"local_points_{cam}.npy")  # (H, W, 3)
        conf = np.load(pi3_dir / f"conf_{cam}.npy")
        if conf.ndim == 3 and conf.shape[-1] == 1:
            conf = conf[..., 0]
        local_z = local_pts[..., 2]
        img_lb = np.asarray(Image.open(pi3_dir / f"image_{cam}.png"))

        proj = project_lidar_to_cam(
            points_ego, T_ego_cam, K_lb,
            img_side=args.img_side,
            min_dist=args.min_dist,
            max_dist=args.max_dist,
        )
        valid = proj["valid_mask"]
        u_v = proj["u"][valid]
        v_v = proj["v"][valid]
        lidar_d = proj["z_cam"][valid]

        pi3_d, has_pi3 = sample_pi3_depth(
            local_z, conf, u_v, v_v, conf_threshold=args.conf_threshold,
        )

        u_m = u_v[has_pi3]
        v_m = v_v[has_pi3]
        lidar_m = lidar_d[has_pi3]
        pi3_m = pi3_d[has_pi3]

        metrics = compute_depth_metrics(lidar_m, pi3_m)
        metrics["n_lidar_in_log"] = int(points_ego.shape[0])
        metrics["n_lidar_in_cam_fov"] = int(u_v.size)
        metrics["n_matched_pi3_conf"] = int(u_m.size)
        metrics["pct_matched_of_fov"] = (
            float(u_m.size / u_v.size) if u_v.size else 0.0
        )

        (out_dir / f"metrics_{cam}.json").write_text(
            json.dumps(metrics, indent=2), encoding="utf-8",
        )

        if u_m.size > 0:
            save_scatter(lidar_m, pi3_m, cam, out_dir / f"scatter_{cam}.png")
            save_overlay(img_lb, u_m, v_m, lidar_m, pi3_m, cam,
                         out_dir / f"overlay_{cam}.png")

        per_cam[cam] = PerCamResult(cam=cam, metrics=metrics)
        overall_lidar.append(lidar_m)
        overall_pi3.append(pi3_m)

        print(
            f"[{cam}] in_fov={u_v.size:>6d} matched={u_m.size:>6d} "
            f"abs_rel={metrics['abs_rel']:.3f} rmse={metrics['rmse']:.2f}m "
            f"d1.25={metrics['delta_1_25']:.3f}"
        )

    all_lidar = np.concatenate(overall_lidar) if overall_lidar else np.empty(0)
    all_pi3 = np.concatenate(overall_pi3) if overall_pi3 else np.empty(0)
    overall_metrics = compute_depth_metrics(all_lidar, all_pi3)

    config = {
        "log_dir": str(log_dir),
        "pi3_dir": str(pi3_dir),
        "anchor_timestamp_ns": anchor_ts,
        "lidar_sweep_timestamp_ns": sweep_ts,
        "lidar_sweep_delta_ms": sweep_dt_ms,
        "lidar_sweep_file": sweep_path.name,
        "n_lidar_in_log": int(points_ego.shape[0]),
        "img_side": args.img_side,
        "conf_threshold": args.conf_threshold,
        "min_dist_m": args.min_dist,
        "max_dist_m": args.max_dist,
        "cameras": list(RING_CAMS_7),
    }
    out_overall = {
        "config": config,
        "overall": overall_metrics,
        "per_cam": {c: r.metrics for c, r in per_cam.items()},
    }
    (out_dir / "metrics_overall.json").write_text(
        json.dumps(out_overall, indent=2), encoding="utf-8",
    )

    # human-readable summary
    lines = []
    lines.append("Pi3 vs AV2 LiDAR depth -- per cam (depth = cam-frame z, m)")
    lines.append("-" * 96)
    lines.append(
        f"{'cam':<22} {'n':>7} {'abs_rel':>9} {'rmse':>7} {'rmse_log':>9} "
        f"{'d<1.25':>8} {'d<1.25^2':>10} {'d<1.25^3':>10}"
    )
    for cam in RING_CAMS_7:
        m = per_cam[cam].metrics
        lines.append(
            f"{cam:<22} {m['n']:>7d} {m['abs_rel']:>9.3f} {m['rmse']:>7.2f} "
            f"{m['rmse_log']:>9.3f} {m['delta_1_25']:>8.3f} "
            f"{m['delta_1_25_2']:>10.3f} {m['delta_1_25_3']:>10.3f}"
        )
    lines.append("-" * 96)
    m = overall_metrics
    lines.append(
        f"{'OVERALL':<22} {m['n']:>7d} {m['abs_rel']:>9.3f} {m['rmse']:>7.2f} "
        f"{m['rmse_log']:>9.3f} {m['delta_1_25']:>8.3f} "
        f"{m['delta_1_25_2']:>10.3f} {m['delta_1_25_3']:>10.3f}"
    )
    summary_text = "\n".join(lines)
    (out_dir / "summary.txt").write_text(summary_text + "\n", encoding="utf-8")
    print()
    print(summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
