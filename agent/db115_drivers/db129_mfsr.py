"""DB-129 map-MFSR experiment: 3 map variants (fine grid / median fusion / both) vs the v6 baseline,
rendered through fast wbev + composite v6 on the flagged frame, with source-distance diagnostics."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")
JOB = r'''
import glob, json, os, shutil, subprocess, time
import numpy as np, cv2

U = "05fa5048-f355-3274-b565-c0ddc547b315"
root = "/content/db128"
H, W = 1024, 2048
P = 41
MID = P + 46

def find1(pat):
    g = glob.glob(pat); assert g, pat
    return g[0]

# diagnostics rep: print the best-source distance distribution before the map is saved
DIAG = ['print("WORLDBEV2", run_name, "grid", _GW, "cw", _CW, "frames", len(_wfis),',
        'print("WBDIAG egod p10/50/90:", np.percentile(_wscore[0][np.isfinite(_wscore[0])], [10, 50, 90]).round(2).tolist(), "| slots_used med:", float(np.median(np.isfinite(_wscore).sum(0))), flush=True)\n        print("WORLDBEV2", run_name, "grid", _GW, "cw", _CW, "frames", len(_wfis),']
FUSE = ['_wmap = np.where(_conf[:, None], _col_conf, np.where(_anyv[:, None], _col_low, 0.0))',
        '_wmap = np.where(_anyv[:, None], np.nan_to_num(np.where(np.isnan(_wmed), 0.0, _wmed)), 0.0)   # DB-129 M-fuse: render the 6-slot MEDIAN (multi-frame fusion) instead of the single best source']
GRID = ['_MHALF, _CW = 46.0, 0.05',
        '_MHALF, _CW = 23.0, 0.025']

variants = {
    "m1_fine":  [GRID, DIAG],
    "m2_fuse+fine": [GRID, FUSE, DIAG],
    "m3_fuse": [FUSE, DIAG],
}
base_map = [
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, 156)"],
    ["_aidx))[:110])", "_aidx))[:60])"],
]
procs = {}
for name, reps in variants.items():
    od = root + "/map_" + name
    os.makedirs(od, exist_ok=True)
    extra = json.dumps(base_map + [list(r) for r in reps])
    lf = open(od + ".log", "w")
    procs[name] = subprocess.Popen(["python", "/content/db125_worker.py", "mx", str(MID), U, od, extra],
                                   stdout=lf, stderr=subprocess.STDOUT)
t0 = time.time()
rcs = {n: p.wait() for n, p in procs.items()}
print("MAPS built %s %.0fs" % (rcs, time.time() - t0), flush=True)
for name in variants:
    lg = open(root + "/map_" + name + ".log").read()
    for ln in lg.splitlines():
        if "WBDIAG" in ln or "WORLDBEV2 " in ln.split("FILLED")[0][:60]:
            print("[%s] %s" % (name, ln.strip()[:180]), flush=True)
    assert rcs[name] == 0, name + " failed: " + lg[-300:]
    shutil.copy(find1(root + "/map_%s/mx_a%03d_worldmap.png" % (name, MID)), root + "/wm_%s.png" % name)

# render fast wbev on 5 probe frames per variant, composite v6, crops vs baseline
CX, CY = [float(x) for x in open(root + "/center.txt").read().split(",")]
CAP_LIM = root + "/band/*/bg*_a%03d_egozone.png"
CAP_REF = root + "/band/*/bg*_a%03d_segcomposite.png"
PROBES = [50, 60, 80]   # band indices i (frame fr_%04d = i+1); aN = 42+i
K = 8
common3 = [
    ["CAP_ONLY = False", "CAP_ONLY = True"],
    ['CAP_LIMIT_TMPL = ""', 'CAP_LIMIT_TMPL = "' + CAP_LIM + '"'],
    ['CAP_REF_TMPL = ""', 'CAP_REF_TMPL = "' + CAP_REF + '"'],
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, 156)"],
    ['WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "%.6f,%.6f"' % (CX, CY)],
]
for name in variants:
    gridfix = [GRID] if "fine" in name else []
    extra = json.dumps(common3 + [list(r) for r in gridfix] +
                       [['WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "%s/wm_%s.png"' % (root, name)]])
    od = root + "/wb_" + name
    ps = []
    for j, i in enumerate(PROBES):
        aN = 42 + i
        os.makedirs("%s/m%d" % (od, j), exist_ok=True)
        lf = open("%s_m%d.log" % (od, j), "w")
        ps.append(subprocess.Popen(["python", "/content/db125_worker.py", "p%d" % j, str(aN), U,
                                    "%s/m%d" % (od, j), extra], stdout=lf, stderr=subprocess.STDOUT))
    rr = [p.wait() for p in ps]
    print("WB_%s rcs=%s" % (name, rr), flush=True)
    assert all(x == 0 for x in rr)

# composite v6 for each variant on probe frames + crops sheet vs current v6
sys_dirs = None
import numpy as _np
def compose(band, ez, fil, fai, wb):
    zone = ez > 127
    bnz = band.sum(2) >= 12
    lower = np.zeros((H, W), bool); lower[H // 2:] = True
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
    def spec_mask(img):
        hsv = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2].astype(np.float32); s = hsv[:, :, 1].astype(np.float32)
        return cv2.dilate(((v > 150) & (s < 70)).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    spec_f = spec_mask(fil); spec_w = spec_mask(wb)
    gray = cv2.cvtColor(fil.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    mu = cv2.boxFilter(lap, -1, (15, 15)); mu2 = cv2.boxFilter(lap * lap, -1, (15, 15))
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
    fb = cv2.blur(fil, (31, 31)); wbb = cv2.blur(wb, (31, 31))
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
    return out

DSTV6 = "/content/drive/MyDrive/koi_waymo2pano_colab/datasets/av2_1plus92_cascade_v1/05fa5048/v6_backup/frames"
rows = []
for j, i in enumerate(PROBES):
    aN = 42 + i
    band = cv2.imread(find1(root + "/band/m*/bg*_a%03d_segcomposite.png" % aN)).astype(np.float32)
    ez = cv2.imread(find1(root + "/band/m*/bg*_a%03d_egozone.png" % aN), 0)
    fil = cv2.imread(find1(root + "/fill/m*/ff*_a%03d_segcomposite.png" % aN)).astype(np.float32)
    fai = cv2.imread(find1(root + "/fill/m*/ff*_a%03d_faithfill_mask.png" % aN), 0)
    outs = [("v6-base", cv2.imread(DSTV6 + "/fr_%04d.png" % (i + 1)))]
    for name in variants:
        wb = cv2.imread(find1(root + "/wb_%s/m%d/p%d_a%03d_segcomposite.png" % (name, j, j, aN))).astype(np.float32)
        outs.append((name, compose(band, ez, fil, fai, wb)))
    for lab, x0, x1 in [("L", 0, 340), ("M", 860, 1290), ("R", 1700, 2048)]:
        tiles = []
        for nm, img in outs:
            c = img[520:760, x0:x1]
            c = cv2.resize(c, (430, 260))
            c = cv2.copyMakeBorder(c, 18, 2, 1, 1, cv2.BORDER_CONSTANT, value=(0, 0, 0))
            cv2.putText(c, "f%03d %s %s" % (i + 1, lab, nm), (4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 128), 1)
            tiles.append(c)
        rows.append(np.hstack(tiles))
sheet = np.vstack(rows)
cv2.imwrite("/content/db129_mfsr.jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
print("MFSR_DONE", flush=True)
'''
a.dr_launch("db129mfsr", JOB)
print("MFSR_LAUNCHED")
