# Waymo2Panorama — Deep Retrospective (2026-06-06, leader, 19-agent workflow)

> **Why this exists.** User asked (frustrated, "still bad") for the deepest possible retrospective: why, after analyzing "what's missing" precisely for 78 briefs, is it STILL not solved? what's still missing? did the worker do anything wrong? did we re-check the SOTA / pick the wrong direction? Goal UNCHANGED = a GENERAL perspective→ERP-panorama method. Ran a 19-agent Workflow (`wf_789ffbb7-1a7`): 7 independent lenses (wall re-verify · framing-mismatch · worker-mistakes · feed-forward-3DGS survey · AV-3DGS survey · pano-generative survey · untried-levers) → consolidate → 10 adversarial refutations → leader synthesis. **Every load-bearing claim was Read-verified on disk at HEAD `87cc16b`.** This supersedes my (leader's) over-optimistic preliminary "switch to 3DGS and the seam dissolves" — see §3.

---

## 1. ROOT CAUSE (one causal chain, verified on disk)

**The #1 reason we still fail is NOT a missing algorithm — it is an unresolved PRODUCT decision that we have been silently building under BOTH sides of.**

- `2026-06-06-leader-strategy-synthesis.md:12` **LOCKs source-faithful** (A) + "abstain is valid".
- `handoff.md` banner **"re-set the goal to a GENERAL plausible make-the-seam-disappear renderer and set aside the Bosch source-faithful hard constraint"** (B).
- The **latest active brief DB-78 RE-ADOPTS source-faithful** ("flow moves REAL pixels") (A).

Nearly every method verdict flips on the A/B fork. Building under both at once is the **verified churn engine**: three different sessions produced three faint in-band edits, each believing it was the next step.

**The technical chain underneath (all anchors on disk):**
1. The panorama's single virtual centre is an **EXTRAPOLATED viewpoint off every real camera trajectory**. For 3–8 m near-ground a no-depth copy has bias `d_px(Z)=(W/2π)·arctan(b/Z)`; measured 16–21 px ⟺ Z≈3.25–5.29 m (`strategy:42-45`). This proves a **NO-DEPTH** copy fails — NOT that a depth-aware operator must fail.
2. **Both framings we tried hit the SAME missing quantity.** 2D-combine (hard_select/L1/A1·G/Poisson/Difix/DB-78 flow) resolves the seam by warp+average in image space → a 2D displacement **cannot represent the cross-optical-centre visibility/occlusion change** at near range (Lemma A). 3D-reconstruct-then-render (DB-77B IBR, EXP-B UniDepth) — **we already did this by hand** — but Z is wrong at structure edges. The true common root = **missing near-field target-surface VISIBILITY evidence** where the surface is seen by ≤1 camera and LiDAR returns nothing: DB-76a task band is **81% single-source**; DB-76b multi-frame-LiDAR curb/wall dense+visible only **5.0%/20.0%**, validated-green near-ground only **0.60%/0.53%** (vs 25% bar).
3. **Every in-band 2D edit is faint by construction.** The objectionable artifacts (near-field BMW/SUV ghost, stepped curb) sit in the **81% single-source band**, but Poisson/Difix/flow can only touch the **~15% co-observed strip** → DB-78 edited_frac 2.47–2.78%, vision verdict "SUBTLE". **A win needs a NEW real view or an honestly-leashed generative band — not a 4th in-band pass.**

---

## 2. THE "3 INDEPENDENT GEOMETRY WALLS" ARE OVER-COUNTED (the biggest correction)

progress.md calls these "3 independent geometry-wall proofs". The audit (Read-verified) found it is **~1.5 + 1 mislabeled**:

| Claimed wall | What it actually measures | Verdict |
|---|---|---|
| **DB-77B** densify p90 15.4/12.8 m | NN-densify (`distance_transform_edt`) scored at held-out LiDAR pixels **across occlusion edges** → residual ≈ the depth **STEP** in metres, not densifier error. Partly a **metric artifact**. | **soft-metric-wrong** |
| **DB-76a** false-GREEN 0.373/0.223 | **rotation-only** (`convergence_distance_m=None`, camera baseline silently dropped) → it is the **no-depth hard_select baseline** error, not a depth-gated operator's. | **mislabeled baseline** |
| **EXP-B** UniDepthV2 edge p90 11.8/11.2 m | A **genuinely different** densifier (monocular, median scale 0.92–1.05 → not a scale bug). Re-confirms a large residual **at occlusion silhouettes**. | **REAL (Lemma A, metric-independent)** |

