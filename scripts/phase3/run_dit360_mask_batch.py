"""Run a batch of DiT360 mask-edit cases with one model load.

Each case uses the same hard-select ERP init image but a different DiT360 mask
and tau value. Mask convention matches `prepare_dit360_seam_inputs.py`:
white/255 preserves source, black/0 lets DiT360 synthesize that region.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from diffusers.models.attention_processor import FluxAttnProcessor2_0
from PIL import Image


DEFAULT_PROMPT = (
    "This is a 360-degree street panorama captured by an autonomous vehicle in "
    "an urban driving scene, with roads, lane markings, sidewalks, cars, "
    "buildings, signs, and sky."
)


def _parse_case(text: str) -> dict[str, object]:
    out: dict[str, object] = {}
    for part in text.split(","):
        key, value = part.split("=", 1)
        out[key.strip()] = value.strip()
    if "name" not in out or "mask" not in out:
        raise ValueError(f"case must include name and mask: {text}")
    out["tau"] = float(out.get("tau", 5.0))
    return out


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
    Image.fromarray(np.vstack(panels)).save(path, quality=92)


def _preserve_metrics(init: Image.Image, mask_path: Path, output: Image.Image) -> dict[str, float]:
    init_np = np.array(init.convert("RGB"), dtype=np.float32)
    out_np = np.array(output.convert("RGB"), dtype=np.float32)
    mask = np.array(Image.open(mask_path).convert("L").resize(init.size, Image.Resampling.NEAREST))
    preserve = mask >= 128
    generate = ~preserve
    if not np.any(preserve):
        return {
            "preserve_fraction": 0.0,
            "generate_fraction": float(generate.mean()),
            "preserve_mae": float("nan"),
            "preserve_rmse": float("nan"),
            "preserve_psnr": float("nan"),
        }
    diff = out_np[preserve] - init_np[preserve]
    mae = float(np.mean(np.abs(diff)))
    rmse = float(np.sqrt(np.mean(diff * diff)))
    psnr = float(20.0 * np.log10(255.0 / max(rmse, 1e-6)))
    return {
        "preserve_fraction": float(preserve.mean()),
        "generate_fraction": float(generate.mean()),
        "preserve_mae": mae,
        "preserve_rmse": rmse,
        "preserve_psnr": psnr,
    }


def _reset_flux_attn_processors(transformer: object) -> None:
    """Restore vanilla Flux attention processors before DiT360 inversion."""
    attn_procs = {}
    for name in transformer.attn_processors.keys():
        if name.endswith("attn.processor"):
            attn_procs[name] = FluxAttnProcessor2_0()
    transformer.set_attn_processor(attn_procs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dit360-root", required=True)
    ap.add_argument("--init-image", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--case", action="append", required=True)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--width", type=int, default=2048)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--guidance", type=float, default=2.8)
    ap.add_argument("--disable-vae-tiling", action="store_true")
    args = ap.parse_args()

    cases = [_parse_case(item) for item in args.case]
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

    load_started = time.time()
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
    load_runtime_s = round(time.time() - load_started, 3)

    init_image = Image.open(args.init_image).convert("RGB").resize((args.width, args.height))
    latent_h = args.height // (pipe.vae_scale_factor * 2)
    latent_w = args.width // (pipe.vae_scale_factor * 2)
    img_dims = latent_h * (latent_w + 2)

    run_summaries: list[dict[str, object]] = []
    for idx, case in enumerate(cases):
        case_started = time.time()
        name = str(case["name"])
        mask_path = Path(str(case["mask"]))
        tau = float(case["tau"])
        print(f"[case {idx + 1}/{len(cases)}] {name} tau={tau} mask={mask_path}", flush=True)

        _reset_flux_attn_processors(pipe.transformer)
        mask = create_mask(str(mask_path), latent_w, latent_h).float()
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
            set_attn_proc_func=lambda proc_name, dh, nh, ap, tau=tau, mask=mask: PersonalizeAnythingAttnProcessor(
                name=proc_name,
                tau=tau / 100.0,
                mask=mask,
                device=device,
                img_dims=img_dims,
            ),
        )

        output = pipe(
            [args.prompt, args.prompt],
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

        case_dir = out_dir / name
        case_dir.mkdir(parents=True, exist_ok=True)
        out_path = case_dir / f"{name}.png"
        panel_path = case_dir / f"{name}_panel.jpg"
        diag_path = case_dir / f"{name}_diagnostics.json"
        output.save(out_path)
        _save_panel(panel_path, init_image, mask_path, output)
        metrics = _preserve_metrics(init_image, mask_path, output)
        summary = {
            "name": name,
            "init_image": str(args.init_image),
            "mask": str(mask_path),
            "output": str(out_path),
            "panel": str(panel_path),
            "mask_convention": "white/255 preserves source; black/0 generates",
            "height": args.height,
            "width": args.width,
            "steps": args.steps,
            "seed": args.seed,
            "guidance": args.guidance,
            "tau": tau,
            "vae_tiling": not args.disable_vae_tiling,
            "model_load_runtime_s": load_runtime_s,
            "case_runtime_s": round(time.time() - case_started, 3),
            "prompt": args.prompt,
            **metrics,
        }
        with open(diag_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        run_summaries.append(summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
        torch.cuda.empty_cache()

    with open(out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": run_summaries}, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
