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

# DB-20260602-12: ▶ AV Perspective Evidence Guidance (NO-GPU evidence-pack kill-test) — ★ L1 KILL-TEST DONE → copy-selection premise KILLED
Status: explored → **the L1 evidence-pack kill-test came back NEGATIVE for copy-selection (2026-06-02): LiDAR decisively prefers one copy BUT BOTH copies are ~20px from the true ego-centre position (straddle=0, same side); the winning copy's residual (16-21px med) is 3-4× the doubling gap. So copy-SELECTION is geometrically the WRONG operation, and the only faithful op (reproject both to LiDAR-true = N1/E2) smears on sparse LiDAR. → DOWNGRADE the reference-guided-diffusion-via-copy-selection premise (L2/L3 + EPI-Mix's "LiDAR collapses to the correct neighbour"). This is the VALID, VALUABLE kill the brief anticipated.** Full facts: `progress.md` "A1 RE-DO part 5". Also: Meta deghost-softmax + Jump depth-over (flow-mag) tested in the same Workflow = NO fix (band already flow-merged; residual = occlusion/textureless). Remaining real path to beat L1 = ACCURATE DENSE DEPTH (plane-sweep MVS untested reach, or LiDAR-anchored learned 3DGS de-shredded), NOT 2D compositing and NOT copy-selection-guided diffusion.
Route: A+B bridge (real-evidence gate before any generative fill)
Progress link: `agent/progress.md` 2026-06-02 "A1 RE-DO part 4" (the ceiling that motivates this) + the forthcoming "Evidence Pack" entry.
Origin: user + codex(gpt-5.5) adversarial dialogue 2026-06-02 (logged `agent/codex_logs/`). Reframe: do NOT treat DiT360 as a ready perspective→ERP solver; abstract it to **"perspective evidence enters ERP space but the model is constrained ONLY inside the valid mask."** Absorbs/expands DB-01 (LiDAR copy-disambiguation).
RESEARCH QUESTION (the crux this whole project keeps hitting): on the surfaces that actually DOUBLE in L1 (the BMW near-object, the mid-range building/tree at a seam), can REAL evidence — the two adjacent raw cameras' projected pixels + LiDAR depth/support + the epipolar candidate line — DECIDE which copy is correct (= the centre-view copy), to within a usable confidence? Today's findings say the doubling lives on mid-range textured surfaces where LiDAR is SPARSE and the two cameras see DIFFERENT surfaces (occlusion) — so this is genuinely uncertain and worth a cheap kill-test BEFORE any GPU/diffusion.
WHY NOW (grounded in part-4): every clean single-source 2D method = ≈L1 (it never learns which copy is right); DrivingForward 3DGS fuses but SHREDS the near-ground (its learned depth — the copy-decider — is wrong where evidence is thin); every "looks-different-from-L1" attempt mixes two copies → ghost/white-spot. ALL of it reduces to "we don't know which copy is correct." This brief tests, cheaply, whether real evidence CAN know.
★ CODEX(gpt-5.5 xhigh) SHARPENING (2026-06-02, log `agent/codex_logs/...-01-VERDICT.md`): my "clean 2D ≈ L1, mixing⇒artifact" was OVERBROAD. "Mixing⇒artifact" is FALSE — only naive ALPHA-blend ghosts. **My view-interp still did `novel=warp_i*(1-a)+warp_j*a` (alpha-blend-with-abstain) — I NEVER implemented Meta's deghost-softmax (prefer larger-flow/nearer source) or Jump's disparity-ordered OVER-compositing. So the A1 view_none ghost (gray car) was likely SELF-INFLICTED.** The gray car is co-visible Lambertian → the RIGHT compositing should single it. Occlusion/disocclusion genuinely needs depth/layers (no 2D fix). So this kill-test ALSO compares **L1 | alpha | Meta-deghost-softmax | Jump-depth-over** on the gray-car/BMW seam (running now as a multi-line Workflow). Codex's decision rule: if depth-ordered-over yields ONE sharp object w/ small holes → the 2D ceiling was self-inflicted; if it fails on confident geometry → stop 2D, go learned dense geometry/3DGS. Codex also: DON'T use diffusion as the CORE solver (rewrites evidence) — only sky/tiny-holes with a mask (reinforces the L2 object-gate).
THREE LAYERS (codex `ARG-C91D → E3B8 → A7F2`; do C91D first, do NOT jump to training a LoRA):
- **L1 — Evidence Pack (NO generation, NO GPU) [THE kill-test]:** per worst-doubling seam build: L1/E1.5 ERP, seam-band mask, the two adjacent cams' ERP slabs, raw camera patches + raw→ERP projection, epipolar candidate line, LiDAR projected points (colored by depth) + valid/support/confidence map. ANSWER ONE QUESTION (vision + a copy-pick metric): at the BMW / mid-range building / tree, does LiDAR+epipolar pick the CORRECT (un-doubled) copy, and on what fraction of the doubling-band pixels is it decisive?
- **L2 — Reference-Guided Fill (cheap prototype, only if L1 passes):** L1/E1.5 backbone, mask = seam band only, reference = projected raw perspective evidence, HARD rule mask-outside byte-exact L1, object-gate (no new car/person/sign without LiDAR/source support). Prototype with SDXL-inpaint/BrushNet before touching DiT360/FLUX. Prove > E1.5 (more continuous) AND not hallucinating like DiT360 outpaint.
- **L3 — EPI-Mix / DiT360 attention surgery (later):** raw evidence in K/V, attention only along the epipolar curve, LiDAR shrinks the curve to one candidate.
KILL CRITERIA: if the L1 evidence pack shows LiDAR/epipolar CANNOT decide the correct copy on the mid-range doubling surface (low support OR ambiguous/wrong copy on most band pixels), then DOWNGRADE the whole reference-guided-diffusion premise (DB-02/03 included) — the residual is genuinely under-determined → ship L1+E1.5 as the honest deliverable, or accept free-generation's hallucination cost. THE KILL IS A VALID, VALUABLE OUTCOME (saves GPU).
MAX SCOPE: L1 = CPU only, ~1 session, vision + one copy-pick metric on BMW + one mid-range building/tree spot (+ generalize to 0bae/fbee if decisive). Do NOT build L2/L3 until L1 passes. The recurring failure mode is charging the generative build before proving the evidence — this brief exists to stop that.
PITFALL (load-bearing): AV2 raw crop is NOT universal truth — non-co-located, so a raw camera shows ITS viewpoint, not necessarily what the seam should see. Rule: co-visible/LiDAR/epipolar-supported → trust raw evidence; unsupported → fall back to E1.5, never free-generate.
Required vision check: YES — eyeball the colored-LiDAR/epipolar overlay on the doubling surface; does it visibly pick the right copy?
Result summary: TBD → `agent/progress.md` "Evidence Pack" entry.

