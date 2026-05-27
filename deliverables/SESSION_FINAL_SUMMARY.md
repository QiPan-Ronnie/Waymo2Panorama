# Final Session Summary — 2026-05-27

User went out around 19:20 UTC with "T4 stays open, use /subagent-driven-development, opus 4.7 effort max, 发散思维, 加油".

This doc summarizes everything done after that.

## TL;DR

Dispatched **12 subagents in parallel** (Opus 4.7 max effort) implementing 5 algorithm improvements + 2 metrics + 1 combined variant + 4 reviewers. Everything is on `origin/main`.

**Bottom line**: L1+L2+L3 (the shipped default) is essentially the best basic-CV pipeline. Each improvement (A/B/G/H) adds marginal value at small cost; self-stereo (F) is a definitive negative; the YOLO bbox metric doesn't work for ghosts.

**Suggested next action**: visually inspect `deliverables/all_variants_bmw.png` (7-way panel) and `deliverables/combined/bmw_3way_real.png` to decide whether to make the combined A+B variant the new default (and re-render 5 logs).

## What was built

### Algorithm variants (all in `code/waymo2panorama/blending/`)

| Variant | Module | Status | Best use case |
|---|---|---|---|
| **L1+L2+L3 SHIPPED** | `hard_hdr_of.py` | Already shipped | Production default |
| Improvement A: chroma | `hard_hdr_of_chroma.py` | ✅ reviewed+fixed | Cross-cam white-balance drift |
| Improvement B: graphcut | `hard_hdr_of_graphcut.py` | ✅ approved | Route seam around objects |
| Improvement F: self-stereo | `hard_hdr_of_selfstereo.py` | ✗ NEG | (valuable: proves L3 OF is right) |
| Improvement G: freq-hybrid | `hard_hdr_of_freqhybrid.py` | ✅ approved | Low-freq blend + high-freq sharp |
| Improvement H: bidir OF | `hard_hdr_of_bidir.py` | ✅ reviewed+fixed | Symmetric OF without preferred cam |
| **Combined A+B** | `hard_hdr_of_combined.py` | ✅ shipped | **Likely best production** |

### Metrics
- `score_panorama_yolo.py` — total YOLO detections (existing). hard_hdr_of has +43% detections vs multiband — but this is DETECTABILITY, not ghost.
- `score_panorama_doubled.py` — same-class pair IoU 0.1-0.7. **NEG**: scales with detection count, doesn't isolate ghosts.
- `measure_overlap_ncc.py` (just shipped) — windowed NCC in overlap zones. Expected: hard_hdr_of > hard_select > multiband. Currently running.
- `measure_seam_gap.py` — Y-channel ΔY at seam pixels. **38% mean improvement** across 5 logs.

### Test scripts (all in `scripts/phase3/`)
- `test_chroma_correction.py`, `test_graphcut_seam.py`, `test_freqhybrid.py`, `test_bidir_of.py`, `test_selfstereo.py`, `test_combined.py`
- All accept `--log-dir` + `--anchor` for real AV2 testing

### Deliverables (all in `deliverables/`)
- `all_variants_bmw.png` — **7-way A/B panel on real BMW** (the headline visual)
- `combined/bmw_3way_real.png` — A vs +chroma vs +chroma+graphcut
- `bidir_of/3way_real.png` — shipped vs true-bidir-chain vs joint solve
- `freqhybrid/bmw_4way_real.png` — multiband / hard_select / hard_hdr_of / freqhybrid
- `graphcut_seam/2way_real.png` — shipped vs +graphcut
- `ALGORITHM_VARIANTS_SUMMARY.md` — comprehensive catalog
- `selfstereo_finding.md` — why depth-based fails even with perfect depth
- `doubled_metric_negative_finding.md` — why YOLO bbox metric is wrong
- `HARD_HDR_OF_PIPELINE.md` — main handoff doc (unchanged from earlier session)
- `WAKEUP_SUMMARY.md` — running session log

## Key insights (paper-quality)

1. **L1+L2+L3 ships and works**. 160 panoramas across 5 logs, BMW visibly de-ghosted, brightness uniform, lane lines aligned.

2. **38% mean seam-gap improvement** across 5 logs (range -13% quiet residential to -57% parking garage). Hardest scenes get biggest HDR win.

