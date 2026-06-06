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

## ⭐ ACTIVE BRIEF (2026-06-06) — read `agent/2026-06-06-leader-strategy-synthesis.md` FIRST

> Direction is LOCKED (see the leader strategy synthesis): main line = source-faithful **multi-center mosaic + provenance/risk/abstain data contract + dual-format (raw canonical + ERP derived view)**; seam = labeled provenance boundary, not always a defect; **abstain is a valid output**. Theory = two self-owned lemmas (occlusion non-identifiability + textureless rank-deficiency), NOT a misread plenoptic citation. DB75 stays permanently `presentation_only`.

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