---

# DB-20260602-13: ▶ ONLY REMAINING LEAD to strictly beat L1 in-band — LEARNED strip-confined Band-MPI / MVSNet (leave-one-camera-out supervised)
Status: proposed (needs user GO — multi-day) — recommended by BOTH codex(gpt-5.5 xhigh) rounds after the 5-angle wall proof
Route: B (learned dense layered depth, strip-confined) — NOT generative (no diffusion core solver)
Origin: codex round-2 (`agent/codex_logs/...-02-VERDICT.md`). After 5 vision-judged angles proved the in-band doubling is depth-accuracy-bound (copy-SELECT→≈L1, copy-MIX→ghost, every depth-REPROJECT→smear/shred/≈L1; LiDAR straddle=0 kills selection), the ONLY thing not yet tried that could beat L1 is a LEARNED dense layered depth in the seam strip.
QUESTION: Can a strip-confined Band-MPI (multi-plane-image) / MVSNet-style cost-volume, supervised WITHOUT GT-ERP by leave-one-camera-out cross-camera reprojection + sparse LiDAR + occlusion masks + smoothness, predict dense layered depth accurate enough to reproject the seam band to the true ego-centre and SINGLE the near-object doubling (BMW) cleanly — where sparse-LiDAR reproject smeared (E2), zero-shot-ish DrivingForward shredded, and cross-view MVS was confident on <1%?
WHY IT MIGHT WORK (vs the dead methods): learns occlusion/disocclusion + a multi-DEPTH (layered) representation per ray (LDI/MPI — the thing single-warp/selection can't do); cost-volume aggregates ALL planes with learned regularization (vs my 24-plane raw-RGB argmax); leave-one-out gives a real supervision signal with NO GT-ERP (codex: no-GT is NOT a blocker). Precedents: MVSNet (ECCV'18 differentiable-homography cost volume), MPI (Zhou'18 learned layered), Band-MPI confined to strips (this project's long-noted "only untested reach").
★ DECISIVE OVERFIT KILL-TEST (codex): OVERFIT the strip-MPI/MVSNet on ONE AV2 log (BMW), supervised by leave-one-camera-out reprojection + LiDAR, render ONLY the seam strip to ego-centre. **If it cannot beat L1 on the BMW seam AFTER overfitting one log → the learned route is not worth scaling → STOP, ship align+E1.5.** (Overfit-first is the cheapest possible falsification.)
KILL CRITERIA: overfit-one-log can't single the BMW cleanly (still doubles/smears/blurs vs L1) → reject the learned route. Or: it works overfit but fails to generalize to fbee/0bae → it's a per-scene optimizer (like full-3DGS), reject for the faithful-data goal.
MAX SCOPE: multi-day, A100. Step 1 = the overfit kill-test on BMW ONLY (do not build the full train/val pipeline until overfit beats L1). NEEDS USER GO (multi-day commitment; per the co-decide-direction rule).
Required vision check: YES — does the overfit strip-render SINGLE the BMW cleanly (sharp, one car) vs L1's doubled-at-cut?
Result summary: TBD → `agent/progress.md`.

