"""4-way Waymo E2ED comparison panel:
  (1) Xihan distance-to-boundary blending (baseline)
  (2) Our L1 sphere + multiband (no color fix)
  (3) Our L1 + L2 HDR + multiband  (smooth seams + color shift fix)  ← RECOMMENDED
  (4) Our L1 + L2 HDR + hard_select (crisp seams + color shift fix)

Each panorama gets seam-detect + per-region Y diagnose. NOTE: seam-detection
metric is biased toward smoother seams — it's a proxy, not ground truth. The
real evaluator is the visual.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnose_xihan_waymo_panorama import find_vertical_seams, region_stats   # noqa: E402


def annotate_top(img, text, h=56):
    H, W = img.shape[:2]
    bar = np.zeros((h, W, 3), dtype=np.uint8)
    cv2.putText(bar, text, (16, h-18), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255,255,255), 2, cv2.LINE_AA)
    return np.concatenate([bar, img], axis=0)


def diagnose(rgb, label):
    H, W = rgb.shape[:2]
    seams = find_vertical_seams(rgb, expected_n_seams=7)
    boundaries = [0] + seams + [W]
    regions = []
    for i in range(len(boundaries)-1):
        x0, x1 = boundaries[i], boundaries[i+1]
        if x1 - x0 < 20:
            continue
        s = region_stats(rgb, x0, x1); s["region_idx"]=i
        regions.append(s)
    Y_vals = [r["Y_med"] for r in regions if r["Y_med"] is not None]
    y_min, y_max = (float(min(Y_vals)),float(max(Y_vals))) if Y_vals else (None,None)
    gap_db = float(20.0*np.log10((y_max+1)/(y_min+1))) if Y_vals else None
    diffs=[]
    for i in range(len(regions)-1):
        a,b=regions[i],regions[i+1]
        if a["Y_med"] is not None and b["Y_med"] is not None:
            diffs.append(abs(b["Y_med"]-a["Y_med"]))
    Y_std = float(np.std(Y_vals)) if Y_vals else None
    return {"label":label,"Y_range":[y_min,y_max] if Y_vals else None,
            "Y_gap_db":gap_db, "Y_std":Y_std,
            "seam_absdY_mean":float(np.mean(diffs)) if diffs else None,
            "seam_absdY_max":float(np.max(diffs)) if diffs else None,
            "regions":regions}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xihan", required=True, type=Path)
    ap.add_argument("--l1-multiband", required=True, type=Path)
    ap.add_argument("--l1-hdr-multiband", required=True, type=Path)
    ap.add_argument("--l1-hdr-hardselect", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    def load(p):
        bgr = cv2.imread(str(p))
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    inputs = [
        ("(1) Xihan distance-to-boundary", args.xihan, "xihan"),
        ("(2) Our L1 sphere + multiband (no HDR)", args.l1_multiband, "l1_multiband"),
        ("(3) Our L1 + L2 HDR + multiband (RECOMMENDED)", args.l1_hdr_multiband, "l1_hdr_multiband"),
        ("(4) Our L1 + L2 HDR + hard_select", args.l1_hdr_hardselect, "l1_hdr_hardselect"),
    ]
    target_shape = None
    panels = []
    diag = {}
    for label, path, key in inputs:
        rgb = load(path)
        if target_shape is None:
            target_shape = rgb.shape
        elif rgb.shape != target_shape:
            rgb = cv2.resize(rgb, (target_shape[1], target_shape[0]), interpolation=cv2.INTER_AREA)
        d = diagnose(rgb, key)
        diag[key] = d
        print(f"=== {label} ===")
        print(f"  Y range {d['Y_range']}  gap {d['Y_gap_db']:.2f} dB  "
              f"Y_std={d['Y_std']:.2f}  mean|dY|={d['seam_absdY_mean']:.2f}  max={d['seam_absdY_max']:.0f}")
        annot = f"{label}  |  Y std={d['Y_std']:.1f}  gap {d['Y_gap_db']:.2f} dB  mean|dY|={d['seam_absdY_mean']:.1f}"
        panels.append(annotate_top(rgb, annot))

    panel = np.concatenate(panels, axis=0)
    cv2.imwrite(str(args.out_dir / "compare_4way.png"),
                cv2.cvtColor(panel, cv2.COLOR_RGB2BGR))
    th_w = 1024
    th_h = int(panel.shape[0] * th_w / panel.shape[1])
    th = cv2.resize(panel, (th_w, th_h), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(args.out_dir / "compare_4way_thumb.png"),
                cv2.cvtColor(th, cv2.COLOR_RGB2BGR))
    (args.out_dir / "compare_4way.json").write_text(
        json.dumps(diag, indent=2), encoding="utf-8")
    print(f"wrote compare_4way.png ({panel.shape[1]}x{panel.shape[0]}), thumb ({th_w}x{th_h})")


if __name__ == "__main__":
    main()
