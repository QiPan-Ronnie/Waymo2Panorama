# Decision Briefs — live queue

Convention: completed briefs are archived in progress.md (newest-first) and deleted here. Full history: git log of this file + progress.md.

---

## Archived (see progress.md)

DB-80..DB-93 + V2.1/V2.2 all completed and recorded in progress.md (milestone tags: db90-v3-porsche-solved, db91-grain-consensus-fixed, db92-generality-pass, best-pano-v2-5scenes, best-pano-v2.1-defringe, v2.2-harmonic-fill). DB-93 (sky + ground completion) CLOSED 2026-06-12 — ground fill v8 (whole-log geometry eligibility, two-box ego self-occlusion, evidence-resolution nadir render) + FLUX.1-Fill sky; deliverable `deliverables/complete_pano_v8/`; commits bfbc244/c589aa7/4f50ec8/a9e3497/f7a8a14.

---

# DB-103: Near-ego large-parallax SCENE-BAND seam misalignment (the determinable-seam frontier)
Status: SHIPPED (2026-06-19, commit aa18629 — `SEAM_FLOWMORPH` default ON). Full arc: audit → root-cause → isolation-NEG → scope sweep → fix → 4-condition validation (severe a309, mild crowd, clean byte-identical, 6-frame temporal stable). Reverts via `SEAM_FLOWMORPH=False`; pristine core in `_baseline_fable5/`.
RESULT: the near-ego car shear = the STAGE-3.5 view-morph's ECC-AFFINE registration failing on a close object's depth-varying (non-affine) parallax (front_left/side_left `max_reg_px=32`). Isolation NEG: forcing object-box depth changed `max_reg_px` by 0 → NOT the depth field. Scope sweep: sporadic-but-recurring, more in busy scenes (crowd a50=10/a85=6.38, highway only a309, bmw clean). FIX = `SEAM_FLOWMORPH` (default OFF until temporal-regression-confirmed): gated on `max_reg_px>8`, replace the affine displacement with dense Farneback optical flow INSIDE the object body. Validated: a309 32→8.6 (shear gone, eyeballed), crowd a50 nudged 10→6.9 (no harm), clean seams byte-identical by construction (gate can't fire). Commits a0d4735/efb8c20/feb08f8/9424bdf/6199346. UNIFIES with DB-102: same physical root (near-field large-parallax 2-D stitching); ground cured by metric-domain depth-reprojection (BEV), objects by flow registration. Backup `_baseline_fable5/` pristine (0 of the new flags) + in git (1537bfe).
[original audit-first brief retained below for the record]
Status was: ACTIVE — Audit-first.
Finding: even the clean middle-only stitch mis-stitches a CLOSE object — a silver sedan ~few m on the left (highway a309, video 00:07): its FRONT (front wheel / bumper / headlight) STEPS/SHEARS at a ring-camera seam; the seam moves with the car (= camera-seam-through-object). The other 3 scenes don't show it ⇒ object-and-geometry-specific (a close object happens to cross a seam). User intuition: "near-ego scenes have many problems" — a real frontier.
First-principles (UNIFIES with DB-102 BEV): a close object has LARGE inter-camera parallax (disparity ∝ baseline/depth); the 2D seam stitch cannot align it — OMC compensates MOTION/shutter only (parallax-blind), the view-morph FLOW breaks on large disparity, the content-DP min-diff seam cannot avoid a large near object. SAME ROOT as the ground defects: near-field large-parallax 2D stitching. Ground was fixed by DEPTH-AWARE reprojection to the virtual centre (BEV); near-ego objects need the same. db89 ALREADY uses a LiDAR depth field `Zd=depth_field(lidar,C)` for the scene band, so the failure is one of: (a) Zd too sparse/coarse at the near-car edge → interpolates across the car/background depth discontinuity → reprojection error; (b) the car is a MOVING object on the OMC/secondary-body path (2D shift, parallax-blind); (c) the content-DP-seam routes THROUGH the car. AUDIT decides before any fix.
Plan: render highway a309 scene band (GROUND off) on the L4, emit/inspect source-cam seam path + moving-vs-static of the car + Zd at the car + the existing `omc_shifts`/`view_morph` report (`max_reg_px`, `seam_diff_med`); full-res car crop. Then fix the identified cause — depth-aware, ADDITIVE, keep the Fable-5 core backup (`_baseline_fable5/`) untouched.
Kill criteria: ZERO per-scene params; if the near-car is genuinely under-determined (no depth / occluded across the seam) → single-source the object (object-moat) or abstain, NEVER fake-align. Any core-algorithm change must be evidence-principled + scene-agnostic.
Required vision check: the near-car front no longer shears at the seam; NO regression on the determinable seam / moving-car intactness (DB-84/85) / the 5-scene baseline.
Output: audit crops `agent/_db103/`; code (if any) additive in db89; brief + progress synced.

---

# DB-102: Metric-domain (BEV) ground reconstruction for the determinable annulus + honest mask on the blind core
Status: STEP 1 DONE / PROMISING (2026-06-19) — first-principles re-attack of ground outpainting post-Bosch (user "Musk-mode" goal).
RESULT (3-scene A/B, eyeballed + BEVDIAG): **`'bev'` renders the WHOLE cap coherently and DEFECT-FREE** (BEVDIAG rendered≈0.99-1.0 at 0-5 m — it does NOT abstain; near-nadir comes out clean flat = honest low-res). The metric-domain fusion removes ALL the complained defects (highway crosswalk smear / bmw soft-wash-lavender / crowd radial speckle) — they were per-pixel-ERP-pole-domain artifacts, gone in the coherent metric raster, ZERO scene params. The near-nadir is honestly SOFT (irreducible: 0-3 m ground is ~80 % of cap PIXELS in ERP and only ever seen at <4° self-occluded grazing → no sharp texture exists). So the first-principles answer = reconstruct in METRIC domain (coherent real where resolvable + honest soft where not) + alpha-mask → downstream Cosmos for sharp near-nadir. **Honest tension (DB-98):** the clean-flat near-nadir is the same irreducible "soft/blur" the user disliked once — bev makes it CLEAN-soft vs fill's DIRTY-smear; sharp = generative only. Code: db89 STAGE-4 `GROUND_MODE='bev'` (additive, gated). Open next routes: (1) TEMPORAL bev (shared whole-log world raster → kill the video flicker, bev's other structural win — untested); (2) optional world-seeded asphalt grain on the soft core (cosmetic, no fake structure); (3) accept bev + mask + Cosmos as the deliverable.
[superseded sub-lines below were the pre-result plan]
Status was: ACTIVE — AUDIT-GATED.
Question: does reconstructing the determinable near-ground (3-7 m, nvalid≈6) in the METRIC ground plane (a LOCAL BEV raster: best-view-per-cell, source-occlusion z-buffered, tone-normed) then resampling into the ERP cap BEAT the current PER-PIXEL ERP fill (speckle / flicker / pole-singularity) — while the truly-blind core (0-3 m near-pole-behind, nvalid 1-3 @ <4°) is honestly MASKED?
First-principles why (question the DOMAIN):
- The ground is a 2-D plane; ERP parametrizes the sphere of DIRECTIONS and is SINGULAR at nadir → one ERP cap pixel ↔ a hugely-stretched source sliver → bilinear amplifies asphalt/JPEG noise → speckle; and per-pixel argmin source pick (db89 L1206) makes neighbours draw DIFFERENT (frame,cam) sources → spatial incoherence + cross-anchor flicker. BOTH defects are artifacts of reconstructing in the WRONG domain.
- The natural domain for a planar surface is a metric BEV raster: UNIFORM ground resolution (no pole singularity), ONE coherent fusion (no per-pixel jump), occlusion + tone handled ONCE, THEN a clean geometric warp to ERP at output only.
- Evidence REOPENS it (vs the shelved DB-99a): this session's data correction showed the cap core/ring is nvalid≈6 @ ~9 m / 11° (NOT the 1-3 universal blind spot that shelved DB-99a) — only the near-pole-behind wedge is 1-3. So the determinable annulus is genuinely recoverable and BEV is the right tool for exactly it.
Mechanism (reuse db89 STAGE-4 machinery — cte/cals/gseg_blocked/boxes_at/bilinear/LiDAR-height/displacement-bucket candidates; ZERO scene params):
  1. Build a LOCAL BEV ground raster in city coords around the ego (~18 m × 18 m, 4 cm/px), cell centres lifted onto the LiDAR ground-height field (same cKDTree as L1058).
  2. Per cell, over the same bucketed candidates × ring cams: project, apply the SAME gates (FOV, egod 5-28 m, moving-box occlusion, two-box ego self-occlusion) + a SOURCE-side LiDAR z-buffer (cell must be the first surface along the source ray → kills hood/object/mover leak = DB-101 defects 2,3). Keep the BEST view per cell (min egod / least grazing), tone-normed per source.
  3. Resample: each ERP cap ray → its LiDAR-height ground point Xg_city → bilinear-sample the BEV raster → coherent, sharp-as-grazing-allows, NO per-pixel jump.
  4. Evidence gate per cell: nvalid≥K & grazing≥θ & post-tone colour-MAD≤m → REAL; else ABSTAIN → blind core masked (DB-99 plate / alpha) for the downstream consumer.
  5. Global truth-ring gain (unchanged, view-dependent → Fresnel-safe).