---

> **Context (2026-06-02):** the user wants two parallel routes — **A = Google-Street-View-style plausible multi-center** (warp REAL overlapping pixels into agreement, hide seams, LiDAR-guided) and **B = DiT360 refined with a real-evidence leash** — toward the ultimate goal of a near-perfect **PLAUSIBLE** seam (no hallucinated salient objects). The briefs below come from the 2026-06-02 divergent+adversarial ideation (`agent/BRAINSTORM-2026-06-02-seam-path-forward.md`, workflow `wf_1fc2d59b-bb5`). **▶ LEAD / FIRST TO DO = DB-11** (Street-View coarse-plane LiDAR-DIBR program, immediately below) — it sequences the cheap de-risk (DB-01 = its step A0) + the eval (DB-05) + PowerPaint (DB-06 = its A4), with per-seam-convergence (DB-04) as a PIVOT fallback. **Start A0 on CPU.** Keep DB-02 (Difix-on-band) and DB-03 (EPI-Mix) as the heavier alternative POSITION mechanisms to fall back to if DB-11 pivots.

# DB-20260602-11: ▶ LEAD — Street-View-style COARSE-PLANE LiDAR-DIBR panorama (Route A primary program)
Status: A0 NEG/MIXED → A1 retracted (buggy) → **A1 RE-DONE 2026-06-02 = ★ POSITIVE: faithful Surround360 flow view-interp is a CLEAN L1++ across 3 anchors; the LiDAR coarse-plane (this brief's headline thesis) is REFUTED (it distorts). The retracted NEG was an experiment-design bug, not a wall.**
A1 RE-DO (2026-06-02, the user's "把这条路走到尽头" mandate): the retracted "flow starves" NEG was a BUG — DIS flow was computed on the full mostly-disjoint ERP slabs → matched content ~300px apart → garbage flow in the band. FIX = compute flow on the OVERLAP STRIP only (mask grayscales to the wedge) → FB-consistency 0%→55-84%, in-band |flow| 300px→3px. With the faithful Surround360 novel-view synthesis (warp i·shift + j·(1−shift), virtual-centre) + FB-gating + confine + composite-onto-L1: **`--prealign none` = clean robust L1++** (singles GEOMETRIC seam doubling on textured co-visible surfaces — strict gain over E1.5's photometric-only; salient objects intact; far field byte-exact M_p90 ≤0.18px; ~3% edited; vision-clean on BMW/0bae/fbee). **`--prealign plane` (THIS BRIEF'S H1/H2 coarse-LiDAR-plane) HURTS** → warp artifacts where the facade fit is approximate (re-confirms A0) → **the win is Meta's FLOW, not our plane.** HONEST LIMIT (end of the 2D path): textureless-wall doubling + large-parallax OCCLUDING near objects are correctly ABSTAINED (kept L1) — singling those needs learned cross-view evidence (DB-02 band-3DGS / DB-03 EPI-Mix). Full facts: `progress.md` 2026-06-02 "A1 RE-DO" entry.
A1 retraction (superseded by the re-do above): code-review (41 agents) confirmed critical bugs — (#1) meshgrid coord SWAP in dis_flow_align made the flow warp garbage; (#7) reduced-scale seam mask leaves coverage holes the blender fills BLACK; (#5) no spatial-propagation flow. AND faithfulness: the old dis_flow_align warped toward L1, never implemented Surround360's novel-view synthesis. The "Route A ceiling ≈ L1+" verdict was retracted — and the re-do confirms it WAS premature (the method works once the flow design bug is fixed).
A1 result: Built the FULL Google method with OSS (pyransac3d / cv2.detail GraphCut+MultiBand / cv2 DIS) + reviewed reproject/confine. **A1_flow = NEG** (optical-flow residual STARVES on textureless → black/torn artifacts; E3 wall inside the full pipeline). **A1_core (no flow) = modest L1+** (ground+BMW preserved, far field byte-clean, photometric seam hidden, but residual blend-ghost + does NOT single doubling). VERDICT: the referenced Google method, faithfully built, tops out at ~L1+ here (its flow step starves on our textureless/wider-baseline surfaces; planes can't do non-planar objects). To single the doubling → learned cross-view fusion (DB-02/03). Figs `deliverables/a1_streetview_pipeline/`; full facts `progress.md` 2026-06-02 A1 entry.
A0 result: NEG/MIXED. Aggregate seam NCC rose (L1 0.822 → plane 0.884) but that MASKED real visual regressions (user-caught, vision-confirmed): full-frame coarse-plane re-render LOSES near-ground (reprojected out of FoV → black), MISALIGNS a building at a seam (approx facade fit), and SLICES the white BMW SUV (a non-planar near object reprojected at the wrong plane depth). LESSON: aggregate NCC is not a sufficient gate; eyeball every output for content-loss/sliced-objects. **REFINED:** A0 only implemented Google STEP 1 (coarse-plane reproject) and naively (full hard replace); it did NOT do Google's steps 2-4 (optical-flow warp + object-aware seam routing + blend/COMPOSITE onto L1) — which are exactly what handle the sliced car / misaligned building / lost ground. So "geometry can't" was premature; the real A1 = build the FULL composited Google-faithful pipeline, then vision-judge. Caveat: E3 flow starved on textureless + selection exhausted + our baseline wider than Street View → genuine uncertainty, not yet fairly tested. Full facts: `progress.md` 2026-06-02 A0 entry; figs `deliverables/a0_plane_dibr_probe/`.
Route: A (the lead Route-A program. ABSORBS/SEQUENCES: DB-01 = its A0; DB-05 = its eval; DB-06 = its A4; DB-04 = a PIVOT fallback; DB-02/DB-03 = heavier alternative position mechanisms if this pivots)
Progress link: `agent/progress.md` 2026-06-02 entries — "A1 CODE-REVIEW (RETRACTS …)" (the verification result that invalidated A1) + "A1 — FULL Google-style pipeline" (the build) + "A0" (the kill-test). Each is a logged experiment result bound to this brief.
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

Result summary: A0 NEG/MIXED (plane-alone misaligns) → A1 RE-DONE = **POSITIVE for the FLOW component** (faithful Surround360 view-interp = clean L1++, singles geometric seam doubling on textured co-visible surfaces, 3 anchors, far-field byte-exact) but **NEGATIVE for THIS brief's coarse-LiDAR-plane thesis** (it distorts). Net: the Street-View-style program yields a real clean L1++ via FLOW, not via our LiDAR plane; the under-determined residual (textureless / large-parallax occlusion) is abstained and routes to DB-02/03 (learned). Full facts → `agent/progress.md` 2026-06-02 "A1 RE-DO" entry (each artifact names GitHub/local/Drive).

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
