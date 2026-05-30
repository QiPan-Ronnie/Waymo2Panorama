"""E4 (backlog #7 floor): SDXL-inpaint the seam strips on the L1 backbone.

The honest NON-hallucination-ish diffusion floor: does a strong public inpainting prior, given the
L1 hard_select context, CONTINUE the seam-strip structure cleanly (no doubling, no invention)?
Far field kept byte-identical (hard re-composite outside the seam mask). VISION-judged.
Mask: invert prepare's `preserve_nonseam_r{R}` so seam strips = inpaint(white), else keep.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch
from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", required=True, help="L1 hard_select 1024x2048 png")
    ap.add_argument("--preserve-mask", required=True, help="preserve_nonseam mask (white=preserve)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="bmw")
    ap.add_argument("--strength", type=float, default=0.85)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--guidance", type=float, default=6.0)
    ap.add_argument("--dilate", type=int, default=6)
    ap.add_argument("--prompt", default=(
        "a clean 360 equirectangular street panorama, urban driving scene, continuous building "
        "facades, road and lane lines, sharp, photorealistic, no seams"))
    args = ap.parse_args()
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    init = Image.open(args.init).convert("RGB")
    W, H = init.size
    preserve = np.array(Image.open(args.preserve_mask).convert("L").resize((W, H), Image.NEAREST)) >= 128
    content = np.array(init).sum(2) > 12
    seam = (~preserve) & content  # inpaint seam strips only, never the black FoV
    if args.dilate > 0:
        import cv2
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (args.dilate * 2 + 1,) * 2)
        seam = cv2.dilate(seam.astype(np.uint8), k).astype(bool) & content
    mask_img = Image.fromarray((seam * 255).astype(np.uint8))
    print(f"[{args.tag}] inpaint seam fraction = {seam.mean()*100:.2f}% of {W}x{H}", flush=True)

    from diffusers import StableDiffusionXLInpaintPipeline
    pipe = StableDiffusionXLInpaintPipeline.from_pretrained(
        "diffusers/stable-diffusion-xl-1.0-inpainting-0.1", torch_dtype=torch.float16, variant="fp16").to("cuda")
    pipe.set_progress_bar_config(disable=True)
    g = torch.Generator("cuda").manual_seed(0)
    out = pipe(prompt=args.prompt, image=init, mask_image=mask_img, height=H, width=W,
               num_inference_steps=args.steps, strength=args.strength, guidance_scale=args.guidance,
               generator=g).images[0]

    # hard re-composite: keep L1 byte-identical outside the seam mask
    o = np.array(out); i = np.array(init)
    comp = np.where(seam[..., None], o, i).astype(np.uint8)
    Image.fromarray(i).save(Path(args.out_dir) / f"{args.tag}_L1.png")
    Image.fromarray(comp).save(Path(args.out_dir) / f"{args.tag}_E4_sdxlinpaint.png")
    Image.fromarray(o).save(Path(args.out_dir) / f"{args.tag}_E4_raw.png")
    print(f"[{args.tag}] done -> {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
