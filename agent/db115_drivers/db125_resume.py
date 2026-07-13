"""DB-125 rescue: kill CPU fill, re-render missing frames with GROUND_TORCH=True, resume pipeline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")

# ---- kill orchestrator + CPU fill workers + finished map worker ----
r = a._exec("pkill -f '_dj_db125all.py'; pkill -f 'db125_worker.py f'; pkill -f 'db125_worker.py mp'; sleep 2; "
            "ps aux | grep db125_worker | grep -v grep | wc -l; echo KILLED", 60)
print((r.get("log_tail") or "").encode("ascii", "replace").decode("ascii"))

# ---- GPU fill for missing frames ----
JOB_FILL = r'''
import glob, json, os, subprocess, time
U = "02678d04-cc9f-3148-9f95-1ba66347dff9"
K = 8
have = set()
for p in glob.glob("/content/db125_fill/m*/*_segcomposite.png"):
    have.add(int(p.split("_a")[-1].split("_")[0]))
need = [x for x in range(43, 135) if x not in have]
print("GF_NEED %d frames: %s..%s" % (len(need), need[0] if need else "-", need[-1] if need else "-"), flush=True)
extra = json.dumps([
    ["FAITH_MASK = False", "FAITH_MASK = True"],
    ["    capg = blackg.copy()", "    capg = blackg.copy(); capg |= egoproj.reshape(H, W)"],
    ['GROUND_RESID = "plate"', 'GROUND_RESID = "inpaint"'],
    ["GROUND_TORCH = False", "GROUND_TORCH = True"],
])
subs = [need[j::K] for j in range(K)]
procs = []
t0 = time.time()
for j in range(K):
    if not subs[j]:
        continue
    od = "/content/db125_fill/g%d" % j
    os.makedirs(od, exist_ok=True)
    lf = open("/content/db125_gfill_m%d.log" % j, "w")
    procs.append(subprocess.Popen(["python", "/content/db125_worker.py", "gf%d" % j,
                                   ",".join(str(x) for x in subs[j]), U, od, extra],
                                  stdout=lf, stderr=subprocess.STDOUT))
rcs = [p.wait() for p in procs]
t = time.time() - t0
print("GF_RCS %s %.0fs (%.1fs/frame)" % (rcs, t, t / max(len(need), 1)), flush=True)
assert all(x == 0 for x in rcs), "gpu fill failed"
print("GF_DONE", flush=True)
'''
a.dr_launch("db125gfill", JOB_FILL)
print("GFILL_LAUNCHED")
