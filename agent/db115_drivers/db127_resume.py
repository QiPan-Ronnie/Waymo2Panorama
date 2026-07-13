"""DB-127 resume: fixed-import centre calc -> fast wbev -> composite v3 -> PP -> batched FLUX -> package,
for the logs whose render layers completed before the import-order bug."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dr2  # noqa: E402

a = dr2.get("a100")
JOB = r'''
import glob, json, os, shutil, subprocess, time
import numpy as np, cv2
import sys as _s
_s.path.insert(0, "/content/w2p_ego/scripts/phase3")
_s.path.insert(0, "/content/w2p_ego/code")
_s.path.insert(0, "/content")

K = 12
DRIVE = "/content/drive/MyDrive/koi_waymo2pano_colab"
H, W = 1024, 2048
REPORT = json.load(open("/content/db127_report.json")) if os.path.exists("/content/db127_report.json") else {}

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

import pandas as pd
from pathlib import Path
from waymo2panorama.data_io.av2_loader import AV2RingLoader

LOGS = ["05fa5048-f355-3274-b565-c0ddc547b315", "070bbf42-31d3-3aa9-aca4-c262afc9077d"]
flux_queue = []
for U in LOGS:
    U8 = U[:8]
    R = REPORT.get(U8, {"uuid": U})
    root = "/content/db127_%s" % U8
    try:
        if not R.get("frame1"):
            print("G2[%s] no cand result recorded, skip" % U8, flush=True)
            continue
        P = R["frame1"][0]
        MID = P + 46
        CDIR = os.path.dirname(find1(root + "/cand/m*/cd*_a%03d_segcomposite.png" % P))
        SC = os.path.basename(find1(root + "/cand/m*/cd*_a%03d_segcomposite.png" % P)).replace("_segcomposite.png", "")
        # ensure map/worldmap staged
        wm = glob.glob(root + "/worldmap.png") or glob.glob(root + "/map/mp_a%03d_worldmap.png" % MID)
        assert wm, "worldmap missing for " + U8
        if not os.path.exists(root + "/worldmap.png"):
            shutil.copy(wm[0], root + "/worldmap.png")
        # centre (FIXED import order)
        LOGD = Path("/content/localav2/val/" + U)
        loader = AV2RingLoader(LOGD)
        ts_mid = loader.anchor_timestamps_ns()[MID]
        pf = pd.read_feather(LOGD / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
        ti = pf["timestamp_ns"].to_numpy(np.int64); tt0 = int(ti[0]); tss = (ti - tt0).astype(np.float64)
        tx = pf[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
        keep = np.concatenate([[True], np.diff(tss) > 0]); tss, tx = tss[keep], tx[keep]
        tc = float(np.clip(float(int(ts_mid) - tt0), tss.min(), tss.max()))
        CX, CY = (float(np.interp(tc, tss, tx[:, i])) for i in range(2))
        n = len(glob.glob("/content/localav2/val/%s/sensors/lidar/*.feather" % U))
        # egomask must match THIS log for the wbev workers
        from db123_egomask import save_ego_mask_npz
        save_ego_mask_npz(LOGD, "/content/egomask_cur.npz")
        # fast wbev (skip if already done)
        CAP_LIM = root + "/band/*/bg*_a%03d_egozone.png"
        CAP_REF = root + "/band/*/bg*_a%03d_segcomposite.png"
        common = [
            ["CAP_ONLY = False", "CAP_ONLY = True"],
            ['CAP_LIMIT_TMPL = ""', 'CAP_LIMIT_TMPL = "' + CAP_LIM + '"'],
            ['CAP_REF_TMPL = ""', 'CAP_REF_TMPL = "' + CAP_REF + '"'],
        ]
        have_wb = len(glob.glob(root + "/wbev/m*/wf*_segcomposite.png"))
        if have_wb < 92:
            t0 = time.time()
            extra_wf = json.dumps(common + [
                ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
                ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % n],
                ['WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "%s/worldmap.png"' % root],
                ['WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "%.6f,%.6f"' % (CX, CY)],
            ])
            rcs = fan("wf", list(range(P + 1, P + 93)), extra_wf, root + "/wbev", U)
            assert all(x == 0 for x in rcs), "wbev failed"
            R["wbev_s"] = round(time.time() - t0)
            print("G2[%s] wbev %.0fs" % (U8, R["wbev_s"]), flush=True)
        # composite v3
        t0 = time.time()
        os.makedirs(root + "/casc/in", exist_ok=True)
        os.makedirs(root + "/casc/mask", exist_ok=True)
        K21 = np.ones((21, 21), np.uint8)
        st = []
        for i, aN in enumerate(range(P + 1, P + 93)):
            band = cv2.imread(find1(root + "/band/m*/bg*_a%03d_segcomposite.png" % aN)).astype(np.float32)
            ez = cv2.imread(find1(root + "/band/m*/bg*_a%03d_egozone.png" % aN), 0)
            fil = cv2.imread(find1(root + "/fill/m*/ff*_a%03d_segcomposite.png" % aN)).astype(np.float32)
            fai = cv2.imread(find1(root + "/fill/m*/ff*_a%03d_faithfill_mask.png" % aN), 0)
            wb = cv2.imread(find1(root + "/wbev/m*/wf*_a%03d_segcomposite.png" % aN)).astype(np.float32)
            zone = ez > 127
            bnz = band.sum(2) >= 12
            nz = fil.sum(2) >= 12
            faith = fai > 127
            wbok = wb.sum(2) >= 12
            gray = cv2.cvtColor(fil.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
            lap = cv2.Laplacian(gray, cv2.CV_32F)
            mu = cv2.boxFilter(lap, -1, (15, 15)); mu2 = cv2.boxFilter(lap * lap, -1, (15, 15))
            sharp = (mu2 - mu * mu) > 40.0
            k7 = np.ones((7, 7), np.uint8)
            sharp = cv2.morphologyEx(sharp.astype(np.uint8), cv2.MORPH_OPEN, k7)
            sharp = cv2.morphologyEx(sharp, cv2.MORPH_CLOSE, k7) > 0
            ok = zone & nz & ~faith & sharp
            nl, lab = cv2.connectedComponents(ok.astype(np.uint8))
            if nl > 1:
                cnt = np.bincount(lab.ravel()); small = np.isin(lab, np.nonzero(cnt < 400)[0][1:])
                ok &= ~small
            fb = cv2.blur(fil, (31, 31)); wbb = cv2.blur(wb, (31, 31))
            dcol = np.abs(fb - wbb).sum(2)
            bad = ok & wbok & (dcol > 45.0)
            ok &= ~bad
            hole1 = zone & ~ok
            t2 = hole1 & wbok
            resid = hole1 & ~wbok
            ring = (cv2.dilate(zone.astype(np.uint8), K21) > 0) & ~zone & bnz
            fil_a, wb_a = fil.copy(), wb.copy()
            if ring.sum() > 500:
                bmed = np.median(band[ring], axis=0)
                for src, m in [(fil_a, ok), (wb_a, t2)]:
                    if m.sum() > 200:
                        smed = np.median(src[m], axis=0)
                        g = np.clip(bmed / np.maximum(smed, 8.0), 0.8, 1.3)
                        src[m] = np.clip(src[m] * g[None, :], 0, 255)
            tempo = band.copy()
            tempo[ok] = fil_a[ok]
            tempo[t2] = wb_a[t2]
            filled = (ok | t2)
            edge = filled & (cv2.dilate((bnz & ~zone).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
            edge |= (bnz & ~zone) & (cv2.dilate(filled.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
            if edge.any():
                tb = cv2.GaussianBlur(tempo, (9, 9), 0)
                tempo[edge] = tb[edge]
            out = np.clip(tempo, 0, 255).astype(np.uint8)
            out[~(zone | bnz)] = 0
            cv2.imwrite(root + "/casc/in/%05d.png" % i, out)
            cv2.imwrite(root + "/casc/mask/%05d.png" % i, resid.astype(np.uint8) * 255)
            st.append((int(zone.sum()), int(ok.sum()), int(t2.sum()), int(resid.sum())))
        tz = sum(s[0] for s in st); o = sum(s[1] for s in st); t2s = sum(s[2] for s in st); rs = sum(s[3] for s in st)
        R["cascade"] = {"zone": tz, "t1pct": round(100.0 * o / max(tz, 1), 1),
                        "t2pct": round(100.0 * t2s / max(tz, 1), 1), "resid": rs}
        print("G2[%s] cascade zone=%d t1=%.1f%% t2=%.1f%% resid=%d" % (U8, tz, 100.0 * o / max(tz, 1), 100.0 * t2s / max(tz, 1), rs), flush=True)
        if rs > 0:
            t0 = time.time()
            r = subprocess.run(["python", "inference_propainter.py", "--video", root + "/casc/in",
                                "--mask", root + "/casc/mask", "--output", root + "/casc/out",
                                "--fp16", "--width", "2048", "--height", "1024"],
                               capture_output=True, text=True, cwd="/content/ProPainter", timeout=3600)
            R["pp_s"] = round(time.time() - t0)
            assert r.returncode == 0, "PP failed: " + (r.stderr or "")[-200:]
            cap = cv2.VideoCapture(root + "/casc/out/in/inpaint_out.mp4")
            fi = 0
            while True:
                okf, f = cap.read()
                if not okf:
                    break
                m = cv2.imread(root + "/casc/mask/%05d.png" % fi, 0) > 127
                if m.any():
                    base = cv2.imread(root + "/casc/in/%05d.png" % fi)
                    base[m] = f[m]
                    cv2.imwrite(root + "/casc/in/%05d.png" % fi, base)
                fi += 1
            print("G2[%s] PP %.0fs on %d px" % (U8, R["pp_s"], rs), flush=True)
        flux_queue.append((U8, SC, CDIR, root, P))
        R["verdict"] = "OK"
    except Exception as e:
        R["verdict"] = "FAIL2: %s" % str(e)[:200]
        print("G2[%s] FAIL %s" % (U8, str(e)[:200]), flush=True)
    REPORT[U8] = R
    json.dump(REPORT, open("/content/db127_report.json", "w"), indent=1)

if flux_queue:
    os.environ["HF_HOME"] = DRIVE + "/cache/huggingface"
    os.environ["HF_HUB_OFFLINE"] = "1"; os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from PIL import Image
    from diffusers import FluxFillPipeline
    t0 = time.time()
    pipe = FluxFillPipeline.from_pretrained("black-forest-labs/FLUX.1-Fill-dev", torch_dtype=torch.bfloat16).to("cuda")
    if hasattr(pipe.vae, "enable_tiling"):
        pipe.vae.enable_tiling()
    print("G2 FLUX load %.0fs" % (time.time() - t0), flush=True)
    for U8, SC, CDIR, root, P in flux_queue:
        try:
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
            ba = np.array(band_px); br = ba[ba.mean(1) > 100]
            m = br.mean(0) if len(br) else ba.mean(0)
            if m[0] > m[2] + 8:
                prompt = "warm dusk sky with soft orange glow and scattered clouds, natural evening light, seamless panorama sky"
            elif m.mean() > 150 and m[2] > m[0] + 10:
                prompt = "clear sunny blue sky with scattered white cumulus clouds, seamless photographic panorama sky"
            else:
                prompt = "pale softly overcast sky with diffuse natural light, seamless panorama sky"
            mask = cv2.dilate(cap.astype(np.uint8) * 255, np.ones((9, 9), np.uint8))
            import torch as _t
            res = pipe(prompt=prompt, image=Image.fromarray(img), mask_image=Image.fromarray(mask),
                       height=H, width=W, guidance_scale=30.0, num_inference_steps=40,
                       generator=_t.Generator("cuda").manual_seed(0)).images[0]
            v7 = np.array(res); v7[~(mask > 127)] = img[~(mask > 127)]
            fmask = cv2.imread(CDIR + "/%s_faithfill_mask.png" % SC, 0)
            gm = cv2.dilate((fmask > 127).astype(np.uint8) * 255, np.ones((7, 7), np.uint8))
            res = pipe(prompt="smooth grey asphalt road surface, photorealistic, seamless ground continuation, soft natural shadow",
                       image=Image.fromarray(v7), mask_image=Image.fromarray(gm),
                       height=H, width=W, guidance_scale=30.0, num_inference_steps=40,
                       generator=_t.Generator("cuda").manual_seed(0)).images[0]
            v8 = np.array(res); keep = ~(gm > 127); v8[keep] = v7[keep]
            frame1 = cv2.cvtColor(v8, cv2.COLOR_RGB2BGR)
            out = root + "/out"
            os.makedirs(out + "/frames", exist_ok=True); os.makedirs(out + "/masks", exist_ok=True)
            cv2.imwrite(out + "/frames/fr_0000.png", frame1)
            cv2.imwrite(out + "/masks/mk_0000.png", np.full((H, W), 255, np.uint8))
            for i in range(92):
                f = cv2.imread(root + "/casc/in/%05d.png" % i)
                cv2.imwrite(out + "/frames/fr_%04d.png" % (i + 1), f)
                cv2.imwrite(out + "/masks/mk_%04d.png" % (i + 1), (f.astype(np.int32).sum(2) >= 12).astype(np.uint8) * 255)
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
            json.dump(REPORT[U8], open(out + "/ledger.json", "w"), indent=1)
            subprocess.run("cp -r %s/* '%s/'" % (out, DST), shell=True, timeout=1800)
            REPORT[U8]["packaged"] = True
            print("G2[%s] PACKAGED" % U8, flush=True)
        except Exception as e:
            REPORT[U8]["packaged"] = "FAIL: %s" % str(e)[:150]
            print("G2[%s] PACKAGE_FAIL %s" % (U8, str(e)[:150]), flush=True)
        json.dump(REPORT, open("/content/db127_report.json", "w"), indent=1)
print("G2_REPORT", json.dumps(REPORT), flush=True)
print("DB127R_ALL_DONE", flush=True)
'''
a.dr_launch("db127res", JOB)
print("RESUME_LAUNCHED")
