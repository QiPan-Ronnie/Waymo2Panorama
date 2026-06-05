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

DB45 is now paused after DB45j produced VGGT calibrated residual diagnostic-only evidence, with no geometry promotion. Its phase0 evidence-only control/registry pass is completed: outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45_geometry_evidence_audit.py`. DB45 v0 fixed 8 controls, registered reusable evidence sources, verified A100 live/env/cache state only, and kept all permission states unchanged (`GREEN=1`, `YELLOW=2`, `RED=5`; no RED promotion; no foundation-model confidence claimed).

DB45a VGGT evidence feasibility gate is completed as a current-runtime **no-go**. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45a_vggt_feasibility_gate.py`. This is not a VGGT model negative: it records that the current Colab runtime has A100/data/repo access and the user-provided HF token is valid, but the remote repo is stale, `vggt` is not importable, the cached VGGT repo tarball is invalid/0-byte, HF Commercial checkpoint file access is still gated/403, no checkpoint cache is present, and the existing VGGT wrapper uses uniform confidence. No install/download/inference was run, no DB45 evidence was accepted, and no permission state changed.

DB45b Existing-evidence permission calibration is completed and accepted as **permission-calibration-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45b_evidence_permission_calibrator.py`. DB45b uses only existing DB25/DB41/DB36/DB40/DB45/depth/parallax artifacts, no A100/model/repair. It keeps all 8 frozen controls unchanged (`permission_state_changes=none`, `red_promotions=[]`) and formalizes the next hard EGSR rules: target-surface support is required; flow-only, detector-clean, case-level depth/parallax, outside-mask preservation, generated-core confidence, and best-pair laundering cannot promote RED. DB41 lower-right/right-line remains no-evidence/abstain; DB36/DB40 remain fake-geometry rejects; DB32 remains caveated handoff/source-sidestep.

DB45c VGGT Commercial access update + schema gate is completed and accepted as **readiness-and-schema-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45c_vggt_access_schema_gate.py`. HF Commercial file access is now cleared (`config.json` HEAD 403 -> 200), but this is not accepted VGGT geometry evidence. Current blockers remain: remote repo stale at `d544214`, `vggt` import missing, VGGT repo cache tarball 0 bytes, no verified checkpoint cache, and existing VGGT wrapper emits uniform `np.ones` confidence. No install/download/inference/repair was run, no DB45 permission state changed, and no RED control was promoted.

DB45d VGGT official setup/load smoke is completed and accepted as **setup-and-api-smoke-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45d_vggt_setup_smoke_gate.py`. One bounded A100 job cloned official VGGT, loaded `facebook/VGGT-1B-Commercial`, cached the checkpoint on Drive, and verified confidence-capable API fields. No AV image inference, renderer, repair, source replacement, or RED promotion was run. This clears the setup/checkpoint/API blocker for a future ROI probe, but still does not create accepted VGGT geometry evidence.

DB45e VGGT frozen-ROI confidence probe is completed and accepted as **vggt-roi-confidence-diagnostic-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45e_vggt_roi_probe_gate.py`. One bounded A100 job ran official VGGT on BMW anchor 0 raw 7-camera input and captured real non-uniform `depth_conf` / `world_points_conf` maps. Because the current evidence pack exposes camera-owner summaries rather than pixel-exact raw-camera target-surface mapping, DB25 and DB41 remain `RED/abstain`, DB41 lower-right preserves zero-LiDAR abstain, DB36/DB40 generated fake-geometry controls remain non-admissible rejects, and no DB45 permission state changed.

DB45f VGGT target-ROI owner-UV sampling gate is completed and accepted as **vggt-target-uv-sampling-diagnostic-only** evidence. Outputs are under `deliverables/dit360_v2/db45_geometry_evidence_audit/`, with script `scripts/phase3/db45f_vggt_target_uv_sampling_gate.py`. The saved A100 inference result was recovered without rerunning VGGT. DB45f samples official VGGT maps at source-owner raw-camera UV pixels used by the frozen ERP seam ROIs and records official preprocessing mapping, but it remains model-diagnostic metadata only: no geometry evidence accepted, no repaired panorama, no source replacement, no RED promotion. It kills VGGT confidence-only RED promotion. DB25/DB41 remain `RED/abstain`, DB41 lower-right remains zero-LiDAR abstain, and DB36/DB40 remain generated fake-geometry rejects.

