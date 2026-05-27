"""3-way (or 2-way) compare on Xihan's Waymo E2ED frame:
  - Xihan distance-to-boundary blending
  - Our L1 sphere + multiband
  - Our L1 sphere + L2 HDR + hard_select  (the color-shift fix)

For each, runs seam-detect + per-region Y diagnose (same protocol as
diagnose_xihan_waymo_panorama.py), then assembles annotated stack.

Outputs:
  compare_<n>way.png        full-res panel
  compare_<n>way_thumb.png  1024-wide for embedding
  compare_diagnose.json     stats for each input
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


def diagnose(rgb: np.ndarray, label: str) -> dict:
    H, W = rgb.shape[:2]
    seams = find_vertical_seams(rgb, expected_n_seams=7)
    boundaries = [0] + seams + [W]
    regions = []
    for i in range(len(boundaries) - 1):
        x0, x1 = boundaries[i], boundaries[i+1]
        if x1 - x0 < 20:
            continue
        s = region_stats(rgb, x0, x1)
        s["region_idx"] = i
        regions.append(s)
    Y_vals = [r["Y_med"] for r in regions if r["Y_med"] is not None]
    y_min, y_max = (float(min(Y_vals)), float(max(Y_vals))) if Y_vals else (None,None)
    gap_db = float(20.0*np.log10((y_max+1)/(y_min+1))) if Y_vals else None
    diffs = []
    for i in range(len(regions)-1):
        a, b = regions[i], regions[i+1]
        if a["Y_med"] is not None and b["Y_med"] is not None:
            diffs.append({"delta_Y": b["Y_med"]-a["Y_med"]})
    abs_diffs = [abs(d["delta_Y"]) for d in diffs]
    return {
        "label": label, "size_wh": [W, H], "seams_x": seams,
        "regions": regions, "Y_range": [y_min, y_max] if Y_vals else None,
        "Y_gap_db": gap_db,
        "seam_absdY_mean": float(np.mean(abs_diffs)) if abs_diffs else None,
        "seam_absdY_max": float(np.max(abs_diffs)) if abs_diffs else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xihan", required=True, type=Path)
    ap.add_argument("--l1", required=True, type=Path, help="our L1 multiband output")
    ap.add_argument("--hard-hdr", type=Path,
                    help="our L1+L2 HDR+hard_select output (optional, makes 3-way)")
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    def load(p):
        bgr = cv2.imread(str(p))
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    panels = []
    diag_results = {}

    xihan_rgb = load(args.xihan)
    H, W = xihan_rgb.shape[:2]
    print(f"=== {args.xihan.name} ({W}x{H}) ===")
    d = diagnose(xihan_rgb, "xihan_distance_to_boundary")
    print(f"  Y range {d['Y_range']}  gap {d['Y_gap_db']:.2f} dB  "
          f"mean|dY|={d['seam_absdY_mean']:.2f}  max={d['seam_absdY_max']:.2f}")
    diag_results["xihan"] = d
    panels.append(annotate_top(xihan_rgb,
        f"(1) Xihan distance-to-boundary  |  Y gap {d['Y_gap_db']:.2f} dB  mean|dY|={d['seam_absdY_mean']:.1f}  max={d['seam_absdY_max']:.0f}"))

    l1_rgb = load(args.l1)
    if l1_rgb.shape != xihan_rgb.shape:
        l1_rgb = cv2.resize(l1_rgb, (W, H), interpolation=cv2.INTER_AREA)
    d = diagnose(l1_rgb, "our_l1_multiband")
    print(f"=== L1 multiband ===")
    print(f"  Y range {d['Y_range']}  gap {d['Y_gap_db']:.2f} dB  "
          f"mean|dY|={d['seam_absdY_mean']:.2f}  max={d['seam_absdY_max']:.2f}")
    diag_results["l1_multiband"] = d
    panels.append(annotate_top(l1_rgb,
        f"(2) Our L1 sphere + multiband     |  Y gap {d['Y_gap_db']:.2f} dB  mean|dY|={d['seam_absdY_mean']:.1f}  max={d['seam_absdY_max']:.0f}"))

    if args.hard_hdr is not None:
        hh_rgb = load(args.hard_hdr)
        if hh_rgb.shape != xihan_rgb.shape:
            hh_rgb = cv2.resize(hh_rgb, (W, H), interpolation=cv2.INTER_AREA)
        d = diagnose(hh_rgb, "our_l1_l2hdr_hard_select")
        print(f"=== L1 + L2 HDR + hard_select ===")
        print(f"  Y range {d['Y_range']}  gap {d['Y_gap_db']:.2f} dB  "
              f"mean|dY|={d['seam_absdY_mean']:.2f}  max={d['seam_absdY_max']:.2f}")
        diag_results["l1_l2hdr_hardselect"] = d
        panels.append(annotate_top(hh_rgb,
            f"(3) Our L1+L2 HDR+hard_select  |  Y gap {d['Y_gap_db']:.2f} dB  mean|dY|={d['seam_absdY_mean']:.1f}  max={d['seam_absdY_max']:.0f}"))

    panel = np.concatenate(panels, axis=0)
    n = len(panels)
    cv2.imwrite(str(args.out_dir / f"compare_{n}way.png"),
                cv2.cvtColor(panel, cv2.COLOR_RGB2BGR))
    # thumb to 1024 wide
    th_w = 1024
    th_h = int(panel.shape[0] * th_w / panel.shape[1])
    th = cv2.resize(panel, (th_w, th_h), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(args.out_dir / f"compare_{n}way_thumb.png"),
                cv2.cvtColor(th, cv2.COLOR_RGB2BGR))
    (args.out_dir / "compare_diagnose.json").write_text(
        json.dumps(diag_results, indent=2), encoding="utf-8")
    print(f"=== wrote compare_{n}way.png ({panel.shape[1]}x{panel.shape[0]}), thumb ({th_w}x{th_h}) ===")


if __name__ == "__main__":
    main()
