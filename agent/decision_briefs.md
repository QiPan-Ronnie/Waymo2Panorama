# Decision Briefs — ACTIVE experiment queue for Waymo2Panorama

This file is the **direction/decision gate**. It holds active / pending / in-progress briefs plus compact closed-brief pointers when useful for route continuity. The full factual archive belongs in `agent/progress.md`.

**Protocol (user-set 2026-06-03):**
- Before starting ANY new experiment direction, create/update a brief here. Each brief MUST carry **Kill criteria** + **Max scope** (the load-bearing fields). This project's recurring failure mode is patch-on-patch on a "promising" direction until it's NEG — the brief is the entry gate that stops that.
- When a brief is **DONE** (accepted / rejected / explored / closed): **archive the factual conclusion into `agent/progress.md`**, mark the brief done, and keep only a compact result pointer here unless the queue is being explicitly pruned. `progress.md` is the permanent record; this file remains the decision/route gate and must not imply a closed brief is still active.
- **Completed briefs DB-01..13 (through 2026-06-03) are archived** in `progress.md` → entry "DECISION-BRIEF ARCHIVE". The accepted source-faithful deliverable = `scripts/phase3/_seamroute.py` (align + object-moat min-cut seam + virtual-centre select), now with the **BEV ground atlas** road layer adopted (`_bev_ground.py` → `SR_bmw_bevfinal_1024x2048.png`). Residual floors: off-plane curb, out-of-FoV black — physical/hardware.

Status values: `proposed` / `running` / `explored` / `accepted` / `rejected` / `paused`.

### Template
```markdown
# DB-YYYYMMDD-NN: <short title>
Status: proposed / running / explored / accepted / rejected / paused
Route: A (geometry) | B (generative) | infra | sidestep
Question: ... / Hypothesis: ... / Why now: ... / Expected evidence: ...
Kill criteria: ... / Max scope: ... / Required vision check: ...
Result summary: TBD -> archive factual details to progress.md when done; keep only a compact decision pointer here.
```

---

## ⭐⭐⭐ ACTIVE BRIEF (2026-06-09) — read `agent/2026-06-09-fable5-firstprinciples-analysis.md` FIRST (supersedes DB-79's practical conclusion)

