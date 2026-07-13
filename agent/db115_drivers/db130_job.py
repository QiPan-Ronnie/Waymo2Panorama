import glob
import json
import os
import shutil
import subprocess
import time

import numpy as np
import cv2

K = 24
H, W = 1024, 2048
DRIVE = "/content/drive/MyDrive/koi_waymo2pano_colab"
ROOTB = "/content/db130"
USED = ["02678d04", "02a00399", "04994d08", "05fa5048", "070bbf42", "0bae3b5e", "2c652f9e",
        "8749f79f", "cd22abca", "9239d493", "d1695c5e", "c062ba0f", "20dd185d", "3bffdcff",
        "dfc32963", "ff0dbfc5", "6803a04a", "6803104a"]
LED = {"gpu": "RTX PRO 6000 Blackwell 96GB", "ncpu": 48, "K": K, "logs": {}}


def led(u8, k, v):
    LED["logs"].setdefault(u8, {})[k] = v
    json.dump(LED, open("/content/db130_ledger.json", "w"), indent=1)
    print("LED[%s] %s=%s" % (u8, k, v), flush=True)


def find1(pat):
    g = glob.glob(pat)
    assert g, pat
    return g[0]


def fan(tag, anchors, extra, root, uuid, k=K):
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
    return [p.wait() for p in procs]


# ---------- tools ----------
t0 = time.time()
subprocess.run("pip install -q s5cmd einops av ftfy sentencepiece protobuf 'diffusers==0.38.0' 'transformers==4.56.2' accelerate", shell=True, timeout=1500)
import torch
assert torch.__version__.startswith("2.11"), "torch got downgraded: " + torch.__version__
subprocess.run("git clone -q https://github.com/sczhou/ProPainter /content/ProPainter && mkdir -p /content/ProPainter/weights", shell=True, timeout=600)
for f in ["ProPainter.pth", "recurrent_flow_completion.pth", "raft-things.pth"]:
    subprocess.run("wget -q https://github.com/sczhou/ProPainter/releases/download/v0.1.0/%s -O /content/ProPainter/weights/%s" % (f, f), shell=True, timeout=600)
print("TOOLS %.0fs" % (time.time() - t0), flush=True)

r = subprocess.run("s5cmd --no-sign-request ls s3://argoverse/datasets/av2/sensor/val/", shell=True, capture_output=True, text=True, timeout=300)
cands = []
for ln in r.stdout.splitlines():
    u = ln.strip().split()[-1].rstrip("/")
    if len(u) > 30 and not any(u.startswith(p) for p in USED):
        cands.append(u)
print("QUEUE %d candidates, first: %s" % (len(cands), [c[:8] for c in cands[:6]]), flush=True)

GRID = ["_MHALF, _CW = 46.0, 0.05", "_MHALF, _CW = 23.0, 0.025"]
FUSE = ["_wmap = np.where(_conf[:, None], _col_conf, np.where(_anyv[:, None], _col_low, 0.0))",
        "_wmap = np.where(_anyv[:, None], np.nan_to_num(np.where(np.isnan(_wmed), 0.0, _wmed)), 0.0)"]

