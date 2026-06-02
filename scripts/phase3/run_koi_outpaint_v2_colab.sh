#!/usr/bin/env bash
# Koi DiT360 outpaint — V2 (2026-06-02): center-only, but with the OFFICIAL-recommended knobs.
# Changes vs v1: (1) tau=50 (official editing.py default; v1 used 5), (2) a SCENE-SPECIFIC,
# spatially-constrained Miami prompt (v1 used a generic street prompt). Per the DiT360 maintainer
# (GitHub issue #21), a specific prompt is the documented fix for incoherent/large outpaint.
# HONEST EXPECTATION: surroundings should be MORE coherent / better lighting match (closer to the
# official "好看"), but still INVENTED, not faithful to the real scene. Faithfulness is unfixable
# by prompt/params — it needs real evidence (neighbor cams / LiDAR).
#
# PREREQ on a fresh A100 runtime (same as v1 — redo because runtimes are ephemeral):
#   git clone --depth 1 https://github.com/Insta360-Research-Team/DiT360.git /content/DiT360
#   (overwrite pa_src/*.py with our vendored external/DiT360/pa_src/*.py)
#   pip uninstall -y torchao
#   cp -r <Drive cache>/hub/models--black-forest-labs--FLUX.1-dev   /content/hf_local/hub/
#   cp -r <Drive cache>/hub/models--Insta360-Research--DiT360-...   /content/hf_local/hub/
#   push run_dit360_trimap_clamp.py + inputs/bmw_hardselect.png + masks/* to /content/koi_outpaint/
set -e
export HF_HOME=/content/hf_local
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
cd /content/koi_outpaint
ROOT=/content/DiT360
M=/content/koi_outpaint/masks

PROMPT="This is a panorama image. It shows a sunny urban street in Miami on a clear day. In the center, a wide straight asphalt road with white lane markings recedes toward an intersection ahead, under a bright blue sky with scattered white clouds. On the left and right are low-rise white and beige commercial storefronts with large glass windows; parked cars along the curb; sidewalks, street signs, traffic lights, and a few palm trees. Consistent bright daytime sunlight everywhere."

echo "############ V2: hard_select, tau=50, scene-specific Miami prompt ############"
python run_dit360_trimap_clamp.py \
  --dit360-root $ROOT \
  --init-image /content/koi_outpaint/inputs/bmw_hardselect.png \
  --out-dir /content/koi_outpaint/out_v2/hardselect \
  --steps 50 --guidance 2.8 --seed 0 \
  --prompt "$PROMPT" \
  --case "name=hs_sector_tau50,core_mask=$M/bmw_hardselect_coremask_sector.png,tau=50" \
  --case "name=hs_window_tau50,core_mask=$M/bmw_hardselect_coremask_window.png,tau=50"

echo "############ V2 DONE ############"
find /content/koi_outpaint/out_v2 -name "*_raw.png" | sort
# Optional ablations to ISOLATE the variable (run separately if v2 looks promising):
#   - tau=5  + scene prompt  -> isolates the PROMPT effect
#   - tau=50 + generic prompt-> isolates the TAU effect
#   - seeds 1,2,3            -> the training-free pipeline is seed-sensitive (issue #16)
