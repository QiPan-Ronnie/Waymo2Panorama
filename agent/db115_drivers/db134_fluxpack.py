import glob, json, os, shutil, subprocess, time, threading
import numpy as np, cv2
H, W = 1024, 2048
DRIVE = "/content/drive/MyDrive/koi_waymo2pano_colab"
def find1(pat):
    g = glob.glob(pat)
    assert g, pat
    return g[0]
LED = {"logs": {}}
def led(u8, k, v):
    LED["logs"].setdefault(u8, {})[k] = v
    print("LED[%s] %s=%s" % (u8, k, v), flush=True)
TLOGS = {"15ec0778": time.time()}
pipe = None
import torch
from PIL import Image
def flux_and_pack(U8, U, SC, CDIR, root, P, led):
    global pipe
    t_log = TLOGS[U8]
    t0 = time.time()
    if pipe is None:
        os.environ["HF_HOME"] = DRIVE + "/cache/huggingface"
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from diffusers import FluxFillPipeline
        pipe = FluxFillPipeline.from_pretrained("black-forest-labs/FLUX.1-Fill-dev", torch_dtype=torch.bfloat16).to("cuda")
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
    keepm = ~(gm > 127)
    v8[keepm] = v7[keepm]
    frame1 = cv2.cvtColor(v8, cv2.COLOR_RGB2BGR)
    led(U8, "flux_s", round(time.time() - t0))
    # ---- package ----
    t0 = time.time()
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
        cv2.putText(c, U8 + " " + lb + " v10-final", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 128), 1)
        tiles.append(c)
    cv2.imwrite(out + "/sample_sheet.jpg", np.vstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 90])
    DST = DRIVE + "/datasets/av2_1plus92_cascade_v1/" + U8
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

U8 = "15ec0778"
r = subprocess.run("s5cmd --no-sign-request ls s3://argoverse/datasets/av2/sensor/val/ | grep 15ec0778", shell=True, capture_output=True, text=True)
U = r.stdout.strip().split()[-1].rstrip("/")
root = "/content/db131_" + U8
P = 76
SC = "cd_a%03d" % P
CDIR = os.path.dirname(glob.glob(root + "/cand/**/%s_segcomposite.png" % SC, recursive=True)[0])
t0 = time.time()
flux_and_pack(U8, U, SC, CDIR, root, P, led)
print("FLUXPACK_TOTAL %.0fs" % (time.time() - t0), flush=True)
print("V141_DONE", flush=True)