DB45 is **paused** after DB45k accepted `vggt-pose-reflection-coordinate-audit-diagnostic-only` evidence. DB45g is completed as **vggt-official-source-decode-path-diagnostic-only**, DB45h as **vggt-residual-job-contract-only**, and DB45j as **vggt-calibrated-residual-diagnostic-only**. DB45j restored official VGGT setup/checkpoint readiness, then DB45i ran one official inference and saved `pose_enc`, decoded cameras, preprocessing mapping, Sim(3), and DB25/DB41 residual tables. DB45k audited those saved outputs only: official camera-from-world center extraction still fails the no-reflection contract, reflected fits are non-admissible, translation-column improvement is only an undocumented convention-conflict diagnostic, official-center pairwise rig-shape error remains material, ROI raw/LiDAR residuals remain large, and every ROI keeps `permission_promotion_allowed=false`; DB41 lower-right remains known LiDAR support `0.000`. Owner-camera confidence, target-ROI owner-UV model confidence, calibrated residual diagnostics, and DB45k coordinate diagnostics are all insufficient for RED promotion under current gates. Do not spend another A100/inference pass on VGGT residuals unless a fresh brief brings new official-source convention evidence and preserves DB25/DB41 no-promotion boundaries. DB47 is now running as the source/frame candidate-mining sidestep route. DB46/DB48 remain side branches for presentation-only or center-preserve experiments and should not jump ahead unless the user explicitly switches priority for meeting/demo needs.

DB47b is completed and accepted as **source-selection-threshold-replay-only** evidence. It freezes the existing DB31 22-row shortlist as the bounded candidate universe, with DB27/DB28 only as same-log comparison context, and reports 7 strict review-bucket rows, 3 relaxed review-bucket rows, and 12 rejected/diagnostic rows. DB47c is completed and accepted as **source-selection-visual-accounting-only** evidence. It reviews all 22 DB47b rows with existing DB28/DB31 visual assets only: 3 strict rows have exact same-log review assets, 4 strict rows are montage-only holds, 3 relaxed rows are holds, and 12 rows remain rejected/diagnostic. DB47d is completed and accepted as **source-selection-exact-review-pack-only** evidence. It reviews the 10 strict/relaxed same-log rows with DB47c plus existing DB28 exact assets only: 3 exact-review candidates (`a105`, `a200`, `a204`) remain not-final, and 7 rows remain missing-exact holds. DB47e is completed and accepted as **source-selection-final-candidate-review-existing-artifacts-only** evidence. It reviews only `a105`, `a200`, and `a204`; confirms `a200` as the current source-sidestep base for the existing DB32 `s40` Bosch-facing handoff candidate; keeps `a204` as an exact final-eligible alternate not selected; keeps `a105` compare-only hold; and preserves the 7 missing-exact holds. These are source-selection/review/accounting gates only: DB47b/c/d/e perform no new dataset scan, exact asset fetch, generation, repair, source replacement, `source_id_map`, permission change, or RED promotion, and do not claim original-G or source-faithful seam repair. DB47 should now pause unless a fresh brief opens a fixed-universe full scan; if the priority is Bosch packaging, move to DB49 exact-lineage source/provenance rerun.

DB49a Bosch data-contract inventory is completed and accepted as **bosch-data-contract-inventory-only** evidence. Outputs are under `deliverables/dit360_v2/db49_bosch_data_contract/`, with script `scripts/phase3/db49a_bosch_data_contract_inventory.py`. DB49a reads existing DB32/DB34/DB38/DB41/DB42/DB43/DB45i/DB47d artifacts only and creates no candidate image, repair, generated mask, abstain mask, risk map, dataset scan, model run, permission change, or RED promotion. It confirms DB32 `s40` is only a caveated Bosch-facing handoff candidate and that uncaveated Bosch training-data use is blocked until at least per-pixel `source_id_map`, `unknown_or_abstain_mask`, `risk_map`, and generation-model/license review are packaged. `generated_mask` is only partial via the existing sky-core mask/overlay; DB47d remains not-final and DB41 lower-right/right-line remains no-evidence/abstain.

