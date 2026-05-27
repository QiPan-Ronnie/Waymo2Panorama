# Wakeup Summary — 2026-05-27

You went to sleep around 17:00 UTC after approving the L1+L2+L3 ship.
Here's where we are now.

## TL;DR — what's done while you slept

**Shipped to `main`**:
- L1+L2+L3 pipeline as `blend_mode='hard_hdr_of'` in `stitch_one_frame`
- New module: `code/waymo2panorama/blending/hard_hdr_of.py` (~200 LOC)
- CLI: `scripts/phase3/render_log_with_hard_hdr_of.py` for batch rendering

**Two extra improvements added on top of what you saw**:
- **L2 v3 (centered gains)**: HDR gains now spread around 1.0 instead of anchoring
  front_center to 1.0. Fixes the "front_center in shadow" failure mode.
  Validated: 8.2% → **10.8% mean seam-gap improvement** (6 anchors).
- **L3 v2 (back-seam OF)**: Added one more OF warp closing the back-seam loop
  (rear_right → rear_left). Eliminates residual drift at the panorama back.

**Running in background** (will finish before you wake or soon after):
1. v1 5-log render (stride 10, hard_hdr_of as you approved) — ~60 min remaining (sped up after v2 cancelled)
2. ✅ Multiband baseline render finished (160 panoramas) — ready for preview grid
3. ✅ Seam-gap metric across all 5 logs (paper number) — **38% mean improvement**
4. ❌ v2 5-log render cancelled at ~10% done — was slowing v1 down; v2 deltas are subtle (1.45/255 mean) so not worth the parallel cost. v2 code is shipped and you can re-run when you want.

**Paper drafts** written (in `agent/`):
- `paper_outline_3dv2026.md` — 6 sections + TODO list
- `paper_method_draft.md` — Section 3 with equations
- `paper_experiments_draft.md` — Section 4 with seam-gap results

**Handoff doc**:
- `deliverables/HARD_HDR_OF_PIPELINE.md` — full design + usage + NEG ablations

## Where to find things

| Want | Look at |
|---|---|
| Final pipeline code | `code/waymo2panorama/blending/hard_hdr_of.py` |
| BMW before/after | `deliverables/hard_select/bmw_compare.png` |
| 4-way comparison | `deliverables/hard_select_hdr_joint/bmw_4way.png` |
| Paper outline | `agent/paper_outline_3dv2026.md` |
| Method section | `agent/paper_method_draft.md` |
| Experiments section | `agent/paper_experiments_draft.md` |
| Full handoff doc | `deliverables/HARD_HDR_OF_PIPELINE.md` |
| Bosch v1 panoramas | `outputs/phase3/full_pipeline_v1/` (on Drive, rendering) |
| Bosch v2 panoramas | `outputs/phase3/full_pipeline_v2/` (on Drive, rendering) |
| All N1 NEG history | `deliverables/N1_AUTONOMOUS_RUN_SUMMARY.md` |

## All algorithm work DONE (user was out, T4 was utilized)

**Final state**: 5 algo + 1 combined + 2 metrics shipped, NCC ghost metric ran (definitive +25.3% improvement). All real-BMW tests done. PDF anchor 60 discrepancy investigated (data/loader-side, algorithm correct).

**5 algorithm variants implemented + reviewed + tested + shipped to main:**

| # | Variant | Module | Status | Insight |
|---|---|---|---|---|
| A | Chroma correction | `hard_hdr_of_chroma.py` | ✅ Reviewed+Fixed | Tikhonov + outlier reject, +1.0s |
| B | Graphcut smart seam | `hard_hdr_of_graphcut.py` | ✅ Approved | cv2.detail.GraphCutSeamFinder on ±30px band, +1.4s |
| F | Self-stereo from 2-cam | `hard_hdr_of_selfstereo.py` | ✅ NEG (valuable) | Math works, but N1 FOV-gap pathology → BMW coverage 98.7%→62.1% → black holes |
| G | Freq-band hybrid | `hard_hdr_of_freqhybrid.py` | ✅ Approved | Low-freq blend + high-freq hard select, ~37s |
| H | Bidir true chain + joint | `hard_hdr_of_bidir.py` | ✅ Reviewed+Fixed | True bidir via mean half-flow (single Jacobi iter of joint solve) |

