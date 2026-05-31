#!/usr/bin/env bash
# Koi's experiment: DiT360 OUTPAINT — keep ONLY the central forward patch, generate the whole surrounding 360.
# Runs on Colab A100. FLUX + DiT360 LoRA must already be copied to /content/hf_local (offline).
# Each source image -> one driver invocation with 2 cases (sector + window). FLUX loads once per image.
# far_weight defaults to 1.0 => the kept center is ANCHORED; the black (core) surroundings generate freely.
set -e
export HF_HOME=/content/hf_local
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
cd /content/koi_outpaint
ROOT=/content/DiT360
M=/content/koi_outpaint/masks

echo "############ IMAGE 1/2 : hard_select ############"
python run_dit360_trimap_clamp.py \
  --dit360-root $ROOT \
  --init-image /content/koi_outpaint/inputs/bmw_hardselect.png \
  --out-dir /content/koi_outpaint/out/hardselect \
  --steps 50 --guidance 2.8 --seed 0 \
  --case "name=hardselect_sector,core_mask=$M/bmw_hardselect_coremask_sector.png" \
  --case "name=hardselect_window,core_mask=$M/bmw_hardselect_coremask_window.png"

echo "############ IMAGE 2/2 : dit-seam-completed (v14 raw) ############"
python run_dit360_trimap_clamp.py \
  --dit360-root $ROOT \
  --init-image /content/koi_outpaint/inputs/bmw_ditseam.png \
  --out-dir /content/koi_outpaint/out/ditseam \
  --steps 50 --guidance 2.8 --seed 0 \
  --case "name=ditseam_sector,core_mask=$M/bmw_ditseam_coremask_sector.png" \
  --case "name=ditseam_window,core_mask=$M/bmw_ditseam_coremask_window.png"

echo "############ ALL 4 OUTPAINT CASES DONE ############"
find /content/koi_outpaint/out -name "*_raw.png" -o -name "*_corecompose.png" | sort
