# First-Principles Analysis — the virtual centre was never a design variable (Fable 5, 2026-06-09)

> Commissioned by the user via `2026-06-09-fable5-firstprinciples-brief.md` ("rethink from the absolute bottom; attack our hypotheses; eyes on the images"). All load-bearing claims below were verified this session on disk (code Read), on the L4 runtime (AV2 calibration probe, result Read-verified at `~/.waymo2panorama/probe_calib_result.json`), and by eye (L1/A1/G/BEST/base-compare/DB-79 boards + Xinhan video frames).

---

## 1. The finding in one paragraph

The project's ERP virtual centre has been **pinned to the AV2 ego-vehicle origin in every script since L1** — `sphere_projection.py:5-6` ("treat every ring camera as if at the ego-vehicle origin"), `db79_fair_metric_wall.py:136-139` (`ego_to_uv` takes norms of raw ego coordinates). Measured on the real BMW log calibration (L4 probe, Read-verified): the 7 ring cameras sit **1.81–2.18 m from the ego origin** but only **0.27–0.30 m from their own centroid** (centroid ≈ `[1.363, -0.004, 1.445]` m in ego frame). Every depth-aware render-back number we have — including DB-79's wall verdict (curb/wall 55–88 px, silhouette 120–150 px) — was measured with the virtual centre **~2 m away from the cameras (mostly height + forward offset)**, which multiplies depth-error-induced reprojection error by the perpendicular baseline. Moving the centre to the ring centroid shrinks that baseline by **~7× in the worst direction and up to ~100× inside each camera's own viewing sector** (the offset becomes nearly collinear with the view ray). The "seam wall" is therefore, on current evidence, **roughly an order of magnitude self-inflicted**. Nobody tested this in 79 briefs: c\* was treated as a fixed fact of the rig ("the rig centre is off every camera path"), never as a free parameter.

## 2. The physics, re-derived from the bottom

Stitching N perspective images into a single-centre ERP is resampling the plenoptic function `L(c, ω)` from samples at camera centres `c_i` to a query centre `c*`. Two distinct error sources:

1. **Adjacent-camera parallax** (`b_cam-cam ≈ 0.21–0.26 m`): drives the *rotation-only* (no-depth) seam jump — the measured 16–21 px @ Z=3–5 m. Independent of c\*. This is what L1/hard_select shows today.
2. **Render-back amplification** (`b_perp` = perpendicular distance from a camera centre to the query ray): for a depth-aware render with depth error δZ, the ERP error is
   `err ≈ (W/2π) · b_perp · δZ / Z²`.
   **b_perp depends entirely on c\*.** With c\* = ego origin, b_perp ≈ 1.45–2.0 m (the 1.44 m camera height is almost all perpendicular for horizontal rays). With c\* = ring centroid, b_perp ≈ **0.01–0.06 m inside a camera's own sector** (offset is along the ray) and ≈ 0.10–0.30 m at sector boundaries.

**Cross-check against DB-79's own numbers (they fit the model exactly):**
- ROT (no-depth, X at 1000 m): predicted `f_cam·b_perp/Z` ≈ 1775·1.75/Z ≈ 310–780 px for Z=4–10 m → measured p90 **318/301 px** ✓
- DEPTH surface (δZ ≈ 6–9 cm): predicted 1775·1.75·0.07/16 ≈ 13 px → measured **4.9–14.9 px** ✓
- DEPTH curb/wall (silhouette-contaminated δZ ≈ 0.5 m): predicted ~97 px @ Z≈4 → measured **55–88 px** ✓

Same δZ, c\* = centroid (b_perp 0.06–0.15 m): surface **≈ 0.4–1 px**, curb/wall silhouette **≈ 3–8 px** (camera px; ÷~5.4 for ERP px at 1024×2048). That is "thin-band cosmetic" territory, not a wall.

**The structural bonus is robustness, not just accuracy:** at b_perp ≈ 0.06 m, even a 1.5 m depth error costs only ~2 ERP px. The 82–89 % of the near-ground task band with no LiDAR return — the region that forced "abstain" — no longer needs cm depth; a coarse plane fill suffices. The no-depth degradation limit is exactly today's L1 (graceful).

**What c\* does NOT fix (honest):** (a) adjacent-camera parallax in rotation-only fallback regions (16–21 px, unchanged — but depth-aware N1 rendering replaces rotation-only wherever any coarse depth exists); (b) true occlusion non-identifiability (Lemma A) — surfaces seen by no camera stay unknowable; the ghost budget at object silhouettes shrinks to a few px of edge misalignment + a visibility flip in a much thinner band, it does not vanish; (c) exposure/WB seams and the near-ground purple/green CA fringing (separate, solvable photometric problems — see §4).

## 3. Why 79 briefs missed it (for the record, no blame)

- The ego frame is the default coordinate system of every AV toolchain; "panorama centre = vehicle origin" was inherited in L1 and never revisited.
- The visible seam in L1/hard_select genuinely does NOT depend on c\* (it is rotation-only, adjacent-baseline driven). The correct intuition "moving c\* won't fix the current picture" silently leaked into evaluating the *depth-aware* route, where c\* dominates.
- DB-79's leader audit even wrote the amplification down ("~1.5 m virtual-centre→cam offset → tens of px is physically correct") — and then accepted the offset as physics instead of as a choice. The deep-retrospective's frontier list contains "don't force a single virtual centre" (representation change) but not "move the single centre" (a one-line change).

