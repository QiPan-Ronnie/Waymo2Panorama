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

# DB-96: Contact-shadow evidence modelling (icebox)
Status: icebox - known principled gap, low priority.
Question: can the cast shadow be treated as evidence-bound object appendage (dark region adjacent to the object mask, luminance-ratio detected) and moved/kept with the body during compositing?
Why: the only remaining visible artefact class on the BMW scene (fill bands show unshadowed background); currently mitigated by harmonic fill.
Plan: only if the downstream consumer flags it; otherwise leave to the generative layer.
