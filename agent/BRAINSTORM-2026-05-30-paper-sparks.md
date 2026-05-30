# Brainstorm read-out — 2026-05-30 — paper sparks (3 clusters) + the convergent recipe

Bar decided by user: **PLAUSIBLE** (look like a coherent real street; seam pixels need not be pixel-faithful) **BUT no hallucinated salient objects** (structure-continuation only — a fake car/person teaches the world model wrong stats).

## The底层 problem (agreed, vision-checked on bmw seam crops)
- Seam = parallax (baseline 21-26cm / depth × focal). Near objects double, far is fine.
- L1 = rigid sphere (assumes single center) → far seams near-perfect, near objects DOUBLE + argmax HARD CUT.
- E1.5 vision verdict (corrected, honest): fixes the PHOTOMETRIC seam (color/brightness step) on FAR seams; does NOTHING for the near-field PARALLAX cut (BMW car seam ~unchanged L1 vs E1.5). So "basically solved" was over-optimistic — E1.5 solves color-not-meeting, not geometry-misaligned.
- Why fusion is hard (all NEG this sprint): need accurate per-pixel depth to align two views, but depth starves mid-range (LiDAR sparse / mono not metric), flow starves on textureless, and at wide baseline a held-out cam's centre is seen by NO neighbour (info simply missing). → faithful fusion is 2D-under-determined → only "plausible generation" or "fuse-geometry-then-refine" can win.
- Useful nuance: near-field doubling is LOCAL/NARROW (car edge + tree behind), not the whole object → only a thin band needs fixing.

## Cluster 1 — diffusion-fixes-3DGS (user's instinct; most publishable)
- **Difix3D+ (CVPR25, NVIDIA, public code+weights)**: SINGLE-STEP diffusion that turns a blurry/warped 3DGS render → clean, conditioned on a CLEAN REFERENCE VIEW via cross-view attention (copies from real neighbor, doesn't invent). ~76ms. **Direct fit**: DrivingForward render (doubling-free, blurry) = its input distribution; feed adjacent real ring cam as reference → seam repaired by copying neighbor. Cheap on 1 A100.
- **latentSplat (ECCV24)**: variational Gaussians — confident/overlap → tight regression (faithful); uncertain/seam → generative decode. Gives an uncertainty = where-to-invent mask.
- **3DGS-Enhancer (NeurIPS24)**: VIDEO-diffusion enhancer → cross-view (ring) consistency, restored views fed back to re-supervise. Kills per-seam flicker.
- **GenWarp (NeurIPS24)**: cross-view attention "soft warp" instead of hard warp → no doubling; we have LiDAR depth (stronger than its mono).
- **ProSplat (2025)**: explicitly wide-baseline/low-overlap; 2-stage feed-forward GS + one-step generative refine; MORS reference selection + epipolar-distance-weighted attention.

## Cluster 2 — plausible generative seam-fill (closest to the liked DiT360 image)
- Tools to CONTROL the two things we care about: **Blended Latent Diffusion** (per-step latent confinement → L1 interior provably untouched); **PowerPaint P_ctxt** (continue structure) + P_obj as NEGATIVE (suppress new objects); **NTN-Diff** (null-text low-freq structure completion); **RealFill** (correspondence-based candidate rejection = faithfulness QA); **LaMa** (zero-hallucination deterministic baseline + A/B control).
- Spark: seam-only PowerPaint/NTN-Diff + anti-object negative + BLD-confined + RealFill-gated = DiT360-quality seam, hard "no invented objects" guarantee, all public, zero-finetune.
- Spark: DrivingForward-as-canvas (low/mid freq) + inject only L1 HIGH-freq detail in overlaps under null-text → doubling-free + sharp, can't invent objects.
- Limit: none of these are metric-3D aware → inject LiDAR/extrinsics as conditioning or they continue structure geometrically-wrong-but-plausible.

## Cluster 3 — temporal / multi-frame (fix the info shortage at root)
- Moving vehicle = free real wide-baseline rig over time. Dominant 2024-25 pattern: render REAL 3D points (LiDAR) from target view → diffusion densifies ONLY holes (**StreetCrafter, FreeVS, ViewCrafter**). Point render = geometric LEASH forbidding hallucinated objects. Better aligned to our bar than free FLUX/DiT360, reuses LiDAR+extrinsics.
- Spark: aggregate AV2 LiDAR ±N frames, colorize from 7 cams, render colored points into the ~7 ERP seam wedges, finetune SVD/ControlNet to densify ONLY wedges → E1.5's confined philosophy + geometry-grounded generative fill.
- Spark: ring-consistency loop (walk the 7 seams, fold each fill back as points, re-render next) → enforces 0=360 wrap consistency that per-seam DiT360 can't.
- Limit: high-fidelity temporal methods are per-scene-optimized (slow per log); none native ERP (wedge→equirect plumbing on us); sky/thin/dynamic-at-seam still lean on the prior.

## THE CONVERGENT RECIPE (all 3 clusters + the plan workflow agree)
**Geometry (3DGS / LiDAR points) owns POSITION (doubling-free); diffusion owns APPEARANCE (sharp/plausible); a REAL reference (neighbor pixels / LiDAR point-render) is the LEASH that forbids hallucinated objects.** This is the user's 3DGS+diffusion instinct, backed by Difix3D+ (CVPR25). Confine to seam band; keep L1 far-field byte-exact; gate by relative_warp (geometry) + object-invention audit (SAM) + VISION.

## Decision pending with user: which first decisive spike
Candidates: (1) Difix3D+ on DrivingForward render, seam-conditioned by neighbor cam (most publishable, public code). (2) Seam-only PowerPaint+BLD+anti-object on E1.5 (cheapest, closest to the liked image, ships now). (3) LiDAR-point-render-leash seam densifier (most novel use of our assets). All judged by relative_warp far-field~0 + object-audit + vision on BMW.
