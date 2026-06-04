# DB42 Seam Decision / Bosch Handoff Synthesis

## Decision

Use `deliverables/dit360_v2/db32_generated_sky_harmonize_v2/db32_generated_sky_harmonize_s40.png` as the current Bosch handoff candidate.

Do not claim that the original `G_bmw_pano` / `A1_view_none` / `BEST_bmw_pano` right-ground seam has been repaired. The current evidence says that local seam repair should stop unless new raw/depth/correspondence evidence appears.

## Why

- DB38 accepted DB32 as the current handoff candidate with caveats: it sidesteps the original G seam by using the cleaner DB28/a200 source and only applies object-gated sky fill/harmonization.
- DB40 explains the A1 right-BMW ghost as candidate/mask mismatch, but rejects v14 DiT360 seam repair because the narrowed long-source mask hallucinates a pole-like vertical object.
- DB41 rejects lower-right/right-white-line repair evidence: `right_roi` LiDAR support is `0.084` and `lower_right_roi` LiDAR support is `0.000`.
- DB37/DB41 together answer the Google/Meta question: production stitchers rely on reliable overlap/depth/flow and abstain or choose a better source when evidence is insufficient.

## Handoff Caveats

- DB32 is a source-sidestep candidate, not a repair of the original G seam.
- The foreground black car remains.
- The lower out-of-FOV band remains.
- The center sky panel discontinuity is reduced, not eliminated.
- Fake generated ground/curb is worse for Bosch/world-model data than an honest capture caveat.

## Artifacts

- Board: `deliverables/dit360_v2/db42_seam_decision_handoff/db42_seam_decision_handoff_board.jpg`
- Manifest: `deliverables/dit360_v2/db42_seam_decision_handoff/db42_seam_decision_handoff_manifest.json`
- DB38 board: `deliverables/dit360_v2/db38_bosch_handoff/db38_bosch_handoff_board.jpg`
- DB40 boards: `deliverables/dit360_v2/db40_v14_mask_alignment/db40_a1_keepout_review_board.jpg`, `deliverables/dit360_v2/db40_v14_mask_alignment/db40_a1_longsrc_review_board.jpg`
- DB41 board: `deliverables/dit360_v2/db41_rightline_evidence_gate/db41_rightline_evidence_board.jpg`
