# Codex (gpt-5.5 xhigh) adversarial verdict #1 — 2026-06-02

Clean extract of codex's final answer (full noisy transcript with tool calls: `2026-06-02-codex-adversarial-01.log`). Prompt: `_prompt_01.md`. Images shown: my L1-vs-graphcut, DrivingForward ERP, gray-car ghost zoom.

## Blunt verdict
My conclusion is **partly right but overbroad**.
- **CORRECT:** single-source, single-registration seam routing cannot create a true ego-center view — it only chooses which off-center camera owns each pixel.
- **WRONG/incomplete:** "mixing ⇒ artifact" is FALSE. Naive ALPHA blending ⇒ artifact. Production systems do NOT average misaligned sources:
  - **Meta Surround360** deghosts TOWARD a source using flow magnitude (softmax; prefers larger-flow = nearer = foreground). `NovelView.h:47`, `NovelView.cpp:78`.
  - **Jump** composites splats in DISPARITY ORDER (over-operator, interval-based).
  - **Google** does a global smooth warp optimization (spline + Ceres).
- **My bug:** my view path STILL does `novel = warp_i*(1-a)+warp_j*a` (run_a1 line 276) = "alpha blend with abstain", NOT Meta/Jump occlusion-aware compositing. **So I never implemented deghosting → my "2D ceiling = L1" may be SELF-INFLICTED.**

## What I'm missing
**Layered visibility, not more graphcut.** Google's later stitching paper: a single registration fails with depth variation → use MULTIPLE registrations + MRF seam + anti-duplication/tearing terms → moves toward layer-based stereo. My graphcut has ONE warped image per camera, not multiple depth hypotheses/layers. Jump: averaging splats ghosts; separate surfaces must be composited in disparity order with the over-operator — that is the family to test, using rig/LiDAR/flow as geometry evidence, NOT alpha blending.

## Can 2D fix near-field doubling?
- **YES** for co-visible Lambertian surfaces with good correspondence — dense flow warps both views to an intermediate ray and collapses duplication. **The gray car is exactly this case** (exact Meta deghost may fix it).
- **NO** for occlusion/disocclusion — two cameras see different background → 2D has no hidden surface to recover. Parallax: near object 3 m / 25 cm baseline ≈ 27 px shift; 10 m ≈ 8 px. True ego-center needs depth/layers/visibility.

## Diffusion / 3DGS
- **Do NOT use diffusion as the core solver for world-model data** — it hides artifacts by rewriting evidence. Only for sky/top/bottom or tiny disocclusion holes WITH a generated-region mask. (Matches the user's "don't let DiT free-generate" + the DB-12 object-gate.)
- Better path: **source-grounded single-center rendering** — dense MVS/flow-triangulated LDI or learned 3DGS, LiDAR-anchored, source-fidelity masks. Classical LiDAR DIBR is the right CLASS but IP-Basic completion is too sparse/smeary at boundaries. DrivingForward has the right representation class but is not deliverable quality yet.

## ★ Single decisive next experiment
One-seam **Jump-style LDI / disparity-ordered over-composite kill-test** on the BMW / gray-car seam:
1. RAFT/GMFlow or plane-sweep MVS on the two raw adjacent cameras (constrained by AV2 calibration).
2. Triangulate confident correspondences; fuse with LiDAR hits.
3. Render only that seam crop to ego-center ERP with z-buffer / disparity-interval OVER-compositing.
4. NO diffusion. Holes stay marked or fall back to L1 with a mask.
5. Compare: **L1 | my alpha view-interp | exact Meta deghost softmax | depth-ordered over**.

**Decision rule:** if depth-ordered-over yields ONE sharp object with only small holes → the 2D ceiling was self-inflicted (alpha-blend was the bug). If it fails on confident geometry → STOP 2D polish; the remaining path is learned dense geometry/3DGS.
