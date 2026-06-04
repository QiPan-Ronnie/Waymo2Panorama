# Decision Briefs — ACTIVE experiment queue for Waymo2Panorama

This file is the **direction/decision gate**, and it holds ONLY **active / pending / in-progress** briefs.

**Protocol (user-set 2026-06-03):**
- Before starting ANY new experiment direction, create/update a brief here. Each brief MUST carry **Kill criteria** + **Max scope** (the load-bearing fields). This project's recurring failure mode is patch-on-patch on a "promising" direction until it's NEG — the brief is the entry gate that stops that.
- When a brief is **DONE** (accepted / rejected / explored / closed): **archive its conclusion into `agent/progress.md`**, mark it done, then **DELETE it from this file.** `progress.md` is the permanent record; this file stays a short live queue.
- **Completed briefs DB-01..13 (through 2026-06-03) are archived** in `progress.md` → entry "DECISION-BRIEF ARCHIVE". The accepted source-faithful deliverable = `scripts/phase3/_seamroute.py` (align + object-moat min-cut seam + virtual-centre select), now with the **BEV ground atlas** road layer adopted (`_bev_ground.py` → `SR_bmw_bevfinal_1024x2048.png`). Residual floors: off-plane curb, out-of-FoV black — physical/hardware.

Status values: `proposed` / `running` / `explored` / `accepted` / `rejected` / `paused`.

### Template
```markdown
# DB-YYYYMMDD-NN: <short title>
Status: proposed / running / explored / accepted / rejected / paused
Route: A (geometry) | B (generative) | infra | sidestep
Question: ... / Hypothesis: ... / Why now: ... / Expected evidence: ...
Kill criteria: ... / Max scope: ... / Required vision check: ...
Result summary: TBD → archive to progress.md when done, then delete here.
```

