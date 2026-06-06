# Waymo2Panorama — Agent Handoff

> ## ⭐⭐ 2026-06-06 LATEST — DB-76a COMPLETE (all 4 batteries) → DB-77B next
> DB-76a (measurement-only geometry/abstain audit) is **DONE**: ① GREEN overlap 22-37% geometrically wrong + 81% single-source; ② task-band abstain ~3%; ③ forward-stereo recovers only ~1% validated GREEN (forward-only); ④ multi-frame LiDAR = **mid geometry base** (near-ground 3-4× gain → 11-18%, curb/wall 5-20%, <25% bar; clean clean+dense, BMW near-field local smear). **Source-faithful single-centre repair = physical wall, confirmed.** Leader (2026-06-06) re-set the goal to a GENERAL **plausible "make-the-seam-disappear" renderer** and set aside the Bosch source-faithful hard constraint for now. Next = **DB-77B Branch B leashed renderer** (written into `decision_briefs.md`, proposed): geometry owns position + learned owns appearance + real-pixels/geometry/object-protection = the leash; band-confined single-step refiner; `generated_mask`; PLAUSIBLE not source-faithful. Distinction from prior NEG: DiT360 seam-completion = no leash (invents cars), DrivingForward = leash mis-tuned (blur). Code on GitHub: `4c1d875` (db76a ①②) + `3188cb7` (db76b ③④). Boards: `deliverables/db76b_stereo_temporal_coverage/`. **Status: DB-77B ACTIVE; Phase 0+1 leashed IBR explored on L4 — MIXED** — depth-correct IBR CLOSES the seam where geometry is dense (clean facades/road ROIs become continuous → core claim validated), TEARS where sparse/near-field (BMW; root cause = sparse geometry 8-12% + nearest densify → depth wrong at edges). **Awaiting leader direction: (B) 3DGS surfel densify [A100] / (C) band-confined refiner [A100, band 46-57% large] / (D) road-only IBR + facade hard_select [L4].** Code `9963594`; boards `deliverables/db77b_leashed_renderer/`. One active brief at a time.** Runtime secrets: non-repo file only (never repo/log); A100/L4 only with user go.
>
> ## ⭐ 2026-06-06 READ FIRST — LEADER STRATEGY SYNTHESIS & DB76a HANDOFF
> Before acting, read **`agent/2026-06-06-leader-strategy-synthesis.md`**. It is the self-contained synthesis of the 2026-06-06 deep-discussion (two multi-agent literature surveys + GPT Pro + adversarial audit) that LOCKED the direction: main line = source-faithful **multi-center mosaic + provenance/risk/abstain data contract + dual-format (raw canonical + ERP derived view)**; seam = labeled provenance boundary, not always a defect; **abstain is valid**. Theory = two self-owned lemmas (non-identifiability + rank-deficiency), NOT a misread plenoptic citation. Next experiment = **DB76a (measurement only, no RGB repair)**: GREEN reliability via leave-one-camera-out render-back + abstain-mass + forward-stereo + ring-temporal coverage, with pre-registered thresholds + kill criteria. DB75 stays permanently `presentation_only`. DB76a is specified but **not yet opened as a brief**; no remote/GPU run since DB75; no active brief.
>
> ## 2026-06-05 CURRENT DB75 HANDOFF UPDATE - read before older banners
> The latest closed goal produced a viewable seam result, but not a source-faithful repair. The current best viewable seam version is **DB75 full-ERP raw-source seam-band blending fallback**, accepted only as `presentation_only/source_mixed_not_repair`. Output root: `deliverables/layered_target_raycaster/db75_full_erp_source_mixed_fallback/`. Key files: `fetch/02a00399_a000_bmw_source_mixed_candidate.png`, `DB75_full_review_board.jpg`, `DB75_same_roi_comparison_sheet.jpg`, `DB75_vision_verdict.json`, and `DB75_manifest.json`.
>
> DB75 selected variant `soft_r64_a080_g1`: BMW ROI seam energy improved `95.9359 -> 56.2037` (`+41.4%`), global seam energy improved `83.8104 -> 56.6742`, source-mix fraction is `0.1171`, changed fraction is `0.03436`, `generated_mask=0`, and strict secret scan hits are `0`. Vision review says the hard camera-block seam is visibly softened in the road band and right wall/vehicle region, but user follow-up review marks the practical failure clearly: the seam is softened but not connected, the left road patch remains misaligned, the right curb/sidewalk/wall-base remains stepped, and the black BMW/SUV region still shows overlap/ghosting. Pixels in the seam band are source-mixed and slightly softened; this is not single-source truth, not target-surface alignment, not a general algorithm success, not Bosch training-ready, not source-faithful repair, not geometry repair, and not RED promotion.
>
> DB74 immediately before it is completed and rejected as repair: temporal/multiframe raw-source candidates were generated, but `temporal_selected_fraction=0.0`, `temporal_any_valid_fraction=0.001147`, and the operator map was effectively empty. DB72 source-label optimization and DB73 ground-candidate labels are also diagnostic/rejected as seam repair. Do not continue by tuning source-label/ground/temporal weights unless a fresh brief brings new evidence or a different operator family.
>
> Current route status: no active brief is open after DB75. If continuing the general algorithm, the next useful direction should be a fresh decision brief for source-backed local alignment / operator eligibility / geometry-consistency source selection over full ERP, with per-pixel source/provenance sidecars and protected-mask vetoes. A fallback presentation branch is allowed only with an explicit brief and honest `presentation_only` classification. Every new experiment still requires `decision_briefs.md` first, one active brief only, then `progress.md` plus README/plan/handoff sync. Never write runtime URL/token/HF token/Bearer/endpoint JSON or secret-like values into repo artifacts, logs, prompts, or shell output.
>
> ## 2026-06-04 CURRENT EGSR STATE - read before older banners
> Current execution is anchored to `Waymo2Panorama` Waymo-style data/artifacts/calibration/ROIs, not a generic AV2 assumption. DB32 `s40` is the Bosch-facing presentation/handoff candidate with source-sidestep + generated-sky caveats; it is not a fully source-faithful panorama, not a source-faithful ceiling, and not an original `G_bmw_pano` seam repair. `G_bmw_pano` is only the classic BMW failure / diagnostic reference and has been visually rejected as the default repair base.
>
> DB43 Source-Faithfulness Gate and DB44 EGSR dispatcher are accepted. DB45 Geometry foundation evidence audit is paused after DB45k accepted VGGT pose/reflection coordinate audit **diagnostic-only** evidence. DB45j produced real official VGGT inference and saved `pose_enc`, decoded cameras, preprocessing mapping, Sim(3), and DB25/DB41 residual tables. DB45k then confirmed that documented camera-from-world center extraction still fails the no-reflection contract, reflected fits are non-admissible, translation-column improvement is only an undocumented convention-conflict diagnostic, official-center pairwise rig-shape error remains material, and all target ROI rows remain no-promotion; DB41 lower-right remains known LiDAR support `0.000`. DB45 accepts no target-surface geometry evidence, no metric ego truth, no source-faithful repair permission, no permission-state change, and no RED promotion. Do not continue VGGT residual patch-on-patch, rerun inference, or reinterpret residuals as permission. DB47 source/frame candidate mining has completed its current CPU/local existing-artifact source-selection review stack: DB47a accepted only `source-selection-inventory-only`, DB47b accepted only `source-selection-threshold-replay-only`, DB47c accepted only `source-selection-visual-accounting-only`, DB47d accepted only `source-selection-exact-review-pack-only`, and DB47e accepted only `source-selection-final-candidate-review-existing-artifacts-only` evidence. DB47e reviews only `a105`, `a200`, and `a204`; confirms `a200` as the current source-sidestep base for the existing DB32 `s40` Bosch-facing handoff candidate; keeps `a204` as an exact final-eligible alternate not selected; keeps `a105` compare-only hold; and preserves the other 7 DB47d missing-exact holds. DB47e performs no full scan, exact asset fetch, repair, generation, source replacement, `source_id_map`, permission change, or RED promotion, and it accepts no source-faithful or original-G repair. DB49a is completed as `bosch-data-contract-inventory-only`; DB49b is completed as `sidecar-starter-pack-partial-only`, creating only partial DB32 `generated_mask`, `unknown_or_abstain_mask`, and `risk_map` sidecars from existing sky-mask/out-of-FOV/DB41 evidence; DB49c is completed as `source-id-map-feasibility-inventory-only`, finding no complete per-pixel `source_id_map` artifact for the exact DB32 lineage and creating no map; DB49d is completed as `source-map-instrumentation-only`, adding default-off `_seamroute.py` provenance sidecar export for future exact reruns but creating no DB32 map; DB49e is completed only as `exact-lineage-source-map-rerun-preflight-only` and is paused before rerun because the local target log is absent and no secure runtime secret source is available. DB50 Phase0 is completed as `egsr-operator-readiness-existing-artifacts-only`: all 29 DB44 components were reviewed, with 0 executable new source-faithful repair targets, 0 executable LPAM targets, 0 RED promotions, 3 presentation-only, 2 source-sidestep-only, 1 already-satisfied keep control, 1 existing BEV caveated control, and 22 abstain/reject components. DB51 is completed as `egsr-target-source-pair-acquisition-queue-only`: it creates no repaired ERP and ranks DB47f fixed-universe exact source-selection closure as the next seam-quality route if secure runtime/data preconditions are satisfied; it also keeps DB50b LPAM/local alignment blocked until fixed raw/source-pair evidence and protected-structure checks exist. DB49c records DB28/DB41 camera labels as ROI diagnostic/count evidence only, DB34 source preservation as not an owner map, and DB49d sidecar support as future reproducible support only, not a DB32 owner artifact until an exact lineage rerun validates it. DB49 creates no repaired image, no generated pixels beyond existing DB32 sky caveats, no permission-state change, and no RED promotion; DB32 `s40` remains not uncaveated Bosch training data. DB28/DB32 remain source-sidestep/current-handoff evidence only, not original-G seam repair and not a source-faithful ceiling. DB25/DB41 remain `RED/abstain`; DB41 lower-right remains zero-LiDAR abstain; DB36/DB40 remain generated fake-geometry rejects.
>
> DB47f fixed-universe exact source-selection closure preflight is completed as `fixed-universe-exact-closure-preflight-only`, DB56 completed the exact asset closure step, and DB57 completed the exact-candidate visual review with no candidate promotion. The fixed 8 DB51 gaps (`a201`, `a209`, `a210`, `a211`, `a031`, `a038`, `a040` compare+final, plus `a105` final) now have `15/15` required exact compare/final assets under DB28 paths, but none visually displaces `a200`/DB32. This is source-selection exact evidence only: no final panorama is newly selected, no source-faithful local seam repair/source replacement/generation/`source_id_map`/permission change/RED promotion occurred, and DB32/G/DB41/DB25 claim boundaries remain unchanged. DB47f should now pause unless a fresh brief brings genuinely new source-selection evidence.
>
> DB52 DB47f secure-runtime/data intake contract is completed as `secure-runtime-contract-only` and paused. It did not use the chat-pasted A100 tunnel JSON or HF token as secrets, did not read token values, and ran no remote/status/exec/A100/HF/VGGT/model/network/exact fetch/repair/generation/source replacement/`source_id_map`/permission change/RED promotion. It records that the only approved runtime inputs are a `COLAB_URL`/`COLAB_TOKEN` process env pair or a non-repo runtime secret file (`W2P_RUNTIME_SECRET_FILE` or documented default non-repo locations), or replicated local target data. Current in-process booleans remain false for env runtime pair, approved non-repo runtime secret source, and local target log, so `closure_batch_allowed_now=false`. DB47f remains the next seam-quality route only after that safe data path exists.
>
> DB53 DB47f token-free launch harness dry-run is completed as `db47f-token-free-launch-harness-dry-run-only` and paused. It records the no-secret argv/output mapping for exactly the 8 DB47f anchors and expected compare/final assets, but safe data path remains false and it ran no remote/status/exec/A100/HF/VGGT/model/`_seamroute.py`/exact fetch or copy/repair/generation/source replacement/`source_id_map`/permission change/RED promotion. This is not seam evidence or closure. Do not add more infra-only layers; the next DB47 action should be the actual bounded closure batch after approved env/non-repo runtime secret source or local target data exists.
>
> DB54 DB47f local exact-asset recovery audit is completed as `local-exact-asset-recovery-audit-only` and paused. It scanned bounded local artifact roots plus zip member names for the fixed DB47f 15 required compare/final assets (`a201`, `a209`, `a210`, `a211`, `a031`, `a038`, `a040` compare+final, plus `a105` final) and found `0` local file matches and `0` zip-entry-only matches. It ran no remote/status/exec/A100/HF/VGGT/model/dataset scan/`_seamroute.py`/renderer/zip extraction/image copy/exact fetch/repair/generation/source replacement/`source_id_map`/permission change/RED promotion. The DB47f gaps are therefore not hidden in current local artifacts. Do not repeat local recovery or add more DB47f infra-only layers; the next DB47 action still requires approved env/non-repo runtime secret source or local target data, then the actual bounded closure batch.
>
> DB55 EGSR O3 photometric polish acceptance audit is completed as `egsr-o3-photometric-operator-acceptance-audit-only`. It accepts O3 only as `source-derived bounded photometric polish` for T1/YELLOW-GREEN low-structure photometric seams. The audit used existing risk-gated local Y evidence only: 14-anchor mean seam dY improvement mean `17.71%`, p95 improvement mean `5.39%` with weak p95 cases disclosed, changed fraction mean `0.034`, and max Y delta `9.10`. It ran no raw-data load/remote/status/exec/A100/HF/VGGT/model/dataset scan/seamroute/renderer/generation/source replacement/`source_id_map`/permission change/RED promotion. O3 does not repair geometry, DB41/DB25, original G/A1/BEST, or make DB32 source-faithful; it is a bounded photometric sub-operator only.
>
> DB56 DB47f exact closure batch execution is completed as `accepted_exact_closure_assets_complete`. It used an approved process-env runtime source for exactly one `/status` + one `/exec` fixed 8-anchor `_seamroute.py` batch; job `55a0c9f7f40a4af9979f73dc3073532e` completed `state=done`, `exit=0`. Because the executor log tail truncated the result JSON marker, the script then used `--fetch-only` against the existing completed job and deterministic remote paths; it did not submit a second `/exec`. The sanitized manifest/board show `15/15` required assets present, hard checks PASS, and secret scan hits `0`; endpoint/token values are absent from repo artifacts. DB56 is not a final candidate selection, not source-faithful/original-G repair, not `source_id_map`, not DB41/DB25 repair, not RED promotion, and not uncaveated Bosch training data.
>
> DB57 DB47f exact-candidate visual review is completed as `db47f-exact-candidate-visual-review-only` with no candidate promotion. It reviewed exactly the 8 DB56 anchors from existing exact assets only and selected no new final candidate. `a201/a209/a210/a211` are held as near-duplicates with no clear visual win over `a200`/DB32 and no DB32 lineage; `a031/a038/a040` are rejected for relaxed context/lighting shift; `a105` is rejected for different context/no clear win. No remote/model/rerun/repair/source replacement/`source_id_map`/RED promotion occurred. Keep `a200`/DB32 as the current caveated source-sidestep handoff base and stop DB47f patch-on-patch.
>
> DB58 is completed as **accepted CPU-local preflight abstain/no-repair**. It tested whether existing VGGT/raw-camera/LiDAR/flow evidence could justify a raw-camera-backed local warp/composite for the DB25 longline ROI `[850, 420, 1650, 720]` on `02a00399-3857-444e-8db3-a8f58489c394` / anchor `0`. The answer under current artifacts is no: failed gates are `vggt_pose_coordinate_admissibility` and `target_surface_lidar_flow_support`; diagnostic/blocking gates are `raw_owner_uv_preflight` and `source_id_and_sidecar_support`. No remote/status/exec, A100, network, new VGGT inference, seamroute rerun, renderer, warp/composite, generation, source replacement, `source_id_map`, RED promotion, or permission change occurred. `A1_view_none` and `G_bmw_pano` remain diagnostic visual references only under DB58; directly repairing A1/G requires a fresh decision brief with raw-camera source truth, same-ROI before/after, protected masks, and explicit diagnostic/source-faithful claim boundaries. DB41 right/lower-right remains a negative control/abstain boundary unless a separate brief brings new evidence.
>
> DB59 is completed as **accepted CPU-local preflight diagnostic/no-promotion/no-repair**. It tested whether VGGT can supplement A1/G-style Google/Meta geometry/visibility/occlusion evidence for `A1_view_none` and `G_bmw_pano`, but stopped before A100/VGGT/model action. Frozen ROI set is DB25 longline `[850, 420, 1650, 720]` as primary diagnostic target plus DB41 `right_roi` and `lower_right_roi` as negative controls only. Raw 7-camera BMW anchor `0` is the only source-truth input; A1/G are diagnostic display references only and VGGT may not run on pano pixels. Current failed gates are `vggt_pose_coordinate_admissibility` and `target_surface_lidar_flow_support`; blocked gate is `source_id_map_and_protected_masks`. Existing DB45f official VGGT raw-anchor owner-UV evidence already covers the frozen DB25/DB41 rows, while DB45k keeps coordinate/reflection non-admissible. No remote/status/exec, A100, network, new VGGT inference, repair, generation, source replacement, `source_id_map`, RED promotion, permission change, or chat-pasted secret use occurred.
>
> DB60 is completed as **ungated presentation-only VGGT-prior quick-look / rejected as repair**. It used existing DB45f official VGGT DB25 heatmap grids as a soft prior to make fixed-ROI A1/G candidates, intentionally ignoring evidence gates only for visualization. It ran CPU/local only: no remote/status/exec, A100, network, new VGGT inference, DiT/FLUX/prompt generation, inpainting, seamroute rerun, source replacement, `source_id_map`, RED promotion, permission change, or secret use. Vision result is weak: A1 remains visibly seamed with only subtle low-frequency attenuation, and G gets a wavy sidewalk/curb distortion. Keep DB60 artifacts under `deliverables/dit360_v2/db60_vggt_ungated_quicklook/` as diagnostic stress-test evidence only; do not treat them as source-faithful, raw-camera-backed, fixed A1/G/BEST/DB32, Bosch-ready, or permission-changing.
>
> DB61 is completed as a **fresh-A100 ungated A1/G quick-look / rejected as repair**. It no longer reuses DB45f result JSON as geometry input: after the runtime source was updated through a non-repo secret file, DB61 reached the Colab A100 endpoint, first exposed a missing remote `vggt` module, then bootstrapped official `facebookresearch/vggt`, ran `facebook/VGGT-1B-Commercial` on the raw 7-camera BMW anchor `0`, and produced DB25-only owner-UV sampled VGGT metadata plus A1/G fixed-ROI quick-look outputs. Hard checks pass: `a100_job_submitted=true`, `new_vggt_inference=true`, `db25_only_remote_scope=true`, `uses_db45f_result_json_as_evidence=false`, and secret scan hits `0`; the local HF token was not forwarded after the safety reviewer rejected sending it through the Cloudflare endpoint. Vision result is weak: A1 keeps the long seam with only low-frequency attenuation, and G keeps/gets sidewalk-curb waviness. DB61 is presentation-only diagnostic stress-test evidence, not source-faithful/raw-camera-backed repair, not a fixed A1/G/BEST/DB32, not `source_id_map`, not RED promotion, and not Bosch training data.
>
> DB62 is completed as a **VGGT point-guided raw-camera source composite / rejected as repair**. It directly tested the user's A1/G question after DB61 by using official VGGT `world_points`, `depth`, `depth_conf`, and `world_points_conf` to score raw-camera source candidates inside the fixed DB25 ROI, then compositing only raw-camera ERP slab pixels into A1/G diagnostic candidates. Outputs are under `deliverables/dit360_v2/db62_vggt_raw_source_composite/`. Hard checks pass: `new_vggt_inference=true`, `raw_camera_pixels_used=true`, `vggt_point_confidence_scoring_used=true`, `db25_only_remote_scope=true`, and secret scan hits `0`. Vision rejects the result as repair: soft alpha changes only sparse high-margin islands and leaves the long seam visible; hard selected raw source crop exposes blocky/vertical wall and boundary discontinuities. DB62 is raw-camera-backed diagnostic/presentation-only evidence, not source-faithful, not an accepted repair, not a fixed A1/G/BEST/DB32, not `source_id_map`, not RED promotion, and not Bosch training data.
>
> DB63 is completed as a **VGGT high-confidence component-gated raw-source probe / fragmented sparse no-repair**. It used only existing DB62 VGGT alpha, source-label, and hard raw-source outputs to test whether DB62 contained a smaller continuous repairable island inside DB25; no remote/A100/new VGGT inference occurred. The strict component gate kept only `2` components covering `0.021025` of ROI, amplified alpha mean was `0.011252`, and A1/G ROI p95 delta stayed `0.0`. Vision shows small wall/sign/near-wall islands, not a continuous longline seam repair surface. DB63 is diagnostic/presentation-only evidence; do not continue this branch by simply amplifying DB62/DB63 alpha.
>
> DB64 is now **running but paused after Phase4b z-visibility cause evidence** as the next route after the external GPT Pro audit. The audit's suggested `DB58 / LTR-v0` name is intentionally renumbered because DB58 is already closed. DB64 adopts **LTR-v0 layered target-raycaster** as the next main research route: raw cameras + calibration + LiDAR/depth/VGGT diagnostics where admissible -> target-ray projection -> source visibility/z-buffer checks -> sidecars -> cause/repairability maps. Phase0 CPU/local preflight found reusable LTR components but no local target data. Phase1 used exactly one secure-runtime CPU Colab `/status` plus one `/exec` Drive data preflight and confirmed fixed BMW target and clean-control raw/calib/LiDAR data are present on Drive. Phase2a was blocked by missing `av2`; Phase2b installed only the explicit project dependency `av2>=0.3` and ran the two-case LiDAR-zbuffer diagnostic on CPU. Phase2 remains rejected as repair: its LiDAR RGB-copy variants changed <1% of ERP, dropped NCC, and showed sparse/blocky wall/object patches rather than a continuous BMW seam repair. Phase3 ran one CPU Colab sidecar-only instrumentation job for the same two cases and created complete sidecar maps under `deliverables/layered_target_raycaster/db64_ltr_v0/phase3_sidecar_instrumentation/`; Phase3 is target-ray evidence only, not repair/source truth/semantic layer truth. Phase4a split Phase3 unknown into cause/repairability maps under `deliverables/layered_target_raycaster/db64_ltr_v0/phase4_cause_map/`. Phase4b then ran exactly one CPU Colab z-visibility cause instrumentation job and created complete raw projection/z-buffer maps under `deliverables/layered_target_raycaster/db64_ltr_v0/phase4b_z_visibility_cause/`: `z_cause_primary_map`, `camera_geom_valid_count_map`, `camera_zbuffer_hit_count_map`, `camera_z_mismatch_count_map`, `camera_visible_count_map`, `z_residual_min_cm_u16`, and `z_repairability_map`. Aggregate seam z-cause means are `no_target_surface_support=0.5394`, `no_camera_geom_valid=0.0193`, `no_raw_zbuffer_support=0.2109`, `z_mismatch_or_occlusion_conflict=0.0373`, `single_visible_source=0.0855`, and `multi_source_visible=0.1077`. BMW's extra weakness is mainly `no_target_surface_support +0.0664` and lower `multi_source_visible -0.0331`, not z mismatch or camera geometry; among supported-but-not-visible BMW seam pixels, `82.38%` are no-zbuffer support, `12.62%` are z conflict, and `5.00%` are no camera geometry. The latest GPT Pro review recommends the next candidate as `DB64 Phase5a - Continuous target-surface evidence preflight`: multiframe LiDAR fusion or conservative dense/local surface evidence first, with minimal protected-mask veto inside the same sub-scope, then only later conservative layer fitting and abstain-aware RGB renderer. Phase5a is **not opened, not authorized, and not run** in this handoff; the next agent must write a proper DB64 Phase5a brief/extension before any A100/CPU Colab/script action. `a100_needed_now=false` at this checkpoint, secret scan hits `0`, and no VGGT/model/generation/source replacement/RED promotion occurred. Do not promote Phase4a/Phase4b repairability maps to RGB repair permission. HardSelect++ is only the conservative control/product baseline; geometry-conditioned synthesis remains presentation-only and not sensor truth.
>
> Next DB45 work, if any, must bring new official-source convention evidence in a fresh bounded brief; do not continue VGGT residual patch-on-patch, rerun inference, or reinterpret large residuals as permission. DB47f should now pause after DB57 no-promotion unless a fresh brief brings genuinely new source-selection evidence; do not rerun DB56/DB57 or patch the same candidates. Next DB49 exact rerun is allowed only after a secure runtime secret source is available (`COLAB_URL`/`COLAB_TOKEN` env vars or non-repo runtime secret file) or the full target data path under `data/argoverse2/val` is replicated locally; do not use chat-pasted tokens in shell commands or artifacts. Next DB50 work requires a fresh target-specific sub-brief with raw/source-pair evidence, protected-structure checks, maps, and same-ROI before/after vision; do not patch-on-patch after Phase0 found no executable new local repair target. Until then, keep DB49e paused or package a human-facing handoff while keeping missing fields explicit; do not narratively fill `source_id_map`, `unknown_or_abstain_mask`, or `risk_map`. Do not reopen prompt-only DiT/FLUX ground/curb/lane/right-line repair, and do not continue any direction after kill criteria without archiving it in `progress.md`.

