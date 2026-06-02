# Decision Briefs — experiment gate for Waymo2Panorama

This file is the **direction/decision gate**. It is STRONGLY BOUND to `progress.md` but does NOT duplicate it.

- **`decision_briefs.md` (this file)** = the *decision state* of each experiment direction: why do it, which directional hypothesis it tests, success/kill criteria, allowed scope, and a one-line result + link to `progress.md`.
- **`progress.md`** = the *facts*: exact commands, anchor/data/params, image artifacts, metrics, vision-check conclusion, POS/MIXED/NEG evidence (with GitHub/local/Drive locations).

**Rule (also in `README.md`): before starting ANY new experiment direction, create/update a brief here. Each brief has Kill criteria + Max scope — those are the load-bearing fields.** This project's recurring failure mode is *not* lack of ideas; it is patch-on-patch on a direction that "looks promising" until it's NEG. The brief is the entry gate that prevents that.

Status values: `proposed` / `running` / `explored` / `accepted` / `rejected` / `paused`.

### Template
```markdown
# DB-YYYYMMDD-NN: <short title>
Status: proposed / running / explored / accepted / rejected / paused
Route: A (street-view/geometry) | B (DiT360/generative) | infra | sidestep
Progress link: TBD
Question: ...
Hypothesis: ...
Why now: ...
Expected evidence: ...
Kill criteria: ...
Max scope: ...
Required vision check: ...
Result summary: TBD. Full details in `agent/progress.md#...`
```

---

> **Context (2026-06-02):** the user wants two parallel routes — **A = Google-Street-View-style plausible multi-center** (warp REAL overlapping pixels into agreement, hide seams, LiDAR-guided) and **B = DiT360 refined with a real-evidence leash** — toward the ultimate goal of a near-perfect **PLAUSIBLE** seam (no hallucinated salient objects). The briefs below come from the 2026-06-02 divergent+adversarial ideation (`agent/BRAINSTORM-2026-06-02-seam-path-forward.md`, workflow `wf_1fc2d59b-bb5`). **▶ LEAD / FIRST TO DO = DB-11** (Street-View coarse-plane LiDAR-DIBR program, immediately below) — it sequences the cheap de-risk (DB-01 = its step A0) + the eval (DB-05) + PowerPaint (DB-06 = its A4), with per-seam-convergence (DB-04) as a PIVOT fallback. **Start A0 on CPU.** Keep DB-02 (Difix-on-band) and DB-03 (EPI-Mix) as the heavier alternative POSITION mechanisms to fall back to if DB-11 pivots.

# DB-20260602-11: ▶ LEAD — Street-View-style COARSE-PLANE LiDAR-DIBR panorama (Route A primary program)
Status: proposed → STARTING A0 (CPU)
Route: A (the lead Route-A program. ABSORBS/SEQUENCES: DB-01 = its A0; DB-05 = its eval; DB-06 = its A4; DB-04 = a PIVOT fallback; DB-02/DB-03 = heavier alternative position mechanisms if this pivots)
Progress link: TBD (this session: a Colab CPU runtime is up)
Inspiration (grounded 2026-06-02, workflow `wf_23dfe385`): Google Street View's ACTUAL pipeline (Anguelov 2010 IEEE; Google "Seamless" 2017 blog) + Surround360. SV defeats non-co-located parallax with a **COARSE plane/mesh depth reprojection** + a residual flow-warp + seam/blend — NOT rotation-only, NOT flow-only. Our edge over SV: **real LiDAR** (they only estimate depth from flow).

RESEARCH QUESTION: Can a robust COARSE LiDAR plane-mesh (ground + a few facade planes) reproject the 7 non-co-located AV2 cameras into one ERP so near/mid surfaces ALIGN to within a few px at every seam (killing L1's doubling) — WITHOUT the per-pixel-depth smear that killed E2, and WITHOUT relying on optical flow — under the PLAUSIBLE bar (no hallucinated salient objects)?

WHY NOW / THE GAP: L1 = rotation-only (drops camera translation → near doubling, math-guaranteed). E2 = per-pixel LiDAR depth (sparse/noisy → smear; N1 error ∝ baseline·focal·Δ(1/z)). The MIDDLE we never isolated = robust **COARSE-PLANE** depth (a fitted plane is stable under LiDAR noise) — exactly SV's trick. Cheap, mostly CPU, uses our LiDAR edge.

HYPOTHESES (mechanistic + prediction):
- **H1 (core):** coarse plane-mesh DIBR (ray → hit the fitted plane → reproject into the covering camera with FULL R, C, depth) aligns plane-supported near/mid pixels → seam residual disparity <~5 px, far field stays L1-clean, NO smear (depth from a robust plane, not noisy per-pixel z).
- **H2:** a LIGHT, texture-gated + FB-consistency-gated global flow-warp ON TOP of H1 cleans residual misalignment WITHOUT E3's textureless starvation (H1 already aligns the bulk; flow only nudges textured residuals, abstains elsewhere).
- **H3:** graph-cut seam routing driven by the closed-form LiDAR parallax-budget cost places the hard cut through low-parallax/agreeing regions → routed seam invisible; never cuts a salient object.
- **H4:** coverage holes (poles / no-LiDAR / no-cam) filled by structure-continuation inpaint (PowerPaint + P_obj-negative + SAM/YOLO veto + LiDAR free-space) → plausible, object-invention ≈ 0.

EVALUATION (locked proxy metrics; VISION wins on conflict):
- `relative_warp` far-field ≈ 0 (don't break the far field) [validated ruler];
- in-band LiDAR-measured residual seam disparity ↓ (target <5 px on plane-supported pixels);
- object-safety gate: no net-new salient objects vs the real source strips (= DB-05);
- MANDATORY vision check on EVERY output image.

INNER-LOOP EXPERIMENT LADDER (cheap-first; each step isolates ONE variable; lock protocol in git before running):
- **A0 [KILL-TEST · CPU · ~1–2h] (= DB-01):** on BMW, fit ground+facade planes to the LiDAR sweep; measure plane-fit residual + seam-band coverage; render colorized LiDAR/plane points into the seam band and check (vision + NCC) whether the plane/LiDAR selects the CORRECT un-doubled copy on the MID-RANGE doubling surface (the 10–30 m building, NOT the near car). **KILL:** planes don't fit sanely OR LiDAR can't vote on the doubling surface → downgrade this program; PIVOT to plane-sweep MVS (DB-07) or the selection floor.
- **A1 [H1 · CPU]:** coarse-plane DIBR reproject all 7 cams → ERP; vision + seam-disparity + relative_warp far-field. **KILL:** smears like E2 OR far field broken → PIVOT to per-seam single-plane (DB-04) or up to plane-sweep/MPI.
- **A2 [H3 · CPU]:** add LiDAR-parallax graph-cut seam routing (reuse `seam_routing` external_cost + `parallax_budget_map`). **KILL:** seam still visible / moves <2–3% of pixels.
- **A3 [H2 · CPU/light-GPU]:** add the light gated flow-warp residual. **KILL:** textureless starve / introduces warp.
- **A4 [H4 · A100] (= DB-06):** inpaint poles/holes (PowerPaint, anti-object). **KILL:** object-gate fires.
- **A5 [integration]:** full pipeline vs L1+E1.5 on BMW; generalize to fbee/0bae; pass object-gate. CONCLUDE if stable across 3 anchors.

OUTER-LOOP DIRECTION CRITERIA: DEEPEN (A1 works → finer/per-region planes, facades, objects); PIVOT (A1 smears → per-seam single-plane DB-04, or plane-sweep/MPI real-depth DB-07); CONCLUDE (full pipeline beats L1+E1.5 on the locked metrics + passes object-gate on 3 anchors → write the method).

MAX SCOPE: A0–A2 are CPU (this session's runtime). Do NOT escalate to GPU (A3+) until A0+A1 are POS on vision. **Hard stop:** if A0 or A1 hits its kill criterion, STOP and run an outer-loop reflection / PIVOT — do NOT patch-on-patch (the project's recurring failure mode).

REQUIRED VISION CHECK: every A-step output, eyeballed; eyes beat metrics on conflict.

AUTORESEARCH SETUP (on go): workspace `experiments/streetview-dibr/{protocol,code,results,analysis}`; reuse `code/.../projection` (N1 scalar→plane), `graphcut_seam`, `seam_confined`, `parallax_budget_map`; lock each A-step protocol (git) BEFORE running; synthesize findings after A1/A3/A5; the `/loop` continuity is set up ONLY with the user's explicit go (per the discuss-before-charging rule).

Result summary: TBD. Full facts → `agent/progress.md` (each artifact names GitHub/local/Drive).

---

# DB-20260602-01: Shared LiDAR copy-disambiguation kill-test (gates DB-02 & DB-03)
Status: proposed
Route: A+B (shared de-risk)
Progress link: TBD
Question: On the MID-RANGE surface that actually doubles (10–30 m building/tree, NOT the near car), can colorized LiDAR points disambiguate the CORRECT (un-doubled) copy between the two adjacent cameras?
Hypothesis: LiDAR depth on the doubling surface selects the true copy on >X% of supported band pixels; if it can't, both the Difix geometry-leash (DB-02) and the EPI-Mix attention-collapse (DB-03) premises weaken together.
Why now: One cheap test gates the two most credible (but unbuilt) wall-breakers before any GPU spend.
Expected evidence: render colorized LiDAR points into the BMW seam band; vision + NCC: does the LiDAR point pick the un-doubled copy on the mid-range surface? quantify support coverage there.
Kill criteria: if LiDAR cannot vote on the mid-range doubling surface (low support OR wrong copy), DOWNGRADE DB-02/DB-03 and pivot to temporal MVS (DB-07) or the selection floor.
Max scope: ~1 day, CPU + vision; no training, no GPU.
Required vision check: YES — eyeball the chosen-copy overlay on the doubling surface.
Result summary: TBD. Full details in `agent/progress.md#...`

