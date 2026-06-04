# EGSR Seam Repair and Route Roadmap Plan

> **For agentic workers:** This is a strategy and decision-roadmap plan, not an execution log. Do not run experiments from this file directly. Before any new experiment direction, open or update a decision brief in `agent/decision_briefs.md` with kill criteria and max scope.

**Goal:** Maintain one durable plan for the next Waymo2Panorama exploration stage, led by a general seam repair strategy while preserving non-seam fallback routes already discussed. Execution stays anchored to the current repo's Waymo data, artifacts, calibration, and ROIs unless a later brief explicitly opens a separate dataset contract.

**Architecture:** Treat seam repair as evidence-gated segment dispatch, not one global inpainting model. Each seam component is classified, assigned an evidence state, then routed to the weakest sufficient operator; no-evidence segments abstain in the source-faithful branch and may only be edited in a separately labeled presentation branch.

**Tech Stack:** Existing Waymo2Panorama artifacts and scripts, current repo Waymo calibration/raw cameras/ROIs, current seamroute/BEV/E1.5 outputs, optional future LPAM-style local alignment, optional future geometry-foundation evidence, optional future constrained diffusion for presentation-only outputs.

---

## 0. Current Fixed Facts

These are the constraints this plan must not forget.

- DB32 `s40` is the current Bosch-facing presentation/handoff candidate, with source-sidestep + generated-sky caveats. It is not a fully source-faithful panorama, not a source-faithful ceiling, and not a repaired original-G seam.
- Do not claim `G_bmw_pano`, `A1_view_none`, or `BEST_bmw_pano` has a fixed right-ground/right-line seam.
- `G_bmw_pano` is the classic BMW failure / diagnostic reference and has been visually rejected as the default repair base.
- DB35-42 closed the current G/A1/BEST seam repair lanes under existing evidence: donor patching, v14 trimap replay, prompt-only DiT ground seam, right-line evidence gate, and mask-only A1/G replay are not acceptable seam solutions.
- DB41 is the key right-line evidence boundary: `right_roi` LiDAR support is low, and `lower_right_roi` has zero LiDAR support on the actual target surface. Current evidence does not support source-faithful lower-right white-line/curb repair.
- DiT360 remains useful for sky/out-of-FOV and presentation experiments, but current project evidence says prompt-only ground/curb/lane inpainting invents fake geometry.
- Any future experiment direction must first be opened as a decision brief with kill criteria and max scope.
- Any brief that hits its kill criteria must stop, write `progress.md`, and must not continue patch-on-patch under the same direction.

---

## 0.1 Execution Status (2026-06-04)

DB43 Source-Faithfulness Eval v2 / Fake-Geometry Gate + EGSR triage is completed and accepted as the precondition gate for DB44. Outputs are under `deliverables/dit360_v2/db43_source_faithfulness_gate/`, with script `scripts/phase3/db43_source_faithfulness_gate.py`.

DB43 locks these next-step constraints: DB32 `s40` is caveated handoff/source-sidestep, DB41 lower-right/right-line is abstain under current evidence, prompt-only ground/curb/lane/right-line repair remains blocked, and detector-clean fake road/curb/lane/slab/pole outputs must reject by reason code rather than scalar score.

DB44 Layer-aware seam routing / EGSR dispatcher v0 is completed and accepted as a CPU-only dispatcher dry-run gate. Outputs are under `deliverables/dit360_v2/db44_layer_aware_dispatcher/`, with script `scripts/phase3/db44_layer_aware_dispatcher.py`. DB44 did not repair or generate a new ERP; it mapped 29 DB43 known cases into layer/evidence/operator/claim components, kept DB41 RED/abstain, and executed no operators.

DB45 is now running. Its phase0 evidence-only control/registry pass is completed: outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45_geometry_evidence_audit.py`. DB45 v0 fixed 8 controls, registered reusable evidence sources, verified A100 live/env/cache state only, and kept all permission states unchanged (`GREEN=1`, `YELLOW=2`, `RED=5`; no RED promotion; no foundation-model confidence claimed).

DB45a VGGT evidence feasibility gate is completed as a current-runtime **no-go**. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45a_vggt_feasibility_gate.py`. This is not a VGGT model negative: it records that the current Colab runtime has A100/data/repo access and the user-provided HF token is valid, but the remote repo is stale, `vggt` is not importable, the cached VGGT repo tarball is invalid/0-byte, HF Commercial checkpoint file access is still gated/403, no checkpoint cache is present, and the existing VGGT wrapper uses uniform confidence. No install/download/inference was run, no DB45 evidence was accepted, and no permission state changed.