> **DONE THIS SESSION (2026-06-03, A100) — full record in `progress.md` (top "DiT360 SESSION SYNTHESIS" entry); kept here only as pointers so this queue stays short:**
> - **DB-44 Layer-aware seam routing / EGSR dispatcher v0** = **ACCEPTED dry-run gate**: mapped 29 DB43 known cases into layer/evidence/operator/claim components. No operator executed, no RED repair, DB41 right/lower-right abstain, DB32 caveated handoff, G diagnostic only. Results: `deliverables/dit360_v2/db44_layer_aware_dispatcher/`. Detail in progress.md.
> - **DB-43 Source-Faithfulness Eval v2 / Fake-Geometry Gate + EGSR triage** = **ACCEPTED gate**: built reason-coded known-case manifest/boards and locked DB44 preconditions. DB32 is caveated handoff/source-sidestep, DB41 remains abstain, fake road/curb/lane/slab/pole outputs reject, and G remains diagnostic only. Results: `deliverables/dit360_v2/db43_source_faithfulness_gate/`. Detail in progress.md.
> - **DB-42 seam decision and Bosch handoff synthesis** = **ACCEPTED**: packaged DB32 as current Bosch handoff candidate with explicit caveats and consolidated DB37-41 negative evidence. Results: `deliverables/dit360_v2/db42_seam_decision_handoff/`. Detail in progress.md.
> - **DB-41 right-white-line raw-camera evidence gate** = **CLOSED / repair rejected**: exact right/lower-right white-line ROIs fail the source-evidence gate (`right_roi` LiDAR 0.084; `lower_right_roi` LiDAR 0.000), and vision shows no continuous source-faithful white-line/curb geometry. Results: `deliverables/dit360_v2/db41_rightline_evidence_gate/`. Detail in progress.md.
> - **DB-40 A1/G v14 mask-alignment replay** = **CLOSED / seam repair rejected, root-cause accepted**: A1 keepout proves the right BMW slab/ghost came from candidate/mask mismatch, but the long_source-only A100 rerun generated a pole-like vertical artifact despite object-gate PASS. Do not proceed to G with this v14 DiT360 seam-repair route. Results: `deliverables/dit360_v2/db40_v14_mask_alignment/`. Detail in progress.md.
> - **DB-39 v14 trimap-clamp replay audit** = **REJECTED as G-family seam solution**: existing exact r008/h016/w025 v14 replay matrix already covers G/BEST/A1; board shows raw/soft/core variants either keep the seam or create vertical slice/slab artifacts. Results: `deliverables/dit360_v2/db39_v14_trimap_replay/`. Detail in progress.md.
> - **DB-38 Bosch-ready candidate handoff board** = **ACCEPTED DB32 as current handoff candidate with caveats**: board compares G/DB19/DB28/DB32/DB36 under Bosch world-model constraints; DB32 is the defensible source-sidestep handoff, not a fix for original G. Results: `deliverables/dit360_v2/db38_bosch_handoff/`. Detail in progress.md.
> - **DB-37 Google/Meta seam-mechanism gap audit** = **CLOSED / no new local repair opened**: public Google/Meta/StreetView mechanisms map to reliable overlap/depth/flow/global warp/source selection, all blocked by BMW ROI evidence or already tested by DB11-36. Result: `deliverables/dit360_v2/db37_google_meta_gap_audit/db37_google_meta_gap_audit.md`. Detail in progress.md.
> - **DB-36 ultra-narrow DiT360 red-line seam mask** = **REJECTED**: one A100 case on G with a 0.816% core mask passed object gate and preserved outside-mask pixels, but vision failed due fake pale ground slabs/black holes. Results: `deliverables/dit360_v2/db36_user_redline_mask/`. Detail in progress.md.
> - **DB-35 seam-first target board and donor diagnostic** = **REJECTED as repair / evidence accepted**: same-ROI board proved G/BEST/A1/DB14 variants do not solve the user seam; BEST/A1 donor patch does not safely improve the right white-line. Results: `deliverables/dit360_v2/db35_seam_first/`. Detail in progress.md.
> - **DB-34 current-best DB32 s40 QA and review pack** = **ACCEPTED current-best reference**: fresh object gate PASS (`netnew=0`), non-core/source pixels byte-exact, review board/manifest produced. Results: `deliverables/dit360_v2/db34_current_best_qa/`. Detail in progress.md.
> - **DB-33 Cube-face local sky-boundary harmonization** = **REJECTED**: source pixels stayed byte-exact, but local boundary variants either gave no improvement over DB-32 s40 or introduced visible sky halos/diagonal color bands in rectilinear review. Results: `deliverables/dit360_v2/db33_local_sky_boundary_harmonize/`. Detail in progress.md.
> - **DB-32 generated-sky chroma harmonization for a200** = **ACCEPTED with small-gain caveat**: CPU-only color match changes only the DB-29 generated sky core (`noncore_max_abs_diff=0`); best visual tradeoff is `s40`, reducing sky color mismatch without touching source content. Results: `deliverables/dit360_v2/db32_generated_sky_harmonize_v2/`. Detail in progress.md.
> - **DB-31 multi-log relaxed-clean source candidate scan** = **CLOSED / no successor found**: bounded scan of 22 relaxed-clean candidates plus exact seamroute on top non-BMW candidates did not beat BMW anchor `200`; a200 remains the current source base. Results: `deliverables/dit360_v2/db31_multilog_candidate_scan/`. Detail in progress.md.
> - **DB-30 sky-panel harmonization for a200** = **REJECTED before DiT**: automatic HSV/connectivity sky-panel mask included building/vehicle/road-adjacent regions; no DiT run. Results: `deliverables/dit360_v2/db30_sky_panel_a200/`. Detail in progress.md.
> - **DB-29 DiT360 sky-only completion for clean-subset anchor 200** = **ACCEPTED with sky-panel caveat**: object gate PASS (`netnew=0`), black sky band filled, source content preserved, but visible center sky color/panel discontinuity remains. Results: `deliverables/dit360_v2/db29_sky_clean_a200/`. Detail in progress.md.
> - **DB-28 clean-subset source-boundary candidate mining** = **ACCEPTED source candidate**: strict-clean anchor `200` is a better base than BMW anchor 0 for follow-up sky-only completion; no long mid-frame red-line defect, seamcore risk `5.05%`. Results: `deliverables/dit360_v2/db28_clean_subset_refine/`. Detail in progress.md.
> - **DB-27 temporal/frame-selection scan** = **EXPLORED / REJECTED for current BMW window**: nearby anchors 20/40 modestly improve LiDAR/risk metrics but remain the same source-label partition and are not clean same-scene replacements. Results: `deliverables/dit360_v2/db27_temporal_frame_scan/`. Detail in progress.md.
> - **D2 DiT360 seam-completion, WIDE ground-risk mask (5.56%) + tau{20,50}** = **NEG** (object-gate FAIL: invents small cars + melts textureless cuts). → superseded by DB-14 (thin mask). Results: `deliverables/dit360_v2/gr_tau*`.
> - **D4 DiT360 SKY-ONLY outpaint** = **POSITIVE** (gate-clean upper-hemisphere fill; rooflines byte-exact). → folded into DB-19. Results: `deliverables/dit360_v2/op_sky_t50_s0.png`, `sky_roofline_cmp.jpg`.
> - **DB-23 DiT360 ground/full outpaint rejudge** = **REJECTED**: ground gate PASS but vision FAIL due fake bottom road/lane/curb geometry; full gate FAIL with net-new `traffic_light`. Results: `deliverables/dit360_v2/db23_d4b_rejudge_montage.jpg`. Detail in progress.md.
> - **DB-24 Google/Meta-style long-line diagnosis** = **CLOSED explanatory**: the user-marked long line is a source/camera-id boundary in near-ground/dark-wall low-texture regions; Google/Meta-style flow would need reliable correspondences that this ROI does not show yet. Results: `deliverables/dit360_v2/db24_google_meta_line_diag/`. Detail in progress.md.
> - **DB-25 AV raw-camera evidence pack** = **CLOSED evidence-only**: ROI uses four camera labels, near-ground=62.3%, LiDAR support=9.4%, best pair flow reliable=68.2% but key right dark-wall pair `6-5` only 10.5%; recommendation = abstain from geometry warp. Results: `deliverables/dit360_v2/db25_longline_evidence_fetch/`. Detail in progress.md.
> - **DB-26 source-safe photometric attenuation** = **REJECTED**: low-frequency color attenuation changed only 1.07% of pano but did not remove the long line and introduced dark-wall color wash/smudge risk. Results: `deliverables/dit360_v2/db26_photometric_fetch/db26_attenuated_roi_montage.jpg`. Detail in progress.md.
> - **DB-20 DiT360 lever mining** = **MOSTLY SUPERSEDED / CLOSED**: prompt bug fixed, sky generalization accepted, T1 near-ground seam levers paused/rejected after DB-14 + DB-21. Reopen only through a new brief with new evidence.
> - **DB-19 sky-only combo/generalization** = **ACCEPTED** for BMW + 0bae; 2c65 gate-clean diagnostic with base-slab caveat. Results: `deliverables/dit360_v2/db19_G_bmw_pano_sky_t50_s0_postcompose_thr45.png`, `db19_0bae_sky_t50_s0_postcompose_thr45.png`, `db19_2c65_sky_t50_s0_postcompose_thr45.png`. Detail in progress.md.
> - **DB-22 CubeComposer/rectilinear diagnostic** = **CLOSED informative only**: rectilinear view confirmed DB-21 mask placement was not the root problem; DiT semantic ground redraw is. Result montage: `deliverables/dit360_v2/db22_rectilinear_diag/db22_rect_bmw_rightline_montage.jpg`. Detail in progress.md.
> - **DB-15/16/17** (non-DiT reroute / Poisson / line-snap) = CLOSED, superseded by the BEV ground atlas (codex round-8 lead). Detail in progress.md.
> - INFRA recipe + /code-review fixes (box-overlap object gate, fail-safe asserts, flood-fill outpaint mask) recorded in progress.md.

