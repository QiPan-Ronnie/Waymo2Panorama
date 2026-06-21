# Decision Briefs — live queue

Convention: completed briefs are archived (one line each) here and recorded in full in `progress.md` (newest-first). Full history: `git log` of this file + `progress.md`.
RESULTS GO IN `deliverables/` — not `agent/` (agent/ is working/evidence scratch only).

---

# DB-105: Route-2 middle-only current-core video set for Xinhan fallback
Status: active - user requested start now on current L4.
Question: with the current Fable-5 core plus shipped `SEAM_FLOWMORPH=True`, are the 4 scenes × 93 exact Route-2 frames clean enough in the middle scene band when sky and ground are black (`GROUND_MODE='off'`) to serve Xinhan's fallback setting: first frame can carry full/ground context, frames 2-93 carry only clean middle-band perspective-to-pano content with no sky/ground loss?
Why: existing `deliverables/route2_middle_v1/` already has the correct frame windows and black sky/ground, but most v1 clips were rendered before DB-103/104; only `highway_seamfixed.mp4` was patched after the near-car flow fix. Xinhan's fallback requires the middle band itself to be robust, so this should be rerendered as an isolated current-core v2 rather than relying on pre-fix v1 frames.
Plan: render the same windows as `route2_middle_v1` / `ground_video_v1` into `datasets/route2_middle_v2`: bmw anchors 0-92, crowd 0-92, clean 0-92, highway 225-317. Use `scripts/phase3/db89_ghost_recovery.py` through the route2 wrapper with only `GROUND_MODE='off'`; no sky fill, no ground fill, no generation, no algorithm tuning, no frame-window changes. Assemble 4 mp4s, fetch to `deliverables/route2_middle_v2/`, then vision-check the middle band.
Kill criteria: stop if the live runtime cannot render with current repo/core, if any scene has missing frames after one forward and one reverse resume-safe pass, if a rendered clip has nonblack sky/ground caused by a wrong `GROUND_MODE`, or if vision review finds recurring middle-band object/seam breaks that DB-103 flow does not address.
Max scope: at most 4 scenes × 93 frames, one isolated output dataset (`route2_middle_v2`), assemble/fetch/review only. No BEV/fill ground, no FLUX/DiT/3DGS, no Waymo, no contract-note expansion, no algorithm edits beyond a reproducible v2 driver.
Required vision check: inspect all 4 mp4s and representative crops/frames for middle-band seam cuts, close vehicles, pedestrians/crowd, lane/curb continuity, and verify sky+ground are black. Compare at least highway v2 against `highway_seamfixed.mp4`; compare crowd against the known mild DB-103 case.
Output: Drive `koi_waymo2pano_colab/datasets/route2_middle_v2/`; local `deliverables/route2_middle_v2/`; result recorded in `progress.md` when complete.

---

## Archived (full record in progress.md)

