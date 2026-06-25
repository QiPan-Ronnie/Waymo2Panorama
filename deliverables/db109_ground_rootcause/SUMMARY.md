# DB-109 Nadir Ground — Root Cause → Architecture → Deliverable (2026-06-24 autonomous /loop)

One-page consolidation of the ground-outpainting investigation. Evidence files are all in this folder; eyeball them in the order below.

---

## 0. The problem you raised
On the `loop2` video you saw three defects in the nadir ground: ① the ego-car "lid" (车机盖) at centre, ② the road is "tile-ized" / 格子 (faithful pixels but distorted into a quilt of strips), ③ the scene-band lane lines connect into the ground but warp. The earlier Fable-5 version swirled but *occasionally* showed genuinely-restored road; the current one never swirls but never reaches that restored look.

## 1. Root cause (Evidence A, B) — proven, not assumed
- **`evidenceA_evAcoh/evAold_seg_label_spread.png`** (label = which source-frame won each pixel; spread = source agreement):
  - Current (B-coherent): coverage 100%, but **27 source frames quilt the cap** and **median spread 19.8** (borderline). The 格子 = ~27 arc-bands (egod-sweet iso-distance arcs) mis-registered at the seams + the spread>30 red radial streaks + per-frame grazing-stretch.
  - Old (Fable-5): coverage only **5.8%**, but where it wrote, **spread 1.6 = pristine**. → your "occasionally restored road" = those tiny pristine patches; the rest was NS-inpaint = swirl.
- **`evidenceB_sweet_vs_agree.png` / `_crop`**: changing the source PICK (egod-sweet → best-agreement argmin-to-median) moved pixels by only **2.66/255** — the quilt is **NOT selection-fixable**. The selection layer is exhausted.
- **What the 格子 actually is:** radial grazing-stretch + GENUINE multi-view disagreement (spread 15–30) in the high-spread fan — no single source is clean there.

## 2. The hard physical fact (Evidence C)
- **`evidenceC_spread_8_14_30.png` / `_crop`**: tightening the agreement gate SPREAD_MAX → real-render coverage = **6.6% @≤8, 22% @≤14, ~92% @≤30**. At ≤14 the road + lane-lines are CLEAN; the rest is honest plate.
- ⇒ **On a traffic frame only ~6–22% of the nadir cap is faithfully recoverable from pure reprojection. "Full + clean" is physically impossible by reprojection alone.** Blend / world-accumulation fight this ceiling (low cap + blur risk).

## 3. Architecture (you co-decided: A)
> **Faithful spread-gated base (clean, deterministic-coherent) + GENERATIVE fill for the honest holes.**

This resolves all three defects at once: ② abstain the mediocre fan (no quilt), ① + ③ become part of the generated hole, swirl gone (deterministic + pristine only). Matches your own "Cosmos regenerates appearance downstream" framing + the GENERAL north-star.

## 4. Faithful base — built + verified (Evidence D, E)
- **Gate LOCKED at `SPREAD_MAX=14`** (`evidenceD_*`): 14 keeps clean road+lanes & abstains the mediocre fan; 18 reintroduces quilt-creep. Per-frame Laplacian decreases smoothly across a298→a303 → the faithful base is **temporally coherent**. (Precision over recall: the abstain region is the generative-fill region, so a returning quilt would poison the generative conditioning.)
- **`faithfill_mask` verified** (`evidenceE_faithbase_mask_overlay.png`): the mask = the deep-nadir abstain fan + centre cap; the upper near-ground road + lane-lines are KEPT. The mask does NOT eat faithful pixels. → **faithful-base + mask = the Cosmos-ready artifact.**
- New code is all gated, shipped default unchanged: `COHERENT_PICK` (default "sweet"), `FAITH_MASK` (default off), a per-cap winning-source label dump, a BEV raster/mask export.

## 5. Generative fill — what works and what doesn't (Evidence F, G)
- **Vanilla SDXL on the ERP nadir = FAIL** (`evidenceF_base_vs_genfill_a300.png`): streaky + pole-distorted (a 2D model fights the ERP pole; also per-frame = would re-swirl).
- **Vanilla SDXL on the BEV (top-down) domain = also FAIL** (`evidenceG_bevfill_*`): the BEV faithful raster is GOOD (mostly real top-down asphalt + lane-line, only ~14% holes), but stock SDXL left the big occlusion hole black (degenerate on a large hard-edged mask).
- ⇒ **The generative fill is genuinely the DOWNSTREAM stage** — a 360/video-aware model (Cosmos / DiT360, A100-class), NOT cheaply solvable on the L4 with stock inpainting.
- **Silver lining:** the **world-anchored BEV is the right SUBSTRATE** — undistorted, temporally coherent by construction, only ~14% holes. The clean end-state path = world-BEV faithful + a proper generative hole-fill in BEV + ERP reprojection.