# DB-20260602-02: Route A — Difix-on-band (band-confined 3DGS fuse + single-step refiner)
Status: proposed
Route: A
Progress link: TBD
Question: Does band-confining the finetuned-DrivingForward 3DGS render (the one render verified DOUBLING-FREE here) + a single-step reference refiner (Difix3D+), anchored to byte-exact L1 far field, give a clean PLAUSIBLE seam without the global warp that got full-frame 3DGS rejected?
Hypothesis: confinement makes far field = L1 (global warp structurally impossible) while keeping the near-field fuse; the refiner fixes the residual blur.
Why now: ranked #1; the only POSITION mechanism independently verified doubling-free in this codebase.
Expected evidence: (spike, zero diffusion) composite EXISTING finetuned-3DGS render into seam band only → relative_warp far-field≈0 + vision: band car SINGLE (no doubling) though blurry. Then Difix refine + vision + object-gate (DB-05).
Kill criteria: if band-confined 3DGS still doubles the mid-range surface, OR the refiner invents texture in the ~18.6° band center where no neighbor is co-visible (hallucination violation, fails DB-05 gate), OR refiner under-sharpens to ≈E1.5 → reject.
Max scope: 30-min spike first (no GPU); then ≤1 GPU-day (Difix zero-shot; optional 1-day band-crop LoRA only if zero-shot under-sharpens).
Required vision check: YES — band car single? object invented in co-visibility hole?
Result summary: TBD. Full details in `agent/progress.md#...`

