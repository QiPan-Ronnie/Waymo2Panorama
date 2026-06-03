"""Build OUTPAINT preserve-masks from an init ERP panorama (the black sky/ground band = the fill target).

Mask convention matches run_dit360_trimap_clamp.py `_load_preserve_mask`: WHITE(255)=preserve source
(byte-clamped), BLACK(0)=generate. So for outpaint, preserve = the CAPTURED content, generate = the
black band we want DiT360 to fill.

Produces several mask "angles" (the user wants outpaint tried from different angles):
  full   : fill ALL black (sky + ground + inter-slab gaps)            -> max completeness, max hallucination risk
  sky    : fill only black ABOVE the content horizon                  -> low risk (sky has no objects)
  ground : fill only black BELOW the horizon                          -> medium risk (road/ground)
  band   : fill only a THIN ring just outside the content edge        -> structure-continuation only, safest

Run (CPU): python _outpaint_mask.py <init.png> <out_dir> [horizon_frac=0.5] [band_px=60]
"""
from __future__ import annotations
import sys
from pathlib import Path
import cv2
import numpy as np


def main():
    init_path = sys.argv[1]; outdir = Path(sys.argv[2]); outdir.mkdir(parents=True, exist_ok=True)
    hor = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    band_px = int(sys.argv[4]) if len(sys.argv) > 4 else 60
    img = cv2.imread(init_path); H, W = img.shape[:2]
    # captured content = non-black; close small holes so interior dark (e.g. dark wall) isn't treated as gap
    content = (img.sum(2) > 12).astype(np.uint8)
    content = cv2.morphologyEx(content, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
    # but KEEP the true outer black band: erode-then-dilate closed only fills interior specks; recompute black
    contour_fill = content.copy()
    # flood-fill exterior to separate the true outer black band from interior dark that got closed
    cb = content.astype(bool)
    black = ~cb
    rows = np.arange(H)[:, None] * np.ones((1, W))
    cont_d = cv2.dilate(content, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (band_px * 2 + 1, band_px * 2 + 1))).astype(bool)

    def save(name, generate):
        preserve = ~generate
        m = np.where(preserve, 255, 0).astype(np.uint8)
        cv2.imwrite(str(outdir / f"opmask_{name}.png"), m)
        # preview: green=generate over the init
        pv = img.copy().astype(np.float32); g = np.zeros_like(pv); g[..., 1] = 255
        pv[generate] = 0.5 * pv[generate] + 0.5 * g[generate]
        cv2.imwrite(str(outdir / f"opmask_{name}_preview.jpg"), np.clip(pv, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"  {name}: generate={100*generate.mean():.1f}% of pano", flush=True)

    print(f"[outpaint-mask] {init_path} {W}x{H} content={100*cb.mean():.1f}% horizon_frac={hor} band_px={band_px}", flush=True)
    save("full", black)
    save("sky", black & (rows < H * hor))
    save("ground", black & (rows > H * hor))
    save("band", black & cont_d)
    save("band_sky", black & cont_d & (rows < H * hor))
    print(f"[saved] {outdir}/opmask_{{full,sky,ground,band,band_sky}}.png (+ _preview.jpg)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
