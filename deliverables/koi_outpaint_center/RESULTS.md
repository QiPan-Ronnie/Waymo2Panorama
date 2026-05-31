# Koi's experiment — DiT360 OUTPAINT (keep ONLY the center patch, generate the whole 360)

**Date:** 2026-05-30 · **Runtime:** Colab A100-40GB · **Status:** DONE (4/4 cases ran, all vision-checked)

## What Koi asked
WeChat: "你到时候完全只留一个，就是只用最中心那张，只用最中心那块让他补完整的，看看效果" + "先試試看完全只用正中心那個".
= DiT360 **Outpainting**: keep ONLY the central forward patch (front_center camera's view = road + sky straight ahead), black out the entire surrounding 360, let DiT360 outpaint a full panorama. Center = the "anchor". Run on both BMW source images.

## Setup (reproducible)
- Driver: `scripts/phase3/run_dit360_trimap_clamp.py` (patched: `load_lora_weights(..., weight_name="adapter_model.safetensors")` — required in HF offline mode). Runner: `scripts/phase3/run_koi_outpaint_colab.sh`.
- Masks: `scripts/phase3/make_outpaint_center_mask.py` → `*_coremask_{sector,window}.png` (WHITE=preserve center ~5%, BLACK=generate ~95%). **sector** = full-height central column (~52° az = one front cam); **window** = central rectangle.
- **`far_weight=1.0` (script default — NOT 0).** Geometry: `core`=BLACK=generate (always free, 94.8%); `far`=interior of the WHITE center=clamped to source with `far_weight`. To keep the center as a true anchor it must be `far_weight=1.0`. (The pre-run note said 0; that was wrong — it would have un-anchored the center.) Confirmed: `corecompose_far_mae_vs_init = 0.0` (kept center byte-identical to source).
- FLUX.1-dev + DiT360 LoRA copied Drive→local SSD (`/content/hf_local`, 32G) to avoid the 600s FUSE-load timeout; loaded offline. 50 steps, guidance 2.8, seed 0. ~13 min for all 4.
- Inputs: the two v14 BMW images (`hard_select` and `raw`, near-identical). 4 cases = 2 imgs × {sector, window}.

## Files
- **`koi_outpaint_COMPARISON.jpg`** — top: 2 real inputs; below: 4 outpaint results (the one image to "看效果").
- `results/<case>/<case>_raw.png` — DiT360 full generation (center anchored + 95% generated).
- `results/<case>/<case>_corecompose.png` — generated surroundings + byte-exact real center hard-pasted.
- `results/<case>/<case>_overall_review.jpg` — input | tri-map | raw | softcompose | corecompose stacked.

## VISION verdict (eyes, per the standing rule — not metrics)
1. **DiT360's pano generation is genuinely good.** From a ~5% center anchor it produces a fully coherent, photorealistic, **full-sphere** 360 (sky + ground + buildings all around, plausible road/lane/sidewalk geometry). As a capability demo of "扩成完整 360", it works well.
2. **The preserved center is a visible BOXY seam.** The real Miami center (bright **sunny blue sky** + forward road) does not harmonize with the generated **grey-overcast** surroundings — it sticks out as a rectangle (lighting/tone/lane-marking discontinuity at the anchor border). The extreme keep-5% ratio makes the anchor clash, not blend.
3. **The 95% is ENTIRELY FICTIONAL.** It invented a different city (British-looking high street: brick corner buildings, blue-door storefronts, murals/signs in another style) — nothing like the real Miami scene. With identical seed/prompt and near-identical inputs, all 4 converged on a similar invented street.
4. **Hallucinated salient objects.** Invented cars, a white van, street signs, posters. For Bosch **world-model training data this is the disqualifier**: a fabricated car/sign teaches wrong scene statistics. sector vs window and hard_select vs dit-seam make little difference at this keep-ratio.

## Bottom line (honest)
Extreme outpaint (keep 5% / generate 95%) = a **plausible-looking but fully fictional** panorama. Great "看看效果" demo of DiT360's generative power; **not faithful** to the real surroundings and **not usable as faithful AV training data** (hallucinated objects + a boxy, lighting-mismatched anchor). This re-confirms the project's standing finding: DiT360 = strong generative panorama baseline, **not** a source-faithful reconstruction of the real 360. The faithful-data path remains: real evidence (the 7 cams / LiDAR) must drive the structure; generation may only continue structure, never invent salient objects.