---

## PROPOSED NEXT GOAL PREP QUEUE (2026-06-04)

These briefs are copied from `agent/plans/2026-06-04-egsr-seam-and-route-roadmap.md` as the prepared queue for the next goal. They are **not running yet**. Start exactly one brief at a time, keep the stated max scope, and archive results into `progress.md` when a brief is done.

Shared hard constraints for all briefs below:
- Preserve DB42/DB43 language: DB32 `s40` is the Bosch-facing presentation/handoff candidate with source-sidestep + generated-sky caveats. It is not a fully source-faithful panorama, not a source-faithful ceiling, and not an original `G_bmw_pano` / `A1_view_none` / `BEST_bmw_pano` seam repair.
- Keep DB41 as a negative evidence boundary: under current evidence, the lower-right/right-line region is no-evidence/abstain for source-faithful repair.
- Do not reopen prompt-only DiT/FLUX ground, curb, lane, or right-line repair.
- Do not treat object-gate PASS as sufficient. DB23/DB36/DB40 prove detector-clean outputs can still contain fake road, curb, lane, slab, hole, vertical slice, or pole-like artifacts.
- Keep source-faithful, evidence-only, and presentation-only outputs separate. Any generated/presentation output must carry explicit `generated_mask` / edit mask and must not be described as Bosch training-data truth.
- `G_bmw_pano` is the classic BMW failure / diagnostic reference and has been visually rejected as the default repair base. Any classic BMW presentation attempt must choose its base from existing same-ROI boards before generation.
- If any brief hits its kill criteria, stop that direction, write the result to `progress.md`, and do not continue patch-on-patch under the same direction.

