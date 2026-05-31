"""Build ONE complete results sheet for Koi's DiT360 outpaint experiment:
2 inputs + all 4 raw outputs + all 4 corecompose (real-center-kept) outputs, labeled.
"""
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

BASE = Path("deliverables/koi_outpaint_center")
R = BASE / "results"
SRC = BASE.parent / "dit360_seam_completion/runs_v14_trimap_clamp_bmw/trimap_r008_h016_w025_tau5"
W = 980
BAND = 34

rows = [
    ("INPUT 1  L1 hard_select  (real BMW/Miami; black = sky/ground cams can't see)",
     SRC / "trimap_r008_h016_w025_tau5_hard_select_fullres_1024x2048.png"),
    ("INPUT 2  L1 + DiT seam-completed (v14 raw)  ~ identical to input 1",
     SRC / "trimap_r008_h016_w025_tau5_raw_fullres_1024x2048.png"),
    ("RAW  hard_select x SECTOR  (DiT360 full generation: center kept, 95% generated)",
     R / "hardselect_sector/hardselect_sector_raw.png"),
    ("RAW  hard_select x WINDOW", R / "hardselect_window/hardselect_window_raw.png"),
    ("RAW  dit-seam x SECTOR", R / "ditseam_sector/ditseam_sector_raw.png"),
    ("RAW  dit-seam x WINDOW", R / "ditseam_window/ditseam_window_raw.png"),
    ("CORECOMPOSE  hard_select x SECTOR  (generated surroundings + byte-exact REAL center)",
     R / "hardselect_sector/hardselect_sector_corecompose.png"),
    ("CORECOMPOSE  hard_select x WINDOW", R / "hardselect_window/hardselect_window_corecompose.png"),
    ("CORECOMPOSE  dit-seam x SECTOR", R / "ditseam_sector/ditseam_sector_corecompose.png"),
    ("CORECOMPOSE  dit-seam x WINDOW", R / "ditseam_window/ditseam_window_corecompose.png"),
]


def panel(label, path):
    img = Image.open(path).convert("RGB")
    h = round(img.height * W / img.width)
    img = img.resize((W, h), Image.Resampling.BICUBIC)
    band = Image.new("RGB", (W, BAND), (16, 16, 16))
    ImageDraw.Draw(band).text((10, 10), label, fill=(255, 255, 255))
    return np.vstack([np.array(band), np.array(img)])


out = np.vstack([panel(l, p) for l, p in rows])
dst = BASE / "koi_outpaint_ALL_results.jpg"
Image.fromarray(out).save(dst, quality=90)
print("wrote", dst, out.shape)
