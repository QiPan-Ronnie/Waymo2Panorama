# -*- coding: utf-8 -*-
"""Build fix6: full-anchor-space reproduction of 0aa4e8f5 (band 2nd half + motion-aware window + full chain)."""
rc = open("db130_recompose2_job.py", encoding="utf-8").read()
fn = rc[rc.index("def compose_one("):rc.index("LOGS = [")]

HEAD = '''import glob, json, os, shutil, subprocess, time
import numpy as np, cv2
import pandas as pd
from pathlib import Path
import sys
sys.path.insert(0, "/content")
sys.path.insert(0, "/content/w2p_ego/scripts/phase3")
sys.path.insert(0, "/content/w2p_ego/code")
H, W = 1024, 2048
K = 24
DRIVE = "/content/drive/MyDrive/koi_waymo2pano_colab"
def find1(pat):
    g = glob.glob(pat)
    assert g, pat
    return g[0]
def fan(tag, anchors, extra, root, uuid, k=K):
    subs = [anchors[j::k] for j in range(k)]
    procs = []
    for j in range(k):
        if not subs[j]:
            continue
        od = "%s/m%d" % (root, j)
        os.makedirs(od, exist_ok=True)
        lf = open("%s_w%d.log" % (root, j), "w")
        procs.append(subprocess.Popen(["python", "/content/db125_worker.py", "%s%d" % (tag, j),
                                       ",".join(str(x) for x in subs[j]), uuid, od, extra],
                                      stdout=lf, stderr=subprocess.STDOUT))
    return [p.wait() for p in procs]
'''

