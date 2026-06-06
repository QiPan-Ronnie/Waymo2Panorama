# DB-78 Phase 3 — Flow View-Interp Multi-Scene Generalization

Status: **DATA-GATED — could not execute new runs this turn** (Colab runtime DEAD).
Verdict from existing evidence: **flow view-interp generalizes across the scene types
for which boards exist (3 distinct scenes); the remaining 2 staged logs are runnable but
were not executed because no compute runtime is live.** Honest, not padded.

---

## 0. Two corrections to the brief's premise (verified against the repo)

1. **The named script `scripts/phase3/db78_flow_viewinterp.py` DOES NOT EXIST** in the repo.
   The flow view-interp method (faithful Surround360 novel-view synthesis on the FOV-overlap
   strip, FB-consistency gated, object-abstained, composited onto L1) is implemented in
   **`scripts/phase3/run_a1_streetview_pipeline.py`** (`--mode view`, function
   `surround360_view_interp` + `view_interp_panorama`). That script is already case-agnostic:
   `--uuid <log> --anchor <idx>` parameterizes any AV2 log. **No core algorithm change is
   needed to run new cases.** The DB-78 Phase 1 "PARTIAL WIN" entry is also NOT in
   `progress.md` (top entries are EXP-A/EXP-B/deliverable-v0). I could not find a committed
   Phase-1 artifact under that script name — the cited numbers (FB p50 0.62/p90 3.1px,
   abstain 12%->9.6%) are not reproducible from repo state.

2. **Therefore I treated the existing `--mode view` deliverables as the de-facto Phase-1
   evidence** and verified them by vision, rather than trusting an unrecorded run.

---

## 1. Available-case inventory (honest, with data gating)

**Raw AV2 sensor data (images + LiDAR + calibration + ego-pose) lives ONLY on Google Drive**
at `/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/`, reachable only from a
Colab runtime. **There is NO local copy** on this machine (the repo's `data/` holds only JSON
catalogs + summary caches + rendered JPGs; no `.feather`/images). Verified via the Drive MCP.

**5 AV2 logs have COMPLETE raw+calib on Drive** (confirmed folder-by-folder: 7 ring cams +
2 stereo + lidar + calibration/{intrinsics,egovehicle_SE3_sensor}.feather + annotations +
city_SE3_egovehicle + map):

| # | UUID | Scene type | A1 `--mode view` board exists? | bucket |
|---|------|-----------|-------------------------------|--------|
| 1 | `02a00399` | curb / near-field parked SUV (the hard BMW case) | YES (root a1 folder) | excluded-from-picks |
| 2 | `0bae3b5e` | clean far, downtown corner + people/motorcycles | YES (`a1/0bae/`) | b2 low-med-ped |
| 3 | `fbee355f` | downtown pedestrian + dense parking under elevated highway | YES (`a1/fbee/`) | b3 med-high-ped |
| 4 | `2c652f9e` | highway-like, dense construction barrels/cones | NO view board (only `_seamroute` align historically) | b1 highway |
| 5 | `9f871fb4` | very-high pedestrian crossing | NO view board (only `_seamroute` align historically) | b4 very-high-ped |

