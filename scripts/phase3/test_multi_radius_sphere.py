"""
Multi-radius sphere test (direction B, 5.22 prompt B5 candidate, never tried).

Render the SAME anchor with multiple sphere radii R = {inf, 30, 10, 5, 3} m.
At R = inf: legacy L1 baseline (current default), correct for far objects, ghost on near.
At R = 10 m: scene-median depth, should reduce ghost on mid-field objects.
At R = 5 m: closer, helps near objects but introduces ghost on far objects.

Hypothesis: finite R uniformly improves BMW-style near-field ghost
WITHOUT degrading far field (because at far depth, the angular shift is small).

If hypothesis confirmed, next step is per-pixel R selection via cross-cam NCC.

USAGE:
  python scripts/phase3/test_multi_radius_sphere.py \
    --log-dir /content/drive/MyDrive/.../02a00399-... \
    --anchor-idx 0 \
    --out-dir deliverables/multi_radius_test
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
from waymo2panorama.data_io.av2_loader import AV2RingLoader
from waymo2panorama.pipeline.stitch_frame import stitch_one_frame

RADII = [None, 30.0, 10.0, 5.0, 3.0]  # None == infinity / legacy L1
LABELS = ["R=inf (legacy)", "R=30m", "R=10m", "R=5m", "R=3m"]


def render_at_radius(frame, erp_hw, R):
    out, _summary = stitch_one_frame(
        frame=frame,
        erp_hw=erp_hw,
        convergence_distance_m=R,
        blend_mode="multiband",  # keep L1 baseline blend so we isolate the R effect
    )
    return out


def stack_panels(panels: list[np.ndarray], labels: list[str], crop=None) -> np.ndarray:
    """Stack RGB panels vertically, each labeled. Optional crop=(y0, y1, x0, x1)."""
    if crop is not None:
        y0, y1, x0, x1 = crop
        panels = [p[y0:y1, x0:x1] for p in panels]
    h, w = panels[0].shape[:2]
    label_h = 36
    out = []
    for p, lab in zip(panels, labels):
        band = np.zeros((label_h, w, 3), dtype=np.uint8)
        cv2.putText(band, lab, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        out.append(np.vstack([band, p.astype(np.uint8)]))
    return np.vstack(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, default=0)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    loader = AV2RingLoader(Path(args.log_dir))
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[args.anchor_idx])
    log_short = Path(args.log_dir).name.split("-")[0]

    panels = []
    for R in RADII:
        print(f"[render] R={R}")
        erp = render_at_radius(frame, erp_hw, R)
        panels.append(erp)
        tag = f"R{'inf' if R is None else int(R)}"
        cv2.imwrite(str(out_dir / f"{log_short}_a{args.anchor_idx:03d}_{tag}.png"),
                    cv2.cvtColor(erp.astype(np.uint8), cv2.COLOR_RGB2BGR))

    # Full ERP stack
    stacked_full = stack_panels(panels, LABELS)
    cv2.imwrite(str(out_dir / f"{log_short}_a{args.anchor_idx:03d}_full_stack.png"),
                cv2.cvtColor(stacked_full, cv2.COLOR_RGB2BGR))

    # BMW crop stack (right-front cam region, BMW is around theta = +60° to +120°)
    H, W = erp_hw
    crop = (int(H * 0.30), int(H * 0.85), int(W * 0.10), int(W * 0.45))
    stacked_bmw = stack_panels(panels, LABELS, crop=crop)
    cv2.imwrite(str(out_dir / f"{log_short}_a{args.anchor_idx:03d}_bmw_crop_stack.png"),
                cv2.cvtColor(stacked_bmw, cv2.COLOR_RGB2BGR))

    print(f"[done] saved to {out_dir}")


if __name__ == "__main__":
    main()