> ## 🔑 2026-06-03 — DRIVE / ACCOUNT MIGRATED to 1jingshuo1 (infra; read once)
> Workspace `MyDrive/koi_waymo2pano_colab/` is **OWNED by `panq@usc.edu`**, now **shared (Editor) + shortcut'd into `1jingshuo1@gmail.com`'s My Drive** → the Colab path `/content/drive/MyDrive/koi_waymo2pano_colab/` is **identical under both accounts; scripts unchanged.** Operate under **1jingshuo1** going forward (Colab login + Claude Drive connector both = 1jingshuo1; verified 2026-06-03: mounts + **write-OK** + repo at `be82ba7`). The **Colab tunnel** (`url`+`token` in `runtime/active_url.json`) is **independent of the Drive account**. This is **ACCESS migration, NOT ownership** — data still physically lives in panq. ⚠️ **If panq/USC access is ever lost** (cross-org usc→gmail ownership transfer is BLOCKED → must COPY): copy `results/dfwd_av2_finetune_v1/` (finetuned depth_net/gs_net, ~1.3 h A100, irreplaceable) + `cache/df_env_torch22cu121.tar.zst` (2.6 GB `df` env) into 1jingshuo1's OWN Drive; `data/argoverse2/` is the PUBLIC AV2 dataset (re-downloadable); don't touch `secrets/`. Full note → `README.md` §"Account & Drive access".