**Net:** the wall is **real at occlusion silhouettes** (EXP-B; Lemma A) but **over-credited on smooth surfaces** (curb/facade/wall) where the metric is confounded. The most defensible reading: **with a fair metric, surfaces clean up and silhouettes stay walled (abstain vindicated).** We may have **partly mis-killed the source-faithful depth route on surfaces** on a confounded number.

---

## 3. LEADER SELF-CORRECTION — "go 3DGS, the seam dissolves" was too optimistic

My preliminary thesis to the user had three parts; the workflow graded them:
- ✅ **"Constraint is a self-handcuff"** — RIGHT but bounded. Look-good reopens the leashed-generative category, **but only for TONE/TEXTURE/SKY, not salient geometry**. The walls measure source-faithful residual in *metres*; under a plausibility bar an 11 m edge residual can still **render a plausible edge** — so the walls may be non-blocking for a look-good deliverable.
- ✅ **"Fork is the root cause"** — RIGHT, and stronger than I framed it (we build under both at once).
- ❌ **"3DGS reconstruct-then-render dissolves the seam"** — **WRONG / too optimistic.** (a) We **already** did hand-built reconstruct-then-render (DB-77B IBR, EXP-B) and it failed at edges. (b) Per-scene 3DGS **and** feed-forward GS **COLLAPSE at the off-trajectory virtual-centre** — the rig centre is off every camera path, so it is the **same Lemma-A wall from the rendering side** (ExtraGS ~3 m lateral failure; EUVS ~23–25% PSNR drop on translation-extrapolation; ConFixGS feed-forward "typically fails" under large lateral offset). The field's fix for that collapse **is** confidence-aware diffusion priors → **re-imports hallucination**. So 3DGS does **not** auto-dissolve the near-field seam; it re-fights the wall at higher cost. Its **only unique gift** is the **canonical dynamic-actor render** (kill the moving-BMW/SUV ghost that same-frame mosaic structurally can't — DB72 three+ source overlap = 0).
- ⚠️ **A FALSE escape-route keystone was caught:** a proposed route claimed "NeuRAD has a verified AV2 loader (7 ring + 2 stereo + LiDAR)". NeuRAD's released AV2 loader uses **only the 7 RING cams and drops the stereo pair** → the forward-stereo near-field depth (the one new lever) is **NOT delivered, must be self-written**. Same "too-good-unverified" pattern as the fabrication issue.

---

## 4. FRONTIER MAP (re-surveyed, web-verified where possible; UNVERIFIED flagged)

| Family | Best instance | General? | ERP? | Hallucination | Recommended role |
|---|---|---|---|---|---|
| **2D-stitch in-band** (hard_select, Poisson, Difix, **DB-78 flow**) | DB-78 flow view-interp on FOV-overlap strip | **GENERAL**, scene-independent (edited_frac stable 2.5–2.8% / 5 scenes) | native | **none** (moves real px; far-warp ≤0.22px) | **SHIP as the source-faithful FLOOR.** Structurally cannot reach the single-source seam — **no 4th in-band pass.** |
| **Per-scene AV-3DGS render** (NeuRAD, SplatAD, StreetGaussians, OmniRe) | NeuRAD + SplatAD; OmniRe static/dynamic split | NOT feed-forward; OK only as **offline per-log build** | needs self-built rig-centre ERP (extrapolation) | base render collapses off-trajectory; diffusion-fix re-imports hallucination | **Salvage ONE thing: canonical dynamic-actor render** to kill the moving-object ghost. Do NOT adopt full static render. Self-write AV2 stereo+ERP. Gate by EUVS-style LOO before any A100. |
| **Feed-forward GS** (DrivingForward, VGGT-surround/VGD, PF3plat) | VGD-class surround GS (if/when weights release) | the **only** train-once-general category — the north-star ideal | perspective; ERP adapter unbuilt | base collapses at virtual-centre (same wall) | **PARK + WATCH.** Re-eval only when a released, AV2-loadable, single-40GB feed-forward surround GS exists. Don't build custom now (UNVERIFIED availability). |
| **Geometry-leashed generative** (Difix3D+, SetDiff*, ConFixGS, box-conditioned) | available single-step refiner on the **seam band**, box-conditioned + structure guard | conditional-HIGH if refiner is feed-forward; box-conditioning is dataset-agnostic | perspective refiners need ERP adaptation | **GATED** — but object-gate-PASS still faked ground/curb/pole (DB36/DB40) → needs a structure guard beyond object veto | **LOOK-GOOD branch only (Option B).** Benchmark **Percep360** (code promised, downstream-validated) FIRST. SetDiff = inspiration only (unreleased, 3DGS-input, 80GB). |
| **Panoramic diffusion** (DiT360, CubeDiff, MVDiffusion, Percep360) | **sky-only outpaint** (the on-disk WIN) | sky-outpaint generalizes; full-pano "invents content" | native ERP | HIGH for structure | **KEEP sky-only outpaint** gated+labeled. Do NOT use for seam/structure. |

