"""Cascade finish: wait for wbc render -> Tier2 composite -> Tier3 ProPainter -> mp4 + cmp sheet + ledger."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")
JOB = r'''
import glob, os, subprocess, time
import numpy as np, cv2

# ---- wait for the wbc render to finish (poll its log) ----
t0 = time.time()
while time.time() - t0 < 3600:
    log = open("/content/_dj_wbcasc.log").read() if os.path.exists("/content/_dj_wbcasc.log") else ""
    if "WBC_ALL_DONE" in log or "Traceback" in log:
        break
    time.sleep(20)
assert "WBC_ALL_DONE" in log, "wbc render failed or timed out"
import re
m = re.search(r"WBC_ALL_DONE (\d+)s", log)
T_RENDER = int(m.group(1))
print("CF_RENDER %ds for 31 frames = %.1fs/frame" % (T_RENDER, T_RENDER / 31.0), flush=True)

# ---- Tier2 composite: paste wbev pixels into the vi_mask holes ----
os.makedirs("/content/casc_in", exist_ok=True)
os.makedirs("/content/casc_mask", exist_ok=True)
stats = []
wb_frames = []
for i in range(31):
    aN = 110 + i
    base = cv2.imread("/content/vi_in/%05d.png" % i, cv2.IMREAD_COLOR)
    hole = cv2.imread("/content/vi_mask/%05d.png" % i, 0) > 127
    wcands = glob.glob("/content/wbc_out/**/c_a%d_segcomposite.png" % aN, recursive=True)
    assert wcands, "missing wbev frame c_a%d" % aN
    wb = cv2.imread(wcands[0], cv2.IMREAD_COLOR)
    assert wb.shape == base.shape, "shape mismatch %s vs %s" % (wb.shape, base.shape)
    wb_frames.append(wcands[0])
    valid = wb.astype(np.int32).sum(2) >= 12
    fill = hole & valid
    resid = hole & ~valid
    out = base.copy()
    out[fill] = wb[fill]
    cv2.imwrite("/content/casc_in/%05d.png" % i, out)
    cv2.imwrite("/content/casc_mask/%05d.png" % i, resid.astype(np.uint8) * 255)
    stats.append((int(hole.sum()), int(fill.sum()), int(resid.sum())))
th = sum(s[0] for s in stats); tf = sum(s[1] for s in stats); tr = sum(s[2] for s in stats)
print("CF_COMPOSITE holes=%d wbev_filled=%d (%.1f%%) residual=%d (%.1f%%)" % (
    th, tf, 100.0 * tf / max(th, 1), tr, 100.0 * tr / max(th, 1)), flush=True)

# ---- Tier3: ProPainter on the residual (skip if nothing left) ----
if tr > 0:
    t0 = time.time()
    r = subprocess.run(["python", "inference_propainter.py",
                        "--video", "/content/casc_in", "--mask", "/content/casc_mask",
                        "--output", "/content/casc_out", "--fp16",
                        "--width", "2048", "--height", "1024"],
                       capture_output=True, text=True, cwd="/content/ProPainter", timeout=3000)
    print("CF_PP rc=%d %.0fs" % (r.returncode, time.time() - t0), flush=True)
    if r.returncode != 0:
        print("CF_PP_ERR", "\n".join((r.stderr or "").splitlines()[-12:]), flush=True)
    cap = cv2.VideoCapture("/content/casc_out/casc_in/inpaint_out.mp4")
    finals = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        finals.append(f)
    cap.release()
    assert len(finals) == 31, "PP returned %d frames" % len(finals)
else:
    print("CF_PP skipped (zero residual)", flush=True)
    finals = [cv2.imread("/content/casc_in/%05d.png" % i, cv2.IMREAD_COLOR) for i in range(31)]

# ---- final mp4 ----
H, W = finals[0].shape[:2]
vw = cv2.VideoWriter("/content/cascade_final.mp4", cv2.VideoWriter_fourcc(*"mp4v"), 10, (W, H))
for f in finals:
    vw.write(f)
vw.release()
DRIVE = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db115pro/db123"
os.system("ffmpeg -y -loglevel error -i /content/cascade_final.mp4 -c:v libx264 -pix_fmt yuv420p '%s/cascade_final_cd22abca.mp4'" % DRIVE)

# ---- comparison sheet at f15 + temporal strip ----
i15 = 15
inp = cv2.imread("/content/vi_in/%05d.png" % i15, cv2.IMREAD_COLOR)
comp15 = cv2.imread("/content/casc_in/%05d.png" % i15, cv2.IMREAD_COLOR)
fin15 = finals[i15]
cap = cv2.VideoCapture("/content/vi_out/vi_in/inpaint_out.mp4")
pp_only = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    pp_only.append(f)
cap.release()
pp15 = pp_only[i15]
Hf, Wf = inp.shape[:2]
rows = []
for name, o in [("input_gated(holes)", inp), ("cascade_final(Tier1+2+3)", fin15)]:
    c = o[330:700, :]
    c = cv2.resize(c, (1800, int(c.shape[0] * 1800 / Wf)))
    c = cv2.copyMakeBorder(c, 22, 4, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    cv2.putText(c, "a125 " + name, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 128), 1)
    rows.append(c)
msk = cv2.imread("/content/vi_mask/%05d.png" % i15, 0)
ys, xs = np.where(msk > 127)
order = np.argsort(xs); xs_s = xs[order]
splits = np.where(np.diff(xs_s) > 150)[0]
groups = np.split(np.arange(len(xs_s)), splits + 1)
for g in groups[:3]:
    gx = xs_s[g]
    x0, x1 = max(0, int(gx.min()) - 60), min(Wf, int(gx.max()) + 60)
    if x1 - x0 < 200:
        x0, x1 = max(0, x0 - 80), min(Wf, x1 + 80)
    tiles = []
    for name, o in [("hole", inp), ("Tier2 wbev", comp15), ("final", fin15), ("PP-only", pp15)]:
        c = o[470:700, x0:x1]
        c = cv2.resize(c, (620, int(c.shape[0] * 620 / c.shape[1])))
        c = cv2.copyMakeBorder(c, 20, 4, 2, 2, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        cv2.putText(c, "%s x%d-%d" % (name, x0, x1), (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 128), 1)
        tiles.append(c)
    rows.append(np.hstack(tiles))
# temporal strip: same crop at f5/f15/f25 of the final
gx = xs_s
x0, x1 = max(0, int(gx.min()) - 60), min(Wf, int(gx.min()) + 700)
tiles = []
for fi in (5, 15, 25):
    c = finals[fi][470:700, x0:x1]
    c = cv2.resize(c, (820, int(c.shape[0] * 820 / c.shape[1])))
    c = cv2.copyMakeBorder(c, 20, 4, 2, 2, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    cv2.putText(c, "final f%02d (temporal)" % fi, (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 128), 1)
    tiles.append(c)
rows.append(np.hstack(tiles))
ww = max(r_.shape[1] for r_ in rows)
sheet = np.vstack([np.pad(r_, ((0, 0), (0, ww - r_.shape[1]), (0, 0))) for r_ in rows])
cv2.imwrite("/content/cascade_cmp.jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
cv2.imwrite(DRIVE + "/cascade_cmp_cd22abca.jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
print("CF_LEDGER map_build=990s(once,amortised) render=%.1fs/frame pp=Tier3 total_92f_log~%.0fmin" % (
    T_RENDER / 31.0, (990 + 92 * T_RENDER / 31.0 + 67 * 3) / 60.0), flush=True)
print("CF_DONE", flush=True)
'''
a.dr_launch("cascfin", JOB)
print("CASCFIN_LAUNCHED")