# DB-20260602-03: Route B — EPI-Mix (epipolar-masked + LiDAR-collapsed reference attention)
Status: proposed
Route: B
Progress link: TBD
Question: Can a seam-confined diffusion that puts the REAL neighbor-camera pixels in keys/values, with a hard epipolar-curve attention mask (from the already-coded closed-form F) + LiDAR collapsing the epipolar ambiguity to the true pixel, resolve the doubling WITHOUT forward-warping (so it dodges the E2–E6 depth-error wall) and WITHOUT hallucinating?
Hypothesis: depth only re-weights attention (never warps) → no N1 blowup; epipolar mask makes off-line hallucination geometrically unreachable; real neighbor pixels in K/V give a faithful target.
Why now: the project's own log names this "the only untested principled wall-breaker."
Expected evidence: gated by DB-01 first. Then on one LiDAR-supported BMW seam: epipolar+LiDAR reference attention, far-field byte-faithful composite; vision (no doubling, no invented object) + relative_warp far≈0 + NCC vs the REAL neighbor (not the loser copy).
Kill criteria: where LiDAR absent (~50% of band + textureless mid-range that dominates doubling), if the soft-continuation invents a salient object that the anti-object/SAM veto can't catch → reject for data use; or if attention surgery can't beat E1.5 on the supported band.
Max scope: ~1 GPU-day (attention-processor surgery on FLUX-fill/SDXL-inpaint); training-free v1 first.
Required vision check: YES — doubling resolved? any invented object?
Result summary: TBD. Full details in `agent/progress.md#...`