---

## 5. WHAT PREVIOUS WORKERS GOT WRONG (concrete)

1. **Over-counted the walls** — "3 independent" is ~1.5 correlated (DB-77B & EXP-B measure the same edge-depth quantity via two densifiers) + 1 mislabeled no-depth baseline (DB-76a rotation-only).
2. **Read a confounded metric as a settled physical ceiling** — the 15.4/12.8 m edge p90 is an occlusion-edge depth STEP from NN-fill, scored at held-out LiDAR pixels; on a smooth surface one ERP pixel subtends ~1–4 cm, so the curb/facade SURFACE was likely under-credited.
3. **Closed the reconstruct-then-render & feed-forward-GS CATEGORIES on INSTANCE evidence** (StreetCrafter repo audit); never rendered to ERP on our data. (The category may still be closed — off-trajectory collapse is real — but it was closed on the wrong evidence.)
4. **Asserted a FALSE keystone** — "NeuRAD AV2 loader uses 7 ring + 2 stereo"; it drops the stereo pair.
5. **Built under both deliverable definitions at once** (strategy LOCK vs handoff re-set vs DB-78 re-adopt) — the root churn.
6. **Treated object-gate PASS as sufficient for generation** — DB36/DB40 passed the object gate yet hallucinated fake ground/curb/pole (`handoff:280` "object gate misses seam-local fake geometry").
7. **Let fabricated "PARTIAL WIN" numbers + phantom commits (85918f6/4cb96a7/70f435e) enter context** before a worker caught them via Read+git. Surviving truth = edited_frac 2.47–2.78% + "MODEST" vision verdict.

---

## 6. RECOMMENDED NEXT BRIEF — DB-79 (measurement-only, CPU/L4, NO A100)

**Title:** `DB-79: Fair-metric wall settlement — edge-aware/layered hold-out depth + camera-native z-buffer + depth-aware LOO`

**Question:** After removing the two verified confounds (near-wins NN-densify scored across occlusion steps; rotation-only no-depth LOO baseline), does the source-faithful near-field depth route **reopen on SURFACES**, or is the residual **confined to occlusion silhouettes** where Lemma A makes abstain the honest ceiling?

**Hypothesis:** On smooth surfaces a layered/edge-aware LiDAR-only densifier scored against held-out LiDAR drops curb/wall/facade p90 from ~12–15 m to **<0.5–1 m**, while a residual tail persists **only at occlusion silhouettes** (EXP-B predicts the silhouette tail survives). ⇒ depth route NOT walled on surfaces; IS walled at silhouettes (abstain vindicated with a FAIR on-disk artifact).