Plan (AUDIT-GATED — measure before building, per [[feedback-isolate-input-variable]]):
  - STEP 0 (NO-render audit, L4): GROUND_MODE='bevaudit' dumps per BEV cell {nvalid_after_occlusion, best_grazing_deg, azimuth_spread, tonenorm_colour_MAD, ring_radius_m} for 3 anchors (highway a260, bmw a047, crowd a045). Analyse the RADIAL profile. GATE: 3-7 m annulus nvalid≥4 & grazing≥~8° & low MAD → GO build the render; if the annulus is itself starved → determinable region smaller than believed → record + fall back to middle-only.
  - STEP 1 (if GO): implement the BEV render branch; render the 3 anchors; A/B vs current db89 (speckle/coherence) and vs middle-only (coverage). Eyeball every image.
Pro/con (adversarial, both sides):
  + kills pole-singularity + per-pixel speckle + flicker BY CONSTRUCTION; gives Cosmos/standalone REAL near-ground instead of masking ~44%; the source z-buffer also kills smear/ghost/car-eaten (DB-101) for free; unifies BEV+visibility+abstain.
  − misregistration on the 8.4% non-planar cap → double-image (mitigate: LiDAR-height cells + best-SINGLE-view not a mean + planarity/agreement gate); BEV adds compute (mitigate: LOCAL tile not whole-log); the 3-4 m ring may still be soft at grazing — honest physical resolution, acceptable IF coherent & non-flickering. If audit says annulus starved → BEV dead, honest fall back to mask (still a finding).
