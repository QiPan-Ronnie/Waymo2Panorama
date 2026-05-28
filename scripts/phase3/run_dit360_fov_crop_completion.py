"""Run DiT360 on a cropped 360-degree FoV band.

This experiment changes the DiT360 question from "fill the whole empty ERP"
to "crop the AV2 ring-camera FoV band, then complete only the holes inside
that compact 360 strip."  The preserve/generate mask is derived from the
hard-select camera footprint so the model is not asked to invent a full sky
or ego-vehicle bottom that AV2 never observed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PreparedInput:
    name: str
    source_path: Path
    mode: str
    image: Image.Image
    preserve_mask: np.ndarray
    crop_box: tuple[int, int, int, int]


def _parse_input(text: str) -> tuple[str, Path]:
    name, value = text.split("=", 1)
    return name.strip(), Path(value.strip())


def _load_rgb(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def _valid_footprint(img: Image.Image, threshold: int, erode_px: int) -> np.ndarray:
    arr = np.array(img.convert("RGB"))
    valid = arr.max(axis=2) > threshold
    if erode_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_px * 2 + 1, erode_px * 2 + 1))
        valid = cv2.erode(valid.astype(np.uint8), k).astype(bool)
    return valid


def _dilate(mask: np.ndarray, radius_px: int) -> np.ndarray:
    if radius_px <= 0:
        return mask.copy()
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius_px * 2 + 1, radius_px * 2 + 1))
    return cv2.dilate(mask.astype(np.uint8), k).astype(bool)


def _reset_flux_attn_processors(transformer: object) -> None:
    attn_procs = {}
    for name in transformer.attn_processors.keys():
        if name.endswith("attn.processor"):
            attn_procs[name] = FluxAttnProcessor2_0()
    transformer.set_attn_processor(attn_procs)


def _downsample_any(mask: np.ndarray, out_hw: tuple[int, int], thresh: float = 0.01) -> np.ndarray:
    h, w = out_hw
    small = cv2.resize(mask.astype(np.float32), (w, h), interpolation=cv2.INTER_AREA)
    return small > thresh


def _latent_weights(
    core: np.ndarray,
    halo: np.ndarray,
    latent_hw: tuple[int, int],
    halo_weight: float,
    far_weight: float,
    device: torch.device,
    dtype: torch.dtype,
) -> tuple[torch.Tensor, torch.Tensor]:
    core_l = _downsample_any(core, latent_hw, thresh=0.01)
    halo_l = _downsample_any(halo, latent_hw, thresh=0.01) & ~core_l
    far_l = ~(core_l | halo_l)
    halo_w = np.zeros(latent_hw, dtype=np.float32)
    far_w = np.zeros(latent_hw, dtype=np.float32)
    halo_w[halo_l] = np.clip(halo_weight, 0.0, 1.0)
    far_w[far_l] = np.clip(far_weight, 0.0, 1.0)

    def wrap(arr: np.ndarray) -> torch.Tensor:
        arr = np.concatenate([arr[:, -1:], arr, arr[:, :1]], axis=1)
        arr = arr.reshape(1, -1, 1)
        return torch.from_numpy(arr).to(device=device, dtype=dtype)

    return wrap(halo_w), wrap(far_w)


def _make_model_mask(preserve: np.ndarray) -> np.ndarray:
    return np.where(preserve, 255, 0).astype(np.uint8)


def _overlay_mask(img: Image.Image, generate: np.ndarray) -> Image.Image:
    arr = np.array(img.convert("RGB")).astype(np.float32)
    red = np.zeros_like(arr)
    red[..., 0] = 255.0
    red[..., 1] = 48.0
    arr[generate] = arr[generate] * 0.55 + red[generate] * 0.45
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _label_band(width: int, label: str) -> np.ndarray:
    band = np.zeros((36, width, 3), dtype=np.uint8)
    cv2.putText(band, label[:140], (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (255, 255, 255), 2)
    return band


def _fit_width(img: Image.Image, width: int) -> np.ndarray:
    if img.width == width:
        return np.array(img.convert("RGB"))
    h = max(1, round(img.height * width / img.width))
    return np.array(img.convert("RGB").resize((width, h), Image.Resampling.BICUBIC))


def _save_review(path: Path, rows: list[tuple[str, Image.Image]], width: int) -> None:
    panels = []
    for label, img in rows:
        arr = _fit_width(img, width)
        panels.append(np.vstack([_label_band(width, label), arr]))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.vstack(panels)).save(path, quality=92)


def _prepare_inputs(
    inputs: list[tuple[str, Path]],
    hard_select: Image.Image,
    y0: int,
    y1: int,
    threshold: int,
    erode_px: int,
    include_native: bool,
    include_erp_resized: bool,
) -> list[PreparedInput]:
    if y1 <= y0:
        raise ValueError(f"invalid crop y range: {y0}:{y1}")
    w, h = hard_select.size
    if y0 < 0 or y1 > h:
        raise ValueError(f"crop y range {y0}:{y1} outside source height {h}")
    crop_box = (0, y0, w, y1)
    footprint = _valid_footprint(hard_select, threshold=threshold, erode_px=erode_px)[y0:y1, :]
    prepared: list[PreparedInput] = []
    for name, path in inputs:
        src = _load_rgb(path)
        crop = src.crop(crop_box)
        if include_native:
            prepared.append(
                PreparedInput(
                    name=f"{name}_native_y{y0:03d}_{y1:03d}",
                    source_path=path,
                    mode="native_crop",
                    image=crop,
                    preserve_mask=footprint.copy(),
                    crop_box=crop_box,
                )
            )
        if include_erp_resized:
            resized = crop.resize((2048, 1024), Image.Resampling.BICUBIC)
            mask_resized = cv2.resize(footprint.astype(np.uint8), (2048, 1024), interpolation=cv2.INTER_NEAREST).astype(bool)
            prepared.append(
                PreparedInput(
                    name=f"{name}_erp_resized_y{y0:03d}_{y1:03d}",
                    source_path=path,
                    mode="erp_resized_crop",
                    image=resized,
                    preserve_mask=mask_resized,
                    crop_box=crop_box,
                )
            )
    return prepared


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dit360-root", required=True)
    ap.add_argument("--hard-select", required=True, help="hard-select image used to derive the valid camera footprint")
    ap.add_argument("--input", action="append", required=True, help="name=/path/to/image.png")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--y0", type=int, default=256)
    ap.add_argument("--y1", type=int, default=768)
    ap.add_argument("--threshold", type=int, default=4)
    ap.add_argument("--erode-px", type=int, default=6)
    ap.add_argument("--halo-px", type=int, default=16)
    ap.add_argument("--halo-weight", type=float, default=0.25)
    ap.add_argument("--far-weight", type=float, default=1.0)
    ap.add_argument("--tau", type=float, default=50.0)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--guidance", type=float, default=2.8)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--review-width", type=int, default=1100)
    ap.add_argument("--native", action="store_true")
    ap.add_argument("--erp-resized", action="store_true")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    if not args.native and not args.erp_resized:
        args.erp_resized = True

    dit_root = Path(args.dit360_root).resolve()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(dit_root))

    from pa_src.attn_processor import PersonalizeAnythingAttnProcessor, set_flux_transformer_attn_processor  # noqa: E402
    from pa_src.pipeline import RFPanoInversionParallelFluxPipeline  # noqa: E402
    from pa_src.utils import create_mask  # noqa: E402

    hard_select = _load_rgb(Path(args.hard_select))
    input_items = [_parse_input(item) for item in args.input]
    prepared = _prepare_inputs(
        input_items,
        hard_select=hard_select,
        y0=args.y0,
        y1=args.y1,
        threshold=args.threshold,
        erode_px=args.erode_px,
        include_native=args.native,
        include_erp_resized=args.erp_resized,
    )

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
    if hasattr(pipe.vae, "enable_tiling"):
        pipe.vae.enable_tiling()
    if hasattr(pipe.vae, "enable_slicing"):
        pipe.vae.enable_slicing()
    load_runtime_s = round(time.time() - load_started, 3)

    summaries: list[dict[str, object]] = []
    for idx, item in enumerate(prepared):
        case_started = time.time()
        case_dir = out_dir / item.name
        case_dir.mkdir(parents=True, exist_ok=True)
        init_image = item.image.convert("RGB")
        width, height = init_image.size
        raw_path = case_dir / f"{item.name}_raw.png"
        compose_path = case_dir / f"{item.name}_corecompose.png"
        diag_path = case_dir / f"{item.name}_diagnostics.json"
        if args.skip_existing and raw_path.exists() and compose_path.exists() and diag_path.exists():
            with open(diag_path, "r", encoding="utf-8") as f:
                summaries.append(json.load(f))
            print(f"[case {idx + 1}/{len(prepared)}] {item.name} exists, skip", flush=True)
            continue

        latent_h = height // (pipe.vae_scale_factor * 2)
        latent_w = width // (pipe.vae_scale_factor * 2)
        img_dims = latent_h * (latent_w + 2)
        init_np = np.array(init_image)

        core = ~item.preserve_mask
        halo = _dilate(core, args.halo_px) & ~core
        far = ~(core | halo)
        model_mask_np = _make_model_mask(item.preserve_mask)
        model_mask_path = case_dir / f"{item.name}_preserve_mask.png"
        Image.fromarray(model_mask_np, mode="L").save(model_mask_path)
        init_path = case_dir / f"{item.name}_input.png"
        init_image.save(init_path)
        _overlay_mask(init_image, core).save(case_dir / f"{item.name}_mask_overlay.jpg", quality=92)

        print(f"[case {idx + 1}/{len(prepared)}] {item.name} size={width}x{height}", flush=True)
        _reset_flux_attn_processors(pipe.transformer)
        inv_started = time.time()
        inverted_latents, image_latents, latent_image_ids = pipe.invert(
            source_prompt="",
            image=init_image,
            height=height,
            width=width,
            num_inversion_steps=args.steps,
            gamma=1.0,
        )
        invert_runtime_s = round(time.time() - inv_started, 3)

        _reset_flux_attn_processors(pipe.transformer)
        model_mask = create_mask(str(model_mask_path), latent_w, latent_h).float()
        model_mask = torch.cat([model_mask[:, 0:1], model_mask, model_mask[:, -1:]], dim=-1).view(-1, 1)
        halo_w, far_w = _latent_weights(
            core=core,
            halo=halo,
            latent_hw=(latent_h, latent_w),
            halo_weight=args.halo_weight,
            far_weight=args.far_weight,
            device=device,
            dtype=dtype,
        )

        def clamp_callback(_pipe, _step: int, timestep, callback_kwargs: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
            latents = callback_kwargs["latents"]
            t_norm = float(timestep.item()) / 1000.0
            weight = far_w
            if t_norm >= 0.50:
                weight = torch.maximum(weight, halo_w)
            if torch.any(weight > 0):
                latents[1:2] = latents[1:2] * (1.0 - weight) + latents[0:1] * weight
            return {"latents": latents}

        set_flux_transformer_attn_processor(
            pipe.transformer,
            set_attn_proc_func=lambda proc_name, dh, nh, ap, mask=model_mask: PersonalizeAnythingAttnProcessor(
                name=proc_name,
                tau=args.tau / 100.0,
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
            height=height,
            width=width,
            start_timestep=0.0,
            stop_timestep=0.99,
            num_inference_steps=args.steps,
            eta=1.0,
            guidance_scale=args.guidance,
            generator=torch.Generator(device=device).manual_seed(args.seed),
            mask=model_mask,
            use_timestep=True,
            callback_on_step_end=clamp_callback,
            callback_on_step_end_tensor_inputs=["latents"],
        ).images[1]

        raw_np = np.array(output.convert("RGB"))
        compose_np = init_np.copy()
        compose_np[core] = raw_np[core]
        output.save(raw_path)
        Image.fromarray(compose_np).save(compose_path)
        _save_review(
            case_dir / f"{item.name}_review.jpg",
            [
                ("input cropped band", init_image),
                ("mask overlay: red = generated", _overlay_mask(init_image, core)),
                ("DiT360 raw completion", output),
                ("corecompose diagnostic", Image.fromarray(compose_np)),
            ],
            width=args.review_width,
        )

        summary = {
            "name": item.name,
            "source_path": str(item.source_path),
            "hard_select_footprint": str(args.hard_select),
            "mode": item.mode,
            "crop_box": item.crop_box,
            "height": height,
            "width": width,
            "steps": args.steps,
            "seed": args.seed,
            "guidance": args.guidance,
            "tau": args.tau,
            "tau_internal": args.tau / 100.0,
            "threshold": args.threshold,
            "erode_px": args.erode_px,
            "halo_px": args.halo_px,
            "core_generate_fraction": float(core.mean()),
            "halo_fraction": float(halo.mean()),
            "far_fraction": float(far.mean()),
            "model_load_runtime_s": load_runtime_s,
            "invert_runtime_s": invert_runtime_s,
            "case_runtime_s": round(time.time() - case_started, 3),
            "input": str(init_path),
            "mask": str(model_mask_path),
            "raw_output": str(raw_path),
            "corecompose_output": str(compose_path),
            "method": "FoV-cropped 360 band completion; hard-select footprint preserved, holes/boundaries generated",
            "mask_convention": "white/255 preserves source; black/0 generates",
        }
        with open(diag_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        summaries.append(summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
        torch.cuda.empty_cache()

    with open(out_dir / "v17_fov_crop_completion_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": summaries}, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
