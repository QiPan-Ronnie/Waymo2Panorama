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

# DB-58: VGGT-assisted raw-camera-backed seam ROI repair feasibility
Status: proposed / primary prepared next attempt / not running yet
Route: A (geometry) / target-specific EGSR / raw-camera-backed repair

Question: Can one fixed BMW seam ROI be repaired by using VGGT only as geometry evidence for a raw-camera-backed local warp/composite, without generation, prompt inpainting, source replacement, or overclaiming source faithfulness?

Hypothesis: DB37 showed the Google/Meta-style gap is not the seamline algorithm shape but the missing reliable overlap/depth/visibility evidence. DB45 proved official VGGT can produce diagnostic pose/depth/point outputs but has not yet passed the coordinate/reflection/no-promotion gate. A narrow DB58 pass may still become useful if it treats VGGT as a target-surface evidence gate, combines it with raw camera owner/UV mapping and existing LiDAR/flow checks, and attempts a local source-backed warp/composite only when those gates pass. If VGGT pose/depth cannot align to the Waymo-style rig/LiDAR or the target ROI remains low-evidence, the accepted result must be abstain/no-repair rather than another patch.

Why now: DB57 stopped DB47f patch-on-patch after no candidate displaced `a200`/DB32, DB50 found `0` executable new source-faithful repair targets under current local artifacts, and the user explicitly reframed the remaining seam problem as a Google/Meta-style geometry/overlap deficiency rather than a prompt-tuning problem. The next credible seam-quality attempt is therefore not DiT/FLUX repair, but a single-ROI test of whether VGGT can supply the missing geometry evidence needed for source-backed compositing.

Expected evidence:
- One fixed target ROI, defaulting to DB25 longline ROI `[850, 420, 1650, 720]` on `02a00399-3857-444e-8db3-a8f58489c394` / anchor `0`, because it is the user-visible long seam and has nonzero but weak existing evidence (`near_ground=62.3%`, LiDAR support `9.4%`, key `6-5` flow reliability `10.5%`). DB41 right/lower-right may appear only as negative controls unless a separate brief changes their evidence state.
- A source/provenance preflight showing ERP ROI owner camera labels, raw `u_img/v_img` mapping or an explicit blocker if raw-UV ownership cannot be recovered, and any required DB49d sidecar/reproducibility dependency.
- A VGGT evidence audit for the same raw camera pixels: pose/decode convention, coordinate/reflection state, point/depth confidence, alignment to the known rig and available LiDAR, and target-surface support. VGGT confidence alone is not sufficient.
- If and only if raw-owner, geometry, visibility, and protected-structure gates pass, one local source-backed warp/composite candidate inside the fixed ROI, using raw camera texture and preserving lane/curb/object/building-edge masks. No generated pixels are allowed in the source-faithful branch.
- One manifest and one review board under `deliverables/dit360_v2/db58_vggt_raw_camera_seam_roi/` with before/after same-ROI, raw camera crops, source-id/UV maps, VGGT/LiDAR/flow evidence, protected masks, abstain regions, and explicit claim labels.

Kill criteria:
- The target expands beyond the single fixed ROI, or any DB41 lower-right/right-line region is promoted from no-evidence/abstain without a fresh brief and new target-surface evidence.
- VGGT pose/depth is used despite unresolved reflection/scale/axis ambiguity, undocumented coordinate convention, missing pose/decode artifacts, or failure to align against the known rig/LiDAR sanity checks.
- Raw camera owner/UV mapping is guessed, inferred from RGB similarity, or replaced by ROI-level camera labels as if it were a per-pixel `source_id_map`.
- VGGT confidence, detector-clean output, flow-only support, case-level depth/parallax, or visually plausible geometry is treated as source truth without target-surface raw/LiDAR/visibility support.
- Any DiT/FLUX/inpainting/refiner/generation/prompt-only ground/curb/lane/right-line repair is run under DB58.
- The candidate changes lane markings, curb geometry, object boundaries, BMW shape/wheels/windows, building edges, or creates fake slabs, poles, repeated texture, warped white lines, melted asphalt, or new objects.
- The output is described as a fixed `G_bmw_pano`/A1/BEST seam, fully source-faithful panorama, source-faithful ceiling, `source_id_map` completion, RED promotion, or uncaveated Bosch training data.
- Endpoint URLs, bearer tokens, HF tokens, Cloudflare JSON tokens, or other secret-like values are written to repo files, manifests, boards, logs, shell output captures, or prompts.
- If any gate fails, stop with a DB58 abstain/no-repair result and write `progress.md`; do not continue with patch-on-patch or presentation-only fallback under the same brief.

Max scope:
- Start CPU/local with existing artifacts and source inspection. No experiment may run until this brief is reviewed as the active brief.
- At most one fixed ROI, one target UUID, one anchor, one manifest, one board, and one bounded script family.
- Optional A100/VGGT use is allowed only if a secure runtime secret source is available via process env or non-repo secret file, and only for the exact DB58 target evidence extraction. No broad VGGT rerun, no model sweep, no dataset scan, no prompt sweep.
- No full-panorama repair, no source replacement, no generation, no DB49e exact-lineage rerun unless DB58 explicitly depends on already existing source/provenance sidecar support and a separate DB49 brief authorizes it.
- Use brainstorming before changing scope, and use read-only adversarial audit / multi-position reasoning before any remote/model action. Any subagent/audit result must be summarized in `progress.md`.

Required vision check:
- Board must show `G_bmw_pano` as diagnostic reference only, current `a200`/DB32 caveated handoff context, the fixed DB25 longline ROI, raw camera crops, source-owner/UV evidence or blocker, VGGT/LiDAR/flow evidence, protected lane/curb/object/building-edge masks, before/after same-ROI if a composite is attempted, abstain regions, and explicit labels: `source-backed candidate`, `diagnostic`, `abstain`, `rejected`, or `presentation-only` if applicable.
- Rectilinear/cubemap or same-ROI crop review is mandatory before any visual improvement claim.

Output location: `deliverables/dit360_v2/db58_vggt_raw_camera_seam_roi/`

Result summary: TBD -> archive to `progress.md` when done, then delete or mark closed here.

# DB-45: Geometry foundation evidence audit
Status: paused
Route: A (geometry) / evidence-only

Question: Can VGGT/Fast3R/CUT3R/DAC/DAP/PriOr-Flow/FlowSeek-style evidence turn any currently RED seam into YELLOW/GREEN, or improve the confidence calibration for layer-aware routing?

Hypothesis: Modern multi-view geometry/depth/flow foundation models may provide denser pointmaps, tracks, confidence, or panoramic depth/flow cues than the current LiDAR/DA-V2/flow metadata, but they must be calibrated as evidence only. They must not be trusted as renderers or as truth in unseen/no-evidence regions.

Why now: DB41 and DB25 show current raw/LiDAR/flow evidence is insufficient for key BMW right-line/lower-right regions. Before trying new repairs, test whether newer geometry/depth/flow methods add reliable evidence or simply hallucinate confidence on known negatives.

Expected evidence:
- Evidence-only outputs: geometry confidence, pointmap/track support, depth/flow confidence, occlusion/no-evidence flags, and correlation with raw-camera/LiDAR/parallax evidence.
- Permission-state deltas: which segments remain RED, which become YELLOW/GREEN, and why.
- No repaired panorama in this brief.

Completed substeps under this brief:
- DB45a VGGT feasibility gate: current-runtime no-go, not a VGGT model negative.
- DB45b existing-evidence permission calibration: accepted permission-calibration-only guardrails, no RED promotion.
- DB45c VGGT Commercial access update + schema gate: HF file access cleared, but VGGT route remains not evidence-ready.
- DB45d VGGT official setup/load smoke: setup/checkpoint/API ready for a future ROI probe, but no geometry evidence accepted.
- DB45e VGGT frozen-ROI confidence probe: official VGGT inference ran once on BMW anchor 0 and accepted diagnostic owner-camera confidence only; no target-surface mapping, no geometry evidence, no RED promotion.
- DB45f VGGT target-ROI owner-UV sampling gate: accepted target-pixel VGGT metadata as diagnostic-only; confidence-only RED promotion killed; no geometry evidence, no RED promotion.
- DB45g VGGT pose/pointmap metric-residual readiness gate: accepted official-source decode-path diagnostic-only; actual pose tensors/decoded extrinsics still missing; no inference, no geometry evidence, no RED promotion.
- DB45h VGGT calibrated residual job contract gate: accepted residual-job contract-only; defines pose/decode/preprocess/rig/residual requirements; no inference, no geometry evidence, no RED promotion.
- DB45i/DB45j VGGT calibrated residual extractor: latest setup replay cleared runtime import, official VGGT inference ran once, saved pose/decode/preprocess/residual diagnostics, but Sim(3) reflection and target residual gates block geometry evidence; no permission change, no RED promotion.

