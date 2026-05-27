# Archived experiment findings (2026-05-27 consolidation)

These 10 markdown files were created as standalone findings during various experiments. User decided on 2026-05-27 that this pattern bloated the repo — same info lives in `../../agent/progress.md` entries.

**Don't add new files here**. New experiment findings → entry in `progress.md`. This folder is read-only history.

## What's here

| File | Topic | Read this if you need |
|---|---|---|
| `N1_AUTONOMOUS_RUN_SUMMARY.md` | N1 depth-based family (4 variants) all NEG | The full N1 NEG story (also in progress.md entries from 5.26-5.27 morning) |
| `HARD_HDR_OF_PIPELINE.md` | L1+L2+L3 shipped pipeline design + NEG ablation history | hard_hdr_of.py module rationale |
| `doubled_metric_negative_finding.md` | Why YOLO doubled-pair metric doesn't work for ghost | Metric design lessons |
| `selfstereo_finding.md` | F variant: derive depth from cam-pair OF, NEG | Why depth-based reprojection has FOV-gap pathology |
| `ALGORITHM_VARIANTS_SUMMARY.md` | 7 variants catalog (A chroma, B graphcut, F selfstereo, G freqhybrid, H bidir, A+B combined) | Quick reference for variant modules in `code/waymo2panorama/blending/` |
| `NCC_FINDING.md` | NCC pano-vs-winner metric: +25.3% definitive ghost reduction | The headline quantitative result for hard_hdr_of |
| `SESSION_FINAL_SUMMARY.md` | 5.27 12-subagent autonomous session full breakdown | Audit trail for the subagent-driven-development session |
| `WAKEUP_SUMMARY.md` | 5.27 wakeup TL;DR | Quick situational awareness for that morning |
| `CALIBRATION_CHECK_FINDING.md` | AV2 calibration bias ~1.3 px, BA refine dead | Direction A verdict (in seam problem investigation) |
| `parallax_diagnostic_notes.md` | Visual review notes during parallax debugging | Historical |