DB45b Existing-evidence permission calibration is completed and accepted as **permission-calibration-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45b_evidence_permission_calibrator.py`. DB45b uses only existing DB25/DB41/DB36/DB40/DB45/depth/parallax artifacts, no A100/model/repair. It keeps all 8 frozen controls unchanged (`permission_state_changes=none`, `red_promotions=[]`) and formalizes the next hard EGSR rules: target-surface support is required; flow-only, detector-clean, case-level depth/parallax, outside-mask preservation, generated-core confidence, and best-pair laundering cannot promote RED. DB41 lower-right/right-line remains no-evidence/abstain; DB36/DB40 remain fake-geometry rejects; DB32 remains caveated handoff/source-sidestep.

DB45c VGGT Commercial access update + schema gate is completed and accepted as **readiness-and-schema-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45c_vggt_access_schema_gate.py`. HF Commercial file access is now cleared (`config.json` HEAD 403 -> 200), but this is not accepted VGGT geometry evidence. Current blockers remain: remote repo stale at `d544214`, `vggt` import missing, VGGT repo cache tarball 0 bytes, no verified checkpoint cache, and existing VGGT wrapper emits uniform `np.ones` confidence. No install/download/inference/repair was run, no DB45 permission state changed, and no RED control was promoted.

DB45d VGGT official setup/load smoke is completed and accepted as **setup-and-api-smoke-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45d_vggt_setup_smoke_gate.py`. One bounded A100 job cloned official VGGT, loaded `facebook/VGGT-1B-Commercial`, cached the checkpoint on Drive, and verified confidence-capable API fields. No AV image inference, renderer, repair, source replacement, or RED promotion was run. This clears the setup/checkpoint/API blocker for a future ROI probe, but still does not create accepted VGGT geometry evidence.

DB45e VGGT frozen-ROI confidence probe is completed and accepted as **vggt-roi-confidence-diagnostic-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45e_vggt_roi_probe_gate.py`. One bounded A100 job ran official VGGT on BMW anchor 0 raw 7-camera input and captured real non-uniform `depth_conf` / `world_points_conf` maps. Because the current evidence pack exposes camera-owner summaries rather than pixel-exact raw-camera target-surface mapping, DB25 and DB41 remain `RED/abstain`, DB41 lower-right preserves zero-LiDAR abstain, DB36/DB40 generated fake-geometry controls remain non-admissible rejects, and no DB45 permission state changed.