Parked future subtracks under this brief unless split later:
- Geometry foundation evidence job: VGGT / Fast3R / CUT3R pointmaps, tracks, confidence, and multi-view consistency.
- Depth risk upgrade: DAC / DAP versus current DA-V2-style depth metadata, especially ERP/large-FoV depth confidence.
- Flow confidence audit: PriOr-Flow / FlowSeek confidence, occlusion, and forward-backward reliability; no blind flow warp.
- Waymo sensor artifact taxonomy: SplatAD / SplatFlow / Street Gaussians-style diagnostics for HDR/color, rolling shutter/sync, dynamic object, and parallax categories; not final panorama rendering.

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
- Phase3 sub-scope / DB45c (2026-06-04): VGGT Commercial access update + evidence extractor readiness/schema gate. Question: now that the user-provided HF token can access `facebook/VGGT-1B-Commercial` files, can VGGT be prepared as a bounded DB45 evidence source without laundering model output into source truth? Hypothesis: the HF gated-file blocker may be cleared, but the current route still cannot accept VGGT evidence until runtime dependencies, cache integrity, extractor schema, and DB45b negative controls are satisfied. Why now: DB45a stopped partly on 403 gated-file access; the current HEAD check returns 200, so the route needs a fresh readiness decision before any A100 install/download/inference. Expected evidence: token-valid and config-HEAD status, stale/ready runtime flags, cache/dependency status, explicit rejection of uniform-confidence wrapper output, a target-ROI evidence schema, permission deltas, kill checks, and a board. Kill criteria: token written to any artifact; old `run_vggt_multi_anchor.py` uniform `np.ones` confidence is accepted as evidence; a VGGT renderer/repaired ERP is produced; any RED control is promoted without target-surface support; high confidence appears on DB25/DB41/DB36/DB40 controls; setup becomes an unbounded install/download; DB32 is described as fully source-faithful or as original-G repair. Max scope: local/CPU manifest + board only, optional remote status/import/cache checks only if needed, no install, no model download, no inference, no panorama repair, no source replacement, frozen DB45 8-control set. Required vision check: board must show access delta, remaining blockers, target-ROI evidence schema, and DB45b guardrail outcomes; no repaired image is allowed. Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
- Phase3 result (2026-06-04): DB45c accepted **readiness-and-schema-only** evidence. HF Commercial file access is now cleared (`config.json` HEAD 403 -> 200), but VGGT is **not evidence-ready**: remote repo is still stale (`d544214`), `vggt` import is missing, the VGGT repo cache tarball is still 0 bytes, no verified checkpoint cache is recorded, and the existing wrapper still emits uniform `np.ones` confidence. No model download/inference/repair was run. `accepted_db45_geometry_evidence=false`, `permission_state_changes=none`, `red_promotions=[]`, route state `access_cleared_but_not_evidence_ready`. Detail archived at top of `progress.md`.
- Phase4 sub-scope / DB45d (2026-06-04): VGGT official setup + Commercial checkpoint load smoke gate. Question: after DB45c cleared HF Commercial file access, can the current A100 runtime load the official VGGT code/checkpoint and expose auditable confidence fields needed for a future DB45 ROI extractor? Hypothesis: official VGGT can be installed/loaded in a bounded remote setup, and the API should expose `depth_conf` / `point_conf` or equivalent real confidence outputs; if not, the VGGT route remains blocked before any AV seam inference. Why now: DB45c turned the access blocker into runtime/cache/schema blockers; the next minimal step is to clear or confirm the setup/checkpoint/API blocker without touching seam repair. Expected evidence: remote setup command transcript summary, official repo/version path, import status, Commercial checkpoint load status, dependency/cache/disk status, evidence of confidence-capable API fields from official output inspection or source inspection, no-token leak check, kill checks, and a board. Kill criteria: HF/Colab token is written to artifacts; setup mutates local repo or silently downgrades project dependencies; old uniform-confidence wrapper is run or accepted; no real confidence/validity field can be found; setup exceeds one bounded job or becomes open-ended debugging; AV seam inference is attempted before setup passes; any ERP/renderer/repair/source replacement is produced; any RED control is promoted; DB32/G claims are overstated. Max scope: one A100 setup/load-smoke job plus local manifest/board; may clone/install official VGGT and download/load exactly `facebook/VGGT-1B-Commercial` once under runtime/Drive cache; no AV image inference, no repaired panorama, no renderer, no source replacement, no diffusion/refiner, no RED promotion. Required vision check: text board must show setup/load/confidence-field verdict and explicit no-model-output-as-seam-evidence decision; no repaired image is allowed. Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
- Phase4 result (2026-06-04): DB45d accepted **setup-and-api-smoke-only** evidence. One A100 job cloned official VGGT (`a288dd0`), loaded `facebook/VGGT-1B-Commercial` successfully, cached `model.safetensors` on Drive, and verified confidence-capable API fields (`depth_conf`, `world_points_conf`, track confidence) plus model heads. No AV image inference, renderer, repair, source replacement, or permission promotion ran. `vggt_setup_ready_for_future_roi_probe=true`, but `accepted_db45_geometry_evidence=false`, `vggt_roi_inference_ran=false`, `permission_state_changes=none`, `red_promotions=[]`. Future ROI probe still requires a new bounded sub-scope, current extractor sync/upload, real confidence fields, frozen controls, and DB45b kill criteria. Detail archived at top of `progress.md`.
- Phase5 sub-scope / DB45e (2026-06-04): VGGT frozen-ROI confidence evidence probe v0. Question: when official VGGT is run on the raw 7-camera BMW anchor, do real VGGT confidence fields add ROI-specific evidence that can change any DB45 permission state? Hypothesis: VGGT may expose useful confidence/risk metadata, but DB25/DB41 RED controls should remain RED unless confidence is tied to target-surface overlap and raw/LiDAR support; DB36/DB40 generated fake geometry must remain reject controls because raw-camera VGGT confidence cannot validate generated-core artifacts. Why now: DB45d cleared setup/checkpoint/API readiness; the next smallest evidence step is one bounded ROI reducer, not a repaired panorama. Expected evidence: uploaded current extractor path, one A100 VGGT inference over BMW anchor 0 raw ring cameras, ROI summaries for DB25 longline / DB41 right / DB41 lower-right, real `depth_conf` / `world_points_conf` statistics, camera-label overlap, existing LiDAR/flow comparison, generated-fake controls marked non-admissible, kill checks, and a board. Kill criteria: old `run_vggt_multi_anchor.py` or uniform `np.ones` confidence is used; any token appears in artifacts; more than one anchor/log is run; output is a renderer, ERP repair, source replacement, or generated image; VGGT confidence alone promotes DB25/DB41 RED; DB41 lower-right is not preserved as zero-LiDAR abstain; DB36/DB40 generated fake artifacts are laundered; DB32 is described as fully source-faithful or original-G repair; setup turns into open-ended debugging. Max scope: one A100 inference job, one log/anchor (`02a00399...`, anchor 0), 7 raw ring cameras, ROI reducer over three source-evidence ROIs plus DB36/DB40 non-admissible fake controls; no repair, no renderer, no source replacement, no diffusion/refiner, no permission promotion unless DB45b target-surface support criteria are explicitly met. Required vision check: board must include ROI table, VGGT confidence bands/statistics, existing LiDAR/flow support, final permission labels, and explicit no-repair/no-RED-promotion checks; no repaired image is allowed. Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
- Phase5 result (2026-06-04): DB45e accepted **vggt-roi-confidence-diagnostic-only** evidence. One A100 job ran official VGGT on BMW anchor 0 raw 7-camera input and captured real non-uniform `depth_conf` / `world_points_conf` maps. Because the current evidence pack only supports camera-owner summaries, not pixel-exact target-surface mapping, all DB25/DB41 ROIs remain `RED/abstain`; DB41 lower-right preserves zero-LiDAR abstain; DB36/DB40 generated fake-geometry controls remain non-admissible rejects. `accepted_db45_geometry_evidence=false`, `permission_state_changes=none`, `red_promotions=[]`. Detail archived at top of `progress.md`.
- Phase6 sub-scope / DB45f (2026-06-04): VGGT target-ROI owner-UV sampling gate v0. Question: can the renderer's actual ERP-to-raw-camera source-owner `u_img/v_img` mapping turn DB45e's owner-camera confidence into pixel-targeted evidence for DB25/DB41 seam ROIs, and does that evidence justify any permission-state change? Hypothesis: exact source-owner UV sampling is feasible and will be stronger than DB45e owner summaries, but it will still not promote DB25/DB41 because VGGT pointmap/confidence sampled at target ROI owner pixels lacks DB45b target-surface raw/LiDAR support and may show model-internal confidence even on zero-LiDAR/no-evidence regions. Why now: DB45e proved official VGGT confidence exists but owner-camera aggregation is too weak; `render_camera_to_erp` already computes raw `u_img/v_img`, so the next minimal step is to expose/sample that mapping before abandoning or expanding VGGT. Expected evidence: one A100 job over BMW anchor 0 raw 7-camera input; explicit official preprocessing coordinate mapping; per-ROI source-owner UV valid fraction; sampled `depth`, `depth_conf`, `world_points`, and `world_points_conf` stats at target ROI owner pixels; optional model-internal point disagreement for overlap pixels; existing LiDAR/flow support side-by-side; generated-fake controls marked non-admissible; no-token scan; board with owner-UV sampled confidence/validity heatmaps and final permission labels. Kill criteria: renderer UV mapping is guessed rather than derived from the same calibration/projection math; VGGT preprocessing crop/pad mapping is not recorded; old uniform wrapper is used; more than one log/anchor is run; output is a renderer, repaired ERP, source replacement, or generated image; VGGT confidence alone promotes DB25/DB41 RED; DB41 lower-right does not remain zero-LiDAR abstain; model-internal pointmaps are treated as metric ego truth without LiDAR scale/residual; DB36/DB40 generated fake artifacts are laundered; DB32/G claims are overstated; the job becomes open-ended debugging. Max scope: one A100 inference job, one log/anchor (`02a00399...`, anchor 0), 7 raw ring cameras, three source-evidence ROIs plus DB36/DB40 non-admissible controls, no repair/generation/source replacement, no permission promotion unless DB45b target-surface support is explicitly satisfied. Required vision check: board must show sampled owner-UV validity, VGGT confidence bands/heatmaps, existing LiDAR/flow, generated-control boundary, and explicit no-repair/no-RED-promotion checks. Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
- Phase6 result (2026-06-04): DB45f accepted **vggt-target-uv-sampling-diagnostic-only** evidence. The one allowed A100 inference job completed remotely (`0404998afa534865b137b4c7eb97f41d`, exit `0`) and wrote Drive JSON; a later read-only recovery job (`81b6e87db75d445eb058829fc4a58865`, exit `0`) compacted that saved JSON without rerunning VGGT. Owner-UV sampling succeeded for DB25 longline / DB41 right / DB41 lower-right using the renderer's raw-camera UV mapping and official VGGT crop preprocessing. This improves DB45e's owner-camera summary into pixel-targeted model diagnostics, but it does not satisfy DB45b target-surface raw/LiDAR support and it kills VGGT confidence-only RED promotion. DB25/DB41 remain `RED/abstain`; DB41 lower-right remains zero-LiDAR abstain; DB36/DB40 generated fake-geometry controls remain non-admissible rejects. `accepted_db45_geometry_evidence=false`, `permission_state_changes=none`, `red_promotions=[]`. Detail archived at top of `progress.md`.
- Phase7 sub-scope / DB45g (2026-06-04): VGGT pose/pointmap metric-residual readiness gate v0. Question: can official VGGT outputs be decoded and calibrated against the known Waymo-style camera rig and LiDAR evidence well enough to test target-surface residuals, rather than relying on confidence or owner-UV sampling alone? Hypothesis: a source-faithful promotion still probably fails because VGGT pointmaps live in a model coordinate frame and DB41 lower-right has zero LiDAR support, but an explicit readiness gate can determine whether a future residual job is legitimate or should be killed before another inference. Why now: DB45f killed confidence-only RED promotion; the only remaining VGGT path that could matter is calibrated pose/pointmap residual evidence against raw camera rays and LiDAR/planes. Expected evidence: official VGGT source/API inspection for `pose_enc`/camera decoding and coordinate conventions; whether camera centers can be aligned to known rig centers by a documented Sim(3) or equivalent; required residual metrics for sampled owner-UV points; explicit LiDAR/raw comparison schema; no-token scan; board or manifest showing ready/blocked status and next-run permission. Kill criteria: no official/dependable camera or pose decode path is found; coordinate alignment is guessed; Sim(3)/scale residual is not recorded; pointmaps are treated as metric ego truth without calibration; DB41 lower-right is promoted despite zero LiDAR support; confidence, detector-clean, or source-owner UV alone promotes RED; more than one source-inspection job is needed; any model inference/render/repair/source replacement/generation is run under this readiness sub-scope; DB32/G claims are overstated. Max scope: one lightweight remote source/API inspection job plus local manifest/board; no VGGT inference, no model load, no download, no repaired panorama, no renderer, no source replacement, no diffusion/refiner, no RED promotion. Required vision check: board must show official decode readiness, alignment/residual schema, DB45b guardrails, and explicit `no inference/no repair/no promotion` labels. Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
- Phase7 status (2026-06-04): source fallback diagnostic accepted, runtime readiness still paused on executor availability. Added `scripts/phase3/db45g_vggt_pose_decode_readiness_gate.py`; dry-run local manifest/board works. The one allowed runtime source/API inspection has not started: one provided Cloudflare tunnel returned HTTP `530` at `/exec` and `/status`, and a later provided tunnel hostname failed DNS resolution (`NXDOMAIN` / `getaddrinfo failed`) before `/exec` submission. Because this is only a tunnel blocker, DB45g performed a CPU/local official-source fallback inspection over public official VGGT docs/source: official source documents `pose_encoding_to_extri_intri`, OpenCV camera-from-world extrinsics, and depth/point unprojection utilities. This accepts only `vggt-official-source-decode-path-diagnostic-only`. Local DB45f confirms `pose_enc` / `pose_enc_list` appear in prediction keys, but the actual pose tensor / decoded extrinsics were not stored. Therefore `residual_readiness=false`, `accepted_db45_geometry_evidence=false`, `model_inference_ran=false`, `permission_state_changes=none`, `red_promotions=[]`. Re-run only the same runtime source/API inspection when a reachable executor is available; any residual inference/extractor still needs a fresh bounded sub-scope and must save/decode pose/extrinsics and align against Waymo rig/LiDAR before using pointmaps.
- Phase8 sub-scope / DB45h (2026-06-04): VGGT calibrated residual job contract gate v0. Question: after DB45g documents the official decode path but shows that DB45f did not save pose tensors/decoded extrinsics, can we define the minimal future residual extractor contract that would make VGGT pointmaps admissible as evidence rather than confidence-only metadata? Hypothesis: a contract can be specified now: a future bounded extractor must save `pose_enc`, decoded extrinsics/intrinsics, preprocessing transforms, camera-center Sim(3) alignment to the Waymo rig, LiDAR/raw reprojection residuals, and control-specific permission deltas. The contract itself will not accept geometry evidence because no new inference or decoded tensors are produced. Why now: executor/tunnel is unavailable, but the project can still prevent the next A100 run from becoming open-ended or overclaiming. Expected evidence: CPU/local manifest and board with required fields, alignment/residual schema, thresholds, no-run checks, DB25/DB41/DB36/DB40 control behavior, and explicit next-brief requirements. Kill criteria: contract treats VGGT world points as metric ego truth without Sim(3)/LiDAR/raw residuals; allows confidence/owner-UV/detector-clean to promote RED; fails to preserve DB41 lower-right zero-LiDAR abstain; launders DB36/DB40 generated fake geometry; authorizes residual inference under DB45h; produces repaired ERP/render/source replacement/generated output; exceeds CPU/local schema scope; DB32/G claims are overstated. Max scope: CPU/local only, existing DB45 artifacts only, one script/manifest/board; no network dependency beyond already inspected official-source facts, no A100, no model load/download/inference, no renderer/repair/source replacement/diffusion, no permission promotion. Required vision check: board must show the contract ladder, required hard checks, DB41/DB25 control outcomes, and `no inference/no geometry/no RED promotion` labels. Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
- Phase8 result (2026-06-04): DB45h accepted **vggt-residual-job-contract-only** diagnostic evidence. CPU/local script `scripts/phase3/db45h_vggt_residual_job_contract_gate.py` produced the future extractor contract and board without model action. Required future saved outputs are `pose_enc`, decoded extrinsics/intrinsics, preprocessing mapping, Waymo rig extrinsics, and LiDAR/raw residuals; required ladder is official decode -> camera centers -> Sim(3) rig alignment -> target-surface LiDAR/raw residuals. DB25/DB41 remain `RED/abstain`, DB41 lower-right remains zero-LiDAR abstain, DB36/DB40 generated controls remain rejects, and DB32 remains caveated handoff. `accepted_db45_geometry_evidence=false`, `runtime_ready=false`, `model_inference_ran=false`, `permission_state_changes=none`, `red_promotions=[]`. Detail archived at top of `progress.md`.
- Phase9 sub-scope / DB45i (2026-06-04): VGGT calibrated residual extractor v0. Question: with DB45h's contract frozen and an A100 offered, can one bounded official VGGT run save `pose_enc`, decode camera extrinsics/intrinsics through the official path, align VGGT camera centers to the Waymo rig by Sim(3), and produce target-surface LiDAR/raw residual diagnostics strong enough to change any DB45 permission state? Hypothesis: the extractor will likely be useful as a calibration diagnostic but will not promote DB25/DB41 because DB41 lower-right has zero LiDAR support and DB25/DB41 right have sparse target-surface support; however, it can kill or justify the VGGT residual route with actual decoded pose tensors rather than confidence-only metadata. Why now: DB45e/f killed VGGT confidence-only promotion; DB45g/h established the decode path and required contract; the only remaining VGGT path that could matter is calibrated residual evidence. Expected evidence: one BMW log/anchor A100 inference result or a paused DNS/tunnel record; saved pose tensor shape/sample, decoded extrinsics/intrinsics shape/sample, preprocessing mapping, AV2/Waymo rig camera centers, Sim(3) scale/RMS/max/per-camera residuals, owner-UV point residual summaries, sparse LiDAR residual summaries per DB25/DB41 ROI, generated-control boundary, no-token scan, and a board. Kill criteria: executor DNS/status is unreachable; official `pose_encoding_to_extri_intri` cannot decode `pose_enc`; decoded camera centers cannot be aligned to AV2/Waymo rig with recorded Sim(3); reflection/degenerate scale is accepted; Sim(3) RMS or max residual exceeds the contract threshold; LiDAR/raw target-surface residuals are missing but RED is promoted; confidence/owner-UV validity/detector-clean alone promotes RED; DB41 lower-right is promoted despite zero LiDAR support; DB36/DB40 generated fake geometry is laundered; output includes repaired ERP, renderer image, source replacement, diffusion/refiner, generated pixels, or prompt-only seam repair; more than one log/anchor or retry job is used under this sub-scope; DB32/G claims are overstated. Max scope: one A100 inference/extractor job if executor is reachable, one log/anchor (`02a00399...`, anchor 0), 7 raw ring cameras, frozen DB25/DB41 ROIs plus DB36/DB40 non-admissible controls, compact JSON recovery only if log truncates; no repaired panorama, no renderer output, no source replacement, no diffusion/refiner, no promotion unless DB45b target-surface support and DB45h residual gates pass. Required vision check: board must show decode status, Sim(3) alignment, ROI residual table, DB41 lower-right zero-LiDAR boundary, generated-control rejection, and `no repair/no generation/no RED promotion unless residual gates pass` labels. Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
- Phase9 status (2026-06-04): DB45i is **blocked-or-paused on runtime VGGT import**, not on DNS for the latest attempt. Added `scripts/phase3/db45i_vggt_calibrated_residual_extractor.py` and generated local manifest/board plus sanitized remote-result/reachability records. Earlier bounded remote attempts stopped before `/status`/`/exec` because provided tunnel hostnames failed DNS resolution. The latest user-provided A100 endpoint passed approved non-sandbox `/status` and submitted one DB45i `/exec` job (`07381bc2c9d44811bf8717ffca5a1582`, exit `0`), but the remote runtime failed before official VGGT inference with `ModuleNotFoundError: No module named 'vggt'`. Therefore `model_inference_ran=false`, no `pose_enc` tensor was saved, no decoded extrinsics/intrinsics or Sim(3) residuals exist, and no target-surface residual evidence was accepted. `accepted_db45_diagnostic_evidence=false`, `accepted_db45_geometry_evidence=false`, `runtime_ready=false`, `model_inference_ran=false`, `permission_state_changes=none`, `red_promotions=[]`. DB45i must not continue patch-on-patch by ad hoc installing/rerunning under the same sub-scope; any runtime bootstrap/cache restore plus residual retry needs a fresh bounded brief or explicitly scoped setup replay. Detail archived at top of `progress.md`.
- Phase10 sub-scope / DB45j (2026-06-04): A100 runtime VGGT bootstrap replay + single DB45i residual retry.
  - Question: Can the currently reachable A100 runtime restore/import official VGGT from the existing DB45d setup path/cache and then run exactly one DB45i calibrated residual extractor retry without turning DB45i into patch-on-patch?
  - Hypothesis: the latest DB45i failure is an ephemeral runtime import/setup loss, not a model or HF-access failure; rerunning the already accepted DB45d setup/load smoke once should make `vggt` importable, after which one DB45i retry may either produce decoded pose/Sim(3)/residual diagnostics or cleanly hit the next hard blocker.
  - Why now: `/status` and `/exec` are now reachable and HF access is expected to be available, but DB45i is blocked before inference solely by missing `vggt` in the new Colab runtime.
  - Expected evidence: one setup replay result from `scripts/phase3/db45d_vggt_setup_smoke_gate.py --run-remote` showing official repo/import/checkpoint load status, then at most one `scripts/phase3/db45i_vggt_calibrated_residual_extractor.py --run-remote` retry result; manifest/board updates must report whether `pose_enc`, decoded cameras, preprocessing mapping, Sim(3), and target-surface residuals exist.
  - Kill criteria: setup replay fails to import official `vggt` or load/cache `facebook/VGGT-1B-Commercial`; token or endpoint appears in artifacts; setup becomes open-ended debugging; more than one setup replay or one residual retry is used; DB45i still lacks `pose_enc`/decoded cameras/preprocess mapping after retry; Sim(3) or LiDAR/raw residual gates fail but RED is promoted; confidence/owner-UV/detector-clean alone promotes RED; DB41 lower-right is promoted despite zero LiDAR support; any repaired ERP, renderer image, source replacement, generated pixels, diffusion/refiner, or prompt-only seam repair is produced; DB32/G claims are overstated.
  - Max scope: at most two remote jobs on the current reachable A100 executor: one DB45d setup/load replay and one DB45i residual retry; one BMW log/anchor (`02a00399...`, anchor 0), 7 raw ring cameras, frozen DB25/DB41 ROIs plus DB36/DB40 non-admissible controls; no repair/generation/source replacement/renderer, no permission change unless DB45b target-surface support and DB45h residual gates pass, no local model download.
  - Required vision check: DB45d setup board if changed plus DB45i residual board; DB45i board must show import/inference/decode/Sim(3)/ROI residual status, DB41 lower-right zero-LiDAR boundary, generated-control rejection, and `no repair/no generation/no RED promotion` labels.
  - Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
  - Result (accepted diagnostic-only): DB45j ran exactly the allowed setup replay plus one DB45i retry. Setup/load replay succeeded (`setup_ready=true`), and DB45i official VGGT inference succeeded with `pose_enc_shape=[7,9]`, decoded extrinsics `[7,3,4]`, and preprocessing mapping count `7`. The residual route remains non-admissible for permission promotion: `sim3_contract_thresholds_pass=false` because `reflection_detected=true` despite mean/max center residuals `0.217991/0.319168 m`; DB25/DB41 target residuals are large and all ROI rows keep `permission_promotion_allowed=false`; DB41 lower-right preserves known LiDAR support `0.000`. Accepted evidence type is `vggt-calibrated-residual-diagnostic-only`; `accepted_db45_geometry_evidence=false`, `permission_state_changes=none`, `red_promotions=[]`, no repair/generation/source replacement/renderer occurred. Detail archived at top of `progress.md`.