# ---------- v10 compose (module-level for multiprocessing) ----------
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

    def spec_mask(img):
        hsv = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2].astype(np.float32)
        s = hsv[:, :, 1].astype(np.float32)
        return cv2.dilate(((v > 150) & (s < 70)).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0

    spec_f = spec_mask(fil)
    spec_w = spec_mask(wb)
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


# ---------- main queue ----------
import sys
sys.path.insert(0, "/content")
sys.path.insert(0, "/content/w2p_ego/scripts/phase3")
sys.path.insert(0, "/content/w2p_ego/code")
from pathlib import Path
from db123_egomask import save_ego_mask_npz
import pandas as pd

done = []
flux_queue = []
prefetch = None
ci = 0
while len(done) < 2 and ci < len(cands):
    U = cands[ci]
    ci += 1
    U8 = U[:8]
    root = ROOTB + "_" + U8
    t_log = time.time()
    try:
        t0 = time.time()
        if prefetch and prefetch[0] == U:
            prefetch[1].wait()
        else:
            subprocess.run("s5cmd --no-sign-request cp 's3://argoverse/datasets/av2/sensor/val/%s/*' /content/localav2/val/%s/" % (U, U), shell=True, timeout=3600)
        if ci < len(cands):
            prefetch = (cands[ci], subprocess.Popen("s5cmd --no-sign-request cp 's3://argoverse/datasets/av2/sensor/val/%s/*' /content/localav2/val/%s/" % (cands[ci], cands[ci]), shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        n = len(glob.glob("/content/localav2/val/%s/sensors/lidar/*.feather" % U))
        led(U8, "localize_s", round(time.time() - t0))
        led(U8, "n_frames", n)
        assert n > 100
        save_ego_mask_npz(Path("/content/localav2/val/" + U), "/content/egomask_cur.npz")
        os.makedirs(root, exist_ok=True)
        # band-gate
        t0 = time.time()
        extra_bg = json.dumps([["GROUND_MODE = \"fill\"", "GROUND_MODE = \"off\""],
                               ["EGO_BLACK = False", "EGO_BLACK = True"]])
        rcs = fan("bg", list(range(0, n)), extra_bg, root + "/band", U)
        assert all(x == 0 for x in rcs), "band failed"
        led(U8, "band_s", round(time.time() - t0))
        reg = {}
        for mf in glob.glob(root + "/band/m*/manifest*.json"):
            m = json.load(open(mf))
            for c in m.get("cases", []):
                aN = int(c["case"].split("_a")[-1])
                vm = c.get("view_morph") or {}
                vals = [v.get("max_reg_px", 0.0) for v in (vm.values() if isinstance(vm, dict) else vm)]
                reg[aN] = max([0.0] + vals)
        clean = sorted(x for x, v in reg.items() if v <= 8.0)
        best = (None, None)
        lo = prev = None
        for x in clean + [None]:
            if lo is None:
                lo = x
            elif x is None or x != prev + 1:
                if prev is not None and (best[0] is None or prev - lo > best[1] - best[0]):
                    best = (lo, prev)
                lo = x
            prev = x if x is not None else prev
        run_len = (best[1] - best[0] + 1) if best[0] is not None else 0
        led(U8, "clean_run", [best[0], best[1], run_len])
        if run_len < 93:
            led(U8, "verdict", "SKIP")
            shutil.rmtree("/content/localav2/val/" + U, ignore_errors=True)
            continue
        # cand
        t0 = time.time()
        legal = list(range(best[0], best[1] - 92 + 1))
        cand = legal[::max(1, len(legal) // 10)][:10]
        extra_cd = json.dumps([
            ["FAITH_MASK = False", "FAITH_MASK = True"],
            ["    capg = blackg.copy()", "    capg = blackg.copy(); capg |= egoproj.reshape(H, W)"],
            ['GROUND_RESID = "plate"', 'GROUND_RESID = "inpaint"'],
            ['"residual_inpaint_px": int(resid_m.sum()),',
             '"residual_inpaint_px": int(resid_m.sum()), "fg_occ_px": int(fg_occ.sum()), "nadir_imperfect_px": int((resid_m | fg_occ).sum()),'],
            ["GROUND_TORCH = False", "GROUND_TORCH = True"],
        ])
        rcs = fan("cd", cand, extra_cd, root + "/cand", U)
        rows = []
        for mf in glob.glob(root + "/cand/m*/manifest*.json"):
            m = json.load(open(mf))
            for c in m.get("cases", []):
                aN = int(c["case"].split("_a")[-1])
                gf = c.get("ground_stats") or c.get("ground_fill") or {}
                imp = gf.get("nadir_imperfect_px")
                if imp is None:
                    imp = (gf.get("residual_inpaint_px") or 0) + (gf.get("fg_occ_px") or 0)
                rows.append((imp, aN, c["case"], os.path.dirname(mf)))
        rows.sort()
        imp, P, SC, CDIR = rows[0]
        led(U8, "cand_s", round(time.time() - t0))
        led(U8, "frame1", [P, imp])
        MID = P + 46
        # map-M2 || fill
        t0 = time.time()
        extra_map = json.dumps([
            ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
            ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % n],
            ["_aidx))[:110])", "_aidx))[:60])"],
            GRID, FUSE,
        ])
        os.makedirs(root + "/map", exist_ok=True)
        mlf = open(root + "/map.log", "w")
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
        rcs = fan("ff", list(range(P + 1, P + 93)), extra_ff, root + "/fill", U)
        assert all(x == 0 for x in rcs), "fill failed"
        led(U8, "fill_s", round(time.time() - t0))
        mrc = mproc.wait()
        led(U8, "map_s", round(time.time() - t0))
        assert mrc == 0, "map failed: " + open(root + "/map.log").read()[-300:]
        shutil.copy(find1(root + "/map/mp_a%03d_worldmap.png" % MID), root + "/worldmap.png")
        # centre
        from waymo2panorama.data_io.av2_loader import AV2RingLoader
        LOGD = Path("/content/localav2/val/" + U)
        loader = AV2RingLoader(LOGD)
        ts_mid = loader.anchor_timestamps_ns()[MID]
        pf = pd.read_feather(LOGD / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
        ti = pf["timestamp_ns"].to_numpy(np.int64)
        tt0 = int(ti[0])
        tss = (ti - tt0).astype(np.float64)
        tx = pf[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
        keep = np.concatenate([[True], np.diff(tss) > 0])
        tss, tx = tss[keep], tx[keep]
        tc = float(np.clip(float(int(ts_mid) - tt0), tss.min(), tss.max()))
        CX, CY = (float(np.interp(tc, tss, tx[:, i2])) for i2 in range(2))
        # wbev fast (m2 map)
        t0 = time.time()
        extra_wf = json.dumps(common + [
            ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
            ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % n],
            GRID,
            ['WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "%s/worldmap.png"' % root],
            ['WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "%.6f,%.6f"' % (CX, CY)],
        ])
        rcs = fan("q", list(range(P + 1, P + 93)), extra_wf, root + "/wbev", U)
        assert all(x == 0 for x in rcs), "wbev failed"
        led(U8, "wbev_s", round(time.time() - t0))
        # compose v10 (parallel)
        t0 = time.time()
        os.makedirs(root + "/c10/in", exist_ok=True)
        from multiprocessing import Pool
        with Pool(16) as pool:
            st = pool.map(compose_one, [(root, i2, P + 1 + i2) for i2 in range(92)])
        tz = sum(s[0] for s in st)
        led(U8, "compose_s", round(time.time() - t0))
        led(U8, "cascade", {"zone2": tz, "t1pct": round(100.0 * sum(s[1] for s in st) / tz, 1),
                            "t2pct": round(100.0 * sum(s[2] for s in st) / tz, 1),
                            "residpct": round(100.0 * sum(s[3] for s in st) / tz, 2)})
        led(U8, "prep_total_s", round(time.time() - t_log))
        flux_queue.append((U8, U, SC, CDIR, root, P))
        done.append(U8)
        led(U8, "verdict", "OK")
        shutil.rmtree("/content/localav2/val/" + U, ignore_errors=True)
    except Exception as e:
        led(U8, "verdict", "FAIL: %s" % str(e)[:180])
        continue

# ---------- batched FLUX + package ----------
if flux_queue:
    os.environ["HF_HOME"] = DRIVE + "/cache/huggingface"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from PIL import Image
    from diffusers import FluxFillPipeline
    t0 = time.time()
    pipe = FluxFillPipeline.from_pretrained("black-forest-labs/FLUX.1-Fill-dev", torch_dtype=torch.bfloat16).to("cuda")
    if hasattr(pipe.vae, "enable_tiling"):
        pipe.vae.enable_tiling()
    LED["flux_load_s"] = round(time.time() - t0)
    print("FLUX load %.0fs" % (time.time() - t0), flush=True)
    for U8, U, SC, CDIR, root, P in flux_queue:
        try:
            t0 = time.time()
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
            keep = ~(gm > 127)
            v8[keep] = v7[keep]
            frame1 = cv2.cvtColor(v8, cv2.COLOR_RGB2BGR)
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
            tiles = []
            for idx, lb in [(0, "frame1_v8"), (1, "f001"), (46, "f046"), (92, "f092")]:
                fr = cv2.imread(out + "/frames/fr_%04d.png" % idx)
                c = cv2.resize(fr, (1400, 700))
                c = cv2.copyMakeBorder(c, 24, 4, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
                cv2.putText(c, U8 + " " + lb, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 128), 1)
                tiles.append(c)
            cv2.imwrite(out + "/sample_sheet.jpg", np.vstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 90])
            DST = DRIVE + "/datasets/av2_1plus92_cascade_v1/" + U8
            os.makedirs(DST, exist_ok=True)
            json.dump(LED["logs"][U8], open(out + "/ledger.json", "w"), indent=1)
            shutil.copy(root + "/worldmap.png", out + "/worldmap_m2.png")
            subprocess.run("cp -r %s/* '%s/'" % (out, DST), shell=True, timeout=1800)
            led(U8, "flux_pack_s", round(time.time() - t0))
            led(U8, "packaged", True)
        except Exception as e:
            led(U8, "packaged", "FAIL: %s" % str(e)[:150])
subprocess.run("cp /content/db130_ledger.json '%s/datasets/av2_1plus92_cascade_v1/db130_ledger.json'" % DRIVE, shell=True)
print("G_LEDGER", json.dumps(LED), flush=True)
print("DB130_ALL_DONE", flush=True)
