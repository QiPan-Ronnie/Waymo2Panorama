"""Make a single-page bar chart summarising the 4 Wave-3 NEG findings — the C-pillar
evidence for the paper narrative shift (B-with-C → C-headline-with-B-supplement)."""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "images/wave3_neg_findings_summary.png"


def main() -> None:
    # 4 NEG findings + 1 reference positive (T14b drop-in safe)
    rows = [
        ("T2: OmniStitch vs L1 (anchor 60)", -6.67, "ΔPSNR vs L1, dB", "Only published\nAV-360 baseline also\nloses 7/7 cams"),
        ("T18: Depth Pro vs Pi3 abs_rel ratio", 2.84, "x worse (lower better, 1.0=Pi3)", "Apple SOTA monocular\nfails on AV2 outdoor:\nabs_rel 0.580 vs 0.204"),
        ("T12 v2: temporal Pi3 K=3 far-bias", -23.92, "%-bias >40m (less negative better)", "Multi-baseline\nhypothesis FALSE.\nSingle K=1: -23.7%"),
        ("T17: Panacea+ modality gap", 0.0, "n/a (modality mismatch)", "BEV->video generator,\nnot RGB ERP consumer.\nReal consumer = ViPE"),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 6.8))

    palette = ["#c44e52", "#dd8452", "#55a467", "#8172b3"]

    for ax, (name, val, ylabel, blurb), color in zip(axes.flat, rows, palette):
        ax.bar([0], [val], color=color, edgecolor="black", width=0.45)
        ax.set_xticks([])
        ax.set_ylabel(ylabel, fontsize=9)
        ax.set_title(name, fontsize=10, fontweight="bold")
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.text(0.55, 0.5, blurb, transform=ax.transAxes, fontsize=9,
                ha="left", va="center", bbox=dict(boxstyle="round,pad=0.5", fc="lightyellow", ec="gray"))
        # value annotation
        if val != 0.0:
            ax.text(0, val + (0.5 if val >= 0 else -0.8), f"{val:+.2f}" if abs(val) < 10 else f"{val:+.1f}",
                    ha="center", fontsize=11, fontweight="bold")
        else:
            ax.text(0, 0.5, "structural\nmodality\ngap", ha="center", va="center", fontsize=11, fontweight="bold")
        # neutral baseline lines for T18 (1.0 = Pi3 parity)
        if name.startswith("T18"):
            ax.axhline(1.0, linestyle="--", color="green", linewidth=1.0, label="Pi3 baseline")
            ax.legend(loc="upper right", fontsize=8)
        ax.set_xlim(-0.6, 1.3)
        ax.grid(axis="y", alpha=0.3)

    fig.suptitle(
        "Wave-3 NEG findings: 4 independent failures motivating C-headline (paper narrative shift)",
        fontsize=12,
    )
    fig.tight_layout()
    fig.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
