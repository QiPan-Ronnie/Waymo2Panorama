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

# DB-20260603-22: ▶ NEW — CubeComposer-inspired cube-face seam continuity probe (CPU first, no full model run)
Status: **proposed** (source-inspected; CPU diagnostic only after DB-21 seam-line DiT was rejected)
Route: B-support / representation diagnostic

Origin: User asked whether TencentARC/CubeComposer can be borrowed. Primary-source check: CubeComposer generates 4K 360 video by decomposing to cubemap faces and uses cube-face context, cube-aware positional encoding, padding, and blending for boundary continuity. It is **not** an AV multi-camera stitching algorithm. After DB-21, the main failure is no longer ERP mask placement but DiT semantic redrawing of ground structure, so this brief is diagnostic/explanatory, not a GPU repair path.

Question: Can a cube-face or rectilinear view explain whether ERP projection contributed to the BMW right-line defect, and provide better diagnostic figures for the report?

Hypothesis: even if CubeComposer's model is not directly applicable, its representation lesson is useful for visualization: inspect seam defects in a less distorted cube/rectilinear local view. This can support the final explanation of why DB-14/21 failed, but should not trigger another DiT GPU sweep unless it reveals a truly different defect.

Plan:
1. CPU-only: render the BMW right-ground seam area as a rectilinear/cube-face crop from `G_bmw_pano` and overlay the DB-21 masks/results.
2. Use the figure to decide whether the residual is mainly projection/visualization, source-data geometry, or generative redraw. Do **not** start a full CubeComposer/Wan run.
3. Only create a cube-derived mask if the rectilinear view reveals a distinct, previously missed defect region. Otherwise archive as diagnostic evidence.

Kill criteria: (a) cube/rectilinear view does not make the defect clearer; (b) it only restates DB-21's already-known semantic redraw failure; (c) any proposed cube-derived mask is materially the same as the rejected ERP ROI. If any holds, archive DB-22 as "informative only".
Max scope: CPU diagnostic only; no Wan/CubeComposer checkpoint downloads or full model inference in this brief.
Required vision check: YES — cube/rectilinear crop + ERP reproject overlay must be inspected before GPU.
Result summary: TBD → archive to progress.md when done, then delete here.

---

# DB-20260603-19: ▶ PARTIAL — cleanest faithful-ish pano = current-best base + **SKY-outpaint**; then generalize
Status: **BMW accepted / generalization pending** (`G_bmw_pano` + sky-only + postcompose thr45 is archived in `progress.md`; DB-14/21 seam-line DiT rejected, so assemble without a DiT seam layer)
Route: B (generative, constrained + object-gated)
Origin: this session's WIN = **sky-only outpaint is gate-clean** (archived POSITIVE in progress; `deliverables/dit360_v2/op_sky_t50_s0.png`, `sky_roofline_cmp.jpg`). Assemble the proven faithful layers into ONE deliverable and test generalization.

Plan (in order, each gated):
1. **BMW combo DONE:** current best base `G_bmw_pano` → sky-only outpaint → CPU sky-edge postcompose thr45 = `deliverables/dit360_v2/db19_G_bmw_pano_sky_t50_s0_postcompose_thr45.png` (Drive mirror recorded in progress). Source-faithful horizontal band + generated upper sky; no DiT seam layer.
2. **Re-judge D4b ground/full outpaint** (already RAN; judging was blocked by the tunnel outage): `results/dit360_outpaint_v2/{ground_t50_s0,full_t50_s0}/` → hardened object gate + vision. codex predicts ground = high-risk (invents lane/curb/cars). Decide: any ground completion usable, or ground outpaint stays REJECTED (sky-only stands).
3. **Multi-anchor generalize**: re-run the proven sky-outpaint on **0bae + 2c65** (bevfinal + masks already PREPPED on Drive `results/seamroute/SR_{0bae,2c65}_bevfinal*` + `dit360_outpaint_v2/masks_{0bae,2c65}/`). Confirm the sky-win is not BMW-specific.

Kill criteria: combo — if sky-only introduces objects/boundary artifacts on the chosen base, fall back to current-best base without outpaint; ground outpaint — if it invents past the hardened gate OR looks worse than honest black → ground REJECTED (note honestly; may still be a "plausible demo", NOT faithful data); multi-anchor — if sky-outpaint hallucinates on 0bae/2c65 → the win is anchor-specific, report that.
Max scope: A100 sequential; each ~3–5 min; vision + gate EVERY output; record each result's Drive + local location.
Required vision check: YES.
Result summary: TBD → archive to progress.md when done, then delete here.

---

# DB-20260603-20: ▶ PARTIAL — DiT360 paper/code levers after DB-21
Status: **partially superseded** (Dir 1 sky generalization remains active; T1 seam-line levers paused/rejected unless new evidence appears)
Route: B (generative, constrained + object-gated)
Origin: user 2026-06-03 "再仔细看原论文还有什么我们可以用的, 我们疏忽的内容". A 6-agent Workflow mined the DiT360 paper + OUR actual code (`pa_src/pipeline.py`, `attn_processor.py`, `yaw_rotate.py`, `run_dit360_trimap_clamp.py`) for inference levers we never used, cross-checked vs all 16 prior variants, adversarially KILLED 5 of 8. **Full raw record: `agent/codex_logs/round11_dit360_levermining_raw.json`.**

