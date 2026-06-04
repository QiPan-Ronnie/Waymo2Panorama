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

---

# DB-20260603-26: Source-safe photometric attenuation for the long-line seam
Status: **proposed**
Route: A (traditional / Google-Meta-style color seam handling)

Question: Can we reduce the visibility of the user-marked long horizontal seam/slab line using only source-safe low-frequency luminance/color attenuation, without geometry warp, source overwrite, or generated content?

Hypothesis: DB-25 shows the long-line ROI does not have enough reliable correspondence for a full geometry warp, especially around the dark-wall/right-side pair. However, a Google/Meta-style photometric seam treatment may still reduce the line: adjust only low-frequency Y/color bias in a narrow, confidence-bounded band around camera-id transitions, leaving high-frequency structure and object/road geometry intact.

Why now: the user flagged the long line as a major visible issue. DB-23 rejected generative ground/full outpaint, and DB-25 rejected blind geometry warp. The remaining safe lever is photometric attenuation, not semantic generation.

Expected evidence:
- CPU-only before/after crops for the marked ROI and full-pano downsample.
- Difference/changed-mask showing edits are narrow, low-frequency, and do not move lane lines, curb, cars, building edges, or dark-wall geometry.
- A visual verdict: did the line become less salient without creating blur/ghost/false geometry?

Kill criteria:
- Any geometry movement, lane/curb bending, car/body blur, or building-edge smear = reject.
- Any broad color wash over real content = reject.
- If the line remains equally visible at normal viewing scale, close as no-op.
- If it only looks better by hiding structural evidence behind blur, reject for Bosch data.

Max scope: one CPU prototype on BMW long-line ROI using existing current-best pano; no A100, no DiT360, no optical-flow sweep. This brief can produce a diagnostic candidate only, not a new official deliverable unless vision clearly passes.

Required vision check: YES — inspect full image and red-line ROI crops.

Result summary: TBD → archive to progress.md when done, then delete here.

> **DONE THIS SESSION (2026-06-03, A100) — full record in `progress.md` (top "DiT360 SESSION SYNTHESIS" entry); kept here only as pointers so this queue stays short:**
> - **D2 DiT360 seam-completion, WIDE ground-risk mask (5.56%) + tau{20,50}** = **NEG** (object-gate FAIL: invents small cars + melts textureless cuts). → superseded by DB-14 (thin mask). Results: `deliverables/dit360_v2/gr_tau*`.
> - **D4 DiT360 SKY-ONLY outpaint** = **POSITIVE** (gate-clean upper-hemisphere fill; rooflines byte-exact). → folded into DB-19. Results: `deliverables/dit360_v2/op_sky_t50_s0.png`, `sky_roofline_cmp.jpg`.
> - **DB-23 DiT360 ground/full outpaint rejudge** = **REJECTED**: ground gate PASS but vision FAIL due fake bottom road/lane/curb geometry; full gate FAIL with net-new `traffic_light`. Results: `deliverables/dit360_v2/db23_d4b_rejudge_montage.jpg`. Detail in progress.md.
> - **DB-24 Google/Meta-style long-line diagnosis** = **CLOSED explanatory**: the user-marked long line is a source/camera-id boundary in near-ground/dark-wall low-texture regions; Google/Meta-style flow would need reliable correspondences that this ROI does not show yet. Results: `deliverables/dit360_v2/db24_google_meta_line_diag/`. Detail in progress.md.
> - **DB-25 AV raw-camera evidence pack** = **CLOSED evidence-only**: ROI uses four camera labels, near-ground=62.3%, LiDAR support=9.4%, best pair flow reliable=68.2% but key right dark-wall pair `6-5` only 10.5%; recommendation = abstain from geometry warp. Results: `deliverables/dit360_v2/db25_longline_evidence_fetch/`. Detail in progress.md.
> - **DB-20 DiT360 lever mining** = **MOSTLY SUPERSEDED / CLOSED**: prompt bug fixed, sky generalization accepted, T1 near-ground seam levers paused/rejected after DB-14 + DB-21. Reopen only through a new brief with new evidence.
> - **DB-19 sky-only combo/generalization** = **ACCEPTED** for BMW + 0bae; 2c65 gate-clean diagnostic with base-slab caveat. Results: `deliverables/dit360_v2/db19_G_bmw_pano_sky_t50_s0_postcompose_thr45.png`, `db19_0bae_sky_t50_s0_postcompose_thr45.png`, `db19_2c65_sky_t50_s0_postcompose_thr45.png`. Detail in progress.md.
> - **DB-22 CubeComposer/rectilinear diagnostic** = **CLOSED informative only**: rectilinear view confirmed DB-21 mask placement was not the root problem; DiT semantic ground redraw is. Result montage: `deliverables/dit360_v2/db22_rectilinear_diag/db22_rect_bmw_rightline_montage.jpg`. Detail in progress.md.
> - **DB-15/16/17** (non-DiT reroute / Poisson / line-snap) = CLOSED, superseded by the BEV ground atlas (codex round-8 lead). Detail in progress.md.
> - INFRA recipe + /code-review fixes (box-overlap object gate, fail-safe asserts, flood-fill outpaint mask) recorded in progress.md.
