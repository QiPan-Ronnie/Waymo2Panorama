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
