"""DB-125 stage 1: push generic worker, launch 8-way parallel band-gate (EGO_BLACK fine band) after localize."""
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")

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
print("WORKER_PUSHED")

JOB = r'''
import glob, json, os, subprocess, time
U = "02678d04-cc9f-3148-9f95-1ba66347dff9"
K = 8
# wait for localize+egomask
t0 = time.time()
while time.time() - t0 < 3600:
    log = open("/content/_dj_db125loc.log").read() if os.path.exists("/content/_dj_db125loc.log") else ""
    if "LOC_ALL_DONE" in log or "Traceback" in log:
        break
    time.sleep(15)
assert "LOC_ALL_DONE" in log, "localize failed: " + log[-300:]
N = len(glob.glob("/content/localav2/val/%s/sensors/lidar/*.feather" % U))
print("BG_NFRAMES %d" % N, flush=True)
FRAMES = list(range(0, N))
subs = [FRAMES[j::K] for j in range(K)]
procs = []
t0 = time.time()
extra = json.dumps([["GROUND_MODE = \"fill\"", "GROUND_MODE = \"off\""],
                    ["EGO_BLACK = False", "EGO_BLACK = True"]])
for j in range(K):
    od = "/content/db125_band/m%d" % j
    os.makedirs(od, exist_ok=True)
    lf = open("/content/db125_bg_m%d.log" % j, "w")
    p = subprocess.Popen(["python", "/content/db125_worker.py", "bg%d" % j,
                          ",".join(str(x) for x in subs[j]), U, od, extra],
                         stdout=lf, stderr=subprocess.STDOUT)
    procs.append(p)
print("BG_LAUNCHED %d workers" % K, flush=True)
rcs = [p.wait() for p in procs]
print("BG_RCS", rcs, "%.0fs" % (time.time() - t0), flush=True)
# aggregate manifests -> per-anchor max_reg_px -> clean run
reg = {}
for j in range(K):
    for mf in glob.glob("/content/db125_band/m%d/manifest*.json" % j):
        m = json.load(open(mf))
        for c in m.get("cases", []):
            aN = int(c["case"].split("_a")[-1])
            vm = c.get("view_morph") or {}
            vals = [v.get("max_reg_px", 0.0) for v in (vm.values() if isinstance(vm, dict) else vm)]
            reg[aN] = max([0.0] + vals)
print("BG_COVERED %d/%d" % (len(reg), N), flush=True)
clean = sorted(aN for aN, v in reg.items() if v <= 8.0)
best_lo, best_hi, lo = None, None, None
prev = None
for aN in clean + [None]:
    if lo is None:
        lo = aN
    elif aN is None or aN != prev + 1:
        if prev is not None and (best_lo is None or prev - lo > best_hi - best_lo):
            best_lo, best_hi = lo, prev
        lo = aN
    prev = aN if aN is not None else prev
print("BG_CLEANRUN lo=%s hi=%s len=%s" % (best_lo, best_hi,
      (best_hi - best_lo + 1) if best_lo is not None else 0), flush=True)
json.dump({"N": N, "reg": {str(k): v for k, v in reg.items()},
           "clean_lo": best_lo, "clean_hi": best_hi},
          open("/content/db125_bg_summary.json", "w"))
print("BG_DONE", flush=True)
'''
a.dr_launch("db125bg", JOB)
print("BG_LAUNCHED")