# DB-20260602-04: Per-seam adaptive convergence depth (automatic stitch-distance)
Status: proposed
Route: A
Progress link: TBD
Question: Does a single robust LiDAR-mode convergence distance r* fit PER SEAM (not global, not per-anchor — the untried axis) drop the LiDAR-measured residual seam disparity below ~5 px without far-field distortion?
Hypothesis: a rigid rotation+convergence remap (no per-pixel depth → cannot smear like E2) collapses the dominant-depth layer of each seam; expected to be a bounded incremental L1 upgrade, NOT a full solver (bimodal near-car + mid-building in one wedge defeats a single r*).
Why now: cheapest possible win; `convergence_distance_m` scalar path + the LiDAR disparity scorer already exist; CPU-only.
Expected evidence: per-seam 1-D line search r∈{6,10,15,25,40,1000}m scored by existing `_pair_exact_parallax_px`; per-seam min; vision far-field clean.
Kill criteria: if every seam's residual curve is flat or bimodal (no single clear minimum below ~5px) → it's not a solver, file as minor polish only.
Max scope: ~1 CPU-hour.
Required vision check: YES — far field still clean, dominant layer singled.
Result summary: TBD. Full details in `agent/progress.md#...`

# DB-20260602-05: In-band seam metric + object-safety GATE (infrastructure)
Status: proposed
Route: infra
Progress link: TBD
Question: Build the missing acceptance instrument — an in-band seam-geometry metric + an object-invention gate (net-new salient instances vs the real source strips, requiring LiDAR support) — so every future experiment is auto-judged (relative_warp is far-field-only and blind to in-band geometry + invention).
Hypothesis: a gate that flags net-new salient objects with zero LiDAR footprint would have killed the 16 DiT360 versions in minutes; it can certify L1+E1.5 today and unblock autonomous iteration.
Why now: lowest-variance contribution; enables DB-02/03/06/07 to self-verify.
Expected evidence: run the gate on the KNOWN-bad DiT360 koi_outpaint result (invented van/cars) vs clean L1 — must flag the van/cars on the outpaint and pass L1. Then inject synthetic cars into L1 strips to measure recall@conf.
Kill criteria: if detector recall is too low to catch a half-car straddling the cut even at the operating point → report as "invention rate at recall R", not a guarantee.
Max scope: GPU-day (SAM inference + synthetic-object recall study).
Required vision check: YES — confirm flagged regions match the real invented objects.
Result summary: TBD. Full details in `agent/progress.md#...`

# DB-20260602-06: PowerPaint structure-continuation floor (textureless, hard anti-object)
Status: proposed
Route: B
Progress link: TBD
Question: On the TEXTURELESS mid-range band where geometry/flow have no evidence, does PowerPaint (P_ctxt positive + P_obj negative, Blended-Latent confined) + a SAM/YOLO object-veto + LiDAR free-space prior give a plausible, auditable, zero-object-hallucination fill that abstains-to-E1.5 on hard frames?
Hypothesis: it won't fix doubling (concede), but it's a safe complementary polish for the regions every geometry route can't reach, with an auditable hallucination floor.
Why now: complementary to DB-02/03/07, not a re-do; YOLO scorer already in-repo.
Expected evidence: on the 4 KOI/BMW frames: band-fill + YOLO band-diff — (a) band continuous to vision, (b) object-veto fire rate = the hallucination rate the leash catches.
Kill criteria: if P_obj-negative does NOT drive invention to ~0, or it only duplicates what E1.5 already does byte-safe → drop.
Max scope: ~1 day, no finetune.
Required vision check: YES — continuous? any invented object slip the veto?
Result summary: TBD. Full details in `agent/progress.md#...`

