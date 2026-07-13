"""DB-125 stage 2: frame-1 candidate render (fill + FAITH + imperfect) on legal positions, pick argmin."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")
JOB = r'''
import glob, json, os, subprocess, time
U = "02678d04-cc9f-3148-9f95-1ba66347dff9"
K = 8
s = json.load(open("/content/db125_bg_summary.json"))
lo, hi = s["clean_lo"], s["clean_hi"]
assert lo is not None and hi - lo + 1 >= 93, "clean run too short: %s" % s
legal = list(range(lo, hi - 92 + 1))
cand = legal[::max(1, len(legal) // 10)][:10]
print("CAND_POSITIONS", cand, flush=True)
extra = json.dumps([
    ["FAITH_MASK = False", "FAITH_MASK = True"],
    ["    capg = blackg.copy()", "    capg = blackg.copy(); capg |= egoproj.reshape(H, W)"],
    ['GROUND_RESID = "plate"', 'GROUND_RESID = "inpaint"'],
    ['"residual_inpaint_px": int(resid_m.sum()),',
     '"residual_inpaint_px": int(resid_m.sum()), "fg_occ_px": int(fg_occ.sum()), "nadir_imperfect_px": int((resid_m | fg_occ).sum()),'],
])
subs = [cand[j::K] for j in range(K)]
procs = []
t0 = time.time()
for j in range(K):
    if not subs[j]:
        continue
    od = "/content/db125_cand/m%d" % j
    os.makedirs(od, exist_ok=True)
    lf = open("/content/db125_cand_m%d.log" % j, "w")
    p = subprocess.Popen(["python", "/content/db125_worker.py", "cd%d" % j,
                          ",".join(str(x) for x in subs[j]), U, od, extra],
                         stdout=lf, stderr=subprocess.STDOUT)
    procs.append(p)
rcs = [p.wait() for p in procs]
print("CAND_RCS", rcs, "%.0fs" % (time.time() - t0), flush=True)
best = None
rows = []
for mf in glob.glob("/content/db125_cand/m*/manifest*.json"):
    m = json.load(open(mf))
    for c in m.get("cases", []):
        aN = int(c["case"].split("_a")[-1])
        gf = c.get("ground_stats") or c.get("ground_fill") or {}
        imp = gf.get("nadir_imperfect_px")
        if imp is None:
            imp = (gf.get("residual_inpaint_px") or 0) + (gf.get("fg_occ_px") or 0)
        rows.append((imp, aN, c["case"], mf))
rows.sort()
for imp, aN, case, mf in rows:
    print("CAND a%03d imperfect=%d case=%s" % (aN, imp, case), flush=True)
assert rows, "no candidates parsed"
imp, aN, case, mf = rows[0]
json.dump({"best_anchor": aN, "best_case": case, "imperfect": imp,
           "cand_dir": os.path.dirname(mf), "window": [aN, aN + 92]},
          open("/content/db125_cand_summary.json", "w"))
print("CAND_BEST a%03d imperfect=%d window=[%d,%d]" % (aN, imp, aN, aN + 92), flush=True)
print("CAND_DONE", flush=True)
'''
a.dr_launch("db125cand", JOB)
print("CAND_LAUNCHED")
