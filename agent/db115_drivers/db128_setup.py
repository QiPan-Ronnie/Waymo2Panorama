"""DB-128 setup on fresh 40GB A100: v9 tree (6+2+5 edits, md5 fcbc077e) + tools + rebuild 05fa5048 middle layers."""
import base64
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

W2P = r"D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama"
KP = os.path.join(W2P, "scripts", "phase3", "db89_ghost_recovery.py")
TARGET = hashlib.md5(open(KP, "rb").read()).hexdigest()
a = dr2.get("a100")

# ---- base tree ----
r = a._exec(
    'Z="/content/drive/MyDrive/koi_waymo2pano_colab/bundles/w2p_bundle8_relay.zip"\n'
    "rm -rf /content/waymo2panorama /content/w2p_ego\n"
    'unzip -oq "$Z" -d /content\n'
    "cp -r /content/waymo2panorama /content/w2p_ego\n"
    "md5sum /content/w2p_ego/scripts/phase3/db89_ghost_recovery.py\necho TREE_OK", 600)
t = r.get("log_tail") or ""
assert "cebab759a30a1af56f88d694ea6bd182" in t, "BASE BAD: " + t[-150:]
print("BASE_OK")

# ---- collect all edits: 6 (DB-123) + 2 (WORLDBEV_CENTER) + 5 (CAP v9) ----
it = open(os.path.join(W2P, "agent", "db115_drivers", "db124_install_tree_wbev.py"), encoding="utf-8").read()
ns = {}
exec(compile(it[it.index("OLD0 = "):it.index("all_edits = ")], "<e1>", "exec"), ns)
edits = [(ns["OLD0"], ns["NEW0"]), (ns["OLD0b"], ns["NEW0b"]), (ns["E1o"], ns["E1n"]),
         (ns["E2o"], ns["E2n"]), (ns["E3o"], ns["E3n"]), (ns["E4o"], ns["E4n"])]
C1o = 'WORLDBEV_FILL = ""  # DB-109 coherence test: path to a FLUX-filled world-BEV png; if set, worldbev OVERRIDES the built map with it so every target frame samples the SAME generated map ("generate once + sample" = temporal coherence by construction). Empty = build normally.'
C1n = C1o + '\nWORLDBEV_CENTER = ""  # DB-123 cascade: "x,y" city metres; pins the map grid origin so a WORLDBEV_FILL map built at one anchor stays registered when sampled from neighbouring anchors. Empty = anchor-centred (unchanged).'
C2o = """        # EMC capture-time poses per camera (same as the per-cap path).
        _MHALF, _CW = 46.0, 0.05
        _mcx, _mcy = float(ta[0]), float(ta[1])"""
C2n = C2o + """
        if WORLDBEV_CENTER:
            _mcx, _mcy = (float(_v) for _v in WORLDBEV_CENTER.split(","))"""
edits += [(C1o, C1n), (C2o, C2n)]
pa = open(os.path.join(W2P, "agent", "db115_drivers") + "\\..\\..\\..\\..\\" , encoding="utf-8") if False else None
src126 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "db126_patch_ab.py"), encoding="utf-8").read()
ns2 = {}
exec(compile(src126[src126.index("EDITS = ["):src126.index("lines = [")], "<e2>", "exec"), ns2)
edits += ns2["EDITS"]

lines = ["import hashlib, base64",
         "p = '/content/w2p_ego/scripts/phase3/db89_ghost_recovery.py'",
         "src = open(p, encoding='utf-8').read()"]
for o, n in edits:
    ob = base64.b64encode(o.encode()).decode()
    nb = base64.b64encode(n.encode()).decode()
    lines.append("o = base64.b64decode('%s').decode(); n = base64.b64decode('%s').decode()" % (ob, nb))
    lines.append("assert src.count(o) == 1, 'anchor:' + o[:50]")
    lines.append("src = src.replace(o, n)")
lines.append("open(p, 'wb').write(src.encode('utf-8'))")
lines.append("print('PATCHED_MD5', hashlib.md5(open(p, 'rb').read()).hexdigest())")
r = a._exec("python3 - <<'PYEOF'\n%s\nPYEOF" % "\n".join(lines), 300)
t = r.get("log_tail") or ""
print(t[-120:])
assert TARGET in t, "PATCH MISMATCH vs " + TARGET
print("TREE_V9_OK")

# ---- push worker + egomask module ----
for local, remote in [
    (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "_.py"), None),
]:
    pass
for local, remote in [
    (os.path.join(W2P, "agent", "db115_drivers", "db123_egomask_analytic.py"), "/content/db123_egomask.py"),
]:
    b = base64.b64encode(open(local, "rb").read()).decode()
    a._exec("python3 -c \"import base64; open('%s','wb').write(base64.b64decode('%s'))\"" % (remote, b), 120)
WORKER = r'''import warnings, sys, json
warnings.warn = lambda *a, **k: None
sys.path.insert(0, "/content/w2p_ego/scripts/phase3")
sys.path.insert(0, "/content/w2p_ego/code")
import video_gen_av2 as V
tag = sys.argv[1]
anchors = [int(x) for x in sys.argv[2].split(",")]
uuid = sys.argv[3]
outdir = sys.argv[4]
extra = json.loads(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else []
py = V.batch_py(uuid, tag, anchors)
def rep(p, old, new):
    assert p.count(old) >= 1, "MISS: " + old[:60]
    return p.replace(old, new)
py = rep(py, "/content/drive/MyDrive/koi_waymo2pano_colab/datasets/av2_ground_video_v1", outdir)
py = rep(py, "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val", "/content/localav2/val")
py = rep(py, "BAND_TORCH = False", "BAND_TORCH = True")
py = rep(py, 'EGO_IMG_MASK = ""', 'EGO_IMG_MASK = "/content/egomask_cur.npz"')
for o, n in extra:
    py = rep(py, o, n)
exec(compile(py, "<w>", "exec"))
'''
b = base64.b64encode(WORKER.encode()).decode()
a._exec("python3 -c \"import base64; open('/content/db125_worker.py','wb').write(base64.b64decode('%s'))\"" % b, 120)
print("MODULES_OK")