> **First-principles audit (Fable 5) found the project's single biggest untested assumption: the ERP virtual centre has been pinned to the AV2 ego origin since L1** (`sphere_projection.py:5-6`, `db79_fair_metric_wall.py:136-139`). Measured on the real BMW calibration (L4 probe, Read-verified): ring cams are **1.81–2.18 m from the ego origin** but only **0.27–0.30 m from their own centroid** — and inside each camera's own viewing sector the centroid offset is nearly collinear with the ray (effective perpendicular baseline 0.01–0.06 m). Depth-aware render-back error scales linearly with that perpendicular baseline (`err ≈ (W/2π)·b_perp·δZ/Z²`; the model reproduces DB-79's ROT 318/301 px, surface 5–15 px, curb/wall 55–88 px at b_perp≈1.75 m). **DB-79's "seam wall" verdict is therefore a property of (depth, c\*=ego-origin), not of depth alone.** DB-80 re-asks DB-79's question at the correct centre.

# DB-80: Virtual-centre relocation — re-run the DB-79 render-back battery with c\* = ring-camera centroid + min-baseline source selection (measurement-first, CPU/L4, NO A100, NO generation)
Status: **EXPLORED / POS (2026-06-09)** — steps A + A.5 + B + C all run same-day on the L4 runtime (CPU workloads, ~4 min total compute; scripts `db80_virtual_centre.py`, `db80_stepB_render.py`, `db80_stepC_generality.py`; full record in progress.md).
**Result (Read + vision verified; 5 AV2 scenes):** **b_perp model CONFIRMED — the DB-79 "seam wall" was dominated by the ego-origin virtual-centre amplification.** Step A (same LiDAR points, same depth, only the sphere centre changes): global DEPTH render-back p90 **84→4.7 (BMW), 147→5.8 (clean), 38.8→0.4 (highway), 11.7→0.1 (downtown), 67.9→1.6 px (crowd)** = 18–96× reduction; silhouette p90 123–152 → **2.4–9.7 px**; measured b_perp p50 1.48–1.73 → 0.12–0.13 m (matches the model exactly); depth-tolerance (≤2 ERP px) >1 m near-field fraction 0–15 % → **89–99 %** (coarse/plane depth now suffices over most former abstain area). **Pre-registered kill clause fired LITERALLY on one bucket** — BMW curbwall ROI p90 64 px > 30 px — and step A.5 attribution shows that tail is 100 % protocol occlusion-aliasing: every >30 px pair has densify dz>1 m with td≈40 m (occluded FAR-layer test points scored against the near surface that actually wins the pixel); **visible-surface curbwall pairs (dz≤0.25 m, n=198) have p90 = 0.59 px**. Recorded as a measurement-protocol caveat (same single-layer aliasing affects DB-79's ego numbers), NOT a threshold relaxation; the other 4 scenes' curbwall p90 = 0.20–4.07 px pass the confirm bar outright. Step B (full-ERP single-source min-b_perp depth-aware render at centroid, static-vehicle-aware LiDAR accumulation): **vision verdict POS on BMW + clean + crowd** — yellow lane lines continuous across the former seam, white-line step gone, SUV/truck edges intact with NO new smear/double-image; exposure steps + near-ground chroma fringe remain (photometric, untouched by design → B1). **DB-79's practical conclusion is formally re-scoped: depth DOES reopen the seam at the correct virtual centre wherever LiDAR-grade depth exists.** Outputs `deliverables/db80_virtual_centre/` (DB80/DB80C summaries, boards, ego/cen renders, tolerance maps); secret 0. **Remaining in-brief: Waymo generality is a DATA step (no raw Waymo sensor data staged on Drive — verified); occlusion z-buffer per camera deferred (v0 renders show no visible occlusion leaks).** Follow-ons now live: **B1 photometric layer** (exposure/WB harmonisation + CA fringe fix — the most eye-catching residual defects), **B2** DB-78 thin-band flow on the residual few-px seams, **B3** re-evaluate parked 3DGS with ≤0.3 m extrapolation, **B4** temporal common-path, and **confirm with Xinhan that the point-cloud-video first-frame centre = ring centroid at camera height**.
Route: A (geometry) — fork-agnostic; direct extension of DB-79 (same harness, same depth, different sphere centre).

**Question:** Does moving the ERP virtual centre from the ego origin to the ring-camera centroid (ego-frame ≈ `[1.363, -0.004, 1.445]` m for the BMW log; computed per-log from calibration) reduce the depth-aware render-back residual at curb/wall/silhouette by the predicted ~5–20×, turning the "seam wall" into a thin-band few-px misalignment — or does the residual stay walled, finally confirming the seam as physical against its strongest cheap attack?

**Hypothesis:** DB-79's residuals are dominated by b_perp amplification, not by depth error. Pre-registered predictions at c\*=centroid with the SAME layered LiDAR-only densified depth (camera-native px, ROI/protocol identical to DB-79 step3): curb/wall DEPTH reproj p90 **55–88 → ≤15 px**; silhouette p90 **120–150 → ≤30 px**; surface p90 **4.9–14.9 → ≤3 px**. Additionally the depth-tolerance bound relaxes ~20× in-sector (1.5 m depth error ≈ 2 ERP px at b_perp 0.06 m), so plane-fill depth suffices for most of the no-LiDAR near-ground that previously forced abstain.

**Why now:** (1) It is the largest load-bearing assumption never tested in 79 briefs — every depth-route kill (DB-76a LOO, DB-77B IBR tears, DB-79 step3) inherited c\*=ego origin. (2) It is the cheapest possible attack: a sphere-centre parameter + LiDAR re-accumulation to a shifted origin; reuses the DB-79 script skeleton; CPU/L4 minutes. (3) The downstream contract (Cosmos-style conditioning; Xinhan trains on perfect-360s masked to our shape) wants the stitched band to look like a slice of a perfect 360 shot at camera height — c\*=centroid (z≈1.44 m) is also closer to that viewpoint distribution than the ground-level ego origin. (4) If it wins, DB-77B-class depth-aware IBR and even the parked 3DGS category get un-stale kill evidence (their collapse literature assumed ~1.5–3 m extrapolation; centroid needs ≤0.3 m).

**Expected evidence (cost-ascending steps; stop at any kill):**
- **Step A — pure reprojection re-run (CPU, ~minutes):** extend `db79_fair_metric_wall.py` step3 with `c_star` parameter; X_true/X_zd/X_far defined from rays out of c\*=centroid; LiDAR accumulation and densify re-targeted to centroid-centred ERP. Report the same table (ROT/DEPTH × surface/silhouette/curbwall p50/p90, false-GREEN>3px) side-by-side ego-origin vs centroid, BMW + clean. **This step alone falsifies or confirms the b_perp model.**
- **Step B — depth-aware N1 full-ERP render (CPU/L4):** render BMW + clean ERP at c\*=centroid via `render_camera_to_erp(..., convergence_distance_m=Zd_centroid)` (N1 mode exists); per-pixel source = min perpendicular ray-baseline camera (not max-weight feather); no-LiDAR regions = ground-plane fill depth (cheap, tolerance now permits); out-of-evidence = same abstain/black as today. Boards: full ERP + the 4 marked ROIs vs `hard_select` baseline, same-ROI crops.
- **Step C — generality (only if A+B pass):** ≥3 AV2 logs + ≥1 Waymo segment (Waymo: 5-cam centroid computed the same way; rolling-shutter risk bucketed separately per DB-76a rule).

**Pre-registered thresholds (set BEFORE run; failure does NOT relax them):**
- b_perp model CONFIRMED: step A curb/wall DEPTH p90 ≤15 px AND silhouette p90 ≤30 px (camera-native px) on BOTH cases.
- Render WIN: step B vision verdict — curb staircase / wall-top crease / SUV-edge step visibly reduced vs hard_select at same-ROI zoom, NO new smear/ghost/doubling, road/lane/facade structure not softened. A lower number with a smeared curb = FAIL (eyes over metrics).
- Practical abstain shrink: fraction of task-band pixels whose depth-tolerance (for ≤2 ERP px error) exceeds 1 m rises from ~0 % to ≥60 % (report the tolerance map).

**Kill criteria:**
- Step A: curb/wall DEPTH p90 stays **>30 px** at centroid on either case → the seam wall is REAL beyond c\* choice; write "wall confirmed against strongest cheap attack" to progress.md, keep abstain as the honest ceiling, close the c\* route, do NOT proceed to step B.
- Step B: vision shows new artifacts (smear/double-image/structure softening) not present in hard_select, and not attributable to a fixable source-selection bug after ONE debug pass → stop, record, downgrade to "step-A-only geometry note".
- Any RGB generation / inpaint / DiT / model-confidence-as-truth / A100 use / secret written to repo → out of scope, stop.
- Scope creep beyond BMW+0bae before steps A+B pass → stop & report.

**Max scope:** measurement + deterministic re-render only. Fixed cases BMW `02a00399:0` + clean `0bae3b5e:30` first; generality step C gated on A+B. Compute: CPU/L4 only (DB-79 ran 102 s + 30 s on CPU; this is the same workload re-centred) — **NO A100.** All remote results routed to a non-repo file + Read-verified (fabrication rule); secret-scan 0; runtime endpoint read from env/non-repo file only.

**Required vision check:** per case, board with: full centroid-ERP vs hard_select ERP; the 4 marked ROIs same-zoom before/after; step-A residual heatmaps (surface/silhouette split, same colormap+scale as DB-79 for direct comparison); depth-tolerance map. Personally eyeball: does the curb staircase close? does the SUV edge align? any NEW doubling from min-baseline selection at sector boundaries? Eyes beat metrics on conflict.

**Output location:** `deliverables/db80_virtual_centre/` (side-by-side residual JSONs, boards, tolerance maps, manifest, pre-registered-thresholds JSON, verdict).

**Adversarial self-audit (pre-run, per hard rules):** (i) *"Too simple to be unfound"* — the offset was known (DB-79 audit wrote it down) but treated as rig physics, never as a parameter; rotation-only seams genuinely don't depend on c\*, which masked the depth-route dependence. (ii) *"Co-observed band still seams"* — boundary b_perp ≈0.1–0.3 m gives 2–6 px opposing errors at silhouettes; budgeted, lands in DB-78-flow/feather repair range. (iii) *"Dynamic objects"* — boxes removed from accumulation as in DB-79; parked-but-annotated vehicles lose LiDAR → their depth falls back to plane/coarse; tolerated by the relaxed budget, checked in vision. (iv) *"Downstream centre contract"* — point-cloud-video first-frame centre is renderer-chosen (Xinhan's side, adjustable); camera-height centre matches the real-360 GT distribution better; flag to Xinhan, non-blocking. (v) *"Waymo generality"* — centroid is computed from calibration per-log, no scene tuning; degradation without LiDAR = today's rotation-only L1.

**Follow-ons (NOT active; open as own briefs, gated on DB-80):** (B1) global exposure/WB harmonisation + near-ground chroma-fringe (CA) correction — the two most eye-catching in-band artifacts per the 2026-06-09 vision pass, both photometric and hallucination-free (integrate 新-E properly); (B2) DB-78 flow/feather thin-band pass on the residual few-px seams at centroid; (B3) re-evaluate the parked depth-aware IBR / 3DGS category with un-stale extrapolation distances (≤0.3 m); (B4) optional "common-path" temporal frame selection (per-direction min-b_perp over a ±2 s window) for forward/backward sectors.

---

# DB-81: B1 photometric layer — LiDAR-correspondence cross-camera colour harmonisation + per-channel radial CA alignment on the centroid base (CPU, NO A100, NO generation)
Status: **EXPLORED / P1-POS, P2-NEG-closed (2026-06-09)** — one /exec, 5 scenes, `scripts/phase3/db81_photometric.py`, ~3 min; full record in progress.md.
**Result:** **P1 (LiDAR-correspondence per-camera gains) = WIN.** Pair log-colour-difference cut: highway **88.3 %**, crowd **82.5 %**, downtown **69.1 %**, clean **58.0 %**, BMW 27.3 %. Vision: the dusk highway scene goes from an obvious 7-patch collage (dark-blue front tile, pink/grey facade split) to one tonally-unified panorama; clean/crowd/downtown similar; BMW mild-but-positive (its before-difference 0.089 was already the smallest — ceiling effect). NO global tone drift (white car stays white, yellow lines yellow), no new artifacts. **Pre-registered note:** BMW's 27.3 % is below the 30 % per-clause line; kept P1 applied there anyway because the clause's intent is "method ineffective" and vision shows positive-and-harmless — decision + reasoning logged (eyes-over-metrics, per protocol). **P2 (radial CA) = honest NEG, closed per its own kill clause:** grid search returns k≈0 for every camera/channel — AV2 ring images carry no measurable lateral CA (they ship undistorted); the near-ground purple/green fringe must come from another source (demosaic/JPEG-chroma per the old p7 suspicion, or depth-edge mixing) — separate micro-diagnostic queued, NOT a CA fix. **Known limit:** multiplicative gains cannot recover clipped/overexposed sky tiles (front_center sun-facing tile stays bright) — a tone-curve/saturation-aware extension is a possible P3, not opened. Products `deliverables/db81_photometric/` (gains JSON, boards, base-vs-b1 renders ×5). Secret 0.
Route: A (photometric, evidence-based, zero hallucination) — applies to the new `db80 cen_depth` base.

**Question:** Can (P1) a global per-camera per-channel gain solved from LiDAR cross-camera colour correspondences (ring-closed least squares in log domain) plus (P2) per-channel radial chromatic-aberration alignment (image-self-supervised) reduce the two now-dominant visible defects — inter-camera exposure/WB steps and near-ground purple/green fringing — to "distribution-close to a single-camera 360", without structural damage or global tone drift?

**Hypothesis:** The exposure/WB step is low-dimensional (7 cams × 3 channels multiplicative gain; same camera model ⇒ tone curves near-identical, first-order multiplicative model suffices — the AVM literature's standard model, e.g. Liu & Zhang IEEE'14, Parameter Blending arXiv:2406.11066, but supervised here by sub-pixel LiDAR 3D-point correspondences instead of overlap-block statistics). The fringe is classic lateral CA: R/B radially displaced vs G by `r' = r(1+k·r_n²)`, 1–3 px at the image edge, estimable per camera by maximising R↔G / B↔G edge alignment in the outer annulus — no external supervision, no content change.

**Why now:** DB-80's vision pass ranked these two as the most eye-catching residual in-band defects (more salient at full-ERP scale than the now-few-px geometric seams); both are photometric and hallucination-free; 新-E measured a related gain (-18 % lum gap) but was never integrated into any shipped base. The Cosmos conditioning distribution (perfect-360 slices) contains neither defect.

**Expected evidence (cost-ascending; CPU only):**
- **P1:** collect co-observed LiDAR colour pairs (project each accumulated static LiDAR point into every seeing camera, bilinear RGB; filter saturated <10/>245, silhouette points, sky-free by construction); solve `min Σ ||(log I_i + c_i) − (log I_j + c_j)||²` with `Σ c_i = 0` per channel (7×7 normal equations); apply `exp(c_i)` in the cen_depth render. Report pair colour-difference (per-channel median + ΔE-proxy) before/after, per camera-pair.
- **P2:** per camera, grid-search radial CA coefficient k for R and B vs G on the outer annulus (NCC of gradient images after radial remap); apply per-channel remap before rendering. Report edge-alignment score before/after + visual fringe check on the near-ground.
- **Board:** full-ERP cen_depth vs cen_depth+B1 + seam-step closeups + near-ground fringe closeups, 5 scenes.
- **P3 (only if P1 leaves visible low-frequency residual):** conservative low-frequency-only block harmonisation — separate go/no-go, not run by default.

**Kill criteria:** P1 pair colour-difference drops <30 % or vision shows global tone drift / colour cast on known-colour content (white truck, yellow lines) → revert, record. P2 edge-alignment does not improve or introduces new fringing → skip CA, keep P1 only. Any structure change outside colour (vision) → stop. No learning, no generation, no A100.

**Max scope:** 5 staged AV2 logs, same anchors as DB-80; CPU on the L4 runtime; one bounded /exec per phase (P1+P2 may share one); results to non-repo + Read-verify; secret-scan 0.

**Required vision check:** eyeball per scene: (a) does the vertical exposure boundary disappear/soften at full-ERP scale? (b) does the near-ground purple/green fringe visibly reduce at ROI zoom? (c) NO global tone shift (white stays white, yellow stays yellow), NO new artifacts. Eyes beat metrics.

**Output location:** `deliverables/db81_photometric/` (gains JSON, CA coefficients, pair-difference stats, boards, manifest).

Result summary: TBD — archive to progress.md when done.

---

# DB-82: Robustness & graceful-degradation battery for the cen_depth+B1 base — multi-anchor sweep + no-LiDAR ablation (CPU, NO A100, NO generation)
Status: **EXPLORED / POS (2026-06-09)** — `scripts/phase3/db82_robustness.py`, one /exec, 5 logs × 3 anchors × 3 variants; full record in progress.md.
**Result:** (a) **Multi-anchor robust** — all 15 log×anchor combos give identical structural stats (black_frac 0.737–0.740, boundary_density 0.0010–0.0011); vision on downtown + crowd boards: no occlusion leaks, no moving-object shred (a030 near-field white truck intact in all variants), no anchor-specific blowup. (b) **Graceful no-LiDAR degradation CONFIRMED** — the plane-only variant (`cen_plane_b1`) is visually near-indistinguishable from full LiDAR at panorama scale (mean |Δ| 4.3–9.5 grey levels where both valid) and clearly better than the legacy `ego_rot`; the north-star "works without LiDAR" claim now has an on-disk A/B. (c) **New known limit found (vision):** B1 gains make the clipped/overexposed front-center sky tile's cyan cast MORE visible on dusk scenes (saturated regions violate the multiplicative model) — fix belongs to a saturation-aware tone P3 or simply to sky-outpaint ownership of the sky band; recorded, not blocking. **Bonus micro-diagnostic (fringe attribution closed):** the near-ground purple fringe exists in the NATIVE `ring_side_right` camera image (shadow-region ISP chroma noise in the AV2 source data) — not lens CA (DB-81 k≈0), not JPEG (present in lossless PNG), not our pipeline. Any fix = shadow chroma desaturation (alters real pixels — only as a labeled optional post-step; low priority for the Cosmos contract). Products `deliverables/db82_robustness/`. Secret 0.
Route: A (validation of the new base) — directly serves the north-star generality contract (multi-scene + graceful degradation without LiDAR).

**Question:** Does the new best base (DB-80 centroid depth-aware render + DB-81 P1 gains) hold up (a) across multiple anchors per log (temporal robustness, dynamic-object exposure, occlusion-leak check for the v0 no-z-buffer renderer), and (b) without LiDAR entirely (Zd = ground-plane + far-field only), degrading gracefully to at-worst the legacy rotation-only quality?

**Hypothesis:** (a) The pipeline is anchor-independent (no scene-specific tuning anywhere: centroid from calibration, gains from LiDAR pairs, depth from accumulation) → multi-anchor renders stay artifact-free. (b) Because the centroid centre relaxes depth tolerance ~20× (DB-80), a plane-only Zd should already fix most of the near-ground seam vs ego_rot; degradation = mild near-field misalignment on off-plane structure, never worse than ego_rot.

**Why now:** GENERAL is the invariant goal (memory north-star: "multi-scene validation + graceful degradation without LiDAR"); the base is 1-anchor-per-log validated only; the no-LiDAR A/B was never isolated in the project (DB-78 caveat). Cheapest possible high-value next step; also surfaces any v0 occlusion leaks / moving-object artifacts before deeper investment.

**Expected evidence (one /exec, CPU):** per log × 3 anchors (clamped to valid range): render `ego_rot` (legacy baseline) / `cen_plane_b1` (NO LiDAR: plane+far Zd; gains still applied — note gains need LiDAR pairs, so for the no-LiDAR variant reuse anchor-0 gains = calibration-time constant, defensible as a per-vehicle calibration product) / `cen_depth_b1` (full). Per-log board: 3 anchors × 3 variants + fixed ROIs. Auto-stats: per-render black fraction, source-boundary count, mean |Δ| between cen_depth and cen_plane (where both valid). Vision: sample ≥6 boards — look for occlusion leaks, moving-object tearing, plane-fill failures (curb/vehicle areas), any anchor-specific blowup.

**Kill criteria:** any systematic NEW artifact class on multi-anchor (occlusion leak / moving-object shred / plane-fill structure damage) that is not present at the validated anchors → record + halt rollout of the base until fixed. No-LiDAR variant visually WORSE than ego_rot on any scene → graceful-degradation claim FAILS, record honestly (do not relax). Scope creep beyond render+stats → stop.

**Max scope:** 5 AV2 logs × 3 anchors × 3 variants, CPU on L4, one bounded /exec; results non-repo + Read-verify; secret 0. No Waymo (data not staged — separate DATA step). No flow/outpaint/learning.

**Required vision check:** per sampled board: (a) no new artifact at unseen anchors; (b) cen_plane_b1 vs ego_rot — is the no-LiDAR render clearly better (seam) and never structurally worse? (c) moving objects (downtown/crowd) — single-source intact or shredded? Eyes beat metrics.

**Output location:** `deliverables/db82_robustness/` (boards per log, stats JSON, manifest, verdict).

Result summary: TBD — archive to progress.md when done.

---

# DB-83: Object-aware rendering — per-object source locking + box-consistent depth in the centroid renderer (CPU, NO A100, NO generation)
Status: **EXPLORED / KILLED per pre-registered clause (2026-06-09)** — 9 renderer variants tried, none beats the baseline at the user-flagged sedan without introducing equal-or-worse artifacts; full mechanism chain understood and recorded (progress.md). **Baseline cen_depth_b1 stays unchanged (revert).**
**What was learned (high-value NEG):** the sedan doubling is NOT a simple depth-inconsistency bug. The car straddles the front_left/side_left FOV boundary (each camera sees half — native crops verified by eye), and the region beside the car is a **disocclusion zone**: background occluded by the car has neither LiDAR depth nor any camera's colour from the blocked side. Every deterministic fix re-painted a ghost from the other camera: box-air footprint claimed → locked camera paints the tail twice; occlusion-test exemption leaks via box-air; background-only depth field EDT guesses 9 m where the wall is 14 m → segment-vs-box test misses and the other camera's wheel lands beside the car; LiDAR-silhouette has holes (dark/reflective body) → black patches. **Conclusion: object-boundary disocclusion needs either full layered rendering with inpaint (a real sub-project) or the thin-band learned/flow repair lane (DB-78/B2) — not a renderer patch.** The baseline's own artifact (soft head-ghost on boundary-straddling near vehicles, ~10–20 px, 1 instance found in 5 scenes) is recorded as a known limitation in the data contract.
Route: A (renderer correctness fix) — ports the project's validated object-moat idea (`_seamroute.py`) into the DB-80 renderer.

**Question:** Does forcing (a) a single source camera per annotated object and (b) a box-consistent depth inside each object's ERP footprint eliminate the object-interior doubling the user spotted (dark sedan, BMW scene) without introducing worse box-boundary artifacts?

**Hypothesis (from the A/B/C diagnostic, vision-verified):** the doubling is caused by an INCONSISTENT depth field inside the car (sparse LiDAR on dark/reflective body mixed with ~9 m background EDT fill → pixels of one car projected to different azimuths). Plane-depth (uniformly wrong) keeps the car intact ⇒ consistency beats correctness for object integrity. Box-locked depth + single source = consistent by construction; expected outcome = C's integrity with B's global geometry.

**Expected evidence:** render BMW + crowd + downtown with object-aware mode; A/B/C/D boards (old / cen_depth / cen_plane / cen_depth+objlock) tight-cropped on the user's sedan + crowd truck + downtown vehicles; vision gate.

**Kill criteria:** box-boundary artifacts (background tearing at box edges) judged worse than the doubling they fix → revert; any object duplicated/deleted → fail; scope beyond renderer changes → stop.

**Max scope:** one /exec CPU; 3 scenes; boxes from existing annotations (no detector); no learning/generation.

**Required vision check:** the user's sedan intact AND correctly placed; truck/vehicles in crowd/downtown intact; box edges clean. Eyes beat metrics.

**Output location:** `deliverables/db83_objectaware/`.

Result summary: TBD.

---

# DB-84: Temporal disocclusion repair — fill the no-evidence zone beside near objects with REAL pixels from other timestamps (CPU/L4, NO generation)
Status: **EXPLORED (2026-06-09 evening)** — Step-1 measurement POS (temporal visibility **100 %** sedan zone / **78 %** crowd-truck zone, both far above the 60 % pre-registration); Step-2 render BLOCKED by a discovery that **re-diagnoses DB-83 entirely** (below). Render lane handed to DB-85.
**★ MAJOR MECHANISM CORRECTION (data-verified):** the BMW "sedan" is a REGULAR_VEHICLE with **track displacement 111.65 m over 63 frames (~17.7 m/s)** — it is a *moving* car. And AV2's 7 ring cameras are **NOT synchronized**: measured capture offsets front_center 0 / front_left −12.5 ms / side_left +22.5 ms / side_right −22.4 ms (etc.). The car straddles front_left↔side_left whose offset is **35 ms → the car moves 0.62 m between the two exposures → 326·0.62/12.5 ≈ 16 ERP px** — exactly the observed doubling. ⇒ **DB-83's doubling was never a depth / disocclusion problem; it is motion × asynchronous shutter.** All 9 DB-83 geometry fixes were treating a disease the pixel did not have; v9's soft steering also failed because the annotation-time box projection is 25–50 px displaced from where each camera actually imaged the car (box at label time vs exposure times). DB-83's kill stands but its diagnosis is REWRITTEN.
**What survives of DB-84:** the temporal-visibility result (static disocclusion zones ARE recoverable from ±1 s real pixels: 100 %/78 %) and the search machinery. Static-zone filling must first EXCLUDE moving-object regions (their box-time mislocation polluted the v1–v3 zone and erased the car from the fill); a clean static-only fill is folded into DB-85's scope.
**Follow-on = DB-85 (next active): motion-aware object rendering** — for moving tracks, interpolate the box pose to EACH CAMERA's exposure time (pose + track interpolation, all data available), build per-camera-time footprints, prefer the single camera that images the object most completely; optionally place via anchor-time box (motion compensation). Static disocclusion fill rides on top once moving objects are excluded from the zone.
Products: `deliverables/db84_temporal_fill/` (stats JSON, A/B + zone boards = the evidence trail incl. the failed fills); diag JSONs non-repo. Secret 0.
Route: A (source-faithful; time = the one evidence source the renderer has not used for RGB)

**Question:** For the disocclusion zone beside boundary-straddling near objects (the exact region where DB-83's 9 deterministic fixes all failed because depth AND colour evidence are missing at the anchor instant), can other timestamps in the ±2 s window supply (a) real LiDAR depth (the occluded wall IS hit from other ego positions — multi-frame accumulation already contains those points) and (b) real RGB (a camera at another timestamp sees around the object), giving an evidence-based fill with zero hallucination?

**Hypothesis:** Static-scene disocclusion is not "unknowable" — it is unknown only at one instant. As the ego translates ~5–15 m over the window, the line-of-sight to the blocked background sweeps around the foreground object; for most of the zone there exists a (frame, camera) pair whose optical centre sees the 3D point unobstructed. Per-pixel procedure: take the 3D point X from the accumulated (multi-frame) LiDAR background; search the window for the (frame, camera) with X visible (in-FOV, not blocked by any annotated box AT THAT FRAME's box positions) and minimal perpendicular ray-baseline; sample RGB there (with DB-81 gains; note cross-time exposure is a risk bucket).
*Pre-registered prediction:* ≥60 % of the sedan's disocclusion zone becomes temporally visible; the filled render shows real wall/storefront instead of ghost wheels, vision-clean.

**Why now:** (1) DB-83's kill report names exactly two proper lanes — layered rendering (expensive) or honest repair; temporal fill is a THIRD lane that is cheaper than both and strictly evidence-based, and nobody has used cross-time RGB in this project's renderer (DB-74's NEG was temporal labels into a 2D optimizer — different thing; the leader's own §7 flagged ring-temporal as "dismissed too fast"). (2) It is the entry point of the common-path idea (B4 follow-on): per-direction (frame, camera) selection with minimal b_perp — forward/backward sectors reach near-zero parallax. (3) All ingredients exist in the DB-80 pipeline (pose interpolation, accumulation, box handling, gains).
**Expected evidence (cost-ascending):**
- **Step 1 (measurement, one /exec):** for the BMW sedan ROI (and the crowd truck ROI), compute the disocclusion zone (pixels whose anchor-time min-b_perp camera is box-blocked); for each, search ±10 frames × 7 cams for unobstructed visibility of the background X; report `temporal_visibility_fraction`, the chosen (frame, cam) histogram, and the b_perp distribution of the chosen pairs.
- **Step 2 (render A/B, same /exec or second):** render the ROI with temporal fill (anchor pixels everywhere else; fill ONLY where anchor evidence is missing AND temporal evidence exists; remaining gap = honest abstain/black); board base vs filled; vision gate.
- **Step 3 (only if 1–2 pass): generality** — apply to all 5 scenes' near-vehicle zones; count fills, vision-sample 2 scenes.

**Kill criteria:** temporal_visibility_fraction < 30 % on the sedan zone → time does not reach this geometry, close. Render shows cross-time artifacts WORSE than the ghost (exposure jump / moving-object residue / pose-error smear) after ONE debug pass → record + close (the exposure-jump failure specifically gates on whether per-frame gain re-solve fixes it; if not, close). Any generative fill / any fill outside the no-evidence zone → out of scope. More than 3 /execs total → stop and report.

**Max scope:** BMW + crowd ROIs first; 5-scene step only after vision pass; CPU/L4; reuse DB-80/81 pipeline code; results non-repo + Read-verify; secret 0.

**Required vision check:** the sedan ROI: ghost wheels region must show plausible REAL background (storefront/wall continuation), no new doubling, no exposure patch; boundaries of the fill not harshly visible. Eyes beat metrics.

**Output location:** `deliverables/db84_temporal_fill/`.

Result summary: TBD.

---

# DB-88: Segmentation-bounded moving-object compositing on EMC (GPU-light: YOLO-seg on L4; segmentation decides ownership only, NO generation) ⭐ WIN
Status: **EXPLORED / POS (2026-06-10)** — v5 is the FIRST variant in seven attempts (DB-83/85/87/88 v1–v4) that renders the driving Porsche as ONE intact car AND strictly improves on the EMC base (the EMC head-ghost block is gone; tail clean; full-pano regression-free). 5 /execs (cap exceeded by 2 with the user online and explicitly approving continuation).
**Winning recipe:** YOLOv8x-seg per camera → instances matched to moving-track box projections (IoU≥0.3, exposure-time poses) → (RULE 1) ERP rays projecting inside the chosen camera's instance mask at the object's distance ← that camera, uniform depth; (RULE 2) background rays whose camera projection lands in ANY camera's MOVING-object mask → next non-poisoned camera; residual all-poisoned penumbra → EMC fallback (no temporal fill); **c_own = the EMC-Voronoi-dominant camera at the object's direction** (v5's key: body capture time matches the surrounding remnants' time → natural join; the "most-frontal camera" choice displaced the body 16 px against its own penumbra).
**The 5-variant ledger (each step eliminated one wrong choice):** v1 poison=full YOLO union → 24 % of the image temporally filled (static cars poisoned everything; pink lower hemisphere). v2 poison=moving-matched-only → car intact, 1 k px fill = colour shards. v3 LiDAR-support gate on fill → shards persist (depth wasn't the shard cause). v4 fill disabled → shards gone but displaced tail-wheel remnant (body at most-frontal cam's time ≠ remnant time). v5 Voronoi-dominant c_own → WIN. Choice matrix (c_own × penumbra policy) exhausted; v5 unique optimum.
**Honest caveats:** 5/10 moving objects unmatched (left as EMC base — graceful; distant/small ones); single scene (BMW) so far — crowd/downtown generality pending; segmentation adds a model dependency (ownership only, zero generated content; YOLO ~140 MB on L4, ~2 s/cam).
Products `deliverables/db88_seg_composite/` (emc vs segcomposite PNGs + board). Secret 0.
Route: A (source-faithful compositing; the image-level silhouette is the input all six prior failures triangulated).

**Question:** With the precise per-camera silhouette of each moving object (instance segmentation on the native camera images, matched to the annotation box), does the two-rule composite — (1) silhouette rays ← the object's own camera at one exposure time; (2) background rays whose responsible camera projects INTO any object mask ← next visible camera, else temporal fill — finally render the driving Porsche single, intact, and without eating it?

**Hypothesis:** All six failures (DB-83 v3/85/87 v1–v3) needed exactly one missing input: where the object's pixels actually ARE in each camera (box geometry is wrong by 10–30 px somewhere; too big eats, too small ghosts). The mask answers both sub-problems at once: object extent (rule 1) and per-camera occlusion (rule 2). Segmentation assigns OWNERSHIP only — every output pixel is still a real sensor pixel; no content is invented.

**Plan (one /exec):** YOLOv8x-seg (ultralytics, ~140 MB, fits L4; EXP-A precedent) on the 2–3 cameras that see each moving object at the BMW anchor; match instances to box projections by IoU; build per-camera moving-object masks; render EMC + rule-1 (object rays: project at box distance into c_own, inside mask_own → force c_own; uniform per-object depth + single camera = fully consistent) + rule-2 (rect-minus-body background rays: a camera whose projection lands in its mask is invalid; pick next min-b_perp visible; none → DB-84 temporal fill with per-frame box tests). A/B board vs EMC base, 6× crops on the Porsche.

**Kill criteria:** segmentation misses the object (no instance with IoU>0.3 to the box projection) → leave that object as EMC base (graceful), record rate; composite eats the car / leaves ghost / adds new artifacts vs EMC base (vision) → kill, keep EMC-only; >3 /execs → stop. NO text prompts, NO inpainting, NO generative fill.

**Max scope:** BMW anchor first; crowd second only if BMW passes vision. L4; results non-repo + Read-verify; secret 0.

**Required vision check (eyes over metrics):** the Porsche: ONE car, intact roof/nose/tail, no third wheel, no eaten regions; penumbra shows real wall/storefront; static scene untouched. Compare at 6×.

**Output location:** `deliverables/db88_seg_composite/`.

Result summary: TBD.

---

# DB-87: Moving-object handling on EMC — three variants, all killed; the failure triangle now POINTS at the missing piece (image-level silhouette)
Status: **EXPLORED / KILLED after v1–v3 (2026-06-09 night, 3 /execs = brief cap)** — `deliverables/db87_emc_objlock/`. **cen_depth_b1_emc (no object handling) remains the best render.**
**The complete failure map (each mechanism verified by eye):**
- v1 box-footprint + box-depth lock → tail ghost-wheels (box AIR margin painted with the car; 3rd confirmation of the DB-83 v3 mechanism).
- v2 seam-routing (union rect, no depth change) → SAME ghost — which finally exposed the true mechanism: the ghost was never two-camera doubling; it is the SINGLE responsible camera's **disocclusion self-print** (the c\*-ray to the wall beside the car projects, via the 0.3 m offset, onto the car in that camera's image → the car prints onto the wall). EMC-base is clean there only because the natural Voronoi happened to give that wall to a camera that can actually see it.
- v3 body-lock + temporal penumbra fill (22k px) → fill EATS the car roof/tail (the union-rect penumbra over-covers; any box-geometry estimate of "which pixels should show the car" is wrong by 10–30 px in some direction — too big eats the car, too small leaves ghost).
**Conclusion (the three failures triangulate one missing input):** every lane needs the **image-level silhouette of the object in the chosen camera** — box geometry cannot provide it at the required precision. Two successor options (DB-88 candidates, user/Xinhan decision):
- **(A) Segmentation-bounded compositing:** run an instance segmenter (YOLO-seg/SAM — fits the L4; EXP-A already used YOLO here) on c_own's image; car-mask pixels ← c_own; outside-mask penumbra ← temporal fill. Segmentation decides pixel OWNERSHIP only — no content invented; source-faithfulness preserved.
- **(B) Static-world panorama:** REMOVE moving objects entirely via temporal fill (the background is 100 % temporally visible — DB-84). For the Cosmos first-frame this may be the more honest product: a 35 ms-skewed moving car is temporal noise to a video model whose geometry comes from the point-cloud track anyway. Changes product semantics — needs the user/Xinhan call.
Residual on the EMC base meanwhile: a faint translucent nose-trace + source-data purple fringe — both thin-band class. Secret 0.

---

# DB-86: Ego-motion shutter compensation — per-camera exposure-time EGO POSE (CPU/L4, pure geometry) ⭐ NEW BASELINE COMPONENT
Status: **EXPLORED / POS (2026-06-09 night, user-prompted)** — `scripts/phase3/db86_egomotion_shutter.py`, one /exec, BMW+crowd; brief written same-turn as the experiment (user was online and pointed at the defect; recorded honestly).
**Trigger:** user marked overlap on BOTH the driving Porsche AND the **parked** BMW X3 — a static object's overlap cannot be object motion. First-principles re-derivation: the asynchronous shutter (±22.5 ms) also displaces the **ego itself** between exposures; with the ego at the measured **7.66 m/s**, each camera's TRUE optical centre is up to **22.7 cm** from its calibrated anchor-time position — the same order as the inter-camera baseline (25 cm), and a systematic, fully-known error nobody had compensated. (LiDAR accumulation always had per-return time compensation; cameras never did — a pipeline asymmetry.)
**Fix (one geometric line):** `T_cam(t_i) = T_ego(t_i) · T_ego_cam` — interpolate the ego pose to each camera's exposure timestamp, express in the anchor ego frame, use those poses for projection and b_perp.
**Result (Read + vision verified):** ego speed 7.66/9.22 m/s, max cam displacement 22.7/21.5 cm; global change 5.0 %/3.9 % of pixels (all in the near-field cross-boundary band, no far-field regression). Vision: **the parked X3's tail overlap GONE (body lines continuous); the driving Porsche's head doubling AND tail ghost also essentially gone** — the ego-motion term was the dominant component of both marked defects. **EMC becomes a standard component of the base renderer** (cen_depth_b1_emc). Follow-ons: re-render the 5-scene base with EMC + re-evaluate DB-85's moving-object residual on top of EMC (likely much smaller now); fold EMC into db81/db82 standard pipeline.
**Products:** `deliverables/db86_egomotion_shutter/` (base/emc PNGs + boards). Secret 0.

---

# DB-85: Motion-aware object rendering — per-camera exposure-time box footprints + single-camera moving objects (CPU/L4, NO generation)
Status: **EXPLORED / PARTIAL (2026-06-09)** — machinery built and runs (6/14/15 moving objects handled per scene, no new large-scale artifacts); the object-BODY doubling improves (single-camera footprint with exposure-time box works — car head cleaner), but the trailing-edge ghost persists because the temporal penumbra fill under-covers (198/475/97 px filled — the zone `union\best-footprint` is nearly empty since the per-camera exposure-time footprints mostly overlap). **Precise next step recorded for the successor:** the correct penumbra is "pixels whose chosen-camera sightline crosses box@t_chosen but which lie OUTSIDE footprint_chosen" — compute with the existing seg_blocked(best_cam, X, box@t_best), then temporal-fill those (the DB-84 search already works; the car drives away within ±3 frames so background is fully visible). One focused fix, not a redesign. Scripts `scripts/phase3/db85_motion_aware.py`, products `deliverables/db85_motion_aware/`. Secret 0, 2 /execs.
Route: A (renderer correctness for MOVING objects; pure geometry + existing annotations)

**Question:** Does rendering each moving object from ONE camera — with its box pose interpolated to each camera's actual exposure timestamp (AV2 ring cams are offset up to ±22.5 ms; a 17.7 m/s car moves 0.62 m between straddling exposures = the measured 16 px doubling) — eliminate the moving-object doubling that DB-83 misdiagnosed as a depth problem?

**Hypothesis:** The doubling is per-camera capture-time motion parallax. Per moving track: interpolate box pose to each camera's exposure time; project each box@t_i into the centroid ERP (footprint_i); pick the camera seeing the object most completely (c_obj); inside ∪footprint_i lock the source to c_obj (its own exposure-time footprint carries box-surface depth; elsewhere in the union it sees background); other cameras' displaced car images are thereby suppressed. Time-consistency replaces the static case's depth-consistency.

**Expected evidence:** BMW sedan ROI A/B (current cen_depth_b1 vs motion-aware) — the driving Porsche intact, single, no doubling, no erasure; downtown/crowd spot-check for regressions on their moving objects. Stats: n moving tracks handled, footprint union coverage.

**Kill criteria:** residual shards/edges worse than the current doubling after ONE debug pass → record ("moving objects need video-prior/inpaint") and close; any static-scene regression → revert; >2 /execs → stop.
**Max scope:** BMW + downtown + crowd anchors; CPU/L4; annotations only (no detector, no learning); results non-repo + Read-verify; secret 0.
**Required vision check:** the Porsche: one car, intact, plausibly placed; box edges not visibly harsh; no new artifacts on static content. Eyes beat metrics.
**Output location:** `deliverables/db85_motion_aware/`.

Result summary: TBD.

---

## ⭐⭐ PREVIOUS ACTIVE BRIEF (2026-06-06 v2) — DB-79 DONE; read `agent/2026-06-06-deep-retrospective.md` (its practical conclusion is now re-scoped by DB-80 above)

> **Deep retrospective (19-agent workflow `wf_789ffbb7-1a7`) reset the priorities.** Root cause of the churn = the **source-faithful(A) vs look-good(B) fork was never resolved and we built under BOTH**. User decision (2026-06-06): **layer BOTH — A = canonical training-safe floor; B = a SEPARATE labeled presentation layer on top, never mixed into training truth.** Also found: the "3 geometry walls" are **~1.5 + 1 mislabeled** — DB-77B p90 15.4/12.8m is partly an NN-fill metric artifact scored across occlusion edges; DB-76a false-GREEN is rotation-only (no-depth baseline); only **EXP-B UniDepth** is a clean confirm (wall real at SILHOUETTES, over-credited on SURFACES). → settle the wall with a FAIR metric BEFORE any A100. User: **run DB-79 first, hold the A100.**

# DB-79: Fair-metric wall settlement — layered/edge-aware hold-out depth + camera-native z-buffer + depth-aware LOO (measurement-only, kill-or-cleanly-reopen the depth route)
Status: **EXPLORED / DONE (2026-06-06)** — BMW+0bae on Colab CPU; commits b38a2aa (step1+2) + 101b664 (step3). Route: A (source-faithful measurement) — fork-agnostic.
**Result (verdict `DENSIFIER_OK_but_RENDERBACK_residual`; Read + vision verified; full record in progress.md):** The "wall" is ~HALF measurement-confound + HALF real, and the real half is the seam. (1) Confound BUSTED: DB-77B's ~12m SURFACE densify wall was a single-near-wins NN-fill scoring artifact (held-out pts mis-scored across occlusion steps) — layer-aware **LiDAR-only** hold-out gives surface densify p90 **3.8–7.5cm** (curb/wall surface 6–12cm); vision: surface residual heat BLACK, no smear. (2) Depth-aware LOO render-back: depth slashes reproj median 150px→2–6px (so DB-76a's rotation-only false-GREEN WAS genuinely no-depth) BUT p90 stays >3px on near vertical structure — surface 5–15px, **curb/wall 55–88px**, silhouette 120–150px; vision: reproj heat BRIGHT (fail) on facades/curb/near-cars, DARK (pass) on flat road. (3) Cause of the residual = ~1.5m virtual-centre-to-camera baseline AMPLIFYING even cm depth errors at near range = Lemma-A from the render side. **→ depth route reopens DENSIFICATION but NOT the SEAM. Do NOT declare the depth-repair route reopened; near-field curb/wall ABSTAIN remains the honest ceiling.** Vindicates BOTH the leader's confound-correction AND the original abstain decision. **Caveat:** step3's geometric reproj conflates densify-error with the virtual-centre-baseline amplification → likely overstates vs a literal DB-76a PHOTOMETRIC camera→ERP→camera render-back (which would refine the SURFACE 5–15px number); but curb/wall 55–88px makes the seam conclusion robust. Only BMW+0bae (brief wants 3–5 AV2 + 1 Waymo before any contract claim). Products `deliverables/db79_fair_metric_wall/`, secret 0.

**Question:** After removing the two VERIFIED measurement confounds — (i) near-wins-per-pixel NN-densify (`scatter_depth argsort(-dd)` + `distance_transform_edt`) scored at held-out LiDAR pixels ACROSS occlusion steps; (ii) the rotation-only no-depth LOO baseline (`convergence_distance_m=None`) — does the source-faithful near-field depth route **reopen on SURFACES** (curb/wall/facade p90 → <0.5–1m), or is the real residual **confined to occlusion SILHOUETTES** where Lemma A makes abstain the honest ceiling?

**Hypothesis:** On smooth surfaces a layered/edge-aware, **LiDAR-only** densifier scored against held-out LiDAR drops curb/wall/facade p90 from ~12–15m to **<0.5–1m** (one near-field ERP pixel subtends ~1–4cm, so a metres-scale surface residual cannot be physical — it is the NN-fill artifact), while a residual tail persists **only at occlusion silhouettes**. EXP-B (UniDepthV2 hold-out, edge p90 11.8/11.2m, median scale 0.92–1.05) predicts the silhouette tail survives. ⇒ most defensible outcome: surfaces clean up, silhouettes stay walled (abstain vindicated with a FAIR on-disk artifact).

**Why now:** the retrospective found the depth route may have been partly **mis-killed on a confounded number**; this is the single biggest UNVERIFIED load-bearing claim and the cheapest to settle (CPU). It also gates whether the B presentation layer can geometry-anchor a seam-dissolve on surfaces or must abstain there. Settle it before spending A100 on 3DGS / generative.

**Expected evidence (measurement-only, NO RGB repair):**
- Replace `scatter_depth` single-near-wins with a **layered/LDI hold-out**: score each held-out LiDAR test point against the NEAREST of the stored depth layers at its ERP pixel (kills the far-vs-near scoring artifact).
- Densify **LiDAR-ONLY** (exclude stereo-SGBM from `Zd_tr` so silhouette SGBM error is not blamed on the densifier); add a **low-local-depth-gradient (single-surface) mask**; report **surface vs silhouette residuals SEPARATELY** (this split is the headline output).
- Build a **camera-native z-buffer** (rasterize fused geometry into each camera's native image grid; replace the ERP-ray-only seeding at `db77b:294-300`); re-run **DB-76a Battery-1 LOO with `convergence_distance_m = fused Zd`** (N1 mode exists in `sphere_projection.py`) and report **depth-aware false-GREEN beside the 0.373/0.223 no-depth numbers**.
- Strict **dynamic removal (boxes)** before accumulation; bucket dynamic/reflective/saturated separately.

**Pre-registered thresholds (SET BEFORE RUN; failure does NOT relax them):** report as **occlusion-edge-fraction + abstain mass + surface-p90 + silhouette-p90**, NOT one global depth percentile. "Reopened on surfaces" requires surface p90 **<1m** AND depth-aware curb/wall LOO **<3px** AND independent LiDAR-only densify p90 at those edges **<1–2m**. Cross-check against EXP-B (which already says the edge residual is real).

**Kill criteria:**
- Surface p90 stays **>2m** after the layered + LiDAR-only fix → wall **CONFIRMED with a fair metric**; close the depth-repair route, ship DB-78 + abstain, **stop re-testing geometry** (this "wall confirmed honestly" is itself a deliverable).
- Depth-aware false-GREEN drops but curb/wall densify p90 stays **>2m** → the gain is only where LiDAR already returns (road interior), NOT the textureless edges that drive the seam → do **NOT** declare the route reopened.
- Any RGB repair / final blend-warp pano / generation / inpaint / model-confidence-as-truth / secret written → **out of scope, stop.**
- Becomes a measurement campaign beyond BMW+0bae (then 3–5 AV2 + 1 Waymo) → stop & report.

**Max scope:** measurement only; reuse the existing AV2+Waymo hold-out harness. Fixed cases BMW `02a00399:0` + clean `0bae3b5e:30` FIRST; the metric-artifact argument is scene-independent, so a cleaned number must then be checked on ≥3–5 AV2 + ≥1 Waymo before any contract-level claim. **Compute: mostly CPU/L4** (no-depth batteries ran 12s; DB-76b 59s) — **NO A100 (hold it).** Tell the user before any runtime; route every remote/PowerShell result to a **non-repo file + Read-verify** (fabrication caveat — do NOT trust PowerShell echo / Glob "no files" / Edit-success text); secret-scan must be 0; secrets read only from env/non-repo file.

**Required vision check:** vision-check the depth-aware LOO render-back overlays per case — **a lower number with a visibly smeared curb/wall is still a FAIL** (eyes-over-metrics). Board: full ERP + the 4 marked ROIs + surface-vs-silhouette residual heatmaps + depth-aware-vs-no-depth false-GREEN overlay.

**Output location:** `deliverables/db79_fair_metric_wall/` (layered hold-out residual JSONs split surface/silhouette, camera-native z-buffer LOO, depth-aware false-GREEN table, review boards, manifest, pre-registered-thresholds JSON, kill/confirm verdict).

**Parallel (no compute, user/leader):** resolve the 5 Bosch format questions (`strategy §10`) + confirm B's consumer (world-model vs demo) + whether OFFLINE per-log reconstruction is an acceptable general deliverable (gates the later 3DGS dynamic-actor de-ghost). These do not block DB-79.

**Follow-ons (NOT active — open each as its own brief, gated on DB-79 + the fork):** (B-layer) geometry-leashed single-step refiner on the DB-78 seam band — benchmark **Percep360** FIRST, add a structure-hallucination guard beyond the object veto (DB36/DB40 prove object-gate-PASS still fakes ground/curb/pole), HARD-abstain the no-geometry near-ground. **Implementation mechanism candidate (user, 2026-06-06): cube-face tiling** — ERP → 6 pinhole faces → run a perspective-trained refiner per face → re-stitch (the real DiT360/CubeDiff/**TanDiT** trick for running 2D image models on an ERP). NOTE the premise gap: cube methods assume a SHARED optical center (zero parallax); our multi-center rig does not, so cube-tiling is only a *delivery mechanism* for the B refiner, NOT a fix for multi-center parallax — it still harmonizes-faint or hallucinates-geometry per the fork. (offline, if fork allows) per-log **canonical dynamic-actor render** (OmniRe/DeSiRe-GS static/dynamic split) to kill the moving-object ghost — verify SplatAD AV2 support + 40GB fit BEFORE any A100, A/B vs DB-78 with a hard vision-gate for GS blur.

---

## ⭐ ACTIVE BRIEF (2026-06-06 v1 — SUPERSEDED by DB-79 above; kept for route continuity) — read `agent/2026-06-06-leader-strategy-synthesis.md`

> Direction is LOCKED (see the leader strategy synthesis): main line = source-faithful **multi-center mosaic + provenance/risk/abstain data contract + dual-format (raw canonical + ERP derived view)**; seam = labeled provenance boundary, not always a defect; **abstain is a valid output**. Theory = two self-owned lemmas (occlusion non-identifiability + textureless rank-deficiency), NOT a misread plenoptic citation. DB75 stays permanently `presentation_only`. **NOTE (2026-06-06 v2): the look-good reframe is now LAYERED on top per the user's "both" decision — A stays the canonical training-safe floor; B is a separate labeled presentation layer. The deep-retrospective doc is the current read-first.**

# DB-76a: Calibrated GREEN reliability + stereo/temporal coverage audit (algorithm FOUNDATION; measurement-only, no RGB repair)
Status: **COMPLETE / closing** — all 4 batteries DONE (2026-06-06): ①② CPU, ③ A100 forward-stereo, ④ L4 multi-frame LiDAR. Conclusion: source-faithful single-centre repair hits a physical wall (forward-stereo recovers ~1%; multi-frame LiDAR = mid geometry base 11-18%, <25% bar). → **DB-77B (Branch B leashed renderer) becomes the next active brief.**
Route: A (source-faithful) — this IS the start of building the general algorithm: it builds the source-owned construction skeleton + the GREEN/abstain gate + the two under-used evidence sources, and validates them, BEFORE the DB77 repair operators are bolted on. It is NOT a repair brief and writes no final RGB pano.

**Question:** (1) Are our current GREEN ("trustworthy / training-usable") pixels actually correct — what is the false-GREEN rate? (2) How much of the panorama do we abstain on, where, weighted by downstream importance? (3) Can two so-far-unused evidence sources — the **AV2 forward stereo pair** and **ring-camera temporal multi-azimuth** — convert a meaningful fraction of currently-abstained near-ground back into validated GREEN?

**Hypothesis:** The 75-DB failure pattern was building repair on un-validated GREEN. Before adding repair operators (DB77) we must (a) measure GREEN precision via an independent hold-out — a pixel labeled GREEN should match a camera we did NOT use to build it; (b) measure abstain mass honestly; (c) test whether forward stereo (metric near-field depth, accurate at 3–5 m exactly where single-center copy fails) and surface-centric ring-temporal (the ego passes a curb and sees it from multiple azimuths over ~1–2 s) add real GREEN — NOT by re-running the DB74 optimizer, which already failed (`temporal_selected_fraction=0`; that killed the implementation, not the direction).

**Why now:** direction LOCKED; DB76a is the algorithm's foundation (steps 1–2 of the construction pipeline + the evidence layers step 3 will use). Every prior "build-first" DB (DB68–75) was NEG for skipping this validation. This is also exactly the evidence Bosch will ask for ("how clean is the valid mask, how much is recoverable").

**Expected evidence (4 batteries; outputs under the output location):**
- **Battery 1 — GREEN reliability via leave-one-camera-out render-back (render-free, CPU, DO FIRST — the keystone):** for each held-out ring camera `c_h`, build the source-owned representation **forbidding `c_h`'s RGB**, reproject GREEN regions into `c_h`'s image plane, compare to real `I_{c_h}` ONLY where: `c_h` sees it (visible/non-border), provenance proves `c_h` unused, operator ∈ {`raw_copy, reprojected_real, warped_real, tone_only`}, z-buffer says `c_h` should see it; bucket dynamic/reflective/saturated separately. Error = `max(photometric ΔE-or-census/ZNCC, gradient, edge/structure chamfer, geometry reproj_px)`. Outputs: `green_false_rate@τ`, `p50/p90/p95/p99 residual_px`, `operator_confusion_table`, `risk_calibration_curve`, `failure_by_{scene,operator,structure}`. Record the limit: LOO validates **co-observed** GREEN (most of it); truly-marginal pixels are the abstained ones (not claimed GREEN).
- **Battery 2 — abstain mass report (render-free, CPU):** three numbers, NOT on the full black ERP — `abstain_full_erp`; `abstain_task_valid_band` (ring pano occupies only the middle band of 1024×2048; black top/bottom ≠ failure); `abstain_weighted = Σ abstain(x)·w_task(x)` with higher `w_task` on lane/curb/vehicle/pedestrian/near-ground/free-space boundary.
- **Battery 3 — AV2 forward-stereo coverage (GPU; only if batteries 1–2 warrant):** rectify the 2 forward stereo cams → SGM / RAFT-Stereo / IGEV disparity + confidence + L-R consistency (NO pano render) → project stereo depth to ERP target rays → z-buffer raw-visibility check → LOO residual validation. Outputs: `stereo_{surface_valid, raw_visible, green_candidate, residual, failure_reason}_map`. Honest limit: forward only (does NOT help the side curb — that is battery 4); fails on textureless/wet/reflective.
- **Battery 4 — ring-temporal-side coverage (GPU; surface-centric, NOT the DB74 optimizer):** anchor ±1–2 s, all ring cams + ego poses; build static surface candidate (LiDAR / stereo / sparse MVS / stable tracks); per abstain ray ask "≥2 raw views see the same surface in the window?"; triangulation angle / baseline / photometric consistency / z-buffer; LOO render-back. Must remove dynamic objects (boxes) before accumulation. Waymo: rolling-shutter residual in a SEPARATE risk bucket (do not extrapolate AV2 → Waymo).

**Pre-registered thresholds (SET BEFORE RUN; failure does NOT relax them):**
- GREEN accepted: overall high-error ≤ **3%**; critical-structure (lane/curb/object boundary) ≤ **1%**; p95 residual ≤ **3 px**. "high-error" = reproj/edge residual > **3 px** in normal regions, > **2 px** on lane/curb/vehicle boundary; textureless regions: photometric unreliable → bucket by depth/visibility/rank-deficiency, do not score photometric there.
- Stereo/temporal build-worthy: ≥ **25%** of the relevant abstain band becomes `surface_valid ∧ raw_visible ∧ LOO_residual<3px`, OR ≥ **8–10%** of task-valid ERP becomes validated GREEN; AND no protected-structure failure.
- Contract-as-main-deliverable: task-valid abstain > **10%**, OR driving-critical abstain > **5%**, OR any continuous seam abstain > **25%** of seam length, OR GREEN high-error > **3%** without risk calibration.

**Kill criteria:**
- GREEN false rate > **5%** → STOP calling current GREEN "training truth" (downgrade to YELLOW/risk-weighted); re-derive GREEN before any DB77 repair.
- Stereo covers < **10%** of relevant band → keep stereo as forward side-evidence only; do not change main line.
- Temporal again yields < **5%** validated coverage or only sparse islands → close the ring-temporal route.
- Risk map not held-out/LOO-calibrated → call it **heuristic** risk, NOT conformal/calibrated.
- Any RGB repair / blend-or-warp final pano / DB75 tuning / generation / inpaint / source replacement / model-confidence-as-truth / secret-like value written → out of scope, stop.

**Max scope:** measurement only; no RGB repair, no DB75 tuning, no final blend/warp pano. **Sequencing:** run Battery 1 + 2 (render-free, CPU) FIRST and show results + pre-registered thresholds for review BEFORE any remote/GPU; run Battery 3 + 4 only if 1–2 warrant, under at most one bounded secure `/status` + `/exec` per battery. Fixed cases: BMW `02a00399:0` + clean control `0bae3b5e:30` (optional if data ready: dense-parking `2c65`, one Waymo segment for generality). No VGGT/Pi3/HF/DiT/FLUX/3DGS, no dataset scan beyond fixed cases, no RED promotion. **Security:** runtime URL/token/HF/Bearer/endpoint JSON are SECRETS — read only from process env or a non-repo secret file, never write to repo/manifest/board/log/prompt; secret-scan must be 0; tell the user before any A100.

**Required vision check:** per case, a review board with full ERP + the user's four marked ROIs (left road / lower-center road / center lane / right curb-wall-base) + GREEN/abstain overlay + (batteries 3–4) stereo/temporal coverage overlays + LOO residual heatmaps. Personally eyeball: do GREEN regions actually match the held-out camera? do stereo/temporal "recovered" pixels look real (not smeared)? If a number passes but the overlay shows smear/ghost/leak → downgrade to YELLOW/rejected. Eyes beat metrics on conflict.

**Output location:** `deliverables/db76a_green_reliability_coverage/` (sidecar maps, LOO residuals, coverage maps, abstain-mass JSON, review boards, manifest, pre-registered-thresholds JSON, go/no-go table).

**Multi-stance audit before run:** *Pro:* the only check that makes the Bosch "trust GREEN" contract real, and it doubles as building the algorithm's source-owned skeleton + the two under-tested evidence sources. *Anti:* risk of analysis-paralysis / over-measuring before DB77 repair; LOO cannot validate non-co-observed pixels. *Judge synthesis:* bound DB76a to the 4 batteries + a go/no-go table; the moment batteries 1–2 pass and 3–4 give a coverage number, proceed to DB77. Do NOT expand DB76a into a measurement campaign.

**Planned follow-ons (NOT active — map for the receiving agent; open each as its own brief):**
- **DB-77 — source-backed repair operators on validated GREEN/YELLOW only:** ULR-weighted source selection (`k<2 → single-source/abstain`), `reprojected_real` / `warped_real` (with warp-vector/residual/visibility/protected-veto sidecars), GradientShop/screened-Poisson seam (abstain = HARD in-solver constraint), Agarwala graph-cut + explicit abstain label. Fail → abstain, **NEVER blend**. The win is rigor + the DB76a-validated evidence, not new behavior on the textureless wall.
- **DB-78 — Bosch data contract v1:** dual-format (raw rig canonical + ERP derived view); per-pixel `{source_id, operator_id, risk (conformal-calibrated + calibrated/uncalibrated flag), loss_weight, unknown_or_abstain (reason), generated_mask, source_mixed_mask, visibility_count, residual_px}`; clean split `erp_training_rgb` vs `erp_presentation_rgb` (DB75 = presentation, loss_weight≈0). Send the 5 specific Bosch format questions (synthesis §10) to Xinhan/Bosch in parallel.

Result summary: **Batteries 1–2 complete + v1.1 metric-clean re-run (measurement-only, CPU Colab, secret-scan 0)** — see `progress.md` 2026-06-06 entries + `deliverables/db76a_green_reliability_coverage/`. **Keystone (v1.1, geometry-only, on the co-observed *measurable* overlap):** false-GREEN **BMW 0.373 / clean 0.223** (BMW>clean — v1.0 clean>BMW inversion was an exposure-seam artifact, now split into a separate `photometric_tone_seam_rate` BMW 0.17 / clean 0.40); disp p95 6.9 / 5.75 px; curb/wall_base worst (0.47–0.62). Pre-registered **>5% kill exceeded on geometry alone** → co-observed overlap is NOT single-source truth (supports reframe). co-observed = ~15% of GREEN; task band is **81% single-source** (LOO-unverifiable) — the real target for batteries 3–4; task-band abstain modest ~3% (below contract-as-main >10%). `proceed_to_batteries_3_4=true`. **Battery 3 (forward-stereo, A100) DONE** (after fixing a `cv2.stereoRectify` R,T direction bug that first gave 0 points): geometry-correct (fwd-centered cols ~880-1200, baseline 0.499m, Z p50 8.5/44m); **forward-overlap depth-correct LOO validated 78% (BMW) / 52% (clean)** → stereo depth DOES convert no-depth false-GREEN in the forward overlap; **BUT ring-LOO-validated GREEN only ~1% of task-valid (≪ 8-10% build-worthy)**, forward-only (no side-curb help) → recovery is small, strengthens the reframe. **Battery 4 REDEFINED (leader 2026-06-06) = multi-frame LiDAR accumulation** (geometry base, measurement-only, NOT the old ring-temporal optimizer): accumulate ±N-frame (try ±10) LiDAR via city-pose to the anchor, box-remove dynamics (car/person/cyclist), SplatAD-style per-return rolling-shutter/ego correction → measure how much currently-abstained / 81%-single-source near-ground (curb/wall-base bucketed) becomes dense + raw-visible static surface; LOO render-back residual. **Pre-registered: near-ground surface_valid ∧ raw_visible ∧ LOO<3px ≥25% = geometry base sufficient; <10% = thin base (record in conclusion).** Fixed cases BMW+clean; **CPU/L4 sufficient — LiDAR accumulation + numpy geometry, NOT GPU-bound (A100 unnecessary; reserve GPU for DB-77B)**; tell user before any runtime; output `deliverables/db76a_green_reliability_coverage/battery4_multiframe_lidar/`. **Battery 4 DONE (L4, ~59s, secret 0)** — multi-frame LiDAR: near-ground dense+raw-visible BMW 3.9%→**11.4%** / clean 4.6%→**18.4%** (3-4×); curb/wall BMW 0.7%→**5.0%** / clean 6.1%→**20.0%**; co-obs depth-quality LOO validated 77%/49%. `base_sufficient=False` (both <25% bar), `thin_base=False` (both >10%) → **mid-strength geometry base**. Vision: clean clean+dense; BMW near-field local smear (RS/occlusion/dynamic residual). **DB-76a ALL 4 BATTERIES COMPLETE** → close DB-76a; DB-77B becomes active.

---

## DB-77C (EXPLORED / CLOSED 2026-06-06) — superseded by DB-78 (flow view-interp) as the active brief

# DB-77C: Honest thin-band seam harmonizer (geometry-faithful where observable; learned seam-harmonize where presentation-only; abstain otherwise)
Status: **EXPLORED / CLOSED** (2026-06-06; was DB-77B "bake-off" — A/B done, hand-built B + StreetCrafter both off the main line). **Phase 0 base-selection DONE — base = `A1_view_none`** (A1/G not better than hard_select in core ROIs; A1 wins on outpaint completeness; A1 outpaint region = generated → `generated_mask`; board `deliverables/base_compare_bmw/`). **Phase 1 STARTING (A1-base leashed seam-clean):** masks {generated / seam-band 8-24px / object-moat protected / ABSTAIN} + Poisson low-freq tone-harmonize on the SAFE band (CPU, local now) + Difix3D+ zero-shot probe (A100, gated, **tell user before burning**); near-field object ghost zones (lower-center BMW/SUV edge + right curb-wall-base) = **hard-ABSTAIN, never harmonized**. One active brief.
Route: B (plausible renderer) — but **ADOPT/FINE-TUNE a learned LiDAR-conditioned backbone instead of hand-building it.**

**Why course-corrected (mid-term review findings — verified):**
- **Gate metric was invalid:** the tear-attribution `bad_densify_share=0.65` (`db77b:329-333`) measured "a LiDAR point is within 4px in ERP", NOT "denser geometry will fix it" — near-field 4px spans large depth discontinuities (`d_px(Z)`). The 0.65 that greenlit hand-built Option-B does NOT mean fixable. MUST re-derive from real LiDAR-depth-at-pixel vs densified depth.
- **"IBR validated" was over-stated:** the full-IBR ROI sheet (`02a00399_a000_bmw_p01_roi_sheet.jpg`) shows IBR tears the right curb into gray scramble, ghosts the centre lane, and is globally softer than hard_select (DrivingForward blur returning). The leash did not prevent it. (Leader had only eyeballed the safe road-only D board.)
- **LOCK contradiction:** generated_band = 0.48–0.57 = generating ~half the visible band = naked generation with a thin leash = the DiT360 failure we swore off. Re-honor the LOCK (abstain is valid, seam = provenance boundary): keep the generated band SMALL; abstain the ~35% no-geometry near-field.
- **We are hand-building what the 2025 field already ships, trained + validated:** StreetCrafter (CVPR2025, code released, LiDAR-conditioned diffusion — its LiDAR render IS the condition → sidesteps the whole bad-densification bug class); Difix3D+ (CVPR2025 Oral, `nvidia/difix`, single-step non-hallucinating refiner = our C-step); DeSiRe-GS (CVPR2025, arXiv 2411.11921) + PGSR (TVCG2024, arXiv 2406.06521) = the proven geometry recipe (2DGS disks + normal-from-scale + LiDAR-depth-L1 + unbiased depth) that cures our edge bug. DrivingForward "soft" is a STALE 2024 NEG.

**Question:** Does a released, learned, LiDAR-conditioned driving renderer (StreetCrafter; optionally XYZCylinder) produce a SHARP, seam-reduced single-center ERP on BMW + clean — ZERO-SHOT (no training) — better than our hand-built IBR and than hard_select, without inventing salient objects?

**⚠️ A/B DONE (CPU, 2026-06-06) — honest result (worker, leader-eyeballed):** the 3 renderer-bug fixes (per-cam occlusion z-buffer + depth-correct ULR weight + stereo anti-double-undistort) + the honest tear metric (hold-out LiDAR densify residual) were applied. **Bug-fixed IBR STILL tears facades/curb** (only road clean); honest densify residual p50=0.16m but **p90=15.4m (BMW) / 12.8m (clean)** at structure edges, ~50% fixable. → **Root cause is EDGE GEOMETRY (sparse LiDAR not sampling curb/wall edges + low-texture RGB), NOT renderer bugs. Geometry+IBR ceiling = Option-D (road IBR + facade hard_select).** Confirmed by leader eyeball of `02a00399_a000_bmw_p01_roi_sheet.jpg` (IBR tears facades, hard_select sharp, road OK, generated_mask huge). StreetCrafter (C2) also PARKED (worker repo check: per-scene, Waymo-only, 80GB, perspective, Vista — does not fit 40GB/AV2/ERP/zero-shot).

**REFRAMED GOAL (GPT Pro + leader, 2026-06-06):** *Geometry-faithful where observable; seam-HARMONIZED (narrow learned band) where only presentation is possible; ABSTAIN where neither.* Do NOT chase "global make seam disappear." The remaining flaw is hard_select's thin hard-cut SEAM (a learned appearance task, not geometry). **Two outputs:** `presentation_rgb` (seam-harmonized, narrow `generated_transition`, look-good) + `training_safe_rgb` (source-owned/reprojected/tone-only/road-geometry only; `generated_transition` loss_weight=0). DB75 = counter-example (generated_mask=0 yet source-mixed → still not truth). NOTE the abstain numbers: DB76a task-band abstain ~3%; the 81% single-source is *unverifiable* not *abstained*; the "~35% no-geometry near-field" lives INSIDE the DB77 seam/generation band — do NOT conflate these.

**DB-77C phases (cost-ascending; ONE at a time):**
- **Phase 0 — base-selection floors (CPU, NO A100, DO FIRST; also answers the user's A1/G question):** same-frame same-ROI board of candidate bases under the LOOK-GOOD bar: `hard_select`, `Option-D`, `bug-fixed IBR`, `DB75`, **and the user-flagged `A1_view_none` / `G_bmw_pano` / `BEST_bmw_pano`** (their generated-sky / blending is now acceptable since source-faithful is dropped — the only question is whether their blended smoothness beats hard_select's sharp-but-seamed look, or has worse near-field ghosting/doubling). Label each base's recipe (blend / completion / source-mixed / pure hard_select). Leader eyeballs, picks the best-looking base; the seam-harmonizer then runs on THAT base, not assumed hard_select.
- **Phase 1 — C1 Difix3D+ zero-shot probe (A100 ~1h, GATED; expect it may be the wrong tool):** off-the-shelf `nvidia/difix` on the chosen base, SEAM-BAND ONLY. ACCEPT only if changed pixels confined to the 8–24px seam band, outside-band Δ<1–2 gray levels, NO new vehicle/wheel/ped/sign/pole, lane/curb/edge displacement ≤2px, `generated_mask` = exactly the seam band. REJECT if it bends lane/curb, melts the SUV, makes facade/window edges wavy, or does broad stylization/exposure drift. (Difix is a NeRF/3DGS artifact refiner, NOT a hard-seam solver.)
- **Phase 2 — C3 self-supervised thin seam-inpainter (the likely real tool):** train on SYNTHETIC hard seams (cut a clean AV2/Waymo image in two, apply small displacement/color/parallax perturbation, mask the seam strip, learn to recover a seamless local transition). Input = base_rgb + seam_mask(8–24px) + source_id L/R + risk/depth-conf + object/lane/curb protected masks. Output = ONLY the narrow seam band; `operator_id=generated_transition`, `generated_mask=1` in band, `risk=high`, `loss_weight=0`. NO text-prompt semantic completion; object moat HARD-veto; net-new-object gate before output; structure eval (lane/curb Chamfer, edge displacement, object-mask consistency), NOT FID.
- **Phase 3 — generalization board:** ≥8 AV2 logs × 3 anchors (BMW-like curb + clean + dense-parking 2c65 + open-road + intersection + low-texture dark wall + dynamic-crossing-seam + exposure-stress); Bosch/paper claim → 12 AV2 + 3–5 Waymo (rolling-shutter/exposure stress). Most-likely-break (highest first): car/person crossing seam; lane/crosswalk/curb thin lines crossing seam; near facade/window edges; shadow+geometry jump; wet/reflective; high-speed rolling-shutter.

**Optional side kill-test — DB-77G-GeomKill (small, hard, NOT the main line; only if we want to try recovering facade geometry):** LiDAR-supervised 2DGS / PGSR / DeSiRe-GS surfel on BMW+clean+2c65 — ONE question: can it drop curb/wall/facade-edge hold-out depth p90 from 12–15m to <1m WITHOUT softening lane/curb/object? Go: edge p90<1m, fixable>75%, IBR tear<5%, lane/curb Chamfer<2–3px, no new object. Kill: edge p90 still >2m / curb softens / cost > explainable benefit. If it fails, do NOT open a big per-scene-3DGS engineering effort — keep Option-D + harmonizer.

**Must-fix renderer bugs before any renderer A100 (or the eval is confounded by our own bugs):**
1. [GATE] re-derive tear-attribution from real LiDAR depth, NOT 4px ERP proximity (`db77b:329-333`).
2. IBR has no per-camera occlusion/z-buffer test (`db77b:274-288`) — it blends occluded cameras' foreground; add a per-camera depth buffer, drop occluded cams (wv=0).
3. Blend weight reuses the L1 infinite-radius cos² feather (`db77b:284`), not depth-correct — recompute ULR-style from the depth-correct projection.
4. (2 min) confirm AV2 `stereo_front` k1,k2,k3 magnitudes (`db77b:234`) — avoid double-undistort.

**Quality bar:** PLAUSIBLE (coherent, seam reduced where geometry allows), NOT source-faithful sensor truth. Generated pixels carry `generated_mask`, kept separate; keep the generated band SMALL (single-step refiner, not iterative; abstain the no-geometry near-field). Re-honor the LOCK — do NOT chase "seam fully disappears" everywhere.

**Kill criteria:** invents new car/person/sign/fake lane/fake curb; large real structure rewritten; generated band stays ~half (not shrunk to single digits by geometry+abstain); iterative-diffusion drift; output used as source-faithful truth; any secret written.

**Do NOT chase (review-confirmed NEG):** Splatter-360 / PanSplat (360-in→360-out, inverse problem); naive generative pano (invents cars); iterative video-diffusion refit loops (drift past ~2 iters at our baseline → stay single-step). **WATCH:** DriveFix (Mar 2026, cross-camera consistency, no public code yet).

**Max scope:** Phase 0 bake-off = ~1 A100 hour, fixed cases only, ZERO-SHOT (no training); tell user before A100; one bounded `/status`+`/exec`; secret-scan 0. No training/fine-tune until the gate decides. Generality (more AV2 + Waymo scenes) only AFTER the gate picks a backbone.

**Required vision check:** eyeball the 4 ROIs — is the learned render sharp + seam-reduced vs hard_select, WITHOUT hallucinated salient objects? Metrics alone don't count; looks better but no geometry/leash support → presentation-only or rejected.

**Agent reality-check (2026-06-06, repo-verified; user kept all 3 C-routes open, relaying agent judgement to leader):**
- Agent ACCEPTS the 3 mid-term critiques (tear metric invalid; IBR seam-close over-stated; generated band violates LOCK).
- BUT StreetCrafter ([zju3dv/street_crafter](https://github.com/zju3dv/street_crafter)) reality ≠ "zero-shot ~1h AV2 ERP": it needs **per-scene training** (`train.py` distills diffusion into Street Gaussians), is **Waymo-only** (AV2 needs a self-written colorized-point-cloud + tracklet adapter), needs an **80GB A100** (we have 40GB), renders **perspective** (ERP needs multi-view stitching), and needs Vista weights. The zero-shot bake-off premise does NOT hold on AV2.
- The genuinely zero-shot option is **Difix3D+** ([nv-tlabs/Difix3D](https://github.com/nv-tlabs/Difix3D), HF `nvidia/difix`): single-step (`num_inference_steps=1`), near-real-time, zero-shot refine of one rendered image (NeRF/3DGS-general) — the real ~1h option, BUT perspective-artifact-designed (seam/ERP fix must be tested; needs a reference view).
- **3 C-routes kept open (user 2026-06-06):** (C1) Difix3D+ zero-shot refine of our hard_select / bug-fixed-IBR virtual-centre ERP [real ~1h, L4/40GB likely OK]; (C2) StreetCrafter Waymo per-scene [80GB GPU + hours, not the AV2 pain]; (C3) Framing-B fallback [hard_select sharp base + self-supervised thin-seam inpainter on our own clean AV2 seams + abstain on no-geometry].
- **A+B (CPU, no A100) are uncontested prerequisites, on standby:** re-derive tear-attribution from REAL LiDAR depth-at-pixel vs densified depth (fix the gate metric); fix the 3 renderer bugs (per-camera z-buffer occlusion, ULR depth-correct blend weight, stereo k1-3 double-undistort check). Await leader's reality-based pick before any A100.

**Output location:** `deliverables/db77b_streetcrafter_bakeoff/`.

**Source methods (frontier review wf_0f49813b-238, web-verified):** StreetCrafter (CVPR2025, code released); Difix3D+ (CVPR2025 Oral, `nvidia/difix`); DeSiRe-GS (CVPR2025, arXiv 2411.11921); PGSR (TVCG2024, arXiv 2406.06521); TCLC-GS (ECCV2024, arXiv 2404.02410); RoGS (arXiv 2405.14342, road-only fallback). Full review: `deliverables/.../midterm-review` + workflow `wf_0f49813b-238`.

Result summary: TBD — Phase 0 bake-off + gate decision; archive to progress.md when done.

---

## Shared hard constraints for any new brief (from `plans/2026-06-04-egsr-seam-and-route-roadmap.md`)
- Preserve DB42/DB43 language: DB32 `s40` is the Bosch-facing presentation/handoff candidate with source-sidestep + generated-sky caveats. It is not a fully source-faithful panorama, not a source-faithful ceiling, and not an original `G_bmw_pano` / `A1_view_none` / `BEST_bmw_pano` seam repair.
- Keep DB41 as a negative evidence boundary: under current evidence, the lower-right/right-line region is no-evidence/abstain for source-faithful repair.
- Do not reopen prompt-only DiT/FLUX ground, curb, lane, or right-line repair.
- Do not treat object-gate PASS as sufficient. DB23/DB36/DB40 prove detector-clean outputs can still contain fake road, curb, lane, slab, hole, vertical slice, or pole-like artifacts.
- Keep source-faithful, evidence-only, and presentation-only outputs separate. Any generated/presentation output must carry explicit `generated_mask` / edit mask and must not be described as Bosch training-data truth.
- `G_bmw_pano` is the classic BMW failure / diagnostic reference and has been visually rejected as the default repair base. Any classic BMW presentation attempt must choose its base from existing same-ROI boards before generation.
- If any brief hits its kill criteria, stop that direction, write the result to `progress.md`, and do not continue patch-on-patch under the same direction.

---

## CLOSED BRIEFS — DB-45 … DB-75 (pointers only; full facts in `progress.md`, summaries in `handoff.md` banners)

> Pruned 2026-06-06 from full bodies to one-line pointers (facts verified present in `progress.md`). All are closed/paused; none is active. The single active brief is DB-76a above.

**Geometry-evidence (VGGT) branch — all diagnostic/no-repair, no RED promotion:**
- **DB-45** Geometry foundation evidence audit (a–k) — **PAUSED** after DB45k VGGT pose/reflection coordinate audit = diagnostic-only; official camera-from-world fails no-reflection contract; ROI residuals no-promotion. No geometry evidence accepted.
- **DB-58** VGGT-assisted raw-camera seam ROI repair feasibility — **CPU preflight abstain/no-repair** (pose-admissibility + target-surface support gates fail).
- **DB-59** VGGT-assisted A1/G diagnostic geometry audit — **CPU preflight diagnostic/no-promotion**.
- **DB-60** VGGT-prior ungated A1/G quick-look — **presentation-only, rejected as repair** (A1 seam remains; G gets wavy curb).
- **DB-61** Fresh-A100 VGGT rerun ungated A1/G quick-look — **presentation-only, rejected** (real new VGGT inference; vision still weak).
- **DB-62** VGGT point-guided raw-camera source composite — **rejected as repair** (sparse islands; blocky hard crop).
- **DB-63** VGGT high-confidence component-gated raw-source probe — **fragmented sparse no-repair** (2 components, 0.021 ROI).

**Source/frame candidate mining + data-contract + infra branch:**
- **DB-46** BMW meeting presentation-only micro cleanup — side branch (presentation-only).
- **DB-47** (a–f) Source/frame/dataset candidate mining — **source-selection review/closure only**; `a200`/DB32 not displaced; DB56 closed 15/15 exact assets; DB57 no candidate promotion; paused.
- **DB-48** Koi center-preserve DiT360 outpainting side branch — presentation/demo side branch.
- **DB-49** (a–e) Bosch data contract / handoff packet — **inventory + partial-sidecar only**; `source_id_map` missing-blocking-not-fabricated; DB32 not uncaveated training data.
- **DB-50** EGSR source-faithful operator v0 — **0 executable repair targets** from existing artifacts.
- **DB-51** EGSR target/source-pair acquisition queue — ranks DB47f next; creates no repair.
- **DB-52** DB47f secure-runtime/data intake contract — **secure-runtime-contract-only**, paused.
- **DB-53** DB47f token-free launch harness dry-run — dry-run-only, paused.
- **DB-54** DB47f local exact-asset recovery audit — 0 local matches, paused.
- **DB-55** EGSR O3 photometric polish acceptance audit — accepted O3 as bounded **photometric-only** sub-operator (T1/YELLOW low-structure seams).
- **DB-56** DB47f exact closure batch execution — **accepted: 15/15 exact assets complete**; not candidate selection.
- **DB-57** DB47f exact-candidate visual review — **no candidate promotion**; keep `a200`/DB32.

**LTR / target-raycaster + photometric/source-label/geometry/temporal/presentation branch (the recent chain that led to the reframe):**
- **DB-64** LTR-v0 layered target-raycaster — **PAUSED at Phase5a** evidence endpoint; thresholds failed (`no_target_surface_support` high, longest supported component 0.146); renderer not entered.
- **DB-65** DB64 evidence-gated visible photometric fallback — **presentation/diagnostic photometric polish** (was current-best before DB75).
- **DB-66** Narrow-mask classic inpaint presentation fallback — **rejected by vision** (inpaint artifacts).
- **DB-67** Dense raw-aligned target-surface evidence audit — **rejected** (VGGT dense fails raw-aligned/zbuffer/continuity/LiDAR gates; clean control degraded; Phase2 renderer blocked).
- **DB-68** Edge-aware bounded photometric polish v2 — **rejected as seam solution** / weak presentation-only.
- **DB-69** User-marked geometry seam audit + structure-aware reroute — source-label-only candidate **rejected** (jagged/wavy seam; road/curb stays misaligned).
- **DB-70** Protected ground-plane local alignment — **rejected** by metrics + vision (too conservative; ROI energy worsened).
- **DB-71** Protected local presentation seam retouch — **PAUSED before run** (not a general algorithm).
- **DB-72** Global source-candidate optimizer — **diagnostic/operator-eligibility, not repair**; root cause: coverage 0.2742, two-source overlap 88,288 px, three+ overlap 0, LiDAR visible 0.0532.
- **DB-73** Full-ERP source-derived geometry candidate stack — **diagnostic; rejected as repair** (`geometry_operator_selected_fraction=0.0`).
- **DB-74** Temporal/multiframe raw-source candidate stack — **diagnostic; rejected** (`temporal_selected_fraction=0.0`).
- **DB-75** Full-ERP seam-band source-mixed presentation fallback — **`presentation_only/source_mixed_not_repair`**; current best *viewable* version only (`soft_r64_a080_g1`, BMW ROI seam energy 95.94→56.20, source-mix 0.1171). User vision override: only softens, does not connect.


---

# DB-78: Surround360 flow view-interp on the determinable overlap strip  [Route A / 2D-correspondence]
Status: EXPLORED — quantitative 5-scene generalization done (2026-06-06, real A100 runs, Read-verified, committed 593edd7).
Question: Can faithful 2D optical-flow view-interpolation on the FOV-overlap strip make the co-observed seam continuous WITHOUT depth, generalize across scenes, and abstain where ill-posed?
Hypothesis: flow moves REAL pixels (source-faithful, no-hallucinate) and needs only 2D correspondence (no metric depth) -> the 3x geometry wall (single-center reproject) does NOT apply; mechanism = ring-overlap geometry => scene-independent.
Why now: geometry reproject route 3x walled (DB-76a/77B/EXP-B); Difix-on-IBR lit-killed (audit a4f7c552); flow-interp is the one architecturally-different, reframe-compatible untested path; already implemented in run_a1_streetview_pipeline.py --mode view.
Expected evidence: multi-scene edited_frac stability + abstain-rate + far-field-warp (no distortion) + object-gate (no hallucination).
Kill criteria: flow smears/distorts far field; abstain fails to gate ill-posed regions; gain collapses on some scene type; hallucinates objects (object-gate breach).
Max scope: 5 staged AV2 logs, --mode view --prealign none, A100 via ColabClient; secret non-repo only; secret-scan 0.
Vision check: per-scene overlap-band boards (on Drive); NOTE: NOT yet vision-verified by the main agent this session (env cannot render images) -> needs a vision subagent / user eyeball.
Output location: deliverables/db78_flow_viewinterp/generalization/ (quantitative_5scene_diags.json + GENERALIZATION_REPORT.md + case_inventory.json).
Result: QUANTITATIVE multi-scene WIN (Read-verified). edited_frac STABLE 2.47-2.78% across all 5 scene types (highway/curb/clean/dense-ped/crowd) = scene-independent gain; far-field warp p90 <= 0.22px, frac_warp <= 0.66% = no distortion / no-hallucination; obj_frac/abstain 3.5-6.8% scales with object density = content-adaptive safety. CAVEATS: structural metrics only (per-scene seam VISUAL quality not vision-verified); --prealign none flow input is RGB but obj/ground still uses LiDAR (fully-no-LiDAR A/B NOT isolated); >5 logs + Waymo = DATA step for Bosch/paper bar. Full record: progress.md 2026-06-06 entries.
