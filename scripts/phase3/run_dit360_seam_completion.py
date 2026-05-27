"""Run DiT360 seam completion on prepared Waymo2Panorama inputs.

This is a thin, reproducible wrapper around DiT360's `editing.py`. It assumes
the input image is a 1024x2048 ERP panorama and the mask follows the convention
used by `prepare_dit360_seam_inputs.py`: white/255 preserves the source image,
black/0 lets DiT360 synthesize that region.

The FLUX.1-dev base model is gated on Hugging Face. If loading fails with a
GatedRepoError, authenticate the Colab runtime first, then rerun this script.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image


DEFAULT_PROMPT = (
    "This is a 360-degree street panorama captured by an autonomous vehicle in "
    "an urban driving scene, with roads, lane markings, sidewalks, cars, "
    "buildings, signs, and sky."
)


def _save_panel(path: Path, init: Image.Image, mask_path: Path, output: Image.Image) -> None:
    init_np = np.array(init.convert("RGB"))
    out_np = np.array(output.convert("RGB"))
    mask = np.array(Image.open(mask_path).convert("L").resize(init.size, Image.Resampling.NEAREST))
    overlay = init_np.copy().astype(np.float32)
    gen = mask < 128
    red = np.zeros_like(overlay)
    red[..., 0] = 255.0
    red[..., 1] = 48.0
    overlay[gen] = 0.55 * overlay[gen] + 0.45 * red[gen]
    panels = []
    for label, img in [
        ("input", init_np),
        ("mask red = generated", overlay.astype(np.uint8)),
        ("DiT360 output", out_np),
    ]:
        band = np.zeros((36, img.shape[1], 3), dtype=np.uint8)
        cv2.putText(band, label, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        panels.append(np.vstack([band, img]))
    Image.fromarray(np.vstack(panels)).save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dit360-root", required=True)
    ap.add_argument("--init-image", required=True)
    ap.add_argument("--mask", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--new-prompt", default=None)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--width", type=int, default=2048)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--guidance", type=float, default=2.8)
    ap.add_argument("--tau", type=float, default=20.0)
    ap.add_argument("--invert-mask", action="store_true")
    ap.add_argument("--disable-vae-tiling", action="store_true")
    ap.add_argument("--output-name", default="dit360_output.png")
    args = ap.parse_args()

    dit_root = Path(args.dit360_root).resolve()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(dit_root))

    from pa_src.attn_processor import PersonalizeAnythingAttnProcessor, set_flux_transformer_attn_processor  # noqa: E402
    from pa_src.pipeline import RFPanoInversionParallelFluxPipeline  # noqa: E402
    from pa_src.utils import create_mask  # noqa: E402

    device = torch.device("cuda:0")
    dtype = torch.float16
    torch.set_float32_matmul_precision("high")

    started = time.time()
    pipe = RFPanoInversionParallelFluxPipeline.from_pretrained(
        "black-forest-labs/FLUX.1-dev",
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
    ).to(device)
    pipe.load_lora_weights("Insta360-Research/DiT360-Panorama-Image-Generation")
    if not args.disable_vae_tiling:
        if hasattr(pipe.vae, "enable_tiling"):
            pipe.vae.enable_tiling()
        if hasattr(pipe.vae, "enable_slicing"):
            pipe.vae.enable_slicing()

    init_image = Image.open(args.init_image).convert("RGB").resize((args.width, args.height))
    latent_h = args.height // (pipe.vae_scale_factor * 2)
    latent_w = args.width // (pipe.vae_scale_factor * 2)
    img_dims = latent_h * (latent_w + 2)

    mask = create_mask(args.mask, latent_w, latent_h).float()
    if args.invert_mask:
        mask = 1 - mask
    mask = torch.cat([mask[:, 0:1], mask, mask[:, -1:]], dim=-1).view(-1, 1)

    inverted_latents, image_latents, latent_image_ids = pipe.invert(
        source_prompt="",
        image=init_image,
        height=args.height,
        width=args.width,
        num_inversion_steps=args.steps,
        gamma=1.0,
    )

    set_flux_transformer_attn_processor(
        pipe.transformer,
        set_attn_proc_func=lambda name, dh, nh, ap: PersonalizeAnythingAttnProcessor(
            name=name,
            tau=args.tau / 100.0,
            mask=mask,
            device=device,
            img_dims=img_dims,
        ),
    )

    prompt = args.prompt
    new_prompt = args.new_prompt or args.prompt
    image = pipe(
        [prompt, new_prompt],
        inverted_latents=inverted_latents,
        image_latents=image_latents,
        latent_image_ids=latent_image_ids,
        height=args.height,
        width=args.width,
        start_timestep=0.0,
        stop_timestep=0.99,
        num_inference_steps=args.steps,
        eta=1.0,
        guidance_scale=args.guidance,
        generator=torch.Generator(device=device).manual_seed(args.seed),
        mask=mask,
        use_timestep=True,
    ).images[1]

    out_path = out_dir / args.output_name
    image.save(out_path)
    _save_panel(out_dir / (Path(args.output_name).stem + "_panel.jpg"), init_image, Path(args.mask), image)

    meta = {
        "init_image": str(args.init_image),
        "mask": str(args.mask),
        "output": str(out_path),
        "mask_convention": "white/255 preserves source unless --invert-mask is set",
        "invert_mask": args.invert_mask,
        "height": args.height,
        "width": args.width,
        "steps": args.steps,
        "seed": args.seed,
        "guidance": args.guidance,
        "tau": args.tau,
        "vae_tiling": not args.disable_vae_tiling,
        "runtime_s": round(time.time() - started, 3),
        "prompt": prompt,
        "new_prompt": new_prompt,
    }
    with open(out_dir / (Path(args.output_name).stem + "_diagnostics.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(json.dumps(meta, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