- **DB-80..DB-93 + V2.1/V2.2** — the Fable-5 recentre breakthrough + the v8 complete-panorama stack (recentred depth-aware mosaic + photometric harmonization + ground fill v8 + FLUX.1-Fill sky). Endorsed core = `scripts/phase3/db89_ghost_recovery.py` + `sky_fill_flux.py`. Deliverable `deliverables/complete_pano_v8/`.
- **DB-97** (ground-fill temporal videos) — DONE: 4 scenes × 93 frames → `deliverables/ground_video_v1/`. The first temporal stress test; it EXPOSED the ground + seam problems below.
- **DB-98** (nadir speckle / black streaks / softness) — root-caused to the near-pole-behind PHYSICAL BLIND SPOT (<4° grazing, ego self-occluded; steeper-view backfire + no-gate-streak-return both confirm). SUPERSEDED by the DB-102 BEV reframe.
- **DB-99 / DB-99a** (nadir 白团 truth-ring plate / whole-log BEV fusion) — the plate idea + the shelved heavy BEV; SUPERSEDED/realized cleanly by DB-102.
- **DB-101** (visibility-consistent ground / middle-only mask) — middle-only (`GROUND_MODE='off'`, BLACK top+bottom) is CLEAN+defect-free across 4 scenes; the 3-way fill/bev/mask choice is folded into DB-102 and gated on the DB-94 Cosmos contract. SUPERSEDED by DB-102.
- **DB-102** (METRIC / BEV ground reconstruction) — DONE/validated. First-principles: the ground defects (speckle/smear/白团/lavender) are per-pixel-ERP-pole-domain artifacts; reconstruct the ground in the METRIC (BEV) domain (depth-reproject to the virtual centre) → defect-free by construction, single-frame + temporally, ZERO params. Near-nadir 0-3 m = sensor-agnostic blind spot (cameras AND roof-LiDAR both can't see under the car) → honest soft + mask → downstream Cosmos. Code `GROUND_MODE='bev'` (additive, gated); NOT flipped to default (ground deliverable = a Cosmos-contract policy, see DB-94). Evidence: `agent/_db102/crops/AB_*`.
- **DB-103** (near-ego scene-band SEAM shear) — SHIPPED (commit aa18629, `SEAM_FLOWMORPH` default ON). The STAGE-3.5 view-morph's ECC-AFFINE registration shears a close object straddling a seam (a309 car `max_reg_px=32`). Fix = dense Farneback optical flow inside the object body, gated on `max_reg_px>8` (surgical: clean seams byte-identical). Validated 4 ways (severe a309 32→8.6 shear-gone, mild crowd, clean unchanged, 6-frame temporal). In the scene band → improves fill/middle-only/bev alike. Deliverable `deliverables/route2_middle_v1/highway_seamfixed.mp4`.
- **DB-104** (ROBUST stitching) — the close-car residual is EXHAUSTIVELY isolated to the PERSPECTIVE physical limit: the two seam cameras see the car's FRONT vs REAR (raw frames), so the overlap has huge perspective disparity; object-box-depth (32→32), YOLO→SAM, and mask-hole-fill (8.64→8.64) are ALL NEG — only the dense flow helps (32→8.6 = the 2-D physical floor). Robustness = the affine→flow ESCALATION (shipped). `SEAM_MASK_FILL` added as a gated general tool (default OFF, fill-holes not dilation → no v7 giant-instance risk). **DEFERRED open sub-item: Tier-2 graceful single-source degrade** (route the seam to the object edge / single-source the more-complete camera) for the un-registrable case — designed (see git), not built (no failing case yet to validate against).

---

# DB-105: Near-field unified solve — step 1: can dense-LiDAR geometric reproject beat "fall back to plane"?
Status: ACTIVE (the one live brief). First-principles framing below; this is the safe, faithful, no-contract-dependency first step.

**First-principles framing (the unification).** "The closer to the AV2 cameras, the worse" = small Z simultaneously inflates FIVE physical quantities: (1) parallax `d_px≈(W/2π)arctan(b/Z)`, (2) occlusion severity (adjacent cameras see DIFFERENT surfaces of a near object — front vs rear), (3) grazing angle (near-ground imaged only at low grazing → stretched source slivers), (4) ERP-pole coordinate stretch, (5) sparse-depth error amplified by b_perp. Split: {1,4,5} are REPRESENTATION problems (already half-cured by DB-102 BEV metric domain); {2,3} are PHYSICAL-OBSERVATION problems (occlusion boundary + grazing/blind-spot) that NO representation can fix. After stripping representation, the residual near-field defect on BOTH routes is pure observation insufficiency: stitching=occlusion boundary (8.6 px floor), ground=grazing low-res + 0–3 m true blind spot. For observation insufficiency there are EXACTLY three responses: (A) TIME (whole-log: a surface unseen now was seen another moment — already used), (B) GEOMETRY (reconstruct 3D, reproject to virtual centre, z-buffer visibility, leave disocclusion holes), (C) PRIOR/GENERATION (fill holes with a learned prior — plausible, not truth). A fails on the true blind spot; B fails on disocclusion holes; C is the ONLY filler of true-blind/disocclusion but is plausible-not-faithful. Literature confirms the modern large-parallax answer is B+C: PIS3R (2508.04236) = VGGT 3D-reconstruct → reproject → point-conditioned diffusion fills holes; MagicRoad (2507.23340) = surfel road + segmentation-guided video inpaint. We are ASSET-COMPLEMENTARY to PIS3R: we HAVE LiDAR (metric, no VGGT needed), calibration, ego-poses, time — it has none. The unified near-field solve = A(whole-log) + B(LiDAR/learned geometry reproject to virtual centre + z-buffer) + C(geometry-conditioned generation for holes, object-veto + temporal-consistent). ONE framework for both routes; it unifies "faithful geometry" and "plausible generation" (geometry owns the observed, generation only the holes, conditioned so it cannot fabricate conflicting salient structure).

Question: on the a309 highway near-car seam (residual 8.6 px after flow, 32 px affine) AND one near-ground fill-speckle patch, does DENSE per-point LiDAR depth + z-buffer visibility, reprojected from the virtual centre, beat the shipped `depth_field` (which Rule-8-degrades to a plane in hard regions) and beat DB-104's box-single-depth (which was NEG 32→32)?
Hypothesis: DB-104 only tested ONE box depth (NEG). Dense per-point LiDAR + z-buffer (the mature surround-view / PIS3R visibility op) may recover the correct visible surface at the occlusion boundary and the curb/near-ground, dropping residual. If it does NOT, the residual is confirmed pure DISOCCLUSION (no source pixel exists) → only C (generation) can fill it → justifies pivoting to the contract+generation experiment.
Why now: user named both near-field routes; PIS3R/OmniStitch say "3D-reconstruct-then-align" is the modern large-parallax answer; this is the faithful, L4-runnable, contract-independent first step that ISOLATES "how much geometry can recover vs how much MUST be generated" (honours isolate-the-variable).
Expected evidence: a309 seam residual px (vs 8.6 flow / 32 affine); near-ground reproject incoherence (vs fill speckle); z-buffer visibility map; disocclusion-hole area (= the mass that still MUST be generated).
Kill criteria: if dense-LiDAR+z-buffer is NOT better than 8.6 px at the seam AND not cleaner than fill at the ground → geometry (B) is spent for near-field → residual is pure disocclusion/blind-spot → STOP B, pivot to C (generation) + the DB-94 contract experiment (two first-frames into Cosmos). Max 2 scenes, DIAGNOSTIC ONLY (no full-pano render).
Max scope: one diagnostic script (dense LiDAR densify + z-buffer visibility + reproject) on a309 + one near-ground patch, L4. Do NOT modify the shipped pipeline; no retrain.
Required vision check: eyeball the reprojected seam (is the car nose un-sheared?), the reprojected near-ground (cleaner than fill?), and the disocclusion-hole map.
Output: `deliverables/db105_nearfield_geometry/`.

---

# DB-94: Xinhan centre-contract confirmation
Status: queued - needs a meeting/message with Xinhan. **This now GATES the ground deliverable choice (fill vs bev vs middle-only mask).**
Question: confirm the downstream Cosmos consumer uses point-cloud first frames whose centre = our ring-camera centroid at camera height (the DB-80 virtual centre), so panorama and point cloud are concentric; and whether it wants a soft-confidence / binary masked hole for the unseen nadir.
Why: if the consumer assumes ego-origin instead, every panorama is ~0.5-1.5 m off-centre relative to the point cloud. If it honors masks, the near-nadir blind spot DISSOLVES (ship bev+mask, let Cosmos outpaint).
Plan: prepare a one-page contract note (centre definition, ERP convention, resolution, axes, mask semantics) from the existing deliverables; review with Xinhan.
Kill criteria: n/a (coordination task).

---

# DB-95: Waymo dataset migration - the next generality gate
Status: queued - the big one (north-star generality).
Question: does the full stack (evidence calculus + ECC-OMC + view-morph + content seam + depth gating + BEV ground) run on Waymo Open Dataset with ONLY loader-level changes (camera count/layout, shutter timing, annotation format)?
Why: the north star is a GENERAL perspective-to-ERP method; AV2 5-scene + no-LiDAR are passed; a second dataset with different ring geometry (5 cameras, different stagger) is the real test that nothing is AV2-specific.
Plan: write a Waymo loader exposing the same frame interface (images, K, T_ego_cam, per-camera timestamps, ego poses, LiDAR, tracks); run the unchanged pipeline on 2-3 Waymo segments; vision-check.
Kill criteria: any fix that requires touching the ALGORITHM (not the loader) must be evidence-principled and scene-agnostic, else record as a dataset-specific limitation.
Required vision check: moving vehicles single-and-intact; seams clean; graceful degradation where evidence is missing.

---

# DB-96: Contact-shadow evidence modelling (icebox)
Status: icebox - known principled gap, low priority.
Question: can the cast shadow be treated as evidence-bound object appendage (dark region adjacent to the object mask, luminance-ratio detected) and moved/kept with the body during compositing?
Why: a remaining visible artefact class on the BMW scene (fill bands show unshadowed background); currently mitigated by harmonic fill.
Plan: only if the downstream consumer flags it; otherwise leave to the generative layer.
