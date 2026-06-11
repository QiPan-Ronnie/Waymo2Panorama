"""Production sky fill: FLUX.1-Fill-dev (+ DiT360 panorama LoRA) over ground-filled
panoramas, with AUTO-PROMPT derived from the observed sky band (sunny/dusk/overcast).

Mask-conditioned inpainting is the trained objective of FLUX.1-Fill — it continues
the OBSERVED cloud band into the cap (the RF-inversion route cannot; see progress.md
2026-06-11). Scene pixels outside the mask are restored byte-exact after generation.

The HF token is passed at runtime and NEVER stored:
    python sky_fill_flux.py --token hf_xxx [--ground-dir ...] [--out-dir ...]
"""
from __future__ import annotations
import argparse, base64, json
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient

SCENES = ["02a00399_a000_bmw", "9f871fb4_a030_downtown", "fbee355f_a030_crowd",
          "0bae3b5e_a030_clean", "2c652f9e_a030_highway"]

REMOTE = r'''
import os, json, pathlib, numpy as np, torch, cv2
os.environ['HF_HOME'] = '/content/hf_dit'
from PIL import Image
from huggingface_hub import login
login(token='__TOKEN__', add_to_git_credential=False)   # in-process only
from diffusers import FluxFillPipeline
pipe = FluxFillPipeline.from_pretrained('black-forest-labs/FLUX.1-Fill-dev', torch_dtype=torch.bfloat16).to('cuda')
if hasattr(pipe.vae, 'enable_tiling'): pipe.vae.enable_tiling()
try:
    pipe.load_lora_weights('Insta360-Research/DiT360-Panorama-Image-Generation', weight_name='adapter_model.safetensors')
    print('lora ok')
except Exception as e:
    print('lora skip', str(e)[:200])

SC = __SCENES__
GD = pathlib.Path('__GROUND_DIR__')
OUT = pathlib.Path('__OUT_DIR__')
OUT.mkdir(parents=True, exist_ok=True)
rep = {}
for sc in SC:
    p = GD / f'{sc}_segcomposite.png'
    if not p.exists():
        rep[sc] = 'NO GROUND PANO'; continue
    img = np.array(Image.open(p))
    H, W = img.shape[:2]
    black = img.astype(int).sum(2) < 12
    cap = np.zeros_like(black)
    band_px = []
    for u in range(W):
        k = 0
        while k < H and black[k, u]: k += 1
        cap[:k, u] = True
        band_px.extend(img[k:k + 20, u].astype(np.float32))
    band = np.array(band_px)
    bright = band[band.mean(1) > 100]
    m = bright.mean(0) if len(bright) else band.mean(0)
    if m[0] > m[2] + 8:
        prompt = 'warm dusk sky with soft orange glow and scattered clouds, natural evening light, seamless panorama sky'
    elif m.mean() > 150 and m[2] > m[0] + 10:
        prompt = 'clear sunny blue sky with scattered white cumulus clouds, seamless photographic panorama sky'
    else:
        prompt = 'pale softly overcast sky with diffuse natural light, seamless photographic panorama sky'
    mask = cv2.dilate(cap.astype(np.uint8) * 255, np.ones((9, 9), np.uint8))
    res = pipe(prompt=prompt, image=Image.fromarray(img), mask_image=Image.fromarray(mask),
               height=H, width=W, guidance_scale=30.0, num_inference_steps=40,
               generator=torch.Generator('cuda').manual_seed(0)).images[0]
    out = np.array(res)
    out[~(mask > 127)] = img[~(mask > 127)]   # scene bytes restored exactly
    Image.fromarray(out).save(OUT / f'{sc}_complete_v7.png')
    rep[sc] = {'prompt': prompt.split(',')[0], 'sky_mean': [int(x) for x in m]}
    print('done', sc)
(OUT / '_report.json').write_text(json.dumps(rep, indent=1))
print('V7_ALL_DONE')
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="HF token (runtime only; never stored)")
    ap.add_argument("--ground-dir", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db94_ground_v2")
    ap.add_argument("--out-dir", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db97_complete_v7")
    ap.add_argument("--timeout-s", type=int, default=3600)
    args = ap.parse_args()
    py = (REMOTE.replace("__TOKEN__", args.token)
                .replace("__SCENES__", json.dumps(SCENES))
                .replace("__GROUND_DIR__", args.ground_dir)
                .replace("__OUT_DIR__", args.out_dir))
    b = base64.b64encode(py.encode()).decode()
    bash = "set +x\npython - <<'PY'\nimport base64\nexec(compile(base64.b64decode('" + b + "').decode(), '<skyfill>', 'exec'))\nPY"
    client = ColabClient()
    sub = client.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content", "timeout_s": args.timeout_s}, timeout=180)
    print(json.dumps({"job_id": sub["job_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