DB49b sidecar starter pack is completed and accepted as **sidecar-starter-pack-partial-only** evidence. Outputs are under `deliverables/dit360_v2/db49_bosch_data_contract/`, with script `scripts/phase3/db49b_sidecar_starter_pack.py`. DB49b creates only three partial DB32 sidecars from existing evidence: sky-core `generated_mask`, out-of-FOV-plus-DB41 `unknown_or_abstain_mask`, and a partial contract `risk_map`. It keeps `source_id_map_created=false`, candidate pixels unchanged, `ready_for_uncaveated_bosch_training_data=false`, DB41 right/lower-right abstain, and no repair/generation/model/executor/network/permission change/RED promotion. Future DB49 work must not treat these partial sidecars as a complete Bosch data contract.

DB49c `source_id_map` feasibility and ownership-evidence inventory is completed and accepted as **source-id-map-feasibility-inventory-only** evidence. Outputs are under `deliverables/dit360_v2/db49_bosch_data_contract/`, with script `scripts/phase3/db49c_source_id_map_feasibility.py`. DB49c finds no complete per-pixel `source_id_map` artifact for the exact DB32 lineage and creates no map. DB28 and DB41 camera labels remain ROI-level diagnostic/count evidence only; DB34 source preservation is not an owner map; DB49b sidecars are not owner maps; `_seamroute.py`'s internal routed label remains only future reproducible support unless an exact lineage rerun saves and validates an owner artifact. `source_id_map_status=missing_blocking_not_fabricated`, `ready_for_uncaveated_bosch_training_data=false`, and no repair/generation/model/executor/network/permission change/RED promotion occurred.

DB49d seamroute source/provenance sidecar instrumentation is completed and accepted as **source-map-instrumentation-only** evidence. Outputs are under `deliverables/dit360_v2/db49_bosch_data_contract/`, with script `scripts/phase3/db49d_seamroute_source_map_instrumentation.py` and a default-off patch to `scripts/phase3/_seamroute.py`. DB49d adds optional future rerun sidecars (`routed_source_id_map`, `valid_mask`, `virtual_center_effect_mask`, `ground_reproject_effect_mask`, `final_source_state_map`, `source_id_overlay`, `source_id_sidecar_legend`) without running seamroute, creating a DB32 map, modifying candidate pixels, or changing default outputs. Virtual-centre composited/effect pixels are explicitly marked non-single-source (`250`) and invalid/out-of-FOV remains `255`. `source_id_map_status=missing_until_exact_seamroute_rerun_not_fabricated`, `ready_for_uncaveated_bosch_training_data=false`, and no repair/generation/model/executor/network/permission change/RED promotion occurred.

DB49e exact-lineage source/provenance preflight is completed as **exact-lineage-source-map-rerun-preflight-only** evidence and is paused before any rerun. Outputs are under `deliverables/dit360_v2/db49_bosch_data_contract/`, with script `scripts/phase3/db49e_exact_lineage_preflight.py`. DB49e confirms the a200/DB32 lineage and DB49d sidecar support, but it creates no `source_id_map` because the local target log is absent and no secure runtime secret source is available (`COLAB_URL`/`COLAB_TOKEN` env vars or non-repo runtime secret file). It runs no seamroute dataset/render/model/executor/network, modifies no candidate pixels, and makes no repair/source-faithful/training-ready claim. DB49e may continue only with one exact rerun after secure runtime/data preconditions are satisfied; otherwise it should remain paused or yield to seam-quality work under a fresh brief.

DB50 EGSR source-faithful operator Phase0 is completed as **egsr-operator-readiness-existing-artifacts-only** evidence. Outputs are under `deliverables/dit360_v2/db50_egsr_operator_v0/`, with script `scripts/phase3/db50_egsr_operator_readiness.py`. DB50 reviewed all 29 DB44 components and found no executable new source-faithful repair target from current local artifacts: `phase0_executable_repair_targets=0`, `lpam_executable_targets=0`, `red_promotions=0`, and `unsafe_db32_source_faithful_claims=0`. Counts are 3 presentation-only, 1 already-satisfied keep/source-preservation control, 2 source-sidestep-only, 1 existing BEV caveated source-faithful control, and 22 abstain/reject. DB50 creates no repaired ERP, runs no dataset/render/model/executor/network, and makes no new source-faithful/original-G claim. Any real operator implementation now needs a fresh target-specific DB50 sub-brief with raw/source-pair evidence, protected-structure checks, maps, and same-ROI before/after vision.