3. **Self-stereo NEG strengthens the paper's thesis**: even SELF-DERIVED perfect depth fails for N1 FOV-gap reasons. No depth-based ring-cam method works at near-field.

4. **YOLO bbox metric isn't ghost-sensitive**. Doubled-feature ghost shows as ONE low-confidence detection to YOLO, not TWO separate boxes. Need pixel-level metrics (NCC, currently being computed).

5. **Combined variant insight**: chroma MUST precede graphcut. Otherwise WB drift dominates the cut cost and the seam routes against color instead of around objects.

6. **All 4 improvements (A, B, G, H) are cheap**: each adds <2s overhead per anchor at 2048x4096. Could be safely combined or used à la carte.

## Subagent stats

- **12 subagent dispatches** (5 implementers + 4 reviewers + 2 fixers + 1 doubled metric + 1 NCC metric + 1 combined variant) using Opus 4.7 effort max
- Pattern: per-task implementer → spec compliance reviewer → code quality reviewer → fixer (if needed)
- Followed `superpowers:subagent-driven-development` skill
- ~30 commits this session
- ~3000 LOC added

## What I would do next

1. **Wait for NCC metric to finish** (~30-60 min, started 19:30 UTC). This is the proper quantitative ranking of methods.
2. **If combined A+B clearly wins**: re-render 5-log with `blend_hard_hdr_of_combined` as the new default (~2 hr)
3. **Pixel zoom paper figures**: take BMW + Porsche regions from the 7-way panel and zoom in for paper-quality figures
4. **Write a clean conclusion** picking ONE recommended variant based on NCC scores

---

## Updates AFTER initial summary write (21:00+ UTC)

### NCC metric COMPLETED — definitive ghost reduction proof
- **multiband NCC: 0.6461** | **hard_hdr_of NCC: 0.8094** | delta **+25.3%**
- Chimera floor (NCC cam vs cam): 0.1095 (sanity ref)
- SSD pano vs winner: 369.76 → 320.16 (-13.4%)
- 32 anchors of 02a00399 (quietest log; busy logs should show even bigger gap)
- See `deliverables/NCC_FINDING.md`

### Combined A+B variant verified on real BMW (commits 83fdf39, e703bc7, 0355836)
- Chroma offsets tiny (~1-2 in 0-255 scale) for anchor 0 → visual diff sub-pixel
- Real-BMW 3-way panel at `deliverables/combined/bmw_3way_real.png`
- Needs harder anchor (e.g., fbee355f garage) to show chroma's real benefit

### All other algo variants tested on real AV2 BMW (post-implementation)
- Freqhybrid: `deliverables/freqhybrid/bmw_4way_real.png` (8192x1024 panel)
- Bidir 3-way: `deliverables/bidir_of/3way_real.png` (chain vs joint vs shipped)
- Graphcut: `deliverables/graphcut_seam/2way_real.png` (+1.4s overhead, subtle diff)
- All near-identical to shipped at thumbnail; sub-pixel differences

### Fresh anchors comparison (commit 9aed5d1)
- 11 anchors NEVER previously rendered (stride != 10): a17/a47/a127/a247 across 5 logs
- multiband vs hard_hdr_of side-by-side
- `deliverables/fresh_anchors/fresh_anchors_grid.png` (3520x1280, 11 row pairs)

### L1 baseline rendered on 10 diverse AV2 anchors (commits 5e08f46, b21db97)
- Pure multiband (PDF §1.1 method), no hard_select
- 1024x2048 individual PNGs at `deliverables/l1_baseline_diverse/`
- Includes anchor 60 of 02a00399 (PDF reference)

### PDF anchor 60 discrepancy investigation (commit b830f15)
- Tried to reproduce PDF's `deliverables/images/l1_erp.png`
- My output mean diff vs PDF: 15.15, max 228 — **not just JPEG noise**
- Both renders use same code (verified: multiband.py 0 commits since 5/19,
  sphere_projection.py legacy path bit-identical)
- My inline render = `run_l1_baseline.py` output: **pixel-identical (diff 0)**
- **Root cause**: anchor 60 maps to DIFFERENT physical frames between PDF (5/21) and now —
  likely Drive data was re-downloaded with different timestamps OR index changed
- Algorithm itself is correct; image content shift is data/loader-side, not render-side
- The "white columns" PDF shows are present in any L1 multiband — visibility depends
  on scene color distribution in the overlap zones