> ## ⏩⏩⏩ 2026-06-02 — PROTOCOL + ROADMAP (read this, then the 2026-05-30 banner below)
> **Work protocol is now 4 living docs** (see `README.md`): `README.md`=rules · `handoff.md`=consensus+roadmap (this) · `progress.md`=facts (each product names GitHub/local/Drive) · **`decision_briefs.md`=experiment GATE** (open a brief with Kill criteria + Max scope BEFORE any new direction). A **3-Location rule** (GitHub/local/Drive) is enforced in README.
> **Current roadmap (two parallel routes toward a near-perfect PLAUSIBLE seam — no hallucinated objects):** Route **A** = Google-Street-View-style plausible multi-center (top card: **Difix-on-band**, band-confined 3DGS fuse + single-step refiner, far field byte-exact L1). Route **B** = DiT360 + real-evidence leash (top card: **EPI-Mix**, epipolar+LiDAR reference attention — depth re-weights attention, never warps → dodges the E2–E6 wall). **▶ CURRENT LEAD = DB-11** (Street-View-style COARSE-PLANE LiDAR-DIBR program, Route A): fit ground+facade planes to LiDAR → reproject all 7 cams to ERP at true (plane) depth → graph-cut seam + blend → light flow residual → inpaint holes. It sequences DB-01 (=A0 kill-test, CPU), DB-05 (eval), DB-06 (A4); DB-04 = PIVOT fallback; DB-02/DB-03 = heavier alternative position mechanisms. **Starting A0 on CPU** (cheap de-risk before any GPU). All directions parked as briefs → `decision_briefs.md` (DB-20260602-01..11; DB-10 selection-family = REJECTED).
> **New-agent onboarding**: full-project arc + infra + rules in `agent/HANDOFF-PROMPT-full-project-2026-06-02.md` (original 8 methods → Xinhan Waymo meeting → PLAUSIBLE reframe → other-agent seam work → Colab+Drive → current routes). Every historical exploration path is indexed under `notes/archived/` (`notes/archived/README.md`).
> **Process rule (hard-won)**: discuss direction JOINTLY before building; vision-check EVERY image; never re-charge a NEG family (E2 depth-reproject, copy-selection). 