# ---- background: tools + localize + rebuild middle layers for 05fa5048 (P=41) ----
JOB = r'''
import glob, json, os, shutil, subprocess, time
U = "05fa5048-f355-3274-b565-c0ddc547b315"
P = 41
K = 8
def run(cmd, tmo=1800):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=tmo)
    print("RUN[%s] rc=%d" % (cmd[:60], r.returncode), flush=True)
    return r
run("pip install -q s5cmd einops av")
run("git clone -q https://github.com/sczhou/ProPainter /content/ProPainter && mkdir -p /content/ProPainter/weights")
for f in ["ProPainter.pth", "recurrent_flow_completion.pth", "raft-things.pth"]:
    run("wget -q https://github.com/sczhou/ProPainter/releases/download/v0.1.0/%s -O /content/ProPainter/weights/%s" % (f, f), 600)
t0 = time.time()
run("s5cmd --no-sign-request cp 's3://argoverse/datasets/av2/sensor/val/%s/*' /content/localav2/val/%s/" % (U, U), 3600)
print("LOC %.0fs" % (time.time() - t0), flush=True)
import sys
sys.path.insert(0, "/content")
from pathlib import Path
from db123_egomask import save_ego_mask_npz
save_ego_mask_npz(Path("/content/localav2/val/" + U), "/content/egomask_cur.npz")
print("EGOMASK_OK", flush=True)

def fan(tag, anchors, extra, root):
    subs = [anchors[j::K] for j in range(K)]
    procs = []
    for j in range(K):
        if not subs[j]:
            continue
        od = "%s/m%d" % (root, j)
        os.makedirs(od, exist_ok=True)
        lf = open("%s_w%d.log" % (root, j), "w")
        procs.append(subprocess.Popen(["python", "/content/db125_worker.py", "%s%d" % (tag, j),
                                       ",".join(str(x) for x in subs[j]), U, od, extra],
                                      stdout=lf, stderr=subprocess.STDOUT))
    return [p.wait() for p in procs]

root = "/content/db128"
# band: window frames only (P..P+92) + EGO_BLACK — diagnosis needs the window, not all 156
t0 = time.time()
extra_bg = json.dumps([["GROUND_MODE = \"fill\"", "GROUND_MODE = \"off\""],
                       ["EGO_BLACK = False", "EGO_BLACK = True"]])
rcs = fan("bg", list(range(P, P + 93)), extra_bg, root + "/band")
print("BAND %s %.0fs" % (all(x == 0 for x in rcs), time.time() - t0), flush=True)
# map60 || fill
MID = P + 46
extra_map = json.dumps([
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, 156)"],
    ["_aidx))[:110])", "_aidx))[:60])"],
])
os.makedirs(root + "/map", exist_ok=True)
mlf = open(root + "/map.log", "w")
t0 = time.time()
mproc = subprocess.Popen(["python", "/content/db125_worker.py", "mp", str(MID), U, root + "/map", extra_map],
                         stdout=mlf, stderr=subprocess.STDOUT)
CAP_LIM = root + "/band/*/bg*_a%03d_egozone.png"
CAP_REF = root + "/band/*/bg*_a%03d_segcomposite.png"
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
rcs = fan("ff", list(range(P + 1, P + 93)), extra_ff, root + "/fill")
print("FILL %s %.0fs" % (all(x == 0 for x in rcs), time.time() - t0), flush=True)
mrc = mproc.wait()
print("MAP rc=%d %.0fs" % (mrc, time.time() - t0), flush=True)
shutil.copy(glob.glob(root + "/map/mp_a%03d_worldmap.png" % MID)[0], root + "/worldmap.png")
# centre
import numpy as np, pandas as pd
sys.path.insert(0, "/content/w2p_ego/scripts/phase3"); sys.path.insert(0, "/content/w2p_ego/code")
from waymo2panorama.data_io.av2_loader import AV2RingLoader
LOGD = Path("/content/localav2/val/" + U)
loader = AV2RingLoader(LOGD)
ts_mid = loader.anchor_timestamps_ns()[MID]
pf = pd.read_feather(LOGD / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
ti = pf["timestamp_ns"].to_numpy(np.int64); tt0 = int(ti[0]); tss = (ti - tt0).astype(np.float64)
tx = pf[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
keep = np.concatenate([[True], np.diff(tss) > 0]); tss, tx = tss[keep], tx[keep]
tc = float(np.clip(float(int(ts_mid) - tt0), tss.min(), tss.max()))
CX, CY = (float(np.interp(tc, tss, tx[:, i])) for i in range(2))
open(root + "/center.txt", "w").write("%.6f,%.6f" % (CX, CY))
print("CENTER %.6f,%.6f" % (CX, CY), flush=True)
extra_wf = json.dumps(common + [
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, 156)"],
    ['WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "%s/worldmap.png"' % root],
    ['WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "%.6f,%.6f"' % (CX, CY)],
])
t0 = time.time()
rcs = fan("wf", list(range(P + 1, P + 93)), extra_wf, root + "/wbev")
print("WBEV %s %.0fs" % (all(x == 0 for x in rcs), time.time() - t0), flush=True)
print("DB128_LAYERS_DONE", flush=True)
'''
a.dr_launch("db128setup", JOB)
print("SETUP_LAUNCHED")