- Phase11 sub-scope / DB45k (2026-06-04): VGGT pose/reflection coordinate audit from existing outputs only.
  - Question: Is the DB45j Sim(3) reflection failure caused by a documented coordinate/order/extrinsic-convention issue in the saved DB45i outputs, or should the VGGT residual route remain diagnostic-only/paused under the current evidence gates?
  - Hypothesis: A bounded saved-artifact audit may identify whether the reflection flag is a bookkeeping/convention artifact, but the pairwise rig-shape and target-surface residual evidence will likely keep VGGT residuals non-admissible for geometry promotion.
  - Why now: DB45j finally produced real official VGGT pose/decode/preprocess/residual outputs, and the single hard alignment blocker is concrete (`reflection_detected=true`). Before abandoning or rerunning VGGT, the project needs one falsifiable audit of extractor/coordinate assumptions that does not consume A100 time or turn into patch-on-patch.
  - Expected evidence: one CPU/local script, manifest, and board using only existing DB45i/DB45h outputs plus local code inspection. Report documented camera order, decoded extrinsic convention, saved VGGT/Waymo centers, pairwise-distance consistency, non-reflective and reflected similarity fits, documented axis/order hypotheses, target ROI residual boundary, and a final route recommendation.
  - Kill criteria: requires arbitrary axis flips, reflection, camera permutations, or threshold changes not derived from code/docs; pairwise rig-shape inconsistency remains material; non-reflective Sim(3) still fails DB45h/DB45i thresholds; ROI residuals remain no-promotion; any DB25/DB41 RED promotion relies on VGGT confidence/pointmaps without raw/LiDAR target-surface support; DB41 lower-right is promoted despite known LiDAR support `0.000`; any A100/executor/model inference/download, renderer, ERP repair, source replacement, generated pixels, diffusion/refiner, or prompt-only seam repair is attempted; DB32/G claims are overstated; token/endpoint strings appear in artifacts.
  - Max scope: CPU/local saved-artifact audit only. Inputs are existing `db45i_vggt_calibrated_residual_remote_result.json`, `db45i_vggt_calibrated_residual_manifest.json`, `db45h_vggt_residual_job_contract_manifest.json`, and local source files. No network, no A100, no executor, no model load, no HF access, no new raw-data scan, no new VGGT inference, no pointmap rerun, no repair/generation/source replacement, no permission change, no RED promotion.
  - Required vision check: DB45k board must show the reflection/non-reflection verdict, pairwise rig-shape audit, best admissible documented hypothesis, ROI no-promotion table, DB41 lower-right zero-LiDAR boundary, and explicit `diagnostic-only/no repair/no generation/no RED promotion` labels.
  - Output location: `deliverables/dit360_v2/db45_geometry_evidence_audit/`.
  - Result (accepted diagnostic-only / route paused): DB45k ran CPU/local over existing DB45i/DB45h/DB45g artifacts only. Official camera-from-world center extraction still prefers reflection and fails the no-reflection contract (`mean/max=0.217990/0.319167 m`, pass=false); reflected fit has `det_R=-1.0` and remains non-admissible. Translation-column-as-center gives a non-reflective center fit (`mean/max=0.173113/0.373909 m`, center thresholds pass), but conflicts with DB45g official-source camera-from-world convention and is diagnostic-only. Official-center pairwise rig-shape error remains material (`mean/rms/max=0.193334/0.218761/0.378413 m`), all DB25/DB41 ROI rows stay no-promotion, and DB41 lower-right preserves LiDAR support `0.000`. Accepted evidence type is `vggt-pose-reflection-coordinate-audit-diagnostic-only`; `accepted_db45_geometry_evidence=false`, `permission_state_changes=none`, `red_promotions=[]`, no A100/model/reinfer/repair/generation/source replacement occurred. Detail archived at top of `progress.md`.

Required vision check:
- Board must include raw-camera support crop, LiDAR/depth/flow evidence overlays if available, model confidence overlay, and final permission-state label.
- Mandatory visual check on DB41 lower-right and DB36/DB40 fake-geometry negatives.

