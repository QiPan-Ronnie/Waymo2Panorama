# Decision Briefs — live queue

Convention: completed briefs are archived in progress.md (newest-first) and deleted here. Full history: git log of this file + progress.md.

---

## Archived (see progress.md)

DB-80..DB-93 + V2.1/V2.2 all completed and recorded in progress.md (milestone tags: db90-v3-porsche-solved, db91-grain-consensus-fixed, db92-generality-pass, best-pano-v2-5scenes, best-pano-v2.1-defringe, v2.2-harmonic-fill). DB-93 (sky + ground completion) CLOSED 2026-06-12 — ground fill v8 (whole-log geometry eligibility, two-box ego self-occlusion, evidence-resolution nadir render) + FLUX.1-Fill sky; deliverable `deliverables/complete_pano_v8/`; commits bfbc244/c589aa7/4f50ec8/a9e3497/f7a8a14.

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
Status: ACTIVE (2026-06-12) - launched on A100 (80GB).
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

---

# DB-96: Contact-shadow evidence modelling (icebox)
Status: icebox - known principled gap, low priority.
Question: can the cast shadow be treated as evidence-bound object appendage (dark region adjacent to the object mask, luminance-ratio detected) and moved/kept with the body during compositing?
Why: the only remaining visible artefact class on the BMW scene (fill bands show unshadowed background); currently mitigated by harmonic fill.
Plan: only if the downstream consumer flags it; otherwise leave to the generative layer.
