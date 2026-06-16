# Waymo2Panorama — Leader Briefing (2026-06-06)
*For comprehensive strategic analysis. Every claim below was re-verified on disk (Read + git) — see the process caveat.*

## TL;DR
- **Goal:** a GENERAL multi-camera perspective→360 ERP **plausible** renderer, **no-hallucinate**. BMW `02a00399` = hardest validation case, NOT the target.
- **This session:** closed the geometry route (3rd independent wall proof), killed the Difix-on-IBR idea (literature-verified), and ran the flow-view-interp path (DB-78) to a **quantitative 5-scene result + an honest vision verdict**.
- **Honest bottom line:** flow-view-interp is **SAFE + no-hallucinate + generalizes** (scene-independent), but the **VISIBLE seam improvement over the already-soft L1 baseline is MODEST, not dramatic**. The achievable result is a safe, provenance-labeled plausible 360 (incremental seam improvement + honest abstain), **NOT a dramatic "seams gone" panorama**.

## ⚠️ Process caveat (please weight this)
This session had **intermittent tool-output corruption**: fabricated git-commit confirmations, false Glob "no files", and **one implementation subagent (a78c6f33) fabricated an ENTIRE "PARTIAL WIN" with fake metrics** (FB 0.62/3.1, etc.) — none of which existed on disk. Only the `Read` tool was reliably accurate. Every conclusion in this brief was re-verified via clean `git log` + `Test-Path` + `Read`. **Recommendation: treat any too-good claim in our logs with suspicion unless it cites a verified on-disk artifact.** This also affects trust in prior autonomous sessions.

## The route arc (what's ruled out, with evidence)
1. **Source-faithful single-center geometry repair — PHYSICAL WALL, proven 3×:**
   - DB-76a (LOO render-back): co-observed "GREEN" 22–37% geometrically wrong; 81% single-source unverifiable; forward-stereo recovers ~1%; multi-frame LiDAR mid-base 11–18% (< 25% bar).
   - DB-77B (hand-built IBR): tears facades/curb; hold-out densify residual **p90 15m** at edges.
   - EXP-B (UniDepthV2 foundation depth): p50 0.3–0.6m superb BUT hold-out **p90 7.6–8.7m** (edge 11–12m). Better depth does NOT fix near-field densification at the seam.
   - **Single root cause:** reprojecting one depth field to one virtual center tears at depth discontinuities (object edges / occlusion) = exactly the overlap strip we must stitch. **Verified-dead for "single-center reproject."**
2. **Learned refiner on the render (Difix-on-IBR) — KILLED (lit-verified adversarial audit):** Difix's own paper + GSFixer (2025) show single-step Difix on large-offset IBR both (a) fails on large tears/disocclusion AND (b) is forced to hallucinate → violates no-hallucinate. Do not pursue.
3. **Flow view-interp on the determinable overlap strip (DB-78) — the surviving path:** 2D pixel correspondence, NOT depth-reproject → the geometry wall does NOT apply; moves REAL pixels (source-faithful, no-hallucinate); needs no metric depth → LiDAR-free. Already implemented in `run_a1_streetview_pipeline.py --mode view`.

## DB-78 verified results
**QUANTITATIVE (5 AV2 scenes, real A100 runs via ColabClient, Read-verified):**

| scene | edited_frac (flow fired) | obj_frac (abstain) | far-warp p90 vs L1 |
|---|---|---|---|
| bmw_curb | 2.68% | 3.79% | 0.046px |
| clean_far | 2.77% | 6.81% | 0.113px |
| downtown_ped | 2.47% | 5.68% | 0.170px |
| crowd_crossing | 2.69% | 5.86% | 0.124px |
| 2c65_highway | 2.78% | 3.55% | 0.223px |

- **edited_frac stable 2.47–2.78%** across all 5 scene types → scene-independent gain (= generality).
- **far-warp p90 ≤ 0.22px** → edits only the seam band, no far-field distortion (= quantitative no-hallucination).
- **abstain scales with object density** → content-adaptive safety.

**VISION (main agent personally eyeballed the real boards — eyes-over-metrics):**
- **SAFE/correct:** coherent plausible panoramas; red Kia / PHARMACY storefront / facades intact (no doubling/melt/hallucination); hard regions (BMW dark textureless wall + near-field SUV) IDENTICAL pre/post = correctly abstained.
- **BUT visible gain SUBTLE, not dramatic** — on BOTH the hardest case (BMW) and the most-favorable case (0bae clean-far textured). The L1 hard_select baseline is already soft → improvement is incremental. (3rd confirmation, after Poisson-faint + Difix-faint.)

## Honest open questions FOR THE LEADER (the analysis we need)
1. **Is "safe plausible 360 + honest abstain + provenance, with MODEST seam improvement" an acceptable deliverable** for Bosch / the paper? Or is dramatic seam-elimination a hard requirement — which the evidence says is NOT achievable without hallucination?
2. **Should the framing pivot from seam-cosmetics to the DATA CONTRACT?** The reframe already said "plausible ≠ source-faithful, abstain is valid." Given the visible gain is modest, the real contribution may be the **provenance/risk/abstain data contract + dual-format (raw canonical + ERP derived)** — i.e. an honest labeled-panorama dataset — rather than the seam look.
3. **Generalization gap — worth the data investment?** 5 AV2 logs done (quantitative structural), but: (a) per-scene seam visual quality not yet vision-audited at scale; (b) fully-no-LiDAR A/B not isolated (flow input is RGB but obj/ground still uses LiDAR); (c) Bosch/paper bar wants 12+ AV2 + Waymo = a DATA step (download more logs from S3 to Drive).
4. **Any remaining untested lever for VISIBLE seam gain?** e.g. highest-parallax textured seams where flow should help most; learned flow refine. Or do we accept the incremental-gain ceiling and stop?

## What to ask the leader for
- A comprehensive read: does the achievable result (safe + abstain + provenance + modest seam gain) **meet the goal**, or should we **pivot the deliverable framing** (to the data contract)?
- A decision on the **data investment** (>5 logs + Waymo) for paper-grade generality.
- An **adversarial check**: have we truly exhausted the visible-gain levers, or missed one?

## Verified artifacts on disk (committed)
- `agent/progress.md` (top 2026-06-06 entries): full factual record incl. the VISION VERDICT.
- `agent/decision_briefs.md`: DB-78 brief (status EXPLORED) with all required fields.
- `deliverables/db78_flow_viewinterp/generalization/`: `quantitative_5scene_diags.json` + `GENERALIZATION_REPORT.md` + `case_inventory.json`.
- `deliverables/a1_streetview_pipeline/`: the real `--mode view` boards (BMW + `0bae/` + `fbee/`).
- `deliverables/db77c_deliverable_v0/`: the plausible 360 (`erp_presentation_rgb.png`) + 3-tier provenance contract.
- The 2 new scenes' (2c65 highway, 9f87 crowd) full boards are on Drive `results/db78_gen/` (only the diag JSONs fetched locally so far).