Result summary: DB45 is paused after DB45k accepted VGGT pose/reflection coordinate audit **diagnostic-only** evidence. The route has real official inference/pose/decode/preprocess/residual outputs, but official camera-from-world center extraction still fails the no-reflection contract, reflected fits are not admissible, translation-column improvement is an undocumented convention-conflict diagnostic, and target ROI residuals remain no-promotion. Do not continue VGGT residual patch-on-patch, rerun inference, or reinterpret residuals as permission.

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
Status: DB57 visual review complete / no candidate promotion / DB47f paused
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
- Phase0 / DB47a (2026-06-04): CPU/local existing-artifact inventory only. Scope is DB27 temporal scan, DB28 strict-clean source scan, DB31 multilog candidate scan, DB32 sky/source-sidestep handoff candidate, DB34 current-best QA, DB38 Bosch handoff, DB42 seam decision, and DB43 source-faithfulness gate summaries/boards if present. No A100, no executor, no new dataset scan, no repaired panorama, no generation, no source replacement, no promotion of source-sidestep as original-G repair. Expected output is a manifest/board that reports existing scanned counts, strict/relaxed/source-sidestep accepted counts, reject/caveat reasons, evidence gaps, and a next-scan contract.
- Phase1 / DB47b (2026-06-04): Candidate-universe freeze and threshold replay over existing shortlist metrics only.
  - Question: Can DB47 define a fixed candidate universe and produce strict/relaxed/rejected/accounting labels before any broader full scan?
  - Hypothesis: Existing DB31 shortlist metrics should be enough to separate a narrow strict source-sidestep cluster from relaxed/diagnostic/rejected candidates, while also showing that this is not a dataset-wide acceptance claim and not original-G seam repair.
  - Why now: DB47a found 36 existing candidate records but did not yet freeze the next executable candidate universe or report acceptance/reject accounting for a bounded DB47 pass.
  - Expected evidence: one CPU/local manifest and board over the 22 DB31 shortlist rows, with DB27/DB28 as same-log comparison only; report total universe size, strict accepted, relaxed accepted, rejected-by-reason, abstain/insufficient-evidence distribution, per-log counts, local-image availability, and next-scan readiness.
  - Kill criteria: expands beyond DB31/DB27/DB28 without a new brief; treats DB31 shortlist as the full Waymo distribution; reports only pretty top candidates; promotes source-sidestep as original-G repair; selects a final handoff candidate without same-ROI vision; hides reject reasons or image-availability gaps; claims DB41/right-line repair or any source-faithful repair.
  - Max scope: CPU/local only, existing JSON/boards only, no executor/A100/model inference/new dataset scan/panorama repair/generation/source replacement/diffusion/refiner/RED promotion. Output location: `deliverables/dit360_v2/db47_source_candidate_mining/`.
  - Required vision check: board must show the fixed universe label, strict/relaxed/rejected counts, reject reasons, DB28/DB31 visual references, explicit `source-sidestep-only` boundary, and no final repair/handoff claim.
- Phase2 / DB47c (2026-06-04): Same-ROI bucket visual/accounting review over DB47b buckets.
  - Question: Do the DB47b strict/relaxed/failure buckets survive a bounded same-ROI visual sanity review using only existing local DB28/DB31 evidence?
  - Hypothesis: The strict bucket should remain a same-log source-sidestep review cluster centered on the known DB28/a200-style scene; the relaxed same-log rows and non-BMW rows should remain diagnostic or hold because they either shift the scene/source boundary or already failed exact seamroute follow-up.
  - Why now: DB47b froze metric buckets but explicitly selected no final candidate. The next useful step is to attach visual/accounting verdicts to those buckets before deciding whether DB47 needs a same-log review, a full scan, or no more source-mining work.
  - Expected evidence: one CPU/local manifest and board using DB47b manifest, DB28 strict-clean montage/summary, DB31 ROI/full montages, and already-local DB28/DB31 exact compare assets if present. Report exact-asset availability, strict/relaxed/rejected visual verdict counts, missing-asset limits, and next-step recommendation.
  - Kill criteria: creates a new candidate image; runs a new scan, renderer, A100, executor, model, generation, or source replacement; selects a final candidate from montage-only evidence; treats same-log source-sidestep as original-G seam repair; hides missing exact assets; uses DB32/G/DB41 claims beyond inherited boundaries; reports only wins without failure rows.
  - Max scope: CPU/local only, existing DB28/DB31/DB47b artifacts only, no new dataset scan, no repair, no generation, no source replacement, no permission change, no RED promotion. Output location: `deliverables/dit360_v2/db47_source_candidate_mining/`.
  - Required vision check: board must show strict rows, relaxed rows, rejected/failure rows, exact compare asset availability, DB28/DB31 visual references, explicit `review-only/no final candidate` label, and the inherited DB41 abstain/source-sidestep boundary.
- Phase3 / DB47d (2026-06-04): Exact same-log review pack for DB47c strict/relaxed rows.
  - Question: Do the DB47c strict/relaxed same-log rows have enough exact local evidence to narrow DB47's next action without selecting a final candidate?
  - Hypothesis: only the three strict rows with exact DB28 assets (`a105`, `a200`, `a204`) can remain exact-review candidates; strict montage-only and relaxed rows must stay hold because they lack local exact compare/final evidence. This should clarify whether DB47 should pause, do a full bounded scan, or later open a real final-candidate review.
  - Why now: DB47c established counts but did not make the exact assets self-contained enough for handoff or adversarial review. Before any full scan or candidate promotion, the exact same-log evidence and missing-asset boundary need a compact board and manifest.
  - Expected evidence: one CPU/local manifest and board over the 10 DB47c strict/relaxed same-log rows, using only DB47c manifest, DB28 strict-clean summary/montage, and existing DB28 exact compare/final images. Report exact asset rows, missing exact rows, per-anchor metrics, visual review labels, and next-step constraint.
  - Kill criteria: creates or fetches a new candidate image; runs seamroute, renderer, A100, executor, model, generation, source replacement, full scan, or repair; selects a final handoff candidate; promotes montage-only or relaxed rows; treats source-sidestep as original-G repair; hides missing exact assets; overstates DB32, `G_bmw_pano`, or DB41 lower-right/right-line.
  - Max scope: CPU/local only, existing DB47c/DB28 artifacts only, 10 same-log strict/relaxed rows, no new scan, no exact asset fetch, no repair/generation/source replacement/permission change/RED promotion. Output location: `deliverables/dit360_v2/db47_source_candidate_mining/`.
  - Required vision check: board must show all exact rows and missing-exact holds, pasted exact compare/final evidence where available, DB28 metric context, explicit `source-sidestep review-only/no final candidate/no original-G repair`, and inherited DB41 abstain boundary.
- Phase4 / DB47e (2026-06-04): Existing-artifact final-candidate review over exact rows.
  - Question: Among the current DB47d exact-review rows, is there enough local exact evidence to confirm a bounded source-sidestep candidate for source selection, or must DB47 stay review-only/hold?
  - Hypothesis: `a200` should remain the most defensible source-sidestep base for the DB32 Bosch-facing handoff candidate because it has exact compare/final lineage and downstream DB29/DB32/DB34 QA, while `a204` may be a near neighbor to review and `a105` must stay compare-only hold unless a real final asset already exists.
  - Why now: DB45/VGGT residual evidence is paused after DB45k and no prompt-only repair route should continue. Before opening DB49 exact-lineage packaging, DB47 should either confirm the existing source-selection base or explicitly keep source selection unresolved.
  - Expected evidence: one CPU/local manifest and board using only existing DB47d/DB28/DB32/DB34/DB41/G diagnostic artifacts. Report exact asset availability, same-ROI/full-context crops for `a105`, `a200`, and `a204`, final-eligibility, visual verdicts, reject/hold reasons, inherited DB47b/c/d accounting, DB41 abstain boundary, and claim label `source-sidestep candidate only`.
  - Kill criteria: selects a final from metrics, montage-only rows, or DB47d labels alone; promotes any missing-exact hold; creates/fetches/reruns candidate imagery; runs seamroute, renderer, executor, A100, model, HF/VGGT, generation, source replacement, or repair; hides DB47b/c/d reject accounting; worsens or moves the seam defect into DB41/right-line risk; claims source-faithful repair, original-G repair, uncaveated Bosch training readiness, or a filled `source_id_map`; includes endpoint/HF/Colab token strings.
  - Max scope: CPU/local existing-artifact review only over `a105`, `a200`, `a204`; final-eligible rows must have exact compare and exact final image already present; `a201/a209/a210/a211/a031/a038/a040` remain missing-exact holds; no new scan, no exact asset fetch, no repair/generation/source replacement/permission change/RED promotion. Output location: `deliverables/dit360_v2/db47_source_candidate_mining/`.
  - Required vision check: board must show full candidate context plus same-ROI/source-boundary crops, exact compare/final availability, DB32 `s40` caveated handoff context, `G_bmw_pano` as diagnostic failure reference only, DB41 right/lower-right abstain boundary, reject/hold accounting, and explicit `not source-faithful / not original-G repair / not source_id_map evidence`.
- Phase5 / DB47f (2026-06-04): Fixed-universe exact source-selection closure preflight.
  - Status: accepted preflight / paused pending secure runtime/data.
  - Question: Can DB47 close the fixed 8 exact/final evidence gaps identified by DB51, without turning source selection into an unbounded scan, local repair, or token-leaking remote workflow?
  - Hypothesis: DB47f can be a useful seam-quality sidestep only if it closes exactly the known gaps: seven missing exact holds (`a201`, `a209`, `a210`, `a211`, `a031`, `a038`, `a040`) plus the `a105` final gap. If local data or a secure runtime secret source is absent, DB47f must stop at a documented preflight pause rather than using chat-pasted tokens or promoting montage-only rows.
  - Why now: DB51 ranked DB47f as the next single seam-quality route after DB50 found 0 executable source-faithful repair targets. DB47e confirmed `a200` only as the current source-sidestep base and preserved the missing-exact holds, so DB47 cannot honestly advance until those fixed gaps are either closed or explicitly paused.
  - Expected evidence: one CPU/local script, manifest, and board under `deliverables/dit360_v2/db47_source_candidate_mining/`. Manifest must list the fixed 8 targets, current local exact-asset status, local data/runtime secret preconditions, the redacted exact rerun/fetch contract, DB41/DB32/G claim boundaries, and whether DB47f is ready or paused. If secure runtime/data preconditions are absent, no remote job is submitted.
  - Kill criteria: target universe expands beyond the 8 DB51 gaps; a candidate is promoted from metrics, montage-only rows, or missing final assets; an unbounded dataset scan starts; a chat-pasted HF/Colab/tunnel token is echoed, stored, committed, embedded in a command artifact, or scanned into outputs; more than one closure batch or more than 8 anchors is submitted; seamroute/renderer output is used as local repair instead of source-selection evidence; DB41/no-evidence is promoted; DB32 is described as fully source-faithful or source-faithful ceiling; source selection is described as original-G/G-A1-BEST repair; a `source_id_map` or Bosch training-ready claim is fabricated; any prompt-only DiT/FLUX ground/curb/lane/right-line repair is run.
  - Max scope: CPU/local preflight always; optional exact asset closure only if secure runtime/data preconditions are available through `COLAB_URL`/`COLAB_TOKEN` env vars or an approved non-repo runtime secret source. No HF/VGGT/model inference, no diffusion/generation, no local seam repair, no source replacement, no permission change, no RED promotion. Output location: `deliverables/dit360_v2/db47_source_candidate_mining/`.
  - Required vision check: board must show DB47e/DB51 context, the 8-target closure table, local missing-asset status, precondition verdict, DB41/DB25 abstain boundary, DB32/G claim boundary, and explicit `no repair / no remote unless secure preconditions / no token use / no RED promotion`.

Required vision check:
- Include both wins and failures.
- Same-ROI/source-boundary boards for accepted and rejected candidates.
- Explicit reason for why a selected candidate is a source-sidestep rather than seam repair.
- Phase0 vision check: board must show accepted source-sidestep/current-best candidates and rejected/no-successor candidate-mining outcomes side by side as text/available thumbnails; no new repaired image is allowed.

