"""Generate a small figure visualizing T14b 10-anchor IPM hybrid honest numbers
vs the 3-anchor cherry-picked subset. Saves into deliverables/images/.

Numbers come from notes/ipm_hybrid_report + Wave-3 progress block:
  - 3-anchor (60, 0, 150) mean ground-only ΔPSNR = +0.20 ± 0.11 dB
  - 10-anchor mean ground-only ΔPSNR = +0.048 ± 0.181 dB (7/10 positive)
  - 10-anchor mean full ΔPSNR = -0.010 ± 0.082 dB (drop-in safe)
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
AGG3 = HERE.parent / "outputs/phase3/p3.2_ipm_hybrid/agg_3anchors.json"
OUT = HERE / "images/ipm_hybrid_10anchor_honest.png"


def main() -> None:
    # Per-anchor 3-anchor data (locally available)
    agg = json.loads(AGG3.read_text(encoding="utf-8"))["agg"]
    anchors_3 = ["anchor_000", "anchor_060", "anchor_150"]
    d_full_3 = [agg[a]["mean_cycle"]["PSNR_delta_full"] for a in anchors_3]
    d_grnd_3 = [agg[a]["mean_cycle"]["PSNR_delta_groundOnly"] for a in anchors_3]
    labels_3 = ["0", "60", "150"]

    # 10-anchor honest aggregates (from progress block; per-anchor data on Drive)
    mean_full_10 = -0.010
    sd_full_10 = 0.082
    mean_grnd_10 = 0.048
    sd_grnd_10 = 0.181

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))

    # --- Left panel: ground-only ΔPSNR per anchor (3 local) + 10-anchor mean band ---
    ax = axes[0]
    x = np.arange(len(anchors_3))
    ax.bar(x, d_grnd_3, color=["#4c72b0", "#dd8452", "#55a467"], edgecolor="black", width=0.6)
    for xi, v in zip(x, d_grnd_3):
        ax.text(xi, v + 0.015 if v >= 0 else v - 0.04, f"+{v:.2f}" if v >= 0 else f"{v:.2f}",
                ha="center", fontsize=10, fontweight="bold")
    ax.axhline(0, color="gray", linewidth=0.8)
    # Overlay 10-anchor honest band
    ax.axhspan(mean_grnd_10 - sd_grnd_10, mean_grnd_10 + sd_grnd_10,
               alpha=0.18, color="red", label=f"10-anchor honest mean +/- 1 sd\n  = {mean_grnd_10:+.3f} +/- {sd_grnd_10:.3f}")
    ax.axhline(mean_grnd_10, linestyle="--", color="red", linewidth=1.4)
    # Overlay 3-anchor honest mean
    ax.axhline(np.mean(d_grnd_3), linestyle=":", color="black", linewidth=1.2,
               label=f"3-anchor cherry-picked mean = {np.mean(d_grnd_3):+.3f}")
    ax.set_xticks(x)
    ax.set_xticklabels(labels_3)
    ax.set_xlabel("Anchor index (3 cherry-picked anchors: top-3 parallax per T6)")
    ax.set_ylabel("Ground-only ΔPSNR  (dB, Hybrid - L1)")
    ax.set_title("Ground-only ΔPSNR: 3 cherry-picked vs 10-anchor honest")
    ax.legend(loc="lower right", fontsize=8)
    ax.set_ylim(-0.2, 0.55)
    ax.grid(axis="y", alpha=0.3)

    # --- Right panel: full-image ΔPSNR per anchor + 10-anchor honest mean band ---
    ax = axes[1]
    ax.bar(x, d_full_3, color=["#4c72b0", "#dd8452", "#55a467"], edgecolor="black", width=0.6)
    for xi, v in zip(x, d_full_3):
        ax.text(xi, v + 0.012 if v >= 0 else v - 0.025, f"+{v:.2f}" if v >= 0 else f"{v:.2f}",
                ha="center", fontsize=10, fontweight="bold")
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.axhspan(mean_full_10 - sd_full_10, mean_full_10 + sd_full_10,
               alpha=0.18, color="red", label=f"10-anchor honest mean +/- 1 sd\n  = {mean_full_10:+.3f} +/- {sd_full_10:.3f}")
    ax.axhline(mean_full_10, linestyle="--", color="red", linewidth=1.4)
    ax.set_xticks(x)
    ax.set_xticklabels(labels_3)
    ax.set_xlabel("Anchor index")
    ax.set_ylabel("Full-image ΔPSNR  (dB, Hybrid - L1)")
    ax.set_title("Full-image ΔPSNR: drop-in safe across both samples")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_ylim(-0.15, 0.3)
    ax.grid(axis="y", alpha=0.3)

    fig.suptitle(
        "T14b IPM Hybrid (10-anchor honest): parallax-conditional method contribution + drop-in-safe full image",
        fontsize=12,
    )
    fig.tight_layout()
    fig.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