DB51 EGSR target/source-pair acquisition queue is completed as **egsr-target-source-pair-acquisition-queue-only** evidence. Outputs are under `deliverables/dit360_v2/db51_egsr_target_acquisition/`, with script `scripts/phase3/db51_egsr_target_acquisition_queue.py`. DB51 creates no repaired ERP and runs no dataset/render/model/executor/network/exact asset fetch. It ranks the next route as DB47f fixed-universe exact source-selection closure if secure runtime/data preconditions are satisfied; otherwise DB50 operator implementation remains paused. DB51 also records that DB50b LPAM/local alignment still needs a fixed raw/source-pair target plus protected-structure checks, DB49e remains provenance/data-contract rather than seam-quality, DB45 geometry evidence should be fixed-target only, and DB46/DB48 remain presentation-only. DB47 has 8 exact/final gaps: seven missing exact holds plus `a105` final missing. DB25/DB41 remain acquisition blockers, not repair permissions.

DB47f fixed-universe exact source-selection closure preflight is completed as **fixed-universe-exact-closure-preflight-only** evidence and paused. Outputs are under `deliverables/dit360_v2/db47_source_candidate_mining/`, with script `scripts/phase3/db47f_fixed_universe_exact_closure_preflight.py`. DB47f locks the closure universe to exactly 8 DB51 gaps (`a201`, `a209`, `a210`, `a211`, `a031`, `a038`, `a040` compare+final and `a105` final), confirms all 8 are still unresolved locally, and stops because local target data is absent and no secure runtime secret source is available in-process. It runs no exact asset fetch/rerun, dataset scan, seamroute/renderer, model, executor/A100, repair, generation, source replacement, `source_id_map`, permission change, or RED promotion. DB47 remains paused until secure runtime/data preconditions are satisfied for one bounded 8-anchor exact closure batch.

DB52 DB47f secure-runtime/data intake contract is completed as **secure-runtime-contract-only** evidence and paused. Outputs are under `deliverables/dit360_v2/db52_secure_runtime_contract/`, with script `scripts/phase3/db52_secure_runtime_contract.py`. DB52 creates a token-safe launch contract for the future DB47f closure batch: approved inputs are only `COLAB_URL`/`COLAB_TOKEN` env vars, a non-repo runtime secret file, or replicated local target data. The current in-process state still has no env runtime pair, no approved non-repo runtime secret file, and no local target log, so `closure_batch_allowed_now=false`. It runs no remote/status/exec/A100/HF/VGGT/model/network/exact fetch/repair/generation/source replacement/`source_id_map`/permission change/RED promotion, and it rejects chat-pasted JSON/token values as command/artifact secrets.

DB53 DB47f token-free launch harness dry-run is completed as **db47f-token-free-launch-harness-dry-run-only** evidence and paused. Outputs are under `deliverables/dit360_v2/db53_db47f_launch_harness/`, with script `scripts/phase3/db53_db47f_launch_harness_dryrun.py`. DB53 adds the deterministic no-secret argv/output mapping for exactly the 8 DB47f anchors and expected compare/final assets, but safe data path remains false and no remote/status/exec/A100/HF/VGGT/model/`_seamroute.py`/exact fetch or copy/repair/generation/source replacement/`source_id_map`/permission change/RED promotion occurred. This is not seam-quality evidence or closure; it is only launch-risk reduction. The next DB47 action should be the actual bounded closure batch after approved env/non-repo runtime secret source or local target data exists, not another infra-only layer.

