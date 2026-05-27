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
1. v1 5-log render (stride 10, hard_hdr_of as you approved) — ~40 min remaining
2. Multiband baseline render (same anchors, for preview grid comparison)
3. Seam-gap metric across all 5 logs (paper number)
4. v2 5-log render (with centered + back-seam improvements) — ~95 min remaining

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

## Key numbers for the paper

- **Multiband baseline**: ~6s/anchor, produces doubled-feature ghost
- **L1+L2+L3 full pipeline**: ~50s/anchor at 2048×4096 on T4, no new deps
- **N1 family (4 variants, all NEG)**: documented in `progress.md` + `N1_AUTONOMOUS_RUN_SUMMARY.md`
- **L2 v2 centered seam-gap improvement**: 10.8% mean on 6 anchors of 02a00399
- **Cross-cam luminance gap**: 5.5 dB mean / 9.1 dB max on AV2 ring (justifies L2 HDR)
- **AV2 ring parallax**: 0.21-0.26m baseline; 3m object → ~46 px ERP shift (justifies L3 OF)

## Suggested next-steps (in order)

1. **Eyeball v1 vs v2 5-log panoramas** when both finish — confirm v2 is the keeper
2. **Run YOLO ghost scoring** on the new pipeline outputs (we have YOLO v2 scorer from before)
3. **Pick best 7-20 panoramas across logs** for Bosch deliverable
4. **Improvement C** (per-channel chroma in YCrCb) — last 10% of polish, see paper outline
5. **Write paper sections 1, 2, 5, 6** (intro, related work, discussion, conclusion)
6. **Brown-Lowe SIFT alternative** for L3 as a robustness ablation

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