> ## ⏩⏩ 2026-05-30 — READ THIS FIRST (supersedes the 2026-05-29 + 2026-05-28 banners below)
> **Two big shifts today: (1) a positive deliverable confirmed; (2) a reframe that may dissolve the whole impossibility.**
>
> **① POSITIVE (vision-confirmed): E1.5 reliably fixes the PHOTOMETRIC seam.** `E1.5` = seam-confined low-frequency multiband blend (`code/waymo2panorama/blending/seam_confined.py`, mode `hard_seamconfined`, lowfreq-cutoff 5). On FAR seams it removes the color/brightness step cleanly; far field stays BYTE-IDENTICAL to L1. It does NOT fix the near-field PARALLAX cut (e.g. the BMW-car seam ≈ unchanged) — but "fix color, not geometry" is a real, shippable sub-result. See `deliverables/e1_seam_confined/seams_ABD_montage.png` (A/B far seams = fixed; D car seam = unchanged).
>
> **② REFRAME (today's discussion — possibly the most important insight): we were locked into "single optical center + GEOMETRIC FAITHFULNESS", which is physically impossible for non-co-located cams at near range.** Verified that **Google Street View itself is NOT single-center and NOT geometrically faithful** — it is 7 non-co-located cams (same as us), has parallax, and HIDES seams via local optical-flow warp + seam routing through low-texture + favorable far-field statistics; it openly "tolerates residual seams". The industry standard is a **multi-center mosaic forced into visual agreement**, i.e. PLAUSIBLE not faithful. **User has set the bar to PLAUSIBLE (look like a coherent real street; no hallucinated salient objects).** Under this bar, most of this sprint's NEGs were the FAITHFUL bar over-rejecting good-looking results. We have an edge Street View lacks: **real LiDAR depth** (they only estimate it from flow).
>
> **③ Koi's DiT360 OUTPAINT experiment — DONE 2026-05-30, vision-checked, NEG-for-data / POS-as-demo.** Koi asked to keep ONLY the central forward patch (~5%) and let DiT360 outpaint the whole surrounding 360 ("看看效果"). Ran 4 cases (2 BMW imgs × sector/window) on A100. Verdict: DiT360 makes a coherent, photoreal, full-sphere 360 from the tiny anchor (good capability demo), BUT (a) the preserved center is a **boxy lighting-mismatched rectangle** (sunny anchor vs generated overcast), (b) the 95% is **entirely fictional** (invented a different British-looking city), (c) it **hallucinates salient objects** (cars/van/signs) → the disqualifier for Bosch data. Re-confirms: DiT360 = generative pano baseline, NOT a source-faithful 360 reconstructor. Files: `deliverables/koi_outpaint_center/` (`RESULTS.md`, `koi_outpaint_COMPARISON.jpg`). `far_weight=1.0` (the pre-run note's "0" was wrong — it anchors the center).
>
> **③+ VERIFIED 2026-06-02 (Koi pushback "跑错了吗 / 格式不对吗"):** NOT a bug; our format is CORRECT; the input↔output divergence is BY DESIGN. Official released outpaint I/O (project page Petra/Sydney) ALSO regenerates surroundings (input 75–88% black → output filled ~100%); issue #21 maintainer says divergence is EXPECTED for the training-free pipeline (fix = specific spatially-constrained prompt). **base = FLUX.1-dev (~12B DiT; non-commercial license → flag for Bosch); outpaint = TRAINING-FREE (Personalize-Anything inversion + token replacement); the LoRA was NOT trained on outpaint** (only text-to-panorama). The only knobs we set off-official: tau=5 (vs 50) + a generic prompt. **NEXT (queued, needs A100): re-run center-only with official tau=50 + scene-specific Miami prompt — more coherent but STILL invented, not faithful.** Full detail + evidence + I/O quantification: `agent/progress.md` 2026-06-02 entry (workflow `wf_8106e8a0-537`).
>
> - **Today's work, full detail**: `agent/progress.md` top entry (2026-05-30). Autonomous diffusion-sprint log: `agent/EXPLORATION-seam-synthesis-sprint.md`. Paper sparks (20 papers, 3 clusters): `notes/archived/BRAINSTORM-2026-05-30-paper-sparks.md` (archived). Auto-generated plan (one candidate, NOT committed to): `notes/archived/PLAN-plausible-360-synthesis.md` (archived). **Discussion package for the user**: `agent/方向讨论_2026-05-30/` (`00_方向总览.md` + `方法与论文_汇总.xlsx`).
> - **STATE**: in a CALM JOINT direction-discussion with the user (no charging at a method). The convergent recipe across all papers = geometry (3DGS/LiDAR) owns POSITION, diffusion owns APPEARANCE, a real reference (neighbor pixels / LiDAR point-render) is the LEASH against hallucination. **Open question being discussed: do we formally drop "geometric faithfulness" and adopt the Street-View-style "plausible multi-center + hide-the-seam (now LiDAR-guided)" target?** If yes, many prior NEGs (E3 flow-warp, DiT360 seam-fill) deserve re-evaluation under the relaxed bar.
> - The 2026-05-29 ladder (E0/E1/E2) below is still valid as the IN-BAND-fusion attempt; its conclusion = in-band faithful fusion is hard (E2/E3/E5/E6/#3 all NEG). The 2026-05-28 section is older NEG-ladder history.

> ## ⏩ 2026-05-29 PIVOT — (superseded by the 2026-05-30 banner above; kept for detail)
> The "make the seam invisible" exploration below is SETTLED as dead (triple-confirmed). The project has **pivoted** to a new validated direction and is executing it:
> **Keep rigid L1 hard_select as the globally-clean geometry backbone (far field byte-identical → cannot warp); fuse ONLY the ~7 near-field seam strips.** Executed as an E0→E1→E1.5→E2 ladder.
> - Full session-by-session detail: `agent/progress.md` (top entries, 2026-05-29).
> - **Clean experiment→result→files archive** (start here): `agent/experiments/2026-05-29-E0-ruler-and-E1-seam-fusion.md`.
> - State as of 2026-05-29: E0 ruler validated (`relative_warp`), E1/E1.5 run on Colab (far field byte-identical; near-field seam proven to be PARALLAX, not photometric). The 2026-05-28 section below is the prior-era seam-exploration log, kept for the NEG ladder.

**Updated**: 2026-05-28 (Latest: metric parallax-budget mapping was tested after RGB+DA-V2 superpixel source coherence, dense-depth-aware DP seam routing, dense Depth Anything V2 metadata, LiDAR depth-visibility, sparse stereo v5, semantic object-coherent hard_select, same-frame/temporal ground-plane replacement, DiT360 v14, region-coherent seam v3a/v3b, and DiT-as-oracle source selection. It is POS as impossibility/risk evidence: LiDAR-supported seam pixels have p90 parallax 17.65 px, 23.92% are >=10 px, and 7.14% are >=20 px. Depth remains useful as seam-risk metadata and physical framing, not as a local seam/source-selection solver. Risk-gated local Y repair remains the stable POS optional polish.)
**Maintainer**: rotating Claude sessions; user is Qi Pan (panq@usc.edu), advisor Koi Chen

---

## 🎯 LATEST FINDING (2026-05-28) — read this FIRST

**6 layers of "no-depth / no-DL" seam fixes tested plus sparse stereo displacement external validation, semantic object-coherent source switching, same-frame ground-plane seam replacement, temporal ego-motion ground replacement, LiDAR depth-visibility metadata, dense DA-V2 depth-edge metadata, dense-depth-aware DP seam routing, RGB+DA-V2 superpixel coherence, metric parallax-budget mapping, and the DiT360 generative seam-completion path pushed through fixed masks, adaptive masks, post/soft/evidence compose, multi-seed, fidelity-budget compose, low-frequency-only harmonization, Poisson/gradient-domain gated compose, DiT-as-oracle source selection, and v14 tri-map latent clamp. Seam-local alignment is safer than full OF, and DP seam-routing is a clean hard-select seam ablation, but neither beats L1 hard_select. Region-coherent/component-only seam repair is safer than DP routing but mostly reverts to hard_select. Sparse stereo remains too sparse to move panorama seams. Semantic instance masks are safer than generation but cover too little of the road/facade/lane seam. Same-frame and temporal one-plane ground replacement introduce real geometry/evidence, but both create pasted/driven strips and cannot handle vehicles/facades/poles. Depth metadata is useful as risk annotation but not a local seam solver: sparse LiDAR flags high-parallax/unknown strips, dense DA-V2 marks plausible depth boundaries, depth-aware seam routing reduces dY only by sacrificing source fidelity, superpixel coherence makes larger but still wrong source-swap blocks, and parallax-budget maps quantify that many supported seam pixels physically require 10-20+ px cross-camera displacement. DiT360 can fill masked seam strips, but the visually smoother variants rely on nontrivial source rewriting; using DiT only as a source-selection oracle still creates blocky source swaps, and v14 latent clamp still has the same raw-vs-fidelity trade-off. New source-evidence seam confidence maps are POS as diagnostics, and risk-gated local Y repair is POS as conservative seam-color polish: it reduces luminance seams without claiming to solve impossible geometry.**

| # | Method | Result |
|---|---|---|
| 1 | L0 — AV2 calibration BA refine | ❌ NEG. Bias ~1.3 px (negligible vs 46 ERP px parallax) |
| 2 | L1 — Single fixed sphere R={∞,30,10,5,3} m | ❌ Trade-off, no R fits all depths (Δ = baseline/R - baseline/D) |
| 3 | L1 — Multi-R per-pixel v1 (Y-diff argmin) | ❌ Frankenstein doubling at object boundaries (pedestrian) |
| 4 | L1 — Multi-R per-pixel v2 (HDR + 9×9 NCC + 11px median R) | ❌ Marginally better than v1, still doubled, worse than L1 hard_select |
| 5 | L2 — Seam-first local ECC alignment on hard_select | ⚠️ MIXED / weak NEG. No BMW fragmentation, but no clear visual improvement over hard_select |
| 6 | L2 — DP seam-routing v2 on hard_select | ❌ NEG / weak MIXED. Moves seam path but still cuts visible cars/people/lines; not better than hard_select |
| 7 | Stage B — DiT360 masked seam completion + post/evidence/fidelity/lowfreq compose | ❌ NEG as main solver. Adaptive masks and fidelity-budget compose confirm the trade-off: smoother raw/loose outputs rewrite evidence; safe/lowfreq outputs preserve structure but do not fix geometry |
| 8 | Meta / diagnostic — source-evidence seam confidence map | ✅ POS as metadata. High structure-risk is sparse (<1% seam band on 3 anchors), high color-risk is broader (~8-10%); useful for filtering/loss weighting/future region-coherent decisions |
| 9 | L2 — risk-gated local Y seam repair | ✅ POS as conservative polish. Mean seam dY reduction 10-19% on 3 anchors and 18.19% mean on fresh11; no new ghost/warp; does not fix geometry |
| 10 | Temporal — ego-motion ground seam probe | ❌ NEG as solver. Uses nearby frames + AV2 ego poses, but ground-only strips drag cars/pedestrians/facades and drop NCC hard_select -> temporal_repair (BMW 1.0000 -> 0.8583; fbee 0.9956 -> 0.8015; 0bae 0.9997 -> 0.7588) |
| 11 | L1/L2 — same-frame raw ground-plane seam layer | ❌ NEG. Directly targets road/lane seam mismatch but one road plane inserts rectangular blocks; NCC drops hard_select -> ground_strict (BMW 0.9969 -> 0.9188; fbee 0.9874 -> 0.9488; 0bae 0.9934 -> 0.9319) |
| 12 | L2 — semantic object-coherent hard_select | ⚠️ weak MIXED / mostly NEG. YOLOv8x-seg masks protect a tiny fraction of seam pixels; fbee seam dY improves slightly, BMW/clean worsen or stay neutral; road/facade/lane seams remain |
| 13 | A2/v5 — sparse stereo displacement on YOLO ghosty anchor | ❌ NEG. fbee anchor85 has edge-object score 17 but only 201 stereo pts total; A2v5 overlap mean L1 barely changes 31.80 -> 31.72 |
| 14 | Depth — LiDAR visibility/risk metadata | ✅/⚠️ POS as metadata, weak NEG as repair. LiDAR covers 49.26% seam band; 28.60% of supported seam is high depth-risk. Depth-veto Y repair is safer but weaker (mean dY reduction 10.85% vs source-gate 15.94%) and does not solve geometry |
| 15 | Depth — dense DA-V2 edge metadata | ✅/⚠️ POS as diagnostic baseline, weak NEG as repair. Dense depth risk marks 4.81% seam band and correlates with RGB structure risk (0.42-0.51), but dense-depth-veto Y repair is weaker (10.07% mean dY reduction) and still does not solve geometry |
| 16 | Depth — dense-depth-aware DP seam routing | ❌ NEG. dY improves 22.63 -> 8.70, but source-fidelity NCC collapses hard_select/RGB route/depth route = 0.9925/0.8779/0.8233; depth cost moves seams to smoother but wrong local paths |
| 17 | Region — RGB+DA-V2 superpixel coherence | ❌ NEG. Larger SLIC regions avoid jagged DP paths but create blocky source swaps; dY improves 22.63 -> 13.25 while NCC drops 0.9925 -> 0.9131 |
| 18 | Meta/Depth — metric parallax-budget map | ✅ POS as impossibility/risk evidence. On LiDAR-supported seam pixels, p90 parallax is 17.65 px; 23.92% are >=10 px and 7.14% are >=20 px. Low correlation with 2D color/structure risk shows RGB-only seam costs miss physical parallax risk |

**Latest practical pivot**: after DiT360 v9, the best route is not "make seam invisible at all costs"; it is **L1 hard_select + seam risk/confidence metadata**. `scripts/phase3/seam_confidence_map.py` produces color-risk, structure-risk, and reliability-risk maps from the original source slabs/weights only. On BMW, pedestrian/object, and clean far-field anchors, high color-risk spans ~7.6-9.6% of seam band, but high structure-risk is sparse (~0.6-0.9%). This supports a defensible paper/Bosch story: broad low-risk seams can be color-corrected or tolerated; sparse high-structure regions should be flagged, downweighted, or handled by future object/region-coherent methods rather than hallucinated.

**Latest depth-visibility test**: `scripts/phase3/depth_visibility_seam_probe.py` projects the nearest AV2 LiDAR sweep to ERP and uses it only as seam metadata, not as a rendering surface. On BMW, fbee pedestrian/object, and clean far-field anchors, LiDAR supports 49.26% of the seam band and marks 28.60% of supported seam as high depth-risk. Adding this as a veto to Y-only seam repair reduces mean dY less than the source-risk-only repair (10.85% vs 15.94%) because it refuses to polish high-parallax strips. Visual evidence in `deliverables/depth_visibility_seam_probe/depth_visibility_three_anchor_compact_review.jpg`; full Drive outputs in `results/depth_visibility_seam_probe_v1/`. Conclusion: depth is useful for risk maps/filtering/loss weighting, but sparse LiDAR veto is not a geometry stitcher.

**Latest dense-depth test**: `scripts/phase3/dense_depth_edge_seam_probe.py` runs Depth Anything V2 Small on each raw camera, projects the relative depth maps into ERP slabs, and uses depth-edge / normalized depth-disagreement only as a seam veto. On the same three anchors, dense depth risk marks 4.81% of the seam band and correlates with RGB structure risk (0.42-0.51), meaning it mostly confirms existing image-edge evidence rather than adding source geometry. Dense-depth-veto Y repair reduces mean dY by 10.07%, below source-risk-only repair at 15.94%. Evidence in `deliverables/dense_depth_edge_seam_probe/dense_depth_edge_three_anchor_compact_review.jpg`; Drive `results/dense_depth_edge_seam_probe_v1/`. Conclusion: dense monocular depth is a useful diagnostic baseline, but still not a seam solver without layer/visibility/source synthesis.

**Latest depth-aware routing test**: `scripts/phase3/test_depth_aware_seam_routing.py` adds dense DA-V2 risk as an external cost to DP seam routing. It reduces mean seam dY from 22.63 to 8.70, but the real metric goes the wrong way: mean NCC pano-vs-winning-camera drops from hard_select 0.9925 to RGB route 0.8779 and depth route 0.8233. Visual review shows jagged seam paths/source swaps. Evidence in `deliverables/depth_aware_seam_routing/depth_aware_route_three_anchor_compact_review.jpg`; Drive `results/depth_aware_seam_routing_v1/`. Conclusion: depth-edge routing optimizes a local smoothness proxy, not source-faithful geometry. Do not tune this route further unless replacing local DP with real layered visibility/source synthesis.

**Latest superpixel coherence test**: `scripts/phase3/test_superpixel_depth_coherent.py` uses SLIC on RGB+DA-V2 relative depth and assigns seam-split superpixels to one source camera. It changes only 0.328% of pixels and improves mean seam dY from 22.63 to 13.25, but mean NCC drops from 0.9925 to 0.9131 and visual review shows rectangular/pasted blocks. Evidence in `deliverables/superpixel_depth_coherent/superpixel_depth_three_anchor_compact_review.jpg`; Drive `results/superpixel_depth_coherent_v1/`. Conclusion: region-level source selection is a better abstraction than row-wise DP, but SLIC regions are not true scene layers and still do not beat hard_select.

**Latest parallax-budget test**: `scripts/phase3/parallax_budget_map.py` projects the nearest AV2 LiDAR sweep to ERP and computes the expected ERP displacement of the same 3D point between adjacent physical camera centers. This is not a repair method; it quantifies the physical seam difficulty. On BMW, fbee pedestrian/object, and clean far-field anchors, LiDAR supports 50.23% of the seam band. Among supported pixels, p90 parallax is 17.65 px, 23.92% are >=10 px, and 7.14% are >=20 px. Per-anchor p90 is 20.04 / 16.23 / 16.67 px. Correlation with 2D source/structure/color risk is low, so RGB-only seam costs miss much of the physical parallax risk. Evidence in `deliverables/parallax_budget_map/parallax_budget_three_anchor_compact_review.jpg`; Drive `results/parallax_budget_map_v1/`. Conclusion: POS as paper/Bosch risk evidence, not a stitcher.

**Latest optional repair**: `scripts/phase3/seam_risk_gated_color_repair.py` uses the confidence map to adjust only Y-channel luminance near low-structure-risk seam regions. It keeps hard_select camera assignment, leaves chroma untouched, and makes no geometry/warp/DL changes. On three primary anchors it reduces mean seam dY by 10.17%, 18.73%, and 18.93% with changes limited to ~3.1-3.6% of pixels. On fresh11, 11/11 anchors improve mean seam dY; aggregate mean reduction is 18.19%, changed pixel fraction is 3.47%, and no obvious new artifact appears in compact review. Treat this as an optional local L2 seam-color polish, not geometry repair.

**Latest source-selection exhaustion test**: `region_coherent_seam.py` v3a/v3b and `dit_oracle_source.py` were tested on BMW, pedestrian/object, and clean far-field anchors. Region-coherent DP seam routing still creates jagged source swaps; component-only repair is safer but mostly reverts to hard_select. DiT-as-oracle uses the DiT360 r008/tau5 raw output only as a target while copying final pixels from real camera slabs. It still falls below hard_select (safe NCC 0.9488/0.9154/0.9337 vs hard_select 0.9999/0.9938/0.9990) and creates blocky source swaps around cars, pedestrians, trees, lanes, and building edges. Conclusion: do not keep tuning local seam/source selection unless adding genuinely new information such as depth, temporal evidence, or object-level labels.

**Latest ground-plane tests**: `scripts/phase3/test_ground_plane_layer.py` used same-frame raw camera samples on a local road plane; `scripts/phase3/test_temporal_ground_seam.py` used nearby 20Hz AV2 frames and ego poses on a local ground plane. Both are real geometry/evidence routes, not seam-cost tweaks, but the one-plane assumption fails visually. Same-frame ground replacement creates rectangular pasted blocks and drops NCC from hard_select to ground_strict (BMW 0.9969 -> 0.9188, fbee 0.9874 -> 0.9488, 0bae 0.9934 -> 0.9319). Temporal replacement inserts dragged strips, smears pedestrians/cars/facades, and reduces source-fidelity NCC from ~1.0 to 0.8583/0.8015/0.7588. Evidence in `deliverables/ground_plane_layer_compact_mid_review.jpg`, Drive `results/ground_plane_layer_v1/`, and `deliverables/temporal_ground_seam/three_anchor_v1/`. Conclusion: ground/temporal may still matter, but only with layered/object/depth reasoning; one-plane replacement is not the solver.

**Latest semantic object test**: `scripts/phase3/test_semantic_object_coherent.py` projected YOLOv8x-seg vehicle/person masks from raw camera views into ERP and forced near-seam object support to one source camera. It changes only 0.05-0.21% of pixels. fbee seam dY improves slightly (28.07 -> 26.74), but BMW worsens (15.40 -> 16.80) and clean far-field is neutral/slightly worse. Visual output is almost hard_select with occasional mask-boundary source switches. Evidence in `deliverables/semantic_object_coherent_compact_review.jpg` and Drive `results/semantic_object_coherent_v1/`. Conclusion: semantics are useful as risk metadata/veto, but object-mask-only repair does not solve road/facade/lane mixed-depth seams.

**Latest sparse-stereo validation**: `scripts/phase3/score_ghost_yolo_v2.py` selected fbee anchor85 as the most ghost-likely stride-5 anchor (edge-object score 17). `run_wide_baseline_stereo.py` + `run_l1_sparse_disp.py` v5 produced only 201 final 3D points across 7 adjacent pairs; effective displacement anchors are 0-3 per affected camera. Overlap alignment barely changes (mean L1 31.80 -> 31.72, Pearson 0.812 -> 0.813), and visual review is nearly identical to plain multiband. Evidence in `deliverables/sparse_stereo_v5_fbee_a085_review.jpg` and Drive `results/sparse_stereo_v5_fbee_a085/`. Conclusion: sparse-displacement post-warp remains exhausted; do not spend more time scaling it.

**Latest DiT360 refinement**: the first r040/tau20 DiT360 run was visual NEG. Follow-up r004/r008/r012/r020 seam masks plus invalid-region outpainting showed that hard post-compose restores all white-mask pixels exactly but can reintroduce hard boundaries. v7 small-mask/tau sweep found r008/tau5 visually most plausible but still weak; v8 evidence gating reduced suspicious edits but reverted toward hard_select; v9 multi-seed did not rescue it. v10 added source-risk adaptive masks (`adaptive_lowstruct_r006`, `adaptive_color_r008_guardstruct`, `adaptive_expand_histruct_r024`), fidelity-budget compose, and low-frequency-only harmonization on BMW. v11 generalized to `fbee355f_a095` and `0bae3b5e_a030`: raw preserve MAE remains high (4.46-4.88), with visible vertical smears / structure rewriting; lowfreq suppresses edge-region edits but remains cosmetic. v12 residual-multiband and v13 OpenCV Poisson/gradient-domain gated compose reduced preserve MAE to 0.05-0.28 and reduced edge/boundary edits by an order of magnitude, but the visual verdict is still not a geometry fix: `poisson_y_safe` is almost hard_select, while RGB/loose variants bring back blur/smear around cars, pillars, lanes, and building edges. v14 tri-map latent clamp (core free, halo soft clamp, far clamp) reduced explicit hard post-compose boundaries but still failed: BMW raw far MAE ~3.4-3.5 and fbee/0bae raw far MAE ~4.1-4.4 show non-seam drift; soft/core compose restores far fidelity (~0.01 MAE) but reverts toward hard_select and leaves the geometry seam. v17 FoV-cropped completion gives a cleaner qualitative 360 band but hallucinates missing scene content, and v18 reference-canvas / alternating-camera masks confirm vanilla DiT360 is prompt+mask panorama inpainting, not source-faithful multi-reference stitching. Treat DiT360 as a paper discussion/baseline or low-frequency/color prior, not a Bosch training-data solver.

**Fundamental diagnosis**: at object/background boundary, foreground (5m) wants R=5m, background (30m) wants R=30m. Per-pixel argmin switches rapidly even with smoothing → cam_A's R=5m slab + cam_B's R=30m slab composited = Frankenstein. **Criterion right, execution can't enforce object-level coherence.**

**Current safest visual baseline**: L1 `hard_select` on AV2 raw. It removes multiband ghost/halo and avoids near-field OF fragmentation. `hard_hdr_of.py` remains an important ablation (+25.3% NCC, -37.7% seam-gap ΔY mean across 5 logs), but do **not** call it unconditional production default after the user visually rejected OF on the BMW near-field case. HDR and OF/local-align should be optional ablations.

**4 paths from here**:
- **Source-evidence confidence maps + gated color repair** — current best forward direction. Keep L1 `hard_select` as the panorama, output seam risk/confidence metadata for Bosch filtering/loss weighting, and optionally apply low-risk local Y seam repair. Evidence in `deliverables/seam_confidence_map/three_anchor_v1/` and `deliverables/seam_risk_gated_color_repair/{three_anchor_v1,fresh11_v1}/`.
- **DiT360 as constrained qualitative baseline** — A100 BMW r040 run succeeded after HF auth, `torchao` upgrade, and VAE tiling. Fixed masks, adaptive masks, post/soft/evidence/fidelity compose, multi-seed, low-frequency-only harmonization, fbee/0bae generalization, residual-multiband compose, and Poisson/gradient-domain gated compose were tested. The useful variants are qualitative/color-prior ablations; none is source-faithful geometry repair. Evidence in `deliverables/dit360_seam_completion/runs_v10_*`, `runs_v11_*`, `runs_v12_residual_multiband/`, and `runs_v13_poisson_gate/`.
- **Object/depth/layer coherence** — plain DP/source selection and one-plane temporal replacement are NEG; only revisit if adding stronger object/depth/semantic coherence, not another hand-tuned low-level cost.
- **Paper pivot to "impossibility framing"** — use calibration / fixed-R / multi-R / local-align NEG evidence to argue no-depth panorama has a ceiling, then propose artifact minimization and confidence maps.
- **Ship hard_select-first baseline** — deliver AV2 raw L1 hard_select (+ optional Y-only HDR where color shift matters) as the conservative Bosch-facing baseline.

**Full details**: `agent/progress.md` top entries + `deliverables/dit360_seam_completion/` + `deliverables/seam_routing_v2/three_anchor_v1_review/` + `deliverables/seam_local_align/three_anchor_v1/`.

---

## 📋 Documentation Rules (user 2026-05-27)

**Living docs (3 only)**: `agent/handoff.md` (this file) + `agent/progress.md` + `agent/README.md`. Write to these, not new mds.

✅ **DO**:
- Experiment results → new entry at top of `progress.md`
- Visual evidence → `deliverables/<topic>/*.png` (PNGs OK, they're not bloat)
- Formal handoff to humans → `deliverables/handoff_to_<name>_<date>.md`
- Paper drafts → `paper/`

❌ **DON'T**:
- Don't create `deliverables/*_FINDING.md` / `*_SUMMARY.md` / `*_PIPELINE.md` (use `progress.md`)
- Don't create per-experiment standalone .md in `deliverables/` (use `progress.md`)
- Don't add files to `deliverables/archived/` or `notes/archived/` — those are read-only history

---

## Recent milestones (5-27 catch-up, read first)

In reverse chronological order — the 8-route TL;DR below is the broader project state from 5-26.

### 2026-05-27 ~23:00 — L1+L2 HDR runs on Xihan's REAL Waymo frame, **color shift solved**
- End-to-end on Colab T4: user accepted Waymo Open Dataset EULA (`panq@usc.edu`) → `gcloud auth login` → `gsutil cp` shard 0 of `gs://waymo_open_dataset_end_to_end_camera_v_1_0_0` to Drive (1.7 GB, 94 s) → parse `end_to_end_driving_data_pb2.E2EDFrame` → 8 cam (K, T_ego_cam, distortion) extracted → run our pipeline.
- Frame `8e737334b520fdd0c04e36f463b2d211-085` (the one Xihan handed us) verified.
- **Output**: `deliverables/xihan/l1_on_waymo/{README.md, compare_4way_thumb.png, l1_hdr_multiband_1024x512.png}` — visually shows our L1 sphere + 8-cam L2 HDR + multiband produces uniform brightness, no over-exposed center cam.
- **2 critical bugs fixed (Waymo-only, no AV2 code path touched)**:
  1. Waymo cam frame (x=fwd, y=left, z=up) ≠ OpenCV convention (x=right, y=down, z=fwd) → added `R_WAYMOCAM_OPENCVCAM` rotation to `T_ego_cam` before sphere_projection.
  2. `hard_hdr_of.py:32-41` `RING_PAIRS` hardcoded for 7-cam AV2 (indices 0..6, no index 7) → on 8-cam Waymo, cam[7] unconstrained → garbage gain. Inline `compute_hdr_gains_waymo8` in runner with 8-cam ring pairs.
- L3 OF chain still 7-cam-bound (ValueError on 8 cams) — color shift already solved by L1+L2; L3 is parallax not color, port to follow.
- New scripts: `scripts/phase3/{parse_waymo_e2ed_frame, run_waymo_e2ed_l1, compare_xihan_vs_l1, compare_waymo_4way}.py`.

### 2026-05-27 ~22:30 — **Seam root-cause investigation conclusion: 4 "no-depth" layers all NEG, empirical impossibility evidence**
Tested 4 layers of fix without explicit depth:
1. ❌ **L0 Calibration refine (BA)** — AV2 bias ~1.3 px global. Negligible vs 46 ERP px parallax.
2. ❌ **L1 Single fixed R** — R={∞,30,10,5,3} all trade-offs, no winner (fbee a95 行人 在 R=5/3 反而更糟).
3. ❌ **L1 Multi-R per-pixel v1** (Y diff argmin) — Frankenstein doubling at object boundaries.
4. ❌ **L1 Multi-R per-pixel v2** (HDR + 9×9 NCC + 11px median R) — marginally better than v1 but still doubled, still worse than L1 hard_select.

**Fundamental diagnosis**: at object/background boundary, foreground (5m) wants R=5m, background (30m) wants R=30m. Per-pixel argmin switches rapidly, even with smoothing → cam_A's R=5m slab + cam_B's R=30m slab composited = Frankenstein. **Criterion is right, execution can't enforce object-level coherence.** Real fix needs MRF/graphcut on R label map OR SAM segmentation-aware OR bilateral filter on R map OR go back to explicit depth (user excluded NeRF/3DGS).

**Empirically confirms what brainstorm derived analytically**: 7-cam different optical centers ⇒ no `2D surface` projection fits all depths exactly. Any "no-depth basic-CV" method has ceiling = L1 hard_select + L2 HDR + L3 OF (current ship, +25.3% NCC). Going beyond needs MRF/segmentation/explicit-depth.

**Code shipped (clean, don't pollute AV2 main)**:
- `code/waymo2panorama/blending/multi_radius_select.py` — both v1 (per-pixel argmin) + v2 (HDR+NCC+medR)
- `scripts/phase3/calibration_check.py` — RANSAC + data F vs calib F comparison
- `scripts/phase3/test_multi_radius_sphere.py` — single-R sweep
- `scripts/phase3/test_multi_r_select.py` — v1 driver
- `scripts/phase3/test_multi_r_select_v2.py` — v2 driver

**Visual evidence (all on `main`)**:
- `deliverables/multi_radius_test/bmw_crop_stack_small.jpg` — 5-row R sweep
- `deliverables/multi_radius_test/02a00399_a000_bmw_crop_hires_q88.jpg` — hi-res sweep
- `deliverables/multi_r_select/fbee355f_a095_bmw_crop_q85.jpg` — v1 NEG evidence (pedestrian doubled)
- `deliverables/multi_r_select_v2/fbee355f_a095_v2_bmw_crop_q85.jpg` — v2 NEG (still doubled)

**Quantitative**: `outputs/calibration_check/{02a00399,0bae3b5e,fbee355f}_v2.json` on Drive.

**Full historical writeup**: `deliverables/archived/CALIBRATION_CHECK_FINDING.md` (CALIBRATION_CHECK_FINDING was moved to archived/ during 2026-05-27 doc consolidation).

**3 paths from here** (user-decided):
- (B') **Substantial fix**: MRF graphcut / SAM / bilateral filter on R map (half day to 1 day)
- (C) **Paper pivot to "impossibility framing"**: write paper around mathematical impossibility + artifact-minimization framework (2-3 hr drafting)
- (D) **Ship as-is**: L1+L2+L3 `hard_hdr_of` already +25.3% NCC, -37.7% seam-gap. Done deliverable for Bosch.

### 2026-05-27 ~17:30 — Xihan handoff: L1 principle + 2 examples + Waymo brighten
- `deliverables/l1_sphere_principle.md` (8 sections) — full L1 baseline math + Waymo porting caveats.
- `deliverables/xihan/l1_examples_panel.png` — 2 representative L1 outputs (clean far-field + ghost near-field).
- `scripts/phase3/brighten_xihan_waymo_panorama.py` — post-hoc joint global Y-gain on Xihan's pre-stitched panorama. **Seam |ΔY| 40.86 → 33.36 = -18%**. CLAHE baseline +14% worse (CLAHE doesn't know cam boundaries).
- `deliverables/handoff_to_xihan_2026-05-27_brighten_and_l1.md` — full 7-section handoff.

---

## TL;DR

Sub-project of the Koi paper line. **Goal (per 5.22 meeting with teammate + Bosch)**: produce **clean 360° ERP panorama dataset** that **Bosch's autonomous-driving world model** can consume — they tested panorama input and it works, but real panorama datasets are scarce/expensive to collect, so we synthesize from existing AV ring-cam logs (AV2 ours, Waymo teammate's). Target venue: **3DV 2026** (main or D&B), advisor Koi Chen.

**Current state (2026-05-26 evening)**:
- **8 stitching routes done + benchmarked** on AV2 raw inputs (L1 sphere `cycle-PSNR 12.34 dB` baseline + L3 Pi3 NEG + 新-A through 新-E). Final Koi deliverable shipped: `deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.{md,pdf}` (13 pages, 11 figures).
- **Stage 2 (T1-T5, 5-25 → 5-26)**: WS1 ship (HDR-Waymo adapter, ego mask, cos⁴ feather, Waymo loader skeleton) ✓. T4 Option B reweight + T5 L1+ORB hybrid all NEG for documented reasons.
- **WS4 D1-D6 (5-26)**: A2 sparse-stereo displacement + B1 disparity-aware graphcut seam shipped + tested. Visual NEG: halos persist. Initially I thought halos were parallax artifacts in L1 baseline.
- **WS4-Diag2 (5-26 evening, KEY FINDING)**: ran decisive A/B experiment — same L1 sphere code, same anchor 60, only varied input (AV2 raw 2048×1550 vs pi3-cache 504×504 letterbox). **AV2 raw output is clean; pi3-cache output has halos**. The "halos" everyone (including the 5.22 prompt) was complaining about were **pi3-cache input degradation** (lanczos resize + letterbox boundary Gibbs ringing leaks into multiband low-freq bands), not stitching pipeline bugs. **7 prior NEG attempts (T4 v1/v2/v3 + T5 v1/v2/v3 + WS4 A2/B1) were misdiagnosed** — they tried to fix the symptom on the wrong root cause. Evidence in `deliverables/parallax_visual_review/smoking_gun_input_is_root_cause.png` (3-row stack, 2048×3170). **Pi3 dependency is no longer needed** for the panorama pipeline.
- **Real artifacts still to address (from 5.22 prompt)**: (a) **2-wheel ghost** in `l1_erp.png` (AV2 raw) — this is REAL parallax artifact in the proper baseline, not the same as the misdiagnosed halo; (b) cylinder white seam traces — likely also pi3-cache rendered, needs re-render with AV2 raw to confirm; (c) **teammate's Waymo color-shift issue** — WS1.1 HDR adapter shipped, ready for teammate to test.
- **新-F VGGT** (4th backbone NEG) — blocked: gated HF repo, user click pending.
- **T13 self-sup Pi3 finetune** — deferred. Probably **no longer relevant** since we don't need Pi3.
- **Paper angle**: original candidate A' Method paper. Now stronger story emerges: "L1 sphere + cos² feather + simple WA on AV2 raw is a clean baseline; 7 'fix-the-halo' attempts (T4/T5/WS4) all failed because the halo wasn't there in the proper pipeline — it was a pi3-cache input degradation artifact. This isolates the parallax + multiband interaction question and ablates 7 candidate fixes."
- **Infrastructure**: agent-colab-direct v0.1.0 active. Works via HTTP curl + Bearer token from Drive's `active_url.json` even when MCP server not registered. 2-hour Stage 2 session ran 3 Colab GPU jobs + 50+ exec calls + 30+ file ops without issue.

**What the next agent should do**:
- **First step always**: read top 3 entries of `agent/progress.md` (WS4-Diag2 root cause + WS4-D6 NEG + earlier stage 2). Then `deliverables/parallax_visual_review/smoking_gun_input_is_root_cause.png` for the visual evidence.
- **If user wants to address real 5.22 prompt items (recommended)**:
  - 2-wheel ghost in AV2 raw L1: parallax-fixing direction needs **depth-aware** approach. Options: (a) re-extract stereo at AV2 raw full-res (not pi3-cache downsampled) and retry WS4 A2/B1 (they may work on dense full-res data); (b) Pi3 forward splat using **AV2 raw input** instead of letterboxed 504×504 (un-letterbox before Pi3, then use depth — Pi3 forward splat NEG result from 5-21 may be partially attributable to letterbox too); (c) different paradigm (4D Gaussian, neural radiance).
  - Cylinder white seam traces: re-render `route_cylinder_vs_sphere.png` with AV2 raw to verify whether traces are pi3-cache artifacts or geometric (cos² fast decay at v_max=±45°). If geometric, the cos⁴ feather fix already shipped (WS1.3) should help — re-render with that on AV2 raw to verify.
  - Teammate Waymo color-shift: HDR adapter is at `code/waymo2panorama/color/hdr_waymo_adapter.py`. Ship to teammate.
- **If user wants paper writeup**: the new framing (7 NEG → root cause = wrong input) is a sharper ablation story. Start with `agent/progress.md` 5-26 evening entry, weave in pre-existing 8-route benchmarks.
- **If user wants to run a Colab task**: notebook + HTTP-direct via `agent-colab-direct`. MCP server not registered in some sessions — fallback to raw HTTP curl with token from `MyDrive/.../runtime/active_url.json` works fine.

**What the next agent should NOT do**:
- **Don't continue adding "halo fix" attempts on pi3-cache output** — root cause is pi3-cache input itself.
- **Don't pursue WS4 D8 (C1 RAFT), D9 (hybrid), D10** as previously planned — those plans assumed halo was pipeline issue.
- **Don't keep Pi3 as the L1 input source** — Pi3 was useful as a depth backbone for L3, but pi3-cache letterbox images degrade L1 output. Use AV2 raw via `AV2RingLoader.load_synced_frame()`.

---

## Start here (new agent onboarding, ~30 min read)

In this order:

1. **`agent/progress.md`** — single source of truth, every track's "怎么做 / 结果 / Deliverables / Status / Next" block. Top of file is the latest entry. Read top 5-10 blocks for current state.
2. **`deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.md`** — what we told the advisor. Has the 8-route summary table (§1), per-route details (§1.1-§1.8), downstream demos (§2), external baselines (§3), pending GPU routes (§4), final ranking table (§5).
3. **`C:\Users\14294\.claude\plans\snug-shimmying-wave.md`** — full plan v6.1 with strategic context (why we pivoted from system integration to stitching-only), 16 routes table, sequencing, decision gates G1-G5, risk register, contracts for each subagent.
4. **`notes/archived/2026-05-15-brainstorm-survey.md`** — original brainstorm survey (CV concepts, dataset comparison, related work). Archived reference; concepts still apply.

---

## Project chain context (Koi Chen paper line)

```
AV2 / Waymo / nuScenes multi-cam (THIS PROJECT)
        │
        ▼ stitch 7-cam → 360° ERP
ERP panoramic video + 3D cache
        │
        ▼ (downstream, paused per v6.1 pivot)
04-pantheon360  (3D-aware 360° video diffusion, CVPR 2026)
        │
        ▼
360 world simulation / Cosmos-Predict / Argus
```

We control the **first step only**. Downstream (Pantheon360 / GEN3C / Argus / Cosmos) is paused per v6.1 strategic pivot — user has paper deadlines on stitching methodology, downstream is post-publication work.

---

## 8 stitching routes (current snapshot)

| ID | Name | Verdict | Headline metric | Code |
|---|---|---|---|---|
| **L1** | Sphere baseline + 5-band Laplacian | ✅ Strong baseline | cycle-PSNR **12.34 ± 1.31 dB** (10 anchors) | `code/waymo2panorama/projection/sphere.py` |
| **L3** | Pi3 forward-splat | ❌ Structural NEG (paper Section 4) | -3.15 dB vs L1, 10/10 lose | `code/waymo2panorama/pipeline/lift_and_project.py` |
| **IPM** (T14) | Ground plane hybrid | ⚠️ Marginal +0.05 dB | +0.20 dB on ground-only mask | `code/waymo2panorama/projection/ipm_ground.py` |
| **新-A** | Cylindrical L2 | ⚠️ Coverage gain only | +24.9 pp coverage, cycle ~flat | `code/waymo2panorama/projection/cylinder.py` |
| **新-B** | Graph-cut seam | ✅ Visual win | -12.4% seam-band \|grad\| (4/4 anchors) | `code/waymo2panorama/blending/graphcut_seam.py` |
| **新-C** | IPM multi-region (ground+sky+building) | ✅ Method win | **+0.20 dB on ground** (4× T14), building branch deferred | `code/waymo2panorama/projection/ipm_multi_region.py` |
| **新-D** | Wide-baseline stereo (邻 cam) | ⚠️ Partial (5/7 pairs) | 44 inlier 3D pts/pair median, sparse | `code/waymo2panorama/stereo/wide_baseline_stereo.py` |
| **新-E** | HDR cross-cam compensation | ✅ Drop-in preprocess | **-18% lum gap** (4 anchors mean) | `code/waymo2panorama/color/hdr_gain_estimate.py` |

Plus **3 external NEG**: OmniStitch (-6.67 dB), Apple Depth Pro (2.84× worse abs_rel), Temporal Pi3 (no improvement).
Plus **3 downstream demos** (paused): ViPE SLAM, GEN3C, Panacea+ (modality NEG).

---

## Open decisions (G3 v6 gate, awaits user/Koi)

| Decision | Default if no input | Trigger to flip |
|---|---|---|
| **Paper angle**: A' Method / B-with-C / C Negative-only | A' Method (3 positives stackable) | Koi reviews PDF + says "not enough positives, go B" |
| **Run 新-F VGGT** (4th backbone NEG) | Skip (gated repo blocker + low marginal value) | User requests HF access + says "do it" |
| **Run T13 Pi3 self-sup finetune** (5-6 day A100) | Skip (high cost, paper angle A' doesn't need it) | Koi says "need at least 1 backbone-level win" |
| **Target venue**: 3DV 2026 main / D&B / CVPR workshop | 3DV 2026 main (angle A' fits) | Reviewer feedback on draft v0 |

---

## Currently in-flight

- **Colab runtime**: not currently running. The old agent-colab-queue worker scripts have been deleted from this repo. Next Colab task: open `notebooks/runtime.ipynb`, Run All, get `✓ READY` + tunnel URL. The notebook lives in this repo and is reproducible.
- **New 新-F VGGT attempt**: still gated on HF access (`facebook/VGGT-1B-Commercial`). The old `jobs/phase3-new-f-vggt-*.json` specs are now historical artifacts (worker no longer pulls them). When user gets HF access, re-attempt via the new framework: `mcp__colab-direct__exec(cmd=["bash", "scripts/<new-f-script>.sh"])` instead of submitting a job spec.
- **T13 Pi3 self-sup finetune**: still deferred pending Koi feedback. Same migration story — when greenlit, run via new framework.
- **Paper draft v0**: not started. Gated on Koi 拍板 about paper angle.

To unblock 新-F: user clicks "Agree and access" at https://huggingface.co/facebook/VGGT-1B-Commercial. After that, the next agent reframes the work as a `colab-direct` task (no jobs/ json needed).

---

## Video deliverables (added 2026-05-23, paper supplementary)

7 of 8 routes have 5-second mp4 videos at anchor 60 area (start_sec=3.0, 100 frames @ 20 fps, 1024×2048 ERP). 新-D wide-baseline stereo is NOT videoable (outputs sparse 3D points, not dense ERP).

All on Drive at `MyDrive/koi_waymo2pano_colab/outputs/<route>_video/<log_id>/<route>_video.mp4`:

| Route | Drive folder | Size | Wall time | Direct view URL |
|---|---|---|---|---|
| L1 baseline (HDR `--also-baseline`) | `hdr_video/.../baseline_video.mp4` | 17 MB | (parallel with HDR) | `13jNNJCV8FjMGMUbqo03I47ZMTTTJBpro` |
| L3 Pi3 forward-splat | `l3_video/.../l3_video.mp4` | 24 MB | 7 min | `1PZEvwFoCeQUc0oatymgYL7cw0XyF-AcL` |
| T14 IPM ground hybrid | `ipm_hybrid_video/.../ipm_hybrid_video.mp4` | 13 MB | 7.7 min | `1ozuDgzl4g-Anxg1qHJTq8m6liQrSDkn4` |
| 新-A 柱面 (L2) | `cylindrical_video/.../cylindrical_video.mp4` | 26 MB | 5.7 min | `1YvkYTW2dEHrBkH0wKTmxl2s9UoZwIs1z` |
| 新-B Graph-cut seam | `graphcut_video/.../graphcut_video.mp4` | 17 MB | 16 min | `1aA9iw8RTLFTOXFwGYFYAwBFFIvHbwa2s` |
| 新-C IPM multi-region | `ipm_multi_region_video/.../ipm_multi_region_video.mp4` | 13 MB | 12 min | `1O5dAAq6MASxUtFyebuzrPN3fK6FTbLoX` |
| 新-E HDR cross-cam | `hdr_video/.../hdr_video.mp4` | 15 MB | 16 min | `1Ln-BV6zU_FwQ7yzdY2_e9Y0X3V74-cUA` |

**Video driver scripts** (all at `scripts/run_*_video.py`, follow same pattern as `run_l1_baseline.py`):
- `run_l3_video.py` — Pi3 → Sim(3) → forward-splat per frame
- `run_cylindrical_video.py` — sphere → cylinder, multiband blend
- `run_graphcut_video.py` — L1 + apply_graphcut_seams (Boykov-Kolmogorov min-cut)
- `run_hdr_video.py` — L1 + per-cam 6-param gain+bias (Huber LS); `--also-baseline` flag writes parallel L1 mp4
- `run_ipm_hybrid_video.py` — Pi3 + detect_ground_from_pi3 + ipm_project_ground + sphere fallback
- `run_ipm_multi_region_video.py` — Pi3 + ipm_project_multi_region (ground + sky routing; building branch off by default)

**To generate more videos** (different anchor / log / duration): copy a `run_*_video.py` script, edit args (or just pass CLI flags), write a new `jobs/phase3-*-video-*.json` spec. Worker picks up automatically. Pattern documented in `jobs/README.md`.

---

## Infrastructure (must-know)

### agent-colab-direct (current — use this for ALL new Colab work)

- Framework repo: <https://github.com/QiPan-Ronnie/agent-colab-direct> v0.1.0, public, MIT
- Entry: `notebooks/runtime.ipynb` (generated; regenerate via `colab-direct generate-notebook` if config drifts)
- Setup cell does: pip install agent-colab-direct → mount Drive → clone this repo → start Flask executor + Cloudflare quick-tunnel + heartbeat (writes URL+token to `<workspace>/runtime/active_url.json` every 5s)
- Agent invokes MCP tools: `mcp__colab-direct__exec(cmd=[...])`, `__shell`, `__write_file`, `__read_file`, `__status`, `__list_jobs`, `__get_job`, `__wait_for_job`, `__kill_job`, `__shell_reset`, `__heartbeat`, `__refresh_url` (12 tools total)
- `exec` is auto sync↔async: short cmds return inline with `{mode: "sync", stdout, exit_code}`; long cmds return `{mode: "async", job_id}` and the agent can `wait_for_job(id)` later
- `shell` is a persistent bash session (pexpect): `cd`/`export`/`source venv/bin/activate` stick across calls (SSH-like UX)
- For mid-task resume on long batch jobs: decorate functions with `@cd.checkpointed(unit_id_fn=..., storage_dir=drive_path)`. On disconnect+resume, completed units skip via `.done` marker files
- See `[[agent-colab-direct-framework]]` memory for details
- MCP server registration: `colab-direct mcp` + `COLAB_DIRECT_RUNTIME_DIR=<local Drive>/koi_waymo2pano_colab/runtime` — example `.mcp.json` snippet in `agent-colab-direct/docs/migration_from_acq.md`

### agent-colab-queue (FROZEN — do NOT submit new jobs)

- Pip package stays installed locally (`uvx --from .../tools/agent-colab-queue`) — kept ONLY for reading old `jobs/*.json` archive
- **Do NOT call `mcp__agent-colab-queue__submit_job`** for new work
- Old `jobs/*.json` (86 files) preserved in this repo as audit archive — they document what we tried on the old infra
- Old worker scripts (`scripts/cell_acq_worker.py`, `cell_worker_bootstrap.py`, `runtime_filter.py`, `code/waymo2panorama/utils/drive_queue.py`) deleted 2026-05-24
- See `[[agent-colab-queue-framework]]` memory (marked frozen)

### Drive workspace
- Root: `MyDrive/koi_waymo2pano_colab/` (panq@usc.edu)
- Cached fileIds in `[[drive-folder-ids-koi-waymo2pano]]` memory
- Subdirs: `outputs/phase3/` (all run results), `data/argoverse2/` (4 logs, ~32 GB), `cache/` (tar'd conda envs for fast restore), `worker/heartbeat.json`, `results/<job_id>.json`

### GitHub
- Repo: `git@github.com:QiPan-Ronnie/Waymo2Panorama.git`
- Direct push to main is **authorized** (per memory `[[feedback-direct-push-main-waymo2pano]]`) — no PR review needed for this repo
- Worker pulls from main on every poll

### Code layout
```
code/waymo2panorama/
  data_io/         AV2 ring loader (RING_CAMS_7, calib parsing)
  projection/      sphere / cylinder / ipm_ground / ipm_multi_region
  blending/        multiband (Laplacian pyramid) + graphcut_seam
  stereo/          wide_baseline_stereo
  color/           hdr_gain_estimate
  alignment/       sim3_align (Umeyama)
  pipeline/        lift_and_project (L3 forward-splat) + depth_bayesian_fusion
scripts/
  phase2/          single-anchor evals (eval_pi3_vs_lidar, eval_cycle_consistency)
  phase3/          multi-anchor batch evals + each new route driver + run_pi3_multi_anchor + run_vggt_multi_anchor
  phase4/          (future) T13 finetune scripts will go here
deliverables/
  handoff_to_koi_w2_2026-05-21_v6cpu_done.{md,pdf}    THE Koi handoff
  _make_*.py / _render_pdf*.py                         figure / PDF tooling
  images/route_*.png                                   actual figures used
  learning_plan.md                                     user's own CV roadmap
agent/
  progress.md          single source of truth, append-only log
  handoff.md           this file
notes/
  new_{a,b,c,d,e,f}_*.md  per-route research/design docs
  t13_*.md                T13 finetune design doc
jobs/*.json              Colab queue specs (worker pulls these)
outputs/phase3/*         all run results (mostly mirrored to Drive)
```

---

## Defensive lessons (HARD-WON — read before any Colab job)

These cost us hours/dollars. Don't repeat:

1. **`set -e -o pipefail` + `cmd | head -N` = death**. `head` closes stdin after N lines, sends SIGPIPE upstream, pipefail kills the script. Use `tail -N` instead. Bit us on T11 GEN3C install (exit 141, 5 sec into install).
2. **Conda activate hates `set -u`**. `activate-gcc_linux-64.sh` references unbound `SYS_SYSROOT`. Wrap `conda activate` in `set +u`/`set -u`. Bit us on T11 v1.
3. **HF gated repos need explicit access click**. `facebook/VGGT-1B-Commercial` 403'd on us even with HF token — must click "Agree and access" on the HF model page. Bit us on 新-F.
4. **Worker sorts jobs alphabetically, NOT by created_at**. If you submit `bbb-install` then `aaa-eval`, eval runs first. Name jobs with prefix order in mind.
5. **Tar conda env to Drive after heavy installs**. Saves ~50 min on Colab reconnects. See `[[feedback-colab-tar-env-to-drive]]` for the zstd-tar restore pattern.
6. **No way to remotely shut down Colab runtime**. Worker is a Python process inside Colab; we can stop submitting jobs but A100 stays alive until user manually disconnects. Tell user explicitly when failing.
7. **Pi3 vs VGGT input pipeline difference**: Pi3 takes raw (B, V, 3, H, W) tensor; VGGT takes file paths via `load_and_preprocess_images()`. The driver `run_vggt_multi_anchor.py` saves letterbox images to a tmp dir then feeds paths to VGGT.
8. **`eval_cycle_consistency.py` applies Sim(3) alignment via `pose_<cam>.npy`**. For Pi3 this converts world→ego. For VGGT/Depth Pro we use AV2 truth extrinsics so save `pose_<cam>.npy = T_ego_cam` to make Sim(3) collapse to identity.
9. **Pi3 repo path on fresh Colab session is `/content/Pi3`, NOT `/content/01-pi3-Pi3`**. Use the 3-URL clone fallback: `git clone yyfan2014/Pi3 || yyfz/Pi3 || yyfan2014/Pi3-clean`. Bit us on L3 video v1 + v2 (wasted 3 min total). Pass `--pi3-repo /content/Pi3` explicitly in job spec.
10. **API signature drift between similar modules**: `detect_ground_from_pi3()` does NOT take `conf` kwarg even though `segment_regions_from_pi3()` does. Don't copy-paste kwarg patterns between modules — always `grep -n "^def "` the target module first. Bit us on T14 video v1 (crashed first frame).
11. **Path resolution from `scripts/` vs `scripts/phase3/`**: scripts at `scripts/` level need `here / "../code"` (1 level up) and `here / "../../01-pi3/..."` (2 levels up). Scripts at `scripts/phase3/` need `"../../code"` and `"../../../01-pi3/..."`. Don't blindly copy the path constants between locations. Bit us on L3 video v1.
12. **Python `print()` is block-buffered when piped via `tee`** (default 4-8 KB buffer). Long Pi3 model loads can take 30-60s with NO output visible in `tail -f run.log`. Use `print(..., flush=True)` or `PYTHONUNBUFFERED=1` env var for real-time progress on long-running scripts.
13. **Worker idle ≠ A100 free**. Worker Python process can be alive + polling but A100 runtime is still allocated and billed (~$3-4/h). Always tell user to disconnect Colab runtime when work finishes, even if worker is "just idle".
14. **Drive API metadata cache delay**: `modifiedTime` from `search_files` can be 30-60s stale. If checking worker liveness, do 2-3 reads spaced ~30s apart before declaring worker dead.
15. **Colab FUSE write vs Drive backend sync** (NEW, 2026-05-24 smoke test): a file written to `/content/drive/...` from Colab is **instantly visible inside the Colab kernel** (FUSE mount), but propagation to Google's actual Drive backend can lag **minutes**. During agent-colab-direct smoke test, `active_url.json` was confirmed written by the executor's heartbeat thread, but Drive MCP `search_files` returned empty for >2 minutes. Don't tight-loop on Drive search after a Colab write; either read the file via Colab's left file panel (FUSE, instant), open a separate Colab notebook tab to cat it, or just wait 60-90s. Captured in memory `[[feedback-drive-colab-sync-delay]]`.
16. **agent-colab-direct daily-use validation pending** (2026-05-24): Smoke test passed end-to-end on Colab CPU, but the first real research task using the new framework hasn't happened yet. Expect rough edges — when you (next agent) hit one, take notes for v0.1.1 polish. Don't silently work around bugs; flag them to the user.

---

## Memory references (Claude session memory)

Located in `C:\Users\14294\.claude\projects\D--BaiduSyncdisk-2024-to-future-koi-chen\memory\` (auto-loaded into every new session — agent reads `MEMORY.md` index then pulls relevant entries):
- `[[agent-colab-direct-framework]]` — **active** framework: 12 MCP tools, 6 optimizations, smoke-tested 2026-05-23
- `[[agent-colab-queue-framework]]` — **FROZEN** as of 2026-05-23; do NOT submit new jobs through it
- `[[drive-folder-ids-koi-waymo2pano]]` — cached Drive fileIds (root, results, heartbeat); still valid
- `[[feedback-direct-push-main-waymo2pano]]` — user authorizes direct push to main for THIS repo
- `[[feedback-colab-tar-env-to-drive]]` — zstd-tar pattern for heavy installs (Apex/TE etc.); now used inside agent-colab-direct's `cache.py`
- `[[feedback-prefer-robust-frameworks]]` — engineer fixes over workarounds (drove the agent-colab-direct refactor)
- `[[feedback-drive-colab-sync-delay]]` — **NEW 2026-05-24** Colab FUSE write ≠ Drive backend sync; don't tight-loop on Drive search

---

## Tooling

- Python 3.10/3.12 (Colab compat)
- AV2 API: `pip install av2`
- Pi3: `../../../01-pi3/code/official/Pi3` (local clone) + HF `yyfz233/Pi3X` (open)
- VGGT: `git clone https://github.com/facebookresearch/vggt` + HF `facebook/VGGT-1B-Commercial` (GATED)
- Pytorch: bf16 native on A100, fp32 fallback
- HF auth: token at `C:\Users\14294\.huggingface\token` (local only, NEVER send to Colab/agent)
- GitHub SSH key: `C:\Users\14294\.ssh\id_ed25519_github_new` (local only, NEVER send to Colab/agent)
- Compute: Colab Pro A100 (user's account, panq@usc.edu)