DB54 DB47f local exact-asset recovery audit is completed as **local-exact-asset-recovery-audit-only** evidence and paused. Outputs are under `deliverables/dit360_v2/db54_local_artifact_recovery/`, with script `scripts/phase3/db54_local_exact_asset_recovery.py`. DB54 scanned bounded local artifact roots and zip member names for the fixed DB47f 15 required compare/final assets and found `0` local file matches and `0` zip-entry-only matches. It ran no remote/status/exec/A100/HF/VGGT/model/dataset scan/`_seamroute.py`/renderer/zip extraction/image copy/exact fetch/repair/generation/source replacement/`source_id_map`/permission change/RED promotion. DB47f gaps are not hidden in current local artifacts; do not repeat local recovery or add more DB47f infra-only layers. The next DB47 action still requires approved env/non-repo runtime secret source or local target data, then the actual bounded closure batch.

DB55 EGSR O3 photometric polish acceptance audit is completed as **egsr-o3-photometric-operator-acceptance-audit-only** evidence. Outputs are under `deliverables/dit360_v2/db55_egsr_o3_photometric_operator/`, with script `scripts/phase3/db55_egsr_o3_photometric_operator_audit.py`. DB55 accepts O3 only as `source-derived bounded photometric polish` for T1/YELLOW-GREEN low-structure photometric seams. It audits existing risk-gated local Y repair evidence only: 14-anchor mean seam dY improvement mean `17.71%`, p95 improvement mean `5.39%` with weak p95 cases disclosed, changed fraction mean `0.034`, and max Y delta `9.10`. It runs no new repair/raw-data load/dataset scan/remote/A100/model/seamroute/renderer/generation/source replacement/`source_id_map`/permission change/RED promotion. O3 is not geometry repair, not DB41/DB25 repair, not original G/A1/BEST repair, and not a DB32 source-faithfulness or Bosch training-ready permission change.

DB56 DB47f exact closure batch execution is opened but paused before remote execution. Script `scripts/phase3/db56_db47f_exact_closure_batch.py` is prepared and passes `py_compile` for exactly one fixed 8-anchor closure batch, but the current process has no approved runtime secret source (`COLAB_URL`/`COLAB_TOKEN` env pair or non-repo runtime secret file). A chat-pasted A100 endpoint/token is still rejected under DB52 as a command/artifact secret. No `/exec`, exact asset fetch, A100 job, model/generation/source replacement, `source_id_map`, permission change, or RED promotion occurred. This is a safe-runtime pause, not a seam/model negative; the next DB47 action remains the same DB56 bounded batch once approved runtime input exists.

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
- `license_generation_caveat`: explicit generation-model/license review before Bosch/commercial/training-data release.

This data-contract framing does not replace seam repair. It prevents seam repair from being accepted for the wrong reason.

DB49a is the current inventory baseline for this section: it reports which fields are available, partial, missing, or manually required. DB49b is the current partial sidecar starter pack: it materializes only the sky-core generated mask, out-of-FOV/DB41 abstain mask, and partial risk map that can be derived from existing evidence. DB49c is the current source-ownership boundary: no complete per-pixel `source_id_map` exists in current artifacts for the exact DB32 lineage, and ROI camera-label diagnostics cannot be promoted into ownership truth. DB49d is only default-off future rerun instrumentation for real source/provenance sidecars; it is not a recovered DB32 owner artifact. DB49e is a preflight pause: it confirms the exact rerun path but does not execute it until secure runtime/data preconditions are available. Future DB49 work must not convert the still-missing `source_id_map` or full sidecars into narrative claims.

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

These are roadmap items, not direct execution commands. DB43 and DB44 are completed and accepted; DB45 is paused after DB45j accepted VGGT residual diagnostic-only evidence with no geometry promotion, and DB47 is the current running source/frame candidate-mining sidestep brief. Any remaining item must become or remain a live decision brief before work starts.

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
- DB45g VGGT pose/pointmap metric-residual readiness gate: accepted official-source decode-path diagnostic-only; runtime source/API inspection still blocked by executor/tunnel availability; DB45f has pose keys but no stored pose tensor/decoded extrinsics; no inference, no geometry evidence, no RED promotion.
- DB45h VGGT calibrated residual job contract gate: accepted residual-job contract-only; future extractor must save pose/decode/preprocess/rig/residual fields and pass Sim(3) rig alignment plus LiDAR/raw target-surface residuals; no inference, no geometry evidence, no RED promotion.
- DB45i/DB45j VGGT calibrated residual extractor: after one setup/load replay, one official VGGT inference succeeded and produced pose/decode/preprocess/Sim(3)/ROI residual diagnostics. Accepted evidence type is `vggt-calibrated-residual-diagnostic-only`; `accepted_db45_geometry_evidence=false`, `permission_state_changes=none`, `red_promotions=[]`. Sim(3) fails the contract due `reflection_detected=true`; ROI raw/LiDAR residuals remain no-promotion; DB41 lower-right remains zero-LiDAR abstain. Do not continue VGGT residual patch-on-patch without a fresh reflection/coordinate evidence brief.

