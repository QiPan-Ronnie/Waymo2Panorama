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

# DB-20260603-18: ▶▶ ACTIVE LEAD — DiT360 EXPLORATION PROGRAM (seam completion + outpaint + paper-derived inference tricks)
Status: **running** (A100 always-on; autonomous overnight; ultracode). Big user goal 2026-06-03.
Route: B (generative — DiT360/FLUX, bounded by masks + object-safety gate)
GOAL (user): explore DiT360 for our AV ERP panorama as far as possible. TWO targets:
  **(T1)** HIDE the wavy near-ground seam (the non-generative physical floor) via thin-mask generation.
  **(T2)** OUTPAINT the black sky/ground band (the biggest gap to "Google-Maps look") — RE-TRY from multiple angles, judge if usable for faithful-ish data (koi asked to re-tune outpaint; the OLD full-frame center-outpaint hallucinated cars/vans → was rejected; test whether a constrained outpaint is different).
Best init = `SR_bmw_bevfinal_1024x2048.png` (bleed-free, code-review-fixed); alt inits = G_bmw_pano / BEST_bmw_pano / A1_view_none.
DiT360 (arXiv 2510.11712, Insta360): FLUX.1-dev + LoRA, hybrid TRAINING = image-level (perspective-guidance + panoramic-refinement) + token-level (**circular-padding** wraparound, **yaw-loss** rotation-robust, **cube-loss** distortion). → the LoRA already "knows" wraparound/yaw/distortion. INFERENCE = RF-Inversion (gamma/eta) + PersonalizeAnything attn (tau) + our trimap latent-clamp (core free / halo soft / far byte-clamp); circular latent padding already applied. Env: A100-40GB, FLUX+DiT360-LoRA cached, code `/content/DiT360`, runner `run_dit360_trimap_clamp.py`.
SUB-DIRECTIONS (each = own kill-test; results under Drive `results/dit360_seam_v2/` (T1) + `results/dit360_outpaint_v2/` (T2), fetched to `deliverables/dit360_v2/`):
- **D1 seam corecompose baseline** on bevfinal (= the old DB-14; RUNNING).
- **D2 param sweep**: tau{1,5,10,20} × guidance{2.0,2.8,4.0} × core-radius{r008,r016} × halo — regime that visibly smooths the wavy seam WITHOUT inventing objects.
- **D3 yaw-ensemble** (NEW, from the yaw-loss): roll pano by several yaw offsets, seam-complete each, median-merge where consistent (or roll seams to benign azimuths).
- **D4 OUTPAINT sky/ground** (T2), multiple angles: (a) sky-only (low-risk, no objects), (b) thin structure-continuation band, (c) full hemisphere; guidance/prompt/mask variants; anti-object gate. Judge usable vs hallucinate.
- **D5 object-safety gate** (DB-05): SAM/YOLO band-diff vs source → reject any output with net-new salient objects. Gates ALL generative outputs.
- **D6 multi-anchor** (0bae/2c65) once a BMW config is good.
KILL (per sub-dir): D2 — no tau/guidance regime both smooths AND passes the object gate → seam-DiT is cosmetic-only; D3 — ensemble blurs or no variance drop → drop; D4 — even sky-only/thin outpaint hallucinates past the gate OR looks worse than black → outpaint stays REJECTED for faithful data (note honestly; may still be a "plausible demo"); D5 — detector recall too low → report "invention rate at recall R".
MAX SCOPE: A100 autonomous; each config ~3-5 min (sequential GPU); vision EVERY output + object gate; record each result's location. codex r10 adversarial running to reprioritize.
Required vision check: YES — every generated pano + seam/curb/sky crops; scan for invented/bent structure.
Result summary: TBD → archive to progress.md; sub-results recorded as produced.

---

