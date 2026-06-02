"""Local zoom into the two user-circled regions of A1_view_none_L1_vs_result.jpg (L1 top / result
bottom). No Colab needed — just crops the existing figure so we can characterize the issues."""
import sys
from pathlib import Path
import numpy as np
try:
    import cv2
    def imread(p): return cv2.imread(str(p))
    def imwrite(p, im): cv2.imwrite(str(p), im, [cv2.IMWRITE_JPEG_QUALITY, 95])
    def resize(im, w): return cv2.resize(im, (w, round(im.shape[0]*w/im.shape[1])), interpolation=cv2.INTER_NEAREST)
except Exception:
    from PIL import Image
    def imread(p): return np.array(Image.open(p))[..., ::-1].copy()
    def imwrite(p, im): Image.fromarray(im[..., ::-1]).save(p, quality=95)
    def resize(im, w):
        from PIL import Image
        h = round(im.shape[0]*w/im.shape[1]); return np.array(Image.fromarray(im[..., ::-1]).resize((w, h), Image.NEAREST))[..., ::-1].copy()

D = Path(r"D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\deliverables\a1_streetview_pipeline")
im = imread(D / "A1_FINAL_L1_vs_result.jpg")
Hf, Wf = im.shape[:2]
print("figure", Wf, "x", Hf)
# layout: [label30 + L1panel] over [label30 + RESpanel]; each panel ~ Wf wide, height = Wf*1024/2048
ph = round(Wf * 1024 / 2048)
l1_y0 = 30; res_y0 = 30 + ph + 30
print("panel h", ph, "L1 y0", l1_y0, "RES y0", res_y0)

def both(name, u0, u1, v0, v1, zoom=900):
    # u,v in ERP(2048x1024) -> figure x,y
    x0 = round(u0 * Wf / 2048); x1 = round(u1 * Wf / 2048)
    a0 = l1_y0 + round(v0 * ph / 1024); a1 = l1_y0 + round(v1 * ph / 1024)
    b0 = res_y0 + round(v0 * ph / 1024); b1 = res_y0 + round(v1 * ph / 1024)
    L = im[a0:a1, x0:x1]; R = im[b0:b1, x0:x1]
    pair = np.vstack([L, np.full((4, L.shape[1], 3), 255, np.uint8), R])
    imwrite(D / f"ZOOM_{name}.jpg", resize(pair, zoom))
    print("saved ZOOM_%s.jpg  (L1 over result)  u[%d:%d] v[%d:%d]" % (name, u0, u1, v0, v1))

# gray car (front-left): scan a wide u window, mid-low rows
both("graycar_final", 560, 1000, 340, 560)
# BMW (right)
both("bmw_final", 1600, 1980, 360, 590)