**Parked future subtracks under DB-45 unless split into separate briefs or resumed under DB45i after executor recovery:**

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

**Status update (2026-06-04):** DB47a Phase0 is completed as `source-selection-inventory-only` evidence. CPU/local script `scripts/phase3/db47_source_candidate_inventory.py` inventories existing DB27/DB28/DB31/DB34/DB38/DB42/DB43 artifacts and produces `deliverables/dit360_v2/db47_source_candidate_mining/db47a_source_candidate_inventory_manifest.json` plus board. It reviews 36 existing candidate records, preserves DB28/DB32 as source-sidestep/current-handoff evidence only, and sets the next full-scan contract. No new dataset scan, repair, generation, source replacement, permission change, or RED promotion occurred.

**Phase1 update (2026-06-04):** DB47b is completed as `source-selection-threshold-replay-only` evidence. CPU/local script `scripts/phase3/db47b_candidate_universe_threshold_replay.py` freezes DB31's 22-row shortlist as the fixed universe, uses DB27/DB28 only for comparison, and writes `deliverables/dit360_v2/db47_source_candidate_mining/db47b_candidate_universe_threshold_replay_manifest.json` plus board. Counts are 7 strict review-bucket rows, 3 relaxed review-bucket rows, and 12 rejected/diagnostic rows across 5 logs. The strict/relaxed labels are review queues only, not accepted final panoramas. No full scan, repair, generation, source replacement, permission change, or RED promotion occurred. Next DB47 work must either do a bounded same-ROI visual/accounting review over strict/failure buckets or open a separate fixed-universe full-scan brief.

**Phase2 update (2026-06-04):** DB47c is completed as `source-selection-visual-accounting-only` evidence. CPU/local script `scripts/phase3/db47c_same_roi_bucket_review.py` uses DB47b plus existing DB28/DB31 summaries, montages, and exact assets only, and writes `deliverables/dit360_v2/db47_source_candidate_mining/db47c_same_roi_bucket_review_manifest.json` plus board. It reviews all 22 rows: 3 strict rows have exact same-log review assets, 4 strict rows are montage-only holds, 3 relaxed rows are holds, 2 same-log rows remain weak-margin rejects, 3 non-BMW rows are confirmed existing failures, and 7 non-BMW rows remain no-successor rejects. Exact assets exist for only 6 rows, with 11 unique exact local assets and 16 montage-only rows, so DB47c selects no final candidate. No scan, repair, generation, source replacement, permission change, or RED promotion occurred. Next DB47 work must have a fresh brief and either run exact same-log review or pause DB47.

**Phase3 update (2026-06-04):** DB47d is completed as `source-selection-exact-review-pack-only` evidence. CPU/local script `scripts/phase3/db47d_exact_same_log_review.py` uses DB47c plus existing DB28 strict-clean summary/montage and exact compare/final assets only, and writes `deliverables/dit360_v2/db47_source_candidate_mining/db47d_exact_same_log_review_manifest.json` plus board. It reviews the 10 strict/relaxed same-log rows: 3 rows have exact compare evidence (`a105`, `a200`, `a204`), 2 of those have final images, and 7 rows remain missing-exact holds. DB47d selects no final candidate and accepts no source-faithful repair. No scan, exact asset fetch, repair, generation, source replacement, permission change, or RED promotion occurred. Next DB47 work must have a fresh brief and either run a stricter final-candidate review, open a fixed-universe full scan, or pause DB47 and feed DB49 data-contract packaging.

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

**Scope:** packaging/reporting only after candidate outputs exist. DB49a inventory and DB49b partial sidecar starter pack are complete; any next DB49 step needs a bounded sub-brief and must keep `source_id_map` missing unless real source-ownership evidence is available.

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