## 4. What my eyes say the defects actually are (vision pass, this session)

Ranked by visual salience on the full ERP (L1/G/BEST/bevfinal + ROI zooms):
1. **Coverage**: ~50 % of the ERP is black (vertical FOV + inter-camera gaps). Sky-outpaint (E8) fixes the top hemisphere well; its ground fill hallucinates white arc "lane lines" (visible in `e8_dit360_outpaint/bmw_outpaint_corecompose.png`).
2. **Exposure/WB steps** between cameras — the single most eye-catching in-band artifact at full-image scale, still present in every base I inspected despite 新-E (-18 % lum gap was measured but evidently not integrated into the shipped bases).
3. **Near-ground purple/green chroma fringing** (camera FOV-edge CA/vignetting; ERP near-ground = camera bottom edge). Ugly, purely photometric, untouched by any brief I can find.
4. **Geometric steps/ghosts** (curb staircase, wall-top crease, SUV edge) — the project's near-exclusive focus. Real, but at full-ERP scale less salient than 1–3; and per §2 its depth-aware component is mostly the c\* artifact.
5. Scalloped sky/ground borders per camera (shape issue; Xinhan's masking already mimics it downstream).

**Downstream reality check (Xinhan video, frames eyeballed):** the Cosmos-style model is being trained with first frames that are *perfect 360s masked to our stitched shape* — i.e. the consumer's expected conditioning distribution is "a perfect single-centre 360, cropped to the stitched mask". Holes are native to the contract; what is out-of-distribution is exposure steps, chroma fringing, and geometric tears *inside* the visible band. So "make the stitched band look like a slice of a perfect 360" is the precise target — and a centroid-centred ERP at camera height (1.44 m) is also closer to the real-360-camera viewpoint distribution than the current ground-level ego origin.

## 5. Attack results on the six standing hypotheses (brief §4)

| # | Hypothesis | Verdict after attack |
|---|---|---|
| 1 | Near-field multi-centre parallax is the root wall | **Partly overturned.** The adjacent-camera component (16–21 px rotation-only) is real but small; the catastrophic render-back numbers were dominated by the c\*=ego-origin amplification. Re-measure at centroid before calling anything a wall. |
| 2 | A/B fork is the churn driver; layer both | **Superseded by a sharper target:** "indistinguishable from a perfect-360 slice + honest holes + no fabricated salient geometry" (= Cosmos conditioning distribution). Faithfulness = honest masks; looks = distribution match. No fork remains. |
| 3 | DB-79: surfaces cm-recoverable, seam is a real wall | **Measurement valid, conclusion scoped too broadly.** The 55–88 px is a property of (depth, c\*=ego origin), not of (depth) alone. The densifier result (cm surfaces) survives; the "seam cannot be geometry-anchored" verdict must be re-tested at c\*=centroid (DB-80). |
| 4 | 3DGS / reconstruct-then-render doesn't dissolve the seam | **Weakened.** The off-trajectory collapse literature was applied with a ~1.5–2 m extrapolation in mind; at centroid the extrapolation is ≤0.3 m. Category stays parked (cost), but its kill evidence is now stale. |
| 5 | Abstain is valid/honest | **Confirmed, and strengthened** by the downstream contract (masked-360 training). But the abstain *area* should shrink a lot once depth tolerance relaxes ~20×. |
| 6 | Generative safe only for sky/tone | **Confirmed** for our layer (DiT ground fill hallucinates lane arcs — seen this session). The consumer's own generator handles holes; we should not fill salient geometry ourselves. |

## 6. The one concrete direction (→ DB-80 in `decision_briefs.md`)

**Recentre the ERP at the ring-camera centroid (camera height), re-render depth-aware (N1 mode already in `sphere_projection.py`), select per-pixel source by minimum perpendicular ray-baseline, and re-run the DB-79 render-back battery.** Pure geometry, CPU/L4, no new models, no generation. Pre-registered prediction: curb/wall DEPTH reproj p90 drops ≥4× (55–88 → <15 cam px); if it does not, the wall is finally confirmed against its strongest cheap attack and abstain stands with a clean conscience. Follow-ons if it wins: photometric pass (global exposure/WB harmonisation + CA-aware near-ground deghosting = visual-salience items 2–3), then the thin-band flow/feather (DB-78) on the residual few-px seams, then multi-scene generality.

*Optional later lever (logged, not in scope): time — ego motion sweeps the camera centroid along the trajectory, so per-direction frame selection ("common-path") can push b_perp toward 0 for forward/backward sectors; side sectors are bounded by the lane offset. Only worth opening after DB-80 settles the static-frame budget.*

## 7. Trust notes

- L4 probe routed to a non-repo file and Read-verified (fabrication rule honored); raw runtime URL/token kept out of the repo.
- Calibration numbers are from the actual BMW log feather (`02a00399…/calibration/egovehicle_SE3_sensor.feather`), not from memory.
- Surround360 analogy (14-cam ring of ~25 cm-scale radius renders near-seamless 360 at its own centre, flow only, no LiDAR) is supporting colour, not load-bearing; rig radius not re-verified to the cm. [Meta engineering post](https://engineering.fb.com/2016/04/12/video-engineering/introducing-facebook-surround-360-an-open-high-quality-3d-360-video-capture-system/).
- The b_perp error model is small-angle; the DB-80 brief pre-registers exact numeric thresholds so the model itself is falsified or confirmed by step A within minutes of CPU time.
