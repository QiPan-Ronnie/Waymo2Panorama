"""
P3.X — Plot multi-anchor trends from P3.1b aggregates.

Two 4-panel figures (saved to <output_dir>):
  - lidar_trends.png:  per-anchor abs_rel, RMSE, d<1.25, n matched
  - cycle_trends.png:  per-anchor L1 vs L3 PSNR + dPSNR + Pi3 mean depth vs LiDAR mean depth

CPU-only post-process. Reads the two batch aggregates and draws.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lidar-aggregate", required=True)
    ap.add_argument("--cycle-aggregate", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    lidar = json.loads(Path(args.lidar_aggregate).read_text())
    cycle = json.loads(Path(args.cycle_aggregate).read_text())

    # ----- lidar trends -----
    pa = lidar["per_anchor"]
    idxs = sorted(int(k) for k in pa.keys())
    abs_rel = [pa[str(i)]["overall"]["abs_rel"] for i in idxs]
    rmse = [pa[str(i)]["overall"]["rmse"] for i in idxs]
    d1 = [pa[str(i)]["overall"]["delta_1_25"] for i in idxs]
    d2 = [pa[str(i)]["overall"]["delta_1_25_2"] for i in idxs]
    n = [pa[str(i)]["overall"]["n"] for i in idxs]
    lidar_mu = [pa[str(i)]["overall"]["lidar_depth_mean"] for i in idxs]
    pi3_mu = [pa[str(i)]["overall"]["pi3_depth_mean"] for i in idxs]

    fig, axes = plt.subplots(2, 2, figsize=(11, 7), dpi=120)
    ax = axes[0][0]
    ax.plot(idxs, abs_rel, "o-", color="C0", label="abs_rel")
    ax.axhline(np.mean(abs_rel), ls="--", color="C0", alpha=0.4, label=f"mean={np.mean(abs_rel):.3f}")
    ax.fill_between(idxs, np.mean(abs_rel) - np.std(abs_rel), np.mean(abs_rel) + np.std(abs_rel),
                     color="C0", alpha=0.12)
    ax.set_xlabel("anchor idx")
    ax.set_ylabel("abs_rel")
    ax.set_title("Pi3 vs LiDAR abs_rel per anchor")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax = axes[0][1]
    ax.plot(idxs, rmse, "s-", color="C1", label="RMSE (m)")
    ax.axhline(np.mean(rmse), ls="--", color="C1", alpha=0.4, label=f"mean={np.mean(rmse):.2f}m")
    ax.fill_between(idxs, np.mean(rmse) - np.std(rmse), np.mean(rmse) + np.std(rmse),
                     color="C1", alpha=0.12)
    ax.set_xlabel("anchor idx")
    ax.set_ylabel("RMSE (m)")
    ax.set_title("Pi3 vs LiDAR RMSE per anchor")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax = axes[1][0]
    ax.plot(idxs, d1, "^-", color="C2", label="δ<1.25")
    ax.plot(idxs, d2, "v-", color="C3", label="δ<1.25²")
    ax.axhline(0.7, ls=":", color="gray", alpha=0.5)
    ax.set_xlabel("anchor idx")
    ax.set_ylabel("δ threshold accuracy")
    ax.set_title("Depth threshold accuracy per anchor")
    ax.set_ylim(0, 1.0)
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax = axes[1][1]
    ax.plot(idxs, lidar_mu, "o-", color="C4", label="LiDAR mean depth")
    ax.plot(idxs, pi3_mu, "s-", color="C5", label="Pi3 mean depth")
    for i, (l, p) in enumerate(zip(lidar_mu, pi3_mu)):
        ax.plot([idxs[i], idxs[i]], [p, l], color="gray", alpha=0.3, lw=0.5)
    ax.set_xlabel("anchor idx")
    ax.set_ylabel("mean depth (m)")
    ax.set_title("Pi3 systematic underestimation per anchor")
    ax.grid(True, alpha=0.3)
    ax.legend()

    fig.suptitle("P3.1b LiDAR eval — per-anchor trends (10 anchors, AV2 log 02a00399)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_dir / "lidar_trends.png")
    plt.close(fig)
    print(f"wrote {out_dir / 'lidar_trends.png'}")

    # ----- cycle trends -----
    pac = cycle["per_anchor"]
    cidxs = sorted(int(k) for k in pac.keys() if "mean" in pac[k])
    l1p = [pac[str(i)]["mean"]["PSNR_L1"] for i in cidxs]
    l3p = [pac[str(i)]["mean"]["PSNR_L3"] for i in cidxs]
    dp = [pac[str(i)]["mean"]["PSNR_delta_L3_minus_L1"] for i in cidxs]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4), dpi=120)
    ax = axes[0]
    ax.plot(cidxs, l1p, "o-", color="C0", label="L1 (sphere projection)")
    ax.plot(cidxs, l3p, "s-", color="C3", label="L3 (Pi3 forward splat)")
    ax.fill_between(cidxs, l1p, l3p, where=[a > b for a, b in zip(l1p, l3p)],
                     color="red", alpha=0.1, label="L1 wins region")
    ax.set_xlabel("anchor idx")
    ax.set_ylabel("PSNR (dB)")
    ax.set_title("Cycle-consistency PSNR per anchor")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax = axes[1]
    bars = ax.bar(cidxs, dp, color=["C2" if d >= 0 else "C3" for d in dp], width=20)
    ax.axhline(0, color="k", lw=0.5)
    ax.axhline(np.mean(dp), ls="--", color="C3", alpha=0.5,
                label=f"mean={np.mean(dp):.2f}±{np.std(dp):.2f}dB")
    for i, d in zip(cidxs, dp):
        ax.text(i, d - 0.15, f"{d:.2f}", ha="center", fontsize=7)
    ax.set_xlabel("anchor idx")
    ax.set_ylabel("ΔPSNR (L3 − L1) dB")
    ax.set_title("L3 forward-splat vs L1 baseline — gap per anchor")
    ax.grid(True, alpha=0.3)
    ax.legend()

    fig.suptitle("P3.1b cycle-consistency — per-anchor trends (10 anchors)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_dir / "cycle_trends.png")
    plt.close(fig)
    print(f"wrote {out_dir / 'cycle_trends.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