DB45f VGGT target-ROI owner-UV sampling gate is completed and accepted as **vggt-target-uv-sampling-diagnostic-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45f_vggt_target_uv_sampling_gate.py`. The saved A100 inference result was recovered without rerunning VGGT. DB45f samples official VGGT maps at source-owner raw-camera UV pixels used by the frozen ERP seam ROIs and records official preprocessing mapping, but it remains model-diagnostic metadata only: no geometry evidence accepted, no repaired panorama, no source replacement, no RED promotion. It kills VGGT confidence-only RED promotion. DB25/DB41 remain `RED/abstain`, DB41 lower-right remains zero-LiDAR abstain, and DB36/DB40 remain generated fake-geometry rejects.

DB45 is **not closed** by these readiness/diagnostic passes. The next DB45 step, if continuing the source-faithful EGSR mainline, must open a fresh bounded question and bring true target-surface evidence beyond model confidence: track consistency, calibrated pointmap residuals against LiDAR/raw evidence, or another signal that can be compared directly against the same RED controls. Owner-camera confidence and target-ROI owner-UV model confidence are both now known insufficient for promotion. DB46/DB48 remain side branches for presentation-only or center-preserve experiments and should not jump ahead unless the user explicitly switches priority for meeting/demo needs.

---

## 0.2 Strategic Framing: Data Product / Data Contract

The project should not be framed only as "make one ERP seam look smooth." The Bosch/world-model framing is a provenance-labeled, evidence-budgeted, multi-center panorama data product.

This means every output should explain:

- which raw camera owns each source-derived pixel;
- which pixels are generated or presentation-only;
- which pixels are unknown, abstained, or should be downweighted/excluded;
- where seam, parallax, curb, object, or out-of-FOV risk is concentrated;
- why a candidate was accepted, rejected, or caveated.

The practical data contract should include:

- `source_id_map`: raw camera ownership per pixel;
- `generated_mask`: DiT/Cube/outpaint/generated pixels, not sensor observations;
- `unknown_or_abstain_mask`: unsupported or intentionally unfilled regions;
- `risk_map`: seam, near-ground, curb, object, parallax, low-evidence risk;
- `eval_report`: reason-coded accept/reject/caveat record, not just one score;
- `presentation_flag`: whether an output is meeting/demo-only rather than training-data/source-faithful.

This data-contract framing does not replace seam repair. It prevents seam repair from being accepted for the wrong reason.

---

## 1. Where EGSR Comes From

EGSR is not copied from one top-tier paper. It is an engineering synthesis from three sources.

### 1.1 Project-derived evidence

The strongest source is the project history itself:

- L1 hard-select and source-faithful seamroute show that single-source decisions are safer than averaging misaligned views.
- E1.5/risk-gated local color repair shows low-frequency photometric seams can be polished, but geometry seams remain.
- BEV ground atlas shows planar road can be improved when the surface model is valid, while curb/off-plane ground remains a physical floor.
- DB25/DB41 evidence packs show that some seam regions have insufficient LiDAR/flow/raw support and should abstain rather than be hallucinated.
- DB36/DB39/DB40 show that object-gate-passing DiT outputs can still be visually fake due to pole/slice/slab/fake-ground artifacts.

### 1.2 Borrowed method ideas

These papers/systems contribute modules, not a full solution:

- LPAM, ICCV 2025: local patch alignment before seam cutting for large-parallax image stitching. Borrowed idea: treat low-quality seam pixels as local patch-alignment problems before recutting the seam.
- Pano360, CVPR 2026/arXiv: perspective-to-panorama with geometric consistency, camera-pose guidance, and multi-feature seam optimization. Borrowed idea: move beyond pairwise 2D stitching into 3D-aware, pose-guided seam reasoning.
- VGGT, Fast3R, CUT3R, CVPR 2025 family: dense geometry/pointmap/track/confidence evidence. Borrowed idea: use foundation geometry as an evidence source, not as unquestioned truth.
- Depth Any Camera / Depth Any Panoramas / PriOr-Flow: depth and panoramic flow can provide risk/confidence metadata. Borrowed idea: use depth/flow to gate repair permission.
- MEt3R, CVPR 2025: generated views need multi-view consistency evaluation. Borrowed idea: generated presentation outputs need stronger consistency gates than visual realism alone.
- DiT360 / cubemap-style generation tools: useful for sky/out-of-FOV and presentation-only cleanup, not source-faithful AV seam reconstruction.

### 1.3 Synthesis from this discussion and GPT Pro

The taxonomy, GREEN/YELLOW/RED permission state, operator dispatch table, and strict split between source-faithful and presentation-only branches are our synthesis. They are not directly from one paper.

Latest GPT Pro review agrees with the EGSR direction but changes the first-wave ordering: LPAM should not be opened as an independent early brief. It should be a sub-operator inside layer-aware routing and only run on evidence-GREEN segments. The first-wave seam stack should therefore be:

1. Source-Faithfulness Eval v2 / Fake-Geometry Gate.
2. Layer-aware seam routing / EGSR dispatcher.
3. Geometry foundation evidence audit.

---

## 2. Main Route: EGSR

**Name:** EGSR, Evidence-Gated Segment Repair.

**Core principle:** Optimize correct dispatch, not maximum visual smoothness.

The method should be able to say:

- This far-wall color seam can be polished.
- This static structure seam can try local alignment.
- This planar road seam can try BEV/ground reprojection.
- This vehicle seam must be single-source.
- This curb/lane seam has no evidence and must abstain.
- This sky hole can be generated, but only as generated/presentation output.

### 2.1 Expected outputs

Every EGSR run should output more than one image:

- `source_faithful_erp`
- `presentation_erp` if any generated or cosmetic branch is used
- `segment_map`
- `operator_map`
- `source_id_map`
- `risk_map`
- `generated_mask`
- `unknown_or_abstain_mask`
- `segment_report`
- `eval_report`

### 2.2 Segment taxonomy

| ID | Segment type | Default claim |
| --- | --- | --- |
| T0 | Low-risk source boundary | keep/reroute source |
| T1 | Photometric-only seam | low-frequency polish |
| T2 | Far/static textured structure | graph-cut or LPAM-style local alignment if evidence passes |
| T3 | Planar road/asphalt seam | BEV road atlas or planar source reprojection if valid |
| T4 | Lane marking / road-line seam | source-only repair only with strong line evidence |
| T5 | Curb / sidewalk edge / off-plane ground | usually abstain or source selection |
| T6 | Object-interior seam | single-source object ownership |
| T7 | Object-adjacent occlusion seam | choose one source, avoid blending |
| T8 | Out-of-FOV sky / upper black band | generated/presentation with mask |
| T9 | Out-of-FOV ground / lower black band | abstain for source-faithful branch |
| T10 | No-evidence / low-support seam | abstain for source-faithful branch |
| T11 | Temporal/sensor/exposure artifact | sensor/color correction or reject frame |
| T12 | Bad source/frame candidate | source/frame selection, not local repair |

### 2.3 Evidence states

| State | Meaning | Allowed source-faithful action |
| --- | --- | --- |
| GREEN | raw/depth/flow/semantic evidence supports repair | source reroute, local alignment, BEV/geometry reprojection |
| YELLOW | weak geometry but low semantic risk | low-frequency polish, source-only choice, cautious diagnostic |
| RED | no evidence, protected structure, high parallax, or contradiction | abstain; presentation-only branch must be separately labeled |

### 2.4 Operator library

| Operator | Role | Allowed branch |
| --- | --- | --- |
| O0 Keep/abstain | preserve current source and mark risk | source-faithful |
| O1 Source-only hard select | choose one source, no averaging | source-faithful |
| O2 Graph-cut / seam routing | route seam through low-risk source boundary | source-faithful |
| O3 Low-frequency photometric polish | change only color/luminance, no geometry | source-faithful if bounded |
| O4 BEV road atlas / planar road layer | road source reprojection on valid plane | source-faithful if no bleed |
| O5 LPAM-style local patch alignment | align local co-visible patches then recut | source-derived but must be gated |
| O6 Depth/LiDAR/geometry-foundation evidence | permit or reject repair; maybe reproject verified source pixels | evidence/source-faithful only if verified |
| O7 Object-aware seam ownership | force object to one source, moat around object | source-faithful |
| O8 Constrained diffusion cleanup | visual cleanup in allowed masks | presentation-only unless proven otherwise |
| O9 Sky-only diffusion | fill sky/out-of-FOV sky | presentation-only/generated |
| O10 Source/frame selection | choose better source base | source-faithful if selection is valid |

---

## 2.5 Source-Faithfulness Eval v2 / Fake-Geometry Gate

Before adding new repair methods, the plan needs a better gate. Current object gates are not sufficient: DB23, DB36, and DB40 show detector-clean outputs can still contain fake road, curb, lane, slab, hole, or pole-like seam geometry.

This gate should be treated as the first enabling component for EGSR. It should not generate or repair images. It should run on known project positives and negatives and produce reason-coded labels.

Minimum known-case requirements:

- reject DB23-style fake bottom road/lane/curb even if object count is clean;
- reject DB36 fake pale slabs/black holes in the user-marked seam;
- reject DB40 pole-like vertical artifact despite object-gate PASS;
- label DB32 `s40` as acceptable Bosch handoff candidate with generated-sky/source-sidestep caveats, not fully source-faithful;
- label DB41 lower-right as no-evidence/abstain;
- output reject reasons such as `fake_road`, `fake_curb`, `fake_lane`, `object_shape_changed`, `vertical_slice`, `no_source_evidence`, `generated_region`, `source_sidestep`.

This gate is the reason DB-43 below is stronger than a pure taxonomy dry run.

---

## 3. BMW Classic Seam Mapping

BMW remains a hard validation case, not the whole method.

| Region | Current classification | Main action |
| --- | --- | --- |
| Upper sky / top out-of-FOV | T8 | sky-only generation and harmonization; generated mask required |
| Long wall/source-boundary seam | T1/T2 candidate | first triage, then photometric polish or local patch alignment if evidence allows |
| Right BMW / object-adjacent seam | T6/T7 | single-source object ownership and keepout; no diffusion through object |
| Right white-line / curb / lower-right road | T4/T5/T10 under current DB41 evidence | source-faithful abstain unless new evidence appears |
| Lower out-of-FOV ground band | T9/T10 | source-faithful abstain; presentation-only fill only if explicitly labeled |

This means a visually cleaner BMW meeting output may exist in a presentation branch, but it cannot be merged with Bosch/source-faithful claims.

### 3.1 Classic BMW base policy

Do not treat `G_bmw_pano` as the default base just because it is the classic reference. The project has visually rejected it as a final candidate.

Current base roles:

- `G_bmw_pano`: diagnostic failure/reference for why the classic seam is hard; not the default repair base.
- `DB19` sky-only G variant: possible classic-line presentation reference if sky helps and ground seam is explicitly caveated.
- `A1_view_none` / A1 keepout variants: possible diagnostic or presentation base only if BMW ghost/slice is controlled; still not accepted as long-seam repair.
- `BEST_bmw_pano`: negative/donor diagnostic unless same-ROI review proves it beats G without ghosting.
- `DB32 s40` / `a200`: separate Bosch source-sidestep handoff track, not original-G repair.

If a presentation-only classic BMW attempt is opened, base selection must be a bounded decision step using existing same-ROI boards before any generation. It must not silently start from `G_bmw_pano`.

---

## 4. Planned Decision Brief Sequence

These are roadmap items, not direct execution commands. DB43 and DB44 are completed and accepted; DB45 is the next recommended source-faithful EGSR brief. Any remaining item must become or remain a live decision brief before work starts.

### DB-43: Source-Faithfulness Eval v2 / Fake-Geometry Gate + EGSR triage (completed / accepted 2026-06-04)

**Status:** completed and archived in `agent/progress.md`; do not rerun or reopen this direction unless a new brief changes the evidence set or gate requirements.

**Question:** Can the project build a reason-coded gate that rejects smooth-but-fake seam outputs and assigns existing seam segments to source-faithful, presentation-only, or abstain states?

**Scope:** no A100, no model inference, no new panorama generation. Use DB23/DB32/DB35-42 boards, manifests, and existing crops only. Fixed known-case set first; do not expand into dataset scanning.

**Expected output:** one eval/triage board and one manifest showing fake-geometry verdicts, segment class, evidence state, chosen operator, claim level, and reject/caveat reason codes.

**Kill criteria:**

- gate passes DB23/DB36/DB40-style fake road/curb/lane/slab/pole artifacts;
- DB32 is mislabeled as fully source-faithful instead of source-sidestep with generated-sky caveat;
- taxonomy cannot separate long seam, BMW object seam, right-line/curb, sky, and no-evidence areas;
- DB41 lower-right is mislabeled as source-faithful repairable;
- the next recommended action collapses back to prompt-only DiT ground seam;
- source-faithful and presentation-only branches are not separated.

**Max scope:** 20-30 known cases/ROIs, all from existing artifacts. This is a gate and triage brief, not a repair brief.

### DB-44: Layer-aware seam routing / EGSR dispatcher v0

**Status:** completed and archived in `agent/progress.md`; do not rerun or extend this direction unless a new brief opens a source-faithful operator implementation or an evidence-state update.

**Question:** Can the project turn seam taxonomy into a reusable source-faithful dispatcher where road, curb, object, lane, sky, and unknown regions receive different actions?

**Candidate operators:** keep/abstain, source-only hard select, object ownership, graph-cut reroute, BEV road, low-frequency polish, and LPAM-style local alignment only for evidence-GREEN far/static segments.

**Scope:** fixed 8-12 panoramas or 20-30 seam components. Output layer map, evidence state, operator map, and source-faithful output. No diffusion in the training/source-faithful branch. LPAM is a sub-operator, not a standalone first-wave route.

**Kill criteria:**

- behaves like prior depth-aware DP/superpixel routing and creates blocky source swaps;
- cannot classify planar road versus curb/off-plane ground;
- cuts protected object, lane, or curb structures;
- fails to abstain on DB41 lower-right/right-line;
- no visible improvement on easy far/static or planar GREEN/YELLOW segments;
- source NCC/raw consistency collapses;
- LPAM/local alignment bends object, curb, or lane topology;
- method improves BMW only and over-edits cleaner source candidates.

### DB-45: Geometry foundation evidence audit

**Question:** Can VGGT/Fast3R/CUT3R/DAC/DAP/PriOr-Flow-style evidence turn any currently RED seam into YELLOW/GREEN?

**Scope:** evidence-only first. No repair until confidence is calibrated against raw/LiDAR/project evidence.

**Completed DB-45 substeps:**

- DB45a VGGT feasibility gate: current-runtime no-go, not a VGGT model negative.
- DB45b existing-evidence permission calibration: accepted permission-calibration-only guardrails, no RED promotion.
- DB45c VGGT Commercial access update + schema gate: HF file access cleared, but VGGT route remains not evidence-ready.
- DB45d VGGT official setup/load smoke: setup/checkpoint/API ready for a future ROI probe, but no geometry evidence accepted.
- DB45e VGGT frozen-ROI confidence probe: accepted diagnostic owner-camera confidence only; no target-surface mapping, no geometry evidence, no RED promotion.
- DB45f VGGT target-ROI owner-UV sampling gate: accepted diagnostic-only target-pixel VGGT metadata; confidence-only RED promotion killed; no geometry evidence, no RED promotion.

**Parked future subtracks under DB-45 unless split into separate briefs:**

- Geometry foundation evidence job: VGGT / Fast3R / CUT3R-style pointmaps, tracks, confidence, and multi-view consistency as evidence only.
- Depth risk upgrade: DAC / DAP versus current DA-V2-style depth metadata, especially ERP/large-FoV depth confidence.
- Flow confidence audit: PriOr-Flow / FlowSeek-style confidence, occlusion, and forward-backward reliability; no blind flow warp.
- Waymo sensor artifact taxonomy: SplatAD / SplatFlow / StreetGaussians-style diagnostics for HDR/color, rolling shutter/sync, dynamic object, and parallax categories; not final panorama rendering.

**Minimum decisive experiment:** 8 fixed seam segments: positives with known raw/LiDAR/flow support, and negatives including DB41 lower-right, DB25 dark-wall/key-pair low-flow seam, DB36/DB40 generated fake geometry, and object-adjacent occlusion.

**Kill criteria:**

- high confidence on DB41 lower-right/no-evidence ROI;
- high confidence on DB36/DB40 generated fake slabs or pole-like artifacts;
- model confidence disagrees with raw-camera/LiDAR evidence;
- inferred geometry fills unseen regions and is treated as truth;
- no actionable change to permission states.

### DB-46: BMW meeting presentation-only micro cleanup

**Question:** Can a separately labeled presentation branch make the classic BMW seam look cleaner for discussion without claiming source-faithful repair?

**Scope:** first choose the base from existing same-ROI boards. Then rectilinear/cubemap local cleanup only; very small masks; generated/edit mask required.

**Kill criteria:**

- attempt silently uses `G_bmw_pano` as the base without a base-selection decision;
- any new object, fake lane, fake curb, fake road topology, pole-like slice, or BMW shape change;
- output is later confused with Bosch training-data output;
- improvement is only visible in full ERP but fails rectilinear crop review.

### DB-47: Source/frame/dataset-level candidate mining

**Question:** Is the right solution to avoid hard seams by choosing better frames/sources/logs rather than locally repairing them?

**Scope:** bounded candidate scan with fixed metrics and fixed visual review. Do not choose only the prettiest top-10 cases; report total scanned, strict accepted, relaxed accepted, rejected-by-reason, abstain-mask distribution, scene distribution, and failure boards.

**Kill criteria:**

- scan becomes unbounded;
- selected source is a distribution/cherry-pick artifact;
- new candidate moves the seam defect rather than reducing it.

### DB-48: Koi center-preserve DiT360 outpainting side branch

**Question:** Does official-style center-preserve outpainting become more coherent with stricter preserve ratio, tau, and scene prompt?

**Scope:** presentation/demo branch only; max 4-6 cases; not seam-source repair.

**Kill criteria:**

- invented salient vehicles/people/signs dominate;
- preserved center is visibly boxed or lighting-mismatched;
- branch starts being interpreted as Bosch source-faithful data.

### DB-49: Bosch-facing data contract / handoff packet

**Question:** How should the final Bosch-facing output expose caveats, generated regions, abstain masks, and current-best image selection?

**Scope:** packaging/reporting only after candidate outputs exist.

**Required language:** DB32 `s40` is the current defensible handoff candidate; it avoids the worst seam through source-sidestep and sky completion, but is not an original-G seam repair. Ground/object/lane/curb generation is not training data. No-evidence ROI is abstained. Generated sky/out-of-FOV is explicitly masked.

**Required caveats:** output is a multi-center source mosaic, not a physically single-center capture; generated sky/out-of-FOV is not sensor evidence; one sample does not prove Waymo-wide generality; any commercial/Bosch use must check generation-model license; downstream world-model impact requires Bosch's own protocol.

**Kill criteria:**

- report hides generated/unknown regions;
- claim language overstates seam repair;
- DB32/source-sidestep and original-G seam repair are mixed together.

---

## 5. Backup Routes if EGSR Fails

EGSR can fail in multiple ways. The response depends on the failure mode.

| Failure mode | Next route |
| --- | --- |
| Taxonomy is unstable | build a smaller manual segment vocabulary and keep it as a review tool |
| Evidence states are wrong | improve evidence calibration before any repair |
| Source-safe operators do not improve | pivot to source/frame selection and honest abstain masks |
| BMW right-line remains RED | do not keep local ground repair; require new raw/depth/temporal evidence |
| LPAM/local alignment bends structures | restrict LPAM to far/static T2 only |
| Geometry foundation models hallucinate unseen surfaces | keep them as risk metadata only |
| Presentation branch looks better but untrustworthy | keep presentation branch separate from Bosch/data output |
| Dataset-level candidate mining finds cleaner frames | use source-sidestep as primary route, like DB32/a200 |
| All seam repair routes fail | ship current best with explicit risk/unknown masks and Bosch caveats |

---

## 6. Routes Not to Reopen Blindly

Do not spend more work on these without genuinely new evidence or a new decision brief:

- prompt-only DiT/FLUX ground, curb, or lane seam repair;
- G/A1/BEST donor patching;
- repeating the v14 trimap-clamp matrix;
- right-white-line micro repair under current DB41 evidence;
- full-ground outpainting as Bosch data;
- DP/source selection that reduces seam color metrics by causing blocky source swaps;
- object-gate-only acceptance of generated outputs.

---

## 7. Goal-Mode Start Checklist

Before starting a goal-mode exploration:

- Choose exactly one brief target, likely DB-43 first.
- Write the decision brief in `agent/decision_briefs.md`.
- Include kill criteria, max scope, and required vision check.
- Confirm whether the branch is source-faithful, presentation-only, or evidence-only.
- Do not run A100 or model inference until the brief permits it.
- Preserve DB42 language: DB32 `s40` is the current Bosch handoff candidate; original G/A1/BEST seam is not fixed.

---

## 8. Source Links Used for This Plan

- LPAM, ICCV 2025: https://openaccess.thecvf.com/content/ICCV2025/papers/Liao_Leveraging_Local_Patch_Alignment_to_Seam-cutting_for_Large_Parallax_Image_ICCV_2025_paper.pdf
- Pano360, CVPR 2026/arXiv: https://arxiv.org/abs/2603.12013
- VGGT, CVPR 2025: https://arxiv.org/abs/2503.11651
- Fast3R, CVPR 2025: https://openaccess.thecvf.com/content/CVPR2025/html/Yang_Fast3R_Towards_3D_Reconstruction_of_1000_Images_in_One_Forward_CVPR_2025_paper.html
- CUT3R, CVPR 2025: https://cut3r.github.io/
- Depth Any Camera, CVPR 2025: https://arxiv.org/abs/2501.02464
- Depth Any Panoramas, CVPR 2026: https://insta360-research-team.github.io/DAP_website/
- PriOr-Flow, ICCV 2025: https://openaccess.thecvf.com/content/ICCV2025/html/Liu_PriOr-Flow_Enhancing_Primitive_Panoramic_Optical_Flow_with_Orthogonal_View_ICCV_2025_paper.html
- FlowSeek, ICCV 2025: https://openaccess.thecvf.com/content/ICCV2025/html/Poggi_FlowSeek_Optical_Flow_Made_Easier_with_Depth_Foundation_Models_and_ICCV_2025_paper.html
- MEt3R, CVPR 2025: https://openaccess.thecvf.com/content/CVPR2025/html/Asim_MET3R_Measuring_Multi-View_Consistency_in_Generated_Images_CVPR_2025_paper.html
- SplatAD, CVPR 2025: https://openaccess.thecvf.com/content/CVPR2025/papers/Hess_SplatAD_Real-Time_Lidar_and_Camera_Rendering_with_3D_Gaussian_Splatting_CVPR_2025_paper.pdf
- Street Gaussians, ECCV 2024: https://www.ecva.net/papers/eccv_2024/papers_ECCV/html/9243_ECCV_2024_paper.php
- DiT360 official repository: https://github.com/Insta360-Research-Team/DiT360
