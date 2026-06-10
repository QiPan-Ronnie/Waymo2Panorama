# Decision Briefs — live queue

Convention: completed briefs are archived in progress.md (newest-first) and deleted here. Full history: git log of this file + progress.md.

---

## Archived (see progress.md)

DB-80..DB-92 + V2.1/V2.2 all completed and recorded in progress.md (milestone tags: db90-v3-porsche-solved, db91-grain-consensus-fixed, db92-generality-pass, best-pano-v2-5scenes, best-pano-v2.1-defringe, v2.2-harmonic-fill).

---

# DB-93: Sky outpainting integration (upper hemisphere)
Status: queued - BLOCKED on user approval (needs FLUX env, likely A100 GPU).
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
