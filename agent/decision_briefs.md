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

# DB-20260603-14: ▶▶ ACTIVE LEAD (next GPU run) — DiT360 FAITHFUL **THIN**-seam (trimap r008) on the best base
Status: **proposed** (CPU prep DONE + pipeline verified; NEEDS A100). **This is the SEAM method of record** — it REPLACES yesterday's mis-run wide ground-risk mask (= NEG, archived to progress).
Route: B (generative, CONSTRAINED: thin r008 seam core only; far/halo byte-exact; NOT a wide solver, NOT full outpaint)

★ **WHY THIS BRIEF (user correction 2026-06-03):** yesterday's seam run used a **WIDE ground-risk mask (5.56%) + tau{20,50}** → it INVENTED small cars + melted textureless cuts (object-gate FAIL → NEG, archived). **That is NOT our prior, trusted method.** Our prior method = the **v14 trimap-clamp THIN seam: r008 core (~1.6%), tau5 (light touch), corecompose** — the image the user judged "其实也可以": `deliverables/dit360_seam_completion/runs_v14_trimap_clamp_bmw/trimap_r008_h016_w025_tau5/..._raw_fullres_1024x2048.png`. The productive UNEXPLORED cell = **THIN mask × MODERATE tau** (NOT wide × high). tau5 on the thin core was found ≈no-op in v14 history (raw ≈ hard_select); the question is whether a *slightly* higher tau on the *same thin* mask gives a real bridge while staying gate-clean.

Question: Does the v14 THIN-seam trimap-clamp on the **current best base** (`SR_bmw_bevfinal_1024x2048.png`, bleed-free) with **r008 core × tau {5, 8, 12}** (guidance 2.8, halo soft-clamp, far byte-exact, fixed seed) VISIBLY smooth the residual wavy near-ground seam WITHOUT inventing salient objects?

Hypothesis: thin core = too little canvas to fit a whole salient object → object gate stays green; moderate tau (8–12) on the thin core gives the light plausible bridge tau5 didn't; far/halo byte-exact = zero global hallucination.

Kill criteria: (a) any tau where the thin core SMEARS/bends a lane line/edge OR the object gate flags net-new salient objects → that tau rejected; (b) the whole thin × moderate-tau cell under-changes (≈ bevfinal at every safe tau) → DiT thin-seam confirmed cosmetic-only → CLOSE T1; (c) helps only where already clean → drop.
Max scope: A100, ONE anchor (BMW) first; r008 × tau{5,8,12} = 3 cases (~15 min sequential); vision EVERY output + object gate; do NOT widen the mask, do NOT batch other anchors until BMW passes vision + gate.
Required vision check: YES — seam smoothing AND scan the thin core for invented/bent structure.
READY STATE: init=`results/seamroute/SR_bmw_bevfinal_1024x2048.png`; core_mask=`results/dit360_seam_completion/inputs_v14_trimap/02a00399_a000/02a00399_a000_mask_preserve_nonseam_r008.png` (⚠️ init + core_mask 都 **Drive-only，本地没有** → 跑前从 Drive 拉到 runtime；bevfinal 本 session 重生成过 = bleed-free，Drive 副本是 canonical); runner `scripts/phase3/run_dit360_trimap_clamp.py` (per-case guidance/seed override added this session); FLUX+LoRA cached `cache/huggingface`; DiT360 code clone `/content/DiT360`; INFRA recipe (local FLUX cache / `pip uninstall torchao` / torchvision object gate / tau scale 0–100) in progress.md. BLOCKER: A100 runtime — needs a fresh tunnel url+token.
Result summary: TBD → archive to progress.md when done, then delete here.

---

# DB-20260603-19: ▶ NEW (prepared-to-run) — cleanest faithful-ish pano = bevfinal + thin-seam + **SKY-outpaint**; then generalize
Status: **proposed** (D4 sky-outpaint already POSITIVE this session; this brief assembles + generalizes it)
Route: B (generative, constrained + object-gated)
Origin: this session's WIN = **sky-only outpaint is gate-clean** (archived POSITIVE in progress; `deliverables/dit360_v2/op_sky_t50_s0.png`, `sky_roofline_cmp.jpg`). Assemble the proven faithful layers into ONE deliverable and test generalization.

Plan (in order, each gated):
1. **COMBO pano**: bevfinal → (DB-14 thin-seam if it passes) → **sky-only outpaint** (opmask_sky, tau50, guid2.8 — the proven recipe) = ONE pano: source-faithful horizontal band + smoothed seam + generated upper sky. Vision + object gate. = the best "Google-Maps-like" faithful-ish output to show.
2. **Re-judge D4b ground/full outpaint** (already RAN; judging was blocked by the tunnel outage): `results/dit360_outpaint_v2/{ground_t50_s0,full_t50_s0}/` → hardened object gate + vision. codex predicts ground = high-risk (invents lane/curb/cars). Decide: any ground completion usable, or ground outpaint stays REJECTED (sky-only stands).
3. **Multi-anchor generalize**: re-run the proven sky-outpaint (+ combo) on **0bae + 2c65** (bevfinal + masks already PREPPED on Drive `results/seamroute/SR_{0bae,2c65}_bevfinal*` + `dit360_outpaint_v2/masks_{0bae,2c65}/`). Confirm the sky-win is not BMW-specific.

Kill criteria: combo — if thin-seam fails DB-14, ship bevfinal + sky only; ground outpaint — if it invents past the hardened gate OR looks worse than honest black → ground REJECTED (note honestly; may still be a "plausible demo", NOT faithful data); multi-anchor — if sky-outpaint hallucinates on 0bae/2c65 → the win is anchor-specific, report that.
Max scope: A100 sequential; each ~3–5 min; vision + gate EVERY output; record each result's Drive + local location.
Required vision check: YES.
Result summary: TBD → archive to progress.md when done, then delete here.

---

> **DONE THIS SESSION (2026-06-03, A100) — full record in `progress.md` (top "DiT360 SESSION SYNTHESIS" entry); kept here only as pointers so this queue stays short:**
> - **D2 DiT360 seam-completion, WIDE ground-risk mask (5.56%) + tau{20,50}** = **NEG** (object-gate FAIL: invents small cars + melts textureless cuts). → superseded by DB-14 (thin mask). Results: `deliverables/dit360_v2/gr_tau*`.
> - **D4 DiT360 SKY-ONLY outpaint** = **POSITIVE** (gate-clean upper-hemisphere fill; rooflines byte-exact). → folded into DB-19. Results: `deliverables/dit360_v2/op_sky_t50_s0.png`, `sky_roofline_cmp.jpg`.
> - **DB-15/16/17** (non-DiT reroute / Poisson / line-snap) = CLOSED, superseded by the BEV ground atlas (codex round-8 lead). Detail in progress.md.
> - INFRA recipe + /code-review fixes (box-overlap object gate, fail-safe asserts, flood-fill outpaint mask) recorded in progress.md.
