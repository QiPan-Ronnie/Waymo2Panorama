---

You are taking over the **Waymo2Panorama** project for Qi Pan / Koi Chen / Bosch.

Read this prompt first, then read the living docs below before making claims or running experiments. This project has repeatedly failed when an agent saw one promising visual result, ran too far, and only later discovered that the route was not source-faithful. Your first job is to understand the full project state, then discuss next directions with the user.

## 0. Required Reading Order

1. `agent/README.md`
   - Project operating protocol.
   - The Experiment Decision Gate is mandatory: before any new experiment direction, open a brief in `agent/decision_briefs.md` with kill criteria and max scope.
2. `agent/handoff.md`
   - Current high-level handoff state.
3. `agent/progress.md`
   - Permanent experiment record. Latest entries are at the top.
   - For this takeover, pay special attention to DB27-42, plus the older seam/ghost/depth/DiT entries.
4. `agent/decision_briefs.md`
   - Active / pending queue only. Completed DBs are summarized here as pointers but archived in `progress.md`.
   - As of this handoff, there is no active repair brief. DB42 is the current accepted synthesis.
5. This file:
   - Use it only as a map. Trust the living docs and images over this summary if there is any mismatch.

After reading the four living docs, respond to the user in 3-5 sentences:
- what you read,
- what you understand the current state to be,
- what direction you think is most worth discussing next,
- and what you will not do without opening a new decision brief.

Do not start experiments immediately.

## 1. One-Sentence Goal

Build a clean, plausible, source-faithful 360 ERP panorama from non-co-located surround cameras for Bosch/world-model data. The output should look closer to a Google Street View / Meta 360 style panorama, but it must not hallucinate salient driving evidence such as cars, people, poles, lane markings, curb geometry, or building structure.

The hard physical problem is multi-camera parallax and occlusion. AV2/Waymo surround cameras are not a single optical center. Near and mid-range objects cannot be both perfectly single-center and strictly source-faithful without depth/visibility/source redundancy. When evidence is missing, abstain is better than fake repair.

## 2. Current Honest State

Current Bosch handoff candidate:

```text
deliverables/dit360_v2/db32_generated_sky_harmonize_v2/db32_generated_sky_harmonize_s40.png
```

DB32 `s40` is accepted only as the current handoff candidate with caveats. It is a source-sidestep based on the cleaner DB28/a200 source plus object-gated sky completion/harmonization. It is **not** a fix of the original `G_bmw_pano` / `A1_view_none` / `BEST_bmw_pano` seam.

Current final synthesis:

```text
deliverables/dit360_v2/db42_seam_decision_handoff/db42_seam_decision_handoff_board.jpg
deliverables/dit360_v2/db42_seam_decision_handoff/db42_seam_decision_handoff_report.md
deliverables/dit360_v2/db42_seam_decision_handoff/db42_seam_decision_handoff_manifest.json
```

Do not overclaim:

- Do not say the original G/A1/BEST right-ground seam is fixed.
- Do not say DiT360 solved ground seam repair.
- Do not treat object-gate PASS as sufficient for seam quality.
- Do not treat a visually smoother ghost/blend as safe for Bosch/world-model use.
- Do not edit ground/curb/white-line regions with prompt-only inpainting under current evidence.

## 3. What Happened in the 2026-06-04 Goal

The previous agent completed a full seam-focused goal and committed the work through DB42.

Recent commits:

```text
2bcc7cc Accept DB42 seam handoff synthesis
0851f1c Close DB41 right-line evidence gate
c31215e Close DB40 A1 seam replay
e730f27 Record DB40 A1 keepout replay
3cd3ab7 Open DB40 A1 seam mask alignment
86751da Reject DB39 v14 trimap replay
7c17b21 Accept DB38 Bosch handoff candidate
b777654 Close DB37 seam mechanism audit
5198a90 Reject DB36 DiT redline mask
20a9bc2 Reject DB35 seam donor patch
37be310 Accept DB34 current best QA pack
e74424f Reject DB33 local sky boundary harmonization
```

The user later shut down the A100. Current follow-up can be CPU-only unless a new brief justifies GPU.

### DB33: local Cube/rectilinear sky-boundary harmonization

Tested a bounded CubeComposer-inspired CPU-only local sky color-field correction on DB32/DB29 sky core. Rejected. Source pixels stayed byte-exact, but useful strengths either did nothing or introduced sky halos / diagonal color bands. Keep rectilinear/cube-face review as a diagnostic tool, not as a local sky harmonization solver.

### DB34: current-best QA for DB32 `s40`

