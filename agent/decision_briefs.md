# Decision Briefs — live queue

Convention: completed briefs are archived in progress.md (newest-first) and deleted here. Full history: git log of this file + progress.md.

---

## Archived (see progress.md)

DB-80..DB-92 + V2.1/V2.2 all completed and recorded in progress.md (milestone tags: db90-v3-porsche-solved, db91-grain-consensus-fixed, db92-generality-pass, best-pano-v2-5scenes, best-pano-v2.1-defringe, v2.2-harmonic-fill).

---

# DB-93: Sky outpainting integration (upper hemisphere)
Status: **v2 EXPLORED on A100 (2026-06-11) — mechanism PROVEN, one precise blocker identified; gradient v3.2 remains the shipped baseline.**
**v2 result:** photometric-anchor mechanism WORKS — with init = gradient dome the generation faithfully follows init photometry instead of the old postcard-sky prior (the core v1 failure is fixed in principle). BUT the anchor is contaminated: the source panorama carries a GRAY VIGNETTE BAND at the camera FOV upper edge (real pixels, not black, so both the gradient fill and the sky mask correctly leave it) and DiT360 amplifies that gray into a full overcast dome inconsistent with the sunny scene below (verified by eye, tau 15 and 50, two inits). **DB-93 v3 precise next step: detect the vignette band (low-luminance boundary strip vs same-column sky statistics) and include it in BOTH the fill domain and the generation mask, so the anchor is built only from healthy sky evidence.** Results: results/db93v2_sky on Drive; A/B/C boards in ~/.waymo2panorama. Old status: User's eyes overruled the old "sky-only WIN" record: the generated sky was a postcard-blue cumulus wallpaper inconsistent with the scene's actual haze/tone (verified by eye on the db19_0bae overall_review). FIRST-PRINCIPLES root cause: init had a BLACK cap = no photometric anchor -> FLUX prior free-ran to its ideal-sky mode; plus the lever-mining prompt bug (DEFAULT_PROMPT enumerated the very object classes the gate rejects). **v2 fix: init = sky-fill v3 gradient dome (photometry 100% from OBSERVED sky) + match-existing/anti-object prompt + tau sweep {15,50}.** FLUX 67.5GB cache intact on Drive (cache/huggingface/hub); honest-gradient fallback (scripts/phase3/sky_fill_gradient.py, deliverables/sky_fill_v3) already shipped as the abstain-compatible baseline and applied to all 75 dataset panoramas.
Question: does the previously validated DiT360 sky-only outpaint (gate-clean upper-hemisphere fill; constraint+object-gate recipe, tau=50) compose cleanly on top of the v2.2 5-scene composites?
Why: the v2.2 panoramas have black upper hemispheres; the sky outpaint was validated as a WIN earlier (recipe in memory: local FLUX cache, uninstall torchao, torchvision gate).
Plan: restore the FLUX env (ask user for A100), run sky-only outpaint on the 5 v2.2 panoramas, object-gate as validated, A/B board.
Kill criteria: any change below the horizon line -> reject (sky-only contract); hallucinated structures in sky -> tighten gate or stop.
Required vision check: horizon continuity, no content invented below horizon, 5/5 scenes.

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

# DB-96: Contact-shadow evidence modelling (icebox)
Status: icebox - known principled gap, low priority.
Question: can the cast shadow be treated as evidence-bound object appendage (dark region adjacent to the object mask, luminance-ratio detected) and moved/kept with the body during compositing?
Why: the only remaining visible artefact class on the BMW scene (fill bands show unshadowed background); currently mitigated by harmonic fill.
Plan: only if the downstream consumer flags it; otherwise leave to the generative layer.