Result summary: Phase0 DB47a accepted `source-selection-inventory-only` evidence. CPU/local script `scripts/phase3/db47_source_candidate_inventory.py` reads existing DB27/DB28/DB31/DB34/DB38/DB42/DB43 artifacts only and produced `deliverables/dit360_v2/db47_source_candidate_mining/db47a_source_candidate_inventory_manifest.json` plus `db47a_source_candidate_inventory_board.jpg`. It reviewed 36 existing candidate records (DB27 7 nearby anchors, DB28 7 strict-clean anchors, DB31 22 shortlist candidates across 5 logs), preserved DB28/DB32 as source-sidestep/current-handoff evidence only, and set the next full DB47 scan contract. Phase1 DB47b accepted `source-selection-threshold-replay-only` evidence: CPU/local script `scripts/phase3/db47b_candidate_universe_threshold_replay.py` freezes the DB31 22-row shortlist as the bounded universe, uses DB27/DB28 only as comparison context, reports 7 strict review-bucket rows, 3 relaxed review-bucket rows, and 12 rejected/diagnostic rows with reject reasons, inherits DB41 lower-right/right-line abstain without evaluating or promoting it, and selects no final panorama. Phase2 DB47c accepted `source-selection-visual-accounting-only` evidence: CPU/local script `scripts/phase3/db47c_same_roi_bucket_review.py` reviews all 22 DB47b rows using DB47b plus existing DB28/DB31 summaries/montages/exact assets only; verdicts are 3 `review_exact_same_log`, 4 strict montage-only hold, 3 relaxed hold, 2 same-log weak-margin rejects, 3 confirmed existing failures, and 7 non-BMW no-successor rejects. Phase3 DB47d accepted `source-selection-exact-review-pack-only` evidence: CPU/local script `scripts/phase3/db47d_exact_same_log_review.py` reviews the 10 strict/relaxed same-log rows using DB47c plus existing DB28 exact assets only; verdicts are 3 `exact_review_candidate_not_final` (`a105`, `a200`, `a204`), 4 strict missing-exact holds, and 3 relaxed missing-exact holds. Phase4 DB47e accepted `source-selection-final-candidate-review-existing-artifacts-only` evidence: CPU/local script `scripts/phase3/db47e_final_candidate_review.py` reviews only `a105`, `a200`, and `a204`; confirms `a200` as the current source-sidestep base for the existing DB32 `s40` handoff candidate; keeps `a204` as an exact final-eligible alternate not selected; keeps `a105` compare-only hold; and preserves the 7 DB47d missing-exact holds. Phase5 DB47f accepted only `fixed-universe-exact-closure-preflight-only` evidence: CPU/local script `scripts/phase3/db47f_fixed_universe_exact_closure_preflight.py` confirmed the fixed 8 exact/final gaps and paused before closure until runtime/data preconditions were available. DB56 then accepted `accepted_exact_closure_assets_complete` evidence: one fixed 8-anchor remote `_seamroute.py` batch completed with job `exit=0`, and fetch-only deterministic asset recovery closed all `15/15` required compare/final assets under DB28 exact paths. DB47 now has exact asset availability for the fixed DB47f universe, but no final panorama is selected yet. No new dataset scan beyond the fixed target/anchors, repair, generation, source replacement, `source_id_map`, permission change, RED promotion, source-faithful/original-G claim, or uncaveated Bosch training-data claim is accepted.

# DB-54: DB47f local exact-asset recovery audit
Status: accepted / paused
Route: sidestep / source-selection evidence

Question: Are any of the fixed DB47f missing exact compare/final assets already present in the local worktree or historical/untracked deliverables under alternate folders, names, or zip entries?

Hypothesis: Because the worktree contains many historical and untracked seamroute/DiT deliverables, at least some DB47f gaps may be recoverable by cataloging local artifacts before using A100 or any token-bearing executor. If no matching assets exist locally, the result still usefully confirms that DB47f genuinely needs a safe runtime/data path.

Why now: DB53 says to stop adding infra-only layers. Before running the actual bounded DB47f closure batch on A100, a token-free local recovery audit can test whether the required exact evidence is already present and avoid unnecessary remote work.

Expected evidence:
- One CPU/local manifest and board listing the fixed 8 DB47f targets only: `a201`, `a209`, `a210`, `a211`, `a031`, `a038`, `a040`, and `a105`.
- For each required compare/final asset, report canonical-path status, alternate local file matches, zip-entry matches if any, image readability/dimensions for local file matches, and remaining missing assets.
- Explicit claim boundary: found paths are local recovery candidates only until same-ROI visual/lineage review accepts them; this brief does not repair an ERP or select a final candidate.

Kill criteria:
- Search expands beyond the fixed 8 DB47f targets or treats arbitrary BMW/GhostKill images as DB28 exact assets without the `SR_bmw_db28_a<anchor>_*` tag.
- Any zip is extracted, any candidate image is copied into a canonical location, or any seamroute/renderer/model/A100/network command is run.
- A found path is promoted to source-faithful repair, original-G/A1/BEST repair, `source_id_map`, RED promotion, or uncaveated Bosch training-data readiness.
- Chat-pasted HF/Colab/tunnel token values are echoed, stored, scanned into output, or embedded in commands/artifacts.
- DB41 lower-right/right-line or DB25 abstain boundaries are promoted from recovered source-selection assets.

Max scope:
- CPU/local only; read file names, image metadata, and zip member names under bounded repo artifact roots.
- No A100, no executor, no network, no HF/VGGT, no model inference, no dataset scan, no seamroute/renderer execution, no exact asset fetch, no image copy/extraction, no repair/generation/source replacement/permission change/RED promotion.
- Output location: `deliverables/dit360_v2/db54_local_artifact_recovery/`.

Required vision check:
- Board must show the 8-target table, found/missing required assets, thumbnails for local image matches where present, zip-entry matches separately labeled as not recovered files, DB47f/DB53 context, and explicit `local catalog only / no closure / no repair / no token use` boundary.

Result summary: DB54 accepted only `local-exact-asset-recovery-audit-only` evidence and paused with `status=paused_no_local_exact_assets_found`. CPU/local script `scripts/phase3/db54_local_exact_asset_recovery.py` scanned bounded local artifact roots by filename plus zip member names only (`2084` files, `18` zip files, `238` zip members) and found `0` local file matches and `0` zip-entry-only matches for the fixed DB47f 15 required assets. All `15` required compare/final assets remain missing. No A100/executor/network/HF/VGGT/model/dataset scan/seamroute/renderer/zip extraction/image copy/exact fetch/repair/generation/source replacement/`source_id_map`/permission change/RED promotion occurred. Detail archived in `progress.md` 2026-06-04 DB54 entry.

# DB-55: EGSR O3 photometric polish acceptance audit
Status: accepted
Route: A (geometry-adjacent) / source-derived bounded photometric operator

Question: Can the existing risk-gated local Y seam repair be accepted as EGSR's O3 photometric-only operator, with a precise allowed-use contract and explicit no-geometry/no-DB41/no-G-family claim boundary?

Hypothesis: The 14-anchor evidence from the existing three-anchor and fresh11 risk-gated local Y repair runs is enough to accept O3 as a bounded source-derived luminance polish: it reduces seam Y discontinuity while changing only a small local fraction and never moving structure. It should be part of EGSR for T1/YELLOW photometric seams, but must remain forbidden for RED/no-evidence, lane/curb/object geometry, original-G repair, and Bosch training-ready claims.

Why now: DB50 found no executable new source-faithful geometry/LPAM target from current local artifacts, DB54 confirmed DB47f local exact assets are absent, and DB43/DB44 already need a concrete operator library rather than only a dispatcher. O3 is the one existing positive local operator that can be formalized without A100 or new data.

Expected evidence:
- One CPU/local manifest and board under `deliverables/dit360_v2/db55_egsr_o3_photometric_operator/`.
- Aggregate metrics over existing `deliverables/seam_risk_gated_color_repair/three_anchor_v1/three_anchor_repair_summary.json` and `fresh11_v1/fresh11_repair_summary.json`: improvement distribution, changed fraction, max Y delta, and per-anchor wins/limits.
- Acceptance contract: allowed only for T1/YELLOW photometric seams with low structure-risk; source labels and geometry remain unchanged; output must be labeled source-faithful photometric polish, not geometry repair.
- Rejection/abstain contract for DB41 lower-right/right-line, DB25 dark-wall low-evidence line, G/A1/BEST classic BMW geometry seam, fake generated geometry controls, lane/curb/object-adjacent seams, and any high-structure-risk region.

Kill criteria:
- Any claim that O3 fixes geometry, DB41 lower-right/right-line, lane/curb/object seams, original `G_bmw_pano`/A1/BEST, or DB32 source-faithfulness.
- Any new repair run, dataset scan, A100/executor/network/model/generation/source replacement, or prompt-only DiT/FLUX use.
- The audit hides cases where p95 improvement is weak or unchanged, or treats mean Y improvement as sufficient for geometry acceptance.
- The operator changes source ownership, creates a `source_id_map`, fills abstain regions, or promotes RED controls.
- It ignores the earlier DB26 long-line photometric attenuation rejection and fails to distinguish O3's low-structure local-Y gate from unsafe broad low-frequency wash.

Max scope:
- CPU/local existing-artifact audit only. Read the two risk-gated Y summary JSON files, existing review boards, DB43/DB44/DB50/DB54 manifests if needed, and `scripts/phase3/seam_risk_gated_color_repair.py`.
- No new panorama repair run, no raw data load, no A100, no executor, no network, no HF/VGGT, no model inference, no renderer, no image copy/extraction, no generation, no source replacement, no permission change, no RED promotion.
- Output location: `deliverables/dit360_v2/db55_egsr_o3_photometric_operator/`.

Required vision check:
- Board must show three-anchor and fresh11 review panels, aggregate metric table, allowed/forbidden operator contract, DB26 unsafe photometric-control distinction, DB41/DB25 abstain boundary, and explicit `photometric-only / no geometry repair / no source replacement / no RED promotion`.

Result summary: DB55 accepted `O3` only as `source-derived bounded photometric polish` for T1/YELLOW-GREEN low-structure photometric seams. CPU/local script `scripts/phase3/db55_egsr_o3_photometric_operator_audit.py` reads existing O3 summaries/boards only and aggregates 14 anchors: mean seam dY improvement mean/median/min/max `17.71/18.87/7.13/23.63%`, p95 improvement mean/median/min/max `5.39/5.87/0.00/11.63%`, changed fraction mean/max `0.034/0.039`, and max Y delta `9.10`. Weak p95 case `9f871fb4_a017` is disclosed. Accepted geometry repair, DB41/DB25 repair, original-G/A1/BEST repair, `source_id_map`, permission change, RED promotion, and uncaveated Bosch training-data claims remain false. No new repair run/raw data load/A100/executor/network/model/generation/source replacement occurred. Detail archived in `progress.md` 2026-06-04/05 DB55 entry.

# DB-56: DB47f exact closure batch execution
Status: accepted exact-closure assets complete / DB57 follow-up completed with no candidate promotion
Route: sidestep / source-selection evidence

Question: With a reachable A100 executor and the DB53 token-free launch harness, can DB47f close the fixed 8 exact source-selection gaps by producing the missing compare/final assets, without expanding the target universe or turning source selection into repair?

Hypothesis: One bounded remote `_seamroute.py` batch over the exact DB47f target universe can produce the missing `SR_bmw_db28_a<anchor>_compare.jpg` and `SR_bmw_db28_a<anchor>_final_1024x2048.png` assets for review. The result may close source-selection evidence gaps, but it still remains source-sidestep/exact-candidate evidence only, not source-faithful local seam repair, not original `G_bmw_pano`/A1/BEST repair, and not Bosch training-ready data.

Why now: DB51 ranked DB47f as the next seam-quality route if secure runtime/data preconditions are satisfied; DB52/DB53 defined the token-safe env/harness contract; DB54 proved the exact assets are not hidden locally; DB55 has now formalized the only existing photometric operator. The user has provided a reachable A100 executor, so the next useful DB47 action is the actual bounded closure batch, not another infra-only layer.

Expected evidence:
- One remote `/status` check and at most one `/exec` batch using process-only `COLAB_URL`/`COLAB_TOKEN`; no endpoint/token value may be written to repo files, manifests, boards, logs, progress, or shell output.
- Exactly 8 fixed anchors: `a201`, `a209`, `a210`, `a211`, `a031`, `a038`, `a040`, and `a105`.
- Required local evidence after fetch: compare+final for the first 7 anchors, final for `a105`, copied only into the expected DB28 exact-asset paths from DB53; no arbitrary candidate image destinations.
- A DB56 manifest/board under `deliverables/dit360_v2/db47_source_candidate_mining/` reporting job state, output existence/hash/size, missing assets, visual review labels, DB41/DB25 abstain boundary, DB32/G claim boundary, and secret-scan status.

Kill criteria:
- Executor `/status` is unreachable, unauthorized, or reports active jobs that make the one-batch run unsafe.
- Remote repo/data path lacks `_seamroute.py` or the target AV2 log; stop before widening the run or changing dataset/log.
- More than one batch, more than 8 anchors, or any anchor outside the DB47f fixed universe is submitted.
- Any remote command runs HF/VGGT, DiT/FLUX, model inference, dataset scan beyond the fixed target log/anchors, source replacement, generation, or prompt-only repair.
- Any endpoint URL, bearer token, HF token, or secret-like string is echoed, stored, committed, included in a command artifact, or written to manifest/board/progress.
- Output is promoted from source-selection exact evidence into source-faithful repair, original-G/A1/BEST repair, `source_id_map`, RED promotion, DB41/DB25 repair, or uncaveated Bosch training-data readiness.
- Visual review shows the newly closed assets are worse than current a200/DB32 or move the seam into DB41/no-evidence regions; then mark the candidate rejected/hold rather than patching again.

Max scope:
- One bounded remote closure batch only: `/status`, `/exec`, polling, and file fetch for expected outputs.
- No HF/VGGT/model inference, no diffusion/generation, no new scan, no source replacement, no `--save-source-id-map`, no Bosch data-contract promotion, no RED promotion.
- CPU/local postprocessing may build the DB56 manifest/board and copy exact fetched assets into the DB28 expected local paths only.
- Output location: `deliverables/dit360_v2/db47_source_candidate_mining/` plus exact compare/final assets under `deliverables/dit360_v2/db28_clean_subset_refine/`.