Accepted DB32 `s40` as current-best reference. Object gate passed. Non-core/source pixels are byte-exact. Caveats remain: foreground black car, lower out-of-FoV black band, residual sky panel discontinuity.

### DB35: seam-first target board and donor diagnostic

Re-centered on the user-priority G/A1/BEST seam issue. Built same-ROI comparison board and donor patch diagnostics from BEST/A1 into G. Rejected. Donor patching either barely changed the seam or blurred/softened lower-right ground; it did not produce a source-faithful repair.

Key result:

```text
deliverables/dit360_v2/db35_seam_first/db35_seam_target_board.jpg
deliverables/dit360_v2/db35_seam_first/db35_rightline_donor_diag_board.jpg
```

### DB36: ultra-narrow DiT360 red-line mask on G

Ran one A100 DiT360 case on the user-marked G seam with a much narrower mask. Object gate passed and outside-mask pixels were preserved, but vision failed: fake pale ground slabs, black/patchy holes, and synthetic ground/curb artifacts. Rejected.

Key result:

```text
deliverables/dit360_v2/db36_user_redline_mask/db36_reject_review_board.jpg
```

### DB37: Google/Meta seam-mechanism gap audit

Compared the project against public Google Street View / Google Jump / Meta Surround360 style mechanisms. Conclusion: production systems rely on calibrated rigs, reliable overlap, flow/depth/visibility, source redundancy, seam planning, and abstain/source selection. The BMW G right-ground seam is a counter-case with weak evidence. No new local repair was opened.

Key result:

```text
deliverables/dit360_v2/db37_google_meta_gap_audit/db37_google_meta_gap_audit.md
```

### DB38: Bosch handoff board

Accepted DB32 `s40` as the current Bosch handoff candidate with explicit caveats. DB32 is the defensible candidate because it avoids fake ground and preserves source content. It is not an original-G seam fix.

Key result:

```text
deliverables/dit360_v2/db38_bosch_handoff/db38_bosch_handoff_board.jpg
```

### DB39: v14 trimap replay audit

The user emphasized the old v14 trimap-clamp method:

```text
deliverables/dit360_seam_completion/runs_v14_trimap_clamp_bmw/trimap_r008_h016_w025_tau5/trimap_r008_h016_w025_tau5_raw_fullres_1024x2048.png
```

That old result is locally good in places, especially the right white BMW. The exact v14 family was audited across G/BEST/A1 tau variants. Rejected as a G-family seam solution: G gets a pole/slice artifact, BEST inherits ghosting/slabs, A1 gets a right-side vertical slice.

Key result:

```text
deliverables/dit360_v2/db39_v14_trimap_replay/db39_v14_trimap_replay_board.jpg
```

### DB40: A1/G v14 mask-alignment root cause

Two read-only subagents and local forensic boards converged on the root cause: the old v14 mask/method was reused over changed init images. The seam core intersected candidate-specific white-BMW/sidewalk/building content, creating the A1 right-BMW slab/ghost and G pole-like slice.

A1 keepout A100 run:
- fixed the user-marked right white BMW slab/ghost,
- object gate passed,
- but full/long-source view still had vertical edit bands.

A1 long-source-only run:
- reduced edited support,
- but generated a pole-like vertical object despite object-gate PASS.

DB40 accepted the root-cause diagnosis and rejected the repair route.

Key results:

```text
deliverables/dit360_v2/db40_v14_mask_alignment/db40_mask_alignment_forensic_board.jpg
deliverables/dit360_v2/db40_v14_mask_alignment/masks/db40_keepout_mask_preview_board.jpg
deliverables/dit360_v2/db40_v14_mask_alignment/db40_a1_keepout_review_board.jpg
deliverables/dit360_v2/db40_v14_mask_alignment/db40_a1_longsrc_review_board.jpg
```

### DB41: right-white-line raw-camera evidence gate

Tested whether the exact user-marked right/lower-right white-line band had enough raw-camera/LiDAR/flow evidence for a source-faithful micro repair.

ROIs:

```text
right_roi=[1440,360,2048,720]
lower_right_roi=[1580,560,2048,790]
```

Metrics:

- `right_roi`: valid 0.759, near-ground 0.519, LiDAR support 0.084, best flow reliable 0.863, but LiDAR threshold fails and visible support is not a continuous white-line/curb surface.
- `lower_right_roi`: valid 0.421, near-ground 1.000, LiDAR support 0.000, best flow reliable 0.731, but flow attaches to vehicle/edge fragments rather than continuous road-line geometry.