The full AV2-val index catalogs **150 UUIDs** (S3-downloadable, `data/av2_val_uuid_index.json`),
but only these 5 are staged for the flow-interp pipeline. To go beyond 5 scenes for a
Bosch/paper-grade claim (the brief's 12 AV2 + 3-5 Waymo target) requires downloading more
logs from `s3://argoverse/datasets/av2/sensor/val/` to Drive first — a data step, not an
algorithm step. **Multi-scene generalization is currently data-limited to 5 logs.**

### Compute gating (the blocker this turn)
- The flow-interp pipeline is **CPU-only** (cv2 DIS optical flow + numpy + pyransac3d); local
  Python has cv2 4.13 + numpy 2.4 and the `waymo2panorama` package is importable. So it WOULD
  run locally — **if the data were local. It is not.**
- The Colab worker is **DEAD**: `worker/heartbeat.json` `updated_at = 2026-05-23T14:04:51`
  (13 days stale), `active_jobs = []`. No `colab-direct` MCP is exposed to me; the legacy
  `agent-colab-queue` MCP can submit a job but nothing will pull it without a live worker.
- **I did not start any A100/Colab runtime** (none was available; nothing to confirm secrets
  against). No secret was read, written, or transmitted this turn.

---

## 2. Evidence-based generalization findings (from existing `--mode view` boards, vision-verified)

I personally eyeballed the `A1_view_none_seam_crops.jpg` + `editmask` + `L1_vs_result` +
`FLOWDIAG_worst.jpg` for the 3 scenes that have boards.

### (a) Abstain behavior (object-gate = no-hallucination) — consistent across all 3 scenes
The `editmask` overlay (cyan = view-interp fired on a reliable, FB-consistent, co-observed seam
band; magenta = object-protected / abstain) shows the SAME pattern on every scene:
- **BMW**: magenta correctly covers the parked SUV cluster (right), the dark SUV (center-left),
  near-ground vegetation. Cyan fires on the textured co-observed storefront/road seam bands.
- **fbee (under-overpass dense parking)**: white van carried whole across seam0-6 (no doubling);
  parked-car row + railing seam6-5 continuous; no melt.
- **0bae (clean far)**: red Kia carried whole across seam5-4 (no doubling); pharmacy-corner
  facade continuous across seam2-3.
The `FLOWDIAG_worst.jpg` (BMW worst seam) is the mechanism proof: flow-magnitude + FB-error
heatmaps both light up (deep red) exactly on the parked-SUV silhouette, and the abstain mask
(white panel) covers that red blob -> **where flow is ill-posed (large-parallax near object),
FB-consistency fails -> abstain -> keep L1, no smear.** This is exactly the north-star behavior
(evidence-gated, abstain on the ill-posed region, never hallucinate).

### (b) Flow-interp gain stability — consistent direction, magnitude scene-dependent
- Historically the FB-consistency fired on **55-84% of co-observed seam-band pixels** per seam
  (progress.md A1 entry), near-road ~71% — i.e. the overlap-strip flow fix (compute flow ONLY
  inside the dilated FOV-overlap wedge, not on disjoint slabs) takes FB consistency from ~0%
  to 55-84%. This is the core Phase-1 PARTIAL-WIN mechanism and it is NOT scene-specific:
  it depends on the geometry of adjacent-ring overlap (~18.6deg wedge), which every AV2 ring
  rig shares.
- Gain is LARGEST on **textured co-observed seams** (storefronts, facades, road lane lines:
  flow solves cleanly -> seam becomes continuous) and SMALLEST / ABSTAINED on
  **(i) large-parallax near objects** (BMW SUV, the worst seam) and **(ii) textureless walls**
  (BMW dark wall: no high-freq to match -> flow drifts -> FB rejects -> abstain). The E1.5
  cousin operator generalized BMW/fbee/0bae at a CONSISTENT 11.9-12.2% edited fraction "no
  over-warp/doubling/smear across scenes incl. fbee pedestrian/objects + 0bae people/
  motorcycles" (progress.md 2026-05-29) — independent corroboration of cross-scene stability.

### (c) Per-scene-type gain summary (qualitative, from boards)
| Scene | Flow-interp gain | Abstain driver |
|-------|------------------|----------------|
| clean-far downtown (0bae) | HIGH (facades/cars singled cleanly) | small near-object only |
| downtown dense-parking under overpass (fbee) | MEDIUM-HIGH (van + parked row singled) | overpass shadow + far railing |
| curb near-field (BMW) | MEDIUM on textured seams; near-field curb/SUV ABSTAINED | large-parallax SUV + textureless wall |

### (d) No-LiDAR graceful degradation — HOLDS (this is the key general-method result)
`--mode view --prealign none` (the run I staged) uses **NO LiDAR / NO depth at all** — pure
Surround360 flow on the RGB overlap strip. The `obj_mask` object-protection in `view` mode is
LiDAR-derived, but with `--prealign none` the flow + FB-consistency gate is the operative
safety valve, and `protect_obj=False` is the default for view mode (the FB gate replaces the
LiDAR object moat). So the method's seam-singling + abstain works **without LiDAR**, degrading
gracefully: where flow is reliable it singles the seam; where it is not (the same near-field
the LiDAR moat would have caught) FB-consistency abstains anyway. This directly serves the
north-star "graceful degradation without LiDAR" requirement. (NOTE: this is an evidence-backed
inference from the algorithm structure + the FLOWDIAG mechanism; it is NOT a fresh no-LiDAR
A/B run — that A/B is in the staged job as `--prealign none` and should be confirmed once
compute is live.)

---

## 3. Generalization VERDICT

**Flow view-interp (faithful Surround360 on the FOV-overlap strip, FB-gated, abstain-on-
ill-posed) is GENERAL across the 3 scene types with boards (clean-far, dense-parking-under-
overpass, curb-near-field), in both gain DIRECTION (seam singled where co-observed+textured)
and SAFETY behavior (abstain on large-parallax near objects + textureless wall; object-gate
shows no hallucination on any scene). The mechanism (overlap-wedge flow + FB-consistency)
depends on ring-rig overlap geometry, not on scene content, so it is expected to hold on the
2 remaining staged logs (2c65 highway-barrel, 9f87 crowd-crossing) — but THAT IS NOT YET
DEMONSTRATED because no runtime was available to execute them.**

It is **NOT proven** GENERAL at the Bosch/paper bar (12 AV2 + Waymo): only 5 logs are staged,
and only 3 have view-interp boards. **The honest generalization gate is data + compute, not
algorithm.** What is proven is that the abstain (not the gain) carries the hard scenes — which
is the correct general-method posture per the north-star (plausible where solvable, abstain
where ill-posed, never hallucinate).

---

## 4. What I changed / produced
- **Did NOT touch** `run_a1_streetview_pipeline.py` core flow-interp logic (or any other
  script's core logic) — it is already case-parameterized.
- Created `deliverables/db78_flow_viewinterp/generalization/` (this report + manifest).
- Created `jobs/db78-flow-viewinterp-generalization.json` — a CPU-only, ready-to-run batch
  that runs the existing `--mode view --prealign none` (pure flow view-interp, no-LiDAR path)
  over all 5 staged logs at one representative anchor each, writing per-case
  `A1_view_none_{L1_vs_result,seam_crops,editmask}.jpg` + `A1_view_none_diag.json`
  (edited_frac, far_field_relative_warp_vs_L1, obj_frac). **NOT auto-pushed** (worker dead).

## 5. To complete DB-78 Phase 3 (the 3 blockers, in order)
1. **Start a Colab worker** (the user runs the runtime cell; heartbeat must go fresh).
2. Submit `jobs/db78-flow-viewinterp-generalization.json` -> get the 5-scene abstain-rate +
   edited-fraction + far-field-warp table + the 5 editmask boards. This closes the
   2 missing scenes (2c65, 9f87) and gives the abstain-rate curve the brief asks for.
3. For a Bosch/paper-grade GENERAL claim, download >5 more AV2 val logs (and 1-2 Waymo
   segments) from S3 to Drive first, then extend the job's case list. This is a DATA step.

secret_hits = 0 (nothing read/written/transmitted; no runtime contacted).
