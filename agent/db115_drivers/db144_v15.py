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
USED = []
MACHINE_SHARD = "__MS__"   # "i,k": this machine takes cands[i::k]
LED = {"gpu": "Blackwell96G", "K": K, "pipeline": "v14-specmap", "machine": MACHINE_SHARD, "logs": {}}


def led(u8, k, v):
    LED["logs"].setdefault(u8, {})[k] = v
    json.dump(LED, open("/content/db134_ledger.json", "w"), indent=1)
    subprocess.run("cp /content/db134_ledger.json '%s/datasets/av2_1plus92_v15/db144_v15_ledger_m%s.json'" % (DRIVE, MACHINE_SHARD.replace(",", "of")), shell=True)
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
        lf = open("%s_%s_w%d.log" % (root, tag, j), "w")
        procs.append(subprocess.Popen(["python", "/content/db125_worker.py", "%s%d" % (tag, j),
                                       ",".join(str(x) for x in subs[j]), uuid, od, extra],
                                      stdout=lf, stderr=subprocess.STDOUT,
                                      env={**os.environ, "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}))
    return [p.wait() for p in procs]


GRID_BASE = "_MHALF, _CW = 46.0, 0.05"
FUSE = ["_wmap = np.where(_conf[:, None], _col_conf, np.where(_anyv[:, None], _col_low, 0.0))",
        "_wmap = np.where(_anyv[:, None], np.nan_to_num(np.where(np.isnan(_wmed), 0.0, _wmed)), 0.0)"]
def compose_one(args):
    root, i, aN = args
    band = cv2.imread(find1(root + "/band/m*/b*_a%03d_segcomposite.png" % aN)).astype(np.float32)
    ez = cv2.imread(find1(root + "/band/m*/b*_a%03d_egozone.png" % aN), 0)
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

    ring0 = (cv2.dilate(zone2.astype(np.uint8), np.ones((21, 21), np.uint8)) > 0) & ~zone2 & bnz
    _gb = cv2.cvtColor(band.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    rl = float(np.median(_gb[ring0])) if ring0.sum() > 500 else 90.0
    v_thr = max(150.0, rl * 1.35)   # DB-130 fix: relative specular gate — sunny roads are bright+unsaturated by NATURE; only kill glare clearly above the frame's own road tone

    def spec_mask(img, valid):
        hsv = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2].astype(np.float32)
        s = hsv[:, :, 1].astype(np.float32)
        sel = zone2 & valid
        med = float(np.median(v[sel])) if sel.sum() > 500 else rl
        v_eff = v * (rl / max(med, 1.0))   # DB-130 v10.2: normalise source exposure to the band ring BEFORE the glare test — exposure offset is gain's job, not a specular signature
        return cv2.dilate(((v_eff > v_thr) & (s < 70)).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0

    spec_f = spec_mask(fil, nz)
    spec_w = spec_mask(wb, wbok)
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
    os.makedirs(root + "/c10/rmask", exist_ok=True)
    cv2.imwrite(root + "/c10/rmask/%05d.png" % i, resid.astype(np.uint8) * 255)
    cv2.imwrite(root + "/c10/in/%05d.png" % i, out)
    return (int(zone2.sum()), int(ok.sum()), int(t2.sum()), int(resid.sum()))




_POOL = []
SPLIT_OF = {}
for _sp in ("train", "val"):
    _rr = subprocess.run("s5cmd --no-sign-request ls s3://argoverse/datasets/av2/sensor/%s/" % _sp,
                         shell=True, capture_output=True, text=True, timeout=900)
    for ln in _rr.stdout.splitlines():
        u = ln.strip().split()[-1].rstrip("/")
        if len(u) > 30:
            _POOL.append(u)
            SPLIT_OF[u] = _sp
LOG100 = sorted(_POOL)
print("POOL %d logs (train+val)" % len(LOG100), flush=True)
_mi, _mk = (int(x) for x in MACHINE_SHARD.split(","))
cands = LOG100[_mi::_mk]
_mdir = DRIVE + "/datasets/av2_1plus92_v15"
os.makedirs(_mdir, exist_ok=True)
json.dump({"run": "run100_v14.1", "total": len(LOG100), "machine_shard": MACHINE_SHARD,
           "my_cands": cands, "log100": LOG100},
          open(_mdir + "/manifest_v15_m%s.json" % MACHINE_SHARD.replace(",", "of"), "w"), indent=1)
print("QUEUE %d candidates (machine shard %s)" % (len(cands), MACHINE_SHARD), flush=True)
for _mf in glob.glob(_mdir + "/db144_v15_ledger_m%s.json" % MACHINE_SHARD.replace(",", "of")):
    try:
        LED["logs"].update(json.load(open(_mf)).get("logs", {}))
    except Exception:
        pass
DONE_VERDICTS = {k for k, v in LED["logs"].items()
                 if str(v.get("verdict", "")).startswith(("OK", "SKIP"))}
print("RESUME %d already adjudicated" % len(DONE_VERDICTS), flush=True)

import sys
sys.path.insert(0, "/content")
sys.path.insert(0, "/content/w2p_ego/scripts/phase3")
sys.path.insert(0, "/content/w2p_ego/code")
from pathlib import Path
from db123_egomask import save_ego_mask_npz
from waymo2panorama.data_io.av2_loader import AV2RingLoader
import pandas as pd
from multiprocessing import Pool

pipe = None   # resident FLUX, lazy-loaded on first success
import torch
from PIL import Image

TLOGS = {}
flux_thread = None
def _pipe_retry(**kw):
    for _t in range(3):
        try:
            return pipe(**kw)
        except torch.cuda.OutOfMemoryError:
            print("FLUX_OOM retry %d" % _t, flush=True)
            torch.cuda.empty_cache()
            time.sleep(90)
    return pipe(**kw)


def flux_and_pack(U8, U, SC, CDIR, root, P, dirty_strips, led):
    global pipe
    t_log = TLOGS[U8]
    t0 = time.time()
    if pipe is None:
        os.environ["HF_HOME"] = DRIVE + "/cache/huggingface"
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from diffusers import FluxFillPipeline
        for _t2 in range(4):
            try:
                pipe = FluxFillPipeline.from_pretrained("black-forest-labs/FLUX.1-Fill-dev", torch_dtype=torch.bfloat16).to("cuda")
                break
            except torch.cuda.OutOfMemoryError:
                print("FLUX_LOAD_OOM retry %d" % _t2, flush=True)
                pipe = None
                torch.cuda.empty_cache()
                time.sleep(120)
        if hasattr(pipe.vae, "enable_tiling"):
            pipe.vae.enable_tiling()
        led(U8, "flux_load_s", round(time.time() - t0))
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
    res = _pipe_retry(prompt=prompt, image=Image.fromarray(img), mask_image=Image.fromarray(mask),
               height=H, width=W, guidance_scale=30.0, num_inference_steps=40,
               generator=torch.Generator("cuda").manual_seed(0)).images[0]
    v7 = np.array(res)
    v7[~(mask > 127)] = img[~(mask > 127)]
    fmask = cv2.imread(CDIR + "/%s_faithfill_mask.png" % SC, 0)
    gm = cv2.dilate((fmask > 127).astype(np.uint8) * 255, np.ones((7, 7), np.uint8))
    res = _pipe_retry(prompt="smooth grey asphalt road surface, photorealistic, seamless ground continuation, soft natural shadow",
               image=Image.fromarray(v7), mask_image=Image.fromarray(gm),
               height=H, width=W, guidance_scale=30.0, num_inference_steps=40,
               generator=torch.Generator("cuda").manual_seed(0)).images[0]
    v8 = np.array(res)
    keepm = ~(gm > 127)
    v8[keepm] = v7[keepm]
    frame1 = cv2.cvtColor(v8, cv2.COLOR_RGB2BGR)
    led(U8, "flux_s", round(time.time() - t0))
    # ---- package v15: A (filled, strict mask) + B (hood-black) ----
    t0 = time.time()
    out = root + "/out"
    strip_of = dict(dirty_strips)
    for V in ("A", "B"):
        os.makedirs(out + "/%s/frames" % V, exist_ok=True)
        os.makedirs(out + "/%s/masks" % V, exist_ok=True)
    cv2.imwrite(out + "/A/frames/fr_0000.png", frame1)
    cv2.imwrite(out + "/A/masks/mk_0000.png", np.full((H, W), 255, np.uint8))
    shutil.copy(out + "/A/frames/fr_0000.png", out + "/B/frames/fr_0000.png")
    shutil.copy(out + "/A/masks/mk_0000.png", out + "/B/masks/mk_0000.png")
    for i2 in range(92):
        aN = P + 1 + i2
        f = cv2.imread(root + "/c10/in/%05d.png" % i2)
        rm = cv2.imread(root + "/c10/rmask/%05d.png" % i2, 0)
        mk = (((f.astype(np.int32).sum(2) >= 12) & (rm < 128)).astype(np.uint8) * 255)
        if i2 in strip_of:
            sx = strip_of[i2]
            if sx < 0:
                mk[:] = 0
            else:
                x0, x1 = max(0, sx - 90), min(W, sx + 90)
                mk[:, x0:x1] = 0
        cv2.imwrite(out + "/A/frames/fr_%04d.png" % (i2 + 1), f)
        cv2.imwrite(out + "/A/masks/mk_%04d.png" % (i2 + 1), mk)
        bf = cv2.imread(find1(root + "/band/m*/b*_a%03d_segcomposite.png" % aN))
        cv2.imwrite(out + "/B/frames/fr_%04d.png" % (i2 + 1), bf)
        cv2.imwrite(out + "/B/masks/mk_%04d.png" % (i2 + 1),
                    (bf.astype(np.int32).sum(2) >= 12).astype(np.uint8) * 255)
    for V in ("A", "B"):
        subprocess.run("ffmpeg -y -loglevel error -framerate 10 -i %s/%s/frames/fr_%%04d.png -c:v libx264 -pix_fmt yuv420p %s/%s/clip_%s_%s.mp4"
                       % (out, V, out, V, U8, V), shell=True, timeout=600)
    tiles = []
    for idx, lb in [(0, "frame1"), (1, "A_f001"), (46, "A_f046"), (92, "A_f092")]:
        fr = cv2.imread(out + "/A/frames/fr_%04d.png" % idx)
        c = cv2.resize(fr, (1400, 700))
        c = cv2.copyMakeBorder(c, 24, 4, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        cv2.putText(c, U8 + " " + lb + " v15", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 128), 1)
        tiles.append(c)
    cv2.imwrite(out + "/sample_sheet.jpg", np.vstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 90])
    DST = DRIVE + "/datasets/av2_1plus92_v15/%s/%s_w1" % (SPLIT_OF[U], U8)
    os.makedirs(DST, exist_ok=True)
    json.dump(LED["logs"][U8], open(out + "/ledger.json", "w"), indent=1)
    shutil.copy(root + "/worldmap.png", out + "/worldmap_m2.png")
    subprocess.run("cp -r %s/* '%s/'" % (out, DST), shell=True, timeout=1800)
    led(U8, "pack_s", round(time.time() - t0))
    led(U8, "total_s", round(time.time() - t_log))
    led(U8, "total_s", round(time.time() - t_log))
    led(U8, "verdict", "OK")
    shutil.rmtree("/content/localav2/val/" + U, ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)

prefetch = None
ok_count = 0
ci = 0
while ci < len(cands):
    U = cands[ci]
    ci += 1
    U8 = U[:8]
    if U8 in DONE_VERDICTS:
        continue
    root = "/content/db131_" + U8
    t_log = time.time()
    try:
        t0 = time.time()
        if prefetch and prefetch[0] == U:
            prefetch[1].wait()
        else:
            subprocess.run("s5cmd --no-sign-request cp 's3://argoverse/datasets/av2/sensor/%s/%s/*' /content/localav2/val/%s/" % (SPLIT_OF[U], U, U), shell=True, timeout=3600)
        if ci < len(cands):
            prefetch = (cands[ci], subprocess.Popen("s5cmd --no-sign-request cp 's3://argoverse/datasets/av2/sensor/%s/%s/*' /content/localav2/val/%s/" % (SPLIT_OF[cands[ci]], cands[ci], cands[ci]), shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        LOGD = Path("/content/localav2/val/" + U)
        N = len(glob.glob(str(LOGD / "sensors/cameras/ring_front_center/*.jpg")))
        led(U8, "localize_s", round(time.time() - t0))
        led(U8, "n_anchors", N)
        SEAM_X = {}
        try:
            import pandas as _pd
            _cal = _pd.read_feather("/content/localav2/val/%s/calibration/egovehicle_SE3_sensor.feather" % U)
            _yaws = {}
            for _, _row in _cal.iterrows():
                _n = str(_row["sensor_name"])
                if not _n.startswith("ring_"):
                    continue
                qw, qx, qy, qz = (float(_row[k]) for k in ("qw", "qx", "qy", "qz"))
                _dx = 2 * (qx * qz + qw * qy)
                _dy = 2 * (qy * qz - qw * qx)
                _dzz = 1 - 2 * (qx * qx + qy * qy)
                _fx = _dzz * 0 + _dx  # optical axis +z in cam frame -> ego components
                _R20 = 2 * (qx * qz - qw * qy)
                _R21 = 2 * (qy * qz + qw * qx)
                _R00 = 1 - 2 * (qy * qy + qz * qz)
                _R10 = 2 * (qx * qy + qw * qz)
                _ax = _dx
                _ay = _dy
                _yaws[_n] = float(np.degrees(np.arctan2(2 * (qy * qz - qw * qx) * -1 + 0 * _ay, 1)))  # placeholder
            # exact same math as validated db143: forward = R @ [0,0,1]
            _yaws = {}
            for _, _row in _cal.iterrows():
                _n = str(_row["sensor_name"])
                if not _n.startswith("ring_"):
                    continue
                qw, qx, qy, qz = (float(_row[k]) for k in ("qw", "qx", "qy", "qz"))
                _Rm = np.array([
                    [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)],
                    [2 * (qx * qy + qw * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qw * qx)],
                    [2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx * qx + qy * qy)]])
                _d = _Rm @ np.array([0.0, 0.0, 1.0])
                _yaws[_n] = float(np.degrees(np.arctan2(_d[1], _d[0])))
            for _a in _yaws:
                for _b in _yaws:
                    if _a < _b:
                        _mid = (_yaws[_a] + _yaws[_b]) / 2.0
                        if abs(_yaws[_a] - _yaws[_b]) > 180:
                            _mid += 180.0
                        SEAM_X[(_a, _b)] = int(round((0.5 - _mid / 360.0) * W)) % W
        except Exception as _e:
            print("SEAMCAL_FAIL %s" % str(_e)[:80], flush=True)
        assert N > 200, "camera frames missing"
        save_ego_mask_npz(LOGD, "/content/egomask_cur.npz")
        os.makedirs(root, exist_ok=True)
        # ---- v14: pose-only window prediction + speculative map launch (overlaps the probe) ----
        from waymo2panorama.data_io.av2_loader import AV2RingLoader as _L14
        loader = _L14(LOGD)
        all_ts = loader.anchor_timestamps_ns()
        pf = pd.read_feather(LOGD / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
        ti = pf["timestamp_ns"].to_numpy(np.int64)
        tt0 = int(ti[0])
        tss = (ti - tt0).astype(np.float64)
        tx = pf[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
        keep = np.concatenate([[True], np.diff(tss) > 0])
        tss, tx = tss[keep], tx[keep]
        def ta_of(idx):
            tc = float(np.clip(float(int(all_ts[idx]) - tt0), tss.min(), tss.max()))
            return np.array([np.interp(tc, tss, tx[:, i2]) for i2 in range(2)])
        DM = {}
        for p in range(0, N - 93):
            cmp_ = ta_of(p + 46)
            DM[p] = max(float(np.linalg.norm(ta_of(a) - cmp_)) for a in range(p, p + 93, 6))
        P_g = max(DM, key=DM.get)
        MID_g = P_g + 46
        R_g = float(np.clip(DM[P_g] + 20.0, 23.0, 46.0))
        GRIDD_g = [GRID_BASE, "_MHALF, _CW = %.1f, %.6f" % (R_g, R_g / 920.0)]
        extra_map_g = json.dumps([
            ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
            ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % N],
            ["_aidx))[:110])", "_aidx))[:60])"],
            GRIDD_g, FUSE,
        ])
        os.makedirs(root + "/map", exist_ok=True)
        mprocs = []
        for _si in range(8):
            _od = root + "/map/s%d" % _si
            os.makedirs(_od, exist_ok=True)
            _ex = json.dumps(json.loads(extra_map_g) + [
                ['WORLDBEV_SHARD = ""', 'WORLDBEV_SHARD = "%d,8"' % _si],
                ['WORLDBEV_DUMP = ""', 'WORLDBEV_DUMP = "%s/map/shard_%d.npz"' % (root, _si)],
            ])
            _lf = open(root + "/map/s%d.log" % _si, "w")
            mprocs.append(subprocess.Popen(["python", "/content/db125_worker.py", "ms%d" % _si, str(MID_g), U, _od, _ex],
                                           stdout=_lf, stderr=subprocess.STDOUT))
        led(U8, "specmap", [P_g, round(DM[P_g], 1), round(R_g, 1)])
        # ---- band stage 1: PROBE every 3rd anchor (finds the window at 1/3 cost) ----
        t0 = time.time()
        extra_bg = json.dumps([["GROUND_MODE = \"fill\"", "GROUND_MODE = \"off\""],
                               ["EGO_BLACK = False", "EGO_BLACK = True"]])
        probes = list(range(0, N, 3))
        rcs = fan("bg", probes, extra_bg, root + "/band", U)
        assert all(x == 0 for x in rcs), "probe failed"
        led(U8, "probe_s", round(time.time() - t0))
        reg = {}
        for mf in glob.glob(root + "/band/m*/manifest*.json"):
            m = json.load(open(mf))
            for c in m.get("cases", []):
                aN = int(c["case"].split("_a")[-1])
                vm = c.get("view_morph") or {}
                vals = [v.get("max_reg_px", 0.0) for v in (vm.values() if isinstance(vm, dict) else vm)]
                reg[aN] = max([0.0] + vals)
        clean_probe = set(x for x, v in reg.items() if v <= 8.0)
        # ---- motion-aware window: reuse the pose dmax table (v14) ----
        best = None
        for p in range(0, N - 93):
            win_probes = [a for a in range(p, p + 93) if a % 3 == 0]
            if not win_probes or not all(a in clean_probe for a in win_probes):
                continue
            dmax = DM[p]
            if best is None or dmax > best[1]:
                best = (p, dmax)
        if best is None:
            led(U8, "verdict", "SKIP_no_clean_window")
            shutil.rmtree("/content/localav2/val/" + U, ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)
            continue
        P, dmax = best
        led(U8, "window", [P, P + 92])
        led(U8, "dmax_m", round(dmax, 1))
        if dmax < 8.0:
            led(U8, "verdict", "SKIP_static")
            shutil.rmtree("/content/localav2/val/" + U, ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)
            continue
        # ---- band stage 2: FINE render of the window (skip already-probed anchors) ----
        t0 = time.time()
        for _try in range(3):
            fine = [a for a in range(P, P + 93)
                    if not glob.glob(root + "/band/m*/b*_a%03d_segcomposite.png" % a)]
            if not fine:
                break
            if _try:
                print("FINE_RETRY %d: %d frames missing (frame-level OOM)" % (_try, len(fine)), flush=True)
            rcs = fan("bf" if _try == 0 else "br%d" % _try, fine, extra_bg, root + "/band", U)
            assert all(x == 0 for x in rcs), "fine band failed"
        regf = {}
        pairf = {}
        for mf in glob.glob(root + "/band/m*/manifest*.json"):
            m = json.load(open(mf))
            for c in m.get("cases", []):
                aN2 = int(c["case"].split("_a")[-1])
                vm = c.get("view_morph") or {}
                for v in (vm.values() if isinstance(vm, dict) else vm):
                    r_ = float(v.get("max_reg_px", 0.0))
                    if r_ > regf.get(aN2, 0.0):
                        regf[aN2] = r_
                        pairf[aN2] = tuple(v.get("cam_pair", []))
                regf.setdefault(aN2, 0.0)
        dirty = [a for a in range(P, P + 93) if regf.get(a, 99.0) > 8.0]
        led(U8, "fine_s", round(time.time() - t0))
        led(U8, "dirty_frames", [[a, round(regf.get(a, 99.0), 1)] for a in dirty[:10]])
        if len(dirty) > 3:
            led(U8, "verdict", "SKIP_fine_dirty_%d" % len(dirty))
            shutil.rmtree("/content/localav2/val/" + U, ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)
            continue
        dirty_strips = []
        for a2 in dirty:
            _pr = pairf.get(a2)
            _sx = SEAM_X.get(tuple(sorted(_pr))) if _pr else None
            dirty_strips.append((a2 - (P + 1), -1 if _sx is None else int(_sx)))
        MID = P + 46
        # ---- single-frame cand (frame-1 base) ----
        t0 = time.time()
        extra_cd = json.dumps([
            ["FAITH_MASK = False", "FAITH_MASK = True"],
            ["    capg = blackg.copy()", "    capg = blackg.copy(); capg |= egoproj.reshape(H, W)"],
            ['GROUND_RESID = "plate"', 'GROUND_RESID = "inpaint"'],
            ["GROUND_TORCH = False", "GROUND_TORCH = True"],
        ])
        os.makedirs(root + "/cand", exist_ok=True)
        clf = open(root + "/cand.log", "w")
        crc = subprocess.run(["python", "/content/db125_worker.py", "cd", str(P), U, root + "/cand", extra_cd],
                             stdout=clf, stderr=subprocess.STDOUT).returncode
        assert crc == 0, "cand failed"
        led(U8, "cand_s", round(time.time() - t0))
        SC = "cd_a%03d" % P
        CDIR = os.path.dirname(glob.glob(root + "/cand/**/%s_segcomposite.png" % SC, recursive=True)[0])
        # ---- map (adaptive R, fused) || fill ----
        t0 = time.time()

        # ---- v14: validate the speculative map against the ACTUAL window ----
        cm_g = ta_of(MID_g)
        far = max(float(np.linalg.norm(ta_of(a) - cm_g)) for a in range(P, P + 93, 4))
        spec_ok = far <= (R_g - 2.0)
        led(U8, "specmap_hit", bool(spec_ok))
        if spec_ok:
            MID_use, R_use = MID_g, R_g
        else:
            for p_ in mprocs:
                p_.kill()
            for f_ in glob.glob(root + "/map/shard_*.npz"):
                os.remove(f_)
            MID_use, R_use = MID, float(np.clip(dmax + 14.0, 23.0, 46.0))
            GRIDD_r = [GRID_BASE, "_MHALF, _CW = %.1f, %.6f" % (R_use, R_use / 920.0)]
            extra_map_r = json.dumps([
                ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
                ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % N],
                ["_aidx))[:110])", "_aidx))[:60])"],
                GRIDD_r, FUSE,
            ])
            mprocs = []
            for _si in range(8):
                _od = root + "/map/s%d" % _si
                _ex = json.dumps(json.loads(extra_map_r) + [
                    ['WORLDBEV_SHARD = ""', 'WORLDBEV_SHARD = "%d,8"' % _si],
                    ['WORLDBEV_DUMP = ""', 'WORLDBEV_DUMP = "%s/map/shard_%d.npz"' % (root, _si)],
                ])
                _lf = open(root + "/map/s%d.log" % _si, "a")
                mprocs.append(subprocess.Popen(["python", "/content/db125_worker.py", "mr%d" % _si, str(MID), U, _od, _ex],
                                               stdout=_lf, stderr=subprocess.STDOUT))
        CAP_LIM = root + "/band/*/b*_a%03d_egozone.png"
        CAP_REF = root + "/band/*/b*_a%03d_segcomposite.png"
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
        mrcs = [p.wait() for p in mprocs]
        assert all(x == 0 for x in mrcs), "map shard failed: %s" % mrcs
        mrc = subprocess.run(["python", "/content/db131_merge.py", root + "/map/shard_*.npz", root + "/map/merged.npz"],
                             capture_output=True, text=True, timeout=600).returncode
        assert mrc == 0, "merge failed"
        GRIDD_use = [GRID_BASE, "_MHALF, _CW = %.1f, %.6f" % (R_use, R_use / 920.0)]
        extra_map_use = json.dumps([
            ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
            ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % N],
            ["_aidx))[:110])", "_aidx))[:60])"],
            GRIDD_use, FUSE,
        ])
        _extra_f = json.dumps(json.loads(extra_map_use) + [['WORLDBEV_LOAD = ""', 'WORLDBEV_LOAD = "%s/map/merged.npz"' % root]])
        os.makedirs(root + "/map/final", exist_ok=True)
        _flf = open(root + "/map/final.log", "w")
        mrc = subprocess.run(["python", "/content/db125_worker.py", "mf", str(MID_use), U, root + "/map/final", _extra_f],
                             stdout=_flf, stderr=subprocess.STDOUT).returncode
        assert mrc == 0, "map finalise failed"
        led(U8, "map_s", round(time.time() - t0))
        shutil.copy(find1(root + "/map/final/mf_a%03d_worldmap.png" % MID_use), root + "/worldmap.png")
        cm = ta_of(MID_use)
        # ---- wbev ----
        t0 = time.time()
        extra_wf = json.dumps(common + [
            ['GROUND_MODE = "fill"', 'GROUND_MODE = "worldbev"'],
            ["WORLDBEV_WIN = (0, 92)", "WORLDBEV_WIN = (0, %d)" % N],
            GRIDD_use,
            ['WORLDBEV_FILL = ""', 'WORLDBEV_FILL = "%s/worldmap.png"' % root],
            ['WORLDBEV_CENTER = ""', 'WORLDBEV_CENTER = "%.6f,%.6f"' % (float(cm[0]), float(cm[1]))],
        ])
        rcs = fan("q", list(range(P + 1, P + 93)), extra_wf, root + "/wbev", U)
        assert all(x == 0 for x in rcs), "wbev failed"
        led(U8, "wbev_s", round(time.time() - t0))
        # ---- compose v10.2 ----
        t0 = time.time()
        os.makedirs(root + "/c10/in", exist_ok=True)
        with Pool(16) as pool:
            st = pool.map(compose_one, [(root, i2, P + 1 + i2) for i2 in range(92)])
        tz = sum(s[0] for s in st)
        led(U8, "compose_s", round(time.time() - t0))
        led(U8, "cascade", {"t1pct": round(100.0 * sum(s[1] for s in st) / tz, 1),
                            "t2pct": round(100.0 * sum(s[2] for s in st) / tz, 1),
                            "residpct": round(100.0 * sum(s[3] for s in st) / tz, 2)})
        if 100.0 * sum(s[3] for s in st) / max(1, tz) > 15.0:
            led(U8, "verdict", "SKIP_resid_gt15")
            shutil.rmtree("/content/localav2/val/" + U, ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)
            continue
        # ---- hand off to the background FLUX+pack thread; CPU moves on to the next log ----
        TLOGS[U8] = t_log
        if flux_thread is not None:
            flux_thread.join()
        import threading
        flux_thread = threading.Thread(target=flux_and_pack, args=(U8, U, SC, CDIR, root, P, list(dirty_strips), led))
        flux_thread.start()
        ok_count += 1
        print("QUEUED_FLUX %d (%s)" % (ok_count, U8), flush=True)
    except Exception as e:
        led(U8, "verdict", "FAIL: %s" % str(e)[:180])
        shutil.rmtree("/content/localav2/val/" + U, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
        continue
if flux_thread is not None:
    flux_thread.join()
print("DB134_QUEUE_EXHAUSTED ok=%d" % ok_count, flush=True)
