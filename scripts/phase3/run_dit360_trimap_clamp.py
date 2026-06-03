"""Run DiT360 seam completion with tri-map latent consistency.

This is a stricter follow-up to the raw DiT360 and post-compose experiments.
Instead of letting DiT360 freely edit the whole panorama and then hard-pasting
the original image back outside the seam, this script gives the model a
three-zone mask:

  core seam:   free generation
  halo:        limited latent pull toward the source image
  far region: latent clamp to the source image

The final raw output is still saved, along with two posthoc references:
soft-compose and core-only compose. The raw output is the main candidate; the
posthoc variants are diagnostics for the edit-vs-fidelity trade-off.
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
class Case:
    name: str
    core_mask: Path
    tau: float = 5.0
    halo_px: int = 16
    halo_weight: float = 0.25
    far_weight: float = 1.0
    halo_min_t: float = 0.50
    far_min_t: float = 0.00
    compose_halo_alpha: float = 0.45
    guidance: float | None = None   # per-case (overrides --guidance) so a tau×guidance sweep loads FLUX once
    seed: int | None = None         # per-case seed (for seed ensembles); None -> fall back to --guidance/--seed


def _parse_case(text: str) -> Case:
    values: dict[str, str] = {}
    for part in text.split(","):
        key, value = part.split("=", 1)
        values[key.strip()] = value.strip()
    if "name" not in values or "core_mask" not in values:
        raise ValueError(f"case must include name and core_mask: {text}")
    return Case(
        name=values["name"],
        core_mask=Path(values["core_mask"]),
        tau=float(values.get("tau", 5.0)),
        halo_px=int(values.get("halo_px", 16)),
        halo_weight=float(values.get("halo_weight", 0.25)),
        far_weight=float(values.get("far_weight", 1.0)),
        halo_min_t=float(values.get("halo_min_t", 0.50)),
        far_min_t=float(values.get("far_min_t", 0.00)),
        compose_halo_alpha=float(values.get("compose_halo_alpha", 0.45)),
        guidance=(float(values["guidance"]) if "guidance" in values else None),
        seed=(int(values["seed"]) if "seed" in values else None),
    )


def _parse_crop(text: str) -> tuple[str, tuple[int, int, int, int]]:
    name, coords = text.split("=", 1)
    vals = [int(v) for v in coords.split(",")]
    if len(vals) != 4:
        raise ValueError(f"crop must be name=x0,y0,x1,y1: {text}")
    x0, y0, x1, y1 = vals
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"invalid crop box: {text}")
    return name, (x0, y0, x1, y1)


def _load_rgb(path: Path, size: tuple[int, int] | None = None) -> Image.Image:
    img = Image.open(path).convert("RGB")
    if size is not None and img.size != size:
        img = img.resize(size, Image.Resampling.BICUBIC)
    return img


def _load_preserve_mask(path: Path, size: tuple[int, int]) -> np.ndarray:
    mask = Image.open(path).convert("L")
    if mask.size != size:
        mask = mask.resize(size, Image.Resampling.NEAREST)
    return np.array(mask) >= 128


def _dilate(mask: np.ndarray, radius_px: int) -> np.ndarray:
    if radius_px <= 0:
        return mask.copy()
    k = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (radius_px * 2 + 1, radius_px * 2 + 1),
    )
    return cv2.dilate(mask.astype(np.uint8), k).astype(bool)


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


def _make_model_mask(core: np.ndarray, halo: np.ndarray) -> np.ndarray:
    preserve = ~(core | halo)
    return np.where(preserve, 255, 0).astype(np.uint8)


def _save_mask(path: Path, mask: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask, mode="L").save(path)


def _preview(init: np.ndarray, core: np.ndarray, halo: np.ndarray, far: np.ndarray) -> np.ndarray:
    out = init.astype(np.float32).copy()
    red = np.zeros_like(out)
    red[..., 0] = 255.0
    yellow = np.zeros_like(out)
    yellow[..., 0] = 255.0
    yellow[..., 1] = 210.0
    green = np.zeros_like(out)
    green[..., 1] = 210.0
    out[far] = 0.90 * out[far] + 0.10 * green[far]
    out[halo] = 0.55 * out[halo] + 0.45 * yellow[halo]
    out[core] = 0.50 * out[core] + 0.50 * red[core]
    return np.clip(out, 0, 255).astype(np.uint8)


def _label_band(width: int, label: str) -> np.ndarray:
    band = np.zeros((36, width, 3), dtype=np.uint8)
    cv2.putText(band, label[:120], (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (255, 255, 255), 2)
    return band


def _fit_width(img: Image.Image | np.ndarray, width: int) -> np.ndarray:
    if isinstance(img, np.ndarray):
        img = Image.fromarray(img)
    if img.width == width:
        return np.array(img.convert("RGB"))
    h = max(1, round(img.height * width / img.width))
    return np.array(img.convert("RGB").resize((width, h), Image.Resampling.BICUBIC))


def _save_review(path: Path, rows: list[tuple[str, Image.Image | np.ndarray]], width: int) -> None:
    panels = []
    for label, img in rows:
        arr = _fit_width(img, width)
        panels.append(np.vstack([_label_band(width, label), arr]))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.vstack(panels)).save(path, quality=92)


def _save_crop_review(
    path: Path,
    rows: list[tuple[str, Image.Image | np.ndarray]],
    crops: list[tuple[str, tuple[int, int, int, int]]],
) -> None:
    if not crops:
        return
    rendered_rows = []
    for label, img in rows:
        if isinstance(img, np.ndarray):
            pil = Image.fromarray(img)
        else:
            pil = img.convert("RGB")
        cells = []
        for crop_name, box in crops:
            crop = np.array(pil.crop(box))
            cells.append(np.vstack([_label_band(crop.shape[1], f"{label} | {crop_name}"), crop]))
        max_h = max(c.shape[0] for c in cells)
        padded = []
        for cell in cells:
            if cell.shape[0] < max_h:
                cell = np.vstack([cell, np.zeros((max_h - cell.shape[0], cell.shape[1], 3), dtype=np.uint8)])
            padded.append(cell)
        rendered_rows.append(np.hstack(padded))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.vstack(rendered_rows)).save(path, quality=92)


def _mae(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    if not np.any(mask):
        return float("nan")
    diff = a[mask].astype(np.float32) - b[mask].astype(np.float32)
    return float(np.mean(np.abs(diff)))


def _soft_compose(init_np: np.ndarray, raw_np: np.ndarray, core: np.ndarray, halo: np.ndarray, halo_alpha: float) -> np.ndarray:
    alpha = np.zeros(core.shape, dtype=np.float32)
    alpha[core] = 1.0
    alpha[halo] = np.clip(halo_alpha, 0.0, 1.0)
    if np.any(halo):
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=2.0, sigmaY=2.0)
        alpha[core] = 1.0
    out = init_np.astype(np.float32) * (1.0 - alpha[..., None]) + raw_np.astype(np.float32) * alpha[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def _core_compose(init_np: np.ndarray, raw_np: np.ndarray, core: np.ndarray) -> np.ndarray:
    out = init_np.copy()
    out[core] = raw_np[core]
    return out


def _reset_flux_attn_processors(transformer: object) -> None:
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
    ap.add_argument("--crop", action="append", default=[])
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--width", type=int, default=2048)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--guidance", type=float, default=2.8)
    ap.add_argument("--review-width", type=int, default=1100)
    ap.add_argument("--disable-vae-tiling", action="store_true")
    args = ap.parse_args()

    cases = [_parse_case(item) for item in args.case]
    crops = [_parse_crop(item) for item in args.crop]
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
    pipe.load_lora_weights(
        "Insta360-Research/DiT360-Panorama-Image-Generation",
        weight_name="adapter_model.safetensors",  # required in HF offline mode (auto-guess disabled)
    )
    if not args.disable_vae_tiling:
        if hasattr(pipe.vae, "enable_tiling"):
            pipe.vae.enable_tiling()
        if hasattr(pipe.vae, "enable_slicing"):
            pipe.vae.enable_slicing()
    load_runtime_s = round(time.time() - load_started, 3)

    init_image = _load_rgb(Path(args.init_image), (args.width, args.height))
    init_np = np.array(init_image, dtype=np.uint8)
    latent_h = args.height // (pipe.vae_scale_factor * 2)
    latent_w = args.width // (pipe.vae_scale_factor * 2)
    img_dims = latent_h * (latent_w + 2)

    print("[invert] source image once for all cases", flush=True)
    inv_started = time.time()
    base_inverted_latents, base_image_latents, latent_image_ids = pipe.invert(
        source_prompt="",
        image=init_image,
        height=args.height,
        width=args.width,
        num_inversion_steps=args.steps,
        gamma=1.0,
    )
    invert_runtime_s = round(time.time() - inv_started, 3)

    run_summaries: list[dict[str, object]] = []
    for idx, case in enumerate(cases):
        case_started = time.time()
        print(
            f"[case {idx + 1}/{len(cases)}] {case.name} "
            f"tau={case.tau} halo_px={case.halo_px} halo_w={case.halo_weight}",
            flush=True,
        )
        case_dir = out_dir / case.name
        case_dir.mkdir(parents=True, exist_ok=True)

        core_preserve = _load_preserve_mask(case.core_mask, init_image.size)
        core = ~core_preserve
        dilated = _dilate(core, case.halo_px)
        halo = dilated & ~core
        far = ~dilated
        model_mask_np = _make_model_mask(core, halo)
        model_mask_path = case_dir / f"{case.name}_model_mask_far_preserve.png"
        _save_mask(model_mask_path, model_mask_np)
        tri_preview = _preview(init_np, core, halo, far)
        Image.fromarray(tri_preview).save(case_dir / f"{case.name}_trimap_preview.jpg", quality=92)

        _reset_flux_attn_processors(pipe.transformer)
        model_mask = create_mask(str(model_mask_path), latent_w, latent_h).float()
        model_mask = torch.cat([model_mask[:, 0:1], model_mask, model_mask[:, -1:]], dim=-1).view(-1, 1)
        halo_w, far_w = _latent_weights(
            core=core,
            halo=halo,
            latent_hw=(latent_h, latent_w),
            halo_weight=case.halo_weight,
            far_weight=case.far_weight,
            device=device,
            dtype=dtype,
        )

        def clamp_callback(_pipe, _step: int, timestep, callback_kwargs: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
            latents = callback_kwargs["latents"]
            t_norm = float(timestep.item()) / 1000.0
            weight = torch.zeros_like(far_w)
            if t_norm >= case.far_min_t:
                weight = torch.maximum(weight, far_w)
            if t_norm >= case.halo_min_t:
                weight = torch.maximum(weight, halo_w)
            if torch.any(weight > 0):
                latents[1:2] = latents[1:2] * (1.0 - weight) + latents[0:1] * weight
            return {"latents": latents}

        set_flux_transformer_attn_processor(
            pipe.transformer,
            set_attn_proc_func=lambda proc_name, dh, nh, ap, tau=case.tau, mask=model_mask: PersonalizeAnythingAttnProcessor(
                name=proc_name,
                tau=tau / 100.0,
                mask=mask,
                device=device,
                img_dims=img_dims,
            ),
        )

        output = pipe(
            [args.prompt, args.prompt],
            inverted_latents=base_inverted_latents.clone(),
            image_latents=base_image_latents.clone(),
            latent_image_ids=latent_image_ids,
            height=args.height,
            width=args.width,
            start_timestep=0.0,
            stop_timestep=0.99,
            num_inference_steps=args.steps,
            eta=1.0,
            guidance_scale=(case.guidance if case.guidance is not None else args.guidance),
            generator=torch.Generator(device=device).manual_seed(case.seed if case.seed is not None else args.seed),
            mask=model_mask,
            use_timestep=True,
            callback_on_step_end=clamp_callback,
            callback_on_step_end_tensor_inputs=["latents"],
        ).images[1]

        raw_np = np.array(output.convert("RGB"), dtype=np.uint8)
        soft_np = _soft_compose(init_np, raw_np, core, halo, case.compose_halo_alpha)
        core_np = _core_compose(init_np, raw_np, core)

        raw_path = case_dir / f"{case.name}_raw.png"
        soft_path = case_dir / f"{case.name}_softcompose.png"
        core_path = case_dir / f"{case.name}_corecompose.png"
        output.save(raw_path)
        Image.fromarray(soft_np).save(soft_path)
        Image.fromarray(core_np).save(core_path)

        rows: list[tuple[str, Image.Image | np.ndarray]] = [
            ("input hard_select", init_image),
            ("tri-map: red core, yellow halo, green far", tri_preview),
            ("DiT360 tri-map raw", output),
            ("softcompose diagnostic", soft_np),
            ("core-only compose diagnostic", core_np),
        ]
        _save_review(case_dir / f"{case.name}_overall_review.jpg", rows, args.review_width)
        _save_crop_review(case_dir / f"{case.name}_crop_review.jpg", rows, crops)

        summary = {
            "name": case.name,
            "init_image": str(args.init_image),
            "core_mask": str(case.core_mask),
            "model_mask": str(model_mask_path),
            "raw_output": str(raw_path),
            "softcompose_output": str(soft_path),
            "corecompose_output": str(core_path),
            "height": args.height,
            "width": args.width,
            "steps": args.steps,
            "seed": (case.seed if case.seed is not None else args.seed),
            "guidance": (case.guidance if case.guidance is not None else args.guidance),
            "tau": case.tau,
            "tau_internal": case.tau / 100.0,
            "halo_px": case.halo_px,
            "halo_weight": case.halo_weight,
            "far_weight": case.far_weight,
            "halo_min_t": case.halo_min_t,
            "far_min_t": case.far_min_t,
            "compose_halo_alpha": case.compose_halo_alpha,
            "core_fraction": float(core.mean()),
            "halo_fraction": float(halo.mean()),
            "far_fraction": float(far.mean()),
            "raw_core_mae_vs_init": _mae(raw_np, init_np, core),
            "raw_halo_mae_vs_init": _mae(raw_np, init_np, halo),
            "raw_far_mae_vs_init": _mae(raw_np, init_np, far),
            "soft_core_mae_vs_init": _mae(soft_np, init_np, core),
            "soft_halo_mae_vs_init": _mae(soft_np, init_np, halo),
            "soft_far_mae_vs_init": _mae(soft_np, init_np, far),
            "corecompose_core_mae_vs_init": _mae(core_np, init_np, core),
            "corecompose_halo_mae_vs_init": _mae(core_np, init_np, halo),
            "corecompose_far_mae_vs_init": _mae(core_np, init_np, far),
            "model_load_runtime_s": load_runtime_s,
            "invert_runtime_s": invert_runtime_s,
            "case_runtime_s": round(time.time() - case_started, 3),
            "mask_convention": "white/255 preserves source; black/0 generates",
            "method": "tri-map latent consistency: core free, halo soft clamp, far clamp",
        }
        with open(case_dir / f"{case.name}_diagnostics.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        run_summaries.append(summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
        torch.cuda.empty_cache()

    with open(out_dir / "trimap_clamp_summary.json", "w", encoding="utf-8") as f:
        json.dump({"runs": run_summaries}, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