# DB-45: Geometry foundation evidence audit
Status: running
Route: A (geometry) / evidence-only

Question: Can VGGT/Fast3R/CUT3R/DAC/DAP/PriOr-Flow/FlowSeek-style evidence turn any currently RED seam into YELLOW/GREEN, or improve the confidence calibration for layer-aware routing?

Hypothesis: Modern multi-view geometry/depth/flow foundation models may provide denser pointmaps, tracks, confidence, or panoramic depth/flow cues than the current LiDAR/DA-V2/flow metadata, but they must be calibrated as evidence only. They must not be trusted as renderers or as truth in unseen/no-evidence regions.

Why now: DB41 and DB25 show current raw/LiDAR/flow evidence is insufficient for key BMW right-line/lower-right regions. Before trying new repairs, test whether newer geometry/depth/flow methods add reliable evidence or simply hallucinate confidence on known negatives.

Expected evidence:
- Evidence-only outputs: geometry confidence, pointmap/track support, depth/flow confidence, occlusion/no-evidence flags, and correlation with raw-camera/LiDAR/parallax evidence.
- Permission-state deltas: which segments remain RED, which become YELLOW/GREEN, and why.
- No repaired panorama in this brief.

Parked subtracks under this brief unless split later:
- DB-45a geometry foundation audit: VGGT / Fast3R / CUT3R pointmaps, tracks, confidence, and multi-view consistency.
- DB-45b depth risk upgrade: DAC / DAP versus current DA-V2-style depth metadata, especially ERP/large-FoV depth confidence.
- DB-45c flow confidence audit: PriOr-Flow / FlowSeek confidence, occlusion, and forward-backward reliability; no blind flow warp.
- DB-45d Waymo sensor artifact taxonomy: SplatAD / SplatFlow / Street Gaussians-style diagnostics for HDR/color, rolling shutter/sync, dynamic object, and parallax categories; not final panorama rendering.

Minimum decisive experiment:
- 8 fixed seam segments maximum for first pass.
- Positives: far/static seam with known raw support, LiDAR-supported facade/road seam, clean `a200`/DB32-like seam.
- Negatives: DB41 lower-right/right-line, DB25 dark-wall/key-pair low-flow seam, DB36/DB40 generated fake geometry, object-adjacent occlusion seam.
- Compare model confidence/tracks/pointmaps against raw-camera reprojection residual, LiDAR support where available, existing flow reliability, and human visual verdict.