Required vision check:
- Board must show all 8 anchors, each required compare/final availability, same-ROI crops/thumbnails for every fetched exact asset, explicit pass/hold/reject labels, current a200/DB32 context, inherited DB41/DB25 abstain boundary, and the claim line `source-selection exact closure only / no repair / no source_id_map / no RED promotion / no token in artifacts`.

Result summary: DB56 accepted `accepted_exact_closure_assets_complete` evidence. `scripts/phase3/db56_db47f_exact_closure_batch.py` ran exactly one fixed 8-anchor remote `_seamroute.py` batch through an approved process-env runtime source; `/status` reported `colab-gpu` on `NVIDIA A100-SXM4-40GB` with `active_jobs_before=0`, job `55a0c9f7f40a4af9979f73dc3073532e` completed `state=done`, `exit=0`, and a fetch-only follow-up used the existing completed job plus deterministic remote paths to fetch all `15/15` required exact assets without a second `/exec`. Outputs are `deliverables/dit360_v2/db47_source_candidate_mining/db56_db47f_exact_closure_manifest.json`, `db56_db47f_exact_closure_board.jpg`, and the fetched `SR_bmw_db28_a<anchor>_*` compare/final assets under `deliverables/dit360_v2/db28_clean_subset_refine/`. Secret scan hits are `0`; endpoint/token values are absent from artifacts. This closes asset availability only: no final candidate is selected, no local seam repair/source replacement/model/generation/source_id_map/RED promotion occurred, and DB41/DB25 plus DB32/G claim boundaries remain unchanged. Detail archived in `progress.md` 2026-06-04/05 DB56 entry.

# DB-57: DB47f exact-candidate visual final review
Status: accepted visual review / no candidate promotion
Route: sidestep / source-selection evidence

Question: Now that DB56 has closed the fixed DB47f exact asset gaps, do any of the 8 newly available same-log source-selection candidates deserve a visual final-candidate accept/hold/reject decision, without turning source selection into local seam repair?

Hypothesis: The DB56 exact assets may reveal one or more candidates that are visually competitive with the current `a200`/DB32 source-sidestep base. A CPU-only review can make the next DB47 decision by comparing the fixed candidates side by side with current DB32/a200 and DB41/DB25 boundaries. If the evidence is ambiguous or worse than current a200/DB32, the correct outcome is hold/reject, not another patch.

Why now: DB56 completed the exact asset availability precondition that blocked DB47f. The next seam-quality step is therefore not another remote batch, infra layer, or presentation branch; it is the required visual accounting over the exact assets that now exist.

Expected evidence:
- One CPU/local manifest and board under `deliverables/dit360_v2/db47_source_candidate_mining/`.
- Review exactly the DB56 fixed anchors: `a201`, `a209`, `a210`, `a211`, `a031`, `a038`, `a040`, and `a105`.
- Include current context: DB47e `a200` current source-sidestep base, DB32 `s40` caveated handoff, DB41 lower-right/right-line abstain, and `G_bmw_pano` diagnostic reference boundary.
- For every candidate, report exact compare/final availability, image size/hash, source-selection verdict (`accept`, `hold`, or `reject`), visual reason codes, and whether it is allowed to displace the current a200/DB32 base.

Kill criteria:
- Any target outside the fixed DB56 8-anchor universe is added, or any missing/alternate non-DB28 asset is treated as exact evidence.
- Any `/status`, `/exec`, A100, network, HF/VGGT/model inference, `_seamroute.py` rerun, renderer/dataset scan, diffusion/generation, prompt-only repair, local seam repair, source replacement, `source_id_map`, permission change, or RED promotion occurs.
- A candidate is promoted from metrics or thumbnail appearance alone without visual same-ROI/source-boundary accounting.
- DB41 lower-right/right-line, DB25 low-evidence line, or generated/fake-geometry controls are promoted.
- Output is described as source-faithful local repair, original `G_bmw_pano`/A1/BEST repair, Bosch training-ready data, or a source-faithful ceiling.
- If visual review shows no candidate beats the current a200/DB32 source-sidestep base, the branch must stop with hold/reject decisions rather than patch-on-patch.
- Any endpoint URL, bearer token, HF token, or secret-like value is written to outputs.

Max scope:
- CPU/local existing-asset review only.
- Read DB56/DB47e/DB32/DB41 context plus the exact DB28 compare/final images for the 8 fixed anchors.
- Create one manifest and one board; do not modify candidate images or create a new panorama.
- Output location: `deliverables/dit360_v2/db47_source_candidate_mining/`.

Required vision check:
- Board must show all 8 candidates with compare/final thumbnails, current `a200`/DB32 context, DB41 abstain context, and explicit labels for `source-selection exact review only / no repair / no source_id_map / no RED promotion / no token in artifacts`.
- Manual visual review must inspect the board before any candidate is accepted; if evidence is insufficient, verdicts must be `hold` or `reject`.

Result summary: DB57 accepted `db47f-exact-candidate-visual-review-only` evidence and selected no new final candidate. CPU/local script `scripts/phase3/db57_db47f_visual_candidate_review.py` reviewed exactly the 8 DB56 fixed anchors using existing DB56/DB47e/DB32/DB41/G diagnostic context plus DB28 exact compare/final assets. All `8/8` candidates have exact compare+final assets, but visual review found no clear improvement over the current `a200`/DB32 source-sidestep base: `a201/a209/a210/a211` are held as near-duplicates with no clear win and no DB32 lineage, `a031/a038/a040` are rejected for relaxed context/lighting shift, and `a105` is rejected for different context/no clear win. No `/status`, `/exec`, A100, network, HF/VGGT/model inference, `_seamroute.py` rerun, renderer/dataset scan, diffusion/generation, repair, source replacement, `source_id_map`, permission change, RED promotion, final-candidate selection, source-faithful/original-G claim, or uncaveated Bosch training-data claim occurred. Detail archived in `progress.md` 2026-06-04/05 DB57 entry.

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
Status: paused after DB49e preflight (DB49a accepted inventory-only; DB49b accepted partial sidecar starter pack; DB49c accepted source-id feasibility; DB49d accepted source-map instrumentation-only; DB49e paused on preflight preconditions)
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
- Phase0 / DB49a (2026-06-04): Existing-artifact data-contract inventory.
  - Question: Can the current Bosch-facing state be expressed as a provenance/caveat contract without inventing new masks or overstating DB32/DB47?
  - Hypothesis: Existing DB32/DB42/DB43/DB47 artifacts are enough to define the minimum contract fields, current evidence availability, and blocking gaps; this should make DB32 usable as a caveated handoff candidate while preventing misuse as source-faithful original-G repair.
  - Why now: DB47d narrowed source-selection review evidence but still selected no final candidate, and DB45's VGGT residual route has no accepted geometry promotion. The lowest-risk useful progress is to lock the data-product contract that future EGSR outputs must satisfy.
  - Expected evidence: one CPU/local manifest and board using existing DB32/DB34/DB38/DB42/DB43/DB45/DB47 artifacts only; report required fields (`source_id_map`, `generated_mask`, `unknown_or_abstain_mask`, `risk_map`, `eval_report`, `candidate_image`, `caveat_table`), available evidence paths, missing/blocking fields, claim labels, and next-step contract.
  - Kill criteria: creates or modifies candidate image/masks; runs renderer/model/executor/A100/generation/repair/source replacement; hides generated sky/out-of-FOV or unknown/abstain regions; claims DB32 is fully source-faithful or original-G repair; treats DB47d exact-review rows as final; omits DB41 lower-right/right-line abstain; omits generation-model/license caveat; includes secrets or current endpoint tokens.
  - Max scope: CPU/local reporting only, existing artifacts only, no new image generation/repair/mask generation, no new dataset scan, no permission change, no RED promotion. Output location: `deliverables/dit360_v2/db49_bosch_data_contract/`.
  - Required vision check: board must show current handoff candidate reference, generated/abstain/risk/eval contract status, DB47 review-only status, DB41 abstain, DB32 source-sidestep/generated-sky caveats, and explicit `not source-faithful / not original-G repair / no final candidate from DB47d alone` labels.
  - Result (accepted inventory-only): `scripts/phase3/db49a_bosch_data_contract_inventory.py` produced `db49a_bosch_data_contract_inventory_manifest.json` and `db49a_bosch_data_contract_inventory_board.jpg` using existing artifacts only. `candidate_image`, `eval_report`, `caveat_table`, and `presentation_flag` are available; `generated_mask` is partial from the existing sky-core mask; per-pixel `source_id_map`, `unknown_or_abstain_mask`, and `risk_map` remain missing/blocking; generation-model/license review remains required. DB32 `s40` stays caveated handoff only; DB47d stays not-final; DB41 lower-right/right-line stays no-evidence/abstain; `ready_for_uncaveated_bosch_training_data=false`.
- Phase1 / DB49b (2026-06-04): DB32 sidecar starter pack from existing masks/evidence only.
  - Question: Can DB49 package real available sidecars for DB32 (`generated_mask`, partial `unknown_or_abstain_mask`, partial `risk_map`) while explicitly refusing to fabricate the missing `source_id_map`?
  - Hypothesis: The existing DB34 sky-core mask, DB32 candidate image, and DB41 abstain ROIs are sufficient to create a truthful starter sidecar pack: generated sky core, out-of-FOV/DB41 abstain regions, and a conservative risk map. This will make DB49a's contract gaps concrete without pretending the sidecars are complete Bosch training-data metadata.
  - Why now: DB49a identified missing/partial contract fields. The next useful CPU-local step is to package the fields that are genuinely derivable from current evidence, while keeping `source_id_map` and full per-pixel risk as blocking gaps.
  - Expected evidence: one CPU/local script/manifest/board plus three sidecar PNGs: sky-core generated mask, partial unknown-or-abstain mask, partial risk map. Manifest must report derivation rules, pixel counts/fractions, DB41 ROI inclusion, out-of-FOV detection, incomplete fields, and `ready_for_uncaveated_bosch_training_data=false`.
  - Kill criteria: modifies DB32 candidate image; creates a source_id_map by guesswork; treats DB41 rectangles as source-faithful repair evidence; hides lower out-of-FOV black band; claims a complete Bosch dataset contract; runs renderer/model/executor/A100/generation/repair/source replacement; changes permission state or RED promotion; includes current endpoint/HF/Colab token strings.
  - Max scope: CPU/local packaging only, existing DB32/DB34/DB41/DB43/DB49a artifacts only, no model/network/A100, no new repair, no generated pixels, no dataset scan. Output location: `deliverables/dit360_v2/db49_bosch_data_contract/`.
  - Required vision check: board must show DB32 candidate, generated sky-core mask, unknown/abstain mask, risk map, DB41 lower-right/right-line labels, source_id_map missing, and explicit `not training-ready / not source-faithful repair / not original-G repair`.
  - Result (accepted partial sidecars only): `scripts/phase3/db49b_sidecar_starter_pack.py` produced `db49b_generated_mask_sky_core_only.png`, `db49b_unknown_or_abstain_mask_partial.png`, `db49b_risk_map_partial.png`, `db49b_sidecar_overlay_on_db32.jpg`, `db49b_sidecar_starter_pack_manifest.json`, and `db49b_sidecar_starter_pack_board.jpg`. Hard checks pass; DB32 candidate sha256 is unchanged; `source_id_map_created=false`; `ready_for_uncaveated_bosch_training_data=false`; DB41 right/lower-right remains abstain; no repair/generation/model/executor/network/permission change/RED promotion occurred.
