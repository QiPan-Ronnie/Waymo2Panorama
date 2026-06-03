"""Codex round-3 decisive validation (local CPU, synthetic-on-real-texture):
the seam ghost = AVERAGING two copies offset by residual disparity d. Take a real sharp texture T,
make a second copy B = shift(T, d) (the misaligned reprojection), and compare the two compositors:
  avg(d)  = 0.5*(T + B)      <- what view-interp / lidar_avg do
  pick(d) = T                <- single-source
as d sweeps 0..8 px. If avg's high-frequency energy (Laplacian variance) collapses with d while pick's
stays flat, the ghost is the AVERAGE operator (implementation), not physics — and the crossover d sets
the gate: forbid averaging once residual disparity exceeds it. Outputs a strip + a metric curve."""
from __future__ import annotations
import sys
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "deliverables" / "ghostkill"
OUT.mkdir(parents=True, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8")


def lap_var(img):
    g = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float64)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def shift(img, dx):
    H, W = img.shape[:2]
    xx, yy = np.meshgrid(np.arange(W), np.arange(H))
    return cv2.remap(img, (xx + dx).astype(np.float32), yy.astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def main():
    # real sharp texture: a building-facade crop from the BMW L1 panel (high-freq windows/edges)
    src = cv2.imread(str(OUT / "GK_bmw_bmw.png"))
    if src is None:
        print("need GK_bmw_bmw.png"); return 1
    w5 = src.shape[1] // 5
    T = src[40:300, 30:30 + 300].copy()  # textured region of the L1 panel (windows + car edge)
    T = T.astype(np.float32)

    ds = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
    rows_avg, rows_pick = [], []
    print(f"{'d_px':>5} {'avg_lapvar':>11} {'pick_lapvar':>12} {'avg/pick':>9}")
    pick = T  # single source, independent of d
    lv_pick = lap_var(pick)
    metrics = []
    for d in ds:
        B = shift(T, d)
        avg = 0.5 * (T + B)
        lv_avg = lap_var(avg)
        metrics.append((d, lv_avg, lv_pick, lv_avg / lv_pick))
        print(f"{d:>5.1f} {lv_avg:>11.1f} {lv_pick:>12.1f} {lv_avg/lv_pick:>9.3f}")
        if d in (0.0, 2.0, 4.0, 8.0):
            tag = lambda im, t: np.vstack([_lab(t), np.clip(im, 0, 255).astype(np.uint8)])
            rows_avg.append(tag(avg, f"avg d={d:.0f}"))
    # visual strip: avg at d=0,2,4,8 then pick
    rows_avg.append(np.vstack([_lab("pick (any d)"), np.clip(pick, 0, 255).astype(np.uint8)]))
    strip = np.hstack(rows_avg)
    strip = cv2.resize(strip, (strip.shape[1] * 2, strip.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT / "perturb_strip.png"), strip)

    # metric curve
    import math
    H, W = 300, 520
    plot = np.full((H, W, 3), 255, np.uint8)
    dvals = [m[0] for m in metrics]; ratios = [m[3] for m in metrics]
    x0, y0, pw, ph = 50, 20, 440, 250
    for gx in range(0, 9):
        px = x0 + int(gx / 8 * pw)
        cv2.line(plot, (px, y0), (px, y0 + ph), (235, 235, 235), 1)
    for gy in range(0, 6):
        py = y0 + int(gy / 5 * ph); cv2.line(plot, (x0, py), (x0 + pw, py), (235, 235, 235), 1)
    def pt(d, r):
        return (x0 + int(d / 8 * pw), y0 + ph - int(min(r, 1.0) * ph))
    for k in range(1, len(dvals)):
        cv2.line(plot, pt(dvals[k - 1], ratios[k - 1]), pt(dvals[k], ratios[k]), (0, 0, 200), 2)
    for d, r in zip(dvals, ratios):
        cv2.circle(plot, pt(d, r), 3, (0, 0, 200), -1)
    cv2.line(plot, pt(0, 1.0), pt(8, 1.0), (0, 160, 0), 1)  # pick = flat 1.0
    cv2.putText(plot, "avg sharpness / pick sharpness  vs  residual disparity d(px)", (8, 292),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1)
    cv2.putText(plot, "pick=1.0 (green)", (x0 + pw - 150, y0 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 130, 0), 1)
    cv2.imwrite(str(OUT / "perturb_curve.png"), plot)

    # gate: largest d with avg/pick >= 0.9 (averaging still ~sharp)
    okds = [d for d, _, _, r in metrics if r >= 0.9]
    gate_d = max(okds) if okds else 0.0
    print(f"\n[gate] averaging keeps >=90% sharpness only up to d={gate_d:.1f}px -> forbid avg above that.")
    print(f"[saved] {OUT}/perturb_strip.png + perturb_curve.png")
    return 0


def _lab(t, w=300):
    b = np.zeros((18, w, 3), np.uint8); cv2.putText(b, t, (4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
    return b


if __name__ == "__main__":
    raise SystemExit(main())