Kill criteria: ZERO per-scene params (any scene-specific constant = fail). nvalid<4 across the annulus → BEV dead-on-arrival, ship middle-only mask + record. Double-image/ghost on curbs → cap confidence / best-single-view / abstain, never tune per scene.
Required vision check (eyes>metrics, multi-scene): determinable annulus COHERENT (no speckle/jump/flicker), curbs+lane-lines SINGLE (no double-image), occlusion-correct (no car-eaten / hood-ghost / smear); blind core honestly masked, not fabricated.
Output: code in scripts/phase3/db89_ghost_recovery.py (bevaudit + bev branches, LOCAL/uncommitted until validated); audit npy/json + panos in datasets/db102_bev/ on Drive; analysis + ledger in progress.md.

---

# DB-94: Xinhan centre-contract confirmation
Status: queued - needs a meeting/message with Xinhan.
Question: confirm the downstream Cosmos-style consumer uses point-cloud first frames whose centre = our ring-camera centroid at camera height (the DB-80 virtual centre), so panorama and point cloud are concentric.
Why: if the consumer assumes ego-origin instead, every panorama is ~0.5-1.5 m off-centre relative to the point cloud.
Plan: prepare a one-page contract note (centre definition, ERP convention, resolution, axes) from the existing deliverables; review with Xinhan.
Kill criteria: n/a (coordination task).

---

# DB-95: Waymo dataset migration - the next generality gate
Status: queued - the big one.
Question: does the full stack (evidence calculus + ECC-OMC + view-morph + content seam + depth gating + harmonic fill) run on Waymo Open Dataset with ONLY loader-level changes (camera count/layout, shutter timing, annotation format)?
Why: the north star is a GENERAL perspective-to-ERP method; AV2 5-scene + no-LiDAR are passed; a second dataset with different ring geometry (5 cameras, different stagger) is the real test that nothing is AV2-specific.
Plan: write a Waymo loader exposing the same frame interface (images, K, T_ego_cam, per-camera timestamps, ego poses, LiDAR, tracks); run the unchanged pipeline on 2-3 Waymo segments; vision-check.
Kill criteria: any fix that requires touching the ALGORITHM (not the loader) must be evidence-principled and scene-agnostic, else record as dataset-specific limitation.
Required vision check: moving vehicles single-and-intact; seams clean; graceful degradation where evidence is missing.

---

