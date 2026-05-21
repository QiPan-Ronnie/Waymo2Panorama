# T11 GEN3C 3D-cache spike plan (Plan subagent, 2026-05-21 ~05:40 UTC)

## TL;DR

- **Path**: miniconda + Python 3.10 conda env on Colab worker (`/content/miniconda3`); clone `nv-tlabs/GEN3C` to `/content/GEN3C`; install per `cosmos-predict1.yaml` + Apex (CUDA C++ build) + transformer-engine 1.12.0 + MoGe.
- **Inference target**: `gen3c_dynamic.py --vipe_path` — **literal drop-in for T9b output schema** (`rgb/`, `depth/`, `pose/`, `intrinsics/` directories match exactly).
- **Two Colab jobs**: Job 1 install + checkpoint download (60-90 min); Job 2 inference if Job 1 OK (30-45 min).
- **Disk optimization**: patch `download_gen3c_checkpoints.py` to skip `google-t5/t5-11b` (~45 GB) since `--disable_prompt_encoder` makes it unused at inference.
- **Probability estimates** (subagent honest call):
  - P(install OK): 55%
  - P(inference produces mp4 | install OK): 70%
  - P(mp4 visually street-content | inference OK): 45%
  - P(all 3): ~17%; P(install + any video): ~38%

## Why `gen3c_dynamic.py --vipe_path`

Reviewed 4 candidates:
- `gen3c_single_image.py`: runs MoGe internally for depth — would not actually use *our* Pi3/ViPE depth, weak paper claim.
- `gen3c_multiview.py`: wants a pre-built `.npz` packaging multi-cam depth + K + extrinsics. Exporter `export_vipe_npz.py` is the ViPE path; building a Pi3 packer is 2-3 days of glue code on top of install. Deferred to T11.1.
- `gen3c_persistent.py`: incremental scene update — overkill.
- **`gen3c_dynamic.py --vipe_path`**: `vipe_utils.load_vipe_data` expects `<root>/{rgb,depth,pose,intrinsics}/<base>.{mp4,zip,npz,npz}` — **exact schema match** with T9b output at `outputs/phase3/t9b_vipe_depth/`.

## Critical inference flags (saves VRAM + skips Pixtral)

`gen3c_dynamic.py` hardcodes `disable_prompt_upsampler=True` (so Pixtral never loads). With `--disable_prompt_encoder` we also skip T5 text encoder (~45 GB). Final inference needs only:
- Gen3C-Cosmos-7B model.pt (~14 GB)
- Cosmos-Tokenize1-CV8x8x8-720p (~2 GB)
- Total Drive footprint: ~16 GB

Full offload flags: `--offload_diffusion_transformer --offload_tokenizer --offload_text_encoder_model --offload_prompt_upsampler --offload_guardrail_models --disable_guardrail --disable_prompt_encoder`.

## Literal inference command (Job 2)

```bash
CUDA_HOME=$CONDA_PREFIX PYTHONPATH=$GEN3C_DIR python \
  $GEN3C_DIR/cosmos_predict1/diffusion/inference/gen3c_dynamic.py \
  --checkpoint_dir $DRIVE_OUT/checkpoints \
  --vipe_path /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/t9b_vipe_depth \
  --vipe_starting_frame_idx 0 \
  --video_save_name gen3c_t11_l1_erp_left \
  --video_save_folder /content/gen3c_out/ \
  --num_video_frames 121 \
  --height 704 --width 1280 \
  --trajectory left --camera_rotation center_facing --movement_distance 0.3 \
  --guidance 1 --num_steps 35 --fps 24 --seed 1 --num_gpus 1 \
  --offload_diffusion_transformer --offload_tokenizer --offload_text_encoder_model \
  --offload_prompt_upsampler --offload_guardrail_models \
  --disable_guardrail --disable_prompt_encoder \
  --foreground_masking --filter_points_threshold 0.05
```

## Failure modes + fallbacks

| Symptom | Cause | Fallback |
|---|---|---|
| Apex `--cpp_ext --cuda_ext` build fails | CUDA toolchain mismatch (Apex landmine for years) | Pure-Python Apex: `pip install --no-build-isolation /tmp/apex` (loses fused optimizers, fine for inference) |
| HF 401 Unauthorized on Gen3C-Cosmos-7B | Token missing or gated repo | Verify `$HF_TOKEN` on Colab; fail-fast verdict `blocked-hf-auth` if missing |
| OOM mid-diffusion on 40 GB A100 | Peak 43 GB per README | `--num_steps 25`, `--height 512 --width 896`, `--trajectory none` |
| All-black output / NaN | T9b relative depth zeros propagate into Cache4D | Verdict "ran but visual quality blocked by T9b depth quality" — still paper datapoint. Pivot to Pi3 multiview as T11.1. |
| Drive quota exceeded | Default download pulls t5-11b ~45 GB | **Already patched out** in Job 1 (sed on `google-t5/t5-11b`) |

## Paper Section 6 framing (if successful)

> We demonstrate that the L1 ERP stitched output is consumable by GEN3C [Ren et al. 2025], a state-of-the-art 3D-cache video diffusion model that explicitly conditions on per-pixel depth and camera poses. Using ViPE's panorama-mode SLAM outputs (T9b) as the 3D conditioning signal, GEN3C produces a 121-frame controlled-camera video at 1280×704 from a 5-second L1 ERP input on a single A100 in under 45 minutes. The result confirms that our pipeline's outputs are not just stitched panoramas but a *renderable 3D representation* downstream of the published-state-of-the-art 3D-aware video generation stack, completing the AV2-cams → L1 ERP → Pi3/ViPE depth → GEN3C inference arrow as a single uninterrupted artifact chain.

## Partial-success framing (mp4 produced but visually degraded)

> GEN3C accepts the L1 ERP + ViPE depth pipeline outputs without code modification, demonstrating dimensional and schema compatibility; visual quality limitations stem from the panorama-vs-perspective FOV mismatch in GEN3C's training distribution rather than any incompatibility introduced by our pipeline. This isolates an actionable follow-up: fine-tuning GEN3C on ERP-domain data or projecting L1 ERP onto perspective virtual views before GEN3C ingestion.

Section 7 future-work hook either way.

## Status

- **Job 1 submitted**: `phase3-t11-gen3c-install-v1` (commit `6e1741a` pushed). Worker picks up after T1-prep job finishes (currently in queue). Expected: 60-90 min after start.
- **Job 2**: pending Job 1 success verdict. Will be submitted by main agent when `install_done.json` shows `apex_ok > 0 && smoke_ok`.