Kill criteria:
- High confidence on DB41 lower-right/no-evidence ROI.
- High confidence on DB36/DB40 generated fake slabs, holes, vertical slices, or pole-like artifacts.
- Confidence conflicts with raw-camera or LiDAR evidence.
- Inferred geometry fills unseen regions and is treated as source truth.
- Cannot distinguish clean source seams from no-evidence seams.
- Only useful if used as an image renderer.
- No actionable change to EGSR permission states.

Max scope:
- Evidence-only; no panorama repair, no source replacement, no diffusion/refiner.
- 8 fixed segments for first pass; expand only through a follow-up brief.
- Do not download/run heavy models locally unless execution environment and scope are explicitly approved in the running brief.
- First running pass (2026-06-04): CPU/local manifest + board over existing DB25/DB41/DB43/DB44 evidence, plus A100 live/env/cache preflight only. No heavy model download, no model inference, no renderer, no repaired ERP.
- Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
- Phase0 result (2026-06-04): `gate_pass=true` for the fixed 8-control evidence audit; no RED promotion and no foundation-model confidence claim. DB45 remains running, because this only locked controls/registry/preflight and did not run an actual scoped foundation-model evidence job. Detail archived at top of `progress.md`.
- Phase1 sub-scope (2026-06-04): VGGT evidence feasibility gate only. Check current Colab repo/cache/env/HF-readiness against the frozen 8-control evidence schema. No install, no model download, no inference, no renderer, no repaired ERP. If VGGT repo/cache/env is missing or the only available confidence is uniform/non-evidential, stop the VGGT route, write `progress.md`, and do not continue patch-on-patch.
- Phase1 result (2026-06-04): VGGT current-runtime route is **no-go**, not a model negative. A100/data/repo are reachable and the user-provided HF token is valid, but remote repo is stale, `vggt` is not importable, the VGGT repo cache tarball is invalid/0-byte, HF Commercial checkpoint file access is still gated/403, no HF checkpoint cache was observed, and the current wrapper uses uniform confidence. No DB45 evidence accepted, no permission-state change, no RED promotion. Detail archived at top of `progress.md`.
- Phase2 sub-scope / DB45b (2026-06-04): Existing-evidence permission calibration. Question: can the current structured LiDAR/flow/depth/parallax/fake-geometry evidence define a stricter EGSR permission rule before any new foundation model is allowed? Hypothesis: current evidence should not promote any DB25/DB41/DB36/DB40 RED controls, but it can formalize the rule that flow-only, detector-clean, or case-level depth signals are insufficient without target-surface support. Why now: VGGT is waiting on gated access, while DB45 can still advance by turning existing evidence into a reusable permission gate. Expected evidence: calibration rows over the frozen 8 controls, false-positive examples for flow-only and detector-clean signals, permission deltas, kill checks, and boards showing raw/LiDAR/flow/fake-geometry controls. Kill criteria: any RED control is promoted by flow-only, detector-clean, case-level depth/parallax, or non-target-surface evidence; DB41 lower-right is not zero-LiDAR abstain; DB36/DB40 fake geometry is not rejected; DB32 is described as fully source-faithful or original-G repair; output suggests a repair operator or generated ERP. Max scope: CPU/local only, existing artifacts only, no A100, no model download/inference, no panorama repair/source replacement/diffusion, fixed DB45 8-control set only. Required vision check: board must include DB25/DB41 evidence overlays and DB36/DB40 fake-geometry references, plus final permission labels. Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
- Phase2 result (2026-06-04): DB45b accepted **permission-calibration-only** evidence. `gate_pass=true`, 8 rows, 17/17 checks PASS, `permission_state_changes=none`, `red_promotions=[]`. It formalizes that target-surface support is required; flow-only, detector-clean, case-level depth/parallax, outside-mask preservation, and best-pair laundering cannot promote RED. DB25/DB41/DB36/DB40 remain RED; DB32 remains source-sidestep/handoff. Detail archived at top of `progress.md`.

Required vision check:
- Board must include raw-camera support crop, LiDAR/depth/flow evidence overlays if available, model confidence overlay, and final permission-state label.
- Mandatory visual check on DB41 lower-right and DB36/DB40 fake-geometry negatives.