Closed as repair rejected. Do not edit this band without new raw/depth/correspondence evidence.

Key result:

```text
deliverables/dit360_v2/db41_rightline_evidence_gate/db41_rightline_evidence_board.jpg
```

### DB42: seam decision and Bosch handoff synthesis

Packaged the final state:

- use DB32 `s40` as current Bosch handoff candidate,
- do not claim G/A1/BEST seam is fixed,
- reopen seam repair only with new raw/depth/correspondence evidence and strict kill criteria.

Key result:

```text
deliverables/dit360_v2/db42_seam_decision_handoff/
```

## 4. The Three User-Marked G/A1/BEST Results

These are diagnostic references, not final deliverables.

### `G_bmw_pano`

Path:

```text
deliverables/ghostkill/G_bmw_pano.jpg
```

Assessment:
- likely the most honest source-faithful diagnostic among the G-family,
- but the right white-line / long dark-wall seam / lower-right ground band remains visibly wrong,
- DiT/v14 attempts tend to create pole-like slices, fake ground, black holes, or slabs.

Do not run more prompt-only DiT on this target.

### `A1_view_none`

Assessment:
- visually smoother in some regions,
- but still has parallax and blend artifacts,
- the right white BMW ghost/slab was diagnosed as mask/source mismatch and can be locally reduced with keepout,
- but the keepout fix leaves vertical edit bands, so it is not final.

A1 is useful to study mask alignment, not as a final output.

### `BEST_bmw_pano`

Path:

```text
deliverables/ghostkill/BEST_bmw_pano.jpg
```

Assessment:
- may look closer in some seam regions,
- but has ghosting in buildings/vehicles,
- unsafe as donor because ghost transfers into the repaired image.

Do not use BEST donor patching as a production repair.

## 5. Code State and Known Engineering Issues

Important scripts:

```text
scripts/phase3/_ghostkill_compare.py
scripts/phase3/_seamroute.py
scripts/phase3/_bev_ground.py
scripts/phase3/db35_rightline_donor_diag.py
scripts/phase3/db36_user_redline_mask.py
scripts/phase3/db39_v14_trimap_replay_audit.py
scripts/phase3/db40_v14_mask_alignment_forensic.py
scripts/phase3/db40_build_keepout_masks.py
scripts/phase3/db40_build_longsource_component_mask.py
scripts/phase3/db41_rightline_evidence_review.py
scripts/phase3/db42_seam_decision_handoff.py
```

This is not a simple bug situation. The current code reached a real formulation boundary. Still, there are engineering risks:

- `_seamroute.py` is a research prototype with several generations of ideas mixed together: DP seam, object moat, virtual-center select, ground-road reproject, DiT risk mask output.
- Many ROIs and masks are hand-coded for the BMW case.
- v14 masks are not safely reusable across init images.
- Object gate catches net-new salient objects, but misses seam-local fake geometry.
- There is no unified repairability evaluator across all candidates.

If you continue engineering work, first make the evaluation and candidate-mining pipeline clearer. Do not patch `_seamroute.py` blindly.

## 6. What Has Been Tried Before

See `progress.md` for full detail. Major families:

- L1 hard_select baseline.
- fixed-radius spherical projection variants.
- multi-radius per-pixel variants.
- ECC/local alignment.
- DP seam routing.
- object-aware seam routing / object moat.
- view interpolation / virtual center / flow-based alignment.
- LiDAR depth reprojection.
- dense depth / Depth Anything style seam metadata.
- sparse stereo and ground-plane seam layers.
- BEV ground atlas.
- line snap / curb / road-line handling.
- photometric attenuation.
- donor patching.
- DiT360 wide-mask ground repair.
- DiT360 thin/v14 trimap repair.
- DiT360 sky-only completion.
- Cube/rectilinear diagnostics.
- Google/Meta/StreetView mechanism audit.

The pattern is stable:

- source-faithful local seam routing removes some ghosting but leaves physical parallax/curb/ground limits;
- blending can look smoother but creates ghost evidence;
- DiT can fill sky safely under strong masks but invents ground/curb/objects when asked to repair driving geometry;
- source selection / frame selection has been more valuable than repairing a bad seam.

## 7. Current Best Strategic Hypotheses

Discuss these with the user before acting.

### Direction A: Dataset-level candidate mining

Highest short-term value.

Hypothesis: the right move is not to repair bad G/A1/BEST seams, but to find better source bases across more logs/anchors, like DB28/a200 did.