## 6. Deliverables in this folder
- Faithful-base + mask renders (a298–303, and a285–317 video being rendered: `faithbase_full_h264.mp4` / `faithbase_nadir_h264.mp4` / `faithbase_nadir_montage.png` / `evidenceH_oldquilt_vs_faithbase.png`).
- All `evidence{A..H}_*` boards (the proof chain above).
- `{run}_faithfill_mask.png` per frame = the generative-fill region for Cosmos.

## ★★ FINAL HONEST STATE (2026-06-25, full-res eye-verified — supersedes the optimistic notes below; 4 over-claims were caught by the user this session)
The ground splits into two PHYSICALLY-DISTINCT parts:
- **Near-field road (directly seen, below the horizon) = GENUINE real + clean.** Bright/textured scenes (bmw/crowd/clean) are mostly this → their good frames (`GOODFRAME_{bmw,crowd,clean}_dit360.png`) are VALID genuine real road (lane-lines, crosswalks) + a tiny DiT360-filled hole.
- **Deep nadir CENTRE (directly below/behind the ego) = ALWAYS self-occluded by the ego's own hood/trunk** (front-pod rig) → NO clean real-road view exists in ANY frame (self-occ ON → 0% there, eye-verified `evidenceAA`). Physically GENERATION-ONLY.
- **Genuine non-generative real coverage ≈ 23% on the hard dusk highway** (the near-field; = the user's remembered "20%+" = what Fable-5/the good frames render with self-occ ON), much higher on bright/textured scenes. **The earlier Lever-1 (41%) / world-map (84%) ADDED hood sky-reflection smear on top — RETRACTED as fake. Coverage% is NOT a realness proxy.**
- **Honest deliverable: `GOODFRAMES_honest_final.png`** = bmw/crowd/clean (genuine real, clean complete) + highway `GOODFRAME_hw_v5_honest.png` (~23% genuine real near-field + FLUX-img2img-refine the honest deep hole; honest = real where real, generated where physically blind).
- **The deep-nadir-centre generation, for TEMPORAL VIDEO coherence, is the downstream COSMOS video model's job** (single-frame FLUX/DiT360 flicker). The one strategic decision left.
- **LESSON: eyeball EVERY result at full native resolution before any "clean/solved" claim — brightened crops + coverage% fooled me 4×.**

## ★ UPDATE (2026-06-24/25) — (optimistic, partly retracted above) GOOD FRAMES via faithful base + DiT360
The generative stage is no longer a TODO — **DiT360 (360-native, FLUX.1-dev + Insta360 LoRA) runs OFFLINE on the A100** from the Drive cache (`external/DiT360` + `cache/huggingface`). Recipe: noise-fill the hole → DiT360 (`weight_name=adapter_model.safetensors`, tau=10, `--disable-vae-tiling`) → hard composite (faithful base exact). Because it is 360-native there is **no ERP-pole distortion** (which broke vanilla SDXL in both ERP and BEV).
- **`GOODFRAME_bmw_dit360.png`** (bmw, 96.8% faithful + 3% DiT360) and **`GOODFRAME_crowd_dit360.png`** (crowd, 64% + DiT360) = **clean COMPLETE 360 ground** — asphalt + lane lines + crosswalk continue into the nadir, no quilt/swirl/blocks/holes. See the set in **`GOODFRAMES_bmw_crowd_highway.png`**.
- **Coverage spectrum (`evidenceK`)**: bmw 96.8% > crowd 64% > clean 35% > highway 22% > downtown 0% — the faithful method is condition-dependent; good/medium scenes give clean complete frames; only the hardest (dusk highway, downtown-idle) need more (highway = honest best-effort; downtown-idle = a candidate-window edge case = known open item).
- DiT360 tuning evidence: `evidenceL_dit360_tau_sweep.png` (tau=10 cleanest; higher tau → blocks/hallucinated lanes).

## 7. Recommendation / next co-decide (for you)
The faithful half is **solved and clean**. The remaining piece is the generative fill, which needs **your call on the downstream model + GPU**:
1. **Cosmos (your existing downstream plan)** — feed it the faithful-base + faithfill_mask; it regenerates appearance with video coherence. Needs the DB-94 Cosmos contract confirmed (does it ingest the mask?).
2. **DiT360 / FLUX-fill in BEV domain, then reproject** — clean + coherent, but needs an A100 (FLUX = 24 GB, OOM on L4) and engineering.
3. **Ship the faithful-base + honest mask as-is** (clean real ground + neutral hole) if "honest-but-incomplete" is acceptable for the current milestone.

My recommendation: **lock the faithful-base (done) as the foundation, then route the holes to Cosmos (option 1)** — it's your intended pipeline, gives video coherence (which any per-frame 2D inpaint cannot), and keeps the method GENERAL. Confirm the Cosmos mask contract (DB-94) and I'll wire the faithful-base + mask into it.