★ **CODE-VERIFIED BUG FIXED LOCALLY (near-zero risk, applies to ALL trimap runs):** `run_dit360_trimap_clamp.py:32-36` previously enumerated "lane markings, cars, buildings, signs" = the EXACT salient classes the object gate rejects. FLUX-dev is guidance-distilled (no CFG-negative; `pipeline.py:980-986` guidance = scalar embed) so the prompt string is the ONLY semantic steer. Fixed to a seam-repair anti-object prompt: `"continuous existing street panorama texture with no new salient objects; preserve existing geometry and edges; smooth local seam repair only; no vehicles, no people, no signs, no traffic lights, no new buildings"`.

⚠️ **CODE CAVEATS (every knob sweep must respect):** (1) a LEGACY clamp at `pa_src/pipeline.py:1053-1056` fires whenever `mask` passed AND `timestep/1000>=0.5`, hard-copying batch[0]→batch[1] over preserve regions — it CO-EXISTS with the runner's clamp_callback; confirm which dominates before crediting a swept knob. (2) RF window/eta/gamma are hardcoded (`start0/stop0.99/eta1.0` @ :384-388, `gamma1.0` @ :315) → Dirs 2/3 need them plumbed as args.

**SURVIVING DIRECTIONS after DB-21 (updated 2026-06-03):**
1. **Sky-outpaint GENERALIZE + prompt fix** (T2, low risk, ~1.5 A100h). Extend the D4 sky WIN to 0bae+2c65 (masks prepped), HOLD tau50/guid2.8/halo32, A/B ONLY the prompt (anti-object vs the buggy one). PASS = gate netnew=0 + rooflines byte-exact + no boundary seam on BOTH anchors. KILL = sky win is BMW-specific (gate fail / boundary on either).
2. **PAUSED / do not spend A100 now — Multi-yaw generate-and-SELECT (T1).** DB-21 shows even current-base aligned masks redraw semantic ground structure; yaw decorrelation may move artifacts but does not solve source-faithful line geometry. Reopen only if a CPU/cube diagnostic shows the defect is projection-frame dependent rather than generative redraw.
3. **PAUSED / do not spend A100 now — RF faithful micro-sweep (eta/gamma).** DB-21 already found the core-moving regime: it moved, stayed far/halo byte-exact, and still hallucinated sidewalk/curb/planter content. RF knobs are unlikely to turn semantic completion into geometry-preserving line repair without new conditioning.

**GLOBAL KILL updated:** T1-DiT faithful near-ground seam-hide is CLOSED for now after DB-14 + DB-21. T2's faithful win = sky-outpaint (Dir 1); ground/full outpaint stays rejected unless separately re-judged and surprisingly passes gate + vision.

**DROPPED (do NOT re-propose — adversarially killed, full reasons in the raw json):** RF window-shrink/stop_timestep (INVERTED mechanism = the D2 car-invention regime); real-evidence donor / shift_mask (re-litigates v18 "vanilla DiT360 = inpainting NOT multi-ref stitching" NEG + its select step = the twice-rejected copy-SELECTION family DB-10/12); PA layer-subset (speculative, unknown LoRA depth split — park as contingency); cube-face metric (measurement wrapper for Dir 2, no standalone slot; vision wins); additive concept_process (fallback to dropped donor); thin tau{8,12} (interpolates the known no-op→invent bracket); wide/ground-risk mask + ground/full outpaint + post-compose + multi-seed (confirmed dead poles).

Max scope: A100 only for Dir 1 sky generalization; T1 seam knobs require a new decision brief and new evidence before any GPU spend. Vision + object gate EVERY output, record each result location.
Required vision check: YES.
Result summary: TBD → archive to progress.md when done, then delete here.

---

> **DONE THIS SESSION (2026-06-03, A100) — full record in `progress.md` (top "DiT360 SESSION SYNTHESIS" entry); kept here only as pointers so this queue stays short:**
> - **D2 DiT360 seam-completion, WIDE ground-risk mask (5.56%) + tau{20,50}** = **NEG** (object-gate FAIL: invents small cars + melts textureless cuts). → superseded by DB-14 (thin mask). Results: `deliverables/dit360_v2/gr_tau*`.
> - **D4 DiT360 SKY-ONLY outpaint** = **POSITIVE** (gate-clean upper-hemisphere fill; rooflines byte-exact). → folded into DB-19. Results: `deliverables/dit360_v2/op_sky_t50_s0.png`, `sky_roofline_cmp.jpg`.
> - **DB-15/16/17** (non-DiT reroute / Poisson / line-snap) = CLOSED, superseded by the BEV ground atlas (codex round-8 lead). Detail in progress.md.
> - INFRA recipe + /code-review fixes (box-overlap object gate, fail-safe asserts, flood-fill outpaint mask) recorded in progress.md.