Result summary: TBD -> archive to `progress.md` when done, then delete this brief.

# DB-46: BMW meeting presentation-only micro cleanup
Status: proposed
Route: B (generative) / presentation-only

Question: Can a separately labeled presentation branch make the classic BMW seam look cleaner for discussion without claiming source-faithful repair?

Hypothesis: A narrow rectilinear/cubemap micro-cleanup branch may produce a visually clearer meeting board, but it should remain a demo/presentation asset with generated/edit masks. It must not contaminate Bosch/source-faithful claims.

Why now: The user has a Bosch/Koi discussion need and wants to show the seam situation as clearly as possible. The source-faithful branch should still abstain on DB41-like no-evidence regions, but a presentation branch can be useful if explicitly labeled.

Base-selection rule:
- Do not silently start from `G_bmw_pano`. It is a visually rejected diagnostic reference.
- Before any generation, choose a base from existing same-ROI boards: possible candidates include DB19 sky-only G variant, A1 keepout/mask-aligned diagnostic variants, or no base if all are too risky.
- DB32 `s40` is a separate Bosch source-sidestep handoff track, not a classic-G repair base.

Expected evidence:
- Base-selection board from existing artifacts.
- Very small edit masks and generated/edit masks.
- Full ERP plus rectilinear/cubemap crops.
- `presentation-only` label in filename/manifest/board.
- Comparison against G diagnostic reference, DB19 sky-only, A1/BEST diagnostics if used, and DB32 only as separate handoff track.

Kill criteria:
- Uses `G_bmw_pano` as the base without a base-selection decision.
- Any new car, person, sign, pole, lane marking, curb, road topology, or fake object appears.
- BMW shape, wheels, windows, or object boundary changes.
- Produces pole-like slice, fake slab, repeated texture, melted asphalt, or fake white line.
- Improvement is only visible in full ERP but fails rectilinear/crop review.
- Output is later described as Bosch training-data/source-faithful repair.

Max scope:
- Presentation branch only.
- Max 3 cases for first pass: one far/static seam, one road-color seam excluding curb/lane, and one DB41-like no-evidence negative if used only to verify abstain/presentation labeling.
- No broad prompt sweep; no more than one small parameter set per base unless a follow-up brief opens it.

Required vision check:
- Same-ROI and rectilinear/cubemap crop review.
- Explicit generated/edit mask overlay.
- Manual visual verdict and claim-level label required before showing in any handoff board.

Result summary: TBD -> archive to `progress.md` when done, then delete this brief.

# DB-47: Source/frame/dataset-level candidate mining
Status: proposed
Route: sidestep / dataset-level source selection

Question: Is the right solution for some hard seams to avoid them by choosing better frames, sources, anchors, or logs rather than locally repairing them?

Hypothesis: Some seams are no-evidence or physically underdetermined under the current source frame. A source/frame selection branch may yield more defensible panoramas than local repair, as DB28/DB32 already demonstrated for the current handoff candidate. The method must avoid cherry-picking by reporting acceptance/reject statistics.

Why now: If DB43/DB44 keep key BMW right-line/curb segments RED, a scalable dataset-level solution may be to avoid hard seams rather than hallucinate them.

Expected evidence:
- Stratified scan report, not only top pretty examples.
- Total scanned, strict accepted, relaxed accepted, rejected-by-reason, abstain-mask distribution, scene distribution, object density, seam risk, LiDAR support, and failure boards.
- Clear separation between source-sidestep handoff candidates and original-G seam repair claims.

Kill criteria:
- Scan becomes unbounded.
- Only reports top-10 prettiest examples.
- Selected set is distributionally narrow or cherry-picked.
- New candidate simply moves the seam defect elsewhere.
- Promotes source-sidestep as original-G seam repair.
- No acceptance-rate / reject-reason accounting.

Max scope:
- Bounded scan only. Scope must be specified in the running brief before any execution.
- No local repair or generation in this brief.
- First pass should reuse existing candidate mining metrics where possible.