CPU-only first:
- scan more logs/anchors,
- score seam risk, object risk, camera-id boundary, near-ground seam, LiDAR support, sky/out-of-FoV, and ghost risk,
- render top-N montages,
- compare against DB32/G/A1/BEST in the same ROIs.

Open as a new brief before running, likely DB43.

### Direction B: Evidence-aware seam routing redesign

Do not merely tune current `_seamroute.py`; redesign the cost and evaluation.

Hypothesis: seam routing should include evidence confidence:
- low LiDAR support = high risk or abstain,
- low flow consistency = high risk,
- near-ground / white-line / curb = high risk unless geometry support exists,
- object/tall structure edges = hard moat,
- source-boundary slabs = penalized.

CPU-only first. Open a brief, likely DB44, only after agreeing on kill criteria.

### Direction C: Raw-camera evidence-guided local geometry repair

Only attempt local warp/homography/plane-aware correction in ROIs where evidence gate passes. DB41 failed for the exact BMW lower-right white-line region, so do not repair that region without new evidence.

The next useful step is to find other candidate ROIs that pass evidence, not to force this one.

### Direction D: Literature-driven new route exploration

The user explicitly wants exploration of recent top-tier papers that may help. Do this before code if the user asks for a route discussion.

Search only primary sources:
- CVPR / ICCV / ECCV / SIGGRAPH / TOG / TPAMI / IJCV / ICLR / NeurIPS where relevant.
- Official project pages, OpenAccess, arXiv only when venue is clear or it is being used as radar, not as settled fact.

Target literature clusters:
- large-parallax image stitching and seam cutting,
- deep local patch alignment for stitching,
- 3D foundation geometry models such as DUSt3R / MASt3R / VGGT-style dense geometry,
- autonomous-driving novel view synthesis / 3D Gaussian splatting / dynamic scene reconstruction,
- panorama / 360 diffusion and outpainting under geometric constraints,
- Street View / Google Jump / Meta Surround360 production-style stitching and deghosting,
- evidence-guided or control-guided inpainting where source pixels can constrain generation.

The review must answer:
- what mechanism could transfer to Waymo2Panorama,
- what evidence/data it requires,
- whether it can preserve source content,
- whether it can run CPU-only for a kill test,
- what would make it fail on G/A1/BEST.

Do not cite a paper as "latest best" without checking current source/date/venue.

### Direction E: Bosch handoff packaging

If the user needs a short-term deliverable, keep DB32 `s40` and produce an honest caveat board/report. Do not wait for seam repair to become perfect.

## 8. Do Not Do These Without a New Brief

- Do not run more A100 prompt sweeps on G/A1/BEST ground seam.
- Do not repeat the v14 trimap matrix unless the mask/source support is fundamentally changed.
- Do not use BEST/A1 donor patching as a repair.
- Do not treat `view_none` blending as safe just because it looks smooth.
- Do not edit the right/lower-right white-line band after DB41 unless new raw/depth/correspondence evidence is available.
- Do not use DiT360 to generate ground/curb/white-line content for Bosch handoff.
- Do not claim a route is good without inspecting the generated images.

## 9. Infrastructure Notes

Local workspace:

```text
D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama
```

Drive/Colab route is ephemeral:

- fetch `runtime/active_url.json` fresh before using the executor,
- tunnel URL/token rotate,
- the user must provide a live CPU/GPU JSON or restart the worker,
- keep model weights/cache on Drive/Colab, not local.

As of this handoff:
- A100 was shut down by the user after DB42,
- CPU-only route discussion and literature review can continue,
- do not assume any runtime is live.

Git:
- recent DB33-42 work is committed.
- `git status` still shows many historical untracked deliverables; do not delete or revert them unless explicitly instructed.

## 10. Recommended First Response to User

After reading the required files, say something like:

```text
我已经读完 README/handoff/progress/decision_briefs 和 2026-06-04 handoff。我的理解是：DB32 s40 是当前 Bosch handoff candidate，但不是 G/A1/BEST seam 修复；DB35-42 已经把 G-family DiT/v14/donor/right-line repair 在当前证据下关掉了。下一步我建议先不改 pipeline，也不跑 A100，而是开一个新 brief 做全项目路线审计/文献雷达，重点找 source selection、evidence-aware seam routing、3D geometry foundation model、large-parallax stitching 里是否有新突破。只要要动实验，我会先写 decision brief 和 kill criteria。
```

Then ask the user whether they want:

1. literature-first route mining,
2. CPU-only candidate/source mining,
3. evidence-aware seam routing redesign,
4. or Bosch handoff packaging.

Do not start coding before the user chooses the route.

