
import glob, json, os, subprocess, time
import numpy as np, cv2
def find1(pat):
    g = glob.glob(pat); assert g, pat
    return g[0]
U = "05fa5048-f355-3274-b565-c0ddc547b315"
root = "/content/db128"
H, W = 1024, 2048
P = 41
K = 8
CX, CY = [float(x) for x in open(root + "/center.txt").read().split(",")]
CAP_LIM = root + "/band/*/bg*_a%03d_egozone.png"
CAP_REF = root + "/band/*/bg*_a%03d_segcomposite.png"
extra = json.dumps([
    ["CAP_ONLY = False", "CAP_ONLY = True"],
    ['CAP_LIMIT_TMPL = ""', 'CAP_LIMIT_TMPL = "' + CAP_LIM + '"'],
    ['CAP_REF_TMPL = ""', 'CAP_REF_TMPL = "' + CAP_REF + '"'],
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
    ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, 156)"],
    ["_MHALF, _CW = 46.0, 0.05", "_MHALF, _CW = 23.0, 0.025"],
    ['WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "%s/wm_m2_fuse+fine.png"' % root],
    ['WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "%.6f,%.6f"' % (CX, CY)],
])
anchors = list(range(P + 1, P + 93))
subs = [anchors[j::K] for j in range(K)]
ps = []
t0 = time.time()
for j in range(K):
    od = root + "/wbev_m2/m%d" % j
    os.makedirs(od, exist_ok=True)
    lf = open(root + "/wbev_m2_w%d.log" % j, "w")
    ps.append(subprocess.Popen(["python", "/content/db125_worker.py", "q%d" % j,
                                ",".join(str(x) for x in subs[j]), U, od, extra],
                               stdout=lf, stderr=subprocess.STDOUT))
rcs = [p.wait() for p in ps]
print("WBEV_M2 rcs=%s %.0fs" % (rcs, time.time() - t0), flush=True)
assert all(x == 0 for x in rcs)
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


os.makedirs(root + "/c10/in", exist_ok=True)
os.makedirs(root + "/c10/mask", exist_ok=True)
st = []
t0 = time.time()
for i, aN in enumerate(range(P + 1, P + 93)):
    band = cv2.imread(find1(root + "/band/m*/bg*_a%03d_segcomposite.png" % aN)).astype(np.float32)
    ez = cv2.imread(find1(root + "/band/m*/bg*_a%03d_egozone.png" % aN), 0)
    fil = cv2.imread(find1(root + "/fill/m*/ff*_a%03d_segcomposite.png" % aN)).astype(np.float32)
    fai = cv2.imread(find1(root + "/fill/m*/ff*_a%03d_faithfill_mask.png" % aN), 0)
    wb = cv2.imread(find1(root + "/wbev_m2/m*/q*_a%03d_segcomposite.png" % aN)).astype(np.float32)
    out = compose(band, ez, fil, fai, wb)
    cv2.imwrite(root + "/c10/in/%05d.png" % i, out)
print("V10_COMP %.0fs" % (time.time() - t0), flush=True)
# resid masks were baked with Telea inside compose(); recompute resid for PP from black-in-managed:
# compose() already Telea-fills resid, so v10 uses compose output directly (PP not needed at frame level).
DST = "/content/drive/MyDrive/koi_waymo2pano_colab/datasets/av2_1plus92_cascade_v1/05fa5048"
for i in range(92):
    f = cv2.imread(root + "/c10/in/%05d.png" % i)
    cv2.imwrite(DST + "/frames/fr_%04d.png" % (i + 1), f)
    cv2.imwrite(DST + "/masks/mk_%04d.png" % (i + 1), (f.astype(np.int32).sum(2) >= 12).astype(np.uint8) * 255)
os.makedirs(root + "/v10pack", exist_ok=True)
subprocess.run("cp '%s/frames/fr_0000.png' %s/v10pack/fr_0000.png" % (DST, root), shell=True)
for i in range(92):
    subprocess.run("cp %s/c10/in/%05d.png %s/v10pack/fr_%04d.png" % (root, i, root, i + 1), shell=True)
subprocess.run("ffmpeg -y -loglevel error -framerate 10 -i %s/v10pack/fr_%%04d.png -c:v libx264 -pix_fmt yuv420p /content/clip_v10.mp4" % root, shell=True, timeout=600)
subprocess.run("cp /content/clip_v10.mp4 '%s/clip_05fa5048_1plus92.mp4'" % DST, shell=True)
subprocess.run("cp /content/clip_v10.mp4 '%s/clip_v10_mfsr.mp4'" % DST, shell=True)
subprocess.run("cp '%s/wm_m2_fuse+fine.png' '%s/worldmap_v10_m2.png'" % (root, DST), shell=True)
print("V10_PACKAGED", flush=True)