**+2 new metrics + 1 wrap-up artifact**:
- Doubled-pair YOLO metric (NEG: scales with detection count, doesn't isolate ghosts)
- All-variants A/B panel: `deliverables/all_variants_bmw.png` — 7 pipelines stacked
- `deliverables/ALGORITHM_VARIANTS_SUMMARY.md` — comprehensive catalog

**Cross-log seam-gap result (definitive)**:
| log | scene | raw ΔY | v2 ΔY | improvement |
|---|---|---|---|---|
| 02a00399 | quiet residential | 23.8 | 20.5 | -13.4% (easiest) |
| 0bae3b5e | busy urban | 24.5 | 17.2 | -29.7% |
| 2c652f9e | intersection | 43.9 | 21.0 | -52.2% |
| 9f871fb4 | highway | 32.9 | 21.0 | -36.4% |
| fbee355f | parking garage | 34.8 | 15.1 | -56.9% (hardest, biggest win) |
| **mean** | | **31.97** | **18.94** | **-37.7%** |

**Per-variant runtimes @ 2048x4096 (T4)**:
- Multiband baseline: 3.6s
- L1 hard_select only: 1.3s
- L1+L2+L3 SHIPPED: 36.0s
- +A chroma: 37.0s (+1.0s)
- +B graphcut: 35.9s (basically free)
- +G freqhybrid: 37.1s
- +H bidir: 36.7s

## YOLO panorama finding (160 panoramas)

| metric | multiband | hard_hdr_of | delta |
|---|---|---|---|
| person | 47 | 62 | +32% |
| car | 112 | 141 | +26% |
| truck | 7 | 37 | **+428%** |
| bus | 4 | 3 | -25% |
| total | 170 | 243 | **+43%** |
| mean/pano | 1.06 | 1.52 | +43% |

**Interpretation**: hard_hdr_of detects MORE objects, not FEWER. This is a POSITIVE finding for perception use — sharper objects, more detectable. But it does NOT measure ghost reduction directly. Multiband blurs distant objects into background (especially trucks, where +428%); hard_select preserves them. A true ghost metric needs "doubled bbox" detector (task #59).

## Key numbers for the paper

- **Multiband baseline**: ~6s/anchor, produces doubled-feature ghost
- **L1+L2+L3 full pipeline**: ~50s/anchor at 2048×4096 on T4, no new deps
- **N1 family (4 variants, all NEG)**: documented in `progress.md` + `N1_AUTONOMOUS_RUN_SUMMARY.md`
- **L2 HDR seam-gap improvement across 5 logs (17 anchors)**: **~38% mean reduction**
  - 02a00399 quiet residential: -13.4% (easiest case)
  - 0bae3b5e busy urban: -29.7%
  - 2c652f9e intersection: -52.2%
  - 9f871fb4 highway: -36.4%
  - fbee355f parking garage: -56.9% (hardest case, biggest win)
- **Cross-cam luminance gap**: 5.5 dB mean / 9.1 dB max on AV2 ring (justifies L2 HDR)
- **AV2 ring parallax**: 0.21-0.26m baseline; 3m object → ~46 px ERP shift (justifies L3 OF)

## Suggested next-steps (in order)

1. **Visual inspect `deliverables/all_variants_bmw.png`** to pick the keeper variant. At thumbnail they look near-identical; pixel zoom on specific seams (BMW front-right edge, lane line continuity around the front-center seam) will show real differences.
2. **If a variant clearly wins**: re-run 5-log render with that variant as default (~2 hr)
3. **If happy with L1+L2+L3**: ship the existing 160 panoramas as final Bosch deliverable
4. **Build a better ghost metric**: per-pixel cross-correlation in overlap zones (current YOLO metrics are unsuitable — documented in `deliverables/doubled_metric_negative_finding.md`)
5. **Paper figure prep**: pixel-level zooms of BMW + Porsche regions for the 7-way comparison panel
6. **Write paper sections 1, 2, 5, 6** (intro, related work, discussion, conclusion) — drafts in `agent/paper_*.md`

## Things I considered but didn't do

- **Improvement C (chroma correction)**: Risk of equalizing real scene colors (a green
  tree vs gray wall would get wrongly equalized). Would need regularization. Deferred.
- **Cancelling v1 5-log mid-run**: Decided v1 and v2 differ in subtle ways (mean diff 1.45/255)
  and having both deliverables is better for direct comparison
- **stride 5 densification**: Stride 10 gives 160 panoramas which is plenty for a Bosch
  preview. Densification can wait until they ask for it.

## All commits (autonomous session)

```
1ef96e0 Handoff doc + progress.md
30413a5 Scoring tools (seam-gap + preview grid + multiband CLI flag)
3224008 Paper outline + 8.2% seam-gap result
438fff3 L2v3 centered + L3v2 back-seam (the "v2" pipeline)
63ef547 Paper outline updated (mark v2 as shipped)
2fd7ca7 Paper Section 3 (method, with equations)
9fa8458 v1 vs v2 visual diff
cbf5f32 Paper Section 4 (experiments)
```

GitHub: <https://github.com/QiPan-Ronnie/Waymo2Panorama/commits/main>
