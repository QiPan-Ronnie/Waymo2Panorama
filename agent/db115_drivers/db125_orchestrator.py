"""DB-125 master orchestrator: runs the ENTIRE remaining cascade-1+92 pipeline on the A100, unattended.
Waits for cand -> fill 92 (8w) || worldbev map build -> wbev 93 (8w) -> gated cascade composite -> PP
-> FLUX sky+ground for frame-1 -> package 93 frames + masks + mp4 -> upload to Drive cascade_v1."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")
JOB = r'''
import glob, json, os, subprocess, time
import numpy as np, cv2

U = "02678d04-cc9f-3148-9f95-1ba66347dff9"
U8 = U[:8]
K = 8
DRIVE = "/content/drive/MyDrive/koi_waymo2pano_colab"
DST = DRIVE + "/datasets/av2_1plus92_cascade_v1/" + U8
LEDGER = {"uuid": U, "stages": {}}

def mark(k, v):
    LEDGER["stages"][k] = v
    print("DB125[%s] %s" % (k, v), flush=True)

# ---------- STEP A: wait for cand ----------
t0 = time.time()
while time.time() - t0 < 7200:
    if os.path.exists("/content/db125_cand_summary.json"):
        break
    log = open("/content/_dj_db125cand.log").read() if os.path.exists("/content/_dj_db125cand.log") else ""
    assert "Traceback" not in log, "cand failed: " + log[-400:]
    time.sleep(20)
cs = json.load(open("/content/db125_cand_summary.json"))
P = cs["best_anchor"]; SC = cs["best_case"]; CDIR = cs["cand_dir"]
mark("cand", "best a%03d %s imperfect=%d" % (P, SC, cs["imperfect"]))
BAND_ANCHORS = list(range(P + 1, P + 93))   # 92 band frames

# ---------- STEP B: fill 92 (8 workers) in parallel with worldbev map build ----------
extra_fill = json.dumps([
    ["FAITH_MASK = False", "FAITH_MASK = True"],
    ["    capg = blackg.copy()", "    capg = blackg.copy(); capg |= egoproj.reshape(H, W)"],
    ['GROUND_RESID = "plate"', 'GROUND_RESID = "inpaint"'],
])
subs = [BAND_ANCHORS[j::K] for j in range(K)]
procs = []
t0 = time.time()
for j in range(K):
    od = "/content/db125_fill/m%d" % j
    os.makedirs(od, exist_ok=True)
    lf = open("/content/db125_fill_m%d.log" % j, "w")
    procs.append(subprocess.Popen(["python", "/content/db125_worker.py", "f%d" % j,
                                   ",".join(str(x) for x in subs[j]), U, od, extra_fill],
                                  stdout=lf, stderr=subprocess.STDOUT))
MID = P + 46
extra_map = json.dumps([
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, 157)"],
])
os.makedirs("/content/db125_map", exist_ok=True)
mlf = open("/content/db125_map.log", "w")
mproc = subprocess.Popen(["python", "/content/db125_worker.py", "mp",
                          str(MID), U, "/content/db125_map", extra_map],
                         stdout=mlf, stderr=subprocess.STDOUT)
rcs = [p.wait() for p in procs]
t_fill = time.time() - t0
mark("fill", "rcs=%s %.0fs (%.1fs/frame amortised)" % (rcs, t_fill, t_fill / 92.0))
assert all(r == 0 for r in rcs), "fill worker failed"
mrc = mproc.wait()
t_map = time.time() - t0
mark("map", "rc=%d done at +%.0fs" % (mrc, t_map))
assert mrc == 0, "map build failed: " + open("/content/db125_map.log").read()[-400:]
maps = glob.glob("/content/db125_map/mp_a%03d_worldmap.png" % MID)
assert maps, "worldmap missing"
import shutil
shutil.copy(maps[0], "/content/db125_worldmap.png")

# map centre = ta(MID), same interp math as the kernel
import pandas as pd
from pathlib import Path
sys_path0 = "/content/w2p_ego/scripts/phase3"
import sys as _sys
_sys.path.insert(0, sys_path0); _sys.path.insert(0, "/content/w2p_ego/code")
from waymo2panorama.data_io.av2_loader import AV2RingLoader
log_dir = Path("/content/localav2/val/" + U)
loader = AV2RingLoader(log_dir)
all_ts = loader.anchor_timestamps_ns()
ts = all_ts[MID]
pf = pd.read_feather(log_dir / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
ti = pf["timestamp_ns"].to_numpy(np.int64); tt0 = int(ti[0]); tss = (ti - tt0).astype(np.float64)
tx = pf[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
keep = np.concatenate([[True], np.diff(tss) > 0]); tss, tx = tss[keep], tx[keep]
tc = float(np.clip(float(int(ts) - tt0), tss.min(), tss.max()))
CX, CY = (float(np.interp(tc, tss, tx[:, i])) for i in range(2))
mark("center", "a%03d ta=(%.6f,%.6f)" % (MID, CX, CY))

# ---------- STEP C: wbev render 93 frames (P..P+92) with shared map ----------
WB_ANCHORS = list(range(P, P + 93))
extra_wb = json.dumps([
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, 157)"],
    ['WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "/content/db125_worldmap.png"'],
    ['WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "%.6f,%.6f"' % (CX, CY)],
])
subs = [WB_ANCHORS[j::K] for j in range(K)]
procs = []
t0 = time.time()
for j in range(K):
    od = "/content/db125_wbev/m%d" % j
    os.makedirs(od, exist_ok=True)
    lf = open("/content/db125_wbev_m%d.log" % j, "w")
    procs.append(subprocess.Popen(["python", "/content/db125_worker.py", "w%d" % j,
                                   ",".join(str(x) for x in subs[j]), U, od, extra_wb],
                                  stdout=lf, stderr=subprocess.STDOUT))
rcs = [p.wait() for p in procs]
t_wb = time.time() - t0
mark("wbev", "rcs=%s %.0fs (%.1fs/frame)" % (rcs, t_wb, t_wb / 93.0))
assert all(r == 0 for r in rcs), "wbev worker failed"

def find1(pat):
    g = glob.glob(pat)
    assert g, "missing " + pat
    return g[0]

# ---------- STEP D: gated cascade composite (Tier1 gates -> Tier2 wbev) ----------
os.makedirs("/content/db125_casc/in", exist_ok=True)
os.makedirs("/content/db125_casc/mask", exist_ok=True)
stats = []
for i, aN in enumerate(BAND_ANCHORS):
    band = cv2.imread(find1("/content/db125_band/m*/bg*_a%03d_segcomposite.png" % aN), cv2.IMREAD_COLOR)
    ez = cv2.imread(find1("/content/db125_band/m*/bg*_a%03d_egozone.png" % aN), 0)
    fil = cv2.imread(find1("/content/db125_fill/m*/f*_a%03d_segcomposite.png" % aN), cv2.IMREAD_COLOR)
    fai = cv2.imread(find1("/content/db125_fill/m*/f*_a%03d_faithfill_mask.png" % aN), 0)
    wb = cv2.imread(find1("/content/db125_wbev/m*/w*_a%03d_segcomposite.png" % aN), cv2.IMREAD_COLOR)
    zone = ez > 127
    nz = fil.astype(np.int32).sum(2) >= 12
    faith = fai > 127
    gray = cv2.cvtColor(fil, cv2.COLOR_BGR2GRAY).astype(np.float32)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    mu = cv2.boxFilter(lap, -1, (15, 15))
    mu2 = cv2.boxFilter(lap * lap, -1, (15, 15))
    sharp = (mu2 - mu * mu) > 40.0
    k7 = np.ones((7, 7), np.uint8)
    sharp = cv2.morphologyEx(sharp.astype(np.uint8), cv2.MORPH_OPEN, k7)
    sharp = cv2.morphologyEx(sharp, cv2.MORPH_CLOSE, k7) > 0
    ok = zone & nz & ~faith & sharp
    nl, lab = cv2.connectedComponents(ok.astype(np.uint8))
    if nl > 1:
        cnt = np.bincount(lab.ravel()); small = np.isin(lab, np.nonzero(cnt < 400)[0][1:])
        ok &= ~small
    tempo = band.copy()
    tempo[ok] = fil[ok]
    hole1 = zone & (tempo.astype(np.int32).sum(2) < 12)
    wbok = wb.astype(np.int32).sum(2) >= 12
    t2 = hole1 & wbok
    tempo[t2] = wb[t2]
    hole2 = hole1 & ~wbok
    cv2.imwrite("/content/db125_casc/in/%05d.png" % i, tempo)
    cv2.imwrite("/content/db125_casc/mask/%05d.png" % i, hole2.astype(np.uint8) * 255)
    stats.append((int(zone.sum()), int(ok.sum()), int(t2.sum()), int(hole2.sum())))
tz = sum(s[0] for s in stats); t1s = sum(s[1] for s in stats); t2s = sum(s[2] for s in stats); rs = sum(s[3] for s in stats)
mark("cascade", "zone=%d tier1=%d(%.1f%%) tier2=%d(%.1f%%) resid=%d(%.2f%%)" % (
    tz, t1s, 100.0 * t1s / max(tz, 1), t2s, 100.0 * t2s / max(tz, 1), rs, 100.0 * rs / max(tz, 1)))

# ---------- STEP E: ProPainter on residual ----------
if rs > 0:
    t0 = time.time()
    r = subprocess.run(["python", "inference_propainter.py",
                        "--video", "/content/db125_casc/in", "--mask", "/content/db125_casc/mask",
                        "--output", "/content/db125_casc/out", "--fp16",
                        "--width", "2048", "--height", "1024"],
                       capture_output=True, text=True, cwd="/content/ProPainter", timeout=3600)
    mark("pp", "rc=%d %.0fs" % (r.returncode, time.time() - t0))
    assert r.returncode == 0, "PP failed: " + (r.stderr or "")[-400:]
    cap = cv2.VideoCapture("/content/db125_casc/out/in/inpaint_out.mp4")
    finals = []
    while True:
        okf, f = cap.read()
        if not okf:
            break
        finals.append(f)
    cap.release()
    assert len(finals) == 92, "PP frames %d" % len(finals)
    # composite back ONLY the hole pixels (PP output is resized/recompressed video)
    for i in range(92):
        m = cv2.imread("/content/db125_casc/mask/%05d.png" % i, 0) > 127
        if m.any():
            base = cv2.imread("/content/db125_casc/in/%05d.png" % i, cv2.IMREAD_COLOR)
            base[m] = finals[i][m]
            cv2.imwrite("/content/db125_casc/in/%05d.png" % i, base)
else:
    mark("pp", "skipped (zero residual)")

# ---------- STEP F: frame-1 FLUX sky (v7) + ground (v8), offline Drive cache ----------
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
mark("flux_load", "%.0fs" % (time.time() - t0))
img = cv2.cvtColor(cv2.imread(CDIR + "/%s_segcomposite.png" % SC), cv2.COLOR_BGR2RGB)
H, W = img.shape[:2]
black = img.astype(int).sum(2) < 12
cap = np.zeros_like(black)
band_px = []
for u in range(W):
    kk = 0
    while kk < H and black[kk, u]:
        kk += 1
    cap[:kk, u] = True
    band_px.extend(img[kk:kk + 20, u].astype(np.float32))
band_arr = np.array(band_px)
bright = band_arr[band_arr.mean(1) > 100]
m = bright.mean(0) if len(bright) else band_arr.mean(0)
if m[0] > m[2] + 8:
    prompt = "warm dusk sky with soft orange glow and scattered clouds, natural evening light, seamless panorama sky"
elif m.mean() > 150 and m[2] > m[0] + 10:
    prompt = "clear sunny blue sky with scattered white cumulus clouds, seamless photographic panorama sky"
else:
    prompt = "pale softly overcast sky with diffuse natural light, seamless photographic panorama sky"
mask = cv2.dilate(cap.astype(np.uint8) * 255, np.ones((9, 9), np.uint8))
t0 = time.time()
res = pipe(prompt=prompt, image=Image.fromarray(img), mask_image=Image.fromarray(mask),
           height=H, width=W, guidance_scale=30.0, num_inference_steps=40,
           generator=torch.Generator("cuda").manual_seed(0)).images[0]
v7 = np.array(res)
v7[~(mask > 127)] = img[~(mask > 127)]
mark("sky", "%.0fs prompt=%s" % (time.time() - t0, prompt.split(",")[0]))
fmask = cv2.imread(CDIR + "/%s_faithfill_mask.png" % SC, 0)
gm = (fmask > 127).astype(np.uint8) * 255
gm = cv2.dilate(gm, np.ones((7, 7), np.uint8))
t0 = time.time()
res = pipe(prompt="smooth grey asphalt road surface, photorealistic, seamless ground continuation, soft natural shadow",
           image=Image.fromarray(v7), mask_image=Image.fromarray(gm),
           height=H, width=W, guidance_scale=30.0, num_inference_steps=40,
           generator=torch.Generator("cuda").manual_seed(0)).images[0]
v8 = np.array(res)
keep = ~(gm > 127)
v8[keep] = v7[keep]
mark("ground", "%.0fs faithfill_px=%d" % (time.time() - t0, int((fmask > 127).sum())))
del pipe
torch.cuda.empty_cache()
frame1 = cv2.cvtColor(v8, cv2.COLOR_RGB2BGR)

# ---------- STEP G: package 93 frames + mask twins + mp4 ----------
os.makedirs("/content/db125_out/frames", exist_ok=True)
os.makedirs("/content/db125_out/masks", exist_ok=True)
cv2.imwrite("/content/db125_out/frames/fr_0000.png", frame1)
cv2.imwrite("/content/db125_out/masks/mk_0000.png", np.full((H, W), 255, np.uint8))
for i in range(92):
    f = cv2.imread("/content/db125_casc/in/%05d.png" % i, cv2.IMREAD_COLOR)
    cv2.imwrite("/content/db125_out/frames/fr_%04d.png" % (i + 1), f)
    mk = (f.astype(np.int32).sum(2) >= 12).astype(np.uint8) * 255
    cv2.imwrite("/content/db125_out/masks/mk_%04d.png" % (i + 1), mk)
r = subprocess.run("ffmpeg -y -loglevel error -framerate 10 -i /content/db125_out/frames/fr_%04d.png -c:v libx264 -pix_fmt yuv420p /content/db125_out/clip_" + U8 + "_1plus92.mp4",
                   shell=True, capture_output=True, text=True, timeout=600)
mark("mp4", "rc=%d" % r.returncode)

# sample sheet for quick eyeballing (small)
tiles = []
for idx, lab in [(0, "frame1_v8"), (1, "band f001"), (46, "band f046"), (92, "band f092")]:
    fr = cv2.imread("/content/db125_out/frames/fr_%04d.png" % idx, cv2.IMREAD_COLOR)
    c = cv2.resize(fr, (1400, 700))
    c = cv2.copyMakeBorder(c, 24, 4, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    cv2.putText(c, U8 + " " + lab, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 128), 1)
    tiles.append(c)
sheet = np.vstack(tiles)
cv2.imwrite("/content/db125_out/sample_sheet.jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])

# ---------- STEP H: upload to Drive ----------
t0 = time.time()
os.makedirs(DST, exist_ok=True)
LEDGER["frame1"] = {"anchor": P, "case": SC, "imperfect": cs["imperfect"]}
LEDGER["window"] = [P, P + 92]
json.dump(LEDGER, open("/content/db125_out/ledger.json", "w"), indent=1)
r = subprocess.run("cp -r /content/db125_out/* '%s/'" % DST, shell=True, capture_output=True, text=True, timeout=1800)
mark("upload", "rc=%d %.0fs" % (r.returncode, time.time() - t0))
print("DB125_ALL_DONE", flush=True)
'''
a.dr_launch("db125all", JOB)
print("ORCH_LAUNCHED")