- Phase2 / DB49c (2026-06-04): `source_id_map` feasibility and ownership-evidence inventory.
  - Question: Can DB49 recover or reproduce a real per-pixel `source_id_map` for DB32 from existing source-ownership artifacts or scripts, without guessing ownership from RGB pixels or DB41 overlays?
  - Hypothesis: Existing DB28/DB32/DB34/DB41 artifacts may expose partial camera-owner evidence or a reproducible source-owner generation path, but unless a structured per-pixel owner map exists or can be regenerated under the exact DB32 candidate lineage, `source_id_map` must remain missing/blocking.
  - Why now: DB49b packaged generated/unknown/risk partial sidecars; `source_id_map` is now the largest remaining Bosch data-contract blocker. DB45 is still paused because the current `/status` check failed DNS again before any `/exec`.
  - Expected evidence: one CPU/local script/manifest/board inventorying existing candidate lineage, source/owner/camera-id artifacts, reproducible scripts, partial ROI owner evidence, missing fields, and an explicit `source_id_map_status`. If a complete existing owner map is not found, no `source_id_map` PNG should be created.
  - Kill criteria: infers source ownership from RGB similarity, mask color, or ROI overlays; creates a guessed `source_id_map`; treats DB41 ROI camera labels as full-panorama source ownership; modifies DB32 candidate; runs A100/executor/model/network; claims training-ready or source-faithful repair; hides missing lineage or missing exact owner map; includes current endpoint/HF/Colab token strings.
  - Max scope: CPU/local inventory only over existing DB28/DB29/DB32/DB34/DB41/DB43/DB49 artifacts and repository scripts; no new scan, no renderer, no repair, no generation, no source replacement, no permission change, no RED promotion. Output location: `deliverables/dit360_v2/db49_bosch_data_contract/`.
  - Required vision check: board must show DB32 candidate, DB49b sidecars, any discovered owner/camera-id evidence, source lineage, explicit `source_id_map missing/not fabricated` unless a true complete map exists, and `not training-ready / not original-G repair`.
  - Result (accepted inventory-only): `scripts/phase3/db49c_source_id_map_feasibility.py` produced `db49c_source_id_map_feasibility_manifest.json` and `db49c_source_id_map_feasibility_board.jpg`. No complete per-pixel `source_id_map` was recovered for the exact DB32 lineage; no map was created. DB28/DB41 camera labels remain ROI-level diagnostic/count evidence only; DB34 noncore byte-exact preservation remains preservation evidence only; DB49b sidecars are not ownership maps; `_seamroute.py`'s internal routed label is only a future reproducible path candidate unless a new bounded brief instruments/reruns the exact lineage and saves an owner artifact. `source_id_map_status=missing_blocking_not_fabricated`, `ready_for_uncaveated_bosch_training_data=false`, and no repair/generation/model/executor/network/permission change/RED promotion occurred.
- Phase3 / DB49d (2026-06-04): Seamroute source/provenance sidecar instrumentation.
  - Question: Can the existing `_seamroute.py` route be instrumented to save auditable per-pixel source/provenance sidecars for future exact reruns without changing default panorama behavior or fabricating DB32 ownership?
  - Hypothesis: `_seamroute.py` already computes the routed `label` needed for a real source-owner sidecar. A default-off flag can save `routed_source_id_map`, valid mask, virtual-centre composite/effect mask, and legend so future exact lineage reruns can satisfy the Bosch data-contract map field while preserving mixed/composite caveats.
  - Why now: DB49c proved current artifacts have no complete `source_id_map`; DB45's VGGT residual route has no accepted geometry promotion; the lowest-risk progress is to prepare the accepted seamroute for future provenance capture rather than guessing ownership from ROI labels.
  - Expected evidence: one CPU/local patch plus manifest/board showing optional flag, sidecar filenames, label conventions, invalid/mixed/composite codes, default behavior unchanged, no DB32 map created, and no repair/generation/model/executor action.
  - Kill criteria: changes default `_seamroute.py` output pixels; creates a DB32 `source_id_map` without rerunning exact lineage; treats virtual-centre blended/composited pixels as single-source truth; hides invalid/out-of-FOV pixels; runs A100/executor/model/network; claims training-ready/source-faithful DB32; promotes DB41 or repairs original G/A1/BEST; includes endpoint/HF/Colab token strings.
  - Max scope: CPU/local instrumentation and static audit only; optional/default-off `_seamroute.py` sidecar flag; no dataset run, no renderer execution, no candidate image modification, no repair, no generation, no permission change, no RED promotion. Output location: `deliverables/dit360_v2/db49_bosch_data_contract/`.
  - Required vision check: board must show the source/provenance sidecar contract, explicit default-off behavior, `source_id_map for DB32 still missing until exact rerun`, virtual-centre composite caveat, and `not training-ready / not original-G repair`.
  - Result (accepted instrumentation-only): `_seamroute.py` now has default-off `--save-source-id-map` / `--sidecar-dir` support for future exact reruns, and `scripts/phase3/db49d_seamroute_source_map_instrumentation.py` produced `db49d_seamroute_source_map_instrumentation_manifest.json` and `db49d_seamroute_source_map_instrumentation_board.jpg`. The future sidecar contract includes `routed_source_id_map`, `valid_mask`, `virtual_center_effect_mask`, `ground_reproject_effect_mask`, `final_source_state_map`, `source_id_overlay`, and `source_id_sidecar_legend`. DB49d creates no DB32 source map, runs no seamroute dataset/render/model/executor/network, changes no default output pixels unless the flag is explicitly passed, and marks virtual-centre composited/effect pixels as non-single-source truth (`250`) while preserving invalid/out-of-FOV (`255`). `source_id_map_status=missing_until_exact_seamroute_rerun_not_fabricated`, `ready_for_uncaveated_bosch_training_data=false`, and no repair/generation/permission change/RED promotion occurred.
- Phase4 / DB49e (2026-06-04): Exact-lineage source/provenance rerun preflight and one-run gate.
  - Question: Can DB49 use DB49d's default-off sidecar support to produce a validated source/provenance sidecar pack for the exact DB32 lineage (`DB28/a200 source base -> DB29 sky corecompose -> DB32 s40`), without modifying DB32 or hiding generated/unknown/composite regions?
  - Hypothesis: The exact seamroute lineage can produce real routed source/provenance sidecars for the a200 source base, but it must first pass a local preflight: DB32 SHA/source lineage unchanged, DB49d flags present, target data path/current-repo lineage available, secure runtime secrets available, and no generated-sky or virtual-centre composite pixels mislabeled as camera-owned. If the secure runtime/data preconditions are absent, DB49e should stop at a documented preflight pause rather than use pasted chat tokens or fabricate ownership.
  - Why now: DB47e confirmed `a200` as the current source-sidestep base for DB32, and DB49c/DB49d established that `source_id_map` is the largest remaining Bosch data-contract blocker. This is the next useful data-contract step; it is not a seam-repair or generation step.
  - Expected evidence: one CPU/local preflight manifest and board; if and only if secure runtime secrets and the exact data path are available, at most one exact rerun of `_seamroute.py --uuid 02a00399-3857-444e-8db3-a8f58489c394 --anchor 200 --tag db49e_a200_exact --save-source-id-map --sidecar-dir <db49e sidecar dir>` producing source/provenance sidecars. Manifest must report DB32 SHA, DB34 source lineage, DB49b/c/d dependencies, runtime/data precondition status, sidecar paths/counts if produced, generated-sky mask integration status, virtual-centre/composite code handling, DB41 abstain carry-forward, and claim labels.
  - Kill criteria: DB32 `s40` SHA differs from `ade90f2bb629abac88e6516d6a2abd0d6785619024c0be4d5a01ea23dc4a8930`; DB34 source base is not DB28/a200; `_seamroute.py` default behavior or DB32 candidate pixels change; local/remote target data is missing; no secure runtime secret source exists (`COLAB_URL`/`COLAB_TOKEN` environment variables or a non-repo runtime secret file); the pasted chat token is echoed, stored, committed, or embedded in a command artifact; more than one rerun job is submitted; generated sky, out-of-FOV, DB41 abstain, invalid, or virtual-centre composite/effect pixels are folded into camera ownership; RGB similarity/overlay colors are used as ownership truth; sidecar dimensions do not align 1:1 with DB32; a rerun output cannot be tied to exact a200 lineage; DB32 is claimed source-faithful, original-G repair, or uncaveated Bosch training data; any repair/generation/model/HF/VGGT/DiT/FLUX action is run.
  - Max scope: CPU/local preflight always; optional single remote seamroute exact rerun only if secure runtime/data preconditions pass. No image repair, no generation, no model inference, no HF/VGGT, no DiT/FLUX, no source replacement, no permission change, no RED promotion. Output location: `deliverables/dit360_v2/db49_bosch_data_contract/`.
  - Required vision check: board must show DB32 candidate unchanged, DB34 a200 lineage, DB49b generated/unknown/risk sidecars, DB49d source-sidecar contract, runtime/data precondition result, generated-sky/non-source boundary, DB41 abstain boundary, and explicit `not source-faithful / not original-G repair / not training-ready unless source_id_map plus masks are validated`.

Required vision check:
- Final board must include the image, masks, risk/abstain overlays, and same-ROI caveat crops.
- Manual claim-language review before any Bosch/Koi-facing use.

Result summary: DB49a accepted as `bosch-data-contract-inventory-only`; DB49b accepted as `sidecar-starter-pack-partial-only`; DB49c accepted as `source-id-map-feasibility-inventory-only`; DB49d accepted as `source-map-instrumentation-only`; DB49e accepted only `exact-lineage-source-map-rerun-preflight-only` and paused before rerun. DB49e preflight confirms the a200/DB32 lineage and DB49d sidecar support, but no source map was created because the local target log/data path is absent and no secure runtime secret source is available (`COLAB_URL/COLAB_TOKEN` env or non-repo runtime secret file). Chat-pasted runtime JSON is not a secure runtime source for DB49e because DB49e forbids token echo/store/command-artifact use. See the 2026-06-04 DB49e block at the top of `agent/progress.md`. `source_id_map` remains missing/blocking for DB32 until one exact lineage rerun saves and validates a true owner artifact; no narrative fill-in and no pasted-token command usage.

# DB-50: EGSR source-faithful operator v0 / GREEN-YELLOW segment repair
Status: accepted Phase0 / paused pending follow-up sub-brief
Route: A / source-faithful EGSR operator implementation

Question: After DB43/DB44 accepted the fake-geometry gate and layer-aware dispatcher, can the project move from gate/report to a bounded source-faithful operator pass that only touches GREEN/YELLOW eligible seam components and keeps RED/no-evidence components abstained?

Hypothesis: A useful first source-faithful EGSR operator must be weaker than a global inpainting model: keep/abstain, source-only hard select, DB-proven BEV road atlas where already valid, low-frequency photometric polish only when evidence says the seam is photometric, and LPAM-like local patch alignment only for evidence-GREEN far/static segments. Under current evidence, DB41 right/lower-right and generated fake-geometry controls should stay RED/abstain/reject; if no eligible executable target exists in current local artifacts, DB50 should stop at operator-readiness evidence instead of patch-on-patch.

Why now: DB43/DB44 are accepted, DB45 VGGT residuals are diagnostic-only/no-promotion, DB47e confirms `a200` only as source-sidestep/current base, and DB49e is a provenance/data-contract pause rather than seam-quality progress. The main goal is now seam-quality and algorithm formation, so the next useful step is to audit which EGSR operators can actually execute safely under the current evidence gates.

Expected evidence:
- One CPU/local script, manifest, and board using existing DB43/DB44/DB45/DB47/DB49 evidence only.
- Per-component operator-readiness table for the DB44 components: evidence state, protected structures, allowed branch, candidate operator, required inputs, executable-now status, and stop reason.
- Explicit counts for GREEN/YELLOW/RED, executable source-faithful components, abstain components, source-sidestep-only components, presentation-only/generated components, and rejected fake-geometry controls.
- If no safe operator can run from current local artifacts, the output must say so and recommend the next brief/precondition; no panorama repair is created under DB50 Phase0.
- If an operator is later allowed by a follow-up sub-brief, expected evidence must include `source_faithful_erp`, `segment_map`, `operator_map`, non-fabricated provenance/source-state map, `risk_map`, `unknown_or_abstain_mask`, segment report, and same-ROI before/after board.

Kill criteria:
- Any DB41 right/lower-right, DB25/DB41 RED, generated fake-geometry, or no-evidence component is repaired or promoted.
- LPAM/local alignment is marked executable without far/static GREEN evidence, raw/source pair support, and protected-structure checks.
- The run creates a new panorama, source replacement, generated pixels, diffusion/DiT/FLUX output, VGGT residual promotion, or A100/HF/model action under this Phase0 readiness scope.
- The output hides DB32's source-sidestep/generated-sky caveats or calls DB32 a source-faithful ceiling.
- `G_bmw_pano` is treated as the default repair base instead of a classic BMW failure / diagnostic reference.
- Operator-readiness is judged by a scalar metric or pretty board only, without reason-coded stop conditions and same-ROI vision requirements.
- It suggests continuing after a kill condition instead of writing `progress.md` and stopping.

Max scope:
- Phase0 only: CPU/local existing-artifact operator-readiness audit over DB44's 29 components and frozen guardrails.
- No new panorama repair, no renderer/dataset run, no source replacement, no generation, no HF/VGGT/DiT/FLUX, no A100/executor, no DB49e source-map rerun.
- No more than one script/manifest/board. Output location: `deliverables/dit360_v2/db50_egsr_operator_v0/`.
- Any actual operator implementation or remote run requires a follow-up DB50 sub-brief with its own kill criteria and max scope.

