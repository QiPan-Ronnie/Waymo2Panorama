"""Fix accelerate, download Wan2.1-VACE-1.3B-diffusers, run DiT video inpainting
on the 31-frame gated band + residual-hole masks, save comparison video."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")
JOB = r'''
import os, subprocess, time
r = subprocess.run(["pip", "install", "-q", "-U", "accelerate"], capture_output=True, text=True)
print("WV_PIP_ACC rc=%d" % r.returncode, flush=True)
os.environ["HF_TOKEN"] = open("/content/hf_token").read().strip()
os.environ["HF_HOME"] = "/content/hf_home"
import inspect
from diffusers import WanVACEPipeline
sig = inspect.signature(WanVACEPipeline.__call__)
print("WV_SIG", [n for n in sig.parameters if n != "self"][:14], flush=True)

import numpy as np, cv2, torch
from PIL import Image
t0 = time.time()
pipe = WanVACEPipeline.from_pretrained("Wan-AI/Wan2.1-VACE-1.3B-diffusers", torch_dtype=torch.bfloat16).to("cuda")
print("WV_LOADED %.0fs" % (time.time() - t0), flush=True)

# inputs: 31 gated frames + residual hole masks; Wan wants mod-16 dims. Use 1280x640 (2:1 kept).
W2, H2 = 1280, 640
video, masks = [], []
for i in range(31):
    im = cv2.cvtColor(cv2.imread("/content/vi_in/%05d.png" % i, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    mk = cv2.imread("/content/vi_mask/%05d.png" % i, 0)
    video.append(Image.fromarray(cv2.resize(im, (W2, H2), interpolation=cv2.INTER_AREA)))
    mkr = cv2.resize(mk, (W2, H2), interpolation=cv2.INTER_NEAREST)
    masks.append(Image.fromarray(mkr))
PROMPT = ("street-level driving scene, asphalt road surface with lane markings and sidewalks, "
          "photorealistic, consistent geometry")
t0 = time.time()
out = pipe(video=video, mask=masks, prompt=PROMPT, height=H2, width=W2,
           num_frames=31, num_inference_steps=30, guidance_scale=5.0,
           generator=torch.Generator("cuda").manual_seed(0)).frames[0]
print("WV_GEN %.0fs" % (time.time() - t0), flush=True)
def to8(fr):
    ar = np.array(fr)
    if ar.dtype != np.uint8:
        ar = (np.clip(ar, 0.0, 1.0) * 255.0).astype(np.uint8)
    return ar
DRIVE = "/content/drive/MyDrive/koi_waymo2pano_colab"
D123 = DRIVE + "/results/db115pro/db123"
vw = cv2.VideoWriter("/content/wan_out.mp4", cv2.VideoWriter_fourcc(*"mp4v"), 10, (W2, H2))
for fr in out:
    vw.write(cv2.cvtColor(to8(fr), cv2.COLOR_RGB2BGR))
vw.release()
os.system("ffmpeg -y -loglevel error -i /content/wan_out.mp4 -c:v libx264 -pix_fmt yuv420p '%s/wanvace_out_cd22abca.mp4'" % D123)
# comparison stills at f15: input w/ holes vs Wan vs ProPainter (resized to same)
def loadvid(p):
    cap = cv2.VideoCapture(p); fr = []
    while True:
        ok, f = cap.read()
        if not ok: break
        fr.append(f)
    cap.release(); return fr
pp = loadvid("/content/vi_out/vi_in/inpaint_out.mp4")
inp = cv2.imread("/content/vi_in/00015.png", cv2.IMREAD_COLOR)
Hf, Wf = inp.shape[:2]
wan15 = cv2.resize(cv2.cvtColor(to8(out[15]), cv2.COLOR_RGB2BGR), (Wf, Hf))
pp15 = cv2.resize(pp[15], (Wf, Hf))
rows = []
for name, o in [("input_gated(holes)", inp), ("WanVACE_DiT", wan15), ("ProPainter", pp15)]:
    c = o[330:700, :]
    c = cv2.resize(c, (1800, int(c.shape[0] * 1800 / Wf)))
    c = cv2.copyMakeBorder(c, 22, 4, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    cv2.putText(c, "a125 " + name, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 128), 1)
    rows.append(c)
msk = cv2.imread("/content/vi_mask/00015.png", 0)
ys, xs = np.where(msk > 127)
order = np.argsort(xs); xs_s = xs[order]
splits = np.where(np.diff(xs_s) > 150)[0]
groups = np.split(np.arange(len(xs_s)), splits + 1)
for g in groups[:3]:
    gx = xs_s[g]
    x0, x1 = max(0, gx.min() - 60), min(Wf, gx.max() + 60)
    if x1 - x0 < 200: x0, x1 = max(0, x0 - 80), min(Wf, x1 + 80)
    tiles = []
    for name, o in [("Wan", wan15), ("PP", pp15)]:
        c = o[470:700, x0:x1]
        c = cv2.resize(c, (850, int(c.shape[0] * 850 / c.shape[1])))
        c = cv2.copyMakeBorder(c, 20, 4, 2, 2, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        cv2.putText(c, "%s x%d-%d" % (name, x0, x1), (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 128), 1)
        tiles.append(c)
    rows.append(np.hstack(tiles))
ww = max(r_.shape[1] for r_ in rows)
sheet = np.vstack([np.pad(r_, ((0, 0), (0, ww - r_.shape[1]), (0, 0))) for r_ in rows])
cv2.imwrite("/content/wan_cmp.jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
cv2.imwrite(D123 + "/wanvace_cmp_cd22abca.jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
print("WV_DONE", flush=True)
'''
a.dr_launch("wanvace", JOB)
print("WANVACE_LAUNCHED")
