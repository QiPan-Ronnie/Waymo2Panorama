# Decision Briefs — live queue

Convention: completed briefs are archived (one line each) here and recorded in full in `progress.md` (newest-first). Full history: `git log` of this file + `progress.md`.
RESULTS GO IN `deliverables/` — not `agent/` (agent/ is working/evidence scratch only).

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