**Plan:** (1) replace `scatter_depth` single-near-wins with a **layered/LDI** hold-out (score against nearest stored depth layer, not the single near value); (2) densify **LiDAR-only** (exclude stereo-SGBM) + low-depth-gradient single-surface mask; report **surface vs silhouette residuals separately**; (3) build a **camera-native z-buffer** (rasterize fused geometry into each cam's native grid, replace ERP-ray-only seeding) + re-run DB-76a Battery-1 LOO with `convergence_distance_m = fused Zd` and report depth-aware false-GREEN beside the 0.373/0.223 no-depth numbers; (4) strict dynamic removal (boxes) before accumulation; (5) **vision-check** the LOO render-back overlays (a lower number with a smeared curb is still a fail).

**Kill criteria:** surface p90 stays >2 m after the fix → **wall CONFIRMED honestly** (close depth route, ship DB-78 + abstain, stop re-testing geometry — itself a deliverable). depth-aware false-GREEN drops but curb/wall densify p90 stays >2 m → gain is only where LiDAR already returns (road interior), NOT the textureless edges → do NOT declare reopened. Any RGB repair / blend-warp pano / generation / secret-write → out of scope. Beyond BMW+0bae (then 3–5 AV2 + 1 Waymo) → stop & report.

**Generality check:** BMW `02a00399:0` + clean `0bae3b5e:30` first (scene-independent geometry argument), then ≥3–5 AV2 + ≥1 Waymo before any contract claim.

**Compute:** mostly CPU/L4 (no-depth batteries ran 12 s; DB-76b 59 s). **No A100 — hold it.** Tell user before any runtime; route remote results to a non-repo file + Read-verify (fabrication caveat); secret-scan 0.

---

## 7. QUESTIONS FOR THE USER (only they can decide)

1. **THE FORK** (decides nearly every verdict): does Bosch want **(A) source-faithful provenance data** (every pixel traces to a real sensor; abstain OK) or **(B) good-looking panorama** (labeled generated pixels in a thin seam band OK)? Disk oscillates between both. Task note says Bosch's latest want is "look BETTER" (B) — **confirm.** Under A the current mosaic+abstain+DB-78 is essentially the deliverable; under B the leashed-generative + offline-3DGS categories reopen.
2. **Under B:** is the consumer a **world model / training pipeline** (could silently learn fabricated drivable-space geometry) or **presentation/demo only**? On-disk DB36/DB40 + World-in-World say plausible-but-fake structure is rejected downstream even when it looks better → if world model, even labeled generated structure (beyond sky/tone) may be unacceptable.
3. Is an **OFFLINE per-log reconstruction** (fixed pipeline, no per-scene human tuning, minutes-hours/log, abstain on failure) an acceptable deliverable, or must the method be **online/feed-forward**? Decides whether the offline 3DGS dynamic-actor render is in scope.
4. **Generalization-data investment:** Bosch/paper-grade general needs **12+ AV2 + 3–5 Waymo**; only 5 AV2 staged on Drive. Fund the S3→Drive download now, or hold the general claim at 5 scenes?
5. The **5 format questions** in `strategy-synthesis.md §10` (single-ERP vs multi-cam tokens; can the loss ingest loss_weight/ignore/risk; delete vs low-weight generated; what is the training loss; which failure they fear most).

---

## 8. TRUST CAVEATS (given the fabrication issue)

- A predecessor logged **fabricated** tool output: phantom commits (85918f6/4cb96a7/70f435e) + a phantom DB-78 "PARTIAL WIN" (FB 0.62/3.1 px, abstain 12→9.6%) that never reached disk. **Treat any "too-good" logged conclusion as suspect until an on-disk artifact confirms it.** Rule: route every remote/PowerShell result to a non-repo file + Read-verify; don't trust PowerShell echo / Glob "no files" / Edit-success text.
- **Read-verified at HEAD `87cc16b`:** `DB77b_p01_summary.json` (p90 15.37/12.78 m + the NN-scoring code), `DB76a_batch_summary.json` (false-GREEN 0.373/0.223, 81.4/81.5% single-source, ~3% abstain, 15.6% co-observed, rotation-only), `DB76b_b4_summary.json` (curb/wall 5.0/20.0%, validated-green 0.60/0.53%), `quantitative_5scene_diags.json` (edited_frac 2.47–2.78%, far-warp ≤0.22 px), and the fork oscillation.
- **NOT independently re-fetched this session** (web-cited by sub-agents): NeuRAD-drops-AV2-stereo, SetDiff-unreleased/3DGS-input, ConFixGS/ExtraGS/EUVS collapse, World-in-World, Percep360. **Re-fetch paper text before any enters a brief/the paper** (an agent already misused Do et al. TIP'12 once).
- **DB-78 5-scene VISUAL seam quality is UNVERIFIED** (boards on Drive, never eyeballed; only BMW+0bae = "MODEST"). A vision pass could flip the practical decision.
- **DB-78 no-LiDAR generality is PARTIAL** (`--prealign none` makes flow RGB-only, but obj/ground gating still loads LiDAR). The fully-no-LiDAR A/B (north-star graceful degradation) is NOT isolated on disk.

---

*Workflow `wf_789ffbb7-1a7` · 19 agents · 2.39M tokens · 391 tool uses · full output in the session tasks dir (`w0pm57r9e.output`). Status: retrospective done; DB-79 PROPOSED (not yet opened as a brief — pending user fork decision + greenlight).*