# DB-20260602-07: Multi-frame plane-sweep MVS in the seam wedge (temporal)
Status: proposed
Route: A (temporal)
Progress link: TBD
Question: Does a cost-volume plane-sweep over target + frames −2..+2 (ego moves 1–3 m → real wide baseline) MEASURE peaked depth on the mid-range building/tree that E2 couldn't depth and E3-flow starved on?
Hypothesis: measuring depth from cross-view photo-consistency (not imposing a depth prior into N1) avoids the depth-error→shift blowup and the temporal baseline finally triangulates the dominant mid-range surface.
Why now: the documented "remaining real option" after E2 died.
Expected evidence: BMW wedge cost-volume; winning-depth map + cost-curve sharpness (peak-to-second-peak). On LiDAR-supported pixels does it beat E2 in abs_rel AND is the curve PEAKED on the doubling surface?
Kill criteria: if the cost curve is FLAT exactly on the textureless mid-range (depth-undetermined like E3) → kill fast before any compositing build. Needs DB (static/dynamic split) for movers.
Max scope: ~1 GPU-day; kill-test is 2–3 hours.
Required vision check: YES — depth map sane on the doubling surface.
Result summary: TBD. Full details in `agent/progress.md#...`

# DB-20260602-08: Frame/seam SELECTION as the deliverable (sidestep)
Status: proposed
Route: sidestep
Progress link: TBD
Question: For a training-DATA corpus (not a live stitcher), can we auto-detect which of the 7 seams in which frames are HARD (high closed-form LiDAR parallax-budget AND a salient object on the cut), ship L1+E1.5 everywhere, and DOWN-SELECT / time-shift to frames where every seam is solvable → a clean PLAUSIBLE 360 corpus at near-zero risk without solving the worst seam?
Hypothesis: many frames/seams are benign; choosing the geometry beats fighting it for a data deliverable.
Why now: near-zero-risk guaranteed-clean-corpus FLOOR that runs in parallel with the wall-breakers; uses existing parallax-budget map + YOLO scorer.
Expected evidence: per-frame per-seam hardness score over the logs; the fraction of frames where all 7 seams are "solvable"; vision-spot-check that the selected corpus is clean.
Kill criteria: if too few frames pass (corpus too small to be useful) → revisit threshold or combine with a fix.
Max scope: spike (hours), deterministic.
Required vision check: YES — spot-check the selected clean corpus.
Result summary: TBD. Full details in `agent/progress.md#...`

# DB-20260602-09: DiT360 v2 center-outpaint re-run (Koi demo, low-stakes)
Status: proposed
Route: B (demo, NOT a seam solver)
Progress link: `agent/progress.md` 2026-06-02 entry (v1 done); script `scripts/phase3/run_koi_outpaint_v2_colab.sh` staged
Question: Does re-running the center-only outpaint with official tau=50 + a scene-specific Miami prompt make the generated surroundings more coherent (lighting match) than the v1 generic-prompt run?
Hypothesis: more coherent / closer to official "好看", but STILL invented — faithfulness is unfixable by prompt/params.
Why now: Koi asked to retry with adjusted params/prompt ("看看效果"); low-stakes generator demo.
Expected evidence: v2 raw vs v1 raw, vision side-by-side: lighting/coherence improved? object hallucination still present (run DB-05 gate)?
Kill criteria: N/A (demo). Do NOT mistake it for a faithful-data path.
Max scope: ~13 min on A100 (1 invocation, sector+window).
Required vision check: YES — and explicitly label "demo, not faithful data".
Result summary: TBD. Full details in `agent/progress.md#...`

# DB-20260602-10: Copy-SELECTION family (routing / two-copy disambiguation / ConsensusGate)
Status: rejected
Route: A
Progress link: `agent/progress.md` (selection-route-exhausted verdict; depth-aware routing moved <1% pixels, NCC→0.82)
Question: Can choosing among real source pixels (parallax-budget seam routing, two-copy disambiguation, a learned per-column selector) remove the geometric offset?
Hypothesis (FALSIFIED): selection can hide the photometric step but CANNOT remove the geometric offset (two views always double the same surface).
Why now: logged as REJECTED so no future agent re-charges it. The explicit "selection route exhausted" verdict + the depth-aware-routing probe (moved <1% of pixels) settle it.
Kill criteria: already met.
Result summary: NEG. Selection hides color, not geometry. See `progress.md` selection entries.
