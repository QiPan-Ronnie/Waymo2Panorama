# Adversarial round 10 — DiT360 exploration plan (gpt-5.5 xhigh, be my opposition). Big autonomous goal, A100 always on.

You are my adversarial counterpart. Be ruthless, concrete, prioritize. The user gave a big goal: explore DiT360 for our AV panorama as far as possible on an A100, overnight.

## Context
- Source-faithful 7-cam AV2 ERP panorama (non-co-located ring cams). Our best non-generative deliverable = `_seamroute.py` → `SR_bmw_bevfinal_1024x2048.png` (align + object-moat seam + virtual-centre select + BEV ground atlas). Residual visible defects: (1) the near-ground seam is slightly WAVY (parallax kink, proven a physical floor for non-generative); (2) black sky/ground band (no top/bottom cameras — vertical FoV gap, the biggest gap to "Google-Maps look").
- DiT360 (arXiv 2510.11712, Insta360): FLUX.1-dev + LoRA, hybrid TRAINING (image-level perspective guidance + panoramic refinement; token-level circular-padding + yaw-loss + cube-loss). Inference = RF-Inversion + PersonalizeAnything attention. We have it running on A100 (FLUX + DiT360 LoRA cached). Our existing pipeline `run_dit360_trimap_clamp.py` does trimap latent-clamp: core seam = free generate, halo = soft clamp, far = byte-clamp to source. Circular latent padding already applied.
- Prior DiT360 history (all-agent, May): 16 seam variants — most ≈ no-op or cosmetic; v14 trimap-clamp = best fidelity but RAW≈hard_select. Full-frame center-OUTPAINT (sky/ground) = looked complete but HALLUCINATED cars/vans → rejected as unfaithful. The user now wants to RE-TRY outpaint "from different angles, see if it's really unusable."

## My planned exploration (CRITIQUE + REPRIORITIZE this)
1. **Seam completion** on the bevfinal init: trimap corecompose; sweep tau / halo_px / core-radius / guidance / steps to find the sweet spot that visibly smooths the wavy seam WITHOUT inventing objects.
2. **Yaw-ensemble** (new, from the yaw-loss): roll the pano by several yaw offsets, run seam completion each, average/median-merge — or roll so seams sit at benign azimuths. Hypothesis: yaw-robust model → ensemble reduces seam variance.
3. **Outpaint the black sky/ground** (user's added goal), multiple angles: (a) thin structure-continuation band only (extend road/facade/sky a little, not full hemisphere); (b) sky-only outpaint (top is low-risk — sky has no objects); (c) ground-only; with anti-object/SAM gate. See if constrained outpaint is usable as faithful-ish data.
4. **Object-safety gate** (SAM/YOLO band-diff) on every generative output → reject if net-new salient objects.
5. Multi-anchor (0bae/2c65) once BMW config is good.

## Your job
1. **Reprioritize**: given the user cares most about (a) the wavy near-ground seam and (b) the black-band "Google-Maps look", which of my 5 is highest expected value? Which will likely fail and why?
2. **Seam**: is trimap-corecompose the right tool, or does the wavy GROUND seam need something else (the core is a thin vertical strip; the wavy kink is on the near-ground — will a thin-seam regen even touch it)? Should the core mask follow the ground wave specifically? What tau/guidance regime avoids both no-op AND hallucination?
3. **Yaw-ensemble**: real or gimmick? How to merge without blurring (median? only where consistent?).
4. **Outpaint**: is sky-only / thin-band outpaint genuinely safer than the rejected full-outpaint, or will it still hallucinate at the band boundary? What mask + guidance + prompt would make it faithful-ish, and what's the cheapest kill-test? Be honest if it's a dead end for FAITHFUL data but fine for a "plausible" demo.
5. **From the paper**: what DiT360-specific capability are we NOT exploiting at inference (cube-consistency via py360convert? perspective-guidance? RF-inversion gamma/eta? the panoramic-refinement pass)? Name concrete inference-time uses.
6. What's the single highest-value experiment to run FIRST after the baseline, and the kill-test for each direction?

Prefer concrete, cheap-first kill-tests. Don't flatter.