# DB-97: Ground-fill temporal-consistency videos (4 scenes × 93 consecutive frames)
Status: v1 DONE (2026-06-18) - all 4 scenes 93/93 assembled + downloaded to `deliverables/ground_video_v1/`; vision-scrubbed (scene band + mid-ground clean across all 4; residual = bottom-nadir softness, no black wedges). v2 re-render GATED on the DB-98 (b)/(c) decision. See progress.md 2026-06-18 entry.
Question: does the scene-band + ground-fill pipeline (NO sky) hold up frame-to-frame when run over a continuous 93-frame window, and does it make a coherent moving video?
Why: every result so far is a SINGLE anchor. A continuous clip (a) is a far stronger demo for Bosch / the world-model consumer than stills, and (b) stress-tests temporal stability of the ground reprojection across consecutive anchors (no per-frame flicker / no coverage collapse mid-window).
Scope: 4 of the 5 logs (pick the ones with sustained ego motion across the window — ground fill needs ego displacement; a stationary stretch starves it). For each: one window of 93 CONSECUTIVE anchors (start frame chosen by motion profile). Per anchor: full `run_case` (scene band + STAGE-4 ground) — sky LEFT BLACK (do NOT run sky_fill_flux). Assemble each scene's 93 panoramas (ordered by anchor) into one mp4 → 4 clips. Output: `datasets/av2_ground_video_v1/<tag>/frame_NNN.png` + `<tag>.mp4` on Drive.
Method: adapt the proven `dataset_gen_av2.py` template (CASES = consecutive anchors, dataset-mode lean saves, per-anchor try/except isolation, resume-safe skip-if-exists). 372 renders total (~3 min each) → multi-hour batch; checkpoint by per-frame PNG so a disconnect resumes.
Kill criteria: if a chosen window shows persistent `low_coverage_warning` (stationary ego), RE-PICK the window — never tune the algorithm per scene. If consecutive frames flicker/jitter in the ground band, record as a temporal-stability finding (candidate-selection determinism), do not paper over with smoothing.
Required vision check: scrub each mp4 — ground band stable (no per-frame lane-line jumping), moving cars single-and-intact, no new artifact class vs the single-anchor v8 result.

---

