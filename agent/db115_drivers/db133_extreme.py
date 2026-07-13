import glob, json, os, shutil, subprocess, time, threading
import numpy as np, cv2
import pandas as pd
from pathlib import Path
import sys
sys.path.insert(0, "/content")
sys.path.insert(0, "/content/w2p_ego/scripts/phase3")
sys.path.insert(0, "/content/w2p_ego/code")
H, W = 1024, 2048
DRIVE = "/content/drive/MyDrive/koi_waymo2pano_colab"
TT = {}
T00 = time.time()
def clk(k, t0):
    TT[k] = round(time.time() - t0, 1)
    print("CLK %s=%.1fs (wall %.1fs)" % (k, TT[k], time.time() - T00), flush=True)
def find1(pat):
    g = glob.glob(pat)
    assert g, pat
    return g[0]
def fan(tag, anchors, extra, root, uuid, k):
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
    return procs
def wait_ok(procs, what):
    rcs = [p.wait() for p in procs]
    assert all(x == 0 for x in rcs), what + " failed: %s" % rcs
def compose_one(args):
    root, i, aN = args
    band = cv2.imread(find1(root + "/band/m*/bg*_a%03d_segcomposite.png" % aN)).astype(np.float32)
    ez = cv2.imread(find1(root + "/band/m*/bg*_a%03d_egozone.png" % aN), 0)
    fil = cv2.imread(find1(root + "/fill/m*/ff*_a%03d_segcomposite.png" % aN)).astype(np.float32)
    fai = cv2.imread(find1(root + "/fill/m*/ff*_a%03d_faithfill_mask.png" % aN), 0)
    wb = cv2.imread(find1(root + "/wbev/m*/q*_a%03d_segcomposite.png" % aN)).astype(np.float32)
    zone = ez > 127
    bnz = band.sum(2) >= 12
    lower = np.zeros((H, W), bool)
    lower[H // 2:] = True
    colmax = np.full(W, -1)
    ys, xs = np.nonzero(bnz)
    np.maximum.at(colmax, xs, ys)
    rowidx = np.arange(H)[:, None]
    incontent = (rowidx <= (colmax[None, :] - 3)) & lower
    bh_raw = incontent & ~bnz & ~zone
    nlb, blab = cv2.connectedComponents(bh_raw.astype(np.uint8))
    bandhole = np.zeros_like(bh_raw)
    edge_line = (rowidx >= (colmax[None, :] - 4)) & lower
    for bi in range(1, nlb):
        m = blab == bi
        s = m.sum()
        if s < 200 or s > 30000 or (m & edge_line).any():
            continue
        bandhole |= m
    zone2 = zone | bandhole
    nz = fil.sum(2) >= 12
    faith = fai > 127
    wbok = wb.sum(2) >= 12

    ring0 = (cv2.dilate(zone2.astype(np.uint8), np.ones((21, 21), np.uint8)) > 0) & ~zone2 & bnz
    _gb = cv2.cvtColor(band.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    rl = float(np.median(_gb[ring0])) if ring0.sum() > 500 else 90.0
    v_thr = max(150.0, rl * 1.35)   # DB-130 fix: relative specular gate — sunny roads are bright+unsaturated by NATURE; only kill glare clearly above the frame's own road tone

    def spec_mask(img, valid):
        hsv = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2].astype(np.float32)
        s = hsv[:, :, 1].astype(np.float32)
        sel = zone2 & valid
        med = float(np.median(v[sel])) if sel.sum() > 500 else rl
        v_eff = v * (rl / max(med, 1.0))   # DB-130 v10.2: normalise source exposure to the band ring BEFORE the glare test — exposure offset is gain's job, not a specular signature
        return cv2.dilate(((v_eff > v_thr) & (s < 70)).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0

    spec_f = spec_mask(fil, nz)
    spec_w = spec_mask(wb, wbok)
    gray = cv2.cvtColor(fil.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    mu = cv2.boxFilter(lap, -1, (15, 15))
    mu2 = cv2.boxFilter(lap * lap, -1, (15, 15))
    sharp = (mu2 - mu * mu) > 40.0
    k7 = np.ones((7, 7), np.uint8)
    sharp = cv2.morphologyEx(sharp.astype(np.uint8), cv2.MORPH_OPEN, k7)
    sharp = cv2.morphologyEx(sharp, cv2.MORPH_CLOSE, k7) > 0
    ok = zone2 & nz & ~faith & sharp & ~spec_f
    k11 = np.ones((11, 11), np.uint8)
    ok = cv2.morphologyEx(ok.astype(np.uint8), cv2.MORPH_OPEN, k7)
    ok = cv2.morphologyEx(ok, cv2.MORPH_CLOSE, k11) > 0
    ok &= zone2 & nz & ~faith & ~spec_f
    nl, lab = cv2.connectedComponents(ok.astype(np.uint8))
    if nl > 1:
        cnt = np.bincount(lab.ravel())
        ok &= ~np.isin(lab, np.nonzero(cnt < 2000)[0][1:])
    fb = cv2.blur(fil, (31, 31))
    wbb = cv2.blur(wb, (31, 31))
    ok &= ~(ok & wbok & (np.abs(fb - wbb).sum(2) > 45.0))
    hole1 = zone2 & ~ok
    t2 = hole1 & wbok & ~spec_w
    resid = hole1 & ~(wbok & ~spec_w)
    fil_a, wb_a = fil.copy(), wb.copy()
    zl, zlab = cv2.connectedComponents(zone2.astype(np.uint8))
    for zi in range(1, zl):
        m = zlab == zi
        if m.sum() < 300:
            continue
        ring = (cv2.dilate(m.astype(np.uint8), np.ones((21, 21), np.uint8)) > 0) & ~zone2 & bnz
        if ring.sum() < 300:
            continue
        bmed = np.median(band[ring], axis=0)
        for src, sm in [(fil_a, ok & m), (wb_a, t2 & m)]:
            if sm.sum() > 200:
                smed = np.median(src[sm], axis=0)
                g = np.clip(bmed / np.maximum(smed, 8.0), 0.75, 1.35)
                src[sm] = np.clip(src[sm] * g[None, :], 0, 255)
    tempo = band.copy()
    tempo[ok] = fil_a[ok]
    tempo[t2] = wb_a[t2]
    if resid.any():
        t8 = np.clip(tempo, 0, 255).astype(np.uint8)
        t8 = cv2.inpaint(t8, resid.astype(np.uint8) * 255, 7, cv2.INPAINT_TELEA)
        tempo[resid] = t8[resid].astype(np.float32)
    filled = (ok | t2 | resid)
    edge = filled & (cv2.dilate((bnz & ~zone2).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
    edge |= (bnz & ~zone2) & (cv2.dilate(filled.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
    if edge.any():
        tb = cv2.GaussianBlur(tempo, (9, 9), 0)
        tempo[edge] = tb[edge]
    out = np.clip(tempo, 0, 255).astype(np.uint8)
    out[~(zone2 | bnz)] = 0
    cv2.imwrite(root + "/c10/in/%05d.png" % i, out)
    return (int(zone2.sum()), int(ok.sum()), int(t2.sum()), int(resid.sum()))




# ---- resident FLUX pre-load in a thread (overlaps localize+probe) ----
pipe_box = {}
def load_flux():
    os.environ["HF_HOME"] = DRIVE + "/cache/huggingface"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from diffusers import FluxFillPipeline
    t0 = time.time()
    p = FluxFillPipeline.from_pretrained("black-forest-labs/FLUX.1-Fill-dev", torch_dtype=torch.bfloat16).to("cuda")
    if hasattr(p.vae, "enable_tiling"):
        p.vae.enable_tiling()
    pipe_box["pipe"] = p
    clk("flux_preload_bg", t0)
fx = threading.Thread(target=load_flux)
fx.start()

r = subprocess.run("s5cmd --no-sign-request ls s3://argoverse/datasets/av2/sensor/val/ | grep 0b86f508", shell=True, capture_output=True, text=True)
U = r.stdout.strip().split()[-1].rstrip("/")
U8 = U[:8]
root = "/content/db133"
t0 = time.time()
subprocess.run("s5cmd --no-sign-request cp 's3://argoverse/datasets/av2/sensor/val/%s/*' /content/localav2/val/%s/" % (U, U), shell=True, timeout=3600)
clk("localize", t0)
from db123_egomask import save_ego_mask_npz
save_ego_mask_npz(Path("/content/localav2/val/" + U), "/content/egomask_cur.npz")
LOGD = Path("/content/localav2/val/" + U)
N = len(glob.glob(str(LOGD / "sensors/cameras/ring_front_center/*.jpg")))
from waymo2panorama.data_io.av2_loader import AV2RingLoader
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

# ---- stage 1: probe ----
t0 = time.time()
extra_bg = json.dumps([["GROUND_MODE = \"fill\"", "GROUND_MODE = \"off\""],
                       ["EGO_BLACK = False", "EGO_BLACK = True"]])
wait_ok(fan("bg", list(range(0, N, 3)), extra_bg, root + "/band", U, 24), "probe")
clk("probe", t0)
reg = {}
for mf in glob.glob(root + "/band/m*/manifest*.json"):
    m = json.load(open(mf))
    for c in m.get("cases", []):
        aN = int(c["case"].split("_a")[-1])
        vm = c.get("view_morph") or {}
        vals = [v.get("max_reg_px", 0.0) for v in (vm.values() if isinstance(vm, dict) else vm)]
        reg[aN] = max([0.0] + vals)
clean_probe = set(x for x, v in reg.items() if v <= 8.0)
best = None
for p in range(0, N - 93):
    wp = [a for a in range(p, p + 93) if a % 3 == 0]
    if not wp or not all(a in clean_probe for a in wp):
        continue
    cm = ta_of(p + 46)
    dmax = max(float(np.linalg.norm(ta_of(a) - cm)) for a in range(p, p + 93, 6))
    if best is None or dmax > best[1]:
        best = (p, dmax)
assert best, "no window"
P, dmax = best
MID = P + 46
R = float(np.clip(dmax + 14.0, 23.0, 46.0))
print("WINDOW P=%d dmax=%.1f R=%.1f" % (P, dmax, R), flush=True)
GRIDD = ["_MHALF, _CW = 46.0, 0.05", "_MHALF, _CW = %.1f, %.6f" % (R, R / 920.0)]
FUSE = ["_wmap = np.where(_conf[:, None], _col_conf, np.where(_anyv[:, None], _col_low, 0.0))",
        "_wmap = np.where(_anyv[:, None], np.nan_to_num(np.where(np.isnan(_wmed), 0.0, _wmed)), 0.0)"]
base_map = [
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % N],
    ["_aidx))[:110])", "_aidx))[:60])"],
    GRIDD, FUSE,
]

# ---- stage 2: map(16 shards) || fine || cand — ALL AT ONCE ----
t0 = time.time()
os.makedirs(root + "/map", exist_ok=True)
map_procs = []
for si in range(16):
    od = root + "/map/s%d" % si
    os.makedirs(od, exist_ok=True)
    ex = json.dumps(base_map + [
        ['WORLDBEV_SHARD = ""', 'WORLDBEV_SHARD = "%d,16"' % si],
        ['WORLDBEV_DUMP = ""', 'WORLDBEV_DUMP = "%s/map/shard_%d.npz"' % (root, si)],
    ])
    lf = open(root + "/map/s%d.log" % si, "w")
    map_procs.append(subprocess.Popen(["python", "/content/db125_worker.py", "ms%d" % si, str(MID), U, od, ex],
                                      stdout=lf, stderr=subprocess.STDOUT))
fine = [a for a in range(P, P + 93) if a % 3 != 0]
fine_procs = fan("bf", fine, extra_bg, root + "/band", U, 14)
extra_cd = json.dumps([
    ["FAITH_MASK = False", "FAITH_MASK = True"],
    ["    capg = blackg.copy()", "    capg = blackg.copy(); capg |= egoproj.reshape(H, W)"],
    ['GROUND_RESID = "plate"', 'GROUND_RESID = "inpaint"'],
    ["GROUND_TORCH = False", "GROUND_TORCH = True"],
])
os.makedirs(root + "/cand", exist_ok=True)
clf = open(root + "/cand.log", "w")
cand_proc = subprocess.Popen(["python", "/content/db125_worker.py", "cd", str(P), U, root + "/cand", extra_cd],
                             stdout=clf, stderr=subprocess.STDOUT)
wait_ok(fine_procs, "fine")
clk("fine", t0)
# ---- fill right after fine (needs fine egozones); map still running in parallel ----
t1 = time.time()
CAP_LIM = root + "/band/*/b*_a%03d_egozone.png"
CAP_REF = root + "/band/*/b*_a%03d_segcomposite.png"
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
fill_procs = fan("ff", list(range(P + 1, P + 93)), extra_ff, root + "/fill", U, 14)
wait_ok(fill_procs, "fill")
clk("fill", t1)
assert cand_proc.wait() == 0, "cand failed"
wait_ok(map_procs, "map shards")
clk("map_shards", t0)
t1 = time.time()
assert subprocess.run(["python", "/content/db131_merge.py", root + "/map/shard_*.npz", root + "/map/merged.npz"],
                      capture_output=True, text=True, timeout=600).returncode == 0
os.makedirs(root + "/map/final", exist_ok=True)
exf = json.dumps(base_map + [['WORLDBEV_LOAD = ""', 'WORLDBEV_LOAD = "%s/map/merged.npz"' % root]])
flf = open(root + "/map/final.log", "w")
assert subprocess.run(["python", "/content/db125_worker.py", "mf", str(MID), U, root + "/map/final", exf],
                      stdout=flf, stderr=subprocess.STDOUT).returncode == 0
clk("map_merge_final", t1)
shutil.copy(find1(root + "/map/final/mf_a%03d_worldmap.png" % MID), root + "/worldmap.png")
cm = ta_of(MID)
# ---- wbev ----
t0 = time.time()
extra_wf = json.dumps(common + [
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % N],
    GRIDD,
    ['WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "%s/worldmap.png"' % root],
    ['WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "%.6f,%.6f"' % (float(cm[0]), float(cm[1]))],
])
wait_ok(fan("q", list(range(P + 1, P + 93)), extra_wf, root + "/wbev", U, 24), "wbev")
clk("wbev", t0)
# ---- compose (band glob covers bg/bf; wbev tag q; fill tag ff) ----
t0 = time.time()
os.makedirs(root + "/c10/in", exist_ok=True)
from multiprocessing import Pool
with Pool(16) as pool:
    st = pool.map(compose_one, [(root, i2, P + 1 + i2) for i2 in range(92)])
tz = sum(s[0] for s in st)
clk("compose", t0)
print("CASCADE t1=%.1f%% t2=%.1f%% resid=%.2f%%" % (
    100.0 * sum(s[1] for s in st) / tz, 100.0 * sum(s[2] for s in st) / tz,
    100.0 * sum(s[3] for s in st) / tz), flush=True)
# ---- FLUX (pre-loaded) ----
fx.join()
pipe = pipe_box["pipe"]
import torch
from PIL import Image
t0 = time.time()
SC = "cd_a%03d" % P
CDIR = os.path.dirname(glob.glob(root + "/cand/**/%s_segcomposite.png" % SC, recursive=True)[0])
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
keepm = ~(gm > 127)
v8[keepm] = v7[keepm]
frame1 = cv2.cvtColor(v8, cv2.COLOR_RGB2BGR)
clk("flux_gen", t0)
# ---- pack + upload ----
t0 = time.time()
out = root + "/out"
os.makedirs(out + "/frames", exist_ok=True)
os.makedirs(out + "/masks", exist_ok=True)
cv2.imwrite(out + "/frames/fr_0000.png", frame1)
cv2.imwrite(out + "/masks/mk_0000.png", np.full((H, W), 255, np.uint8))
for i2 in range(92):
    f = cv2.imread(root + "/c10/in/%05d.png" % i2)
    cv2.imwrite(out + "/frames/fr_%04d.png" % (i2 + 1), f)
    cv2.imwrite(out + "/masks/mk_%04d.png" % (i2 + 1), (f.astype(np.int32).sum(2) >= 12).astype(np.uint8) * 255)
subprocess.run("ffmpeg -y -loglevel error -framerate 10 -i %s/frames/fr_%%04d.png -c:v libx264 -pix_fmt yuv420p %s/clip_%s_1plus92.mp4" % (out, out, U8), shell=True, timeout=600)
DST = DRIVE + "/datasets/av2_1plus92_cascade_v1/" + U8 + "_extreme"
os.makedirs(DST, exist_ok=True)
subprocess.run("cp -r %s/* '%s/'" % (out, DST), shell=True, timeout=1800)
clk("pack_upload", t0)
crit = TT.get("probe", 0) + TT.get("fine", 0) + TT.get("fill", 0) + TT.get("map_merge_final", 0) + TT.get("wbev", 0) + TT.get("compose", 0) + TT.get("flux_gen", 0) + TT.get("pack_upload", 0)
print("EXTREME_TOTAL wall=%.1fs resident-critical=%.1fs breakdown=%s" % (time.time() - T00, crit, json.dumps(TT)), flush=True)
print("DB133_DONE", flush=True)