Required vision check:
- Board must show the operator-readiness matrix plus canonical visual context for DB32/a200 handoff, `G_bmw_pano` diagnostic failure, DB41 abstain, BEV/source-faithful ceiling, and fake-geometry rejects.
- Board must label `no repair / no generation / no RED promotion / DB32 caveated / G diagnostic only`.

Result summary: DB50 Phase0 accepted as `egsr-operator-readiness-existing-artifacts-only`; see the 2026-06-04 DB50 block at the top of `agent/progress.md`. CPU/local script `scripts/phase3/db50_egsr_operator_readiness.py` reviewed all 29 DB44 components and created `deliverables/dit360_v2/db50_egsr_operator_v0/db50_egsr_operator_readiness_manifest.json` plus board. No repair/generation/remote/model/source replacement/DB49e rerun occurred. Current local artifacts contain 0 executable new source-faithful repair targets and 0 executable LPAM targets: counts are 3 presentation-only, 1 already-satisfied keep control, 2 source-sidestep-only, 1 existing BEV caveated control, and 22 abstain/reject. DB41 remains RED/no-evidence/abstain, `G_bmw_pano` remains diagnostic only, DB32 remains caveated handoff, and DB50 must not continue patch-on-patch without a fresh target-specific sub-brief carrying raw/source-pair evidence and protected-structure checks.

# DB-51: EGSR target/source-pair evidence acquisition queue
Status: accepted / paused pending next brief
Route: A / evidence acquisition for source-faithful operators

Question: After DB50 found zero executable new source-faithful repair targets, what is the smallest evidence-acquisition queue that could make a future DB50 sub-brief actually executable without violating DB41/no-evidence or DB32/G claim boundaries?

Hypothesis: The next seam-quality progress is not another repair attempt; it is to acquire or validate target-specific evidence. The likely highest-value queue is (1) DB47 fixed-universe exact source-selection closure for the remaining same-log holds, because source/frame selection is the only currently accepted way to avoid the hard seam, and (2) a future LPAM/local-alignment target only if raw/source-pair support and protected-structure checks can be proven. DB49e remains useful for provenance but is not seam-quality; DB46/DB48 remain presentation-only.

Why now: DB50 Phase0 made the immediate operator gap explicit: current local artifacts have 0 executable repair targets and 0 LPAM targets. Continuing to write operators without target/source-pair evidence would repeat the project's patch-on-patch failure mode.

Expected evidence:
- One CPU/local script, manifest, and board using existing DB47d/e, DB50, DB44, DB25, DB41, and DB49e artifacts only.
- A ranked acquisition queue with each item labeled as `source-selection`, `operator-target`, `provenance`, `presentation-only`, or `geometry-evidence`.
- For each item: required evidence, currently available evidence, blockers, allowed next action, kill criteria pointer, expected output location, and whether it can become source-faithful repair, source-sidestep, diagnostic, presentation-only, or data-contract evidence.
- Explicit handling of DB47 missing-exact holds (`a201`, `a209`, `a210`, `a211`, `a031`, `a038`, `a040`) and `a105` compare-only final gap.
- Explicit rejection of DB25/DB41 right/lower-right as current local operator targets.

Kill criteria:
- Promotes DB41 right/lower-right or DB25/DB41 RED regions based on flow-only, montage-only, or model-confidence-only evidence.
- Treats DB47 source selection as original-G seam repair or fully source-faithful local repair.
- Treats DB49e/source_id_map provenance as seam-quality improvement.
- Treats DB46/DB48 presentation-only work as Bosch training-data/source-faithful output.
- Uses chat-pasted runtime or HF tokens, runs `/exec`, A100, HF/VGGT/DiT/FLUX, renderer, dataset scan, exact asset fetch, or panorama repair under this acquisition-queue scope.
- Creates guessed exact assets, guessed raw/source-pair evidence, or guessed source ownership.
- Produces a recommendation without a fixed max-scope and kill criteria for the next brief.

Max scope:
- CPU/local existing-artifact acquisition planning only.
- No new panorama, no source replacement, no repair, no generation, no model inference, no remote/executor, no HF/A100, no DB49e rerun, no exact asset fetch.
- One script/manifest/board under `deliverables/dit360_v2/db51_egsr_target_acquisition/`.
- Any actual asset fetch, source-selection exact closure, LPAM/local alignment, geometry model, or provenance rerun requires a fresh follow-up brief.

Required vision check:
- Board must show the acquisition queue, DB47 missing-exact holds, DB50 no-target result, DB41/DB25 abstain evidence, DB32/G claim boundary, and explicit `no repair / no remote / no token use / no RED promotion`.

Result summary: DB51 accepted only `egsr-target-source-pair-acquisition-queue-only`; see the 2026-06-04 DB51 block at the top of `agent/progress.md`. CPU/local script `scripts/phase3/db51_egsr_target_acquisition_queue.py` produced `deliverables/dit360_v2/db51_egsr_target_acquisition/db51_egsr_target_acquisition_manifest.json` plus board. It created no repair, no generation, no source replacement, no exact asset fetch, no remote/executor/model/HF/A100 action, no DB49e rerun, no permission change, and no RED promotion. The ranked queue is: (1) DB47f fixed-universe exact source-selection closure if secure runtime/data preconditions are satisfied; (2) DB50b LPAM/local-alignment target evidence only after a fixed segment has raw/source-pair support and protected-structure checks; (3) DB49e provenance, not seam-quality; (4) fixed-target geometry evidence only if it serves a selected DB51/DB50 target; (5) DB46/DB48 presentation-only only after explicit priority switch. DB47 has 8 exact/final gaps (seven missing exact holds plus `a105` final gap). DB25/DB41 remain acquisition blockers and repair abstains. Chat-pasted runtime/HF tokens are still not authorized as command/artifact secrets.

# DB-52: DB47f secure-runtime/data intake contract
Status: accepted / paused pending safe runtime/data path
Route: infra / source-selection precondition

Question: Can the project convert the newly available A100/HF situation into a token-safe, auditable launch contract for the next DB47f fixed-universe exact closure batch, without using chat-pasted secrets in commands or artifacts?

Hypothesis: DB47f is the right next seam-quality route only after secure runtime/data preconditions are satisfied. A CPU/local contract pass can materially advance the route by freezing allowed secret sources, target data checks, launch preconditions, and kill gates so the next closure batch can run once env or a non-repo runtime secret source is available, while still stopping under the current in-process state.

Why now: DB51 ranked DB47f as the next route, DB47f preflight stopped only because secure runtime/data was absent, HF gated access is now reachable, and the user has an A100 tunnel. The remaining risk is not model access; it is accidentally turning a pasted token into command/artifact state or launching an unbounded closure/fetch run.

Expected evidence:
- One CPU/local script, manifest, and board under `deliverables/dit360_v2/db52_secure_runtime_contract/`.
- Boolean-only checks for approved secret sources: `COLAB_URL`/`COLAB_TOKEN` env, `HF_TOKEN`/configured HF auth, and optional non-repo runtime secret file path.
- Fixed 8-anchor DB47f closure target list and required compare/final assets, inherited from DB47f/DB51.
- A token-free launch contract that states exactly when DB47f closure may run and exactly what remains forbidden.
- Explicit `no remote / no exec / no exact fetch / no repair / no generation / no source replacement / no source_id_map / no RED promotion` status if the safe preconditions are not available.

Kill criteria:
- Uses, echoes, stores, commits, or embeds a chat-pasted tunnel/HF token, endpoint URL, or bearer token in commands/artifacts.
- Reads secret values when only boolean availability is needed.
- Treats the pasted JSON as a secure runtime secret source.
- Runs `/status`, `/exec`, A100, HF/VGGT, model inference, renderer, exact asset fetch, dataset scan, or panorama repair under this contract scope.
- Expands beyond the fixed 8 DB47f targets.
- Calls the contract an exact closure, source-faithful repair, original-G repair, source map, or uncaveated Bosch training-data step.
- Promotes DB25/DB41 RED/no-evidence regions or changes DB32/G claim boundaries.

Max scope:
- CPU/local contract and readiness audit only.
- One script, one manifest, one board.
- No remote/network/model/data fetch, no candidate image modification, no generated pixels, no permission change, no RED promotion.
- Output location: `deliverables/dit360_v2/db52_secure_runtime_contract/`.

Required vision check:
- Board must show the approved secret-source policy, current precondition booleans, fixed 8 DB47f targets, stop/launch decision, DB32/G/DB41 claim boundaries, and explicit no-token/no-remote/no-repair/no-RED status.

Result summary: DB52 accepted only `secure-runtime-contract-only`; see the 2026-06-04 DB52 block at the top of `agent/progress.md`. CPU/local script `scripts/phase3/db52_secure_runtime_contract.py` produced `deliverables/dit360_v2/db52_secure_runtime_contract/db52_secure_runtime_contract_manifest.json` plus board. Current in-process preconditions remain false: no `COLAB_URL`/`COLAB_TOKEN` env pair, no approved non-repo runtime secret file, no local target log data, and `closure_batch_allowed_now=false`. A configured local HF auth file exists, but no HF/network recheck was run under this CPU/local contract scope. DB52 ran no remote/status/exec/A100/HF/VGGT/model/renderer/exact fetch/repair/generation/source replacement/source_id_map/permission change/RED promotion. Future DB47f closure may run only after env or non-repo runtime secret source, or local target data, is available; chat-pasted JSON/token values remain rejected as command/artifact secrets.

# DB-53: DB47f token-free launch harness dry-run
Status: accepted / paused pending safe runtime/data path
Route: infra / source-selection precondition

Question: Can DB47f be prepared as a deterministic token-free launch harness, using existing `_seamroute.py` behavior and DB47f/DB52 gates, without launching remote execution or creating exact assets now?

Hypothesis: DB52 fixed the secret-intake policy, but the next runtime-enabled turn can still drift into unbounded scan, wrong tags, extra claims, or patch-on-patch unless the exact 8-anchor command plan and output mapping are frozen now. A CPU/local dry-run manifest can materially advance DB47f by making the future one-batch closure mechanically auditable while keeping current execution paused.

Why now: DB47f remains the next seam-quality route, but current safe data path is absent. The useful local work is to remove launch ambiguity, not to run another repair or presentation branch.

Expected evidence:
- One CPU/local script, manifest, and board under `deliverables/dit360_v2/db53_db47f_launch_harness/`.
- Per-anchor dry-run command plan for exactly the 8 DB47f anchors, using `_seamroute.py --uuid 02a00399-3857-444e-8db3-a8f58489c394 --anchor <anchor> --tag bmw_db28_a<anchor>`.
- Expected remote output names and local destination names for exact `compare` and `final_1024x2048` assets only.
- Explicit launch preconditions inherited from DB52; if absent, the runner status must be dry-run/paused.
- No shell script with secrets, no endpoint values, and no command artifact containing token values.

Kill criteria:
- Runs `/status`, `/exec`, A100, network, HF/VGGT, model inference, renderer/dataset scan, exact asset fetch, or `_seamroute.py`.
- Uses chat-pasted tunnel/HF token, endpoint URL, bearer token, or any secret value in commands/artifacts.
- Expands beyond the 8 DB47f anchors or changes anchor/tag mapping.
- Writes or copies exact compare/final assets under this dry-run scope.
- Treats generated dry-run commands as executed evidence.
- Calls source-selection closure original-G repair, source-faithful local repair, source map, or Bosch training-ready output.
- Promotes DB25/DB41 RED/no-evidence or changes DB32/G claim boundaries.

Max scope:
- CPU/local dry-run plan only.
- One script, one manifest, one board.
- No remote/network/model/data fetch, no `_seamroute.py` execution, no candidate image modification, no generated pixels, no permission change, no RED promotion.
- Output location: `deliverables/dit360_v2/db53_db47f_launch_harness/`.

Required vision check:
- Board must show DB52 precondition status, the 8-anchor command/output table, exact compare/final destination mapping, dry-run stop decision, and explicit no-token/no-remote/no-exact-fetch/no-repair/no-RED labels.

Result summary: DB53 accepted only `db47f-token-free-launch-harness-dry-run-only`; see the 2026-06-04 DB53 block at the top of `agent/progress.md`. CPU/local script `scripts/phase3/db53_db47f_launch_harness_dryrun.py` produced `deliverables/dit360_v2/db53_db47f_launch_harness/db53_db47f_launch_harness_manifest.json` plus board. It created a no-secret argv/output mapping for exactly the 8 DB47f anchors and records expected compare/final names only. It ran no remote/status/exec/A100/network/HF/VGGT/model/_seamroute/renderer/exact fetch or copy/repair/generation/source replacement/source_id_map/permission change/RED promotion. DB53 remains paused because DB52's safe data path is still false; it is launch-risk reduction only, not seam evidence or closure.