# DB-98: Ground-fill quality — frosted speckle + jagged black streaks (exposed by the DB-97 video)
Status: ACTIVE (2026-06-18) — root-caused from code; fix + confirm pending. (DB-97's 3 other scenes keep rendering meanwhile; this gates DB-97 video QUALITY, not its completion.)
Trigger: playing the bmw ground-fill video, two artifacts that single-anchor stills under-showed: (A) a FROSTED SPECKLE over the whole filled ground, and (B) JAGGED BLACK streaks/wedges in the near-ground corners that worsen at later (open-intersection) anchors. Plus the known lavender cast.

Root causes (code-grounded in `db89_ghost_recovery.py` STAGE 4):
- **(A) speckle = per-pixel independent source selection.** `pick = np.argmin(dist_s, axis=0)` chooses the rendering source PER PIXEL. Adjacent ERP nadir pixels therefore draw from DIFFERENT (frame,camera) sources, which at 4–6° grazing are stretched differently and carry different auto-exposure → a salt-and-pepper mosaic. The resolution-matched low-pass only partially masks it. → ALGORITHMIC, not physical.
- **(B) black streaks = no source-pixel-validity check.** `bilinear()` (L157) only clips coords; the FOV gate (L1086) only tests the rectangular border `px∈[2,ww-2]` + occlusion. AV2 ring images have BLACK/vignette borders; at grazing angles nadir rays land near a source image's edge and bilinear-sample those black pixels → nonzero-DARK values (so NOT caught as holes by `resid_m` sum<12 → never Telea-filled) → the ERP warp smears them radially into jagged black wedges. Worse at open-intersection anchors where more nadir points only have grazing edge-of-image sources. (Secondary: genuine coverage holes where no clean source exists.)
- **lavender = grazing Fresnel sky reflection** (physical; global cast-gain partly handles it; stronger in bright open scenes).

Deeper principle: the innermost nadir annulus (under/just-behind the car) is UNDER-DETERMINED at most anchors — only grazing, edge-of-image, exposure-mismatched sources. Forcing full-res fill there yields grain+black+lavender that flickers. Evidence-gated doctrine → where evidence is insufficient, ABSTAIN cleanly (smooth/neutral) rather than render garbage.

Fix directions (to A/B test, eye-verified, zero scene params):
1. **Source-validity mask** (fixes B): reject a sample whose sampled source pixel (small neighborhood) is near-black/invalid (AV2 border/vignette) or below a luminance floor; rejected → no-sample → falls to smooth residual inpaint instead of dark streak. Also widen the FOV margin beyond 2 px.
2. **Spatially-coherent source assignment** (fixes A): pick ONE source per nadir sector/region (or graph-cut labeling like the scene-band content seam), not per-pixel; or median-blend within a source-consistent region. Cuts the mosaic.
3. **Evidence-quality abstain for the inner annulus**: where only grazing/invalid sources exist, render a smooth low-pass/neutral disk (honest) rather than speckle.
**REVISED after pro/con adversarial pass (2026-06-18):** my first-pass causes were over-confident; the devil's-advocate corrected them:
- **Black streaks — border-leak hypothesis DOWNGRADED.** AV2 ring images are full-frame rectangular RGB (1550×2048), likely NO black border/vignette → "border leak" probably wrong. Upgraded suspects: (iii) genuine COVERAGE HOLES (nvalid=0 points) + Telea producing radial streaks when inpainting large holes (Telea does exactly this), and (ii) ego self-occlusion leak (two-box miss → dark car body). The salt-and-pepper black inside the wedges looks like per-pixel NaN→0 = holes, not a contiguous dark smear. ⇒ if it's holes, my proposed source-validity mask would make it WORSE (more rejects → bigger holes).
- **Speckle — patchwork hypothesis DOWNGRADED.** The speckle is LUMINANCE noise on gray road, not colored patches; the 6-slot median+nearest-to-median pick keeps neighbors near-median (weak patchwork). Upgraded suspect: grazing UNDERSAMPLING/aliasing — one ERP pixel ↔ a hugely stretched source sliver → bilinear amplifies asphalt texture + JPEG noise. ⇒ region-coherent source assignment would NOT fix undersampling (one stretched source is still noisy).
- **Inner-nadir ABSTAIN promoted to primary route.** For a VIDEO, temporal stability > per-frame detail; the under-determined inner annulus is better rendered as a smooth/stable honest fill than detail-that-flickers. This is our own evidence-gated doctrine, not yet applied to ground.
- **Honest caveat:** v8 "ground clean" was eye-checked on favorable stills (a000); the video is a more honest stress test that reveals ground-fill is marginal at open-intersection anchors. Tell koi.

DECISIVE DIAGNOSTIC (must run BEFORE any fix — isolate the variable): instrument STAGE 4 on bmw a092 (worst frame) to emit (1) holes→magenta map (does the jagged black turn magenta? → coverage holes (iii)), (2) nvalid source-count heatmap, (3) slot0-only single-source render (does speckle persist? → undersampling, not patchwork), + check one a092 source JPEG for black borders. The 3 outcomes pick the fix; do NOT code the fix on a guess (cost us 2 days before — see [[feedback-isolate-input-variable]]).
Kill criteria: any fix must stay evidence-principled + scene-agnostic; if speckle is irreducible without over-smoothing, prefer the abstain route over fake detail. Reject the source-validity mask if the diagnostic shows black=holes.

**DIAGNOSTIC RESULT (a047 worst + a092 clean, 2026-06-18) — ROOT CAUSE CONFIRMED, both earlier hypotheses REJECTED:**
- holes_pct = 0.2–0.3% (tiny scattered) → black wedges are NOT coverage holes. dark_filled (lum<40) = 0.0% → NOT dark-border/hood leak. AV2 images full-frame (border-leak dead).
- The `_DIAG_nvalid` heatmap is decisive: the cap is almost all nvalid=6 (white), but the black-wedge corners are GRAY STREAKY regions with nvalid=1–3, exactly co-located with the render's black streaks.
- ⇒ **TRUE CAUSE: extreme-grazing, very-low-source-count corners.** Those near-nadir-behind points are seen by only 1–3 sources at <4° grazing; the samples are unreliable dark-grey stretched slivers (lum 40–90, so they slipped my <40 dark check) that the ERP warp smears into streaky black wedges. The whole-cap speckle is the SAME mechanism (grazing undersampling), milder where nvalid is high. This is the under-determined-inner-annulus the pro/con flagged.
- ⇒ **FIX = evidence-quality gate + smooth abstain (NOT validity-mask, NOT region-labeling):** render real pixels only where evidence is sufficient (e.g. nvalid ≥ 4); where source support is low, ABSTAIN → smooth Navier-Stokes inpaint from the good neighbourhood (NOT Telea, which streaks) + extend the resolution-low-pass over the whole cap to kill speckle. Threshold is on source count / grazing angle = physical, scene-agnostic, zero per-scene params. Temporally stable (smooth abstain doesn't flicker). A/B target: a047 (wedges gone, smooth) + a092 (no regression, stays sharp).
Required vision check: scrub the re-rendered bmw video — no speckle, no black streaks, ground stable frame-to-frame, lane lines continuous.

**POST-DIAGNOSTIC IMPLEMENTATION & EXPERIMENT LEDGER (2026-06-18) — incl. ALL failures, per record-everything doctrine:**

- **Fix 1 — source-agreement spread-gate + NS inpaint (commit e272011).** spread = mean per-pixel source-disagreement; abstain where spread>30 → Navier-Stokes inpaint (NOT Telea, which streaks); where evidence sufficient, pick nearest-to-median source. Result: streaks REMOVED on a047, a092 no regression — ✓ on stills. ❌ **USER-REJECTED on video**: the abstained corners read as BLUR/虚化 ("白团"); smooth abstain looks like makeup-blur, not honest texture.
- **Fix 2 — LiDAR ground-height reprojection (commit 75423ac).** Hypothesis: streaks = flat-plane assumption wrong at curbs/slopes → at grazing each source samples a DIFFERENT world point → disagree. Fix: march cap rays onto the measured LiDAR ground surface (cKDTree height field, 3 iters) so all sources sample the SAME real point. DZMAP diagnostic confirmed ground non-flat (8.4% of cap >15 cm dev, p95=27 cm, LiDAR 100% coverage) **but only PARTIAL co-location** with the spread streaks (right curb = both bright; left corner = spread-bright but height-dev NOT) → TWO causes, LiDAR-height addresses only one. Committed (a092 no regression). ⚠️ **commit message OVERCLAIMED** — see no-gate test: LiDAR-height alone does NOT remove the streaks.
- **FAILED / NEGATIVE experiments (recorded; NOT in committed code):**
  - **t_g≤18 far-ground gate — FAILED**: no change. The wedges are NOT far-ground large-t_g points.
  - **Steeper-view admission** (lower egod/disp/bucket floors 5→2 m, let the precise two-box slab test reject occlusion instead of crude distance floors) — **BACKFIRED**: residual_inpaint jumped to 389378 px (41% of cap), MORE blur. PROVES the close steep views ARE ego-occluded (slab test correctly rejects them) → no clean steep source exists for the near-behind ground. NEGATIVELY confirms the softness is a genuine physical limit. REJECTED.
  - **No-spread-gate test** (LiDAR-height + SPREAD_MAX=1e9, raw real pixels) — the BLACK STREAKY WEDGES **RETURN**. ⇒ the spread-gate is STILL necessary even after LiDAR-height; ⇒ LiDAR-height is NOT the streak fix (it corrects geometry, but grazing sources still disagree at the ERP pole). Streaks = fundamental grazing+pole undersampling, INDEPENDENT of geometry correctness.
- **CORRECTED FINAL UNDERSTANDING (2026-06-18):** the near-pole-behind nadir annulus (ground directly under/just-behind the car) is the rig's **PHYSICAL BLIND SPOT** — only ever seen at <4° grazing; steeper views are self-occluded by the ego body (verified by the backfire). At the ERP pole, tiny pose/calib error → huge sampling divergence → sources disagree → streaks. NOT a geometry bug (LiDAR-correct geometry still streaks), NOT fixable into sharp real texture (the information isn't captured at that resolution). Three renderings of that region, NONE is "sharp real": **(a)** no-gate = real pixels but black radial streaks (ERP-pole warp of low-res grazing data); **(b)** spread-gate = clean but soft/abstain-blob (CURRENT committed); **(c)** honest resolution-matched low-pass = real data shown at its true (low) resolution, soft-but-real, no blob/no streaks — **NOT YET implemented**. Committed db89 = LiDAR-height (geometry, helps curbs) + spread-gate (the actual streak remover); residual near-pole softness = the HONEST physical evidence limit, NOT makeup.
- **OPEN DECISION (user, pending):** accept (b) current, or implement (c) honest resolution-matched render, BEFORE re-rendering all 4 scenes → `av2_ground_video_v2/`. User pushed back on softness twice; both softness-reduction directions (steeper-view, no-gate) are now exhausted-confirmed dead → softness is fundamental. DB-97's 3 other scenes are still finishing v1 on PRE-fix code (throwaway / old-artifact reference).
- **→ SUPERSEDED by DB-99 (2026-06-18, 2-round 33-agent first-principles workflow):** the (b)/(c) binary is a FALSE dilemma. The 白团 is the `NS-inpaint (L1209) → wv^1.5 row-weighted low-pass (L1216-1223)` chain rendering invented low-freq structure — delete it and write a structureless truth-ring DC plate into the existing abstain mask; the residual VIDEO flicker is a scalar `gn_glob` tone-pulse fixed by a temporal median. ~60 lines CPU, no BEV map. See DB-99.

---

# DB-101: Visibility-consistent ground render — the ROOT fix (absorbs DB-99 plate + the z-buffer-gate idea)
Status: ACTIVE — awaiting user's 3-way call (2026-06-19); full state in progress.md. TARGET-side footprint gate validated (box1 + crowd smear gone, zero params) but it is the "make the fabrication less bad" branch. MIDDLE-ONLY tested (GROUND_MODE="mask", db89 STAGE-4): skip ground outpaint, keep determinable scene band, neutral-grey + alpha mask the unseen cap → CLEAN + defect-free across 4 scenes (highway/crowd/bmw/clean, `agent/_db101_mask/`). KEY FINDING: STAGE-4 outpaints the WHOLE near-field (~bottom 44%, ground within ~7m), not just the blind cap → middle-only is clean but masks most of the near-ground. 3-WAY DECISION (gated on DB-94 mask-vs-fill): (A) ship middle-only [Cosmos-ideal], (B) keep STAGE-4 fill [defected], (C) visibility-consistent: fill determinable 3-7m + mask only blind 0-3m. db89 edits LOCAL (remote_py), NOT committed.
Root-cause reframe after user-annotated highway/crowd video defects (near car eaten by the road; a duplicated bright "car-front"/ego-hood ghost on the road; crowd colored smear) + the nadir 白团. User directive: solve at the ALGORITHM root, scene-agnostic — NOT per-scene patches.
Root cause: STAGE-4 ground fill has NO consistent multi-view visibility model. It samples candidate images along ground-plane/LiDAR-height rays with only PARTIAL occlusion gates (ego two-box + tracked-mover box). Every defect is one symptom of that:
  (1) car-eaten-by-road = TARGET-ray visibility ignored (a nearer object sits on the ERP ray, but ground is painted anyway);
  (2) duplicated car-front / bright ego-hood ghost = SOURCE-ray visibility ignored (the source ray first hits the ego hood / an occluder, but its pixel is sampled as ground);
  (3) crowd colored smear = source-visibility + static/dynamic leak (a sign / parked car / untracked object sampled as ground);
  (4) 白团 swirl = fabricating (NS-inpaint + heavy low-pass) where it should ABSTAIN.
Principle (ONE rule, ZERO per-scene params): each ERP ray's color = the FIRST visible STATIC surface it hits, colored from sources that UN-OCCLUDEDLY observed that exact surface point; ABSTAIN (honest flat plate / mask) where no un-occluded source saw it.
Mechanism (reuse db64 phase2 LiDAR z-buffer + object boxes + LiDAR ground height):
  1. SOURCE-side visibility: accept a candidate sample only if the ground point X is the first LiDAR hit along that source ray (depth(X) ~= source LiDAR z-buffer at its projection). Kills hood/object/mover leak (defects 2,3).
  2. TARGET-side visibility: if the ERP nadir ray hits nearer LiDAR geometry before the ground -> it is NOT ground -> do not paint cap, abstain. Keeps the scene-band object (defect 1).
  3. STATIC/DYNAMIC separation: dynamic-object points (boxes) excluded from the static ground evidence (no ghost); dynamics live in their own layer.
  4. EVIDENCE gate + ABSTAIN: no un-occluded source -> abstain -> honest flat plate (this is where the DB-99 plate lives); the spread/agreement gate stays as a secondary check.
  5. NO-LiDAR degradation: occupancy from multi-frame/plane; uncertain -> abstain (degrade by abstaining, NEVER fabricate).
Plan: implement source+target z-buffer visibility in db89 STAGE-4; emit a visibility/abstain mask sidecar for verification; render a few anchors across MULTIPLE scenes (highway w/ the car+ghost, crowd w/ smear, bmw, clean) on the L4; eyeball.
Kill criteria: ZERO per-scene params (any scene-specific constant = fail). Defects 1-4 must vanish by the visibility RULE, not by tuning. If the LiDAR z-buffer is too sparse to occlusion-test (gaps) -> record the limit and abstain there (do not fabricate). If visibility-gating collapses coverage (huge abstain) -> that is an honest finding (region genuinely unobserved), report it, do not relax the gate to fake coverage.
Required vision check (eyes>metrics, MULTI-scene, same logic no knobs): highway — white sedan no longer eaten + no duplicated car-front; crowd — circled smear gone; bmw/clean — nadir honest, no new artifact.
Output: code in scripts/phase3/db89_ghost_recovery.py; masks + panos in datasets/db101_visibility/ on Drive.

---

# DB-99: Nadir "白团" — fix as a rendering bug + scalar temporal-median, NOT a BEV map (supersedes DB-98 (b)/(c))
Status: ABSORBED into DB-101 (2026-06-18) — the structureless truth-ring plate is now DB-101's ABSTAIN renderer (step 4); the root cause turned out deeper (multi-view visibility), surfaced by user-annotated car-eaten/ghost defects. The db89 plate edit (L1203-1223) is already in place and stays as the abstain look. Original context below. (From a 2-round, 33-agent first-principles workflow: 5-lens converge → 15 adversarial verify; then 3 sub-problem solves + a strategic NO-GO on the heavy BEV map.)
Question: is the DB-97 video's residual bottom-nadir 白团 swirl + per-frame flicker fixable cheaply (CPU, ~60 lines) WITHOUT any whole-log BEV map?
Hypothesis (code-grounded): the swirl is NOT a physics wall and NOT the DB-98 (b)/(c) choice — it is the `NS-inpaint (L1209) → wv^1.5 row-weighted low-pass (L1216-1223)` chain painting invented low-freq structure. Replace that chain with a STRUCTURELESS per-anchor truth-ring DC plate (reuse `ref_med` @L1194, view-dependent → Fresnel-safe) written into the existing abstain mask (UNION: tier-2 spread>30 ∪ tier-3 ~anyg) → swirl gone by construction. The residual VIDEO flicker is a scalar `gn_glob` (@L1196) tone-pulse + per-pixel pick noise → fix with a 5-anchor 1D temporal MEDIAN on the scalar gain (+ OPTIONAL world-coord-seeded static asphalt grain: high-pass + line/curb-masked → physically cannot draw a lane line). The near-pole-behind blind spot stays physically unrecoverable — we make it HONEST (flat + grain + alpha/conf), not fake-sharp.
Why now: DB-97 v1 exposed it; user reports some frames' ground painting is bad; the (b)/(c) binary is a false dilemma; the fix is ~60 lines CPU, falsifiable LOCALLY with zero GPU before any re-render.
Plan:
- STEP 0 (local CPU, no Colab): appearance prototype on exported `highway_085 / clean_069 / highway_041` — approximate fillzone + truth-ring from the final frame, composite DC-plate (and +grain), eyeball vs current; answer "does the bare plate already beat the 白团?".
- STEP 1 (CPU edit, db89 STAGE-4): replace L1203-1223 with the nadir-grain-floor post-pass (DC plate on the UNION mask, pole-darken, feather the annulus boundary); MANDATORY 5-anchor temporal median on the scalar `gn_glob`; ALWAYS emit `alpha=0` nadir mask + float32 conf sidecar + 3-float pose JSON `{erp_centre_city, erp_yaw_ref, ground_z}`; OPTIONAL world-seeded static grain.
- STEP 2 (Colab L4, same as DB-97): re-render bmw + highway (worst) ground videos → `deliverables/ground_video_v2/`; vision-scrub.
- Parallel, non-blocking: send Xinhan the one DB-94 question (centre==C? soft-conf or binary mask?). If centre==C + soft conf → the nadir problem dissolves (ship plate+mask, let Cosmos outpaint).
Expected evidence: swirl gone (plate REPLACES, not darkens); zero salient line/curb; same-scene different-anchor near-identical at the pole; `gn_glob` tone does not pulse across the window.
Kill criteria: (a) bare DC plate does NOT beat the 白团 by eye → swirl wasn't the low-pass, re-diagnose; (b) plate reads as an obvious smooth disc even with grain+feather → the boundary (not the swirl) is the defect, reconsider; (c) any fabricated lane/curb appears → drop the grain, ship plate+mask only; (d) `gn_glob` median lags/ghosts on turns → shorten window. NO re-walk of K1–K5 (NS-as-fill / DiT / per-region gains / steeper-view / gate-removal); zero scene-params.
Max scope: ~60 lines in ONE function; 2 worst scenes for the re-render; do NOT build the BEV map; do NOT tune per scene.
Required vision check (eyes > metrics): bottom ~200 ERP rows on 085/mid/041 — swirl/白团/speckle/wedge gone, line-free, pole-stable; then scrub the re-rendered video.
Output: prototype PNGs `agent/_vision_frames/_proto/`; code `scripts/phase3/db89_ghost_recovery.py`; video `deliverables/ground_video_v2/`.

---

# DB-99a: whole-log city-frame BEV ground-texture fusion (SHELVED)
Status: icebox / shelved (2026-06-18). The round-1 convergent idea + round-2 P2 design (geometry-only BEV ledger, three-tier planarity gate, phase-correlation sub-cell align, Fresnel kept OUT of the buffer, +40-line no-render audit). Technically sound for TEMPORAL STABILITY + √N speckle denoise on the DETERMINABLE 20-28 m annulus — but the strategic challenge showed it does NOT touch the complained blind-spot pixels (physically unrecoverable), carries a systematic-curb double-image risk (dz→dx 11-19× at grazing on the 8.4% non-planar cap), and the Cosmos consumer regenerates the region. Revisit ONLY if DB-99 STEP 2 shows the determinable annulus itself visibly flickers after the cheap fix.

---

# DB-96: Contact-shadow evidence modelling (icebox)
Status: icebox - known principled gap, low priority.
Question: can the cast shadow be treated as evidence-bound object appendage (dark region adjacent to the object mask, luminance-ratio detected) and moved/kept with the body during compositing?
Why: the only remaining visible artefact class on the BMW scene (fill bands show unshadowed background); currently mitigated by harmonic fill.
Plan: only if the downstream consumer flags it; otherwise leave to the generative layer.