# DB-20260603-14: ▶ ACTIVE — DiT360 thin-seam (trimap-clamp) completion ON the BEV-improved deliverable
Status: **proposed** (CPU prep DONE + pipeline verified; NEEDS GPU)
Route: B (generative — but CONSTRAINED: thin seam band only; NOT a core solver, NOT full outpaint)
Origin: user 2026-06-03 — revived the OLD `runs_v14_trimap_clamp` result and judged the small-mask version "其实也可以"; proposes applying it to the LATEST deliverable to smooth the residual seam. codex (earlier rounds) explicitly approved diffusion for thin seams/holes with a hard mask (NOT as a core solver).

Question: Does DiT360 trimap-clamp **corecompose** (regenerate ONLY the ~1.6% thin seam "core"; halo soft-clamped; far 95% BYTE-EXACT) applied to the **BEV-improved deliverable** (`SR_bmw_bevfinal_1024x2048.png`) visibly SMOOTH the residual seam (incl. the off-plane curb the BEV road layer can't fix) — WITHOUT inventing salient objects — i.e., a cleaner, more "Google-Map-like" seam at bounded faithfulness cost?

Hypothesis: applied to the de-ghosted + BEV-road base (residual = a small seam/curb kink, NOT L1's big misalignment), the thin core has little to invent → a light, plausible bridge; far/halo byte-exact guarantees no global hallucination; the 1.6% core is too thin to fit a whole car/sign → object invention bounded.

Why now: the source-faithful geometry path is EXHAUSTED — DB-11/12/13 + IPM NEG + the BEV atlas (road = representation-fixable but modest ERP payoff) + the curb off-plane floor. The only remaining lever to make the seam/curb cleaner is generation. The trimap pipeline exists and is verified ready.

Expected evidence: corecompose pano vs the bevfinal base — vision: is the residual seam/curb smoother/more continuous? + object-safety gate (SAM/YOLO band-diff, DB-05): zero net-new salient instances vs the source strips. + far/halo MAE = 0 (byte-exact preservation confirmed).

Kill criteria: (a) the core regeneration SMEARS/bends a lane line/edge or invents a salient object the anti-object gate flags → reject for faithful data; (b) corecompose under-changes (≈ bevfinal, no visible smoothing) → not worth the GPU/faithfulness cost; (c) it only helps where already clean → drop.

Max scope: GPU (L4 enough), ~140s/anchor. Step 1 = ONE anchor (BMW) corecompose on the bevfinal init + the existing r008 mask + vision + object gate. Do NOT batch/generalize until BMW passes vision + gate. The faithfulness line: thin-seam synthetic is acceptable ONLY with the anti-object gate green + every image eyeballed.

Required vision check: YES — eyeball the seam smoothing AND scan the core band for any invented/bent structure.

READY STATE (CPU prep done 2026-06-03): init = `results/seamroute/SR_bmw_bevfinal_1024x2048.png` (**the BEV-improved deliverable, adopted 2026-06-03**); core_mask `results/dit360_seam_completion/inputs_v14_trimap/02a00399_a000/02a00399_a000_mask_preserve_nonseam_r008.png` (same inter-camera seam azimuths, still aligns); weights cached `cache/huggingface` (32G); DiT360 code `external/DiT360` (42M, put or clone on GPU); runner `scripts/phase3/run_dit360_trimap_clamp.py` (`--init-image` ← bevfinal; `--case name=...,core_mask=...`; `--guidance 2.8 --steps 50`). BLOCKER: GPU runtime (current Colab = CPU).

Result summary: TBD → archive to progress.md when done, then delete here.

---

> **ARCHIVED 2026-06-03 (moved to progress.md):**
> - **DB-15/16/17 (non-DiT "hide-the-seam" program: reroute / Poisson / line-snap) = SUPERSEDED by the BEV ground atlas** (codex round-8 lead). DB-15 (visibility-aware reroute) tested = marginal/NEG (line-w 10 & 50 → seam barely moves, pano unchanged); DB-16/17 not needed. The BEV ground atlas (`_bev_ground.py`) is the road-layer ceiling (road = representation-fixable, modest ERP payoff); the curb is the off-plane floor. Non-generative road path EXHAUSTED. Full detail: `progress.md` codex-round-8 entry.