Required vision check:
- Include both wins and failures.
- Same-ROI/source-boundary boards for accepted and rejected candidates.
- Explicit reason for why a selected candidate is a source-sidestep rather than seam repair.

Result summary: TBD -> archive to `progress.md` when done, then delete this brief.

# DB-48: Koi center-preserve DiT360 outpainting side branch
Status: proposed
Route: B (generative) / presentation-demo side branch

Question: Does official-style center-preserve DiT360 outpainting become more coherent with stricter preserve ratio, tau, and scene prompt, and is it useful as a Koi-facing capability demo?

Hypothesis: Center-preserve outpainting may improve as a visual/demo branch with official-style parameters, but it will remain invented/presentation output rather than source-faithful AV reconstruction.

Why now: Koi explicitly wanted this branch revisited, and prior center-only outpainting showed capability but failed as data due invented surroundings, salient objects, and lighting/box mismatch.

Expected evidence:
- Full ERP outputs with preserved-center diff and generated region mask.
- Object gate and visual artifact review.
- Explicit `presentation-demo-only` label.
- Comparison against prior center-outpaint negative and DB32/source-faithful handoff candidate.

Kill criteria:
- Invented salient vehicles, people, signs, traffic lights, poles, or road topology dominate.
- Preserved center is visibly boxed, lighting-mismatched, or inconsistent.
- Surroundings become a different city/scene.
- Branch starts being interpreted as Bosch source-faithful data.
- Commercial/license concerns are ignored in Bosch-facing claims.

Max scope:
- Max 4-6 cases.
- Presentation/demo branch only.
- Not a seam-source repair; do not use it to claim original-G seam is fixed.

Required vision check:
- Full ERP plus center preserve crop, generated mask overlay, object/semantic review, and side-by-side with prior outpaint and current handoff candidate.

Result summary: TBD -> archive to `progress.md` when done, then delete this brief.

# DB-49: Bosch-facing data contract / handoff packet
Status: proposed
Route: infra / handoff / data contract

Question: How should the final Bosch-facing output expose caveats, generated regions, abstain masks, risk maps, and current-best image selection?

Hypothesis: The safest Bosch deliverable is a provenance-labeled data product, not a single uncaveated image. Explicit maps and reason-coded reports prevent generated or no-evidence regions from being misused as sensor evidence.

Why now: DB42 already established a handoff candidate and caveats. Future EGSR work should feed into a clearer data contract: source ownership, generated masks, unknown/abstain masks, risk maps, and eval reports.

Expected evidence / deliverable shape:
- Bosch-facing summary board/report.
- `source_id_map`
- `generated_mask`
- `unknown_or_abstain_mask`
- `risk_map`
- `eval_report`
- candidate image selection and caveat table.
- Separate labels for `source-faithful`, `source-sidestep`, `presentation-only`, `generated`, and `abstain`.

Required language:
- DB32 `s40` is the current defensible handoff candidate.
- DB32 avoids the worst seam through source-sidestep and sky completion/harmonization, but is not an original-G seam repair.
- Ground/object/lane/curb generation is not training data.
- No-evidence ROI is abstained.
- Generated sky/out-of-FOV is explicitly masked.
- Output is a multi-center source mosaic, not a physically single-center capture.
- One sample does not prove Waymo-wide generality.
- Any commercial/Bosch use must check generation-model license.
- Downstream world-model impact requires Bosch's own protocol.

Kill criteria:
- Report hides generated/unknown/abstain regions.
- Claim language overstates seam repair.
- DB32/source-sidestep and original-G seam repair are mixed together.
- Presentation-only output is shown without generated/edit masks.
- Data contract lacks source ownership or risk/abstain maps.

Max scope:
- Packaging/reporting only after candidate outputs exist.
- No new image generation or repair in this brief unless explicitly opened by another brief.

Required vision check:
- Final board must include the image, masks, risk/abstain overlays, and same-ROI caveat crops.
- Manual claim-language review before any Bosch/Koi-facing use.

Result summary: TBD -> archive to `progress.md` when done, then delete this brief.
