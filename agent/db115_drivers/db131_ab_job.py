import glob
import json
import os
import shutil
import subprocess
import time

import numpy as np
import cv2

# wait for tools
t0 = time.time()
while time.time() - t0 < 1800:
    lg = open("/content/_dj_db131tools.log").read() if os.path.exists("/content/_dj_db131tools.log") else ""
    if "TOOLS_DONE" in lg:
        break
    time.sleep(15)
assert "TOOLS_DONE" in lg

r = subprocess.run("s5cmd --no-sign-request ls s3://argoverse/datasets/av2/sensor/val/ | grep 0b86f508", shell=True, capture_output=True, text=True)
U = r.stdout.strip().split()[-1].rstrip("/")
subprocess.run("s5cmd --no-sign-request cp 's3://argoverse/datasets/av2/sensor/val/%s/*' /content/localav2/val/%s/" % (U, U), shell=True, timeout=3600)
import sys
sys.path.insert(0, "/content")
from pathlib import Path
from db123_egomask import save_ego_mask_npz
save_ego_mask_npz(Path("/content/localav2/val/" + U), "/content/egomask_cur.npz")
print("AB_READY", flush=True)

MID = 94
N = 316
R, CW = 40.9, 40.9 / 920.0
GRIDD = ["_MHALF, _CW = 46.0, 0.05", "_MHALF, _CW = %.1f, %.6f" % (R, CW)]
FUSE = ["_wmap = np.where(_conf[:, None], _col_conf, np.where(_anyv[:, None], _col_low, 0.0))",
        "_wmap = np.where(_anyv[:, None], np.nan_to_num(np.where(np.isnan(_wmed), 0.0, _wmed)), 0.0)"]
base = [
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % N],
    ["_aidx))[:110])", "_aidx))[:60])"],
    GRIDD, FUSE,
]

# ---- A: full single-process build ----
os.makedirs("/content/abA", exist_ok=True)
t0 = time.time()
rc = subprocess.run(["python", "/content/db125_worker.py", "fa", str(MID), U, "/content/abA",
                     json.dumps(base)], capture_output=True, text=True, timeout=3600).returncode
tA = time.time() - t0
print("AB_FULL rc=%d %.0fs" % (rc, tA), flush=True)
assert rc == 0

# ---- B: 8 shards in parallel + merge + LOAD finaliser ----
t0 = time.time()
procs = []
for i in range(8):
    od = "/content/abS%d" % i
    os.makedirs(od, exist_ok=True)
    extra = json.dumps(base + [
        ['WORLDBEV_SHARD = ""', 'WORLDBEV_SHARD = "%d,8"' % i],
        ['WORLDBEV_DUMP = ""', 'WORLDBEV_DUMP = "/content/shard_%d.npz"' % i],
    ])
    lf = open(od + ".log", "w")
    procs.append(subprocess.Popen(["python", "/content/db125_worker.py", "s%d" % i, str(MID), U, od, extra],
                                  stdout=lf, stderr=subprocess.STDOUT))
rcs = [p.wait() for p in procs]
t_shards = time.time() - t0
print("AB_SHARDS rcs=%s %.0fs" % (rcs, t_shards), flush=True)
assert all(x == 0 for x in rcs)
t1 = time.time()
rc = subprocess.run(["python", "/content/db131_merge.py", "/content/shard_*.npz", "/content/merged.npz"],
                    capture_output=True, text=True, timeout=600).returncode
t_merge = time.time() - t1
print("AB_MERGE rc=%d %.0fs" % (rc, t_merge), flush=True)
assert rc == 0
t1 = time.time()
os.makedirs("/content/abB", exist_ok=True)
extra = json.dumps(base + [['WORLDBEV_LOAD = ""', 'WORLDBEV_LOAD = "/content/merged.npz"']])
rc = subprocess.run(["python", "/content/db125_worker.py", "fb", str(MID), U, "/content/abB", extra],
                    capture_output=True, text=True, timeout=1200).returncode
t_final = time.time() - t1
tB = time.time() - t0
print("AB_FINAL rc=%d %.0fs | B total %.0fs vs A %.0fs (%.1fx)" % (rc, t_final, tB, tA, tA / tB), flush=True)
assert rc == 0

# ---- compare the two worldmaps ----
A = cv2.imread(glob.glob("/content/abA/fa_a%03d_worldmap.png" % MID)[0]).astype(np.float32)
B = cv2.imread(glob.glob("/content/abB/fb_a%03d_worldmap.png" % MID)[0]).astype(np.float32)
d = np.abs(A - B).sum(2)
nzA = A.sum(2) > 12
print("AB_DIFF med=%.2f p99=%.2f mean=%.3f nonblackA=%.1f%% identical=%.2f%%" % (
    np.median(d[nzA]), np.percentile(d[nzA], 99), d[nzA].mean(),
    100.0 * nzA.mean(), 100.0 * (d < 1).mean()), flush=True)
print("AB_DONE", flush=True)