TAIL = '''
r = subprocess.run("s5cmd --no-sign-request ls s3://argoverse/datasets/av2/sensor/val/ | grep 0aa4e8f5", shell=True, capture_output=True, text=True)
U = r.stdout.strip().split()[-1].rstrip("/")
U8 = U[:8]
root = "/content/db130_" + U8
t_all = time.time()
subprocess.run("s5cmd --no-sign-request cp 's3://argoverse/datasets/av2/sensor/val/%s/*' /content/localav2/val/%s/" % (U, U), shell=True, timeout=3600)
from db123_egomask import save_ego_mask_npz
save_ego_mask_npz(Path("/content/localav2/val/" + U), "/content/egomask_cur.npz")
n_cam = len(glob.glob("/content/localav2/val/%s/sensors/cameras/ring_front_center/*.jpg" % U))
print("F6 n_anchor_space=%d (lidar was 156)" % n_cam, flush=True)
from waymo2panorama.data_io.av2_loader import AV2RingLoader
LOGD = Path("/content/localav2/val/" + U)
loader = AV2RingLoader(LOGD)
all_ts = loader.anchor_timestamps_ns()
pf = pd.read_feather(LOGD / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
ti = pf["timestamp_ns"].to_numpy(np.int64)
tt0 = int(ti[0])
tss = (ti - tt0).astype(np.float64)
tx = pf[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
keep = np.concatenate([[True], np.diff(tss) > 0])
tss, tx = tss[keep], tx[keep]
def ta_of(idx):
    tc = float(np.clip(float(int(all_ts[idx]) - tt0), tss.min(), tss.max()))
    return np.array([np.interp(tc, tss, tx[:, i2]) for i2 in range(2)])
N = len(all_ts)
# band: render ONLY the second half (156..N-1); first half already done
t0 = time.time()
extra_bg = json.dumps([["GROUND_MODE = \\"fill\\"", "GROUND_MODE = \\"off\\""],
                       ["EGO_BLACK = False", "EGO_BLACK = True"]])
rcs = fan("bh", list(range(156, N)), extra_bg, root + "/band2", U)
print("F6 band2 ok=%s %.0fs" % (all(x == 0 for x in rcs), time.time() - t0), flush=True)
# merge manifests from band + band2, full clean-run
reg = {}
for mf in glob.glob(root + "/band/m*/manifest*.json") + glob.glob(root + "/band2/m*/manifest*.json"):
    m = json.load(open(mf))
    for c in m.get("cases", []):
        aN = int(c["case"].split("_a")[-1])
        vm = c.get("view_morph") or {}
        vals = [v.get("max_reg_px", 0.0) for v in (vm.values() if isinstance(vm, dict) else vm)]
        reg[aN] = max([0.0] + vals)
clean = set(x for x, v in reg.items() if v <= 8.0)
# motion-aware window pick: legal windows fully clean AND dmax>=10
best = None
for p in range(0, N - 93):
    if not all(a in clean for a in range(p, p + 93)):
        continue
    cmid = ta_of(p + 46)
    dmax = max(float(np.linalg.norm(ta_of(a) - cmid)) for a in range(p, p + 93, 6))
    if best is None or dmax > best[1]:
        best = (p, dmax)
assert best is not None, "no clean 93-window in full space"
P, dmax = best
print("F6 window P=%d dmax=%.1f" % (P, dmax), flush=True)
assert dmax >= 8.0, "no moving window found (best dmax %.1f)" % dmax
MID = P + 46
R = float(np.clip(dmax + 14.0, 23.0, 46.0))
CWn = R / 920.0
GRIDD = ["_MHALF, _CW = 46.0, 0.05", "_MHALF, _CW = %.1f, %.6f" % (R, CWn)]
FUSE = ["_wmap = np.where(_conf[:, None], _col_conf, np.where(_anyv[:, None], _col_low, 0.0))",
        "_wmap = np.where(_anyv[:, None], np.nan_to_num(np.where(np.isnan(_wmed), 0.0, _wmed)), 0.0)"]
# cand on this window (10 samples in legal positions near P): use fixed P (motion-picked); render frame1 candidate at P
t0 = time.time()
extra_cd = json.dumps([
    ["FAITH_MASK = False", "FAITH_MASK = True"],
    ["    capg = blackg.copy()", "    capg = blackg.copy(); capg |= egoproj.reshape(H, W)"],
    ['GROUND_RESID = "plate"', 'GROUND_RESID = "inpaint"'],
    ["GROUND_TORCH = False", "GROUND_TORCH = True"],
])
os.makedirs(root + "/cand6", exist_ok=True)
clf = open(root + "/cand6.log", "w")
crc = subprocess.run(["python", "/content/db125_worker.py", "c6", str(P), U, root + "/cand6", extra_cd],
                     stdout=clf, stderr=subprocess.STDOUT).returncode
print("F6 cand rc=%d %.0fs" % (crc, time.time() - t0), flush=True)
assert crc == 0
SC = "c6_a%03d" % P
CDIR = find1(root + "/cand6/m0") if os.path.isdir(root + "/cand6/m0") else root + "/cand6"
CDIR = os.path.dirname(find1(root + "/cand6/**/c6_a%03d_segcomposite.png" % P))
# map || fill
t0 = time.time()
extra_map = json.dumps([
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % N],
    ["_aidx))[:110])", "_aidx))[:60])"],
    GRIDD, FUSE,
])
os.makedirs(root + "/map6", exist_ok=True)
mlf = open(root + "/map6.log", "w")
mproc = subprocess.Popen(["python", "/content/db125_worker.py", "m6", str(MID), U, root + "/map6", extra_map],
                         stdout=mlf, stderr=subprocess.STDOUT)
CAP_LIM = root + "/band*/*/b*_a%03d_egozone.png"
CAP_REF = root + "/band*/*/b*_a%03d_segcomposite.png"
common = [
    ["CAP_ONLY = False", "CAP_ONLY = True"],
    ['CAP_LIMIT_TMPL = ""', 'CAP_LIMIT_TMPL = "' + CAP_LIM + '"'],
    ['CAP_REF_TMPL = ""', 'CAP_REF_TMPL = "' + CAP_REF + '"'],
]
extra_ff = json.dumps(common + [
    ["FAITH_MASK = False", "FAITH_MASK = True"],
    ["    capg = blackg.copy()", "    capg = blackg.copy(); capg |= egoproj.reshape(H, W)"],
    ["GROUND_TORCH = False", "GROUND_TORCH = True"],
])
shutil.rmtree(root + "/fill", ignore_errors=True)
rcs = fan("ff", list(range(P + 1, P + 93)), extra_ff, root + "/fill", U)
print("F6 fill ok=%s %.0fs" % (all(x == 0 for x in rcs), time.time() - t0), flush=True)
mrc = mproc.wait()
print("F6 map rc=%d %.0fs" % (mrc, time.time() - t0), flush=True)
assert mrc == 0, open(root + "/map6.log").read()[-300:]
shutil.copy(find1(root + "/map6/m6_a%03d_worldmap.png" % MID), root + "/worldmap6.png")
cmid = ta_of(MID)
CX, CY = float(cmid[0]), float(cmid[1])
t0 = time.time()
extra_wf = json.dumps(common + [
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % N],
    GRIDD,
    ['WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "%s/worldmap6.png"' % root],
    ['WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "%.6f,%.6f"' % (CX, CY)],
])
shutil.rmtree(root + "/wbev", ignore_errors=True)
rcs = fan("q", list(range(P + 1, P + 93)), extra_wf, root + "/wbev", U)
print("F6 wbev ok=%s %.0fs" % (all(x == 0 for x in rcs), time.time() - t0), flush=True)
from multiprocessing import Pool
os.makedirs(root + "/c10/in", exist_ok=True)
with Pool(16) as pool:
    st = pool.map(compose_one, [(root, i2, P + 1 + i2) for i2 in range(92)])
tz = sum(s[0] for s in st)
print("F6 compose t1=%.1f%% t2=%.1f%% resid=%.2f%%" % (
    100.0 * sum(s[1] for s in st) / tz, 100.0 * sum(s[2] for s in st) / tz,
    100.0 * sum(s[3] for s in st) / tz), flush=True)
# FLUX frame-1 for the new window
os.environ["HF_HOME"] = DRIVE + "/cache/huggingface"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
import torch
from PIL import Image
from diffusers import FluxFillPipeline
t0 = time.time()
pipe = FluxFillPipeline.from_pretrained("black-forest-labs/FLUX.1-Fill-dev", torch_dtype=torch.bfloat16).to("cuda")
if hasattr(pipe.vae, "enable_tiling"):
    pipe.vae.enable_tiling()
print("F6 flux load %.0fs" % (time.time() - t0), flush=True)
t0 = time.time()
img = cv2.cvtColor(cv2.imread(CDIR + "/%s_segcomposite.png" % SC), cv2.COLOR_BGR2RGB)
black = img.astype(int).sum(2) < 12
cap = np.zeros_like(black)
band_px = []
for u in range(W):
    kk = 0
    while kk < H and black[kk, u]:
        kk += 1
    cap[:kk, u] = True
    band_px.extend(img[kk:kk + 20, u].astype(np.float32))
ba = np.array(band_px)
br = ba[ba.mean(1) > 100]
m = br.mean(0) if len(br) else ba.mean(0)
if m[0] > m[2] + 8:
    prompt = "warm dusk sky with soft orange glow and scattered clouds, natural evening light, seamless panorama sky"
elif m.mean() > 150 and m[2] > m[0] + 10:
    prompt = "clear sunny blue sky with scattered white cumulus clouds, seamless photographic panorama sky"
else:
    prompt = "pale softly overcast sky with diffuse natural light, seamless panorama sky"
mask = cv2.dilate(cap.astype(np.uint8) * 255, np.ones((9, 9), np.uint8))
res = pipe(prompt=prompt, image=Image.fromarray(img), mask_image=Image.fromarray(mask),
           height=H, width=W, guidance_scale=30.0, num_inference_steps=40,
           generator=torch.Generator("cuda").manual_seed(0)).images[0]
v7 = np.array(res)
v7[~(mask > 127)] = img[~(mask > 127)]
fmask = cv2.imread(CDIR + "/%s_faithfill_mask.png" % SC, 0)
gm = cv2.dilate((fmask > 127).astype(np.uint8) * 255, np.ones((7, 7), np.uint8))
res = pipe(prompt="smooth grey asphalt road surface, photorealistic, seamless ground continuation, soft natural shadow",
           image=Image.fromarray(v7), mask_image=Image.fromarray(gm),
           height=H, width=W, guidance_scale=30.0, num_inference_steps=40,
           generator=torch.Generator("cuda").manual_seed(0)).images[0]
v8 = np.array(res)
keep = ~(gm > 127)
v8[keep] = v7[keep]
frame1 = cv2.cvtColor(v8, cv2.COLOR_RGB2BGR)
print("F6 flux gen %.0fs" % (time.time() - t0), flush=True)
DST = DRIVE + "/datasets/av2_1plus92_cascade_v1/" + U8
cv2.imwrite(DST + "/frames/fr_0000.png", frame1)
cv2.imwrite(DST + "/masks/mk_0000.png", np.full((H, W), 255, np.uint8))
for i2 in range(92):
    f = cv2.imread(root + "/c10/in/%05d.png" % i2)
    cv2.imwrite(DST + "/frames/fr_%04d.png" % (i2 + 1), f)
    cv2.imwrite(DST + "/masks/mk_%04d.png" % (i2 + 1), (f.astype(np.int32).sum(2) >= 12).astype(np.uint8) * 255)
os.makedirs(root + "/pack6", exist_ok=True)
subprocess.run("cp '%s/frames/'fr_*.png %s/pack6/" % (DST, root), shell=True)
subprocess.run("ffmpeg -y -loglevel error -framerate 10 -i %s/pack6/fr_%%04d.png -c:v libx264 -pix_fmt yuv420p /content/clip6.mp4" % root, shell=True, timeout=600)
subprocess.run("cp /content/clip6.mp4 '%s/clip_%s_1plus92.mp4'" % (DST, U8), shell=True)
tiles = []
for idx, lb in [(0, "frame1_v8"), (1, "f001"), (46, "f046"), (92, "f092")]:
    fr = cv2.imread(DST + "/frames/fr_%04d.png" % idx)
    c = cv2.resize(fr, (1400, 700))
    c = cv2.copyMakeBorder(c, 24, 4, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    cv2.putText(c, U8 + " " + lb + " v10.5-motionwin", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 128), 1)
    tiles.append(c)
cv2.imwrite(DST + "/sample_sheet.jpg", np.vstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 90])
subprocess.run("cp '%s/sample_sheet.jpg' /content/sheet6.jpg" % DST, shell=True)
json.dump({"window": [P, P + 92], "dmax": dmax, "R": R, "n_anchor_space": N}, open(DST + "/ledger_v10.5.json", "w"), indent=1)
print("F6 total %.0fs" % (time.time() - t_all), flush=True)
print("FIX6_DONE", flush=True)
'''

open("db130_fix6_job.py", "w", encoding="utf-8").write(HEAD + fn + TAIL)
print("BUILT6")
