"""Build a labeled comparison montage of Koi's DiT360 outpaint experiment.
Rows: 2 source inputs, then the 4 outpaint raw results. Each row = full-width ERP + label band.
"""
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

BASE = Path("deliverables/koi_outpaint_center")
R = BASE / "results"
W = 1024  # downscale ERP width for a compact montage (each ERP 2048->1024)
BAND = 40

rows = [
    ("INPUT 1: L1 hard_select (real BMW/Miami scene, FoV band only)",
     BASE.parent / "dit360_seam_completion/runs_v14_trimap_clamp_bmw/trimap_r008_h016_w025_tau5/trimap_r008_h016_w025_tau5_hard_select_fullres_1024x2048.png"),
    ("INPUT 2: L1 + DiT seam-completed (v14 raw)",
     BASE.parent / "dit360_seam_completion/runs_v14_trimap_clamp_bmw/trimap_r008_h016_w025_tau5/trimap_r008_h016_w025_tau5_raw_fullres_1024x2048.png"),
    ("OUTPAINT  hard_select x SECTOR  (keep ~5% center column, generate 95%)",
     R / "hardselect_sector/hardselect_sector_raw.png"),
    ("OUTPAINT  hard_select x WINDOW  (keep ~5% center rect, generate 95%)",
     R / "hardselect_window/hardselect_window_raw.png"),
    ("OUTPAINT  dit-seam x SECTOR",
     R / "ditseam_sector/ditseam_sector_raw.png"),
    ("OUTPAINT  dit-seam x WINDOW",
     R / "ditseam_window/ditseam_window_raw.png"),
]


def panel(label, path):
    img = Image.open(path).convert("RGB")
    h = round(img.height * W / img.width)
    img = img.resize((W, h), Image.Resampling.BICUBIC)
    band = Image.new("RGB", (W, BAND), (18, 18, 18))
    d = ImageDraw.Draw(band)
    d.text((10, 12), label, fill=(255, 255, 255))
    return np.vstack([np.array(band), np.array(img)])


panels = [panel(l, p) for l, p in rows]
out = np.vstack(panels)
dst = BASE / "koi_outpaint_COMPARISON.jpg"
Image.fromarray(out).save(dst, quality=90)
print("wrote", dst, out.shape)
