"""Koi's experiment: keep ONLY the center patch, outpaint the whole surrounding 360.

Builds DiT360 core_mask PNGs (convention: WHITE=255 = PRESERVE, BLACK=0 = GENERATE) for two
variants of "the center":
  (a) center-sector : keep the full front_center camera's azimuthal sector (~52deg wide column
      in the middle of the ERP), generate everything else (~308deg + sky/ground). This is the
      literal "只用最中心那张相机扩成360".
  (b) center-window : keep a smaller central rectangular window (a perspective-ish crop), generate
      all around. Closer to the DiT360 project-page Editing/Outpainting demo.

For BOTH, the preserve region is intersected with actual CONTENT (non-black) so we never "preserve"
a black ERP pixel. The surrounding generate region includes the originally-black FoV (sky/ground)
AND the other cameras' content — all handed to DiT360 to outpaint.

Run (local, no GPU needed):
  python scripts/phase3/make_outpaint_center_mask.py --init <hard_select_1024x2048.png> \
      --out-dir <dir> --tag bmw_hardselect
Produces: {tag}_coremask_sector.png, {tag}_coremask_window.png, and _preview.jpg for each.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", required=True, help="L1 hard_select ERP 1024x2048 png")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="bmw")
    ap.add_argument("--sector-deg", type=float, default=52.0,
                    help="(a) azimuthal width to keep around image center = one front cam FoV")
    ap.add_argument("--window-wfrac", type=float, default=0.16,
                    help="(b) central window width as fraction of W")
    ap.add_argument("--window-hfrac", type=float, default=0.34,
                    help="(b) central window height as fraction of H (around equator)")
    args = ap.parse_args()
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    rgb = np.array(Image.open(args.init).convert("RGB"))
    H, W = rgb.shape[:2]
    content = rgb.sum(2) > 12  # non-black actual camera content

    cx = W // 2  # ERP horizontal center column = forward (theta=0), front_center camera
    cy = H // 2  # equator

    def save(mask_preserve: np.ndarray, name: str):
        # preserve only where we want AND there is real content
        keep = mask_preserve & content
        png = np.where(keep, 255, 0).astype(np.uint8)
        Image.fromarray(png, mode="L").save(out / f"{args.tag}_coremask_{name}.png")
        # preview: red overlay = will be GENERATED (i.e. NOT kept), green = kept
        prev = rgb.astype(np.float32).copy()
        gen = ~keep
        red = np.zeros_like(prev); red[..., 0] = 255
        grn = np.zeros_like(prev); grn[..., 1] = 210
        prev[gen] = 0.55 * prev[gen] + 0.45 * red[gen]
        prev[keep] = 0.6 * prev[keep] + 0.4 * grn[keep]
        Image.fromarray(np.clip(prev, 0, 255).astype(np.uint8)).save(out / f"{args.tag}_coremask_{name}_preview.jpg", quality=92)
        print(f"[{args.tag}] {name}: keep {100*keep.mean():.1f}% of ERP  -> generate {100*(1-keep.mean()):.1f}%", flush=True)

    # (a) center azimuthal sector (full height) — "keep front_center camera"
    half_cols = int(round(args.sector_deg / 360.0 * W / 2))
    sector = np.zeros((H, W), bool)
    sector[:, cx - half_cols: cx + half_cols] = True
    save(sector, "sector")

    # (b) center rectangular window around the equator
    hw = int(round(args.window_wfrac * W / 2))
    hh = int(round(args.window_hfrac * H / 2))
    window = np.zeros((H, W), bool)
    window[cy - hh: cy + hh, cx - hw: cx + hw] = True
    save(window, "window")

    print(f"[{args.tag}] wrote masks -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
